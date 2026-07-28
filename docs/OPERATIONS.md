# Operations

How Cadenza behaves once it is running. This describes what the code does, not
what a curation tool ideally would do. Written for the person who owns the
library and carries the consequences.

Paths below are the ones inside the container. The bundled compose file maps
them to the NAS as `/volume1/music` → `/music`,
`/volume1/music/.cadenza-quarantine` → `/quarantine`, and
`/volume1/docker/cadenza/config` → `/config`.

---

## The safety model

### The one rule the rest depends on

No code path in Cadenza calls `unlink()` on a file in your library. Removal
means a move into `/quarantine`. The single exception is the purge routine,
which deletes files *already sitting in quarantine* and only when you have
turned on `hard_delete_allowed`.

Two places used to break this rule and no longer do:

- `POST /quarantine/purge?force=true` skipped the `hard_delete_allowed` check.
  The `force` parameter is gone; there is no per-request override.
- Conversion with "keep original" turned off called `src.unlink()` directly, with
  no quarantine, no setting check and no audit entry. The transcoder now never
  deletes the source; it reports `source_replaced=True` and the job routes the
  original through quarantine like any other removal.

### What is reversible

| Operation | Audit `reversible` | How it is undone |
|---|---|---|
| Duplicate → quarantine | `true` | `POST /quarantine/{id}/restore`, or restore the whole group |
| Converted source → quarantine | `true` | Same. The converted file stays where it was written |
| Organize (move/rename) | `true` | No automated undo. The audit row holds both paths; move it back by hand |
| Restore from quarantine | `false` | Recorded as an event; quarantine the file again if you change your mind |
| Tag enrichment | `false` | Not automatic. The audit `detail.changed` holds `{field: [old, new]}` per field |
| Manual tag edit | `false` | Same: the audit `detail` holds old and new per field |
| Purge | `false` | Nothing |

"Reversible" on an organize row means the operation was an in-library move whose
source and destination were both recorded — not that a button exists to reverse
it. There is no undo command anywhere in the API.

### What is not reversible

- **Purged files.** Deleted with `unlink()`. No copy is kept anywhere.
- **Tag values overwritten in place.** Enrichment and manual edits write tags
  into the audio file with mutagen. The previous tags are not backed up to disk;
  they survive only as values inside the audit row's JSON. The `raw_tags` column
  on a track holds only *extra* container-specific tags seen at scan time, not a
  full tag snapshot.
- **Embedded artwork replaced by enrichment.** The old image is not kept.
- **`.lrc` sidecars.** Enrichment writes `path.with_suffix(".lrc")` with
  `write_text`. An existing file at that path is overwritten and its previous
  contents are recorded nowhere.
- **`cover.jpg` in an album folder.** Replaced only when the new artwork is
  wider than the file already there; when it is replaced, the old file is gone.

Conversion itself never overwrites. If the destination name is taken, the
transcoder picks `name (2).ext`, `name (3).ext` … up to 200 attempts and fails
the item rather than writing over anything. It writes to a hidden, uniquely
named temp file (`.stem.part-<random>.ext`) and renames on success, so an
interrupted or crashed conversion leaves no half-written track and no visible
debris.

### What quarantine guarantees

When a file is quarantined:

1. It is **moved**, not copied, to
   `/quarantine/<YYYYMMDD>/<its path relative to /music>`. The original tree is
   mirrored, so you can find and move files back with File Station alone,
   without Cadenza running.
2. The destination is re-checked with `contained()` after it is built. If the
   result would land outside the quarantine root, nothing is moved and the
   operation fails.
3. If a file of that name already exists in quarantine, a free name
   `name__dup.ext`, `name__dup_2.ext` … is found. If all 200 candidates are
   taken, the move is refused rather than performed onto an occupied path.
4. A `.lrc` sidecar next to the track is moved along with it. A failed sidecar
   move is logged at debug level and does not fail the operation. `.cue`, `.log`
   and `cover.jpg` are **not** taken along.
