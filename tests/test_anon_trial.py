"""Tests for the anonymous no-login free trial (try-before-you-sign-in)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from playstore_review_service.db import UsageLog
from playstore_review_service.worker import process_one
from test_service import APP_ID, build_service

# Enabling the trial requires auth to be ON (something to gate into) + a non-default secret.
AUTH = {
    "GOOGLE_CLIENT_ID": "test-client",
    "GOOGLE_CLIENT_SECRET": "test-secret",
    "SESSION_SECRET": "test-session-secret-not-the-default",
}


def _analyze(client, question, app_id=APP_ID):
    return client.post("/api/analyze", json={"app_id": app_id, "question": question})


def test_health_and_me_expose_anon_trial(tmp_path, monkeypatch):
    with build_service(
        tmp_path, monkeypatch, ANON_TRIAL_ENABLED="1", ANON_DAILY_ANALYSES="1", **AUTH
    ) as (client, _sf):
        h = client.get("/api/health").json()
        assert h["auth"] is True and h["anon_trial"] is True
        me = client.get("/api/me").json()
        assert me["authenticated"] is False and me["is_anon"] is True
        assert me["quota"] == 1 and me["remaining"] == 1


def test_me_401_when_trial_off(tmp_path, monkeypatch):
    # Auth on, trial off → preserves today's "login required everywhere" behavior.
    with build_service(tmp_path, monkeypatch, ANON_TRIAL_ENABLED="0", **AUTH) as (client, _sf):
        assert client.get("/api/me").status_code == 401
        assert client.get("/api/health").json()["anon_trial"] is False


def test_anon_one_then_gated(tmp_path, monkeypatch):
    with build_service(
        tmp_path, monkeypatch, ANON_TRIAL_ENABLED="1", ANON_DAILY_ANALYSES="1", **AUTH
    ) as (client, sf):
        r1 = _analyze(client, "what do users complain about?")
        assert r1.status_code == 200 and r1.json()["status"] == "queued"
        assert process_one(sf) is True
        assert client.get("/api/me").json()["remaining"] == 0
        # a second DISTINCT question is gated → 429 (sign in for more)
        assert _analyze(client, "a different question about speed").status_code == 429


def test_anon_cached_reask_is_free_even_when_trial_used(tmp_path, monkeypatch):
    with build_service(
        tmp_path, monkeypatch, ANON_TRIAL_ENABLED="1", ANON_DAILY_ANALYSES="1", **AUTH
    ) as (client, sf):
        q = "what do users love?"
        assert _analyze(client, q).status_code == 200
        process_one(sf)
        # trial used, but re-asking the SAME question hits the cache → free, not 429
        again = _analyze(client, q).json()
        assert again["cache_hit"] is True and again["status"] == "done"
        # anon results must NOT leak a share token (sharing is a signed-in feature)
        assert "share_token" not in again["result"]
        # a brand-new question is still gated
        assert _analyze(client, "anything about battery drain?").status_code == 429


def test_anon_polls_only_own_job(tmp_path, monkeypatch):
    with build_service(
        tmp_path, monkeypatch, ANON_TRIAL_ENABLED="1", ANON_DAILY_ANALYSES="1", **AUTH
    ) as (client_a, _sf):
        job_id = _analyze(client_a, "own job test").json()["job_id"]
        # a different browser (fresh cookie jar) = a different anon identity
        client_b = TestClient(client_a.app)
        assert client_b.get(f"/api/jobs/{job_id}").status_code == 404
        # the creator can read its own job
        assert client_a.get(f"/api/jobs/{job_id}").status_code == 200


def test_anon_blocked_when_spend_fraction_reached(tmp_path, monkeypatch):
    with build_service(
        tmp_path,
        monkeypatch,
        ANON_TRIAL_ENABLED="1",
        ANON_DAILY_ANALYSES="1",
        GLOBAL_DAILY_SPEND_CAP_USD="1.0",
        ANON_SPEND_FRACTION="0.5",
        **AUTH,
    ) as (client, sf):
        with sf() as s:  # today's spend already at 60% of the cap (> 50% fraction)
            s.add(UsageLog(event="seed", cost_usd=0.6))
            s.commit()
        # anon path is refused (protect signed-in headroom) before consuming the trial
        assert _analyze(client, "blocked by the anon sub-budget?").status_code == 429
