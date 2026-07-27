"""FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.config import APP_NAME, APP_VERSION, get_settings
from app.db.base import engine, init_db
from app.logging_conf import setup_logging
from app.services.job_runner import runner
from app.services.scheduler import scheduler

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    setup_logging(s.log_level, s.config_dir / "logs" / "cadenza.log")
    s.ensure_dirs()
    await init_db()
    await runner.start()
    await scheduler.start()
    log.info("%s %s ready - music_root=%s quarantine=%s",
             APP_NAME, APP_VERSION, s.music_root, s.quarantine_root)
    try:
        yield
    finally:
        await scheduler.shutdown()
        await runner.stop()
        await engine.dispose()
        log.info("%s stopped", APP_NAME)


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title=APP_NAME,
        version=APP_VERSION,
        description="Music library curation, deduplication and tagging for Synology NAS",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    # In production the UI is served by nginx from the same origin; this only
    # exists so `npm run dev` on a workstation can reach the API.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    )
    app.include_router(api_router, prefix=s.api_prefix)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok", "app": APP_NAME, "version": APP_VERSION}

    return app


app = create_app()
