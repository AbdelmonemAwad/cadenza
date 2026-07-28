# Contributing to Cadenza

Thanks for your interest. This document covers the local setup, the conventions
the codebase follows, and what a reviewable change looks like.

## Local development

You do not need a NAS to work on Cadenza. Point it at any folder of audio files.

```bash
git clone https://github.com/AbdelmonemAwad/cadenza.git
cd cadenza
```

**Backend**

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
```

You also need `ffmpeg` and `fpcalc` on your PATH:

| Platform | Command |
|---|---|
| Debian/Ubuntu | `sudo apt install ffmpeg libchromaprint-tools` |
| macOS | `brew install ffmpeg chromaprint` |
| Windows | `winget install Gyan.FFmpeg` and download Chromaprint |

Run it:

```bash
CADENZA_MUSIC_ROOT=/path/to/music \
CADENZA_CONFIG_DIR=./_devconfig \
CADENZA_QUARANTINE_ROOT=./_devquarantine \
python -m uvicorn app.main:app --reload --port 8000
```

**Frontend**

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173, proxies /api to port 8000
```

## Branches and versions

**One working branch, `dev`. Everything reaches `main` through a pull
request.** A ruleset on `main` enforces it — a direct push is refused with
`GH013: Changes must be made through a pull request` — and after each merge
`dev` is fast-forwarded to `main` so the two never drift.

`dev` exists only because a pull request needs a source branch; there is no
second line of development, and nothing should ever sit on `dev` that is not
on its way to `main` this week.

**Every change that ships bumps `VERSION`.** DSM compares the build number
after the dash and silently declines a package that is not newer than the one
installed, so a forgotten bump does not fail — it looks like "the install did
nothing", which is a much worse way to find out. `scripts/check-version.sh`
checks the shape, that `backend/app/config.py` agrees, that the generated
`INFO` agrees, and that the number actually increased against the base branch.
Documentation-only changes are exempt because they ship nothing.

Add a `CHANGELOG.md` entry in the same commit. Say what the user sees, not
what the diff does.

## Before opening a pull request

```bash
cd backend  && ruff check app tests && pytest
cd frontend && npx tsc --noEmit && npm run build
```

CI runs exactly these, plus a Docker build, the `.spk` build, a secret scan
over both the working tree and the history, a dependency-licence check, and
guards for control characters, CRLF and version consistency. **A red build is
never merged.**

### If you change a database model

Add a migration to `backend/app/db/migrations.py` in the same change.
`create_all` will not add a column to a table that already exists, so without
one your change reaches every fresh install and no database that already holds
a library — and CI would not notice, because CI always starts empty. The guard
in `tests/test_migrations.py` fails by name if you forget. Do not "fix" it by
editing `tests/baseline.sql`; that makes the test pass and leaves every
existing user with a database the code no longer matches.

## Conventions

**Language.** All source code, comments, docstrings, commit messages and API
error strings are in English. User-facing UI text is never hardcoded — it goes
in `frontend/src/i18n/`.

**Comments.** Explain *why*, not *what*. The line `# increment counter` above
`i += 1` adds nothing; a note explaining why a threshold is 0.90 rather than
0.5 saves the next reader an hour. Prefer no comment over a redundant one.

**Adding a translation key.** Add it to `frontend/src/i18n/en.ts` first, then
to `ar.ts`. The Arabic catalogue is typed as `Catalogue<typeof en>`, so a
missing key fails `tsc` — this is intentional and should not be worked around
with `as any`.

**Adding a language.** Copy `ar.ts`, translate the values, then register it in
`LOCALES` in `frontend/src/i18n/index.tsx`. Right-to-left languages need no
extra CSS: the layout uses logical properties throughout.

**Safety rules.** These are not negotiable, because the software operates on
libraries people cannot rebuild:

- Nothing is ever deleted outright. Removal means moving to quarantine.
- Every destructive operation supports `dry_run` and defaults to it.
- Every file mutation writes an `AuditLog` row.
- A duplicate cluster must always keep exactly one copy; the API rejects
  anything else.

**Adding a metadata provider.** Subclass `BaseProvider`, implement `lookup()`,
return `TrackMetadata` with a calibrated `confidence`, then add per-field trust
values to `FIELD_TRUST` in `providers/aggregator.py`. Set `rate_calls` and
`rate_period` to the provider's documented limits — not higher.

**Adding a conversion preset.** Add the entry to `PRESETS` in
`core/transcode.py`, then add `label`/`desc` to both i18n catalogues and list
the name in `LOCALISED_PRESETS` in `pages/Convert.tsx`.

## Tests

`backend/tests/` holds the suite. Engine tests are deterministic and need no
network, no NAS and no audio files — fingerprints are synthesised, and tracks
are inserted straight into SQLite.

A bug fix should come with a test that fails without it. The LSH banding test
in `test_fingerprint.py` is the model: it encodes the property that was broken
(a re-encoded copy must share at least one bucket), not just the symptom.

## Commit messages

Use a short imperative subject with a scope prefix:

```
dedup: index fingerprints under multiple LSH bands

A single band lost roughly half of all re-encoded pairs, because one
flipped bit moves the track to a different bucket.
```
