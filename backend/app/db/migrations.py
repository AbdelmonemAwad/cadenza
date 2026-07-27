"""Schema migrations for an appliance database.

Why this exists
---------------
`Base.metadata.create_all` creates tables that do not exist and looks at nothing
else. It does not add a column to a table that is already there. So every schema
change shipped so far would have behaved like this:

  * a fresh install creates the new schema and works
  * an existing install keeps the old one, and the first query touching the new
    column fails with `no such column`

and CI would never see it, because CI always starts from an empty database. The
only person who finds out is a user who already had data -- which is the exact
population an update must not break.

Design
------
No Alembic. This is a package a user installs from Package Center; there is no
place to run a CLI step, and a migration that needs a human is a migration that
does not happen. Instead:

  * `schema_version` holds one integer.
  * `MIGRATIONS` maps that integer to the statements that take the database from
    version n-1 to n.
  * They run at startup, in order, each inside a transaction that also bumps the
    version -- so a migration either lands completely or not at all, and a crash
    halfway through leaves the database on the version it actually has.
  * Before the first pending migration, the database is copied with `VACUUM
    INTO`. A plain file copy is wrong here: the database runs in WAL mode, so the
    newest committed rows are in `-wal`, not in the `.db` file. `VACUUM INTO`
    writes one consistent file with no other process needing to stop.

A brand-new database is stamped at the latest version without running anything,
because `create_all` has just produced that schema directly.

The guard
---------
`tests/test_migrations.py` builds a database from `baseline.sql`, migrates it,
and asserts the result is identical to what `create_all` produces today. Adding a
column to a model without adding a migration fails that test. That is the whole
point: the failure has to be visible to the person making the change, not to the
user who updates six months later.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

log = logging.getLogger(__name__)

# Version 1 is the schema as it stood when migrations were introduced. Existing
# databases created before that have no schema_version table and are treated as
# version 1, because that is exactly what they contain.
BASELINE_VERSION = 1

# version -> (what it does, the statements that get there)
#
# Statements run in order. Keep them additive: SQLite can add a column but only
# recent versions can drop one, and a released build has to migrate a database
# written by any older release. To retire a column, stop writing it and leave it.
MIGRATIONS: dict[int, tuple[str, tuple[str, ...]]] = {}

SCHEMA_VERSION = max(MIGRATIONS) if MIGRATIONS else BASELINE_VERSION


async def _table_exists(conn: AsyncConnection, name: str) -> bool:
    row = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"), {"n": name})
    return row.first() is not None


async def read_version(conn: AsyncConnection) -> int | None:
    """The recorded version, or None if this database has never been stamped."""
    if not await _table_exists(conn, "schema_version"):
        return None
    row = await conn.execute(text("SELECT version FROM schema_version LIMIT 1"))
    found = row.first()
    return int(found[0]) if found else None


async def stamp(conn: AsyncConnection, version: int) -> None:
    await conn.execute(text(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"))
    await conn.execute(text("DELETE FROM schema_version"))
    await conn.execute(text("INSERT INTO schema_version (version) VALUES (:v)"),
                       {"v": version})


async def back_up_if_pending(engine: AsyncEngine, db_path: Path | None) -> Path | None:
    """Copy the database before anything changes it. Returns the copy, or None.

    Called by init_db *before* it opens the migration transaction, and that is
    not a stylistic choice: SQLite refuses `VACUUM` inside a transaction, so a
    backup taken from within the migration would fail every time -- which the
    first version of this did.

    `VACUUM INTO` rather than a file copy. The database runs in WAL mode, so the
    most recent committed rows are in the `-wal` file; copying only the `.db`
    produces a backup missing everything since the last checkpoint, and it would
    look perfectly valid.
    """
    if db_path is None or db_path.name == ":memory:":
        return None

    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        if not await _table_exists(conn, "tracks"):
            return None                      # nothing anyone would miss
        current = await read_version(conn)
        if current is None:
            current = BASELINE_VERSION
        if not [v for v in MIGRATIONS if current < v <= SCHEMA_VERSION]:
            return None                      # nothing to do, nothing to protect

        target = db_path.with_name(f"{db_path.name}.v{current}.bak")
        try:
            target.unlink(missing_ok=True)
            # VACUUM INTO takes no bound parameters. The path is our own
            # setting, never a request value; single quotes are doubled so a
            # directory name containing one cannot terminate the literal.
            escaped = str(target).replace("'", "''")
            await conn.execute(text(f"VACUUM INTO '{escaped}'"))
        except Exception:                               # noqa: BLE001
            # A failed backup must not stop the upgrade. The alternative is an
            # application that refuses to start over a database that is fine.
            log.warning("could not back up the database before migrating; continuing",
                        exc_info=True)
            return None

    log.info("database backed up to %s before migrating", target.name)
    return target


async def upgrade(conn: AsyncConnection, *, fresh: bool) -> int:
    """Bring the database up to SCHEMA_VERSION. Returns the version it is on."""
    if fresh:
        # create_all has just built the current schema. Running the migrations
        # over it would try to add columns that are already there.
        await stamp(conn, SCHEMA_VERSION)
        return SCHEMA_VERSION

    current = await read_version(conn)
    if current is None:
        current = BASELINE_VERSION
        log.info("database predates schema versioning; treating it as v%d", current)
        await stamp(conn, current)

    if current >= SCHEMA_VERSION:
        if current > SCHEMA_VERSION:
            # Downgrade: the user installed an older package over a newer one.
            # Say so instead of failing later with an unexplained SQL error.
            log.warning(
                "database is at schema v%d but this build understands v%d. "
                "It will be left alone. Install the newer version again, or "
                "restore the backup taken before it upgraded.", current, SCHEMA_VERSION)
        return current

    for version in sorted(v for v in MIGRATIONS if current < v <= SCHEMA_VERSION):
        description, statements = MIGRATIONS[version]
        log.info("applying schema v%d: %s", version, description)
        for statement in statements:
            await conn.execute(text(statement))
        await stamp(conn, version)
        current = version

    return current
