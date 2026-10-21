"""Dataset/experiment row-execution tracing helpers (issue #2665).

Covers the pure span-building helpers in ``tracer.services.dataset_tracing`` and
the best-effort emission contract: tracing must never raise or fail a run.
"""

import pytest

from tracer.services import dataset_tracing as dt


@pytest.mark.unit
def test_new_ids_are_hex_and_correct_width():
    trace_id = dt.new_trace_id()
    span_id = dt.new_span_id()
    # 128-bit trace id = 32 hex chars; 64-bit span id = 16 hex chars.
    assert len(trace_id) == 32
    assert len(span_id) == 16
    int(trace_id, 16)  # plain hex, no dashes (SimSpanDict parses with int(x, 16))
    int(span_id, 16)
    assert dt.new_trace_id() != trace_id  # unique per call


@pytest.mark.unit
def test_now_ns_is_epoch_nanoseconds():
    import time

    assert abs(dt.now_ns() - time.time_ns()) < 1_000_000_000


@pytest.mark.unit
def test_identity_attributes_only_stamps_provided_values():
    attrs = dt.identity_attributes(
        dataset_id="ds-1",
        row_id="row-2",
        column_id="col-3",
        variant="0",
        source="dataset",
    )
    assert attrs[dt.ATTR_DATASET_ID] == "ds-1"
    assert attrs[dt.ATTR_DATASET_ROW_ID] == "row-2"
    assert attrs[dt.ATTR_DATASET_COLUMN_ID] == "col-3"
    assert attrs[dt.ATTR_DATASET_VARIANT] == "0"
    assert attrs[dt.ATTR_DATASET_SOURCE] == "dataset"


@pytest.mark.unit
def test_identity_attributes_omits_none():
    attrs = dt.identity_attributes(dataset_id="ds-1", row_id=None, column_id=None)
    assert dt.ATTR_DATASET_ID in attrs
    assert dt.ATTR_DATASET_ROW_ID not in attrs
    assert dt.ATTR_DATASET_COLUMN_ID not in attrs


@pytest.mark.unit
def test_build_span_matches_sim_span_dict_schema():
    span = dt.build_span(
        name="dataset.run.row",
        trace_id="0123456789abcdef0123456789abcdef",
        span_id="1122334455667788",
        parent_span_id=None,
        start_time=1_000,
        end_time=2_000,
        span_kind=dt.SPAN_KIND_CHAIN,
        attributes={"fi.dataset.id": "ds-1"},
    )
    assert span["name"] == "dataset.run.row"
    assert span["trace_id"] == "0123456789abcdef0123456789abcdef"
    assert span["span_id"] == "1122334455667788"
    assert span["parent_span_id"] is None
    assert span["start_time"] == 1_000
    assert span["end_time"] == 2_000
    # gen_ai.span.kind is mirrored onto fi.span.kind by the collector; the
    # builder defaults it to the requested kind.
    assert span["attributes"][dt.ATTR_SPAN_KIND] == dt.SPAN_KIND_CHAIN
    assert span["status_code"] == "OK"


@pytest.mark.unit
def test_build_span_preserves_error_status_and_does_not_clobber_kind():
    span = dt.build_span(
        name="llm.model",
        trace_id="0123456789abcdef0123456789abcdef",
        span_id="1122334455667788",
        parent_span_id="8877665544332211",
        start_time=1,
        end_time=2,
        span_kind=dt.SPAN_KIND_LLM,
        attributes={dt.ATTR_SPAN_KIND: dt.SPAN_KIND_LLM, "error.type": "timeout"},
        status_code="ERROR",
    )
    assert span["status_code"] == "ERROR"
    assert span["attributes"][dt.ATTR_SPAN_KIND] == dt.SPAN_KIND_LLM
    assert span["attributes"]["error.type"] == "timeout"


@pytest.mark.unit
def test_emit_dataset_spans_forwards_project_and_never_raises(monkeypatch):
    captured = {}

    def _fake_emit(spans, **kwargs):
        captured["spans"] = spans
        captured["kwargs"] = kwargs

    monkeypatch.setattr(
        "tracer.services.collector_ingest.emit_spans_to_collector", _fake_emit
    )
    spans = [{"name": "dataset.run.row", "attributes": {}}]
    dt.emit_dataset_spans(spans, organization_id="org-1", workspace_id="ws-1")
    assert captured["spans"] is spans
    assert captured["kwargs"]["project_name"] == "dataset-execution"
    # "experiment" — the only non-observe type the collector's ProjectType knows.
    assert captured["kwargs"]["project_type"] == "experiment"
    assert captured["kwargs"]["organization_id"] == "org-1"
    assert captured["kwargs"]["workspace_id"] == "ws-1"


@pytest.mark.unit
def test_emit_dataset_spans_swallows_emission_failure(monkeypatch):
    def _boom(spans, **kwargs):
        raise RuntimeError("collector down")

    monkeypatch.setattr(
        "tracer.services.collector_ingest.emit_spans_to_collector", _boom
    )
    # Must not raise — tracing is best-effort by design.
    dt.emit_dataset_spans(
        [{"name": "dataset.run.row", "attributes": {}}],
        organization_id="org-1",
        workspace_id=None,
    )


@pytest.mark.unit
def test_emit_dataset_spans_empty_is_noop(monkeypatch):
    called = []

    def _fake_emit(spans, **kwargs):
        called.append(spans)

    monkeypatch.setattr(
        "tracer.services.collector_ingest.emit_spans_to_collector", _fake_emit
    )
    dt.emit_dataset_spans([], organization_id="org-1", workspace_id=None)
    assert called == []
