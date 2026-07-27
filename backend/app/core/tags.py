"""Unified tag reading and writing across ID3v1/v2, Vorbis, MP4/iTunes and ASF/WMA."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile
from mutagen.aac import AAC
from mutagen.apev2 import APEv2File
from mutagen.asf import ASF
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (
    APIC,
    COMM,
    ID3,
    SYLT,
    TALB,
    TCON,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TPOS,
    TRCK,
    TSRC,
    TXXX,
    USLT,
    ID3NoHeaderError,
)
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE

# Fields that count toward tag completeness, and how much each is worth.
_COMPLETENESS_WEIGHTS: dict[str, float] = {
    "title": 0.20, "artist": 0.18, "album": 0.15, "albumartist": 0.10,
    "year": 0.10, "track_no": 0.09, "genre": 0.06, "disc_no": 0.04,
    "isrc": 0.04, "mb_recording_id": 0.04,
}


@dataclass(slots=True)
class TagSet:
    title: str | None = None
    artist: str | None = None
    albumartist: str | None = None
    album: str | None = None
    year: int | None = None
    date: str | None = None
    track_no: int | None = None
    total_tracks: int | None = None
    disc_no: int | None = None
    total_discs: int | None = None
    genre: str | None = None
    composer: str | None = None
    isrc: str | None = None
    comment: str | None = None
    compilation: bool = False
    mb_recording_id: str | None = None
    mb_release_id: str | None = None
    mb_artist_id: str | None = None
    acoustid: str | None = None
    apple_id: str | None = None
    lyrics: str | None = None
    synced_lyrics: str | None = None       # LRC text
    has_artwork: bool = False
    artwork_px: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def completeness(self) -> float:
        total = 0.0
        for name, weight in _COMPLETENESS_WEIGHTS.items():
            if getattr(self, name, None) not in (None, "", 0):
                total += weight
        return round(min(total, 1.0), 4)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def merge_missing_from(self, other: TagSet) -> TagSet:
        """Fill gaps only -- an existing value is never overwritten."""
        for name in self.__slots__:  # type: ignore[attr-defined]
            if name == "extra":
                continue
            if getattr(self, name) in (None, "", 0, False) \
                    and getattr(other, name) not in (None, "", 0):
                setattr(self, name, getattr(other, name))
        return self


# --------------------------------- Reading ---------------------------------

def read_tags(path: Path) -> TagSet:
    audio = MutagenFile(str(path))
    if audio is None:
        return TagSet()

    if isinstance(audio, MP3) or (audio.tags.__class__.__name__ == "ID3" if audio.tags else False):
        return _read_id3(path, audio)
    if isinstance(audio, (FLAC, OggVorbis)):
        return _read_vorbis(audio)
    if isinstance(audio, MP4):
        return _read_mp4(audio)
    if isinstance(audio, ASF):
        return _read_asf(audio)
    if isinstance(audio, (WAVE, AAC, APEv2File)):
        return _read_generic(audio)
    return _read_generic(audio)


def _first(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return str(v[0]) if v else None
    return str(v)


def _int(v) -> int | None:
    s = _first(v)
    if not s:
        return None
    m = re.match(r"\s*(\d+)", s)
    return int(m.group(1)) if m else None


def _split_pair(v) -> tuple[int | None, int | None]:
    """Parse "3/12" or a (3, 12) tuple."""
    if v is None:
        return None, None
    if isinstance(v, (list, tuple)) and v and isinstance(v[0], (list, tuple)):
        pair = v[0]
        return (pair[0] or None, pair[1] or None) if len(pair) >= 2 else (pair[0] or None, None)
    s = _first(v) or ""
    m = re.match(r"\s*(\d+)\s*(?:/\s*(\d+))?", s)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2)) if m.group(2) else None


def _year_from(s: str | None) -> int | None:
    if not s:
        return None
    m = re.search(r"(\d{4})", s)
    if not m:
        return None
    y = int(m.group(1))
    return y if 1860 <= y <= 2200 else None


def _read_id3(path: Path, audio) -> TagSet:
    try:
        id3 = audio.tags if audio.tags is not None else ID3(str(path))
    except ID3NoHeaderError:
        return TagSet()
    if id3 is None:
        return TagSet()

    def g(frame: str) -> str | None:
        fr = id3.get(frame)
        return str(fr.text[0]) if fr and getattr(fr, "text", None) else None

    track_no, total_tracks = _split_pair(g("TRCK"))
    disc_no, total_discs = _split_pair(g("TPOS"))
    date = g("TDRC") or g("TYER") or g("TDAT")

    t = TagSet(
        title=g("TIT2"), artist=g("TPE1"), albumartist=g("TPE2"), album=g("TALB"),
        date=date, year=_year_from(date), track_no=track_no, total_tracks=total_tracks,
        disc_no=disc_no, total_discs=total_discs, genre=g("TCON"), composer=g("TCOM"),
        isrc=g("TSRC"), compilation=(g("TCMP") == "1"),
    )

    for frame in id3.getall("TXXX"):
        desc = (frame.desc or "").lower()
        value = str(frame.text[0]) if frame.text else None
        if desc in ("musicbrainz release track id", "musicbrainz track id"):
            t.mb_recording_id = t.mb_recording_id or value
        elif desc == "musicbrainz album id":
            t.mb_release_id = value
        elif desc == "musicbrainz artist id":
            t.mb_artist_id = value
        elif desc == "acoustid id":
            t.acoustid = value
        elif desc:
            t.extra[desc] = value

    for u in id3.getall("UFID"):
        if "musicbrainz" in (u.owner or "").lower():
            t.mb_recording_id = t.mb_recording_id or u.data.decode("ascii", "ignore")

    uslt = id3.getall("USLT")
    if uslt:
        t.lyrics = uslt[0].text or None
    sylt = id3.getall("SYLT")
    if sylt:
        t.synced_lyrics = _sylt_to_lrc(sylt[0])

    pictures = id3.getall("APIC")
    if pictures:
        t.has_artwork = True
        t.artwork_px = image_width(pictures[0].data)

    comments = id3.getall("COMM")
    if comments:
        t.comment = comments[0].text[0] if comments[0].text else None
    return t


def _read_vorbis(audio) -> TagSet:
    tags = {k.lower(): v for k, v in (audio.tags or {}).items()}

    def g(*keys: str) -> str | None:
        for k in keys:
            if k in tags:
                return _first(tags[k])
        return None

    date = g("date", "year", "originaldate")
    t = TagSet(
        title=g("title"), artist=g("artist"), albumartist=g("albumartist", "album artist"),
        album=g("album"), date=date, year=_year_from(date),
        track_no=_int(g("tracknumber")), total_tracks=_int(g("tracktotal", "totaltracks")),
        disc_no=_int(g("discnumber")), total_discs=_int(g("disctotal", "totaldiscs")),
        genre=g("genre"), composer=g("composer"), isrc=g("isrc"),
        comment=g("comment", "description"),
        compilation=(g("compilation") or "").strip() in ("1", "true", "yes"),
        mb_recording_id=g("musicbrainz_trackid", "musicbrainz_recordingid"),
        mb_release_id=g("musicbrainz_albumid"),
        mb_artist_id=g("musicbrainz_artistid"),
        acoustid=g("acoustid_id"),
        lyrics=g("lyrics", "unsyncedlyrics"),
        synced_lyrics=g("syncedlyrics", "lyrics_synced"),
    )
    pictures = getattr(audio, "pictures", None) or []
    if pictures:
        t.has_artwork = True
        t.artwork_px = pictures[0].width or image_width(pictures[0].data)
    elif "metadata_block_picture" in tags:
        t.has_artwork = True
    return t


_MP4_MAP = {
    "\xa9nam": "title", "\xa9ART": "artist", "aART": "albumartist", "\xa9alb": "album",
    "\xa9day": "date", "\xa9gen": "genre", "\xa9wrt": "composer", "\xa9cmt": "comment",
    "\xa9lyr": "lyrics",
}


def _read_mp4(audio) -> TagSet:
    tags = audio.tags or {}
    t = TagSet()
    for key, attr in _MP4_MAP.items():
        if key in tags:
            setattr(t, attr, _first(tags[key]))
    t.year = _year_from(t.date)
    t.track_no, t.total_tracks = _split_pair(tags.get("trkn"))
    t.disc_no, t.total_discs = _split_pair(tags.get("disk"))
    t.compilation = bool(_first(tags.get("cpil")) in ("True", "1"))

    for k, v in tags.items():
        if not k.startswith("----"):
            continue
        name = k.split(":")[-1].lower()
        try:
            value = v[0].decode("utf-8", "ignore") if isinstance(v[0], bytes) else str(v[0])
        except (IndexError, AttributeError):
            continue
        if name == "musicbrainz track id":
            t.mb_recording_id = value
        elif name == "musicbrainz album id":
            t.mb_release_id = value
        elif name == "musicbrainz artist id":
            t.mb_artist_id = value
        elif name == "acoustid id":
            t.acoustid = value
        elif name == "isrc":
            t.isrc = value
        else:
            t.extra[name] = value

    if "cnID" in tags:
        t.apple_id = _first(tags["cnID"])
    covers = tags.get("covr")
    if covers:
        t.has_artwork = True
        t.artwork_px = image_width(bytes(covers[0]))
    return t


_ASF_MAP = {
    "Title": "title", "Author": "artist", "WM/AlbumArtist": "albumartist",
    "WM/AlbumTitle": "album", "WM/Year": "date", "WM/Genre": "genre",
    "WM/Composer": "composer", "WM/ISRC": "isrc", "Description": "comment",
    "WM/Lyrics": "lyrics",
}


def _read_asf(audio) -> TagSet:
    tags = audio.tags or {}
    t = TagSet()
    for key, attr in _ASF_MAP.items():
        if key in tags:
            setattr(t, attr, str(tags[key][0]))
    t.year = _year_from(t.date)
    t.track_no = _int(str(tags["WM/TrackNumber"][0])) if "WM/TrackNumber" in tags else None
    t.disc_no = _int(str(tags["WM/PartOfSet"][0])) if "WM/PartOfSet" in tags else None
    t.has_artwork = "WM/Picture" in tags
    return t


def _read_generic(audio) -> TagSet:
    tags = audio.tags
    if not tags:
        return TagSet()
    flat = {str(k).lower(): v for k, v in dict(tags).items()}
    date = _first(flat.get("date") or flat.get("year"))
    return TagSet(
        title=_first(flat.get("title")), artist=_first(flat.get("artist")),
        album=_first(flat.get("album")), albumartist=_first(flat.get("albumartist")),
        date=date, year=_year_from(date), genre=_first(flat.get("genre")),
        track_no=_int(flat.get("tracknumber") or flat.get("track")),
    )


# --------------------------------- Writing ---------------------------------

def write_tags(path: Path, tags: TagSet, artwork: bytes | None = None,
               artwork_mime: str = "image/jpeg") -> None:
    """Write tags using the scheme appropriate to the file's container."""
    audio = MutagenFile(str(path))
    if audio is None:
        raise ValueError(f"unsupported container: {path.name}")

    if isinstance(audio, MP3) or path.suffix.lower() in (".mp3", ".aac"):
        _write_id3(path, tags, artwork, artwork_mime)
    elif isinstance(audio, (FLAC, OggVorbis)):
        _write_vorbis(audio, tags, artwork, artwork_mime)
    elif isinstance(audio, MP4):
        _write_mp4(audio, tags, artwork, artwork_mime)
    elif isinstance(audio, ASF):
        _write_asf(audio, tags)
    else:
        raise ValueError(f"writing tags is not supported for {path.suffix}")


