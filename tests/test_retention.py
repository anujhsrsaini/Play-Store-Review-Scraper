"""Tests for privacy retention (Plans 3.5): TTL purge + delete-my-data.

Purge reclaims terminal jobs past the retention window (with their linked
usage/metric rows) and expired snapshots no analysis references (with their
cached reviews). Delete-my-data removes one identity's jobs, linked rows, and
user row — never the shared de-identified cache.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from playstore_review_service.db import (
    Analysis,
    AnalysisMetric,
    CachedReview,
    ComparisonJob,
    ComparisonResult,
    Job,
    Snapshot,
    UsageLog,
    User,
    init_db,
    make_engine,
    make_session_factory,
    new_job_id,
    utcnow,
)
from playstore_review_service.retention import delete_user_data, purge_expired


def _session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/retention_test.db")
    init_db(engine)
    return make_session_factory(engine)


def _job(user_id="u1", status="done", days_old=100):
    return Job(
        id=new_job_id(),
        user_id=user_id,
        app_id="com.example.a",
        country="us",
        lang="en",
        question="q?",
        question_hash="h",
        status=status,
        created_at=utcnow() - timedelta(days=days_old),
    )


def _compare_job(**overrides):
    params = {
        "id": new_job_id(),
        "user_id": "u1",
        "app_a_id": "com.example.a",
        "app_b_id": "com.example.b",
        "canonical_pair_hash": "p",
        "status": "done",
    }
    params.update(overrides)
    return ComparisonJob(**params)


def test_purge_removes_old_terminal_jobs_and_links(tmp_path):
    sf = _session(tmp_path)
    with sf() as s:
        old, recent, active = _job(days_old=100), _job(days_old=10), _job(status="queued")
        old_compare = _compare_job(created_at=utcnow() - timedelta(days=100))
        live_compare = _compare_job(status="fetching", created_at=utcnow() - timedelta(days=100))
        s.add_all([old, recent, active, old_compare, live_compare])
        s.add(UsageLog(job_id=old.id, event="stub", cost_usd=0.1))
        s.add(AnalysisMetric(job_id=old.id))
        s.add(UsageLog(job_id=old_compare.id, event="stub", cost_usd=0.2))
        s.commit()
        counts = purge_expired(s, job_retention_days=90)
    assert counts["jobs"] == 1
    assert counts["comparison_jobs"] == 1
    assert counts["usage_log"] == 2
    assert counts["analysis_metrics"] == 1
    with sf() as s:
        remaining = s.execute(select(Job.id)).scalars().all()
        assert len(remaining) == 2  # recent terminal + active jobs kept
        remaining_compare = s.execute(select(ComparisonJob.id)).scalars().all()
        assert remaining_compare == [live_compare.id]  # active compare kept


def test_purge_reclaims_only_unreferenced_expired_snapshots(tmp_path):
    sf = _session(tmp_path)
    with sf() as s:
        orphan = Snapshot(
            app_id="com.orphan", country="us", lang="en", expires_at=utcnow() - timedelta(hours=1)
        )
        kept = Snapshot(
            app_id="com.kept", country="us", lang="en", expires_at=utcnow() - timedelta(hours=1)
        )
        s.add_all([orphan, kept])
        s.flush()
        s.add(CachedReview(snapshot_id=orphan.id, review_id="r1"))
        s.add(CachedReview(snapshot_id=kept.id, review_id="r2"))
        s.add(
            Analysis(
                app_id="com.kept",
                country="us",
                lang="en",
                question_hash="h",
                snapshot_id=kept.id,
                answer={},
            )
        )
        s.commit()
        counts = purge_expired(s)
    assert counts["snapshots"] == 1
    assert counts["reviews"] == 1
    with sf() as s:
        assert s.execute(select(Snapshot.id)).scalars().all() == [kept.id]
        assert s.execute(select(Analysis.id)).scalars().all() != []


def test_delete_user_data_removes_identity_but_not_shared_cache(tmp_path):
    sf = _session(tmp_path)
    with sf() as s:
        s.add(User(id="u9", email="u9@x.com"))
        job = _job(user_id="u9", days_old=1)
        other = _job(user_id="other", days_old=1)
        compare = _compare_job(user_id="u9")
        s.add_all([job, other, compare])
        s.add(UsageLog(job_id=job.id, event="stub", cost_usd=0.2))
        s.add(UsageLog(job_id=compare.id, event="stub", cost_usd=0.3))
        snap = Snapshot(
            app_id="com.example.a",
            country="us",
            lang="en",
            expires_at=utcnow() + timedelta(hours=1),
        )
        s.add(snap)
        s.flush()
        s.add(
            ComparisonResult(
                app_a_id="com.example.a",
                app_b_id="com.example.b",
                canonical_pair_hash="p",
                snapshots_hash="s",
                result={},
            )
        )
        s.commit()
        counts = delete_user_data(s, "u9")
    assert counts == {
        "jobs": 1,
        "comparison_jobs": 1,
        "usage_log": 2,
        "analysis_metrics": 0,
        "users": 1,
    }
    with sf() as s:
        assert s.execute(select(Job.id)).scalars().all() == [other.id]
        assert s.execute(select(ComparisonJob.id)).scalars().all() == []
        assert s.get(User, "other") is None  # never existed; only u9's row removed
        assert s.execute(select(Snapshot.id)).scalars().all() == [snap.id]  # cache kept
        assert s.execute(select(ComparisonResult.id)).scalars().all() != []  # shared result kept


def test_delete_my_data_endpoint(tmp_path, monkeypatch):
    from playstore_review_service.worker import process_one
    from test_service import APP_ID, build_service

    with build_service(tmp_path, monkeypatch) as (client, sf):
        body = client.post(
            "/api/analyze", json={"app_id": APP_ID, "question": "delete me please?"}
        ).json()
        assert process_one(sf) is True
        assert client.get("/api/me").json()["used"] == 1
        res = client.delete("/api/me/data")
        assert res.status_code == 200
        assert res.json()["deleted"]["jobs"] == 1
        assert res.json()["deleted"]["users"] == 1  # local dev identity row
        assert client.get("/api/me").json()["used"] == 0
        assert client.get(f"/api/jobs/{body['job_id']}").status_code == 404
