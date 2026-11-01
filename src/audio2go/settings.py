# SPDX-License-Identifier: GPL-3.0-or-later
"""Application settings (GSettings with in-memory fallback)."""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, fields, replace
from pathlib import Path

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

SCHEMA_ID = 'org.audio2go.Audio2Go'
_SCHEMA_XML = 'org.audio2go.Audio2Go.gschema.xml'

OUTPUT_FORMATS = (
    ('copy', 'Copy original'),
    ('mp3', 'MP3'),
    ('aac', 'AAC (M4A)'),
    ('opus', 'Opus'),
)
OUTPUT_FORMAT_IDS = frozenset(fmt[0] for fmt in OUTPUT_FORMATS)

DEFAULT_MP3_QUALITY = 'v2'
DEFAULT_AAC_QUALITY = 'cbr192'
DEFAULT_OPUS_BITRATE = 160
DEFAULT_ARTWORK_MAX_SIZE = 600

MP3_QUALITIES = (
    ('v0', 'V0 (VBR)'),
    ('v1', 'V1 (VBR)'),
    ('v2', 'V2 (VBR, default)'),
    ('v3', 'V3 (VBR)'),
    ('v4', 'V4 (VBR)'),
    ('v5', 'V5 (VBR)'),
    ('cbr320', '320 kbps CBR'),
    ('cbr256', '256 kbps CBR'),
    ('cbr192', '192 kbps CBR'),
    ('cbr128', '128 kbps CBR'),
)
MP3_QUALITY_IDS = frozenset(q[0] for q in MP3_QUALITIES)

AAC_QUALITIES = (
    ('vbr5', 'VBR 5 (highest)'),
    ('vbr4', 'VBR 4'),
    ('vbr3', 'VBR 3'),
    ('vbr2', 'VBR 2'),
    ('vbr1', 'VBR 1'),
    ('cbr320', '320 kbps CBR'),
    ('cbr256', '256 kbps CBR'),
    ('cbr192', '192 kbps CBR (default)'),
    ('cbr128', '128 kbps CBR'),
)
AAC_QUALITY_IDS = frozenset(q[0] for q in AAC_QUALITIES)

OPUS_BITRATES = (96, 128, 160, 192, 256)

# 0 keeps the original resolution when embedding.
ARTWORK_SIZES = (
    (300, '300 px'),
    (500, '500 px'),
    (600, '600 px (default)'),
    (800, '800 px'),
    (1000, '1000 px'),
    (1200, '1200 px'),
    (0, 'Original (no downscale)'),
)
_ARTWORK_SIZE_ALIASES = {
    720: 800,
}


def normalize_output_format(value: str | None) -> str:
    raw = (value or '').strip().lower()
    if raw in OUTPUT_FORMAT_IDS:
        return raw
    return 'opus'


def normalize_mp3_quality(value: str | None) -> str:
    raw = (value or '').strip().lower()
    if raw in MP3_QUALITY_IDS:
        return raw
    return DEFAULT_MP3_QUALITY


def normalize_aac_quality(value: str | None) -> str:
    raw = (value or '').strip().lower()
    if raw in AAC_QUALITY_IDS:
        return raw
    return DEFAULT_AAC_QUALITY


def normalize_opus_bitrate(value: int | None) -> int:
    try:
        bitrate = int(value if value is not None else DEFAULT_OPUS_BITRATE)
    except (TypeError, ValueError):
        return DEFAULT_OPUS_BITRATE
    if bitrate in OPUS_BITRATES:
        return bitrate
    closest = min(OPUS_BITRATES, key=lambda item: abs(item - bitrate))
    return closest


def opus_bitrate_label(bitrate: int) -> str:
    kbps = int(bitrate)
    if kbps == DEFAULT_OPUS_BITRATE:
        return f'{kbps} kbps (default)'
    return f'{kbps} kbps'


