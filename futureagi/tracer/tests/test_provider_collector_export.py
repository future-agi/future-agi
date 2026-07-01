"""Provider-pull -> fi-collector export: pulled calls reach CH `spans`/`traces` (collector-owned write)."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tracer.utils import observability_provider as op


@pytest.mark.unit
def test_provider_collector_span_id_is_deterministic_16hex():
    a = op._provider_collector_span_id("vapi", "call_123")
    b = op._provider_collector_span_id("vapi", "call_123")
    c = op._provider_collector_span_id("retell", "call_123")
    assert a == b  # stable across re-polls (ReplacingMergeTree upsert)
    assert a != c  # provider-scoped
    assert len(a) == 16 and int(a, 16) >= 0  # valid 64-bit OTLP span id


@pytest.mark.unit
def test_to_epoch_ns_handles_datetime_seconds_and_ns():
    dt = datetime(2026, 6, 19, 12, 0, 0, tzinfo=UTC)
    assert op._to_epoch_ns(dt) == int(dt.timestamp() * 1e9)
    assert op._to_epoch_ns(1_700_000_000) == 1_700_000_000 * 1_000_000_000  # seconds
    assert op._to_epoch_ns(1_700_000_000_000_000_000) == 1_700_000_000_000_000_000  # ns
    assert op._to_epoch_ns(None) is None


@pytest.mark.unit
def test_export_builds_conversation_span_and_drops_raw_log(monkeypatch):
    captured = {}

    def _fake_emit(
        spans,
        *,
        project_name,
        project_type,
        organization_id,
        workspace_id,
        service_name="fi-simulation",
    ):
        captured["spans"] = spans
        captured["project_name"] = project_name
        captured["organization_id"] = organization_id
        captured["service_name"] = service_name
        return len(spans)

    import tracer.services.collector_ingest as ci

    monkeypatch.setattr(ci, "emit_spans_to_collector", _fake_emit)

    project = SimpleNamespace(
        id=SimpleNamespace(hex="0123456789abcdef0123456789abcdef"),
        name="pull-vapi",
        trace_type="observe",
        organization_id="org-1",
        workspace_id="ws-1",
    )
    trace = SimpleNamespace(id=SimpleNamespace(hex="ffffffffffffffffffffffffffffffff"))
    span = SimpleNamespace(
        project=project,
        trace=trace,
        name="Vapi Call Log",
        input={"messages": []},
        output="ok",
        start_time=datetime(2026, 6, 19, 12, 0, 0, tzinfo=UTC),
        end_time=datetime(2026, 6, 19, 12, 0, 30, tzinfo=UTC),
        span_attributes={
            "raw_log": {"id": "call_123", "huge": "nested"},
            "call.status": "completed",
            "call.duration": 30,
        },
    )

    op._export_provider_call_to_collector(span, "vapi", "call_123")

    assert captured["project_name"] == "pull-vapi"
    assert captured["organization_id"] == "org-1"
    # provider pulls labeled fi-provider, not the default fi-simulation
    assert captured["service_name"] == "fi-provider"
    s = captured["spans"][0]
    # CONVERSATION root so it lands in the voice-call list.
    assert s["attributes"]["gen_ai.span.kind"] == "CONVERSATION"
    assert s["attributes"]["gen_ai.system"] == "vapi"
    # raw_log dropped (OTLP can't carry it); scalar call.* attrs kept for empty-raw_log read path.
    assert "raw_log" not in s["attributes"]
    assert s["attributes"]["call.status"] == "completed"
    assert s["attributes"]["call.duration"] == 30
    assert s["attributes"]["input.value"] == {"messages": []}
    assert s["attributes"]["output.value"] == "ok"
    assert s["parent_span_id"] is None
    assert s["trace_id"] == "ffffffffffffffffffffffffffffffff"
    assert s["span_id"] == op._provider_collector_span_id("vapi", "call_123")
    assert "start_time" in s and "end_time" in s


@pytest.mark.unit
def test_export_stashes_normalized_transcript(monkeypatch):
    captured = {}

    def _fake_emit(spans, **kwargs):
        captured["spans"] = spans
        return len(spans)

    import tracer.services.collector_ingest as ci
    from tracer.services.observability_providers import ObservabilityService

    monkeypatch.setattr(ci, "emit_spans_to_collector", _fake_emit)
    # raw_log is dropped on export, so stash the normalized transcript for the read path.
    monkeypatch.setattr(
        ObservabilityService,
        "process_raw_logs",
        staticmethod(
            lambda raw_log, provider, span_attributes=None: {
                "transcript": [{"role": "bot", "content": "Hi"}]
            }
        ),
    )

    project = SimpleNamespace(
        id=SimpleNamespace(hex="0" * 32),
        name="pull-vapi",
        trace_type="observe",
        organization_id="org-1",
        workspace_id=None,
    )
    span = SimpleNamespace(
        project=project,
        trace=SimpleNamespace(id=SimpleNamespace(hex="f" * 32)),
        name="Vapi Call Log",
        input=None,
        output=None,
        start_time=None,
        end_time=None,
        span_attributes={"raw_log": {"id": "c1", "transcript": "..."}},
    )
    op._export_provider_call_to_collector(span, "vapi", "c1")
    attrs = captured["spans"][0]["attributes"]
    assert attrs["fi.conversation.transcript"] == [{"role": "bot", "content": "Hi"}]


@pytest.mark.unit
def test_export_no_org_is_noop(monkeypatch):
    called = {"n": 0}
    import tracer.services.collector_ingest as ci

    monkeypatch.setattr(
        ci, "emit_spans_to_collector", lambda *a, **k: called.__setitem__("n", 1)
    )
    project = SimpleNamespace(
        name="p", trace_type="observe", organization_id="", workspace_id=None
    )
    span = SimpleNamespace(project=project)
    op._export_provider_call_to_collector(span, "vapi", "call_1")
    assert called["n"] == 0  # no org -> skipped, never raises


@pytest.mark.unit
def test_export_fires_regardless_of_legacy_env(monkeypatch):
    # CDC is assumed always dropped: the export no longer gates on the env var.
    monkeypatch.delenv("CH25_DROP_LEGACY_CDC_CHAIN", raising=False)
    called = {"n": 0}
    import tracer.services.collector_ingest as ci

    monkeypatch.setattr(
        ci, "emit_spans_to_collector", lambda *a, **k: called.__setitem__("n", 1)
    )
    project = SimpleNamespace(
        id=SimpleNamespace(hex="0" * 32),
        name="pull-vapi",
        trace_type="observe",
        organization_id="org-1",
        workspace_id=None,
    )
    span = SimpleNamespace(
        project=project,
        trace=SimpleNamespace(id=SimpleNamespace(hex="f" * 32)),
        name="Vapi Call Log",
        input=None,
        output=None,
        start_time=None,
        end_time=None,
        span_attributes={"raw_log": {"id": "c1"}, "call.status": "completed"},
    )
    op._export_provider_call_to_collector(span, "vapi", "c1")
    assert called["n"] == 1  # export always fires now


@pytest.mark.unit
def test_export_stamps_error_status_for_failed_call(monkeypatch):
    captured = {}

    def _fake_emit(spans, **kwargs):
        captured["spans"] = spans
        return len(spans)

    import tracer.services.collector_ingest as ci

    monkeypatch.setattr(ci, "emit_spans_to_collector", _fake_emit)
    project = SimpleNamespace(
        id=SimpleNamespace(hex="0" * 32),
        name="pull-twilio",
        trace_type="observe",
        organization_id="org-1",
        workspace_id=None,
    )
    span = SimpleNamespace(
        project=project,
        trace=SimpleNamespace(id=SimpleNamespace(hex="f" * 32)),
        name="Twilio Call",
        input=None,
        output=None,
        start_time=None,
        end_time=None,
        status="OK",
        span_attributes={"raw_log": {"id": "c2"}, "call.status": "error"},
    )
    op._export_provider_call_to_collector(span, "twilio", "c2")
    # Failed call must carry ERROR status (collector copies status_code -> spans.status).
    assert captured["spans"][0].get("status_code") == "ERROR"
