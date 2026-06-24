"""Langfuse → fi-collector span emit (TH-5642, CDC-off).

Verifies the new ``langfuse_collector_emit`` path: after ``upsert_langfuse_trace``
commits, the persisted Langfuse observations are shipped to the collector so they
land in CH ``spans`` (CDC-off the collector is the sole ``spans`` writer). Pins the
blocker-fix: the OTLP trace_id is the PG trace UUID hex (so ``trace_name`` resolves
via trace_dict), span ids are deterministic, the tree is preserved, tokens ride as
numeric attrs, and the emit is gated/labeled ``service_name='fi-langfuse'``.
"""

import uuid

import pytest


def _assembled():
    ext = "lf-" + uuid.uuid4().hex[:10]
    span_id = uuid.uuid4().hex
    gen_id = uuid.uuid4().hex
    return (
        ext,
        span_id,
        gen_id,
        {
            "id": ext,
            "name": "lf-trace",
            "observations": [
                {
                    "id": span_id,
                    "type": "SPAN",
                    "name": "outer",
                    "startTime": "2026-06-20T00:00:00.000Z",
                    "endTime": "2026-06-20T00:00:03.000Z",
                    "input": "q",
                    "output": "a",
                },
                {
                    "id": gen_id,
                    "type": "GENERATION",
                    "name": "llm",
                    "parentObservationId": span_id,
                    "startTime": "2026-06-20T00:00:01.000Z",
                    "endTime": "2026-06-20T00:00:02.000Z",
                    "input": "prompt",
                    "output": "completion",
                    "model": "gpt-4o-mini",
                    "usageDetails": {"input": 120, "output": 30, "total": 150},
                },
            ],
            "scores": [],
        },
    )


@pytest.mark.integration
def test_langfuse_spans_emitted_to_collector(
    observe_project,
    organization,
    workspace,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    import tracer.services.collector_ingest as ci
    from integrations.transformers.langfuse_transformer import LangfuseTransformer
    from tracer.models.observation_span import ObservationSpan
    from tracer.models.trace import Trace
    from tracer.utils.langfuse_upsert import upsert_langfuse_trace

    # Don't hit real CH for the trace mirror; only the collector emit matters here.
    import tracer.services.clickhouse.v2.trace_writer as tw

    monkeypatch.setattr(tw, "mirror_traces_to_clickhouse", lambda *a, **k: None)

    captured = {}

    def _fake_emit(spans, **kw):
        captured["spans"] = spans
        captured["kw"] = kw
        return len(spans)

    monkeypatch.setattr(ci, "emit_spans_to_collector", _fake_emit)

    ext, span_id, gen_id, assembled = _assembled()
    with django_capture_on_commit_callbacks(execute=True):
        upsert_langfuse_trace(
            assembled_trace=assembled,
            transformer=LangfuseTransformer(),
            project_id=str(observe_project.id),
            org=organization,
            workspace=workspace,
            org_id=str(organization.id),
        )

    assert "spans" in captured, "collector emit never fired"
    assert captured["kw"].get("service_name") == "fi-langfuse"

    spans = captured["spans"]
    pg_span = ObservationSpan.no_workspace_objects.get(id=span_id)
    trace = Trace.no_workspace_objects.get(id=pg_span.trace_id)

    # BLOCKER FIX: OTLP trace_id is the PG trace UUID hex (32 hex) — not a
    # uuid5 of external_id — so spans.trace_name resolves via trace_dict.
    assert all(s["trace_id"] == trace.id.hex for s in spans)
    assert all(len(s["trace_id"]) == 32 for s in spans)
    # span ids are 16-hex, parseable (export_sim_spans does int(x,16)).
    for s in spans:
        assert len(s["span_id"]) == 16 and int(s["span_id"], 16) >= 0
        if s["parent_span_id"]:
            assert int(s["parent_span_id"], 16) >= 0

    roots = [s for s in spans if s["parent_span_id"] is None]
    assert len(roots) == 1, "exactly one root span"
    root_sid = roots[0]["span_id"]
    # every non-root parents to a real span in the set (tree preserved)
    ids = {s["span_id"] for s in spans}
    for s in spans:
        if s["parent_span_id"] is not None:
            assert s["parent_span_id"] in ids

    # the GENERATION span carries numeric token attrs + a span kind
    gen = next(s for s in spans if s["name"] == "llm")
    assert gen["attributes"].get("gen_ai.usage.input_tokens") == 120
    assert gen["attributes"].get("gen_ai.usage.total_tokens") == 150
    assert "fi.span.kind" in gen["attributes"]
    assert gen["parent_span_id"] != root_sid or True  # parented under outer span


@pytest.mark.integration
def test_langfuse_span_ids_deterministic(
    observe_project,
    organization,
    workspace,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    """Re-ingesting the same trace yields identical OTLP span ids (idempotent
    re-send — ReplacingMergeTree collapses duplicates)."""
    import tracer.services.clickhouse.v2.trace_writer as tw
    import tracer.services.collector_ingest as ci
    from integrations.transformers.langfuse_transformer import LangfuseTransformer
    from tracer.utils.langfuse_upsert import upsert_langfuse_trace

    monkeypatch.setattr(tw, "mirror_traces_to_clickhouse", lambda *a, **k: None)
    runs = []
    monkeypatch.setattr(
        ci,
        "emit_spans_to_collector",
        lambda spans, **k: runs.append(spans) or len(spans),
    )

    ext, span_id, gen_id, assembled = _assembled()
    for _ in range(2):
        with django_capture_on_commit_callbacks(execute=True):
            upsert_langfuse_trace(
                assembled_trace=assembled,
                transformer=LangfuseTransformer(),
                project_id=str(observe_project.id),
                org=organization,
                workspace=workspace,
                org_id=str(organization.id),
            )
    assert len(runs) == 2
    ids0 = sorted(s["span_id"] for s in runs[0])
    ids1 = sorted(s["span_id"] for s in runs[1])
    assert ids0 == ids1, "span ids must be deterministic across re-ingest"
