# SPDX-License-Identifier: GPL-3.0-or-later
"""Application CSS for the main window."""

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gtk

from audio2go.accents import ACCENT_COLORS

# Adwaita ease-out cubic.
_EASE = 'cubic-bezier(0.33, 1, 0.68, 1)'


def window_css() -> str:
    """Compact app CSS aligned with Adwaita motion and color."""
    swatches = '\n'.join(
        f'button.audio2go-color-{key} {{ background-color: {hex_color}; }}'
        for key, _label, hex_color in ACCENT_COLORS
    )
    accents = '\n'.join(
        f"""
row.audio2go-accent-{key},
row.audio2go-accent-{key}:hover,
row.audio2go-accent-{key}:active,
row.audio2go-accent-{key}:selected {{
    background-image: none;
    background-color: alpha({hex_color}, 0.32);
}}
row.audio2go-accent-{key}.audio2go-device-selected,
row.audio2go-accent-{key}.audio2go-device-selected:hover,
row.audio2go-accent-{key}.audio2go-device-selected:active,
row.audio2go-accent-{key}.audio2go-device-selected:selected {{
    background-image: none;
    background-color: alpha({hex_color}, 0.44);
}}
"""
        for key, _label, hex_color in ACCENT_COLORS
    )
    return f"""
button.audio2go-device-icon,
button.audio2go-stop {{
    min-width: 32px;
    min-height: 32px;
    padding: 0;
}}
button.audio2go-sync {{
    min-width: 112px;
    min-height: 40px;
    padding-left: 12px;
    padding-right: 12px;
}}

.audio2go-sync-bar {{
    background-color: @window_bg_color;
    min-height: 48px;
}}

.audio2go-path-subtitle {{
    font-feature-settings: "tnum";
}}
.audio2go-color-row {{
    padding-top: 8px;
    padding-bottom: 8px;
    padding-left: 4px;
    padding-right: 4px;
}}

image.audio2go-device-kind {{
    min-width: 16px;
    min-height: 16px;
    color: @window_fg_color;
}}

button.audio2go-color {{
    min-width: 24px;
    min-height: 24px;
    padding: 0;
    border: none;
    background-image: none;
    border-radius: 9999px;
    box-shadow: inset 0 0 0 1px alpha(@window_fg_color, 0.18);
    transition: box-shadow 150ms {_EASE};
}}
button.audio2go-color:hover {{
    box-shadow: inset 0 0 0 1px alpha(@window_fg_color, 0.36);
}}
button.audio2go-color:checked {{
    box-shadow:
        0 0 0 2px @window_bg_color,
        0 0 0 4px @window_fg_color;
}}
{swatches}
{accents}

.audio2go-flash-veil {{
    background-color: @success_bg_color;
    opacity: 0;
    transition: opacity 200ms {_EASE};
}}
.audio2go-flash-veil.visible {{
    opacity: 0.34;
}}
"""


def banner_fill_css(fraction: float) -> str:
    """GNOME success-green fill for the Syncing music banner."""
    percent = max(0.0, min(100.0, fraction * 100.0))
    if percent <= 0.05:
        return '.audio2go-sync-bar { background-image: none; }'
    edge = max(0.0, percent - 2.5)
    return f"""
.audio2go-sync-bar {{
    background-image: linear-gradient(
        to right,
        alpha(@success_bg_color, 0.72) 0%,
        alpha(@success_bg_color, 0.72) {edge:.1f}%,
        alpha(@success_bg_color, 0.28) {percent:.1f}%,
        transparent {percent:.1f}%
    );
}}
"""


_window_provider: Gtk.CssProvider | None = None


def install_window_styles(display: Gtk.Display) -> Gtk.CssProvider:
    """Install application-priority CSS for *display* (once per process)."""
    global _window_provider
    css = window_css()
    if _window_provider is not None:
        return _window_provider
    provider = Gtk.CssProvider()
    provider.load_from_string(css)
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _window_provider = provider
    return provider
