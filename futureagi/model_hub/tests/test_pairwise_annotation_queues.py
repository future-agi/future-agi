"""Pairwise queue API contracts. All sources are local dataset rows."""
from datetime import timedelta

import pytest
from django.urls import path, resolve
from django.utils import timezone
from drf_yasg import openapi
from drf_yasg.generators import OpenAPISchemaGenerator

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import WorkspaceMembership
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    QueueItem,
    QueueItemAssignment,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.models.score import Score
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = pytest.mark.django_db
BASE = "/model-hub/annotation-queues/"


def _items(queue_id, suffix=""):
    return f"{BASE}{queue_id}/items/{suffix}"


@pytest.fixture
def sources(organization, workspace):
    dataset = Dataset.objects.create(
        name="Pair sources", organization=organization, workspace=workspace
    )
    return [Row.objects.create(dataset=dataset, order=i) for i in range(4)]


def _source(row):
    return {"source_type": "dataset_row", "source_id": str(row.pk)}


def _queue(client, **overrides):
    response = client.post(
        BASE,
        {"name": "Pairwise queue", "mode": "pairwise", "auto_assign": True,
         "annotations_required": 2, **overrides},
        format="json",
    )
    assert response.status_code == 201, response.data
    assert response.data["mode"] == "pairwise"
    return AnnotationQueue.objects.get(pk=response.data["id"])


