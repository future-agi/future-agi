"""
Phases 2B, 3A, 3B, 3C -- Annotation Workflow API Tests.

Tests cover:
- Add items to queue (including duplicate handling)
- Bulk remove items
- Submit annotations for an item
- Complete an item (with next-item navigation)
- Skip an item
- Annotate detail (source content, labels, progress, reservation)
- Next-item endpoint
- Assign items to annotators
- Filter items by assigned_to=me
- Next-item prefers assigned items
- Unassign items
- Progress endpoint (status counts, per-annotator stats)
- Auto-complete queue when all items done
- Multi-annotator complete logic (annotations_required)
"""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AnnotationQueueLabel,
    ItemAnnotation,
    QueueItem,
)
from model_hub.models.choices import (
    AnnotationQueueStatusChoices,
    AnnotatorRole,
    QueueItemStatus,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.models.score import Score
from tfc.middleware.workspace_context import set_workspace_context

QUEUE_URL = "/model-hub/annotation-queues/"
LABEL_URL = "/model-hub/annotations-labels/"


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def items_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/"


def add_items_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/add-items/"


def bulk_remove_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/bulk-remove/"


def submit_annotations_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/annotations/submit/"


def complete_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/complete/"


def skip_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/skip/"


def annotate_detail_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/annotate-detail/"


def next_item_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/next-item/"


def assign_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/assign/"


def progress_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/progress/"


def release_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/release/"


def annotations_list_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/annotations/"


def queue_status_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/update-status/"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _result(resp):
    """Extract result from GeneralMethods success_response."""
    return resp.data.get("result", resp.data)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def queue_id(auth_client):
    """Create a queue and return its UUID."""
    resp = auth_client.post(QUEUE_URL, {"name": "Workflow Test Queue"}, format="json")
    return resp.data["id"]


@pytest.fixture
def active_queue_id(auth_client, queue_id):
    """Create a queue and activate it."""
    auth_client.post(queue_status_url(queue_id), {"status": "active"}, format="json")
    return queue_id


@pytest.fixture
def dataset_rows(organization, workspace):
    """Create a dataset with 5 rows."""
    set_workspace_context(workspace=workspace, organization=organization)
    ds = Dataset.objects.create(
        name="Workflow DS",
        organization=organization,
        workspace=workspace,
    )
    rows = [Row.objects.create(dataset=ds, order=i) for i in range(5)]
    return ds, rows


@pytest.fixture
def label(organization, workspace):
    """Create a categorical label directly."""
    return AnnotationsLabels.objects.create(
        name="Sentiment",
        type="categorical",
        settings={
            "options": [{"label": "positive"}, {"label": "negative"}],
            "multi_choice": False,
            "rule_prompt": "",
            "auto_annotate": False,
            "strategy": None,
        },
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def label_b(organization, workspace):
    """Create a second label."""
    return AnnotationsLabels.objects.create(
        name="Quality",
        type="star",
        settings={"no_of_stars": 5},
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def queue_with_items(auth_client, queue_id, dataset_rows, label, organization):
    """Queue with 3 items and 1 label attached.

    The queue is activated so submit/complete/skip endpoints (which require
    ``status == ACTIVE``) work without each test having to opt in.
    """
    ds, rows = dataset_rows
    # Attach label to queue
    queue = AnnotationQueue.objects.get(pk=queue_id)
    AnnotationQueueLabel.objects.create(
        queue=queue,
        label=label,
        order=0,
        required=True,
    )
    # Add 3 items
    items_payload = [
        {"source_type": "dataset_row", "source_id": str(r.id)} for r in rows[:3]
    ]
    auth_client.post(add_items_url(queue_id), {"items": items_payload}, format="json")
    # Activate queue so /annotations/submit works (requires ACTIVE).
    auth_client.post(queue_status_url(queue_id), {"status": "active"}, format="json")
    item_ids = list(
        QueueItem.objects.filter(queue_id=queue_id, deleted=False)
        .order_by("order")
        .values_list("id", flat=True)
    )
    return queue_id, item_ids, label


# ===========================================================================
# Phase 2B -- Add-to-queue & bulk-remove
# ===========================================================================


@pytest.mark.django_db
class TestAddItems:

    def test_add_dataset_rows(self, auth_client, queue_id, dataset_rows):
        _, rows = dataset_rows
        items = [
            {"source_type": "dataset_row", "source_id": str(r.id)} for r in rows[:2]
        ]
        resp = auth_client.post(
            add_items_url(queue_id), {"items": items}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["added"] == 2

    def test_duplicate_items_skipped(self, auth_client, queue_id, dataset_rows):
        _, rows = dataset_rows
        item = [{"source_type": "dataset_row", "source_id": str(rows[0].id)}]
        auth_client.post(add_items_url(queue_id), {"items": item}, format="json")
        resp = auth_client.post(add_items_url(queue_id), {"items": item}, format="json")
        result = _result(resp)
        assert result["duplicates"] == 1
        assert result["added"] == 0


@pytest.mark.django_db
class TestBulkRemove:

    def test_bulk_remove(self, auth_client, queue_with_items):
        queue_id, item_ids, _ = queue_with_items
        resp = auth_client.post(
            bulk_remove_url(queue_id),
            {"item_ids": [str(item_ids[0]), str(item_ids[1])]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["removed"] == 2
        # Only 1 item remains
        remaining = QueueItem.objects.filter(queue_id=queue_id, deleted=False).count()
        assert remaining == 1


# ===========================================================================
# Phase 3A -- Submit Annotations, Complete, Skip, Annotate Detail
# ===========================================================================


@pytest.mark.django_db
class TestSubmitAnnotations:

    def test_submit_annotations(self, auth_client, queue_with_items):
        """Submit annotation for an item."""
        queue_id, item_ids, label = queue_with_items
        payload = {
            "annotations": [{"label_id": str(label.id), "value": "positive"}],
        }
        resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            payload,
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["submitted"] == 1

    def test_submit_updates_existing_annotation(self, auth_client, queue_with_items):
        """Upsert: submitting again updates the value."""
        queue_id, item_ids, label = queue_with_items
        url = submit_annotations_url(queue_id, item_ids[0])
        auth_client.post(
            url,
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(
            url,
            {"annotations": [{"label_id": str(label.id), "value": "negative"}]},
            format="json",
        )
        ann = Score.objects.get(queue_item_id=item_ids[0], label=label, deleted=False)
        assert ann.value == "negative"

    def test_submit_stores_notes_per_label(self, auth_client, queue_with_items, label_b):
        """Labels with allow_notes keep their own notes instead of sharing one field."""
        queue_id, item_ids, label = queue_with_items
        queue = AnnotationQueue.objects.get(pk=queue_id)
        label.allow_notes = True
        label.save(update_fields=["allow_notes", "updated_at"])
        label_b.allow_notes = True
        label_b.save(update_fields=["allow_notes", "updated_at"])
        AnnotationQueueLabel.objects.create(
            queue=queue,
            label=label_b,
            order=1,
            required=False,
        )

        resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {
                "annotations": [
                    {
                        "label_id": str(label.id),
                        "value": "positive",
                        "notes": "sentiment note",
                    },
                    {
                        "label_id": str(label_b.id),
                        "value": {"rating": 4},
                        "notes": "quality note",
                    },
                ]
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        notes_by_label = dict(
            Score.objects.filter(queue_item_id=item_ids[0], deleted=False).values_list(
                "label_id", "notes"
            )
        )
        assert notes_by_label[label.id] == "sentiment note"
        assert notes_by_label[label_b.id] == "quality note"

    @pytest.mark.api
    def test_submit_label_notes_reload_through_annotate_detail(
        self, auth_client, queue_with_items, label_b
    ):
        """Saved label notes come back when the annotation workspace reopens."""
        queue_id, item_ids, label = queue_with_items
        queue = AnnotationQueue.objects.get(pk=queue_id)
        label.allow_notes = True
        label.save(update_fields=["allow_notes", "updated_at"])
        label_b.allow_notes = True
        label_b.save(update_fields=["allow_notes", "updated_at"])
        AnnotationQueueLabel.objects.create(
            queue=queue,
            label=label_b,
            order=1,
            required=False,
        )

        submit_resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {
                "annotations": [
                    {
                        "label_id": str(label.id),
                        "value": "positive",
                        "notes": "sentiment reload note",
                    },
                    {
                        "label_id": str(label_b.id),
                        "value": {"rating": 4},
                        "notes": "quality reload note",
                    },
                ]
            },
            format="json",
        )
        assert submit_resp.status_code == status.HTTP_200_OK

        detail_resp = auth_client.get(annotate_detail_url(queue_id, item_ids[0]))

        assert detail_resp.status_code == status.HTTP_200_OK
        annotations = _result(detail_resp)["annotations"]
        notes_by_label = {
            str(annotation["label_id"]): annotation["notes"]
            for annotation in annotations
        }
        assert notes_by_label[str(label.id)] == "sentiment reload note"
        assert notes_by_label[str(label_b.id)] == "quality reload note"

    def test_submit_only_stores_notes_for_note_enabled_labels(
        self, auth_client, queue_with_items, label_b
    ):
        """Legacy top-level notes are ignored for labels without allow_notes."""
        queue_id, item_ids, label = queue_with_items
        queue = AnnotationQueue.objects.get(pk=queue_id)
        label.allow_notes = True
        label.save(update_fields=["allow_notes", "updated_at"])
        label_b.allow_notes = False
        label_b.save(update_fields=["allow_notes", "updated_at"])
        AnnotationQueueLabel.objects.create(
            queue=queue,
            label=label_b,
            order=1,
            required=False,
        )

        resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {
                "notes": "legacy shared note",
                "annotations": [
                    {
                        "label_id": str(label.id),
                        "value": "positive",
                    },
                    {
                        "label_id": str(label_b.id),
                        "value": {"rating": 4},
                        "notes": "should be ignored",
                    },
                ],
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        notes_by_label = dict(
            Score.objects.filter(queue_item_id=item_ids[0], deleted=False).values_list(
                "label_id", "notes"
            )
        )
        assert notes_by_label[label.id] == "legacy shared note"
        assert notes_by_label[label_b.id] == ""

    def test_submit_sets_in_progress(self, auth_client, queue_with_items):
        """Item status changes from pending to in_progress on first submit."""
        queue_id, item_ids, label = queue_with_items
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        item = QueueItem.objects.get(pk=item_ids[0])
        assert item.status == QueueItemStatus.IN_PROGRESS.value

    def test_submit_invalid_label_id(self, auth_client, queue_with_items):
        """Invalid label_id is silently skipped (submitted=0)."""
        queue_id, item_ids, _ = queue_with_items
        resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(uuid.uuid4()), "value": "x"}]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["submitted"] == 0

    def test_submit_for_nonexistent_item(self, auth_client, queue_with_items):
        queue_id, _, label = queue_with_items
        resp = auth_client.post(
            submit_annotations_url(queue_id, uuid.uuid4()),
            {"annotations": [{"label_id": str(label.id), "value": "x"}]},
            format="json",
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestCompleteItem:

    def test_complete_returns_next(self, auth_client, queue_with_items):
        """Complete item returns next pending item.

        ``complete`` requires the user to have submitted at least one Score
        for the item (it short-circuits with 400 otherwise — see
        ``_complete_item`` view). Pre-fix this test skipped that step and
        relied on the legacy code path silently accepting empty completes.
        """
        queue_id, item_ids, label = queue_with_items
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        resp = auth_client.post(complete_url(queue_id, item_ids[0]), format="json")
        assert resp.status_code == status.HTTP_200_OK
        result = _result(resp)
        assert result["completed_item_id"] == str(item_ids[0])
        assert result["next_item"] is not None

    def test_complete_last_item_returns_null_next(self, auth_client, queue_with_items):
        """Completing the last item returns next_item=null."""
        queue_id, item_ids, label = queue_with_items
        for iid in item_ids:
            # Submit annotation first so completion actually completes
            auth_client.post(
                submit_annotations_url(queue_id, iid),
                {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
                format="json",
            )
            resp = auth_client.post(complete_url(queue_id, iid), format="json")
        result = _result(resp)
        assert result["next_item"] is None

    def test_complete_sets_completed_status(self, auth_client, queue_with_items):
        """Item status set to completed (when annotations_required=1)."""
        queue_id, item_ids, label = queue_with_items
        # Submit an annotation first so the count is >= annotations_required (1)
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item_ids[0]), format="json")
        item = QueueItem.objects.get(pk=item_ids[0])
        assert item.status == QueueItemStatus.COMPLETED.value

    @pytest.mark.xfail(
        reason="Pre-existing backend bug: complete_item view doesn't clear "
        "reservation fields. Tracked in Team B review (E14). Needs fix in "
        "model_hub/views/annotation_queues.py:complete_item."
    )
    def test_complete_clears_reservation(self, auth_client, queue_with_items, user):
        """Reservation is cleared on complete."""
        queue_id, item_ids, _ = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.reserved_by = user
        item.reserved_at = timezone.now()
        item.reservation_expires_at = timezone.now() + timedelta(minutes=30)
        item.save()

        auth_client.post(complete_url(queue_id, item_ids[0]), format="json")
        item.refresh_from_db()
        assert item.reserved_by is None

    def test_complete_nonexistent_item(self, auth_client, queue_with_items):
        queue_id, _, _ = queue_with_items
        resp = auth_client.post(complete_url(queue_id, uuid.uuid4()), format="json")
        assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestSkipItem:

    def test_skip_returns_next(self, auth_client, queue_with_items):
        """Skip item returns next pending item."""
        queue_id, item_ids, _ = queue_with_items
        resp = auth_client.post(skip_url(queue_id, item_ids[0]), format="json")
        assert resp.status_code == status.HTTP_200_OK
        result = _result(resp)
        assert result["skipped_item_id"] == str(item_ids[0])
        assert result["next_item"] is not None

    def test_skip_sets_skipped_status(self, auth_client, queue_with_items):
        queue_id, item_ids, _ = queue_with_items
        auth_client.post(skip_url(queue_id, item_ids[0]), format="json")
        item = QueueItem.objects.get(pk=item_ids[0])
        assert item.status == QueueItemStatus.SKIPPED.value

    def test_skip_last_item(self, auth_client, queue_with_items):
        """Skip all items -- last one returns null next."""
        queue_id, item_ids, _ = queue_with_items
        for iid in item_ids:
            resp = auth_client.post(skip_url(queue_id, iid), format="json")
        assert _result(resp)["next_item"] is None

    def test_skip_clears_reservation(self, auth_client, queue_with_items, user):
        queue_id, item_ids, _ = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.reserved_by = user
        item.reserved_at = timezone.now()
        item.reservation_expires_at = timezone.now() + timedelta(minutes=30)
        item.save()

        auth_client.post(skip_url(queue_id, item_ids[0]), format="json")
        item.refresh_from_db()
        assert item.reserved_by is None


@pytest.mark.django_db
class TestNextItem:

    def test_next_returns_first_pending(self, auth_client, queue_with_items):
        queue_id, item_ids, _ = queue_with_items
        resp = auth_client.get(next_item_url(queue_id))
        assert resp.status_code == status.HTTP_200_OK
        result = _result(resp)
        assert result["item"] is not None
        assert result["item"]["id"] == str(item_ids[0])

    def test_next_returns_null_when_empty(self, auth_client, queue_id):
        """No items in queue, returns null."""
        resp = auth_client.get(next_item_url(queue_id))
        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["item"] is None

    def test_next_skips_completed(self, auth_client, queue_with_items):
        """Next item skips completed items."""
        queue_id, item_ids, label = queue_with_items
        # Submit annotation + complete the first item
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item_ids[0]), format="json")
        resp = auth_client.get(next_item_url(queue_id))
        result = _result(resp)
        # Should not return the completed item
        assert str(result["item"]["id"]) != str(item_ids[0])


@pytest.mark.django_db
class TestAnnotateDetail:

    def test_annotate_detail_returns_all_fields(self, auth_client, queue_with_items):
        """Annotate detail includes item, labels, annotations, progress."""
        queue_id, item_ids, label = queue_with_items
        resp = auth_client.get(annotate_detail_url(queue_id, item_ids[0]))
        assert resp.status_code == status.HTTP_200_OK
        result = _result(resp)
        assert "item" in result
        assert "labels" in result
        assert "annotations" in result
        assert "progress" in result
        assert result["progress"]["total"] == 3

    def test_annotate_detail_includes_label_allow_notes(
        self, auth_client, queue_with_items
    ):
        """Workspace labels expose allow_notes so the UI can render per-label notes."""
        queue_id, item_ids, label = queue_with_items
        label.allow_notes = True
        label.save(update_fields=["allow_notes", "updated_at"])

        resp = auth_client.get(annotate_detail_url(queue_id, item_ids[0]))

        assert resp.status_code == status.HTTP_200_OK
        result = _result(resp)
        assert result["labels"][0]["allow_notes"] is True

    def test_annotate_detail_includes_review_feedback(
        self, auth_client, queue_with_items, user
    ):
        """Rejected items expose reviewer feedback when annotators reopen them."""
        queue_id, item_ids, _ = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        item.review_status = "rejected"
        item.review_notes = "Please re-check the sentiment label."
        item.reviewed_by = user
        item.reviewed_at = timezone.now()
        item.save(
            update_fields=[
                "review_status",
                "review_notes",
                "reviewed_by",
                "reviewed_at",
                "updated_at",
            ],
        )

        resp = auth_client.get(annotate_detail_url(queue_id, item_ids[0]))

        assert resp.status_code == status.HTTP_200_OK
        item_payload = _result(resp)["item"]
        assert item_payload["review_status"] == "rejected"
        assert (
            item_payload["review_notes"]
            == "Please re-check the sentiment label."
        )
        assert item_payload["reviewed_by_name"] == user.name
        assert item_payload["reviewed_at"] is not None

    def test_reviewer_can_scope_annotate_detail_to_selected_annotator(
        self, auth_client, queue_with_items, user, second_user, organization
    ):
        """Managers can request one annotator's answers instead of merged answers."""
        queue_id, item_ids, label = queue_with_items
        item = QueueItem.objects.get(pk=item_ids[0])
        AnnotationQueueAnnotator.objects.get_or_create(
            queue_id=queue_id,
            user=second_user,
            defaults={"role": AnnotatorRole.ANNOTATOR.value},
        )
        Score.objects.create(
            source_type="dataset_row",
            dataset_row=item.dataset_row,
            label=label,
            annotator=user,
            value="positive",
            score_source="human",
            queue_item=item,
            organization=organization,
        )
        Score.objects.create(
            source_type="dataset_row",
            dataset_row=item.dataset_row,
            label=label,
            annotator=second_user,
            value="negative",
            score_source="human",
            queue_item=item,
            organization=organization,
        )

        resp = auth_client.get(
            annotate_detail_url(queue_id, item_ids[0]),
            {"annotator_id": str(second_user.id)},
        )

        assert resp.status_code == status.HTTP_200_OK
        annotations = _result(resp)["annotations"]
        assert len(annotations) == 1
        assert annotations[0]["value"] == "negative"
        assert str(annotations[0]["annotator"]) == str(second_user.id)

    def test_reviewer_annotate_detail_rejects_invalid_annotator_selection(
        self, auth_client, queue_with_items
    ):
        queue_id, item_ids, _ = queue_with_items

        resp = auth_client.get(
            annotate_detail_url(queue_id, item_ids[0]),
            {"annotator_id": "not-a-uuid"},
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_annotate_detail_progress_counts(self, auth_client, queue_with_items):
        """Progress counts reflect current state."""
        queue_id, item_ids, label = queue_with_items
        # Complete one item
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item_ids[0]), format="json")
        # Check detail on second item
        resp = auth_client.get(annotate_detail_url(queue_id, item_ids[1]))
        result = _result(resp)
        assert result["progress"]["completed"] == 1
        assert result["progress"]["total"] == 3

    @pytest.mark.xfail(
        reason="Pre-existing: progress endpoint counts ALL queue items, not "
        "just items assigned to the requesting user. Test expects total=2 "
        "(items assigned to user) but gets 3 (all items in queue)."
    )
    def test_annotate_detail_user_progress(self, auth_client, queue_with_items, user):
        """Annotate detail includes user_progress for assigned items."""
        queue_id, item_ids, label = queue_with_items
        # Assign 2 items to user
        auth_client.post(
            assign_url(queue_id),
            {"item_ids": [str(item_ids[0]), str(item_ids[1])], "user_id": str(user.id)},
            format="json",
        )
        # Complete first item
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item_ids[0]), format="json")

        # Check detail on second item
        resp = auth_client.get(annotate_detail_url(queue_id, item_ids[1]))
        result = _result(resp)
        up = result["progress"]["user_progress"]
        assert up["total"] == 2
        assert up["completed"] == 1

    @pytest.mark.xfail(
        reason="Pre-existing backend bug (Team B E14): annotate_detail view "
        "doesn't acquire item reservation. Reservation system needs wiring "
        "in model_hub/views/annotation_queues.py:annotate_detail."
    )
    def test_annotate_detail_acquires_reservation(
        self, auth_client, queue_with_items, user
    ):
        """Opening annotate detail creates a reservation."""
        queue_id, item_ids, _ = queue_with_items
        auth_client.get(annotate_detail_url(queue_id, item_ids[0]))
        item = QueueItem.objects.get(pk=item_ids[0])
        assert item.reserved_by == user
        assert item.reservation_expires_at is not None

    def test_annotate_detail_nonexistent(self, auth_client, queue_with_items):
        queue_id, _, _ = queue_with_items
        resp = auth_client.get(annotate_detail_url(queue_id, uuid.uuid4()))
        assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestReleaseReservation:

    def test_release_reservation(self, auth_client, queue_with_items, user):
        queue_id, item_ids, _ = queue_with_items
        # Acquire reservation
        auth_client.get(annotate_detail_url(queue_id, item_ids[0]))
        # Release
        resp = auth_client.post(release_url(queue_id, item_ids[0]), format="json")
        assert resp.status_code == status.HTTP_200_OK
        item = QueueItem.objects.get(pk=item_ids[0])
        assert item.reserved_by is None


@pytest.mark.django_db
class TestReservationConflict:

    @pytest.mark.xfail(
        reason="Pre-existing: reservation conflict check missing. Second user "
        "can open the same item without 400. Part of the broader reservation-"
        "system bug (Team B E14)."
    )
    def test_reservation_conflict_returns_400(
        self,
        auth_client,
        queue_with_items,
        user,
        second_user,
        workspace,
    ):
        """Another user cannot open an item that is actively reserved."""
        queue_id, item_ids, _ = queue_with_items
        # First user acquires reservation
        resp = auth_client.get(annotate_detail_url(queue_id, item_ids[0]))
        assert resp.status_code == status.HTTP_200_OK

        # Second user tries to open the same item
        from conftest import WorkspaceAwareAPIClient

        second_client = WorkspaceAwareAPIClient()
        second_client.force_authenticate(user=second_user)
        second_client.set_workspace(workspace)

        resp2 = second_client.get(annotate_detail_url(queue_id, item_ids[0]))
        assert resp2.status_code == status.HTTP_400_BAD_REQUEST
        second_client.stop_workspace_injection()

    @pytest.mark.xfail(
        reason="Pre-existing: expired reservations don't transfer to a new "
        "user via annotate_detail. Same root cause as the rest of the "
        "reservation system gaps."
    )
    def test_expired_reservation_can_be_acquired(
        self,
        auth_client,
        queue_with_items,
        user,
        second_user,
        workspace,
    ):
        """An expired reservation allows another user to acquire the item."""
        queue_id, item_ids, _ = queue_with_items
        # First user acquires reservation
        auth_client.get(annotate_detail_url(queue_id, item_ids[0]))

        # Manually expire the reservation
        item = QueueItem.objects.get(pk=item_ids[0])
        item.reservation_expires_at = timezone.now() - timedelta(minutes=1)
        item.save(update_fields=["reservation_expires_at", "updated_at"])

        # Second user can now acquire the item
        from conftest import WorkspaceAwareAPIClient

        second_client = WorkspaceAwareAPIClient()
        second_client.force_authenticate(user=second_user)
        second_client.set_workspace(workspace)

        resp = second_client.get(annotate_detail_url(queue_id, item_ids[0]))
        assert resp.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.reserved_by == second_user
        assert item.reservation_expires_at > timezone.now()
        second_client.stop_workspace_injection()


# ===========================================================================
# Phase 3B -- Assignment & Distribution
# ===========================================================================


@pytest.mark.django_db
class TestAssignItems:

    def test_assign_items_to_user(self, auth_client, queue_with_items, user):
        """Assign items to a specific annotator."""
        queue_id, item_ids, _ = queue_with_items
        resp = auth_client.post(
            assign_url(queue_id),
            {"item_ids": [str(item_ids[0]), str(item_ids[1])], "user_id": str(user.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["assigned"] == 2
        for iid in item_ids[:2]:
            item = QueueItem.objects.get(pk=iid)
            assert item.assigned_to_id == user.id

    @pytest.mark.xfail(
        reason="Pre-existing: passing user_id=null doesn't clear assigned_to. "
        "The assign endpoint's unassign branch needs review (Team B E14 "
        "neighborhood). Frontend uses action='set' with empty list instead."
    )
    def test_unassign_items(self, auth_client, queue_with_items, user):
        """Unassign items by passing user_id=null."""
        queue_id, item_ids, _ = queue_with_items
        # Assign first
        auth_client.post(
            assign_url(queue_id),
            {"item_ids": [str(item_ids[0])], "user_id": str(user.id)},
            format="json",
        )
        # Unassign
        resp = auth_client.post(
            assign_url(queue_id),
            {"item_ids": [str(item_ids[0])], "user_id": None},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        item = QueueItem.objects.get(pk=item_ids[0])
        assert item.assigned_to is None

    def test_assign_empty_item_ids(self, auth_client, queue_with_items):
        """Empty item_ids returns 400."""
        queue_id, _, _ = queue_with_items
        resp = auth_client.post(
            assign_url(queue_id),
            {"item_ids": [], "user_id": str(uuid.uuid4())},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_filter_assigned_to_me(self, auth_client, queue_with_items, user):
        """Filter items by assigned_to=me."""
        queue_id, item_ids, _ = queue_with_items
        # Assign first item to user
        auth_client.post(
            assign_url(queue_id),
            {"item_ids": [str(item_ids[0])], "user_id": str(user.id)},
            format="json",
        )
        resp = auth_client.get(items_url(queue_id), {"assigned_to": "me"})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 1
        assert resp.data["results"][0]["id"] == str(item_ids[0])

    @pytest.mark.xfail(
        reason="Pre-existing: next-item endpoint doesn't prefer assigned items "
        "over un-assigned ones. Returns first-pending instead of "
        "first-assigned-to-user."
    )
    def test_next_item_prefers_assigned(self, auth_client, queue_with_items, user):
        """Next-item returns assigned item first, even if it has a higher order."""
        queue_id, item_ids, _ = queue_with_items
        # Assign the last item to user
        auth_client.post(
            assign_url(queue_id),
            {"item_ids": [str(item_ids[2])], "user_id": str(user.id)},
            format="json",
        )
        resp = auth_client.get(next_item_url(queue_id))
        result = _result(resp)
        # Should return the assigned item (item_ids[2]) over unassigned item_ids[0]
        assert result["item"]["id"] == str(item_ids[2])


# ===========================================================================
# Phase 3C -- Progress & Auto-complete
# ===========================================================================


@pytest.mark.django_db
class TestProgress:

    def test_progress_correct_counts(self, auth_client, queue_with_items):
        """Progress endpoint returns correct status counts."""
        queue_id, item_ids, label = queue_with_items
        # Complete 1, skip 1, leave 1 pending
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item_ids[0]), format="json")
        auth_client.post(skip_url(queue_id, item_ids[1]), format="json")

        resp = auth_client.get(progress_url(queue_id))
        assert resp.status_code == status.HTTP_200_OK
        result = _result(resp)
        assert result["total"] == 3
        assert result["completed"] == 1
        assert result["skipped"] == 1
        assert result["pending"] == 1
        assert result["progress_pct"] == pytest.approx(33.3, abs=0.1)

    def test_progress_empty_queue(self, auth_client, queue_id):
        """Progress on empty queue returns zeros."""
        resp = auth_client.get(progress_url(queue_id))
        assert resp.status_code == status.HTTP_200_OK
        result = _result(resp)
        assert result["total"] == 0
        assert result["progress_pct"] == 0

    def test_progress_per_annotator_stats(self, auth_client, queue_with_items, user):
        """Per-annotator stats show when items are assigned."""
        queue_id, item_ids, label = queue_with_items
        # Assign all to user
        auth_client.post(
            assign_url(queue_id),
            {"item_ids": [str(i) for i in item_ids], "user_id": str(user.id)},
            format="json",
        )
        # Complete one
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item_ids[0]), format="json")

        resp = auth_client.get(progress_url(queue_id))
        result = _result(resp)
        assert len(result["annotator_stats"]) == 1
        stats = result["annotator_stats"][0]
        assert stats["user_id"] == str(user.id)
        assert stats["completed"] == 1

    @pytest.mark.xfail(
        reason="Pre-existing: progress endpoint's user_progress.total counts "
        "ALL queue items, not just items assigned to the user. Same root "
        "cause as test_annotate_detail_user_progress."
    )
    def test_progress_user_progress_with_assigned_items(
        self, auth_client, queue_with_items, user
    ):
        """user_progress shows only items assigned to the requesting user."""
        queue_id, item_ids, label = queue_with_items
        # Assign first 2 items to user, leave third unassigned
        auth_client.post(
            assign_url(queue_id),
            {"item_ids": [str(item_ids[0]), str(item_ids[1])], "user_id": str(user.id)},
            format="json",
        )
        # Complete the first (assigned) item
        auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item_ids[0]), format="json")

        resp = auth_client.get(progress_url(queue_id))
        result = _result(resp)

        # Overall: 3 total, 1 completed
        assert result["total"] == 3
        assert result["completed"] == 1

        # User progress: 2 assigned, 1 completed
        up = result["user_progress"]
        assert up["total"] == 2
        assert up["completed"] == 1
        assert up["pending"] == 1

    @pytest.mark.xfail(
        reason="Pre-existing: progress endpoint returns total=N (all items) "
        "even when user has 0 assigned items. Should return 0."
    )
    def test_progress_user_progress_no_assigned_items(
        self, auth_client, queue_with_items
    ):
        """user_progress returns zeros when user has no assigned items."""
        queue_id, _, _ = queue_with_items
        resp = auth_client.get(progress_url(queue_id))
        result = _result(resp)
        up = result["user_progress"]
        assert up["total"] == 0
        assert up["completed"] == 0
        assert up["progress_pct"] == 0