5. A `quarantine_items` row records the original path, the quarantine path, size,
   SHA256, the reason, the duplicate group id, and `purge_after`.
6. The track row is set to `quarantined`, and an audit row is written with
   `reversible=true`.

In the duplicate-apply job, each file's move and its database record commit in
one transaction, one file at a time. A failure on file 40 does not roll back the
rows for files 1–39 that are already sitting in quarantine.

What quarantine does not guarantee: that the file is safe from anything other
than Cadenza. Under the default compose file, `/quarantine` lives inside your
music share. It is protected from Cadenza's own scanner only because the folder
name starts with a dot and `skip_hidden` defaults to `true`. **If you turn
`skip_hidden` off, the scanner will walk into `.cadenza-quarantine` and index
quarantined files as library tracks.**

Keep quarantine on the same filesystem as the library. `shutil.move` across
filesystems degrades to a full copy plus delete, which turns every "delete" into
a byte-for-byte copy and requires the free space to do it.

### Retention

`purge_after` is stamped **at the moment of quarantine** as
`now + quarantine_retention_days`. The default is 30 days. The setting accepts
1–3650 over the API; 0 is rejected, because a retention of zero would make every
future quarantine immediately purgeable and quietly remove the safety net.

Changing the retention setting later does not re-stamp items already in
quarantine. They keep the deadline they were given.

Reaching `purge_after` does nothing on its own. It only makes an item *eligible*
for a purge job. Out of the box, nothing ever purges: the scheduled purge task
ships disabled, and `hard_delete_allowed` ships off. Files accumulate in
quarantine until you act.

### `hard_delete_allowed`

This is the only switch that permits permanent deletion. It defaults to `false`.

`purge_expired()` reads it first:

```python
if not self.s.hard_delete_allowed:
    return {"purged": 0, "skipped": 0, "blocked": 1}
```

Nothing is opened, nothing is unlinked, and the response reports `blocked: 1`.
There is no argument, query parameter, or request body field that bypasses this;
enabling permanent deletion is a settings change
(`PATCH /api/v1/settings` with `{"hard_delete_allowed": true}`), and nothing
else.

When it is on and a purge job runs without dry-run:

- Only items with `restored = false` and `purge_after <= now` are considered.
- Each path is re-checked with `contained()` against the quarantine root
  immediately before the `unlink()`. A path that fails is skipped and logged at
  error level; the row is left in place.
- Each file is unlinked, an audit row is written, the quarantine row is deleted,
  and the transaction is **committed per item** — so a later failure cannot roll
  back the record of files already deleted.
- An item whose file is already missing from disk still counts as purged and its
  row is removed.

To reach a real deletion, three independent things must be true: `hard_delete_allowed`
is on, a `quarantine_purge` job runs with `dry_run=false`, and the item has passed
its retention date. Via the scheduled task there is a fourth: the task submits with
`dry_run_default`, so with the shipped default of `true` the scheduled purge only
ever reports statistics.

### Guard rails inside the duplicate flow

- A duplicate cluster always keeps at least one copy. `PATCH
  /duplicates/groups/{id}/members` counts keepers after applying your overrides
  and returns 400 if the result would be zero.
- Ambiguous clusters — where the best score beats the runner-up by less than
  0.03 — are never proposed for automatic quarantine.
- Clusters with confidence below 0.90 are never proposed for automatic
  quarantine either. In practice that means every `metadata` cluster (confidence
  0.80) waits for a human.

---

## Dry runs

`dry_run_default` is `true`. What a dry run means, precisely: **your library on
disk is not modified.** It does not mean nothing happens.

