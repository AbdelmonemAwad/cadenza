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

# Every change that ships must carry a new version. DSM silently declines a
# package whose build number is not newer than the installed one, and the
# Releases page is where users are told to download from -- so a forgotten bump
# means people keep receiving the previous build while the code says otherwise.
# That is exactly what happened here: VERSION reached 2.1.0 while the newest
# published release was still v1.0.0, the stub package that starts nothing.
#
# Documentation-only changes are exempt, because they ship nothing.
#
# Every branch below either passes for a stated reason or fails loudly. The
# first version of this silently did nothing when `origin/<base>` was missing --
# which, under the default shallow checkout, is always. The gate looked present
# in the workflow and had never once run.
if [ -n "${CHECK_BUMPED:-}" ]; then
    base="${GITHUB_BASE_REF:-main}"

    if ! git rev-parse --verify -q "origin/${base}" >/dev/null 2>&1; then
        echo "::error::cannot compare against origin/${base}."
        echo "  A shallow checkout has no base ref and no merge base, so this"
        echo "  check cannot run. The job needs actions/checkout with"
        echo "  fetch-depth: 0."
        exit 1
    fi

    previous="$(git show "origin/${base}:VERSION" 2>/dev/null | tr -d ' \t\r\n')"
    if [ -z "${previous}" ]; then
        echo "::error::could not read VERSION from origin/${base}"
        exit 1
    fi

    if ! all_changed="$(git diff --name-only "origin/${base}...HEAD" 2>&1)"; then
        echo "::error::git diff origin/${base}...HEAD failed: ${all_changed}"
        echo "  (a shallow clone has no merge base; use fetch-depth: 0)"
        exit 1
    fi
    changed="$(printf '%s\n' "${all_changed}" | grep -vE '^docs/|\.md$' || true)"

    if [ -z "${changed}" ]; then
        echo "  no shipped files changed; no version bump required"
    elif [ "${previous}" = "${VERSION}" ]; then
        echo "::error file=VERSION::still ${VERSION}, but shipped files changed:"
        printf '%s\n' "${changed}" | head -5 | sed 's/^/    /'
        echo "  Bump VERSION (and APP_VERSION), or DSM declines the upgrade"
        echo "  and the Releases page keeps serving the previous build."
        status=1
    else
        # Different is not the same as newer. DSM compares the build number and
        # declines anything that is not strictly greater, so a downgrade -- or a
        # careless edit -- would produce a package that installs nowhere while
        # the check reported a successful bump.
        # sort -V, not a hand-rolled field sort. The first attempt converted the
        # dash to a dot, sorted on four numeric keys and converted back with
        # `s/\./-/4` -- but the string has only three dots, so nothing was
        # converted back and every comparison failed, rejecting valid bumps as
        # well as downgrades. Version sort also gets 2.10.0 > 2.1.0 right, which
        # a lexical comparison does not.
        newest="$(printf '%s\n%s\n' "${previous}" "${VERSION}" | sort -V | tail -1)"
        if [ "${newest}" != "${VERSION}" ]; then
            echo "::error file=VERSION::${VERSION} is not newer than ${previous} on ${base}"
            echo "  DSM refuses a package whose build number did not increase."
            status=1
        else
            echo "  bumped: ${previous} -> ${VERSION}  ok"
        fi
    fi
fi

[ "${status}" -eq 0 ] && echo "version metadata is consistent"
exit "${status}"
