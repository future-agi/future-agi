"""Paired automation scope refusals and complete withheld-source payloads.

Rule errors compare the entire parsed JSON body without normalization. Queue
items are intentionally different records: compare their entire source payloads
(not unrelated item IDs, order or navigation), and scan every response for leaks.
No serializer, manager, source resolver or authorization helper is replaced.
"""

import uuid
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
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from tracer.models.project import Project

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]
ENTITLEMENTS = "ee.usage.services.entitlements.Entitlements.can_create"
WITHHELD = {
    "type": "dataset_row",
    "deleted": True,
    "error": "queue_source_unavailable",
}


@pytest.fixture
def source_world(organization, workspace, user):
    foreign_org = Organization.objects.create(name="NF foreign source organization")
    foreign_workspace = Workspace.no_workspace_objects.create(
        name="NF foreign source workspace",
        organization=foreign_org,
        created_by=user,
        is_active=True,
    )
    sibling_workspace = Workspace.no_workspace_objects.create(
        name="NF sibling source workspace",
        organization=organization,
        created_by=user,
        is_active=True,
    )
    queue = AnnotationQueue.no_workspace_objects.create(
        name="NF source queue",
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
    sources = {}
    for name, org, ws in (
        ("local", organization, workspace),
        ("organization", foreign_org, foreign_workspace),
        ("workspace", organization, sibling_workspace),
    ):
        dataset = Dataset.no_workspace_objects.create(
            name=f"NF {name} dataset", organization=org, workspace=ws
        )
        row = Row.no_workspace_objects.create(dataset=dataset, order=1)
        column = Column.no_workspace_objects.create(
            dataset=dataset, name="input", data_type="text", source="OTHERS"
        )
        Cell.no_workspace_objects.create(
            dataset=dataset, row=row, column=column, value=f"NF {name} content"
        )
        project = Project.no_workspace_objects.create(
            name=f"NF {name} project",
            organization=org,
            workspace=ws,
            model_type="GenerativeLLM",
            trace_type="observe",
        )
        sources[name] = SimpleNamespace(dataset=dataset, row=row, project=project)
    assert sources["organization"].dataset.organization_id != organization.pk, (
        "SETUP_AQ-XWS-NF: foreign dataset must belong to another organization"
    )
    assert sources["organization"].project.organization_id != organization.pk, (
        "SETUP_AQ-XWS-NF: foreign project must belong to another organization"
    )
    for source in (sources["workspace"].dataset, sources["workspace"].project):
        assert source.organization_id == organization.pk, (
            "SETUP_AQ-XWS-NF: sibling sources must share the caller organization"
        )
        assert source.workspace_id != workspace.pk, (
            "SETUP_AQ-XWS-NF: sibling sources must belong to another workspace"
        )
    assert user.can_access_workspace(workspace), (
        "SETUP_AQ-XWS-NF: source tests must use a real workspace member"
    )
    assert not queue.dataset_id and not queue.project_id and not queue.is_default, (
        "SETUP_AQ-XWS-NF: custom queue must not have a narrower source binding"
    )
    return SimpleNamespace(queue=queue, user=user, sources=sources)


def _payload(source_type, source_id):
    key = "dataset_id" if source_type == "dataset_row" else "project_id"
    return {
        "name": "NF scope rule",
        "source_type": source_type,
        "conditions": {"filter": [], "scope": {key: str(source_id)}},
        "enabled": True,
        "trigger_frequency": "manual",
    }


def _source(world, name, source_type):
    source = world.sources[name]
    return source.dataset if source_type == "dataset_row" else source.project


@pytest.mark.parametrize("method", ["post", "put", "patch"])
@pytest.mark.parametrize("source_type", ["dataset_row", "trace"])
@pytest.mark.parametrize("foreign_scope", ["organization", "workspace"])
def test_rule_scope_foreign_and_absent_have_identical_errors(
    auth_client, source_world, method, source_type, foreign_scope
):
    world = source_world
    foreign = _source(world, foreign_scope, source_type)
    missing = uuid.uuid4()
    assert type(foreign).no_workspace_objects.filter(pk=foreign.pk).exists(), (
        "SETUP_AQ-XWS-NF: foreign scope locator must resolve outside caller scope"
    )
    assert not type(foreign).no_workspace_objects.filter(pk=missing).exists(), (
        "SETUP_AQ-XWS-NF: missing scope locator must never have been used"
    )
    url = f"/model-hub/annotation-queues/{world.queue.pk}/automation-rules/"
    with patch(ENTITLEMENTS, return_value=SimpleNamespace(allowed=True, reason=None)):
        control = auth_client.post(
            url,
            _payload(source_type, _source(world, "local", source_type).pk),
            format="json",
        )
        assert control.status_code == 201, (
            "SETUP_AQ-XWS-NF: identical request with local source must be accepted"
        )
        rule = AutomationRule.no_workspace_objects.get(queue=world.queue)
        if method != "post":
            url += f"{rule.pk}/"
        before = list(AutomationRule.no_workspace_objects.order_by("id").values())
        responses = [
            getattr(auth_client, method)(
                url, _payload(source_type, source_id), format="json"
            )
            for source_id in (foreign.pk, missing)
        ]
    observed = [(response.status_code, response.json()) for response in responses]
    assert [status for status, _ in observed] == [400, 400], (
        "AQ-XWS-NF: foreign and absent scope locators must both be refused"
    )
    assert observed[0] == observed[1], (
        "AQ-XWS-NF: complete scope refusal envelopes must be identical"
    )
    for _, body in observed:
        assert body["code"] == "automation_rule_scope_unavailable", (
            "AQ-XWS-NF: scope errors must retain the fixed unavailable code"
        )
        assert body["attr"] == "conditions", (
            "AQ-XWS-NF: scope errors must identify conditions consistently"
        )
        assert (
            "Automation rule source is outside the queue workspace or unavailable."
            in str(body)
        ), "AQ-XWS-NF: scope errors must retain the fixed unavailable message"
    assert (
        list(AutomationRule.no_workspace_objects.order_by("id").values()) == before
    ), "AQ-XWS-NF: rejected scope writes must not create or modify rules"
    assert not QueueItem.no_workspace_objects.filter(queue=world.queue).exists(), (
        "AQ-XWS-NF: rejected scope writes must not enqueue source rows"
    )


def _item(world, row, order, captured):
    return QueueItem.no_workspace_objects.create(
        queue=world.queue,
        organization_id=world.queue.organization_id,
        workspace_id=world.queue.workspace_id,
        source_type="dataset_row",
        dataset_row=row,
        order=order,
        source_preview=(
            {"type": "dataset_row", "input_preview": "NF forbidden captured content"}
            if captured
            else None
        ),
    )


def _read_sources(client, world, endpoint, items):
    base = f"/model-hub/annotation-queues/{world.queue.pk}/"
    responses, payloads = [], []
    if endpoint == "export":
        response = client.get(base + "export/", {"export_format": "json"})
        assert response.status_code == 200, (
            "AQ-XWS-NF: queue export must remain readable when sources are withheld"
        )
        body = response.json()
        exported = {entry["item_id"]: entry for entry in body["result"]}
        payloads = [(exported[str(item.pk)]["source"],) for item in items]
        responses.append(body)
    else:
        for item in items:
            suffix = "annotate-detail/" if endpoint == "annotate-detail" else ""
            response = client.get(base + f"items/{item.pk}/{suffix}")
            assert response.status_code == 200, (
                "AQ-XWS-NF: queue items must remain readable with withheld sources"
            )
            body = response.json()
            result = body.get("result", body)
            if endpoint == "annotate-detail":
                payloads.append(
                    (
                        result["item"]["source_content"],
                        result["item"]["source_preview"],
                    )
                )
            else:
                payloads.append((result["source_preview"],))
            responses.append(body)
    return payloads, responses


@pytest.mark.parametrize("endpoint", ["annotate-detail", "export", "preview"])
@pytest.mark.parametrize("foreign_scope", ["organization", "workspace"])
@pytest.mark.parametrize("missing_kind", ["deleted", "absent"])
@pytest.mark.parametrize("captured", [False, True], ids=["live", "captured"])
def test_foreign_and_missing_item_sources_have_identical_withheld_payloads(
    auth_client, source_world, endpoint, foreign_scope, missing_kind, captured
):
    world = source_world
    foreign_row = world.sources[foreign_scope].row
    assert Row.no_workspace_objects.filter(pk=foreign_row.pk, deleted=False).exists(), (
        "SETUP_AQ-XWS-NF: foreign source row must exist and be live"
    )
    missing_row = None
    if missing_kind == "deleted":
        missing_row = Row.no_workspace_objects.create(
            dataset=world.sources["local"].dataset, order=2, deleted=True
        )
        assert Row.all_objects.filter(pk=missing_row.pk, deleted=True).exists(), (
            "SETUP_AQ-XWS-NF: deleted source must be persisted but unavailable"
        )
    foreign_item = _item(world, foreign_row, 1, captured)
    missing_item = _item(world, missing_row, 2, captured)
    if missing_kind == "absent":
        assert missing_item.dataset_row_id is None, (
            "SETUP_AQ-XWS-NF: absent source models the FK cleared by hard deletion"
        )
    local_item = _item(world, world.sources["local"].row, 3, False)
    payloads, bodies = _read_sources(
        auth_client, world, endpoint, [foreign_item, missing_item, local_item]
    )
    assert payloads[0] == payloads[1], (
        "AQ-XWS-NF: foreign and missing source payloads must match in full"
    )
    assert all(payload == WITHHELD for group in payloads[:2] for payload in group), (
        "AQ-XWS-NF: every unavailable source must use the exact withheld shape"
    )
    assert all(payload.get("error") is None for payload in payloads[2]), (
        "AQ-XWS-NF: local source control must not be withheld"
    )
    assert "NF local dataset" in str(payloads[2]), (
        "AQ-XWS-NF: local source control must expose its authorized dataset"
    )
    assert all(
        marker not in str(bodies)
        for marker in (
            f"NF {foreign_scope} dataset",
            f"NF {foreign_scope} content",
            "NF forbidden captured content",
        )
    ), "AQ-XWS-NF: no response may leak foreign or stale captured source content"