| Job | With `dry_run=true` |
|---|---|
| `scan` | Ignores the flag entirely. Always reads files and always writes track rows. Never writes to the library |
| `dedup_analyze` | Always dry. Reads tracks, computes clusters and verdicts, writes `duplicate_groups`/`duplicate_members`. Touches no file |
| `dedup_apply` | Returns the count of members flagged for quarantine and their total bytes. Moves nothing |
| `organize` | Returns `moved` as a *count of planned moves* plus a `preview` list of up to 300 `from`/`to` pairs. Moves nothing |
| `convert` | Returns the computed destination path with `skipped_reason: "dry run: nothing was written"`. ffmpeg is not invoked and no source is quarantined |
| `enrich` | **Queries the metadata providers over the network** and writes the provider cache. Reports confidence and per-field changes. Writes no tags, no artwork, no `.lrc`, and no audit row |
| `quarantine_purge` | Returns quarantine statistics only |

Two consequences worth holding onto:

- A dry-run enrichment is not offline. It makes outbound HTTPS calls to
  MusicBrainz, AcoustID, Discogs, Last.fm, Apple Music and LRCLIB, and consumes
  whatever rate budget those have.
- A dry-run organize is optimistic. It counts every changed plan as `moved`
  without performing the containment check or the "does the target exist now?"
  check. Those run only on the real pass, where a target that appeared since
  planning is refused with `target appeared since planning, not moved`.

Most API endpoints carry their own `dry_run` field defaulting to `true` and pass
it explicitly. `dry_run_default` is what applies when the flag is omitted, which
happens in exactly three places: `POST /api/v1/jobs` without `dry_run`, the
"run now" endpoint for a scheduled task, and every scheduled task firing on its
cron.

---

## How duplicates are decided

### Four layers, strongest first

Each track ends up in at most one cluster. Layers run cheapest first, and each
one skips tracks a stronger layer already claimed.

| Layer | Basis | Confidence |
|---|---|---|
| `exact_file` | Identical SHA256 over the whole file | 1.00 |
| `exact_audio` | Identical MD5 over the decoded audio stream — same audio, different tags | 0.99 |
| `acoustic` (AcoustID) | Same AcoustID from the lookup service | 0.97 |
| `acoustic` (fingerprint) | Chromaprint similarity above threshold | 0.92 |
| `metadata` | Fuzzy title + artist + duration | 0.80 |

### The 0.50 floor on fingerprint comparison

`fingerprint.similarity()` decompresses both Chromaprint fingerprints into
32-bit words and measures the fraction of agreeing bits, searching ±60 words of
offset to absorb differences in leading silence.

Because it is a Hamming bit-agreement ratio, **two completely unrelated songs
score about 0.50, not 0.0.** Unrelated bits agree half the time. The scale is
therefore not intuitive: 0.50 is the noise floor, and the entire useful range
sits above roughly 0.85. A re-encode of the same master typically scores
0.92–0.99.

`acoustic_match_threshold` defaults to 0.90. The settings policy clamps it to
`[0.85, 0.999]` — an attempt to set it lower over the API is rejected with a 400.
Lowering it toward 0.5 does not make matching "more aggressive"; it makes the
comparison meaningless and starts merging different songs into one cluster, at
which point the decision engine will propose quarantining a track you have only
one copy of.

Supporting constraints on the acoustic layer:

- Candidates are blocked by duration (`duration_tolerance_s`, default 7 s;
  tracks are indexed in their own slot and the adjacent one so pairs straddling a
  boundary still meet) and by 6 LSH bands drawn from disjoint segments of the
  fingerprint. A pair becomes candidates if *any* band collides.
- Every candidate pair is still rejected outright if the durations differ by more
  than `duration_tolerance_s`.
- A bucket with more than 400 members is skipped as badly distributed; the other
  bands still give those tracks a chance to meet.
- If the fingerprint cannot be decoded, `similarity` returns 0.0 and the pair
  never matches.

The metadata layer requires `token_sort_ratio` on the normalised title ≥
`title_fuzzy_threshold` (default 88) **and** `token_set_ratio` on the normalised
artist ≥ threshold − 8, plus the same duration tolerance. Normalisation folds
NFKC, lowercases, strips "(Official Video)"-style noise and `feat.` clauses,
removes Arabic diacritics and tatweel, and folds alef/ya/waw-hamza/ta-marbuta
variants. Metadata blocks larger than 60 members are skipped.

### Which copy is kept

