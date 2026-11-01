# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-device preferences stored on the removable drive."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

from audio2go.accents import DEFAULT_ACCENT, normalize_accent
from audio2go.settings import (
    DEFAULT_AAC_QUALITY,
    DEFAULT_ARTWORK_MAX_SIZE,
    DEFAULT_MP3_QUALITY,
    DEFAULT_OPUS_BITRATE,
    normalize_aac_quality,
    normalize_artwork_max_size,
    normalize_mp3_quality,
    normalize_opus_bitrate,
    normalize_output_format,
)

log = logging.getLogger(__name__)

PROFILE_DIR = 'audio2go'
LEGACY_PROFILE_DIR = '.audio2go'
PROFILE_FILE = 'device.json'
PROFILE_VERSION = 1
_RESERVED_DIRS = frozenset({PROFILE_DIR, LEGACY_PROFILE_DIR})
_MUSIC_DIR_NAMES = ('Music', 'MUSIC', 'music', 'MP3', 'Audio', 'AUDIO')


@dataclass
class DeviceProfile:
    version: int = PROFILE_VERSION
    uuid: str = ''
    name: str = ''
    music_folder: str = ''
    color: str = DEFAULT_ACCENT
    output_format: str = 'opus'
    mp3_quality: str = DEFAULT_MP3_QUALITY
    aac_quality: str = DEFAULT_AAC_QUALITY
    opus_bitrate: int = DEFAULT_OPUS_BITRATE
    skip_existing: bool = True
    copy_non_audio: bool = True
    copy_lyrics: bool = True
    copy_metadata: bool = True
    normalize_volume: bool = False
    replaygain: bool = False
    embed_artwork: bool = True
    artwork_max_size: int = DEFAULT_ARTWORK_MAX_SIZE

    def normalized(self) -> DeviceProfile:
        return replace(
            self,
            version=PROFILE_VERSION,
            uuid=(self.uuid or '').strip(),
            name=(self.name or '').strip(),
            music_folder=_clean_relative(self.music_folder),
            color=normalize_accent(self.color),
            output_format=normalize_output_format(self.output_format),
            mp3_quality=normalize_mp3_quality(self.mp3_quality),
            aac_quality=normalize_aac_quality(self.aac_quality),
            opus_bitrate=normalize_opus_bitrate(self.opus_bitrate),
            skip_existing=_as_bool(self.skip_existing, True),
            copy_non_audio=_as_bool(self.copy_non_audio, True),
            copy_lyrics=_as_bool(self.copy_lyrics, True),
            copy_metadata=_as_bool(self.copy_metadata, True),
            normalize_volume=_as_bool(self.normalize_volume, False),
            replaygain=_as_bool(self.replaygain, False),
            embed_artwork=_as_bool(self.embed_artwork, True),
            artwork_max_size=normalize_artwork_max_size(self.artwork_max_size),
        )


def profile_dir(mount: str) -> Path:
    return Path(mount) / PROFILE_DIR


def profile_path(mount: str) -> Path:
    return profile_dir(mount) / PROFILE_FILE


def profile_search_dirs(mount: str) -> tuple[Path, ...]:
    if not mount:
        return ()
    root = Path(mount)
    return (root / PROFILE_DIR, root / LEGACY_PROFILE_DIR)


def is_reserved_relative(relative: Path) -> bool:
    return bool(relative.parts) and relative.parts[0] in _RESERVED_DIRS


def guess_music_folder(mount: str) -> str:
    root = Path(mount)
    for name in _MUSIC_DIR_NAMES:
        candidate = root / name
        if candidate.is_dir():
            return name
    return ''


def music_destination(mount: str, profile: DeviceProfile) -> str:
    root = Path(mount).expanduser()
    rel = _clean_relative(profile.music_folder)
    dest = (root / rel) if rel else root
    try:
        resolved = dest.resolve()
        mount_r = root.resolve()
    except OSError:
        return str(dest)
    if resolved != mount_r and mount_r not in resolved.parents:
        return str(mount_r)
    return str(resolved)


