# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/).

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
