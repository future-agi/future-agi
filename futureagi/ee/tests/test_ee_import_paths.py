"""EE subpackages are only importable through the ``ee.`` prefix.

They were top-level apps (``usage``, ``licensing``, ...) before moving under
futureagi/ee/. A leftover bare import raises ModuleNotFoundError only when its
code path runs, and most of those paths sit inside ``try/except Exception``,
so the failure is silent at runtime.
"""

import re
from pathlib import Path

EE_ROOT = Path(__file__).resolve().parents[1]

EXCLUDED_DIRS = (
    # Private cloud overlay, checked by its own repository.
    EE_ROOT / "cloud",
    # Standalone live-LLM example scripts run with ee/ as the working directory.
    EE_ROOT / "experiments" / "src" / "playground",
)


def _ee_subpackages():
    return sorted(
        path.name
        for path in EE_ROOT.iterdir()
        if path.is_dir()
        and not path.name.startswith("_")
        and not (EE_ROOT.parent / path.name).exists()
    )


def test_ee_modules_do_not_import_subpackages_without_ee_prefix():
    bare_import = re.compile(
        rf"^\s*(?:from|import)\s+(?:{'|'.join(_ee_subpackages())})(?:\.|\s|$)"
    )
    offenders = []
    for path in EE_ROOT.rglob("*.py"):
        if any(path.is_relative_to(excluded) for excluded in EXCLUDED_DIRS):
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if bare_import.match(line):
                offenders.append(
                    f"{path.relative_to(EE_ROOT)}:{lineno}: {line.strip()}"
                )

    assert offenders == []
