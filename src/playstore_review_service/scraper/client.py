"""Scraper client: wraps `google-play-scraper` behind a resilient, testable boundary.

Key responsibilities (spec §4.1):
- Honest review pagination — fixes the legacy bug where ``reviews(count=5000)`` returned
  only ~200 (a single page). We page via the continuation token until the cap or
  exhaustion, and the returned count reflects what was actually fetched.
- A hard server-side review cap regardless of caller request.
- Polite inter-page delay + exponential backoff with jitter on transient errors.
- Error classification — no raw library exception escapes. Rate-limit responses are
  surfaced as :class:`RateLimitedUpstream` so the worker can back off the whole job.
- PII anonymization on by default (``anonymize=False`` is a LOCAL-DEBUG-ONLY escape
  hatch; the hosted worker must never pass it — see spec §4.6).

Library facts this module is built against (verified on google-play-scraper 1.2.7):
- ``gps.reviews()`` ALWAYS returns a ``_ContinuationToken`` object as its second element,
  never ``None``. Exhaustion is signaled by the token's inner ``.token`` attribute being
  ``None``. :func:`fetch_reviews` normalizes that to plain ``None`` before it reaches
  :func:`paginate_reviews`.
- ``Retry-After`` headers are NOT observable: the library consumes HTTP errors inside its
  transport and re-raises message-only exceptions. Rate limiting manifests as
  ``ExtraHTTPError`` (with the status code in the message) or a generic
  ``PlayGatewayError`` exception. We classify both; pacing relies on our own backoff.

The page loop is a pure function (:func:`paginate_reviews`) taking an injected
``fetch_page`` callable, so the logic is unit-testable without the network. NOTE: a proxy
transport is NOT yet wired (``SCRAPER_PROXY_URL`` in env.example is reserved for the
0.1-spike decision); when needed it must be added at the library/transport level.

All functions here are synchronous/blocking. In an async worker context, run them in a
thread executor (``asyncio.get_event_loop().run_in_executor``) — never directly on the
event loop.
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from .errors import (
    AppNotFound,
    InvalidAppId,
    RateLimitedUpstream,
    ScraperUnavailable,
    ScrapeTimeout,
)
from .models import AppInfo, Review

logger = logging.getLogger(__name__)

T = TypeVar("T")

PLAY_REVIEW_PAGE_SIZE = 200  # Google Play serves at most ~200 reviews per page.
DEFAULT_MAX_REVIEWS = 500  # aligned with env.example MAX_REVIEWS_PER_ANALYSIS
HARD_MAX_REVIEWS = 2000  # server-side ceiling; bounds cost + scrape time (spec §4.1)
DEFAULT_DELAY_SECONDS = 1.0  # polite pacing; pass 0 ONLY in tests

APP_ID_RE = re.compile(r"^[a-zA-Z0-9._]{1,200}$")
MAX_QUERY_LENGTH = 200

# Message fragments that identify a rate-limit response from the library's transport
# (it does not expose status codes or headers — message sniffing is all we have).
_RATE_LIMIT_MARKERS = ("429", "PlayGatewayError", "Too Many Requests")

# A page fetcher takes a continuation token (None on the first call) and returns
# (rows, next_token). next_token MUST be None when there are no more pages — adapters
# around the real library are responsible for normalizing its token sentinel.
PageFetcher = Callable[[object | None], "tuple[list[dict], object | None]"]


@dataclass(frozen=True, slots=True)
class FetchResult:
    app_id: str
    country: str
    lang: str
    sort: str
    requested: int
    reviews: list[Review] = field(default_factory=list)
    complete: bool = True  # False when pagination aborted early on a persistent error

    @property
    def fetched(self) -> int:
        return len(self.reviews)


def validate_app_id(app_id: str) -> None:
    """Defense-in-depth guard (spec §4.5): reject anything that isn't a package id."""
    if not isinstance(app_id, str) or not APP_ID_RE.match(app_id):
        raise InvalidAppId(f"invalid app id: {app_id!r}")


def _validate_query(query: str) -> None:
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_LENGTH:
        raise ValueError(f"invalid search query (1..{MAX_QUERY_LENGTH} chars required)")


def classify_exception(exc: Exception) -> Exception:
    """Map a raw library/transport exception to our typed error taxonomy.

    The returned error's message is ``str(exc)`` which may contain upstream HTTP context
    (URLs, status text). Per spec §4.5 the API layer must NEVER serialize these messages
    into responses — log ``type(exc).__name__`` + request id instead.
    """
    if isinstance(exc, AppNotFound | RateLimitedUpstream | ScrapeTimeout | ScraperUnavailable):
        return exc
    text = str(exc)
    if any(marker in text for marker in _RATE_LIMIT_MARKERS):
        return RateLimitedUpstream(text)
    if isinstance(exc, TimeoutError):
        return ScrapeTimeout(text)
    return ScraperUnavailable(text)


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

    :class:`AppNotFound` is terminal (not retried). Every other failure is retried up to
    ``attempts`` times, then re-raised classified (:func:`classify_exception`) — a 429 /
    gateway throttle surfaces as :class:`RateLimitedUpstream`, not a generic failure.
    ``attempts`` must be >= 1.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
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
                "scrape attempt %d failed (%s); retrying in %.1fs",
                attempt + 1,
                type(exc).__name__,
                delay,
            )
            sleep(delay)
    assert last_exc is not None
    raise classify_exception(last_exc) from last_exc


