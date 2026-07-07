"""Resolver-level mapping matrix: every mapping kind the eval-task picker
offers per row type resolves to the seeded value.

Span cases exercise ``_process_mapping`` directly (CH-free). Trace / session
cases exercise ``_process_trace_mapping`` / ``_process_session_mapping`` under
``eval_read_source("clickhouse")`` — the ``spans`` / ``traces`` branches read
the CH sidecar (``test_tfc``), so those rows are seeded via ``_ch_seed``.
voiceCalls dispatch to the span resolver (RowType.VOICE_CALLS →
EvalTargetType.SPAN, run_entry.py), so a voice span case documents that reuse.
"""

import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

import model_hub.tasks  # noqa: F401  (import-cycle breaker, cf. test_eval_task_runtime)
from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.services.clickhouse.v2.eval_loader import eval_read_source
from tracer.tests._ch_seed import (
    seed_ch_spans,
    seed_ch_traces,
    truncate_ch_spans,
)
from tracer.utils.eval import (
    EvalSkippedMissingAttribute,
    _process_mapping,
    _process_session_mapping,
    _process_trace_mapping,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

_MISSING_TEMPLATE_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def _ch_isolation():
    """Refuse the ch_rehearsal baseline, skip if CH is unreachable, and
    truncate the shared ``spans`` table before + after each test."""
    from tracer.services.clickhouse.v2 import get_v2_config

    if get_v2_config().get("database") == "ch_rehearsal":
        pytest.skip("refusing to seed CH against the ch_rehearsal baseline")
    try:
        truncate_ch_spans()
    except Exception:
        pytest.skip("ClickHouse not reachable for the mapping-resolution matrix")
    yield
    try:
        truncate_ch_spans()
    except Exception:
        pass


_ROOT_ATTRS = {
    "input": "root-in",
    "output": "root-out",
    "custom_attr": "root-custom",
    "metadata": {"user": {"plan": "pro"}},
    "messages": [{"content": "m0"}, {"content": "m1"}],
    "raw_json": '{"a": {"b": "deep"}}',
}


def _make_span(
    project,
    trace,
    *,
    span_id,
    parent,
    attrs,
    model,
    start,
    latency=123,
    input=None,
    output=None,
    provider=None,
    total_tokens=None,
):
    span = ObservationSpan.objects.create(
        id=span_id,
        project=project,
        trace=trace,
        parent_span_id=parent,
        name=span_id,
        observation_type="llm",
        start_time=start,
        end_time=start + timedelta(seconds=1),
        latency_ms=latency,
        model=model,
        provider=provider,
        total_tokens=total_tokens,
        input=input,
        output=output,
        status="OK",
        span_attributes=attrs,
    )
    return span


@pytest.fixture
def custom_eval_template(db, organization, workspace):
    from model_hub.models.evals_metric import EvalTemplate

    return EvalTemplate.objects.create(
        name="custom-eval-template",
        description="custom eval",
        organization=organization,
        workspace=workspace,
        config={"type": "pass_fail", "criteria": "x", "custom_eval": True},
    )


@pytest.fixture
def mapping_corpus(db, observe_project, eval_template):
    """One session → 2 traces (root+child spans each), seeded in PG and CH."""
    now = timezone.now()
    session = TraceSession.objects.create(
        project=observe_project, name="map-session", bookmarked=True
    )
    # trace_a's root starts EARLIER → traces.0 / traces.first.
    trace_a = Trace.objects.create(
        project=observe_project,
        session=session,
        name="trace-A",
        input={"q": "qa"},
        metadata={"env": "prod"},
        tags=["a"],
    )
    trace_b = Trace.objects.create(
        project=observe_project,
        session=session,
        name="trace-B",
        input={"q": "qb"},
        metadata={"env": "dev"},
        tags=["b"],
    )
    spans = []
    for trace, root_offset, child_custom in (
        (trace_a, 10, "child-a"),
        (trace_b, 5, "child-b"),
    ):
        root_start = now - timedelta(minutes=root_offset)
        # DB columns (input/output/provider/total_tokens) survive the CH eval
        # span read; span_attributes and latency_ms do NOT (_construct_from_chspan
        # reconstructs columns only). So the trace/session spans.* cases below
        # reference columns, while the PG span cases exercise span_attributes.
        root = _make_span(
            observe_project,
            trace,
            span_id=f"root_{trace.name}",
            parent=None,
            attrs=dict(_ROOT_ATTRS),
            model="gpt-4",
            start=root_start,
            input={"col": "root-in"},
            output={"col": "root-out"},
            provider="openai",
            total_tokens=15,
        )
        child = _make_span(
            observe_project,
            trace,
            span_id=f"child_{trace.name}",
            parent=root.id,
            attrs={"custom_attr": child_custom},
            model="gpt-4o-mini",
            start=root_start + timedelta(seconds=30),
            input={"col": f"{child_custom}-in"},
        )
        spans.extend([root, child])
    seed_ch_spans(spans)
    seed_ch_traces([trace_a, trace_b])

    from types import SimpleNamespace

    return SimpleNamespace(
        project=observe_project,
        session=session,
        trace_a=trace_a,
        trace_b=trace_b,
        root_a=spans[0],
        eval_template=eval_template,
    )


def _assert_resolved(out, key, expected):
    """Compare honoring the resolver's json.dumps of non-string values."""
    got = out[key]
    if isinstance(expected, str):
        assert got == expected
    else:
        assert json.loads(got) == expected


# ── Span (_process_mapping) ────────────────────────────────────────────────

_SPAN_CASES = [
    ("input", "root-in"),
    ("output", "root-out"),
    ("custom_attr", "root-custom"),
    ("metadata.user.plan", "pro"),
    ("messages.1.content", "m1"),
    ("raw_json.a.b", "deep"),
    ("model", "gpt-4"),
    ("latency_ms", 123),
]


@pytest.mark.parametrize("path,expected", _SPAN_CASES, ids=[c[0] for c in _SPAN_CASES])
def test_span_mapping_resolves(mapping_corpus, path, expected):
    out = _process_mapping(
        {"k": path}, mapping_corpus.root_a, eval_template_id=_MISSING_TEMPLATE_ID
    )
    _assert_resolved(out, "k", expected)


def test_span_mapping_missing_raises_typed_skip(mapping_corpus):
    with pytest.raises(ValueError, match="Required attribute 'nope'") as exc:
        _process_mapping(
            {"prompt": "nope"},
            mapping_corpus.root_a,
            eval_template_id=_MISSING_TEMPLATE_ID,
        )
    assert isinstance(exc.value, EvalSkippedMissingAttribute)
    assert exc.value.skipped_reason == "missing_required_attribute: nope"


def test_span_mapping_missing_custom_eval_returns_empty(
    mapping_corpus, custom_eval_template
):
    out = _process_mapping(
        {"k": "nope"},
        mapping_corpus.root_a,
        eval_template_id=custom_eval_template.id,
    )
    assert out == {"k": ""}


def test_span_mapping_attribute_prefix_is_not_stripped(mapping_corpus):
    # The FE strips the ``span_attributes.`` prefix (stripAttributePathPrefix);
    # the resolver does NOT, so a prefixed path misses.
    with pytest.raises(ValueError, match="span_attributes.input"):
        _process_mapping(
            {"k": "span_attributes.input"},
            mapping_corpus.root_a,
            eval_template_id=_MISSING_TEMPLATE_ID,
        )


# ── Voice (conversation span, raw_log fallback) ────────────────────────────

_VAPI_RAW_LOG = {
    "startedAt": "2026-05-27T10:00:00Z",
    "messages": [{"role": "bot", "message": "hello-voice"}],
}


@pytest.fixture
def voice_span(db, observe_project, trace):
    span = _make_span(
        observe_project,
        trace,
        span_id="voice_root",
        parent=None,
        attrs={
            "raw_log": _VAPI_RAW_LOG,
            "conversation": {"transcript": [{"role": "user", "text": "hi"}]},
            "stereo_recording_url": "https://x/rec.wav",
        },
        model="gpt-4",
        start=timezone.now() - timedelta(minutes=1),
    )
    span.observation_type = "conversation"
    span.save(update_fields=["observation_type"])
    return span


_VOICE_CASES = [
    ("transcript", [{"role": "user", "text": "hi"}]),
    ("recording_url", "https://x/rec.wav"),
    ("messages.0.message", "hello-voice"),
    ("started_at", "2026-05-27T10:00:00Z"),
]


@pytest.mark.parametrize(
    "path,expected", _VOICE_CASES, ids=[c[0] for c in _VOICE_CASES]
)
def test_voice_span_mapping_resolves(voice_span, path, expected):
    # voiceCalls dispatch to the span resolver (RowType.VOICE_CALLS →
    # EvalTargetType.SPAN, run_entry.py) — same _process_mapping path.
    out = _process_mapping(
        {"k": path}, voice_span, eval_template_id=_MISSING_TEMPLATE_ID
    )
    _assert_resolved(out, "k", expected)


@pytest.mark.parametrize("path", ["messages.0.message", "started_at"])
def test_voice_paths_miss_on_non_conversation_span(mapping_corpus, path):
    # The raw_log fallback is gated on observation_type == "conversation".
    with pytest.raises(ValueError):
        _process_mapping(
            {"k": path}, mapping_corpus.root_a, eval_template_id=_MISSING_TEMPLATE_ID
        )


# ── Trace (_process_trace_mapping, CH read) ────────────────────────────────

# spans.* cases reference DB columns only — the CH eval span loader does not
# reconstruct span_attributes / latency_ms (see _construct_from_chspan).
_TRACE_CASES = [
    ("input", {"q": "qa"}),
    ("name", "trace-A"),
    ("metadata.env", "prod"),
    ("tags", ["a"]),
    ("spans.0.input", {"col": "root-in"}),
    ("spans.first.provider", "openai"),
    ("spans.last.model", "gpt-4o-mini"),
    ("spans.0.output", {"col": "root-out"}),
    ("spans.0.total_tokens", 15),
]


@pytest.mark.parametrize(
    "path,expected", _TRACE_CASES, ids=[c[0] for c in _TRACE_CASES]
)
def test_trace_mapping_resolves(mapping_corpus, path, expected):
    with eval_read_source("clickhouse"):
        out = _process_trace_mapping(
            {"k": path}, mapping_corpus.trace_a, mapping_corpus.eval_template.id
        )
    _assert_resolved(out, "k", expected)


@pytest.mark.parametrize("path", ["spans.99.input", "nonexistent"])
def test_trace_mapping_missing_raises(mapping_corpus, path):
    with eval_read_source("clickhouse"):
        with pytest.raises(ValueError):
            _process_trace_mapping(
                {"k": path}, mapping_corpus.trace_a, mapping_corpus.eval_template.id
            )


# ── Session (_process_session_mapping, CH read) ────────────────────────────

_SESSION_CASES = [
    ("name", "map-session"),
    ("bookmarked", True),
    ("traces.0.input", {"q": "qa"}),
    ("traces.first.name", "trace-A"),
    ("traces.1.input", {"q": "qb"}),
    ("traces.0.spans.0.input", {"col": "root-in"}),
    ("traces.last.spans.last.model", "gpt-4o-mini"),
    ("traces.0.spans.0.output", {"col": "root-out"}),
]


@pytest.mark.parametrize(
    "path,expected", _SESSION_CASES, ids=[c[0] for c in _SESSION_CASES]
)
def test_session_mapping_resolves(mapping_corpus, path, expected):
    with eval_read_source("clickhouse"):
        out = _process_session_mapping(
            {"k": path}, mapping_corpus.session, mapping_corpus.eval_template.id
        )
    _assert_resolved(out, "k", expected)


@pytest.mark.parametrize("path", ["traces.5.input", "nonexistent"])
def test_session_mapping_missing_raises(mapping_corpus, path):
    with eval_read_source("clickhouse"):
        with pytest.raises(ValueError):
            _process_session_mapping(
                {"k": path}, mapping_corpus.session, mapping_corpus.eval_template.id
            )
