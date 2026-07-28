# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/).

## [2.4.0] - 2026-07-28

### Fixed

- **Converting no longer throws your metadata away.** Measured on a DS1821+
  across all twelve presets, before and after:

  | preset | cover before | cover after |
  |---|---|---|
  | `opus_160`, `opus_96` | lost | kept |
  | `ogg_q6` | lost | kept |
  | `wav` | title, artist, album and cover all lost | all kept |

  Three separate causes. `write_tags` did not know what an Opus file is, so
  converting to the preset Cadenza itself recommends as the most efficient
  raised `writing tags is not supported for .opus` — logged once, and the
  conversion still reported success. The cover was embedded only for FLAC, so
  Ogg Vorbis lost it too, silently, because the tags themselves were written.
  And the reader dispatched on the tag class's *name*: WAV and AIFF keep ID3 in
  a container chunk, which mutagen represents as `_WaveID3`, so a correctly
  tagged WAV read back empty.

  Everything in an Ogg container carries Vorbis comments, Opus included; covers
  now go in the `metadata_block_picture` comment that Ogg players expect; and
  the reader dispatches with `isinstance`.

### Added

- **Upload a credential file from Settings, or browse to one already on the
  NAS.** The Apple Music signing key had to be copied onto the NAS by hand into
  a folder the user had to find for themselves. Cadenza now takes the file
  directly, stores it in its own data folder at `0600` through the same writer
  as every other secret, and never reads it back out. Files picked on the NAS
  are copied rather than referenced, so nothing breaks later when the original
  is moved.
- **A folder browser**, deliberately narrow: directories only, rooted in the
  library, the data folder and the DSM volumes, with system paths denied
  outright. File names appear only when the caller names a known credential,
  and then only that credential's extensions — "the `.p8` files in this folder"
  is what the user is already looking for; "everything in this folder" is their
  documents. Folders the service account cannot enter are shown disabled rather
  than hidden, because that is the one thing the user has to go and fix.

## [2.3.1] - 2026-07-28

### Fixed

- **An unreadable `etc/cadenza.env` no longer makes the package unmanageable.**
  `.` is a POSIX special built-in: when it fails, the shell exits immediately
  with a non-zero status, and there is no `|| true` to hang that failure on. So
  one of these files ending up owned by `root` — which is what happens the
  moment anyone edits it over SSH with `sudo` — made `start-stop-status` exit 1
  on *every* invocation. DSM calls `prestop` before an upgrade, got 1, and
  abandoned the upgrade. Observed on a DS1821+; the reason appeared only in
  `/var/log/packages/Cadenza.log`:

  ```
  Begin start-stop-status prestop
  .../etc/cadenza.env: Permission denied
  End start-stop-status prestop ret=[1]
  ```

  Readability is now tested before the built-in is reached, so the package
  starts on its defaults and writes the problem — and the exact `chown` that
  fixes it — to the service log.
- **The Apple Music signing key can be found on the Synology package.**
  `apple_private_key_path` defaulted to the literal `/config/AuthKey.p8`, which
  is the container's layout. On the package the data folder is somewhere else
  entirely, so the key was never found and Apple Music could not be configured
  at all. It now resolves to this install's own data folder unless a deployment
  pins a path explicitly.

## [2.3.0] - 2026-07-28

### Changed

- **A library is browsable in about two minutes instead of forty.** Measured on
  a DS1821+ over 3801 real files, a scan spent its time like this:

  | stage | per file | share |
  |---|---|---|
  | ffprobe | 64 ms | 2.5% |
  | read tags | 19 ms | 0.7% |
  | sha256 | 79 ms | 3.1% |
  | **audio_md5** | **1705 ms** | **67.0%** |
  | **fingerprint** | 678 ms | 26.6% |

  94% went on two figures that nothing needs in order to browse, search, sort by
  quality or organise: `audio_md5` decodes the whole file to PCM and the
  fingerprint decodes two minutes of it again, and both exist only so duplicate
  detection can group by them. They now run as their own job afterwards — one
  you can watch, stop, and start again to continue, because it selects the
  tracks still missing a value.

### Fixed

- **Stopping a scan now stops it.** The button, the endpoint and the cancel flag
  all existed, but `handle_scan` never looked at the flag — so the longest job
  in the application, and the one most worth stopping, ignored the request
  entirely. The scan checks between batches, which keeps everything already
  indexed and stops within seconds.
- **A stopped job is recorded as stopped**, not as `done`. It returned normally
  with partial numbers and was written down as a success, so a scan halted at
  200 of 3801 files reported those 200 as the whole library.
- **Stopping a scan does not empty your library.** A halted scan has
  legitimately not visited most of the files, and the sweep that marks unseen
  tracks `MISSING` would have marked nearly all of them — the user asks a long
  job to stop and watches the library appear to delete itself. The sweep now
  runs only after a pass that finished.
- **Queued jobs can be stopped from the interface.** The runner has always
  accepted cancelling a pending job; the button only appeared once one was
  running, so anything waiting behind a long scan could not be dropped.
