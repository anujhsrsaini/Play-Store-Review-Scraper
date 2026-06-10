"""Typed scraper errors.

The scraper wraps an unofficial third-party library (`google-play-scraper`) that can
break, rate-limit, or return unexpected shapes. Callers (the worker, the API) should
never see a raw library exception or stack trace — every failure is classified into one
of these so it can be surfaced as a clean, typed error.
"""

from __future__ import annotations


class ScraperError(Exception):
    """Base class for all scraper failures."""


class AppNotFound(ScraperError):
    """The requested app id / query returned no app."""


class ScraperUnavailable(ScraperError):
    """The upstream library failed or the page shape changed (likely needs a lib bump)."""


class ScrapeTimeout(ScraperError):
    """A scrape call exceeded its time budget."""


class RateLimitedUpstream(ScraperError):
    """Google Play throttled the request (HTTP 429 / Too Many Requests)."""
