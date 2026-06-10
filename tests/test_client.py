"""Tests for the scraper client: honest pagination, the cap, retries, error classification.

The pagination/retry logic is exercised with injected fakes; the library-facing functions
(fetch_reviews / get_app / search_apps) are exercised by monkeypatching the REAL installed
google_play_scraper module — including its actual ``_ContinuationToken`` sentinel, so the
token protocol these tests encode matches what production sees.
"""

from __future__ import annotations

import pytest
from google_play_scraper.exceptions import ExtraHTTPError, NotFoundError
from google_play_scraper.features.reviews import _ContinuationToken

from playstore_review_service.scraper.client import (
    DEFAULT_MAX_REVIEWS,
    HARD_MAX_REVIEWS,
    classify_exception,
    fetch_reviews,
    get_app,
    paginate_reviews,
    search_apps,
    validate_app_id,
    with_retries,
)
from playstore_review_service.scraper.errors import (
    AppNotFound,
    InvalidAppId,
    RateLimitedUpstream,
    ScraperUnavailable,
    ScrapeTimeout,
)


def _live_token(value: str = "tok"):
    return _ContinuationToken(value, "en", "us", 2, 200, None, None)


def _exhausted_token():
    return _ContinuationToken(None, "en", "us", 2, 200, None, None)


def _pager(pages: list[list[dict]]):
    """Fetcher honoring the paginate_reviews contract: next_token=None at exhaustion."""
    state = {"i": 0}

    def fetch_page(_token):
        i = state["i"]
        if i >= len(pages):
            return [], None
        state["i"] += 1
        next_token = "more" if state["i"] < len(pages) else None
        return pages[i], next_token

    return fetch_page


# ---------------------------------------------------------------- paginate_reviews


def test_pagination_returns_honest_count_and_dedupes(reviews_page1, reviews_page2):
    # page1 = r1, r2 ; page2 = r3, r2(dup). Unique across both = r1, r2, r3.
    out, complete = paginate_reviews(
        _pager([reviews_page1, reviews_page2]), max_reviews=100, delay_seconds=0
    )
    assert [r.review_id for r in out] == ["r1", "r2", "r3"]
    assert complete is True


def test_pagination_honors_cap(reviews_page1, reviews_page2):
    out, complete = paginate_reviews(
        _pager([reviews_page1, reviews_page2]), max_reviews=2, delay_seconds=0
    )
    assert [r.review_id for r in out] == ["r1", "r2"]
    assert complete is True


def test_pagination_zero_cap_returns_empty(reviews_page1):
    out, complete = paginate_reviews(_pager([reviews_page1]), max_reviews=0)
    assert out == [] and complete is True


def test_pagination_stops_on_empty_page():
    out, complete = paginate_reviews(_pager([[]]), max_reviews=50, delay_seconds=0)
    assert out == [] and complete is True


def test_pagination_terminates_when_all_rows_are_duplicates():
    """Stale-page guard: a server repeating the same rows with a live token must not
    loop forever (this was a real bug found in review)."""
    calls = {"n": 0}

    def fetch_page(_token):
        calls["n"] += 1
        if calls["n"] > 5:
            raise AssertionError("infinite loop: fetch_page called too many times")
        return [{"reviewId": "r1", "content": "x", "score": 5}], "live-token"

    out, complete = paginate_reviews(fetch_page, max_reviews=100, delay_seconds=0)
    assert [r.review_id for r in out] == ["r1"]
    assert complete is True
    assert calls["n"] == 2  # first page collects r1; second page is stale -> stop


def test_pagination_returns_partial_results_when_later_page_fails(reviews_page1):
    def fetch_page(token):
        if token is None:
            return reviews_page1, "more"
        raise ScraperUnavailable("page 2 blew up")

    out, complete = paginate_reviews(fetch_page, max_reviews=100, delay_seconds=0)
    assert [r.review_id for r in out] == ["r1", "r2"]  # page 1 preserved, not discarded
    assert complete is False


