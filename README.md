<div align="center">

# Cadenza

**Music library curation for Synology NAS.**
Deduplicate by sound, not by filename. Complete your tags from five sources.
Convert formats. Never lose a file.

[![CI](https://github.com/AbdelmonemAwad/cadenza/actions/workflows/ci.yml/badge.svg)](https://github.com/AbdelmonemAwad/cadenza/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![DSM](https://img.shields.io/badge/DSM-7.x-0f7bd8.svg)](https://www.synology.com/dsm)
[![arch](https://img.shields.io/badge/arch-x86__64-lightgrey.svg)](#requirements)

[العربية](README.ar.md) · [Architecture](docs/ARCHITECTURE.md) · [Contributing](CONTRIBUTING.md)

</div>

---

## What it does

A music library that grew over fifteen years has the same song four times: a
FLAC rip, a 192 kbps MP3 from a phone backup, an untagged copy in
`Downloads/New folder/`, and a duplicate with different cover art. Filename
comparison finds none of these. Cadenza finds all four, tells you which one to
keep and why, and moves the rest somewhere you can get them back from.

- **Four-layer duplicate detection** — byte-identical files, identical audio
  streams under different tags, acoustic fingerprint matches across formats and
  bitrates, and a textual fallback for files that cannot be fingerprinted.
- **Explainable decisions** — every copy gets a scored breakdown across eight
  criteria. You see *why* the FLAC won before anything moves, and you can
  override it.
- **Nothing is ever deleted** — flagged files move to a quarantine folder and
  restore to their original path with one click. Permanent deletion is off by
  default and must be explicitly enabled.
- **Metadata from five sources** — MusicBrainz, Apple Music, Discogs, Last.fm
  and AcoustID, merged by a per-field weighted vote rather than
  first-source-wins. Discogs knows pressing years; Apple has the best artwork;
  MusicBrainz owns the identifiers.
- **Audio conversion** — twelve FFmpeg presets, from FLAC archival down to
  Opus 96k, with tags and cover art carried across.
- **Arabic and English UI** — full right-to-left support, switchable at runtime.
- **Runs on a schedule** — cron-based background scanning and cleanup.

## Screenshots

> Dashboard, duplicate review with score breakdown, and the conversion page.
> *(Add screenshots to `docs/images/` and reference them here.)*

## Requirements

| | |
|---|---|
| **Hardware** | Synology NAS, `x86_64` — DS1821+, DVA3221, DS920+, DS1621+, and similar |
| **DSM** | 7.0 or newer |
| **Prerequisite** | Container Manager (or the older Docker package) installed and running |
| **Free space** | ~2 GB for the container image and the library index |

ARM-based models (DS220j, DS223, …) are not supported: the image is built for
`linux/amd64`.

## Install

Download the latest `.spk` from
[Releases](https://github.com/AbdelmonemAwad/cadenza/releases), then in DSM:

**Package Center → Manual Install → select the file**

The installer asks for your music folder path, a web port, a timezone, and a
default language. It never modifies files during installation.

Open Cadenza from the DSM desktop, or go to `http://<nas-address>:8760`.

### First run

1. **Settings → Provider API keys.** Add at least an
   [AcoustID key](https://acoustid.org/new-application) (free) — acoustic
   fingerprinting is what makes cross-format duplicate detection work.
   MusicBrainz needs no key, only a contact address.
2. **Dashboard → Scan library.** A first scan hashes and fingerprints every
   file, so expect roughly 20–60 minutes for 50,000 tracks. Later scans are
   incremental and take seconds.
3. **Duplicates → Analyse.** Review the report. Nothing has moved yet.
4. When the proposed decisions look right, **Move duplicates to quarantine**.

## Build from source

```bash
git clone https://github.com/AbdelmonemAwad/cadenza.git
cd cadenza
make build          # builds the Docker image, then dist/Cadenza-x86_64-1.0.0-0001.spk
```

Building the `.spk` needs Linux, WSL2 or macOS (it uses `tar` and `md5sum`);
you can also run it directly on the NAS over SSH. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the development setup.

## How duplicate detection works

Four layers run cheapest-first, and each file is attributed to the strongest
layer that catches it.

| Layer | Method | Catches |
|---|---|---|
| `exact_file` | SHA-256 over the whole file | Literal copies |
| `exact_audio` | MD5 over the decoded audio stream | Same audio, different tags or artwork |
| `acoustic` | Chromaprint fingerprint + AcoustID | Same recording as FLAC vs MP3, any bitrate |
| `metadata` | Normalised fuzzy text match | Files that could not be fingerprinted |

The acoustic layer is the expensive one, so candidate pairs are narrowed by a
duration window plus multi-band LSH bucketing before any comparison runs.

**One calibration detail worth knowing.** Fingerprint similarity is a Hamming
bit-agreement ratio, so two *completely different* songs score around **0.50**,
not 0. The baseline is 0.5 because unrelated bits agree half the time. The
default threshold is 0.90; a re-encode of the same master lands at 0.92–0.99.
Setting the threshold below ~0.85 will produce false matches.

### Choosing which copy to keep

Eight weighted criteria, all visible in the UI:

| Criterion | Weight | Rationale |
|---|---|---|
| Format | 30% | Lossless outranks lossy |
| Bitrate | 22% | Measured against the best lossy peer in the cluster |
| Resolution | 12% | Sample rate and bit depth |
| Tag completeness | 16% | Weighted across ten fields |
| Artwork | 8% | Present, and at what resolution |
| Duration | 5% | Catches truncated copies |
| Lyrics | 4% | Synced beats plain |
| Path quality | 3% | Penalises `Downloads/`, `New folder/`, `(1)` |

When the top two scores are within 0.03 the cluster is marked **ambiguous** and
is never auto-actioned — it waits for you.

## Metadata sources

| Source | Key needed | Strongest at |
|---|---|---|
| [MusicBrainz](https://musicbrainz.org) | No (contact e-mail) | Identifiers, canonical titles |
| [AcoustID](https://acoustid.org) | Free | Identifying a recording from audio alone |
| [Apple Music](https://developer.apple.com/musickit/) | Developer Program | Artwork, ISRC, track numbers |
| [Discogs](https://www.discogs.com/developers) | Free token | Pressing years, rare and regional releases |
| [Last.fm](https://www.last.fm/api) | Free | Genres, correcting misspelled artists |
| [LRCLIB](https://lrclib.net) | No | Synced lyrics |

Results are merged with a **per-field weighted vote**: each field is won by the
source with the highest (match confidence × that source's trust for that
field). Conflicts are surfaced in the UI rather than silently resolved.

## Configuration

Everything is editable in **Settings**, and stored in
`/var/packages/Cadenza/var/config/settings.json` on the NAS. Environment
variables use the `CADENZA_` prefix — see [`.env.example`](.env.example).

Path templates support `{albumartist}` `{artist}` `{album}` `{year}` `{track}`
`{disc}` `{title}` `{genre}`:

```
{albumartist}/{year} - {album}/{track:02d} - {title}
```

## Safety model

This software operates on files people cannot re-download. The design reflects
that:

- Every destructive job supports `dry_run`, and **defaults to it**.
- Deletion means moving to quarantine, with the original tree mirrored so
  restores are obvious. Retention is 30 days by default.
- Permanent purge is disabled unless explicitly turned on.
- Every file mutation writes an audit-log entry with source, destination, and
  whether it can be reversed.
- A duplicate cluster must always retain exactly one copy — the API refuses
  anything else.
- The container runs as a normal user, never root, so files written into your
  share stay owned by you.

## Project layout

```
backend/     FastAPI service, engines, providers, tests
frontend/    React + TypeScript UI with en/ar i18n
docker/      Dockerfile, compose file, nginx config, entrypoint
packaging/   Synology .spk build scripts and DSM metadata
docs/        Architecture notes
```

## Licence

[MIT](LICENSE).

Cadenza is not affiliated with Synology, Apple, MusicBrainz, Discogs or Last.fm.
Respect each provider's terms of service and rate limits when configuring keys.
