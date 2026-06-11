---
_harness_template: "Plans.md.template"
_harness_version: "4.15.0"
---

# Play Store Review-Analysis Service — Plans.md

> **Project**: hosted Play Store review-analysis web service (FastAPI + Gemini)
> **作成日**: 2026-06-08 · **Revised**: 2026-06-08 (pivot: local CLI → hosted SaaS)
> **Updated by**: Claude Code
> Product contract: `spec.md` (precedence: `spec.md` > `Plans.md`).

Goal: a gated web service where a user asks a free-form question about a Google Play app
and the **owner's Gemini API** answers, grounded in scraped reviews. Planned by a
5-perspective agent panel (Product/UX, Backend Architecture, Gemini/LLM, Security & Cost,
Infra/Hosting).

**Build order is risk-first**: check whether scraping works from a datacenter IP (0.1, a
proxy-decision — not a project gate, since the owner has EC2 and is committed) early; ship
cost/security controls (Phase 3) before going live.

---

## Phase 0: De-risk & Foundation

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 0.1 | **Datacenter-IP scraping SPIKE** (proxy-decision, not a project gate): scrape from a datacenter IP (the owner's EC2, or a Render/Railway free instance) and measure 429/block behavior, reviews-before-block, recovery time. EC2 is the same datacenter-IP class as Render — does not avoid the issue. Decide if a residential proxy/scraping API is needed. `[tdd:skip:spike-investigation]` | Written findings recorded here: works y/n from datacenter IP, max reviews before block, recovery time, proxy needed y/n (+ rough cost) | - | blocked (needs owner's EC2/datacenter IP — cannot run from this machine) |
| 0.2 | **Secrets hygiene + repo skeleton**: confirm `.env`/secret patterns gitignored (done), add `env.example` (names only; `.env*` blocked by write-guard), gitleaks pre-commit; scaffold `src/` package, `pyproject.toml` (py3.11+, Hatchling), Ruff + pytest; delete UTF-16 `requirements.txt`. `[tdd:skip:tooling-setup]` | `pip install -e ".[dev]"` works; `ruff check` + `pytest` exit 0; no secret committable | - | cc:完了 (uncommitted; gitleaks pre-commit deferred) |
| 0.3 | **Scraper module**: wrap `google-play-scraper` in `scraper/` — fix pagination (honest counts), review cap, backoff + polite delay, dataclasses (`AppInfo`/`Review`), pinned version, error classification, **anonymize PII at ingest**. `[tdd:required]` | Tests on fixtures: requested N → fetched ≤N real rows; missing fields don't crash; PII dropped/hashed; errors classified | 0.2 | cc:完了 (4-agent panel review REQUEST_CHANGES → all fixed → 46 tests, 99% cov, ruff+mypy clean) |

---

## Phase 1: Async Job Spine (de-risk latency)

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.1 | **Data model** (`review_snapshots, cached_reviews, jobs, analyses, usage_log`; SQLAlchemy 2.0; SQLite local / Postgres prod). Alembic migrations deferred to 5.1 (localhost uses `create_all`). `users/api_keys` tables arrive with 3.1. `[tdd:required]` | Schema builds; round-trip insert/read tested | 0.2 | cc:完了 (db.py; Alembic + users tables pending) |
| 1.2 | **FastAPI + DB-backed job queue + worker**: `POST /api/analyze` → enqueue → worker scrapes (0.3) → persists snapshot; `GET /api/jobs/{id}` polling `queued/scraping/analyzing/done/error`; `FOR UPDATE SKIP LOCKED` on Postgres; standalone `pmr-worker` + dev in-process thread. `[tdd:required]` | End-to-end (scraper mocked): submit → poll → done with persisted snapshot | 1.1, 0.3 | cc:完了 (webapp.py + worker.py; e2e tested) |
| 1.3 | **Scrape-and-cache + single-flight**: 24h snapshot reuse; identical active jobs coalesced (same job returned). `[tdd:required]` | Test: 2nd request within window does no re-scrape; identical concurrent requests share one job | 1.2 | cc:完了 (ensure_snapshot + job coalescing; tested) |

---

