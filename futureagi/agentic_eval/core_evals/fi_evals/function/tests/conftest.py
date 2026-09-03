"""Ensure Django is configured before importing the evaluator functions.

``functions.py``'s import chain reaches Django models, so the app registry must
be populated before collection. When these tests run inside the backend's own
settings (Docker, ``bin/test``), Django is already configured and this module
does nothing. Standalone, it configures the minimum app set those imports need,
so the deterministic evaluators stay unit-testable without the full stack.
"""

import sys
from pathlib import Path

# futureagi/ -- the backend root, so that `agentic_eval` and `tfc` are importable.
_BACKEND_ROOT = Path(__file__).resolve().parents[5]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import django  # noqa: E402
from django.conf import settings  # noqa: E402

if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={},
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "accounts",
            "model_hub",
            "tracer",
        ],
        REST_FRAMEWORK={},
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()
