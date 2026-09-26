"""Tests for the Sarvam AI adapter (OpenAI-compatible) — no network, injected `post`.

Test credentials are obvious non-secret placeholders (``fake-token-*``).
"""

from __future__ import annotations

import json

import pytest

from playstore_review_service.config import Settings
from playstore_review_service.llm import LLMError, cost_usd
from playstore_review_service.llm_openai import openai_compatible_analyze

BASE = "https://api.sarvam.ai/v2"
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


def test_sends_bearer_auth_and_openai_body():
    captured: dict = {}
    post = _capturing_post(captured, _Resp(_chat_response(json.dumps(ANSWER))))
    payload, usage = openai_compatible_analyze(
        "What do users hate?",
        "[id=r1 | ★1 | 2026-05-01 | v1] keeps crashing",
        base_url=BASE,
        api_key="fake-token-abc",
        model="deepseekv4-flash",
        post=post,
    )
    assert captured["url"] == f"{BASE}/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer fake-token-abc"
    assert captured["body"]["model"] == "deepseekv4-flash"
    assert [m["role"] for m in captured["body"]["messages"]] == ["system", "user"]
    # schema is injected into the prompt (no response_format reliance)
    assert "JSON Schema" in captured["body"]["messages"][0]["content"]
    assert captured["timeout"][0] == 10  # (connect, read) tuple
    assert payload["summary"].startswith("Users mostly")
    assert usage == {"tokens_in": 1234, "tokens_out": 56, "cost_usd": None}


def test_real_cost_from_ticks_preferred_over_estimate():
    resp = _chat_response(
        json.dumps(ANSWER),
        usage={"prompt_tokens": 4063, "completion_tokens": 921, "cost_in_usd_ticks": 20060000},
    )
    _payload, usage = openai_compatible_analyze(
        "q",
        "l",
        base_url=BASE,
        api_key="fake-token",
        model="deepseekv4-flash",
        post=_capturing_post({}, _Resp(resp)),
    )
    assert usage["cost_usd"] == 20060000 * 1e-11  # ~$0.0002, the real billed amount


def test_cost_usd_none_when_ticks_absent():
    _payload, usage = openai_compatible_analyze(
        "q",
        "l",
        base_url=BASE,
        api_key="fake-token",
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
        model="deepseekv4-flash",
        post=post,
    )
    assert payload["summary"].startswith("Users mostly")
    assert usage["tokens_in"] > 0 and usage["tokens_out"] > 0  # estimated when usage absent


def test_http_error_becomes_llmerror_without_leaking_key():
    def post(url, **kw):
        return _Resp({"error": "Authorization failed"}, status=403)

    with pytest.raises(LLMError) as ei:
        openai_compatible_analyze(
            "q",
            "l",
            base_url=BASE,
            api_key="fake-token-leakcheck",
            model="m",
            post=post,
        )
    assert "fake-token-leakcheck" not in str(ei.value)


def test_malformed_choices_raises_llmerror():
    def post(url, **kw):
        return _Resp({"unexpected": "shape"})

    with pytest.raises(LLMError):
        openai_compatible_analyze(
            "q", "l", base_url=BASE, api_key="fake-token", model="m", post=post
        )


def test_non_json_content_raises_llmerror():
    def post(url, **kw):
        return _Resp(_chat_response("I cannot answer that."))

    with pytest.raises(LLMError):
        openai_compatible_analyze(
            "q", "l", base_url=BASE, api_key="fake-token", model="m", post=post
        )


def test_base_url_trailing_slash_normalized():
    captured: dict = {}
    post = _capturing_post(captured, _Resp(_chat_response(json.dumps(ANSWER))))
    openai_compatible_analyze(
        "q",
        "l",
        base_url=BASE + "/",
        api_key="fake-token",
        model="m",
        post=post,
    )
    assert captured["url"] == f"{BASE}/chat/completions"  # no double slash


# ---------------------------------------------------------- provider resolution


def _settings(**over) -> Settings:
    base = dict(
        database_url="sqlite://",
        llm_base_url="",
        llm_api_key="",
        llm_model="deepseekv4-flash",
        llm_planner_model="deepseekv4-flash",
        google_client_id="",
        google_client_secret="",
        google_redirect_uri="",
        session_secret="test",
        session_cookie_secure=False,
        per_user_daily_analyses=15,
        anon_trial_enabled=False,
        anon_daily_analyses=1,
        anon_spend_fraction=0.5,
        max_reviews_per_analysis=500,
        scrape_cache_ttl_hours=24,
        global_daily_spend_cap_usd=5.0,
        scrape_delay_seconds=0.0,
        dev_inprocess_worker=False,
    )
    base.update(over)
    return Settings(**base)


def test_provider_resolves_sarvam_when_key_and_url_set():
    s = _settings(llm_api_key="fake-token", llm_base_url=BASE)
    assert s.provider() == "openai_compatible"


def test_provider_falls_back_to_stub():
    assert _settings().provider() == "stub"
    # key missing -> not selected
    assert _settings(llm_base_url=BASE).provider() == "stub"
    assert _settings(llm_api_key="fake-token").provider() == "stub"


# ---------------------------------------------------------- Sarvam AI tests


def test_sarvam_response_with_reasoning_content():
    resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(ANSWER),
                    "reasoning_content": "Detailed thinking about review r1 and crashes...",
                }
            }
        ],
        "usage": {"prompt_tokens": 500, "completion_tokens": 120},
    }
    post = _capturing_post({}, _Resp(resp))
    payload, usage = openai_compatible_analyze(
        "q",
        "l",
        base_url="https://api.sarvam.ai/v2",
        api_key="fake-sarvam-key",
        model="deepseekv4-flash",
        post=post,
    )
    assert payload["summary"].startswith("Users mostly")
    assert usage["tokens_in"] == 500
    assert usage["tokens_out"] == 120
    assert usage["cost_usd"] is None


def test_sarvam_response_with_think_tags_in_content():
    content = f"<think>\nAnalyzing negative sentiment.\n</think>\n{json.dumps(ANSWER)}"
    resp = {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 300, "completion_tokens": 80},
    }
    post = _capturing_post({}, _Resp(resp))
    payload, _ = openai_compatible_analyze(
        "q",
        "l",
        base_url="https://api.sarvam.ai/v2",
        api_key="fake-sarvam-key",
        model="deepseekv4-flash",
        post=post,
    )
    assert payload["summary"].startswith("Users mostly")


def test_sarvam_empty_content_triggers_llmerror():
    # Model ran out of completion tokens during reasoning
    resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "Incomplete thought...",
                }
            }
        ]
    }
    post = _capturing_post({}, _Resp(resp))
    with pytest.raises(LLMError):
        openai_compatible_analyze(
            "q",
            "l",
            base_url="https://api.sarvam.ai/v2",
            api_key="fake-sarvam-key",
            model="deepseekv4-flash",
            post=post,
        )


def test_sarvam_pricing_registered():
    assert cost_usd("deepseekv4-flash", 1_000_000, 1_000_000) == pytest.approx(0.95)
    assert cost_usd("gemma4", 1_000_000, 1_000_000) == pytest.approx(1.55)
    assert cost_usd("sarvam-105b", 1_000_000, 1_000_000) == pytest.approx(1.25)
    assert cost_usd("glm5.3", 1_000_000, 1_000_000) == pytest.approx(6.10)
