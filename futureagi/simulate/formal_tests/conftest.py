"""
Lightweight stub layer for simulate formal tests.

Stubs out Django and model imports WITHOUT replacing the simulate package itself,
so pure functions in simulate/utils/ can be imported from their real location.
"""
import sys
import types


def _make_module(name):
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


# ── structlog ─────────────────────────────────────────────────────────────────

structlog = _make_module("structlog")


class _NullLogger:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def exception(self, *a, **k): pass
    def debug(self, *a, **k): pass


structlog.get_logger = lambda *a, **k: _NullLogger()

# ── django ────────────────────────────────────────────────────────────────────

_make_module("django")
_make_module("django.db")
django_db_models = _make_module("django.db.models")
_make_module("django.utils")
django_core = _make_module("django.core")
django_core_exc = _make_module("django.core.exceptions")
_make_module("django.core.validators")

django_db_models.Model = object
django_db_models.Manager = object


class _ValidationError(Exception):
    pass


django_core_exc.ValidationError = _ValidationError
sys.modules["django.core"] = django_core
django_core.exceptions = django_core_exc

# ── pydantic stub ─────────────────────────────────────────────────────────────

pydantic = _make_module("pydantic")
pydantic.AfterValidator = lambda f: f

# ── tracer stubs (for ProviderChoices used in semantics.py) ──────────────────

from enum import Enum


class _ProviderChoices(str, Enum):
    VAPI = "vapi"
    RETELL = "retell"
    ELEVEN_LABS = "eleven_labs"
    LIVEKIT = "livekit"
    OTHERS = "others"


_make_module("tracer")
tracer_models = _make_module("tracer.models")
tracer_obs = _make_module("tracer.models.observability_provider")
tracer_obs.ProviderChoices = _ProviderChoices
tracer_models.observability_provider = tracer_obs

# ── simulate model/schema stubs (avoid real Django models) ───────────────────
# NOTE: do NOT stub "simulate" or "simulate.utils" — let the real package load.
# Only stub the sub-modules that import Django ORM models.

for _mod_name in (
    "simulate.models",
    "simulate.models.chat_message",
    "simulate.pydantic_schemas",
    "simulate.pydantic_schemas.chat",
    "simulate.serializers",
    "simulate.serializers.chat_message",
):
    _m = _make_module(_mod_name)
    # Add common stub attributes so import-time name lookups don't fail
    _m.CallExecution = object
    _m.SimulateEvalConfig = object
    _m.ChatMessageModel = object
    _m.ChatRole = object
    _m.ChatMessageSerializer = object
