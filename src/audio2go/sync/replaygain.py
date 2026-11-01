# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure ReplayGain with ffmpeg and write tags. Existing tags are left as-is."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from audio2go.sync.encode import ffmpeg_bin

log = logging.getLogger(__name__)

_RG_REFERENCE_LUFS = -18.0
_GAIN_RE = re.compile(r'track_gain\s*=\s*([+-]?\d+(?:\.\d+)?)\s*dB', re.I)
_PEAK_RE = re.compile(r'track_peak\s*=\s*([+-]?\d+(?:\.\d+)?)', re.I)
_LUFS_RE = re.compile(r'I:\s*([+-]?\d+(?:\.\d+)?)\s*LUFS', re.I)
_TRUEPEAK_RE = re.compile(r'True peak:\s*([+-]?\d+(?:\.\d+)?)\s*dB', re.I)
_RG_KEY_HINTS = (
    'replaygain_track_gain',
    'replaygain_album_gain',
    'r128_track_gain',
    'r128_album_gain',
)


def has_replaygain(path: Path) -> bool:
    try:
        from mutagen import File as MutagenFile
    except Exception:
        return False
    try:
        audio = MutagenFile(str(path))
    except Exception:
        return False
    if audio is None:
        return False
    tags = getattr(audio, 'tags', None)
    if tags is None:
        tags = audio
    try:
        keys = [str(key).lower() for key in tags]
    except Exception:
        keys = []
    if any(any(hint in key for hint in _RG_KEY_HINTS) for key in keys):
        return True
    getall = getattr(tags, 'getall', None)
    if not callable(getall):
        return False
    try:
        for frame in getall('TXXX') or ():
            desc = str(getattr(frame, 'desc', '') or '').lower()
            if 'replaygain' in desc or desc.startswith('r128_'):
                return True
        if getall('RVA2'):
            return True
    except Exception:
        return False
    return False


def apply_replaygain(path: Path) -> None:
    """Write track ReplayGain if the destination has none. No-op otherwise."""
    if not path.is_file() or has_replaygain(path):
        return
    ffmpeg = ffmpeg_bin()
    if ffmpeg is None:
        log.debug('Skipping ReplayGain for %s: ffmpeg is not installed', path)
        return
    measured = _measure(path, ffmpeg)
    if measured is None:
        return
    gain, peak = measured
    try:
        _write_tags(path, gain, peak)
    except Exception:
        log.warning('Could not write ReplayGain tags for %s', path, exc_info=True)


def _measure(path: Path, ffmpeg: str) -> tuple[float, float] | None:
    blob = _ffmpeg_filter(ffmpeg, path, 'replaygain')
    if blob:
        gain_match = _GAIN_RE.search(blob)
        peak_match = _PEAK_RE.search(blob)
        if gain_match:
            gain = float(gain_match.group(1))
            peak = float(peak_match.group(1)) if peak_match else 1.0
            return gain, max(0.0, peak)
    blob = _ffmpeg_filter(ffmpeg, path, 'ebur128=peak=true')
    if not blob:
        return None
    lufs_match = _LUFS_RE.search(blob)
    if not lufs_match:
        return None
    lufs = float(lufs_match.group(1))
    gain = _RG_REFERENCE_LUFS - lufs
    peak_db = _TRUEPEAK_RE.search(blob)
    if peak_db:
        peak = 10 ** (float(peak_db.group(1)) / 20.0)
    else:
        peak = 1.0
    return gain, max(0.0, peak)


def _ffmpeg_filter(ffmpeg: str, path: Path, filtr: str) -> str:
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                '-hide_banner',
                '-nostats',
                '-i',
                str(path),
                '-map',
                '0:a:0',
                '-af',
                filtr,
                '-f',
                'null',
                '-',
            ],
            check=False,
            capture_output=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug('ReplayGain analysis failed for %s: %s', path, exc)
        return ''
    return ((proc.stderr or b'') + (proc.stdout or b'')).decode(
        'utf-8', errors='replace'
    )


def _write_tags(path: Path, gain: float, peak: float) -> None:
    from mutagen import File as MutagenFile
    from mutagen.flac import FLAC
    from mutagen.id3 import ID3, TXXX, ID3NoHeaderError
    from mutagen.mp4 import MP4

    gain_text = f'{gain:+.2f} dB'
    peak_text = f'{peak:.6f}'
    suffix = path.suffix.lower()
    if suffix == '.mp3':
        try:
            tags = ID3(str(path))
        except ID3NoHeaderError:
            tags = ID3()
        for desc in ('replaygain_track_gain', 'replaygain_track_peak'):
            tags.delall(f'TXXX:{desc}')
        tags.add(TXXX(encoding=3, desc='replaygain_track_gain', text=[gain_text]))
        tags.add(TXXX(encoding=3, desc='replaygain_track_peak', text=[peak_text]))
        tags.save(str(path))
        return

    audio = MutagenFile(str(path))
    if audio is None:
        raise RuntimeError('unsupported audio file')
    if isinstance(audio, MP4):
        audio['----:com.apple.iTunes:replaygain_track_gain'] = [gain_text.encode()]
        audio['----:com.apple.iTunes:replaygain_track_peak'] = [peak_text.encode()]
        audio.save()
        return
    if isinstance(audio, FLAC) or suffix in {'.ogg', '.oga', '.opus'}:
        audio['REPLAYGAIN_TRACK_GAIN'] = [gain_text]
        audio['REPLAYGAIN_TRACK_PEAK'] = [peak_text]
        audio.save()
        return
    tags = getattr(audio, 'tags', None)
    if tags is None:
        raise RuntimeError('no tag container')
    try:
        tags['REPLAYGAIN_TRACK_GAIN'] = [gain_text]
        tags['REPLAYGAIN_TRACK_PEAK'] = [peak_text]
        audio.save()
    except Exception:
        audio['replaygain_track_gain'] = [gain_text]
        audio['replaygain_track_peak'] = [peak_text]
        audio.save()
