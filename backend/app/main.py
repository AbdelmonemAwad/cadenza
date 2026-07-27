"""FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.config import APP_NAME, APP_VERSION, get_settings
from app.core.auth import ensure_initialised
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

    # Generate a first-run password before serving anything. A fixed default
    # would be worse than none: it looks protected while every install shares
    # the same credential.
    if ensure_initialised():
        # The password itself is deliberately NOT logged. setup_logging has
        # already attached a rotating file handler on the config volume, which
        # is a shared folder on a NAS: writing the credential there would leave
        # a world-readable copy in five rotated files long after first run.
        # It is written 0600 to initial-password.txt instead.
        log.warning("=" * 62)
        log.warning("First run: an administrator password has been generated.")
        log.warning("Read it from %s", s.config_dir / "initial-password.txt")
        log.warning("Sign in and change it; that file is then removed.")
        log.warning("=" * 62)

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
        # Interactive docs are off by default: on an unauthenticated instance
        # they hand out a machine-readable map of every destructive endpoint.
        # Set CADENZA_ENABLE_DOCS=true on a development machine.
        docs_url="/api/docs" if s.enable_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if s.enable_docs else None,
    )
    # Off unless someone explicitly asks for it, which nothing in this project
    # does. nginx serves the UI from the same origin in a deployment, and the
    # Vite dev server proxies /api and /health to the backend, so the browser
    # sees same-origin requests in both cases.
    #
    # This used to allow any http://localhost:<port> with credentials. It was
    # justified as a convenience for `npm run dev` -- which, given the proxy,
    # never needed it. What it did buy was a rule letting every other web app
    # on the machine make credentialed requests to Cadenza and read the
    # replies, on a box whose whole job is to be reachable from the network.
    # The setting stays for an unusual setup that runs the UI elsewhere, but it
    # takes exact origins and is empty by default.
    if s.cors_dev_origins:
        log.warning("CORS is enabled for %s -- intended for development only",
                    s.cors_dev_origins)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(s.cors_dev_origins),
            allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
        )
    app.include_router(api_router, prefix=s.api_prefix)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok", "app": APP_NAME, "version": APP_VERSION}

    return app


app = create_app()