def normalize_artwork_max_size(value: int | None) -> int:
    try:
        size = int(value if value is not None else DEFAULT_ARTWORK_MAX_SIZE)
    except (TypeError, ValueError):
        return DEFAULT_ARTWORK_MAX_SIZE
    if size in _ARTWORK_SIZE_ALIASES:
        return _ARTWORK_SIZE_ALIASES[size]
    allowed = {px for px, _label in ARTWORK_SIZES}
    if size in allowed:
        return size
    return DEFAULT_ARTWORK_MAX_SIZE


def default_music_directory() -> Path:
    music = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_MUSIC)
    if music:
        return Path(music)
    return Path.home() / 'Music'


def default_source_directory() -> str:
    return str(default_music_directory())


def _get_string(settings: Gio.Settings, key: str, fallback: str) -> str:
    try:
        return settings.get_string(key)
    except Exception:
        return fallback


def _get_int(settings: Gio.Settings, key: str, fallback: int) -> int:
    try:
        return settings.get_int(key)
    except Exception:
        return fallback


def _get_bool(settings: Gio.Settings, key: str, fallback: bool) -> bool:
    try:
        return settings.get_boolean(key)
    except Exception:
        return fallback


def _checkout_schema_xml() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / 'data' / _SCHEMA_XML
        if candidate.is_file():
            return candidate
    return None


def _schema_cache_dirs() -> list[Path]:
    dirs = [Path(GLib.get_user_cache_dir()) / 'audio2go' / 'gschemas']
    repo_cache = Path(__file__).resolve().parents[2] / '.cache' / 'gschemas'
    if repo_cache not in dirs:
        dirs.append(repo_cache)
    return dirs


