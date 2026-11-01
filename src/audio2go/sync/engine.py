# SPDX-License-Identifier: GPL-3.0-or-later
"""Walk a source library and copy or transcode into a destination tree."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from audio2go.device_profile import is_reserved_relative
from audio2go.settings import AppSettings
from audio2go.sync.artwork import (
    embed_artwork,
    has_embedded_artwork,
    resize_artwork,
    source_artwork,
)
from audio2go.sync.encode import FORMAT_EXTENSIONS, ffmpeg_bin, transcode_argv
from audio2go.sync.replaygain import apply_replaygain

log = logging.getLogger(__name__)

LOSSLESS_EXTS = {
    '.flac',
    '.wav',
    '.aiff',
    '.aif',
    '.wv',
    '.ape',
    '.tta',
    '.dsf',
    '.dff',
    '.alac',
}
LOSSY_EXTS = {
    '.mp3',
    '.ogg',
    '.oga',
    '.opus',
    '.wma',
    '.mpc',
    '.aac',
}
CONTAINER_EXTS = {'.m4a', '.mp4', '.m4b'}
AUDIO_EXTS = LOSSLESS_EXTS | LOSSY_EXTS | CONTAINER_EXTS
LYRIC_EXTS = {
    '.lrc',
    '.lrcx',
    '.lrc64',
    '.txt',
    '.srt',
    '.vtt',
    '.krc',
    '.qrc',
    '.ttml',
}


class SyncState(StrEnum):
    SCANNING = 'scanning'
    RUNNING = 'running'
    DONE = 'done'
    ERROR = 'error'
    CANCELLED = 'cancelled'


class FileAction(StrEnum):
    COPY = 'copy'
    TRANSCODE = 'transcode'
    SKIP = 'skip'


@dataclass
class FileOp:
    action: FileAction
    source: Path
    destination: Path
    relative: Path


@dataclass
class SyncProgress:
    state: SyncState
    current: int = 0
    total: int = 0
    action: str = ''
    path: str = ''
    message: str = ''
    copied: int = 0
    transcoded: int = 0
    skipped: int = 0
    errors: int = 0
    error_detail: str = ''


@dataclass
class SyncResult:
    state: SyncState
    copied: int = 0
    transcoded: int = 0
    skipped: int = 0
    errors: int = 0
    message: str = ''
    error_messages: list[str] = field(default_factory=list)


ProgressCallback = Callable[[SyncProgress], None]


def _is_hidden(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return any(part.startswith('.') for part in path.parts)
    return any(part.startswith('.') for part in rel.parts)


def _codec_from_mutagen(path: Path) -> str | None:
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
    info = getattr(audio, 'info', None)
    codec = getattr(info, 'codec', None)
    if isinstance(codec, str) and codec:
        return codec.lower()
    codec_name = getattr(info, 'codec_name', None)
    if isinstance(codec_name, str) and codec_name:
        return codec_name.lower()
    return None


def is_audio_file(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTS


def is_lyric_file(path: Path) -> bool:
    return path.suffix.lower() in LYRIC_EXTS


def is_lossless(path: Path) -> bool:
    ext = path.suffix.lower()
    if ext in LOSSLESS_EXTS:
        return True
    if ext in LOSSY_EXTS:
        return False
    if ext in CONTAINER_EXTS:
        codec = _codec_from_mutagen(path)
        if codec is None:
            return False
        return 'alac' in codec or 'pcm' in codec or 'flac' in codec
    return False


def destination_for(source: Path, relative: Path, output_format: str) -> Path:
    if output_format == 'copy' or not is_lossless(source):
        return relative
    ext = FORMAT_EXTENSIONS.get(output_format)
    if ext is None:
        return relative
    return relative.with_suffix(ext)


class SyncEngine:
    """Run one library sync on a worker thread."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._proc: subprocess.Popen[bytes] | None = None
        self._proc_lock = threading.Lock()

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def cancel(self) -> None:
        self._cancel.set()
        with self._proc_lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def start(
        self,
        settings: AppSettings,
        on_progress: ProgressCallback,
        on_done: Callable[[SyncResult], None],
    ) -> None:
        if self.busy:
            raise RuntimeError('A sync is already running')
        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(settings, on_progress, on_done),
            name='audio2go-sync',
            daemon=True,
        )
        self._thread.start()

    def _emit(self, callback: ProgressCallback, progress: SyncProgress) -> None:
        try:
            callback(progress)
        except Exception:
            log.debug('Progress callback failed', exc_info=True)

    def _run(
        self,
        settings: AppSettings,
        on_progress: ProgressCallback,
        on_done: Callable[[SyncResult], None],
    ) -> None:
        result = SyncResult(state=SyncState.ERROR, message='Sync failed')
        try:
            result = self._sync(settings, on_progress)
        except Exception as exc:
            log.exception('Sync crashed')
            result = SyncResult(
                state=SyncState.ERROR,
                message=str(exc),
                error_messages=[str(exc)],
            )
            self._emit(
                on_progress,
                SyncProgress(
                    state=SyncState.ERROR,
                    message=str(exc),
                    error_detail=str(exc),
                    errors=1,
                ),
            )
        finally:
            try:
                on_done(result)
            except Exception:
                log.debug('Done callback failed', exc_info=True)

    def _sync(
        self, settings: AppSettings, on_progress: ProgressCallback
    ) -> SyncResult:
        source = Path(settings.source_directory).expanduser()
        dest = Path(settings.destination_directory).expanduser()
        if not source.is_dir():
            raise FileNotFoundError(f'Source folder not found: {source}')
        if not settings.destination_directory.strip():
            raise FileNotFoundError('Choose a destination folder')
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise FileNotFoundError(f'Cannot create destination: {exc}') from exc
        if not dest.is_dir():
            raise FileNotFoundError(f'Destination folder not found: {dest}')

        source_r = source.resolve()
        dest_r = dest.resolve()
        if source_r == dest_r:
            raise ValueError('Source and destination cannot be the same folder')
        try:
            dest_r.relative_to(source_r)
            raise ValueError('Destination cannot be inside the source folder')
        except ValueError as exc:
            if str(exc).startswith('Destination'):
                raise

        self._emit(
            on_progress,
            SyncProgress(state=SyncState.SCANNING, message='Scanning library…'),
        )
        ops = self._collect_ops(source, dest, settings)
        total = len(ops)
        copied = transcoded = skipped = errors = 0
        error_messages: list[str] = []

        if self._cancel.is_set():
            return SyncResult(state=SyncState.CANCELLED, message='Stopped')

        for index, op in enumerate(ops, start=1):
            if self._cancel.is_set():
                result = SyncResult(
                    state=SyncState.CANCELLED,
                    copied=copied,
                    transcoded=transcoded,
                    skipped=skipped,
                    errors=errors,
                    message='Stopped',
                    error_messages=error_messages,
                )
                self._emit(
                    on_progress,
                    SyncProgress(
                        state=SyncState.CANCELLED,
                        current=index - 1,
                        total=total,
                        copied=copied,
                        transcoded=transcoded,
                        skipped=skipped,
                        errors=errors,
                        message='Stopped',
                    ),
                )
                return result

            display = str(op.relative).replace('\\', '/')
            action_label = {
                FileAction.COPY: 'Copying',
                FileAction.TRANSCODE: 'Transcoding',
                FileAction.SKIP: 'Skipping',
            }[op.action]
            self._emit(
                on_progress,
                SyncProgress(
                    state=SyncState.RUNNING,
                    current=index,
                    total=total,
                    action=action_label,
                    path=display,
                    message=f'{action_label} {display}',
                    copied=copied,
                    transcoded=transcoded,
                    skipped=skipped,
                    errors=errors,
                ),
            )

            try:
                if op.action == FileAction.SKIP:
                    skipped += 1
                    continue
                op.destination.parent.mkdir(parents=True, exist_ok=True)
                if op.action == FileAction.COPY:
                    shutil.copy2(op.source, op.destination)
                    copied += 1
                    if (
                        is_audio_file(op.source)
                        and settings.embed_artwork
                        and settings.artwork_max_size > 0
                    ):
                        self._apply_artwork(op.source, op.destination, settings)
                    if is_audio_file(op.source) and settings.replaygain:
                        apply_replaygain(op.destination)
                else:
                    self._transcode(op, settings)
                    transcoded += 1
                    if settings.embed_artwork:
                        self._apply_artwork(op.source, op.destination, settings)
                    if settings.replaygain:
                        apply_replaygain(op.destination)
            except Exception as exc:
                errors += 1
                detail = f'{display}: {exc}'
                error_messages.append(detail)
                log.warning('Sync file failed: %s', detail)

        state = SyncState.DONE if errors == 0 else SyncState.ERROR
        if errors and (copied or transcoded or skipped):
            message = f'Finished with {errors} error{"s" if errors != 1 else ""}'
        elif errors:
            message = error_messages[0] if error_messages else 'Sync failed'
        else:
            message = 'Sync complete'
        result = SyncResult(
            state=state,
            copied=copied,
            transcoded=transcoded,
            skipped=skipped,
            errors=errors,
            message=message,
            error_messages=error_messages,
        )
        self._emit(
            on_progress,
            SyncProgress(
                state=state,
                current=total,
                total=total,
                action='Done',
                message=message,
                copied=copied,
                transcoded=transcoded,
                skipped=skipped,
                errors=errors,
                error_detail=error_messages[0] if error_messages else '',
            ),
        )
        return result

    def _collect_ops(
        self, source: Path, dest: Path, settings: AppSettings
    ) -> list[FileOp]:
        ops: list[FileOp] = []
        for path in source.rglob('*'):
            if self._cancel.is_set():
                break
            if not path.is_file() or _is_hidden(path, source):
                continue
            try:
                relative = path.relative_to(source)
            except ValueError:
                continue
            if is_reserved_relative(relative):
                continue
            audio = is_audio_file(path)
            if not audio:
                if is_lyric_file(path):
                    if not settings.copy_lyrics:
                        continue
                elif not settings.copy_non_audio:
                    continue
                dest_rel = relative
                action = FileAction.COPY
            elif settings.output_format != 'copy' and is_lossless(path):
                dest_rel = destination_for(path, relative, settings.output_format)
                action = FileAction.TRANSCODE
            else:
                dest_rel = relative
                action = FileAction.COPY
            destination = dest / dest_rel
            if settings.skip_existing and destination.is_file():
                action = FileAction.SKIP
            ops.append(
                FileOp(
                    action=action,
                    source=path,
                    destination=destination,
                    relative=relative,
                )
            )
        ops.sort(key=lambda op: str(op.relative).lower())
        return ops

    def _transcode(self, op: FileOp, settings: AppSettings) -> None:
        if ffmpeg_bin() is None:
            raise FileNotFoundError(
                'ffmpeg is required to transcode. Install ffmpeg and try again.'
            )
        argv = transcode_argv(
            op.source,
            op.destination,
            settings.output_format,
            mp3_quality=settings.mp3_quality,
            aac_quality=settings.aac_quality,
            opus_bitrate=settings.opus_bitrate,
            copy_metadata=settings.copy_metadata,
            normalize_volume=settings.normalize_volume,
        )
        try:
            with self._proc_lock:
                self._proc = subprocess.Popen(
                    argv,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            proc = self._proc
            assert proc is not None
            while proc.poll() is None:
                if self._cancel.is_set():
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    raise RuntimeError('Stopped')
                time.sleep(0.05)
            stderr = b''
            if proc.stderr is not None:
                stderr = proc.stderr.read() or b''
            if proc.returncode != 0:
                detail = stderr.decode('utf-8', errors='replace').strip()
                raise RuntimeError(detail or f'ffmpeg exited {proc.returncode}')
        finally:
            with self._proc_lock:
                self._proc = None
            if self._cancel.is_set() and op.destination.is_file():
                try:
                    op.destination.unlink()
                except OSError:
                    pass

    def _apply_artwork(
        self, source: Path, destination: Path, settings: AppSettings
    ) -> None:
        try:
            if has_embedded_artwork(destination):
                return
            art = source_artwork(source)
            if art is None:
                return
            data, mime = resize_artwork(art[0], art[1], settings.artwork_max_size)
            embed_artwork(destination, data, mime)
        except Exception:
            log.debug('Could not embed artwork for %s', destination, exc_info=True)
