"""Trace-row tag residuals must not invent child-filter witness columns."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from tracer.models.entity_tag_assignment import EntityTagAssignment
from tracer.models.eval_task import EvalTask, RunType
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.trace import Trace
from tracer.selectors.eval_tasks.row_resolver import _trace_any_span_filter_descriptors
from tracer.services.eval_tasks.reconciler import reconcile
from tracer.tests._ch_seed import _get_ch_client, seed_ch_spans


def _leaf(column="tag", col_type="SYSTEM_METRIC", op="equals", value="cohort"):
    return {
        "column_id": column,
        "filter_config": {
            "col_type": col_type,
            "filter_type": "text",
            "filter_op": op,
            "filter_value": value,
        },
    }


@pytest.mark.parametrize("column", ["tag", "tags"])
@pytest.mark.parametrize("col_type", ["SYSTEM_METRIC", "NORMAL", ""])
def test_tag_residual_does_not_consume_a_witness_ordinal(column, col_type):
    assert _trace_any_span_filter_descriptors(
        [
            _leaf(column, col_type),
            _leaf("locale", "SPAN_ATTRIBUTE", "in", ["en"]),
            _leaf(column, col_type),
            _leaf("customer_tier", "SPAN_ATTRIBUTE", "in", ["vip"]),
        ]
    ) == (("locale", "SPAN_ATTRIBUTE"), ("customer_tier", "SPAN_ATTRIBUTE"))


def test_attribute_named_tag_still_has_an_exact_child_witness():
    assert _trace_any_span_filter_descriptors(
        [_leaf("tag", "SPAN_ATTRIBUTE"), _leaf("tags", "SPAN_ATTRIBUTE")]
    ) == (("tag", "SPAN_ATTRIBUTE"), ("tags", "SPAN_ATTRIBUTE"))


@pytest.mark.django_db
@pytest.mark.integration
@pytest.mark.parametrize("op", ["equals", "in", "not_in"])
@pytest.mark.parametrize("with_child_filter", [False, True])
def test_trace_tag_preview_and_reconcile_use_real_classifier_witnesses(
    project,
    custom_eval_config,
    ch_seed,
    auth_client,
    settings,
    op,
    with_child_filter,
):
    """Exercise row_type=traces, not trace sampling of the default span rows."""
    settings.EVAL_SAMPLING_CLOSED_SETTLE_SECONDS = 0
    cache.clear()
    now = timezone.now()
    client = _get_ch_client()
    try:
        assert client.query("SELECT version()").result_rows[0][0].startswith("25.")
        columns = {
            row[0]: row[1] for row in client.query("DESCRIBE TABLE traces").result_rows
        }
        assert columns["id"] == "UUID"
    finally:
        client.close()

    traces = {}
    children = {}
    spans = []
    for source in ("sdk", "manual", "excluded"):
        trace = Trace.objects.create(project=project, name=f"tag-witness-{source}")
        traces[source] = str(trace.id)
        root = ObservationSpan.objects.create(
            id=uuid.uuid4().hex[:16],
            project=project,
            trace=trace,
            name="root",
            observation_type="llm",
            start_time=now - timedelta(minutes=5),
        )
        child = ObservationSpan.objects.create(
            id=uuid.uuid4().hex[:16],
            project=project,
            trace=trace,
            parent_span_id=root.id,
            name="child",
            observation_type="llm",
            start_time=now - timedelta(minutes=5) + timedelta(seconds=1),
            span_attributes={
                "locale": "en",
                **({"tag.tags": '["cohort"]'} if source == "sdk" else {}),
            },
        )
        children[str(trace.id)] = child
        spans.extend([root, child])
    seed_ch_spans(spans)
    EntityTagAssignment.objects.create(
        project=project,
        entity_kind="trace",
        trace_id=traces["manual"],
        span_id="",
        start_hour=datetime(1970, 1, 1, tzinfo=UTC),
        observation_type="",
        service_name="",
        manual_tags=[{"name": "cohort"}],
    )
    leaves = [_leaf(op=op, value="cohort" if op == "equals" else ["cohort"])]
    if with_child_filter:
        leaves.append(_leaf("locale", "SPAN_ATTRIBUTE", "in", ["en"]))
    filters = {"filters": leaves}
    policy = {
        "version": 1,
        "sampling_unit": "trace",
        "mode": "closed",
        "window": {
            "start": (now - timedelta(hours=1)).isoformat(),
            "end": (now - timedelta(minutes=1)).isoformat(),
        },
        "default": {"rate": 100},
    }
    expected = (
        {traces["excluded"]} if op == "not_in" else {traces["sdk"], traces["manual"]}
    )
    preview = auth_client.post(
        "/tracer/eval-task/sampling_preview/",
        {
            "project": str(project.id),
            "row_type": "traces",
            "sampling_policy": policy,
            "filters": filters,
        },
        format="json",
    )
    assert preview.status_code == 200, preview.content
    assert sum(
        cell["eligible_rows"] for cell in preview.data["result"]["strata"]
    ) == len(expected)
    assert sum(
        cell["selected_rows"] for cell in preview.data["result"]["strata"]
    ) == len(expected)
    task = EvalTask.objects.create(
        project=project,
        row_type="traces",
        run_type=RunType.HISTORICAL,
        filters=filters,
        sampling_policy=policy,
    )
    task.evals.add(custom_eval_config)
    reconcile(task)
    entries = list(EvalLogger.objects.filter(eval_task_id=str(task.id)))
    assert {str(entry.trace_id) for entry in entries} == expected
    assert len(entries) == len(expected)
    for entry in entries:
        selection = entry.output_metadata["_task_selection"]
        witnesses = selection.get("filter_witnesses", [])
        if with_child_filter:
            assert len(witnesses) == 1
            assert witnesses[0]["filter_ordinal"] == 0
            assert witnesses[0]["column_id"] == "locale"
            assert witnesses[0]["span_id"] == children[str(entry.trace_id)].id
        else:
            assert not witnesses

    # An unchanged proof must not duplicate or take ownership of new entries.
    original_ids = {entry.id for entry in entries}
    reconcile(task)
    assert (
        set(
            EvalLogger.objects.filter(eval_task_id=str(task.id)).values_list(
                "id", flat=True
            )
        )
        == original_ids
    )
