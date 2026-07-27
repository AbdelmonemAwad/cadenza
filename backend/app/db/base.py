"""Async SQLite engine and session plumbing."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
_settings.ensure_dirs()

engine = create_async_engine(
    _settings.db_url,
    echo=False,
    future=True,
    pool_pre_ping=True,
    connect_args={"timeout": 30},
)

SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@event.listens_for(engine.sync_engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    # WAL lets the UI read while a scan writes -- essential for libraries
    # with hundreds of thousands of files.
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA temp_store=MEMORY")
    cur.execute("PRAGMA cache_size=-64000")      # 64 MB page cache
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with SessionFactory() as session:
        yield session


async def init_db() -> None:
    from app.db import models  # noqa: F401  -- registers the tables

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # FTS5 index for fast library search
        await conn.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS track_fts USING fts5("
            "title, artist, album, albumartist, content='tracks', content_rowid='id')"
        ))
        for trigger in _FTS_TRIGGERS:
            await conn.execute(text(trigger))


_FTS_TRIGGERS = (
    """CREATE TRIGGER IF NOT EXISTS tracks_ai AFTER INSERT ON tracks BEGIN
         INSERT INTO track_fts(rowid, title, artist, album, albumartist)
         VALUES (new.id, new.title, new.artist, new.album, new.albumartist);
       END""",
    """CREATE TRIGGER IF NOT EXISTS tracks_ad AFTER DELETE ON tracks BEGIN
         INSERT INTO track_fts(track_fts, rowid, title, artist, album, albumartist)
         VALUES ('delete', old.id, old.title, old.artist, old.album, old.albumartist);
       END""",
    """CREATE TRIGGER IF NOT EXISTS tracks_au AFTER UPDATE ON tracks BEGIN
         INSERT INTO track_fts(track_fts, rowid, title, artist, album, albumartist)
         VALUES ('delete', old.id, old.title, old.artist, old.album, old.albumartist);
         INSERT INTO track_fts(rowid, title, artist, album, albumartist)
         VALUES (new.id, new.title, new.artist, new.album, new.albumartist);
       END""",
)
