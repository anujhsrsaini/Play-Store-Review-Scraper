# Play Store Review Analysis Service

Ask anything about any Google Play app's reviews — answers grounded in real, cited
reviews, with sentiment computed from star ratings. FastAPI backend + background worker
+ a simple web UI. Reviews are scraped via `google-play-scraper`, cached for 24h, and
analyzed by Gemini (or a built-in keyword stub when no API key is set).

> Product contract: `spec.md` · Task ledger: `Plans.md`

## Local testing — quickstart

```sh
# 1. Setup (once)
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Optional: real LLM analysis (otherwise a no-cost stub analyzer is used)
cp env.example .env        # then put your key in GEMINI_API_KEY=...

# 3. Run (single process: web + in-process dev worker + SQLite)
pmr-serve                  # → http://localhost:8000
```

Open **http://localhost:8000**, search an app (or paste a package id like
`com.whatsapp`), pick it, ask a question (presets provided), and watch the job run:
queued → fetching reviews (live count) → analyzing → answer with verified quotes,
a star-ratings sentiment bar, and a data-quality banner.

What to expect locally:

- **First question on an app**: scrapes up to `MAX_REVIEWS_PER_ANALYSIS` (default 500)
  reviews with a polite 1s delay between pages — takes a minute or two.
- **Re-asks / repeat questions**: served from the 24h snapshot + answer cache — instant
  and free (no Gemini call).
- **No `GEMINI_API_KEY`**: the flow still works end-to-end via a clearly-labeled
  keyword-frequency stub. With a key: real Gemini (`gemini-2.5-flash-lite` by default),
  structured output, and quote verification (fabricated quotes are dropped).
- **Spend guard**: `GLOBAL_DAILY_SPEND_CAP_USD` (default $5) hard-stops Gemini calls
  for the day when the estimated cost would cross it.

## Architecture (localhost shape)

```
Browser (static/index.html)
   │  POST /api/analyze → job_id      GET /api/jobs/{id} (poll)
   ▼
FastAPI (webapp.py) ──► SQLite/Postgres: jobs, review_snapshots(24h TTL),
   │                     cached_reviews (PII-free), analyses (answer cache), usage_log
   ▼
Worker (worker.py — in-process thread for dev; `pmr-worker` standalone for prod)
   scrape (cache-first, capped, backoff) → curate sample → Gemini/stub → verify quotes
```

- DB: SQLite by default (`local.db`); set `DATABASE_URL` to Postgres for production
  (the job queue then uses `FOR UPDATE SKIP LOCKED`).
- PII: reviewer names/images are dropped at the scraper boundary AND the DB schema has
  no column for them.
- Security: the Gemini key never leaves the server; review text and questions are
  treated as untrusted data (delimiter stripping, no tools, escaped rendering).

## Configuration (env vars — see `env.example`)

| Var | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | *(empty → stub)* | Owner's Gemini key, server-side only |
| `GEMINI_DEFAULT_MODEL` | `gemini-2.5-flash-lite` | Analysis model |
| `DATABASE_URL` | `sqlite:///./local.db` | SQLAlchemy URL |
| `MAX_REVIEWS_PER_ANALYSIS` | `500` | Reviews scraped per app snapshot |
| `SCRAPE_CACHE_TTL_HOURS` | `24` | Snapshot reuse window |
| `GLOBAL_DAILY_SPEND_CAP_USD` | `5` | Daily Gemini kill-switch |
| `SCRAPE_DELAY_SECONDS` | `1.0` | Polite inter-page delay |
| `DEV_INPROCESS_WORKER` | `1` | Run worker inside the web process (dev) |

## Development

```sh
pytest                 # 69 tests, no network needed
ruff check . && ruff format --check . && mypy src
```

## Disclaimers

- Not affiliated with or endorsed by Google. Scrapes publicly visible Play Store pages
  via the unofficial `google-play-scraper` library, which can break or be rate-limited
  at any time; data is a point-in-time snapshot, not authoritative.
- For personal/educational market research. You are responsible for complying with
  Google Play's Terms of Service and applicable laws when operating this software.
- Scraped reviews contain user-generated content. Reviewer names/images are dropped at
  ingest; do not redistribute scraped datasets without assessing your own data-protection
  obligations.
- No warranty; use at your own risk.
