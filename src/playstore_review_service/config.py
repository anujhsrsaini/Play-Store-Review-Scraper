"""Environment-driven settings.

Secrets live in the environment (or a local ``.env`` the user creates from
``env.example``) — never in code. ``GEMINI_API_KEY`` unset is a supported mode: the
worker falls back to a no-LLM stub analyzer so the full flow stays testable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines). Never overrides real env vars."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    # Legacy Gemini (google-genai SDK) path.
    gemini_api_key: str
    gemini_model: str
    # Provider abstraction (e.g. OCI GenAI via its OpenAI-compatible endpoint).
    llm_provider: str
    llm_base_url: str
    llm_api_key: str
    llm_compartment_id: str
    llm_model: str  # the model used for analysis (cheap tier)
    llm_planner_model: str  # heavy tier, reserved for future deep-analysis use
    # Auth (Google OAuth). When client id/secret are unset, auth is DISABLED and a single
    # local dev user is used — so localhost works with zero setup; prod sets the creds.
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str  # explicit callback URL (prod, behind a TLS proxy); blank = auto
    session_secret: str
    session_cookie_secure: bool  # set True in prod (HTTPS) so the session cookie is Secure
    per_user_daily_analyses: int
    max_reviews_per_analysis: int
    scrape_cache_ttl_hours: int
    global_daily_spend_cap_usd: float
    scrape_delay_seconds: float
    dev_inprocess_worker: bool

    def auth_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    def provider(self) -> str:
        """Resolve the active LLM provider. Order: explicit OpenAI-compatible (OCI) →
        legacy Gemini key → no-LLM stub (always works for local testing)."""
        if self.llm_provider == "openai_compatible" and self.llm_api_key and self.llm_base_url:
            return "openai_compatible"
        if self.gemini_api_key:
            return "gemini"
        return "stub"


@lru_cache
def get_settings() -> Settings:
    _load_dotenv(Path(".env"))
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "sqlite:///./local.db"),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        gemini_model=os.environ.get("GEMINI_DEFAULT_MODEL", "gemini-2.5-flash-lite"),
        llm_provider=os.environ.get("LLM_PROVIDER", ""),
        llm_base_url=os.environ.get("LLM_BASE_URL", ""),
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        llm_compartment_id=os.environ.get("LLM_COMPARTMENT_ID", ""),
        llm_model=os.environ.get("LLM_CHEAP_MODEL", "xai.grok-3-mini"),
        llm_planner_model=os.environ.get("LLM_PLANNER_MODEL", "xai.grok-4"),
        google_client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
        google_client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        google_redirect_uri=os.environ.get("GOOGLE_REDIRECT_URI", ""),
        session_secret=os.environ.get("SESSION_SECRET", "dev-insecure-session-secret"),
        session_cookie_secure=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
        per_user_daily_analyses=int(os.environ.get("PER_USER_DAILY_ANALYSES", "15")),
        max_reviews_per_analysis=int(os.environ.get("MAX_REVIEWS_PER_ANALYSIS", "500")),
        scrape_cache_ttl_hours=int(os.environ.get("SCRAPE_CACHE_TTL_HOURS", "24")),
        global_daily_spend_cap_usd=float(os.environ.get("GLOBAL_DAILY_SPEND_CAP_USD", "5")),
        scrape_delay_seconds=float(os.environ.get("SCRAPE_DELAY_SECONDS", "1.0")),
        dev_inprocess_worker=os.environ.get("DEV_INPROCESS_WORKER", "1") == "1",
    )
