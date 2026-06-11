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
from . import llm
from .config import Settings, get_settings
from .db import (
    Analysis,
    CachedReview,
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
    sentiment = an.star_sentiment(r.score for r in rows)
    curated = an.curate_reviews(reviews)
    lines = an.format_review_lines(curated)

    if settings.gemini_api_key:
        estimated = llm.cost_usd(
            settings.gemini_model, llm.estimate_tokens(lines), llm.MAX_OUTPUT_TOKENS
        )
        if today_spend_usd(session) + estimated > settings.global_daily_spend_cap_usd:
            raise SpendCapExceeded(
                f"daily Gemini spend cap (${settings.global_daily_spend_cap_usd}) reached"
            )
        payload, usage = llm.gemini_analyze(
            job.question, lines, api_key=settings.gemini_api_key, model=settings.gemini_model
        )
        payload = an.verify_quotes(
            payload, {str(r["review_id"]): str(r["text"] or "") for r in reviews}
        )
        model = settings.gemini_model
        cost = llm.cost_usd(model, usage["tokens_in"], usage["tokens_out"])
        event = "gemini_call"
    else:
        payload = an.stub_analysis(job.question, curated)
        usage = {"tokens_in": 0, "tokens_out": 0}
        model, cost, event = "stub", 0.0, "stub"

    payload["sentiment_breakdown"] = sentiment
    payload["data_quality"] = (
        f"Based on {len(curated)} of {snap.review_count} fetched reviews "
        f"(sort={snap.sort}, {job.lang}/{job.country}, fetched {snap.fetched_at:%Y-%m-%d %H:%M} UTC"
        f"{', INCOMPLETE fetch' if not snap.complete else ''}). "
        "Review metrics are sample-based; lifetime ratings are point-in-time."
    )

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
        except (SpendCapExceeded, llm.LLMError) as exc:
            job.status = "error"
            job.error = str(exc)[:200]
            logger.warning("job %s: %s", job.id, exc)
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