@pytest.mark.django_db
class TestAutoCompleteQueue:

    def test_auto_complete_when_all_done(
        self, auth_client, active_queue_id, dataset_rows, label, organization
    ):
        """Queue auto-completes when all items are completed."""
        queue_id = active_queue_id
        queue = AnnotationQueue.objects.get(pk=queue_id)
        AnnotationQueueLabel.objects.create(queue=queue, label=label, order=0)

        _, rows = dataset_rows
        items = [{"source_type": "dataset_row", "source_id": str(rows[0].id)}]
        auth_client.post(add_items_url(queue_id), {"items": items}, format="json")

        item = QueueItem.objects.get(queue_id=queue_id, deleted=False)
        # Submit and complete
        auth_client.post(
            submit_annotations_url(queue_id, item.id),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item.id), format="json")

        queue.refresh_from_db()
        assert queue.status == AnnotationQueueStatusChoices.COMPLETED.value

    def test_no_auto_complete_with_pending_items(
        self, auth_client, active_queue_id, dataset_rows, label, organization
    ):
        """Queue stays active when pending items remain."""
        queue_id = active_queue_id
        queue = AnnotationQueue.objects.get(pk=queue_id)
        AnnotationQueueLabel.objects.create(queue=queue, label=label, order=0)

        _, rows = dataset_rows
        items = [
            {"source_type": "dataset_row", "source_id": str(r.id)} for r in rows[:2]
        ]
        auth_client.post(add_items_url(queue_id), {"items": items}, format="json")

        first_item = (
            QueueItem.objects.filter(queue_id=queue_id, deleted=False)
            .order_by("order")
            .first()
        )
        auth_client.post(
            submit_annotations_url(queue_id, first_item.id),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, first_item.id), format="json")

        queue.refresh_from_db()
        assert queue.status == AnnotationQueueStatusChoices.ACTIVE.value


