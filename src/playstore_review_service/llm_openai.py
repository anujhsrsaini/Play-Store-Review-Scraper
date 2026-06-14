"""OpenAI-compatible LLM adapter — used for Oracle OCI Generative AI.

See ``OCI_GENAI_INTEGRATION.md``. OCI exposes an OpenAI-shaped ``/chat/completions`` API
under ``…/openai/v1``. Auth needs all three of: a GenAI API key (Bearer), a
``CompartmentId`` header, and an IAM policy. We call it with plain ``requests`` so the
custom header is trivial and deps stay minimal.

Same security/grounding contract as the Gemini path (spec §4.3, §4.5): review text and
the user's question are untrusted; the model gets no tools and no secrets; the JSON schema
is injected into the prompt (``response_format`` support is unreliable across gateways) and
the result is tolerantly parsed, then quote-verified by the caller. Errors surface as
:class:`LLMError` with the exception TYPE name only — never the API key or raw payload.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .llm import (
    SYSTEM_PROMPT,
    LLMError,
    build_prompt,
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
# Reasoning models (e.g. xai.grok-3-mini) spend completion budget on hidden reasoning
# tokens BEFORE emitting the answer, so the JSON output needs generous headroom or it
# truncates mid-object. Larger than the Gemini cap for this reason.
OCI_MAX_TOKENS = 6000

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
    compartment_id: str,
    model: str,
    timeout: tuple[int, int] = DEFAULT_TIMEOUT,
    post: PostFn = _default_post,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Call the OpenAI-compatible chat endpoint. Returns (payload, {tokens_in, tokens_out}).

    ``post`` is injectable for tests; production uses ``requests.post``.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if compartment_id:  # required by OCI; harmless for other OpenAI-compatible gateways
        headers["CompartmentId"] = compartment_id

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{schema_instruction()}"},
            {"role": "user", "content": build_prompt(question, review_lines)},
        ],
        "temperature": TEMPERATURE,
        "max_tokens": OCI_MAX_TOKENS,
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
        content = data["choices"][0]["message"]["content"]
    except KeyError as exc:
        raise LLMError("llm response missing choices/message") from exc
    except Exception as exc:
        # type-name only — never leak the API key (it's in the request headers) or payload
        raise LLMError(f"openai-compatible call failed: {type(exc).__name__}") from exc

    payload = normalize_payload(extract_json(content))

    usage = data.get("usage") or {}
    tokens_in = usage.get("prompt_tokens") or estimate_tokens(str(body["messages"]))
    tokens_out = usage.get("completion_tokens") or estimate_tokens(content)
    return payload, {"tokens_in": int(tokens_in), "tokens_out": int(tokens_out)}
