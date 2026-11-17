"""A queue item over a trace id that another organization also holds exports
only the evaluations of its own project's spans.

One trace id is held by two organizations. The first organization's project
holds a span on it and the Postgres trace row; the second organization's
project holds its own span on it. Each organization has an evaluation row on
its own span with no eval config, the shape of an imported score. An
evaluation row names a span id and a trace id, nothing more.

The first organization's owner adds three sources to a queue of its project
through the queue's add-items route: the shared trace, a trace only the first
organization holds (its evaluation is on a child span) and the first
organization's span on the shared trace. The cases pin that the second
organization's evaluation is in none of the queue's export, its export fields,
its export to a dataset or the reader those three share, and that the first
organization's own evaluations export as before.

ClickHouse rows are seeded with the tests' own seed helpers. Postgres, the
views and the helpers run for real.
"""

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.annotation_queues import AnnotationQueue, QueueItem
from model_hub.models.develop_dataset import Cell, Column
from model_hub.views.annotation_queues import _eval_metrics_for_queue_items
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.tests._ch_seed import seed_ch_spans, seed_ch_traces

pytestmark = pytest.mark.django_db

QUEUE_URL = "/model-hub/annotation-queues/"

OTHER_EVAL = "n1-other-organization-eval"
OTHER_EXPLANATION = "n1 explanation written by the other organization"
OTHER_TAG = "n1-other-organization-tag"
OTHER_MARKERS = (OTHER_EVAL, OTHER_EXPLANATION, OTHER_TAG)
OWN_EVAL = "n1-own-eval"
OWN_EXPLANATION = "n1 own explanation on the shared trace"
OWN_CHILD_EXPLANATION = "n1 own explanation on a child span"


def _project(organization, workspace, name):
    return Project.no_workspace_objects.create(
        name=f"N1 {name} {uuid.uuid4().hex[:6]}",
        organization=organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )


def _span(project, trace, *, parent=None):
    now = timezone.now()
    return ObservationSpan.no_workspace_objects.create(
        id=f"n1_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        parent_span_id=parent.id if parent is not None else None,
        name="n1 span",
        observation_type="llm",
        start_time=now - timedelta(seconds=5),
        end_time=now,
        status="OK",
    )


def _evaluation(span, eval_id, explanation, tags=()):
    return EvalLogger.no_workspace_objects.create(
        trace=span.trace,
        observation_span=span,
        eval_id=eval_id,
        output_float=0.5,
        eval_explanation=explanation,
        eval_tags=list(tags),
    )


def _result(response):
    assert response.status_code == status.HTTP_200_OK, response.data
    return response.data["result"]


def _text(response):
    return json.dumps(response.data, default=str)


@pytest.fixture
def shared_trace(user, organization, workspace, auth_client):
    other_org = Organization.objects.create(name=f"N1 other {uuid.uuid4().hex[:6]}")
    other_workspace = Workspace.no_workspace_objects.create(
        name="N1 other workspace",
        organization=other_org,
        created_by=user,
        is_active=True,
        is_default=True,
    )
    own_project = _project(organization, workspace, "own")
    other_project = _project(other_org, other_workspace, "other")
    assert other_project.organization_id != own_project.organization_id, (
        "SETUP_N1: the two projects belong to two organizations"
    )

    # Postgres keeps one trace row per id, here in the first organization's
    # project. Each organization holds its own parentless span on the id.
    shared = Trace.no_workspace_objects.create(project=own_project, name="N1 shared")
    own_span = _span(own_project, shared)
    other_span = _span(other_project, shared)
    only_own = Trace.no_workspace_objects.create(project=own_project, name="N1 own")
    own_root = _span(own_project, only_own)
    own_child = _span(own_project, only_own, parent=own_root)
    seed_ch_spans([own_span, other_span, own_root, own_child])
    seed_ch_traces([shared, only_own])
    _evaluation(other_span, OTHER_EVAL, OTHER_EXPLANATION, [OTHER_TAG])
    _evaluation(own_span, OWN_EVAL, OWN_EXPLANATION)
    _evaluation(own_child, OWN_EVAL, OWN_CHILD_EXPLANATION)

    queue = AnnotationQueue.no_workspace_objects.create(
        name=f"N1 queue {uuid.uuid4().hex[:6]}",
        status="active",
        organization=organization,
        workspace=workspace,
        project=own_project,
        created_by=user,
    )
    clear_workspace_context()
    added = auth_client.post(
        f"{QUEUE_URL}{queue.id}/items/add-items/",
        {
            "project_id": str(own_project.id),
            "items": [
                {"source_type": "trace", "source_id": str(shared.id)},
                {"source_type": "trace", "source_id": str(only_own.id)},
                {"source_type": "observation_span", "source_id": own_span.id},
            ],
        },
        format="json",
    )
    assert added.status_code == status.HTTP_200_OK, ("SETUP_N1: add-items", added.data)
    summary = added.data["result"]
    assert summary["added"] == 3 and summary["errors"] == [], ("SETUP_N1", summary)

    items = list(
        QueueItem.no_workspace_objects.filter(queue=queue, deleted=False).order_by(
            "order"
        )
    )
    by_source = {str(item.trace_id or item.observation_span_id): item for item in items}
    assert {str(item.project_id) for item in items} == {str(own_project.id)}, (
        "SETUP_N1: add-items records the first organization's project on every item"
    )
    clear_workspace_context()
    return SimpleNamespace(
        queue=queue,
        queue_url=f"{QUEUE_URL}{queue.id}/",
        items=items,
        shared_item=by_source[str(shared.id)],
        only_own_item=by_source[str(only_own.id)],
        span_item=by_source[str(own_span.id)],
        request=SimpleNamespace(
            user=user, organization=organization, workspace=workspace
        ),
    )


