# Deployment log — Review Lens on the Oracle Ampere box

Box: `ubuntu@144.24.101.125` (`~/reviews`, rsync-deployed, **not** a git repo).
TLS: metabase-stack Caddy → `reviewlens-web:8000` on the shared `edge` network
(web joins it via compose alias; see `docker-compose.yml`).
DB: `reviews-db-1` (Postgres 16). Pre-deploy backup every time:
`docker compose exec -T db pg_dump -U pmr -Fc playstore_reviews > backups/<name>.dump`.

## Standard deploy

```sh
# 0. Local gates: python3 -m pytest, ruff check, ruff format --check, frontend tsc + build
# 1. Backup (see above)
# 2. Sync (box .env is NEVER overwritten):
rsync -avz --delete --exclude='.env' --exclude='.git/' --exclude='node_modules/' \
  --exclude='frontend/dist/' --exclude='__pycache__/' --exclude='*.pyc' \
  --exclude='.venv/' --exclude='venv/' --exclude='*.db' --exclude='.pytest_cache/' \
  --exclude='.mypy_cache/' --exclude='.ruff_cache/' --exclude='.coverage' \
  --exclude='tsconfig.tsbuildinfo' --exclude='.claude/' --exclude='.claude-plugin/' \
  --exclude='backups/' --exclude='out/' \
  -e "ssh -i '<key>' -o BatchMode=yes" ./ ubuntu@144.24.101.125:~/reviews/
# 3. Schema deltas by hand (create_all only adds TABLES; new COLUMNS need ALTER).
# 4. Rebuild + restart: docker compose build web worker && docker compose up -d web worker
# 5. Verify: /api/health, homepage bundle strings, /api/me shape, one live compare in DB.
```

Gotchas learned:
- A plain `compose up` rename once broke the Caddy route (502s) — the `edge`
  alias in compose now pins it.
- Cookieless `curl` polls of `/api/compare/{id}` return "job not found" (anon
  identity is cookie-bound); verify via DB instead.
- Reasoning model needs `max_tokens=16000` + bounded prompt (6 themes / 2 quotes
  / 10 quotes) or big compares burn the budget and fall back to stub.

## History

- **2026-09-24** — Sarvam switch (`backups/db_pre_sarvam_*`).
- **2026-09-26 08:11 UTC** — Sarvam-only LLM, Dodo-only billing (`637e647`).
  DB: added `tier`, `dodo_customer_id` (+index), `subscription_status`,
  `paid_until`, `extra_credits` to `users`. Fixed Caddy 502 via edge alias.
  Proved first real Sarvam compare on prod; raised budget 6k→16k after
  diagnosing reasoning exhaustion.
- **2026-09-26 ~15:48 UTC** — UX gap closure (`2f821df`): landing pricing,
  honest quotas, checkout confirmation, paid-status header, compare paywall +
  titles, polling fixes, footer, ARIA/mobile pass. No schema change.
