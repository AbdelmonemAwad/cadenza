# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/).

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
