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


def test_app_info_carries_gemini_context_fields():
    raw = {
        "appId": "com.x",
        "icon": "https://example.com/i.png",
        "description": "Long description",
        "summary": "Short blurb",
        "recentChanges": "Fixed crashes; added dark mode",
    }
    info = AppInfo.from_raw(raw)
    assert info.icon == "https://example.com/i.png"  # surfaced for the UI
    assert info.description == "Long description"
    assert info.summary == "Short blurb"
    assert info.recent_changes == "Fixed crashes; added dark mode"


def test_app_info_schema_resilient_on_missing_fields():
    info = AppInfo.from_raw({"appId": "com.x"})
    assert info.app_id == "com.x"
    assert info.score is None
    assert info.histogram is None


def test_app_info_rejects_malformed_histogram():
    assert AppInfo.from_raw({"appId": "a", "histogram": [1, 2, 3]}).histogram is None
    assert AppInfo.from_raw({"appId": "a", "histogram": "junk"}).histogram is None
    assert AppInfo.from_raw({"appId": "a", "histogram": [1, 2, 3, 4, 5]}).histogram == [
        1,
        2,
        3,
        4,
        5,
    ]


def test_app_info_app_id_override():
    assert AppInfo.from_raw({}, app_id="com.forced.id").app_id == "com.forced.id"


def test_review_anonymized_by_default(reviews_page1):
    review = Review.from_raw(reviews_page1[0])
    assert review.review_id == "r1"
    assert review.score == 1
    assert review.text.startswith("App keeps crashing")
    assert review.app_version == "4.3.1"
    assert review.thumbs_up == 12
    assert review.user_name is None  # PII dropped by default
    # userImage must never surface — the model has no field for it at all (spec §4.6).
    assert not hasattr(review, "user_image")
    assert "userImage" not in [f for f in review.__slots__]


def test_review_keeps_name_when_not_anonymized(reviews_page1):
    review = Review.from_raw(reviews_page1[0], anonymize=False)
    assert review.user_name == "Jane Doe"


def test_review_silently_ignores_reply_fields(reviews_page1):
    # Real library returns replyContent/repliedAt/appVersion; discarding is intentional.
    review = Review.from_raw(reviews_page1[0])
    assert not hasattr(review, "reply_content")
    assert not hasattr(review, "replied_at")


def test_review_created_at_type_coercion():
    from_dt = Review.from_raw({"reviewId": "a", "at": datetime(2026, 5, 12, 10, 30, 0)})
    assert from_dt.created_at == "2026-05-12T10:30:00"
    from_str = Review.from_raw({"reviewId": "b", "at": "2026-05-12T10:30:00"})
    assert from_str.created_at == "2026-05-12T10:30:00"
    from_none = Review.from_raw({"reviewId": "c"})
    assert from_none.created_at is None
    # Raw epoch int (upstream post-processing change) must NOT leak an int through.
    from_int = Review.from_raw({"reviewId": "d", "at": 1715510400})
    assert from_int.created_at is None or isinstance(from_int.created_at, str)
    assert from_int.created_at.startswith("2024-05-12")  # epoch correctly converted


def test_public_api_imports_are_stable():
    import playstore_review_service as pkg

    for name in pkg.__all__:
        assert hasattr(pkg, name), f"public symbol {name!r} missing from top-level import"
    assert pkg.__version__ == "0.1.0"
