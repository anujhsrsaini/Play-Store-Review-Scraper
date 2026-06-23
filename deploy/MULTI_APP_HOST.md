# Multi-app host on Oracle Ampere — architecture & runbook

How to run several subdomain apps (Review Lens is the first) on **one dedicated Oracle
Ampere VM (2 OCPU / 12 GB / ARM64)**, behind a single **host Caddy**, with **AWS Route 53**
pointing subdomains at the box. Synthesized from a 4-agent design panel
(proxy/DNS · security · resources/ops · deploy workflow).

```
Route 53 (AWS)                         Oracle Ampere box (one public IPv4)
  reviews.<domain>  ──A──►   ┌─ Caddy :80/:443 (host, systemd) ─┐
  app2.<domain>     ──A──►   │   import /opt/caddy/apps.d/*      │──► 127.0.0.1:8011  Review Lens stack
  app3.<domain>     ──A──►   └──────────────────────────────────┘──► 127.0.0.1:8012  app2 stack
                                                                  └─► 127.0.0.1:8013  app3 stack
Each app = its own Docker Compose stack (web [+ worker] + its OWN Postgres), bound to a
private 127.0.0.1 port. Only 80/443 are ever public.
```

## Opinionated decisions (the panel agreed on these)

| Area | Decision | Why |
|---|---|---|
| Reverse proxy | **Caddy installed on the host** (apt + systemd), not containerized | Reaches each app's `127.0.0.1:80xx` directly; one cert store; decoupled from app restarts. A containerized proxy would need `host.docker.internal`/host-net hacks. |
| TLS | **HTTP-01** (Caddy default), per-subdomain certs | Zero plugins/IAM; port 80 is open anyway. (Switch to DNS-01 + Route 53 plugin only if you want a `*.<domain>` wildcard or to close port 80.) |
| DNS | **Per-subdomain `A` records** → box IP, TTL 300 | Only declared names resolve (tighter than a wildcard). Apex untouched. |
| Database | **One Postgres container per app** | Isolation > the trivial ~40 MB/container overhead; independent backup/upgrade/restore. |
| Process mgmt | **Compose `restart: unless-stopped` + `systemctl enable docker`** | No per-app systemd units to maintain; everything returns after reboot. |
| Monitoring | **External uptime ping + cron disk alert + OCI budget alert** | No Prometheus/Grafana — wrong cost/benefit at this scale (would eat 1–2 GB). |
| Config layout | **Central Caddyfile that `import`s one symlinked vhost snippet per app** | Adding an app never hand-edits the main file; each app owns its snippet. |
| Capacity | **~8 app ceiling** on 12 GB / 2 OCPU | Bursty + uncorrelated; leaves OS page cache + build headroom. Resize trigger in §Ops. |

## 🔴 Must-do before public (blockers)

1. **Rotate the OCI `LLM_API_KEY`** — it was exposed in chat. (Git history is clean; no purge needed. Keep `OCI_GENAI_INTEGRATION.md`, which holds your tenancy OCID, untracked.)
2. **No container on `0.0.0.0`** — every published port is `127.0.0.1:<port>`; DB/worker publish nothing. Audit: `docker ps --format '{{.Names}} {{.Ports}}'`.
3. **Firewall closed at BOTH layers** — OCI security list allows only 80/443 (SSH source-restricted to your IP); host iptables ACCEPT for 80/443 inserted **above** Oracle's default REJECT.
4. **SSH hardened** — key-only, `PermitRootLogin no`, `PasswordAuthentication no`, non-root `deploy` user (verify a second session works before closing the first).
5. **Spend cap fails closed** — confirm `GLOBAL_DAILY_SPEND_CAP_USD` hard-stops OCI calls (not just warns) + set an OCI Budget alert as the provider-level backstop.
6. **Secrets**: each app's `.env` is `chmod 600`, owned by `deploy`, gitignored, never baked into an image.
7. **HTTPS cookies + OAuth exact-match**: `SESSION_COOKIE_SECURE=1`; Google redirect URI registered as the exact `https://reviews.<domain>/auth/callback`.

## 1. Provision the box (once)

Run `deploy/bootstrap-box.sh` on a fresh Ubuntu ARM64 Ampere instance (installs Docker +
compose, host Caddy, the `deploy` user, 4 GB swap, log rotation, unattended-upgrades,
fail2ban, and the iptables 80/443 ACCEPT). Then **manually** open 80/443 in the OCI
**security list** (console) — the script can't touch the cloud-side firewall.

Oracle gotchas the script handles, but verify:
- **iptables REJECT trap** — ACCEPT for 80/443 must sit *above* the catch-all REJECT, then `netfilter-persistent save`.
- **ARM64 images** — `caddy:2`, `postgres:16-alpine`, `python:3.12-slim`, `node:*` are all multi-arch; build on the box.
- **Boot volume** — bump to ~100 GB at create time (images + DB + 14 d backups + logs).
- **Always-Free reclaim** — an always-running service + the 5-min uptime ping keep the VM above the idle-reclaim threshold.

## 2. Layout & port registry

