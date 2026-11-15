"""Manual-assignment discovery must not depend on a new telemetry arrival."""

from datetime import UTC, datetime, timedelta

import pytest
from django.utils import timezone

from tracer.models.entity_tag_assignment import EntityTagAssignment
from tracer.models.eval_task import EvalTask, RunType
from tracer.models.observation_span import EvalLogger
from tracer.services.eval_tasks.reconciler import reconcile
from tracer.services.eval_tasks.sampling.service import preview_policy
from tracer.tests._ch_seed import seed_ch_spans
from tracer.tests.test_eval_sampling_clickhouse import seed_contact

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def tag_filters():
    return {
        "filters": [
            {
                "column_id": "tag",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "manual-discovery",
                },
            }
        ]
    }


@pytest.mark.parametrize("row_type", ["spans", "traces"])
def test_live_poll_discovers_manual_edit_behind_telemetry_cursor(
    project, custom_eval_config, ch_seed, row_type
):
    now = timezone.now()
    spans = seed_contact(project, None, {}, traces=1, seed=False)
    # Physical versions, event times and creation times all precede the normal
    # cursor overlap. No CH writes occur after the first reconcile below.
    for span in spans:
        span.start_time = now - timedelta(hours=2)
        span.created_at = now - timedelta(hours=2)
        span.updated_at = span.created_at
    seed_ch_spans(spans, version_from_created_at=True)
    task = EvalTask.objects.create(
        project=project,
        name="manual-assignment-discovery",
        row_type=row_type,
        run_type=RunType.CONTINUOUS,
        filters=tag_filters(),
        sampling_policy={
            "version": 1,
            "sampling_unit": "trace",
            "mode": "live",
            "window": {"start": (now - timedelta(days=1)).isoformat(), "end": None},
            "default": {"rate": 100},
        },
    )
    task.evals.add(custom_eval_config)
    reconcile(task)
    task.refresh_from_db()
    assert task.continuous_cursor > spans[0].created_at + timedelta(minutes=5)
    assert not EvalLogger.objects.filter(eval_task_id=str(task.pk)).exists()

    assignment = EntityTagAssignment.objects.create(
        project=project,
        entity_kind="trace",
        trace_id=str(spans[0].trace_id),
        span_id="",
        start_hour=datetime(1970, 1, 1, tzinfo=UTC),
        observation_type="",
        service_name="",
        manual_tags=[{"name": "manual-discovery"}],
    )
    assert sum(cell["eligible_rows"] for cell in preview_policy(task)["strata"]) == 1
    reconcile(task)
    entries = EvalLogger.objects.filter(eval_task_id=str(task.pk))
    assert entries.count() == 1, (
        "manual edit was visible in preview but never discovered by live polling"
    )
    entry = entries.get()
    assert str(entry.trace_id) == str(spans[0].trace_id)
    if row_type == "spans":
        assert entry.observation_span_id == spans[0].id

    # Empty assignments remain discoverable (negative filters can enter), while
    # positive-filter exits use the existing current-state pending sweep.
    from tracer.models.eval_sampling import EvalSamplingMember

    frozen = list(
        EvalSamplingMember.objects.filter(cohort__task=task).values_list(
            "unit_id", "selected", "stratum", "inclusion_probability"
        )
    )
    assignment.manual_tags = []
    assignment.revision = 2
    assignment.save()
    reconcile(task)
    assert not entries.exists()
    assignment.manual_tags = [{"name": "manual-discovery"}]
    assignment.revision = 3
    assignment.save()
    reconcile(task)
    assert entries.count() == 1
    assert (
        list(
            EvalSamplingMember.objects.filter(cohort__task=task).values_list(
                "unit_id", "selected", "stratum", "inclusion_probability"
            )
        )
        == frozen
    )


def old_spans(project, *, traces=1):
    spans = seed_contact(project, None, {}, traces=traces, seed=False)
    old = timezone.now() - timedelta(hours=2)
    for span in spans:
        span.start_time = span.created_at = span.updated_at = old
    seed_ch_spans(spans, version_from_created_at=True)
    return spans


def live_task(project, config, *, op="equals"):
    filters = tag_filters()
    filters["filters"][0]["filter_config"]["filter_op"] = op
    task = EvalTask.objects.create(
        project=project,
        row_type="spans",
        run_type=RunType.CONTINUOUS,
        filters=filters,
        sampling_policy={
            "version": 1,
            "sampling_unit": "trace",
            "mode": "live",
            "window": {
                "start": (timezone.now() - timedelta(days=1)).isoformat(),
                "end": None,
            },
            "default": {"rate": 100},
        },
    )
    task.evals.add(config)
    return task


