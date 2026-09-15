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

### The rest of the audit — 2.10.1 to 2.10.3

Verified by the test suite and CI's package build, and 2.10.3 was exercised
on a DS1821+ running DSM 7.2 on 2026-09-13 — see **In progress** for what
that pass covered and what it turned up.

- Discogs can reject a release again: its relevance check compared the
  searched title against itself and scored every release 1.0.
- One failed request no longer replaces the dashboard until the browser is
  reloaded, and the Activity log says when it could not load.
- A scan cannot index the quarantine, whatever it is named and whatever
  `skip_hidden` is set to; tidying up cannot walk into it either.
- A scan with an unusable `ffprobe` fails and names the binary, instead of
  indexing the whole library as healthy with no technical data.
- The duplicates header sums the same set it counts; "Started" on a job is
  when it started; schedule actions report their failures.
- "Matched tracks" refreshes after a run; linking waits for MusicKit's own
  ready event rather than a 400 ms guess; imported playlists — and the
  entries the library lacks, with their Apple Music links — are listed on
  the page.

### Runs on both machines it was built for — 2.10.4

- A library the service account cannot read is a state the dashboard shows,
  with the Control Panel steps, rather than a crash at import. That crash is
  what kept the DVA3221 at `start_failed` for 47 days over one missing
  permission; with the permission granted, 2.10.3 scanned its 3,801-file
  library there at about ten files a second on the Atom C3538.
- `/settings/health` reports whether the library is `readable`, not only
  whether it exists — a share the account may not enter still answers `stat`.
- The quarantine folder is created by the first move into it, and nothing
  creates directories at import any more.

### Progress that ends where the job did — 2.10.5

- Duplicate analysis no longer finishes showing `20000/4`. Progress is written
  as a pair in one statement, reports from the engine's thread are applied in
  order with a final `100/100`, and the engine reports on one scale.
- Enrichment runs past the fifth track (2.10.7): the job commits after every
  track instead of holding the write lock across every provider call, and a
  progress report that cannot get the lock is logged rather than fatal.

### Integrations explained where they are set up — 2.10.6

- Each provider key says what the source contributes and links to where the
  key is issued. The Apple Music card explains the Developer Program, the Team
  ID, the MusicKit key and the `.p8` upload, with links.
- Linking Apple Music is explained before the button: Apple's own window, no
  password ever seen by Cadenza, a revocable token. Each step that can fail
  names itself, and a failure at Apple's window on a plain-HTTP address points
  at HTTPS through DSM's reverse proxy, which the install guide walks through.
- Still to confirm on hardware: whether Apple's window hands the token back to
  a plain `http://` origin at all. That needs a Developer Program account.
- 2.10.8: the catalogue works with no key at all through Apple's key-free
  search — matching, artwork, track numbers, release dates, links — and the
  page says the Developer Program is needed only for playlists.
- 2.10.10: enrichment reports `unmatched` apart from `failed`, and points at
  AcoustID when most tracks could not be identified by name.

### Releases that carry what they must — 2.10.9 and 2.10.11

- The package build rides out a short outage at GitHub (2.10.9): curl's own
  backoff runs for up to three minutes on a `5xx`, and a real `404` still
  fails at once.
- A rerun cannot publish a release with a stale source archive (2.10.11).
  2.10.8 went out without the chromaprint source because the rerun picked the
  artifact a failed attempt had uploaded. Artifacts are replaced on rerun, the
  source is uploaded only from a successful build, and the release job checks
  that both the ffmpeg and the chromaprint archive are present before it
  publishes — no corresponding source, no release.

### Preview, then apply — 2.11.0

- Nothing the pages offered had ever written to a library: enrichment was a
  hard-coded preview, organizing had no page, and the dashboard's actions were
  previews with nowhere to go. The Library page applies enrichment after a
  confirmation, the new Organize page previews the plan and applies it, and
  Convert opens on the engine's suggestions when a library has no legacy
  formats. Documented with the rescan a media server needs afterwards.
- 2.11.1: a lookup no longer drops the provider that lost a race for the
  caller's session, and enrichment no longer holds the write lock across its
  network calls — the provider cache reads and writes through short sessions
  of its own.

---

## In progress

An audit of every feature area produced 51 confirmed defects, each verified by
an independent pass before it was accepted. Thirty shipped in 2.5.0 through
2.8.0 — the data-integrity ones first, then the places where the interface
and the application disagreed, then the wrong numbers. The ten that were
listed here after that shipped in 2.10.1 and 2.10.3, and a further one found
in the meantime in 2.10.2. Nothing from the audit is outstanding.

2.10.3 was installed on a DS1821+ running DSM 7.2 on 2026-09-13, over a
4,025-track library, and later the same day on the DVA3221 (see below);
on the DS1821+ it was exercised as follows: the package upgraded in place with
the database and the account intact; a scan passed the new `ffprobe`
preflight, which settles whether the FFmpeg 9.0 build's shared libraries load
on DSM; the duplicates header agreed with the dashboard over 1,324 groups;
the Jobs page showed real start times and reported the job it queued; the
Apple Music page rendered its new section; and the application log carried
no error or warning after the upgrade.

Open from that pass:

- **#54** — shipped in 2.10.5; see **Done**.
- **DVA3221** — now running 2.10.3 (DSM 7.2.1, Atom C3538): the only thing
  wrong was a share permission, which 2.10.4 turns from a silent crash into a
  banner. A full first scan there took 6 minutes 37 seconds for 3,801 files,
  against about two minutes on the DS1821+.

---

## Planned

### Multiple library folders

Add and remove library paths from Settings rather than setting one at install
time. Every containment check, the scanner, the organizer and the quarantine
mirror assume a single root today, so this is a real change rather than a new
field — and it needs the schema migrations that 2.2.0 introduced.

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
