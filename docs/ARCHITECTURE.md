# Architecture

## Why the package carries everything

DSM 7 tightened package isolation considerably, and those constraints dictate
the entire shape of this project.

| DSM 7 constraint | Consequence | How the package resolves it |
|---|---|---|
| Packages run as an unprivileged service account, never root | Cannot install system libraries | Every dependency travels inside the package |
| No `apt`/`yum`; `/usr/local/lib` is package-scoped | `libchromaprint` and `ffmpeg` are unobtainable | Both are bundled, statically where possible |
| DSM's bundled Python is 3.8 and sparsely packaged | `pyacoustid`, `mutagen`, `pydantic` need building | A python-build-standalone CPython 3.12 travels with the package |
| An unsigned package may not grant itself shared-folder access | Music paths may be unreadable | Stated at install time; the user ticks the service account once |

The `.spk` therefore contains the whole application: interpreter, wheels,
`ffmpeg`/`ffprobe`/`fpcalc`, the built frontend, and one control script. DSM
starts a single Python process. `install_dep_packages` is empty — no Container
Manager, no Python from Package Center, nothing.

**It used to be a Docker orchestrator.** The `.spk` loaded a bundled image and
drove `docker compose`. That could not work on DSM 7 for an unsigned package:
the Docker socket is `root:root 0660`, there is no pre-existing `docker` group
for a package user to join, and requesting root in `conf/privilege` makes DSM
refuse the package at upload with error 4557 — measured, not assumed. See
issue #2.

### Requesting root is not an option

This is worth stating plainly because it is the constraint everything else
bends around. Synology's own AudioStation runs as root. A third-party package
that asks for it is rejected at upload unless Synology signed it. So Cadenza
has no privileged component at all, and the one thing it genuinely cannot do
for itself — grant its service account access to a shared folder — is asked of
the user once, at install time, in the wizard and again in the install log.

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

### Where the data lives

The database, the account, the API keys and the artwork cache all sit in one
data folder. Which folder that is gets decided **once** and then recorded, in
`etc/data-dir.conf` and again under `var/` — two copies because they fail
differently: `etc/` is what survives `postinst` rewriting the environment file
on an upgrade, but only if DSM's staging worked, while `var/` is `@appdata`,
which DSM preserves on its own with no staging involved.

It used to be re-derived from the install wizard's answer on every start, with
a silent fallback to the package directory when that path was not writable.
That is a data-loss trap and it fired on real hardware: the wizard had recorded
a folder the service account could not reach, so Cadenza used its own folder
and put everything there without saying so — and the moment the configured
folder became writable, the next restart would have switched to it, found it
empty, and offered to create an account as though the library were gone.

If the recorded folder cannot be reached, Cadenza **refuses to start**.
Starting anyway would present a fresh account over an empty database while the
real one sat intact a directory away, and a user who accepts that offer begins
writing into the wrong place.

### Schema migrations

`create_all` adds missing tables and looks at nothing else, so a new column on
an existing model reaches every fresh install and no database that already
holds a library — and CI can never see it, because CI always starts empty. The
only person who finds out is someone who already had data.

There is a `schema_version` table, an ordered migration list, a `VACUUM INTO`
backup before the first pending migration (a file copy would miss everything in
the WAL and still look valid), and a test that builds a database at the frozen
baseline schema, migrates it, and requires the result to match what the models
produce. Adding a column without a migration fails in CI, by name.

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

Measured on a DS1821+ (Ryzen V1500B) against a real 3801-file library, per
file, with the bundled binaries:

| Scan stage | Mean | Share |
|---|---|---|
| walk the tree | 0.2 s for all 3801 | — |
| `ffprobe` | 64 ms | 2.5% |
| read tags | 19 ms | 0.7% |
| `sha256` | 79 ms | 3.1% |
| **`audio_md5`** | **1705 ms** | **67.0%** |
| **fingerprint** | 678 ms | 26.6% |

That distribution is why indexing and analysis are separate jobs. `audio_md5`
decodes the whole file to PCM and the fingerprint decodes two minutes of it
again; between them they are 94% of the cost and **nothing needs either of them
to browse, search, sort by quality or organise**. They exist so
`find_exact_audio` can group by checksum and `find_acoustic` can compare
fingerprints — duplicate detection, and nothing else.

So a scan indexes, and the analysis pass follows it: reporting progress,
stoppable, and resumable, because it selects the tracks still missing a value.
The same library is browsable in about two minutes instead of forty.

| Operation | Approximate cost |
|---|---|
| Indexing scan | ~2 min for 3801 files |
| Analysis pass (checksums + fingerprints) | ~40 min for the same library, in the background |
| Incremental rescan | seconds (size + mtime short-circuit) |
| Duplicate analysis | 2–5 min |
| Metadata enrichment | Rate-limited by providers — MusicBrainz allows 1 req/s |
| Conversion | ~4–10× realtime per core |

The scan commits in batches of 200 to bound memory on very large libraries, and
checks for a stop request between batches — so stopping keeps everything
already indexed and takes seconds.
