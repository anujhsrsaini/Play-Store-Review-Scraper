"""End-to-end service tests: API -> queue -> worker -> result, with a mocked scraper.

No network, no LLM key (stub analyzer path), tmp-file SQLite per test session.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

import playstore_review_service.config as config_mod
from playstore_review_service.db import UsageLog
from playstore_review_service.scraper.client import FetchResult
from playstore_review_service.scraper.errors import AppNotFound
from playstore_review_service.scraper.models import AppInfo, Review
from playstore_review_service.worker import SpendCapExceeded, process_one

APP_ID = "com.example.calc"


def _fake_reviews(n: int = 30) -> list[Review]:
    out = []
    for i in range(n):
        score = (i % 5) + 1
        out.append(
            Review(
                review_id=f"r{i}",
                score=score,
                text=("keeps crashing constantly" if score <= 2 else "love the clean design"),
                created_at=f"2026-05-{(i % 28) + 1:02d}T10:00:00",
                app_version="4.3.1",
                thumbs_up=i,
            )
        )
    return out


def _fake_get_app(app_id, **kw):
    if app_id == "com.missing.app":
        raise AppNotFound(app_id)
    return AppInfo(
        app_id=app_id,
        title="Example Calc",
        score=4.3,
        ratings=1000,
        histogram=[10, 20, 30, 40, 100],
        installs="1,000,000+",
        icon="https://example.com/icon.png",
    )


def _fake_fetch_reviews(app_id, *, country="us", lang="en", max_reviews=500, on_page=None, **kw):
    reviews = _fake_reviews(30)
    if on_page:
        on_page(len(reviews))
    return FetchResult(
        app_id=app_id,
        country=country,
        lang=lang,
        sort="NEWEST",
        requested=max_reviews,
        reviews=reviews,
        complete=True,
    )


def _fake_search(query, **kw):
    return [
        AppInfo(
            app_id=APP_ID,
            title="Example Calc",
            score=4.3,
            installs="1M+",
            icon="https://example.com/icon.png",
        )
    ]


@contextmanager
def build_service(tmp_path, monkeypatch, **env):
    """Build a TestClient + session_factory on tmp SQLite with a fully mocked scraper.

    Auth and all LLM provider vars are blanked (hermetic; stub analyzer, auth disabled →
    local user) unless overridden via **env. Used by the `service` fixture and tests that
    need custom settings (low quota, auth enabled)."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("DEV_INPROCESS_WORKER", "0")
    for var in (
        "GEMINI_API_KEY",
        "LLM_PROVIDER",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_COMPARTMENT_ID",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_REDIRECT_URI",
    ):
        monkeypatch.setenv(var, "")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config_mod.get_settings.cache_clear()

    import playstore_review_service.webapp as webapp_mod
    import playstore_review_service.worker as worker_mod

    for mod in (webapp_mod, worker_mod):
        monkeypatch.setattr(mod.scraper_client, "get_app", _fake_get_app, raising=True)
        monkeypatch.setattr(mod.scraper_client, "fetch_reviews", _fake_fetch_reviews, raising=True)
        monkeypatch.setattr(mod.scraper_client, "search_apps", _fake_search, raising=True)

    from playstore_review_service.db import make_engine, make_session_factory

    app = webapp_mod.create_app()
    with TestClient(app) as client:
        engine = make_engine(config_mod.get_settings().database_url)
        yield client, make_session_factory(engine)
    config_mod.get_settings.cache_clear()


@pytest.fixture()
def service(tmp_path, monkeypatch):
    with build_service(tmp_path, monkeypatch) as svc:
        yield svc


def test_health(service):
    client, _sf = service
    body = client.get("/api/health").json()
    assert body["ok"] is True and "stub" in body["llm"]


def test_search_endpoint(service):
    client, _sf = service
    apps = client.get("/api/search", params={"q": "calculator"}).json()
    assert apps[0]["app_id"] == APP_ID
    assert apps[0]["icon"] == "https://example.com/icon.png"  # surfaced for the UI


def test_share_permalink_returns_cached_analysis(service):
    client, sf = service
    body = client.post("/api/analyze", json={"app_id": APP_ID, "question": "shareable?"}).json()
    process_one(sf)
    done = client.get(f"/api/jobs/{body['job_id']}").json()["result"]
    analysis_id = done["analysis_id"]
    assert done["app"]["icon"] == "https://example.com/icon.png"
    assert done["app"]["histogram"] == [10, 20, 30, 40, 100]
    # permalink fetch returns the same analysis
    shared = client.get(f"/api/analysis/{analysis_id}").json()
    assert shared["analysis_id"] == analysis_id
    assert shared["answer"]["summary"] == done["answer"]["summary"]
    assert client.get("/api/analysis/999999").status_code == 404


