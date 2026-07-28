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

---

## In progress

An audit of every feature area produced 51 confirmed defects, each verified by
an independent pass before it was accepted. The data-integrity ones are fixed;
these are the rest, in the order they are being worked through.

| | area | what you see |
|---|---|---|
| ▸ | Library | the page does not reload when the scan it started finishes, so it looks like nothing happened |
| ▸ | Jobs | timestamps are shifted by the viewer's UTC offset everywhere in the app |
| ▸ | Settings | the password-change form exists but no page renders it |
| ▸ | Settings | number fields offer ranges the API rejects, and one bad field fails the whole save |
| ▸ | Jobs | a settings change does not reach the job runner until the package restarts |
| ▸ | Jobs | jobs left "running" by a restart are never closed out |
| | Library | search silently drops matches past 5000 and reports a wrong total |
| | Library | the track count includes quarantined, missing and corrupt rows |
| | Duplicates | the metadata layer blocks on 5-second buckets but compares with a 7-second tolerance |
| | Duplicates | an ignored group comes back on the next analysis |
| | Quarantine | the page shows at most 200 items with no pager |
| | Metadata | enrichment writes a different year than the one it voted for |
| | Metadata | Discogs' relevance check can never reject a release |
| | Metadata | tracks are marked as having artwork when none was written |
| | Apple Music | "Matched tracks" is never refreshed after a match run |
| | Dashboard | "Incomplete albums" counts only files carrying a track-total tag |
| | Jobs | `succeeded` and `failed` are always 0 — nothing writes them |
| | Organize | progress sits at 0/N for the whole run |

---

## Planned

### Multiple library folders

Add and remove library paths from Settings rather than setting one at install
time. Every containment check, the scanner, the organizer and the quarantine
mirror assume a single root today, so this is a real change rather than a new
field — and it needs the schema migrations that 2.2.0 introduced.

### Cleaning and tidying options

A set of explicit choices for what "tidy up" means: empty folders, orphaned
`.lrc` and cover files, Synology's `@eaDir` leftovers, files that no longer
have a track row, and folders left behind by a move. Preview first, quarantine
rather than delete, as everywhere else.

### Usage statistics and a log viewer

A statistics section built on what the database already records — tracks over
time, storage reclaimed, jobs run, duplicates resolved — and a viewer for
`cadenza.log`, which currently cannot be read from the interface at all. The
log is redacted for the three provider keys before it is written; anything
serving it has to sit behind the session and take no path parameter.

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
