#!/usr/bin/env bash
# =============================================================================
#  Builds a DSM 7 compatible .spk package (x86_64, Docker-wrapped).
#
#  Usage:
#     ./packaging/synology/build-spk.sh
#
#  Requirements: bash, tar, coreutils. Docker is no longer needed: the package
#  does not embed a container image (see issue #2). Runs on Linux, WSL2 or
#  macOS, or directly on the NAS over SSH.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PKG_NAME="Cadenza"
PKG_VERS="${PKG_VERS:-1.0.0-0001}"
PKG_ARCH="${PKG_ARCH:-x86_64}"

BUILD_DIR="${ROOT_DIR}/build"
STAGE="${BUILD_DIR}/stage"      # becomes package.tgz -> /var/packages/<pkg>/target
SPK_DIR="${BUILD_DIR}/spk"      # becomes the .spk archive itself
DIST_DIR="${ROOT_DIR}/dist"
OUT="${DIST_DIR}/${PKG_NAME}-${PKG_ARCH}-${PKG_VERS}.spk"

info() { printf '\033[1;34m[build]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

# The package no longer embeds the container image, so building one is not part
# of packaging any more. SKIP_IMAGE is accepted and ignored so existing
# invocations keep working.
: "${SKIP_IMAGE:=1}"

# ---- 2) Assemble the package payload -------------------------------------
info "assembling package payload ..."
rm -rf "${BUILD_DIR}"
mkdir -p "${STAGE}"/{docker,bin,doc,ui,conf} "${SPK_DIR}" "${DIST_DIR}"

# INFO declares dsmuidir="ui", which DSM resolves against target/ -- that is,
# against the contents of package.tgz. This directory was only ever copied into
# the OUTER archive, so /var/packages/Cadenza/target/ui never existed and the
# desktop icon could not be registered. The .spk verification in CI looked for
# ui/config in the outer archive and passed, which is why nothing caught it.
cp -r "${SCRIPT_DIR}/ui/." "${STAGE}/ui/"

# conf/resource points port-config at a protocol-file resolved against target/
# as well, so the service definition has to travel in the payload for the same
# reason.
cp "${SCRIPT_DIR}/conf/cadenza.sc" "${STAGE}/conf/"

# The Container Manager project file, so it is available on the NAS.
cp "${ROOT_DIR}/docker-compose.yml" "${STAGE}/docker/"
cp "${ROOT_DIR}/README.md"    "${STAGE}/doc/" 2>/dev/null || true
cp "${ROOT_DIR}/README.ar.md" "${STAGE}/doc/" 2>/dev/null || true
cp "${ROOT_DIR}/LICENSE"      "${STAGE}/doc/" 2>/dev/null || true

# The container image is deliberately NOT bundled. This package cannot reach
# the Docker daemon on DSM 7 (see issue #2), so shipping a ~1.2 GB image inside
# it would add a 244 MB download that nothing can use. The image is published
# to ghcr.io instead and Container Manager pulls it directly.

# A small CLI, linked into /usr/local/bin by usr-local-linker.
cat > "${STAGE}/bin/cadenza" <<'CLI'
#!/bin/sh
# Minimal CLI wrapper around the Cadenza HTTP API.
PORT="$(grep -E '^CADENZA_HTTP_PORT=' /var/packages/Cadenza/etc/cadenza.env 2>/dev/null | cut -d= -f2)"
: "${PORT:=8760}"
BASE="http://127.0.0.1:${PORT}/api/v1"
case "${1:-help}" in
    scan)     curl -sS -X POST "${BASE}/jobs" -H 'Content-Type: application/json' \
                  -d '{"kind":"scan","params":{}}' ;;
    dedup)    curl -sS -X POST "${BASE}/duplicates/analyze" \
                  -H 'Content-Type: application/json' -d '{}' ;;
    report)   curl -sS "${BASE}/duplicates/report?limit=50" ;;
    convert)  curl -sS "${BASE}/convert/candidates?limit=50" ;;
    stats)    curl -sS "${BASE}/dashboard/stats" ;;
    jobs)     curl -sS "${BASE}/jobs?limit=10" ;;
    health)   curl -sS "${BASE}/settings/health" ;;
    *) echo "usage: cadenza {scan|dedup|report|convert|stats|jobs|health}" ;;
