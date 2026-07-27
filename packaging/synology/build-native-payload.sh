#!/usr/bin/env bash
# Assemble the native payload: interpreter, dependencies, tools, app, UI.
#
# Everything here is downloaded and staged on the BUILD machine. Nothing is
# fetched, compiled or resolved on the NAS -- there is no compiler there, and a
# package that needs the network at install time fails on exactly the machines
# that most need to work offline.
#
# Run from anywhere; paths are derived from the script's own location.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STAGE="${1:?usage: build-native-payload.sh <stage-dir>}"
CACHE="${CADENZA_BUILD_CACHE:-${ROOT_DIR}/build/cache}"

# One variable drives the interpreter version. Twelve of the compiled wheels
# are cp3XX-tagged, so nothing else may hardcode a minor version.
PY_VERSION="${PY_VERSION:-3.12.8}"
PBS_RELEASE="${PBS_RELEASE:-20241219}"
# The plain x86_64 target, deliberately -- not x86_64_v2/v3. INFO declares
# arch="x86_64", which DSM treats as generic across apollolake, braswell,
# denverton, geminilake and v1000. A microarchitecture build would fault on the
# older Atom boxes for no measurable gain.
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/cpython-${PY_VERSION}+${PBS_RELEASE}-x86_64-unknown-linux-gnu-install_only.tar.gz"

# ffmpeg is the LGPL build, not a GPL one: the GPL variants pull in x264 and
# friends that Cadenza never uses, and a GPL binary would extend that licence
# over everything distributed with it.
#
# It is LGPL *v3*, not v2.1, and the difference is not pedantic. BtbN's lgpl
# variant configures --enable-version3 because it unconditionally enables the
# Apache-2.0 OpenCORE-AMR and VMAF libraries, and Apache-2.0 is incompatible
# with LGPLv2.1. The archive ships COPYING.LGPLv3 as its LICENSE.txt. So the
# text that must accompany the binaries is LGPLv3, and LGPLv3 reaches GPLv3's
# patent and installation-information terms -- which matter here, because this
# is installed on a consumer NAS appliance.
#
# An earlier version of this comment claimed "the release job ships the
# corresponding source archive". No such job existed. The source archives are
# fetched below and published with the package, so the statement is now backed
# by something.
#
# The SHARED LGPL build, not the static one. Static gives two ~106 MB binaries
# that duplicate the same libraries: 212 MB where shared costs about half, since
# ffmpeg and ffprobe then load one copy of each .so. LD_LIBRARY_PATH is already
# set by start-stop-status, so the cost of shared is one more directory on it.
FFMPEG_URL="${FFMPEG_URL:-https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n7.1-latest-linux64-lgpl-shared-7.1.tar.xz}"
FPCALC_URL="${FPCALC_URL:-https://github.com/acoustid/chromaprint/releases/download/v1.5.1/chromaprint-fpcalc-1.5.1-linux-x86_64.tar.gz}"

