# SPDX-License-Identifier: GPL-3.0-or-later
"""Application entry point."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    from audio2go.application import Application

    app = Application()
    return app.run(argv if argv is not None else sys.argv)


if __name__ == '__main__':
    raise SystemExit(main())
