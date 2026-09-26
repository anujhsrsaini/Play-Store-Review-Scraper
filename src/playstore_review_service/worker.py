"""Background worker: claims jobs, scrapes (cache-first), analyzes, persists.

Runs in three modes:
- standalone process: ``python -m playstore_review_service.worker`` (production shape);
- in-process daemon thread for localhost dev (``DEV_INPROCESS_WORKER=1``, the default);
- ``process_one()`` called directly from tests.

Cost controls (spec §4.4): reviews-per-analysis cap (scraper-side), answer cache (skip
the LLM entirely on identical questions over the same snapshot), and a GLOBAL DAILY
SPEND kill-switch checked before every Gemini call — at the cap, jobs fail with a clear
error instead of spending more.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta
from datetime import time as dtime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import analysis as an
from . import llm, llm_openai
from . import retention as retention_mod
from .config import Settings, get_settings
from .db import (
    Analysis,
    AnalysisMetric,
    CachedReview,
    ComparisonJob,
    ComparisonResult,
    DailySpend,
    Job,
    Snapshot,
    UsageLog,
    make_engine,
    make_session_factory,
    utcnow,
)
from .scraper import client as scraper_client
from .scraper.errors import (
    RateLimitedUpstream,
    ScraperError,
    ScraperUnavailable,
    ScrapeTimeout,
)

logger = logging.getLogger(__name__)


class SpendCapExceeded(Exception):
    pass


def today_spend_usd(session: Session) -> float:
    midnight = datetime.combine(utcnow().date(), dtime.min)
    total = session.execute(
        select(func.coalesce(func.sum(UsageLog.cost_usd), 0.0)).where(
            UsageLog.created_at >= midnight
        )
    ).scalar_one()
    return float(total)


def _utc_day() -> str:
    return utcnow().date().isoformat()


SPEND_ALERT_THRESHOLDS = (0.5, 0.8, 1.0)  # Plans 3.3: warn at 50/80/100% of cap


def _alert_new_thresholds(before_usd: float, after_usd: float, cap: float) -> None:
    """Log a warning for each spend-alert threshold crossed upward (Plans 3.3).

    Provider-console budgets are the primary alerting; these log lines are the
    in-app backstop so threshold crossings are visible in host logs.
    """
    if cap <= 0:
        return
    for threshold in SPEND_ALERT_THRESHOLDS:
        if before_usd < threshold * cap <= after_usd:
            logger.warning(
                "daily LLM spend at %d%% of cap ($%.2f of $%.2f)",
                int(threshold * 100),
                after_usd,
                cap,
            )


def reserve_spend(session: Session, estimated: float, cap: float) -> bool:
    """Atomically reserve `estimated` against the daily cap under a row lock, so concurrent
    workers can't all pass the check before any writes (spec §4.4). Returns False if the cap
    would be exceeded. SQLite serializes writers; Postgres uses SELECT ... FOR UPDATE."""
    day = _utc_day()
    row = session.get(DailySpend, day, with_for_update=True)
    if row is None:
        row = DailySpend(day=day, spent_usd=0.0)
        session.add(row)
        session.flush()
    if row.spent_usd + estimated > cap:
        session.commit()
        return False
    before = row.spent_usd
    row.spent_usd += estimated
    session.commit()
    _alert_new_thresholds(before, row.spent_usd, cap)
    return True


def reconcile_spend(session: Session, delta: float, cap: float | None = None) -> None:
    """Adjust today's reserved spend by `delta` (actual − estimate, or release on failure).

    When the caller's cap is given, upward threshold crossings (50/80/100%) also
    alert — true-ups can push spend over a threshold the reservation didn't reach.
    """
    if not delta:
        return
    row = session.get(DailySpend, _utc_day(), with_for_update=True)
    if row is None:
        session.commit()
        return
    before = row.spent_usd
    row.spent_usd = max(0.0, row.spent_usd + delta)
    session.commit()
    if cap is not None:
        _alert_new_thresholds(before, row.spent_usd, cap)


def claim_next_job(session: Session) -> Job | None:
    stmt = select(Job).where(Job.status == "queued").order_by(Job.created_at).limit(1)
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":  # multi-worker safety in production
        stmt = stmt.with_for_update(skip_locked=True)
    job = session.execute(stmt).scalars().first()
    if job is None:
        return None
    job.status = "scraping"
    job.started_at = utcnow()
    session.commit()
    return job


def claim_next_comparison_job(session: Session) -> ComparisonJob | None:
    """Claim the oldest queued Battle Lens job (mirrors :func:`claim_next_job`)."""
    stmt = (
        select(ComparisonJob)
        .where(ComparisonJob.status == "queued")
        .order_by(ComparisonJob.created_at)
        .limit(1)
    )
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":  # multi-worker safety in production
        stmt = stmt.with_for_update(skip_locked=True)
    job = session.execute(stmt).scalars().first()
    if job is None:
        return None
    job.status = "fetching"
    job.progress_stage = "fetching"
    job.progress_detail = "Fetching both apps"
    job.started_at = utcnow()
    session.commit()
    return job


def _compare_winner(avg_a: float | None, avg_b: float | None) -> str | None:
    """Deterministic winner from per-side star averages (spec §4.3: never pay the
    LLM for a number). ``None`` when neither side has usable scores."""
    if avg_a is None and avg_b is None:
        return None
    if avg_a is not None and avg_b is not None and abs(avg_a - avg_b) < 0.05:
        return "tie"
    if avg_a is None:
        return "b"
    if avg_b is None:
        return "a"
    return "a" if avg_a > avg_b else "b"


def _snapshot_for_app(
    session: Session,
    *,
    app_id: str,
    country: str,
    lang: str,
    job_id: str,
    settings: Settings,
    on_progress: Callable[[int], None] | None = None,
) -> tuple[Snapshot, bool]:
    """Shared snapshot fetch behind ``ensure_snapshot`` and the compare path.

    Returns ``(snapshot, stale)``. Snapshots are keyed (app, country, lang) and
    shared across questions, periods, and comparisons — so the fetch is always
    FULL (no cutoff): time-windowing happens at analysis via ``in_period`` /
    ``lookback_cutoff_iso``. Truncating the stored snapshot would poison the
    other consumers of the same row. Stale reuse on transient failure mirrors
    ``ensure_snapshot`` (spec §4.6).
    """
    snap = (
        session.execute(
            select(Snapshot).where(
                Snapshot.app_id == app_id,
                Snapshot.country == country,
                Snapshot.lang == lang,
            )
        )
        .scalars()
        .first()
    )
    if snap is not None and snap.expires_at > utcnow():
        return snap, False

    def on_page(count: int) -> None:
        if on_progress is not None:
            on_progress(count)

    try:
        app_info = scraper_client.get_app(app_id, country=country, lang=lang)
        result = scraper_client.fetch_reviews(
            app_id,
            country=country,
            lang=lang,
            max_reviews=settings.max_reviews_per_analysis,
            delay_seconds=settings.scrape_delay_seconds,
            on_page=on_page,
        )
    except (RateLimitedUpstream, ScraperUnavailable, ScrapeTimeout) as exc:
        if snap is None:
            raise
        logger.warning(
            "job %s: live scrape failed (%s); reusing stale snapshot %s",
            job_id,
            type(exc).__name__,
            snap.id,
        )
        session.add(UsageLog(job_id=job_id, event="stale_reuse", cost_usd=0.0))
        session.commit()
        return snap, True

    if snap is None:
        snap = Snapshot(app_id=app_id, country=country, lang=lang, expires_at=utcnow())
        session.add(snap)
        session.flush()
    else:
        for old in session.execute(
            select(CachedReview).where(CachedReview.snapshot_id == snap.id)
        ).scalars():
            session.delete(old)

    snap.sort = result.sort
    snap.review_count = result.fetched
    snap.complete = result.complete
    snap.app_meta = asdict(app_info)
    snap.fetched_at = utcnow()
    snap.expires_at = utcnow() + timedelta(hours=settings.scrape_cache_ttl_hours)
    for review in result.reviews:  # Review dataclass is anonymized; no name is persisted
        session.add(
            CachedReview(
                snapshot_id=snap.id,
                review_id=review.review_id,
                score=review.score,
                text=review.text,
                created_at=review.created_at,
                app_version=review.app_version,
                thumbs_up=review.thumbs_up,
                reply_content=review.reply_content,
                replied_at=review.replied_at,
            )
        )
    session.add(UsageLog(job_id=job_id, event="scrape", cost_usd=0.0))
    session.commit()
    return snap, False


def ensure_snapshot(session: Session, job: Job, settings: Settings) -> tuple[Snapshot, bool]:
    """Single-analysis entry point: fresh snapshot reuse + progress on the job."""

    def on_page(count: int) -> None:
        job.progress = count
        session.commit()

    return _snapshot_for_app(
        session,
        app_id=job.app_id,
        country=job.country,
        lang=job.lang,
        job_id=job.id,
        settings=settings,
        on_progress=on_page,
    )


def _analyze(
    session: Session, job: Job, snap: Snapshot, settings: Settings, stale: bool = False
) -> Analysis:
    cached = (
        session.execute(
            select(Analysis).where(
                Analysis.app_id == job.app_id,
                Analysis.country == job.country,
                Analysis.lang == job.lang,
                Analysis.question_hash == job.question_hash,
                Analysis.period == job.period,
                Analysis.snapshot_id == snap.id,
            )
        )
        .scalars()
        .first()
    )
    if cached is not None:
        session.add(UsageLog(job_id=job.id, event="cache_hit", cost_usd=0.0))
        return cached

    rows = (
        session.execute(select(CachedReview).where(CachedReview.snapshot_id == snap.id))
        .scalars()
        .all()
    )
    reviews: list[dict[str, Any]] = [
        {
            "review_id": r.review_id,
            "score": r.score,
            "text": r.text,
            "created_at": r.created_at,
            "app_version": r.app_version,
            "thumbs_up": r.thumbs_up,
        }
        for r in rows
    ]
    # Scope the corpus to the selected review window (30/60/90 days — no lifetime view).
    period = job.period or an.DEFAULT_PERIOD
    cutoff = an.period_cutoff_iso(period, utcnow())
    corpus = an.in_period(reviews, cutoff)
    earliest, latest = an.date_range(corpus)
    # Does the newest-N sample (capped at MAX_REVIEWS_PER_ANALYSIS) actually reach back past
    # the window's start? If its oldest review is more recent than the cutoff, older in-window
    # reviews were truncated by the cap and weren't analyzed.
    oldest_sampled = an.date_range(reviews)[0]
    period_complete = oldest_sampled is None or oldest_sampled <= cutoff[:10]

    # Headline sentiment is the star ratings of the reviews IN the selected window, so the %
    # always matches the period the user picked.
    sentiment = {**an.star_sentiment(r["score"] for r in corpus), "source": "period_sample"}

    curated = an.curate_reviews(corpus, question=job.question)  # question-aware (N3)
    lines = an.format_review_lines(curated)

    started = utcnow()
    llm_fallback = False
    provider = settings.provider()
    if provider == "stub":
        payload = an.stub_analysis(job.question, curated)
        usage = {"tokens_in": 0, "tokens_out": 0}
        model, cost, event = "stub", 0.0, "stub"
    else:
        model = settings.llm_model
        # Estimate with the provider's real output ceiling, then ATOMICALLY reserve
        # against the daily cap before spending (§4.4).
        max_out = llm_openai.DEFAULT_MAX_TOKENS
        estimated = llm.cost_usd(model, llm.estimate_tokens(lines), max_out)
        if not reserve_spend(session, estimated, settings.global_daily_spend_cap_usd):
            raise SpendCapExceeded(
                f"daily LLM spend cap (${settings.global_daily_spend_cap_usd}) reached"
            )
        try:
            payload, usage = llm_openai.openai_compatible_analyze(
                job.question,
                lines,
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                model=model,
            )
            payload = an.verify_quotes(
                payload, {str(r["review_id"]): str(r["text"] or "") for r in corpus}
            )
            real_cost = usage.get("cost_usd")  # prefer provider's real billed cost
            cost = (
                float(real_cost)
                if real_cost is not None
                else llm.cost_usd(model, usage["tokens_in"], usage["tokens_out"])
            )
            reconcile_spend(  # true-up reserved → actual (alerts on threshold crossings)
                session, cost - estimated, settings.global_daily_spend_cap_usd
            )
            event = provider
        except llm.LLMError:
            # Don't fail the whole analysis on an LLM/parse error — degrade to the
            # keyword stub so the user still gets a grounded (if shallower) answer (N4).
            reconcile_spend(session, -estimated)  # release; nothing was billed to us
            logger.warning("job %s: LLM failed, falling back to stub", job.id)
            payload = an.stub_analysis(job.question, curated)
            payload["caveats"] = [
                *payload.get("caveats", []),
                "Full LLM analysis was unavailable; showing a keyword-based fallback.",
            ]
            usage = {"tokens_in": 0, "tokens_out": 0}
            model, cost, event, llm_fallback = "stub-fallback", 0.0, "llm_fallback", True
        except Exception:
            reconcile_spend(session, -estimated)  # release the reservation on failure
            raise

    payload = an.clamp_answer(payload)

    payload["sentiment_breakdown"] = sentiment
    period_label = an.PERIOD_LABELS.get(period, "all time")
    payload["period"] = period
    payload["period_coverage"] = {
        "period": period,
        "label": period_label,
        "from": earliest,
        "to": latest,
        "reviews": len(corpus),
        "complete": period_complete,
        # Average ★ of the reviews we actually analyzed — the "recent" rating, distinct from
        # Google's all-time aggregate (app.score / the rating-distribution histogram).
        "avg_rating": an.average_score(r["score"] for r in corpus),
    }
    sentiment_note = f"sentiment % is from these {sentiment['counted']} reviews' stars"
    span = f" ({earliest}–{latest})" if earliest else ""
    scope = f"{len(curated)} reviews from {period_label}{span}"
    payload["data_quality"] = (
        f"Based on {scope} "
        f"(sort={snap.sort}, {job.lang}/{job.country}, fetched {snap.fetched_at:%Y-%m-%d %H:%M} UTC"
        f"{', INCOMPLETE fetch' if not snap.complete else ''}); {sentiment_note}."
    )
    extra_caveats = []
    if stale:
        extra_caveats.append(
            f"Live Play Store data was temporarily unavailable; this answer uses a "
            f"cached snapshot fetched {snap.fetched_at:%Y-%m-%d %H:%M} UTC."
        )
    if not corpus:
        extra_caveats.append(f"No reviews were found in {period_label} within the fetched sample.")
    elif not period_complete:
        extra_caveats.append(
            f"The fetched sample only reaches back to {oldest_sampled}, so older reviews "
            f"in {period_label} weren't included."
        )
    if extra_caveats:
        payload["caveats"] = [*payload.get("caveats", []), *extra_caveats]

    record = Analysis(
        app_id=job.app_id,
        country=job.country,
        lang=job.lang,
        question_hash=job.question_hash,
        period=period,
        snapshot_id=snap.id,
        answer=payload,
        model=model,
        prompt_tokens=usage["tokens_in"],
        completion_tokens=usage["tokens_out"],
    )
    session.add(record)
    session.add(
        UsageLog(
            job_id=job.id,
            event=event,
            tokens_in=usage["tokens_in"],
            tokens_out=usage["tokens_out"],
            cost_usd=cost,
        )
    )
    session.add(
        AnalysisMetric(
            job_id=job.id,
            provider=provider,
            model=model,
            latency_ms=int((utcnow() - started).total_seconds() * 1000),
            reviews_fetched=snap.review_count,
            reviews_curated=len(curated),
            verified_quotes=len(payload.get("supporting_quotes") or []),
            dropped_quotes=sum(
                1 for c in payload.get("caveats") or [] if "unverifiable quote" in c
            ),
            not_enough_data=bool(payload.get("not_enough_data")),
            llm_fallback=llm_fallback,
        )
    )
    session.flush()
    return record


def _snapshot_corpus(session: Session, snap: Snapshot, cutoff_iso: str) -> list[dict[str, Any]]:
    """Cached rows for one snapshot as analysis dicts, scoped to the lookback window.

    Snapshots store the FULL fetch (shared across consumers); the lookback window
    applies here at analysis time, mirroring the single-analysis period filter.
    """
    rows = (
        session.execute(select(CachedReview).where(CachedReview.snapshot_id == snap.id))
        .scalars()
        .all()
    )
    reviews: list[dict[str, Any]] = [
        {
            "review_id": r.review_id,
            "score": r.score,
            "text": r.text,
            "created_at": r.created_at,
            "app_version": r.app_version,
            "thumbs_up": r.thumbs_up,
        }
        for r in rows
    ]
    return an.in_period(reviews, cutoff_iso)


def _run_comparison(session: Session, job: ComparisonJob, settings: Settings) -> ComparisonResult:
    """Fetch both corpora, analyze, and persist the result. Raises like :func:`process_one`."""

    def stage(percent: int, stage_name: str, detail: str) -> None:
        job.progress_percent = percent
        job.progress_stage = stage_name
        job.progress_detail = detail
        session.commit()

    focus = job.custom_focus or ""
    cutoff = an.lookback_cutoff_iso(job.lookback_days, utcnow())
    stage(5, "fetching_a", f"Fetching {job.app_a_id}")
    snap_a, stale_a = _snapshot_for_app(
        session,
        app_id=job.app_a_id,
        country=job.country,
        lang=job.lang,
        job_id=job.id,
        settings=settings,
    )
    stage(40, "fetching_b", f"Fetching {job.app_b_id}")
    snap_b, stale_b = _snapshot_for_app(
        session,
        app_id=job.app_b_id,
        country=job.country,
        lang=job.lang,
        job_id=job.id,
        settings=settings,
    )
    # Capture plain ids now: an IntegrityError rollback below detaches ORM objects,
    # so re-assignable ints (not object attribute reads) must survive that path.
    snap_a_id, snap_b_id = snap_a.id, snap_b.id
    fetched_a_iso, fetched_b_iso = snap_a.fetched_at.isoformat(), snap_b.fetched_at.isoformat()
    job.snapshot_a_id = snap_a_id
    job.snapshot_b_id = snap_b_id
    snaps_hash = an.snapshots_hash(
        job.app_a_id,
        snap_a_id,
        fetched_a_iso,
        job.app_b_id,
        snap_b_id,
        fetched_b_iso,
    )
    focus_hash = an.custom_focus_hash(focus or None)
    # Another worker (or the route) may have finished this exact key meanwhile.
    existing = (
        session.execute(
            select(ComparisonResult).where(
                ComparisonResult.canonical_pair_hash == job.canonical_pair_hash,
                ComparisonResult.country == job.country,
                ComparisonResult.lang == job.lang,
                ComparisonResult.lookback_days == job.lookback_days,
                ComparisonResult.custom_focus_hash == focus_hash,
                ComparisonResult.snapshots_hash == snaps_hash,
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        session.add(UsageLog(job_id=job.id, event="cache_hit", cost_usd=0.0))
        return existing

    stage(70, "analyzing", "Comparing corpora")
    corpus_a = _snapshot_corpus(session, snap_a, cutoff)
    corpus_b = _snapshot_corpus(session, snap_b, cutoff)
    question = focus or f"compare {job.app_a_id} vs {job.app_b_id}"
    curated_a = an.curate_reviews(corpus_a, question=question)
    curated_b = an.curate_reviews(corpus_b, question=question)
    lines_a = an.format_review_lines(curated_a)
    lines_b = an.format_review_lines(curated_b)

    started = utcnow()
    llm_fallback = False
    provider = settings.provider()
    if provider == "stub":
        payload = an.stub_compare(job.app_a_id, job.app_b_id, curated_a, curated_b, focus)
        usage = {"tokens_in": 0, "tokens_out": 0}
        model, cost, event = "stub", 0.0, "stub"
    else:
        model = settings.llm_model
        max_out = llm_openai.DEFAULT_MAX_TOKENS
        estimated = llm.cost_usd(model, llm.estimate_tokens(lines_a + lines_b), max_out)
        if not reserve_spend(session, estimated, settings.global_daily_spend_cap_usd):
            raise SpendCapExceeded(
                f"daily LLM spend cap (${settings.global_daily_spend_cap_usd}) reached"
            )
        try:
            payload, usage = llm_openai.openai_compatible_compare(
                job.app_a_id,
                job.app_b_id,
                lines_a,
                lines_b,
                focus,
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                model=model,
            )
            payload = an.verify_comparison_quotes(
                payload,
                {str(r["review_id"]): str(r["text"] or "") for r in corpus_a},
                {str(r["review_id"]): str(r["text"] or "") for r in corpus_b},
                app_a_id=job.app_a_id,
                app_b_id=job.app_b_id,
            )
            real_cost = usage.get("cost_usd")  # prefer provider's real billed cost
            cost = (
                float(real_cost)
                if real_cost is not None
                else llm.cost_usd(model, usage["tokens_in"], usage["tokens_out"])
            )
            reconcile_spend(  # true-up reserved → actual (alerts on threshold crossings)
                session, cost - estimated, settings.global_daily_spend_cap_usd
            )
            event = provider
        except llm.LLMError:
            # Degrade to the keyword stub so the user still gets a grounded (if
            # shallower) comparison instead of an error (mirrors single-analysis N4).
            reconcile_spend(session, -estimated)  # release; nothing was billed to us
            logger.warning("compare job %s: LLM failed, falling back to stub", job.id)
            payload = an.stub_compare(job.app_a_id, job.app_b_id, curated_a, curated_b, focus)
            payload["caveats"] = [
                *payload.get("caveats", []),
                "Full LLM analysis was unavailable; showing a keyword-based fallback.",
            ]
            usage = {"tokens_in": 0, "tokens_out": 0}
            model, cost, event, llm_fallback = "stub-fallback", 0.0, "llm_fallback", True

    payload = an.clamp_answer(payload)
    avg_a = an.average_score(r["score"] for r in corpus_a)
    avg_b = an.average_score(r["score"] for r in corpus_b)
    payload["winner"] = _compare_winner(avg_a, avg_b)
    if stale_a or stale_b:
        payload["caveats"] = [
            *payload.get("caveats", []),
            "Live Play Store data was temporarily unavailable; "
            "one or both corpora use cached snapshots.",
        ]
    record = ComparisonResult(
        app_a_id=job.app_a_id,
        app_b_id=job.app_b_id,
        canonical_pair_hash=job.canonical_pair_hash,
        country=job.country,
        lang=job.lang,
        lookback_days=job.lookback_days,
        snapshots_hash=snaps_hash,
        custom_focus_hash=focus_hash,
        result=payload,
        model=model,
        prompt_tokens=usage["tokens_in"],
        completion_tokens=usage["tokens_out"],
    )
    session.add(record)
    try:
        session.flush()
    except IntegrityError:
        # Lost a race with another worker on the same key — adopt the winner.
        # The rollback wipes uncommitted job edits, so re-apply them from plain values.
        session.rollback()
        record = (
            session.execute(
                select(ComparisonResult).where(
                    ComparisonResult.canonical_pair_hash == job.canonical_pair_hash,
                    ComparisonResult.country == job.country,
                    ComparisonResult.lang == job.lang,
                    ComparisonResult.lookback_days == job.lookback_days,
                    ComparisonResult.custom_focus_hash == focus_hash,
                    ComparisonResult.snapshots_hash == snaps_hash,
                )
            )
            .scalars()
            .one()
        )
        job.snapshot_a_id = snap_a_id
        job.snapshot_b_id = snap_b_id
    session.add(
        UsageLog(
            job_id=job.id,
            event=event,
            tokens_in=usage["tokens_in"],
            tokens_out=usage["tokens_out"],
            cost_usd=cost,
        )
    )
    session.add(
        AnalysisMetric(
            job_id=job.id,
            provider=provider,
            model=model,
            latency_ms=int((utcnow() - started).total_seconds() * 1000),
            reviews_fetched=(snap_a.review_count or 0) + (snap_b.review_count or 0),
            reviews_curated=len(curated_a) + len(curated_b),
            verified_quotes=len(payload.get("supporting_quotes") or []),
            dropped_quotes=sum(1 for c in payload.get("caveats") or [] if "unverifiable" in c),
            not_enough_data=bool(payload.get("not_enough_data")),
            llm_fallback=llm_fallback,
        )
    )
    session.flush()
    return record


def process_compare_one(session_factory, settings: Settings | None = None) -> bool:
    """Claim and fully process one Battle Lens job. Returns False when the queue is empty."""
    settings = settings or get_settings()
    with session_factory() as session:
        job = claim_next_comparison_job(session)
        if job is None:
            return False
        try:
            record = _run_comparison(session, job, settings)
            job.comparison_result_id = record.id
            job.status = "done"
            job.progress_percent = 100
            job.progress_stage = "complete"
            job.progress_detail = "Complete"
        except ScraperError as exc:
            job.status = "error"
            job.error = type(exc).__name__  # classified type only — no raw messages out
            logger.warning("compare job %s scraper failure: %s", job.id, type(exc).__name__)
        except SpendCapExceeded as exc:
            job.status = "error"
            job.error = "spend_cap_reached"  # classified code; full detail stays in logs
            logger.warning("compare job %s: %s", job.id, exc)
        except Exception:
            job.status = "error"
            job.error = "internal_error"
            logger.exception("compare job %s failed unexpectedly", job.id)
        job.finished_at = utcnow()
        session.commit()
        return True


def process_one(session_factory, settings: Settings | None = None) -> bool:
    """Claim and fully process one job. Returns False when the queue is empty."""
    settings = settings or get_settings()
    with session_factory() as session:
        job = claim_next_job(session)
        if job is None:
            return False
        try:
            snap, stale = ensure_snapshot(session, job, settings)
            job.snapshot_id = snap.id
            job.status = "analyzing"
            session.commit()
            record = _analyze(session, job, snap, settings, stale=stale)
            job.analysis_id = record.id
            job.status = "done"
        except ScraperError as exc:
            job.status = "error"
            job.error = type(exc).__name__  # classified type only — no raw messages out
            logger.warning("job %s scraper failure: %s", job.id, type(exc).__name__)
        except SpendCapExceeded as exc:
            job.status = "error"
            job.error = "spend_cap_reached"  # classified code; full detail stays in logs
            logger.warning("job %s: %s", job.id, exc)
        except llm.LLMError as exc:
            job.status = "error"
            job.error = "llm_error"
            logger.warning("job %s LLM failure: %s", job.id, type(exc).__name__)
        except Exception:
            job.status = "error"
            job.error = "internal_error"
            logger.exception("job %s failed unexpectedly", job.id)
        job.finished_at = utcnow()
        session.commit()
        return True


PURGE_INTERVAL_SECONDS = 3600.0  # TTL purge runs at most hourly, never on the hot path


def run_loop(session_factory, stop: threading.Event | None = None, interval: float = 1.0) -> None:
    stop = stop or threading.Event()
    logger.info("worker loop started")
    last_purge = 0.0
    while not stop.is_set():
        # Serve both queues: single analyses first, then Battle Lens comparisons.
        single_empty = not process_one(session_factory)
        compare_empty = not process_compare_one(session_factory)
        if single_empty and compare_empty:
            stop.wait(interval)
        if time.monotonic() - last_purge >= PURGE_INTERVAL_SECONDS:
            last_purge = time.monotonic()
            try:
                with session_factory() as session:
                    retention_mod.purge_expired(
                        session, job_retention_days=get_settings().data_retention_days
                    )
            except Exception:
                logger.exception("retention purge failed (worker continues)")


def start_inprocess_worker(session_factory) -> threading.Event:
    """Dev convenience: run the worker as a daemon thread inside the web process.
    Production runs ``python -m playstore_review_service.worker`` separately."""
    stop = threading.Event()
    thread = threading.Thread(target=run_loop, args=(session_factory, stop), daemon=True)
    thread.start()
    return stop


def main() -> None:  # pragma: no cover - standalone entrypoint
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    engine = make_engine(settings.database_url)
    from .db import init_db

    init_db(engine)
    run_loop(make_session_factory(engine))


if __name__ == "__main__":  # pragma: no cover
    main()
