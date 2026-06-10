"""Tests for the scraper boundary models — conversion, PII anonymization, resilience."""

from __future__ import annotations

from datetime import datetime

from playstore_review_service.scraper.models import AppInfo, Review


def test_app_info_preserves_market_research_fields(app_raw):
    info = AppInfo.from_raw(app_raw)
    assert info.app_id == "com.example.calc"
    assert info.score == 4.27
    assert info.histogram == [1200, 800, 1500, 4000, 11000]  # not discarded
    assert info.real_installs == 1530221
    assert info.offers_iap is True
    assert info.ad_supported is True
    assert info.updated == 1716950400
    assert info.version == "4.3.1"


def test_app_info_schema_resilient_on_missing_fields():
    # A near-empty dict (simulating an upstream schema change) must not crash.
    info = AppInfo.from_raw({"appId": "com.x"})
    assert info.app_id == "com.x"
    assert info.score is None
    assert info.histogram is None


def test_app_info_app_id_override():
    info = AppInfo.from_raw({}, app_id="com.forced.id")
    assert info.app_id == "com.forced.id"


def test_review_anonymized_by_default(reviews_page1):
    review = Review.from_raw(reviews_page1[0])
    assert review.review_id == "r1"
    assert review.score == 1
    assert review.text.startswith("App keeps crashing")
    assert review.app_version == "4.3.1"
    assert review.thumbs_up == 12
    assert review.user_name is None  # PII dropped by default


def test_review_keeps_name_when_not_anonymized(reviews_page1):
    review = Review.from_raw(reviews_page1[0], anonymize=False)
    assert review.user_name == "Jane Doe"


def test_review_handles_datetime_and_string_dates():
    from_dt = Review.from_raw({"reviewId": "a", "at": datetime(2026, 5, 12, 10, 30, 0)})
    assert from_dt.created_at == "2026-05-12T10:30:00"
    from_str = Review.from_raw({"reviewId": "b", "at": "2026-05-12T10:30:00"})
    assert from_str.created_at == "2026-05-12T10:30:00"
    from_none = Review.from_raw({"reviewId": "c"})
    assert from_none.created_at is None
