"""Mode-aware tracing usage emission (TH-5618).

Filling must match the org's billing mode:
  - events mode  → only ``tracing_events``, amount = traces + spans
  - storage mode → only ``storage`` (observe_add bytes), no tracing_events

Regression for the prior behavior, which always filled both dimensions and
emitted ``tracing_events = num_traces`` only (spans lost, span-only batches
metered nothing).
"""

from __future__ import annotations
import pytest
pytest.importorskip("ee.usage.schemas.event_types")
from ee.usage.schemas.event_types import BillingEventType

ORG_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def captured_emit(monkeypatch):
    captured: list = []
    monkeypatch.setattr("ee.usage.services.emitter.emit", lambda e: captured.append(e))
    monkeypatch.setattr("ee.usage.deployment.DeploymentMode.is_oss", lambda: False)
    return captured


@pytest.fixture
def set_mode(monkeypatch):
    def _set(mode):
        monkeypatch.setattr(
            "tracer.utils.usage_emit._tracing_billing_mode", lambda _org: mode
        )

    return _set


def _of_type(captured, event_type):
    return [e for e in captured if e.event_type == event_type]


def test_events_mode_counts_traces_plus_spans_only(captured_emit, set_mode):
    set_mode("events")
    from tracer.utils.usage_emit import emit_span_ingestion_usage

    emit_span_ingestion_usage(
        organization_id=ORG_ID,
        num_traces=3,
        num_spans=10,
        payload_bytes=500,
        source="trace_span",
    )

    tracing = _of_type(captured_emit, BillingEventType.TRACING_EVENT)
    assert len(tracing) == 1
    assert tracing[0].amount == 13
    assert tracing[0].properties["traces"] == 13
    assert tracing[0].properties["source"] == "trace_span"
    # storage must NOT be filled in events mode
    assert _of_type(captured_emit, BillingEventType.OBSERVE_ADD) == []


def test_events_mode_span_only_batch_still_emits(captured_emit, set_mode):
    set_mode("events")
    from tracer.utils.usage_emit import emit_span_ingestion_usage

    emit_span_ingestion_usage(
        organization_id=ORG_ID,
        num_traces=0,
        num_spans=5,
        payload_bytes=0,
        source="trace_span",
    )

    tracing = _of_type(captured_emit, BillingEventType.TRACING_EVENT)
    assert len(tracing) == 1
    assert tracing[0].amount == 5


def test_storage_mode_fills_storage_only(captured_emit, set_mode):
    set_mode("storage")
    from tracer.utils.usage_emit import emit_span_ingestion_usage

    emit_span_ingestion_usage(
        organization_id=ORG_ID,
        num_traces=3,
        num_spans=10,
        payload_bytes=500,
        source="trace_span",
    )

    storage = _of_type(captured_emit, BillingEventType.OBSERVE_ADD)
    assert len(storage) == 1
    assert storage[0].amount == 500
    assert storage[0].properties["units"] == 13
    # tracing_events must NOT be filled in storage mode
    assert _of_type(captured_emit, BillingEventType.TRACING_EVENT) == []


def test_storage_mode_no_bytes_emits_nothing(captured_emit, set_mode):
    set_mode("storage")
    from tracer.utils.usage_emit import emit_span_ingestion_usage

    emit_span_ingestion_usage(
        organization_id=ORG_ID,
        num_traces=3,
        num_spans=10,
        payload_bytes=0,
        source="trace_span",
    )

    assert captured_emit == []


def test_events_mode_no_traces_or_spans_emits_nothing(captured_emit, set_mode):
    set_mode("events")
    from tracer.utils.usage_emit import emit_span_ingestion_usage

    emit_span_ingestion_usage(
        organization_id=ORG_ID,
        num_traces=0,
        num_spans=0,
        payload_bytes=500,
        source="trace_span",
    )

    assert captured_emit == []


# ---------------------------------------------------------------------------
# (tracing_billing_mode × project_type) matrix — TH-5618 wiring guarantees.
#
# Project type is identified by the ``source`` arg:
#   - "trace_span"            → tracing project (trace_ingestion call site)
#   - "voice_observability"   → voice project, span ingest call site
#   - "voice_recording_rehost"→ voice project, S3 audio rehost call site
# ---------------------------------------------------------------------------


def test_case_2_events_voice_emits_events_for_spans_and_storage_for_rehost(
    captured_emit, set_mode
):
    set_mode("events")
    from tracer.utils.usage_emit import emit_span_ingestion_usage

    # Voice span ingest — `payload_bytes` is the span JSON size and must
    # NOT bill storage in events mode; it's already counted as an event.
    emit_span_ingestion_usage(
        organization_id=ORG_ID,
        num_traces=0,
        num_spans=4,
        payload_bytes=1200,
        source="voice_observability",
    )
    # Recording rehost — the audio bytes that landed in our S3. Always
    # storage, regardless of mode.
    emit_span_ingestion_usage(
        organization_id=ORG_ID,
        num_traces=0,
        num_spans=0,
        payload_bytes=750_000,
        source="voice_recording_rehost",
    )

    tracing = _of_type(captured_emit, BillingEventType.TRACING_EVENT)
    assert len(tracing) == 1
    assert tracing[0].amount == 4
    assert tracing[0].properties["source"] == "voice_observability"

    storage = _of_type(captured_emit, BillingEventType.OBSERVE_ADD)
    assert len(storage) == 1
    assert storage[0].amount == 750_000
    assert storage[0].properties["source"] == "voice_recording_rehost"


def test_case_4_storage_voice_emits_storage_for_spans_and_rehost(
    captured_emit, set_mode
):
    # Wiring guarantee for storage-mode voice projects: BOTH the span
    # ingest call (span JSON bytes) AND the rehost call (S3 audio bytes)
    # must produce an OBSERVE_ADD. Losing either is silent revenue leak.
    set_mode("storage")
    from tracer.utils.usage_emit import emit_span_ingestion_usage

    emit_span_ingestion_usage(
        organization_id=ORG_ID,
        num_traces=0,
        num_spans=4,
        payload_bytes=1200,
        source="voice_observability",
    )
    emit_span_ingestion_usage(
        organization_id=ORG_ID,
        num_traces=0,
        num_spans=0,
        payload_bytes=750_000,
        source="voice_recording_rehost",
    )

    assert _of_type(captured_emit, BillingEventType.TRACING_EVENT) == []

    storage = _of_type(captured_emit, BillingEventType.OBSERVE_ADD)
    assert len(storage) == 2
    by_source = {e.properties["source"]: e for e in storage}
    assert by_source["voice_observability"].amount == 1200
    assert by_source["voice_observability"].properties["units"] == 4
    assert by_source["voice_recording_rehost"].amount == 750_000
