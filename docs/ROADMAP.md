# Roadmap

What is done, what is being worked on, and what is deliberately not planned.

This file is kept honest rather than aspirational: an item moves to **Done**
only once it has been exercised on real hardware — a DS1821+ running DSM 7.2 —
not once the code merges. Where something is known to be broken it is listed
here as broken, with a link, rather than left out.

Version numbers are the release the work landed in. `VERSION` at the repository
root is the single source of truth and every change that ships bumps it.

---

## Done

### Runs standalone on DSM 7 — 2.1.x

- Native `.spk`: its own CPython 3.12, its own dependencies as wheels, its own
  `ffmpeg`/`ffprobe`/`fpcalc`. No Docker, no Container Manager, no Python from
  Package Center, no package dependencies at all.
- Runs as an unprivileged service account. Nothing in the package requests
  root — DSM refuses an unsigned package that does, with error 4557 at upload.
- You choose the username and password the first time you open it. Nothing is
  generated and there is no default credential.
- DSM starts and stops it like any other package, and it survives a reboot.

### Your data survives an update — 2.2.0

- The data folder is decided once and remembered, in `etc/data-dir.conf` and
  again under `var/`. It used to be re-derived on every start, which meant a
  restart could switch to an empty folder and present the create-your-account
  screen as though everything had been lost.
- If the remembered folder cannot be reached, Cadenza **refuses to start** and
  says what is unreachable. Starting empty is the one outcome that must not
  happen.
- Schema migrations, with a `VACUUM INTO` backup before the first pending one
  and a CI guard that fails when a model changes without a migration.

### Scanning is fast and stoppable — 2.3.0

- A 3801-file library is browsable in about two minutes instead of forty. The
  fingerprint and audio-checksum figures — 94% of a scan, and needed only by
  duplicate detection — run as their own job afterwards, which reports
  progress, can be stopped, and resumes where it left off.
- Stop actually stops, and a stopped scan does not mark the rest of your
  library as missing.

### Conversion keeps your metadata — 2.4.0

- All twelve presets carry title, artist, album and cover art across. Opus and
  Ogg Vorbis were losing the artwork and WAV was losing everything.
- Credential files can be uploaded from Settings, or picked from a folder
  browser on the NAS.

### Correctness — 2.5.0

- Organising a tidy library no longer renames every file to `… (2)`.
- Titles containing a full stop keep it: `Mr. Brightside` is no longer written
  as `Mr.flac`.
- A partial group restore no longer strands files with no way back.
- A rescan no longer erases the Apple Music and MusicBrainz ids it just found.

### The interface stopped disagreeing with the application — 2.6.0

- The Library page reloads when the scan it started finishes.
- Timestamps are shown in your timezone rather than shifted by your UTC offset.
- A settings change reaches the job runner without a restart.
- Jobs left "running" by a restart are closed out.
- You can change your password: the form existed and no page rendered it.

### Numbers that were not true — 2.7.0 and 2.8.0

- Duplicate detection was discarding up to 100% of pairs inside its own
  tolerance, because the blocking window was narrower than the comparison.
- An ignored duplicate group stays ignored.
- The library list shows the library, not quarantined and missing rows too.
- Search no longer stops at 5000 and reports that as the total.
- The year written to your files matches the year Cadenza shows.
- Quality scores update when the tags do.
- `succeeded` and `failed` on a job are real numbers.

### Tidying — 2.10.0

- Six opt-in categories for what a library leaves behind: empty folders,
  orphaned sidecars, Synology caches, half-written files, index entries for
  files that are gone, and quarantine records whose file has vanished.
- It never removes an audio file, checked twice; anything not obviously
  worthless goes to quarantine rather than being deleted.

### Statistics and the log — 2.9.0

- A Statistics page over a window you choose: library totals, activity per day,
  coverage for lossless/artwork/lyrics, jobs by kind and state, and space in
  quarantine or reclaimable.
- `cadenza.log` readable from the interface, filterable by level and substring.
  The endpoint takes no path parameter and can only ever open the file the
  application is writing.

---

## In progress

An audit of every feature area produced 51 confirmed defects, each verified by
an independent pass before it was accepted. Thirty have been fixed and shipped
in 2.5.0 through 2.8.0 — the data-integrity ones first, then the places where
the interface and the application disagreed, then the wrong numbers.

What is left, in the order it is being worked through:

| area | what you see |
|---|---|
| Metadata | Discogs' relevance check can never reject a release: it compares the searched title against itself |
| Apple Music | "Matched tracks" is not refreshed after a match run |
| Apple Music | "Import and match" stores its result where nothing reads it |
| Apple Music | linking races MusicKit's loader on a fixed 400 ms wait |
| Dashboard | one failed request replaces the whole page, permanently |
| Activity log | every fetch error is swallowed and shows an empty, silent table |
| Library | a scan with an unusable `ffprobe` indexes the whole library as healthy |
| Jobs | "Started" shows when the job was queued, not when it started |
| Jobs | schedule actions report nothing when they fail |
| Duplicates | the header mixes a server-wide count with a saving summed over one page |

---

## Planned

### Multiple library folders

Add and remove library paths from Settings rather than setting one at install
time. Every containment check, the scanner, the organizer and the quarantine
mirror assume a single root today, so this is a real change rather than a new
field — and it needs the schema migrations that 2.2.0 introduced.

### Integration setup guidance

Each provider explained in place: what it gives you, where to get the key, and
a link that goes there. AcoustID and Last.fm need the key in the query string,
so the log redaction that already exists matters here too.

---

## Not planned

- **ARM builds.** The bundled interpreter and FFmpeg are x86_64. A DS220j or
  DS223 would need a separate build of both, and neither has the CPU to
  fingerprint a library in reasonable time.
- **Streaming or playback.** Cadenza curates the library that Plex, Emby,
  Audio Station or Roon then serve. It is not a player.
- **Editing audio.** Conversion is a format change, not an editor.
- **A cloud account.** Everything stays on the NAS. The only outbound requests
  are to the metadata providers you configure.

---

## How to report something

Open an issue. If it is a defect, the ones that get fixed fastest say what you
did, what happened, and what you expected — and include the version from
Package Center and the relevant lines from
`/var/packages/Cadenza/var/logs/service.log`.
