"""FastAPI web app: search, submit analysis job, poll status, serve the local UI.

Endpoints are sync ``def`` — Starlette runs them on a threadpool, so the blocking
scraper/DB calls never block the event loop. Errors return classified, generic
messages (spec §4.5): no stack traces, no upstream payloads, no key material.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import analysis as an
from . import worker as worker_mod
from .config import get_settings
from .db import Analysis, Job, Snapshot, init_db, make_engine, make_session_factory, new_job_id
from .scraper import client as scraper_client
from .scraper.errors import InvalidAppId, RateLimitedUpstream, ScraperError

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
ACTIVE_STATUSES = ("queued", "scraping", "analyzing")


class AnalyzeRequest(BaseModel):
    app_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=3, max_length=500)
    country: str = Field(default="us", pattern=r"^[a-z]{2}$")
    lang: str = Field(default="en", pattern=r"^[a-z]{2}$")


def create_app() -> FastAPI:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)

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

    @app.exception_handler(Exception)
    async def unhandled(_request, exc):  # spec §4.5: never serialize exceptions out
        logger.exception("unhandled error: %s", type(exc).__name__)
        return JSONResponse(status_code=500, content={"error": "internal_error"})

    @app.get("/api/health")
    def health() -> dict:
        provider = settings.provider()
        detail = {
            "openai_compatible": f"OCI/OpenAI-compatible ({settings.llm_model})",
            "gemini": f"gemini ({settings.gemini_model})",
            "stub": "stub (no LLM configured)",
        }[provider]
        return {"ok": True, "provider": provider, "llm": detail}

    @app.get("/api/search")
    def search(q: str, country: str = "us", lang: str = "en") -> list[dict]:
        if not (1 <= len(q.strip()) <= 200) or len(country) != 2 or len(lang) != 2:
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
    def analyze(req: AnalyzeRequest) -> dict:
        try:
            scraper_client.validate_app_id(req.app_id)
        except InvalidAppId as exc:
            raise HTTPException(422, "invalid app_id") from exc
        qhash = an.question_hash(req.question)
        with session_factory() as session:
            # Answer cache: fresh snapshot + same normalized question → no job, no LLM.
            hit = session.execute(
                select(Analysis, Snapshot)
                .join(Snapshot, Analysis.snapshot_id == Snapshot.id)
                .where(
                    Analysis.app_id == req.app_id,
                    Analysis.country == req.country,
                    Analysis.lang == req.lang,
                    Analysis.question_hash == qhash,
                    Snapshot.expires_at > worker_mod.utcnow(),
                )
            ).first()
            if hit is not None:
                record, snap = hit
                return {
                    "job_id": None,
                    "status": "done",
                    "cache_hit": True,
                    "result": _result_payload(record, snap),
                }
            # Single-flight: coalesce into an identical active job.
            active = (
                session.execute(
                    select(Job).where(
                        Job.app_id == req.app_id,
                        Job.country == req.country,
                        Job.lang == req.lang,
                        Job.question_hash == qhash,
                        Job.status.in_(ACTIVE_STATUSES),
                    )
                )
                .scalars()
                .first()
            )
            if active is not None:
                return {"job_id": active.id, "status": active.status, "cache_hit": False}
            job = Job(
                id=new_job_id(),
                app_id=req.app_id,
                country=req.country,
                lang=req.lang,
                question=req.question.strip(),
                question_hash=qhash,
            )
            session.add(job)
            session.commit()
            return {"job_id": job.id, "status": "queued", "cache_hit": False}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict:
        if not job_id.isalnum() or len(job_id) > 32:
            raise HTTPException(422, "invalid job id")
        with session_factory() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise HTTPException(404, "job not found")
            out: dict = {
                "job_id": job.id,
                "status": job.status,
                "progress": job.progress,
                "error": job.error,
            }
            if job.status == "done" and job.analysis_id is not None:
                record = session.get(Analysis, job.analysis_id)
                snap = session.get(Snapshot, job.snapshot_id)
                out["result"] = _result_payload(record, snap)
            return out

    @app.get("/api/analysis/{analysis_id}")
    def get_analysis(analysis_id: int) -> dict:
        """Shareable permalink: fetch a previously-computed analysis by id."""
        with session_factory() as session:
            record = session.get(Analysis, analysis_id)
            if record is None:
                raise HTTPException(404, "analysis not found")
            snap = session.get(Snapshot, record.snapshot_id)
            return _result_payload(record, snap)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


def _result_payload(record: Analysis, snap: Snapshot) -> dict:
    meta = snap.app_meta or {}
    return {
        "app": {
            "app_id": snap.app_id,
            "title": meta.get("title"),
            "score": meta.get("score"),
            "ratings": meta.get("ratings"),
            "installs": meta.get("installs"),
            "version": meta.get("version"),
            "histogram": meta.get("histogram"),
            "icon": meta.get("icon"),
        },
        "analysis_id": record.id,
        "snapshot": {
            "fetched_at": snap.fetched_at.isoformat() + "Z",
            "review_count": snap.review_count,
            "sort": snap.sort,
            "complete": snap.complete,
        },
        "model": record.model,
        "answer": record.answer,
    }


app = create_app()


def main() -> None:  # pragma: no cover - `pmr-serve` entrypoint
    import os

    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        "playstore_review_service.webapp:app",
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8000")),
    )
