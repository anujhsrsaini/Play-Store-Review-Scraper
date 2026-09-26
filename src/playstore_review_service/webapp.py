"""FastAPI web app: search, submit analysis job, poll status, serve the local UI.

Endpoints are sync ``def`` — Starlette runs them on a threadpool, so the blocking
scraper/DB calls never block the event loop. Errors return classified, generic
messages (spec §4.5): no stack traces, no upstream payloads, no key material.
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from . import analysis as an
from . import auth as auth_mod
from . import billing as billing_mod
from . import retention as retention_mod
from . import worker as worker_mod
from .config import DEFAULT_SESSION_SECRET, get_settings
from .db import (
    Analysis,
    ComparisonJob,
    ComparisonResult,
    Job,
    Snapshot,
    User,
    init_db,
    make_engine,
    make_session_factory,
    new_job_id,
)
from .scraper import client as scraper_client
from .scraper.errors import InvalidAppId, RateLimitedUpstream, ScraperError

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
SPA_DIR = STATIC_DIR / "spa"  # built React app (gitignored; produced by `npm run build`)
ACTIVE_STATUSES = ("queued", "scraping", "analyzing")
LOCALE_RE = re.compile(r"^[a-z]{2}$")
RATE_WINDOW_SECONDS = 60.0
RATE_MAX_PER_WINDOW = 40  # per identity, per endpoint group (in-process; Cloudflare for DDoS)
CSP = (
    "default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; "
    "frame-ancestors 'none'"
)


class AnalyzeRequest(BaseModel):
    app_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=3, max_length=500)
    country: str = Field(default="us", pattern=r"^[a-z]{2}$")
    lang: str = Field(default="en", pattern=r"^[a-z]{2}$")
    # Review time-window: sentiment + analysis are scoped to this period (see analysis.PERIOD_DAYS).
    period: str = Field(default="90d", pattern=r"^(30d|60d|90d)$")


class CompareRequest(BaseModel):
    app_a_id: str = Field(min_length=1, max_length=200)
    app_b_id: str = Field(min_length=1, max_length=200)
    country: str = Field(default="us", pattern=r"^[a-z]{2}$")
    lang: str = Field(default="en", pattern=r"^[a-z]{2}$")
    # Lookback window in days for both corpora (bounded: 1..365, no lifetime view).
    lookback_days: int = Field(default=90, ge=1, le=365)
    # Optional free-form angle ("battery life", "onboarding"); blank = general compare.
    custom_focus: str = Field(default="", max_length=500)


COMPARE_ACTIVE_STATUSES = ("queued", "fetching", "analyzing")


def _quota_for_tier(tier: str, settings) -> int:
    quota = settings.per_user_daily_analyses
    if tier == "starter":
        quota = 50
    elif tier == "pro":
        quota = 200
    return quota


def _enforce_analysis_quota(session, user_id: str, settings) -> None:
    """Combined daily quota over single analyses + Battle Lens compares, consumed
    only on a real cache MISS. Raises 429 past quota (spending an extra credit
    when the user has one)."""
    user = session.get(User, user_id)
    tier = user.tier if user else "free"
    quota = _quota_for_tier(tier, settings)
    used = auth_mod.analyses_used_today(session, user_id) + auth_mod.compares_used_today(
        session, user_id
    )
    if used >= quota:
        if user and user.extra_credits > 0:
            user.extra_credits -= 1
        else:
            raise HTTPException(
                429,
                f"daily limit reached ({quota} analyses) — "
                "resets at UTC midnight; upgrade for higher limits",
            )


def _compare_payload(
    record: ComparisonResult, snap_a: Snapshot | None, snap_b: Snapshot | None, share: bool = True
) -> dict:
    """Public payload for a finished comparison. Snapshots may be None (purged after
    the result was computed) — the persisted result JSON is self-contained."""
    payload = {
        "app_a_id": record.app_a_id,
        "app_b_id": record.app_b_id,
        "country": record.country,
        "lang": record.lang,
        "lookback_days": record.lookback_days,
        "comparison": record.result,
        "model": record.model,
        "snapshots": {
            "a_fetched_at": snap_a.fetched_at.isoformat() + "Z" if snap_a else None,
            "b_fetched_at": snap_b.fetched_at.isoformat() + "Z" if snap_b else None,
        },
    }
    if share:
        payload["share_token"] = record.share_token
    return payload


# Plans 3.4: every API body is small JSON (questions ≤500 chars, ids ≤200 chars);
# anything bigger is abuse. Checked on Content-Length before the body is read.
MAX_BODY_BYTES = 1_000_000


def create_app() -> FastAPI:
    settings = get_settings()
    # Fail safe: never run a real auth deployment with the public default signing key,
    # which would let anyone forge a session cookie for any user.
    if settings.auth_enabled() and settings.session_secret == DEFAULT_SESSION_SECRET:
        raise RuntimeError(
            "SESSION_SECRET must be set to a strong random value when auth is enabled "
            "(refusing to start with the insecure default)."
        )
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    rate_hits: dict[str, deque[float]] = defaultdict(deque)

    def rate_limit(key: str) -> None:
        now = time.monotonic()
        dq = rate_hits[key]
        while dq and dq[0] < now - RATE_WINDOW_SECONDS:
            dq.popleft()
        if len(dq) >= RATE_MAX_PER_WINDOW:
            raise HTTPException(429, "rate_limited")
        dq.append(now)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db(engine)
        stop = None
        if settings.dev_inprocess_worker:
            stop = worker_mod.start_inprocess_worker(session_factory)
            logger.info("dev in-process worker started")
        yield
        if stop is not None:
            stop.set()

    app = FastAPI(
        title="Play Store Review Analysis", lifespan=lifespan, docs_url=None, redoc_url=None
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=settings.session_cookie_secure,
    )
    # Same-origin UI + API: no cross-origin browser access, stated explicitly so the
    # posture is deliberate (and tested) rather than a Starlette default side effect.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_methods=[],
        allow_headers=[],
    )
    oauth = auth_mod.make_oauth(settings)
    auth_mod.setup_auth_routes(app, oauth, session_factory, settings)

    def current_principal(request: Request) -> auth_mod.Principal:
        return auth_mod.resolve_principal(request, session_factory, settings)

    @app.exception_handler(Exception)
    async def unhandled(_request, exc):  # spec §4.5: never serialize exceptions out
        logger.exception("unhandled error: %s", type(exc).__name__)
        return JSONResponse(status_code=500, content={"error": "internal_error"})

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Content-Security-Policy", CSP)
        return resp

    @app.middleware("http")
    async def body_size_limit(request: Request, call_next):
        try:
            content_length = int(request.headers.get("content-length", "0"))
        except ValueError:
            content_length = 0
        if content_length > MAX_BODY_BYTES:
            return JSONResponse(status_code=413, content={"detail": "payload_too_large"})
        return await call_next(request)

    @app.get("/api/health")
    def health() -> dict:
        provider = settings.provider()
        detail = {
            "openai_compatible": f"Sarvam ({settings.llm_model})",
            "stub": "stub (no LLM configured)",
        }[provider]
        return {
            "ok": True,
            "provider": provider,
            "llm": detail,
            "auth": settings.auth_enabled(),
            "anon_trial": settings.anon_trial_active(),
        }

    @app.get("/api/me")
    def me(request: Request, p: auth_mod.Principal = Depends(current_principal)) -> dict:
        if p.is_anon:
            quota = settings.anon_daily_analyses
            used = auth_mod.anon_trial_used(request)
            return {
                "authenticated": False,
                "is_anon": True,
                "email": "",
                "name": "",
                "tier": "free",
                "is_paid": False,
                "extra_credits": 0,
                "used": used,
                "quota": quota,
                "remaining": max(0, quota - used),
            }
        with session_factory() as session:
            used = auth_mod.analyses_used_today(session, p.id) + auth_mod.compares_used_today(
                session, p.id
            )
            user = session.get(User, p.id)
            tier = user.tier if user else "free"
            extra_credits = user.extra_credits if user else 0
        quota = _quota_for_tier(tier, settings)
        return {
            "authenticated": settings.auth_enabled(),
            "is_anon": False,
            "email": p.email,
            "name": p.name,
            "tier": tier,
            "is_paid": tier in ("starter", "pro") or extra_credits > 0,
            "extra_credits": extra_credits,
            "used": used,
            "quota": quota,
            "remaining": max(0, quota - used) + extra_credits,
        }

    @app.delete("/api/me/data")
    def delete_my_data(p: auth_mod.Principal = Depends(current_principal)) -> dict:
        """Privacy (Plans 3.5): delete the caller's owned data — their jobs (+ linked
        usage/metric rows) and their user row. The shared de-identified review cache
        is untouched. Anonymous trial callers delete their own browser's jobs."""
        with session_factory() as session:
            counts = retention_mod.delete_user_data(session, p.id)
        return {"deleted": counts}

    @app.get("/api/search")
    def search(
        q: str,
        request: Request,
        country: str = "us",
        lang: str = "en",
        p: auth_mod.Principal = Depends(current_principal),
    ) -> list[dict]:
        rate_limit(f"search:ip:{auth_mod.client_ip(request)}" if p.is_anon else f"search:{p.id}")
        if (
            not (1 <= len(q.strip()) <= 200)
            or not LOCALE_RE.match(country)
            or not LOCALE_RE.match(lang)
        ):
            raise HTTPException(422, "invalid query")
        try:
            apps = scraper_client.search_apps(q.strip(), country=country, lang=lang, n_hits=8)
        except RateLimitedUpstream as exc:
            raise HTTPException(429, "rate_limited_upstream") from exc
        except (ScraperError, ValueError):
            # Generic code only (spec §4.5): never serialize internal exception names.
            raise HTTPException(502, "upstream_unavailable") from None
        return [
            {
                "app_id": a.app_id,
                "title": a.title,
                "score": a.score,
                "installs": a.installs,
                "developer": a.developer,
                "free": a.free,
                "icon": a.icon,
            }
            for a in apps
            if a.app_id  # drop featured/cluster results that come back without a usable id
        ]

    @app.post("/api/analyze")
    def analyze(
        req: AnalyzeRequest,
        request: Request,
        p: auth_mod.Principal = Depends(current_principal),
    ) -> dict:
        rate_limit(f"analyze:ip:{auth_mod.client_ip(request)}" if p.is_anon else f"analyze:{p.id}")
        try:
            scraper_client.validate_app_id(req.app_id)
        except InvalidAppId as exc:
            raise HTTPException(422, "invalid app_id") from exc
        qhash = an.question_hash(req.question)
        with session_factory() as session:
            # Serialize a signed-in user's concurrent submits (row lock) so the quota count
            # can't be raced past by parallel requests. (Anon trial is cookie-counted.)
            if not p.is_anon:
                session.get(User, p.id, with_for_update=True)
            # Answer cache: fresh snapshot + same normalized question → no job, no LLM.
            hit = session.execute(
                select(Analysis, Snapshot)
                .join(Snapshot, Analysis.snapshot_id == Snapshot.id)
                .where(
                    Analysis.app_id == req.app_id,
                    Analysis.country == req.country,
                    Analysis.lang == req.lang,
                    Analysis.question_hash == qhash,
                    Analysis.period == req.period,
                    Snapshot.expires_at > worker_mod.utcnow(),
                )
            ).first()
            if hit is not None:
                record, snap = hit
                return {
                    "job_id": None,
                    "status": "done",
                    "cache_hit": True,
                    "result": _result_payload(record, snap, share=not p.is_anon),
                }
            # Single-flight: coalesce into an identical active job.
            active = (
                session.execute(
                    select(Job).where(
                        Job.app_id == req.app_id,
                        Job.country == req.country,
                        Job.lang == req.lang,
                        Job.question_hash == qhash,
                        Job.period == req.period,
                        Job.status.in_(ACTIVE_STATUSES),
                    )
                )
                .scalars()
                .first()
            )
            if active is not None:
                return {"job_id": active.id, "status": active.status, "cache_hit": False}
            # Quota is consumed only on a real cache MISS (new job). Cached re-asks are free.
            if p.is_anon:
                # Protect signed-in headroom: stop serving the free trial once the day's spend
                # has eaten its allotted fraction of the global cap.
                cap = settings.global_daily_spend_cap_usd
                if worker_mod.today_spend_usd(session) >= settings.anon_spend_fraction * cap:
                    raise HTTPException(
                        429, "the free trial is busy right now — sign in to keep analyzing"
                    )
                if auth_mod.anon_trial_used(request) >= settings.anon_daily_analyses:
                    raise HTTPException(
                        429,
                        f"free analysis used — sign in with Google for "
                        f"{settings.per_user_daily_analyses} a day (free)",
                    )
                auth_mod.consume_anon_trial(request)
            else:
                _enforce_analysis_quota(session, p.id, settings)
            job = Job(
                id=new_job_id(),
                user_id=p.id,  # "anon:<id>" for anon → binds the job to its creator's cookie
                app_id=req.app_id,
                country=req.country,
                lang=req.lang,
                question=req.question.strip(),
                question_hash=qhash,
                period=req.period,
            )
            session.add(job)
            session.commit()
            return {"job_id": job.id, "status": "queued", "cache_hit": False}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str, p: auth_mod.Principal = Depends(current_principal)) -> dict:
        if not job_id.isalnum() or len(job_id) > 32:
            raise HTTPException(422, "invalid job id")
        with session_factory() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise HTTPException(404, "job not found")
            if job.user_id is not None and job.user_id != p.id:
                raise HTTPException(404, "job not found")  # 404 (not 403) — don't confirm existence
            out: dict = {
                "job_id": job.id,
                "status": job.status,
                "progress": job.progress,
                "error": job.error,
            }
            if job.status == "done" and job.analysis_id is not None:
                record = session.get(Analysis, job.analysis_id)
                snap = session.get(Snapshot, job.snapshot_id)
                out["result"] = _result_payload(record, snap, share=not p.is_anon)
            return out

    @app.get("/api/analysis/{token}")
    def get_analysis(token: str) -> dict:
        """Shareable permalink: fetch an analysis by its unguessable share_token.
        Public by design (so shared links open for logged-out viewers), but the token is a
        32-char random hex — not a sequential id — so the set can't be enumerated."""
        if not token.isalnum() or len(token) != 32:
            raise HTTPException(404, "analysis not found")
        with session_factory() as session:
            record = (
                session.execute(select(Analysis).where(Analysis.share_token == token))
                .scalars()
                .first()
            )
            if record is None:
                raise HTTPException(404, "analysis not found")
            snap = session.get(Snapshot, record.snapshot_id)
            return _result_payload(record, snap)

    @app.post("/api/compare")
    def compare(
        req: CompareRequest,
        request: Request,
        p: auth_mod.Principal = Depends(current_principal),
    ) -> dict:
        """Battle Lens: enqueue a side-by-side comparison of two apps.

        Mirrors /api/analyze semantics: canonical pair order (so A-vs-B and B-vs-A
        coalesce), fresh-result cache hit, single-flight on an active job, and quota
        consumed only on a real cache MISS. The worker reuses the shared snapshots
        for both apps, scopes each corpus to ``lookback_days`` at analysis time,
        and persists a ``ComparisonResult``.
        """
        rate_limit(f"compare:ip:{auth_mod.client_ip(request)}" if p.is_anon else f"compare:{p.id}")
        try:
            scraper_client.validate_app_id(req.app_a_id)
            scraper_client.validate_app_id(req.app_b_id)
        except InvalidAppId as exc:
            raise HTTPException(422, "invalid app_id") from exc
        app_a_id, app_b_id = sorted((req.app_a_id, req.app_b_id))
        if app_a_id == app_b_id:
            raise HTTPException(422, "identical apps")
        pair_hash = an.canonical_pair_hash(app_a_id, app_b_id)
        focus_hash = an.custom_focus_hash(req.custom_focus or None)
        with session_factory() as session:
            if not p.is_anon:
                session.get(User, p.id, with_for_update=True)
            # Cache hit: a result for this exact key whose BOTH snapshots are fresh.
            snap_a = (
                session.execute(
                    select(Snapshot).where(
                        Snapshot.app_id == app_a_id,
                        Snapshot.country == req.country,
                        Snapshot.lang == req.lang,
                    )
                )
                .scalars()
                .first()
            )
            snap_b = (
                session.execute(
                    select(Snapshot).where(
                        Snapshot.app_id == app_b_id,
                        Snapshot.country == req.country,
                        Snapshot.lang == req.lang,
                    )
                )
                .scalars()
                .first()
            )
            now = worker_mod.utcnow()
            if (
                snap_a is not None
                and snap_b is not None
                and snap_a.expires_at > now
                and snap_b.expires_at > now
            ):
                snaps_hash = an.snapshots_hash(
                    app_a_id,
                    snap_a.id,
                    snap_a.fetched_at.isoformat(),
                    app_b_id,
                    snap_b.id,
                    snap_b.fetched_at.isoformat(),
                )
                hit = (
                    session.execute(
                        select(ComparisonResult).where(
                            ComparisonResult.canonical_pair_hash == pair_hash,
                            ComparisonResult.country == req.country,
                            ComparisonResult.lang == req.lang,
                            ComparisonResult.lookback_days == req.lookback_days,
                            ComparisonResult.custom_focus_hash == focus_hash,
                            ComparisonResult.snapshots_hash == snaps_hash,
                        )
                    )
                    .scalars()
                    .first()
                )
                if hit is not None:
                    return {
                        "job_id": None,
                        "status": "done",
                        "cache_hit": True,
                        "result": _compare_payload(hit, snap_a, snap_b, share=not p.is_anon),
                    }
            # Single-flight: coalesce into an identical active job (focus text
            # compared in Python — blank/None normalize to the same empty focus).
            wanted_focus = req.custom_focus or ""
            actives = (
                session.execute(
                    select(ComparisonJob).where(
                        ComparisonJob.canonical_pair_hash == pair_hash,
                        ComparisonJob.country == req.country,
                        ComparisonJob.lang == req.lang,
                        ComparisonJob.lookback_days == req.lookback_days,
                        ComparisonJob.status.in_(COMPARE_ACTIVE_STATUSES),
                    )
                )
                .scalars()
                .all()
            )
            for candidate in actives:
                if (candidate.custom_focus or "") == wanted_focus:
                    return {
                        "job_id": candidate.id,
                        "status": candidate.status,
                        "cache_hit": False,
                    }
            # Quota is consumed only on a real cache MISS (new job).
            if p.is_anon:
                cap = settings.global_daily_spend_cap_usd
                if worker_mod.today_spend_usd(session) >= settings.anon_spend_fraction * cap:
                    raise HTTPException(
                        429, "the free trial is busy right now — sign in to keep analyzing"
                    )
                if auth_mod.anon_trial_used(request) >= settings.anon_daily_analyses:
                    raise HTTPException(
                        429,
                        f"free analysis used — sign in with Google for "
                        f"{settings.per_user_daily_analyses} a day (free)",
                    )
                auth_mod.consume_anon_trial(request)
            else:
                _enforce_analysis_quota(session, p.id, settings)
            job = ComparisonJob(
                id=new_job_id(),
                user_id=p.id,
                app_a_id=app_a_id,
                app_b_id=app_b_id,
                canonical_pair_hash=pair_hash,
                country=req.country,
                lang=req.lang,
                lookback_days=req.lookback_days,
                custom_focus=req.custom_focus or None,
                status="queued",
                progress_stage="queued",
                progress_detail="Queued",
            )
            session.add(job)
            session.commit()
            return {"job_id": job.id, "status": "queued", "cache_hit": False}

    @app.get("/api/compare/{job_id}")
    def compare_status(job_id: str, p: auth_mod.Principal = Depends(current_principal)) -> dict:
        if not job_id.isalnum() or len(job_id) > 32:
            raise HTTPException(422, "invalid job id")
        with session_factory() as session:
            job = session.get(ComparisonJob, job_id)
            if job is None:
                raise HTTPException(404, "job not found")
            if job.user_id is not None and job.user_id != p.id:
                raise HTTPException(404, "job not found")  # 404 (not 403) — don't confirm existence
            out: dict = {
                "job_id": job.id,
                "status": job.status,
                "progress_stage": job.progress_stage,
                "progress_percent": job.progress_percent,
                "progress_detail": job.progress_detail,
                "error": job.error,
            }
            if job.status == "done" and job.comparison_result_id is not None:
                record = session.get(ComparisonResult, job.comparison_result_id)
                snap_a = session.get(Snapshot, job.snapshot_a_id)
                snap_b = session.get(Snapshot, job.snapshot_b_id)
                out["result"] = _compare_payload(record, snap_a, snap_b, share=not p.is_anon)
            return out

    @app.get("/api/compare/result/{token}")
    def compare_permalink(token: str) -> dict:
        """Shareable permalink for a comparison (public; unguessable 32-char token)."""
        if not token.isalnum() or len(token) != 32:
            raise HTTPException(404, "comparison not found")
        with session_factory() as session:
            record = (
                session.execute(
                    select(ComparisonResult).where(ComparisonResult.share_token == token)
                )
                .scalars()
                .first()
            )
            if record is None:
                raise HTTPException(404, "comparison not found")
            snap_a = (
                session.execute(
                    select(Snapshot).where(
                        Snapshot.app_id == record.app_a_id,
                        Snapshot.country == record.country,
                        Snapshot.lang == record.lang,
                    )
                )
                .scalars()
                .first()
            )
            snap_b = (
                session.execute(
                    select(Snapshot).where(
                        Snapshot.app_id == record.app_b_id,
                        Snapshot.country == record.country,
                        Snapshot.lang == record.lang,
                    )
                )
                .scalars()
                .first()
            )
            return _compare_payload(record, snap_a, snap_b)

    @app.get("/api/billing/checkout")
    def billing_checkout(
        request: Request,
        plan: str = "starter",
        p: auth_mod.Principal = Depends(current_principal),
    ):
        base_url = str(request.base_url).rstrip("/")
        if p.is_anon:
            return RedirectResponse(url=f"/auth/login?next=/api/billing/checkout?plan={plan}")
        checkout_url = billing_mod.create_checkout_session(
            user_id=p.id,
            email=p.email,
            plan=plan,
            settings=settings,
            base_url=base_url,
        )
        return RedirectResponse(url=checkout_url)

    @app.get("/api/billing/mock-activate")
    def billing_mock_activate(
        plan: str = "starter",
        p: auth_mod.Principal = Depends(current_principal),
    ):
        # Dev/test escape hatch: grants paid tiers WITHOUT payment, so it must never
        # exist in prod (404 unless ALLOW_MOCK_BILLING=1) and must never accept an
        # arbitrary user id (previously an anonymous caller could upgrade any account
        # via ?user_id=<victim>). Always activates the caller's own identity.
        if not settings.allow_mock_billing or p.is_anon:
            raise HTTPException(404, "not found")
        uid = p.id
        with session_factory() as session:
            user = session.get(User, uid)
            if user is None:
                user = auth_mod.upsert_user(session, uid, "local@localhost", "Local User")
            if plan in ("starter", "pro"):
                user.tier = plan
                user.subscription_status = "active"
            elif plan == "pass":
                user.extra_credits += 20
            session.commit()
        return RedirectResponse(url=f"/?checkout=success&plan={plan}")

    @app.post("/api/billing/webhook")
    async def billing_webhook(request: Request):
        payload = await request.body()
        sig_header = request.headers.get("webhook-signature")
        try:
            res = billing_mod.handle_webhook_event(
                payload=payload,
                sig_header=sig_header,
                session_factory=session_factory,
                settings=settings,
                headers=dict(request.headers),
            )
            return res
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    # Privacy policy (Plans 3.5/5.3): static, script-free page. Registered before the
    # SPA catch-all so /privacy always serves this document, never index.html.
    @app.get("/privacy", include_in_schema=False)
    def privacy() -> FileResponse:
        return FileResponse(STATIC_DIR / "privacy.html", media_type="text/html")

    # Serve the built React SPA when present (built via `frontend && npm run build`);
    # otherwise fall back to the legacy single-file UI. The catch-all returns index.html
    # for client-side routes (e.g. /a/:id) while leaving /api and /auth untouched.
    spa_index = SPA_DIR / "index.html"
    if spa_index.exists():
        if (SPA_DIR / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=SPA_DIR / "assets"), name="assets")

        spa_root = SPA_DIR.resolve()

        @app.get("/{full_path:path}")
        def spa(full_path: str) -> FileResponse:
            if full_path.startswith(("api/", "auth/")):
                raise HTTPException(404, "not found")
            if full_path:
                candidate = (SPA_DIR / full_path).resolve()
                # Containment check: never serve a file outside the built SPA dir, even if
                # full_path contains ../ traversal (else arbitrary file disclosure).
                if candidate.is_relative_to(spa_root) and candidate.is_file():
                    return FileResponse(candidate)
            return FileResponse(spa_index)
    else:

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    return app


