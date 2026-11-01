# SPDX-License-Identifier: GPL-3.0-or-later
"""Extract, resize, and embed cover art."""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path

log = logging.getLogger(__name__)

FOLDER_COVER_NAMES = (
    'cover.jpg',
    'cover.jpeg',
    'cover.png',
    'cover.webp',
    'folder.jpg',
    'folder.jpeg',
    'folder.png',
    'front.jpg',
    'front.jpeg',
    'front.png',
    'artwork.jpg',
    'artwork.jpeg',
    'artwork.png',
)


def extract_embedded_artwork(path: Path) -> tuple[bytes, str] | None:
    """Return (bytes, mime) for the largest embedded front cover, if any."""
    try:
        from mutagen import File as MutagenFile
    except Exception:
        return None
    try:
        audio = MutagenFile(str(path))
    except Exception:
        return None
    if audio is None:
        return None

    pictures = getattr(audio, 'pictures', None)
    if pictures:
        pic = max(pictures, key=lambda item: len(getattr(item, 'data', b'') or b''))
        data = getattr(pic, 'data', b'') or b''
        if data:
            mime = getattr(pic, 'mime', '') or 'image/jpeg'
            return data, mime

    tags = getattr(audio, 'tags', None)
    if tags is None:
        return None

    getall = getattr(tags, 'getall', None)
    if callable(getall):
        try:
            apics = list(getall('APIC') or ())
        except Exception:
            apics = []
        if apics:
            pic = max(apics, key=lambda item: len(getattr(item, 'data', b'') or b''))
            data = getattr(pic, 'data', b'') or b''
            if data:
                mime = getattr(pic, 'mime', '') or 'image/jpeg'
                return data, mime

    try:
        covers = tags.get('covr')  # type: ignore[union-attr]
    except Exception:
        covers = None
    if covers:
        cover = covers[0]
        data = bytes(cover)
        if data:
            fmt = getattr(cover, 'imageformat', None)
            mime = 'image/png' if fmt == 14 else 'image/jpeg'
            return data, mime

    raw_blocks: list[str] = []
    for key in ('METADATA_BLOCK_PICTURE', 'metadata_block_picture'):
        try:
            value = tags.get(key)  # type: ignore[union-attr]
        except Exception:
            value = None
        if value:
            raw_blocks.extend(str(item) for item in value)
            break
    for encoded in raw_blocks:
        try:
            from mutagen.flac import Picture

            pic = Picture(base64.b64decode(encoded))
        except Exception:
            continue
        data = pic.data or b''
        if data:
            return data, pic.mime or 'image/jpeg'
    return None


def folder_artwork(directory: Path) -> tuple[bytes, str] | None:
    """Return a conventional folder cover image next to the audio file."""
    for name in FOLDER_COVER_NAMES:
        path = directory / name
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if not data:
            continue
        suffix = path.suffix.lower()
        mime = {
            '.png': 'image/png',
            '.webp': 'image/webp',
        }.get(suffix, 'image/jpeg')
        return data, mime
    return None


def has_embedded_artwork(path: Path) -> bool:
    """True when *path* already has cover art in its tags."""
    return extract_embedded_artwork(path) is not None


def source_artwork(path: Path) -> tuple[bytes, str] | None:
    """Prefer embedded art, then a cover file in the same folder."""
    return extract_embedded_artwork(path) or folder_artwork(path.parent)


def resize_artwork(
    data: bytes, mime: str, max_edge: int
) -> tuple[bytes, str]:
    """Scale so the longest edge is at most *max_edge* (0 = original)."""
    if max_edge <= 0 or not data:
        return data, mime
    try:
        from PIL import Image
    except ImportError:
        log.debug('Pillow is not installed; skipping artwork resize')
        return data, mime

    try:
        with Image.open(io.BytesIO(data)) as im:
            width, height = im.size
            if max(width, height) <= max_edge:
                return data, mime
            if im.mode in ('RGBA', 'LA', 'P'):
                rgba = im.convert('RGBA')
                background = Image.new('RGB', rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.split()[-1])
                im = background
            elif im.mode != 'RGB':
                im = im.convert('RGB')
            im.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format='JPEG', quality=90, optimize=True)
            return buf.getvalue(), 'image/jpeg'
    except Exception:
        log.debug('Could not resize artwork', exc_info=True)
        return data, mime


def embed_artwork(path: Path, image_bytes: bytes, mime: str = 'image/jpeg') -> None:
    """Write front-cover art into *path* using mutagen."""
    if not image_bytes:
        return
    suffix = path.suffix.lower()
    if suffix == '.flac':
        from mutagen.flac import FLAC, Picture

        audio = FLAC(path)
        audio.clear_pictures()
        pic = Picture()
        pic.type = 3
        pic.mime = mime
        pic.desc = 'Cover'
        pic.data = image_bytes
        audio.add_picture(pic)
        audio.save()
        return
    if suffix == '.mp3':
        from mutagen.id3 import APIC, ID3
        from mutagen.mp3 import MP3

        audio = MP3(path, ID3=ID3)
        if audio.tags is None:
            audio.add_tags()
        assert audio.tags is not None
        audio.tags.delall('APIC')
        audio.tags.add(
            APIC(
                encoding=3,
                mime=mime,
                type=3,
                desc='Cover',
                data=image_bytes,
            )
        )
        audio.save()
        return
    if suffix in {'.m4a', '.mp4', '.aac'}:
        from mutagen.mp4 import MP4, MP4Cover

        audio = MP4(path)
        fmt = MP4Cover.FORMAT_PNG if mime == 'image/png' else MP4Cover.FORMAT_JPEG
        audio['covr'] = [MP4Cover(image_bytes, imageformat=fmt)]
        audio.save()
        return
    if suffix in {'.opus', '.ogg'}:
        from mutagen.flac import Picture
        from mutagen.oggopus import OggOpus

        try:
            audio = OggOpus(path)
        except Exception:
            from mutagen.oggvorbis import OggVorbis

            audio = OggVorbis(path)
        pic = Picture()
        pic.type = 3
        pic.mime = mime
        pic.desc = 'Cover'
        pic.data = image_bytes
        audio['metadata_block_picture'] = [
            base64.b64encode(pic.write()).decode('ascii')
        ]
        audio.save()
        return
    log.debug('Artwork not embedded for %s', path)
