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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from . import analysis as an
from . import auth as auth_mod
from . import worker as worker_mod
from .config import DEFAULT_SESSION_SECRET, get_settings
from .db import (
    Analysis,
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

    @app.get("/api/health")
    def health() -> dict:
        provider = settings.provider()
        detail = {
            "openai_compatible": f"OCI/OpenAI-compatible ({settings.llm_model})",
            "gemini": f"gemini ({settings.gemini_model})",
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
                "used": used,
                "quota": quota,
                "remaining": max(0, quota - used),
            }
        with session_factory() as session:
            used = auth_mod.analyses_used_today(session, p.id)
        quota = settings.per_user_daily_analyses
        return {
            "authenticated": settings.auth_enabled(),
            "is_anon": False,
            "email": p.email,
            "name": p.name,
            "used": used,
            "quota": quota,
            "remaining": max(0, quota - used),
        }

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
        except (ScraperError, ValueError) as exc:
            raise HTTPException(502, type(exc).__name__) from exc
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
                used = auth_mod.analyses_used_today(session, p.id)
                if used >= settings.per_user_daily_analyses:
                    raise HTTPException(
                        429,
                        f"daily limit reached ({settings.per_user_daily_analyses} analyses) — "
                        "resets at UTC midnight; cached re-asks remain free",
                    )
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
