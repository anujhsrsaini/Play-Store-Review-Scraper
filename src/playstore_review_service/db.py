"""Database layer (SQLAlchemy 2.0).

Localhost default is SQLite (zero setup); production points DATABASE_URL at Postgres.
All timestamps are stored as NAIVE UTC for cross-backend comparison consistency.

PII rule (spec §4.6): ``cached_reviews`` has NO column for reviewer names or images —
anonymization is structural at the persistence layer, not a flag.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def new_job_id() -> str:
    return uuid.uuid4().hex


def new_token() -> str:
    """Unguessable id for shareable permalinks (prevents IDOR enumeration)."""
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # OAuth sub, or "local"
    email: Mapped[str] = mapped_column(String(320), index=True, default="")
    name: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Snapshot(Base):
    """One cached scrape of an app's reviews per (app_id, country, lang)."""

    __tablename__ = "review_snapshots"
    __table_args__ = (UniqueConstraint("app_id", "country", "lang"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[str] = mapped_column(String(200), index=True)
    country: Mapped[str] = mapped_column(String(8))
    lang: Mapped[str] = mapped_column(String(8))
    sort: Mapped[str] = mapped_column(String(32), default="NEWEST")
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    complete: Mapped[bool] = mapped_column(Boolean, default=True)
    app_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class CachedReview(Base):
    __tablename__ = "cached_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("review_snapshots.id"), index=True)
    review_id: Mapped[str] = mapped_column(String(64))
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str | None] = mapped_column(String(40), nullable=True)  # ISO 8601
    app_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    thumbs_up: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reply_content: Mapped[str | None] = mapped_column(Text, nullable=True)  # developer reply
    replied_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_job_id)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    app_id: Mapped[str] = mapped_column(String(200), index=True)
    country: Mapped[str] = mapped_column(String(8))
    lang: Mapped[str] = mapped_column(String(8))
    question: Mapped[str] = mapped_column(Text)
    question_hash: Mapped[str] = mapped_column(String(64), index=True)
    period: Mapped[str] = mapped_column(String(8), default="90d")  # review time-window
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)  # reviews fetched so far
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("review_snapshots.id"), nullable=True
    )
    analysis_id: Mapped[int | None] = mapped_column(ForeignKey("analyses.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Analysis(Base):
    """The answer cache: one row per (app, locale, normalized question, snapshot)."""

    __tablename__ = "analyses"
    __table_args__ = (
        UniqueConstraint("app_id", "country", "lang", "question_hash", "period", "snapshot_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Public, unguessable handle used by the share permalink (NOT the sequential PK).
    share_token: Mapped[str] = mapped_column(String(32), unique=True, index=True, default=new_token)
    app_id: Mapped[str] = mapped_column(String(200), index=True)
    country: Mapped[str] = mapped_column(String(8))
    lang: Mapped[str] = mapped_column(String(8))
    question_hash: Mapped[str] = mapped_column(String(64), index=True)
    period: Mapped[str] = mapped_column(String(8), default="90d")
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("review_snapshots.id"))
    answer: Mapped[dict] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(64), default="stub")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DailySpend(Base):
    """Single row per UTC day holding the running estimated LLM spend. Updated under a
    row lock so the global daily cap is enforced atomically even with multiple workers."""

    __tablename__ = "daily_spend"

    day: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD (UTC)
    spent_usd: Mapped[float] = mapped_column(Float, default=0.0)


class AnalysisMetric(Base):
    """Per-analysis quality/observability metrics (separate from $ usage). Drives the
    quality dashboard: groundedness proxy (verified-quote ratio), abstention rate, latency."""

    __tablename__ = "analysis_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    reviews_fetched: Mapped[int] = mapped_column(Integer, default=0)
    reviews_curated: Mapped[int] = mapped_column(Integer, default=0)
    verified_quotes: Mapped[int] = mapped_column(Integer, default=0)
    dropped_quotes: Mapped[int] = mapped_column(Integer, default=0)
    not_enough_data: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_fallback: Mapped[bool] = mapped_column(Boolean, default=False)  # LLM failed → stub
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class UsageLog(Base):
    __tablename__ = "usage_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event: Mapped[str] = mapped_column(String(32))  # scrape | gemini_call | cache_hit | stub
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


def make_engine(database_url: str):
    # check_same_thread=False: the in-process dev worker thread shares the SQLite file.
    kwargs = (
        {"connect_args": {"check_same_thread": False}} if database_url.startswith("sqlite") else {}
    )
    return create_engine(database_url, **kwargs)


def make_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine) -> None:
    """Create tables. (Alembic migrations arrive with the Phase 5 production deploy.)"""
    Base.metadata.create_all(engine)
