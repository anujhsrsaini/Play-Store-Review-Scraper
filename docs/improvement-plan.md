# Review Lens — Engine & Product Improvement Plan

> **Produced**: 2026-06-16, by a 5-perspective senior panel (Senior AI Eng × 2, Senior
> Data/Scraping Eng, Product Lead, Staff Skeptic), each grounded in the actual code.
> **Scope**: the review-fetching pipeline and the LLM analysis ("agentic") layer — what to
> improve, in what order, and (just as important) what *not* to build yet.

---

## TL;DR — the central decision

The current analysis is **single-shot**: a question-*agnostic* curated sample (~350 reviews:
recent-150 + most-thumbed-100 + 40/star, ≤40K tokens) is stuffed into one Grok call. The
instinct is "make it agentic / add RAG." **The panel's synthesized verdict: do NOT add a
vector DB / embeddings-RAG / multi-step agent yet.** For a ~350-review corpus inside a 1M-token
model, retrieval *removes* signal the model would otherwise see, while adding cost, latency, an
ops surface, and a re-opened prompt-injection surface — to produce *worse* aggregate answers.

Instead: ship the solid engine that exists, and invest in **cheap, high-leverage upgrades** that
everyone (including the skeptic) endorses — then let real usage logs trigger the heavy work.

**The single highest-ROI change: stream the answer.** Biggest felt-quality jump, zero LLM cost,
touches none of the hard-won grounding logic.

**The most important correctness fix: compute the headline sentiment % from the lifetime star
histogram (already fetched, currently ignored), not from the recency-biased sample.**

---

## Current architecture (ground truth)

**Fetching** (`scraper/client.py`, `models.py`, `worker.ensure_snapshot`)
- `google-play-scraper`, `Sort.NEWEST` only, paginated 200/page, cap 500 (hard 2000), 1s delay,
  retry/backoff, typed errors. One `(app_id, country, lang)` snapshot, 24h TTL, **full re-scrape**
  on refresh (no delta). Stores `id, score, text, created_at, app_version, thumbs_up` (names dropped).
- `app()` metadata captured incl. **`histogram` (true lifetime ★ distribution), `recent_changes`,
  `version`** — fetched but **not used** to inform sampling/grounding. `replyContent`/`repliedAt`
  exist in the payload but are **dropped**.

**Analysis** (`analysis.py`, `llm.py`, `llm_openai.py`, `worker._analyze`)
- Sentiment from **stars over the sampled corpus** (not the LLM). `curate_reviews` =
  question-*blind* stratified sample. One Grok call (`grok-3-mini`; `grok-4` "planner" configured
  but **unused**) → JSON `{summary, themes[], supporting_quotes[], caveats[], not_enough_data}`.
- **Quotes** substring-verified; **`summary`/`theme.label`/`caveats` are NOT verified** (only
  length-clamped). JSON parsed tolerantly (`extract_json`) with **no schema validation / repair**.
- Answer cached per `(app, country, lang, normalized_question, snapshot)`. `UsageLog` records
  tokens/cost; **latency, #curated, verified-quote ratio, not_enough_data rate, parse failures
  are not logged**.

**What's genuinely good (keep, don't regress):** star-based sentiment, substring quote
verification, prompt-injected schema + tolerant parse (OCI `response_format` is unreliable),
atomic spend reservation, per-user quota, untrusted-data prompt boundary, the snapshot-bound
answer cache.

---

## Roadmap

Effort: **S** ≤1 day · **M** a few days · **L** 1–2 weeks+. Impact is user/quality value.

### ✅ DO NOW — pre/at launch (cheap, high-leverage, no new infra)

