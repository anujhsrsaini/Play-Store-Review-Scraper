"""playstore_review_service — hosted Play Store review-analysis service.

Phase 0 ships the scraper core. Later phases add the FastAPI app, Postgres cache + job
queue, and the Gemini analysis engine (see ``spec.md`` / ``Plans.md``).
"""

from __future__ import annotations

from .scraper import (
    AppInfo,
    AppNotFound,
    FetchResult,
    RateLimitedUpstream,
    Review,
    ScraperError,
    ScraperUnavailable,
    ScrapeTimeout,
    fetch_reviews,
    get_app,
    search_apps,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "AppInfo",
    "Review",
    "FetchResult",
    "search_apps",
    "get_app",
    "fetch_reviews",
    "ScraperError",
    "AppNotFound",
    "ScraperUnavailable",
    "ScrapeTimeout",
    "RateLimitedUpstream",
]
