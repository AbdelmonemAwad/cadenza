"""A library the service account cannot read must not kill the process.

On the Synology package the quarantine lives inside the library. Creating it at
import time meant that a music share the `cadenza` account had not been let
into raised `PermissionError` before the application had finished importing:
DSM started the package, it died three seconds later, Package Center showed
`start_failed` with no reason, and the one line that explained it sat in a log
nobody opens. A DVA3221 stayed like that for 47 days over one missing
permission.

These pin the three halves of the fix: nothing is created at import, the
quarantine root is not Cadenza's to create at startup, and the first move into
quarantine creates it.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.core.quarantine import QuarantineManager
from app.db.base import SessionFactory, init_db
from app.db.models import QuarantineItem, Track, TrackStatus

pytestmark = pytest.mark.asyncio


async def test_ensure_dirs_leaves_the_quarantine_root_alone(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A quarantine root that cannot be created must not fail startup.

    Simulated with a parent that is a file rather than a directory, which
    raises on every platform the way a share the account may not enter raises
    on DSM. Before the fix this call raised; the package died with it.
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    settings = get_settings()
    monkeypatch.setattr(settings, "quarantine_root", blocker / "quarantine")
    # cache_dir and artwork_cache derive from config_dir, so one patch moves
    # all of Cadenza's own folders under tmp_path.
    monkeypatch.setattr(settings, "config_dir", tmp_path / "config")

    settings.ensure_dirs()

    assert (tmp_path / "config" / "logs").is_dir(), "Cadenza's own folders must exist"
    assert settings.artwork_cache.is_dir()
    assert str(settings.artwork_cache).startswith(str(tmp_path))
    assert not (blocker / "quarantine").exists(), \
        "the quarantine root must not be touched at startup"


async def test_importing_the_db_module_creates_nothing(tmp_path: Path) -> None:
    """The last import-time side effect. Checked in a fresh interpreter,
    because in this one the module has long been imported."""
    env = {**os.environ,
           "CADENZA_CONFIG_DIR": str(tmp_path / "config"),
           "CADENZA_MUSIC_ROOT": str(tmp_path / "music"),
           "CADENZA_QUARANTINE_ROOT": str(tmp_path / "quarantine")}
    backend = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", "import app.db.base"],
        cwd=backend, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    assert not (tmp_path / "config").exists(), "importing created the config folder"
    assert not (tmp_path / "quarantine").exists(), "importing created the quarantine"


async def test_the_first_move_into_quarantine_creates_its_root(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """What replaced the startup mkdir: the move itself makes the folder, so a
    library that becomes writable later works without a restart."""
    await init_db()
    settings = get_settings()
    music = tmp_path / "music"
    music.mkdir()
    quarantine = tmp_path / "later"          # does not exist yet
    monkeypatch.setattr(settings, "music_root", music)
    monkeypatch.setattr(settings, "quarantine_root", quarantine)

    song = music / "album" / "song.mp3"
    song.parent.mkdir()
    song.write_bytes(b"\x00" * 4096)

    async with SessionFactory() as s:
        track = Track(path=str(song), filename=song.name, ext=".mp3",
                      status=TrackStatus.ACTIVE, size_bytes=4096)
        s.add(track)
        await s.flush()

        item = await QuarantineManager(s).quarantine(track, "test")
        await s.commit()

        assert quarantine.is_dir(), "the move did not create the quarantine root"
        assert Path(item.quarantine_path).is_file()
        assert not song.exists()
        stored = (await s.execute(
            select(QuarantineItem).where(QuarantineItem.id == item.id))).scalar_one()
        assert stored.original_path == str(song)
