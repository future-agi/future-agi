"""Queue-owned source boundaries, including rules/items predating validation.

No request context is present during worker evaluations. Only CH selection is
stubbed: the database, membership decisions, serializers and endpoints are real.
Queues and sources follow the managers' workspace rule: in the organization's
default workspace, rows with no workspace are in scope (a queue with no
workspace is read there); in any other workspace only exact matches are.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace, WorkspaceMembership
from conftest import WorkspaceAwareAPIClient
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AutomationRule,
    QueueItem,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.services.bulk_selection import ResolveResult
from model_hub.tasks.annotation_automation import run_due_automation_rules
from model_hub.tests.test_annotation_queues_api import create_workspace_member_user
from model_hub.tests.test_automation_rules_e2e import (
    _allow_automation_rule_entitlements_if_available,
)
from model_hub.utils.annotation_queue_helpers import (
    evaluate_rule,
    resolve_default_queue_item_for_source,
)
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)
from tracer.models.project import Project
from tracer.models.trace import Trace

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]
BASE_URL = "/model-hub/annotation-queues/"
SCOPE_ERROR = "Automation rule source is outside the queue workspace or unavailable."
UNAVAILABLE = "queue_source_unavailable"


@pytest.fixture
def world(organization, workspace, user):
    other = Workspace.no_workspace_objects.create(
        name="AQ-XWS B",
        organization=organization,
        created_by=user,
        is_default=False,
        is_active=True,
    )
    member = create_workspace_member_user(organization, workspace)
    queue = AnnotationQueue.no_workspace_objects.create(
        name="AQ-XWS unbound A",
        organization=organization,
        workspace=workspace,
        created_by=member,
        auto_assign=False,
    )
    AnnotationQueueAnnotator.no_workspace_objects.update_or_create(
        queue=queue,
        user=member,
        defaults={"role": "manager", "roles": ["manager", "reviewer", "annotator"]},
    )
    datasets, rows, projects, traces = {}, {}, {}, {}
    for key, ws in (("A", workspace), ("B", other)):
        datasets[key] = Dataset.no_workspace_objects.create(
            name=f"AQ-XWS-{key}-DATASET",
            organization=organization,
            workspace=ws,
        )
        rows[key] = Row.no_workspace_objects.create(dataset=datasets[key], order=1)
        column = Column.no_workspace_objects.create(
            dataset=datasets[key],
            name="input",
            data_type="text",
            source="OTHERS",
        )
        Cell.no_workspace_objects.create(
            dataset=datasets[key],
            row=rows[key],
            column=column,
            value=f"AQ-XWS-{key}-CELL",
        )
        projects[key] = Project.no_workspace_objects.create(
            name=f"AQ-XWS-{key}-PROJECT",
            organization=organization,
            workspace=ws,
            model_type="GenerativeLLM",
            trace_type="observe",
        )
        # Filter-mode uses a CH selector stub and soft trace locators. Only
        # legacy-ORM tests persist these deprecated PG rows.
        traces[key] = Trace(
            name=f"AQ-XWS-{key}-TRACE",
            project=projects[key],
            input={"message": key},
            output={"response": key},
        )
    assert queue.workspace_id == workspace.pk, "SETUP_AQ-XWS queue workspace"
    assert not queue.dataset_id and not queue.project_id, "SETUP_AQ-XWS unbound queue"
    assert not queue.is_default, "SETUP_AQ-XWS custom queue"
    assert rows["B"].dataset.workspace_id == other.pk, "SETUP_AQ-XWS B source"
    assert member.can_access_workspace(workspace), "SETUP_AQ-XWS A membership"
    assert not member.can_access_workspace(other), "SETUP_AQ-XWS no B membership"
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=member)
    client.set_workspace(workspace)
    with _allow_automation_rule_entitlements_if_available():
        yield SimpleNamespace(
            org=organization,
            a=workspace,
            b=other,
            member=member,
            queue=queue,
            datasets=datasets,
            rows=rows,
            projects=projects,
            traces=traces,
            client=client,
        )
    client.stop_workspace_injection()


def _conditions(world, source_type, side, mode):
    if mode == "filter":
        key = "dataset_id" if source_type == "dataset_row" else "project_id"
        source = (
            world.datasets[side]
            if source_type == "dataset_row"
            else world.projects[side]
        )
        return {"filter": [], "scope": {key: str(source.pk)}}
    field = "dataset__name" if source_type == "dataset_row" else "project__name"
    source = (
        world.datasets[side] if source_type == "dataset_row" else world.projects[side]
    )
    return {"rules": [{"field": field, "op": "eq", "value": source.name}]}


def _saved_rule(world, source_type, side, mode):
    return AutomationRule.no_workspace_objects.create(
        queue=world.queue,
        organization=world.org,
        created_by=world.member,
        name="AQ-XWS pre-existing",
        source_type=source_type,
        conditions=_conditions(world, source_type, side, mode),
        enabled=True,
        trigger_frequency="hourly",
    )


@pytest.mark.parametrize("method", ["create", "update", "partial_update"])
@pytest.mark.parametrize("side", ["A", "B"])
@pytest.mark.parametrize("source_type", ["dataset_row", "trace"])
def test_rule_scope_write_authorization(world, method, side, source_type):
    url = f"{BASE_URL}{world.queue.pk}/automation-rules/"
    payload = {
        "name": "AQ-XWS API",
        "source_type": source_type,
        "conditions": _conditions(world, source_type, side, "filter"),
        "enabled": True,
    }
    if method == "create":
        response = world.client.post(url, payload, format="json")
        accepted = 201
    else:
        rule = _saved_rule(world, source_type, "A", "filter")
        url += f"{rule.pk}/"
        send = world.client.put if method == "update" else world.client.patch
        response = send(url, payload, format="json")
        accepted = 200
    if side == "B":
        assert response.status_code == 400, (
            "AQ-XWS-F2: cross-workspace scope write must be refused"
        )
        assert response.data.get("attr") == "conditions", (
            "AQ-XWS-F2: retain conditions error envelope"
        )
        assert SCOPE_ERROR in str(response.data), (
            "AQ-XWS-F2: scope refusal must use fixed message"
        )
    else:
        assert response.status_code == accepted, (
            "AQ-XWS-F2: same-workspace scope write must succeed"
        )


@pytest.mark.parametrize("mode", ["filter", "legacy"])
@pytest.mark.parametrize("runner", ["manual", "scheduled"])
@pytest.mark.parametrize("source_type", ["dataset_row", "trace"])
@pytest.mark.parametrize("side", ["A", "B"])
def test_saved_rule_selection(world, mode, runner, source_type, side):
    rule = _saved_rule(world, source_type, side, mode)
    # This stub models CH's project-pinned ID return, NOT authorization. The
    # automation boundary must authorize the project before invoking it.
    selected = world.traces[side]
    if source_type == "trace" and mode == "legacy":
        selected.save()
    with patch(
        "model_hub.services.bulk_selection.resolve_filtered_trace_ids",
        return_value=ResolveResult(
            ids=[selected.pk], total_matching=1, truncated=False
        ),
    ):
        clear_workspace_context()
        result = (
            run_due_automation_rules()
            if runner == "scheduled"
            else evaluate_rule(rule, user=world.member)
        )
    items = QueueItem.no_workspace_objects.filter(queue=world.queue)
    if side == "B":
        assert not items.exists(), (
            "AQ-XWS-F1: saved rule must add zero cross-workspace items"
        )
        assert result["added"] == 0, (
            "AQ-XWS-F1: refused selection must report zero additions"
        )
    else:
        assert items.count() == 1, "AQ-XWS-F1: same-workspace rule must add its source"
        assert result["added"] == 1, (
            "AQ-XWS-F1: same-workspace addition must be reported"
        )


@pytest.mark.parametrize("mode", ["filter", "legacy"])
@pytest.mark.parametrize("runner", ["manual", "scheduled"])
def test_saved_rule_on_queue_without_workspace_keeps_running(world, mode, runner):
    rule = _saved_rule(world, "dataset_row", "A", mode)
    AnnotationQueue.no_workspace_objects.filter(pk=world.queue.pk).update(
        workspace=None
    )
    rule = AutomationRule.no_workspace_objects.get(pk=rule.pk)
    assert rule.queue.workspace_id is None and world.a.is_default, (
        "setup: the queue has no workspace and its organization has a default one"
    )
    assert world.datasets["A"].workspace_id == world.a.pk, (
        "setup: the selected dataset is in the default workspace"
    )
    clear_workspace_context()
    result = (
        run_due_automation_rules()
        if runner == "scheduled"
        else evaluate_rule(rule, user=world.member)
    )
    added = list(
        QueueItem.no_workspace_objects.filter(queue=world.queue).values_list(
            "dataset_row_id", flat=True
        )
    )
    assert added == [world.rows["A"].pk], (
        "a saved rule on a queue with no workspace must still add its source"
    )
    stamped = set(
        QueueItem.no_workspace_objects.filter(queue=world.queue).values_list(
            "workspace_id", flat=True
        )
    )
    assert stamped == {world.a.pk}, (
        "items a rule adds to a queue with no workspace carry the organization's "
        "default workspace"
    )
    if runner == "manual":
        assert result["added"] == 1 and not result.get("error"), (
            "a manual run on a queue with no workspace must report the addition"
        )
    else:
        assert result["evaluated"] == 1 and result["errors"] == 0, (
            "a scheduled run on a queue with no workspace must count as evaluated"
        )


@pytest.mark.parametrize("endpoint", ["annotate-detail", "export", "list", "detail"])
@pytest.mark.parametrize("side", ["A", "B"])
@pytest.mark.parametrize("captured", [False, True])
def test_preexisting_item_content_boundary(world, endpoint, side, captured):
    item = QueueItem.no_workspace_objects.create(
        queue=world.queue,
        organization=world.org,
        workspace=world.a,
        source_type="dataset_row",
        dataset_row=world.rows[side],
        order=1,
        source_preview=(
            {
                "type": "dataset_row",
                "dataset_name": f"AQ-XWS-{side}-DATASET",
                "input_preview": f"AQ-XWS-{side}-CELL",
            }
            if captured
            else None
        ),
    )
    assert item.workspace_id != world.b.pk, "SETUP_AQ-XWS item stamped A"
    base = f"{BASE_URL}{world.queue.pk}/"
    if endpoint == "export":
        response = world.client.get(base + "export/", {"export_format": "json"})
    elif endpoint == "list":
        response = world.client.get(base + "items/")
    elif endpoint == "detail":
        response = world.client.get(base + f"items/{item.pk}/")
    else:
        response = world.client.get(base + f"items/{item.pk}/annotate-detail/")
    assert response.status_code == 200, "SETUP_AQ-XWS source response must be readable"
    body = str(response.data)
    if side == "B":
        assert "AQ-XWS-B-CELL" not in body and "AQ-XWS-B-DATASET" not in body, (
            "AQ-XWS-F3: source content from another workspace must never be returned"
        )
        assert UNAVAILABLE in body, (
            "AQ-XWS-F3: inaccessible source must carry fixed unavailable reason"
        )
    else:
        expected = (
            "AQ-XWS-A-CELL"
            if endpoint in ("export", "annotate-detail")
            else "AQ-XWS-A-DATASET"
        )
        assert expected in body, "AQ-XWS-F3: same-workspace source must remain visible"


def test_scope_membership_is_rechecked_on_update(world):
    rule = _saved_rule(world, "dataset_row", "A", "filter")
    WorkspaceMembership.no_workspace_objects.filter(
        user=world.member, workspace=world.a
    ).update(is_active=False)
    # Direct serializer avoids a request middleware rejection concealing the
    # rule-validation boundary; only validation runs, no model save.
    from model_hub.serializers.annotation_queues import AutomationRuleSerializer

    serializer = AutomationRuleSerializer(
        rule,
        data={"name": "renamed"},
        partial=True,
        context={
            "request": SimpleNamespace(
                user=world.member, organization=world.org, workspace=world.a
            )
        },
    )
    assert not serializer.is_valid(), (
        "AQ-XWS-F2: stale membership must invalidate unchanged saved scope"
    )


def _extra_source(world, source_type, side):
    if source_type == "observation_span":
        from tracer.models.observation_span import ObservationSpan

        world.traces[side].save()
        return ObservationSpan.no_workspace_objects.create(
            id=uuid.uuid4().hex[:16],
            project=world.projects[side],
            trace=world.traces[side],
            name=f"AQ-XWS-{side}-SPAN",
            observation_type="llm",
            status="OK",
        ), world.projects[side]
    if source_type == "trace_session":
        from tracer.models.trace_session import TraceSession

        return TraceSession.no_workspace_objects.create(
            project=world.projects[side],
            name=f"AQ-XWS-{side}-SESSION",
        ), world.projects[side]

    from simulate.models import AgentDefinition, Scenarios
    from simulate.models.run_test import RunTest
    from simulate.models.test_execution import CallExecution, TestExecution

    ws = world.a if side == "A" else world.b
    agent = AgentDefinition.no_workspace_objects.create(
        agent_name=f"AQ-XWS-{side}-AGENT",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+12025550123",
        inbound=True,
        organization=world.org,
        workspace=ws,
        languages=["en"],
    )
    scenario = Scenarios.no_workspace_objects.create(
        name="AQ-XWS scenario",
        description="fixture",
        source="script",
        scenario_type=Scenarios.ScenarioTypes.SCRIPT,
        organization=world.org,
        workspace=ws,
        agent_definition=agent,
    )
    run = RunTest.no_workspace_objects.create(
        name="AQ-XWS run",
        agent_definition=agent,
        organization=world.org,
        workspace=ws,
    )
    execution = TestExecution.no_workspace_objects.create(
        run_test=run,
        agent_definition=agent,
        total_calls=1,
        total_scenarios=1,
    )
    call = CallExecution.no_workspace_objects.create(
        test_execution=execution,
        scenario=scenario,
        status="completed",
        call_summary=f"AQ-XWS-{side}-CALL",
    )
    return call, agent


@pytest.mark.parametrize(
    "source_type", ["observation_span", "trace_session", "call_execution"]
)
@pytest.mark.parametrize("mode", ["legacy", "filter"])
@pytest.mark.parametrize("runner", ["manual", "scheduled"])
@pytest.mark.parametrize("side", ["A", "B"])
def test_remaining_source_selection_boundaries(world, source_type, mode, runner, side):
    from contextlib import nullcontext

    source, owner = _extra_source(world, source_type, side)
    conditions = (
        {"filter": [], "scope": {"project_id": str(owner.pk)}}
        if mode == "filter"
        else {"rules": []}
    )
    rule = AutomationRule.no_workspace_objects.create(
        queue=world.queue,
        organization=world.org,
        created_by=world.member,
        name="AQ-XWS all source types",
        source_type=source_type,
        conditions=conditions,
        enabled=True,
        trigger_frequency="hourly",
    )
    selector = {
        "observation_span": "resolve_filtered_span_ids",
        "trace_session": "resolve_filtered_session_ids",
    }.get(source_type)
    stub = (
        patch(
            f"model_hub.services.bulk_selection.{selector}",
            return_value=ResolveResult(
                ids=[source.pk], total_matching=1, truncated=False
            ),
        )
        if selector and mode == "filter"
        else nullcontext()
    )
    with stub:
        clear_workspace_context()
        result = (
            run_due_automation_rules()
            if runner == "scheduled"
            else evaluate_rule(rule, user=world.member)
        )
    count = QueueItem.no_workspace_objects.filter(queue=world.queue).count()
    if side == "B":
        assert count == 0, (
            "AQ-XWS-F1: every source family must exclude another workspace"
        )
    else:
        assert count == 1 and result["added"] == 1, (
            "AQ-XWS-F1: every source family must retain same-workspace selection"
        )


@pytest.mark.parametrize("is_default", [False, True])
@pytest.mark.parametrize("side", ["A", "B"])
def test_bound_queue_and_default_exception(world, is_default, side):
    binding = Dataset.no_workspace_objects.create(
        name="AQ-XWS binding",
        organization=world.org,
        workspace=world.a,
    )
    AnnotationQueue.no_workspace_objects.filter(pk=world.queue.pk).update(
        dataset=binding,
        is_default=is_default,
    )
    rule = _saved_rule(world, "dataset_row", side, "filter")
    clear_workspace_context()
    result = evaluate_rule(rule, user=world.member)
    if is_default and side == "A":
        assert result["added"] == 1, (
            "AQ-XWS-F1: default queue may select another same-workspace source"
        )
    else:
        assert result["added"] == 0, (
            "AQ-XWS-F1: queue binding must never widen workspace scope"
        )


@pytest.mark.parametrize("side", ["A", "B"])
def test_export_to_dataset_omits_inaccessible_source(world, side):
    QueueItem.no_workspace_objects.create(
        queue=world.queue,
        organization=world.org,
        workspace=world.a,
        source_type="dataset_row",
        dataset_row=world.rows[side],
        order=1,
        status="completed",
    )
    target = Dataset.no_workspace_objects.create(
        name="AQ-XWS export target",
        organization=world.org,
        workspace=world.a,
    )
    response = world.client.post(
        f"{BASE_URL}{world.queue.pk}/export-to-dataset/",
        {
            "dataset_id": str(target.pk),
            "column_mapping": [{"field": "input", "column": "input", "enabled": True}],
        },
        format="json",
    )
    assert response.status_code == 200, "SETUP_AQ-XWS dataset export response"
    values = list(
        Cell.no_workspace_objects.filter(dataset=target).values_list("value", flat=True)
    )
    if side == "B":
        assert "AQ-XWS-B-CELL" not in str(values), (
            "AQ-XWS-F3: dataset export must not copy another workspace's cells"
        )
        metadata = list(
            Row.no_workspace_objects.filter(dataset=target).values_list(
                "metadata", flat=True
            )
        )
        assert UNAVAILABLE in str(metadata), (
            "AQ-XWS-F3: exported unavailable row must carry fixed reason"
        )
    else:
        assert "AQ-XWS-A-CELL" in str(values), (
            "AQ-XWS-F3: dataset export must retain same-workspace cells"
        )


@pytest.mark.parametrize("source_type", ["trace", "observation_span", "trace_session"])
@pytest.mark.parametrize("side", ["A", "B"])
def test_captured_tracer_preview_rechecks_project(world, source_type, side):
    from model_hub.serializers.annotation_queues import QueueItemSerializer

    locator = {
        "trace": "trace_id",
        "observation_span": "observation_span_id",
        "trace_session": "trace_session_id",
    }[source_type]
    item = QueueItem.no_workspace_objects.create(
        queue=world.queue,
        organization=world.org,
        workspace=world.a,
        source_type=source_type,
        project=world.projects[side],
        order=1,
        source_preview={"type": source_type, "input_preview": f"AQ-XWS-{side}-INPUT"},
        **{locator: str(uuid.uuid4())},
    )
    request = SimpleNamespace(
        user=world.member, organization=world.org, workspace=world.a
    )
    payload = QueueItemSerializer(item, context={"request": request}).data[
        "source_preview"
    ]
    if side == "B":
        assert "AQ-XWS-B-INPUT" not in str(payload), (
            "AQ-XWS-F3: captured tracer preview must recheck source project"
        )
        assert payload.get("error") == UNAVAILABLE, (
            "AQ-XWS-F3: captured refusal must carry fixed reason"
        )
    else:
        assert "AQ-XWS-A-INPUT" in str(payload), (
            "AQ-XWS-F3: authorized captured preview must remain visible"
        )


@pytest.mark.parametrize("source_type", ["trace", "observation_span", "trace_session"])
@pytest.mark.parametrize("side", ["A", "B"])
def test_live_tracer_cache_rechecks_actual_project(world, source_type, side):
    from model_hub.tests.test_queue_item_source_copy_scope import _copy
    from model_hub.utils.annotation_queue_helpers import (
        CollectorSourceCache,
        resolve_source_content,
        resolve_source_preview,
    )

    trace_id, span_id, session_id = (
        str(uuid.uuid4()),
        uuid.uuid4().hex[:16],
        str(uuid.uuid4()),
    )
    source = _copy(world.projects[side], trace_id=trace_id, span_id=span_id, second=0)
    locator = {
        "trace": {"trace_id": trace_id},
        "observation_span": {"observation_span_id": span_id},
        "trace_session": {"trace_session_id": session_id},
    }[source_type]
    item = QueueItem.no_workspace_objects.create(
        queue=world.queue,
        organization=world.org,
        workspace=world.a,
        source_type=source_type,
        order=1,
        **locator,
    )
    cache = CollectorSourceCache(
        spans={span_id: source},
        trace_roots={trace_id: source},
        sessions={
            session_id: {
                "project_id": str(world.projects[side].pk),
                "display_name": f"AQ-XWS-{side}-SESSION",
            }
        },
    )
    set_workspace_context(workspace=world.a, organization=world.org, user=world.member)
    for resolver in (resolve_source_content, resolve_source_preview):
        payload = resolver(item, ch_cache=cache)
        if side == "B":
            assert "AQ-XWS-B" not in str(payload), (
                "AQ-XWS-F3: live cached source must match queue workspace"
            )
            assert payload.get("error") == UNAVAILABLE, (
                "AQ-XWS-F3: live source refusal must carry fixed reason"
            )
        else:
            assert "AQ-XWS-A" in str(payload), (
                "AQ-XWS-F3: authorized live cached source must remain visible"
            )


@pytest.mark.parametrize("side", ["A", "B"])
def test_call_content_rechecks_source_workspace(world, side):
    from model_hub.utils.annotation_queue_helpers import resolve_source_content

    source, _ = _extra_source(world, "call_execution", side)
    item = QueueItem.no_workspace_objects.create(
        queue=world.queue,
        organization=world.org,
        workspace=world.a,
        source_type="call_execution",
        call_execution=source,
        order=1,
    )
    set_workspace_context(workspace=world.a, organization=world.org, user=world.member)
    payload = resolve_source_content(item)
    if side == "B":
        assert "AQ-XWS-B-CALL" not in str(payload), (
            "AQ-XWS-F3: call content must match queue workspace"
        )
    else:
        assert "AQ-XWS-A-CALL" in str(payload), (
            "AQ-XWS-F3: authorized call content must remain visible"
        )


@pytest.mark.parametrize("side", ["A", "B"])
@pytest.mark.parametrize("method", ["create", "update"])
def test_call_scope_write_checks_agent_definition(world, side, method):
    _, agent = _extra_source(world, "call_execution", side)
    payload = {
        "name": "AQ-XWS call scope",
        "source_type": "call_execution",
        "conditions": {"filter": [], "scope": {"project_id": str(agent.pk)}},
    }
    url = f"{BASE_URL}{world.queue.pk}/automation-rules/"
    if method == "update":
        rule = AutomationRule.no_workspace_objects.create(
            queue=world.queue,
            organization=world.org,
            created_by=world.member,
            name="AQ-XWS existing call rule",
            source_type="call_execution",
            conditions={},
        )
        response = world.client.patch(url + f"{rule.pk}/", payload, format="json")
    else:
        response = world.client.post(url, payload, format="json")
    if side == "B":
        assert response.status_code == 400, (
            "AQ-XWS-F2: call agent scope must be in queue workspace"
        )
        assert SCOPE_ERROR in str(response.data), (
            "AQ-XWS-F2: call agent refusal must use fixed message"
        )
    else:
        assert response.status_code in (200, 201), (
            "AQ-XWS-F2: same-workspace agent scope must succeed"
        )


def test_filter_dataset_match_limit_still_refuses_all(world):
    rule = _saved_rule(world, "dataset_row", "A", "filter")
    Row.no_workspace_objects.create(dataset=world.datasets["A"], order=2)
    clear_workspace_context()
    result = evaluate_rule(rule, user=world.member, cap=1)
    assert result.get("truncated") and result["added"] == 0, (
        "AQ-XWS-F1: scope hardening must retain all-or-nothing match limit"
    )
    assert not QueueItem.no_workspace_objects.filter(queue=world.queue).exists(), (
        "AQ-XWS-F1: match limit must not partially enqueue"
    )


def test_repeated_allowed_run_retains_duplicate_handling(world):
    rule = _saved_rule(world, "dataset_row", "A", "filter")
    clear_workspace_context()
    first = evaluate_rule(rule, user=world.member)
    second = evaluate_rule(rule, user=world.member)
    assert first["added"] == 1, "AQ-XWS-F1: duplicate control first run must add"
    assert second["added"] == 0 and second["duplicates"] == 1, (
        "AQ-XWS-F1: duplicate control second run must not add"
    )


def test_selected_source_without_workspace_runs_in_default_workspace(world):
    rule = _saved_rule(world, "dataset_row", "A", "filter")
    Dataset.no_workspace_objects.filter(pk=world.datasets["A"].pk).update(
        workspace=None
    )
    assert world.queue.workspace_id == world.a.pk and world.a.is_default, (
        "setup: the queue is in its organization's default workspace"
    )
    clear_workspace_context()
    result = evaluate_rule(rule, user=world.member)
    assert result["added"] == 1 and not result.get("error"), (
        "a default-workspace rule must still select a dataset with no workspace"
    )
    added = list(
        QueueItem.no_workspace_objects.filter(queue=world.queue).values_list(
            "dataset_row_id", flat=True
        )
    )
    assert added == [world.rows["A"].pk], (
        "the row of the dataset with no workspace is the one added"
    )


def test_source_render_rechecks_live_caller_membership(world):
    from model_hub.serializers.annotation_queues import QueueItemSerializer

    item = QueueItem.no_workspace_objects.create(
        queue=world.queue,
        organization=world.org,
        workspace=world.a,
        source_type="dataset_row",
        dataset_row=world.rows["A"],
        order=1,
    )
    WorkspaceMembership.no_workspace_objects.filter(
        user=world.member,
        workspace=world.a,
    ).update(is_active=False)
    request = SimpleNamespace(
        user=world.member, organization=world.org, workspace=world.a
    )
    preview = QueueItemSerializer(item, context={"request": request}).data[
        "source_preview"
    ]
    assert "AQ-XWS-A-DATASET" not in str(preview), (
        "AQ-XWS-F3: revoked caller must not receive source preview"
    )
    assert preview.get("error") == UNAVAILABLE, (
        "AQ-XWS-F3: revoked caller must receive fixed unavailable reason"
    )


def _scoped_source(organization, workspace, label):
    """A dataset row with one cell, and a project, in ``workspace`` or in none."""
    dataset = Dataset.no_workspace_objects.create(
        name=f"{label} dataset", organization=organization, workspace=workspace
    )
    row = Row.no_workspace_objects.create(dataset=dataset, order=1)
    column = Column.no_workspace_objects.create(
        dataset=dataset, name="input", data_type="text", source="OTHERS"
    )
    Cell.no_workspace_objects.create(
        dataset=dataset, row=row, column=column, value=f"{label} cell"
    )
    project = Project.no_workspace_objects.create(
        name=f"{label} project",
        organization=organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    if workspace is None:
        # Saving may stamp the ambient workspace; rows made before workspaces
        # existed have none.
        Dataset.no_workspace_objects.filter(pk=dataset.pk).update(workspace=None)
        Project.no_workspace_objects.filter(pk=project.pk).update(workspace=None)
        dataset.refresh_from_db()
        project.refresh_from_db()
    expected = workspace.pk if workspace is not None else None
    assert dataset.workspace_id == project.workspace_id == expected, (
        "setup: the dataset and the project are in the intended workspace"
    )
    return SimpleNamespace(dataset=dataset, row=row, project=project)


def _item_body(world, endpoint, item):
    base = f"{BASE_URL}{world.queue.pk}/"
    if endpoint == "export":
        response = world.client.get(base + "export/", {"export_format": "json"})
    elif endpoint == "list":
        response = world.client.get(base + "items/")
    elif endpoint == "detail":
        response = world.client.get(base + f"items/{item.pk}/")
    else:
        response = world.client.get(base + f"items/{item.pk}/annotate-detail/")
    assert response.status_code == 200, "setup: the item response must be readable"
    return str(response.data)


@pytest.mark.parametrize("endpoint", ["annotate-detail", "export", "list", "detail"])
@pytest.mark.parametrize("captured", [False, True])
def test_default_workspace_queue_shows_dataset_without_workspace(
    world, endpoint, captured
):
    legacy = _scoped_source(world.org, None, "No-workspace source")
    item = QueueItem.no_workspace_objects.create(
        queue=world.queue,
        organization=world.org,
        workspace=world.a,
        source_type="dataset_row",
        dataset_row=legacy.row,
        order=1,
        source_preview=(
            {
                "type": "dataset_row",
                "dataset_name": legacy.dataset.name,
                "input_preview": "No-workspace stored input",
            }
            if captured
            else None
        ),
    )
    assert world.queue.workspace_id == world.a.pk and world.a.is_default, (
        "setup: the queue is in its organization's default workspace"
    )
    body = _item_body(world, endpoint, item)
    expected = (
        "No-workspace source cell"
        if endpoint in ("export", "annotate-detail")
        else "No-workspace source dataset"
    )
    assert expected in body and UNAVAILABLE not in body, (
        "a default-workspace queue must show a dataset row with no workspace"
    )


_TRACER_LOCATORS = {
    "trace": "trace_id",
    "observation_span": "observation_span_id",
    "trace_session": "trace_session_id",
}


@pytest.mark.parametrize("source_type", ["trace", "observation_span", "trace_session"])
def test_default_workspace_queue_shows_captured_tracer_source_without_workspace(
    world, source_type
):
    from model_hub.serializers.annotation_queues import QueueItemSerializer

    legacy = _scoped_source(world.org, None, "No-workspace tracer")
    item = QueueItem.no_workspace_objects.create(
        queue=world.queue,
        organization=world.org,
        workspace=world.a,
        source_type=source_type,
        project=legacy.project,
        order=1,
        source_preview={"type": source_type, "input_preview": "No-workspace capture"},
        **{_TRACER_LOCATORS[source_type]: str(uuid.uuid4())},
    )
    request = SimpleNamespace(
        user=world.member, organization=world.org, workspace=world.a
    )
    payload = QueueItemSerializer(item, context={"request": request}).data[
        "source_preview"
    ]
    assert payload == item.source_preview, (
        "the stored preview of a project with no workspace must be returned"
    )


@pytest.mark.parametrize("source_type", ["trace", "observation_span", "trace_session"])
def test_default_workspace_queue_shows_live_tracer_source_without_workspace(
    world, source_type
):
    from model_hub.tests.test_queue_item_source_copy_scope import _copy
    from model_hub.utils.annotation_queue_helpers import (
        CollectorSourceCache,
        resolve_source_content,
        resolve_source_preview,
    )

    legacy = _scoped_source(world.org, None, "No-workspace live")
    trace_id, span_id, session_id = (
        str(uuid.uuid4()),
        uuid.uuid4().hex[:16],
        str(uuid.uuid4()),
    )
    source = _copy(legacy.project, trace_id=trace_id, span_id=span_id, second=0)
    locator = {
        "trace": {"trace_id": trace_id},
        "observation_span": {"observation_span_id": span_id},
        "trace_session": {"trace_session_id": session_id},
    }[source_type]
    item = QueueItem.no_workspace_objects.create(
        queue=world.queue,
        organization=world.org,
        workspace=world.a,
        source_type=source_type,
        order=1,
        **locator,
    )
    cache = CollectorSourceCache(
        spans={span_id: source},
        trace_roots={trace_id: source},
        sessions={
            session_id: {
                "project_id": str(legacy.project.pk),
                "display_name": "No-workspace live session",
            }
        },
    )
    set_workspace_context(workspace=world.a, organization=world.org, user=world.member)
    for resolver in (resolve_source_content, resolve_source_preview):
        payload = resolver(item, ch_cache=cache)
        assert "No-workspace live" in str(payload), (
            "a live source in a project with no workspace must be shown"
        )
        assert payload.get("error") != UNAVAILABLE, (
            "a live source in a project with no workspace is not withheld"
        )


@pytest.fixture
def custom_world(organization, workspace, user):
    own = Workspace.no_workspace_objects.create(
        name="Queue custom workspace",
        organization=organization,
        created_by=user,
        is_default=False,
        is_active=True,
    )
    other = Workspace.no_workspace_objects.create(
        name="Other custom workspace",
        organization=organization,
        created_by=user,
        is_default=False,
        is_active=True,
    )
    member = create_workspace_member_user(organization, own)
    queue = AnnotationQueue.no_workspace_objects.create(
        name="Custom workspace queue",
        organization=organization,
        workspace=own,
        created_by=member,
        auto_assign=False,
    )
    AnnotationQueueAnnotator.no_workspace_objects.update_or_create(
        queue=queue,
        user=member,
        defaults={"role": "manager", "roles": ["manager", "reviewer", "annotator"]},
    )
    sources = {
        name: _scoped_source(organization, ws, f"Custom {name}")
        for name, ws in (
            ("own", own),
            ("other", other),
            ("default", workspace),
            ("none", None),
        )
    }
    assert queue.workspace_id == own.pk and not own.is_default, (
        "setup: the queue is in a non-default workspace"
    )
    assert member.can_access_workspace(own), "setup: the member can use the queue"
    assert not member.can_access_workspace(other), (
        "setup: the member cannot use the other workspace"
    )
    with _allow_automation_rule_entitlements_if_available():
        yield SimpleNamespace(
            org=organization, own=own, member=member, queue=queue, sources=sources
        )


@pytest.mark.parametrize("placement", ["own", "other", "default", "none"])
def test_custom_workspace_rule_selects_only_its_own_workspace(custom_world, placement):
    world = custom_world
    source = world.sources[placement]
    rule = AutomationRule.no_workspace_objects.create(
        queue=world.queue,
        organization=world.org,
        created_by=world.member,
        name="Custom workspace rule",
        source_type="dataset_row",
        conditions={"filter": [], "scope": {"dataset_id": str(source.dataset.pk)}},
        enabled=True,
        trigger_frequency="hourly",
    )
    clear_workspace_context()
    result = evaluate_rule(rule, user=world.member)
    added = list(
        QueueItem.no_workspace_objects.filter(queue=world.queue).values_list(
            "dataset_row_id", flat=True
        )
    )
    if placement == "own":
        assert added == [source.row.pk] and result["added"] == 1, (
            "a non-default workspace rule must still select its own dataset"
        )
    else:
        assert added == [] and result.get("error") == SCOPE_ERROR, (
            "a non-default workspace rule must never select another scope's dataset"
        )


@pytest.mark.parametrize("placement", ["own", "other", "default", "none"])
@pytest.mark.parametrize("source_type", ["dataset_row", "trace"])
def test_custom_workspace_queue_shows_only_its_own_sources(
    custom_world, placement, source_type
):
    from model_hub.serializers.annotation_queues import QueueItemSerializer

    world = custom_world
    source = world.sources[placement]
    label = f"Custom {placement}"
    if source_type == "dataset_row":
        locator = {"dataset_row": source.row}
    else:
        locator = {
            "trace_id": str(uuid.uuid4()),
            "project": source.project,
            "source_preview": {"type": "trace", "input_preview": f"{label} capture"},
        }
    item = QueueItem.no_workspace_objects.create(
        queue=world.queue,
        organization=world.org,
        workspace=world.own,
        source_type=source_type,
        order=1,
        **locator,
    )
    request = SimpleNamespace(
        user=world.member, organization=world.org, workspace=world.own
    )
    payload = QueueItemSerializer(item, context={"request": request}).data[
        "source_preview"
    ]
    if placement == "own":
        assert label in str(payload) and payload.get("error") != UNAVAILABLE, (
            "a non-default workspace queue must show its own source"
        )
    else:
        assert label not in str(payload) and payload.get("error") == UNAVAILABLE, (
            "a non-default workspace queue must withhold every other scope's source"
        )


@pytest.fixture
def foreign_sources(world):
    other_org = Organization.objects.create(name="Another organization")
    other_default = Workspace.no_workspace_objects.create(
        name="Another organization default",
        organization=other_org,
        created_by=world.member,
        is_default=True,
        is_active=True,
    )
    return {
        "none": _scoped_source(other_org, None, "Foreign none"),
        "default": _scoped_source(other_org, other_default, "Foreign default"),
    }


@pytest.mark.parametrize("queue_workspace", ["default", "none"])
@pytest.mark.parametrize("foreign", ["none", "default"])
@pytest.mark.parametrize("source_type", ["dataset_row", "trace"])
def test_source_in_another_organization_is_never_shown(
    world, foreign_sources, queue_workspace, foreign, source_type
):
    from model_hub.serializers.annotation_queues import QueueItemSerializer

    if queue_workspace == "none":
        AnnotationQueue.no_workspace_objects.filter(pk=world.queue.pk).update(
            workspace=None
        )
        world.queue.refresh_from_db()
    source = foreign_sources[foreign]
    label = f"Foreign {foreign}"
    if source_type == "dataset_row":
        locator = {"dataset_row": source.row}
    else:
        locator = {
            "trace_id": str(uuid.uuid4()),
            "project": source.project,
            "source_preview": {"type": "trace", "input_preview": f"{label} capture"},
        }
    item = QueueItem.no_workspace_objects.create(
        queue=world.queue,
        organization=world.org,
        workspace=world.a,
        source_type=source_type,
        order=1,
        **locator,
    )
    assert source.project.organization_id != world.org.pk, (
        "setup: the source belongs to another organization"
    )
    request = SimpleNamespace(
        user=world.member, organization=world.org, workspace=world.a
    )
    payload = QueueItemSerializer(item, context={"request": request}).data[
        "source_preview"
    ]
    assert label not in str(payload) and payload.get("error") == UNAVAILABLE, (
        "a source in another organization must never be shown"
    )


@pytest.mark.parametrize("queue_workspace", ["default", "none"])
@pytest.mark.parametrize("foreign", ["none", "default"])
def test_rule_never_selects_a_source_in_another_organization(
    world, foreign_sources, queue_workspace, foreign
):
    source = foreign_sources[foreign]
    rule = AutomationRule.no_workspace_objects.create(
        queue=world.queue,
        organization=world.org,
        created_by=world.member,
        name="Another organization rule",
        source_type="dataset_row",
        conditions={"filter": [], "scope": {"dataset_id": str(source.dataset.pk)}},
        enabled=True,
        trigger_frequency="hourly",
    )
    if queue_workspace == "none":
        AnnotationQueue.no_workspace_objects.filter(pk=world.queue.pk).update(
            workspace=None
        )
    rule = AutomationRule.no_workspace_objects.get(pk=rule.pk)
    assert source.dataset.organization_id != world.org.pk, (
        "setup: the dataset belongs to another organization"
    )
    clear_workspace_context()
    result = evaluate_rule(rule, user=world.member)
    assert not QueueItem.no_workspace_objects.filter(queue=world.queue).exists(), (
        "a rule must never add a source from another organization"
    )
    assert result["added"] == 0 and result.get("error") == SCOPE_ERROR, (
        "a rule must refuse another organization's source with the fixed reason"
    )


def test_default_queue_for_project_without_workspace_gets_default_workspace(world):
    legacy = _scoped_source(world.org, None, "No-workspace default queue")
    trace = Trace.no_workspace_objects.create(
        name="No-workspace default queue trace", project=legacy.project
    )
    existing = AnnotationQueue.no_workspace_objects.filter(project=legacy.project)
    assert not existing.exists(), "setup: the project has no queue yet"
    # Operator commands resolve default queues with no request workspace.
    clear_workspace_context()
    item = resolve_default_queue_item_for_source(
        "trace", trace, world.org, world.member
    )
    assert item is not None, "setup: the project resolves a default queue item"
    queue = AnnotationQueue.no_workspace_objects.get(pk=item.queue_id)
    assert queue.is_default and queue.project_id == legacy.project.pk, (
        "setup: the item is on the project's new default queue"
    )
    item.refresh_from_db()
    assert queue.workspace_id == world.a.pk, (
        "a default queue made for a project with no workspace carries the "
        "organization's default workspace"
    )
    assert item.workspace_id == world.a.pk, (
        "an item on that default queue carries the organization's default workspace"
    )
