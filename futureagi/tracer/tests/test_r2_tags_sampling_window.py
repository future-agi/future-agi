"""Real PostgreSQL + native CH25 cross-midnight sampling regressions.

Only the sampling clock and inherited Temporal enqueue seam are controlled.
Queries, source membership, primary-PG assignments, API-key authentication,
reconciliation, materialization and checkpoints use production implementations.
Synthetic source arrival versions are supplied by the existing CH seed adapter.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from rest_framework.test import APIClient

from accounts.models.user import OrgApiKey
from tracer.models.entity_tag_assignment import EntityTagAssignment
from tracer.models.eval_sampling import EvalSamplingCohort, EvalSamplingMember
from tracer.models.eval_task import EvalTask
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.trace import Trace
from tracer.services.clickhouse.query_builders.time_series import TimeSeriesQueryBuilder
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.effective_tags import prepare_effective_tag_params
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService
from tracer.services.eval_tasks.reconciler import reconcile
from tracer.services.eval_tasks.sampling import service
from tracer.services.eval_tasks.sampling.query import live_scan_windows
from tracer.tests._ch_seed import seed_ch_spans

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

START = datetime(2026, 11, 13, tzinfo=UTC)
MIDNIGHT = START + timedelta(days=1)
ROOT_AT = MIDNIGHT - timedelta(minutes=1)
CHILD_AT = MIDNIGHT + timedelta(minutes=1)
FLOOR = MIDNIGHT + timedelta(minutes=5)
SNAPSHOT = MIDNIGHT + timedelta(minutes=10)


def tag_leaf(column="tag", value="cohort", *, col_type="SYSTEM_METRIC"):
    return {
        "column_id": column,
        "filter_config": {
            "col_type": col_type,
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def time_leaf(start=START, end=SNAPSHOT):
    return {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start.isoformat(), end.isoformat()],
        },
    }


@pytest.fixture
def native_client(user, workspace, organization):
    """Authenticate through the actual credential/primary-membership checks."""
    key = OrgApiKey.objects.create(
        organization=organization,
        workspace=workspace,
        user=user,
        type="user",
        enabled=True,
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    return client


@pytest.fixture
def native_statements(monkeypatch):
    """Observe actual driver rendering without replacing any executed query."""
    from clickhouse_driver import Client

    from tracer.services.eval_tasks.sampling.query import READ_SETTINGS

    rendered = []
    original = Client.substitute_params

    def record(client, query, params, context):
        statement = original(client, query, params, context)
        if "sp_project" in params:
            for name in (
                "max_execution_time",
                "max_query_size",
                "max_rows_to_read",
                "max_bytes_to_read",
                "max_memory_usage",
                "max_result_rows",
                "max_result_bytes",
            ):
                assert 0 < context.settings[name] <= READ_SETTINGS[name]
            assert context.settings["read_overflow_mode"] == "throw"
            assert context.settings["result_overflow_mode"] == "throw"
            rendered.append((query, dict(params), statement))
        return statement

    monkeypatch.setattr(Client, "substitute_params", record)
    return rendered


@pytest.fixture
def clock(monkeypatch):
    current = [SNAPSHOT]
    monkeypatch.setattr(service, "sampling_clock", lambda: current[0])
    return current


def seed_pair(project, *, child_at=CHILD_AT, tags=("cohort",), seed_child=True):
    trace = Trace.objects.create(project=project, name="midnight-synthetic")
    root = ObservationSpan.objects.create(
        id=uuid.uuid4().hex[:16],
        project=project,
        trace=trace,
        name="midnight-root",
        observation_type="llm",
        start_time=ROOT_AT,
        total_tokens=1,
    )
    child = ObservationSpan.objects.create(
        id=uuid.uuid4().hex[:16],
        project=project,
        trace=trace,
        parent_span_id=root.id,
        name="midnight-child",
        observation_type="llm",
        start_time=child_at,
        total_tokens=2,
        span_attributes={"tag.tags": json.dumps(list(tags))},
    )
    root.created_at = root.updated_at = MIDNIGHT
    child.created_at = child.updated_at = FLOOR + timedelta(minutes=1)
    seed_ch_spans([root, child] if seed_child else [root], version_from_created_at=True)
    return root, child


def create_task(
    client,
    project,
    config,
    *,
    row_type="traces",
    leaves=None,
    mode="live",
    start=START,
    end=None,
):
    policy = {
        "version": 1,
        "mode": mode,
        "sampling_unit": "trace" if row_type == "traces" else "span",
        "window": {"start": start.isoformat(), "end": end.isoformat() if end else None},
        "default": {"rate": 100},
    }
    response = client.post(
        "/tracer/eval-task/",
        {
            "project": str(project.id),
            "name": "cross-midnight native sampling",
            "row_type": row_type,
            "run_type": "continuous",
            "sampling_rate": 100,
            "sampling_policy": policy,
            "evals": [str(config.id)],
            "filters": {"filters": leaves if leaves is not None else [tag_leaf()]},
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    task = EvalTask.objects.get(pk=response.data["result"]["id"])
    assert task.row_type == row_type
    return task


def matched_list_and_graph(project, root):
    analytics = V2AnalyticsQueryService()
    filters = [time_leaf(), tag_leaf()]
    query, params = TraceListQueryBuilderV2(
        project_id=str(project.id),
        filters=filters,
    ).build_filter_match_query([str(root.trace_id)])
    params = prepare_effective_tag_params(
        analytics.execute_ch_query,
        params,
        deadline=ReadDeadline.start(30000),
    )
    listed = analytics.execute_ch_query(query, params).data
    assert {str(row["trace_id"]) for row in listed} == {str(root.trace_id)}
    query, params = TimeSeriesQueryBuilder(
        project_id=str(project.id),
        filters=filters,
        interval="hour",
        observe_type="trace",
        exact_snapshot=True,
        resolve_span_versions=True,
        start_date=START,
        end_date=SNAPSHOT,
    ).build()
    graphed = analytics.execute_ch_query(query, params).data
    assert sum(int(row["total_tokens"]) for row in graphed) == 3


def entries(task):
    return EvalLogger.objects.filter(eval_task_id=str(task.id), deleted=False)


def evidence(task, stage):
    task.refresh_from_db()
    print(
        json.dumps(
            {
                "stage": stage,
                "members": list(
                    EvalSamplingMember.objects.filter(cohort__task=task).values(
                        "unit_id", "selected", "inclusion_probability"
                    )
                ),
                "entry_trace_ids": [
                    str(value)
                    for value in entries(task).values_list("trace_id", flat=True)
                ],
                "cursor": task.continuous_cursor.isoformat()
                if task.continuous_cursor
                else None,
                "cohort_context": list(
                    EvalSamplingCohort.objects.filter(task=task).values_list(
                        "selection_context", flat=True
                    )
                ),
            },
            sort_keys=True,
            default=str,
        )
    )


def test_sdk_child_across_midnight_is_admitted_after_arrival_floor(
    project,
    custom_eval_config,
    ch_seed,
    native_client,
    clock,
    native_statements,
):
    root, child = seed_pair(project, seed_child=False)
    task = create_task(native_client, project, custom_eval_config)
    assert not EntityTagAssignment.objects.filter(project=project).exists()
    clock[0] = FLOOR
    reconcile(task)
    assert not entries(task).exists()
    assert not EvalSamplingMember.objects.filter(cohort__task=task).exists()
    # Force a true incremental pass: only the child's physical arrival crosses
    # this floor. Its root is on the preceding event day and predates the floor.
    EvalTask.objects.filter(pk=task.pk).update(continuous_cursor=FLOOR)
    seed_ch_spans([child], version_from_created_at=True)
    clock[0] = SNAPSHOT
    task.refresh_from_db()
    policy, _ = service.task_policy(task, now=SNAPSHOT)
    windows = list(live_scan_windows(task, policy, SNAPSHOT, FLOOR))
    assert windows == [{"start": START.isoformat(), "end": MIDNIGHT.isoformat()}]
    matched_list_and_graph(project, root)
    reconcile(task)
    evidence(task, "after-child-arrival")
    print(
        json.dumps(
            {
                "stage": "native-statements",
                "queries": [
                    {
                        "bounds": {
                            key: value
                            for key, value in params.items()
                            if key
                            in {
                                "sp_start",
                                "sp_end",
                                "sp_snapshot",
                                "sp_arrival_floor_ns",
                            }
                            or ("tag" in key and key.endswith(("_start", "_end")))
                        },
                        "rendered_bytes": len(statement.encode()),
                        "shared_bindings": "__effective_tag_bind_" in query,
                    }
                    for query, params, statement in native_statements
                ],
            },
            sort_keys=True,
            default=str,
        )
    )
    assert list(entries(task).values_list("trace_id", flat=True)) == [root.trace_id]
    member = EvalSamplingMember.objects.get(cohort__task=task)
    assert member.unit_id == f"trace:{root.trace_id}"
    assert member.selected and member.inclusion_probability == 1
    from tracer.services.eval_tasks.cursor_policy import CONTINUOUS_CURSOR_OVERLAP

    assert task.continuous_cursor == max(FLOOR, SNAPSHOT - CONTINUOUS_CURSOR_OVERLAP)
    daily = [
        params
        for _, params, _ in native_statements
        if params.get("sp_snapshot") == "2026-11-14 00:10:00.000000"
        and params.get("sp_end") == "2026-11-14 00:00:00.000000"
    ]
    assert daily
    for params in daily:
        assert params["sp_start"] == "2026-11-13 00:00:00.000000"
        assert params["sp_floor"] == "2026-11-14 00:05:00.000000"
        prefixes = [
            name.removesuffix("_assigned_keys")
            for name in params
            if name.endswith("_assigned_keys")
        ]
        assert prefixes
        for prefix in prefixes:
            assert params[f"{prefix}_start"] == "2026-11-13 00:00:00.000000"
            assert params[f"{prefix}_end"] == "2026-11-14 00:10:00.000000"
    reconcile(task)
    evidence(task, "repeat-poll")
    assert entries(task).count() == 1
    assert EvalSamplingMember.objects.filter(cohort__task=task).count() == 1


@pytest.mark.parametrize("mode", ["live", "closed"])
def test_initial_cross_midnight_snapshot_and_closed_window(
    project,
    custom_eval_config,
    ch_seed,
    native_client,
    clock,
    settings,
    mode,
):
    settings.EVAL_SAMPLING_CLOSED_SETTLE_SECONDS = 0
    root, _child = seed_pair(project)
    task = create_task(
        native_client,
        project,
        custom_eval_config,
        mode=mode,
        end=SNAPSHOT if mode == "closed" else None,
    )
    response = native_client.post(
        "/tracer/eval-task/sampling_preview/",
        {
            "project": str(project.id),
            "row_type": "traces",
            "sampling_policy": task.sampling_policy,
            "filters": task.filters,
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert sum(cell["eligible_rows"] for cell in response.data["result"]["strata"]) == 1
    reconcile(task)
    evidence(task, f"initial-{mode}")
    assert list(entries(task).values_list("trace_id", flat=True)) == [root.trace_id]
    assert EvalSamplingMember.objects.get(cohort__task=task).selected


@pytest.mark.parametrize(
    "case",
    [
        "before-policy",
        "at-snapshot",
        "after-snapshot",
        "nonmatching",
        "after-arrival-snapshot",
        "after-closed-end",
    ],
)
def test_child_outside_eligibility_does_not_admit_trace(
    project,
    custom_eval_config,
    ch_seed,
    native_client,
    clock,
    settings,
    case,
):
    child_at = {
        "before-policy": START - timedelta(microseconds=1),
        "at-snapshot": SNAPSHOT,
        "after-snapshot": SNAPSHOT + timedelta(microseconds=1),
        "after-closed-end": CHILD_AT,
    }.get(case, CHILD_AT)
    root, child = seed_pair(
        project,
        child_at=child_at,
        tags=("other",) if case == "nonmatching" else ("cohort",),
        seed_child=False,
    )
    if case == "after-arrival-snapshot":
        child.created_at = child.updated_at = SNAPSHOT + timedelta(seconds=1)
    seed_ch_spans([child], version_from_created_at=True)
    settings.EVAL_SAMPLING_CLOSED_SETTLE_SECONDS = 0
    task = create_task(
        native_client,
        project,
        custom_eval_config,
        mode="closed" if case == "after-closed-end" else "live",
        end=MIDNIGHT if case == "after-closed-end" else None,
    )
    reconcile(task)
    evidence(task, case)
    assert not entries(task).exists()
    assert not EvalSamplingMember.objects.filter(cohort__task=task).exists()


def assign(span, names, *, trace_assignment=False):
    return EntityTagAssignment.objects.create(
        project_id=span.project_id,
        entity_kind="trace" if trace_assignment else "span",
        trace_id=str(span.trace_id),
        span_id="" if trace_assignment else str(span.id),
        start_hour=datetime(1970, 1, 1, tzinfo=UTC)
        if trace_assignment
        else span.start_time.replace(minute=0, second=0, microsecond=0),
        observation_type="" if trace_assignment else span.observation_type,
        service_name="" if trace_assignment else (span.service_name or ""),
        manual_tags=[{"name": name} for name in names],
    )


def test_manual_only_cross_midnight_edit_and_removal_without_telemetry(
    project,
    custom_eval_config,
    ch_seed,
    native_client,
    clock,
):
    root, child = seed_pair(project, tags=())
    task = create_task(native_client, project, custom_eval_config)
    reconcile(task)
    assert not entries(task).exists()
    EvalTask.objects.filter(pk=task.pk).update(continuous_cursor=SNAPSHOT)
    clock[0] = SNAPSHOT + timedelta(minutes=1)
    task.refresh_from_db()
    policy, _ = service.task_policy(task, now=clock[0])
    assert list(live_scan_windows(task, policy, clock[0], SNAPSHOT)) == []
    assignment = assign(child, ["cohort"])
    reconcile(task)
    evidence(task, "manual-edit-only")
    assert list(entries(task).values_list("trace_id", flat=True)) == [root.trace_id]
    context = EvalSamplingCohort.objects.get(task=task).selection_context
    state = context["manual_tag_discovery"]
    assert state["after"] == state["through"]
    assert state["after"][1:3] == [str(child.trace_id), str(child.id)]
    assignment.manual_tags = []
    assignment.save(update_fields=["manual_tags"])
    reconcile(task)
    evidence(task, "manual-removed")
    assert not entries(task).exists()
    # Admission remains a stable frozen decision, not a fresh draw after edits.
    assert EvalSamplingMember.objects.filter(cohort__task=task).count() == 1
    assignment.manual_tags = [{"name": "cohort"}]
    assignment.save(update_fields=["manual_tags"])
    reconcile(task)
    reconcile(task)
    assert entries(task).count() == 1
    assert EvalSamplingMember.objects.filter(cohort__task=task).count() == 1


@pytest.mark.parametrize("kind", ["tag", "tags", "attribute", "no-tag"])
def test_span_membership_does_not_inherit_a_sibling_or_root_tag(
    project,
    custom_eval_config,
    ch_seed,
    native_client,
    clock,
    kind,
    native_statements,
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    root, child = seed_pair(project, tags=(), seed_child=False)
    # The literal raw 'tag' attribute is deliberately not native tag.tags.
    child.span_attributes = {"tag.tags": '["cohort"]', "tag": "literal"}
    seed_ch_spans([child], version_from_created_at=True)
    leaves = (
        []
        if kind == "no-tag"
        else [
            tag_leaf("tag", "literal", col_type="SPAN_ATTRIBUTE")
            if kind == "attribute"
            else tag_leaf(kind)
        ]
    )
    task = create_task(
        native_client, project, custom_eval_config, row_type="spans", leaves=leaves
    )
    with CaptureQueriesContext(connection) as pg_queries:
        reconcile(task)
    if kind in {"attribute", "no-tag"}:
        assert not any(
            "tracer_entity_tag_assignment" in query["sql"] for query in pg_queries
        )
        assert native_statements
        assert not any(
            name.endswith("_assigned_keys")
            for _, params, _ in native_statements
            for name in params
        )
    expected = {root.id, child.id} if kind == "no-tag" else {child.id}
    assert set(entries(task).values_list("observation_span_id", flat=True)) == expected
    assert EvalSamplingMember.objects.filter(cohort__task=task).count() == len(expected)
    reconcile(task)
    assert entries(task).count() == len(expected)


def test_native_plural_keeps_selected_root_semantics(
    project,
    custom_eval_config,
    ch_seed,
    native_client,
    clock,
):
    seed_pair(project)
    task = create_task(
        native_client, project, custom_eval_config, leaves=[tag_leaf("tags")]
    )
    reconcile(task)
    assert not entries(task).exists()
    assert not EvalSamplingMember.objects.filter(cohort__task=task).exists()


def test_task_route_rejects_an_explicit_excluded_date_window(
    project,
    custom_eval_config,
    native_client,
):
    exclusion = time_leaf(MIDNIGHT, SNAPSHOT)
    exclusion["filter_config"]["filter_op"] = "not_between"
    response = native_client.post(
        "/tracer/eval-task/",
        {
            "project": str(project.id),
            "name": "invalid secondary date authority",
            "row_type": "traces",
            "run_type": "continuous",
            "sampling_rate": 100,
            "sampling_policy": {
                "version": 1,
                "mode": "live",
                "sampling_unit": "trace",
                "window": {"start": START.isoformat(), "end": None},
                "default": {"rate": 100},
            },
            "evals": [str(custom_eval_config.id)],
            "filters": {"filters": [tag_leaf(), exclusion]},
        },
        format="json",
    )
    assert response.status_code == 400
    assert "policy" in str(response.data).lower()
    assert not EvalTask.objects.filter(project=project).exists()


def test_assignment_sweep_keeps_the_real_64_identity_page(
    project,
    custom_eval_config,
    ch_seed,
    native_client,
    clock,
):
    from tracer.services.eval_tasks.sampling.tag_discovery import ASSIGNMENT_PAGE_SIZE

    assert ASSIGNMENT_PAGE_SIZE == 64
    root, _child = seed_pair(project, tags=())
    task = create_task(native_client, project, custom_eval_config)
    reconcile(task)
    EvalTask.objects.filter(pk=task.pk).update(continuous_cursor=SNAPSHOT)
    clock[0] = SNAPSHOT + timedelta(minutes=1)
    # 64 low-sorting identities have no source. The one live root is page two.
    for index in range(1, 65):
        EntityTagAssignment.objects.create(
            project=project,
            entity_kind="trace",
            trace_id=str(uuid.UUID(int=index)),
            span_id="",
            start_hour=datetime(1970, 1, 1, tzinfo=UTC),
            observation_type="",
            service_name="",
            manual_tags=[],
        )
    assign(root, ["cohort"], trace_assignment=True)
    reconcile(task)
    assert not entries(task).exists()
    first = EvalSamplingCohort.objects.get(task=task).selection_context[
        "manual_tag_discovery"
    ]
    assert first["after"][1] == str(uuid.UUID(int=64))
    assert first["through"][1] == str(root.trace_id)
    reconcile(task)
    evidence(task, "manual-second-page")
    assert list(entries(task).values_list("trace_id", flat=True)) == [root.trace_id]
    second = EvalSamplingCohort.objects.get(task=task).selection_context[
        "manual_tag_discovery"
    ]
    assert second["after"] == second["through"]


def test_failed_materialization_does_not_acknowledge_assignment_page(
    project,
    custom_eval_config,
    ch_seed,
    native_client,
    clock,
):
    """A real PG trigger rejects one write; no read or membership is mocked."""
    from django.db import DatabaseError, connection

    root, child = seed_pair(project, tags=())
    task = create_task(native_client, project, custom_eval_config)
    reconcile(task)
    EvalTask.objects.filter(pk=task.pk).update(continuous_cursor=SNAPSHOT)
    clock[0] = SNAPSHOT + timedelta(minutes=1)
    assign(child, ["cohort"])
    before = EvalSamplingCohort.objects.get(task=task).selection_context
    table = connection.ops.quote_name(EvalLogger._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(f"""CREATE FUNCTION pg_temp.task1520_fail_entry() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
            IF NEW.eval_task_id::text = '{task.pk}' THEN
                RAISE EXCEPTION 'task1520 controlled materialization failure';
            END IF; RETURN NEW; END $$""")
        cursor.execute(f"""CREATE TRIGGER task1520_fail_entry BEFORE INSERT ON {table}
            FOR EACH ROW EXECUTE FUNCTION pg_temp.task1520_fail_entry()""")
    try:
        with pytest.raises(
            DatabaseError, match="task1520 controlled materialization failure"
        ):
            reconcile(task)
        task.refresh_from_db()
        assert task.continuous_cursor == SNAPSHOT
        assert EvalSamplingCohort.objects.get(task=task).selection_context == before
        assert not entries(task).exists()
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TRIGGER task1520_fail_entry ON {table}")
            cursor.execute("DROP FUNCTION pg_temp.task1520_fail_entry()")
    reconcile(task)
    evidence(task, "materialization-retry")
    assert list(entries(task).values_list("trace_id", flat=True)) == [root.trace_id]
    after = EvalSamplingCohort.objects.get(task=task).selection_context[
        "manual_tag_discovery"
    ]
    assert after["after"] == after["through"]
    reconcile(task)
    assert entries(task).count() == 1


def test_two_native_tag_leaves_share_actual_rendered_bindings_and_budget(
    project,
    custom_eval_config,
    ch_seed,
    native_client,
    clock,
    native_statements,
):
    from tracer.services.clickhouse.v2.effective_tags import (
        bind_effective_tag_query,
        shared_tag_parameter_bytes,
    )
    from tracer.services.entity_tags import TagReadUnavailable
    from tracer.services.eval_tasks.sampling.query import READ_SETTINGS

    root, child = seed_pair(project)
    assign(child, ["cohort"])
    task = create_task(
        native_client, project, custom_eval_config, leaves=[tag_leaf(), tag_leaf()]
    )
    reconcile(task)
    assert list(entries(task).values_list("trace_id", flat=True)) == [root.trace_id]
    assert native_statements
    statements = [
        (sql, params, rendered)
        for sql, params, rendered in native_statements
        if len([key for key in params if key.endswith("_assigned_keys")]) == 2
    ]
    assert statements
    for sql, params, rendered in statements:
        assert "__effective_tag_bind_" in sql
        assert len(rendered.encode()) < READ_SETTINGS["max_query_size"]
        # Repeating prepared leaf constants adds zero unique native collection bytes.
        duplicated = {
            **params,
            **{
                f"repeat_{key}": value
                for key, value in params.items()
                if isinstance(value, (list, tuple))
            },
        }
        assert shared_tag_parameter_bytes(params) == shared_tag_parameter_bytes(
            duplicated
        )
        # Bound at the exact driver-rendered wire size, not an SQL-template estimate.
        with pytest.raises(TagReadUnavailable, match="tag_overlay_budget_exceeded"):
            bind_effective_tag_query(sql, params, max_query_size=len(rendered.encode()))
    print(
        json.dumps(
            {
                "stage": "two-tag-native-budget",
                "rendered_bytes": [
                    len(rendered.encode()) for _, _, rendered in statements
                ],
                "unique_collection_bytes": [
                    shared_tag_parameter_bytes(params) for _, params, _ in statements
                ],
            }
        )
    )
