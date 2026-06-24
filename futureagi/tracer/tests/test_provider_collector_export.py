"""Unit tests for the provider-pull -> fi-collector dual-write (TH-5642).

Pulled calls (vapi/retell/elevenlabs/twilio/bland) write PG but must also reach
CH `spans` (the read store) now that the PeerDB CDC chain is dropped. These
pin the CONVERSATION span built for the collector export, without Django/live
collector — emit_spans_to_collector is mocked.
"""

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
    # provider pulls must be labeled fi-provider, not the default fi-simulation
    assert captured["service_name"] == "fi-provider"
    s = captured["spans"][0]
    # CONVERSATION root so it lands in the voice-call list.
    assert s["attributes"]["gen_ai.span.kind"] == "CONVERSATION"
    assert s["attributes"]["gen_ai.system"] == "vapi"
    # raw_log dropped (OTLP can't carry it); scalar call.* attrs kept for the
    # empty-raw_log read path.
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
    # The detail drawer builds the transcript from raw_log (dropped on export),
    # so the export must stash the normalized transcript for the read path.
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
