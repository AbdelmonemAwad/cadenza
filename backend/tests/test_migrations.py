"""A schema change without a migration must fail here, not on a user's NAS.

`create_all` adds missing tables and ignores everything else, so a new column on
an existing model reaches a fresh install and never reaches a database that
already holds someone's library. The first query touching it fails with
`no such column`, and only for people who already had data -- the one population
an update must not break, and the one CI never simulates because CI always
starts empty.

So these tests do simulate it: build a database at the baseline schema, run the
migrations, and require the result to match what the current models produce.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import models  # noqa: F401  -- registers the tables on Base.metadata
from app.db.base import Base
from app.db.migrations import (
    BASELINE_VERSION,
    MIGRATIONS,
    SCHEMA_VERSION,
    back_up_if_pending,
    read_version,
    upgrade,
)

BASELINE_SQL = Path(__file__).with_name("baseline.sql")


def _schema_of(db: Path) -> list[str]:
    """Every table, column, index and constraint, in a stable order.

    Read back from sqlite_master rather than from the SQLAlchemy metadata, so
    what is compared is what SQLite actually holds -- which is the thing a query
    at runtime will meet.
    """
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
            "  AND name NOT LIKE 'track_fts%' AND name != 'schema_version' "
            "ORDER BY type, name").fetchall()
    finally:
        conn.close()
    # Whitespace differs between a CREATE TABLE that SQLAlchemy emitted and the
    # same statement round-tripped through sqlite_master.
    return [f"{kind}:{name}:{' '.join((sql or '').split())}" for kind, name, sql in rows]


def _build_current(db: Path) -> None:
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    engine.dispose()


def _build_baseline(db: Path) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.executescript(BASELINE_SQL.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


async def _migrate(db: Path) -> int:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    try:
        await back_up_if_pending(engine, db)
        async with engine.begin() as conn:
            return await upgrade(conn, fresh=False)
    finally:
        await engine.dispose()


def test_baseline_file_is_present_and_loadable(tmp_path: Path) -> None:
    assert BASELINE_SQL.is_file(), "tests/baseline.sql is the frozen v1 schema"
    db = tmp_path / "baseline.db"
    _build_baseline(db)
    assert _schema_of(db), "the baseline produced no schema at all"


async def test_migrated_baseline_matches_the_current_models(tmp_path: Path) -> None:
    """The guard.

    If this fails after a model change, the fix is a new entry in MIGRATIONS --
    not an edit to baseline.sql. Editing the baseline makes the test pass and
    leaves every existing user with a database the code no longer matches.
    """
    current = tmp_path / "current.db"
    _build_current(current)

    migrated = tmp_path / "migrated.db"
    _build_baseline(migrated)
    await _migrate(migrated)

    want = _schema_of(current)
    got = _schema_of(migrated)

    missing = [line for line in want if line not in got]
    extra = [line for line in got if line not in want]
    assert not missing and not extra, (
        "the migrated database does not match the models.\n"
        f"missing after migrating: {missing}\n"
        f"present but not in the models: {extra}\n"
        "Add a migration to app/db/migrations.py; do not edit tests/baseline.sql."
    )


async def test_an_unstamped_database_is_treated_as_the_baseline(tmp_path: Path) -> None:
    """Databases written before versioning existed carry no version at all."""
    db = tmp_path / "old.db"
    _build_baseline(db)

    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    try:
        async with engine.begin() as conn:
            assert await read_version(conn) is None
        async with engine.begin() as conn:
            await upgrade(conn, fresh=False)
        async with engine.begin() as conn:
            assert await read_version(conn) == SCHEMA_VERSION
    finally:
        await engine.dispose()


async def test_a_fresh_database_is_stamped_without_running_migrations(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """create_all already produced the current schema; re-applying would fail."""
    db = tmp_path / "new.db"
    _build_current(db)

    # A migration that would raise if it ran. Reaching it means the fresh path
    # is wrong.
    monkeypatch.setitem(MIGRATIONS, SCHEMA_VERSION + 1,
                        ("must not run", ("SELECT raise_if_this_runs();",)))

    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    try:
        async with engine.begin() as conn:
            version = await upgrade(conn, fresh=True)
        assert version == SCHEMA_VERSION
    finally:
        await engine.dispose()


async def test_a_pending_migration_applies_and_backs_up_first(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The paths that no shipped migration exercises yet."""
    db = tmp_path / "data.db"
    _build_baseline(db)

    monkeypatch.setitem(
        MIGRATIONS, BASELINE_VERSION + 1,
        ("add a column", ("ALTER TABLE tracks ADD COLUMN test_column TEXT",)))
    monkeypatch.setattr("app.db.migrations.SCHEMA_VERSION", BASELINE_VERSION + 1)

    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    try:
        await back_up_if_pending(engine, db)
        async with engine.begin() as conn:
            await upgrade(conn, fresh=False)
        async with engine.begin() as conn:
            cols = await conn.execute(text("SELECT * FROM tracks LIMIT 0"))
            assert "test_column" in list(cols.keys())
            assert await read_version(conn) == BASELINE_VERSION + 1
    finally:
        await engine.dispose()

    # The copy taken before the first pending migration. VACUUM INTO rather
    # than a file copy, because in WAL mode the committed tail is in the -wal.
    backup = db.with_name(f"{db.name}.v{BASELINE_VERSION}.bak")
    assert backup.is_file(), "no backup was taken before migrating"

    reference = tmp_path / "reference.db"
    _build_baseline(reference)
    assert _schema_of(backup) == _schema_of(reference), \
        "the backup does not hold the pre-migration schema"


async def test_a_newer_database_is_left_alone(tmp_path: Path) -> None:
    """Installing an older package over a newer one must not rewrite anything."""
    db = tmp_path / "future.db"
    _build_baseline(db)

    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE schema_version (version INTEGER NOT NULL)"))
            await conn.execute(text(
                "INSERT INTO schema_version (version) VALUES (:v)"),
                {"v": SCHEMA_VERSION + 5})
        async with engine.begin() as conn:
            version = await upgrade(conn, fresh=False)
        assert version == SCHEMA_VERSION + 5, "a newer database was downgraded"
    finally:
        await engine.dispose()