| # | Change | Why | Impact | Effort | Source |
|---|--------|-----|--------|--------|--------|
| N1 | **Stream the answer** (SSE the `summary` as it generates) | Latency floor is the scrape + a multi-second LLM; streaming is the biggest *felt* speed win, $0 cost, no correctness risk | High | S–M | AI Eng, Skeptic |
| N2 | **Headline sentiment from `app_meta["histogram"]`**, not the sample; report sample-vs-histogram divergence | Fixes the worst representativeness flaw — currently a recency-biased % is shown as the headline while the lifetime ground truth sits unused | **Very High** (correctness) | S | Data Eng |
| N3 | **Question-aware keyword boosting** in `curate_reviews` (substring/`LIKE` over the cached rows to pull question-relevant reviews into the sample) | Closes the one real gap of question-blind sampling — "the whole RAG argument, defused for ~30 lines," no vector DB | High | S | AI Eng + Skeptic (consensus) |
| N4 | **Pydantic-validate the LLM JSON + one repair retry + stub fallback** (detect `finish_reason=="length"` truncation) | Today a truncated/mis-shaped JSON hard-fails the whole job; cheapest reliability win | High | S | Eval Eng |
| N5 | **Drop orphan themes; tie `not_enough_data` to a relevance floor** (a theme whose quotes all failed verification shouldn't render as fact) | Verifies the parts of the answer that currently aren't; calibrated refusal | High | S | AI Eng, Eval |
| N6 | **Visible "How this was computed" methodology strip** (N reviews of M lifetime, date range, sort, "sentiment from stars", analyzed-on date) + per-theme quote-count/confidence chip | Turns an answer into shareable, act-on-able evidence — the difference between "AI toy" and "I'd put my name on this on LinkedIn" | High | S | Product, Eval |
| N7 | **Emit already-computed metrics** to `UsageLog`/an `AnalysisMetrics` row: latency, #fetched/#curated, verified-quote ratio, not_enough_data rate, parse-fail/repair rate, cache-hit | Free observability; you can't improve what you don't measure | High | S | Eval |
| N8 | **Tiny golden-set eval in CI** (10–20 app×question fixtures on frozen snapshots; assert JSON parses, quote-verified ratio ≥ baseline, injection probes don't alter themes, absent-answer → `not_enough_data`) | Stops silent grounding regressions on prompt/model changes — *without* a full LLM-judge rig | High | M | Eval, Skeptic |
| N9 | **Capture `replyContent` + `repliedAt`** (2 fields + 2 columns) | Unlocks a whole question class (developer responsiveness); the data is being thrown away | Med-High | S | Data Eng |
| N10 | **Injection-probe + toxicity check** over free-text output and surfaced quotes (public share page shows verbatim, attacker-influenceable text) | The share page renders unverified text/quotes to logged-out viewers | Med-High | M | Eval, Security |
| N11 | **Operational pre-launch**: verify spend-cap + quota + quote-verify end-to-end on the live OCI Grok path; confirm the datacenter-IP scrape works from the Ampere box (already done); spend alerts 50/80/100% | The #1 real risk is the scraper, not the LLM | High | S | Skeptic, Data Eng |

### ⏭️ DO NEXT — right after launch, driven by logs

| # | Change | Why | Impact | Effort |
|---|--------|-----|--------|--------|
| X1 | **One-click "App Report"** — batch the 5 presets into one structured report (no typing) | Kills the blank-box problem; delivers <2-min wow; the top shareable artifact. Reuses presets + the async pipeline | **Highest product value** | S–M |
| X2 | **Ungate first value** — let an unauthenticated visitor run one analysis / view a pre-baked example before the Google gate; example gallery + OG share cards | Fixes the share→try funnel everything else feeds; the LinkedIn loop | High | S–M |
| X3 | **Competitor comparison** (2–3 apps side-by-side) — N parallel existing jobs + a diff view | The most-demanded, least-DIY-able workflow; the freelancer's willingness-to-pay feature | High | M |
| X4 | **Version-bucketed sampling for "what changed in the update"** (filter by `app_version` / wire `recent_changes` as grounding) | Core question type, high dev salience; fields already stored | Med-High | S–M |
| X5 | **Opt-in `grok-4` "deep analysis" toggle** (route through the same spend cap; finally uses the configured planner) | Bounded-cost differentiation for hard/comparative questions; not a default, not an agent loop | Med | S |
| X6 | **Per-star quota fetch via `filter_score_with`, proportional to the histogram** | Turns the recency convenience sample into a defensible stratified sample | High | M |
| X7 | **Delta/incremental refresh** (stop paging when reviews are already-seen/older than stored max) + adaptive TTL | Cuts daily refresh ~10–50×; enables fresher data cheaply | High | M |
| X8 | **Feature-request extraction & ranking** (structured: request → frequency → example quotes) | Deepest unmet need for indie devs; deepens the moat | Med-High | M |
| X9 | **Data-quality flags at ingest** (language detect/tag, near-dup/bot clustering, emoji-only, rating↔text mismatch) | Removes corpus pollution; surfaces spam/sarcasm honestly | Med | M |
| X10 | **Export** (copy-as-markdown, report PNG, quotes CSV) | Feeds the share + monetization loop | Med | S |
| X11 | **Semantic answer cache** (embed normalized question, reuse ≥0.95 *within the same snapshot only*, exact-hash first, log near-misses) | Cuts paraphrase-driven spend — but conservative: a wrong cache hit serves a confident answer to a different question | Med | M |
| X12 | **Raise review page size** toward the library max (4500 vs current 200) | 200 leaves ~22× throughput unused; cuts scrape wall-time | Med | S |

### 🕒 DEFER — only when logs prove the trigger condition

| Item | Build it WHEN… |
|------|----------------|
| **Embeddings + hybrid RAG** (per-review vectors, dense+BM25, rerank) for the main answer path | logs show users hitting the 2000-review cap on apps with long reviews, OR needle-in-haystack questions failing that keyword-boost (N3) can't catch. Use `pgvector` on the existing Postgres — no new datastore. |
| **Map-reduce per-app "theme digest"** (cached, incremental) | corpora routinely exceed the context budget (huge apps) and the digest is reused across many users of the same app |
| **Question-type router** (single-shot / RAG / map-reduce / 2-pass) | RAG and map-reduce both exist and you need to choose per query — pointless before either is built |
| **Multi-step / tool-using agent** | a *measured* class of comparative/temporal questions fails single-shot — and even then prefer a single self-verify pass over a multi-turn loop |
| **Multi-locale scraping + aggregation** | demand signal for non-`in/en` markets; schema already supports it, but it multiplies the #1 (IP-block) risk |
| **Trend-over-time** | snapshots have accumulated for weeks (build snapshot **retention now**, surface the chart later — value is back-loaded) |
| **Full LLM-as-judge eval harness** (faithfulness/relevance/coverage, regression gating) | there's a real question distribution from prod to evaluate against; until then the golden-set (N8) is enough |
| **Monitoring/alerts platform** | trim to cron + email + a scraper canary; no Grafana/Prometheus for a solo low-traffic app |

### 🚫 DON'T BUILD (this stage) — with rationale

- **Vector DB infra (Pinecone/standalone)** — searching 350 reviews that already fit in context; pure negative ROI. (pgvector-on-existing-Postgres only, and only when DEFER triggers fire.)
- **Agentic tool-loops** — multiply cost ×N and latency, and **re-open the prompt-injection surface** the security pass closed ("no tools, no secrets"). A single self-verify pass ≠ an agent.
- **Question-embedding similarity *for caching*** beyond the conservative ≥0.95 snapshot-scoped version — wrong-question cache hits are worse than misses for a grounding-first product.
- **Optimizing LLM cost** — it's ~$0.01–0.02/uncached analysis behind a $5/day atomic cap and per-user quota; cost is a solved, rounding-error problem. Don't add infra to "save" it.

---

## Deep-dive A — the analysis layer's eventual target (for when DEFER triggers fire)

Not now, but the shape to grow into so today's cheap wins don't become throwaway:

```
INGEST (once per snapshot, amortized across all users of that app):
  scrape → anonymize → quality/dup filter → (later) embed each review (pgvector)
        → (huge apps) map-reduce THEME DIGEST {themes, prevalence, exemplar review-ids}

QUERY:
  question → router (cheap classifier)
    ├─ broad/aggregate   → stratified sample (+digest) → grok-3-mini single-shot   ← today's path, correct
    ├─ specific/needle   → keyword-boost (N3 now) → hybrid retrieval (later) → single-shot
    ├─ comparative/temporal → version/date-filtered, 2-pass → grok-4 (opt-in deep)
    └─ corpus-aggregate  → answer from digest
  → SELF-VERIFY: substring quote-check (today) + atomic-claim attribution + drop orphan themes
       + coverage-vs-digest + retrieval-floor not_enough_data
  → star-sentiment from histogram (N2) → clamp → snapshot-scoped cache
```

The keyword-boost (N3) and self-verify upgrades (N4/N5) are the on-ramp; embeddings/digest/router
are the same design extended **only when corpus > context**.

## Deep-dive B — corpus quality (the analysis is only as good as the data)

Highest-leverage data changes, in order: **(1) headline sentiment from the histogram [N2]**;
(2) per-star quota sampling proportional to the histogram [X6]; (3) capture developer replies
[N9]; (4) delta refresh [X7]; (5) combine `NEWEST` + `MOST_RELEVANT` (dedupe, tag `source_sort`)
to cut recency blindness; (6) ingest quality flags (lang/dup/mismatch) [X9]. Operationally, the
top risk is **datacenter-IP blocking** — wire the reserved `SCRAPER_PROXY_URL` + a global rate
limiter before scaling volume, and add a **canary scrape** that alerts when the unofficial
library's response shape changes.

## Evaluation & observability (the missing discipline)

- **Now**: golden-set fixtures + deterministic asserts in CI (N8); emit the metrics you already
  compute (N7). Two dashboards: **quality** (faithfulness/verified-quote ratio, abstention rate,
  injection-probe pass rate) and **cost** (daily spend vs cap, cache savings, tokens/analysis).
- **Later**: LLM-as-judge (a *different, strong* model; CoT-then-score; direct not pairwise;
  validate against ~50 human labels) for faithfulness / answer-relevance / citation-correctness,
  wired as a regression gate so model/prompt swaps are safe.

## Product north star

Keep free-form Q&A as the **acquisition wedge**, but the **job** is multi-app / multi-point-in-time
decisions. Promote **One-click App Report (X1)** and **Competitor comparison (X3)** to first-class;
make every answer **self-evidently credible** (N6) so the share link recruits the next user.
Differentiation vs AppFollow/Appbot/AppTweak: *grounded, cited, zero-setup NL answers on ANY app
(incl. competitors you don't own), free.* Don't chase review-reply, integrations, ASO/rank
tracking, or iOS — that's incumbent turf and fights the wedge.

## Sequencing summary

1. **Pre-launch (this week):** N1 stream · N2 histogram-sentiment · N3 keyword-boost · N4 JSON
   reliability · N5 orphan-theme/refusal · N6 methodology strip · N7 metrics · N8 golden-set ·
   N11 operational verification. (N9/N10 close behind.)
2. **Post-launch, log-driven:** X1 App Report → X2 ungate/share loop → X3 comparison → then X4–X12
   as usage justifies.
3. **Defer** RAG/agentic/map-reduce/multi-locale/trends/LLM-judge until the named trigger fires.

---

### File pointers
`scraper/client.py` (fetch/sampling, page size, proxy), `scraper/models.py` (histogram, dropped
reply fields), `worker.py` (`ensure_snapshot` refresh, `_analyze` sentiment/verify), `analysis.py`
(`curate_reviews`, `star_sentiment`, `verify_quotes`, `clamp_answer`, `normalize_question`),
`llm.py`/`llm_openai.py` (prompt, schema, parse, model routing), `db.py` (new columns/indexes,
snapshot retention), `frontend/src/{components/Result.tsx,pages/{Home,Shared}.tsx,hooks/useAnalysis.ts}`
(streaming, methodology strip, report/comparison, ungate).
