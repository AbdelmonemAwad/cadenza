#!/bin/bash
# Emits the DSM 7 package INFO file. Called by build-spk.sh.
set -eu

PKG_NAME="Cadenza"
PKG_VERS="${PKG_VERS:-1.0.0-0001}"
PKG_ARCH="${PKG_ARCH:-x86_64}"

cat <<EOF
package="${PKG_NAME}"
version="${PKG_VERS}"
os_min_ver="7.0-40000"
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
install_dep_packages="ContainerManager>=20.10"
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