def _add(client, queue, sources):
    response = client.post(
        _items(queue.pk, "add-pairs/"),
        {"pairs": [{"source_a": _source(sources[0]), "source_b": _source(sources[1])}]},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["result"]["added"] == 1
    queue.status = "active"
    queue.save(update_fields=["status"])
    return QueueItem.objects.get(queue=queue)


def _submit(client, queue, item, choice="A", comment=""):
    return client.post(
        _items(queue.pk, f"{item.pk}/pairwise/submit/"),
        {"choice": choice, "comment": comment}, format="json",
    )


def _reviewer(organization, workspace, queue):
    user = User.objects.create(
        email="pair-reviewer@example.invalid", name="Second reviewer",
        organization=organization, organization_role=OrganizationRoles.MEMBER,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user, organization=organization, role=OrganizationRoles.MEMBER,
        level=Level.MEMBER, is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user, workspace=workspace, organization_membership=membership,
        role=OrganizationRoles.WORKSPACE_MEMBER, level=Level.MEMBER, is_active=True,
    )
    AnnotationQueueAnnotator.objects.create(
        queue=queue, user=user, role="reviewer", roles=["reviewer"]
    )
    return user


def test_two_reviewers_tallies_and_overall_win_rates(
    auth_client, organization, workspace, user, sources
):
    queue = _queue(auth_client)
    reviewer = _reviewer(organization, workspace, queue)
    item = _add(auth_client, queue, sources)
    assert item.source_a == _source(sources[0])
    assert item.source_b == _source(sources[1])
    assert _submit(auth_client, queue, item, "A", "More accurate").status_code == 200
    item.refresh_from_db()
    assert item.status == "in_progress"
    auth_client.force_authenticate(user=reviewer)
    response = _submit(auth_client, queue, item, "tie", "Equally useful")
    assert response.status_code == 200, response.data
    item.refresh_from_db()
    queue.refresh_from_db()
    assert item.status == "completed"
    assert queue.status == "completed"
    votes = list(item.pairwise_annotations.order_by("created_at"))
    assert [(vote.choice, vote.comment) for vote in votes] == [
        ("A", "More accurate"), ("tie", "Equally useful")
    ]
    assert {vote.annotator_id for vote in votes} == {user.pk, reviewer.pk}
    assert not Score.objects.filter(queue_item=item).exists()
    result = auth_client.get(_items(queue.pk, "pairwise-results/")).data["result"]
    assert result["pairs"][0]["tallies"] == {"A": 1, "B": 0, "tie": 1}
    assert result["overall"] == {
        "total_votes": 2, "tallies": {"A": 1, "B": 0, "tie": 1},
        "win_rates": {"A": 0.5, "B": 0.0, "tie": 0.5},
    }


def test_pairwise_detail_resolves_both_sources_with_queue_scope(auth_client, sources):
    queue = _queue(auth_client)
    item = _add(auth_client, queue, sources)
    response = auth_client.get(_items(queue.pk, f"{item.pk}/annotate-detail/"))
    assert response.status_code == 200, response.data
    detail = response.data["result"]["item"]
    for field in ("source_content", "source_preview"):
        assert detail[field]["type"] == "pairwise"
        for side, row in zip(("source_a", "source_b"), sources[:2]):
            assert detail[field][side]["type"] == "dataset_row"
            assert detail[field][side]["dataset_id"] == str(row.dataset_id)
            assert detail[field][side]["row_order"] == row.order
            assert not detail[field][side].get("deleted")


def test_resubmission_updates_one_vote_and_does_not_meet_required_count(auth_client, sources):
    queue = _queue(auth_client)
    item = _add(auth_client, queue, sources)
    assert _submit(auth_client, queue, item).status_code == 200
    assert _submit(auth_client, queue, item, "B", "Changed mind").status_code == 200
    item.refresh_from_db()
    assert item.pairwise_annotations.count() == 1
    assert item.pairwise_annotations.get().choice == "B"
    assert item.status == "in_progress"


def test_reservation_blocks_other_reviewer_until_expired(
    auth_client, organization, workspace, user, sources
):
    queue = _queue(auth_client)
    reviewer = _reviewer(organization, workspace, queue)
    item = _add(auth_client, queue, sources)
    response = auth_client.get(_items(queue.pk, f"{item.pk}/annotate-detail/"), {"reserve": "true"})
    assert response.status_code == 200, response.data
    item.refresh_from_db()
    assert item.reserved_by_id == user.pk
    auth_client.force_authenticate(user=reviewer)
    response = _submit(auth_client, queue, item)
    assert response.status_code == 409, response.data
    assert item.pairwise_annotations.count() == 0
    item.reservation_expires_at = timezone.now() - timedelta(seconds=1)
    item.save(update_fields=["reservation_expires_at"])
    assert _submit(auth_client, queue, item).status_code == 200
    item.refresh_from_db()
    assert item.reserved_by_id is None


def test_pairwise_submission_honors_manual_assignment(auth_client, organization, workspace, sources):
    queue = _queue(auth_client, auto_assign=False)
    reviewer = _reviewer(organization, workspace, queue)
    item = _add(auth_client, queue, sources)
    auth_client.force_authenticate(user=reviewer)
    assert _submit(auth_client, queue, item).status_code == 403
    QueueItemAssignment.objects.create(queue_item=item, user=reviewer)
    assert _submit(auth_client, queue, item).status_code == 200


@pytest.mark.parametrize("choice", ["C", "a", "", None])
def test_invalid_choice_is_rejected(auth_client, sources, choice):
    queue = _queue(auth_client)
    item = _add(auth_client, queue, sources)
    assert _submit(auth_client, queue, item, choice).status_code == 400
    assert item.pairwise_annotations.count() == 0


def test_paused_queue_rejects_votes(auth_client, sources):
    queue = _queue(auth_client)
    item = _add(auth_client, queue, sources)
    queue.status = "paused"
    queue.save(update_fields=["status"])
    assert _submit(auth_client, queue, item).status_code == 400
    assert item.pairwise_annotations.count() == 0


def test_pairwise_results_empty_and_multiple_pairs(auth_client, sources):
    queue = _queue(auth_client)
    result = auth_client.get(_items(queue.pk, "pairwise-results/")).data["result"]
    assert result["pairs"] == []
    assert result["overall"]["win_rates"] == {"A": 0.0, "B": 0.0, "tie": 0.0}
    item = _add(auth_client, queue, sources)
    response = auth_client.post(
        _items(queue.pk, "add-pairs/"),
        {"pairs": [{"source_a": _source(sources[0]), "source_b": _source(sources[2])}]},
        format="json",
    )
    assert response.status_code == 200, response.data
    second = QueueItem.objects.filter(queue=queue).exclude(pk=item.pk).get()
    assert _submit(auth_client, queue, item, "A").status_code == 200
    assert _submit(auth_client, queue, second, "B").status_code == 200
    result = auth_client.get(_items(queue.pk, "pairwise-results/")).data["result"]
    assert len(result["pairs"]) == 2
    assert result["overall"]["win_rates"] == {"A": 0.5, "B": 0.5, "tie": 0.0}
    second.deleted = True
    second.save(update_fields=["deleted"])
    result = auth_client.get(_items(queue.pk, "pairwise-results/")).data["result"]
    assert len(result["pairs"]) == 1
    assert result["overall"]["total_votes"] == 1


def test_other_organization_cannot_access_pairwise_endpoints(auth_client, sources):
    queue = _queue(auth_client)
    item = _add(auth_client, queue, sources)
    other = Organization.objects.create(name="Other organization")
    AnnotationQueue.no_workspace_objects.filter(pk=queue.pk).update(organization=other)
    QueueItem.no_workspace_objects.filter(pk=item.pk).update(organization=other)
    assert auth_client.get(_items(queue.pk, "pairwise-results/")).status_code == 404
    assert _submit(auth_client, queue, item).status_code == 404
    response = auth_client.post(
        _items(queue.pk, "add-pairs/"),
        {"pairs": [{"source_a": _source(sources[0]), "source_b": _source(sources[1])}]},
        format="json",
    )
    assert response.status_code == 404


def test_foreign_source_and_self_pair_are_rejected_atomically(auth_client, sources):
    queue = _queue(auth_client)
    other = Organization.objects.create(name="Foreign source organization")
    dataset = Dataset.no_workspace_objects.create(name="Foreign", organization=other)
    foreign = Row.objects.create(dataset=dataset, order=0)
    valid = {"source_a": _source(sources[0]), "source_b": _source(sources[1])}
    for invalid in [
        {"source_a": _source(sources[0]), "source_b": _source(foreign)},
        {"source_a": _source(sources[0]), "source_b": _source(sources[0])},
        {"source_a": _source(sources[0])},
    ]:
        response = auth_client.post(
            _items(queue.pk, "add-pairs/"), {"pairs": [valid, invalid]}, format="json"
        )
        assert response.status_code == 400, response.data
        assert not QueueItem.objects.filter(queue=queue).exists()


def test_pairwise_mode_is_immutable_and_standard_add_is_rejected(auth_client, sources):
    queue = _queue(auth_client)
    response = auth_client.patch(f"{BASE}{queue.pk}/", {"mode": "standard"}, format="json")
    assert response.status_code == 400
    response = auth_client.post(
        _items(queue.pk, "add-items/"), {"items": [_source(sources[0])]}, format="json"
    )
    assert response.status_code == 400
    assert not QueueItem.objects.filter(queue=queue).exists()


def test_standard_queue_still_requires_labels(auth_client):
    response = auth_client.post(BASE, {"name": "Missing labels"}, format="json")
    assert response.status_code == 400


def test_standard_queue_defaults_and_label_submission_unchanged(
    auth_client, organization, workspace, sources
):
    label = AnnotationsLabels.objects.create(
        name="Standard label", type="text", organization=organization,
        workspace=workspace, settings={"placeholder": "", "min_length": 0, "max_length": 1000},
    )
    response = auth_client.post(
        BASE, {"name": "Standard", "label_ids": [str(label.pk)]}, format="json"
    )
    assert response.status_code == 201, response.data
    assert response.data["mode"] == "standard"
    queue = AnnotationQueue.objects.get(pk=response.data["id"])
    response = auth_client.post(
        _items(queue.pk, "add-items/"), {"items": [_source(sources[0])]}, format="json"
    )
    assert response.status_code == 200, response.data
    item = QueueItem.objects.get(queue=queue)
    queue.status = "active"
    queue.save(update_fields=["status"])
    response = auth_client.post(
        _items(queue.pk, f"{item.pk}/annotations/submit/"),
        {"annotations": [{"label_id": str(label.pk), "value": {"text": "Useful"}}]},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert Score.objects.get(queue_item=item).value == {"text": "Useful"}
    assert _submit(auth_client, queue, item).status_code == 400
    assert auth_client.get(_items(queue.pk, "pairwise-results/")).status_code == 400
    assert auth_client.post(
        _items(queue.pk, "add-pairs/"),
        {"pairs": [{"source_a": _source(sources[0]), "source_b": _source(sources[1])}]},
        format="json",
    ).status_code == 400


@pytest.mark.parametrize("action", ["approve", "request_changes"])
def test_pairwise_review_detail_and_actions(auth_client, organization, workspace, user, sources, action):
    queue = _queue(auth_client, annotations_required=1, requires_review=True)
    reviewer = _reviewer(organization, workspace, queue)
    item = _add(auth_client, queue, sources)
    assert _submit(auth_client, queue, item, "B", "More precise").status_code == 200
    item.refresh_from_db()
    assert item.review_status == "pending_review"
    detail_url = _items(queue.pk, f"{item.pk}/annotate-detail/")
    review_url = _items(queue.pk, f"{item.pk}/review/")
    own = auth_client.get(detail_url, {"view_mode": "review"}).data["result"]
    assert own["pairwise_annotation"] == {"choice": "B", "comment": "More precise"}
    assert own["pairwise_annotations"][0]["annotator"] == str(user.pk)
    assert auth_client.post(review_url, {"action": action, "notes": "Check accuracy"}, format="json").status_code == 403
    auth_client.force_authenticate(user=reviewer)
    response = auth_client.get(detail_url, {"view_mode": "review"})
    assert response.status_code == 200, response.data
    detail = response.data["result"]
    assert detail["pairwise_annotation"] is None
    assert len(detail["pairwise_annotations"]) == 1
    assert detail["pairwise_annotations"][0]["choice"] == "B"
    assert detail["pairwise_annotations"][0]["comment"] == "More precise"
    assert detail["pairwise_annotations"][0]["annotator"] == str(user.pk)
    response = auth_client.post(review_url, {"action": action, "notes": "Check accuracy"}, format="json")
    assert response.status_code == 200, response.data
    item.refresh_from_db()
    assert item.review_status == ("approved" if action == "approve" else "rejected")
    if action == "request_changes":
        auth_client.force_authenticate(user=user)
        assert _submit(auth_client, queue, item, "tie", "Reconsidered").status_code == 200
        auth_client.force_authenticate(user=reviewer)
        assert auth_client.post(review_url, {"action": "approve"}, format="json").status_code == 200


def test_pairwise_detail_scopes_votes_to_workspace_and_member(auth_client, organization, workspace, user, sources):
    queue = _queue(auth_client, annotations_required=3)
    reviewer = _reviewer(organization, workspace, queue)
    item = _add(auth_client, queue, sources)
    assert _submit(auth_client, queue, item, "A").status_code == 200
    auth_client.force_authenticate(user=reviewer)
    assert _submit(auth_client, queue, item, "B").status_code == 200
    url = _items(queue.pk, f"{item.pk}/annotate-detail/")
    own = auth_client.get(url, {"view_mode": "annotate"}).data["result"]
    assert [vote["annotator"] for vote in own["pairwise_annotations"]] == [str(reviewer.pk)]
    selected = auth_client.get(url, {"view_mode": "review", "annotator_id": str(user.pk)}).data["result"]
    assert [vote["annotator"] for vote in selected["pairwise_annotations"]] == [str(user.pk)]
    assert selected["pairwise_annotation"]["choice"] == "B"
    membership = AnnotationQueueAnnotator.objects.get(queue=queue, user=reviewer)
    membership.role = "annotator"
    membership.roles = ["annotator"]
    membership.save(update_fields=["role", "roles"])
    restricted = auth_client.get(url, {"view_mode": "review", "annotator_id": str(user.pk)}).data["result"]
    assert [vote["annotator"] for vote in restricted["pairwise_annotations"]] == [str(reviewer.pk)]


def test_pairwise_results_paginate_pairs_but_aggregate_entire_queue(auth_client, sources):
    queue = _queue(auth_client)
    first = _add(auth_client, queue, sources)
    response = auth_client.post(_items(queue.pk, "add-pairs/"), {
        "pairs": [{"source_a": _source(sources[2]), "source_b": _source(sources[3])}]
    }, format="json")
    assert response.status_code == 200
    second = QueueItem.objects.filter(queue=queue).exclude(pk=first.pk).get()
    assert _submit(auth_client, queue, first, "A").status_code == 200
    assert _submit(auth_client, queue, second, "B").status_code == 200
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    with CaptureQueriesContext(connection) as queries:
        response = auth_client.get(_items(queue.pk, "pairwise-results/"), {"page": 2, "limit": 1})
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["count"] == 2
    assert result["page"] == 2
    assert result["limit"] == 1
    assert [pair["id"] for pair in result["pairs"]] == [str(second.pk)]
    assert result["overall"]["total_votes"] == 2
    assert result["overall"]["win_rates"] == {"A": 0.5, "B": 0.5, "tie": 0.0}
    # The full-model pair read must be bounded in SQL, not sliced in Python.
    pair_reads = [q["sql"] for q in queries.captured_queries if '"source_a"' in q["sql"] and 'SELECT' in q["sql"].upper()]
    assert pair_reads
    assert all("LIMIT 1" in sql.upper() for sql in pair_reads)
    empty = auth_client.get(_items(queue.pk, "pairwise-results/"), {"page": 3, "limit": 1}).data["result"]
    assert empty["pairs"] == []
    assert empty["overall"] == result["overall"]


def test_pairwise_results_default_page_is_bounded(auth_client, sources):
    queue = _queue(auth_client)
    first = _add(auth_client, queue, sources)
    QueueItem.objects.bulk_create([
        QueueItem(queue=queue, organization=queue.organization, workspace=queue.workspace,
                  source_type="pairwise", source_a=first.source_a, source_b=first.source_b, order=i + 1)
        for i in range(101)
    ])
    result = auth_client.get(_items(queue.pk, "pairwise-results/")).data["result"]
    assert len(result["pairs"]) == 50
    assert result["count"] == 102
    assert result["page"] == 1
    assert result["limit"] == 50
    maximum = auth_client.get(_items(queue.pk, "pairwise-results/"), {"limit": 100}).data["result"]
    assert len(maximum["pairs"]) == 100


@pytest.mark.parametrize("params", [{"page": 0}, {"page": "bad"}, {"limit": 0}, {"limit": 101}])
def test_pairwise_results_reject_invalid_pagination(auth_client, params):
    queue = _queue(auth_client)
    response = auth_client.get(_items(queue.pk, "pairwise-results/"), params)
    assert response.status_code == 400


def test_pairwise_results_schema_documents_exact_query_parameters():
    queue_id = "00000000-0000-0000-0000-000000000001"
    match = resolve(_items(queue_id, "pairwise-results/"))
    generator = OpenAPISchemaGenerator(
        info=openapi.Info(title="Pairwise results", default_version="v1"),
        patterns=[
            path(
                "model-hub/annotation-queues/<uuid:queue_id>/items/pairwise-results/",
                match.func,
            )
        ],
    )
    schema = generator.get_schema(request=None, public=True)
    (path_item,) = schema.paths.values()
    assert path_item["get"]["parameters"] == [
        {
            "name": "page",
            "in": "query",
            "required": False,
            "type": "integer",
            "default": 1,
            "minimum": 1,
            "maximum": 2147483647,
        },
        {
            "name": "limit",
            "in": "query",
            "required": False,
            "type": "integer",
            "default": 50,
            "minimum": 1,
            "maximum": 100,
        },
    ]
