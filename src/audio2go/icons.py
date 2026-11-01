# SPDX-License-Identifier: GPL-3.0-or-later
"""Register the application icon and resolve symbolic icon names safely."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from audio2go import config

log = logging.getLogger(__name__)

ICON_FALLBACKS: dict[str, tuple[str, ...]] = {
    'open-menu-symbolic': (
        'open-menu-symbolic',
        'application-menu-symbolic',
        'view-more-symbolic',
    ),
    'help-about-symbolic': (
        'help-about-symbolic',
        'info-symbolic',
        'dialog-information-symbolic',
        'help-info-symbolic',
        'open-menu-symbolic',
    ),
    'folder-music-symbolic': (
        'folder-music-symbolic',
        'audio-x-generic-symbolic',
        'folder-symbolic',
    ),
    'folder-download-symbolic': (
        'folder-download-symbolic',
        'folder-symbolic',
        'document-save-symbolic',
    ),
    'folder-symbolic': (
        'folder-symbolic',
        'folder',
    ),
    'folder-open-symbolic': (
        'folder-open-symbolic',
        'document-open-symbolic',
        'folder-symbolic',
    ),
    'media-flash-symbolic': (
        'media-flash-sd-mmc-symbolic',
        'media-flash-symbolic',
        'media-memory-sd-symbolic',
        'drive-removable-media-symbolic',
        'drive-harddisk-usb-symbolic',
    ),
    'drive-harddisk-usb-symbolic': (
        'drive-harddisk-usb-symbolic',
        'drive-removable-media-usb-symbolic',
        'drive-removable-media-symbolic',
        'media-removable-symbolic',
        'media-flash-symbolic',
    ),
    'media-eject-symbolic': (
        'media-eject-symbolic',
        'media-optical-symbolic',
        'drive-harddisk-usb-symbolic',
    ),
    'emblem-system-symbolic': (
        'emblem-system-symbolic',
        'preferences-system-symbolic',
        'applications-system-symbolic',
    ),
    'user-trash-symbolic': (
        'user-trash-symbolic',
        'edit-delete-symbolic',
    ),
    'image-x-generic-symbolic': (
        'image-x-generic-symbolic',
        'folder-pictures-symbolic',
        'image-x-generic',
    ),
    'process-stop-symbolic': (
        'process-stop-symbolic',
        'window-close-symbolic',
        'edit-delete-symbolic',
    ),
    'emblem-synchronizing-symbolic': (
        'emblem-synchronizing-symbolic',
        'view-refresh-symbolic',
        'media-playlist-repeat-symbolic',
    ),
}


def _project_icon_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / 'data' / 'icons'
        if (candidate / 'hicolor').is_dir():
            return candidate
    return None


def _icon_search_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path | None) -> None:
        if path is None:
            return
        resolved = path.resolve()
        if resolved in seen or not path.is_dir():
            return
        seen.add(resolved)
        roots.append(path)

    env = os.environ.get('AUDIO2GO_ICON_DIR')
    if env:
        add(Path(env))
    add(_project_icon_root())
    return roots


def _app_icon_file(*, symbolic: bool = False) -> Path | None:
    if symbolic:
        names = (f'hicolor/symbolic/apps/{config.APPLICATION_ID}-symbolic.svg',)
    else:
        names = (
            f'hicolor/scalable/apps/{config.APPLICATION_ID}.svg',
            f'hicolor/symbolic/apps/{config.APPLICATION_ID}-symbolic.svg',
        )
    for root in _icon_search_roots():
        for rel in names:
            path = root / rel
            if path.is_file():
                return path
    return None


def _install_user_app_icon(src: Path, *, symbolic: bool = False) -> None:
    if symbolic:
        dest_dir = (
            Path.home()
            / '.local'
            / 'share'
            / 'icons'
            / 'hicolor'
            / 'symbolic'
            / 'apps'
        )
        dest = dest_dir / f'{config.APPLICATION_ID}-symbolic.svg'
    else:
        dest_dir = (
            Path.home()
            / '.local'
            / 'share'
            / 'icons'
            / 'hicolor'
            / 'scalable'
            / 'apps'
        )
        dest = dest_dir / f'{config.APPLICATION_ID}.svg'
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        if not dest.is_file() or dest.stat().st_mtime < src.stat().st_mtime:
            shutil.copy2(src, dest)
    except OSError as exc:
        log.debug('Could not install user app icon: %s', exc)


def _desktop_exec_line() -> str:
    project = _project_icon_root()
    if project is not None:
        root = project.parent.parent
        run = root / 'run'
        if run.is_file():
            return str(run)
        return f'env PYTHONPATH={root / "src"} python3 -m audio2go.main'
    return 'audio2go'


def _ensure_user_desktop_file() -> None:
    apps = Path.home() / '.local' / 'share' / 'applications'
    apps.mkdir(parents=True, exist_ok=True)
    desktop = apps / f'{config.APPLICATION_ID}.desktop'
    exec_line = _desktop_exec_line()
    body = (
        '[Desktop Entry]\n'
        f'Name={config.APPLICATION_NAME}\n'
        'Comment=Sync and transcode a music library for portable devices\n'
        f'Exec={exec_line}\n'
        f'Icon={config.APPLICATION_ID}\n'
        'Terminal=false\n'
        'Type=Application\n'
        'Categories=AudioVideo;Audio;Music;\n'
        'StartupNotify=true\n'
        f'StartupWMClass={config.APPLICATION_ID}\n'
    )
    try:
        if not desktop.is_file() or desktop.read_text(encoding='utf-8') != body:
            desktop.write_text(body, encoding='utf-8')
    except OSError as exc:
        log.debug('Could not write desktop file: %s', exc)


def resolve_icon_name(preferred: str, display=None) -> str:
    import gi

    gi.require_version('Gtk', '4.0')
    gi.require_version('Gdk', '4.0')
    from gi.repository import Gdk, Gtk

    if display is None:
        display = Gdk.Display.get_default()
    if display is None:
        return preferred
    theme = Gtk.IconTheme.get_for_display(display)
    candidates = ICON_FALLBACKS.get(preferred, (preferred,))
    for name in candidates:
        try:
            if theme.has_icon(name):
                return name
        except Exception:
            continue
    return preferred


def apply_icon(widget, preferred: str, *, display=None) -> str:
    name = resolve_icon_name(preferred, display=display)
    if hasattr(widget, 'set_icon_name'):
        widget.set_icon_name(name)
    elif hasattr(widget, 'set_from_icon_name'):
        widget.set_from_icon_name(name)
    return name


def register_application_icons() -> None:
    import gi

    gi.require_version('Gtk', '4.0')
    gi.require_version('Gdk', '4.0')
    from gi.repository import Gdk, Gtk

    icon_name = config.APPLICATION_ID
    roots = _icon_search_roots()

    scalable = _app_icon_file(symbolic=False)
    if scalable is not None:
        _install_user_app_icon(scalable, symbolic=False)
    symbolic = _app_icon_file(symbolic=True)
    if symbolic is not None:
        _install_user_app_icon(symbolic, symbolic=True)
    _ensure_user_desktop_file()

    display = Gdk.Display.get_default()
    if display is None:
        return

    theme = Gtk.IconTheme.get_for_display(display)
    for root in roots:
        try:
            theme.add_search_path(str(root))
        except Exception as exc:
            log.debug('Could not add icon path %s: %s', root, exc)

    user_icons = Path.home() / '.local' / 'share' / 'icons'
    if user_icons.is_dir():
        try:
            theme.add_search_path(str(user_icons))
        except Exception as exc:
            log.debug('Could not add user icon path: %s', exc)

    try:
        theme.rescan_if_needed()
    except Exception:
        pass

    try:
        Gtk.Window.set_default_icon_name(icon_name)
    except Exception as exc:
        log.debug('set_default_icon_name failed: %s', exc)
