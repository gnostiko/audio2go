# SPDX-License-Identifier: GPL-3.0-or-later
"""Allow ``python3 -m audio2go`` from a PYTHONPATH that includes ``src``."""

from audio2go.main import main

raise SystemExit(main())
