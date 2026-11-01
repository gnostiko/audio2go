# SPDX-License-Identifier: GPL-3.0-or-later
"""Main application window."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from audio2go.accents import ACCENT_COLORS, normalize_accent
from audio2go.device import RemovableVolume, eject_path, list_removable_volumes
from audio2go.device_profile import (
    DeviceProfile,
    load_or_create_profile,
    music_destination,
    save_profile,
)
from audio2go.icons import apply_icon
from audio2go.settings import AppSettings, SettingsStore
from audio2go.setup_dialog import (
    DeviceSetupDialog,
    present_profile_save_failed,
    status_toast,
)
from audio2go.sync.encode import ffmpeg_bin
from audio2go.sync.engine import SyncEngine, SyncProgress, SyncResult, SyncState
from audio2go.theme import ChromeStyleController
from audio2go.window_styles import banner_fill_css, install_window_styles

_WINDOW_UI = str(Path(__file__).with_name('window.ui'))

_ADW_TEMPLATE_TYPES = (
    'ApplicationWindow',
    'ToolbarView',
    'HeaderBar',
    'WindowTitle',
    'ToastOverlay',
    'Clamp',
    'PreferencesGroup',
    'ActionRow',
    'ComboRow',
    'SwitchRow',
    'StatusPage',
)
_adw_types_ensured = False


def ensure_adw_template_types() -> None:
    """Register Adw widgets used by window.ui for GtkBuilder."""
    global _adw_types_ensured
    if _adw_types_ensured:
        return
    Adw.init()
    for name in _ADW_TEMPLATE_TYPES:
        typ = getattr(Adw, name, None)
        if typ is not None:
            GObject.type_ensure(typ)
    _adw_types_ensured = True


def _short_path(path: str) -> str:
    text = (path or '').strip()
    if not text:
        return 'Choose a folder'
    home = str(Path.home())
    if text == home or text.startswith(home + '/'):
        return '~' + text[len(home):]
    return text


def _device_key(volume: RemovableVolume) -> str:
    return volume.uuid or volume.path


@dataclass
class DeviceSession:
    volume: RemovableVolume
    profile: DeviceProfile
    engine: SyncEngine = field(default_factory=SyncEngine)
    row: Adw.ActionRow | None = None
    settings_button: Gtk.Button | None = None
    eject_button: Gtk.Button | None = None
    kind_icon: Gtk.Image | None = None
    progress_fraction: float = 0.0


@Gtk.Template(filename=_WINDOW_UI)
class Audio2GoWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'Audio2GoWindow'

    window_overlay = Gtk.Template.Child()
    flash_veil = Gtk.Template.Child()
    toast_overlay = Gtk.Template.Child()
    toolbar_view = Gtk.Template.Child()
    window_title = Gtk.Template.Child()
    menu_button = Gtk.Template.Child()
    main_stack = Gtk.Template.Child()
    empty_page = Gtk.Template.Child()
    content_box = Gtk.Template.Child()
    source_row = Gtk.Template.Child()
    device_group = Gtk.Template.Child()
    sync_button = Gtk.Template.Child()
    sync_revealer = Gtk.Template.Child()
    sync_banner = Gtk.Template.Child()
    sync_title_label = Gtk.Template.Child()
    stop_button = Gtk.Template.Child()

    def __init__(self, store: SettingsStore | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.store = store if store is not None else SettingsStore()
        self._sessions: dict[str, DeviceSession] = {}
        self._selected_key: str | None = None
        self._vol_debounce_id: int | None = None
        self._setup_dialog: DeviceSetupDialog | None = None
        self._flash_id: int | None = None
        self._banner_shown = 0.0
        self._banner_target = 0.0
        self._banner_tick_id: int | None = None
        self._banner_last_us = 0
        self._banner_hold = False
        self._banner_provider = Gtk.CssProvider()
        self._pending_toasts: list[str] = []
        self._toast_flush_id: int | None = None

        self._setup_window_actions()
        self._setup_header()
        self._setup_library_row()
        install_window_styles(self.get_display())
        self._chrome = ChromeStyleController(self)
        self._setup_volume_monitor()
        self._refresh_library_row()
        self._refresh_devices()
        self.connect('map', self._on_map)
        self.connect('close-request', self._on_close_request)

    def _setup_window_actions(self) -> None:
        for name, callback in (
            ('close', lambda *_: self.close()),
            ('menu', self._on_menu),
            ('sync', self._on_sync_selected),
            ('sync-all', self._on_sync_all),
            ('stop', self._on_stop),
            ('eject', self._on_eject_selected),
            ('device-settings', self._on_device_settings),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', callback)
            self.add_action(action)

    def _on_menu(self, *_args) -> None:
        self.menu_button.set_active(True)

    def _setup_header(self) -> None:
        app = self.get_application()
        if app is not None and hasattr(app, 'build_primary_menu'):
            self.menu_button.set_menu_model(app.build_primary_menu())
        self.menu_button.set_primary(True)
        apply_icon(self.menu_button, 'help-about-symbolic')
        apply_icon(self.stop_button, 'process-stop-symbolic')
        self.flash_veil.set_halign(Gtk.Align.FILL)
        self.flash_veil.set_valign(Gtk.Align.FILL)
        self.flash_veil.set_hexpand(True)
        self.flash_veil.set_vexpand(True)
        self.sync_banner.get_style_context().add_provider(
            self._banner_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 20,
        )

    def _setup_library_row(self) -> None:
        apply_icon(self.source_row, 'folder-music-symbolic')
        choose = Gtk.Button()
        apply_icon(choose, 'folder-open-symbolic')
        choose.set_valign(Gtk.Align.CENTER)
        choose.set_tooltip_text('Select music folder')
        choose.add_css_class('flat')
        choose.connect('clicked', self._on_choose_source)
        self.source_row.add_suffix(choose)
        self.source_row.connect('activated', self._on_choose_source)

    def _setup_volume_monitor(self) -> None:
        self._volume_monitor = Gio.VolumeMonitor.get()
        for signal in ('mount-added', 'mount-removed', 'mount-changed'):
            self._volume_monitor.connect(signal, self._on_volumes_changed)

    def _on_volumes_changed(self, *_args) -> None:
        if self._vol_debounce_id is not None:
            GLib.source_remove(self._vol_debounce_id)
        self._vol_debounce_id = GLib.timeout_add(200, self._refresh_devices)
        return

    def _refresh_library_row(self) -> None:
        snap = self.store.get()
        self.source_row.set_title(_short_path(snap.source_directory))
        self.source_row.set_subtitle('')

    def _refresh_devices(self) -> bool:
        self._vol_debounce_id = None
        volumes = list_removable_volumes()
        seen = {_device_key(volume) for volume in volumes}

        for key, session in list(self._sessions.items()):
            if key in seen:
                continue
            if session.engine.busy:
                session.engine.cancel()
            if session.row is not None:
                self.device_group.remove(session.row)
            del self._sessions[key]

        for volume in volumes:
            key = _device_key(volume)
            existing = self._sessions.get(key)
            if existing is None:
                self._sessions[key] = self._create_session(volume)
            else:
                existing.volume = volume
                self._update_session_row(existing)

        if self._selected_key not in self._sessions:
            preferred = self.store.get().device_uuid
            self._selected_key = None
            if preferred:
                for key, session in self._sessions.items():
                    if (
                        session.volume.uuid == preferred
                        or session.volume.path == preferred
                    ):
                        self._selected_key = key
                        break
            if self._selected_key is None:
                self._selected_key = next(iter(self._sessions), None)
        self._show_selected()
        self._sync_ready_state()
        return GLib.SOURCE_REMOVE

    def _create_session(self, volume: RemovableVolume) -> DeviceSession:
        profile, loaded = self._load_device_profile(volume)
        session = DeviceSession(volume=volume, profile=profile)
        row = Adw.ActionRow(activatable=True)
        row.connect('activated', lambda *_: self._select_session(session))
        kind_icon = Gtk.Image()
        kind_icon.set_valign(Gtk.Align.CENTER)
        kind_icon.add_css_class('audio2go-device-kind')
        apply_icon(kind_icon, volume.icon_name)
        kind_icon.set_tooltip_text(
            'SD card' if volume.kind == 'sd' else 'USB drive'
        )
        row.add_prefix(kind_icon)
        session.kind_icon = kind_icon

        settings_btn = Gtk.Button()
        apply_icon(settings_btn, 'emblem-system-symbolic')
        settings_btn.set_valign(Gtk.Align.CENTER)
        settings_btn.set_tooltip_text('Device settings')
        settings_btn.add_css_class('flat')
        settings_btn.add_css_class('circular')
        settings_btn.add_css_class('audio2go-device-icon')
        settings_btn.connect('clicked', lambda *_: self._open_settings(session))

        eject_btn = Gtk.Button()
        apply_icon(eject_btn, 'media-eject-symbolic')
        eject_btn.set_valign(Gtk.Align.CENTER)
        eject_btn.set_tooltip_text('Eject')
        eject_btn.add_css_class('flat')
        eject_btn.add_css_class('circular')
        eject_btn.add_css_class('audio2go-device-icon')
        eject_btn.connect('clicked', lambda *_: self._eject_session(session))

        row.add_suffix(settings_btn)
        row.add_suffix(eject_btn)

        session.row = row
        session.settings_button = settings_btn
        session.eject_button = eject_btn
        self.device_group.add(row)
        self._apply_session_color(session)
        self._update_session_row(session)
        if loaded:
            name = profile.name.strip() or volume.name
            self._toast(f'Config loaded from {name}')
        return session

    def _select_session(self, session: DeviceSession) -> None:
        self._selected_key = _device_key(session.volume)
        self.store.update(
            device_uuid=session.volume.uuid or session.volume.path,
            device_name=session.profile.name,
            device_configured=True,
            destination_directory=music_destination(
                session.volume.path, session.profile
            ),
        )
        self._show_selected()
        self._sync_ready_state()

    def _show_selected(self) -> None:
        connected = bool(self._sessions)
        self.main_stack.set_visible_child(
            self.content_box if connected else self.empty_page
        )
        for key, session in self._sessions.items():
            if session.row is None:
                continue
            if key == self._selected_key:
                session.row.add_css_class('audio2go-device-selected')
            else:
                session.row.remove_css_class('audio2go-device-selected')
            self._apply_session_color(session)

    def _apply_session_color(self, session: DeviceSession) -> None:
        if session.row is None:
            return
        color = normalize_accent(session.profile.color)
        for key, _label, _hex in ACCENT_COLORS:
            cls = f'audio2go-accent-{key}'
            if key == color:
                session.row.add_css_class(cls)
            else:
                session.row.remove_css_class(cls)

    def _selected_session(self) -> DeviceSession | None:
        if not self._selected_key:
            return None
        return self._sessions.get(self._selected_key)

    def _load_device_profile(
        self, volume: RemovableVolume
    ) -> tuple[DeviceProfile, bool]:
        snap = self.store.get()
        return load_or_create_profile(
            uuid=volume.uuid,
            name=volume.name,
            mount=volume.path,
            seed=DeviceProfile(
                output_format=snap.output_format,
                mp3_quality=snap.mp3_quality,
                aac_quality=snap.aac_quality,
                opus_bitrate=snap.opus_bitrate,
                skip_existing=snap.skip_existing,
                copy_non_audio=snap.copy_non_audio,
                copy_lyrics=snap.copy_lyrics,
                copy_metadata=snap.copy_metadata,
                normalize_volume=snap.normalize_volume,
                replaygain=snap.replaygain,
                embed_artwork=snap.embed_artwork,
                artwork_max_size=snap.artwork_max_size,
            ),
        )

    def _settings_for_session(self, session: DeviceSession) -> AppSettings:
        snap = self.store.get()
        profile = session.profile
        dest = music_destination(session.volume.path, profile)
        return replace(
            snap,
            destination_directory=dest,
            device_name=profile.name,
            output_format=profile.output_format,
            mp3_quality=profile.mp3_quality,
            aac_quality=profile.aac_quality,
            opus_bitrate=profile.opus_bitrate,
            skip_existing=profile.skip_existing,
            copy_non_audio=profile.copy_non_audio,
            copy_lyrics=profile.copy_lyrics,
            copy_metadata=profile.copy_metadata,
            normalize_volume=profile.normalize_volume,
            replaygain=profile.replaygain,
            embed_artwork=profile.embed_artwork,
            artwork_max_size=profile.artwork_max_size,
        )

    def _session_ready(self, session: DeviceSession) -> bool:
        source = (self.store.get().source_directory or '').strip()
        dest = music_destination(session.volume.path, session.profile)
        return bool(source and dest) and not session.engine.busy

    def _update_session_row(self, session: DeviceSession) -> None:
        if session.row is None:
            return
        name = session.profile.name.strip() or session.volume.name
        dest = music_destination(session.volume.path, session.profile)
        session.row.set_title(name)
        busy = session.engine.busy
        session.row.set_subtitle('Syncing' if busy else _short_path(dest))
        if session.kind_icon is not None:
            apply_icon(session.kind_icon, session.volume.icon_name)
            session.kind_icon.set_tooltip_text(
                'SD card' if session.volume.kind == 'sd' else 'USB drive'
            )
        if session.eject_button is not None:
            session.eject_button.set_sensitive(not busy)
        if session.settings_button is not None:
            session.settings_button.set_sensitive(True)

    def _sync_ready_state(self) -> None:
        selected = self._selected_session()
        any_busy = any(item.engine.busy for item in self._sessions.values())
        sync = self.lookup_action('sync')
        if sync is not None:
            sync.set_enabled(selected is not None and self._session_ready(selected))
        self.sync_button.set_sensitive(
            selected is not None and self._session_ready(selected)
        )
        stop = self.lookup_action('stop')
        if stop is not None:
            stop.set_enabled(any_busy)
        self.stop_button.set_sensitive(any_busy)
        eject = self.lookup_action('eject')
        if eject is not None:
            eject.set_enabled(selected is not None and not selected.engine.busy)
        settings = self.lookup_action('device-settings')
        if settings is not None:
            settings.set_enabled(selected is not None)
        show_banner = any_busy or self._banner_hold
        self.sync_revealer.set_reveal_child(show_banner)
        self.toolbar_view.set_reveal_bottom_bars(show_banner)
        for session in self._sessions.values():
            self._update_session_row(session)

    def _on_device_settings(self, *_args) -> None:
        session = self._selected_session()
        if session is not None:
            self._open_settings(session)

    def _open_settings(self, session: DeviceSession) -> None:
        self._select_session(session)
        dialog = DeviceSetupDialog(
            self,
            volume=session.volume,
            profile=session.profile,
            on_changed=lambda current=session: self._on_session_settings_changed(
                current
            ),
            on_saved=lambda: self._toast('Config saved'),
        )
        self._setup_dialog = dialog
        dialog.connect(
            'closed', lambda *_: self._on_session_settings_changed(session)
        )
        dialog.present(self)

    def _on_session_settings_changed(self, session: DeviceSession) -> None:
        if self._setup_dialog is not None and self._setup_dialog.volume is not None:
            if _device_key(self._setup_dialog.volume) == _device_key(session.volume):
                session.profile = self._setup_dialog.profile
        self._apply_session_color(session)
        self._update_session_row(session)
        self._show_selected()
        self._sync_ready_state()

    def _on_choose_source(self, *_args) -> None:
        dialog = Gtk.FileDialog(title='Select music folder')
        current = self.store.get().source_directory
        if current and hasattr(dialog, 'set_initial_folder'):
            try:
                dialog.set_initial_folder(Gio.File.new_for_path(current))
            except GLib.Error:
                pass

        def on_done(dlg: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                folder = dlg.select_folder_finish(result)
            except GLib.Error:
                return
            if folder is None:
                return
            path = folder.get_path()
            if not path:
                return
            self.store.update(source_directory=path)
            self._refresh_library_row()
            self._sync_ready_state()

        dialog.select_folder(self, None, on_done)

    def _on_sync_selected(self, *_args) -> None:
        session = self._selected_session()
        if session is not None:
            self._start_sync(session)

    def _on_sync_all(self, *_args) -> None:
        started = 0
        for session in self._sessions.values():
            if self._session_ready(session) and self._start_sync(session):
                started += 1
        if not started and not self._sessions:
            self._toast('Plug in a device')
        elif not started:
            self._toast('Select a music folder to sync')

    def _on_stop(self, *_args) -> None:
        for session in self._sessions.values():
            if session.engine.busy:
                session.engine.cancel()
                self._update_session_row(session)
        self._sync_ready_state()

    def _on_eject_selected(self, *_args) -> None:
        session = self._selected_session()
        if session is not None:
            self._eject_session(session)

    def _start_sync(self, session: DeviceSession) -> bool:
        if session.engine.busy:
            return False
        snap = self._settings_for_session(session)
        dest = snap.destination_directory
        if not snap.source_directory.strip() or not dest:
            self._toast('Select a music folder')
            return False
        if snap.output_format != 'copy' and ffmpeg_bin() is None:
            self._toast('Install ffmpeg to transcode lossless files')
            return False
        if not save_profile(session.volume.path, session.profile):
            present_profile_save_failed(self)
            return False
        session.progress_fraction = 0.02
        self._banner_hold = False
        self._refresh_banner_progress()
        try:
            session.engine.start(
                snap,
                lambda progress, current=session: self._on_engine_progress(
                    current, progress
                ),
                lambda result, current=session: self._on_engine_done(
                    current, result
                ),
            )
        except Exception as exc:
            session.progress_fraction = 0.0
            self._refresh_banner_progress()
            self._toast(str(exc))
            return False
        self._sync_ready_state()
        return True

    def _eject_session(self, session: DeviceSession) -> None:
        if session.engine.busy:
            self._toast('Stop sync before ejecting')
            return
        path = session.volume.path
        if not path:
            return

        def done(ok: bool, message: str) -> bool:
            self._refresh_devices()
            self._toast(message if message else ('Ejected' if ok else 'Could not eject'))
            return GLib.SOURCE_REMOVE

        eject_path(path, done)

    def _any_busy(self) -> bool:
        return any(item.engine.busy for item in self._sessions.values())

    def _banner_fraction(self) -> float:
        active = [
            item.progress_fraction
            for item in self._sessions.values()
            if item.engine.busy or item.progress_fraction > 0
        ]
        if not active:
            return 0.0
        return sum(active) / len(active)

    def _refresh_banner_progress(self) -> None:
        if self._banner_hold:
            self._set_banner_target(1.0)
            return
        if self._any_busy():
            self._set_banner_target(max(0.02, self._banner_fraction()))
            return
        self._set_banner_target(0.0)

    def _set_banner_target(self, fraction: float) -> None:
        self._banner_target = max(0.0, min(1.0, fraction))
        self._ensure_banner_tick()

    def _ensure_banner_tick(self) -> None:
        if self._banner_tick_id is not None:
            return
        self._banner_last_us = 0
        self._banner_tick_id = self.add_tick_callback(self._on_banner_tick)

    def _on_banner_tick(self, _widget, clock) -> bool:
        frame = clock.get_frame_time()
        if self._banner_last_us == 0:
            self._banner_last_us = frame
            self._apply_banner_fill()
            return GLib.SOURCE_CONTINUE
        dt = min(0.05, (frame - self._banner_last_us) / 1_000_000)
        self._banner_last_us = frame
        t = 1.0 - math.exp(-dt / 0.16)
        self._banner_shown += (self._banner_target - self._banner_shown) * t
        if abs(self._banner_target - self._banner_shown) < 0.003:
            self._banner_shown = self._banner_target
            self._apply_banner_fill()
            self._banner_tick_id = None
            return GLib.SOURCE_REMOVE
        self._apply_banner_fill()
        return GLib.SOURCE_CONTINUE

    def _apply_banner_fill(self) -> None:
        self._banner_provider.load_from_string(banner_fill_css(self._banner_shown))

    def _on_engine_progress(
        self, session: DeviceSession, progress: SyncProgress
    ) -> None:
        GLib.idle_add(self._apply_progress, session, progress)

    def _apply_progress(
        self, session: DeviceSession, progress: SyncProgress
    ) -> bool:
        if progress.state == SyncState.SCANNING:
            session.progress_fraction = 0.04
        else:
            total = max(progress.total, 1)
            session.progress_fraction = (
                min(1.0, progress.current / total) if progress.total else 0.0
            )
        self._refresh_banner_progress()
        self._update_session_row(session)
        return GLib.SOURCE_REMOVE

    def _on_engine_done(self, session: DeviceSession, result: SyncResult) -> None:
        GLib.idle_add(self._finish_sync, session, result)

    def _finish_sync(self, session: DeviceSession, result: SyncResult) -> bool:
        if result.state == SyncState.DONE:
            session.progress_fraction = 1.0
            self._flash_success()
            if not self._any_busy():
                self._banner_hold = True
                GLib.timeout_add(560, self._release_banner)
        elif result.state == SyncState.ERROR:
            session.progress_fraction = 0.0
            self._toast(result.message or 'Sync failed')
        else:
            session.progress_fraction = 0.0
        self._refresh_banner_progress()
        self._update_session_row(session)
        self._sync_ready_state()
        return GLib.SOURCE_REMOVE

    def _release_banner(self) -> bool:
        if self._any_busy():
            self._banner_hold = False
            return GLib.SOURCE_REMOVE
        self._banner_hold = False
        self._sync_ready_state()
        GLib.timeout_add(220, self._reset_banner_fill)
        return GLib.SOURCE_REMOVE

    def _reset_banner_fill(self) -> bool:
        if self._any_busy() or self._banner_hold:
            return GLib.SOURCE_REMOVE
        for session in self._sessions.values():
            session.progress_fraction = 0.0
        self._banner_shown = 0.0
        self._banner_target = 0.0
        if self._banner_tick_id is not None:
            self.remove_tick_callback(self._banner_tick_id)
            self._banner_tick_id = None
        self._apply_banner_fill()
        return GLib.SOURCE_REMOVE

    def _flash_success(self) -> None:
        if self._flash_id is not None:
            GLib.source_remove(self._flash_id)
        self.flash_veil.add_css_class('visible')
        self._flash_id = GLib.timeout_add(350, self._hide_flash)

    def _hide_flash(self) -> bool:
        self._flash_id = None
        self.flash_veil.remove_css_class('visible')
        return GLib.SOURCE_REMOVE

    def _on_map(self, *_args) -> None:
        self._ensure_toast_flush()

    def _toast(self, message: str) -> None:
        self._pending_toasts.append(message)
        self._ensure_toast_flush()

    def _ensure_toast_flush(self) -> None:
        if self._toast_flush_id is not None:
            return
        self._toast_flush_id = GLib.timeout_add(50, self._flush_pending_toasts)

    def _flush_pending_toasts(self) -> bool:
        if not self.toast_overlay.get_mapped():
            return GLib.SOURCE_CONTINUE
        self._toast_flush_id = None
        messages = self._pending_toasts
        self._pending_toasts = []
        for message in messages:
            self._present_toast(message)
        return GLib.SOURCE_REMOVE

    def _present_toast(self, message: str) -> None:
        toast = status_toast(message)
        self.toast_overlay.add_toast(toast)
        seconds = int(toast.get_timeout() or 5)
        if seconds > 0:
            GLib.timeout_add_seconds(seconds, self._dismiss_toast, toast)

    def _dismiss_toast(self, toast: Adw.Toast) -> bool:
        toast.dismiss()
        return GLib.SOURCE_REMOVE

    def _on_close_request(self, *_args) -> bool:
        if self._toast_flush_id is not None:
            GLib.source_remove(self._toast_flush_id)
            self._toast_flush_id = None
        for session in self._sessions.values():
            if session.engine.busy:
                session.engine.cancel()
        return False
