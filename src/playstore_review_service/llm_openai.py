"""OpenAI-compatible LLM adapter — used for Sarvam AI (the only LLM provider).

See ``SARVAM_INTEGRATION.md``. Sarvam exposes an OpenAI-shaped ``/chat/completions``
API; auth is a plain Bearer subscription key. We call it with plain ``requests`` so
deps stay minimal.

Same security/grounding contract (spec §4.3, §4.5): review text and the user's
question are untrusted; the model gets no tools and no secrets; the JSON schema is
injected into the prompt (``response_format`` support is unreliable across gateways)
and the result is tolerantly parsed, then quote-verified by the caller. Errors surface
as :class:`LLMError` with the exception TYPE name only — never the API key or raw
payload.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .llm import (
    SYSTEM_PROMPT,
    LLMError,
    build_comparison_prompt,
    build_prompt,
    comparison_schema_instruction,
    estimate_tokens,
    extract_json,
    normalize_payload,
    schema_instruction,
)

logger = logging.getLogger(__name__)

# (connect, read). Cheap models answer fast; the read budget is generous for a big review
# batch but well under typical reverse-proxy 504 windows. Reasoning "planner" models would
# need a much larger read budget (see OCI doc) — not used on this sync analysis path.
DEFAULT_TIMEOUT: tuple[int, int] = (10, 120)
TEMPERATURE = 0.15
# Reasoning models (e.g. deepseekv4-flash, sarvam-105b) spend completion budget
# on hidden reasoning tokens BEFORE emitting the answer, so the JSON output needs generous headroom.
DEFAULT_MAX_TOKENS = 6000
USD_PER_TICK = 1e-11  # `cost_in_usd_ticks` → USD (empirically derived; see usage parse)

PostFn = Callable[..., Any]


def _default_post(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - real network
    import requests

    return requests.post(*args, **kwargs)


def openai_compatible_analyze(
    question: str,
    review_lines: str,
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: tuple[int, int] = DEFAULT_TIMEOUT,
    post: PostFn = _default_post,
    system_text: str | None = None,
    user_text: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Call the OpenAI-compatible chat endpoint. Returns (payload, {tokens_in, tokens_out}).

    ``post`` is injectable for tests; production uses ``requests.post``.
    ``system_text`` / ``user_text`` override the default single-analysis messages
    (used by the Battle Lens compare path); when omitted, behavior is unchanged.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    messages = [
        {"role": "system", "content": system_text or f"{SYSTEM_PROMPT}\n\n{schema_instruction()}"},
        {"role": "user", "content": user_text or build_prompt(question, review_lines)},
    ]

    def call(msgs: list) -> tuple[dict[str, Any], str]:
        body = {
            "model": model,
            "messages": msgs,
            "temperature": TEMPERATURE,
            "max_tokens": DEFAULT_MAX_TOKENS,
        }
        try:
            resp = post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=body,
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            msg = data["choices"][0]["message"]
            content = (msg.get("content") or "").strip()
            if not content:
                # In reasoning models, if content is empty (token limit), check reasoning
                reasoning = (msg.get("reasoning") or msg.get("reasoning_content") or "").strip()
                if reasoning and "{" in reasoning and "}" in reasoning:
                    content = reasoning
                else:
                    raise LLMError("llm response missing content (token budget exhausted)")
        except KeyError as exc:
            raise LLMError("llm response missing choices/message") from exc
        except LLMError:
            raise
        except Exception as exc:
            # type-name only — never leak the API key (in the headers) or payload
            raise LLMError(f"openai-compatible call failed: {type(exc).__name__}") from exc
        return data, content

    try:
        data, content = call(messages)
        payload = normalize_payload(extract_json(content))
    except LLMError:
        # One repair retry: re-ask with an explicit "valid JSON only" nudge (handles
        # truncation / fenced-prose / minor schema drift) before giving up (N4).
        nudge = "That was not valid JSON. Reply with ONLY the JSON object — no prose, no fences."
        repair = [*messages, {"role": "user", "content": nudge}]
        data, content = call(repair)
        payload = normalize_payload(extract_json(content))

    usage = data.get("usage") or {}
    tokens_in = usage.get("prompt_tokens") or estimate_tokens(str(messages))
    tokens_out = usage.get("completion_tokens") or estimate_tokens(content)
    # Gateways that report the real billed cost as `cost_in_usd_ticks` are preferred
    # over our estimate (empirically 1 tick = 1e-11 USD).
    ticks = usage.get("cost_in_usd_ticks")
    cost_usd = float(ticks) * USD_PER_TICK if ticks is not None else None
    return payload, {
        "tokens_in": int(tokens_in),
        "tokens_out": int(tokens_out),
        "cost_usd": cost_usd,
    }


def openai_compatible_compare(
    app_a_id: str,
    app_b_id: str,
    review_lines_a: str,
    review_lines_b: str,
    custom_focus: str = "",
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: tuple[int, int] = DEFAULT_TIMEOUT,
    post: PostFn = _default_post,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Battle Lens comparison via an OpenAI-compatible endpoint.

    Same transport/retry/usage semantics as :func:`openai_compatible_analyze`
    with the two-corpus prompt + comparison schema. Returns (payload, usage).
    """
    return openai_compatible_analyze(
        "",
        "",
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
        post=post,
        system_text=f"{SYSTEM_PROMPT}\n\n{comparison_schema_instruction()}",
        user_text=build_comparison_prompt(
            app_a_id, app_b_id, review_lines_a, review_lines_b, custom_focus
        ),
    )
