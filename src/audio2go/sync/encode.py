# SPDX-License-Identifier: GPL-3.0-or-later
"""FFmpeg command lines for transcoding library files to MP3 / AAC / Opus."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from audio2go.settings import (
    normalize_aac_quality,
    normalize_mp3_quality,
    normalize_opus_bitrate,
)

_AAC_VBR_TO_NATIVE_CBR = {
    5: 192,
    4: 128,
    3: 96,
    2: 64,
    1: 48,
}

FORMAT_EXTENSIONS = {
    'mp3': '.mp3',
    'aac': '.m4a',
    'opus': '.opus',
}

# Portable-player loudness (louder than broadcast -23 LUFS).
LOUDNORM_FILTER = 'loudnorm=I=-16:TP=-1.5:LRA=11'


def ffmpeg_bin() -> str | None:
    return shutil.which('ffmpeg')


def prefer_fdk_aac() -> bool:
    ffmpeg = ffmpeg_bin()
    if ffmpeg is None:
        return False
    try:
        proc = subprocess.run(
            [ffmpeg, '-hide_banner', '-encoders'],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    blob = (proc.stdout or '') + (proc.stderr or '')
    return 'libfdk_aac' in blob


def mp3_ffmpeg_args(quality: str) -> list[str]:
    q = normalize_mp3_quality(quality)
    if q.startswith('v') and q[1:].isdigit():
        return ['-c:a', 'libmp3lame', '-q:a', q[1:]]
    if q.startswith('cbr') and q[3:].isdigit():
        return ['-c:a', 'libmp3lame', '-b:a', f'{q[3:]}k']
    return ['-c:a', 'libmp3lame', '-q:a', '2']


def aac_ffmpeg_args(quality: str, *, use_fdk: bool | None = None) -> list[str]:
    q = normalize_aac_quality(quality)
    if use_fdk is None:
        use_fdk = prefer_fdk_aac()
    codec = 'libfdk_aac' if use_fdk else 'aac'
    args = ['-c:a', codec]
    if use_fdk:
        args.extend(['-afterburner', '1'])
    if q.startswith('vbr') and q[3:].isdigit():
        mode = max(1, min(5, int(q[3:])))
        if use_fdk:
            args.extend(['-vbr', str(mode)])
        else:
            args.extend(['-b:a', f'{_AAC_VBR_TO_NATIVE_CBR[mode]}k'])
    elif q.startswith('cbr') and q[3:].isdigit():
        args.extend(['-b:a', f'{q[3:]}k'])
    else:
        args.extend(['-b:a', '192k'])
    return args


def opus_ffmpeg_args(bitrate: int) -> list[str]:
    kbps = normalize_opus_bitrate(bitrate)
    return ['-c:a', 'libopus', '-b:a', f'{kbps}k', '-vbr', 'on', '-application', 'audio']


def transcode_argv(
    src: Path,
    dest: Path,
    fmt: str,
    *,
    mp3_quality: str,
    aac_quality: str,
    opus_bitrate: int,
    copy_metadata: bool,
    normalize_volume: bool = False,
) -> list[str]:
    ffmpeg = ffmpeg_bin()
    if ffmpeg is None:
        raise FileNotFoundError('ffmpeg is not installed')

    cmd = [
        ffmpeg,
        '-y',
        '-hide_banner',
        '-loglevel',
        'error',
        '-i',
        str(src),
        '-map',
        '0:a:0',
        '-vn',
    ]

    if copy_metadata:
        cmd.extend(['-map_metadata', '0'])
    else:
        cmd.extend(['-map_metadata', '-1'])

    if normalize_volume:
        cmd.extend(['-af', LOUDNORM_FILTER])

    if fmt == 'mp3':
        cmd.extend(mp3_ffmpeg_args(mp3_quality))
        cmd.extend(['-id3v2_version', '3'])
    elif fmt == 'aac':
        cmd.extend(aac_ffmpeg_args(aac_quality))
    elif fmt == 'opus':
        cmd.extend(opus_ffmpeg_args(opus_bitrate))
    else:
        raise ValueError(f'unsupported transcode format: {fmt}')

    cmd.append(str(dest))
    return cmd
