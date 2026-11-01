# SPDX-License-Identifier: GPL-3.0-or-later
"""Removable volumes: list, resolve, and eject."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

_SD_NAME_RE = re.compile(
    r'\b(sd|sdhc|sdxc|mmc|microsd|micro.?sd|memory.?card|card.?reader)\b',
    re.IGNORECASE,
)
_USB_NAME_RE = re.compile(r'\b(usb|thumb|flash.?drive|pen.?drive)\b', re.IGNORECASE)


@dataclass(frozen=True)
class RemovableVolume:
    name: str
    path: str
    uuid: str
    drive_name: str = ''
    kind: str = 'usb'

    @property
    def icon_name(self) -> str:
        return 'media-flash-symbolic' if self.kind == 'sd' else 'drive-harddisk-usb-symbolic'


def _mount_path(mount: Gio.Mount) -> str:
    root = mount.get_root()
    if root is None:
        return ''
    return root.get_path() or ''


def _is_system_path(path: str) -> bool:
    resolved = str(Path(path).resolve()) if path else ''
    return resolved in {'/', '/home', '/boot', '/usr', '/var', '/etc'}


def _looks_like_user_mount(path: str) -> bool:
    text = path.rstrip('/')
    return (
        '/run/media/' in text + '/'
        or text.startswith('/media/')
        or text.startswith('/mnt/')
    )


def _is_removable_mount(mount: Gio.Mount) -> bool:
    drive = mount.get_drive()
    if drive is not None:
        try:
            if drive.is_removable():
                return True
        except Exception:
            pass
        try:
            if drive.is_media_removable():
                return True
        except Exception:
            pass
    path = _mount_path(mount)
    if not path or _is_system_path(path):
        return False
    return _looks_like_user_mount(path)


def list_removable_volumes() -> list[RemovableVolume]:
    """Mounted removable volumes suitable as a sync destination."""
    monitor = Gio.VolumeMonitor.get()
    found: list[RemovableVolume] = []
    seen: set[str] = set()
    try:
        mounts = list(monitor.get_mounts() or [])
    except Exception:
        mounts = []
    for mount in mounts:
        try:
            if not _is_removable_mount(mount):
                continue
            path = _mount_path(mount)
            if not path or path in seen or not Path(path).is_dir():
                continue
            volume = mount.get_volume()
            drive = mount.get_drive()
            uuid = ''
            if volume is not None:
                try:
                    uuid = volume.get_uuid() or ''
                except Exception:
                    uuid = ''
                if not uuid:
                    try:
                        uuid = volume.get_identifier('uuid') or ''
                    except Exception:
                        uuid = ''
            name = mount.get_name() or Path(path).name or 'Removable drive'
            drive_name = drive.get_name() if drive is not None else ''
            seen.add(path)
            found.append(
                RemovableVolume(
                    name=name,
                    path=path,
                    uuid=uuid or path,
                    drive_name=drive_name,
                    kind=_device_kind(mount, volume, drive, name, drive_name),
                )
            )
        except Exception:
            log.debug('Skipping mount', exc_info=True)
            continue
    found.sort(key=lambda item: item.name.lower())
    return found


def _gicon_names(icon) -> list[str]:
    names: list[str] = []
    if icon is None:
        return names
    try:
        names.extend(icon.get_names() or [])
    except Exception:
        pass
    try:
        names.extend(_gicon_names(icon.get_icon()))
    except Exception:
        pass
    return names


def _unix_device(volume, drive) -> str:
    for obj, key in (
        (volume, Gio.VOLUME_IDENTIFIER_KIND_UNIX_DEVICE),
        (drive, Gio.DRIVE_IDENTIFIER_KIND_UNIX_DEVICE),
    ):
        if obj is None:
            continue
        try:
            value = obj.get_identifier(key) or ''
        except Exception:
            value = ''
        if value:
            return value
    return ''


def _block_root(devname: str) -> str:
    name = Path(devname).name
    if name.startswith('mmcblk') or name.startswith('nvme'):
        return re.sub(r'p\d+$', '', name)
    return re.sub(r'\d+$', '', name)


def _sysfs_text(unix_device: str) -> str:
    name = Path(unix_device).name
    if not name:
        return ''
    chunks: list[str] = []
    for candidate in dict.fromkeys((name, _block_root(name))):
        base = Path('/sys/class/block') / candidate
        for rel in ('uevent', 'device/uevent'):
            path = base / rel
            try:
                chunks.append(path.read_text(encoding='utf-8', errors='replace'))
            except OSError:
                continue
    return '\n'.join(chunks).lower()


def _icon_blob(mount, volume, drive) -> str:
    names: list[str] = []
    for obj in (mount, volume, drive):
        if obj is None:
            continue
        for getter in ('get_symbolic_icon', 'get_icon'):
            try:
                names.extend(_gicon_names(getattr(obj, getter)()))
            except Exception:
                continue
    return ' '.join(names).lower()


def _device_kind(mount, volume, drive, name: str, drive_name: str) -> str:
    unix = _unix_device(volume, drive).lower()
    sysfs = _sysfs_text(unix)
    icons = _icon_blob(mount, volume, drive)
    labels = f'{name} {drive_name}'
    if 'mmcblk' in unix or 'id_bus=mmc' in sysfs:
        return 'sd'
    if any(
        token in icons
        for token in ('media-flash', 'sd-mmc', 'memory-sd', 'memory_sd')
    ):
        return 'sd'
    if 'id_drive_flash_sd' in sysfs:
        return 'sd'
    if _SD_NAME_RE.search(labels) and not _USB_NAME_RE.search(labels):
        return 'sd'
    return 'usb'


def mount_for_path(path: str) -> Gio.Mount | None:
    needle = str(Path(path).expanduser().resolve()) if path else ''
    if not needle:
        return None
    monitor = Gio.VolumeMonitor.get()
    try:
        mounts = list(monitor.get_mounts() or [])
    except Exception:
        return None
    best: Gio.Mount | None = None
    best_len = -1
    for mount in mounts:
        mp = _mount_path(mount)
        if not mp:
            continue
        resolved = str(Path(mp).resolve())
        if needle == resolved or needle.startswith(resolved + '/'):
            if len(resolved) > best_len:
                best = mount
                best_len = len(resolved)
    return best


def eject_path(
    path: str,
    on_done: Callable[[bool, str], None],
) -> None:
    """Unmount / eject the volume that contains *path*."""
    mount = mount_for_path(path)
    if mount is None:
        on_done(False, 'Device is not mounted')
        return

    def finish(ok: bool, message: str) -> None:
        GLib.idle_add(on_done, ok, message)

    def on_eject(src: Gio.Mount, result: Gio.AsyncResult) -> None:
        try:
            src.eject_finish(result)
            finish(True, 'Device ejected')
        except GLib.Error as exc:
            finish(False, exc.message or str(exc))

    def on_unmount(src: Gio.Mount, result: Gio.AsyncResult) -> None:
        try:
            src.unmount_finish(result)
            finish(True, 'Device ejected')
        except GLib.Error as exc:
            finish(False, exc.message or str(exc))

    try:
        if mount.can_eject():
            mount.eject(Gio.MountUnmountFlags.NONE, None, on_eject)
            return
        if mount.can_unmount():
            mount.unmount(Gio.MountUnmountFlags.NONE, None, on_unmount)
            return
    except Exception as exc:
        finish(False, str(exc))
        return
    finish(False, 'This device cannot be ejected from here')
