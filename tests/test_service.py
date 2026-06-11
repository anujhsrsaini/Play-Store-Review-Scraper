"""End-to-end service tests: API -> queue -> worker -> result, with a mocked scraper.

No network, no LLM key (stub analyzer path), tmp-file SQLite per test session.
"""

from __future__ import annotations

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


@pytest.fixture()
def service(tmp_path, monkeypatch):
    """A TestClient + session_factory wired to tmp SQLite and a fully mocked scraper."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("DEV_INPROCESS_WORKER", "0")  # tests drive the worker manually
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    config_mod.get_settings.cache_clear()

    import playstore_review_service.webapp as webapp_mod

    def fake_get_app(app_id, **kw):
        if app_id == "com.missing.app":
            raise AppNotFound(app_id)
        return AppInfo(
            app_id=app_id,
            title="Example Calc",
            score=4.3,
            ratings=1000,
            histogram=[10, 20, 30, 40, 100],
            installs="1,000,000+",
        )

    def fake_fetch_reviews(app_id, *, country="us", lang="en", max_reviews=500, on_page=None, **kw):
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

    def fake_search(query, **kw):
        return [AppInfo(app_id=APP_ID, title="Example Calc", score=4.3, installs="1M+")]

    import playstore_review_service.worker as worker_mod

    for mod in (webapp_mod, worker_mod):
        monkeypatch.setattr(mod.scraper_client, "get_app", fake_get_app, raising=True)
        monkeypatch.setattr(mod.scraper_client, "fetch_reviews", fake_fetch_reviews, raising=True)
        monkeypatch.setattr(mod.scraper_client, "search_apps", fake_search, raising=True)

    app = webapp_mod.create_app()
    with TestClient(app) as client:
        from playstore_review_service.db import make_engine, make_session_factory

        engine = make_engine(config_mod.get_settings().database_url)
        yield client, make_session_factory(engine)
    config_mod.get_settings.cache_clear()


def test_health(service):
    client, _sf = service
    body = client.get("/api/health").json()
    assert body["ok"] is True and "stub" in body["llm"]


def test_search_endpoint(service):
    client, _sf = service
    apps = client.get("/api/search", params={"q": "calculator"}).json()
    assert apps[0]["app_id"] == APP_ID


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
    assert res.status_code == 200 and "Play Store Review Analysis" in res.text