def _exported(world, auth_client):
    answer = auth_client.get(f"{world.queue_url}export/", {"export_format": "json"})
    rows = {row["item_id"]: row for row in _result(answer)}
    assert set(rows) == {str(item.id) for item in world.items}, (
        "SETUP_N1: the export holds every item"
    )
    for row in rows.values():
        assert "deleted" not in row["source"], ("SETUP_N1: source shown", row)
    return answer, rows


def _explanations(entry):
    entries = entry if isinstance(entry, list) else [entry]
    return [value.get("explanation") for value in entries]


def test_export_of_a_shared_trace_item_carries_none_of_the_other_evaluation(
    shared_trace, auth_client
):
    answer, rows = _exported(shared_trace, auth_client)
    evals = rows[str(shared_trace.shared_item.id)]["evals"]

    assert _explanations(evals.get(OWN_EVAL, [])) == [OWN_EXPLANATION], (
        "SETUP_N1: the item's own evaluation exports",
        evals,
    )
    assert list(evals) == [OWN_EVAL], evals
    for marker in OTHER_MARKERS:
        assert marker not in _text(answer), marker


def test_export_fields_offer_none_of_the_other_evaluation(shared_trace, auth_client):
    answer = auth_client.get(f"{shared_trace.queue_url}export-fields/")
    field_ids = {field["id"] for field in _result(answer)["fields"]}

    assert f"eval:{OWN_EVAL}:score" in field_ids, (
        "SETUP_N1: the item's own evaluation is offered",
        sorted(field_ids),
    )
    assert [fid for fid in field_ids if OTHER_EVAL in fid] == []
    for marker in OTHER_MARKERS:
        assert marker not in _text(answer), marker


def test_export_to_dataset_stores_none_of_the_other_evaluation(
    shared_trace, auth_client
):
    offered = _result(auth_client.get(f"{shared_trace.queue_url}export-fields/"))
    mapping = [
        {"field": field["id"], "column": field["column"], "enabled": True}
        for field in offered["fields"]
        if field.get("id") and field.get("column") and not field.get("expand_fields")
    ]
    # Naming the other organization's evaluation by its field id adds nothing.
    mapping.append(
        {
            "field": f"eval:{OTHER_EVAL}:explanation",
            "column": "N1 named field",
            "enabled": True,
        }
    )

    answer = auth_client.post(
        f"{shared_trace.queue_url}export-to-dataset/",
        {
            "dataset_name": f"N1 export {uuid.uuid4().hex[:8]}",
            "status_filter": "all",
            "column_mapping": mapping,
        },
        format="json",
    )
    body = _result(answer)
    assert body["rows_created"] == 3, ("SETUP_N1: every item makes a row", body)
    stored = Cell._base_manager.filter(dataset_id=body["dataset_id"])
    cells = " ".join(value or "" for value in stored.values_list("value", flat=True))
    named = Column._base_manager.filter(dataset_id=body["dataset_id"])
    columns = " ".join(named.values_list("name", flat=True))

    assert OWN_EXPLANATION in cells, "SETUP_N1: the item's own evaluation is stored"
    for marker in OTHER_MARKERS:
        assert marker not in cells, marker
        assert marker not in columns, marker
        assert marker not in _text(answer), marker


def test_shared_reader_keeps_only_rows_on_the_items_own_spans(shared_trace):
    world = shared_trace
    metrics = _eval_metrics_for_queue_items(world.items, request=world.request)

    shared = metrics[world.shared_item.id]
    assert _explanations(shared.get(OWN_EVAL, [])) == [OWN_EXPLANATION], (
        "SETUP_N1: the item's own evaluation is read",
        shared,
    )
    assert list(shared) == [OWN_EVAL], shared
    for item in world.items:
        for marker in OTHER_MARKERS:
            assert marker not in json.dumps(metrics[item.id], default=str), marker


def test_items_no_other_organization_holds_export_their_evaluations_as_before(
    shared_trace, auth_client
):
    world = shared_trace
    _answer, rows = _exported(world, auth_client)
    metrics = _eval_metrics_for_queue_items(world.items, request=world.request)

    # A trace only the first organization holds, evaluated on a child span.
    only_own = rows[str(world.only_own_item.id)]["evals"]
    assert list(only_own) == [OWN_EVAL], only_own
    assert _explanations(only_own[OWN_EVAL]) == [OWN_CHILD_EXPLANATION]
    assert _explanations(metrics[world.only_own_item.id][OWN_EVAL]) == [
        OWN_CHILD_EXPLANATION
    ]
    # The first organization's span on the shared trace.
    span = rows[str(world.span_item.id)]["evals"]
    assert list(span) == [OWN_EVAL], span
    assert _explanations(span[OWN_EVAL]) == [OWN_EXPLANATION]
    assert _explanations(metrics[world.span_item.id][OWN_EVAL]) == [OWN_EXPLANATION]
