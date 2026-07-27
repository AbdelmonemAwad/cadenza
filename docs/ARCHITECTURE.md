# Architecture

## Why a Docker-wrapped package?

DSM 7 tightened package isolation considerably, and those constraints dictate
the entire shape of this project.

| DSM 7 constraint | Consequence | How the container resolves it |
|---|---|---|
| Packages run as an unprivileged `sc-<pkg>` user, never root | Cannot install system libraries | Every dependency ships inside the image |
| No `apt`/`yum`; `/usr/local/lib` is package-scoped | `libchromaprint` and `ffmpeg` are unobtainable | Both are built into the image |
| Shared-folder access is mediated by `privilege` | Music paths may be refused | Paths are passed in as bind mounts |
| DSM's bundled Python is old and sparsely packaged | `pyacoustid`, `mutagen`, `pydantic` need building | Based on `python:3.12-slim` |

The `.spk` therefore contains no application code. It is an **orchestrator**:
it loads a bundled image, drives `docker compose`, registers a port, and adds
an icon to the DSM desktop.

## System diagram

```
+------------------------------- Synology DSM 7 (x86_64) --------------------------------+
|                                                                                        |
|  +----------------+          +-----------------------------------------------------+   |
|  |  DSM Desktop   |          |          Package: Cadenza (.spk)                     |   |
|  |  [Cadenza]     |--------->|  scripts/start-stop-status -> docker compose up -d   |   |
|  +----------------+          |  conf/resource -> port registration                  |   |
|          |                   +-----------------------------------------------------+   |
|          | http://nas:8760                                                             |
|          v                                                                             |
|  +------------------------ Docker container: cadenza ---------------------------+       |
|  |                                                                              |       |
|  |  +---------------------------+        +----------------------------------+   |       |
|  |  |  nginx  :8760             |        |  uvicorn  127.0.0.1:8000         |   |       |
|  |  |  - React SPA (static)     |--/api->|  FastAPI  /api/v1/*              |   |       |
|  |  |  - WebSocket upgrade      |        |  - JobRunner (single-concurrency)|   |       |
|  |  +---------------------------+        |  - APScheduler (cron)            |   |       |
|  |                                       |  - SQLite (WAL) @ /config        |   |       |
|  |                                       +----------------+-----------------+   |       |
|  |                                                        |                     |       |
|  |          ffmpeg / ffprobe / fpcalc  <------------------+                      |       |
|  +------------------------------------------------------------------------------+       |
|                                                                                        |
|  Bind mounts:                                                                          |
|    /volume1/music                    -> /music        (the library)                    |
|    /volume1/music/.cadenza-quarantine-> /quarantine   (safe deletion)                  |
|    /var/packages/Cadenza/var/config  -> /config       (database, logs, cache)          |
+----------------------------------------------------------------------------------------+

                   External providers (outbound HTTPS only)
        MusicBrainz · AcoustID · Discogs · Last.fm · Apple Music · LRCLIB
```

## Data flow for a full clean-up

```
Scan ─► ffprobe ─► read tags (mutagen) ─► SHA256 + audio-stream MD5
   └─► fpcalc fingerprint ─► AcoustID lookup ─► MusicBrainz id
        └─► MetadataAggregator (per-field weighted vote) ─► merged result
             └─► DuplicateEngine (4 layers, LSH-bucketed) ─► clusters
                  └─► DecisionEngine (8 weighted criteria) ─► keep / quarantine
                       └─► Dry-run report ──(user approves)──► Apply
                            └─► Organizer + TagWriter + Artwork + LRC
```

## Component notes

### Job runner

A single-concurrency queue. Two jobs mutating the same tree concurrently is a
data-loss hazard, so serialising them is deliberate rather than a limitation.
Progress is broadcast over a WebSocket; a subscriber that stops draining its
queue is dropped rather than allowed to block the running job.

### Deduplication

Layers run cheapest-first and a `_UnionFind` structure guarantees a path never
lands in two clusters.

The acoustic layer would be O(n²) without blocking, so candidates are narrowed
by a duration window **and** LSH bands over the fingerprint vector. The band
count matters: a single band loses roughly half of all true pairs, because one
flipped bit relocates a track to a different bucket. Six bands of two words
each, hashed on the top 8 bits, keep the miss rate below 1 in 10,000.

Oversized buckets (>400 members) are skipped rather than swept — a badly
distributed band would otherwise cost more than it finds, and the other five
bands still give those tracks a chance to meet.

### Metadata aggregation

Rather than picking one "winning" source, each **field** is decided
independently by `confidence × FIELD_TRUST[field][source]`. This is what lets
Discogs supply the pressing year while Apple supplies the artwork and
MusicBrainz supplies the identifiers, all for the same track.

Existing tags are never overwritten unless the replacement clears
`OVERWRITE_THRESHOLD` (0.80) *and* the caller explicitly opted in.

### Database

SQLite in WAL mode. WAL is what allows the UI to stay responsive while a scan
writes — without it, a library scan blocks every read. An FTS5 virtual table
backs library search, kept in sync by triggers.

## Safety invariants

These hold everywhere in the codebase and should be treated as load-bearing:

1. No code path calls `unlink()` on a library file. Removal is a move into
   quarantine.
2. Every destructive job accepts `dry_run` and defaults to it
   (`dry_run_default = True`).
3. Every mutation writes an `AuditLog` row recording source, destination and
   reversibility.
4. A duplicate cluster always keeps exactly one copy; the API rejects overrides
   that would leave zero keepers.
5. Ambiguous clusters (score margin < 0.03) are never auto-actioned.
6. The container runs as a normal UID/GID so files stay owned by the user.

## Performance envelope

Measured against a DS1821+ (Ryzen V1500B, 4 cores) with 50,000 tracks:

| Operation | Approximate cost |
|---|---|
| First scan (hash + fingerprint) | 20–60 min, I/O bound |
| Incremental rescan | seconds (size + mtime short-circuit) |
| Duplicate analysis | 2–5 min |
| Metadata enrichment | Rate-limited by providers — MusicBrainz allows 1 req/s |
| Conversion | ~4–10× realtime per core |

The scan commits in batches of 200 to bound memory on very large libraries.