def _write_id3(path: Path, t: TagSet, art: bytes | None, mime: str) -> None:
    try:
        id3 = ID3(str(path))
    except ID3NoHeaderError:
        id3 = ID3()

    def setf(frame_cls, value, **kw):
        if value in (None, ""):
            return
        id3.setall(frame_cls.__name__, [frame_cls(encoding=3, text=[str(value)], **kw)])

    setf(TIT2, t.title)
    setf(TPE1, t.artist)
    setf(TPE2, t.albumartist)
    setf(TALB, t.album)
    setf(TCON, t.genre)
    setf(TSRC, t.isrc)
    if t.date or t.year:
        setf(TDRC, t.date or str(t.year))
    if t.track_no:
        setf(TRCK, f"{t.track_no}/{t.total_tracks}" if t.total_tracks else str(t.track_no))
    if t.disc_no:
        setf(TPOS, f"{t.disc_no}/{t.total_discs}" if t.total_discs else str(t.disc_no))

    for desc, value in (
        ("MusicBrainz Release Track Id", t.mb_recording_id),
        ("MusicBrainz Album Id", t.mb_release_id),
        ("MusicBrainz Artist Id", t.mb_artist_id),
        ("Acoustid Id", t.acoustid),
    ):
        if value:
            id3.add(TXXX(encoding=3, desc=desc, text=[value]))

    if t.lyrics:
        id3.setall("USLT", [USLT(encoding=3, lang="und", desc="", text=t.lyrics)])
    if t.comment:
        id3.setall("COMM", [COMM(encoding=3, lang="und", desc="", text=t.comment)])
    if art:
        id3.delall("APIC")
        id3.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=art))

    # v2.3 is understood by a wider range of hardware players than v2.4.
    id3.save(str(path), v2_version=3)