info() { printf '\033[1;34m[native]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

fetch() {
    local url="$1" dest="$2"
    if [ -f "${dest}" ]; then
        info "cached: $(basename "${dest}")"
        return 0
    fi
    info "downloading $(basename "${dest}") ..."
    mkdir -p "$(dirname "${dest}")"
    # -f so an HTML error page is never mistaken for an archive.
    curl -fsSL --retry 3 --retry-delay 2 -o "${dest}.part" "${url}" \
        || die "download failed: ${url}"
    mv "${dest}.part" "${dest}"
}

mkdir -p "${STAGE}" "${CACHE}"

# ---- 1) Interpreter -------------------------------------------------------
fetch "${PBS_URL}" "${CACHE}/cpython-${PY_VERSION}.tar.gz"
info "unpacking CPython ${PY_VERSION} ..."
rm -rf "${STAGE}/python"
tar -xzf "${CACHE}/cpython-${PY_VERSION}.tar.gz" -C "${STAGE}"
[ -x "${STAGE}/python/bin/python3" ] || die "interpreter missing after unpack"

PY_MM="$(echo "${PY_VERSION}" | cut -d. -f1,2)"

# Trim what a server never uses. Keeps the package to a size people will
# actually download over a home connection.
info "trimming the interpreter ..."
rm -rf "${STAGE}/python/lib/python${PY_MM}/test" \
       "${STAGE}/python/lib/python${PY_MM}/idlelib" \
       "${STAGE}/python/lib/python${PY_MM}/tkinter" \
       "${STAGE}/python/lib/python${PY_MM}/turtledemo" \
       "${STAGE}/python/share" \
       "${STAGE}/python/lib/libtcl"*.so "${STAGE}/python/lib/libtk"*.so
find "${STAGE}/python" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# ---- 2) Dependencies ------------------------------------------------------
# Wheels only, for the target platform. --only-binary=:all: is the load-bearing
# flag: without it pip would happily take an sdist that then needs a compiler
# the NAS does not have, and the failure would only appear at install time.
info "resolving dependencies (wheels only, cp${PY_MM/./}) ..."
rm -rf "${STAGE}/lib"
mkdir -p "${STAGE}/lib"
python3 -m pip download \
    --only-binary=:all: \
    --platform manylinux_2_17_x86_64 \
    --python-version "${PY_MM}" \
    --implementation cp \
    -r "${ROOT_DIR}/backend/requirements.txt" \
    -d "${CACHE}/wheels-${PY_MM}" >/dev/null || die "dependency resolution failed"

info "installing $(ls "${CACHE}/wheels-${PY_MM}" | wc -l) wheels into the payload ..."
python3 -m pip install \
    --no-index --find-links "${CACHE}/wheels-${PY_MM}" \
    --target "${STAGE}/lib" \
    --no-compile \
    -r "${ROOT_DIR}/backend/requirements.txt" >/dev/null || die "vendoring failed"
find "${STAGE}/lib" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# ---- 3) External tools ----------------------------------------------------
mkdir -p "${STAGE}/bin"
fetch "${FFMPEG_URL}" "${CACHE}/ffmpeg-lgpl.tar.xz"
info "extracting ffmpeg and ffprobe ..."
rm -rf "${CACHE}/ffmpeg-x" && mkdir -p "${CACHE}/ffmpeg-x"
tar -xJf "${CACHE}/ffmpeg-lgpl.tar.xz" -C "${CACHE}/ffmpeg-x" --strip-components=1
cp "${CACHE}/ffmpeg-x/bin/ffmpeg" "${CACHE}/ffmpeg-x/bin/ffprobe" "${STAGE}/bin/"
# ffplay is deliberately not copied: it needs a display and nothing here uses it.
mkdir -p "${STAGE}/ffmpeg-lib"
# -a to preserve the soname symlinks; copying them as regular files would
# triple the size and defeat the point of the shared build.
cp -a "${CACHE}/ffmpeg-x/lib/." "${STAGE}/ffmpeg-lib/"

fetch "${FPCALC_URL}" "${CACHE}/fpcalc.tar.gz"
info "extracting fpcalc ..."
rm -rf "${CACHE}/fpcalc-x" && mkdir -p "${CACHE}/fpcalc-x"
tar -xzf "${CACHE}/fpcalc.tar.gz" -C "${CACHE}/fpcalc-x" --strip-components=1
cp "${CACHE}/fpcalc-x/fpcalc" "${STAGE}/bin/"
chmod 755 "${STAGE}/bin/"*

# ---- 3b) Licence texts for the binaries we redistribute -------------------
# Without this the .spk shipped LGPL binaries accompanied by nothing but the
# project's own MIT licence, which is a notice defect on its face.
info "staging the licence texts for the bundled binaries ..."
mkdir -p "${STAGE}/doc/licences"
if [ -f "${CACHE}/ffmpeg-x/LICENSE.txt" ]; then
    cp "${CACHE}/ffmpeg-x/LICENSE.txt" "${STAGE}/doc/licences/ffmpeg-LICENSE.txt"
else
    die "the ffmpeg archive carried no LICENSE.txt -- refusing to ship it unlicensed"
fi
# Chromaprint states its terms in LICENSE.md; the release tarball may not carry
# it, so it is fetched from the tag the binary was built from.
fetch "https://raw.githubusercontent.com/acoustid/chromaprint/v1.5.1/LICENSE.md"       "${CACHE}/chromaprint-LICENSE.md"
cp "${CACHE}/chromaprint-LICENSE.md" "${STAGE}/doc/licences/fpcalc-LICENSE.md"

cat > "${STAGE}/doc/licences/README.txt" <<'LICNOTE'
Third-party binaries redistributed with Cadenza
===============================================

Cadenza's own code is MIT (see ../LICENSE). The programs below are separate
works, invoked as separate processes, and carry their own terms.

  bin/ffmpeg, bin/ffprobe, ffmpeg-lib/*
      FFmpeg, LGPL-3.0-or-later. Build: BtbN/FFmpeg-Builds, linux64-lgpl-shared.
      Licence text: ffmpeg-LICENSE.txt

  bin/fpcalc
      Chromaprint fpcalc 1.5.1, MIT AND LGPL-2.1-only. The LGPL applies because
      the released binary incorporates parts of FFmpeg.
      Licence text: fpcalc-LICENSE.md

Corresponding source
--------------------
The complete corresponding source for both is published alongside every release
of this package, as source archives attached to the same GitHub release:

    https://github.com/AbdelmonemAwad/cadenza/releases

If a release you have is missing them, open an issue and they will be provided.

Python dependencies bundled in lib/ have their own licences; see
docs/THIRD-PARTY.md in the project repository for the full inventory.
LICNOTE

# ---- 4) Application and UI ------------------------------------------------
info "staging the application ..."
rm -rf "${STAGE}/app"
mkdir -p "${STAGE}/app"
cp -r "${ROOT_DIR}/backend/app" "${STAGE}/app/"
find "${STAGE}/app" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

if [ -d "${ROOT_DIR}/frontend/dist" ]; then
    info "staging the frontend bundle ..."
    rm -rf "${STAGE}/www"
    cp -r "${ROOT_DIR}/frontend/dist" "${STAGE}/www"
else
    # Loud, not silent: the package would install and serve a bare API, and the
    # user would find a blank page with no explanation.
    die "frontend/dist is missing -- run 'npm run build' in frontend/ first"
fi

# ---- 5) Prove the payload runs before it is shipped -----------------------
# The whole point of vendoring is that nothing is resolved on the NAS, so the
# only honest check is importing the real application out of the staged tree
# with the staged interpreter.
info "verifying the payload imports the application ..."
PYTHONHOME="${STAGE}/python" \
PYTHONPATH="${STAGE}/app:${STAGE}/lib" \
PYTHONNOUSERSITE=1 \
CADENZA_CONFIG_DIR="$(mktemp -d)" \
CADENZA_QUARANTINE_ROOT="$(mktemp -d)" \
"${STAGE}/python/bin/python3" -c "
from app.main import create_app
app = create_app()
paths = {r.path for r in app.routes}
assert '/health' in paths, sorted(paths)[:20]
print(f'  payload OK: {len(paths)} routes, python ' + __import__('sys').version.split()[0])
" || die "the staged payload cannot import the application"

info "verifying the bundled tools run ..."
export LD_LIBRARY_PATH="${STAGE}/ffmpeg-lib:${LD_LIBRARY_PATH:-}"
"${STAGE}/bin/ffmpeg"  -hide_banner -version | head -1 | sed 's/^/  /'
"${STAGE}/bin/ffprobe" -hide_banner -version | head -1 | sed 's/^/  /'
"${STAGE}/bin/fpcalc" -version | head -1 | sed 's/^/  /'

# Every preset has to be encodable, or a conversion fails on the NAS with a
# message the user cannot act on. The LGPL build is the reason to check: it
# omits the GPL-only encoders, and this confirms the ones Cadenza actually
# names are all present.
info "verifying every preset codec is available ..."
missing=""
for enc in flac alac pcm_s16le aac libmp3lame libopus libvorbis; do
    if "${STAGE}/bin/ffmpeg" -hide_banner -encoders 2>/dev/null | grep -qE "^ [A-Z.]+ ${enc} "; then
        printf '  %-12s ok
' "${enc}"
    else
        printf '  %-12s MISSING
' "${enc}"
        missing="${missing} ${enc}"
    fi
done
[ -z "${missing}" ] || die "the ffmpeg build lacks encoders the presets require:${missing}"

# ---- 6) Corresponding source, for the LGPL obligation --------------------
# Written into the build cache and picked up by the release job. LGPL 2.1 s.4
# does not accept "go and download it from upstream" as source delivery, so the
# source has to accompany the binaries rather than be pointed at.
if [ "${FETCH_SOURCES:-1}" = "1" ]; then
    info "fetching the corresponding source for the redistributed binaries ..."

    # BtbN builds from git, not from a release tarball, so `ffmpeg -version`
    # reports a git describe string like "n7.1.5-10-g2aefd64d48-20260727".
    # Pasting that whole string into a release URL 404s; trimming it to "7.1"
    # would be worse, fetching a release tarball that is NOT the source these
    # binaries were built from -- the one thing "corresponding source" has to
    # mean. The commit is in the string, after the "-g" marker.
    ffver="$("${STAGE}/bin/ffmpeg" -hide_banner -version 2>/dev/null | head -1 | awk '{print $3}')"
    [ -n "${ffver}" ] || die "ffmpeg did not report a version"

    # Parameter expansion, not sed. The sed version used a \1 backreference,
    # and the file ended up holding a literal 0x01 byte where that
    # backreference belonged -- so the pattern matched nothing, the commit came
    # out empty, and the build asked GitHub for ".tar.gz". Shell parameter
    # expansion has no escaping layer to lose and no sed dialect to disagree
    # about.
    #
    #   n7.1.5-10-g2aefd64d48-20260727
    #   ${ffver##*-g}   ->  2aefd64d48-20260727   (after the last -g)
    #   ${...%%-*}      ->  2aefd64d48            (up to the next -)
    ffcommit=""
    case "${ffver}" in
        *-g*)
            ffcommit="${ffver##*-g}"
            ffcommit="${ffcommit%%-*}"
            ;;
    esac

    if [ -n "${ffcommit}" ]; then
        info "ffmpeg was built from commit ${ffcommit}"
        fetch "https://github.com/FFmpeg/FFmpeg/archive/${ffcommit}.tar.gz" \
              "${CACHE}/sources/ffmpeg-${ffcommit}.tar.gz"
    else
        # A plain release build: the tagged tarball IS the corresponding source.
        ffrel="${ffver#n}"
        ffrel="${ffrel%%-*}"
        [ -n "${ffrel}" ] || die "cannot determine the ffmpeg version from '${ffver}'"
        info "ffmpeg reports release ${ffrel}"
        fetch "https://ffmpeg.org/releases/ffmpeg-${ffrel}.tar.xz" \
              "${CACHE}/sources/ffmpeg-${ffrel}.tar.xz"
    fi

    fetch "https://github.com/acoustid/chromaprint/archive/refs/tags/v1.5.1.tar.gz" \
          "${CACHE}/sources/chromaprint-1.5.1.tar.gz"

    # Deliberately fatal. If the corresponding source cannot be obtained, the
    # binaries must not ship either -- an LGPL package without its source is a
    # compliance failure, not something to warn about and continue past.
    info "corresponding source staged:"
    ls -1 "${CACHE}/sources" | sed 's/^/  /'
fi

info "payload ready: $(du -sh "${STAGE}" | cut -f1)"