def root_assignment(project, span, tags=None):
    return EntityTagAssignment.objects.create(
        project=project,
        entity_kind="trace",
        trace_id=str(span.trace_id),
        span_id="",
        start_hour=datetime(1970, 1, 1, tzinfo=UTC),
        observation_type="",
        service_name="",
        manual_tags=[{"name": "manual-discovery"}] if tags is None else tags,
    )


def telemetry_rows(project):
    from tracer.tests._ch_seed import _get_ch_client

    client = _get_ch_client()
    try:
        trace_types = {
            row[0]: row[1] for row in client.query("DESCRIBE TABLE traces").result_rows
        }
        span_types = {
            row[0]: row[1] for row in client.query("DESCRIBE TABLE spans").result_rows
        }
        assert trace_types["id"] == "UUID"
        assert span_types["trace_id"] == "String"
        return client.query(
            "SELECT id, _version, tags, attrs_string FROM spans "
            "WHERE project_id = {project:UUID} ORDER BY id, _version",
            parameters={"project": str(project.pk)},
        ).result_rows
    finally:
        client.close()


@pytest.mark.parametrize("target_kind", ["trace", "root-span", "child-span"])
def test_authenticated_edit_reaches_live_sampling_without_telemetry_write(
    project, custom_eval_config, ch_seed, auth_client, target_kind
):
    spans = old_spans(project)
    task = live_task(project, custom_eval_config)
    reconcile(task)
    before = telemetry_rows(project)
    span = spans[1] if target_kind == "child-span" else spans[0]
    version = next(row[1] for row in before if row[0] == span.id)
    body = {
        "project_id": str(project.pk),
        "trace_id": str(span.trace_id),
        "span_id": span.id,
        "start_hour": span.start_time.replace(
            minute=0, second=0, microsecond=0
        ).isoformat(),
        "observation_type": span.observation_type,
        "service_name": getattr(span, "service_name", None) or "",
        "expected_start_time": span.start_time.isoformat(),
        "expected_version": str(version),
        "expected_tag_revision": 0,
        "tags": [{"name": "manual-discovery"}],
    }
    if target_kind == "trace":
        response = auth_client.patch(
            f"/tracer/trace/{span.trace_id}/tags/", body, format="json"
        )
    else:
        response = auth_client.post(
            "/tracer/observation-span/update-tags/", body, format="json"
        )
    assert response.status_code == 200, response.content
    saved = EntityTagAssignment.objects.get(project=project)
    assert saved.entity_kind == ("span" if target_kind == "child-span" else "trace")
    assert saved.manual_tags == [{"name": "manual-discovery"}]
    assert saved.revision == 1
    assert sum(cell["eligible_rows"] for cell in preview_policy(task)["strata"]) == 1
    reconcile(task)
    assert set(
        EvalLogger.objects.filter(eval_task_id=str(task.pk)).values_list(
            "observation_span_id", flat=True
        )
    ) == {span.id}
    assert telemetry_rows(project) == before


@pytest.mark.parametrize("terminal", ["errored", "skipped"])
@pytest.mark.parametrize("changed", ["telemetry", "config"])
def test_assignment_sweeps_do_not_retry_unchanged_terminal_rows(
    project, custom_eval_config, ch_seed, terminal, changed
):
    from tracer.models.observation_span import EvalEntryStatus

    spans = old_spans(project)
    root_assignment(project, spans[0])
    task = live_task(project, custom_eval_config)
    reconcile(task)
    entries = EvalLogger.objects.filter(eval_task_id=str(task.pk))
    entry = entries.get()
    terminal_at = timezone.now() - timedelta(minutes=20)
    entries.update(status=terminal, updated_at=terminal_at)
    before = telemetry_rows(project)
    for _ in range(3):
        result = reconcile(task)
        entry.refresh_from_db()
        task.refresh_from_db()
        assert task.continuous_cursor > terminal_at
        assert result.requeued == 0, "an assignment revisit is not a telemetry change"
        assert entry.status == terminal
        assert entry.updated_at == terminal_at
    assert telemetry_rows(project) == before

    # Withholding telemetry proof from assignment-only pages must not disable
    # either genuine arrival-delta retries or stale-config retries.
    if changed == "telemetry":
        spans[0].span_attributes = {"retry-input": "changed"}
        seed_ch_spans([spans[0]])
    else:
        entries.update(config_hash="stale-config")
    result = reconcile(task)
    entry.refresh_from_db()
    assert result.requeued == 1
    assert entry.status == EvalEntryStatus.PENDING