`DecisionEngine` scores every file in a cluster from 0 to 1 and keeps the
highest. It never touches the filesystem.

| Criterion | Weight | What it measures |
|---|---|---|
| format | 0.30 | Codec rank — FLAC 1.00, ALAC 0.97, Opus 0.62, AAC 0.58, MP3 0.52, WMA 0.30 |
| bitrate | 0.22 | Lossless scores 1.0; lossy is scored against 320 kbps and against the best lossy peer |
| tags | 0.16 | Tag completeness, +0.08 if linked to a MusicBrainz recording |
| resolution | 0.12 | Sample rate and bit depth |
| artwork | 0.08 | Presence and pixel width |
| duration | 0.05 | Penalises copies noticeably shorter than the longest peer — truncation |
| lyrics | 0.04 | Synced 1.0, plain 0.6, none 0.0 |
| path | 0.03 | Penalises `downloads`, `new folder`, `copy`, `temp`, `@eaDir`, `(1)`, ` - copy`, and paths more than 9 components deep |

Ties break on score, then file size, then earliest `first_seen`, so the verdict
is stable across runs. Every member carries a per-criterion breakdown and a
plain-language reason ("lower bitrate; missing or small artwork"), both visible
in the report and in `GET /duplicates/groups`.

The proposed action is `quarantine` only when the verdict is unambiguous **and**
cluster confidence ≥ 0.90. Otherwise losers are marked `skip` and nothing
happens to them until you override the action manually.

### One thing to know before re-analysing

`dedup_analyze` deletes every unresolved `DuplicateGroup` and rebuilds from
scratch. Manual action overrides on groups you have not yet applied or ignored
are lost. Apply or ignore a group before you re-run analysis.

---

## Where everything lives

### `/music` — the library

The only place Cadenza modifies your audio. It moves and renames files
(organize), writes tags and artwork in place (enrich, manual edit), and writes
converted output alongside sources. Every path that reaches the filesystem is
resolved and checked against this root; a request that resolves outside it is
refused, logged at error level, and counted as a failure.

The organizer also removes directories its moves emptied, walking upward and
stopping at the library root. It uses `rmdir`, which fails if the directory is
not empty, so the emptiness check and the deletion are one atomic step.
`@eaDir` and `.DS_Store` are ignored when deciding "empty".

### `/quarantine`

`<YYYYMMDD>/<path relative to /music>`. Files whose source was outside the
library root land directly under the date folder as a bare filename.

### `/config` — all persistent state

| File | Contents | Mode |
|---|---|---|
| `cadenza.db` | SQLite, WAL mode. Tracks, duplicate groups, quarantine records, jobs, scheduled tasks, audit log, provider cache, playlists | default |
| `cadenza.db-wal`, `-shm` | Present while running | default |
| `logs/cadenza.log` (+ up to 5 rotations) | Application log, 8 MB per file | default |
| `cache/`, `cache/artwork/` | Downloaded artwork | default |
| `settings.json` | UI-editable overrides — **includes AcoustID, Discogs and Last.fm keys in plain text** | 0600 |
| `auth.json` | Username, password hash, `session_epoch` | 0600 |
| `secret_key` | Session signing key, 48 bytes hex | 0600 |
| `apple_user_token.json` | Apple Music user token | 0600 |
| `AuthKey.p8` | Apple Music private key, if you supplied one | 0600 |

Every credential file is written through one routine that creates a uniquely
named temp file with `O_EXCL` at mode 0600, `fchmod`s it, `fsync`s it, and
atomically renames it into place. A reader sees the old file or the complete new
one — never a half-written credential — and the contents are never briefly
readable at the process umask. This matters because `/config` is a DSM shared
folder that other packages and users on the NAS can read.

On every startup, `tighten_secret_files()` brings those files down to 0600 if
they are not already. That covers files written by an earlier version and
volumes restored from a backup that dropped the modes. It is silent when the
filesystem does not support `chmod`.

### What to back up

