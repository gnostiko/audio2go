# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-device settings dialog."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields
from pathlib import Path

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk

from audio2go.accents import ACCENT_COLORS, normalize_accent
from audio2go.device import RemovableVolume
from audio2go.device_profile import (
    DeviceProfile,
    music_destination,
    relative_to_mount,
    save_profile,
)
from audio2go.icons import apply_icon
from audio2go.settings import (
    AAC_QUALITIES,
    ARTWORK_SIZES,
    MP3_QUALITIES,
    OPUS_BITRATES,
    OUTPUT_FORMATS,
    normalize_aac_quality,
    normalize_artwork_max_size,
    normalize_mp3_quality,
    normalize_opus_bitrate,
    normalize_output_format,
    opus_bitrate_label,
)


def _folder_label(relative: str) -> str:
    rel = (relative or '').strip()
    return rel if rel else 'Drive root'


# libadwaita default; toasts are transient and user-dismissible (GNOME HIG).
_TOAST_TIMEOUT_SECONDS = 5


def status_toast(message: str) -> Adw.Toast:
    toast = Adw.Toast.new(message)
    toast.set_timeout(_TOAST_TIMEOUT_SECONDS)
    return toast


def present_profile_save_failed(parent: Gtk.Widget | None) -> None:
    dialog = Adw.AlertDialog(
        heading='Unable to save',
        body=(
            'The configuration file could not be written to this device. '
            'The device may need to be formatted.'
        ),
    )
    dialog.add_response('ok', 'OK')
    dialog.set_default_response('ok')
    dialog.set_close_response('ok')
    dialog.present(parent)