esac
echo
CLI
chmod +x "${STAGE}/bin/cadenza"

# ---- 3) package.tgz -------------------------------------------------------
info "creating package.tgz ..."
tar -C "${STAGE}" -czf "${SPK_DIR}/package.tgz" .

# ---- 4) INFO and metadata -------------------------------------------------
info "generating INFO ..."
PKG_VERS="${PKG_VERS}" PKG_ARCH="${PKG_ARCH}" bash "${SCRIPT_DIR}/INFO.sh" > "${SPK_DIR}/INFO"

# DSM verifies these two fields, so they must reflect the real payload.
CHECKSUM="$(md5sum "${SPK_DIR}/package.tgz" | cut -d' ' -f1)"
EXTRACTSIZE="$(du -sk "${STAGE}" | cut -f1)"
{
    echo "checksum=\"${CHECKSUM}\""
    echo "extractsize=\"${EXTRACTSIZE}\""
} >> "${SPK_DIR}/INFO"

# ---- 5) Scripts and configuration ----------------------------------------
cp -r "${SCRIPT_DIR}/scripts"        "${SPK_DIR}/"
cp -r "${SCRIPT_DIR}/conf"           "${SPK_DIR}/"
cp -r "${SCRIPT_DIR}/ui"             "${SPK_DIR}/"
cp -r "${SCRIPT_DIR}/WIZARD_UIFILES" "${SPK_DIR}/" 2>/dev/null || true
chmod 755 "${SPK_DIR}/scripts/"*

# CRLF line endings make DSM's shell fail these scripts silently, so normalise.
find "${SPK_DIR}/scripts" -type f -exec sed -i 's/\r$//' {} +

# ---- 6) Icons -------------------------------------------------------------
for pair in "PACKAGE_ICON.PNG:72" "PACKAGE_ICON_256.PNG:256"; do
    name="${pair%%:*}"; size="${pair##*:}"
    src="${SCRIPT_DIR}/${name}"
    if [[ -f "${src}" ]]; then
        cp "${src}" "${SPK_DIR}/${name}"
    else
        info "generating placeholder icon ${name} (${size}px)"
        python3 - "$size" "${SPK_DIR}/${name}" <<'PY' || die "install Pillow, or place icons in packaging/synology/"
import sys
from PIL import Image, ImageDraw
size, out = int(sys.argv[1]), sys.argv[2]
img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([0, 0, size - 1, size - 1], radius=size // 5, fill=(26, 115, 232, 255))
r = size // 7
cx, cy = size * 0.36, size * 0.70
d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 255))
d.rectangle([cx + r - size * 0.045, size * 0.22, cx + r, cy], fill=(255, 255, 255, 255))
d.polygon([(cx + r - size * 0.045, size * 0.22), (size * 0.78, size * 0.14),
           (size * 0.78, size * 0.26), (cx + r - size * 0.045, size * 0.34)],
          fill=(255, 255, 255, 255))
img.save(out, "PNG")
PY
    fi
done

mkdir -p "${SPK_DIR}/ui/images"
for s in 16 24 32 48 72 256; do
    cp "${SPK_DIR}/PACKAGE_ICON_256.PNG" "${SPK_DIR}/ui/images/cadenza_${s}.png" 2>/dev/null || true
done

# ---- 7) Wrap into the .spk (uncompressed tar, as DSM requires) ------------
info "packaging ${OUT##*/} ..."
rm -f "${OUT}"
tar -C "${SPK_DIR}" -cf "${OUT}" \
    INFO package.tgz scripts conf ui \
    PACKAGE_ICON.PNG PACKAGE_ICON_256.PNG \
    $( [[ -d "${SPK_DIR}/WIZARD_UIFILES" ]] && echo WIZARD_UIFILES )

info "done:"
ls -lh "${OUT}"
echo
echo "  Install: DSM -> Package Center -> Manual Install -> select the file above"
echo "  Requires: Container Manager installed and running on the NAS"