def test_search_rejects_empty_query(service):
    client, _sf = service
    assert client.get("/api/search", params={"q": "  "}).status_code == 422


def test_analyze_full_flow_submit_poll_result(service):
    client, sf = service
    res = client.post("/api/analyze", json={"app_id": APP_ID, "question": "What do users hate?"})
    body = res.json()
    assert body["status"] == "queued" and body["cache_hit"] is False
    job_id = body["job_id"]

    assert process_one(sf) is True  # run the worker once, synchronously

    out = client.get(f"/api/jobs/{job_id}").json()
    assert out["status"] == "done"
    result = out["result"]
    assert result["app"]["title"] == "Example Calc"
    assert result["snapshot"]["review_count"] == 30
    answer = result["answer"]
    assert answer["sentiment_breakdown"]["source"] == "star_ratings"
    assert answer["sentiment_breakdown"]["counted"] == 30
    assert "data_quality" in answer
    assert answer["summary"]


def test_analyze_cache_hit_on_second_identical_question(service):
    client, sf = service
    first = client.post(
        "/api/analyze", json={"app_id": APP_ID, "question": "Top complaints?"}
    ).json()
    process_one(sf)
    again = client.post(
        "/api/analyze", json={"app_id": APP_ID, "question": "  top complaints "}
    ).json()
    assert again["cache_hit"] is True and again["status"] == "done"
    assert again["result"]["answer"]["summary"]
    assert first["job_id"] is not None and again["job_id"] is None


def test_analyze_coalesces_identical_active_jobs(service):
    client, _sf = service
    q = {"app_id": APP_ID, "question": "Is the app buggy?"}
    first = client.post("/api/analyze", json=q).json()
    second = client.post("/api/analyze", json=q).json()
    assert second["job_id"] == first["job_id"]  # single-flight: same job returned


def test_analyze_rejects_bad_app_id(service):
    client, _sf = service
    res = client.post("/api/analyze", json={"app_id": "com/evil;rm", "question": "hi there"})
    assert res.status_code == 422


def test_job_error_classified_for_missing_app(service):
    client, sf = service
    body = client.post(
        "/api/analyze", json={"app_id": "com.missing.app", "question": "anything wrong?"}
    ).json()
    process_one(sf)
    out = client.get(f"/api/jobs/{body['job_id']}").json()
    assert out["status"] == "error"
    assert out["error"] == "AppNotFound"  # classified type, no raw message


def test_job_not_found_and_invalid_id(service):
    client, _sf = service
    assert client.get("/api/jobs/deadbeefdeadbeefdeadbeefdeadbeef").status_code == 404
    assert client.get("/api/jobs/--bad--").status_code == 422


def test_spend_cap_blocks_gemini_calls(service, monkeypatch):
    """With a key set and the cap already consumed, the job fails clearly — no LLM call."""
    client, sf = service
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-never-used")
    monkeypatch.setenv("GLOBAL_DAILY_SPEND_CAP_USD", "0")
    config_mod.get_settings.cache_clear()

    import playstore_review_service.worker as worker_mod

    def must_not_be_called(*a, **kw):
        raise AssertionError("gemini_analyze must not be called past the spend cap")

    monkeypatch.setattr(worker_mod.llm, "gemini_analyze", must_not_be_called)

    body = client.post(
        "/api/analyze", json={"app_id": APP_ID, "question": "does the cap work?"}
    ).json()
    process_one(sf)
    out = client.get(f"/api/jobs/{body['job_id']}").json()
    assert out["status"] == "error"
    assert "spend cap" in out["error"]
    config_mod.get_settings.cache_clear()


