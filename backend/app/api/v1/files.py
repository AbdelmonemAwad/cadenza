"""Picking a folder, and uploading the credential files an integration needs.

Both halves exist because the alternative was worse. The Apple Music signing
key had to be copied onto the NAS by hand, into a folder the user had to find
for themselves -- and on the native package it could not work at all, because
`apple_private_key_path` defaulted to `/config/AuthKey.p8`, which is the
container's layout and not a path that exists there. The Settings page then
offered that path as an editable field even though the policy locks it, so
every attempt to set it answered 400 and discarded the rest of the form.

Uploading is narrow on purpose: a fixed set of known credentials, each with its
own destination, size cap and content check, written through `write_private` so
the file is never briefly world-readable on a shared folder. There is no
caller-supplied filename and no caller-supplied directory.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.config import get_settings
from app.core.browse import (
    browsable_roots,
    list_directories,
    list_files,
    resolve_within_roots,
)
from app.core.paths import PathEscape
from app.core.secretfile import write_private

log = logging.getLogger(__name__)

router = APIRouter()


def _looks_like_private_key(data: bytes) -> bool:
    head = data[:200].lstrip()
    return head.startswith(b"-----BEGIN") and b"PRIVATE KEY" in data[:400]


# name -> (filename in the data folder, size cap, content check, description,
#          the extensions the browser will list when looking for this one)
CREDENTIAL_FILES: dict[str, tuple[str, int, object, str, tuple[str, ...]]] = {
    "apple_private_key": (
        "AuthKey.p8", 64 * 1024, _looks_like_private_key,
        "Apple Music MusicKit signing key (AuthKey_XXXXXXXXXX.p8)",
        (".p8", ".pem", ".key"),
    ),
}


def _destination(kind: str) -> Path:
    settings = get_settings()
    if kind == "apple_private_key":
        # Follows the setting when a deployment pinned one, and otherwise lands
        # in the data folder this install actually uses -- which is the bug
        # that made the container default unusable on the package.
        return settings.apple_key_file
    filename = CREDENTIAL_FILES[kind][0]
    return Path(settings.config_dir) / filename


@router.get("/roots")
async def roots() -> dict:
    """Where a folder picker may start."""
    settings = get_settings()
    return {"roots": [{"path": str(p), "name": p.name or str(p)}
                      for p in browsable_roots(settings)]}


@router.get("/browse")
async def browse(path: str, credential: str | None = None) -> dict:
    """The subdirectories of `path`.

    File names are returned only when `credential` names one of the known
    credential kinds, and then only files with that credential's extension.
    Browsing never discloses a general file listing: "the .p8 files in this
    folder" is what the user is already looking for, "everything in this
    folder" is their documents.
    """
    settings = get_settings()
    suffixes: tuple[str, ...] = ()
    if credential is not None:
        if credential not in CREDENTIAL_FILES:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown credential")
        suffixes = CREDENTIAL_FILES[credential][4]

    try:
        resolved = resolve_within_roots(path, settings)
        entries = list_directories(resolved)
        files = list_files(resolved, suffixes)
    except PathEscape as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "that folder is outside the places Cadenza may browse") from exc

    # The parent is offered only while it is still inside a root, so "up" stops
    # at the top of the volume rather than walking out of it.
    parent: str | None = None
    try:
        parent = str(resolve_within_roots(resolved.parent, settings))
    except PathEscape:
        parent = None
    if parent == str(resolved):
        parent = None

    return {"path": str(resolved), "parent": parent, "entries": entries,
            "files": files, "truncated": len(entries) >= 500}


@router.get("/credentials")
async def credentials() -> dict:
    """Which credential files are in place. Never their contents."""
    out = {}
    for kind, (_, _, _, description, _) in CREDENTIAL_FILES.items():
        destination = _destination(kind)
        out[kind] = {
            "description": description,
            "present": destination.is_file(),
            "path": str(destination),
            "size": destination.stat().st_size if destination.is_file() else 0,
        }
    return out


@router.post("/credentials/{kind}", status_code=status.HTTP_201_CREATED)
async def upload_credential(kind: str, file: UploadFile) -> dict:
    if kind not in CREDENTIAL_FILES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown credential")
    _, limit, check, description, _ = CREDENTIAL_FILES[kind]

    # Read with the cap applied, rather than reading it all and measuring
    # afterwards: an upload endpoint that buffers whatever it is sent is a way
    # to fill the config volume.
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"that file is larger than {limit // 1024} KB")
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "the file is empty")
    if callable(check) and not check(data):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            # Described rather than quoted: a literal PEM header anywhere in
            # the tree is what the secret scanner is there to stop, and it
            # stopped this.
            f"that does not look like a {description}. Apple's key is a small "
            "text file that opens with the standard PEM private-key header line "
            "and ends with the matching footer")

    destination = _destination(kind)
    try:
        write_private(destination, data)
    except OSError as exc:
        log.warning("could not store %s at %s", kind, destination, exc_info=True)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"could not write to {destination.parent}") from exc

    # Never the contents, and never echoed back on any later read.
    log.info("stored credential %s (%d bytes)", kind, len(data))
    return {"stored": kind, "path": str(destination), "size": len(data)}


class ImportRequest(BaseModel):
    path: str


@router.post("/credentials/{kind}/import", status_code=status.HTTP_201_CREATED)
async def import_credential(kind: str, req: ImportRequest) -> dict:
    """Take a copy of a file the user already put on the NAS.

    Copied rather than referenced. Pointing the application at a path the user
    chose would put a filesystem path back into a setting, which is the shape
    that made `apple_private_key_path` both locked and wrong; and a referenced
    file can be moved, renamed or have its permissions changed afterwards,
    which fails later and somewhere else. A copy in the data folder is backed
    up with everything else and cannot drift.

    The source is resolved against the same roots as browsing and read with the
    same cap and content check as an upload, so no route into this file is
    laxer than another.
    """
    if kind not in CREDENTIAL_FILES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown credential")
    _, limit, check, description, _ = CREDENTIAL_FILES[kind]

    settings = get_settings()
    try:
        source = resolve_within_roots(req.path, settings)
    except PathEscape as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "that file is outside the places Cadenza may read") from exc
    if not source.is_file():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "that is not a file")

    try:
        with source.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"could not read {source.name}") from exc

    if len(data) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"that file is larger than {limit // 1024} KB")
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "the file is empty")
    if callable(check) and not check(data):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"that does not look like a {description}")

    destination = _destination(kind)
    if source.resolve() == destination.resolve():
        return {"stored": kind, "path": str(destination), "size": len(data)}
    try:
        write_private(destination, data)
    except OSError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            f"could not write to {destination.parent}") from exc

    log.info("imported credential %s from %s (%d bytes)", kind, source, len(data))
    return {"stored": kind, "path": str(destination), "size": len(data)}


@router.delete("/credentials/{kind}")
async def delete_credential(kind: str) -> dict:
    if kind not in CREDENTIAL_FILES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown credential")
    destination = _destination(kind)
    existed = destination.is_file()
    try:
        destination.unlink(missing_ok=True)
    except OSError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            "could not remove the file") from exc
    return {"removed": kind, "existed": existed}
