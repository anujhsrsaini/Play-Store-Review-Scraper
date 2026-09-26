"""Tests for the Battle Lens (competitor comparison) persistence models.

Covers the schema introduced for Phase 7.2: a ``ComparisonJob`` tracks the
async compare workflow, and ``ComparisonResult`` caches a finished comparison
keyed by the canonical pair + snapshot + focus hashes (dedupe via the unique
constraint). The compare API/worker that writes these rows is still pending;
these tests pin the storage contract it will build on.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from playstore_review_service.db import (
    ComparisonJob,
    ComparisonResult,
    init_db,
    make_engine,
    make_session_factory,
)


@pytest.fixture
def comparison_session(tmp_path):
    db_path = tmp_path / "comparison_test.db"
    engine = make_engine(f"sqlite:///{db_path}")
    init_db(engine)
    return make_session_factory(engine)


def _job(**overrides) -> ComparisonJob:
    params = {
        "app_a_id": "com.example.a",
        "app_b_id": "com.example.b",
        "canonical_pair_hash": "pairhash",
        "lookback_days": 90,
    }
    params.update(overrides)
    return ComparisonJob(**params)


def _result(**overrides) -> ComparisonResult:
    params = {
        "app_a_id": "com.example.a",
        "app_b_id": "com.example.b",
        "canonical_pair_hash": "pairhash",
        "snapshots_hash": "snapshothash",
        "result": {"winner": "a", "themes": []},
    }
    params.update(overrides)
    return ComparisonResult(**params)


def test_comparison_job_round_trip(comparison_session):
    session = comparison_session()
    session.add(_job())
    session.commit()
    fetched = session.execute(select(ComparisonJob)).scalars().one()
    assert fetched.status == "queued"
    assert fetched.progress_percent == 0
    assert fetched.app_a_id == "com.example.a"
    session.close()


def test_comparison_result_links_to_job(comparison_session):
    session = comparison_session()
    session.add(_job(id="job123"))
    session.add(_result())
    session.commit()
    res = session.execute(select(ComparisonResult)).scalars().one()
    job = session.execute(select(ComparisonJob).where(ComparisonJob.id == "job123")).scalars().one()
    job.comparison_result_id = res.id
    job.status = "complete"
    session.commit()
    refetched = (
        session.execute(select(ComparisonJob).where(ComparisonJob.id == "job123")).scalars().one()
    )
    assert refetched.comparison_result_id == res.id
    session.close()


def test_comparison_result_dedupes_on_content_hash(comparison_session):
    """Identical (pair, snapshots, focus) content inserts twice → unique violation,
    so concurrent compares for the same inputs cannot fork duplicate results."""
    session = comparison_session()
    session.add(_result())
    session.commit()
    session.add(_result())
    with pytest.raises(IntegrityError):
        session.commit()
    session.close()
