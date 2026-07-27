# Third-Party Licences

Inventory of every third-party component Cadenza distributes, with the version
shipped, the licence it carries, and how the code is actually used.

Cadenza itself is MIT (`LICENSE`, "Copyright (c) 2026 Cadenza Project").

Two artefacts are covered here, and they ship the same set of components:

- the Synology package built by `packaging/synology/build-native-payload.sh`,
  which vendors a CPython interpreter, every wheel in `backend/requirements.txt`,
  and three native binaries;
- the container image built by `docker/Dockerfile`, which pip-installs the same
  `backend/requirements.txt` (line 28) on top of a base image.

Everything below was read from the shipped wheels' own `dist-info/METADATA` and
from the upstream licence files in the binary archives. Where a package
publishes no SPDX identifier, the section [How this inventory was
produced](#how-this-inventory-was-produced) records what the identifier was
inferred from.

## How to read the "used as" column

| Term | Meaning |
| --- | --- |
| imported | Cadenza's own source contains an `import`; the code runs inside Cadenza's interpreter process. |
| in-process | No literal `import` in `backend/app`, but the module is loaded into the same interpreter at runtime by something that is imported (uvicorn, SQLAlchemy, starlette, PyJWT). Legally identical to "imported". |
| transitive | Pulled in as a dependency of a dependency; loaded in the same interpreter when the dependent code path runs. |
| subprocess | A separate executable launched with `subprocess.run()`. Its own process, its own address space. |
| shipped, unused | Present in the payload but never loaded. |

The distinction between the first three and `subprocess` is the one that carries
legal weight. It is explained in [Why linkage matters](#why-linkage-matters).

## Copyleft components

These six components carry copyleft licences. They are listed separately because
they are the ones that constrain how Cadenza may be distributed.

| Component | Version | Licence | Copyleft | Used as |
| --- | --- | --- | --- | --- |
| mutagen | 1.47.0 | GPL-2.0-or-later | strong | **imported** |
| ffmpeg | n7.1 (BtbN, linux64 lgpl-shared) | LGPL-3.0-or-later | weak | subprocess |
| ffprobe | n7.1 (BtbN, linux64 lgpl-shared) | LGPL-3.0-or-later | weak | subprocess |
| fpcalc | 1.5.1 (chromaprint, linux-x86_64) | LGPL-2.1-only | weak | subprocess |
| certifi | 2026.7.22 | MPL-2.0 | file-level | transitive |

### mutagen 1.47.0 — GPL-2.0-or-later, imported

Tag reading and writing. Imported into Cadenza's own modules, so it runs in the
same interpreter process as Cadenza's MIT-licensed code. This is the unresolved
item; see [The open question](#the-open-question-mutagen).

Licence determined from `mutagen-1.47.0.dist-info/METADATA`
(`License: GPL-2.0-or-later`, plus the GPLv2+ classifier); full text in
`mutagen-1.47.0.dist-info/COPYING`.

### Unidecode — removed

It was pinned directly at `backend/requirements.txt` and is GPL-2.0-or-later,
but it was imported nowhere: a repository-wide, case-insensitive search returned
exactly one hit, the requirements line itself. It was not a transitive
dependency either — removing the pin removes it from the resolved tree
entirely, so nothing else needed it.

It was nevertheless distributed, into the container image by
`docker/Dockerfile` and into the native payload by
`packaging/synology/build-native-payload.sh`. That put a copyleft component
inside an MIT-licensed package in exchange for no functionality at all.

The pin was deleted. Arabic and Latin normalisation is done by
`backend/app/core/dedup.py` without it. A comment in `requirements.txt` records
why it is absent, so it is not innocently added back.

### ffmpeg and ffprobe n7.1 — LGPL-3.0-or-later, subprocess

Downloaded by `packaging/synology/build-native-payload.sh` from BtbN's
`ffmpeg-n7.1-latest-linux64-lgpl-shared-7.1.tar.xz`. The binaries land in
`STAGE/bin/`, the shared `libav*` objects in `STAGE/ffmpeg-lib/`.

Both are invoked only through `subprocess.run()`:

- `ffprobe` — `backend/app/core/audio_probe.py:43`
- `ffmpeg` — `backend/app/core/audio_probe.py:106`, plus transcode paths

**The version is LGPL 3, not LGPL 2.1.** BtbN's `lgpl-shared` variant resolves
through `variants/linux64-lgpl-shared.sh` → `defaults-lgpl-shared.sh` →
`defaults-lgpl.sh`, which sets `FF_CONFIGURE="--enable-version3 --disable-debug"`
and `LICENSE_FILE="COPYING.LGPLv3"`; the build then copies that file into the
archive root as `LICENSE.txt`. `--enable-version3` is forced because the variant
unconditionally enables the Apache-2.0 OpenCORE AMR and VMAF libraries.

The in-source comment at `build-native-payload.sh:27-31` reasons correctly that
choosing the `lgpl` variant avoids the GPL-only x264/x265 components, but it
stops one step short of that conclusion: avoiding GPL does not land on LGPL 2.1,
it lands on LGPL 3.

The difference is not pedantic:

- LGPL-2.1-or-later can be absorbed into GPL-2.0-only code; LGPL-3.0 cannot. If
  anything else in the bundle were GPL-2.0-only, the compatibility analysis
  would change.
- LGPLv3 is a set of additional permissions over GPLv3, so GPLv3's patent grant
  (§11), its patent-retaliation and termination regime (§8), and the
  Installation Information requirement reachable through LGPLv3 §4(e) apply.
  Cadenza ships as a `.spk` installed on a consumer NAS, which is the User
  Product fact pattern those clauses were written for. None of this exists under
  LGPL 2.1.
- The text that must accompany distribution is `COPYING.LGPLv3` plus the GPLv3
  text it incorporates — not `COPYING.LGPLv2.1`.

The aggregate is more precisely
`LGPL-3.0-or-later AND Apache-2.0 AND (BSD/MIT/ISC permissive components)`.

### fpcalc 1.5.1 — LGPL-2.1-only, subprocess

Chromaprint's audio fingerprinter, downloaded from
`chromaprint-fpcalc-1.5.1-linux-x86_64.tar.gz` and shipped as a single
self-contained binary (no accompanying libraries are staged). Invoked only via
`subprocess.run()` at `backend/app/core/fingerprint.py:35-37`.

Upstream `LICENSE.md` at tag v1.5.1 states that the work as a whole is licensed
under LGPL 2.1, with **no "or any later version" grant** — so this is
LGPL-2.1-*only*, unlike the ffmpeg binaries above. Chromaprint's own code is
MIT; the LGPL attaches because the v1.5.1 release binaries incorporate parts of
FFmpeg 4.4.1. A fuller expression is `MIT AND LGPL-2.1-only`; the effective
licence of the distributed binary is LGPL-2.1-only. Sections 4 and 6 apply to
redistribution of the binary.

Note that the MIT-licensed Python wrapper (`pyacoustid`) and this LGPL binary are
distinct components with distinct licences.

### certifi 2026.7.22 — MPL-2.0, transitive

CA certificate bundle, pulled in by both `httpx` (`Requires-Dist: certifi`) and
`requests` (`certifi>=2023.5.7`). MPL-2.0 is file-level copyleft: modifications
to certifi's own files must be published under MPL, but it imposes nothing on
code that merely uses it. Cadenza does not modify it. Licence text at
`certifi-2026.7.22.dist-info/licenses/LICENSE`.

## Permissive components

### Imported by Cadenza's own code

| Package | Version | Licence | Used as |
| --- | --- | --- | --- |
| fastapi | 0.115.6 | MIT | imported (14 files under `backend/app`) |
| sqlalchemy | 2.0.36 | MIT | imported (19 files) |
| pydantic | 2.10.4 | MIT | imported (9 files) |
| rapidfuzz | 3.11.0 | MIT | imported (5 files) |
| httpx | 0.28.1 | BSD-3-Clause | imported (2 files); pinned with the `[http2]` extra |
| pydantic-settings | 2.7.0 | MIT | imported (1 file) |
| pyacoustid | 1.3.1 | MIT | imported at `backend/app/core/fingerprint.py:63` |
| tenacity | 9.0.0 | Apache-2.0 | imported (1 file) |
| PyJWT | 2.10.1 | MIT | imported as `jwt` (1 file); pinned with the `[crypto]` extra |
| APScheduler | 3.11.0 | MIT | imported (1 file) |
| pillow | 11.1.0 | MIT-CMU | imported as `PIL` (1 file) |

### Direct pins loaded in-process, without a literal import

| Package | Version | Licence | Loaded by |
| --- | --- | --- | --- |
| uvicorn | 0.34.0 | BSD-3-Clause | the entry point itself — `python -m uvicorn app.main:app` (`packaging/synology/scripts/start-stop-status:92`); pinned bare, not `uvicorn[standard]` |
| websockets | 14.1 | BSD-3-Clause | uvicorn, to serve the `/jobs/stream` websocket declared at `backend/app/api/v1/jobs.py:83` |
| h11 | 0.14.0 | MIT | uvicorn, for HTTP/1.1 |
| aiosqlite | 0.20.0 | MIT | SQLAlchemy, via the `sqlite+aiosqlite:///` URL at `backend/app/config.py:139` |
| greenlet | 3.1.1 | MIT | SQLAlchemy's async bridge |
| cryptography | 44.0.0 | Apache-2.0 OR BSD-3-Clause (licensee's choice) | PyJWT, for ES256 Apple Music token signing |
| python-multipart | 0.0.20 | Apache-2.0 | starlette, for multipart form parsing |

### Transitive dependencies

| Package | Version | Licence | Comes in via |
| --- | --- | --- | --- |
| starlette | 0.41.3 | BSD-3-Clause | fastapi |
| pydantic-core | 2.27.2 | MIT | pydantic |
| annotated-types | 0.8.0 | MIT | pydantic |
| typing-extensions | 4.16.0 | PSF-2.0 | fastapi |
| anyio | 4.14.2 | MIT | starlette, httpx |
| idna | 3.18 | BSD-3-Clause | httpx, anyio, requests |
| httpcore | 1.0.8 | BSD-3-Clause | httpx |
| h2 | 4.4.0 | MIT | the `httpx[http2]` extra |
| hpack | 4.2.0 | MIT | h2 |
| hyperframe | 6.1.0 | MIT | h2 |
| click | 8.4.2 | BSD-3-Clause | uvicorn |
| cffi | 2.1.0 | MIT-0 | cryptography |
| pycparser | 3.0 | BSD-3-Clause | cffi |
| python-dotenv | 1.2.2 | BSD-3-Clause | pydantic-settings |
| tzlocal | 5.4.4 | MIT | APScheduler |
| audioread | 3.1.0 | MIT | pyacoustid |
| requests | 2.34.2 | Apache-2.0 | pyacoustid |
| charset-normalizer | 3.4.9 | MIT | requests |
| urllib3 | 2.7.0 | MIT | requests |

## Resolved but not shipped

These two appear when dependencies are resolved on a Windows host, because of
environment markers. The payload is built for `manylinux_2_17_x86_64`, so
neither is present in anything Cadenza distributes. They are listed only so the
difference between a Windows `pip list` and this inventory is not mistaken for
an omission.

| Package | Version | Licence | Why it is absent |
| --- | --- | --- | --- |
| colorama | 0.4.6 | BSD-3-Clause (inferred) | click requires it under `platform_system == "Windows"` |
| tzdata | 2026.3 | Apache-2.0 | tzlocal requires it under `platform_system == "Windows"` |

## Why linkage matters

Copyleft obligations turn on whether the licensed code and Cadenza's code form a
single work. The practical test used throughout this document is the process
boundary.

**Importing a module creates a combined work.** When `backend/app` executes
`import mutagen`, the interpreter loads mutagen's bytecode into the same process
and the same address space as Cadenza's own modules. They share a namespace, pass
objects to each other directly, and cannot be separated at runtime — Cadenza does
not function without it. Under the GPL's own reading, the result is a derivative
or combined work, and the GPL's terms extend to the whole of what is distributed.
That is what makes the mutagen question below a genuine question.

**Launching a program in a subprocess does not.** `ffmpeg`, `ffprobe` and
`fpcalc` are executed via `subprocess.run()`. Each runs as a separate operating
system process with its own memory; the only thing crossing the boundary is a
command line in and bytes out. This is arm's-length communication at a defined
interface, not linkage. Under the FSF's own reading of what constitutes a single
program, invoking a separate executable this way keeps the two works separate,
and the copyleft of the invoked program does not reach Cadenza's source.

What *does* survive the process boundary is the obligation attached to
**distributing** those binaries. Cadenza ships them inside the package, so the
LGPL's redistribution terms apply to the binaries themselves regardless of how
they are invoked. That is the subject of the next section.

Two further notes:

- Shipping a component without importing it (Unidecode) still counts as
  distribution. The obligation follows from putting the bytes in the package, not
  from calling into them.
- The `subprocess` reasoning applies to the executables. It says nothing about
  the wheels, all of which run in-process.

## The LGPL source-delivery obligation

LGPL-3.0 (ffmpeg, ffprobe) and LGPL-2.1 (fpcalc) both permit distributing the
binaries inside a larger, differently licensed work. In exchange, distribution
must be accompanied by:

1. a copy of the applicable licence text — `COPYING.LGPLv3` together with the
   GPLv3 text it incorporates for the ffmpeg binaries, and the LGPL 2.1 text for
   fpcalc;
2. the corresponding source for the LGPL-covered work, or a written offer to
   supply it;
3. for LGPLv3 specifically, the means to relink or replace the library (§4), and
   Installation Information where the product is a User Product (§4(e), via
   GPLv3 §6) — directly relevant here, since Cadenza is installed on a consumer
   NAS.

### Current status: not met

`packaging/synology/build-native-payload.sh:29-31` states that "the release job
ships the corresponding source archive next to the package." That job does not
exist. There is exactly one workflow, `.github/workflows/ci.yml`, and it contains
no step that fetches or publishes any ffmpeg or chromaprint source archive.

The licence texts are not shipped either. `build-native-payload.sh` untars the
ffmpeg archive with `--strip-components=1`, then copies only `bin/ffmpeg`,
`bin/ffprobe` and `lib/.` into the stage. The archive's root `LICENSE.txt` — the
LGPLv3 text — stays in the build cache. Meanwhile `build-spk.sh:56` copies only
the project's own `LICENSE` into `doc/`, and that file is the MIT licence.

The released `.spk` therefore contains LGPLv3 and LGPL-2.1 binaries accompanied
solely by an MIT licence. On its face that is a notice defect.

### What closing it requires

- Copy the ffmpeg archive's `LICENSE.txt` into the stage (for example
  `doc/ffmpeg-LICENSE.txt`) and add chromaprint's `LICENSE.md` alongside it.
- Add a release step that publishes the corresponding source archives for both
  upstreams, pinned to the exact versions shipped, or a written offer valid for
  the period the licence requires.
- Correct the comment at `build-native-payload.sh:27-31`, which reasons only
  about avoiding x264/x265, and any NOTICE text that says LGPL 2.1, to reference
  LGPL v3 for ffmpeg/ffprobe and LGPL-2.1-only for fpcalc.

Until those land, this section documents an obligation the project has
identified but does not yet satisfy. It should not be read as a compliance
statement.

## The open question: mutagen

**The repository is MIT. mutagen is GPL-2.0-or-later. Cadenza imports it into
the same interpreter.** Those three facts are each verified above and they are
in tension. This document does not claim the tension is resolved.

The argument that it is a problem: importing mutagen puts GPL code in Cadenza's
process, forming a combined work. The GPL requires that a combined work be
distributed under GPL terms. An MIT `LICENSE` file at the root of a package that
ships and imports GPL code does not, on that reading, describe what is actually
being distributed — the recipient's rights in the combination are governed by
the GPL, whatever the root file says.

Counter-arguments exist — over what counts as a derivative work for a
dynamically imported interpreted module, and over whether the choice is Cadenza's
to make as the copyright holder of its own code. They are not settled here, and
none of them has been tested against this particular arrangement.

The realistic options, none of which has been chosen:

| Option | Effect |
| --- | --- |
| Relicense Cadenza as GPL-2.0-or-later | Removes the conflict; changes the terms for every downstream user. |
| Replace mutagen with a permissively licensed tag library | Keeps MIT; requires reworking the tag read/write paths. |
| Move tag handling behind a process boundary | Applies the same reasoning used for ffmpeg; substantial architectural change. |
| Document the combination and accept GPL terms for the distributed whole | Keeps the code; means the released package is effectively GPL, and the root `LICENSE` must say so. |

Tracked at <https://github.com/AbdelmonemAwad/cadenza/issues/23>. Until that
issue is resolved, treat the licence of the *distributed package* as unsettled,
distinct from the licence of the *source repository*.

The Unidecode finding above is bundled into the same issue, but it is a
different kind of problem: it needs a one-line deletion, not a decision.

## How this inventory was produced

Every wheel entry was read from that wheel's own `dist-info/METADATA` in the
resolved payload, not from a summary tool. Python packaging is mid-migration
from the legacy free-text `License:` field to PEP 639 `License-Expression:`, so
the source of each identifier varies and is worth recording:

- **PEP 639 `License-Expression`** (an actual SPDX identifier, most reliable):
  pydantic, pyacoustid, python-multipart, annotated-types, typing-extensions,
  anyio, idna, httpcore, h2, hpack, click, cffi, tzlocal, audioread, urllib3.
- **Legacy `License:` field carrying a valid SPDX string**: mutagen, certifi,
  httpx, PyJWT, APScheduler, pillow, uvicorn, websockets, h11, greenlet,
  cryptography, starlette, pydantic-core, python-dotenv, requests,
  charset-normalizer, pydantic-settings, sqlalchemy, tzdata.
- **Legacy `License:` field with free text, resolved by classifier**: tenacity
  (`Apache 2.0`), Unidecode (`GPL`, versioned by the GPLv2+ classifier),
  hyperframe (the full MIT text inline).
- **Classifier only, no licence field at all**: fastapi, rapidfuzz, aiosqlite —
  each carries `Classifier: License :: OSI Approved :: MIT License` and nothing
  more.
- **Inferred, not published as SPDX**: colorama carries only the
  non-version-specific `Classifier: License :: OSI Approved :: BSD License`; the
  BSD-3-Clause identifier is an inference. It is not shipped, so nothing turns
  on it.

The three native binaries were identified from their upstream build definitions
and licence files rather than from any packaged metadata, since they carry none:
BtbN's `variants/` scripts for ffmpeg and ffprobe, and chromaprint's `LICENSE.md`
at tag v1.5.1 for fpcalc.

The `used as` classification was established by searching `backend/app` for
import sites and by tracing `Requires-Dist` chains from the direct pins in
`backend/requirements.txt`. "Direct pin" and "imported" are separate axes: a
package can be pinned directly and never imported (Unidecode, uvicorn,
websockets), and the bucketing above keeps them apart.

Regenerate this inventory whenever `backend/requirements.txt`, `FFMPEG_URL` or
`FPCALC_URL` changes.
