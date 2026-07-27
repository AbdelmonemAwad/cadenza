#!/usr/bin/env bash
# Reject control characters in the scripts DSM and CI execute, and check syntax.
#
# Why this exists: a shell script was generated through a layer that ate one
# level of escaping, and a `\1` sed backreference became a literal 0x01 byte.
# The pattern then matched nothing, the extracted value came out empty, and the
# build asked GitHub for ".tar.gz". Nothing in review would show it -- the byte
# is invisible in a diff and in most editors -- and it happened twice in the
# same day, the second time in a file whose own comment described the first.
#
# Control characters in a shell script are never intentional. They are cheap to
# detect and expensive to find by hand, so they are detected here.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

status=0

# DSM control scripts have no extension, so they are named explicitly rather
# than matched by suffix.
mapfile -t FILES < <(find packaging scripts docker -type f \
    \( -name '*.sh' \
       -o -name 'postinst' -o -name 'postuninst' -o -name 'preinst' \
       -o -name 'preuninst' -o -name 'preupgrade' -o -name 'postupgrade' \
       -o -name 'start-stop-status' -o -name 'entrypoint.sh' \) 2>/dev/null | sort)

if [ "${#FILES[@]}" -eq 0 ]; then
    echo "no shell scripts found -- the search paths are probably wrong" >&2
    exit 1
fi

echo "checking ${#FILES[@]} shell scripts"

for f in "${FILES[@]}"; do
    # Tab, newline and carriage return are the only control characters that
    # belong in a text file.
    if LC_ALL=C grep -qP '[\x00-\x08\x0b\x0c\x0e-\x1f]' "$f"; then
        echo "::error file=${f}::control character in a shell script"
        echo "  ${f}:"
        LC_ALL=C grep -nP '[\x00-\x08\x0b\x0c\x0e-\x1f]' "$f" | head -3 | cat -v | sed 's/^/    /'
        status=1
        continue
    fi

    # CRLF makes DSM's shell fail a script with an opaque error, so it is
    # treated the same way.
    if LC_ALL=C grep -q $'\r' "$f"; then
        echo "::error file=${f}::CRLF line endings"
        status=1
        continue
    fi

    # dash is the closest POSIX shell to DSM's BusyBox ash, so scripts that
    # declare /bin/sh are checked with it rather than with bash.
    shell=bash
    head -1 "$f" | grep -q 'bin/sh' && shell=sh
    if command -v dash >/dev/null 2>&1 && [ "$shell" = "sh" ]; then
        shell=dash
    fi
    if ! "${shell}" -n "$f" 2>/tmp/shcheck.err; then
        echo "::error file=${f}::syntax error (${shell} -n)"
        sed 's/^/    /' /tmp/shcheck.err
        status=1
        continue
    fi
    printf '  ok  %-52s (%s)\n' "$f" "$shell"
done

if [ "$status" -eq 0 ]; then
    echo "all shell scripts are clean"
fi
exit "$status"