def test_pagination_first_page_failure_propagates():
    def fetch_page(_token):
        raise ScraperUnavailable("down")

    with pytest.raises(ScraperUnavailable):
        paginate_reviews(fetch_page, max_reviews=10, delay_seconds=0)


def test_pagination_sleeps_between_pages_not_before_first(reviews_page1, reviews_page2):
    calls: list[float] = []
    paginate_reviews(
        _pager([reviews_page1, reviews_page2]),
        max_reviews=100,
        delay_seconds=1.5,
        sleep=calls.append,
    )
    assert calls == [1.5]


def test_pagination_progress_callback(reviews_page1, reviews_page2):
    progress: list[int] = []
    paginate_reviews(
        _pager([reviews_page1, reviews_page2]),
        max_reviews=100,
        delay_seconds=0,
        on_page=progress.append,
    )
    assert progress == [2, 3]  # running count after each page


def test_pagination_invariants_hold_across_caps(reviews_page1, reviews_page2):
    for cap in [1, 2, 3, 4, 10, 100]:
        out, _ = paginate_reviews(
            _pager([reviews_page1, reviews_page2]), max_reviews=cap, delay_seconds=0
        )
        assert len(out) <= cap
        ids = [r.review_id for r in out if r.review_id]
        assert len(ids) == len(set(ids))  # uniqueness


# ---------------------------------------------------------------- with_retries


def test_with_retries_succeeds_after_transient_failures():
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ValueError("transient")
        return "ok"

    assert with_retries(flaky, attempts=3, sleep=lambda _s: None, rng=lambda: 0.0) == "ok"
    assert attempts["n"] == 3


def test_with_retries_wraps_persistent_failure_as_scraper_unavailable():
    with pytest.raises(ScraperUnavailable):
        with_retries(
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            attempts=2,
            sleep=lambda _s: None,
            rng=lambda: 0.0,
        )


def test_with_retries_does_not_retry_app_not_found():
    attempts = {"n": 0}

    def not_found():
        attempts["n"] += 1
        raise AppNotFound("com.missing")

    with pytest.raises(AppNotFound):
        with_retries(not_found, attempts=5, sleep=lambda _s: None, rng=lambda: 0.0)
    assert attempts["n"] == 1


def test_with_retries_attempts_one_calls_fn_exactly_once():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "result"

    assert with_retries(fn, attempts=1, sleep=lambda _s: None, rng=lambda: 0.0) == "result"
    assert calls["n"] == 1


def test_with_retries_rejects_attempts_below_one():
    with pytest.raises(ValueError):
        with_retries(lambda: "never", attempts=0)


# ------------------------------------------------------- error classification


def test_classify_429_as_rate_limited():
    exc = ExtraHTTPError("App not found. Status code 429 returned.")
    assert isinstance(classify_exception(exc), RateLimitedUpstream)


def test_classify_play_gateway_error_as_rate_limited():
    # The library's transport surfaces throttling as this generic Exception message.
    exc = Exception("com.google.play.gateway.proto.PlayGatewayError")
    assert isinstance(classify_exception(exc), RateLimitedUpstream)


def test_classify_timeout():
    assert isinstance(classify_exception(TimeoutError("slow")), ScrapeTimeout)


def test_classify_unknown_as_unavailable():
    assert isinstance(classify_exception(RuntimeError("?")), ScraperUnavailable)


def test_with_retries_surfaces_rate_limit_classification():
    def throttled():
        raise ExtraHTTPError("Status code 429 returned.")

    with pytest.raises(RateLimitedUpstream):
        with_retries(throttled, attempts=2, sleep=lambda _s: None, rng=lambda: 0.0)


# --------------------------------------------------------------- validation


def test_validate_app_id_accepts_package_ids():
    validate_app_id("com.example.app_2")  # no raise


@pytest.mark.parametrize("bad", ["", "com/evil", "a b", "x" * 201, "café.app", "a?b=c"])
def test_validate_app_id_rejects_bad_input(bad):
    with pytest.raises(InvalidAppId):
        validate_app_id(bad)