def test_negative_filter_discovers_removal_from_old_root(
    project, custom_eval_config, ch_seed
):
    spans = old_spans(project)
    assignment = root_assignment(project, spans[0])
    task = live_task(project, custom_eval_config, op="not_equals")
    reconcile(task)
    # The child was already eligible; the root's inclusion is due only to the
    # removal, not a new membership hash or telemetry version.
    entries = EvalLogger.objects.filter(eval_task_id=str(task.pk))
    assert set(entries.values_list("observation_span_id", flat=True)) == {spans[1].id}
    assignment.manual_tags = []
    assignment.revision = 2
    assignment.save()
    reconcile(task)
    assert set(entries.values_list("observation_span_id", flat=True)) == {
        span.id for span in spans
    }


def test_interrupted_reconcile_does_not_checkpoint_assignment_page(
    project, custom_eval_config, ch_seed, monkeypatch
):
    from tracer.models.eval_sampling import EvalSamplingCohort
    from tracer.services.eval_tasks.sampling import pending, tag_discovery

    spans = old_spans(project, traces=2)
    task = live_task(project, custom_eval_config)
    reconcile(task)
    for span in (spans[0], spans[2]):
        root_assignment(project, span)
    monkeypatch.setattr(tag_discovery, "ASSIGNMENT_PAGE_SIZE", 1)
    cohort = EvalSamplingCohort.objects.get(task=task)
    before = cohort.selection_context.copy()
    real_revalidate = pending.revalidate_pending

    def interrupted(*args, **kwargs):
        raise TimeoutError("interrupted after materialization")

    monkeypatch.setattr(pending, "revalidate_pending", interrupted)
    with pytest.raises(TimeoutError, match="interrupted after"):
        reconcile(task)
    cohort.refresh_from_db()
    assert cohort.selection_context == before
    assert EvalLogger.objects.filter(eval_task_id=str(task.pk)).count() == 1
    monkeypatch.setattr(pending, "revalidate_pending", real_revalidate)
    reconcile(task)
    cohort.refresh_from_db()
    assert cohort.selection_context != before
    reconcile(task)
    assert set(
        EvalLogger.objects.filter(eval_task_id=str(task.pk)).values_list(
            "observation_span_id", flat=True
        )
    ) == {spans[0].id, spans[2].id}


@pytest.mark.parametrize("row_type", ["spans", "traces"])
def test_assignment_replay_respects_scope_window_snapshot_and_shared_roots(
    project, custom_eval_config, ch_seed, row_type
):
    import uuid

    from tracer.models.observation_span import ObservationSpan
    from tracer.services.eval_tasks.sampling.query import SamplingQuery, stream_query
    from tracer.services.eval_tasks.sampling.service import task_policy

    now = timezone.now()
    spans = old_spans(project, traces=2)
    extras = []
    for event, arrival in [
        (now - timedelta(hours=1), now + timedelta(minutes=5)),
        (now - timedelta(days=2), now - timedelta(hours=2)),
        (now + timedelta(days=1), now - timedelta(hours=2)),
    ]:
        root = ObservationSpan.objects.create(
            id=uuid.uuid4().hex[:16],
            project=project,
            trace=spans[0].trace,
            parent_span_id=None,
            name="shared-root",
            observation_type="llm",
            start_time=event,
        )
        root.created_at = root.updated_at = arrival
        extras.append(root)
    seed_ch_spans(extras, version_from_created_at=True)
    root_assignment(project, spans[0])
    root_assignment(project, spans[2])
    task = live_task(project, custom_eval_config)
    task.row_type = row_type
    policy, version = task_policy(task, now=now)

    def rows(snapshot):
        return [
            row
            for block in stream_query(
                *SamplingQuery(task, policy, version, snapshot).live_assignment_rows(
                    [str(spans[0].trace_id)]
                )
            )
            for row in block
        ]

    expected_first = {spans[0].id} if row_type == "spans" else {str(spans[0].trace_id)}
    assert {row[0] for row in rows(now)} == expected_first
    expected_later = (
        {spans[0].id, extras[0].id} if row_type == "spans" else expected_first
    )
    assert {row[0] for row in rows(now + timedelta(minutes=10))} == expected_later
