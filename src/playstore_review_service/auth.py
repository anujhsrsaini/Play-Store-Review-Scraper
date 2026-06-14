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
from datetime import datetime
from datetime import time as dtime

from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .db import Job, User, utcnow

logger = logging.getLogger(__name__)

LOCAL_USER_ID = "local"


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
            raise HTTPException(400, "oauth_failed") from exc
        info = token.get("userinfo") or {}
        sub = info.get("sub")
        if not sub:
            raise HTTPException(400, "oauth_no_subject")
        with session_factory() as session:
            upsert_user(session, sub, info.get("email", ""), info.get("name", ""))
        request.session["user_id"] = sub
        return RedirectResponse("/")

    @app.get("/auth/logout")
    def auth_logout(request: Request):
        request.session.clear()
        return RedirectResponse("/")
