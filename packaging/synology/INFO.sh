#!/bin/bash
# Emits the DSM 7 package INFO file. Called by build-spk.sh.
set -eu

PKG_NAME="Cadenza"
PKG_VERS="${PKG_VERS:-1.0.0-0001}"
PKG_ARCH="${PKG_ARCH:-x86_64}"

# DSM 7.2 is the baseline actually targeted. Container Manager (package id
# "ContainerManager") only exists from 7.2; on 7.0 and 7.1 the equivalent
# package is called "Docker", so a dependency written for one cannot be
# satisfied on the other. Declaring 7.2 keeps os_min_ver and the dependency
# consistent instead of advertising untested 7.0 support. See issue #3.
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
