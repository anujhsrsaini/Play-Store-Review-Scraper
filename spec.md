# spec.md — Product Contract

> **Project**: Play-Store-Review-Scraper → **hosted Play Store review-analysis web service**
> **Created**: 2026-06-08 · **Revised**: 2026-06-08 (pivot from local CLI → hosted SaaS)
> Product contract (what is *correct*). `Plans.md` is the task ledger. Precedence: `spec.md` > `Plans.md`.
>
> ⚠️ This revision **reverses** the previous CLI/local contract. A hosted service that
> holds the owner's Gemini key is now in scope; "no secrets / no hosted service" are
> no longer non-goals.

---

## 1. Product Vision

A **hosted web service** for Google Play Store market research. A user picks an app,
asks a **free-form question**, and the service scrapes that app's reviews and uses the
**owner's Gemini API** to answer — grounded in real reviews, covering both positive and
negative sentiment.

**Wedge:** "Ask anything about any app's reviews — free." The paid suites (AppFollow
~$179–559/mo, Appbot ~$49/mo) are owner-centric, dashboard-and-workflow products that
make you connect *your own* app and pay before any value. The gap below them:
zero-friction, natural-language Q&A on *any* app (including competitors), free-tier.

**One-line flow:** pick app → ask "What do users complain about most?" → cited answer +
sentiment breakdown in under a minute.

---

## 2. Locked Decisions (do not relitigate)

- **Access**: gated + rate-limited. Per-user identity; per-user daily quota; caching.
- **Stack**: Python **FastAPI** backend reusing `google-play-scraper` + a simple web UI.
- **Data**: **scrape-and-cache** with a reuse window (default 24h); not on-demand per request.
- **LLM**: the **owner's Gemini API key** does the analysis, server-side only.
- Solo developer; small hosted SaaS scale; cost-conscious.

---

## 3. Target Users & Core Use Cases

Users: indie/solo app developers, ASO/app-marketing freelancers, PMs/analysts doing
competitive review research, data-curious users.

Representative questions the service must answer well:
- What do users complain about most? / What do people love?
- What features are users asking for that don't exist yet?
- Did the latest update upset users? What broke?
- Why are people uninstalling / switching away?
- What do users say about price / subscription / ads / support?
- Summarize positive vs negative themes for an exec.

Ship **free-form questions** + a curated set of **one-click presets** (Top Complaints,
Top Praises, Feature Requests, Sentiment Summary, What Changed in Latest Update).

---

## 4. Correct Behavior (the contract)

### 4.1 Scraping & caching
- Reuse the scraper-correctness rules: **paginate reviews honestly** (the legacy
  `reviews(count=5000)` returning ~200 bug must be fixed), schema-resilient column
  *selection* (no hardcoded `drop()`), preserve market-research fields (`histogram,
  installs, realInstalls, price, offersIAP, adSupported, updated, version, score, ratings`).
- Pin `google-play-scraper` with an upper bound; wrap it in a `scraper/` module that
  converts raw dicts → typed dataclasses and **classifies errors** (`app_not_found`,
  `scraper_unavailable`, `timeout`, `rate_limited_upstream`) — never leak a stack trace.
- **Scrape at most once per `(app_id, country, lang)` per 24h snapshot window.** All users
  served from the cached snapshot. **Single-flight**: concurrent requests for the same cold
  app trigger exactly one scrape and share its result.
- Hard server-side cap on reviews per scrape (e.g. ≤2000); polite delay + exponential
  backoff between pages. (`Retry-After` is NOT observable through `google-play-scraper`
  1.2.7 — its transport consumes HTTP errors and re-raises message-only exceptions —
  so throttling is classified as `rate_limited_upstream` and pacing relies on our own
  backoff. Revisit if the transport is ever replaced.)

### 4.2 Latency / async
- A scrape + LLM call MUST NOT run in a synchronous HTTP request. Pattern: `POST /analyze`
  → enqueue job → return `job_id` → client polls `GET /jobs/{id}` → result. Scraping and
  Gemini run in a **separate worker process**, never the web process.

