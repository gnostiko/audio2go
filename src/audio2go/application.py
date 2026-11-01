# SPDX-License-Identifier: GPL-3.0-or-later
"""Adwaita Application subclass."""

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk

from audio2go import config
from audio2go.icons import register_application_icons
from audio2go.settings import SettingsStore
from audio2go.shortcuts import present_keyboard_shortcuts
from audio2go.theme import apply_color_scheme


class Application(Adw.Application):
    """Main application object.

    Use ``startup`` / ``activate`` signal handlers instead of ``do_startup`` /
    ``do_activate`` vfuncs. Overriding those vfuncs segfaults during
    ``g_object_new`` on some AppImage PyGObject + bundled GLib stacks.
    """

    def __init__(self) -> None:
        self._store: SettingsStore | None = None
        self._startup_complete = False
        super().__init__(
            application_id=config.APPLICATION_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.connect_after('startup', self._on_startup)
        self.connect('activate', self._on_activate)

    @property
    def store(self) -> SettingsStore:
        if self._store is None:
            self._store = SettingsStore()
        return self._store

    def _on_startup(self, *_args) -> None:
        from audio2go.window import ensure_adw_template_types

        ensure_adw_template_types()
        self._startup_complete = True
        GLib.set_application_name(config.APPLICATION_NAME)
        GLib.set_prgname(config.APPLICATION_ID)
        apply_color_scheme(self.store.get().color_scheme)
        try:
            register_application_icons()
        except Exception:
            pass
        self._setup_actions()
        self.set_menubar(None)

    def _on_activate(self, *_args) -> None:
        if not self._startup_complete:
            return
        from audio2go.window import Audio2GoWindow

        win = self.props.active_window
        if not win:
            win = Audio2GoWindow(application=self, store=self.store)
            win.set_icon_name(config.APPLICATION_ID)
        win.present()

    def _setup_actions(self) -> None:
        self.create_action('quit', lambda *_: self.quit(), ['<primary>q'])
        self.create_action('about', self.on_about)
        self.create_action(
            'shortcuts',
            self.on_shortcuts,
            ['<primary>question', '<shift><primary>slash'],
        )

        self.set_accels_for_action('win.close', ['<primary>w'])
        self.set_accels_for_action('win.menu', ['F10'])
        self.set_accels_for_action('win.sync', ['<primary>s'])
        self.set_accels_for_action('win.sync-all', ['<shift><primary>s'])
        self.set_accels_for_action('win.stop', ['Escape'])
        self.set_accels_for_action('win.eject', ['<primary>e'])
        self.set_accels_for_action('win.device-settings', ['<primary>d'])

    def build_primary_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        menu.append('Keyboard shortcuts', 'app.shortcuts')
        menu.append(f'About {config.APPLICATION_NAME}', 'app.about')
        return menu

    def on_shortcuts(self, *_args) -> None:
        present_keyboard_shortcuts(self.props.active_window)

    def on_about(self, *_args) -> None:
        about = Adw.AboutDialog(
            application_name=config.APPLICATION_NAME,
            application_icon=config.APPLICATION_ID,
            developer_name='gnostiko',
            version=config.APPLICATION_VERSION,
            developers=['gnostiko https://github.com/gnostiko'],
            copyright='© 2026 gnostiko',
            license_type=Gtk.License.GPL_3_0,
            comments=(
                'Sync a music library to USB drives, SD cards, and portable players. '
                'Copy files as-is or transcode lossless tracks to MP3, AAC, or Opus.'
            ),
        )
        about.present(self.props.active_window)

    def create_action(
        self, name: str, callback, shortcuts: list[str] | None = None
    ) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect('activate', callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f'app.{name}', shortcuts)
