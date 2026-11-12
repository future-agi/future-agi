"""Entitlements must survive updates and be rechecked at execution time.

Uses the existing Django fixture database; entitlement decisions are replaced at
their optional import site, so these tests never consult billing/Redis services.
RED expectations are recorded separately; this writer only parses the module.
"""

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AutomationRule,
    QueueItem,
)
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.tasks import annotation_automation as tasks
from model_hub.tests.test_annotation_queues_api import create_workspace_member_user
from model_hub.utils import annotation_queue_helpers as helpers
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]
ENTITLEMENTS = "ee.usage.services.entitlements.Entitlements.can_create"
DENIAL = "Automation rules is not available on your plan"
FIXED_REASON = "automation_rule_entitlement_refused"
FIXED_ERROR = (
    "Automation rules are unavailable under the organization's current entitlement."
)


def _decision(allowed):
    return SimpleNamespace(allowed=allowed, reason=None if allowed else DENIAL)


def _sources(organization, workspace, user=None):
    queue = AnnotationQueue.no_workspace_objects.create(
        name=f"AQ-XWS-ENT queue {workspace.pk}",
        organization=organization,
        workspace=workspace,
        created_by=user,
        auto_assign=False,
    )
    if user is not None:
        AnnotationQueueAnnotator.no_workspace_objects.update_or_create(
            queue=queue,
            user=user,
            defaults={"role": "manager", "roles": ["manager", "reviewer", "annotator"]},
        )
    dataset = Dataset.no_workspace_objects.create(
        name=f"AQ-XWS-ENT dataset {workspace.pk}",
        organization=organization,
        workspace=workspace,
    )
    row = Row.no_workspace_objects.create(dataset=dataset, order=1)
    assert queue.workspace_id == dataset.workspace_id, (
        "SETUP_AQ-XWS-ENT same-workspace source"
    )
    return SimpleNamespace(
        org=organization,
        workspace=workspace,
        user=user,
        queue=queue,
        dataset=dataset,
        row=row,
        url=f"/model-hub/annotation-queues/{queue.pk}/automation-rules/",
    )


@pytest.fixture
def world(organization, workspace, user, auth_client):
    result = _sources(organization, workspace, user)
    result.client = auth_client
    assert user.can_access_workspace(workspace), (
        "SETUP_AQ-XWS-ENT authenticated workspace member"
    )
    return result


def _rule(world, *, enabled=True, frequency="manual", deleted=False):
    rule = AutomationRule.no_workspace_objects.create(
        queue=world.queue,
        organization=world.org,
        created_by=world.user,
        name="AQ-XWS-ENT saved rule",
        source_type="dataset_row",
        conditions={"filter": [], "scope": {"dataset_id": str(world.dataset.pk)}},
        enabled=enabled,
        trigger_frequency=frequency,
        deleted=deleted,
    )
    assert rule.queue_id == world.queue.pk, "SETUP_AQ-XWS-ENT persisted rule"
    return rule


def _payload(rule):
    return {
        "name": "AQ-XWS-ENT edited rule",
        "source_type": rule.source_type,
        "conditions": rule.conditions,
        "enabled": True,
        "trigger_frequency": rule.trigger_frequency,
    }


@pytest.mark.parametrize("method", ["put", "patch"])
@pytest.mark.parametrize("initially_enabled", [False, True])
def test_enabled_update_refuses_with_create_error_shape(
    world, method, initially_enabled
):
    rule = _rule(world, enabled=initially_enabled)
    with patch(ENTITLEMENTS, return_value=_decision(False)):
        create = world.client.post(world.url, _payload(rule), format="json")
        assert create.status_code == 403, "SETUP_AQ-XWS-ENT create entitlement refusal"
        response = getattr(world.client, method)(
            f"{world.url}{rule.pk}/",
            _payload(rule),
            format="json",
        )
    assert response.status_code == 403, "AQ-XWS-ENT: enabled update must refuse"
    assert response.data == create.data, (
        "AQ-XWS-ENT: update must retain create's exact error shape"
    )
    rule.refresh_from_db()
    assert rule.enabled is initially_enabled, (
        "AQ-XWS-ENT: denied update must not change enabled"
    )
    assert rule.name == "AQ-XWS-ENT saved rule", (
        "AQ-XWS-ENT: denied update must not save"
    )