**`/config`, in full.** It is the entire state of the installation: the index,
every fingerprint and hash, the duplicate verdicts, the audit history, the
quarantine mapping, your API keys, and your password.

**`/quarantine`, for as long as your retention window.** Until an item is purged
or restored, the quarantine tree holds the *only* copy of every file Cadenza
removed. It deserves the same backup treatment as the library itself.

Two cautions on backing up `/config`:

- SQLite is in WAL mode. Copying `cadenza.db` alone while the app is running can
  capture an inconsistent database. Stop the container first, or use
  `sqlite3 cadenza.db ".backup out.db"`. Cadenza has no built-in export or
  backup command.
- The backup will contain your provider API keys and your password hash in
  files whose only protection is the 0600 mode. Preserve modes, or protect the
  backup another way.

The library itself is outside Cadenza's scope. It does not back up your music
and does not pretend to.

---

## Authentication

### First run

If `auth.json` does not exist there is no account, and the interface shows a
create-your-account screen instead of a sign-in form. The user picks both the
username and the password.

Nothing is generated. Earlier versions produced a random password, wrote it to
`/config/initial-password.txt` and forced a change at first sign-in — which
handed the user a credential they never asked for and had to go and find, and
left a secret sitting on a shared folder until they got round to changing it.
The file is removed on upgrade and nothing reads it.

### Claiming an install happens exactly once

`POST /auth/setup` answers `201` the first time and `409` on every attempt
afterwards, so an install that is already set up cannot be claimed by whoever
reaches it next. It is rate limited on the same limiter as sign-in, because it
is reachable without a session until it is used.

Sign-in takes the username as well as the password, and hashes the password
even when the username is wrong, so the timing does not reveal which of the two
was incorrect.

`GET /auth/status` reports only whether an account exists — `{"configured":
bool}` — which is what the interface needs to decide between the two screens
and nothing more.

New passwords must be at least 10 characters and must differ from the current
one. Changing the password bumps `session_epoch`, which invalidates every
existing session.

### Sessions and revocation

Sessions are stateless signed tokens, not server-side records:
`base64url(json).base64url(HMAC-SHA256)` over `{sub, exp, ep}`, signed with
`/config/secret_key`. They survive a container restart. They last **14 days**.

The token is delivered as the `cadenza_session` cookie — `HttpOnly`,
`SameSite=Strict`, `Path=/`, and `Secure=false` because DSM is commonly plain
HTTP on the LAN. It is also accepted as `Authorization: Bearer <token>`; the
cookie and the header are tried independently, so a stale cookie cannot suppress
a valid bearer token.

Revocation works through a `session_epoch` counter stored beside the password
hash. Every token embeds the epoch current when it was issued; `verify_session`
rejects any token whose epoch does not match. Bumping the epoch invalidates
every token ever issued.

| Action | Effect |
|---|---|
| `POST /auth/logout` | Clears this browser's cookie only. The token itself stays valid until it expires |
| `POST /auth/logout-everywhere` | Bumps the epoch. Every session everywhere is dead immediately. This is the control for a token you believe leaked |
| `POST /auth/password` | Also bumps the epoch, then re-issues a token for the current browser so you are not signed out of the tab you are using |

Deleting `/config/secret_key` also invalidates every session, since the app
regenerates it on next use.

Password hashing is scrypt (N=2¹⁵, r=8, p=1, 32-byte key, 16-byte salt),
roughly 100 ms and 32 MB per verification. Verification runs in a thread so an
unauthenticated caller cannot stall the event loop by hammering the endpoint.
Stored cost parameters are bounds-checked when parsed, so a tampered `auth.json`
cannot turn each sign-in into a memory-exhaustion primitive.

### The sign-in throttle

Applies to `POST /auth/login` and `POST /auth/password` — the second one because
it also verifies the current password, and a stolen cookie is exactly the case
that matters.

- **Per address: 10 attempts per 15 minutes.** Past that, 429 with a
  `Retry-After` header. A successful sign-in clears that address's history, so a
  few typos do not lock anyone out.
