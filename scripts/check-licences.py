#!/usr/bin/env python3
"""Fail the build when a new copyleft dependency appears.

The repository is MIT. It nevertheless distributes one GPL component --
mutagen, imported in-process -- which is recorded below and stated openly in
LICENSE and docs/THIRD-PARTY.md. That is a deliberate, documented position.

What must not happen is a second one arriving unnoticed. Unidecode did exactly
that: GPL-2.0-or-later, pinned directly, imported nowhere, and shipped in both
the container image and the Synology package for months before anyone looked.
It cost the project a copyleft obligation in exchange for no functionality.

So the licence of every distributed dependency is read from the wheel's own
metadata, not inferred from its name, and anything copyleft that is not on the
list below fails the build.

    python scripts/check-licences.py [--requirements backend/requirements.txt]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

# Copyleft dependencies that are known, deliberate and documented. A package
# here is a decision someone made and wrote down; anything else is a surprise.
ACCEPTED: dict[str, str] = {
    # Imported in-process by app/core/tags.py. There is no equivalent library
    # with the same format coverage, so this is the one genuine trade. It makes
    # distributed builds a combined work; LICENSE says so.
    "mutagen": "GPL-2.0-or-later",
    # Weak copyleft, and only a certificate bundle -- no code is combined.
    "certifi": "MPL-2.0",
}

_COPYLEFT = re.compile(
    r"\b(?:A?GPL|LGPL|MPL|EUPL|CDDL|EPL|OSL|SSPL)\b"
    r"|GNU (?:Lesser |Affero )?General Public License"
    r"|Mozilla Public License|Eclipse Public License",
    re.IGNORECASE,
)
# "MIT-0", "BSD-3-Clause" and similar must never trip the pattern above, and
# nor should the word "public" in "public domain".
_PERMISSIVE_ONLY = re.compile(
    r"^\s*(MIT|BSD[\w\- ]*|Apache[\w\.\- ]*|ISC|PSF[\w\- ]*|Unlicense|"
    r"CC0[\w\.\- ]*|Python-2\.0|Zlib|public domain)\s*$",
    re.IGNORECASE,
)


def resolve(requirements: Path, dest: Path) -> list[Path]:
    """Download the exact wheels that get distributed."""
    subprocess.run(
        [sys.executable, "-m", "pip", "download",
         "--only-binary=:all:",
         "--platform", "manylinux_2_17_x86_64",
         "--python-version", "3.12",
         "--implementation", "cp",
         "-r", str(requirements), "-d", str(dest)],
        check=True, capture_output=True,
    )
    return sorted(dest.glob("*.whl"))


def licence_of(wheel: Path) -> tuple[str, str]:
    """Return (licence, where it was read from) for a wheel.

    Read from the distribution itself. Guessing from the project name is how
    a GPL package passes for permissive.
    """
    with zipfile.ZipFile(wheel) as z:
        meta = next((n for n in z.namelist()
                     if n.endswith(".dist-info/METADATA")), None)
        if meta is None:
            return "UNKNOWN", "no METADATA in the wheel"
        text = z.read(meta).decode("utf-8", "replace")

    # Newest and most precise first.
    if m := re.search(r"^License-Expression:\s*(.+)$", text, re.M):
        return m.group(1).strip(), "License-Expression"
    classifiers = re.findall(r"^Classifier: License :: (.+)$", text, re.M)
    if classifiers:
        return "; ".join(c.strip() for c in classifiers), "Classifier"
    if m := re.search(r"^License:\s*(.+)$", text, re.M):
        value = m.group(1).strip()
        if value and value.upper() != "UNKNOWN":
            return value, "License"
    return "UNKNOWN", "not declared"


def is_copyleft(licence: str) -> bool:
    """A copyleft term anywhere in the string wins.

    Deliberately has no permissive escape hatch: a dual licence such as
    "Apache-2.0 OR GPL-3.0" still carries GPL terms for anyone who takes the
    GPL half, and this project ships the dependency to users either way.
    """
    return bool(_COPYLEFT.search(licence))


def _family(licence: str) -> set[str]:
    """The copyleft families named in a licence string, with their versions.

    Metadata spells the same licence several ways -- "GPL-2.0-or-later" and
    "OSI Approved :: GNU General Public License v2 or later (GPLv2+)" -- so the
    comparison is on what family and version are named, not on the text. An
    earlier attempt normalised the whole string by substitution and could not
    match those two at all, which made the guard reject the very packages it
    had been told to accept.
    """
    text = licence.lower()
    text = text.replace("gnu ", "")
    text = text.replace("affero general public license", "agpl")
    text = text.replace("lesser general public license", "lgpl")
    text = text.replace("general public license", "gpl")
    text = text.replace("mozilla public license", "mpl")
    text = text.replace("eclipse public license", "epl")

    families: set[str] = set()
    for name in ("agpl", "lgpl", "gpl", "mpl", "epl", "eupl", "cddl", "osl", "sspl"):
        for m in re.finditer(rf"\b{name}\b[^a-z0-9]*v?(\d+)?", text):
            families.add(f"{name}{m.group(1) or ''}")
    return families


def _same_licence(accepted: str, found: str) -> bool:
    """Is the licence still the one that was accepted?

    True when they name the same copyleft families. A package that relicenses
    -- mutagen going AGPL, say -- names a different family and fails, which is
    the point: the acceptance was a decision about specific terms.
    """
    a, f = _family(accepted), _family(found)
    if not a or not f:
        return False
    # A version recorded as "gpl2" must still match a string that only says
    # "gpl", and vice versa; a different NUMBER is what must not match.
    def base(x: str) -> str:
        return x.rstrip("0123456789")
    return {base(x) for x in a} == {base(x) for x in f} and not (
        {x for x in a if x != base(x)} and {x for x in f if x != base(x)}
        and {x for x in a if x != base(x)} != {x for x in f if x != base(x)})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--requirements", default="backend/requirements.txt", type=Path)
    ap.add_argument("--json", action="store_true", help="emit the full inventory")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        wheels = resolve(args.requirements, Path(tmp))
        if not wheels:
            print("no wheels resolved -- the requirements file may be empty", file=sys.stderr)
            return 1

        inventory, surprises, unknown = [], [], []
        for wheel in wheels:
            name = wheel.name.split("-")[0]
            version = wheel.name.split("-")[1]
            licence, source = licence_of(wheel)
            entry = {"name": name, "version": version,
                     "licence": licence, "read_from": source}
            inventory.append(entry)

            key = name.lower().replace("_", "-")
            if licence == "UNKNOWN":
                unknown.append(entry)
            elif is_copyleft(licence):
                # Matched on the name AND the licence. Accepting by name alone
                # meant a package could relicense -- mutagen going AGPL, say --
                # and keep sailing through on a decision made about GPL-2.0.
                expected = ACCEPTED.get(key)
                if expected is None or not _same_licence(expected, licence):
                    entry["expected"] = expected or "(not accepted)"
                    surprises.append(entry)

    if args.json:
        print(json.dumps(inventory, indent=2))

    print(f"checked {len(inventory)} distributed packages")
    for entry in inventory:
        if is_copyleft(entry["licence"]):
            state = "accepted" if entry["name"].lower().replace("_", "-") in ACCEPTED else "NEW"
            print(f"  copyleft [{state}]: {entry['name']} {entry['version']} "
                  f"-- {entry['licence']} (from {entry['read_from']})")

    if unknown:
        print("\nlicence could not be determined:", file=sys.stderr)
        for entry in unknown:
            print(f"  {entry['name']} {entry['version']}", file=sys.stderr)
        print("Add it to ACCEPTED with its real licence, or check the wheel by "
              "hand -- an undeclared licence is not the same as a permissive "
              "one.", file=sys.stderr)
        return 1

    if surprises:
        print("\nNEW copyleft dependency, not previously accepted:", file=sys.stderr)
        for entry in surprises:
            print(f"  {entry['name']} {entry['version']} -- {entry['licence']} "
                  f"(from {entry['read_from']})", file=sys.stderr)
        print("\nThis project is MIT and ships these to users. Either remove the "
              "dependency, or accept it deliberately: add it to ACCEPTED here, "
              "record it in docs/THIRD-PARTY.md, and say so in LICENSE.",
              file=sys.stderr)
        return 1

    print("no unaccepted copyleft dependencies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