- **`/volume1/music` no longer matches `/volume1/musicbox`.** Existing rows were
  selected with a bare prefix match, so scanning one folder loaded a
  similarly-named sibling's tracks and then marked every one of them `MISSING`,
  because that pass never visits them.

## [2.2.0] - 2026-07-28

### Fixed

- **Updating never starts you from scratch.** The directory holding the
  database, your account and your API keys used to be re-derived on every single
  start, from a path the install wizard recorded. That is a data-loss trap, and
  it was armed on a real DS1821+: the wizard had written
  `/volume1/docker/cadenza`, the service account could not reach it, so Cadenza
  quietly used its own folder instead and put everything there. Nothing said so.
  The moment that folder became writable — the user creates it, or ticks the
  service account on the share — the next restart would have switched to it,
  found it empty, and shown the create-your-account screen as though every scan
  and every setting had been lost. The directory is now decided once and
  remembered, in `etc/data-dir.conf` and again in `var/`, and it is read back
  before anything else. Six scenarios are exercised against DSM's own shell.
- **Cadenza refuses to start rather than pretend your data is gone.** If the
  remembered directory cannot be reached — volume not mounted, permission
  withdrawn — starting anyway would offer a fresh account over an empty database
  while the real one sat intact a directory away, and a user who accepts that
  offer starts writing into the wrong place. It now stops and says exactly what
  is unreachable and what to check.
- **A failed upgrade stage no longer loses the account.** `preupgrade` gives up
  quietly if it cannot create its staging directory and `postupgrade` only
  checks that the directory exists, not that anything landed in it. The record
  of where the data lives is therefore kept a second time under `var/`, which
  DSM preserves on its own with no staging involved.
- **The install wizard stopped misdescribing itself.** Its data-folder default
  was `/volume1/docker/cadenza`, left from when the package wrapped Docker, and
  it could not be left blank — so the zero-configuration path that `postinst`
  was written around was unreachable. It also claimed the installer would grant
  the service account access to the folder, which an unsigned DSM package cannot
  do. The default is now empty (the package's own folder, always writable,
  preserved across updates), and both permission steps are stated plainly. The
  dead `wizard_run_user` field, which nothing has read since the Docker version,
  is gone.
- **`settings.json` can no longer override locked settings.** The API refuses to
  write `config_dir`, `music_root` and the tool paths; the same file is read
  straight back into the settings object at startup, so until now a value that
  the API rejected took effect anyway if it reached the file by any other route.
  `config_dir` is the one that matters: it decides where the database lives.

### Added

- **Schema migrations.** `create_all` adds missing tables and looks at nothing
  else, so a new column on an existing model reached every fresh install and no
  database that already held a library — and CI could never see it, because CI
  always starts empty. The only person who found out was someone who already had
  data. There is now a `schema_version`, an ordered migration table, and a
  guard test that builds a database at the frozen baseline schema, migrates it,
  and requires the result to match what the models produce. Adding a column
  without a migration now fails in CI, by name.
- **A backup before every migration**, taken with `VACUUM INTO` rather than a
  file copy: the database runs in WAL mode, so copying only the `.db` would
  produce a backup missing everything committed since the last checkpoint, and
  it would look perfectly valid.

## [2.1.3] - 2026-07-28

### Fixed

- **DSM can start the package.** It could not before — not on demand, and not at
  boot. DSM 7 calls `start-stop-status prestart` before `start` and treats a
  non-zero exit as a failed precheck; the script handled six verbs and answered
  everything else from a catch-all that printed a usage line and exited 1. So
  every start attempt ended at `prestart ret=[1]`, Package Center reported
  `Failed to pass precheck`, and `start` was never reached. The package had only
  ever been started by hand over SSH, which works and leaves a healthy-looking
  service answering `/health`, so nothing exercised the path DSM actually uses.
  The lifecycle verbs are now no-ops and the catch-all exits 0: an unrecognised
  verb costs nothing if ignored, while refusing it stops the package running at
  all.

## [2.1.2] - 2026-07-28

### Fixed

- **Uninstalling now stops the service.** `preuninst` was a no-op left over from
  the Docker-era package, and its comment still claimed the package did not run
  a service — untrue from the moment Cadenza started running natively. Removing
  the package from Package Center deleted `/var/packages/Cadenza` while uvicorn
  kept running from the deleted files: still bound to the port, still answering
  `/health` with the version that had just been uninstalled, while Package
  Center reported it gone. A reinstall would then have found its own port held
  by its own predecessor and died at startup with `Address already in use`.
  Found on a DS1821+, where an uninstalled 2.0.3 was still serving requests.
- **Stopping no longer depends on the PID file alone.** That file lives on the
  volume, disappears if the data directory is cleared, and says nothing about a
  process that outlived the script which recorded it. `stop` now also finds
  anything still running out of the package's own `target/` directory — read
  from `/proc`, because DSM's `ps` and BusyBox's `ps` disagree about which flags
  print a full command line — and gives it SIGTERM before SIGKILL.

## [2.1.0] - 2026-07-28

### Changed

