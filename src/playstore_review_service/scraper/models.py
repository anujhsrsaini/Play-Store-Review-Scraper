"""Typed models at the scraper boundary.

Raw `google-play-scraper` dicts are converted to these dataclasses here and never leak
past the scraper package. All field access uses `.get()` so a schema change upstream
degrades to ``None`` instead of crashing (spec §4.1).

PII note (spec §4.6): ``userName`` is dropped unless ``anonymize=False`` (local debug
only). ``userImage`` is NEVER parsed — there is intentionally no field for it, so it
cannot leak into persistence regardless of flags.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def _coerce_iso_datetime(value: Any) -> str | None:
    """Normalize the library's ``at``-style values to an ISO string or None.

    The library post-processes timestamps to ``datetime``; ints/floats can appear if
    that upstream processing changes. Anything else degrades via ``str()`` rather than
    violating the ``str | None`` contract.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, int | float):
        try:
            return datetime.fromtimestamp(value, tz=UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def _coerce_histogram(value: Any) -> list[int] | None:
    """Accept only the expected 5-bucket [1★..5★] list; anything else degrades to None."""
    if isinstance(value, list) and len(value) == 5:
        return value
    return None


def _safe_https_url(value: Any) -> str | None:
    """Only allow https URLs for fields rendered as <img src> (blocks javascript:/data:
    URLs from a malicious listing — defense vs stored XSS). Anything else → None."""
    if isinstance(value, str) and value.startswith("https://"):
        return value
    return None


@dataclass(frozen=True, slots=True)
class AppInfo:
    """App metadata, preserving the market-research fields the legacy script discarded.

    ``description``/``summary``/``recent_changes`` are carried for Phase 2's Gemini
    context curation (the "what changed in the latest update" preset needs
    ``recent_changes`` as grounding).
    """

    app_id: str
    title: str | None = None
    score: float | None = None
    ratings: int | None = None
    reviews: int | None = None
    histogram: list[int] | None = None  # [1★, 2★, 3★, 4★, 5★] lifetime counts
    installs: str | None = None
    real_installs: int | None = None
    min_installs: int | None = None
    price: float | None = None
    free: bool | None = None
    offers_iap: bool | None = None
    ad_supported: bool | None = None
    genre: str | None = None
    content_rating: str | None = None
    released: str | None = None
    updated: int | None = None  # epoch seconds
    version: str | None = None
    developer: str | None = None
    icon: str | None = None  # app icon URL (used by the UI)
    description: str | None = None
    summary: str | None = None
    recent_changes: str | None = None

    @classmethod
    def from_raw(cls, raw: dict[str, Any], app_id: str | None = None) -> AppInfo:
        return cls(
            app_id=app_id or raw.get("appId") or raw.get("app_id") or "",
            title=raw.get("title"),
            score=raw.get("score"),
            ratings=raw.get("ratings"),
            reviews=raw.get("reviews"),
            histogram=_coerce_histogram(raw.get("histogram")),
            installs=raw.get("installs"),
            real_installs=raw.get("realInstalls"),
            min_installs=raw.get("minInstalls"),
            price=raw.get("price"),
            free=raw.get("free"),
            offers_iap=raw.get("offersIAP"),
            ad_supported=raw.get("adSupported"),
            genre=raw.get("genre"),
            content_rating=raw.get("contentRating"),
            released=raw.get("released"),
            updated=raw.get("updated"),
            version=raw.get("version"),
            developer=raw.get("developer"),
            icon=_safe_https_url(raw.get("icon")),
            description=raw.get("description"),
            summary=raw.get("summary"),
            recent_changes=raw.get("recentChanges"),
        )


@dataclass(frozen=True, slots=True)
class Review:
    """A single review. ``user_name`` is dropped when anonymized (spec §4.6)."""

    review_id: str
    score: int | None = None
    text: str | None = None
    created_at: str | None = None  # ISO 8601 string
    app_version: str | None = None
    thumbs_up: int | None = None
    user_name: str | None = None  # None when anonymized

    @classmethod
    def from_raw(cls, raw: dict[str, Any], *, anonymize: bool = True) -> Review:
        # userImage is deliberately not read — no field exists for it (spec §4.6).
        return cls(
            review_id=str(raw.get("reviewId") or raw.get("review_id") or ""),
            score=raw.get("score"),
            text=raw.get("content"),
            created_at=_coerce_iso_datetime(raw.get("at")),
            app_version=raw.get("reviewCreatedVersion"),
            thumbs_up=raw.get("thumbsUpCount"),
            user_name=None if anonymize else raw.get("userName"),
        )