class DeviceSettings(Adw.PreferencesPage):
    """Per-device conversion and copy preferences."""

    def __init__(
        self,
        parent: Gtk.Window | None = None,
        *,
        on_changed: Callable[[], None] | None = None,
        on_saved: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._parent = parent
        self._on_changed = on_changed
        self._on_saved = on_saved
        self._saved_notice_id: int | None = None
        self._suppress = False
        self._save_error_shown = False
        self._volume: RemovableVolume | None = None
        self._profile = DeviceProfile()
        self._format_ids = [fmt for fmt, _label in OUTPUT_FORMATS]
        self._mp3_qualities = [qid for qid, _label in MP3_QUALITIES]
        self._aac_qualities = [qid for qid, _label in AAC_QUALITIES]
        self._opus_values = list(OPUS_BITRATES)
        self._art_size_values = [px for px, _label in ARTWORK_SIZES]

        self.add(self._build_device_group())
        self.add(self._build_conversion_group())
        self.add(self._build_files_group())
        self.set_sensitive(False)

    @property
    def profile(self) -> DeviceProfile:
        return self._profile

    @property
    def volume(self) -> RemovableVolume | None:
        return self._volume

    def bind(self, volume: RemovableVolume | None, profile: DeviceProfile | None) -> None:
        self._volume = volume
        self._profile = profile or DeviceProfile()
        self._save_error_shown = False
        self.set_sensitive(volume is not None and profile is not None)
        if volume is None or profile is None:
            return
        self._apply_profile_to_rows()

    def _mount(self) -> str:
        return self._volume.path if self._volume is not None else ''

    def _build_device_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        self._name_row = Adw.EntryRow(title='Name')
        self._name_row.connect('notify::text', self._on_name_changed)
        group.add(self._name_row)

        self._folder_row = Adw.ActionRow(
            title='Folder',
            subtitle='Drive root',
            activatable=True,
        )
        apply_icon(self._folder_row, 'folder-symbolic')
        folder_btn = Gtk.Button()
        apply_icon(folder_btn, 'folder-open-symbolic')
        folder_btn.set_valign(Gtk.Align.CENTER)
        folder_btn.set_tooltip_text('Select folder on device')
        folder_btn.add_css_class('flat')
        folder_btn.connect('clicked', self._on_choose_device_folder)
        self._folder_row.add_suffix(folder_btn)
        self._folder_row.connect('activated', self._on_choose_device_folder)
        group.add(self._folder_row)

        self._color_row = Gtk.ListBoxRow()
        self._color_row.set_activatable(False)
        self._color_row.set_selectable(False)
        self._color_row.add_css_class('audio2go-color-row')
        colors = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        colors.set_halign(Gtk.Align.FILL)
        colors.set_valign(Gtk.Align.CENTER)
        colors.set_hexpand(True)
        self._color_buttons: dict[str, Gtk.ToggleButton] = {}
        for key, label, _hex in ACCENT_COLORS:
            button = Gtk.ToggleButton()
            button.set_tooltip_text(label)
            button.add_css_class('circular')
            button.add_css_class('audio2go-color')
            button.add_css_class(f'audio2go-color-{key}')
            button.set_halign(Gtk.Align.CENTER)
            button.set_valign(Gtk.Align.CENTER)
            button.set_hexpand(False)
            button.connect('toggled', self._on_color_toggled, key)
            cell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            cell.set_hexpand(True)
            cell.set_halign(Gtk.Align.FILL)
            cell.set_valign(Gtk.Align.CENTER)
            cell.append(button)
            self._color_buttons[key] = button
            colors.append(cell)
        self._color_row.set_child(colors)
        group.add(self._color_row)
        return group

    def _build_conversion_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title='Conversion')
        self._format_row = Adw.ComboRow(title='Format')
        self._format_row.set_model(
            Gtk.StringList.new([label for _fmt, label in OUTPUT_FORMATS])
        )
        self._format_row.connect('notify::selected', self._on_format_changed)
        group.add(self._format_row)

        self._quality_row = Adw.ComboRow(title='Quality')
        self._quality_row.connect('notify::selected', self._on_quality_changed)
        group.add(self._quality_row)

        self._normalize_row = Adw.SwitchRow(title='Normalize volume')
        self._normalize_row.connect('notify::active', self._on_normalize_changed)
        group.add(self._normalize_row)

        self._replaygain_row = Adw.SwitchRow(title='Calculate ReplayGain')
        self._replaygain_row.connect('notify::active', self._on_replaygain_changed)
        group.add(self._replaygain_row)

        self._copy_metadata_row = Adw.SwitchRow(title='Copy tags')
        self._copy_metadata_row.connect(
            'notify::active', self._on_copy_metadata_changed
        )
        group.add(self._copy_metadata_row)

        self._embed_art_row = Adw.SwitchRow(title='Embed cover art')
        self._embed_art_row.connect('notify::active', self._on_embed_art_changed)
        group.add(self._embed_art_row)

        self._art_size_row = Adw.ComboRow(title='Cover size')
        self._art_size_row.set_model(
            Gtk.StringList.new([label for _px, label in ARTWORK_SIZES])
        )
        self._art_size_row.connect('notify::selected', self._on_art_size_changed)
        group.add(self._art_size_row)
        return group

    def _build_files_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title='Files')
        self._skip_row = Adw.SwitchRow(title='Skip existing files')
        self._skip_row.connect('notify::active', self._on_skip_changed)
        group.add(self._skip_row)

        self._copy_lyrics_row = Adw.SwitchRow(title='Copy lyric files')
        self._copy_lyrics_row.connect('notify::active', self._on_copy_lyrics_changed)
        group.add(self._copy_lyrics_row)

        self._copy_non_audio_row = Adw.SwitchRow(title='Copy other files')
        self._copy_non_audio_row.connect(
            'notify::active', self._on_copy_non_audio_changed
        )
        group.add(self._copy_non_audio_row)
        return group

    def _changed(self) -> None:
        if self._on_changed is not None:
            self._on_changed()

    def flush(self, *, warn: bool = True, notify: bool = True) -> bool:
        """Write the current device profile to the drive."""
        return self._persist(warn=warn, notify=notify)

    def _persist(self, *, warn: bool = True, notify: bool = True) -> bool:
        mount = self._mount()
        if not mount:
            return False
        cleaned = self._profile.normalized()
        for item in fields(cleaned):
            setattr(self._profile, item.name, getattr(cleaned, item.name))
        ok = save_profile(mount, self._profile)
        self._changed()
        if ok:
            self._save_error_shown = False
            if notify:
                self._schedule_saved_notice()
            return True
        if warn:
            self.warn_save_failed()
        return False

    def cancel_saved_notice(self) -> None:
        if self._saved_notice_id is None:
            return
        GLib.source_remove(self._saved_notice_id)
        self._saved_notice_id = None

    def _schedule_saved_notice(self) -> None:
        self.cancel_saved_notice()
        self._saved_notice_id = GLib.timeout_add(450, self._emit_saved_notice)

    def _emit_saved_notice(self) -> bool:
        self._saved_notice_id = None
        if self._on_saved is not None:
            self._on_saved()
        return GLib.SOURCE_REMOVE

    def warn_save_failed(self, *, force: bool = False) -> None:
        if self._save_error_shown and not force:
            return
        self._save_error_shown = True
        parent = self.get_root()
        if parent is None:
            parent = self._parent
        present_profile_save_failed(parent)

    def _apply_profile_to_rows(self) -> None:
        was = self._suppress
        self._suppress = True
        try:
            self._fill_profile_rows()
        finally:
            self._suppress = was

    def _fill_profile_rows(self) -> None:
        self._profile = self._profile.normalized()
        self._name_row.set_text(self._profile.name)
        self._folder_row.set_subtitle(_folder_label(self._profile.music_folder))
        fmt = normalize_output_format(self._profile.output_format)
        try:
            self._format_row.set_selected(self._format_ids.index(fmt))
        except ValueError:
            self._format_row.set_selected(0)
        self._skip_row.set_active(bool(self._profile.skip_existing))
        self._copy_non_audio_row.set_active(bool(self._profile.copy_non_audio))
        self._copy_lyrics_row.set_active(bool(self._profile.copy_lyrics))
        self._embed_art_row.set_active(bool(self._profile.embed_artwork))
        size = normalize_artwork_max_size(self._profile.artwork_max_size)
        try:
            self._art_size_row.set_selected(self._art_size_values.index(size))
        except ValueError:
            self._art_size_row.set_selected(self._art_size_values.index(600))
        self._art_size_row.set_sensitive(bool(self._profile.embed_artwork))
        self._normalize_row.set_active(bool(self._profile.normalize_volume))
        self._replaygain_row.set_active(bool(self._profile.replaygain))
        self._copy_metadata_row.set_active(bool(self._profile.copy_metadata))
        self._sync_color_buttons()
        self._sync_conversion_ui()

    def _current_format(self) -> str:
        index = int(self._format_row.get_selected())
        if 0 <= index < len(self._format_ids):
            return self._format_ids[index]
        return 'copy'

    def _sync_conversion_ui(self) -> None:
        fmt = self._current_format()
        converting = fmt != 'copy'
        self._quality_row.set_visible(converting)
        self._normalize_row.set_visible(converting)
        self._copy_metadata_row.set_visible(converting)
        if not converting:
            return
        was_suppress = self._suppress
        self._suppress = True
        try:
            self._fill_quality_row(fmt)
        finally:
            self._suppress = was_suppress

    def _fill_quality_row(self, fmt: str) -> None:
        if fmt == 'mp3':
            labels = [label for _qid, label in MP3_QUALITIES]
            values = self._mp3_qualities
            current = normalize_mp3_quality(self._profile.mp3_quality)
            self._quality_row.set_title('MP3')
        elif fmt == 'aac':
            labels = [label for _qid, label in AAC_QUALITIES]
            values = self._aac_qualities
            current = normalize_aac_quality(self._profile.aac_quality)
            self._quality_row.set_title('AAC')
        else:
            labels = [opus_bitrate_label(v) for v in OPUS_BITRATES]
            values = self._opus_values
            current = normalize_opus_bitrate(self._profile.opus_bitrate)
            self._quality_row.set_title('Opus')
        self._quality_row.set_model(Gtk.StringList.new(labels))
        try:
            self._quality_row.set_selected(values.index(current))
        except ValueError:
            self._quality_row.set_selected(0)

    def _on_choose_device_folder(self, *_args) -> None:
        mount = self._mount()
        if not mount:
            return
        dialog = Gtk.FileDialog(title='Select folder on device')
        current = music_destination(mount, self._profile)
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
            try:
                Path(path).resolve().relative_to(Path(mount).resolve())
            except (OSError, ValueError):
                return
            rel = relative_to_mount(mount, path)
            self._profile.music_folder = rel
            self._folder_row.set_subtitle(_folder_label(rel))
            self._persist()

        parent = self._parent if isinstance(self._parent, Gtk.Window) else None
        dialog.select_folder(parent, None, on_done)

    def _sync_color_buttons(self) -> None:
        current = normalize_accent(self._profile.color)
        was = self._suppress
        self._suppress = True
        try:
            for key, button in self._color_buttons.items():
                button.set_active(key == current)
        finally:
            self._suppress = was

    def _on_color_toggled(self, button: Gtk.ToggleButton, color_id: str) -> None:
        if self._suppress:
            return
        if not button.get_active():
            if normalize_accent(self._profile.color) == color_id:
                button.set_active(True)
            return
        self._suppress = True
        for key, other in self._color_buttons.items():
            if key != color_id:
                other.set_active(False)
        self._suppress = False
        self._profile.color = color_id
        self._persist()

    def _on_name_changed(self, *_args) -> None:
        if self._suppress:
            return
        self._profile.name = self._name_row.get_text().strip()
        self._persist()

    def _on_format_changed(self, *_args) -> None:
        if self._suppress:
            return
        index = int(self._format_row.get_selected())
        if 0 <= index < len(self._format_ids):
            self._profile.output_format = self._format_ids[index]
            self._sync_conversion_ui()
            self._persist()

    def _on_skip_changed(self, *_args) -> None:
        if self._suppress:
            return
        self._profile.skip_existing = bool(self._skip_row.get_active())
        self._persist()

    def _on_copy_non_audio_changed(self, *_args) -> None:
        if self._suppress:
            return
        self._profile.copy_non_audio = bool(self._copy_non_audio_row.get_active())
        self._persist()

    def _on_copy_lyrics_changed(self, *_args) -> None:
        if self._suppress:
            return
        self._profile.copy_lyrics = bool(self._copy_lyrics_row.get_active())
        self._persist()

    def _on_embed_art_changed(self, *_args) -> None:
        if self._suppress:
            return
        enabled = bool(self._embed_art_row.get_active())
        self._profile.embed_artwork = enabled
        self._art_size_row.set_sensitive(enabled)
        self._persist()

    def _on_art_size_changed(self, *_args) -> None:
        if self._suppress:
            return
        index = int(self._art_size_row.get_selected())
        if 0 <= index < len(self._art_size_values):
            self._profile.artwork_max_size = self._art_size_values[index]
            self._persist()

    def _on_quality_changed(self, *_args) -> None:
        if self._suppress:
            return
        fmt = self._current_format()
        index = int(self._quality_row.get_selected())
        if fmt == 'mp3' and 0 <= index < len(self._mp3_qualities):
            self._profile.mp3_quality = self._mp3_qualities[index]
        elif fmt == 'aac' and 0 <= index < len(self._aac_qualities):
            self._profile.aac_quality = self._aac_qualities[index]
        elif fmt == 'opus' and 0 <= index < len(self._opus_values):
            self._profile.opus_bitrate = self._opus_values[index]
        else:
            return
        self._persist()

    def _on_normalize_changed(self, *_args) -> None:
        if self._suppress:
            return
        self._profile.normalize_volume = bool(self._normalize_row.get_active())
        self._persist()

    def _on_replaygain_changed(self, *_args) -> None:
        if self._suppress:
            return
        self._profile.replaygain = bool(self._replaygain_row.get_active())
        self._persist()

    def _on_copy_metadata_changed(self, *_args) -> None:
        if self._suppress:
            return
        self._profile.copy_metadata = bool(self._copy_metadata_row.get_active())
        self._persist()


