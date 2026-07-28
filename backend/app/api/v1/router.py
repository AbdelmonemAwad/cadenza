from fastapi import APIRouter, Depends

from app.api.deps import current_user
from app.api.v1 import (
    apple,
    auth,
    convert,
    dashboard,
    duplicates,
    files,
    jobs,
    library,
    metadata,
    quarantine,
    settings,
    statistics,
)

api_router = APIRouter()

# Sign-in must be reachable without a session, for obvious reasons.
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])

# Everything else is gated here rather than endpoint by endpoint, so a new
# route is protected by default instead of by remembering to decorate it.
# These endpoints move, retag and quarantine files the user cannot re-download.
protected = APIRouter(dependencies=[Depends(current_user)])
protected.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
protected.include_router(library.router, prefix="/library", tags=["library"])
protected.include_router(duplicates.router, prefix="/duplicates", tags=["duplicates"])
protected.include_router(metadata.router, prefix="/metadata", tags=["metadata"])
protected.include_router(convert.router, prefix="/convert", tags=["convert"])
protected.include_router(quarantine.router, prefix="/quarantine", tags=["quarantine"])
protected.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
protected.include_router(files.router, prefix="/files", tags=["files"])
protected.include_router(apple.router, prefix="/apple", tags=["apple-music"])
protected.include_router(settings.router, prefix="/settings", tags=["settings"])
protected.include_router(statistics.router, prefix="/statistics",
                         tags=["statistics"])

api_router.include_router(protected)
