"""Product-boundary defects and actor/queue-linkage regression controls.

Only entitlement and ClickHouse I/O are replaced. Absent EE keeps the protected ENT
absence policy (installations without EE stay uncapped); the lead corrected the
brief's mistaken fail-closed request (W1f-lead LEAD-SLICE.json).
Actor and persisted-queue controls already pass by source at the pinned revision;
they are not represented as new RED defects.
"""

import ast
import inspect
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from accounts.models.workspace import Workspace
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AutomationRule,
    QueueItem,
)
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.tasks import annotation_automation as tasks
from model_hub.tests.test_annotation_queues_api import create_workspace_member_user
from model_hub.tests.test_queue_item_source_copy_scope import _Copies, _copy
from model_hub.utils import annotation_queue_helpers as helpers
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.project import Project

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]
ENTITLEMENT_MODULE = "ee.usage.services.entitlements"
ACTOR_REFUSAL = {
    "matched": 0,
    "added": 0,
    "duplicates": 0,
    "error": "Automation rule actor is unavailable or no longer authorized.",
}
ENTITLEMENT_REFUSAL = {
    "matched": 0,
    "added": 0,
    "duplicates": 0,
    "error": (
        "Automation rules are unavailable under the organization's current entitlement."
    ),
}


def _entitlement_module(allowed):
    return SimpleNamespace(
        Entitlements=SimpleNamespace(
            can_create=lambda *_args: SimpleNamespace(allowed=allowed)
        )
    )


@pytest.fixture
def parity_world(organization, workspace, user):
    manager = create_workspace_member_user(organization, workspace)
    queue = AnnotationQueue.no_workspace_objects.create(
        name="AQ-XWS-W1F queue",
        organization=organization,
        workspace=workspace,
        created_by=manager,
        auto_assign=False,
    )
    dataset = Dataset.no_workspace_objects.create(
        name="AQ-XWS-W1F dataset", organization=organization, workspace=workspace
    )
    row = Row.no_workspace_objects.create(dataset=dataset, order=1)
    rule = AutomationRule.no_workspace_objects.create(
        name="AQ-XWS-W1F rule",
        queue=queue,
        organization=organization,
        created_by=user,
        source_type="dataset_row",
        conditions={"filter": [], "scope": {"dataset_id": str(dataset.pk)}},
        trigger_frequency="manual",
        enabled=True,
    )
    assert user.has_global_workspace_access(organization), (
        "SETUP_AQ-XWS-W1F authorized organization owner"
    )
    assert manager.can_access_workspace(workspace), (
        "SETUP_AQ-XWS-W1F manager has live workspace membership"
    )
    assert not manager.has_global_workspace_access(organization), (
        "SETUP_AQ-XWS-W1F manager cannot bypass through global access"
    )
    clear_workspace_context()
    yield SimpleNamespace(
        org=organization,
        workspace=workspace,
        owner=user,
        manager=manager,
        queue=queue,
        row=row,
        rule=rule,
        request=SimpleNamespace(
            user=manager, organization=organization, workspace=workspace
        ),
    )
    clear_workspace_context()


def _run(world, actor, runner):
    if runner == "helper":
        return helpers.evaluate_rule(world.rule, user=actor)
    activity = getattr(
        tasks.evaluate_rule_manual_async,
        "_original_func",
        tasks.evaluate_rule_manual_async,
    )
    return activity(str(world.rule.pk), str(actor.pk) if actor is not None else None)