def test_openai_compatible_provider_used_when_configured(service, monkeypatch):
    """With OCI env configured, the worker routes through the OpenAI-compatible adapter,
    records the model, and still applies quote verification."""
    client, sf = service
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_API_KEY", "fake-token")
    monkeypatch.setenv("LLM_BASE_URL", "https://oci.example/openai/v1")
    monkeypatch.setenv("LLM_COMPARTMENT_ID", "ocid1.tenancy.oc1..test")
    monkeypatch.setenv("LLM_CHEAP_MODEL", "xai.grok-3-mini")
    config_mod.get_settings.cache_clear()

    import playstore_review_service.worker as worker_mod

    calls: dict = {}

    def fake_oci(question, lines, *, base_url, api_key, compartment_id, model, **kw):
        calls["model"] = model
        calls["compartment_id"] = compartment_id
        return (
            {
                "summary": "OCI Grok: crashes dominate the negative reviews.",
                "not_enough_data": False,
                "themes": [],
                # real substring of a fixture review (r0 is 1★ "keeps crashing constantly")
                "supporting_quotes": [
                    {"id": "r0", "quote": "keeps crashing constantly", "stars": 1},
                    {"id": "r0", "quote": "this quote is fabricated", "stars": 1},
                ],
                "caveats": [],
            },
            {"tokens_in": 100, "tokens_out": 20},
        )

    monkeypatch.setattr(worker_mod.llm_openai, "openai_compatible_analyze", fake_oci)

    body = client.post("/api/analyze", json={"app_id": APP_ID, "question": "what is wrong?"}).json()
    process_one(sf)
    out = client.get(f"/api/jobs/{body['job_id']}").json()

    assert out["status"] == "done"
    assert calls["model"] == "xai.grok-3-mini"
    assert calls["compartment_id"] == "ocid1.tenancy.oc1..test"
    answer = out["result"]["answer"]
    assert out["result"]["model"] == "xai.grok-3-mini"
    assert answer["summary"].startswith("OCI Grok")
    # quote verification still runs: the fabricated quote is dropped, the real one kept
    assert [q["quote"] for q in answer["supporting_quotes"]] == ["keeps crashing constantly"]
    assert answer["sentiment_breakdown"]["source"] == "star_ratings"
    config_mod.get_settings.cache_clear()


def test_usage_log_records_stub_event(service):
    client, sf = service
    client.post("/api/analyze", json={"app_id": APP_ID, "question": "log this run?"})
    process_one(sf)
    with sf() as session:
        events = {u.event for u in session.query(UsageLog).all()}
    assert "scrape" in events and "stub" in events


def test_spend_cap_exception_importable():
    assert issubclass(SpendCapExceeded, Exception)


def test_index_served(service):
    client, _sf = service
    res = client.get("/")
    assert res.status_code == 200 and "text/html" in res.headers["content-type"]


def test_spa_client_routes_and_api_separation(service):
    from playstore_review_service.webapp import SPA_DIR

    client, _sf = service
    if not (SPA_DIR / "index.html").exists():
        pytest.skip("React SPA not built (run: cd frontend && npm run build)")
    assert '<div id="root">' in client.get("/").text  # React app at root
    assert '<div id="root">' in client.get("/a/123").text  # client route → SPA fallback
    assert client.get("/api/does-not-exist").status_code == 404  # API not shadowed by SPA


# --------------------------------------------------------------- auth + quota


def test_me_reports_quota_and_usage(service):
    client, sf = service
    me = client.get("/api/me").json()
    assert me["authenticated"] is False  # local dev mode
    assert me["used"] == 0 and me["remaining"] == me["quota"]
    # a cache-miss analysis consumes one
    client.post("/api/analyze", json={"app_id": APP_ID, "question": "uses one?"})
    me2 = client.get("/api/me").json()
    assert me2["used"] == 1 and me2["remaining"] == me2["quota"] - 1


def test_cached_reask_does_not_consume_quota(service):
    client, sf = service
    client.post("/api/analyze", json={"app_id": APP_ID, "question": "free reask?"})
    process_one(sf)
    used_before = client.get("/api/me").json()["used"]
    again = client.post("/api/analyze", json={"app_id": APP_ID, "question": "  FREE reask "}).json()
    assert again["cache_hit"] is True
    assert client.get("/api/me").json()["used"] == used_before  # no extra consumption


def test_per_user_quota_returns_429(tmp_path, monkeypatch):
    with build_service(tmp_path, monkeypatch, PER_USER_DAILY_ANALYSES="2") as (client, _sf):
        ok1 = client.post("/api/analyze", json={"app_id": APP_ID, "question": "first one"})
        ok2 = client.post("/api/analyze", json={"app_id": APP_ID, "question": "second one"})
        blocked = client.post("/api/analyze", json={"app_id": APP_ID, "question": "third one"})
        assert ok1.status_code == 200 and ok2.status_code == 200
        assert blocked.status_code == 429
        assert "daily limit" in blocked.json()["detail"]


def test_auth_required_when_oauth_configured(tmp_path, monkeypatch):
    with build_service(
        tmp_path,
        monkeypatch,
        GOOGLE_CLIENT_ID="fake-client-id",
        GOOGLE_CLIENT_SECRET="fake-client-secret",
    ) as (client, _sf):
        assert client.get("/api/health").json()["auth"] is True
        # no session → protected endpoints 401, public ones still work
        assert (
            client.post("/api/analyze", json={"app_id": APP_ID, "question": "blocked?"}).status_code
            == 401
        )
        assert client.get("/api/search", params={"q": "x"}).status_code == 401
        assert client.get("/").status_code == 200  # landing is public
        assert client.get("/api/health").status_code == 200
