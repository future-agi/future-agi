"""Foreign rule IDs and absent IDs must be indistinguishable before billing.

Compare complete parsed HTTP envelopes, without removing or normalizing fields:
these endpoints do not echo the requested rule ID. Only entitlement decisions
and the Temporal transport are stubbed; SQL capture observes real ORM execution.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AutomationRule,
    QueueItem,
)
from model_hub.models.develop_dataset import Dataset, Row

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]
ENTITLEMENTS = "ee.usage.services.entitlements.Entitlements.can_create"


def _rule(organization, workspace, user):
    queue = AnnotationQueue.no_workspace_objects.create(
        name=f"NF entitlement queue {workspace.pk}",
        organization=organization,
        workspace=workspace,
        created_by=user,
        auto_assign=False,
    )
    AnnotationQueueAnnotator.no_workspace_objects.update_or_create(
        queue=queue,
        user=user,
        defaults={"role": "manager", "roles": ["manager", "reviewer", "annotator"]},
    )
    dataset = Dataset.no_workspace_objects.create(
        name=f"NF entitlement dataset {workspace.pk}",
        organization=organization,
        workspace=workspace,
    )
    Row.no_workspace_objects.create(dataset=dataset, order=1)
    return AutomationRule.no_workspace_objects.create(
        queue=queue,
        organization=organization,
        created_by=user,
        name="NF saved rule",
        source_type="dataset_row",
        conditions={"filter": [], "scope": {"dataset_id": str(dataset.pk)}},
        enabled=True,
        trigger_frequency="manual",
    )


@pytest.fixture
def rules(organization, workspace, user):
    foreign_org = Organization.objects.create(name="NF foreign rule organization")
    foreign_workspace = Workspace.no_workspace_objects.create(
        name="NF foreign rule workspace",
        organization=foreign_org,
        created_by=user,
        is_active=True,
    )
    sibling_workspace = Workspace.no_workspace_objects.create(
        name="NF sibling rule workspace",
        organization=organization,
        created_by=user,
        is_active=True,
    )
    local = _rule(organization, workspace, user)
    foreign = _rule(foreign_org, foreign_workspace, user)
    sibling = _rule(organization, sibling_workspace, user)
    assert foreign.organization_id != local.organization_id, (
        "SETUP_AQ-XWS-NF: foreign rule must have another organization"
    )
    assert sibling.organization_id == local.organization_id, (
        "SETUP_AQ-XWS-NF: sibling rule must share the organization"
    )
    assert sibling.queue.workspace_id != local.queue.workspace_id, (
        "SETUP_AQ-XWS-NF: sibling rule must have another workspace"
    )
    assert user.can_access_workspace(workspace), (
        "SETUP_AQ-XWS-NF: caller must be a local workspace member"
    )
    return SimpleNamespace(local=local, organization=foreign, workspace=sibling)


@pytest.mark.parametrize("action", ["put", "patch", "evaluate", "preview"])
@pytest.mark.parametrize("foreign_scope", ["organization", "workspace"])
@pytest.mark.parametrize("allowed", [False, True], ids=["refused", "allowed"])
@pytest.mark.parametrize("queue_scope", ["local", "foreign"])
def test_rule_notfound_precedes_entitlement_and_selection(
    auth_client, rules, action, foreign_scope, allowed, queue_scope
):
    foreign = getattr(rules, foreign_scope)
    missing = uuid.uuid4()
    assert AutomationRule.no_workspace_objects.filter(pk=foreign.pk).exists(), (
        "SETUP_AQ-XWS-NF: foreign rule must actually exist"
    )
    assert not AutomationRule.no_workspace_objects.filter(pk=missing).exists(), (
        "SETUP_AQ-XWS-NF: comparison ID must never have been used"
    )
    base = f"/model-hub/annotation-queues/{rules.local.queue_id}/automation-rules/"
    control = auth_client.get(f"{base}{rules.local.pk}/")
    assert control.status_code == 200, (
        "SETUP_AQ-XWS-NF: local rule must be visible through the real endpoint"
    )
    if queue_scope == "foreign":
        # Match the foreign rule's own parent as well as trying its ID beneath
        # the local queue; queue-ID mismatch alone must not hide a scope bug.
        base = f"/model-hub/annotation-queues/{foreign.queue_id}/automation-rules/"
    before_rules = list(AutomationRule.no_workspace_objects.order_by("id").values())
    before_items = list(QueueItem.no_workspace_objects.order_by("id").values())
    payload = {
        "name": "NF attempted rename",
        "source_type": "dataset_row",
        "conditions": rules.local.conditions,
        "enabled": True,
        "trigger_frequency": "hourly",
    }
    decision = SimpleNamespace(
        allowed=allowed,
        reason=None if allowed else "Automation rules is not available on your plan",
    )
    responses = []
    with (
        patch(ENTITLEMENTS, return_value=decision) as entitlement,
        patch("tfc.temporal.drop_in.runner.start_activity_sync") as dispatch,
        CaptureQueriesContext(connection) as queries,
    ):
        for rule_id in (foreign.pk, missing):
            url = f"{base}{rule_id}/"
            if action == "evaluate":
                response = auth_client.post(url + "evaluate/", {}, format="json")
            elif action == "preview":
                response = auth_client.get(url + "preview/")
            else:
                response = getattr(auth_client, action)(url, payload, format="json")
            responses.append((response.status_code, response.json()))
    assert [status for status, _ in responses] == [404, 404], (
        "AQ-XWS-NF: foreign and absent rules must both return HTTP 404"
    )
    assert responses[0] == responses[1], (
        "AQ-XWS-NF: complete foreign and absent rule envelopes must be identical"
    )
    assert entitlement.call_count == 0, (
        "AQ-XWS-NF: rule resolution must precede every entitlement lookup"
    )
    assert dispatch.call_count == 0, (
        "AQ-XWS-NF: unavailable rules must not dispatch Temporal work"
    )
    assert all(f'"{Row._meta.db_table}"' not in q["sql"] for q in queries), (
        "AQ-XWS-NF: unavailable dataset rules must not select source rows"
    )
    for model in (AutomationRule, QueueItem):
        assert not any(
            f'{verb} "{model._meta.db_table}"' in query["sql"]
            for query in queries
            for verb in ("INSERT INTO", "UPDATE", "DELETE FROM")
        ), "AQ-XWS-NF: unavailable rules must not issue rule or QueueItem writes"
    assert list(AutomationRule.no_workspace_objects.order_by("id").values()) == (
        before_rules
    ), "AQ-XWS-NF: every persisted rule field must remain unchanged"
    assert (
        list(QueueItem.no_workspace_objects.order_by("id").values()) == before_items
    ), "AQ-XWS-NF: unavailable rules must not add or change QueueItems"


@pytest.mark.parametrize("allowed", [False, True], ids=["refused", "allowed"])
def test_local_preview_exercises_shared_evaluator_without_writes(
    auth_client, rules, allowed
):
    rule = rules.local
    before_rules = list(AutomationRule.no_workspace_objects.order_by("id").values())
    before_items = list(QueueItem.no_workspace_objects.order_by("id").values())
    decision = SimpleNamespace(
        allowed=allowed,
        reason=None if allowed else "Automation rules is not available on your plan",
    )
    with (
        patch(ENTITLEMENTS, return_value=decision) as entitlement,
        patch("tfc.temporal.drop_in.runner.start_activity_sync") as dispatch,
        CaptureQueriesContext(connection) as queries,
    ):
        response = auth_client.get(
            f"/model-hub/annotation-queues/{rule.queue_id}/"
            f"automation-rules/{rule.pk}/preview/"
        )
    assert response.status_code == 200, (
        "AQ-XWS-NF: local preview must reach the shared evaluator response"
    )
    result = response.json()["result"]
    if allowed:
        assert result["matched"] == 1 and not result.get("error"), (
            "AQ-XWS-NF: entitled local preview must select its real source row"
        )
    else:
        assert result == {
            "matched": 0,
            "added": 0,
            "duplicates": 0,
            "error": (
                "Automation rules are unavailable under the organization's current entitlement."
            ),
        }, "AQ-XWS-NF: local preview must preserve the shared evaluator refusal"
    assert entitlement.call_count == 1, (
        "AQ-XWS-NF: authorized local preview must reach the entitlement boundary once"
    )
    assert any(f'"{Row._meta.db_table}"' in q["sql"] for q in queries) is allowed, (
        "AQ-XWS-NF: real source selection must occur only for an entitled local preview"
    )
    assert dispatch.call_count == 0, (
        "AQ-XWS-NF: preview controls must not dispatch work"
    )
    assert (
        list(AutomationRule.no_workspace_objects.order_by("id").values())
        == before_rules
    ), "AQ-XWS-NF: preview controls must not change persisted rule fields"
    assert (
        list(QueueItem.no_workspace_objects.order_by("id").values()) == before_items
    ), "AQ-XWS-NF: preview controls must never add or change QueueItems"
