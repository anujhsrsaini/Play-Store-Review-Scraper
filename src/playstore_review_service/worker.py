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
from dataclasses import asdict
from datetime import datetime, timedelta
from datetime import time as dtime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import analysis as an
from . import llm, llm_openai
from .config import Settings, get_settings
from .db import (
    Analysis,
    AnalysisMetric,
    CachedReview,
    DailySpend,
    Job,
    Snapshot,
    UsageLog,
    make_engine,
    make_session_factory,
    utcnow,
)
from .scraper import client as scraper_client
from .scraper.errors import ScraperError

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
    row.spent_usd += estimated
    session.commit()
    return True


def reconcile_spend(session: Session, delta: float) -> None:
    """Adjust today's reserved spend by `delta` (actual − estimate, or release on failure)."""
    if not delta:
        return
    row = session.get(DailySpend, _utc_day(), with_for_update=True)
    if row is not None:
        row.spent_usd = max(0.0, row.spent_usd + delta)
        session.commit()


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


def ensure_snapshot(session: Session, job: Job, settings: Settings) -> Snapshot:
    """Reuse a fresh snapshot or scrape a new one (24h reuse window, spec §4.1)."""
    snap = (
        session.execute(
            select(Snapshot).where(
                Snapshot.app_id == job.app_id,
                Snapshot.country == job.country,
                Snapshot.lang == job.lang,
            )
        )
        .scalars()
        .first()
    )
    if snap is not None and snap.expires_at > utcnow():
        return snap

    app_info = scraper_client.get_app(job.app_id, country=job.country, lang=job.lang)

    def on_page(count: int) -> None:
        job.progress = count
        session.commit()

    result = scraper_client.fetch_reviews(
        job.app_id,
        country=job.country,
        lang=job.lang,
        max_reviews=settings.max_reviews_per_analysis,
        delay_seconds=settings.scrape_delay_seconds,
        on_page=on_page,
    )

    if snap is None:
        snap = Snapshot(app_id=job.app_id, country=job.country, lang=job.lang, expires_at=utcnow())
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
    session.add(UsageLog(job_id=job.id, event="scrape", cost_usd=0.0))
    session.commit()
    return snap


def _analyze(session: Session, job: Job, snap: Snapshot, settings: Settings) -> Analysis:
    cached = (
        session.execute(
            select(Analysis).where(
                Analysis.app_id == job.app_id,
                Analysis.country == job.country,
                Analysis.lang == job.lang,
                Analysis.question_hash == job.question_hash,
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
    # Headline sentiment from the LIFETIME histogram (true population) when available,
    # else the recency-biased sample — and flag if the sample diverges materially (N2).
    sample_sentiment = an.star_sentiment(r.score for r in rows)
    sentiment = (
        an.sentiment_from_histogram((snap.app_meta or {}).get("histogram")) or sample_sentiment
    )
    sentiment_divergence = None
    if sentiment["source"] == "lifetime_histogram":
        gap = abs(sentiment["positive_pct"] - sample_sentiment["positive_pct"])
        if gap >= 15:
            skew = (
                "more negative"
                if sample_sentiment["positive_pct"] < sentiment["positive_pct"]
                else "more positive"
            )
            sentiment_divergence = (
                f"the {snap.review_count} sampled reviews skew {skew} "
                f"than the lifetime ratings (by {round(gap)} pts)"
            )

    curated = an.curate_reviews(reviews, question=job.question)  # question-aware (N3)
    lines = an.format_review_lines(curated)

    started = utcnow()
    llm_fallback = False
    provider = settings.provider()
    if provider == "stub":
        payload = an.stub_analysis(job.question, curated)
        usage = {"tokens_in": 0, "tokens_out": 0}
        model, cost, event = "stub", 0.0, "stub"
    else:
        model = settings.llm_model if provider == "openai_compatible" else settings.gemini_model
        # Estimate with the provider's real output ceiling (Grok reasoning uses the bigger
        # OCI budget), then ATOMICALLY reserve against the daily cap before spending (§4.4).
        max_out = (
            llm_openai.OCI_MAX_TOKENS if provider == "openai_compatible" else llm.MAX_OUTPUT_TOKENS
        )
        estimated = llm.cost_usd(model, llm.estimate_tokens(lines), max_out)
        if not reserve_spend(session, estimated, settings.global_daily_spend_cap_usd):
            raise SpendCapExceeded(
                f"daily LLM spend cap (${settings.global_daily_spend_cap_usd}) reached"
            )
        try:
            if provider == "openai_compatible":
                payload, usage = llm_openai.openai_compatible_analyze(
                    job.question,
                    lines,
                    base_url=settings.llm_base_url,
                    api_key=settings.llm_api_key,
                    compartment_id=settings.llm_compartment_id,
                    model=model,
                )
            else:  # gemini
                payload, usage = llm.gemini_analyze(
                    job.question, lines, api_key=settings.gemini_api_key, model=model
                )
            payload = an.verify_quotes(
                payload, {str(r["review_id"]): str(r["text"] or "") for r in reviews}
            )
            real_cost = usage.get("cost_usd")  # prefer provider's real billed cost (OCI ticks)
            cost = (
                float(real_cost)
                if real_cost is not None
                else llm.cost_usd(model, usage["tokens_in"], usage["tokens_out"])
            )
            reconcile_spend(session, cost - estimated)  # true-up reserved → actual
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
    sentiment_note = (
        "sentiment % is from the app's lifetime star ratings"
        if sentiment["source"] == "lifetime_histogram"
        else f"sentiment % is from the {sentiment['counted']} sampled reviews' stars"
    )
    payload["data_quality"] = (
        f"Based on {len(curated)} of {snap.review_count} fetched reviews "
        f"(sort={snap.sort}, {job.lang}/{job.country}, fetched {snap.fetched_at:%Y-%m-%d %H:%M} UTC"
        f"{', INCOMPLETE fetch' if not snap.complete else ''}); {sentiment_note}."
    )
    if sentiment_divergence:
        payload["caveats"] = [*payload.get("caveats", []), f"Note: {sentiment_divergence}."]

    record = Analysis(
        app_id=job.app_id,
        country=job.country,
        lang=job.lang,
        question_hash=job.question_hash,
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


def process_one(session_factory, settings: Settings | None = None) -> bool:
    """Claim and fully process one job. Returns False when the queue is empty."""
    settings = settings or get_settings()
    with session_factory() as session:
        job = claim_next_job(session)
        if job is None:
            return False
        try:
            snap = ensure_snapshot(session, job, settings)
            job.snapshot_id = snap.id
            job.status = "analyzing"
            session.commit()
            record = _analyze(session, job, snap, settings)
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


def run_loop(session_factory, stop: threading.Event | None = None, interval: float = 1.0) -> None:
    stop = stop or threading.Event()
    logger.info("worker loop started")
    while not stop.is_set():
        if not process_one(session_factory):
            stop.wait(interval)


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
