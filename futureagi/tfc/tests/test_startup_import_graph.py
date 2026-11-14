"""Heavy libraries must stay off the API/worker startup path.

Each of these is only needed by a few code paths and is imported there,
lazily. Importing one at module level again puts ~10-80 MB into every Django
process (nltk alone drags in scipy and scikit-learn), which the default
single-process install pays on every boot. Runs in a fresh interpreter because
the test session itself imports much more than a server does.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

LAZY_MODULES = (
    "docx",
    "langchain",
    "langchain_community",
    "langfuse",
    "nltk",
    "pypdf",
    "rouge_score",
    "saml2",
    "scipy",
    "sklearn",
)

_PROBE = f"""
import json, sys
import django

lazy = {LAZY_MODULES!r}
django.setup()
after_setup = sorted(m for m in lazy if m in sys.modules)
import tfc.asgi  # noqa: E402,F401  (URL graph and every view module)
after_asgi = sorted(m for m in lazy if m in sys.modules)
print("IMPORT_GRAPH=" + json.dumps({{"setup": after_setup, "asgi": after_asgi}}))
"""


@pytest.mark.slow
def test_heavy_optional_libraries_are_not_imported_at_startup():
    backend_root = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "tfc.settings.test",
        "OTEL_ENABLED": "false",
    }
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=backend_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    marker = next(
        line
        for line in reversed(result.stdout.splitlines())
        if line.startswith("IMPORT_GRAPH=")
    )
    loaded = json.loads(marker.removeprefix("IMPORT_GRAPH="))

    assert loaded == {"setup": [], "asgi": []}
