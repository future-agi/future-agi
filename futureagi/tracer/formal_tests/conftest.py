"""
Lightweight stub layer for tracer formal tests.

Stubs out all Django, model, and celery imports so that the pure-function
tests in this directory can run without a database or Django settings.
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
django_utils_timezone = _make_module("django.utils.timezone")

django_db_models.Model = object
django_db_models.Manager = object


class _FakeField:
    def __init__(self, *a, **k): pass


for _fname in ("CharField", "TextField", "IntegerField", "FloatField",
               "BooleanField", "DateTimeField", "UUIDField", "JSONField",
               "ForeignKey", "ManyToManyField", "OneToOneField",
               "ArrayField", "GenericIPAddressField"):
    setattr(django_db_models, _fname, _FakeField)

django_db.models = django_db_models
django_db.connection = None
django_db.transaction = types.SimpleNamespace(atomic=lambda: None)

django.db = django_db
django.utils = django_utils
django_utils.timezone = django_utils_timezone

# ── model_hub stubs ───────────────────────────────────────────────────────────

for _mod in (
    "model_hub", "model_hub.models", "model_hub.models.prompt_label",
    "model_hub.models.run_prompt",
):
    m = _make_module(_mod)
    m.PromptLabel = object
    m.PromptVersion = object

# ── tfc stubs ─────────────────────────────────────────────────────────────────

for _mod in ("tfc", "tfc.temporal", "tfc.utils", "tfc.utils.payload_storage"):
    m = _make_module(_mod)
    m.temporal_activity = lambda f: f
    m.payload_storage = types.SimpleNamespace(get=lambda *a, **k: None)

# ── tracer model stubs ────────────────────────────────────────────────────────

for _mod in (
    "tracer", "tracer.models", "tracer.models.observation_span",
    "tracer.models.project", "tracer.models.trace_session",
    "tracer.tasks", "tracer.tasks.trace_scanner",
    "tracer.utils", "tracer.utils.adapters", "tracer.utils.otel",
    "tracer.utils.parsers", "tracer.utils.pii_scrubber",
    "tracer.utils.pii_settings",
):
    m = _make_module(_mod)
    # Populate stub attributes tests might reference
    m.ObservationSpan = object
    m.Trace = object
    m.EndUser = object
    m.Project = object
    m.TraceSession = object
    m.scan_traces_task = lambda *a, **k: None
    m.normalize_span_attributes = lambda x: x
    m.bulk_convert_otel_spans_to_observation_spans = lambda *a, **k: []
    m.deserialize_trace_payload = lambda x: x
    m.scrub_pii_in_span_batch = lambda *a, **k: None
    m.get_pii_settings_for_projects = lambda *a, **k: {}