- **Globally: 60 failures per 15 minutes adds a delay, never a refusal.** Each
  attempt beyond 60 adds 0.25 s, capped at 2 s. This is deliberate. A global
  counter that *refused* would let sixty forged requests buy an attacker an
  indefinite sign-in outage for the household. A brake an attacker can stand on
  is worse than the attack. A success does not clear the global counter — it
  measures total recent pressure.
- The attempt is **counted in the same lock acquisition that authorises it**,
  before hashing begins. Otherwise a burst of concurrent requests would all pass
  the check during the ~100 ms of scrypt, and one burst would test hundreds of
  passwords against a limit of ten.
- The key is the client address, and the client cannot choose it. Forwarding
  headers are read only when the connection itself arrived from the loopback,
  which is the only way the bundled nginx reaches the app. `X-Real-IP` is
  preferred; malformed values are ignored rather than used as a key. IPv6 is
  bucketed by /64, since one machine is routinely handed the whole prefix.
- At most 4096 addresses are tracked; the least recently seen is evicted.
- **State is in memory and is lost on restart.** That is accepted: someone who
  can restart the container has already won, and persisting it would turn a
  lockout into a file an attacker could aim at.

### What is not protected by any of this

The API surface behind a valid session is broad. `PATCH /api/v1/settings`
enforces a positive allowlist — a field is writable only if it is named in
`WRITABLE_FIELDS`, and values are validated, not just field names. Deployment
configuration (`music_root`, `quarantine_root`, `config_dir`, `ffmpeg_bin`,
`ffprobe_bin`, `fpcalc_bin`, `apple_private_key_path`, `http_port`,
`api_prefix`, `workers`, `enable_docs`, `cors_dev_origins`, `user_agent`,
`provider_order`) is locked and can only be set through the environment or the
compose file. `hard_delete_allowed`, `dry_run_default` and
`quarantine_retention_days` are writable over the API, so a session is enough to
enable permanent deletion.

Keep the instance on a trusted network. It speaks plain HTTP. Interactive API
docs are off by default, because on an unauthenticated instance they hand out a
machine-readable map of every destructive endpoint. CORS is empty by default.

---

## What is logged

### The application log

`/config/logs/cadenza.log`, rotating at 8 MB with 5 backups, plus the same
stream on the console (which Docker captures, limited to 10 MB × 3 by the
compose file). Format: `timestamp LEVEL [logger] message`.

Written at INFO and above by default: startup and shutdown, the configured music
and quarantine roots, each scheduled task firing, job crashes with full
tracebacks, refused moves outside the library, purge failures, ffmpeg tag-copy
warnings, and — at WARNING — every failed sign-in with the throttle key
(the client IP, or the /64 for IPv6).

Library file and folder paths appear throughout, in messages and in tracebacks.
If your folder names are sensitive, the log is sensitive.

### What is deliberately redacted

Three values are stripped from every finished log line: `acoustid_api_key`,
`lastfm_api_key`, `discogs_token`. They are replaced with `***redacted***`.

The redaction runs in the formatter, after the line is assembled, rather than in
a filter — so it covers the message, its arguments, and traceback text alike.
That is the case that matters: AcoustID and Last.fm require the key in the query
string, neither accepts a header, so the full URL of an outbound request
contains the key, and it arrives in the log inside an httpx exception rather
than in a message anyone wrote. Values shorter than 8 characters are skipped, on
the grounds that they are placeholders and redacting them would replace
unrelated substrings all over the log.

`httpx`, `httpcore` and `apscheduler.executors.default` are pinned to WARNING.
httpx in particular logs full request URLs, query string included, at INFO.

The generated first-run password is never logged, only its file location.

What is **not** redacted: file paths, the Apple Music user token, the
MusicBrainz contact address, and anything else. Assume everything else in the
log is in the clear.

### The audit log

A separate, structured record in the database — `audit_log` — readable at
`GET /api/v1/dashboard/audit` with filters on action and level.

Each row carries a timestamp, action, level, track id, job id, source path,
destination path, a JSON `detail`, and the `reversible` flag.

