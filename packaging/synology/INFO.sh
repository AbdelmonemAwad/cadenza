#!/bin/bash
# Emits the DSM 7 package INFO file. Called by build-spk.sh.
set -eu

PKG_NAME="Cadenza"
PKG_VERS="${PKG_VERS:-$(tr -d ' \t\r\n' < "$(dirname "${BASH_SOURCE[0]}")/../../VERSION")}"
PKG_ARCH="${PKG_ARCH:-x86_64}"

# DSM 7.2 is the baseline actually tested, on a DS1821+ running 7.2.1-69057.
PKG_OS_MIN="${PKG_OS_MIN:-7.2-64561}"

cat <<EOF
package="${PKG_NAME}"
version="${PKG_VERS}"
os_min_ver="${PKG_OS_MIN}"
arch="${PKG_ARCH}"
maintainer="Cadenza Project"
maintainer_url="https://github.com/AbdelmonemAwad/cadenza"
distributor="Cadenza Project"
description="Curate, deduplicate and tag your music library on Synology NAS. Multi-source metadata (MusicBrainz, Apple Music, Discogs, Last.fm), acoustic fingerprint deduplication, safe quarantine instead of deletion, and FFmpeg format conversion."
description_ara="تنظيم مكتبة الموسيقى وتنظيفها على سيرفرات Synology: بيانات من مصادر متعددة، كشف تكرار بالبصمة الصوتية، حذف آمن عبر العزل بدل المسح، وتحويل الصيغ عبر FFmpeg."
displayname="Cadenza"
displayname_ara="كادينزا"
dsmuidir="ui"
dsmappname="SYNO.SDS._ThirdParty.App.Cadenza"
startable="yes"
ctl_stop="yes"
silent_install="no"
silent_upgrade="no"
silent_uninstall="no"
# NO dependencies. This package is standalone: it carries its own CPython, its
# own Python libraries and its own ffmpeg/ffprobe/fpcalc, and it runs as an
# unprivileged package user. It does not need Docker, Container Manager,
# Python from Package Center, or anything else installed on the NAS.
#
# This line used to read install_dep_packages="ContainerManager>=20.10", left
# over from when the package was a wrapper that asked Container Manager to run
# the image. Keeping it meant DSM refused to install Cadenza on a NAS without
# Container Manager -- so the package was not standalone at all, whatever the
# rest of it did.
install_dep_packages=""
install_conflict_packages=""
thirdparty="yes"
support_center="no"
report_url=""
install_reboot="no"
checkport="no"
precheckstartstop="yes"
adminprotocol="http"
adminport="8760"
adminurl="/"
EOF