def _write_vorbis(audio, t: TagSet, art: bytes | None, mime: str) -> None:
    mapping = {
        "title": t.title, "artist": t.artist, "albumartist": t.albumartist,
        "album": t.album, "date": t.date or (str(t.year) if t.year else None),
        "genre": t.genre, "composer": t.composer, "isrc": t.isrc,
        "tracknumber": str(t.track_no) if t.track_no else None,
        "tracktotal": str(t.total_tracks) if t.total_tracks else None,
        "discnumber": str(t.disc_no) if t.disc_no else None,
        "disctotal": str(t.total_discs) if t.total_discs else None,
        "musicbrainz_trackid": t.mb_recording_id,
        "musicbrainz_albumid": t.mb_release_id,
        "musicbrainz_artistid": t.mb_artist_id,
        "acoustid_id": t.acoustid,
        "lyrics": t.lyrics,
        "compilation": "1" if t.compilation else None,
    }
    for k, v in mapping.items():
        if v not in (None, ""):
            audio[k] = [v]

    if art and isinstance(audio, FLAC):
        audio.clear_pictures()
        picture = Picture()
        picture.type, picture.mime, picture.data = 3, mime, art
        picture.width = image_width(art) or 0
        picture.height = picture.width
        audio.add_picture(picture)
    audio.save()


