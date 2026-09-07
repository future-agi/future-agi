"""
conftest.py for agentic_eval tests.

Bootstraps Django before pytest begins collecting test modules.
This must run before any agentic_eval imports so that Django ORM
is ready when test module-level imports execute.
"""

import os
import sys


def pytest_configure(config):
    """Set up Django before test collection begins."""
    # Add the backend root to sys.path so 'tfc.settings' is importable
    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)

    # tfc.settings is a package whose __init__.py is a one-line stub — the real
    # settings live in tfc.settings.settings, which manage.py, asgi.py, wsgi.py
    # and celery.py all use. Pointing Django at the package configured an empty
    # settings object, so INSTALLED_APPS was empty and importing any app model
    # raised "doesn't declare an explicit app_label". The blanket `except
    # Exception: pass` below then swallowed it, and collection failed later at
    # the import site instead.
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")

    try:
        import django

        django.setup()
    except RuntimeError:
        pass  # Already set up (e.g. running under the root conftest)
    except Exception:
        # Outside the Docker environment Django settings may be unavailable.
        # Tests that need Django will fail with a clear import error; tests
        # that only mock at the unit level will still pass.
        pass