### 4.3 LLM analysis
- **Provider is pluggable** (config, not code): `openai_compatible` (Oracle OCI GenAI —
  default `xai.grok-3-mini`, see `OCI_GENAI_INTEGRATION.md`) → legacy `gemini`
  (`gemini-2.5-flash-lite`) → `stub` (no-LLM keyword fallback, always works locally).
  The same grounding prompt, JSON schema, and quote verification apply to every provider.
  Reasoning models (Grok) need extra output-token headroom (reasoning tokens consume the
  budget); the OCI adapter sets a larger `max_tokens`. Never default to a heavy/Pro tier.
- **Context = curated sample**, not the full corpus: most-recent N + most-helpful
  (thumbs-up) N + stratified sample across 1–5 stars / recent versions, capped at a fixed
  token budget (~40K). RAG embeddings are the documented scale-up path, not MVP.
- **Sentiment %** is computed from **star ratings over the full corpus** (free,
  deterministic) — NOT paid to the LLM. The LLM produces qualitative themes + the answer.
- **Structured JSON output** via `response_schema`: `summary, not_enough_data, themes[]
  {label, polarity, prevalence, supporting_quote_ids[]}, sentiment_breakdown
  {pos/neu/neg %, source}, supporting_quotes[] {id, quote, stars, date}, caveats[]`.
- **Grounding (non-negotiable)**: system prompt forbids outside knowledge; every claim
  cites ≥1 verbatim review (id + star + date); `not_enough_data:true` when reviews don't
  support an answer.
- **Quote verification (non-negotiable)**: after generation, every returned quote MUST be
  verified as a real substring (normalized) of its cited review; fabricated quotes are
  dropped/flagged. Validate all cited ids exist in the input set. Temperature ~0.15.

### 4.4 Cost control (owner pays — ship-blockers)
- `MAX_REVIEWS_PER_ANALYSIS` cap + **pre-flight token estimate**; reject/trim over a
  per-call ceiling; cap `max_output_tokens`.
- **Answer cache** keyed on `(app_id, country, lang, normalized_question, snapshot_id)`;
  normalize/canonicalize questions to resist cache-busting; optional question-embedding
  similarity (≥0.95) to catch paraphrases.
- **Per-user daily token/cost quota**, enforced even on cache-miss (rate-limit ≠ cost-limit).
- **Global daily spend kill-switch**: atomic, persisted (DB), checked before every Gemini
  call; disables analysis at the cap until UTC rollover; alerts owner at 50/80/100%.
- **Provider-side Gemini hard billing cap** + Cloud billing budget alerts — the backstop
  the application code cannot bypass. Set it low.

### 4.5 Security (ship-blockers)
- **Gemini key server-side only**: never in client bundles/`NEXT_PUBLIC_*`/`VITE_*`, never
  in responses, never in logs (no SDK debug logging; scrub secret-shaped strings). Generic
  error handler returns `{error, request_id}`, never a serialized exception.
- **`.env` + secret patterns gitignored** (done); `.env.example` with names only;
  pre-commit secret scan (gitleaks). A key that ever hit a commit is burned → rotate.
- **Prompt-injection boundary**: review text AND the user's question are untrusted —
  delimited, labeled as data-not-instructions; the LLM gets **no tools and no secrets** in
  context (injection can't exfiltrate what isn't there); answers rendered as escaped plain
  text (no auto-loaded images/links → no exfil-via-image-URL, no stored XSS).
- **Input validation**: `app_id` strict regex `^[a-zA-Z0-9._]+$` + length cap (anti-SSRF);
  keyword length-capped, control chars stripped. Parameterized DB queries only.
- **Auth**: per-user identity (Google OAuth primary / email magic-link), NOT a shared
  static token; API keys stored **hashed** + prefixed; rate-limit per identity, not raw IP.
- OWASP baseline: request size limits, call timeouts, security headers, CORS locked to
  known origins, prod `/docs` gated/disabled, no debug mode in prod.

### 4.6 Privacy & compliance (hosting raises the bar)
- The owner is now a **data controller**. **Anonymize at ingest** — drop/hash
  `userName`/`userImage` before storing and before sending to Gemini (forced, not optional).
- Define **retention/purge** TTLs for cached reviews and for user questions/answers; minimal
  data-subject delete path; privacy policy + lawful-basis/transfer disclosure (EU review
  data → Gemini is a transfer event).
- **Scraping risk shifts to the owner**: a hosted server makes the requests commercially, so
  the user-responsibility disclaimer no longer shields the owner. Disclaimer must say the
  owner bears scraping/ToS/block risk; serve cached data + "live data unavailable" on block.