## Phase 2: Gemini Analysis Engine

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 2.1 | **Gemini client (worker-only)** + structured JSON output (`response_schema`) + grounding system prompt with untrusted-content delimiters; flash-lite default. Escalate-to-flash retry path still TODO. `[tdd:required]` | Test (mocked Gemini): returns schema-valid JSON; prompt contains delimiters + grounding rules; key never logged | 1.2 | cc:完了 (llm.py; live-key e2e validation pending owner key; escalation path TODO) |
| 2.2 | **Context curation**: stratified sample (recent + most-helpful + star-strata), strip PII, token-budget cap (~40K) + pre-flight token estimate. `[tdd:required]` | Test: large corpus → capped, balanced sample under token budget; names stripped from prompt | 0.3 | cc:完了 (analysis.curate_reviews; tested) |
| 2.3 | **Star-based sentiment (full corpus, free)** merged with LLM themes/quotes into final answer JSON. `[tdd:required]` | Test: sentiment % computed from stars over all reviews; `source=star_ratings`; merged into response | 1.1 | cc:完了 (tested incl. e2e merge) |
| 2.4 | **Quote verification + grounding guards**: every returned quote verified as substring of cited review; validate ids; `not_enough_data` path. Retry→escalate on schema failure still TODO. `[tdd:required]` | Test: fabricated quote dropped/flagged; unknown id rejected; thin-evidence → `not_enough_data:true` | 2.1 | cc:完了 (analysis.verify_quotes; tested; retry/escalate TODO) |
| 2.5 | **Answer cache + usage logging**: cache keyed `(app_id,country,lang,normalized_question,snapshot_id)`; record tokens + cost estimate in `usage_log`; **global daily spend cap enforced pre-call** (pulled forward from 3.3). `[tdd:required]` | Test: identical question on same snapshot → cache hit, no Gemini call; usage row written; cap blocks LLM call | 2.1, 1.1 | cc:完了 (tested incl. spend-cap block) |

---

## Phase 3: Gating, Cost Control & Security (MUST before live)

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.1 | **Auth**: per-user identity (Google OAuth primary / email magic-link); API keys **hashed**+prefixed; revocation. `[tdd:required]` | Test: unauth request rejected; key stored hashed; revoke works; identity attached to jobs | 1.1 | cc:TODO |
| 3.2 | **Per-user quota + rate limiting** (per identity), enforced on cache-miss. `[tdd:required]` | Test: quota decrements on cache-miss; 429 past quota; rate-limit per identity not raw IP | 3.1, 2.5 | cc:TODO |
| 3.3 | **Global spend kill-switch + provider cap**: atomic persisted daily $ counter checked before every Gemini call; disables analysis at cap; alerts 50/80/100%; document Gemini project hard cap + Cloud budget alerts. `[tdd:required]` | Test: simulated spend ≥ cap → analysis disabled + alert fired; counter survives restart (DB-backed); provider cap documented | 2.5 | cc:TODO |
| 3.4 | **Security hardening**: prompt-injection boundary (delimiting, no tools/secrets, escaped plain-text rendering); `app_id`/keyword validation (anti-SSRF); generic error handler (no key/stack leak); CORS, security headers, body-size limits, prod `/docs` gated. `[tdd:required]` | Tests: injection-laced review/question can't alter output or leak prompt; bad `app_id` rejected; errors generic; headers present | 2.1, 1.2 | cc:TODO |
| 3.5 | **Privacy/retention**: forced ingest anonymization verified; TTL purge for reviews + user Q/A; data-subject delete path; privacy policy text. `[tdd:required]` | Test: purge job removes expired rows; delete-my-data path works; privacy policy present | 1.1, 0.3 | cc:TODO |

---

## Phase 4: Web UI

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 4.1 | **Core flow UI**: search → app picker → ask (free-form box + 5 preset buttons) → in-page progress (poll, live "fetched X" count) → result. `[tdd:required]` | User can run search→ask→result locally; presets pre-fill prompts | 1.2, 2.5 | cc:完了 (static/index.html; escaped rendering) |
| 4.2 | **Result presentation**: answer + cited quotes (star+date) + star sentiment bar + scope/caveat banner + free cached re-ask. `[tdd:skip:presentation]` | Result page shows quotes, sentiment chart, caveat banner; re-ask on cached data makes no new Gemini call | 4.1 | cc:完了 |
| 4.3 | **Sign-in UX + quota chip + error/empty states**: OAuth/magic-link screen; "N/M left today" chip (needs 3.1/3.2); error states for app-not-found/scrape-blocked/LLM-error shipped in 4.1. `[tdd:required]` | All listed states render a friendly message; quota chip decrements | 4.1, 3.1, 3.2 | cc:TODO (error states done; sign-in + quota chip blocked on Phase 3) |
| 4.4 | **SSE streaming of LLM answer** (polish). `[tdd:skip:optional-polish]` | Answer streams token-by-token once status=analyzing | 4.2 | cc:TODO |

---

