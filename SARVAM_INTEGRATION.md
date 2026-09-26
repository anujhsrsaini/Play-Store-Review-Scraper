# Using Sarvam AI (OpenAI-compatible v2)

How this project calls LLMs through **Sarvam AI**.
Keys are shared separately — this doc has placeholders only.

---

## TL;DR

- Sarvam AI exposes an **OpenAI-compatible** `/chat/completions` REST API under **`/v2`**.
- We call it with **plain `requests`** (no SDK needed).
- Auth = Bearer token: `Authorization: Bearer <SARVAM_API_KEY>`. No compartment IDs or tenancy OCIDs required.
- Models available on v2:
  - **`deepseekv4-flash`** (default cheap tier): Fast reasoning (~4.5s latency), lowest cost (~$0.23/1M in, $0.68/1M out), reliable JSON schema adherence.
  - **`gemma4`** (zero-reasoning cheap tier): Ultra-fast (~2.5s latency), ~$0.42/1M in, $1.05/1M out.
  - **`glm5.3`** (default planner tier): Deep reasoning (~18s latency), ~$1.45/1M in, $4.55/1M out.
  - **`sarvam-105b`** (alternative planner tier): 105B MoE, 128K context window. Emits verbose reasoning traces (~800–1400 tokens) requiring large completion budgets (`max_tokens >= 4000`) and 90s+ timeouts.

---

## Endpoint

```
https://api.sarvam.ai/v2/chat/completions
```

- Endpoint surface lives under **`/v2`** — NOT `/v1` (legacy) or `/v3` (not active).
- Chat completions: `POST https://api.sarvam.ai/v2/chat/completions`.

## Authentication

| Piece | Where it goes | Notes |
|---|---|---|
| **API key** (`sk_...`) | `Authorization: Bearer sk_...` | Created in Sarvam Dashboard. |

No custom headers like `CompartmentId` are needed.

---

## Request / response shape

Standard OpenAI chat-completions:

```jsonc
// POST https://api.sarvam.ai/v2/chat/completions
{
  "model": "deepseekv4-flash",
  "messages": [{ "role": "user", "content": "..." }],
  "temperature": 0.15,
  "max_tokens": 6000
}
```

Reasoning models (`deepseekv4-flash`, `glm5.3`, `sarvam-105b`) return reasoning in `message.reasoning_content` (or `reasoning`), and the final parsed response in `message.content`.

## Minimal standalone example (Python, plain requests)

```python
import requests

BASE = "https://api.sarvam.ai/v2"
API_KEY = "sk_..."  # Sarvam API key (shared separately)

resp = requests.post(
    f"{BASE}/chat/completions",
    headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    },
    json={
        "model": "deepseekv4-flash",
        "messages": [{"role": "user", "content": "Reply with exactly: Sarvam OK"}],
        "max_tokens": 50,
    },
    timeout=(10, 45),  # (connect, read)
)
resp.raise_for_status()
print(resp.json()["choices"][0]["message"]["content"])
```

cURL equivalent:

```bash
curl -sS -X POST "https://api.sarvam.ai/v2/chat/completions" \
  -H "Authorization: Bearer $SARVAM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseekv4-flash","messages":[{"role":"user","content":"Say OK"}],"max_tokens":20}'
```

---

## Key gotchas

1. **Reasoning-model token budget.** Reasoning models consume completion tokens for hidden thinking before emitting content. If `max_tokens` is set too low (e.g. < 500 for heavy prompts), `message.content` will be empty (`None`) due to token exhaustion. Review Lens configures `DEFAULT_MAX_TOKENS = 6000` to provide plenty of headroom.
2. **Structured / JSON output.** We inject the JSON schema directly into the system prompt ("Respond ONLY with JSON matching the provided schema") and extract the JSON object with `extract_json`, tolerating markdown fences and stripping inline `<think>` tags.
3. **Usage & billing.** Sarvam returns standard OpenAI usage counts (`prompt_tokens`, `completion_tokens`). Token costs are tracked locally via `MODEL_PRICES` against the daily spend cap.

---

## How it's wired in this codebase

Config in `.env`:

```bash
LLM_BASE_URL=https://api.sarvam.ai/v2
LLM_API_KEY=sk_...
LLM_CHEAP_MODEL=deepseekv4-flash
LLM_PLANNER_MODEL=deepseekv4-flash
```

- `src/playstore_review_service/config.py`: loads settings and checks provider.
- `src/playstore_review_service/llm_openai.py`: calls `{base_url}/chat/completions` with bearer token auth and handles defensive extraction.
- `src/playstore_review_service/worker.py`: runs job analysis, checks spend caps, and verifies quote citations against review text.
