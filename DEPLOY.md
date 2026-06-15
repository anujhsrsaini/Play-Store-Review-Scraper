# Deploying to the Oracle Ampere box

The stack runs as four containers via Docker Compose: **Caddy** (public HTTPS) →
**web** (FastAPI) + **worker** (scrape/LLM) + **Postgres**.

## One-time owner setup

1. **Google OAuth client** (Google Cloud Console → APIs & Services → Credentials →
   OAuth client ID → *Web application*):
   - Authorized redirect URI: `https://<your-subdomain>/auth/callback`
   - Put the client id/secret and the same URI into `.env` as `GOOGLE_CLIENT_ID`,
     `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`.
2. **DNS**: an `A` record for `<your-subdomain>` → the Ampere box's public IP.
   Open ports **80** and **443** in the OCI security list / firewall.
3. **OCI cost guard** (the backstop your code can't bypass): set a **budget + alert**
   in the OCI console for the Generative AI service, and keep `GLOBAL_DAILY_SPEND_CAP_USD`
   in `.env` as the in-app kill-switch.
4. Generate a strong `SESSION_SECRET` and `POSTGRES_PASSWORD`. **Required when auth is on** —
   the app refuses to start with the default secret. Generate one with:
   `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`

## `.env` for the box

Copy `env.example` → `.env` and fill in. Required for prod:

```
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://inference.generativeai.us-ashburn-1.oci.oraclecloud.com/openai/v1
LLM_API_KEY=...                  # OCI GenAI key
LLM_COMPARTMENT_ID=ocid1...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=https://<your-subdomain>/auth/callback
SESSION_SECRET=<long-random>
SESSION_COOKIE_SECURE=1
DOMAIN=<your-subdomain>
POSTGRES_PASSWORD=<strong>
GLOBAL_DAILY_SPEND_CAP_USD=20
PER_USER_DAILY_ANALYSES=15
```

## Run

```sh
docker compose up -d --build      # build images + start all 4 services
docker compose logs -f web        # watch startup
docker compose ps                 # health
```

Caddy obtains the TLS cert automatically on first request to `https://<your-subdomain>`.
Tables are created on first start (idempotent). Updates: `git pull && docker compose up -d --build`.

## Notes

- **Dependencies are pinned** in `requirements.txt` (the prod image installs from it, not the
  pyproject ranges). Regenerate after changing deps: `pip-compile --extra postgres pyproject.toml`.
- **Job bound**: a single analysis is bounded by `MAX_REVIEWS_PER_ANALYSIS` (scrape) + the LLM
  HTTP read timeout (OCI: 120s) — a few minutes worst case. There is no hard kill (Python can't
  safely terminate a thread); a future move to a Celery-style worker with `soft_time_limit` adds one.
- **Rate limiting** is in-process (per web container). Put Cloudflare in front for real DDoS
  protection — it also hides the origin IP and adds a WAF.
- **Schema migrations**: v1 auto-creates tables on startup (fine for a fresh DB). Introduce
  Alembic before making breaking schema changes to a populated DB.
- **Backups**: `docker compose exec db pg_dump -U $POSTGRES_USER playstore_reviews > backup.sql`.
- **Local stack test** (no domain/TLS): `docker compose up -d --build db web worker` and hit
  `web` directly, or just use `pmr-serve` against SQLite for everyday local dev.
