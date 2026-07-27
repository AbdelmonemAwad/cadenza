#!/usr/bin/env bash
# The VERSION file is the single source, and every consumer must agree with it.
#
# DSM compares the build number after the dash and silently declines to install
# a package that is not newer than the one already present. A forgotten bump
# therefore does not fail -- it looks like "the install did nothing", which is a
# much worse way to find out.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

status=0
VERSION="$(tr -d ' \t\r\n' < VERSION)"

# <major>.<minor>.<patch>-<4-digit build>, which is the shape DSM parses.
if ! printf '%s' "${VERSION}" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+-[0-9]{4}$'; then
    echo "::error file=VERSION::'${VERSION}' is not <major>.<minor>.<patch>-<build>, e.g. 2.0.0-0002"
    exit 1
fi
echo "VERSION = ${VERSION}"

# The application reports its own version over /health and in the User-Agent
# sent to metadata providers, so a mismatch misreports which build is running.
APP_VERSION="$(grep -E '^APP_VERSION = ' backend/app/config.py | cut -d'"' -f2)"
if [ "${APP_VERSION}" != "${VERSION%-*}" ]; then
    echo "::error file=backend/app/config.py::APP_VERSION is ${APP_VERSION}, VERSION says ${VERSION%-*}"
    status=1
else
    echo "  backend APP_VERSION = ${APP_VERSION}  ok"
fi

# The generated INFO is what DSM actually reads.
INFO_VERSION="$(PKG_VERS="" bash packaging/synology/INFO.sh 2>/dev/null \
                | grep -E '^version=' | cut -d'"' -f2)"
if [ "${INFO_VERSION}" != "${VERSION}" ]; then
    echo "::error file=packaging/synology/INFO.sh::INFO says ${INFO_VERSION}, VERSION says ${VERSION}"
    status=1
else
    echo "  package INFO version = ${INFO_VERSION}  ok"
fi

# On a pull request, require the version to have moved. Landing twice on one
# build number produces a package DSM will not install over the previous one.
if [ -n "${CHECK_BUMPED:-}" ]; then
    base="${GITHUB_BASE_REF:-main}"
    if git rev-parse --verify -q "origin/${base}" >/dev/null 2>&1; then
        previous="$(git show "origin/${base}:VERSION" 2>/dev/null | tr -d ' \t\r\n')"
        if [ -n "${previous}" ] && [ "${previous}" = "${VERSION}" ]; then
            echo "::error file=VERSION::still ${VERSION}; bump it, or DSM will decline the upgrade"
            status=1
        elif [ -n "${previous}" ]; then
            echo "  bumped: ${previous} -> ${VERSION}  ok"
        fi
    fi
fi

[ "${status}" -eq 0 ] && echo "version metadata is consistent"
exit "${status}"