def _compile_checkout_schemas() -> Path | None:
    xml = _checkout_schema_xml()
    if xml is None:
        return None
    compiler = shutil.which('glib-compile-schemas')
    if compiler is None:
        return None
    last_exc: Exception | None = None
    for cache in _schema_cache_dirs():
        compiled = cache / 'gschemas.compiled'
        dest_xml = cache / _SCHEMA_XML
        try:
            stale = (
                not compiled.is_file()
                or not dest_xml.is_file()
                or dest_xml.stat().st_mtime < xml.stat().st_mtime
                or compiled.stat().st_mtime < xml.stat().st_mtime
            )
            if stale:
                cache.mkdir(parents=True, exist_ok=True)
                dest_xml.write_bytes(xml.read_bytes())
                subprocess.run(
                    [compiler, str(cache)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            if compiled.is_file():
                return cache
        except (OSError, subprocess.CalledProcessError) as exc:
            last_exc = exc
            continue
    if last_exc is not None:
        log.debug('Could not compile checkout GSettings schema: %s', last_exc)
    return None


def _schema_source() -> Gio.SettingsSchemaSource | None:
    default = Gio.SettingsSchemaSource.get_default()
    cache = _compile_checkout_schemas()
    if cache is not None:
        try:
            return Gio.SettingsSchemaSource.new_from_directory(
                str(cache), default, True
            )
        except GLib.Error as exc:
            log.debug('Could not load checkout schema source: %s', exc)
    return default


@dataclass
class AppSettings:
    color_scheme: str = 'default'
    source_directory: str = ''
    destination_directory: str = ''
    device_name: str = ''
    device_uuid: str = ''
    device_configured: bool = False
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


class SettingsStore:
    """Read/write GSettings when the schema is available; else memory defaults."""

    def __init__(self) -> None:
        self._settings = self._open_settings()
        self._memory = AppSettings()

    @staticmethod
    def _open_settings() -> Gio.Settings | None:
        source = _schema_source()
        if source is None:
            return None
        schema = source.lookup(SCHEMA_ID, True)
        if schema is None:
            return None
        return Gio.Settings.new_full(schema, None, None)

    def get(self) -> AppSettings:
        s = self._settings
        if s is None:
            snap = replace(self._memory)
        else:
            scheme = _get_string(s, 'color-scheme', 'default')
            if scheme not in ('default', 'light', 'dark'):
                scheme = 'default'
            snap = AppSettings(
                color_scheme=scheme,
                source_directory=_get_string(s, 'source-directory', ''),
                destination_directory=_get_string(s, 'destination-directory', ''),
                device_name=_get_string(s, 'device-name', ''),
                device_uuid=_get_string(s, 'device-uuid', ''),
                device_configured=_get_bool(s, 'device-configured', False),
                output_format=normalize_output_format(
                    _get_string(s, 'output-format', 'opus')
                ),
                mp3_quality=normalize_mp3_quality(
                    _get_string(s, 'mp3-quality', DEFAULT_MP3_QUALITY)
                ),
                aac_quality=normalize_aac_quality(
                    _get_string(s, 'aac-quality', DEFAULT_AAC_QUALITY)
                ),
                opus_bitrate=normalize_opus_bitrate(
                    _get_int(s, 'opus-bitrate', DEFAULT_OPUS_BITRATE)
                ),
                skip_existing=_get_bool(s, 'skip-existing', True),
                copy_non_audio=_get_bool(s, 'copy-non-audio', True),
                copy_lyrics=_get_bool(s, 'copy-lyrics', True),
                copy_metadata=_get_bool(s, 'copy-metadata', True),
                normalize_volume=_get_bool(s, 'normalize-volume', False),
                replaygain=_get_bool(s, 'replaygain', False),
                embed_artwork=_get_bool(s, 'embed-artwork', True),
                artwork_max_size=normalize_artwork_max_size(
                    _get_int(s, 'artwork-max-size', DEFAULT_ARTWORK_MAX_SIZE)
                ),
            )

        if not snap.source_directory.strip():
            snap.source_directory = default_source_directory()
        return snap

    def update(self, **kwargs) -> AppSettings:
        known = {f.name for f in fields(AppSettings)}
        clean = {k: v for k, v in kwargs.items() if k in known}
        current = replace(self.get(), **clean)

        current = replace(
            current,
            output_format=normalize_output_format(current.output_format),
            mp3_quality=normalize_mp3_quality(current.mp3_quality),
            aac_quality=normalize_aac_quality(current.aac_quality),
            opus_bitrate=normalize_opus_bitrate(current.opus_bitrate),
            artwork_max_size=normalize_artwork_max_size(current.artwork_max_size),
        )

        s = self._settings
        if s is None:
            self._memory = current
            return current

        key_map = {
            'color_scheme': ('color-scheme', 'string'),
            'source_directory': ('source-directory', 'string'),
            'destination_directory': ('destination-directory', 'string'),
            'device_name': ('device-name', 'string'),
            'device_uuid': ('device-uuid', 'string'),
            'device_configured': ('device-configured', 'bool'),
            'output_format': ('output-format', 'string'),
            'mp3_quality': ('mp3-quality', 'string'),
            'aac_quality': ('aac-quality', 'string'),
            'opus_bitrate': ('opus-bitrate', 'int'),
            'skip_existing': ('skip-existing', 'bool'),
            'copy_non_audio': ('copy-non-audio', 'bool'),
            'copy_lyrics': ('copy-lyrics', 'bool'),
            'copy_metadata': ('copy-metadata', 'bool'),
            'normalize_volume': ('normalize-volume', 'bool'),
            'replaygain': ('replaygain', 'bool'),
            'embed_artwork': ('embed-artwork', 'bool'),
            'artwork_max_size': ('artwork-max-size', 'int'),
        }
        attrs = clean.keys() if clean else key_map.keys()
        for attr in attrs:
            if attr not in key_map:
                continue
            gkey, kind = key_map[attr]
            value = getattr(current, attr)
            if kind == 'string':
                s.set_string(gkey, str(value))
            elif kind == 'int':
                s.set_int(gkey, int(value))
            elif kind == 'bool':
                s.set_boolean(gkey, bool(value))
        return current