```
/opt/
├── apps/<app>/                 # git checkout per app: docker-compose.yml + deploy/ + .env
├── caddy/
│   ├── Caddyfile               # globals + `import /opt/caddy/apps.d/*.caddy`  (see deploy/Caddyfile.host)
│   └── apps.d/<app>.caddy ->   # symlink to /opt/apps/<app>/deploy/<app>.caddy
└── box/
    ├── PORTS.md                # PORT REGISTRY — check before picking a port (start 8011, +1)
    ├── Makefile                # operator helpers (deploy/Makefile)
    └── backups/                # pg_dump output, synced off-box
```

Port convention: container-internal port is always `8000`; the **published** localhost port
is unique per app, starting at **8011**. Record every app in `/opt/box/PORTS.md` *before*
deploying — a reused port is a silent bind failure.

## 3. Security & isolation (per app)

- **Per-app Docker networks**: a public-facing `edge` net + an `internal: true` `data` net for web↔db; DB never on `edge`, never published.
- **Hardening** on app containers: `user:` non-root, `no-new-privileges:true`, `cap_drop: [ALL]`, `read_only: true` + `tmpfs: [/tmp]` where the app allows.
- **Headers** (set once in Caddy, all apps): HSTS, X-Frame-Options DENY, X-Content-Type-Options nosniff, Referrer-Policy, CSP. App also sets its own CSP.
- **Per-host rate limit** in Caddy + the app's in-process per-user quota + global spend cap (a cost-abuse control).
- **fail2ban** on SSH; **unattended-upgrades** for the OS.
- **Cloudflare is optional/deferred** — it would hide the origin IP + add a WAF, but requires moving DNS off Route 53 or CNAME-proxying. Not needed for a closed-network launch; revisit as a hardening step. (Trade-off: the box's public IP is discoverable via the A record.)

## 4. Resource budget & day-2 ops

- **Per-container caps** (ceilings, not reservations): web `mem_limit 512m / cpus 0.5`, worker `768m / 0.75`, postgres `256m / 0.25`, Caddy (shared) `256m`. ~0.7 GB working set per app under load; ~8 apps fit 12 GB with headroom.
- **Swap**: 4 GB file, `vm.swappiness=10` (safety net, not capacity).
- **Postgres tuning** (small box): `max_connections=20`, `shared_buffers=128MB`, app pool `5–10`.
- **Backups**: nightly `pg_dump -Fc` per app + each `.env` + Caddy's data dir (ACME certs), synced off-box (OCI Object Storage free 20 GB), 14 d local / 30 d remote, **monthly restore test**. Encrypt dumps (`age`/`gpg` public key) so a box compromise can't read them.
- **Logs**: `/etc/docker/daemon.json` json-file `max-size=10m max-file=3`; journald `SystemMaxUse=500M`; Caddy access log with `roll_size`.
- **Monitoring**: healthchecks.io/UptimeRobot on each `https://<sub>/health`; cron disk-space alert (dead-man's switch); `docker stats`/`ctop` for spot checks; OCI Budget alert (50/80/100%) on GenAI.
- **Updates**: `make deploy app=<app>` (`git pull && compose up -d --build`), one app at a time, verify health; monthly `docker image prune`.
- **Resize trigger** (any, sustained): available RAM < 2 GB idle, swap > 1 GB routinely, load avg > 2.0, disk > 70%, or > 8 apps → step to 4 OCPU/24 GB or a second VM.

## 5. Add-an-app runbook (~10 min) — as the `deploy` user

1. Reserve the next port in `/opt/box/PORTS.md`.
2. `git clone <repo> /opt/apps/<app> && cd /opt/apps/<app>`
3. `cp env.example .env && $EDITOR .env` — set `DOMAIN`, the reserved port, generate secrets.
4. Route 53: add `A` record `<app>.<domain>` → box IP, TTL 300.
5. Edit `deploy/<app>.caddy` (subdomain + port), then `ln -sf` it into `/opt/caddy/apps.d/`.
6. `caddy validate --config /opt/caddy/Caddyfile && sudo systemctl reload caddy`
7. `docker compose -f docker-compose.yml -f deploy/compose.colocate.yml up -d --build`
8. Smoke: `curl -fsS http://127.0.0.1:<port>/health` then `curl -fsS https://<app>.<domain>/health`.

**Review Lens specifics** (app #1): set `LLM_*` (rotated OCI key) + `LLM_COMPARTMENT_ID`,
`GOOGLE_CLIENT_ID/SECRET`, `GOOGLE_REDIRECT_URI=https://reviews.<domain>/auth/callback`,
`SESSION_SECRET`, `SESSION_COOKIE_SECURE=1`, `POSTGRES_PASSWORD`, `GLOBAL_DAILY_SPEND_CAP_USD`,
`PER_USER_DAILY_ANALYSES=5`. Register the OAuth redirect URI in Google Console and set the
OCI budget alert. Review Lens already ships `deploy/compose.colocate.yml` (web → 127.0.0.1:8011,
bundled Caddy disabled, mem caps) and `deploy/reviews.caddy` — it *is* the template; new apps
are copies with the port parameterized.

## 6. Rollback & migrations

- **Code**: `git checkout <good-sha> && docker compose -f … -f deploy/compose.colocate.yml up -d --build`. Optionally tag images per deploy for instant rollback without a rebuild.
- **Schema caveat**: the app auto-creates tables but never *alters* them. A code rollback does **not** undo an applied schema change. Always `make backup app=<app>` before deploying model changes, and **introduce Alembic before the first breaking change to a populated DB**.

---
*Companion files in `deploy/`: `bootstrap-box.sh` (provisioning), `Caddyfile.host` (central
config), `Makefile` (operator helpers), plus the existing `compose.colocate.yml` and
`reviews.caddy` for Review Lens.*
