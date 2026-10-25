"""Match the backend image's import root and working directory."""

import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3] / "futureagi"


def enter_backend_root():
    if not (BACKEND_ROOT / "manage.py").is_file():
        raise RuntimeError("acceptance requires the complete backend source checkout")
    backend = str(BACKEND_ROOT)
    sys.path[:] = [backend, *(path for path in sys.path if path != backend)]
    os.chdir(BACKEND_ROOT)


def initialize_runtime(fixture_directory):
    # Resolve caller-relative fixture/compose/evidence paths BEFORE changing cwd.
    # Existing data migrations intentionally open assets relative to manage.py.
    fixture_directory = Path(fixture_directory).resolve()
    enter_backend_root()
    return fixture_directory
