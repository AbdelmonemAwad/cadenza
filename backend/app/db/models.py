"""Database schema (SQLite)."""
from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TrackStatus(str, enum.Enum):
    ACTIVE = "active"
    QUARANTINED = "quarantined"
    MISSING = "missing"
    CORRUPT = "corrupt"


class DupKind(str, enum.Enum):
    EXACT_FILE = "exact_file"          # identical SHA256 over the whole file
    EXACT_AUDIO = "exact_audio"        # identical audio stream, different tags
    ACOUSTIC = "acoustic"              # Chromaprint fingerprint match
    METADATA = "metadata"              # high-confidence textual match


class DupAction(str, enum.Enum):
    KEEP = "keep"
    QUARANTINE = "quarantine"
    SKIP = "skip"


class JobState(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


# --------------------------------- Tracks ---------------------------------
class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    filename: Mapped[str] = mapped_column(String(512))
    ext: Mapped[str] = mapped_column(String(16), index=True)

    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    mtime: Mapped[float] = mapped_column(Float, default=0.0)
    inode: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Technical properties
    codec: Mapped[str | None] = mapped_column(String(32), index=True)
    lossless: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    bitrate: Mapped[int | None] = mapped_column(Integer, index=True)     # bits per second
    sample_rate: Mapped[int | None] = mapped_column(Integer)
    bit_depth: Mapped[int | None] = mapped_column(Integer)
    channels: Mapped[int | None] = mapped_column(Integer)
    duration: Mapped[float | None] = mapped_column(Float, index=True)    # seconds

    # Fingerprints
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    audio_md5: Mapped[str | None] = mapped_column(String(32), index=True)
    fingerprint: Mapped[str | None] = mapped_column(Text)
    acoustid: Mapped[str | None] = mapped_column(String(64), index=True)

    # Tags
    title: Mapped[str | None] = mapped_column(String(512))
    artist: Mapped[str | None] = mapped_column(String(512), index=True)
    albumartist: Mapped[str | None] = mapped_column(String(512), index=True)
    album: Mapped[str | None] = mapped_column(String(512), index=True)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    track_no: Mapped[int | None] = mapped_column(Integer)
    disc_no: Mapped[int | None] = mapped_column(Integer)
    total_tracks: Mapped[int | None] = mapped_column(Integer)
    genre: Mapped[str | None] = mapped_column(String(256))
    isrc: Mapped[str | None] = mapped_column(String(32), index=True)
    mb_recording_id: Mapped[str | None] = mapped_column(String(64), index=True)
    mb_release_id: Mapped[str | None] = mapped_column(String(64), index=True)
    apple_id: Mapped[str | None] = mapped_column(String(64), index=True)

    has_artwork: Mapped[bool] = mapped_column(Boolean, default=False)
    artwork_px: Mapped[int | None] = mapped_column(Integer)
    has_lyrics: Mapped[bool] = mapped_column(Boolean, default=False)
    has_synced_lyrics: Mapped[bool] = mapped_column(Boolean, default=False)

    tag_completeness: Mapped[float] = mapped_column(Float, default=0.0)   # 0..1
    quality_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)

    status: Mapped[TrackStatus] = mapped_column(
        String(16), default=TrackStatus.ACTIVE, index=True)
    error: Mapped[str | None] = mapped_column(Text)

    first_seen: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_scan: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    raw_tags: Mapped[dict | None] = mapped_column(JSON)

    __table_args__ = (
        Index("ix_tracks_dupkey", "duration", "title"),
        Index("ix_tracks_album_group", "albumartist", "album"),
    )


# ---------------------------- Duplicate clusters ----------------------------
class DuplicateGroup(Base):
    __tablename__ = "duplicate_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[DupKind] = mapped_column(String(16), index=True)
    signature: Mapped[str] = mapped_column(String(128), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    reclaimable_bytes: Mapped[int] = mapped_column(Integer, default=0)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    members: Mapped[list[DuplicateMember]] = relationship(
        back_populates="group", cascade="all, delete-orphan", lazy="selectin")


class DuplicateMember(Base):
    __tablename__ = "duplicate_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("duplicate_groups.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[int] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"), index=True)

    score: Mapped[float] = mapped_column(Float, default=0.0)
    score_breakdown: Mapped[dict | None] = mapped_column(JSON)
    proposed_action: Mapped[DupAction] = mapped_column(String(16), default=DupAction.SKIP)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(Text)

    group: Mapped[DuplicateGroup] = relationship(back_populates="members")
    track: Mapped[Track] = relationship(lazy="joined")

    __table_args__ = (UniqueConstraint("group_id", "track_id", name="uq_group_track"),)


# --------------------------------- Quarantine ---------------------------------
class QuarantineItem(Base):
    __tablename__ = "quarantine"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_path: Mapped[str] = mapped_column(String(1024), index=True)
    quarantine_path: Mapped[str] = mapped_column(String(1024), unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(256))
    group_id: Mapped[int | None] = mapped_column(Integer, index=True)
    moved_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    purge_after: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    restored: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime)


# ----------------------------------- Jobs -----------------------------------
class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    state: Mapped[JobState] = mapped_column(String(16), default=JobState.PENDING, index=True)
    params: Mapped[dict | None] = mapped_column(JSON)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)

    total: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    job_kind: Mapped[str] = mapped_column(String(48))
    cron: Mapped[str] = mapped_column(String(64))          # e.g. "0 3 * * *"
    params: Mapped[dict | None] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_run: Mapped[datetime | None] = mapped_column(DateTime)
    next_run: Mapped[datetime | None] = mapped_column(DateTime)


# --------------------------------- Audit log ---------------------------------
class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    level: Mapped[str] = mapped_column(String(16), default="info", index=True)
    track_id: Mapped[int | None] = mapped_column(Integer, index=True)
    job_id: Mapped[int | None] = mapped_column(Integer, index=True)
    src_path: Mapped[str | None] = mapped_column(String(1024))
    dst_path: Mapped[str | None] = mapped_column(String(1024))
    detail: Mapped[dict | None] = mapped_column(JSON)
    reversible: Mapped[bool] = mapped_column(Boolean, default=False)


class ProviderCache(Base):
    """Persisted provider responses: respects rate limits and speeds up rescans."""
    __tablename__ = "provider_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    cache_key: Mapped[str] = mapped_column(String(256), index=True)
    payload: Mapped[dict | None] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)

    __table_args__ = (UniqueConstraint("provider", "cache_key", name="uq_provider_key"),)


class Playlist(Base):
    __tablename__ = "playlists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    source: Mapped[str] = mapped_column(String(32), default="local")  # local | apple
    external_id: Mapped[str | None] = mapped_column(String(128), index=True)
    track_ids: Mapped[list | None] = mapped_column(JSON)
    unmatched: Mapped[list | None] = mapped_column(JSON)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime)