def _write_mp4(audio, t: TagSet, art: bytes | None, mime: str) -> None:
    tags = audio.tags if audio.tags is not None else audio.add_tags()
    mapping = {
        "\xa9nam": t.title, "\xa9ART": t.artist, "aART": t.albumartist,
        "\xa9alb": t.album, "\xa9day": t.date or (str(t.year) if t.year else None),
        "\xa9gen": t.genre, "\xa9wrt": t.composer, "\xa9lyr": t.lyrics,
    }
    for k, v in mapping.items():
        if v not in (None, ""):
            tags[k] = [v]
    if t.track_no:
        tags["trkn"] = [(t.track_no, t.total_tracks or 0)]
    if t.disc_no:
        tags["disk"] = [(t.disc_no, t.total_discs or 0)]
    tags["cpil"] = t.compilation

    freeform = {
        "MusicBrainz Track Id": t.mb_recording_id,
        "MusicBrainz Album Id": t.mb_release_id,
        "MusicBrainz Artist Id": t.mb_artist_id,
        "Acoustid Id": t.acoustid,
        "ISRC": t.isrc,
    }
    for name, value in freeform.items():
        if value:
            tags[f"----:com.apple.iTunes:{name}"] = [value.encode("utf-8")]

    if art:
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        tags["covr"] = [MP4Cover(art, imageformat=fmt)]
    audio.save()


def _write_asf(audio, t: TagSet) -> None:
    mapping = {
        "Title": t.title, "Author": t.artist, "WM/AlbumArtist": t.albumartist,
        "WM/AlbumTitle": t.album, "WM/Year": t.date or (str(t.year) if t.year else None),
        "WM/Genre": t.genre, "WM/ISRC": t.isrc, "WM/Lyrics": t.lyrics,
        "WM/TrackNumber": str(t.track_no) if t.track_no else None,
    }
    for k, v in mapping.items():
        if v not in (None, ""):
            audio[k] = [v]
    audio.save()


# --------------------------------- Helpers ---------------------------------

def extract_artwork(path: Path) -> tuple[bytes, str] | None:
    """Return the embedded cover as (bytes, mime), if present."""
    audio = MutagenFile(str(path))
    if audio is None:
        return None
    if isinstance(audio, FLAC) and audio.pictures:
        p = audio.pictures[0]
        return bytes(p.data), p.mime or "image/jpeg"
    if isinstance(audio, MP4):
        covers = (audio.tags or {}).get("covr")
        if covers:
            mime = "image/png" if covers[0].imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
            return bytes(covers[0]), mime
    tags = getattr(audio, "tags", None)
    if tags is not None and hasattr(tags, "getall"):
        pictures = tags.getall("APIC")
        if pictures:
            return bytes(pictures[0].data), pictures[0].mime or "image/jpeg"
    return None


def image_width(data: bytes) -> int | None:
    """Read image width straight from the header, without decoding the pixels."""
    if not data or len(data) < 24:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(data[16:20], "big")
    if data[:2] == b"\xff\xd8":
        i, n = 2, len(data)
        while i < n - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                return int.from_bytes(data[i + 7:i + 9], "big")
            segment = int.from_bytes(data[i + 2:i + 4], "big")
            if segment <= 0:
                break
            i += 2 + segment
    return None


def _sylt_to_lrc(frame: SYLT) -> str | None:
    """Convert an ID3 SYLT frame (synced lyrics) into LRC text."""
    try:
        lines = []
        for text, ms in frame.text:
            centis = int(ms) // 10
            lines.append(
                f"[{centis // 6000:02d}:{(centis // 100) % 60:02d}.{centis % 100:02d}]{text}")
        return "\n".join(lines) or None
    except Exception:
        return None
