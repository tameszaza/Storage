"""Normalize embedded music covers for clean square mobile artwork."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image

AUDIO_SUFFIXES = frozenset({".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".webm"})


def embedded_cover_size(path: Path) -> tuple[int, int] | None:
    """Return the attached-cover dimensions without decoding the audio."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:stream_disposition=attached_pic", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    try:
        streams = json.loads(result.stdout).get("streams", [])
        stream = next(item for item in streams if item.get("disposition", {}).get("attached_pic"))
        width, height = int(stream["width"]), int(stream["height"])
        return (width, height) if width > 0 and height > 0 else None
    except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError):
        return None


def _render_center_square(source: Path, output: Path) -> None:
    # YouTube images are commonly 16:9 with a square album cover centred inside.
    # Crop the letterbox rather than adding more padding for the phone UI.
    filter_graph = "crop=min(iw\\,ih):min(iw\\,ih):(iw-ow)/2:(ih-oh)"
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source), "-map", "0:v:0",
         "-frames:v", "1", "-vf", filter_graph, str(output)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode or not output.is_file():
        raise ValueError("Could not create a square cover image.")


def _embed_cover(path: Path, cover: Path) -> None:
    data = cover.read_bytes()
    with Image.open(cover) as image:
        width, height = image.size
    suffix = path.suffix.lower()
    if suffix == ".opus":
        from mutagen.flac import Picture
        from mutagen.oggopus import OggOpus

        picture = Picture()
        picture.type = 3
        picture.mime = "image/jpeg"
        picture.desc = "Cover (front)"
        picture.width, picture.height, picture.depth = width, height, 24
        picture.data = data
        audio = OggOpus(path)
        audio["metadata_block_picture"] = [base64.b64encode(picture.write()).decode("ascii")]
        audio.save()
        return
    if suffix == ".m4a":
        from mutagen.mp4 import MP4, MP4Cover

        audio = MP4(path)
        audio["covr"] = [MP4Cover(data, imageformat=MP4Cover.FORMAT_JPEG)]
        audio.save()
        return
    if suffix == ".mp3":
        from mutagen.id3 import APIC, ID3

        tags = ID3(path)
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover (front)", data=data))
        tags.save(path, v2_version=3)
        return
    raise ValueError(f"Embedded-cover updates are not supported for {suffix}.")


def normalize_embedded_cover(source: Path) -> str:
    """Atomically center-crop one non-square embedded cover.

    The original audio is retained unless an entirely rewritten temporary copy
    has a verified square attached image. Return a compact outcome for logs.
    """
    source = Path(source)
    if source.suffix.lower() not in AUDIO_SUFFIXES or not source.is_file() or source.is_symlink():
        return "skipped"
    size = embedded_cover_size(source)
    if not size:
        return "no-cover"
    if size[0] == size[1]:
        return "already-square"
    with tempfile.TemporaryDirectory(prefix=f".{source.stem[:28]}-cover-", dir=source.parent) as directory:
        temporary = Path(directory)
        cover = temporary / "cover.jpg"
        copy = temporary / source.name
        _render_center_square(source, cover)
        shutil.copy2(source, copy)
        _embed_cover(copy, cover)
        if embedded_cover_size(copy) != (min(size), min(size)):
            raise ValueError("Square cover verification failed.")
        os.replace(copy, source)
    return "updated"
