"""
Lightweight stub layer for simulate formal tests.

Stubs out Django and model imports so that the pure-function tests
in this directory can run without a database or Django settings.
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

django = _make_module("django")
django_db = _make_module("django.db")
django_db_models = _make_module("django.db.models")
django_utils = _make_module("django.utils")
django_core = _make_module("django.core")
django_core_exc = _make_module("django.core.exceptions")

django_db_models.Model = object
django_db_models.Manager = object

for _fname in ("CharField", "TextField", "IntegerField", "FloatField",
               "BooleanField", "DateTimeField", "UUIDField", "JSONField",
               "ForeignKey", "ManyToManyField", "OneToOneField"):
    setattr(django_db_models, _fname, lambda *a, **k: None)


class _ValidationError(Exception):
    pass


django_core_exc.ValidationError = _ValidationError
django_db.models = django_db_models
django.db = django_db
django.core = django_core
django_core.exceptions = django_core_exc

# ── pydantic stub ─────────────────────────────────────────────────────────────

pydantic = _make_module("pydantic")
pydantic.AfterValidator = lambda f: f

# ── tracer stubs (for ProviderChoices) ────────────────────────────────────────

from enum import Enum


class _ProviderChoices(str, Enum):
    VAPI = "vapi"
    RETELL = "retell"
    ELEVEN_LABS = "eleven_labs"
    LIVEKIT = "livekit"
    OTHERS = "others"


tracer_models = _make_module("tracer.models")
tracer_obs = _make_module("tracer.models.observability_provider")
tracer_obs.ProviderChoices = _ProviderChoices

for _mod in ("tracer", "tracer.models"):
    _m = sys.modules.get(_mod) or _make_module(_mod)
    _m.ProviderChoices = _ProviderChoices

# ── simulate stubs ────────────────────────────────────────────────────────────

for _mod in ("simulate", "simulate.models", "simulate.models.chat_message",
             "simulate.pydantic_schemas", "simulate.pydantic_schemas.chat",
             "simulate.serializers", "simulate.serializers.chat_message"):
    _make_module(_mod)
