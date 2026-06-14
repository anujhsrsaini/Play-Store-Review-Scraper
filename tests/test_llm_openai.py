"""Tests for the OpenAI-compatible (OCI GenAI) adapter — no network, injected `post`.

Test credentials are obvious non-secret placeholders (``fake-token-*``).
"""

from __future__ import annotations

import json

import pytest

from playstore_review_service.config import Settings
from playstore_review_service.llm import LLMError
from playstore_review_service.llm_openai import openai_compatible_analyze

BASE = "https://inference.generativeai.us-ashburn-1.oci.oraclecloud.com/openai/v1"
ANSWER = {
    "summary": "Users mostly complain about crashes.",
    "not_enough_data": False,
    "themes": [
        {
            "label": "crashes",
            "polarity": "negative",
            "prevalence": "high",
            "supporting_quote_ids": ["r1"],
        }
    ],
    "supporting_quotes": [{"id": "r1", "quote": "keeps crashing", "stars": 1}],
    "caveats": [],
}


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _chat_response(content: str, usage=None) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": usage if usage is not None else {"prompt_tokens": 1234, "completion_tokens": 56},
    }


def _capturing_post(captured: dict, response: _Resp):
    def fake_post(url, **kw):
        captured["url"] = url
        captured["headers"] = kw.get("headers")
        captured["body"] = kw.get("json")
        captured["timeout"] = kw.get("timeout")
        return response

    return fake_post


def test_sends_oci_headers_and_openai_body():
    captured: dict = {}
    post = _capturing_post(captured, _Resp(_chat_response(json.dumps(ANSWER))))
    payload, usage = openai_compatible_analyze(
        "What do users hate?",
        "[id=r1 | ★1 | 2026-05-01 | v1] keeps crashing",
        base_url=BASE,
        api_key="fake-token-abc",
        compartment_id="ocid1.tenancy.oc1..abc",
        model="xai.grok-3-mini",
        post=post,
    )
    assert captured["url"] == f"{BASE}/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer fake-token-abc"
    assert captured["headers"]["CompartmentId"] == "ocid1.tenancy.oc1..abc"
    assert captured["body"]["model"] == "xai.grok-3-mini"
    assert [m["role"] for m in captured["body"]["messages"]] == ["system", "user"]
    # schema is injected into the prompt (no response_format reliance)
    assert "JSON Schema" in captured["body"]["messages"][0]["content"]
    assert captured["timeout"][0] == 10  # (connect, read) tuple
    assert payload["summary"].startswith("Users mostly")
    assert usage == {"tokens_in": 1234, "tokens_out": 56, "cost_usd": None}


def test_real_oci_cost_from_ticks_preferred_over_estimate():
    resp = _chat_response(
        json.dumps(ANSWER),
        usage={"prompt_tokens": 4063, "completion_tokens": 921, "cost_in_usd_ticks": 20060000},
    )
    _payload, usage = openai_compatible_analyze(
        "q",
        "l",
        base_url=BASE,
        api_key="fake-token",
        compartment_id="c",
        model="xai.grok-3-mini",
        post=_capturing_post({}, _Resp(resp)),
    )
    assert usage["cost_usd"] == 20060000 * 1e-11  # ~$0.0002, the real billed amount


def test_cost_usd_none_when_ticks_absent():
    _payload, usage = openai_compatible_analyze(
        "q",
        "l",
        base_url=BASE,
        api_key="fake-token",
        compartment_id="c",
        model="m",
        post=_capturing_post({}, _Resp(_chat_response(json.dumps(ANSWER), usage={}))),
    )
    assert usage["cost_usd"] is None  # worker falls back to its estimate


def test_parses_json_inside_markdown_fences():
    fenced = "```json\n" + json.dumps(ANSWER) + "\n```"
    post = _capturing_post({}, _Resp(_chat_response(fenced, usage={})))
    payload, usage = openai_compatible_analyze(
        "q",
        "lines",
        base_url=BASE,
        api_key="fake-token",
        compartment_id="c",
        model="xai.grok-3-mini",
        post=post,
    )
    assert payload["summary"].startswith("Users mostly")
    assert usage["tokens_in"] > 0 and usage["tokens_out"] > 0  # estimated when usage absent


def test_omits_compartment_header_when_empty():
    captured: dict = {}
    post = _capturing_post(captured, _Resp(_chat_response(json.dumps(ANSWER))))
    openai_compatible_analyze(
        "q", "l", base_url=BASE, api_key="fake-token", compartment_id="", model="m", post=post
    )
    assert "CompartmentId" not in captured["headers"]


def test_http_error_becomes_llmerror_without_leaking_key():
    def post(url, **kw):
        return _Resp({"error": "Authorization failed"}, status=403)

    with pytest.raises(LLMError) as ei:
        openai_compatible_analyze(
            "q",
            "l",
            base_url=BASE,
            api_key="fake-token-leakcheck",
            compartment_id="c",
            model="m",
            post=post,
        )
    assert "fake-token-leakcheck" not in str(ei.value)


def test_malformed_choices_raises_llmerror():
    def post(url, **kw):
        return _Resp({"unexpected": "shape"})

    with pytest.raises(LLMError):
        openai_compatible_analyze(
            "q", "l", base_url=BASE, api_key="fake-token", compartment_id="c", model="m", post=post
        )


def test_non_json_content_raises_llmerror():
    def post(url, **kw):
        return _Resp(_chat_response("I cannot answer that."))

    with pytest.raises(LLMError):
        openai_compatible_analyze(
            "q", "l", base_url=BASE, api_key="fake-token", compartment_id="c", model="m", post=post
        )


def test_base_url_trailing_slash_normalized():
    captured: dict = {}
    post = _capturing_post(captured, _Resp(_chat_response(json.dumps(ANSWER))))
    openai_compatible_analyze(
        "q",
        "l",
        base_url=BASE + "/",
        api_key="fake-token",
        compartment_id="c",
        model="m",
        post=post,
    )
    assert captured["url"] == f"{BASE}/chat/completions"  # no double slash


# ---------------------------------------------------------- provider resolution


def _settings(**over) -> Settings:
    base = dict(
        database_url="sqlite://",
        gemini_api_key="",
        gemini_model="g",
        llm_provider="",
        llm_base_url="",
        llm_api_key="",
        llm_compartment_id="",
        llm_model="xai.grok-3-mini",
        llm_planner_model="xai.grok-4",
        google_client_id="",
        google_client_secret="",
        google_redirect_uri="",
        session_secret="test",
        session_cookie_secure=False,
        per_user_daily_analyses=15,
        max_reviews_per_analysis=500,
        scrape_cache_ttl_hours=24,
        global_daily_spend_cap_usd=5.0,
        scrape_delay_seconds=0.0,
        dev_inprocess_worker=False,
    )
    base.update(over)
    return Settings(**base)


def test_provider_resolves_openai_compatible():
    s = _settings(llm_provider="openai_compatible", llm_api_key="fake-token", llm_base_url=BASE)
    assert s.provider() == "openai_compatible"


def test_provider_falls_back_to_gemini_then_stub():
    assert _settings(gemini_api_key="g-key").provider() == "gemini"
    assert _settings().provider() == "stub"
    # openai_compatible declared but key missing -> not selected
    assert _settings(llm_provider="openai_compatible", llm_base_url=BASE).provider() == "stub"