class DeviceSetupDialog(Adw.Dialog):
    """Device settings in a dialog opened from the device list."""

    def __init__(
        self,
        parent: Gtk.Window | None = None,
        *,
        volume: RemovableVolume | None = None,
        profile: DeviceProfile | None = None,
        on_changed: Callable[[], None] | None = None,
        on_saved: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.set_title('Device')
        self.set_content_width(440)
        self.set_content_height(440)
        self._on_saved = on_saved
        self._saved_toast: Adw.Toast | None = None
        self._settings = DeviceSettings(
            parent, on_changed=on_changed, on_saved=self._toast_saved
        )
        self._settings.bind(volume, profile)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        save = Gtk.Button(label='Save')
        save.add_css_class('suggested-action')
        save.connect('clicked', self._on_save)
        header.pack_end(save)
        toolbar.add_top_bar(header)
        toolbar.set_content(self._settings)
        overlay = Adw.ToastOverlay()
        overlay.set_child(toolbar)
        self._toast_overlay = overlay
        self.set_child(overlay)
        self.connect('closed', self._on_closed)

    def _toast_saved(self) -> None:
        if self._saved_toast is not None:
            self._saved_toast.dismiss()
        toast = status_toast('Config saved')
        self._saved_toast = toast
        toast.connect('dismissed', self._on_saved_toast_dismissed)
        self._toast_overlay.add_toast(toast)
        seconds = int(toast.get_timeout() or 5)
        if seconds > 0:
            GLib.timeout_add_seconds(seconds, self._dismiss_saved_toast, toast)

    def _dismiss_saved_toast(self, toast: Adw.Toast) -> bool:
        toast.dismiss()
        return GLib.SOURCE_REMOVE

    def _on_saved_toast_dismissed(self, toast: Adw.Toast) -> None:
        if self._saved_toast is toast:
            self._saved_toast = None

    def _on_closed(self, *_args) -> None:
        self._settings.cancel_saved_notice()
        self._settings.flush(warn=True, notify=False)

    def _on_save(self, *_args) -> None:
        if self._settings.flush(warn=False, notify=False):
            self._settings.cancel_saved_notice()
            if self._on_saved is not None:
                self._on_saved()
            self.close()
        else:
            self._settings.warn_save_failed(force=True)

    @property
    def profile(self) -> DeviceProfile:
        return self._settings.profile

    @property
    def volume(self) -> RemovableVolume | None:
        return self._settings.volume
