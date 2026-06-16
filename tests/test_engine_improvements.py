"""Tests for the 'Do Now' engine-improvement batch (N2–N9) + a lightweight golden-set
eval (N8) that asserts grounding invariants on every (reviews, question) case."""

from __future__ import annotations

import pytest

from playstore_review_service.analysis import (
    clamp_answer,
    curate_reviews,
    sentiment_from_histogram,
    stub_analysis,
    verify_quotes,
)
from playstore_review_service.llm import LLMError, normalize_payload
from playstore_review_service.worker import process_one
from test_service import APP_ID, build_service


@pytest.fixture()
def service(tmp_path, monkeypatch):
    with build_service(tmp_path, monkeypatch) as svc:
        yield svc


# ----------------------------------------------------------- N2: histogram sentiment


def test_sentiment_from_histogram():
    s = sentiment_from_histogram([10, 10, 20, 30, 30])  # [1★..5★]
    assert s is not None
    assert s["counted"] == 100
    assert s["positive_pct"] == 60.0  # 4★+5★ = 60
    assert s["negative_pct"] == 20.0  # 1★+2★ = 20
    assert s["source"] == "lifetime_histogram"
    # unusable inputs degrade to None (falls back to sample sentiment)
    assert sentiment_from_histogram(None) is None
    assert sentiment_from_histogram([1, 2, 3]) is None
    assert sentiment_from_histogram([0, 0, 0, 0, 0]) is None


# ----------------------------------------------------------- N3: question-aware curation


def test_curate_boosts_question_relevant_reviews():
    # A keyword-matching review that is OLD would be dropped by recency alone…
    reviews = [
        {
            "review_id": "new1",
            "score": 5,
            "text": "great app",
            "created_at": "2026-06-10",
            "thumbs_up": 0,
        },
        {
            "review_id": "new2",
            "score": 5,
            "text": "love it",
            "created_at": "2026-06-09",
            "thumbs_up": 0,
        },
        {
            "review_id": "old_sync",
            "score": 1,
            "text": "the sync feature is broken",
            "created_at": "2020-01-01",
            "thumbs_up": 0,
        },
    ]
    opts = dict(recent_n=2, helpful_n=0, per_star_n=0, relevant_n=5)
    without = {r["review_id"] for r in curate_reviews(reviews, **opts)}
    assert "old_sync" not in without  # recency-only drops it
    with_q = {
        r["review_id"] for r in curate_reviews(reviews, question="why is sync broken?", **opts)
    }
    assert "old_sync" in with_q  # question pulls it in


# ----------------------------------------------------------- N4: payload sanitization


def test_normalize_payload_sanitizes_malformed():
    out = normalize_payload(
        {"summary": 123, "themes": ["not-a-dict", {"label": "ok"}], "supporting_quotes": "nope"}
    )
    assert out["summary"] == "123"
    assert out["themes"] == [{"label": "ok"}]  # non-dict theme dropped
    assert out["supporting_quotes"] == []  # non-list coerced to []
    assert out["not_enough_data"] is False and out["caveats"] == []


# ----------------------------------------------------------- N5: orphan themes + floor


def test_verify_quotes_drops_orphan_theme_and_sets_floor():
    payload = {
        "summary": "s",
        "themes": [{"label": "ghost", "polarity": "negative", "supporting_quote_ids": ["bad"]}],
        "supporting_quotes": [{"id": "bad", "quote": "totally fabricated quote"}],
        "caveats": [],
    }
    out = verify_quotes(payload, {"r1": "the real review text here"})
    assert out["themes"] == []  # orphan theme (no surviving evidence) dropped
    assert out["supporting_quotes"] == []  # fabricated quote dropped
    assert out["not_enough_data"] is True  # nothing verified → floor trips
    assert any("without supporting evidence" in c for c in out["caveats"])


# ----------------------------------------------------------- N4 (integration): LLM→stub fallback


def test_worker_falls_back_to_stub_on_llm_error(tmp_path, monkeypatch):
    with build_service(
        tmp_path,
        monkeypatch,
        LLM_PROVIDER="openai_compatible",
        LLM_API_KEY="fake-token",
        LLM_BASE_URL="https://oci.example/openai/v1",
    ) as (client, sf):
        import playstore_review_service.worker as worker_mod

        def boom(*a, **kw):
            raise LLMError("simulated provider failure")

        monkeypatch.setattr(worker_mod.llm_openai, "openai_compatible_analyze", boom)

        body = client.post(
            "/api/analyze", json={"app_id": APP_ID, "question": "what breaks?"}
        ).json()
        assert process_one(sf) is True
        out = client.get(f"/api/jobs/{body['job_id']}").json()
        assert out["status"] == "done"  # did NOT hard-fail
        assert out["result"]["model"] == "stub-fallback"
        assert any("fallback" in c.lower() for c in out["result"]["answer"]["caveats"])
        # the released spend reservation means no spend was billed
        from playstore_review_service.db import AnalysisMetric

        with sf() as s:
            m = s.query(AnalysisMetric).first()
            assert m is not None and m.llm_fallback is True and m.reviews_curated > 0


# ----------------------------------------------------------- N7: metrics recorded


def test_analysis_metric_recorded(service):
    client, sf = service
    body = client.post(
        "/api/analyze", json={"app_id": APP_ID, "question": "top complaints?"}
    ).json()
    process_one(sf)
    from playstore_review_service.db import AnalysisMetric

    with sf() as s:
        m = s.query(AnalysisMetric).filter(AnalysisMetric.job_id == body["job_id"]).first()
    assert m is not None
    assert m.reviews_fetched == 30 and m.reviews_curated > 0
    assert m.provider == "stub" and m.latency_ms >= 0


# ----------------------------------------------------------- N8: golden-set grounding eval


def _golden_reviews():
    return [
        {
            "review_id": f"r{i}",
            "score": (i % 5) + 1,
            "text": ("crashes on launch every time" if i % 2 else "smooth and fast, love it"),
            "created_at": f"2026-06-{(i % 28) + 1:02d}",
            "thumbs_up": i,
        }
        for i in range(40)
    ]


@pytest.mark.parametrize(
    "question",
    [
        "what do users complain about?",
        "what do people love?",
        "is it fast?",
        "anything about crashes?",
    ],
)
def test_golden_set_grounding_invariants(question):
    """Through the no-LLM stub path: every surfaced quote must trace to a real review,
    and a non-empty corpus must not abstain. (Deterministic CI gate; no judge needed.)"""
    reviews = _golden_reviews()
    curated = curate_reviews(reviews, question=question)
    answer = clamp_answer(stub_analysis(question, curated))
    corpus_text = {r["review_id"]: r["text"] for r in reviews}
    for q in answer["supporting_quotes"]:
        assert q["id"] in corpus_text  # cited review exists
        assert q["quote"] in corpus_text[q["id"]]  # quote is a real substring
    assert answer["summary"]
    assert answer["not_enough_data"] is False  # 40 reviews → must produce an answer
