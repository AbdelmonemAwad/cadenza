"""Serving the built frontend from the application itself.

The container does not need this: nginx sits in front and serves the bundle.
The native Synology package has no nginx -- it is a single Python process
started by DSM -- so the API has to hand out the UI as well.

It is off unless `www_dir` is set, so the container path is byte-for-byte
unchanged and this cannot alter how the deployed image behaves.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

log = logging.getLogger(__name__)

# Hashed asset filenames from the Vite build never change meaning, so they are
# safe to cache hard. index.html must not be, or a user stays on the old bundle
# after an upgrade and sees an API it does not match.
_IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}
_NO_STORE = {"Cache-Control": "no-store"}


def mount_frontend(app: FastAPI, www_dir: Path, api_prefix: str) -> bool:
    """Serve the SPA at /, returning False if there is no bundle to serve."""
    index = www_dir / "index.html"
    if not index.is_file():
        log.warning("no frontend bundle at %s; the API is running without a UI", www_dir)
        return False

    # Deliberately no StaticFiles mount for /assets. It was there, and it meant
    # two code paths serving files with different headers -- the mount ignored
    # the cache policy below, so hashed bundles were served with no
    # Cache-Control at all. One path is easier to reason about, and FileResponse
    # infers the content type and honours range requests just as well.

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str, request: Request) -> FileResponse:
        """Any unmatched GET returns the shell, so client-side routes work.

        Registered last, and it must stay that way: FastAPI matches in
        declaration order, so mounting this before the API routers would
        swallow every endpoint and answer JSON requests with HTML.
        """
        # Belt and braces in case the ordering above is ever disturbed: a
        # missing API route has to stay a 404, not become the SPA shell, or
        # every client error turns into an unparseable HTML body.
        if path.startswith((api_prefix.strip("/"), "health")):
            raise HTTPException(404, "not found")

        # Real files are served as themselves; everything else is the shell.
        # `contained` is not used here because the candidate is built from a
        # URL path, so it is resolved and checked against www_dir directly.
        candidate = (www_dir / path).resolve()
        root = www_dir.resolve()
        if path and (candidate == root or root in candidate.parents) and candidate.is_file():
            return FileResponse(candidate, headers=_IMMUTABLE)

        return FileResponse(index, headers=_NO_STORE)

    log.info("serving the frontend from %s", www_dir)
    return True