def test_partial_update_cadence_rechecks_effective_enabled_rule(world):
    rule = _rule(world)
    assert rule.trigger_frequency == "manual" and rule.enabled, (
        "SETUP_AQ-XWS-ENT enabled manual rule"
    )
    with patch(ENTITLEMENTS, return_value=_decision(False)) as check:
        response = world.client.patch(
            f"{world.url}{rule.pk}/",
            {"trigger_frequency": "hourly"},
            format="json",
        )
    assert response.status_code == 403, "AQ-XWS-ENT: manual-to-hourly PATCH must refuse"
    assert response.data["code"] == "permission_denied", (
        "AQ-XWS-ENT: cadence refusal must retain create code"
    )
    assert check.call_count == 1, "AQ-XWS-ENT: PATCH must recheck exactly once"
    rule.refresh_from_db()
    assert rule.trigger_frequency == "manual", (
        "AQ-XWS-ENT: denied cadence change must not save"
    )


def test_enabled_rename_also_rechecks_entitlement(world):
    rule = _rule(world)
    with patch(ENTITLEMENTS, return_value=_decision(False)):
        response = world.client.patch(
            f"{world.url}{rule.pk}/",
            {"name": "renamed"},
            format="json",
        )
    assert response.status_code == 403, (
        "AQ-XWS-ENT: unchanged enabled state must recheck"
    )


def test_update_excludes_self_but_counts_other_workspace_and_disabled_rules(world):
    rule = _rule(world)
    other_workspace = Workspace.no_workspace_objects.create(
        name="AQ-XWS-ENT other workspace",
        organization=world.org,
        created_by=world.user,
        is_default=False,
        is_active=True,
    )
    other = _sources(world.org, other_workspace, world.user)
    _rule(other, enabled=False)
    _rule(world, deleted=True)

    def at_limit(org_id, resource, current_count):
        assert org_id == str(world.org.pk), (
            "AQ-XWS-ENT: update must use rule organization"
        )
        assert resource == "automation_rules", (
            "AQ-XWS-ENT: update must use automation entitlement"
        )
        return _decision(current_count < 2)

    with patch(ENTITLEMENTS, side_effect=at_limit) as check:
        response = world.client.patch(
            f"{world.url}{rule.pk}/",
            {"name": "allowed rename"},
            format="json",
        )
    assert response.status_code == 200, (
        "AQ-XWS-ENT: a rule at its quota must remain editable"
    )
    assert check.call_count == 1, "AQ-XWS-ENT: enabled update must consult entitlement"
    assert check.call_args.args == (str(world.org.pk), "automation_rules", 1), (
        "AQ-XWS-ENT: count all live org rules excluding only self"
    )


@pytest.mark.parametrize("initially_enabled", [False, True])
def test_disabling_or_remaining_disabled_is_allowed(world, initially_enabled):
    rule = _rule(world, enabled=initially_enabled)
    with patch(ENTITLEMENTS, return_value=_decision(False)) as check:
        response = world.client.patch(
            f"{world.url}{rule.pk}/",
            {"enabled": False},
            format="json",
        )
    assert response.status_code == 200, "AQ-XWS-ENT: disabling must remain available"
    assert check.call_count == 0, "AQ-XWS-ENT: disabled result needs no entitlement"
    rule.refresh_from_db()
    assert rule.enabled is False, "AQ-XWS-ENT: disabled state must persist"


def test_ee_absent_update_remains_allowed(world):
    rule = _rule(world, enabled=False)
    with patch.dict(sys.modules, {"ee.usage.services.entitlements": None}):
        response = world.client.patch(
            f"{world.url}{rule.pk}/",
            {"enabled": True, "trigger_frequency": "hourly"},
            format="json",
        )
    assert response.status_code == 200, "AQ-XWS-ENT: ee-absent update must stay allowed"
    rule.refresh_from_db()
    assert rule.enabled and rule.trigger_frequency == "hourly", (
        "AQ-XWS-ENT: ee-absent update must persist"
    )


