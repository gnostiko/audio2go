# SPDX-License-Identifier: GPL-3.0-or-later
"""GNOME palette hues for per-device row colors."""

from __future__ import annotations

# GNOME palette hues for per-device colors, including extra stops so the
# picker can fill the settings row.
ACCENT_COLORS: tuple[tuple[str, str, str], ...] = (
    ('blue', 'Blue', '#3584e4'),
    ('indigo', 'Indigo', '#1a5fb4'),
    ('teal', 'Teal', '#2190a4'),
    ('mint', 'Mint', '#26a269'),
    ('green', 'Green', '#3a944a'),
    ('gold', 'Gold', '#f5c211'),
    ('yellow', 'Yellow', '#c88800'),
    ('orange', 'Orange', '#ed5b00'),
    ('red', 'Red', '#c01c28'),
    ('pink', 'Pink', '#d56199'),
    ('magenta', 'Magenta', '#c061cb'),
    ('purple', 'Purple', '#9141ac'),
    ('brown', 'Brown', '#986a44'),
    ('slate', 'Slate', '#6f8396'),
)
ACCENT_IDS = frozenset(item[0] for item in ACCENT_COLORS)
DEFAULT_ACCENT = 'blue'


def normalize_accent(value: str | None) -> str:
    raw = (value or '').strip().lower()
    if raw in ACCENT_IDS:
        return raw
    return DEFAULT_ACCENT
