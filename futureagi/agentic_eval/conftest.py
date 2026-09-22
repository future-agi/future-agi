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

    # "tfc.settings" resolves to the package's near-empty __init__.py, not
    # the real settings module: INSTALLED_APPS silently falls back to
    # Django's global default ([]), so every custom app (including
    # "accounts") is unregistered and any model class defined during import
    # (e.g. accounts.models.audit_log.AuditLog) raises "doesn't declare an
    # explicit app_label and isn't in an application in INSTALLED_APPS."
    # The root suite already uses the real module for this; match it.
    #
    # This has to stay here rather than move to pytest.ini's
    # DJANGO_SETTINGS_MODULE (or an exported env var), even though that is
    # the idiomatic pytest-django spot. Setting it before pytest starts
    # makes pytest-django configure Django during plugin init, and
    # tfc.settings.test's LOGGING binds a logging.StreamHandler to whatever
    # sys.stderr is at that moment — pytest's capture stream. When capture
    # is torn down the handler keeps writing to a closed file and the run
    # dies at interpreter shutdown with "ValueError: I/O operation on
    # closed file. / lost sys.stderr" and a non-zero exit, with no test
    # ever reporting a failure. Setting it inside pytest_configure avoids
    # that. Verified both ways on this tree: unset -> 39 passed, exit 0;
    # pre-exported -> exit 1 with the shutdown crash.
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.test")

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