## Phase 5: Deploy & Launch

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 5.1 | **CI/CD + hosting**: Dockerfile + Docker Compose (web + worker + Postgres) deployable to the owner's **EC2** (TLS via Caddy/nginx, systemd) — Render via `render.yaml` as fast-path alternative; GitHub Actions (ruff/mypy/pytest gate) → deploy; Alembic pre-deploy; staging + prod separation. `[tdd:skip:deploy-config]` | Push to main → green CI → deploys to chosen host; staging + prod isolated with own DB/secrets | 1.2 | cc:TODO |
| 5.2 | **Cost monitoring + smoke test**: Gemini budget alerts + project hard cap; host spend cap; periodic network smoke-test health job. `[tdd:skip:ops-config]` | Alerts configured (evidence); smoke job flags scraper breakage | 5.1, 3.3 | cc:TODO |
| 5.3 | **Legal/docs**: LICENSE; hosted disclaimer (owner bears scraping/ToS risk); privacy policy; README rewrite (what it is, how to use). `[tdd:skip:docs-only]` | LICENSE + disclaimer + privacy policy live; README reflects the service | - | cc:TODO |
| 5.4 | **Invite-only beta launch**: tight quotas, waitlist gate. `[tdd:skip:launch]` | Service reachable behind HTTPS, invite-gated, quotas live | 5.1, 5.2, 5.3, Phase 3 | cc:TODO |

---

## Priority Matrix

- **Required (to go live)**: Phase 0 (all), Phase 1 (all), Phase 2 (all), **Phase 3 (all —
  security/cost are non-negotiable for an owner-paid public service)**, 4.1–4.3, 5.1–5.4.
- **Recommended**: 4.4 (SSE streaming), question-embedding cache (in 2.5), staging niceties.
- **Optional / Later**: RAG embeddings retrieval, Redis/arq migration, batch
  pre-summarization theme digest, context caching, history dashboard, multi-country, export, premium Pro tier.
- **Rejected (Non-Goals, spec §7)**: client-side LLM/key exposure, shared static token,
  default Pro model, full-corpus stuffing, on-demand re-scrape, review-reply/integrations,
  monitoring/alerts product, iOS, residential proxy *unless* spike 0.1 proves it's needed.

---

## Planning Validation

- `team_validation_mode`: **subagent** (5 independent perspectives via Task subagents:
  Product/UX, Backend Architecture, Gemini/LLM, Security & Cost, Infra/Hosting).
- `spec.md` / `Plans.md` consistency: ✅ co-revised this session; precedence preserved.
  **Spec delta**: `spec.md` rewritten — reversed the prior "no hosted service / no secrets"
  non-goals; added hosted-service contract (LLM grounding, cost kill-switch, prompt-injection
  boundary, privacy-as-controller). The Security panel flagged the old non-goals as a process
  risk; resolved by this rewrite.
- Reinvention check: panel verified current (2026) Gemini model lineup/pricing, FastAPI
  async-queue patterns, hosting pricing, and the upstream scraper-block issue — not from memory.
- Product-fit: scope held to "free natural-language review Q&A on any app"; incumbents' moats out.
- Security/secrets: owner's Gemini key is now in scope → handled as server-side-only with
  kill-switch + provider cap + injection boundary + hashed auth. **No `.env`/secret read is
  required by any task**; secrets live in the host secret store. `.env` gitignored before any key exists.
- Lint/formatter baseline: absent today → setup task 0.2 precedes implementation.
- Working-plan gate: each implementation task has a verifiable DoD; fixtures (0.3); CI gate (5.1);
  network smoke test (5.2). **Risk gate**: 0.1 is GO/NO-GO before further build.

---

## In Progress

(none)

---

## Completed

- [x] Initialize Harness (harness.toml, CLAUDE.md, AGENTS.md, Plans.md) `pm:approved` (2026-06-08)
- [x] Tighten `.gitignore` (pattern-based globs + secrets block) `pm:approved` (2026-06-08)

---

## Archive

<!-- Superseded: the original local-CLI plan (Phases 0–4) was replaced by this hosted-service
     plan after the 2026-06-08 pivot. Scraper-correctness rules carried forward into Phase 0.3. -->

---

## Status Marker Legend

| Marker | Meaning |
|--------|---------|
| `pm:requested` | Work requested |
| `cc:TODO` | Not started by Claude Code |
| `cc:WIP` | Claude Code is working |
| `cc:完了` | Claude Code completed; awaiting confirmation |
| `pm:approved` | Completion confirmed |
| `blocked` | Blocked; include the reason next to the task |

---

## Last Update

- **Updated at**: 2026-06-08
- **Branch**: main
