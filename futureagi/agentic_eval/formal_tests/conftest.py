"""
Lightweight stub layer for agentic_eval formal tests.

Stubs out Django and model imports so the pure-function tests
in this directory run without a database or Django settings.
"""
import sys
import types


def _make_module(name):
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


# ── structlog ─────────────────────────────────────────────────────────────────

structlog = _make_module("structlog")

_warnings = []


class _CapturingLogger:
    def info(self, *a, **k): pass
    def warning(self, event=None, *a, **k):
        _warnings.append({"event": event, **k})
    def error(self, *a, **k): pass
    def exception(self, *a, **k): pass
    def debug(self, *a, **k): pass


_logger_instance = _CapturingLogger()
structlog.get_logger = lambda *a, **k: _logger_instance


def captured_warnings():
    return list(_warnings)


def reset_warnings():
    _warnings.clear()


# ── django ────────────────────────────────────────────────────────────────────

django = _make_module("django")
django_db = _make_module("django.db")
django_db_models = _make_module("django.db.models")
django_utils = _make_module("django.utils")
django_core = _make_module("django.core")
django_core_exc = _make_module("django.core.exceptions")

django_db_models.Model = object
django_db_models.Manager = object

for _fname in (
    "CharField", "TextField", "IntegerField", "FloatField",
    "BooleanField", "DateTimeField", "UUIDField", "JSONField",
    "ForeignKey", "ManyToManyField", "OneToOneField",
):
    setattr(django_db_models, _fname, lambda *a, **k: None)


class _ValidationError(Exception):
    pass


django_core_exc.ValidationError = _ValidationError
django_db.models = django_db_models
django.db = django_db
django.core = django_core
django_core.exceptions = django_core_exc

# ── accounts / model_hub stubs ────────────────────────────────────────────────

for _mod in (
    "accounts", "accounts.models", "accounts.models.organization",
    "accounts.models.workspace", "model_hub", "model_hub.models",
    "model_hub.models.api_key", "model_hub.models.custom_models",
    "agentic_eval", "agentic_eval.core_evals",
    "agentic_eval.core_evals.run_prompt",
    "agentic_eval.core_evals.run_prompt.available_models",
):
    _make_module(_mod)

# Stub AVAILABLE_MODELS so litellm_models import doesn't fail
sys.modules["agentic_eval.core_evals.run_prompt.available_models"].AVAILABLE_MODELS = []
