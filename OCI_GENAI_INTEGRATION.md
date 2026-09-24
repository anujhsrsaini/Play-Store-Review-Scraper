# Using Oracle OCI Generative AI (OpenAI-compatible)

How this project calls LLMs through **Oracle Cloud Infrastructure (OCI) Generative AI**.
Keys are shared separately — this doc has placeholders only.

---

## TL;DR

- OCI Generative AI exposes an **OpenAI-compatible** `/chat/completions` REST API.
- We call it with **plain `requests`** (no `openai` SDK, no `oci` SDK needed for this path).
- Auth = **three things together**: a GenAI API key (`sk-…`) as a Bearer token, a
  `CompartmentId` header, **and** an IAM policy granting the key access. Missing any one → 404/403.
- Region is **Ashburn (`us-ashburn-1`)** and the key is **region-scoped** (401 elsewhere).
- Models we use: **`xai.grok-4`** (heavy/"planner") and **`xai.grok-3-mini`** (cheap/fast).

---

## Endpoint

```
https://inference.generativeai.us-ashburn-1.oci.oraclecloud.com/openai/v1
```

- The OpenAI-compatible surface lives under **`/openai/v1`** — NOT `/v1`. Calling `/v1/...`
  returns `Path doesn't map to a registered service!`.
- Chat completions: `POST {base}/chat/completions`.

## Authentication — all three are required

| Piece | Where it goes | Notes |
|---|---|---|
| **GenAI API key** (`sk-…`) | `Authorization: Bearer sk-…` | Created in Console → Generative AI → **API Keys**. NOT an IAM key, NOT an SSH key. Region-scoped to Ashburn. |
| **Compartment OCID** | `CompartmentId: ocid1.tenancy.oc1..…` header | OCI resolves model lookups *within a compartment*. We use the tenancy root OCID. Without it: `Entity with key X not found` even for models that exist. |
| **IAM policy** | Console → Identity & Security → Policies (root compartment) | Without it the key authenticates but is authorized for nothing. |

Required IAM policy:

```
Allow any-user to use generative-ai-family in tenancy where ALL {request.principal.type='generativeaiapikey'}
```

## Models available in this tenancy (Ashburn)

`google.gemini-2.5-flash`, `google.gemini-2.5-flash-lite`, `google.gemini-2.5-pro`,
`meta.llama-4-scout-17b-16e-instruct`, `meta.llama-4-maverick-17b-128e-instruct-fp8`,
`xai.grok-3`, `xai.grok-3-fast`, `xai.grok-3-mini`, `xai.grok-3-mini-fast`, `xai.grok-4`

**What we picked and why:**

| Tier | Model | Use |
|---|---|---|
| planner | `xai.grok-4` | Heavy reasoning, low-frequency (e.g. curriculum generation) |
| cheap | `xai.grok-3-mini` | High-frequency, latency-sensitive (chat, classification, tagging) |

> **Reliability note (tested on this gateway):** Grok was 100% stable. **Gemini-on-OCI was
> flaky** (intermittent Google/Vertex `403 aiplatform.endpoints.predict denied` leaking
> through + occasionally unparseable output). **Llama-4 is NOT routable** via the
> OpenAI-compatible endpoint (returns `Entity with key … not found` even with the policy in
> place) — it's reachable only via the native OCI SDK. So prefer **Grok** for this REST path.

---

## Libraries used

- **`requests==2.32.5`** — that's it for the call itself (standard OpenAI-shaped JSON over HTTP).
- No `openai` SDK and no `oci` SDK required for the OpenAI-compatible path. (The OpenAI Python
  SDK would also work — point its `base_url` at `{base}` and pass `api_key` — but we use raw
  `requests` to keep deps minimal and to add the custom `CompartmentId` header easily.)

## Request / response shape

Standard OpenAI chat-completions:

```jsonc
// POST {base}/chat/completions
{
  "model": "xai.grok-4",
  "messages": [{ "role": "user", "content": "…" }],
  "temperature": 0.3,
  "max_tokens": 2048
}
// → response.choices[0].message.content
```

## Minimal standalone example (Python, plain requests)

```python
import requests

BASE = "https://inference.generativeai.us-ashburn-1.oci.oraclecloud.com/openai/v1"
API_KEY = "sk-…"  # GenAI API key (shared separately)
COMPARTMENT = "ocid1.tenancy.oc1..…"  # compartment / tenancy OCID

resp = requests.post(
    f"{BASE}/chat/completions",
    headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "CompartmentId": COMPARTMENT,
    },
    json={
        "model": "xai.grok-3-mini",
        "messages": [{"role": "user", "content": "Reply with exactly: OK from OCI"}],
        "max_tokens": 20,
    },
    timeout=(10, 45),  # (connect, read) — see timeout note below
)
resp.raise_for_status()
print(resp.json()["choices"][0]["message"]["content"])
```

cURL equivalent:

```bash
curl -sS -X POST "$BASE/chat/completions" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -H "CompartmentId: $COMPARTMENT" \
  -d '{"model":"xai.grok-3-mini","messages":[{"role":"user","content":"Say OK"}],"max_tokens":8}'
```

---

## Key gotchas (from setting this up)

1. **Reasoning-model timeouts.** `xai.grok-4` can "think" for **minutes before the first byte**.
   A 60s read timeout aborts mid-think. We tier the HTTP read timeout: **planner 280s, cheap 45s**
   (`requests` timeout = `(connect=10, read=<tier>)`). For any sync HTTP path (API request → LLM),
   run the heavy call in a **background task**, not inline, or a reverse proxy will 504 first.
2. **Structured / JSON output.** `response_format` support varies by gateway/model — don't rely
   on it. We **inject the JSON schema into the prompt** ("Return ONLY valid JSON matching …") and
   parse the result (tolerating ```json fences), rather than passing `response_format`.
3. **Error → cause map:**
   - `Path doesn't map to a registered service!` → wrong path; use `/openai/v1`.
   - `Entity with key <model> not found` → model not available in that compartment, wrong
     `CompartmentId`, or missing IAM policy.
   - `Authorization failed or requested resource not found` (esp. google/xai) → IAM policy /
     entitlement issue.
   - `401 … required information … not provided` in a non-Ashburn region → key is Ashburn-scoped.
4. **Secrets** live only in env / a secrets store (here: gitignored `.env`), never in code or git.

---

## How it's wired in this codebase (for reference)

Provider-abstracted service at `backend/app/services/llm/`:

- `client.py` → `generate(messages, schema=None, model_tier="cheap"|"planner", …)`. Provider is
  chosen by config; the `openai_compatible` branch is the OCI path above (with retry/backoff and a
  deterministic fallback so callers never hang).
- Config (env vars, in `backend/app/core/config.py` → `.env`):

  ```bash
  LLM_PROVIDER=openai_compatible
  LLM_BASE_URL=https://inference.generativeai.us-ashburn-1.oci.oraclecloud.com/openai/v1
  LLM_API_KEY=sk-…                       # shared separately
  LLM_COMPARTMENT_ID=ocid1.tenancy.oc1..…
  LLM_PLANNER_MODEL=xai.grok-4
  LLM_CHEAP_MODEL=xai.grok-3-mini
  ```

- The abstraction also has a Gemini (Google AI Studio) adapter behind the same `generate()`;
  switching providers is a config change, not a code change.