| Action | Level | Recorded |
|---|---|---|
| `quarantine` | warning | Original path → quarantine path, reason, group id, bytes |
| `restore` | info | Quarantine path → restored path, quarantine item id |
| `purge` | warning | Quarantine path, quarantine item id, bytes |
| `organize` | info | Source path → destination path |
| `enrich` | info | File path, confidence, per-field sources, `{field: [old, new]}` |
| `manual_tag_edit` | info | File path, `{field: [old, new]}` |
| `job.<kind>` | info/error | Final state and the keys of the result |

Nothing trims this table. It grows with the number of operations you perform,
and it is the only place a tag's previous value is kept.

---

## Scheduled jobs and the job queue

### The queue

One job runs at a time. The queue is FIFO and lives in memory.

- `submit()` writes a `Job` row in state `pending` and pushes the id.
- Progress is written to the job row and broadcast on the WebSocket at
  `/api/v1/jobs/stream`, which requires the session cookie. A subscriber that
  falls more than 200 messages behind is dropped rather than allowed to stall the
  running job.
- A failed job lands in state `failed` with the first 1000 characters of the
  exception in `message`, and the full traceback in the application log.
- **A restart abandons the queue.** Pending job rows stay `pending` forever and
  are never re-run; the row for a job that was running when the process died
  stays `running`. Neither is cleaned up. Re-submit what you needed.
- Cancelling a `pending` job marks it `cancelled` and it is skipped when it
  reaches the front. Cancelling a *running* job only sets a flag, and only
  `dedup_apply` (per file) and `enrich` (per track) check it. A running `scan`,
  `organize` or `convert` will run to completion regardless.

### The default schedule

Four tasks are seeded into the database on first start:

| Name | Job | Cron (UTC) | Enabled |
|---|---|---|---|
| Nightly scan | `scan` | `0 3 * * *` (`{"full": false}`) | yes |
| Weekly duplicate analysis | `dedup_analyze` | `30 3 * * 0` | yes |
| Enrich incomplete tags | `enrich` | `0 4 * * *` (`{"only_incomplete": true, "limit": 300}`) | no |
| Purge expired quarantine | `quarantine_purge` | `0 5 * * 1` | no |

Neither enabled task modifies your library: an incremental scan only reads and
indexes, and duplicate analysis only writes verdicts to the database.

### Controlling them

- `GET /api/v1/jobs/schedule/tasks` — list, with `last_run` and `next_run`.
- `POST /api/v1/jobs/schedule/tasks` — create or update by name.
- `DELETE /api/v1/jobs/schedule/tasks/{id}`
- `POST /api/v1/jobs/schedule/tasks/{id}/run` — run once, now.

Every change reloads the scheduler immediately.

Four behaviours worth knowing:

1. **All cron expressions are UTC.** The scheduler and every trigger are pinned
   to UTC. The `TZ` environment variable in the compose file does not move them.
   `0 3 * * *` with `TZ=Africa/Cairo` fires at 05:00 or 06:00 local, depending
   on the season.
2. **An invalid cron is accepted on save and ignored at load.** The upsert
   endpoint validates the job kind but not the expression. A bad expression is
   caught during scheduler reload, logged as a warning, and the task is silently
   skipped — while still showing as enabled. After changing a cron, check that
   `next_run` moved.
3. **Deleted or renamed default tasks come back.** Seeding matches on name, so
   on the next restart any of the four default names that is absent is created
   again, with its default enabled state. To disable a default task permanently,
   set `enabled: false` rather than deleting it.
4. **Scheduled jobs submit with `dry_run_default`.** With the shipped default of
   `true`, a scheduled enrichment reports changes and writes no tags, and a
   scheduled purge reports statistics and deletes nothing. Making either of them
   act requires setting `dry_run_default` to `false`, which changes the default
   for every other omitted flag as well.

Missed runs have a one-hour grace period, multiple missed runs coalesce into
one, and a task never runs concurrently with itself.

---

## Recovery

### Restoring from quarantine

