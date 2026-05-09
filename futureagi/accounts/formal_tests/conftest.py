"""
Minimal stubs so the RBAC constants and utils can be loaded without Django.

Only stubs what's needed: django.conf.settings, the middleware thread-local,
and the ORM calls inside the permission utils.
"""

import sys
import types


def _make_module(name):
    mod = types.ModuleType(name)
    sys.modules.setdefault(name, mod)
    return mod


# --- django.conf.settings -----------------------------------------------
_settings = _make_module("django.conf")
_settings.settings = types.SimpleNamespace(DEBUG=False)
_make_module("django")
_make_module("django.conf").settings = _settings.settings

# --- django.db (used only for imports, not executed in unit tests) -------
for _pkg in (
    "django.db",
    "django.db.models",
    "rest_framework",
    "rest_framework.permissions",
):
    _make_module(_pkg)

_rest = sys.modules["rest_framework"]
_rest.permissions = sys.modules["rest_framework.permissions"]
sys.modules["rest_framework.permissions"].BasePermission = object

# --- middleware thread-local stub ---------------------------------------
_mw_pkg = _make_module("tfc.middleware")
_mw_ws = _make_module("tfc.middleware.workspace_context")
_mw_ws.get_current_organization = lambda: None
_make_module("tfc")

# --- tfc.constants (real files — importable without Django) -------------
# We'll import them directly via importlib in the test files.
