"""Tests for the scraper client: honest pagination, the cap, retries, error classification.

These exercise the pure logic with injected fakes — no network, no library required.
"""

from __future__ import annotations

import pytest

from playstore_review_service.scraper.client import paginate_reviews, with_retries
from playstore_review_service.scraper.errors import AppNotFound, ScraperUnavailable


def _pager(pages: list[list[dict]]):
    """Build a fetch_page callable that yields the given pages then signals exhaustion."""
    state = {"i": 0}

    def fetch_page(_token):
        i = state["i"]
        if i >= len(pages):
            return [], None
        state["i"] += 1
        next_token = "more" if state["i"] < len(pages) else None
        return pages[i], next_token

    return fetch_page


def test_pagination_returns_honest_count_and_dedupes(reviews_page1, reviews_page2):
    # page1 = r1, r2 ; page2 = r3, r2(dup). Unique across both = r1, r2, r3.
    out = paginate_reviews(
        _pager([reviews_page1, reviews_page2]),
        max_reviews=100,
        delay_seconds=0,
    )
    assert [r.review_id for r in out] == ["r1", "r2", "r3"]
    assert len(out) == 3  # honest count, duplicate r2 dropped


def test_pagination_honors_cap(reviews_page1, reviews_page2):
    out = paginate_reviews(
        _pager([reviews_page1, reviews_page2]),
        max_reviews=2,
        delay_seconds=0,
    )
    assert len(out) == 2  # stops at the cap, does not fetch everything
    assert [r.review_id for r in out] == ["r1", "r2"]


def test_pagination_zero_cap_returns_empty(reviews_page1):
    assert paginate_reviews(_pager([reviews_page1]), max_reviews=0) == []


def test_pagination_stops_on_empty_page():
    out = paginate_reviews(_pager([[]]), max_reviews=50, delay_seconds=0)
    assert out == []


def test_pagination_sleeps_between_pages_not_before_first(reviews_page1, reviews_page2):
    calls: list[float] = []
    paginate_reviews(
        _pager([reviews_page1, reviews_page2]),
        max_reviews=100,
        delay_seconds=1.5,
        sleep=calls.append,
    )
    # Two pages consumed → exactly one inter-page sleep (none before the first page).
    assert calls == [1.5]


def test_with_retries_succeeds_after_transient_failures():
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ValueError("transient")
        return "ok"

    result = with_retries(flaky, attempts=3, sleep=lambda _s: None, rng=lambda: 0.0)
    assert result == "ok"
    assert attempts["n"] == 3


def test_with_retries_wraps_persistent_failure_as_scraper_unavailable():
    def always_fail():
        raise RuntimeError("boom")

    with pytest.raises(ScraperUnavailable):
        with_retries(always_fail, attempts=2, sleep=lambda _s: None, rng=lambda: 0.0)


def test_with_retries_does_not_retry_app_not_found():
    attempts = {"n": 0}

    def not_found():
        attempts["n"] += 1
        raise AppNotFound("com.missing")

    with pytest.raises(AppNotFound):
        with_retries(not_found, attempts=5, sleep=lambda _s: None, rng=lambda: 0.0)
    assert attempts["n"] == 1  # terminal — not retried