def test_resolution_module_has_no_direct_ee_import():
    tree = ast.parse(inspect.getsource(helpers))
    imports = [
        name
        for node in ast.walk(tree)
        for name in (
            [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else []
        )
        if name == "ee" or name.startswith("ee.")
    ]
    assert not imports, "AQ-XWS-W1F: resolution module must not directly import EE"


@pytest.mark.parametrize("runner", ["helper", "async"])
def test_entitlement_denial_still_refuses(parity_world, runner):
    with (
        patch.dict(sys.modules, {ENTITLEMENT_MODULE: _entitlement_module(False)}),
        patch.object(helpers, "send_rule_completion_email"),
    ):
        result = _run(parity_world, parity_world.owner, runner)
    assert result == ENTITLEMENT_REFUSAL, (
        "AQ-XWS-W1F: entitlement denial must retain the zero-write refusal"
    )
    assert not QueueItem.no_workspace_objects.filter(
        queue=parity_world.queue
    ).exists(), "AQ-XWS-W1F: entitlement denial must not write queue items"


def test_absent_entitlement_service_keeps_ent_absence_policy(parity_world):
    # ENT contract (test_aq_automation_entitlement.py: ee-absent update and scheduled
    # execution stay allowed): installations without EE remain uncapped.
    with (
        patch.dict(sys.modules, {ENTITLEMENT_MODULE: None}),
        patch.object(helpers, "send_rule_completion_email"),
    ):
        result = _run(parity_world, parity_world.owner, "helper")
    assert result.get("added") == 1 and not result.get("error"), (
        "AQ-XWS-W1F: absent EE must keep the uncapped ENT absence policy"
    )


@pytest.mark.parametrize("actor_name", ["owner", "manager"])
@pytest.mark.parametrize("runner", ["helper", "async"])
def test_api_authorized_actor_is_accepted_by_worker(parity_world, actor_name, runner):
    from model_hub.views.annotation_queues import _is_queue_manager

    actor = getattr(parity_world, actor_name)
    assert _is_queue_manager(parity_world.queue, actor), (
        "SETUP_AQ-XWS-W1F actor passes the real API manager gate"
    )
    with (
        patch.dict(sys.modules, {ENTITLEMENT_MODULE: _entitlement_module(True)}),
        patch.object(helpers, "send_rule_completion_email"),
    ):
        result = _run(parity_world, actor, runner)
    assert result.get("added") == 1 and not result.get("error"), (
        "AQ-XWS-W1F: API-authorized owner and queue manager must execute"
    )
    assert list(
        QueueItem.no_workspace_objects.filter(queue=parity_world.queue).values_list(
            "dataset_row_id", flat=True
        )
    ) == [parity_world.row.pk], (
        "AQ-XWS-W1F: actor positive control must persist only the owned row"
    )


@pytest.mark.parametrize("runner", ["helper", "async"])
def test_unavailable_actors_have_identical_refusal(parity_world, runner):
    world = parity_world
    foreign_workspace = Workspace.no_workspace_objects.create(
        name="AQ-XWS-W1F foreign workspace",
        organization=world.org,
        created_by=world.owner,
        is_default=False,
        is_active=True,
    )
    foreign = create_workspace_member_user(world.org, foreign_workspace)
    from accounts.services.hard_erase import authorized_scope
    from model_hub.models.annotation_queues import AnnotationQueueAnnotator
    from model_hub.models.choices import AnnotatorRole

    deleted = create_workspace_member_user(world.org, world.workspace)
    AnnotationQueueAnnotator.no_workspace_objects.create(
        queue=world.queue,
        user=deleted,
        role=AnnotatorRole.MANAGER.value,
        roles=[AnnotatorRole.MANAGER.value],
    )
    assert helpers._automation_rule_actor(world.queue, deleted) is not None, (
        "SETUP_AQ-XWS-W1F deleted actor was authorized before deletion"
    )
    stale_deleted = SimpleNamespace(pk=deleted.pk)
    with authorized_scope([world.org.pk], using="default"):
        deleted.delete()
    missing = SimpleNamespace(pk=uuid.uuid4())
    assert not foreign.can_access_workspace(world.workspace), (
        "SETUP_AQ-XWS-W1F foreign actor lacks queue workspace access"
    )
    assert not type(world.owner).objects.filter(pk=stale_deleted.pk).exists(), (
        "SETUP_AQ-XWS-W1F deleted actor is absent despite retained identifier"
    )
    with (
        patch.dict(sys.modules, {ENTITLEMENT_MODULE: _entitlement_module(True)}),
        patch.object(helpers, "send_rule_completion_email") as notify,
    ):
        results = [
            _run(world, actor, runner)
            for actor in (None, stale_deleted, foreign, missing)
        ]
    assert results == [ACTOR_REFUSAL] * 4, (
        "AQ-XWS-W1F: null deleted foreign and nonexistent actors share one refusal"
    )
    assert (
        notify.call_count == 0
        and not QueueItem.no_workspace_objects.filter(queue=world.queue).exists()
    ), "AQ-XWS-W1F: actor refusal must neither notify nor write"


def test_persisted_queue_id_without_relation_authorizes_only_exact_scope(parity_world):
    world = parity_world
    project = Project.no_workspace_objects.create(
        name="AQ-XWS-W1F project",
        organization=world.org,
        workspace=world.workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    trace_id = str(uuid.uuid4())
    span = _copy(project, trace_id=trace_id, span_id=uuid.uuid4().hex[:16], second=0)
    item = QueueItem.no_workspace_objects.create(
        queue=world.queue,
        organization=world.org,
        workspace=world.workspace,
        source_type="trace",
        trace_id=trace_id,
        project=project,
    )
    loaded = QueueItem.no_workspace_objects.get(pk=item.pk)
    assert "queue" not in loaded._state.fields_cache, (
        "SETUP_AQ-XWS-W1F queue relation is not preloaded"
    )
    foreign_workspace = Workspace.no_workspace_objects.create(
        name="AQ-XWS-W1F foreign queue workspace",
        organization=world.org,
        created_by=world.owner,
        is_default=False,
        is_active=True,
    )
    foreign_queue = AnnotationQueue.no_workspace_objects.create(
        name="AQ-XWS-W1F foreign queue",
        organization=world.org,
        workspace=foreign_workspace,
        created_by=world.owner,
    )
    unavailable = [
        QueueItem(
            queue_id=queue_id,
            organization=world.org,
            workspace=world.workspace,
            source_type="trace",
            trace_id=trace_id,
            project=project,
        )
        for queue_id in (None, uuid.uuid4(), foreign_queue.pk)
    ]
    with patch(
        "tracer.services.clickhouse.v2.get_reader", return_value=_Copies([(1, span)])
    ):
        positive = helpers.resolve_source_preview(loaded, request=world.request)
        refused = [
            helpers.resolve_source_preview(candidate, request=world.request)
            for candidate in unavailable
        ]
    assert positive.get("name") == span.name and "deleted" not in positive, (
        "AQ-XWS-W1F: persisted queue identity must work without a loaded relation"
    )
    assert (
        refused
        == [{"type": "trace", "deleted": True, "error": "queue_source_unavailable"}] * 3
    ), "AQ-XWS-W1F: absent foreign and nonexistent queues share the withheld shape"


@pytest.mark.parametrize("source_type", ["trace", "observation_span"])
def test_foreign_and_nonexistent_sources_share_withheld_shape(
    parity_world, source_type
):
    world = parity_world
    foreign_workspace = Workspace.no_workspace_objects.create(
        name="AQ-XWS-W1F foreign source workspace",
        organization=world.org,
        created_by=world.owner,
        is_default=False,
        is_active=True,
    )
    projects = [
        Project.no_workspace_objects.create(
            name=f"AQ-XWS-W1F source project {index}",
            organization=world.org,
            workspace=workspace,
            model_type="GenerativeLLM",
            trace_type="observe",
        )
        for index, workspace in enumerate((world.workspace, foreign_workspace))
    ]
    spans = [
        _copy(
            project,
            trace_id=str(uuid.uuid4()),
            span_id=uuid.uuid4().hex[:16],
            second=index,
        )
        for index, project in enumerate(projects)
    ]
    source_ids = [
        span.trace_id if source_type == "trace" else span.id for span in spans
    ]
    items = [
        QueueItem(
            queue_id=world.queue.pk,
            organization=world.org,
            workspace=world.workspace,
            source_type=source_type,
            project=project,
            **{f"{helpers.get_fk_field_name(source_type)}_id": source_id},
        )
        for project, source_id in (
            (projects[0], source_ids[0]),
            (projects[1], source_ids[1]),
            (projects[0], str(uuid.uuid4())),
        )
    ]
    with patch(
        "tracer.services.clickhouse.v2.get_reader",
        return_value=_Copies(list(enumerate(spans))),
    ):
        results = [
            helpers.resolve_source_content(item, request=world.request)
            for item in items
        ]
    assert results[0].get("type") == source_type and "deleted" not in results[0], (
        "AQ-XWS-W1F: same-scope source must remain renderable"
    )
    assert (
        results[1]
        == results[2]
        == {
            "type": source_type,
            "deleted": True,
            "error": "queue_source_unavailable",
        }
    ), "AQ-XWS-W1F: foreign and nonexistent sources must share the withheld shape"
