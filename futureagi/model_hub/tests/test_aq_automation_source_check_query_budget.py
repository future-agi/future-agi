"""Request-local source authorization budgets, with real tenant gates and ORM.

The two TestItemsListQueryCount cases are the existing AQ1 RED. These cases
also cover mixed local/foreign/missing sources and the full withheld payload.
Only parse checks run in the writer venue; database execution belongs to AQ1.
"""

import uuid
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models.workspace import Workspace
from conftest import WorkspaceAwareAPIClient
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    QueueItem,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.tests.test_annotation_queues_api import create_workspace_member_user
from tracer.models.project import Project

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


@pytest.fixture
def budget_world(organization, workspace, user):
    foreign = Workspace.no_workspace_objects.create(
        name="W1D foreign workspace",
        organization=organization,
        created_by=user,
        is_active=True,
        is_default=False,
    )
    member = create_workspace_member_user(organization, workspace)
    queue = AnnotationQueue.no_workspace_objects.create(
        name="W1D queue",
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
    datasets, columns = {}, {}
    for name, ws in (("local", workspace), ("foreign", foreign)):
        datasets[name] = Dataset.no_workspace_objects.create(
            name=f"W1D {name} dataset", organization=organization, workspace=ws
        )
        columns[name] = Column.no_workspace_objects.create(
            dataset=datasets[name], name="input", data_type="text", source="OTHERS"
        )
    assert member.can_access_workspace(workspace), "SETUP_AQ-XWS-W1D: local member"
    assert not member.can_access_workspace(foreign), (
        "SETUP_AQ-XWS-W1D: no foreign access"
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=member)
    client.set_workspace(workspace)
    yield SimpleNamespace(
        queue=queue,
        client=client,
        datasets=datasets,
        columns=columns,
        foreign=foreign,
        member=member,
    )
    client.stop_workspace_injection()


def _add_dataset_triplets(world, count, *, captured):
    groups = []
    for _ in range(count):
        group = {}
        for name in ("local", "foreign", "missing"):
            row = None
            if name != "missing":
                row = Row.no_workspace_objects.create(
                    dataset=world.datasets[name], order=1
                )
                Cell.no_workspace_objects.create(
                    dataset=world.datasets[name],
                    column=world.columns[name],
                    row=row,
                    value=f"W1D {name} content",
                )
            group[name] = QueueItem.no_workspace_objects.create(
                queue=world.queue,
                organization_id=world.queue.organization_id,
                workspace_id=world.queue.workspace_id,
                source_type="dataset_row",
                dataset_row=row,
                source_preview=(
                    {"type": "dataset_row", "dataset_name": f"W1D {name} dataset"}
                    if captured
                    else None
                ),
            )
        assert group["missing"].dataset_row_id is None, (
            "SETUP_AQ-XWS-W1D: missing source models the FK cleared by hard deletion"
        )
        assert Row.no_workspace_objects.filter(
            pk=group["foreign"].dataset_row_id
        ).exists(), "SETUP_AQ-XWS-W1D: foreign row really exists"
        groups.append(group)
    return groups


def _read(world, endpoint, item=None):
    base = f"/model-hub/annotation-queues/{world.queue.pk}/"
    if endpoint == "list":
        response = world.client.get(base + "items/", {"limit": 100, "page": 1})
    elif endpoint == "export":
        response = world.client.get(base + "export/", {"export_format": "json"})
    else:
        response = world.client.get(base + f"items/{item.pk}/annotate-detail/")
    assert response.status_code == 200, "SETUP_AQ-XWS-W1D: render request must succeed"
    return response.json()


def _measure(world, endpoint, item):
    _read(world, endpoint, item)  # Warm authentication/URL initialization.
    with CaptureQueriesContext(connection) as queries:
        body = _read(world, endpoint, item)
    # The detail item/source join starts FROM QueueItem, and cells start FROM
    # Cell. This counts only live ownership reads, not the source rendering joins.
    source_reads = sum(
        f'FROM "{Row._meta.db_table}"' in query["sql"]
        for query in queries.captured_queries
    )
    return len(queries.captured_queries), source_reads, body


def _source_payloads(world, endpoint, body, group):
    if endpoint == "annotate-detail":
        return {
            name: (
                detail["item"]["source_content"],
                detail["item"]["source_preview"],
            )
            for name, item in group.items()
            for detail in [_read(world, endpoint, item)["result"]]
        }
    if endpoint == "list":
        entries = {entry["id"]: entry for entry in body["results"]}
        return {
            name: (entries[str(item.pk)]["source_preview"],)
            for name, item in group.items()
        }
    entries = {entry["item_id"]: entry for entry in body["result"]}
    return {name: (entries[str(item.pk)]["source"],) for name, item in group.items()}


@pytest.mark.parametrize("endpoint", ["list", "export", "annotate-detail"])
@pytest.mark.parametrize("captured", [False, True], ids=["live", "captured"])
def test_mixed_source_render_budget_and_withheld_shape(
    budget_world, endpoint, captured
):
    world = budget_world
    group = _add_dataset_triplets(world, 1, captured=captured)[0]
    small, small_reads, _ = _measure(world, endpoint, group["local"])
    _add_dataset_triplets(world, 4, captured=captured)
    large, large_reads, body = _measure(world, endpoint, group["local"])
    payloads = _source_payloads(world, endpoint, body, group)
    withheld = {
        "type": "dataset_row",
        "deleted": True,
        "error": "queue_source_unavailable",
    }
    # These are F3 controls, already green on the base, not purported REDs.
    assert payloads["foreign"] == payloads["missing"], (
        "AQ-XWS-W1D: foreign and missing source payloads must be identical"
    )
    assert all(value == withheld for value in payloads["foreign"]), (
        "AQ-XWS-W1D: withheld payload must remain unchanged"
    )
    assert "W1D local dataset" in str(payloads["local"]), (
        "AQ-XWS-W1D: same-workspace source must remain visible"
    )
    assert "W1D foreign content" not in str(body), (
        "AQ-XWS-W1D: mixed render must not leak foreign content"
    )
    if endpoint != "annotate-detail":
        entries = body["results"] if endpoint == "list" else body["result"]
        assert len(entries) == 15, (
            "SETUP_AQ-XWS-W1D: large response must contain every item"
        )
    assert large == small and small_reads == large_reads == 1, (
        "AQ-XWS-W1D: mixed render must use a flat budget and one source ownership query"
    )


@pytest.mark.parametrize("source_type", ["trace", "observation_span", "trace_session"])
def test_captured_collector_preview_authorization_is_batched(budget_world, source_type):
    world = budget_world
    groups = []
    counts = []
    for size in (1, 4):
        for _ in range(size):
            group = {}
            for name in ("local", "foreign", "missing"):
                project_id = uuid.uuid4()
                if name != "missing":
                    project = Project.no_workspace_objects.create(
                        name=f"W1D {name} project {project_id.hex}",
                        organization_id=world.queue.organization_id,
                        workspace_id=(
                            world.queue.workspace_id
                            if name == "local"
                            else world.foreign.pk
                        ),
                        model_type="GenerativeLLM",
                        trace_type="observe",
                    )
                    project_id = project.pk
                else:
                    assert not Project.no_workspace_objects.filter(
                        pk=project_id
                    ).exists(), (
                        "SETUP_AQ-XWS-W1D: missing project locator must not exist"
                    )
                group[name] = QueueItem.no_workspace_objects.create(
                    queue=world.queue,
                    organization_id=world.queue.organization_id,
                    workspace_id=world.queue.workspace_id,
                    source_type=source_type,
                    project_id=project_id,
                    **{f"{source_type}_id": uuid.uuid4().hex},
                    source_preview={
                        "type": source_type,
                        "project_id": str(project_id),
                        "input_preview": f"W1D {name} captured content",
                    },
                )
            groups.append(group)
        _read(world, "list")
        with CaptureQueriesContext(connection) as queries:
            body = _read(world, "list")
        counts.append(len(queries.captured_queries))
    assert len(body["results"]) == 15, "SETUP_AQ-XWS-W1D: collector page must grow"
    for group in groups:
        payloads = _source_payloads(world, "list", body, group)
        withheld = {
            "type": source_type,
            "deleted": True,
            "error": "queue_source_unavailable",
        }
        assert payloads["foreign"] == payloads["missing"] == (withheld,), (
            "AQ-XWS-W1D: foreign and absent collector projects must have the same withheld shape"
        )
        assert "W1D local captured content" in str(payloads["local"]), (
            "AQ-XWS-W1D: authorized captured collector preview must remain visible"
        )
    assert counts[0] == counts[1], (
        "AQ-XWS-W1D: collector preview ownership queries must not grow with page size"
    )
