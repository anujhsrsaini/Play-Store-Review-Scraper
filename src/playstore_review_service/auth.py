"""Authentication & per-user quota (Plans 6.2, 6.3).

Google OAuth via Authlib + a signed session cookie. When OAuth creds are unset (local
dev), auth is DISABLED and a single local user is used so localhost works with zero setup
— mirroring the LLM stub fallback. In prod (creds set), a session is required.

Per-user daily quota is measured as the number of jobs the user created today (a job is
only created on a cache MISS, so cached re-asks are free and don't consume quota — spec
§4.4). The global spend kill-switch (worker) is the owner-side backstop on top of this.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime

from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .db import Job, User, utcnow

logger = logging.getLogger(__name__)

LOCAL_USER_ID = "local"


@dataclass(frozen=True, slots=True)
class Principal:
    """The caller of a request: either an authenticated User or an anonymous trial visitor.
    `id` is the job-ownership key (User.id, or "anon:<random>"); routes branch on `is_anon`."""

    id: str
    is_anon: bool
    email: str = ""
    name: str = ""
    user: User | None = None


def make_oauth(settings: Settings):
    """Build the Authlib Google client, or None when auth is disabled (local dev)."""
    if not settings.auth_enabled():
        return None
    from authlib.integrations.starlette_client import OAuth  # lazy: only when configured

    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth


def upsert_user(session: Session, user_id: str, email: str = "", name: str = "") -> User:
    user = session.get(User, user_id)
    if user is None:
        user = User(id=user_id, email=email, name=name)
        session.add(user)
        session.commit()
    return user


def resolve_user(request: Request, session_factory: sessionmaker, settings: Settings) -> User:
    """Return the current user from the session, or the local dev user when auth is off.

    Raises 401 when auth is enabled and there is no valid session.
    """
    uid = request.session.get("user_id")
    with session_factory() as session:
        if uid:
            user = session.get(User, uid)
            if user is not None:
                return user
        if not settings.auth_enabled():
            user = upsert_user(session, LOCAL_USER_ID, "local@localhost", "Local Dev")
            request.session["user_id"] = LOCAL_USER_ID
            return user
    raise HTTPException(401, "auth_required")


def client_ip(request: Request) -> str:
    """Real client IP. uvicorn runs with --proxy-headers + FORWARDED_ALLOW_IPS, so
    request.client is already rewritten from the trusted proxy's X-Forwarded-For. Used only
    as an in-memory rate-limit key for anonymous traffic — never persisted."""
    return request.client.host if request.client else "unknown"


def _anon_id(request: Request) -> str:
    """Stable random id for this browser, minted into the signed session cookie."""
    aid = request.session.get("anon_id")
    if not aid:
        aid = secrets.token_hex(16)
        request.session["anon_id"] = aid
    return aid


def anon_trial_used(request: Request) -> int:
    """Trial analyses this browser has spent today (counter in the signed session cookie)."""
    if request.session.get("anon_day") != utcnow().date().isoformat():
        return 0
    return int(request.session.get("anon_count", 0))


def consume_anon_trial(request: Request) -> None:
    """Increment today's per-browser trial counter (resets on a new UTC day)."""
    today = utcnow().date().isoformat()
    if request.session.get("anon_day") != today:
        request.session["anon_day"] = today
        request.session["anon_count"] = 0
    request.session["anon_count"] = int(request.session.get("anon_count", 0)) + 1


def resolve_principal(
    request: Request, session_factory: sessionmaker, settings: Settings
) -> Principal:
    """Authenticated User → Principal(is_anon=False). When auth is off (local dev), the local
    user. When auth is on but there's no session: an anonymous Principal if the trial is active,
    else 401. The signed-in/local resolution is identical to resolve_user (unchanged behavior)."""
    uid = request.session.get("user_id")
    with session_factory() as session:
        if uid:
            user = session.get(User, uid)
            if user is not None:
                return Principal(user.id, False, user.email, user.name, user)
        if not settings.auth_enabled():
            user = upsert_user(session, LOCAL_USER_ID, "local@localhost", "Local Dev")
            request.session["user_id"] = LOCAL_USER_ID
            return Principal(user.id, False, user.email, user.name, user)
    if settings.anon_trial_active():
        return Principal(f"anon:{_anon_id(request)}", True)
    raise HTTPException(401, "auth_required")


def analyses_used_today(session: Session, user_id: str) -> int:
    midnight = datetime.combine(utcnow().date(), dtime.min)
    return int(
        session.execute(
            select(func.count(Job.id)).where(Job.user_id == user_id, Job.created_at >= midnight)
        ).scalar_one()
    )


def setup_auth_routes(app, oauth, session_factory: sessionmaker, settings: Settings) -> None:
    from fastapi.responses import RedirectResponse

    @app.get("/auth/login")
    async def auth_login(request: Request):
        if oauth is None:  # auth disabled — nothing to do
            return RedirectResponse("/")
        # Prefer the explicit, registered redirect URI (correct behind a TLS proxy where the
        # auto-derived URL can come back as http://); fall back to deriving it from the request.
        redirect_uri = settings.google_redirect_uri or str(request.url_for("auth_callback"))
        return await oauth.google.authorize_redirect(request, redirect_uri)

    @app.get("/auth/callback", name="auth_callback")
    async def auth_callback(request: Request):  # pragma: no cover - needs live Google
        if oauth is None:
            return RedirectResponse("/")
        try:
            token = await oauth.google.authorize_access_token(request)
        except Exception as exc:
            # Log the OAuth provider's reason (e.g. "invalid_client") — safe, no secrets —
            # so misconfig is diagnosable. The client only ever sees the generic code.
            detail = getattr(exc, "error", "") or getattr(exc, "description", "")
            logger.warning("oauth callback failed: %s %s", type(exc).__name__, detail)
            raise HTTPException(400, "oauth_failed") from exc
        info = token.get("userinfo") or {}
        sub = info.get("sub")
        if not sub:
            raise HTTPException(400, "oauth_no_subject")
        if info.get("email_verified") is False:
            raise HTTPException(400, "email_not_verified")
        with session_factory() as session:
            upsert_user(session, sub, info.get("email", ""), info.get("name", ""))
        request.session.clear()  # fresh session on login (avoid fixation)
        request.session["user_id"] = sub
        return RedirectResponse("/")

    @app.get("/auth/logout")
    def auth_logout(request: Request):
        request.session.clear()
        return RedirectResponse("/")
