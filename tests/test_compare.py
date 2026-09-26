"""Tests for Battle Lens compare routes: validation, canonical coalescing,
fresh-result cache hits, quota-on-miss, owner-only status, and permalinks.

The compare worker (which fulfills queued jobs) is covered separately; these
tests pin the API contract it builds on. Scraper calls are mocked via
test_service.build_service.
"""

from __future__ import annotations

from datetime import timedelta

from playstore_review_service import analysis as an
from playstore_review_service.db import ComparisonResult, Snapshot, utcnow
from test_service import APP_ID, build_service

APP_B = "com.example.other"


def _payload(app_a=APP_ID, app_b=APP_B, **kw):
    body = {
        "app_a_id": app_a,
        "app_b_id": app_b,
        "country": "us",
        "lang": "en",
        "lookback_days": 90,
        "custom_focus": "",
    }
    body.update(kw)
    return body


def test_compare_queues_job_and_polls_queued(tmp_path, monkeypatch):
    with build_service(tmp_path, monkeypatch) as (client, _sf):
        res = client.post("/api/compare", json=_payload())
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "queued" and body["cache_hit"] is False
        status = client.get(f"/api/compare/{body['job_id']}").json()
        assert status["status"] == "queued"
        assert status["progress_stage"] == "queued"


def test_compare_rejects_bad_and_identical_ids(tmp_path, monkeypatch):
    with build_service(tmp_path, monkeypatch) as (client, _sf):
        bad_id = client.post("/api/compare", json=_payload(app_a="not valid!!"))
        assert bad_id.status_code == 422
        same_id = client.post("/api/compare", json=_payload(app_a=APP_ID, app_b=APP_ID))
        assert same_id.status_code == 422
        assert client.get("/api/compare/not-a-job!!").status_code == 422
        assert client.get("/api/compare/" + "0" * 33).status_code == 422
        assert client.get("/api/compare/" + "f" * 32).status_code == 404
        assert client.get("/api/compare/result/short").status_code == 404


def test_compare_canonical_order_coalesces(tmp_path, monkeypatch):
    """A-vs-B and B-vs-A are the same comparison: one job, same id."""
    with build_service(tmp_path, monkeypatch) as (client, _sf):
        first = client.post("/api/compare", json=_payload()).json()
        second = client.post("/api/compare", json=_payload(app_a=APP_B, app_b=APP_ID)).json()
        assert second["job_id"] == first["job_id"]
        assert second["cache_hit"] is False
        focused = client.post("/api/compare", json=_payload(custom_focus="battery")).json()
        assert focused["job_id"] != first["job_id"]  # different focus = different job


def test_compare_quota_429_on_miss(tmp_path, monkeypatch):
    with build_service(tmp_path, monkeypatch, PER_USER_DAILY_ANALYSES="1") as (client, _sf):
        assert client.post("/api/compare", json=_payload()).status_code == 200
        blocked = client.post("/api/compare", json=_payload(custom_focus="camera"))
        assert blocked.status_code == 429
        assert "daily limit" in blocked.json()["detail"]


def test_compare_worker_empty_queue_returns_false(tmp_path, monkeypatch):
    from playstore_review_service.worker import process_compare_one

    with build_service(tmp_path, monkeypatch) as (_client, sf):
        assert process_compare_one(sf) is False


def test_compare_worker_completes_stub_job(tmp_path, monkeypatch):
    from playstore_review_service.worker import process_compare_one

    with build_service(tmp_path, monkeypatch) as (client, sf):
        job_id = client.post("/api/compare", json=_payload()).json()["job_id"]
        assert process_compare_one(sf) is True
        assert process_compare_one(sf) is False  # queue drained
        done = client.get(f"/api/compare/{job_id}").json()
        assert done["status"] == "done"
        assert done["progress_percent"] == 100
        result = done["result"]
        assert result["comparison"]["winner"] in ("a", "b", "tie", None)
        assert result["comparison"]["not_enough_data"] is False
        assert len(result["share_token"]) == 32  # signed-in: shareable


def test_compare_worker_llm_failure_falls_back_to_stub(tmp_path, monkeypatch):
    from playstore_review_service.worker import process_compare_one

    with build_service(
        tmp_path,
        monkeypatch,
        LLM_BASE_URL="http://127.0.0.1:1",  # nothing listens: fast connection-refused
        LLM_API_KEY="test-key",
        LLM_MODEL="test-model",
    ) as (client, sf):
        job_id = client.post("/api/compare", json=_payload()).json()["job_id"]
        assert process_compare_one(sf) is True
        done = client.get(f"/api/compare/{job_id}").json()
        assert done["status"] == "done"
        caveats = done["result"]["comparison"].get("caveats") or []
        assert any("keyword-based fallback" in c for c in caveats)


def test_compare_worker_spend_cap_errors_job(tmp_path, monkeypatch):
    from playstore_review_service.worker import process_compare_one

    with build_service(
        tmp_path,
        monkeypatch,
        LLM_BASE_URL="http://127.0.0.1:1",
        LLM_API_KEY="test-key",
        LLM_MODEL="test-model",
        GLOBAL_DAILY_SPEND_CAP_USD="0",
    ) as (client, sf):
        job_id = client.post("/api/compare", json=_payload()).json()["job_id"]
        assert process_compare_one(sf) is True
        done = client.get(f"/api/compare/{job_id}").json()
        assert done["status"] == "error"


def test_compare_cache_hit_on_fresh_result_and_permalink(tmp_path, monkeypatch):
    with build_service(tmp_path, monkeypatch) as (client, sf):
        a_id, b_id = sorted((APP_ID, APP_B))
        with sf() as s:
            fresh_until = utcnow() + timedelta(hours=12)
            fetched = utcnow()
            snap_a = Snapshot(
                app_id=a_id, country="us", lang="en", fetched_at=fetched, expires_at=fresh_until
            )
            snap_b = Snapshot(
                app_id=b_id, country="us", lang="en", fetched_at=fetched, expires_at=fresh_until
            )
            s.add_all([snap_a, snap_b])
            s.flush()
            pair_hash = an.canonical_pair_hash(a_id, b_id)
            snaps_hash = an.snapshots_hash(
                a_id,
                snap_a.id,
                snap_a.fetched_at.isoformat(),
                b_id,
                snap_b.id,
                snap_b.fetched_at.isoformat(),
            )
            s.add(
                ComparisonResult(
                    app_a_id=a_id,
                    app_b_id=b_id,
                    canonical_pair_hash=pair_hash,
                    country="us",
                    lang="en",
                    lookback_days=90,
                    snapshots_hash=snaps_hash,
                    custom_focus_hash=an.custom_focus_hash(None),
                    result={"winner": "a", "themes": []},
                )
            )
            s.commit()
        body = client.post("/api/compare", json=_payload()).json()
        assert body["status"] == "done" and body["cache_hit"] is True
        assert body["result"]["comparison"] == {"winner": "a", "themes": []}
        assert len(body["result"]["share_token"]) == 32
        shared = client.get(f"/api/compare/result/{body['result']['share_token']}").json()
        assert shared["comparison"] == {"winner": "a", "themes": []}
        assert shared["app_a_id"] == a_id
