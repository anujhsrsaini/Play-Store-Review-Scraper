"""Scraper client: wraps `google-play-scraper` behind a resilient, testable boundary.

Key responsibilities (spec §4.1):
- Honest review pagination — fixes the legacy bug where ``reviews(count=5000)`` returned
  only ~200 (a single page). We page via the continuation token until the cap or
  exhaustion, and the returned count reflects what was actually fetched.
- A hard server-side review cap regardless of caller request.
- Polite inter-page delay + exponential backoff with jitter on transient errors.
- Error classification — no raw library exception escapes.
- PII anonymization on by default.

The network calls go through small wrappers and the page loop is a pure function
(:func:`paginate_reviews`) that takes an injected ``fetch_page`` callable, so the logic is
fully unit-testable without the library or network — and a proxy transport can be slotted
in later (the 0.1 spike decides whether one is needed) without touching this loop.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from .errors import AppNotFound, ScraperUnavailable
from .models import AppInfo, Review

logger = logging.getLogger(__name__)

T = TypeVar("T")

PLAY_REVIEW_PAGE_SIZE = 200  # Google Play serves at most ~200 reviews per page.
DEFAULT_MAX_REVIEWS = 300
HARD_MAX_REVIEWS = 2000  # server-side ceiling; bounds cost + scrape time (spec §4.1)
DEFAULT_DELAY_SECONDS = 1.0

# A page fetcher takes a continuation token (None on the first call) and returns
# (rows, next_token). next_token is None when there are no more pages.
PageFetcher = Callable[[object | None], "tuple[list[dict], object | None]"]


@dataclass(frozen=True, slots=True)
class FetchResult:
    app_id: str
    country: str
    lang: str
    sort: str
    requested: int
    reviews: list[Review]

    @property
    def fetched(self) -> int:
        return len(self.reviews)


def with_retries(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    rng: Callable[[], float] = random.random,
) -> T:
    """Run ``fn`` with exponential backoff + full jitter.

    :class:`AppNotFound` is treated as terminal (not retried). Any other exception is
    retried up to ``attempts`` times, then re-raised as :class:`ScraperUnavailable`.
    """
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except AppNotFound:
            raise
        except Exception as exc:  # noqa: BLE001 - intentional: classify all lib failures
            last_exc = exc
            if attempt == attempts - 1:
                break
            delay = min(max_delay, base_delay * (2**attempt))
            delay *= 0.5 + 0.5 * rng()  # full jitter
            logger.warning(
                "scrape attempt %d failed: %s; retrying in %.1fs", attempt + 1, exc, delay
            )
            sleep(delay)
    raise ScraperUnavailable(str(last_exc)) from last_exc


def paginate_reviews(
    fetch_page: PageFetcher,
    *,
    max_reviews: int,
    anonymize: bool = True,
    sleep: Callable[[float], None] = time.sleep,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
) -> list[Review]:
    """Page through reviews until ``max_reviews`` or exhaustion. Pure + deterministic.

    Stops when: the cap is reached, a page comes back empty, or ``next_token`` is None.
    De-duplicates by ``review_id``. The returned length is the honest fetched count.
    """
    if max_reviews <= 0:
        return []
    collected: list[Review] = []
    seen: set[str] = set()
    token: object | None = None
    first = True
    while len(collected) < max_reviews:
        if not first and delay_seconds > 0:
            sleep(delay_seconds)  # polite delay between pages, not before the first
        first = False
        rows, token = fetch_page(token)
        if not rows:
            break
        for raw in rows:
            review = Review.from_raw(raw, anonymize=anonymize)
            if review.review_id and review.review_id in seen:
                continue
            if review.review_id:
                seen.add(review.review_id)
            collected.append(review)
            if len(collected) >= max_reviews:
                break
        if token is None:
            break
    return collected[:max_reviews]


def _gps():
    """Lazy import so the module (and its unit tests) load without the library installed."""
    try:
        import google_play_scraper as gps
    except ImportError as exc:  # pragma: no cover - exercised only when lib absent
        raise ScraperUnavailable("google-play-scraper is not installed") from exc
    return gps


def search_apps(
    query: str, *, country: str = "us", lang: str = "en", n_hits: int = 10
) -> list[AppInfo]:
    gps = _gps()
    raws = with_retries(lambda: gps.search(query, lang=lang, country=country, n_hits=n_hits))
    return [AppInfo.from_raw(r) for r in raws]


def get_app(app_id: str, *, country: str = "us", lang: str = "en") -> AppInfo:
    gps = _gps()

    def call():
        try:
            return gps.app(app_id, lang=lang, country=country)
        except gps.exceptions.NotFoundError as exc:
            raise AppNotFound(app_id) from exc

    raw = with_retries(call)
    return AppInfo.from_raw(raw, app_id=app_id)


def fetch_reviews(
    app_id: str,
    *,
    country: str = "us",
    lang: str = "en",
    max_reviews: int = DEFAULT_MAX_REVIEWS,
    sort: object | None = None,
    anonymize: bool = True,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
) -> FetchResult:
    """Fetch up to ``max_reviews`` (capped at :data:`HARD_MAX_REVIEWS`) reviews, honestly."""
    capped = max(0, min(max_reviews, HARD_MAX_REVIEWS))
    gps = _gps()
    sort_val = sort if sort is not None else gps.Sort.NEWEST

    def fetch_page(token):
        result = with_retries(
            lambda: gps.reviews(
                app_id,
                lang=lang,
                country=country,
                sort=sort_val,
                count=PLAY_REVIEW_PAGE_SIZE,
                continuation_token=token,
            )
        )
        rows, next_token = result
        return rows, next_token

    reviews = paginate_reviews(
        fetch_page, max_reviews=capped, anonymize=anonymize, delay_seconds=delay_seconds
    )
    return FetchResult(
        app_id=app_id,
        country=country,
        lang=lang,
        sort=str(sort_val),
        requested=capped,
        reviews=reviews,
    )
