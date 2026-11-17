"""Live run authority is separate from trusted preview-cache ownership checks.

Only entitlement/email/ClickHouse I/O is replaced. Queue ownership, membership,
manager roles, rule evaluation, and backfill writes use the real Django models.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.management.commands.backfill_queue_item_source_preview import (
    backfill_queue_item_source_previews,
)
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AutomationRule,
    QueueItem,
)
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.services.bulk_selection import ResolveResult
from model_hub.tasks import annotation_automation as tasks
from model_hub.tests.test_annotation_queues_api import create_workspace_member_user
from model_hub.tests.test_automation_rules_e2e import (
    _allow_automation_rule_entitlements_if_available,
)
from model_hub.tests.test_queue_item_source_copy_scope import _Copies, _copy
from model_hub.utils import annotation_queue_helpers as helpers
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    get_current_organization,
    get_current_user,
    get_current_workspace,
)
from tracer.models.project import Project

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]
ACTOR_REASON = "automation_rule_actor_unavailable"
ACTOR_ERROR = "Automation rule actor is unavailable or no longer authorized."
ACTOR_REFUSAL = {
    "matched": 0,
    "added": 0,
    "duplicates": 0,
    "error": ACTOR_ERROR,
}


@pytest.fixture
def world(organization, workspace, user):
    member = create_workspace_member_user(organization, workspace)
    queue = AnnotationQueue.no_workspace_objects.create(
        name="AQ-XWS-W1C queue",
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
    dataset = Dataset.no_workspace_objects.create(
        name="AQ-XWS-W1C dataset", organization=organization, workspace=workspace
    )
    row = Row.no_workspace_objects.create(dataset=dataset, order=1)
    assert member.is_active and member.can_access_workspace(workspace), (
        "SETUP_AQ-XWS-W1C live workspace member"
    )
    assert not member.has_global_workspace_access(organization), (
        "SETUP_AQ-XWS-W1C no global-access bypass"
    )
    assert queue.workspace_id == dataset.workspace_id, (
        "SETUP_AQ-XWS-W1C same-workspace dataset"
    )
    with _allow_automation_rule_entitlements_if_available():
        yield SimpleNamespace(
            org=organization,
            workspace=workspace,
            member=member,
            owner=user,
            queue=queue,
            dataset=dataset,
            row=row,
        )
    clear_workspace_context()


def _clear_context():
    clear_workspace_context()
    assert (
        get_current_user(),
        get_current_workspace(),
        get_current_organization(),
    ) == (None, None, None), "SETUP_AQ-XWS-W1C no ambient request authority"


def _rule(world, frequency="hourly"):
    rule = AutomationRule.no_workspace_objects.create(
        queue=world.queue,
        organization=world.org,
        created_by=world.member,
        name="AQ-XWS-W1C saved rule",
        source_type="dataset_row",
        conditions={"filter": [], "scope": {"dataset_id": str(world.dataset.pk)}},
        enabled=True,
        trigger_frequency=frequency,
    )
    assert rule.created_by_id == world.member.pk, (
        "SETUP_AQ-XWS-W1C persisted creator binding"
    )
    return rule


def _revoke(world, rule, state):
    if state == "null_creator":
        AutomationRule.no_workspace_objects.filter(pk=rule.pk).update(created_by=None)
    elif state == "inactive":
        type(world.member).objects.filter(pk=world.member.pk).update(is_active=False)
    elif state == "workspace_removed":
        WorkspaceMembership.no_workspace_objects.filter(
            user=world.member, workspace=world.workspace
        ).update(is_active=False)
    elif state == "organization_removed":
        OrganizationMembership.no_workspace_objects.filter(
            user=world.member, organization=world.org
        ).update(is_active=False)
    elif state == "manager_removed":
        AnnotationQueueAnnotator.no_workspace_objects.filter(
            user=world.member, queue=world.queue
        ).update(role="annotator", roles=["annotator"])
    else:
        raise AssertionError("SETUP_AQ-XWS-W1C unknown revocation state")


def _logged_refusal(warning, rule):
    return any(
        call.kwargs.get("reason") == ACTOR_REASON
        and call.kwargs.get("rule_id") == str(rule.pk)
        for call in warning.call_args_list
    )


@pytest.mark.parametrize(
    "state",
    [
        "null_creator",
        "inactive",
        "workspace_removed",
        "organization_removed",
        "manager_removed",
    ],
)
def test_scheduled_run_requires_live_authorized_creator(world, state):
    rule = _rule(world)
    _revoke(world, rule, state)
    _clear_context()
    with patch.object(helpers.logger, "warning") as warning:
        summary = tasks.run_due_automation_rules()
    rule.refresh_from_db()
    assert (
        summary,
        QueueItem.no_workspace_objects.filter(queue=world.queue).count(),
        rule.trigger_count,
        rule.last_triggered_at,
        _logged_refusal(warning, rule),
    ) == (
        {"checked": 1, "evaluated": 0, "errors": 1, "added": 0, "duplicates": 0},
        0,
        0,
        None,
        True,
    ), "AQ-XWS-W1C: scheduled actor refusal must add nothing and log fixed reason"


@pytest.mark.parametrize("actor_id", [None, "missing", "malformed"])
def test_async_manual_unloadable_actor_refuses(world, actor_id):
    rule = _rule(world, frequency="manual")
    if actor_id == "missing":
        actor_id = str(uuid.uuid4())
    elif actor_id == "malformed":
        actor_id = "not-a-user-uuid"
    activity = getattr(
        tasks.evaluate_rule_manual_async,
        "_original_func",
        tasks.evaluate_rule_manual_async,
    )
    _clear_context()
    with (
        patch.object(helpers.logger, "warning") as warning,
        patch.object(helpers, "send_rule_completion_email") as notify,
    ):
        result = activity(str(rule.pk), actor_id)
    rule.refresh_from_db()
    assert (
        result,
        QueueItem.no_workspace_objects.filter(queue=world.queue).count(),
        rule.trigger_count,
        _logged_refusal(warning, rule),
        notify.call_count,
    ) == (ACTOR_REFUSAL, 0, 0, True, 0), (
        "AQ-XWS-W1C: unloadable manual actor must refuse without creator fallback"
    )


@pytest.mark.parametrize("runner", ["helper", "async"])
@pytest.mark.parametrize("state", ["inactive", "workspace_removed", "manager_removed"])
def test_manual_run_rechecks_actor_after_admission(world, runner, state):
    rule = _rule(world, frequency="manual")
    _revoke(world, rule, state)
    # Deliberately retain the previously loaded, active User instance. The worker
    # must reload it, not trust the request's or scheduler's stale object.
    assert world.member.is_active, "SETUP_AQ-XWS-W1C actor object predates revocation"
    _clear_context()
    with (
        patch.object(helpers.logger, "warning") as warning,
        patch.object(helpers, "send_rule_completion_email") as notify,
    ):
        if runner == "helper":
            result = helpers.evaluate_rule(rule, user=world.member)
        else:
            activity = getattr(
                tasks.evaluate_rule_manual_async,
                "_original_func",
                tasks.evaluate_rule_manual_async,
            )
            result = activity(str(rule.pk), str(world.member.pk))
    assert (
        result,
        QueueItem.no_workspace_objects.filter(queue=world.queue).count(),
        _logged_refusal(warning, rule),
        notify.call_count,
    ) == (ACTOR_REFUSAL, 0, True, 0), (
        "AQ-XWS-W1C: manual execution must recheck live actor membership and manager role"
    )


def test_foreign_and_missing_manual_actor_have_identical_refusal(world):
    rule = _rule(world, frequency="manual")
    other_workspace = Workspace.no_workspace_objects.create(
        name="AQ-XWS-W1C foreign actor workspace",
        organization=world.org,
        created_by=world.owner,
        is_active=True,
        is_default=False,
    )
    foreign = create_workspace_member_user(world.org, other_workspace)
    assert not foreign.can_access_workspace(world.workspace), (
        "SETUP_AQ-XWS-W1C foreign actor cannot enter queue workspace"
    )
    activity = getattr(
        tasks.evaluate_rule_manual_async,
        "_original_func",
        tasks.evaluate_rule_manual_async,
    )
    _clear_context()
    with patch.object(helpers, "send_rule_completion_email") as notify:
        foreign_result = activity(str(rule.pk), str(foreign.pk))
        missing_result = activity(str(rule.pk), str(uuid.uuid4()))
    assert (
        foreign_result,
        missing_result,
        QueueItem.no_workspace_objects.filter(queue=world.queue).count(),
        notify.call_count,
    ) == (ACTOR_REFUSAL, ACTOR_REFUSAL, 0, 0), (
        "AQ-XWS-W1C: foreign and nonexistent actors must have identical refusal shape"
    )


@pytest.mark.parametrize("runner", ["scheduled", "async", "helper", "dry_run"])
def test_active_manager_positive_control(world, runner):
    rule = _rule(world, frequency="hourly" if runner == "scheduled" else "manual")
    _clear_context()
    with patch.object(helpers, "send_rule_completion_email"):
        if runner == "scheduled":
            result = tasks.run_due_automation_rules()
        elif runner == "async":
            activity = getattr(
                tasks.evaluate_rule_manual_async,
                "_original_func",
                tasks.evaluate_rule_manual_async,
            )
            result = activity(str(rule.pk), str(world.member.pk))
        else:
            result = helpers.evaluate_rule(
                rule, user=world.member, dry_run=runner == "dry_run"
            )
    expected = 0 if runner == "dry_run" else 1
    assert result["added"] == expected and not result.get("error"), (
        "AQ-XWS-W1C: active manager positive control must still evaluate"
    )
    assert list(
        QueueItem.no_workspace_objects.filter(queue=world.queue).values_list(
            "dataset_row_id", flat=True
        )
    ) == ([] if runner == "dry_run" else [world.row.pk]), (
        "AQ-XWS-W1C: active manager positive control must persist authorized source"
    )


def _project(organization, workspace, name):
    return Project.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )


def _scheduled_annotator_rule(world, frequency="hourly", filter_key="filter"):
    project = _project(world.org, world.workspace, "Scheduled annotator project")
    rule = _rule(world, frequency=frequency)
    rule.source_type = "trace"
    rule.conditions = {
        filter_key: [
            {
                "column_id": "annotator",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "contains",
                    "filter_value": str(world.member.pk),
                },
            }
        ],
        "scope": {"project_id": str(project.pk)},
    }
    rule.save(update_fields=["source_type", "conditions"])
    return rule


@pytest.mark.parametrize("frequency", ["hourly", "daily"])
@pytest.mark.parametrize("filter_key", ["filter", "filters"])
def test_scheduled_annotator_filter_evaluates_as_live_creator(
    world, frequency, filter_key
):
    """W10-ANNOTATIONS-L01: scoped filters must reach the shared evaluator."""
    rule = _scheduled_annotator_rule(world, frequency, filter_key)
    trace_id = uuid.uuid4()
    _clear_context()
    # Only external trace selection is replaced. Actor/scope checks, queue
    # additions, cadence and run-stat writes use the real application code.
    with patch(
        "model_hub.services.bulk_selection.resolve_filtered_trace_ids",
        return_value=ResolveResult(
            ids=[trace_id], total_matching=1, truncated=False
        ),
    ) as resolve:
        summary = tasks.run_due_automation_rules()
        assert summary == {
            "checked": 1, "evaluated": 1, "errors": 0, "added": 1, "duplicates": 0
        }
        resolve.assert_called_once()
        assert resolve.call_args.kwargs["user"] == world.member
        assert resolve.call_args.kwargs["filters"] == rule.conditions[filter_key]
        assert resolve.call_args.kwargs["workspace"] == world.workspace
        assert resolve.call_args.kwargs["organization"] == world.org
        assert str(resolve.call_args.kwargs["project_id"]) == (
            rule.conditions["scope"]["project_id"]
        )
        rule.refresh_from_db()
        assert rule.trigger_count == 1
        assert rule.last_triggered_at is not None
        assert list(
            QueueItem.no_workspace_objects.filter(queue=world.queue).values_list(
                "trace_id", flat=True
            )
        ) == [trace_id]
        assert tasks.run_due_automation_rules() == {
            "checked": 1, "evaluated": 0, "errors": 0, "added": 0, "duplicates": 0
        }
        resolve.assert_called_once()


@pytest.mark.parametrize(
    "state",
    [
        "null_creator",
        "inactive",
        "workspace_removed",
        "organization_removed",
        "manager_removed",
    ],
)
def test_scheduled_annotator_filter_still_refuses_unavailable_creator(world, state):
    rule = _scheduled_annotator_rule(world)
    _revoke(world, rule, state)
    _clear_context()
    with (
        patch("model_hub.services.bulk_selection.resolve_filtered_trace_ids") as resolve,
        patch.object(helpers.logger, "warning") as warning,
    ):
        summary = tasks.run_due_automation_rules()
    resolve.assert_not_called()
    assert summary == {
        "checked": 1, "evaluated": 0, "errors": 1, "added": 0, "duplicates": 0
    }
    assert _logged_refusal(warning, rule)
    rule.refresh_from_db()
    assert rule.trigger_count == 0
    assert rule.last_triggered_at is None
    assert not QueueItem.no_workspace_objects.filter(queue=world.queue).exists()


def _item(world, source_type, project_id, source_id):
    return QueueItem.no_workspace_objects.create(
        queue=world.queue,
        organization=world.org,
        workspace=world.workspace,
        source_type=source_type,
        project_id=project_id,
        **{f"{helpers.get_fk_field_name(source_type)}_id": source_id},
    )


@pytest.mark.parametrize("source_type", ["trace", "observation_span", "trace_session"])
@pytest.mark.parametrize("using", [None, "default"])
@pytest.mark.parametrize("binding", ["bound", "unbound", "default"])
def test_backfill_uses_queue_ownership_without_request_actor(
    world, source_type, using, binding
):
    own = _project(world.org, world.workspace, "AQ-XWS-W1C own")
    other_workspace = Workspace.no_workspace_objects.create(
        name="AQ-XWS-W1C other workspace",
        organization=world.org,
        created_by=world.owner,
        is_default=False,
        is_active=True,
    )
    foreign = _project(world.org, other_workspace, "AQ-XWS-W1C foreign")
    other_org = Organization.objects.create(name="AQ-XWS-W1C other organization")
    other_org_workspace = Workspace.no_workspace_objects.create(
        name="AQ-XWS-W1C other organization workspace",
        organization=other_org,
        created_by=world.owner,
        is_default=True,
        is_active=True,
    )
    foreign_org = _project(other_org, other_org_workspace, "AQ-XWS-W1C foreign org")
    unbound = _project(world.org, world.workspace, "AQ-XWS-W1C other binding")
    inactive_workspace = Workspace.no_workspace_objects.create(
        name="AQ-XWS-W1C inactive workspace",
        organization=world.org,
        created_by=world.owner,
        is_default=False,
        is_active=False,
    )
    inactive = _project(world.org, inactive_workspace, "AQ-XWS-W1C inactive")
    null_workspace = _project(world.org, None, "AQ-XWS-W1C null workspace")
    Project.no_workspace_objects.filter(pk=null_workspace.pk).update(workspace=None)
    null_workspace.refresh_from_db()
    assert null_workspace.workspace_id is None and world.workspace.is_default, (
        "setup: a same-organization project with no workspace under a default queue"
    )
    world.queue.project = None if binding == "unbound" else own
    world.queue.is_default = binding == "default"
    world.queue.save(update_fields=["project", "is_default"])
    projects = [own, foreign, foreign_org, unbound, inactive, null_workspace]
    items, spans, sessions = [], [], {}
    for index, project in enumerate(projects):
        source_id = str(uuid.uuid4())
        items.append(_item(world, source_type, project.pk, source_id))
        spans.append(
            (
                index,
                _copy(project, trace_id=source_id, span_id=source_id, second=index),
            )
        )
        sessions[source_id] = {
            "project_id": str(project.pk),
            "display_name": project.name,
        }
    # Both a nonexistent project and a nonexistent source in a valid project
    # must produce the same skipped/NULL cache outcome as a foreign source.
    items.append(_item(world, source_type, uuid.uuid4(), str(uuid.uuid4())))
    items.append(_item(world, source_type, own.pk, str(uuid.uuid4())))
    items.append(_item(world, source_type, None, str(uuid.uuid4())))
    assert all(item.source_preview is None for item in items), (
        "SETUP_AQ-XWS-W1C uncached historical items"
    )

    def session_fields(ids, *, project_id=None, **_):
        return {
            sid: sessions[sid]
            for sid in ids
            if sid in sessions
            and (project_id is None or sessions[sid]["project_id"] == str(project_id))
        }

    _clear_context()
    with (
        patch(
            "tracer.services.clickhouse.v2.get_reader",
            side_effect=lambda: _Copies(spans),
        ),
        patch(
            "tracer.services.clickhouse.v2.trace_session_dict_reader.resolve_session_fields",
            side_effect=session_fields,
        ),
    ):
        summary = backfill_queue_item_source_previews(
            queue_id=world.queue.pk, chunk_size=2, using=using
        )
        for item in items:
            item.refresh_from_db()
        # A default workspace also holds its organization's projects with no
        # workspace, so an unbound or default queue stamps that project's item.
        allowed = {0} if binding == "bound" else {0, 3, 5}
        assert summary == (len(allowed), len(items) - len(allowed), 0) and all(
            items[index].source_preview for index in allowed
        ), "AQ-XWS-W1C: backfill must stamp authorized source without request actor"
        assert all(
            item.source_preview is None
            for index, item in enumerate(items)
            if index not in allowed
        ), (
            "AQ-XWS-W1C: foreign nonexistent unbound and legacy sources must remain unstamped"
        )
        assert (
            items[1].source_preview
            == items[-3].source_preview
            == items[-2].source_preview
            is None
        ), (
            "AQ-XWS-W1C: foreign and nonexistent sources must have identical skipped shape"
        )
        assert backfill_queue_item_source_previews(
            queue_id=world.queue.pk, using=using
        ) == (0, len(items) - len(allowed), 0), (
            "AQ-XWS-W1C: backfill must remain idempotent"
        )
        denied = helpers.resolve_source_preview(items[0])
        assert denied == {
            "type": source_type,
            "deleted": True,
            "error": "queue_source_unavailable",
        }, "AQ-XWS-W1C: trusted backfill must not enable actor-free API rendering"
        request = SimpleNamespace(
            user=world.member, organization=world.org, workspace=world.workspace
        )
        visible = helpers.resolve_source_preview(items[0], request=request)
        assert visible.get("name") == items[0].source_preview["name"], (
            "AQ-XWS-W1C: authorized API reader must retain the same preview payload"
        )
