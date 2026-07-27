from fastapi import APIRouter

from app.api.v1 import (
    apple,
    convert,
    dashboard,
    duplicates,
    jobs,
    library,
    metadata,
    quarantine,
    settings,
)

api_router = APIRouter()
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(library.router, prefix="/library", tags=["library"])
api_router.include_router(duplicates.router, prefix="/duplicates", tags=["duplicates"])
api_router.include_router(metadata.router, prefix="/metadata", tags=["metadata"])
api_router.include_router(convert.router, prefix="/convert", tags=["convert"])
api_router.include_router(quarantine.router, prefix="/quarantine", tags=["quarantine"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
api_router.include_router(apple.router, prefix="/apple", tags=["apple-music"])
api_router.include_router(settings.router, prefix="/settings", tags=["settings"])