- Log request-id, pseudonymous user-id, timing, token counts — NOT full prompts/corpora.

---

## 5. Architecture (summary)

```
Browser ─► FastAPI (auth → rate-limit → validate → cache-check → enqueue) ─► job_id
                                                          │
   poll GET /jobs/{id} ◄───────────────────── Postgres (jobs, snapshots, reviews,
                                                          analyses, users, usage_log)
                                                          ▲
   Worker process: single-flight lock → scrape (capped, backoff) → persist snapshot
                   → curate context → Gemini (flash-lite, JSON, grounded) → verify quotes
                   → merge star-sentiment → persist analysis + usage → job done
```

- **Postgres** = single source of truth + cache + job queue (`SELECT … FOR UPDATE SKIP
  LOCKED`). **Redis NOT at launch** — add only when rate-limit/queue becomes a hot path.
- Two processes from one codebase: **web** + **background worker**.
- Endpoints: `POST /analyze`, `GET /jobs/{id}`, `GET /jobs/{id}/result`, `GET /history`,
  `GET /usage`, auth/key management.

---

## 6. Deployment

- **Host**: owner already has an **EC2** instance — the eventual/primary target (self-managed:
  Docker Compose for web + worker + Postgres, TLS via Caddy/nginx, systemd). Render
  (managed, ~$15–21/mo) is fine for a fast first deploy. Either way the egress is a
  **datacenter IP** — see scraping risk §9.1; the host choice does not change that.
- **Async job + poll** makes platform request-timeouts irrelevant.
- **Secrets** in host secret store/env; `.env.example` in repo only.
- **CI/CD**: Dockerfile + GitHub Actions test gate → Render auto-deploy; **Alembic**
  migrations as pre-deploy. Separate `staging` and `prod` services + DBs.
- **Cost guard**: Gemini project spend cap (low) + budget alerts 50/80/95% + app-level
  kill-switch; host spend cap. Periodic network smoke-test as early-warning of scraper breakage.

---

## 7. Non-Goals (explicitly out of scope)

- Client-side LLM calls / exposing the Gemini key in any browser context.
- A shared static access token; public signup with generous free Gemini and no controls.
- Defaulting to Gemini Pro, or stuffing the full review corpus into the prompt.
- On-demand re-scrape per request (cache is mandatory).
- Redis/arq at launch (Postgres-backed queue first); RAG embeddings at launch (scale path).
- Download/revenue estimates, rank-tracking, review-reply, Slack/Zendesk/Tableau
  integrations, multi-app monitoring/alerts, branded PDF export, team seats, iOS/App Store.
- Residential-proxy infra *unless* the scraping spike proves datacenter IPs are blocked.

---

## 8. Definition of Success (v1)

A gated user signs in, picks any Play Store app, asks a free-form question, and within ~a
minute gets a **cited, grounded** answer (real review quotes + star/date) plus a
star-based sentiment breakdown and an honest scope/caveat banner — without the owner's
Gemini key being exposed, without a single user being able to run up an unbounded bill, and
without the service falling over when one scrape fails. Deployed on Render behind HTTPS with
cost alerts and a spend kill-switch live.

---

## 9. Risks (validate first)

1. **#1 — datacenter-IP scraping**: `google-play-scraper` may be `429`'d/blocked from
   datacenter IPs (upstream issue #590; TLS fingerprinting in 2026). This applies to **both
   Render and the owner's EC2** — AWS ranges are blocked at least as aggressively, so the
   owned EC2 does not avoid it. The project is committed regardless; the spike (0.1) is
   therefore a **proxy-decision**, not a GO/NO-GO: measure reviews-before-block + recovery
   from a datacenter IP and decide whether a residential proxy / scraping API (~$30–50/mo)
   is needed in front of the scrape step. Aggressive 24h caching + low volume already
   minimize request rate, which may keep it under the radar without a proxy.
2. **Surprise Gemini bill** — mega-popular app + cache-busting questions. Mitigated by §4.4;
   the provider-side hard cap is the last line of defense.
3. **Prompt injection** via review content / user question — mitigated by §4.5 boundary.
4. **Unofficial-scraper fragility** — pin + classify errors + network smoke test.
