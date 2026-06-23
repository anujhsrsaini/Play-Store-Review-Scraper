# Plan — No-login free trial ("try before you sign in")

> Synthesized from a 4-agent panel (identity/quota · abuse/cost · conversion UX · implementation),
> then simplified in a lead review (see "Review simplifications" below).
> Problem: requiring Google sign-in *before* any value kills the funnel — live logs showed ~4 strangers
> clicked "Sign in with Google" and 0 completed (bounced at Google's consent screen).
> Goal: an anonymous visitor runs **1 free analysis** without login, then a "sign in for 5/day" upsell.

## Guiding principle
The header/landing **never blocks**. Anonymous and signed-in users see the same Search → Ask → Result
loop; only the *quota source* and the *upsell placement* differ. Sign-in is a step taken **after** the
aha moment, not a toll before the door. The **global daily LLM spend cap stays the hard backstop** — the
worst case of any abuse is bounded by one number you already control.

## Review simplifications (vs. the panel's first draft)
- **No new DB table.** The trial counter lives in the existing **signed session cookie** (per-browser, tamper-proof). Dropped `AnonUsage` — the spend sub-budget already bounds cost. → no schema change, no migration.
- **Financial safety = anon spend sub-budget + global cap**, not a per-IP row counter. Anon path turns off once the day's spend ≥ `ANON_SPEND_FRACTION` of the cap, so signed-in users always keep headroom and worst-case anon spend is a small fraction of the cap.
- **Rate-limit anon by client IP** (in-memory key, nothing stored) so cookie-clearing can't bypass the limiter.
- **No `?next=` redirect** — client-side `sessionStorage` replay restores the user's app+question after sign-in (no open-redirect surface).

## Identity & quota model
| Signal | Source | Used for |
|---|---|---|
| `anon_id` | random token in the **signed session cookie** (Starlette SessionMiddleware, already present) | job *ownership* (poll your own job) + the per-browser daily trial counter (`anon_day`, `anon_count`) |
| client IP | `request.client.host` (real IP via `--proxy-headers`; Caddy is sole upstream) | the **rate-limit key** for anon only — used in-memory, **never persisted** |

- **Trial:** `ANON_DAILY_ANALYSES` (default **1**) per browser per UTC day, counted in the signed cookie.
- **Spend sub-budget:** anon analyze is refused once `today_spend ≥ ANON_SPEND_FRACTION × global cap` (default 0.5) → "free trial is busy today, sign in to continue."
- Quota consumed **only on a cache MISS** — cached re-asks are free for anon too (identical to signed-in).
- Cookie-clearing/incognito can reset the per-browser counter; that's acceptable because the spend sub-budget + global cap bound the cost, and the rate limiter (keyed on IP) bounds burst. A persistent per-IP daily cap is deferred (see triggers).

## 🔴 Security must-fixes the panel caught (ship with MVP)
1. **Anon job-ownership gap.** Today `/api/jobs/{id}` treats `user_id IS NULL` as public → any anon could poll any anon job. Fix: stamp anon jobs with `user_id = "anon:<anon_id>"` so the existing ownership check binds each job to its creator's cookie.
2. **Rate-limiter rekey.** `rate_limit("analyze:{user.id}")` collapses for anon. Key anon requests on the **client IP** so each source gets its own bucket (and resets don't bypass it).
3. **No `share_token` for anon** — make durable share links a signed-in feature (a natural upsell). Token still exists internally; just not returned on the anon result payload.

## Backend changes (file → change)
- **config.py** — add `anon_trial_enabled` (`ANON_TRIAL_ENABLED`, default **0/off**), `anon_daily_analyses` (default 1), `anon_spend_fraction` (default 0.5). Add `anon_trial_active()` = `auth_enabled() and anon_trial_enabled` (inert in local dev). **No db.py change.**
- **auth.py** — add a `Principal` (a real `User`, or an anon `{id="anon:<anon_id>", is_anon=True}`). `resolve_principal()` reuses the *exact* existing user/local-user resolution (signed-in path byte-for-byte unchanged); only falls to anon when `anon_trial_active()` and no session, minting/reading `anon_id` from `request.session`. Add cookie-counter helpers `anon_trial_used(request)` / `consume_anon_trial(request)` (read/reset-by-day/increment in `request.session`). Keep `resolve_user` + `analyses_used_today` as-is.
- **webapp.py** — deps: `current_principal` (me/search/analyze/jobs). `/api/analyze`: shared cache + single-flight first (cache hit = free, no quota); on miss, branch — **signed-in block unchanged**; anon block checks (a) spend sub-budget, (b) per-browser cookie counter < `anon_daily_analyses`, then consumes + inserts `Job(user_id="anon:<id>")`. `/api/jobs/{id}` ownership reads `principal.id`. `/api/me` returns an anon shape (`authenticated:false`, `is_anon:true`, `used/quota/remaining`). `/api/health` exposes `anon_trial`. Rate-limit key: `f"analyze:{user.id}"` for signed-in, `f"analyze:ip:{client_ip}"` for anon. Don't include `share_token` in the result payload for anon.

## Frontend changes (file → change)
- **lib/api.ts** — `Me` gains `is_anon`; `Health` gains `anon_trial`. No call-signature changes (cookies already sent same-origin).
- **App.tsx / Home.tsx** — drop the upfront `SignInGate` for anon; 3-state machine: *anon-trial-left* (full app, header "1 free analysis"), *anon-trial-used* (gate card replaces AskPanel; Search still works for cached re-opens), *signed-in* (unchanged). Hero adds: *"No sign-up to try — your first analysis is on us."*
- **Header.tsx** — never blocks: anon→ "1 free analysis" + low-key "Sign in"; anon-used→ "Free analysis used" + gradient "Sign in for 5/day"; signed-in→ unchanged.
- **SignInUpsell.tsx** (new) — `variant: "soft" | "gate"`. Soft = below the first result ("Want more? Sign in for 5 free a day — no spam"). Gate = when trial used ("One more question? Sign in — it's free", **not a dead end**). Microcopy under the Google button: *"We only use your email to set your daily limit. No posting, ever."* On click, stash `{app, question, period}` in `sessionStorage` then go to `/auth/login`.
- **useAnalysis.ts** — treat an anon-exhausted response (429) as a clean transition to the gate (refetch `/api/me`), not a red error.
- **Home.tsx (replay)** — on mount, if a stashed intent exists and `me.authenticated`, restore the app + prefill the question (AskPanel gains optional `defaultQuestion`/`defaultPeriod`), then clear it.

## Recommended config
```
ANON_TRIAL_ENABLED=1            # flip on at launch (default 0 = off → instant rollback)
ANON_DAILY_ANALYSES=1           # per browser/day (the trial)
ANON_SPEND_FRACTION=0.5         # anon path off once daily spend ≥ 50% of cap (protect signed-in headroom)
GLOBAL_DAILY_SPEND_CAP_USD=...  # unchanged — the hard ceiling
PER_USER_DAILY_ANALYSES=5       # unchanged
```

## Test plan
Anon 1 → 2nd is gated (429) · anon cached re-ask is free (doesn't burn the trial) · signed-in still 5/day · anon polls only its own job (other anon → 404) · auth-disabled local mode unchanged · global spend cap still trips for anon-created jobs · anon spend sub-budget refuses anon when spend ≥ fraction · `/api/me` anon shape (and 401 when trial off) · `/api/health` exposes the flag. ruff + mypy clean.

## Rollout & rollback
Flag defaults **off** → deploying the code changes nothing until `ANON_TRIAL_ENABLED=1` is set. Deploy = re-ship + `docker compose -f docker-compose.yml -f deploy/compose.oracle.yml up -d --build`. Verify: `GET /api/health` → `anon_trial:true`; one anon `POST /api/analyze` → 200, second → 429; signed-in account still 5/5; landing reachable without login. **Rollback:** set `ANON_TRIAL_ENABLED=0` + redeploy.

## Deferred (build on evidence — these are the triggers)
- **Persistent per-IP daily cap** (the `AnonUsage` table) — if cookie-reset farming or Google-scrape load (many distinct apps from few sources) shows up.
- **Cloudflare Turnstile** on the anon path — if the spend cap starts hitting daily from anon traffic.
- **Conversion analytics** (anon→sign-in funnel) — once it's live and you want to measure/tune it.
- **Coalesced-poll edge** — a 2nd simultaneous identical question gets a job id it can't poll (pre-existing for signed-in too; rare because the 24h cache usually serves repeats). Fix only if it surfaces.

## MVP definition of done
- [ ] Settings (`anon_trial_enabled` off by default, `anon_daily_analyses`, `anon_spend_fraction`) + `Principal`/`resolve_principal` (signed-in path unchanged)
- [ ] Anon `/api/analyze` (cookie counter + spend sub-budget; cache hits free), anon-owned job polling, `/api/me` anon shape, `/api/health` flag, rate-limit rekey, no `share_token` for anon
- [ ] SPA: gate dropped for anon, anon badge, soft upsell after result + gate card when used, intent replay; signed-in UI unchanged
- [ ] Tests above pass; ruff + mypy clean
- [ ] Live verify on the box; rollback (flag off) confirmed