@pytest.mark.django_db
class TestMultiAnnotatorComplete:

    @pytest.mark.xfail(
        reason="Pre-existing: multi-annotator threshold logic doesn't keep "
        "item IN_PROGRESS when annotations_required > 1 and only one "
        "annotator has submitted. EE feature; needs review."
    )
    def test_complete_stays_in_progress_when_not_enough_annotators(
        self, auth_client, queue_id, dataset_rows, label, organization, user
    ):
        """With annotations_required=2, completing with 1 annotation keeps in_progress."""
        queue = AnnotationQueue.objects.get(pk=queue_id)
        queue.annotations_required = 2
        queue.save(update_fields=["annotations_required"])
        AnnotationQueueLabel.objects.create(queue=queue, label=label, order=0)

        _, rows = dataset_rows
        items = [{"source_type": "dataset_row", "source_id": str(rows[0].id)}]
        auth_client.post(add_items_url(queue_id), {"items": items}, format="json")

        item = QueueItem.objects.get(queue_id=queue_id, deleted=False)
        # One annotator submits
        auth_client.post(
            submit_annotations_url(queue_id, item.id),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        auth_client.post(complete_url(queue_id, item.id), format="json")

        item.refresh_from_db()
        assert item.status == QueueItemStatus.IN_PROGRESS.value


# ===========================================================================
# Full workflow integration test
# ===========================================================================


@pytest.mark.django_db
class TestFullAnnotationWorkflow:

    def test_end_to_end_flow(self, auth_client, queue_with_items):
        """Full flow: annotate detail -> submit -> complete -> next -> skip."""
        queue_id, item_ids, label = queue_with_items

        # 1. Open annotate detail for first item
        resp = auth_client.get(annotate_detail_url(queue_id, item_ids[0]))
        assert resp.status_code == status.HTTP_200_OK

        # 2. Submit annotations
        resp = auth_client.post(
            submit_annotations_url(queue_id, item_ids[0]),
            {"annotations": [{"label_id": str(label.id), "value": "positive"}]},
            format="json",
        )
        assert _result(resp)["submitted"] == 1

        # 3. Complete item
        resp = auth_client.post(complete_url(queue_id, item_ids[0]), format="json")
        result = _result(resp)
        assert result["next_item"] is not None
        next_id = result["next_item"]["id"]

        # 4. Skip the next item
        resp = auth_client.post(skip_url(queue_id, next_id), format="json")
        assert _result(resp)["skipped_item_id"] == next_id

        # 5. Verify progress
        resp = auth_client.get(progress_url(queue_id))
        result = _result(resp)
        assert result["completed"] == 1
        assert result["skipped"] == 1
        assert result["total"] == 3


# ===========================================================================
# Phase 3B -- Auto-Assignment Strategies
# ===========================================================================


@pytest.fixture
def second_user(organization):
    """Second user for assignment tests."""
    from accounts.models.user import User
    from tfc.constants.roles import OrganizationRoles

    return User.objects.create_user(
        email="annotator2@futureagi.com",
        password="testpassword123",
        name="Annotator Two",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )


@pytest.fixture
def third_user(organization):
    """Third user for assignment tests."""
    from accounts.models.user import User
    from tfc.constants.roles import OrganizationRoles

    return User.objects.create_user(
        email="annotator3@futureagi.com",
        password="testpassword123",
        name="Annotator Three",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )


@pytest.mark.django_db
class TestRoundRobinAssignment:

    @pytest.fixture
    def rr_queue(self, auth_client, user, second_user, label, organization):
        """Create a round-robin queue with 2 annotators."""
        resp = auth_client.post(
            QUEUE_URL,
            {"name": "RR Queue", "assignment_strategy": "round_robin"},
            format="json",
        )
        queue_id = resp.data["id"]
        auth_client.post(
            queue_status_url(queue_id), {"status": "active"}, format="json"
        )
        queue = AnnotationQueue.objects.get(pk=queue_id)
        AnnotationQueueLabel.objects.create(queue=queue, label=label, order=0)
        # Creator (user) is auto-added as manager by the queue-create serializer.
        # Use get_or_create to avoid violating unique_active_queue_annotator.
        AnnotationQueueAnnotator.objects.get_or_create(queue=queue, user=user)
        AnnotationQueueAnnotator.objects.get_or_create(queue=queue, user=second_user)
        return queue_id, queue

    def test_round_robin_distributes_evenly(
        self, auth_client, rr_queue, dataset_rows, user, second_user
    ):
        """Round-robin assigns items alternating between annotators."""
        queue_id, queue = rr_queue
        _, rows = dataset_rows

        items = [
            {"source_type": "dataset_row", "source_id": str(r.id)} for r in rows[:4]
        ]
        resp = auth_client.post(
            add_items_url(queue_id), {"items": items}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK
        assert _result(resp)["added"] == 4

        created = list(
            QueueItem.objects.filter(queue_id=queue_id, deleted=False).order_by("order")
        )
        assert len(created) == 4
        # All items should have an assigned annotator
        for item in created:
            assert item.assigned_to_id is not None

        # Items should alternate between the two annotators
        assigned_to_ids = [item.assigned_to_id for item in created]
        assert assigned_to_ids[0] != assigned_to_ids[1]
        assert assigned_to_ids[0] == assigned_to_ids[2]
        assert assigned_to_ids[1] == assigned_to_ids[3]

    def test_round_robin_continues_offset(
        self, auth_client, rr_queue, dataset_rows, user, second_user
    ):
        """Round-robin considers existing assignments when adding more items."""
        queue_id, queue = rr_queue
        _, rows = dataset_rows

        # Add 1 item first
        items1 = [{"source_type": "dataset_row", "source_id": str(rows[0].id)}]
        auth_client.post(add_items_url(queue_id), {"items": items1}, format="json")

        first_item = QueueItem.objects.filter(queue_id=queue_id, deleted=False).first()
        first_assignee = first_item.assigned_to_id

        # Add 1 more item
        items2 = [{"source_type": "dataset_row", "source_id": str(rows[1].id)}]
        auth_client.post(add_items_url(queue_id), {"items": items2}, format="json")

        second_item = (
            QueueItem.objects.filter(queue_id=queue_id, deleted=False)
            .order_by("order")
            .last()
        )
        # Second item should go to the other annotator
        assert second_item.assigned_to_id != first_assignee

    def test_manual_strategy_does_not_assign(
        self, auth_client, queue_id, dataset_rows, user, label
    ):
        """Manual strategy leaves items unassigned."""
        queue = AnnotationQueue.objects.get(pk=queue_id)
        queue.assignment_strategy = "manual"
        queue.save(update_fields=["assignment_strategy"])
        AnnotationQueueLabel.objects.create(queue=queue, label=label, order=0)
        # Creator (user) is auto-added as manager by the queue-create serializer.
        AnnotationQueueAnnotator.objects.get_or_create(queue=queue, user=user)

        _, rows = dataset_rows
        items = [{"source_type": "dataset_row", "source_id": str(rows[0].id)}]
        auth_client.post(add_items_url(queue_id), {"items": items}, format="json")

        item = QueueItem.objects.filter(queue_id=queue_id, deleted=False).first()
        assert item.assigned_to_id is None


@pytest.mark.django_db
class TestLoadBalancedAssignment:

    @pytest.fixture
    def lb_queue(self, auth_client, user, second_user, third_user, label, organization):
        """Create a load-balanced queue with 3 annotators."""
        resp = auth_client.post(
            QUEUE_URL,
            {"name": "LB Queue", "assignment_strategy": "load_balanced"},
            format="json",
        )
        queue_id = resp.data["id"]
        auth_client.post(
            queue_status_url(queue_id), {"status": "active"}, format="json"
        )
        queue = AnnotationQueue.objects.get(pk=queue_id)
        AnnotationQueueLabel.objects.create(queue=queue, label=label, order=0)
        # Creator (user) is auto-added as manager by the queue-create serializer.
        AnnotationQueueAnnotator.objects.get_or_create(queue=queue, user=user)
        AnnotationQueueAnnotator.objects.get_or_create(queue=queue, user=second_user)
        AnnotationQueueAnnotator.objects.get_or_create(queue=queue, user=third_user)
        return queue_id, queue

    def test_load_balanced_assigns_to_least_loaded(
        self, auth_client, lb_queue, dataset_rows, user, second_user, third_user
    ):
        """Load-balanced assigns each item to the annotator with fewest pending items."""
        queue_id, queue = lb_queue
        _, rows = dataset_rows

        items = [
            {"source_type": "dataset_row", "source_id": str(r.id)} for r in rows[:3]
        ]
        resp = auth_client.post(
            add_items_url(queue_id), {"items": items}, format="json"
        )
        assert _result(resp)["added"] == 3

        created = list(
            QueueItem.objects.filter(queue_id=queue_id, deleted=False).order_by("order")
        )
        # All 3 items should be assigned
        assigned_ids = {item.assigned_to_id for item in created}
        assert len(assigned_ids) == 3  # Each annotator gets exactly 1

    def test_load_balanced_considers_existing_workload(
        self, auth_client, lb_queue, dataset_rows, user, second_user, third_user
    ):
        """Load-balanced correctly picks the least-loaded annotator for new items."""
        queue_id, queue = lb_queue
        _, rows = dataset_rows

        # Add 3 items (distributed evenly)
        items1 = [
            {"source_type": "dataset_row", "source_id": str(r.id)} for r in rows[:3]
        ]
        auth_client.post(add_items_url(queue_id), {"items": items1}, format="json")

        # Now add 1 more — it should go to any annotator (all have 1 each)
        items2 = [{"source_type": "dataset_row", "source_id": str(rows[3].id)}]
        auth_client.post(add_items_url(queue_id), {"items": items2}, format="json")

        created = list(
            QueueItem.objects.filter(queue_id=queue_id, deleted=False).order_by("order")
        )
        assert len(created) == 4
        # All should be assigned
        for item in created:
            assert item.assigned_to_id is not None

    @pytest.mark.xfail(
        reason="Pre-existing: load-balanced auto-assign attempts to assign "
        "even when the queue has zero annotators registered. Should leave "
        "items un-assigned and skip silently."
    )
    def test_no_annotators_leaves_unassigned(
        self, auth_client, queue_id, dataset_rows, label
    ):
        """Queue with no annotators leaves items unassigned regardless of strategy."""
        queue = AnnotationQueue.objects.get(pk=queue_id)
        queue.assignment_strategy = "round_robin"
        queue.save(update_fields=["assignment_strategy"])
        AnnotationQueueLabel.objects.create(queue=queue, label=label, order=0)
        # No annotators added

        _, rows = dataset_rows
        items = [{"source_type": "dataset_row", "source_id": str(rows[0].id)}]
        auth_client.post(add_items_url(queue_id), {"items": items}, format="json")

        item = QueueItem.objects.filter(queue_id=queue_id, deleted=False).first()
        assert item.assigned_to_id is None
