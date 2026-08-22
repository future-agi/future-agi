"""Launcher shim: the app lives in the package at harness.ui.app.

Kept so `python ui/server.py` (the Dockerfile CMD and every runbook) keeps working
from a bare checkout, where src/ is not yet on the path.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from harness.ui.app import main  # noqa: E402

if __name__ == "__main__":
    main()