def _result_payload(record: Analysis, snap: Snapshot, share: bool = True) -> dict:
    meta = snap.app_meta or {}
    payload = {
        "app": {
            "app_id": snap.app_id,
            "title": meta.get("title"),
            "score": meta.get("score"),
            "ratings": meta.get("ratings"),
            "installs": meta.get("installs"),
            "version": meta.get("version"),
            "histogram": meta.get("histogram"),
            "icon": meta.get("icon"),
            "reviews": meta.get("reviews"),  # lifetime review count (for the methodology strip)
        },
        "analysis_id": record.id,
        "snapshot": {
            "fetched_at": snap.fetched_at.isoformat() + "Z",
            "review_count": snap.review_count,
            "sort": snap.sort,
            "complete": snap.complete,
        },
        "model": record.model,
        "period": record.period,
        "answer": record.answer,
    }
    # Durable share links are a signed-in feature (a sign-in upsell for anon trial users).
    if share:
        payload["share_token"] = record.share_token
    return payload


def main() -> None:  # pragma: no cover - `pmr-serve` entrypoint
    import os

    import uvicorn

    logging.basicConfig(level=logging.INFO)
    # Factory mode: the app is built per-process at startup (no module-level instance), so
    # importing this module has no side effects and can't crash on a config guard.
    uvicorn.run(
        "playstore_review_service.webapp:create_app",
        factory=True,
        host=os.environ.get("HOST", "127.0.0.1"),  # set 0.0.0.0 in Docker
        port=int(os.environ.get("PORT", "8000")),
        proxy_headers=True,  # trust X-Forwarded-* only from the configured proxy
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
    )
