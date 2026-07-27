"""End-to-end deduplication against a real SQLite database.

No network, no NAS, no audio files: tracks are inserted directly so every
layer of the engine is exercised deterministically.
"""
from __future__ import annotations

import random

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.dedup import DeduplicationEngine, build_dry_run_report
from app.db.base import SessionFactory, init_db
from app.db.models import DupAction, DuplicateGroup, Track, TrackStatus
from tests.conftest import encode_fingerprint, reencode

pytestmark = pytest.mark.asyncio


def _track(**kw) -> Track:
    from pathlib import Path
    defaults = {
        "filename": Path(kw["path"]).name,
        "ext": Path(kw["path"]).suffix,
        "size_bytes": 5_000_000,
        "status": TrackStatus.ACTIVE,
        "tag_completeness": 0.6,
        "quality_score": 0.6,
    }
    return Track(**{**defaults, **kw})


@pytest_asyncio.fixture
async def seeded_session():
    rng = random.Random(7)
    song_a = [rng.getrandbits(32) for _ in range(300)]
    song_b = [rng.getrandbits(32) for _ in range(300)]

    await init_db()
    async with SessionFactory() as session:
        for table in (DuplicateGroup, Track):
            for row in (await session.execute(select(table))).scalars().all():
                await session.delete(row)
        await session.commit()

        session.add_all([
            # 1) Byte-identical copies
            _track(path="/music/a/01.flac", sha256="H1", audio_md5="M1", codec="flac",
                   lossless=True, bitrate=900_000, duration=200.0, title="Alpha",
                   artist="X", size_bytes=40_000_000, tag_completeness=0.9),
            _track(path="/music/backup/01.flac", sha256="H1", audio_md5="M1", codec="flac",
                   lossless=True, bitrate=900_000, duration=200.0, title="Alpha",
                   artist="X", size_bytes=40_000_000, tag_completeness=0.9),

            # 2) Same audio stream, different tags
            _track(path="/music/b/tagged.flac", sha256="H2a", audio_md5="M2", codec="flac",
                   lossless=True, bitrate=900_000, duration=210.0, title="Beta",
                   artist="Y", tag_completeness=0.95, has_artwork=True, artwork_px=1200,
                   size_bytes=41_000_000),
            _track(path="/music/b/untagged.flac", sha256="H2b", audio_md5="M2", codec="flac",
                   lossless=True, bitrate=900_000, duration=210.0, title="Beta",
                   tag_completeness=0.20, size_bytes=41_000_000),

            # 3) Acoustic match: FLAC master versus a transcoded MP3
            _track(path="/music/c/gamma.flac", sha256="H3", audio_md5="M3", codec="flac",
                   lossless=True, bitrate=950_000, duration=180.0, title="Gamma",
                   artist="Z", fingerprint=encode_fingerprint(song_a),
                   size_bytes=38_000_000, tag_completeness=0.9,
                   has_artwork=True, artwork_px=1400),
            _track(path="/music/c/Downloads/gamma (1).mp3", sha256="H4", audio_md5="M4",
                   codec="mp3", lossless=False, bitrate=192_000, duration=181.0,
                   title="Gamma", artist="Z",
                   fingerprint=encode_fingerprint(reencode(song_a, rng)),
                   size_bytes=4_300_000, tag_completeness=0.4),

            # 4) An unrelated song of near-identical length: must stay separate
            _track(path="/music/d/delta.flac", sha256="H5", audio_md5="M5", codec="flac",
                   lossless=True, duration=180.5, title="Delta", artist="W",
                   fingerprint=encode_fingerprint(song_b), size_bytes=37_000_000),

            # 5) Textual match with no fingerprint, Arabic spelling variants
            _track(path="/music/e/enta omry.mp3", sha256="H6", audio_md5="M6", codec="mp3",
                   bitrate=320_000, duration=3600.0, title="أنت عمري",
                   artist="أم كلثوم", size_bytes=140_000_000, tag_completeness=0.7),
            _track(path="/music/e/enta omry copy.mp3", sha256="H7", audio_md5="M7",
                   codec="mp3", bitrate=128_000, duration=3602.0,
                   title="انت عمرى (Official Audio)", artist="ام كلثوم",
                   size_bytes=57_000_000, tag_completeness=0.3),
        ])
        await session.commit()
        yield session


async def test_all_four_layers_detect_their_case(seeded_session):
    report = await DeduplicationEngine(seeded_session).analyze()

    kinds: dict[str, int] = {}
    for c in report.clusters:
        kinds[c.kind.value] = kinds.get(c.kind.value, 0) + 1

    assert report.scanned == 9
    assert kinds.get("exact_file") == 1
    assert kinds.get("exact_audio") == 1
    assert kinds.get("acoustic") == 1
    assert kinds.get("metadata") == 1
    assert report.duplicate_files == 4


async def test_unrelated_song_is_not_merged(seeded_session):
    report = await DeduplicationEngine(seeded_session).analyze()
    acoustic = [c for c in report.clusters if c.kind.value == "acoustic"]
    delta = (await seeded_session.execute(
        select(Track).where(Track.title == "Delta"))).scalar_one()
    assert all(delta.id not in c.track_ids for c in acoustic)


async def test_every_cluster_keeps_exactly_one_copy(seeded_session):
    await DeduplicationEngine(seeded_session).analyze()
    groups = (await seeded_session.execute(select(DuplicateGroup))).scalars().all()
    assert len(groups) == 4
    for g in groups:
        keeps = [m for m in g.members if _action(m) == DupAction.KEEP.value]
        assert len(keeps) == 1, f"cluster {g.kind} kept {len(keeps)} copies"


async def test_best_copy_is_chosen_per_cluster(seeded_session):
    await DeduplicationEngine(seeded_session).analyze()
    groups = (await seeded_session.execute(select(DuplicateGroup))).scalars().all()

    acoustic = next(g for g in groups if g.kind == "acoustic")
    assert _action_for(acoustic, "gamma.flac") == DupAction.KEEP.value
    assert _action_for(acoustic, "gamma (1).mp3") == DupAction.QUARANTINE.value

    exact_audio = next(g for g in groups if g.kind == "exact_audio")
    assert _action_for(exact_audio, "tagged.flac") == DupAction.KEEP.value


async def test_dry_run_report_touches_nothing(seeded_session):
    await DeduplicationEngine(seeded_session).analyze()
    report = await build_dry_run_report(seeded_session)

    assert report["dry_run"] is True
    assert report["duplicate_files"] == 4
    assert report["reclaimable_bytes"] > 0
    assert len(report["by_kind"]) == 4
    assert all(m["breakdown"] for g in report["details"] for m in g["members"])

    tracks = (await seeded_session.execute(select(Track))).scalars().all()
    assert all(t.status == TrackStatus.ACTIVE for t in tracks)


async def test_reanalysis_is_idempotent(seeded_session):
    first = await DeduplicationEngine(seeded_session).analyze()
    second = await DeduplicationEngine(seeded_session).analyze()
    groups = (await seeded_session.execute(select(DuplicateGroup))).scalars().all()

    assert len(groups) == 4, "re-running the analysis must not accumulate clusters"
    assert second.duplicate_files == first.duplicate_files


def _action(member) -> str:
    a = member.proposed_action
    return a if isinstance(a, str) else a.value


def _action_for(group, path_fragment: str) -> str | None:
    for m in group.members:
        if path_fragment in m.track.path:
            return _action(m)
    return None