def test_schedule_skips_denied_org_and_caches_per_org_only_for_this_run(world):
    refused = [_rule(world, frequency="hourly"), _rule(world, frequency="hourly")]
    allowed_org = Organization.objects.create(name="AQ-XWS-ENT allowed organization")
    allowed_workspace = Workspace.no_workspace_objects.create(
        name="AQ-XWS-ENT allowed workspace",
        organization=allowed_org,
        is_default=True,
        is_active=True,
        created_by=world.user,
    )
    # W1c: scheduled runs require a live creator with manager rights in the queue workspace.
    allowed_member = create_workspace_member_user(allowed_org, allowed_workspace)
    allowed = _sources(allowed_org, allowed_workspace, allowed_member)
    allowed_rule = _rule(allowed, frequency="hourly")
    decisions = {str(world.org.pk): False, str(allowed_org.pk): True}

    def decide(org_id, resource, current_count):
        assert resource == "automation_rules", (
            "AQ-XWS-ENT: schedule must use automation entitlement"
        )
        expected = 1 if org_id == str(world.org.pk) else 0
        assert current_count == expected, (
            "AQ-XWS-ENT: schedule must not count evaluated rule twice"
        )
        return _decision(decisions[org_id])

    with (
        patch(ENTITLEMENTS, side_effect=decide) as check,
        patch.object(tasks.logger, "warning") as warning,
        patch.object(tasks, "evaluate_rule", wraps=helpers.evaluate_rule) as evaluate,
    ):
        clear_workspace_context()
        summary = tasks.run_due_automation_rules()
        assert not QueueItem.no_workspace_objects.filter(queue=world.queue).exists(), (
            "AQ-XWS-ENT: refused schedule must add nothing"
        )
        assert list(
            QueueItem.no_workspace_objects.filter(queue=allowed.queue).values_list(
                "dataset_row_id", flat=True
            )
        ) == [allowed.row.pk], "AQ-XWS-ENT: entitled organization's rule must still run"
        assert summary == {
            "checked": 3,
            "evaluated": 1,
            "errors": 2,
            "added": 1,
            "duplicates": 0,
        }, "AQ-XWS-ENT: scheduled refusal must be counted without additions"
        assert [call.args[0].pk for call in evaluate.call_args_list] == [
            allowed_rule.pk
        ], "AQ-XWS-ENT: refused rule must not reach evaluator"
        assert check.call_count == 2, (
            "AQ-XWS-ENT: one entitlement lookup per organization per run"
        )
        for rule in refused:
            assert any(
                call.args == ("automation_rule_scheduled_skipped_entitlement",)
                and call.kwargs.get("rule_id") == str(rule.pk)
                and call.kwargs.get("reason") == FIXED_REASON
                for call in warning.call_args_list
            ), "AQ-XWS-ENT: scheduled refusal must log fixed reason"
        # A new tick must not retain the refused organization's old decision.
        decisions[str(world.org.pk)] = True
        with patch.object(tasks, "is_automation_rule_due", return_value=True):
            second = tasks.run_due_automation_rules()
        assert second["errors"] == 0, "AQ-XWS-ENT: new tick must refresh entitlement"
        assert check.call_count == 4, (
            "AQ-XWS-ENT: entitlement cache must not escape one run"
        )
        assert QueueItem.no_workspace_objects.filter(queue=world.queue).count() == 1, (
            "AQ-XWS-ENT: newly entitled organization must run"
        )


@pytest.mark.parametrize("path", ["endpoint", "helper", "async"])
def test_manual_run_refuses_before_selection(world, path):
    rule = _rule(world)
    with (
        patch(ENTITLEMENTS, return_value=_decision(False)),
        patch.object(
            helpers,
            "_resolve_dataset_rule_ids",
            wraps=helpers._resolve_dataset_rule_ids,
        ) as select,
        patch.object(helpers, "send_rule_completion_email"),
    ):
        if path == "endpoint":
            response = world.client.post(
                f"{world.url}{rule.pk}/evaluate/", {}, format="json"
            )
            assert response.status_code == 403, (
                "AQ-XWS-ENT: manual endpoint must refuse"
            )
            assert response.data["code"] == "permission_denied", (
                "AQ-XWS-ENT: manual refusal must retain create code"
            )
        else:
            if path == "helper":
                result = helpers.evaluate_rule(rule, user=world.user)
            else:
                activity = getattr(
                    tasks.evaluate_rule_manual_async,
                    "_original_func",
                    tasks.evaluate_rule_manual_async,
                )
                result = activity(str(rule.pk), str(world.user.pk))
            assert result == {
                "matched": 0,
                "added": 0,
                "duplicates": 0,
                "error": FIXED_ERROR,
            }, "AQ-XWS-ENT: manual execution must refuse with zero additions"
    assert select.call_count == 0, (
        "AQ-XWS-ENT: refused manual execution must not resolve sources"
    )
    assert not QueueItem.no_workspace_objects.filter(queue=world.queue).exists(), (
        "AQ-XWS-ENT: refused manual run must add nothing"
    )
    rule.refresh_from_db()
    assert rule.last_triggered_at is None and rule.trigger_count == 0, (
        "AQ-XWS-ENT: refused run must not advance rule state"
    )


def test_ee_absent_schedule_remains_allowed(world):
    _rule(world, frequency="hourly")
    with patch.dict(sys.modules, {"ee.usage.services.entitlements": None}):
        clear_workspace_context()
        summary = tasks.run_due_automation_rules()
    assert summary["added"] == 1 and summary["errors"] == 0, (
        "AQ-XWS-ENT: ee-absent schedule must stay allowed"
    )
    assert QueueItem.no_workspace_objects.filter(
        queue=world.queue, dataset_row=world.row
    ).exists(), "AQ-XWS-ENT: ee-absent execution must persist source"