`GET /api/v1/quarantine` lists unrestored items with their original path,
quarantine path, size, reason, move date and purge deadline.

- `POST /api/v1/quarantine/{id}/restore` — moves the file back to its original
  path, creating parent directories as needed.
- `POST /api/v1/quarantine/groups/{group_id}/restore` — restores every
  unrestored member of a duplicate group.

If something now occupies the original path, the file is restored beside it as
`name__restored.ext`, then `name__restored_2.ext`, and so on. It never
overwrites. If all 200 candidates are taken, the restore is refused. The `.lrc`
sidecar comes back too; a failure to move it is suppressed, because the audio is
already back and a stranded sidecar is not worth failing over.

The track row is set back to `active` and re-pointed at wherever the file
actually landed. If no row matches the original path any more, the file is
restored and no row is updated — the next scan re-indexes it.

Restores fail cleanly when the item is unknown, already restored, or its file is
missing from the quarantine directory.

**You do not need Cadenza to restore.** The quarantine tree mirrors your library
layout under a date folder. Moving files back with File Station or `mv` works,
and the next scan picks them up.

### If the database is lost

Your music is unaffected. `cadenza.db` holds only derived state and history.

On next startup, Cadenza creates an empty schema, re-seeds the four default
scheduled tasks, and — if `auth.json` also went missing — generates a new
first-run password.

Lost with it: the track index, every SHA256, audio MD5 and fingerprint, all
duplicate groups and verdicts, job history, the entire audit log, all quarantine
records, provider cache, and playlists.

What survives: the files, the tags written into them, and everything sitting in
`/quarantine`.

The practical consequences:

- **Quarantined files can no longer be restored through the UI.** Their rows are
  gone. Move them back by hand — the mirrored directory layout under
  `/quarantine/<date>/` tells you where each one belongs.
- **Those files are also now inert.** With no rows, the purge job will never
  touch them. They occupy space until you deal with them.
- **The previous values of every enriched or manually edited tag are gone**, as
  the audit log was the only record.
- Recovery is a full rescan: hashing and fingerprinting every file again,
  roughly 20–60 minutes for 50,000 tracks.

Because `auth.json`, `secret_key` and `settings.json` are separate files, losing
the database alone does not cost you your password or your API keys. Losing
`auth.json` — or having it truncated to zero bytes, which `load_credentials`
treats as unconfigured — puts the instance back to having no account at all, so
the next person to open it is offered the create-your-account screen. That is
also the recovery path if you forget your password: stop the package, remove
`auth.json`, start it again, and claim it afresh. Every existing session dies
with the file.

### What cannot be recovered

- **Anything purged.** The file was unlinked and no copy exists. The purge audit
  row records the quarantine path, the item id, and the byte count. The original
  location survives only in the earlier `quarantine` audit row for the same
  file, matched by path — the quarantine row itself is deleted at purge.
- **Tag values overwritten**, once the audit log is gone. While the audit log
  survives, `detail.changed` holds `[old, new]` per field and a manual repair is
  possible field by field.
- **`.lrc` sidecar contents overwritten by enrichment.** Never recorded.
- **`cover.jpg` and embedded artwork replaced by enrichment.** The previous
  image is not kept anywhere.
- **The original of a conversion**, if the source was quarantined with
  "keep original" off and that quarantine item was subsequently purged.
- **Files deleted outside Cadenza.** The next scan marks the row `missing` and
  leaves it. Cadenza has no copy.

### Diagnosing state

`GET /api/v1/settings/health` reports, in one response: whether ffmpeg and
fpcalc are present, whether each of the three volumes exists and is writable
(tested by writing and deleting a probe file), which providers are configured,
and the three current safety values — `dry_run_default`, `hard_delete_allowed`
and `quarantine_retention_days`.

`GET /api/v1/quarantine/stats` reports item count, total bytes, the oldest
entry, the retention setting, and whether hard delete is allowed.

Neither endpoint returns a secret. `GET /api/v1/settings` reports the three
provider keys as booleans only.