def test_search_apps_rejects_bad_query():
    with pytest.raises(ValueError):
        search_apps("")
    with pytest.raises(ValueError):
        search_apps("x" * 201)


# ------------------------------------ library-facing functions (real token protocol)


def test_fetch_reviews_with_real_token_protocol(monkeypatch, reviews_page1, reviews_page2):
    """End-to-end through fetch_reviews using the REAL _ContinuationToken sentinel:
    the library always returns a token OBJECT; exhaustion is its inner .token=None."""
    import google_play_scraper as gps_mod

    calls: list[object] = []

    def fake_reviews(app_id, *, lang, country, sort, count, continuation_token):
        calls.append(continuation_token)
        if continuation_token is None:
            return list(reviews_page1), _live_token()
        return list(reviews_page2), _exhausted_token()  # object, NOT None

    monkeypatch.setattr(gps_mod, "reviews", fake_reviews)
    result = fetch_reviews("com.example.calc", max_reviews=100, delay_seconds=0)

    assert [r.review_id for r in result.reviews] == ["r1", "r2", "r3"]
    assert result.fetched == 3
    assert result.complete is True
    assert len(calls) == 2  # exhausted sentinel was normalized; no extra wasted call
    assert calls[0] is None and calls[1] is not None


def test_fetch_reviews_clamps_above_hard_max(monkeypatch):
    import google_play_scraper as gps_mod

    monkeypatch.setattr(gps_mod, "reviews", lambda *a, **kw: ([], _exhausted_token()))
    result = fetch_reviews("com.x", max_reviews=999_999, delay_seconds=0)
    assert result.requested == HARD_MAX_REVIEWS


def test_fetch_reviews_negative_max_returns_empty_without_calling_lib(monkeypatch):
    import google_play_scraper as gps_mod

    def explode(*a, **kw):
        raise AssertionError("library must not be called for max_reviews<=0")

    monkeypatch.setattr(gps_mod, "reviews", explode)
    result = fetch_reviews("com.x", max_reviews=-5, delay_seconds=0)
    assert result.reviews == [] and result.requested == 0


def test_fetch_reviews_result_fields(monkeypatch, reviews_page1):
    import google_play_scraper as gps_mod

    monkeypatch.setattr(
        gps_mod, "reviews", lambda *a, **kw: (list(reviews_page1), _exhausted_token())
    )
    result = fetch_reviews("com.app", country="in", lang="hi", max_reviews=10, delay_seconds=0)
    assert (result.app_id, result.country, result.lang) == ("com.app", "in", "hi")
    assert result.requested == 10 and result.fetched == 2


def test_fetch_reviews_default_cap_is_default_max(monkeypatch):
    import google_play_scraper as gps_mod

    monkeypatch.setattr(gps_mod, "reviews", lambda *a, **kw: ([], _exhausted_token()))
    result = fetch_reviews("com.x", delay_seconds=0)
    assert result.requested == DEFAULT_MAX_REVIEWS


def test_get_app_maps_not_found_error(monkeypatch):
    import google_play_scraper as gps_mod

    def raise_nf(*a, **kw):
        raise NotFoundError("App not found(404).")

    monkeypatch.setattr(gps_mod, "app", raise_nf)
    with pytest.raises(AppNotFound):
        get_app("com.missing.app")


def test_get_app_returns_app_info(monkeypatch, app_raw):
    import google_play_scraper as gps_mod

    monkeypatch.setattr(gps_mod, "app", lambda *a, **kw: dict(app_raw))
    info = get_app("com.example.calc")
    assert info.app_id == "com.example.calc"
    assert info.histogram == [1200, 800, 1500, 4000, 11000]


def test_search_apps_returns_app_info_list(monkeypatch, search_raw):
    import google_play_scraper as gps_mod

    monkeypatch.setattr(gps_mod, "search", lambda *a, **kw: list(search_raw))
    results = search_apps("calculator")
    assert [a.app_id for a in results] == ["com.example.calc", "com.other.calc"]