def paginate_reviews(
    fetch_page: PageFetcher,
    *,
    max_reviews: int,
    anonymize: bool = True,
    sleep: Callable[[float], None] = time.sleep,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    on_page: Callable[[int], None] | None = None,
) -> tuple[list[Review], bool]:
    """Page through reviews until ``max_reviews`` or exhaustion. Pure + deterministic.

    Termination conditions (all tested):
    - cap reached;
    - empty page;
    - ``next_token`` is None (adapters must normalize the library's token sentinel);
    - **stale page**: a full page that yields zero NEW unique reviews — guards against a
      server repeating the same rows with a live token (would otherwise loop forever);
    - a page fetch failing persistently AFTER some reviews were collected — the partial
      result is returned with ``complete=False`` instead of being discarded. A failure on
      the FIRST page propagates (there is nothing to salvage).

    Returns ``(reviews, complete)``. ``on_page`` (if given) is called with the running
    collected count after each page — Phase 1's worker uses it for job progress.
    """
    if max_reviews <= 0:
        return [], True
    collected: list[Review] = []
    seen: set[str] = set()
    token: object | None = None
    first = True
    complete = True
    while len(collected) < max_reviews:
        if not first and delay_seconds > 0:
            sleep(delay_seconds)  # polite delay between pages, not before the first
        try:
            rows, token = fetch_page(token)
        except Exception as exc:  # noqa: BLE001 - salvage partial results (see docstring)
            if first:
                raise
            logger.warning(
                "page fetch failed after %d reviews collected (%s); returning partial result",
                len(collected),
                type(exc).__name__,
            )
            complete = False
            break
        first = False
        if not rows:
            break
        before = len(collected)
        for raw in rows:
            review = Review.from_raw(raw, anonymize=anonymize)
            if review.review_id:
                if review.review_id in seen:
                    continue
                seen.add(review.review_id)
            collected.append(review)
            if len(collected) >= max_reviews:
                break
        if on_page is not None:
            on_page(len(collected))
        if len(collected) == before:
            break  # stale page: all duplicates — server is repeating; stop
        if token is None:
            break
    return collected[:max_reviews], complete


def _gps():
    """Lazy import so the module (and its unit tests) load without the library installed."""
    try:
        import google_play_scraper as gps
    except ImportError as exc:  # pragma: no cover - exercised only when lib absent
        raise ScraperUnavailable("google-play-scraper is not installed") from exc
    return gps


def _not_found_error():
    """The library's NotFoundError, imported directly (NOT via ``gps.exceptions`` —
    the package __init__ does not export the submodule reliably)."""
    from google_play_scraper.exceptions import NotFoundError

    return NotFoundError


def search_apps(
    query: str, *, country: str = "us", lang: str = "en", n_hits: int = 10
) -> list[AppInfo]:
    _validate_query(query)
    gps = _gps()
    raws = with_retries(lambda: gps.search(query, lang=lang, country=country, n_hits=n_hits))
    return [AppInfo.from_raw(r) for r in raws]


def get_app(app_id: str, *, country: str = "us", lang: str = "en") -> AppInfo:
    validate_app_id(app_id)
    gps = _gps()
    not_found = _not_found_error()

    def call():
        try:
            return gps.app(app_id, lang=lang, country=country)
        except not_found as exc:
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
    on_page: Callable[[int], None] | None = None,
) -> FetchResult:
    """Fetch up to ``max_reviews`` (capped at :data:`HARD_MAX_REVIEWS`) reviews, honestly.

    Synchronous/blocking — wrap in ``run_in_executor`` from async code. The library's
    ``_ContinuationToken`` sentinel (whose inner ``.token`` is None at exhaustion) is
    normalized to plain ``None`` here so :func:`paginate_reviews` terminates correctly.
    """
    validate_app_id(app_id)
    capped = max(0, min(max_reviews, HARD_MAX_REVIEWS))
    gps = _gps()
    sort_val = sort if sort is not None else gps.Sort.NEWEST

    def fetch_page(token):
        rows, next_ct = with_retries(
            lambda: gps.reviews(
                app_id,
                lang=lang,
                country=country,
                sort=sort_val,
                count=PLAY_REVIEW_PAGE_SIZE,
                continuation_token=token,
            )
        )
        # Normalize the library sentinel: exhaustion = inner .token is None.
        exhausted = next_ct is None or getattr(next_ct, "token", None) is None
        return rows, (None if exhausted else next_ct)

    reviews, complete = paginate_reviews(
        fetch_page,
        max_reviews=capped,
        anonymize=anonymize,
        delay_seconds=delay_seconds,
        on_page=on_page,
    )
    return FetchResult(
        app_id=app_id,
        country=country,
        lang=lang,
        sort=str(sort_val),
        requested=capped,
        reviews=reviews,
        complete=complete,
    )
