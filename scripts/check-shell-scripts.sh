#!/usr/bin/env bash
# Reject control characters and broken syntax in generated files.
#
# Why this exists: files in this repository are frequently produced by a
# generation step, and every time one of those steps ate a level of escaping it
# injected a byte that is invisible in a diff and in most editors:
#
#   0x01 in build-native-payload.sh, where a sed backreference belonged -- the
#        pattern then matched nothing and the build requested ".tar.gz"
#   0x00 in .github/workflows/ci.yml, which made the file unparseable
#   0x08 twice in scripts/check-licences.py, inside a regex, so the licence
#        comparison silently matched nothing and rejected its own allowlist
#
# None of those were caught by review. They are caught here, across every kind
# of file that gets generated -- not only shell, which is where it first bit.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

status=0

# DSM control scripts have no extension, so they are named explicitly.
mapfile -t FILES < <(find packaging scripts docker .github -type f \
    \( -name '*.sh' -o -name '*.py' -o -name '*.yml' -o -name '*.yaml' \
       -o -name '*.json' \
       -o -name 'postinst' -o -name 'postuninst' -o -name 'preinst' \
       -o -name 'preuninst' -o -name 'preupgrade' -o -name 'postupgrade' \
       -o -name 'start-stop-status' -o -name 'entrypoint.sh' \) 2>/dev/null | sort)

if [ "${#FILES[@]}" -eq 0 ]; then
    echo "no files found -- the search paths are probably wrong" >&2
    exit 1
fi

echo "checking ${#FILES[@]} generated files"

for f in "${FILES[@]}"; do
    # Tab, newline and carriage return are the only control characters that
    # belong in a text file.
    #
    # -a is load-bearing. Without it grep treats a file containing a NUL byte
    # as binary and the -q form reports nothing at all, so a NUL -- the very
    # first character in the class this guard exists to catch -- passed
    # straight through undetected.
    if LC_ALL=C grep -qaP '[\x00-\x08\x0b\x0c\x0e-\x1f]' "$f"; then
        echo "::error file=${f}::control character in a generated file"
        LC_ALL=C grep -naP '[\x00-\x08\x0b\x0c\x0e-\x1f]' "$f" \
            | head -3 | cat -v | sed 's/^/    /'
        status=1
        continue
    fi

    # CRLF makes DSM's shell fail a script with an opaque error.
    #
    # Compared byte for byte rather than grepped: grep treats CR as part of the
    # line terminator on some platforms and strips it before the pattern is
    # applied, so this check silently found nothing in a file that demonstrably
    # contained CR. Removing every CR and diffing against the original has no
    # line semantics to get in the way.
    if ! LC_ALL=C tr -d '\r' < "$f" | cmp -s - "$f"; then
        echo "::error file=${f}::CRLF line endings"
        status=1
        continue
    fi

    # Syntax, by kind.
    case "$f" in
        *.py)
            if python -c 'import ast,sys; ast.parse(open(sys.argv[1],encoding="utf-8").read())' "$f" 2>/tmp/chk.err
            then printf '  ok  %-52s (python)\n' "$f"
            else echo "::error file=${f}::Python syntax error"; sed 's/^/    /' /tmp/chk.err; status=1
            fi
            continue ;;
        *.json)
            if python -c 'import json,sys; json.load(open(sys.argv[1],encoding="utf-8"))' "$f" 2>/tmp/chk.err
            then printf '  ok  %-52s (json)\n' "$f"
            else echo "::error file=${f}::invalid JSON"; sed 's/^/    /' /tmp/chk.err; status=1
            fi
            continue ;;
        *.yml|*.yaml)
            if python -c 'import yaml,sys; yaml.safe_load(open(sys.argv[1],encoding="utf-8"))' "$f" 2>/tmp/chk.err
            then printf '  ok  %-52s (yaml)\n' "$f"
            else echo "::error file=${f}::invalid YAML"; sed 's/^/    /' /tmp/chk.err; status=1
            fi
            continue ;;
    esac

    # dash is the closest POSIX shell to DSM's BusyBox ash, so scripts that
    # declare /bin/sh are checked with it rather than with bash.
    shell=bash
    head -1 "$f" | grep -q 'bin/sh' && shell=sh
    if command -v dash >/dev/null 2>&1 && [ "$shell" = "sh" ]; then
        shell=dash
    fi
    if "${shell}" -n "$f" 2>/tmp/chk.err; then
        printf '  ok  %-52s (%s)\n' "$f" "$shell"
    else
        echo "::error file=${f}::syntax error (${shell} -n)"
        sed 's/^/    /' /tmp/chk.err
        status=1
    fi
done

if [ "$status" -eq 0 ]; then
    echo "all generated files are clean"
fi
exit "$status"