- **You create the account.** Cadenza no longer generates a password, writes it
  to `initial-password.txt` on the config volume, or forces a change at first
  sign-in. A fresh install has no account at all: the first person to open the
  interface chooses the username and the password. `POST /auth/setup` works
  exactly once and answers `409` afterwards, so nobody can re-claim an install,
  and it is rate limited like sign-in because it is reachable without a session
  until it is used. Sign-in takes the username too, and still hashes the
  password when the username is wrong so the timing does not reveal which was
  incorrect.

### Removed

- The forced password-change screen and its scoped session subject.
- Any credential written to disk that the user did not choose.

## [2.0.3] - 2026-07-28

### Fixed

- **The package required Docker.** `INFO` declared
  `install_dep_packages="ContainerManager>=20.10"`, left from when it wrapped
  the container, and DSM enforces that at install time — so a NAS without
  Container Manager refused to install Cadenza. The package now declares no
  dependencies at all.
- `postinst` no longer generates a `docker-compose.yml`, and the build no longer
  ships one inside the package. `docker-compose.yml` stays in the repository for
  anyone who prefers a container; that is a choice, not a dependency.
- Removed the `synoacltool` call and the root request in `conf/privilege`. DSM
  refuses an unsigned package that asks for root — measured on a DS1821+, error
  4557 at upload — so the one permission that cannot be automated is now stated
  once and points at Control Panel rather than a shell.

## [2.0.0] - 2026-07-27

### Added

- **The package runs the application.** It bundles CPython 3.12, every Python
  dependency as a wheel, and static `ffmpeg`, `ffprobe` and `fpcalc`. It needs
  no Docker, no Python from Package Center and no root, and runs as an
  unprivileged service account. DSM's own `python3` is 3.8; this codebase
  requires 3.11 or newer, which is why the interpreter travels with the package.
- The API serves the built frontend when `www_dir` is set, since the native
  package has no nginx. Unset in the container, so that path is unchanged.
- Sign-in throttling, per address and globally, and a licence check that fails
  the build when a new copyleft dependency appears.

### Fixed

- **Audio conversion had never worked.** The temporary file was named
  `track.flac.part`; no preset passes `-f`, so ffmpeg picks its muxer from the
  extension and `.part` is not one. Every conversion failed for every preset.
  Nothing caught it because the tests never invoked ffmpeg.
- **The desktop icon had never existed.** `INFO` declares `dsmuidir="ui"`, which
  DSM resolves against `target/`, but `ui/` was only copied into the outer
  archive. CI checked the outer archive and passed.
- Six paths that could delete a file with no way back, including conversion
  deleting sources outside quarantine and `POST /quarantine/purge?force=true`
  bypassing `hard_delete_allowed`.
- Path containment and a positive allowlist for settings writable over the API,
  closing a remote code execution primitive (`ffmpeg_bin` was argv[0] of every
  subprocess) and an arbitrary file read.
- Credential files are written `0600` from the moment they exist, and provider
  keys are redacted from the log.

### Removed

- `Unidecode` — GPL-2.0-or-later, a direct pin, imported nowhere, and shipped in
  both the image and the package.

## [1.0.0] - 2026-07-27

Initial release.

### Added

- **Duplicate detection** across four layers: exact file (SHA-256), exact audio
  stream (MD5 of decoded samples), acoustic fingerprint (Chromaprint/AcoustID),
  and normalised fuzzy text matching.
- **Decision engine** scoring every copy across eight weighted criteria, with a
  per-criterion breakdown surfaced in the UI and manual override support.
  Clusters whose top two scores are within 0.03 are flagged ambiguous and never
  auto-actioned.
- **Quarantine** replacing deletion entirely, mirroring the original directory
  tree, with one-click restore and a configurable retention period. Permanent
  purge is disabled by default.
- **Metadata aggregation** from MusicBrainz, AcoustID, Apple Music, Discogs,
  Last.fm and LRCLIB, merged by per-field weighted vote with conflict reporting.
- **Audio conversion** with twelve FFmpeg presets across lossless and lossy
  targets, carrying tags and cover art to the output.
- **Library organisation** with configurable path templates, safe filename
  sanitising, sidecar handling and empty-directory pruning.
- **Tag support** for ID3v1/v2, Vorbis comments, MP4/iTunes atoms and ASF/WMA,
  including embedded artwork and synced lyrics (SYLT and .lrc).
- **Web UI** in English and Arabic with full right-to-left support and a runtime
  language switch.
- **Scheduling** of background jobs via cron expressions, plus a live progress
  feed over WebSocket.
- **Audit log** recording every file mutation with source, destination and
  reversibility.
- **Synology packaging** as a Docker-wrapped `.spk` for DSM 7 on x86_64, with
  an installation wizard, upgrade state preservation, and a `cadenza` CLI.

### Notes

- Arabic text normalisation folds diacritics, tatweel, hamza forms, alef
  maqsura and ta marbuta, so spelling variants of the same title match.
- Fingerprint similarity is a Hamming bit-agreement ratio with a ~0.50 baseline
  for unrelated audio. The default threshold of 0.90 accounts for this; values
  below ~0.85 will produce false matches.
