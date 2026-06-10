"""Typed models at the scraper boundary.

Raw `google-play-scraper` dicts are converted to these dataclasses here and never leak
past the scraper package. All field access uses `.get()` so a schema change upstream
degrades to ``None`` instead of crashing (spec §4.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class AppInfo:
    """App metadata, preserving the market-research fields the legacy script discarded."""

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

    @classmethod
    def from_raw(cls, raw: dict[str, Any], app_id: str | None = None) -> AppInfo:
        return cls(
            app_id=app_id or raw.get("appId") or raw.get("app_id") or "",
            title=raw.get("title"),
            score=raw.get("score"),
            ratings=raw.get("ratings"),
            reviews=raw.get("reviews"),
            histogram=raw.get("histogram"),
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
        at = raw.get("at")
        created_at = at.isoformat() if isinstance(at, datetime) else at
        return cls(
            review_id=str(raw.get("reviewId") or raw.get("review_id") or ""),
            score=raw.get("score"),
            text=raw.get("content"),
            created_at=created_at,
            app_version=raw.get("reviewCreatedVersion"),
            thumbs_up=raw.get("thumbsUpCount"),
            user_name=None if anonymize else raw.get("userName"),
        )
