"""Privacy retention (Plans 3.5): TTL purge of stale rows + per-user data deletion.

Ownership model:
- User-owned: ``User`` rows, ``Job`` rows (carry the free-form question + owner id),
  ``ComparisonJob`` rows (carry the compared app ids + focus + owner id), and the
  ``UsageLog`` / ``AnalysisMetric`` rows linked to those jobs by ``job_id``.
- Shared cache (NOT user-owned): ``Snapshot`` / ``CachedReview`` / ``Analysis`` /
  ``ComparisonResult`` rows are de-identified content keyed by hashes and reused
  across users, so per-user deletion never touches them. Expired snapshots are
  reclaimed by the TTL purge only when no cached analysis still references them.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .db import (
    Analysis,
    AnalysisMetric,
    CachedReview,
    ComparisonJob,
    Job,
    Snapshot,
    UsageLog,
    User,
    utcnow,
)

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = ("done", "error")


def purge_expired(
    session: Session, now: datetime | None = None, job_retention_days: int = 90
) -> dict[str, int]:
    """Delete stale rows. Returns counts per table for observability.

    - Terminal jobs older than ``job_retention_days`` + their job-linked log/metric rows.
      (Today's quota counts only today's jobs, so old rows are dead weight.)
    - Expired snapshots with no referencing analysis + their cached reviews.
    """
    now = now or utcnow()
    counts = {
        "jobs": 0,
        "comparison_jobs": 0,
        "usage_log": 0,
        "analysis_metrics": 0,
        "snapshots": 0,
        "reviews": 0,
    }

    cutoff = now - timedelta(days=job_retention_days)
    old_job_ids = (
        session.execute(
            select(Job.id).where(Job.status.in_(TERMINAL_STATUSES), Job.created_at < cutoff)
        )
        .scalars()
        .all()
    )
    old_compare_ids = (
        session.execute(
            select(ComparisonJob.id).where(
                ComparisonJob.status.in_(("done", "error")), ComparisonJob.created_at < cutoff
            )
        )
        .scalars()
        .all()
    )
    linked_ids = [*old_job_ids, *old_compare_ids]
    if linked_ids:
        for table, key in ((UsageLog, "usage_log"), (AnalysisMetric, "analysis_metrics")):
            res = session.execute(delete(table).where(table.job_id.in_(linked_ids)))
            counts[key] = res.rowcount or 0
    if old_job_ids:
        res = session.execute(delete(Job).where(Job.id.in_(old_job_ids)))
        counts["jobs"] = res.rowcount or 0
    if old_compare_ids:
        res = session.execute(delete(ComparisonJob).where(ComparisonJob.id.in_(old_compare_ids)))
        counts["comparison_jobs"] = res.rowcount or 0

    referenced_snap_ids = select(Analysis.snapshot_id).distinct()
    expired_snaps = (
        session.execute(
            select(Snapshot.id).where(
                Snapshot.expires_at < now, Snapshot.id.not_in(referenced_snap_ids)
            )
        )
        .scalars()
        .all()
    )
    if expired_snaps:
        res = session.execute(
            delete(CachedReview).where(CachedReview.snapshot_id.in_(expired_snaps))
        )
        counts["reviews"] = res.rowcount or 0
        res = session.execute(delete(Snapshot).where(Snapshot.id.in_(expired_snaps)))
        counts["snapshots"] = res.rowcount or 0

    session.commit()
    logger.info("retention purge: %s", counts)
    return counts


def delete_user_data(session: Session, user_id: str) -> dict[str, int]:
    """Delete everything owned by one identity: their jobs and compare jobs (+ linked
    log/metric rows) and their ``User`` row. Shared snapshot/analysis/comparison
    cache rows are untouched."""
    counts = {"jobs": 0, "comparison_jobs": 0, "usage_log": 0, "analysis_metrics": 0, "users": 0}
    job_ids = session.execute(select(Job.id).where(Job.user_id == user_id)).scalars().all()
    compare_ids = (
        session.execute(select(ComparisonJob.id).where(ComparisonJob.user_id == user_id))
        .scalars()
        .all()
    )
    linked_ids = [*job_ids, *compare_ids]
    if linked_ids:
        for table, key in ((UsageLog, "usage_log"), (AnalysisMetric, "analysis_metrics")):
            res = session.execute(delete(table).where(table.job_id.in_(linked_ids)))
            counts[key] = res.rowcount or 0
    if job_ids:
        res = session.execute(delete(Job).where(Job.id.in_(job_ids)))
        counts["jobs"] = res.rowcount or 0
    if compare_ids:
        res = session.execute(delete(ComparisonJob).where(ComparisonJob.id.in_(compare_ids)))
        counts["comparison_jobs"] = res.rowcount or 0
    res = session.execute(delete(User).where(User.id == user_id))
    counts["users"] = res.rowcount or 0
    session.commit()
    logger.info("retention delete-my-data user=%s: %s", user_id, counts)
    return counts