def load_profile_from_file(path: Path) -> DeviceProfile | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning('Could not read device profile %s: %s', path, exc)
        return None
    if not isinstance(raw, dict):
        return None
    known = {item.name for item in fields(DeviceProfile)}
    data = {}
    for key in known:
        if key not in raw:
            continue
        value = _coerce_field(key, raw[key])
        if value is None:
            continue
        data[key] = value
    try:
        return DeviceProfile(**data).normalized()
    except TypeError:
        return DeviceProfile().normalized()


def load_profile(mount: str) -> DeviceProfile | None:
    for directory in profile_search_dirs(mount):
        loaded = load_profile_from_file(directory / PROFILE_FILE)
        if loaded is not None:
            return loaded
    return None


def save_profile(mount: str, profile: DeviceProfile) -> bool:
    if not mount:
        return False
    dest_dir = profile_dir(mount)
    path = profile_path(mount)
    payload = asdict(profile.normalized())
    tmp = path.with_suffix('.json.tmp')
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
        tmp.replace(path)
        return True
    except OSError as exc:
        log.warning('Could not write device profile %s: %s', path, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def default_profile(
    *,
    uuid: str,
    name: str,
    mount: str,
    seed: DeviceProfile | None = None,
) -> DeviceProfile:
    profile = DeviceProfile(
        uuid=uuid,
        name=name.strip() or Path(mount).name or 'Device',
        music_folder=guess_music_folder(mount),
    )
    if seed is not None:
        profile.output_format = seed.output_format
        profile.mp3_quality = seed.mp3_quality
        profile.aac_quality = seed.aac_quality
        profile.opus_bitrate = seed.opus_bitrate
        profile.skip_existing = seed.skip_existing
        profile.copy_non_audio = seed.copy_non_audio
        profile.copy_lyrics = seed.copy_lyrics
        profile.copy_metadata = seed.copy_metadata
        profile.normalize_volume = seed.normalize_volume
        profile.replaygain = seed.replaygain
        profile.embed_artwork = seed.embed_artwork
        profile.artwork_max_size = seed.artwork_max_size
    return profile.normalized()


def load_or_create_profile(
    *,
    uuid: str,
    name: str,
    mount: str,
    seed: DeviceProfile | None = None,
) -> tuple[DeviceProfile, bool]:
    existing = load_profile(mount)
    if existing is None:
        return default_profile(uuid=uuid, name=name, mount=mount, seed=seed), False
    changed = False
    if uuid and existing.uuid != uuid:
        existing = replace(existing, uuid=uuid)
        changed = True
    if not existing.name.strip() and name.strip():
        existing = replace(existing, name=name.strip())
        changed = True
    if changed:
        save_profile(mount, existing)
    return existing.normalized(), True


def relative_to_mount(mount: str, path: str) -> str:
    try:
        rel = Path(path).resolve().relative_to(Path(mount).resolve())
    except (OSError, ValueError):
        return ''
    text = str(rel)
    return '' if text == '.' else text


_BOOL_FIELDS = frozenset(
    {
        'skip_existing',
        'copy_non_audio',
        'copy_lyrics',
        'copy_metadata',
        'normalize_volume',
        'replaygain',
        'embed_artwork',
    }
)
_INT_FIELDS = frozenset({'version', 'opus_bitrate', 'artwork_max_size'})
_STRING_FIELDS = frozenset(
    {
        'uuid',
        'name',
        'music_folder',
        'output_format',
        'mp3_quality',
        'aac_quality',
        'color',
    }
)


def _as_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {'1', 'true', 'yes', 'on'}:
            return True
        if text in {'0', 'false', 'no', 'off', ''}:
            return False
        return default
    return bool(value)


def _coerce_field(name: str, value: object) -> object | None:
    if value is None:
        return None
    if name in _BOOL_FIELDS:
        return _as_bool(value, False)
    if name in _INT_FIELDS:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if name in _STRING_FIELDS:
        return str(value)
    return value


def _clean_relative(value: str | None) -> str:
    text = (value or '').strip().replace('\\', '/')
    if not text or text == '.':
        return ''
    parts: list[str] = []
    for part in text.split('/'):
        if not part or part == '.':
            continue
        if part == '..':
            return ''
        parts.append(part)
    if parts and parts[0] in _RESERVED_DIRS:
        return ''
    return '/'.join(parts)
