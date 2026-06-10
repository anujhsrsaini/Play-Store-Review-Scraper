"""Scraper package: resilient, anonymizing wrapper over `google-play-scraper`."""

from __future__ import annotations

from .client import (
    DEFAULT_MAX_REVIEWS,
    HARD_MAX_REVIEWS,
    PLAY_REVIEW_PAGE_SIZE,
    FetchResult,
    fetch_reviews,
    get_app,
    paginate_reviews,
    search_apps,
    with_retries,
)
from .errors import (
    AppNotFound,
    RateLimitedUpstream,
    ScraperError,
    ScraperUnavailable,
    ScrapeTimeout,
)
from .models import AppInfo, Review

__all__ = [
    "AppInfo",
    "Review",
    "FetchResult",
    "search_apps",
    "get_app",
    "fetch_reviews",
    "paginate_reviews",
    "with_retries",
    "PLAY_REVIEW_PAGE_SIZE",
    "DEFAULT_MAX_REVIEWS",
    "HARD_MAX_REVIEWS",
    "ScraperError",
    "AppNotFound",
    "ScraperUnavailable",
    "ScrapeTimeout",
    "RateLimitedUpstream",
]
