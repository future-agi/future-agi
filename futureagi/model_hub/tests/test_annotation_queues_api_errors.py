"""Targeted coverage for annotation-queue API branches that the existing
integration suites leave dark.

Each test pins a specific view branch flagged as a coverage gap — an
untested error path or state mutation that could regress silently:

- ``TestAssignItems``            — assign action="remove", non-member reject,
                                   soft-deleted-assignment restore.
- ``TestBulkReviewErrors``       — the per-item ``errors[]`` matrix (not-found,
                                   not-pending, no-scores, own-annotation).
- ``TestSubmitEdgeCases``        — resubmitting a SKIPPED item reactivates a
                                   COMPLETED queue; empty item_notes clears the
                                   stored note.
- ``TestAddLabelGaps``           — nonexistent label 404; re-add toggles the
                                   ``required`` flag instead of duplicating.
- ``TestExportToDatasetErrors``  — duplicate column name / all-columns-disabled
                                   400 guards.
- ``TestHardDeleteCascade``      — CASCADE removes items/assignments/threads;
                                   Score rows survive via SET_NULL (real
                                   behavior, not the docstring's claim).
- ``TestAnalyticsStatusBreakdown`` — review-state bucket classification.
- ``TestProgressStats``          — per-annotator + user_progress aggregation.
- ``TestReviewComment``          — action="comment" leaves status untouched.
- ``TestImportAnnotations``      — unknown annotator_id 400.
- ``TestNextItemOrdering``       — rejected rework item surfaces first.
- ``TestUpdateRequiresReviewGate`` — PATCH requires_review fires the EE gate.
- ``TestDiscussionBlockingThread`` — non-reviewer cannot resolve a blocking
                                   thread.

Real Postgres test DB, real DRF client, EE gates bypassed so tests focus on
queue behavior.
"""

import importlib.util
import json
import uuid
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status

from conftest import WorkspaceAwareAPIClient, create_categorical_label
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AnnotationQueueLabel,
    QueueItem,
    QueueItemAssignment,
    QueueItemNote,
    QueueItemReviewThread,
)
from model_hub.models.choices import (
    AnnotationQueueStatusChoices,
    AnnotatorRole,
    QueueItemStatus,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.models.score import Score

QUEUE_URL = "/model-hub/annotation-queues/"


# ---------------------------------------------------------------------------
# Setup helpers (kept self-contained — pytest can't import sibling test modules)
# ---------------------------------------------------------------------------


def _create_queue(auth_client, name="Test Queue", **extra):
    bootstrap_label = "label_ids" not in extra
    if bootstrap_label:
        extra["label_ids"] = [str(create_categorical_label(auth_client))]
    payload = {"name": name, **extra}
    resp = auth_client.post(QUEUE_URL, payload, format="json")
    assert resp.status_code == status.HTTP_201_CREATED, resp.data
    queue_id = resp.data["id"]
    if bootstrap_label:
        AnnotationQueueLabel.objects.filter(queue_id=queue_id).update(required=False)
    auth_client.post(
        f"{QUEUE_URL}{queue_id}/update-status/",
        {"status": "active"},
        format="json",
    )
    return queue_id


def _create_label(organization, workspace, name="Sentiment", label_type="categorical"):
    settings = {}
    if label_type == "categorical":
        settings = {
            "options": [{"label": "Positive"}, {"label": "Negative"}],
            "multi_choice": False,
            "rule_prompt": "",
            "auto_annotate": False,
            "strategy": None,
        }
    return AnnotationsLabels.objects.create(
        name=name,
        type=label_type,
        organization=organization,
        workspace=workspace,
        settings=settings,
    )


def _create_dataset_row(organization, workspace):
    ds = Dataset.objects.create(
        name="Test DS", organization=organization, workspace=workspace
    )
    row = Row.objects.create(dataset=ds, order=1, metadata={"input": "hello"})
    return ds, row


def _add_item(auth_client, queue_id, row):
    resp = auth_client.post(
        f"{QUEUE_URL}{queue_id}/items/add-items/",
        {"items": [{"source_type": "dataset_row", "source_id": str(row.id)}]},
        format="json",
    )
    assert resp.status_code == status.HTTP_200_OK, resp.data
    return (
        QueueItem.objects.filter(queue_id=queue_id, deleted=False)
        .order_by("-created_at")
        .first()
    )


def _second_user(organization, workspace=None):
    from accounts.models.user import User
    from accounts.models.workspace import WorkspaceMembership
    from tfc.constants.roles import OrganizationRoles

    member = User.objects.create_user(
        email=f"annotator-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Annotator",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    if workspace:
        WorkspaceMembership.objects.get_or_create(
            workspace=workspace,
            user=member,
            defaults={"role": OrganizationRoles.WORKSPACE_MEMBER},
        )
    return member


@pytest.fixture(autouse=True)
def _bypass_entitlements():
    """Bypass EE feature + quota gates so tests focus on queue behavior."""
    with ExitStack() as stack:
        stack.enter_context(patch("tfc.ee_gating.check_ee_can_create"))
        stack.enter_context(patch("tfc.ee_gating.check_ee_feature"))
        try:
            entitlements_available = (
                importlib.util.find_spec("ee.usage.services.entitlements") is not None
            )
        except ModuleNotFoundError:
            entitlements_available = False
        if entitlements_available:
            stack.enter_context(
                patch(
                    "ee.usage.services.entitlements.Entitlements.check_feature",
                    return_value=SimpleNamespace(allowed=True, reason=None),
                )
            )
            stack.enter_context(
                patch(
                    "ee.usage.services.entitlements.Entitlements.can_create",
                    return_value=SimpleNamespace(allowed=True, reason=None),
                )
            )
        yield


# ---------------------------------------------------------------------------
# URL + setup helpers
# ---------------------------------------------------------------------------


def _assign_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/assign/"


def _bulk_review_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/bulk-review/"


def _submit_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/annotations/submit/"


def _import_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/annotations/import/"


def _review_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/review/"


def _next_item_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/next-item/"


def _add_label_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/add-label/"


def _hard_delete_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/hard-delete/"


def _analytics_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/analytics/"


def _progress_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/progress/"


def _export_fields_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/export-fields/"


def _export_to_dataset_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/export-to-dataset/"


def _resolve_thread_url(queue_id, item_id, thread_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/discussion/{thread_id}/resolve/"


def _add_items_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/add-items/"


def _skip_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/skip/"


def _release_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/release/"


def _discussion_url(queue_id, item_id):
    return f"{QUEUE_URL}{queue_id}/items/{item_id}/discussion/"


def _next_item_query_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/next-item/"


def _export_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/export/"


def _for_source_url():
    return f"{QUEUE_URL}for-source/"


def _restore_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/restore/"


def _result(resp):
    return resp.data.get("result", resp.data) if hasattr(resp, "data") else resp.data


def _add_annotator(queue_id, annotator, role=AnnotatorRole.ANNOTATOR.value):
    return AnnotationQueueAnnotator.objects.create(
        queue_id=queue_id,
        user=annotator,
        role=role,
        roles=[role],
    )


def _score_for(item, label, annotator, organization, value="Positive"):
    return Score.objects.create(
        source_type=item.source_type,
        dataset_row=item.dataset_row,
        label=label,
        annotator=annotator,
        value=value,
        score_source="human",
        queue_item=item,
        organization=organization,
        workspace=item.workspace,
    )


def _client_for(user, workspace):
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    return client


# ===========================================================================
# assign — remove / non-member / restore
# ===========================================================================


@pytest.mark.django_db
class TestAssignItems:
    def _queue_and_item(self, auth_client, organization, workspace):
        queue_id = _create_queue(auth_client, name="Assign Q")
        _, row = _create_dataset_row(organization, workspace)
        item = _add_item(auth_client, queue_id, row)
        return queue_id, item

    def test_remove_drops_user_and_clears_legacy_fk(
        self, auth_client, organization, workspace
    ):
        queue_id, item = self._queue_and_item(auth_client, organization, workspace)
        annotator = _second_user(organization, workspace)
        _add_annotator(queue_id, annotator)

        add = auth_client.post(
            _assign_url(queue_id),
            {"item_ids": [str(item.id)], "user_ids": [str(annotator.id)], "action": "add"},
            format="json",
        )
        assert add.status_code == status.HTTP_200_OK, add.data
        assert QueueItemAssignment.objects.filter(
            queue_item=item, user=annotator, deleted=False
        ).exists()
        item.refresh_from_db()
        assert item.assigned_to_id == annotator.id

        remove = auth_client.post(
            _assign_url(queue_id),
            {
                "item_ids": [str(item.id)],
                "user_ids": [str(annotator.id)],
                "action": "remove",
            },
            format="json",
        )
        assert remove.status_code == status.HTTP_200_OK, remove.data
        assert not QueueItemAssignment.objects.filter(
            queue_item=item, user=annotator, deleted=False
        ).exists()
        assert QueueItemAssignment.all_objects.filter(
            queue_item=item, user=annotator, deleted=True
        ).exists()
        item.refresh_from_db()
        assert item.assigned_to_id is None

    def test_rejects_non_member_user(self, auth_client, organization, workspace):
        queue_id, item = self._queue_and_item(auth_client, organization, workspace)
        outsider = _second_user(organization, workspace)  # never added to the queue

        resp = auth_client.post(
            _assign_url(queue_id),
            {"item_ids": [str(item.id)], "user_ids": [str(outsider.id)], "action": "add"},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "is not an annotator in this queue" in str(resp.data)
        assert not QueueItemAssignment.objects.filter(
            queue_item=item, user=outsider
        ).exists()

    def test_readd_restores_soft_deleted_assignment(
        self, auth_client, organization, workspace
    ):
        queue_id, item = self._queue_and_item(auth_client, organization, workspace)
        annotator = _second_user(organization, workspace)
        _add_annotator(queue_id, annotator)

        auth_client.post(
            _assign_url(queue_id),
            {"item_ids": [str(item.id)], "user_ids": [str(annotator.id)], "action": "add"},
            format="json",
        )
        QueueItemAssignment.objects.filter(queue_item=item, user=annotator).update(
            deleted=True, deleted_at=timezone.now()
        )

        readd = auth_client.post(
            _assign_url(queue_id),
            {"item_ids": [str(item.id)], "user_ids": [str(annotator.id)], "action": "add"},
            format="json",
        )
        assert readd.status_code == status.HTTP_200_OK, readd.data

        rows = QueueItemAssignment.objects.filter(queue_item=item, user=annotator)
        assert rows.count() == 1  # restored in place, not duplicated
        assert rows.filter(deleted=False).exists()


# ===========================================================================
# bulk-review — per-item error matrix
# ===========================================================================


@pytest.mark.django_db
class TestBulkReviewErrors:
    def test_partial_errors_alongside_valid_item(
        self, auth_client, organization, workspace, user
    ):
        queue_id = _create_queue(auth_client, name="Bulk Review Q")
        label = _create_label(organization, workspace, name="BR-Label")
        AnnotationQueueLabel.objects.get_or_create(
            queue_id=queue_id, label=label, defaults={"order": 5, "required": False}
        )
        other = _second_user(organization, workspace)
        _add_annotator(queue_id, other)

        def _item():
            _, row = _create_dataset_row(organization, workspace)
            return _add_item(auth_client, queue_id, row)

        valid = _item()
        QueueItem.objects.filter(id=valid.id).update(
            review_status="pending_review", status=QueueItemStatus.IN_PROGRESS.value
        )
        _score_for(valid, label, other, organization)

        no_scores = _item()
        QueueItem.objects.filter(id=no_scores.id).update(
            review_status="pending_review", status=QueueItemStatus.IN_PROGRESS.value
        )

        not_pending = _item()
        QueueItem.objects.filter(id=not_pending.id).update(
            review_status="rejected", status=QueueItemStatus.IN_PROGRESS.value
        )
        _score_for(not_pending, label, other, organization)

        own = _item()
        QueueItem.objects.filter(id=own.id).update(
            review_status="pending_review", status=QueueItemStatus.IN_PROGRESS.value
        )
        _score_for(own, label, user, organization)  # reviewer's own annotation

        bogus = str(uuid.uuid4())

        resp = auth_client.post(
            _bulk_review_url(queue_id),
            {
                "action": "approve",
                "item_ids": [
                    str(valid.id),
                    str(no_scores.id),
                    str(not_pending.id),
                    str(own.id),
                    bogus,
                ],
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        result = _result(resp)

        assert result["reviewed"] == 1
        assert str(valid.id) in result["reviewed_item_ids"]

        errors_by_id = {e["item_id"]: e["error"] for e in result["errors"]}
        assert set(errors_by_id) == {
            str(no_scores.id),
            str(not_pending.id),
            str(own.id),
            bogus,
        }
        assert "not found" in errors_by_id[bogus].lower()
        assert "pending review" in errors_by_id[str(not_pending.id)].lower()
        assert "submitted annotation" in errors_by_id[str(no_scores.id)].lower()
        assert "your own annotation" in errors_by_id[str(own.id)].lower()

        valid.refresh_from_db()
        assert valid.review_status == "approved"
        assert valid.status == QueueItemStatus.COMPLETED.value


# ===========================================================================
# submit — completed-queue reactivation + item_notes clear
# ===========================================================================


@pytest.mark.django_db
class TestSubmitEdgeCases:
    def test_resubmitting_skipped_item_reactivates_completed_queue(
        self, auth_client, organization, workspace
    ):
        queue_id = _create_queue(auth_client, name="Reactivate Q")
        label = _create_label(organization, workspace, name="RA-Label")
        AnnotationQueueLabel.objects.get_or_create(
            queue_id=queue_id, label=label, defaults={"order": 5, "required": False}
        )
        _, row = _create_dataset_row(organization, workspace)
        item = _add_item(auth_client, queue_id, row)

        QueueItem.objects.filter(id=item.id).update(
            status=QueueItemStatus.SKIPPED.value
        )
        AnnotationQueue.objects.filter(id=queue_id).update(
            status=AnnotationQueueStatusChoices.COMPLETED.value
        )

        resp = auth_client.post(
            _submit_url(queue_id, item.id),
            {"annotations": [{"label_id": str(label.id), "value": "Positive"}]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert _result(resp)["submitted"] == 1

        queue = AnnotationQueue.objects.get(id=queue_id)
        assert queue.status == AnnotationQueueStatusChoices.ACTIVE.value

    def test_empty_item_notes_clears_existing_note(
        self, auth_client, organization, workspace, user
    ):
        queue_id = _create_queue(auth_client, name="Notes Q")
        label = _create_label(organization, workspace, name="Notes-Label")
        AnnotationQueueLabel.objects.get_or_create(
            queue_id=queue_id, label=label, defaults={"order": 5, "required": False}
        )
        _, row = _create_dataset_row(organization, workspace)
        item = _add_item(auth_client, queue_id, row)

        first = auth_client.post(
            _submit_url(queue_id, item.id),
            {
                "annotations": [{"label_id": str(label.id), "value": "Positive"}],
                "item_notes": "needs another look",
            },
            format="json",
        )
        assert first.status_code == status.HTTP_200_OK, first.data
        assert QueueItemNote.no_workspace_objects.filter(
            queue_item=item, annotator=user, deleted=False
        ).exists()

        cleared = auth_client.post(
            _submit_url(queue_id, item.id),
            {
                "annotations": [{"label_id": str(label.id), "value": "Positive"}],
                "item_notes": "",
            },
            format="json",
        )
        assert cleared.status_code == status.HTTP_200_OK, cleared.data
        assert not QueueItemNote.no_workspace_objects.filter(
            queue_item=item, annotator=user, deleted=False
        ).exists()


# ===========================================================================
# add-label — not found / required toggle
# ===========================================================================


@pytest.mark.django_db
class TestAddLabelGaps:
    def test_nonexistent_label_returns_404(self, auth_client):
        queue_id = _create_queue(auth_client, name="AddLabel Q")
        resp = auth_client.post(
            _add_label_url(queue_id),
            {"label_id": str(uuid.uuid4())},
            format="json",
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert "Label not found" in str(resp.data)

    def test_readd_toggles_required_without_duplicating(
        self, auth_client, organization, workspace
    ):
        queue_id = _create_queue(auth_client, name="Toggle Q")
        label = _create_label(organization, workspace, name="Toggle-Label")

        first = auth_client.post(
            _add_label_url(queue_id),
            {"label_id": str(label.id), "required": False},
            format="json",
        )
        assert first.status_code == status.HTTP_200_OK, first.data
        assert _result(first)["created"] is True

        second = auth_client.post(
            _add_label_url(queue_id),
            {"label_id": str(label.id), "required": True},
            format="json",
        )
        assert second.status_code == status.HTTP_200_OK, second.data
        assert _result(second)["created"] is False
        assert _result(second)["label"]["required"] is True

        bindings = AnnotationQueueLabel.objects.filter(
            queue_id=queue_id, label=label, deleted=False
        )
        assert bindings.count() == 1
        assert bindings.first().required is True


# ===========================================================================
# export-to-dataset — 400 guards
# ===========================================================================


@pytest.mark.django_db
class TestExportToDatasetErrors:
    def _two_plain_fields(self, auth_client, queue_id):
        resp = auth_client.get(_export_fields_url(queue_id))
        assert resp.status_code == status.HTTP_200_OK, resp.data
        fields = _result(resp)["fields"]
        plain = [f for f in fields if not f.get("expand_fields") and f.get("column")]
        assert len(plain) >= 2
        return plain[:2]

    def test_duplicate_column_name_rejected(self, auth_client):
        queue_id = _create_queue(auth_client, name="Dup Export Q")
        f1, f2 = self._two_plain_fields(auth_client, queue_id)
        resp = auth_client.post(
            _export_to_dataset_url(queue_id),
            {
                "dataset_name": "Dup Export DS",
                "column_mapping": [
                    {"field": f1["id"], "column": "SharedCol", "enabled": True},
                    {"field": f2["id"], "column": "SharedCol", "enabled": True},
                ],
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "Duplicate export column name" in str(resp.data)

    def test_all_columns_disabled_rejected(self, auth_client):
        queue_id = _create_queue(auth_client, name="Empty Export Q")
        fields = self._two_plain_fields(auth_client, queue_id)
        resp = auth_client.post(
            _export_to_dataset_url(queue_id),
            {
                "dataset_name": "Empty Export DS",
                "column_mapping": [
                    {"field": f["id"], "enabled": False} for f in fields
                ],
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "at least one export column" in str(resp.data).lower()


# ===========================================================================
# hard-delete — cascade semantics
# ===========================================================================


@pytest.mark.django_db
class TestHardDeleteCascade:
    def test_cascade_removes_children_and_orphans_scores(
        self, auth_client, organization, workspace, user
    ):
        queue_id = _create_queue(auth_client, name="Cascade Q")
        label = _create_label(organization, workspace, name="Cascade-Label")
        AnnotationQueueLabel.objects.get_or_create(
            queue_id=queue_id, label=label, defaults={"order": 5, "required": False}
        )
        _, row = _create_dataset_row(organization, workspace)
        item = _add_item(auth_client, queue_id, row)
        annotator = _second_user(organization, workspace)
        _add_annotator(queue_id, annotator)
        QueueItemAssignment.objects.create(queue_item=item, user=annotator)
        score = _score_for(item, label, annotator, organization)
        thread = QueueItemReviewThread.objects.create(
            queue_item=item,
            blocking=True,
            status=QueueItemReviewThread.STATUS_OPEN,
            action=QueueItemReviewThread.ACTION_REQUEST_CHANGES,
            organization=organization,
            workspace=workspace,
            created_by=user,
        )

        queue_name = AnnotationQueue.objects.get(id=queue_id).name
        resp = auth_client.post(
            _hard_delete_url(queue_id),
            {"force": True, "confirm_name": queue_name},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data

        # CASCADE children are gone entirely.
        assert not AnnotationQueue.all_objects.filter(id=queue_id).exists()
        assert not QueueItem.all_objects.filter(id=item.id).exists()
        assert not QueueItemAssignment.all_objects.filter(queue_item_id=item.id).exists()
        assert not QueueItemReviewThread.all_objects.filter(id=thread.id).exists()

        # Score.queue_item is SET_NULL, so the Score row SURVIVES (orphaned),
        # despite the endpoint docstring implying it cascades.
        score.refresh_from_db()
        assert score.queue_item_id is None


# ===========================================================================
# analytics — status_breakdown buckets
# ===========================================================================


@pytest.mark.django_db
class TestAnalyticsStatusBreakdown:
    def test_review_state_classification(
        self, auth_client, organization, workspace, user
    ):
        queue_id = _create_queue(auth_client, name="Analytics Q")

        def _item(**update):
            _, row = _create_dataset_row(organization, workspace)
            it = _add_item(auth_client, queue_id, row)
            if update:
                QueueItem.objects.filter(id=it.id).update(**update)
            return it

        _item()  # pending (default)
        _item(status=QueueItemStatus.COMPLETED.value)
        _item(status=QueueItemStatus.SKIPPED.value)
        _item(
            review_status="rejected", status=QueueItemStatus.IN_PROGRESS.value
        )  # needs_changes
        _item(
            review_status="pending_review", status=QueueItemStatus.IN_PROGRESS.value
        )  # in_review
        resubmitted = _item(
            review_status="pending_review", status=QueueItemStatus.IN_PROGRESS.value
        )
        QueueItemReviewThread.objects.create(
            queue_item=resubmitted,
            blocking=True,
            status=QueueItemReviewThread.STATUS_ADDRESSED,
            action=QueueItemReviewThread.ACTION_REQUEST_CHANGES,
            organization=organization,
            workspace=workspace,
            created_by=user,
        )

        resp = auth_client.get(_analytics_url(queue_id))
        assert resp.status_code == status.HTTP_200_OK, resp.data
        breakdown = _result(resp)["status_breakdown"]

        assert breakdown["pending"] == 1
        assert breakdown["completed"] == 1
        assert breakdown["skipped"] == 1
        assert breakdown["needs_changes"] == 1
        assert breakdown["in_review"] == 1
        assert breakdown["resubmitted"] == 1


# NOTE: per-annotator + user_progress aggregation is already covered by
# test_annotation_workflow_api.py::test_progress_per_annotator_stats and
# ::test_progress_user_progress_with_assigned_items — no test added here to
# avoid duplicating that coverage.


# ===========================================================================
# review — action="comment"
# ===========================================================================


@pytest.mark.django_db
class TestReviewComment:
    def test_comment_leaves_status_and_reviewed_by_untouched(
        self, auth_client, organization, workspace, user
    ):
        queue_id = _create_queue(auth_client, name="Comment Q")
        label = _create_label(organization, workspace, name="Comment-Label")
        AnnotationQueueLabel.objects.get_or_create(
            queue_id=queue_id, label=label, defaults={"order": 5, "required": False}
        )
        _, row = _create_dataset_row(organization, workspace)
        item = _add_item(auth_client, queue_id, row)
        _score_for(item, label, user, organization)  # comment skips own-annotation guard

        before_status = QueueItem.objects.get(id=item.id).status

        resp = auth_client.post(
            _review_url(queue_id, item.id),
            {"action": "comment", "notes": "Looks reasonable, one nit."},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data

        item.refresh_from_db()
        assert item.reviewed_by_id is None
        assert item.status == before_status
        assert item.review_status in (None, "", "pending")
        assert QueueItemReviewThread.objects.filter(
            queue_item=item, deleted=False
        ).exists()


# ===========================================================================
# import — unknown annotator
# ===========================================================================


@pytest.mark.django_db
class TestImportAnnotations:
    def test_unknown_annotator_id_returns_400(
        self, auth_client, organization, workspace
    ):
        queue_id = _create_queue(auth_client, name="Import Q")
        label = _create_label(organization, workspace, name="Import-Label")
        AnnotationQueueLabel.objects.get_or_create(
            queue_id=queue_id, label=label, defaults={"order": 5, "required": False}
        )
        _, row = _create_dataset_row(organization, workspace)
        item = _add_item(auth_client, queue_id, row)

        resp = auth_client.post(
            _import_url(queue_id, item.id),
            {
                "annotator_id": str(uuid.uuid4()),
                "annotations": [{"label_id": str(label.id), "value": "Positive"}],
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "Annotator not found" in str(resp.data)


# ===========================================================================
# next-item — rework-first ordering
# ===========================================================================


@pytest.mark.django_db
class TestNextItemOrdering:
    def test_rejected_rework_item_surfaces_before_pending(
        self, auth_client, organization, workspace
    ):
        queue_id = _create_queue(auth_client, name="Rework Order Q")
        _, row1 = _create_dataset_row(organization, workspace)
        _, row2 = _create_dataset_row(organization, workspace)
        # first_pending has the lower order → would win without rework priority.
        first_pending = _add_item(auth_client, queue_id, row1)
        rejected = _add_item(auth_client, queue_id, row2)
        QueueItem.objects.filter(id=rejected.id).update(
            review_status="rejected", status=QueueItemStatus.IN_PROGRESS.value
        )

        annotator = _second_user(organization, workspace)
        _add_annotator(queue_id, annotator)
        QueueItemAssignment.objects.create(queue_item=first_pending, user=annotator)
        QueueItemAssignment.objects.create(queue_item=rejected, user=annotator)

        client = _client_for(annotator, workspace)
        resp = client.get(_next_item_url(queue_id))
        assert resp.status_code == status.HTTP_200_OK, resp.data
        item = _result(resp)["item"]
        assert item is not None
        assert item["id"] == str(rejected.id)
        client.stop_workspace_injection()


# ===========================================================================
# update — requires_review EE gate
# ===========================================================================


@pytest.mark.django_db
class TestUpdateRequiresReviewGate:
    def test_patch_requires_review_invokes_ee_gate(self, auth_client):
        queue_id = _create_queue(auth_client, name="Gate Q")

        # Re-patch locally (shadowing the autouse bypass) to capture the call.
        with patch("tfc.ee_gating.check_ee_feature") as gate:
            resp = auth_client.patch(
                f"{QUEUE_URL}{queue_id}/",
                {"requires_review": True},
                format="json",
            )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert gate.called
        called_feature = gate.call_args.args[0] if gate.call_args.args else None
        from tfc.ee_gating import EEFeature

        assert called_feature == EEFeature.REVIEW_WORKFLOW


# ===========================================================================
# discussion — blocking thread resolve permission
# ===========================================================================


@pytest.mark.django_db
class TestDiscussionBlockingThread:
    def test_non_reviewer_cannot_resolve_blocking_thread(
        self, auth_client, organization, workspace, user
    ):
        queue_id = _create_queue(auth_client, name="Blocking Q")
        _, row = _create_dataset_row(organization, workspace)
        item = _add_item(auth_client, queue_id, row)

        annotator = _second_user(organization, workspace)
        _add_annotator(queue_id, annotator)
        QueueItemAssignment.objects.create(queue_item=item, user=annotator)

        thread = QueueItemReviewThread.objects.create(
            queue_item=item,
            blocking=True,
            status=QueueItemReviewThread.STATUS_OPEN,
            action=QueueItemReviewThread.ACTION_REQUEST_CHANGES,
            target_annotator=None,  # whole-item → visible to the annotator
            organization=organization,
            workspace=workspace,
            created_by=user,
        )

        client = _client_for(annotator, workspace)
        resp = client.post(
            _resolve_thread_url(queue_id, item.id, thread.id), {}, format="json"
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert "reviewers or managers" in str(resp.data).lower()
        client.stop_workspace_injection()


# ===========================================================================
# MEDIUM (remaining)
# ===========================================================================


@pytest.mark.django_db
class TestBulkReviewApproveBlocked:
    def test_approve_blocked_by_open_blocking_thread(
        self, auth_client, organization, workspace, user
    ):
        queue_id = _create_queue(auth_client, name="Bulk Blocked Q")
        label = _create_label(organization, workspace, name="BB-Label")
        AnnotationQueueLabel.objects.get_or_create(
            queue_id=queue_id, label=label, defaults={"order": 5, "required": False}
        )
        other = _second_user(organization, workspace)
        _add_annotator(queue_id, other)
        _, row = _create_dataset_row(organization, workspace)
        item = _add_item(auth_client, queue_id, row)
        QueueItem.objects.filter(id=item.id).update(
            review_status="pending_review", status=QueueItemStatus.IN_PROGRESS.value
        )
        _score_for(item, label, other, organization)
        QueueItemReviewThread.objects.create(
            queue_item=item,
            blocking=True,
            status=QueueItemReviewThread.STATUS_OPEN,
            action=QueueItemReviewThread.ACTION_REQUEST_CHANGES,
            organization=organization,
            workspace=workspace,
            created_by=user,
        )

        resp = auth_client.post(
            _bulk_review_url(queue_id),
            {"action": "approve", "item_ids": [str(item.id)]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        result = _result(resp)
        assert result["reviewed"] == 0
        errors_by_id = {e["item_id"]: e["error"] for e in result["errors"]}
        assert str(item.id) in errors_by_id
        assert "must be addressed" in errors_by_id[str(item.id)].lower()

        item.refresh_from_db()
        assert item.review_status == "pending_review"


@pytest.mark.django_db
class TestExportToDatasetCustomMapping:
    def test_rename_column_and_skip_disabled_field(self, auth_client):
        queue_id = _create_queue(auth_client, name="Custom Map Q")
        fields = _result(auth_client.get(_export_fields_url(queue_id))).get("fields")
        plain = [f for f in fields if not f.get("expand_fields") and f.get("column")]
        assert len(plain) >= 2
        keep, disabled = plain[0], plain[1]

        resp = auth_client.post(
            _export_to_dataset_url(queue_id),
            {
                "dataset_name": "Custom Map DS",
                "column_mapping": [
                    {"field": keep["id"], "column": "Renamed Keep", "enabled": True},
                    {"field": disabled["id"], "enabled": False},
                ],
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        columns = _result(resp)["columns"]
        assert "Renamed Keep" in columns
        assert disabled["column"] not in columns  # disabled field skipped


@pytest.mark.django_db
class TestForSourceGaps:
    def test_invalid_span_notes_source_id_returns_404(self, auth_client):
        sources = json.dumps(
            [
                {
                    "source_type": "observation_span",
                    "source_id": str(uuid.uuid4()),
                    "span_notes_source_id": str(uuid.uuid4()),
                }
            ]
        )
        resp = auth_client.get(_for_source_url(), {"sources": sources})
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert "Span notes source not found" in str(resp.data)

    def test_queue_created_by_user_is_included(
        self, auth_client, organization, workspace, user
    ):
        queue_id = _create_queue(auth_client, name="Creator Q")
        # Strip any annotator membership so inclusion can only come from
        # the created_by path (not user_queue_ids / default_queue_ids).
        AnnotationQueueAnnotator.all_objects.filter(
            queue_id=queue_id, user=user
        ).delete()
        _, row = _create_dataset_row(organization, workspace)
        _add_item(auth_client, queue_id, row)

        resp = auth_client.get(
            _for_source_url(),
            {"source_type": "dataset_row", "source_id": str(row.id)},
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        queue_ids = {entry["queue"]["id"] for entry in _result(resp)}
        assert str(queue_id) in queue_ids


# ===========================================================================
# LOW
# ===========================================================================


@pytest.mark.django_db
class TestAddItemsEnumeratedErrors:
    def test_not_found_source_reported_in_errors(self, auth_client):
        queue_id = _create_queue(auth_client, name="Add Not Found Q")
        resp = auth_client.post(
            _add_items_url(queue_id),
            {"items": [{"source_type": "dataset_row", "source_id": str(uuid.uuid4())}]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert "Not found: dataset_row=" in str(resp.data)


@pytest.mark.django_db
class TestSkipTargetedRework:
    def test_skip_denied_for_non_target_annotator(
        self, auth_client, organization, workspace, user
    ):
        queue_id = _create_queue(auth_client, name="Skip Rework Q")
        _, row = _create_dataset_row(organization, workspace)
        item = _add_item(auth_client, queue_id, row)

        target = _second_user(organization, workspace)
        other = _second_user(organization, workspace)
        _add_annotator(queue_id, target)
        _add_annotator(queue_id, other)
        QueueItemAssignment.objects.create(queue_item=item, user=other)
        QueueItem.objects.filter(id=item.id).update(
            review_status="rejected", status=QueueItemStatus.IN_PROGRESS.value
        )
        QueueItemReviewThread.objects.create(
            queue_item=item,
            blocking=True,
            status=QueueItemReviewThread.STATUS_OPEN,
            action=QueueItemReviewThread.ACTION_REQUEST_CHANGES,
            target_annotator=target,  # rework routed to `target`, not `other`
            organization=organization,
            workspace=workspace,
            created_by=user,
        )

        client = _client_for(other, workspace)
        resp = client.post(_skip_url(queue_id, item.id), {}, format="json")
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert "different annotator" in str(resp.data).lower()
        client.stop_workspace_injection()


@pytest.mark.django_db
class TestNextItemBefore:
    def test_before_nonexistent_returns_null(self, auth_client, organization, workspace):
        queue_id = _create_queue(auth_client, name="Before Q")
        _, row = _create_dataset_row(organization, workspace)
        _add_item(auth_client, queue_id, row)

        resp = auth_client.get(
            _next_item_query_url(queue_id), {"before": str(uuid.uuid4())}
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert _result(resp)["item"] is None


@pytest.mark.django_db
class TestReleaseNoReservation:
    def test_release_without_active_reservation(self, auth_client, organization, workspace):
        queue_id = _create_queue(auth_client, name="Release Q")
        _, row = _create_dataset_row(organization, workspace)
        item = _add_item(auth_client, queue_id, row)

        resp = auth_client.post(_release_url(queue_id, item.id), {}, format="json")
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert _result(resp)["released"] is True


@pytest.mark.django_db
class TestDiscussionSearch:
    def test_search_filters_comments(self, auth_client, organization, workspace):
        queue_id = _create_queue(auth_client, name="Discuss Search Q")
        _, row = _create_dataset_row(organization, workspace)
        item = _add_item(auth_client, queue_id, row)

        created = auth_client.post(
            _discussion_url(queue_id, item.id),
            {"comment": "the apple looks off here"},
            format="json",
        )
        assert created.status_code == status.HTTP_200_OK, created.data

        hit = auth_client.get(_discussion_url(queue_id, item.id), {"search": "apple"})
        assert hit.status_code == status.HTTP_200_OK
        assert len(_result(hit)["review_comments"]) >= 1

        miss = auth_client.get(
            _discussion_url(queue_id, item.id), {"search": "zzz-no-match"}
        )
        assert miss.status_code == status.HTTP_200_OK
        assert _result(miss)["review_comments"] == []


@pytest.mark.django_db
class TestDiscussionInvalidUuid:
    def test_resolve_invalid_thread_uuid_returns_400(
        self, auth_client, organization, workspace
    ):
        queue_id = _create_queue(auth_client, name="Bad UUID Q")
        _, row = _create_dataset_row(organization, workspace)
        item = _add_item(auth_client, queue_id, row)

        resp = auth_client.post(
            _resolve_thread_url(queue_id, item.id, "not-a-uuid"), {}, format="json"
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid discussion thread" in str(resp.data)


@pytest.mark.django_db
class TestImportInvalidScoreSource:
    def test_invalid_score_source_is_skipped(self, auth_client, organization, workspace):
        queue_id = _create_queue(auth_client, name="Import Src Q")
        label = _create_label(organization, workspace, name="ImpSrc-Label")
        AnnotationQueueLabel.objects.get_or_create(
            queue_id=queue_id, label=label, defaults={"order": 5, "required": False}
        )
        _, row = _create_dataset_row(organization, workspace)
        item = _add_item(auth_client, queue_id, row)

        resp = auth_client.post(
            _import_url(queue_id, item.id),
            {
                "annotations": [
                    {
                        "label_id": str(label.id),
                        "value": "Positive",
                        "score_source": "totally_bogus_source",
                    }
                ]
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert _result(resp)["imported"] == 0
        assert not Score.objects.filter(queue_item=item, label=label).exists()


@pytest.mark.django_db
class TestExportCsvBlankRow:
    def test_item_without_annotations_emits_blank_row(
        self, auth_client, organization, workspace
    ):
        queue_id = _create_queue(auth_client, name="CSV Blank Q")
        _, row = _create_dataset_row(organization, workspace)
        item = _add_item(auth_client, queue_id, row)

        resp = auth_client.get(_export_url(queue_id), {"export_format": "csv"})
        assert resp.status_code == status.HTTP_200_OK
        body = resp.content.decode()
        lines = [ln for ln in body.splitlines() if ln.strip()]
        assert len(lines) == 2  # header + one blank-annotation row
        assert str(item.id) in body


@pytest.mark.django_db
class TestRestoreLiveQueue:
    def test_restore_non_archived_queue_returns_404(self, auth_client):
        queue_id = _create_queue(auth_client, name="Live Restore Q")
        resp = auth_client.post(_restore_url(queue_id), {}, format="json")
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert "not archived" in str(resp.data).lower()


@pytest.mark.django_db
class TestAutoAssignOnStrategySwitch:
    def test_switch_manual_to_round_robin_assigns_unassigned_items(
        self, auth_client, organization, workspace
    ):
        queue_id = _create_queue(auth_client, name="Strategy Q")
        a1 = _second_user(organization, workspace)
        a2 = _second_user(organization, workspace)
        _add_annotator(queue_id, a1)
        _add_annotator(queue_id, a2)
        _, row1 = _create_dataset_row(organization, workspace)
        _, row2 = _create_dataset_row(organization, workspace)
        item1 = _add_item(auth_client, queue_id, row1)
        item2 = _add_item(auth_client, queue_id, row2)
        assert item1.assigned_to_id is None and item2.assigned_to_id is None

        resp = auth_client.patch(
            f"{QUEUE_URL}{queue_id}/",
            {"assignment_strategy": "round_robin"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data

        item1.refresh_from_db()
        item2.refresh_from_db()
        assert item1.assigned_to_id is not None
        assert item2.assigned_to_id is not None


# ===========================================================================
# AUTH — anonymous access (one representative test per viewset)
# ===========================================================================


@pytest.mark.django_db
class TestAnonymousAccess:
    """Both viewsets enforce IsAuthenticated at the class level, so anonymous
    requests are rejected before any action runs. One check per viewset guards
    against a viewset silently losing its permission class (which would make
    every one of its actions public)."""

    def test_queue_viewset_rejects_anonymous(self):
        from rest_framework.test import APIClient

        resp = APIClient().get(QUEUE_URL)
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_automation_rule_viewset_rejects_anonymous(self):
        from rest_framework.test import APIClient

        resp = APIClient().get(f"{QUEUE_URL}{uuid.uuid4()}/automation-rules/")
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


# ===========================================================================
# Not-found paths — each endpoint's DoesNotExist branch
# ===========================================================================


@pytest.mark.django_db
class TestNotFoundPaths:
    def _queue_and_item(self, auth_client, organization, workspace):
        queue_id = _create_queue(auth_client, name="NotFound Q")
        _, row = _create_dataset_row(organization, workspace)
        item = _add_item(auth_client, queue_id, row)
        return queue_id, item

    def test_submit_unknown_queue_404(self, auth_client):
        resp = auth_client.post(
            f"{QUEUE_URL}{uuid.uuid4()}/items/{uuid.uuid4()}/annotations/submit/",
            {"annotations": [{"label_id": str(uuid.uuid4()), "value": "x"}]},
            format="json",
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert "Queue not found" in str(resp.data)

    def test_release_unknown_item_404(self, auth_client):
        queue_id = _create_queue(auth_client, name="NF Release Q")
        resp = auth_client.post(_release_url(queue_id, uuid.uuid4()), {}, format="json")
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert "Queue item not found" in str(resp.data)

    def test_annotations_list_unknown_item_404(self, auth_client):
        queue_id = _create_queue(auth_client, name="NF List Q")
        resp = auth_client.get(f"{QUEUE_URL}{queue_id}/items/{uuid.uuid4()}/annotations/")
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert "Queue item not found" in str(resp.data)

    def test_import_unknown_item_404(self, auth_client):
        queue_id = _create_queue(auth_client, name="NF Import Q")
        resp = auth_client.post(
            _import_url(queue_id, uuid.uuid4()),
            {"annotations": [{"label_id": str(uuid.uuid4()), "value": "x"}]},
            format="json",
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert "Queue item not found" in str(resp.data)

    def test_review_unknown_item_404(self, auth_client):
        queue_id = _create_queue(auth_client, name="NF Review Q")
        resp = auth_client.post(
            _review_url(queue_id, uuid.uuid4()),
            {"action": "comment", "notes": "x"},
            format="json",
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert "Queue item not found" in str(resp.data)

    def test_evaluate_unknown_rule_404(self, auth_client):
        queue_id = _create_queue(auth_client, name="NF Eval Q")
        resp = auth_client.post(
            f"{QUEUE_URL}{queue_id}/automation-rules/{uuid.uuid4()}/evaluate/",
            {},
            format="json",
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert "Rule not found" in str(resp.data)

    def test_preview_unknown_rule_404(self, auth_client):
        queue_id = _create_queue(auth_client, name="NF Preview Q")
        resp = auth_client.get(
            f"{QUEUE_URL}{queue_id}/automation-rules/{uuid.uuid4()}/preview/"
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert "Rule not found" in str(resp.data)

    def test_resolve_unknown_thread_404(self, auth_client, organization, workspace):
        queue_id, item = self._queue_and_item(auth_client, organization, workspace)
        resp = auth_client.post(
            _resolve_thread_url(queue_id, item.id, uuid.uuid4()), {}, format="json"
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert "Discussion thread not found" in str(resp.data)

    def test_discussion_comment_invalid_uuid_400(
        self, auth_client, organization, workspace
    ):
        queue_id, item = self._queue_and_item(auth_client, organization, workspace)
        resp = auth_client.delete(
            f"{QUEUE_URL}{queue_id}/items/{item.id}/discussion/comments/not-a-uuid/"
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid discussion comment" in str(resp.data)

    def test_discussion_comment_not_found_404(
        self, auth_client, organization, workspace
    ):
        queue_id, item = self._queue_and_item(auth_client, organization, workspace)
        resp = auth_client.delete(
            f"{QUEUE_URL}{queue_id}/items/{item.id}/discussion/comments/{uuid.uuid4()}/"
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert "Discussion comment not found" in str(resp.data)

    def test_reaction_invalid_uuid_400(self, auth_client, organization, workspace):
        queue_id, item = self._queue_and_item(auth_client, organization, workspace)
        resp = auth_client.post(
            f"{QUEUE_URL}{queue_id}/items/{item.id}/discussion/comments/not-a-uuid/reaction/",
            {"emoji": "👍"},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid discussion comment" in str(resp.data)

    def test_reaction_comment_not_found_404(self, auth_client, organization, workspace):
        queue_id, item = self._queue_and_item(auth_client, organization, workspace)
        resp = auth_client.post(
            f"{QUEUE_URL}{queue_id}/items/{item.id}/discussion/comments/{uuid.uuid4()}/reaction/",
            {"emoji": "👍"},
            format="json",
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert "Discussion comment not found" in str(resp.data)


# ===========================================================================
# Permission gates
# ===========================================================================


@pytest.mark.django_db
class TestItemPermissionGates:
    def test_item_create_by_non_manager_403(
        self, auth_client, organization, workspace
    ):
        queue_id = _create_queue(auth_client, name="Perm Create Q")
        annotator = _second_user(organization, workspace)
        _add_annotator(queue_id, annotator)  # annotator, not manager
        client = _client_for(annotator, workspace)
        resp = client.post(f"{QUEUE_URL}{queue_id}/items/", {}, format="json")
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        client.stop_workspace_injection()

    def test_item_destroy_by_non_manager_403(
        self, auth_client, organization, workspace
    ):
        queue_id = _create_queue(auth_client, name="Perm Destroy Q")
        _, row = _create_dataset_row(organization, workspace)
        item = _add_item(auth_client, queue_id, row)
        annotator = _second_user(organization, workspace)
        _add_annotator(queue_id, annotator)
        client = _client_for(annotator, workspace)
        resp = client.delete(f"{QUEUE_URL}{queue_id}/items/{item.id}/")
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        client.stop_workspace_injection()

    def test_discussion_by_non_member_403(self, auth_client, organization, workspace):
        queue_id = _create_queue(auth_client, name="Perm Discuss Q")
        _, row = _create_dataset_row(organization, workspace)
        item = _add_item(auth_client, queue_id, row)
        outsider = _second_user(organization, workspace)  # org member, not queue member
        client = _client_for(outsider, workspace)
        resp = client.get(_discussion_url(queue_id, item.id))
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert "queue members" in str(resp.data).lower()
        client.stop_workspace_injection()


# ===========================================================================
# State mutations / guards
# ===========================================================================


@pytest.mark.django_db
class TestUpdateAutoAssignResync:
    def test_enabling_auto_assign_on_manual_queue_assigns_items(
        self, auth_client, organization, workspace
    ):
        queue_id = _create_queue(auth_client, name="AutoAssign Resync Q")
        a1 = _second_user(organization, workspace)
        a2 = _second_user(organization, workspace)
        _add_annotator(queue_id, a1)
        _add_annotator(queue_id, a2)
        _, row1 = _create_dataset_row(organization, workspace)
        _, row2 = _create_dataset_row(organization, workspace)
        item1 = _add_item(auth_client, queue_id, row1)
        item2 = _add_item(auth_client, queue_id, row2)

        resp = auth_client.patch(
            f"{QUEUE_URL}{queue_id}/", {"auto_assign": True}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data

        # auto_assign + manual strategy fans every item out to all annotators.
        assert QueueItemAssignment.objects.filter(queue_item=item1).exists()
        assert QueueItemAssignment.objects.filter(queue_item=item2).exists()


@pytest.mark.django_db
class TestHardDeleteForceGuard:
    def test_force_false_is_rejected(self, auth_client):
        queue_id = _create_queue(auth_client, name="Force Guard Q")
        name = AnnotationQueue.objects.get(id=queue_id).name
        resp = auth_client.post(
            _hard_delete_url(queue_id),
            {"force": False, "confirm_name": name},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "requires force=true" in str(resp.data)


# ===========================================================================
# Discussion POST validation branches
# ===========================================================================


@pytest.mark.django_db
class TestDiscussionPostValidation:
    def _queue_item(self, auth_client, organization, workspace):
        queue_id = _create_queue(auth_client, name="Discuss Validate Q")
        _, row = _create_dataset_row(organization, workspace)
        return queue_id, _add_item(auth_client, queue_id, row)

    def test_label_not_in_queue_400(self, auth_client, organization, workspace):
        queue_id, item = self._queue_item(auth_client, organization, workspace)
        stray = _create_label(organization, workspace, name="Stray-Label")
        resp = auth_client.post(
            _discussion_url(queue_id, item.id),
            {"comment": "hi", "label_id": str(stray.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "not part of this queue" in str(resp.data)

    def test_target_not_member_400(self, auth_client, organization, workspace):
        queue_id, item = self._queue_item(auth_client, organization, workspace)
        resp = auth_client.post(
            _discussion_url(queue_id, item.id),
            {"comment": "hi", "target_annotator_id": str(uuid.uuid4())},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "must be a member of this queue" in str(resp.data)

    def test_thread_not_found_404(self, auth_client, organization, workspace):
        queue_id, item = self._queue_item(auth_client, organization, workspace)
        resp = auth_client.post(
            _discussion_url(queue_id, item.id),
            {"comment": "hi", "thread_id": str(uuid.uuid4())},
            format="json",
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert "Discussion thread not found" in str(resp.data)


# ===========================================================================
# Review label-comment validation branches
# ===========================================================================


@pytest.mark.django_db
class TestReviewLabelCommentValidation:
    def _queue_item(self, auth_client, organization, workspace, attach_label=False):
        queue_id = _create_queue(auth_client, name="Review Validate Q")
        _, row = _create_dataset_row(organization, workspace)
        item = _add_item(auth_client, queue_id, row)
        label = None
        if attach_label:
            label = _create_label(organization, workspace, name="RV-Label")
            AnnotationQueueLabel.objects.get_or_create(
                queue_id=queue_id, label=label, defaults={"order": 5, "required": False}
            )
        return queue_id, item, label

    def test_missing_label_id_400(self, auth_client, organization, workspace):
        queue_id, item, _ = self._queue_item(auth_client, organization, workspace)
        resp = auth_client.post(
            _review_url(queue_id, item.id),
            {"action": "comment", "label_comments": [{"comment": "x"}]},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "label_id is required" in str(resp.data)

    def test_label_not_in_queue_400(self, auth_client, organization, workspace):
        queue_id, item, _ = self._queue_item(auth_client, organization, workspace)
        stray = _create_label(organization, workspace, name="RV-Stray")
        resp = auth_client.post(
            _review_url(queue_id, item.id),
            {
                "action": "comment",
                "label_comments": [{"comment": "x", "label_id": str(stray.id)}],
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "not part of this queue" in str(resp.data)

    def test_target_not_member_400(self, auth_client, organization, workspace):
        queue_id, item, label = self._queue_item(
            auth_client, organization, workspace, attach_label=True
        )
        resp = auth_client.post(
            _review_url(queue_id, item.id),
            {
                "action": "comment",
                "label_comments": [
                    {
                        "comment": "x",
                        "label_id": str(label.id),
                        "target_annotator_id": str(uuid.uuid4()),
                    }
                ],
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "must be a member of this queue" in str(resp.data)

    def test_comment_action_requires_text_400(
        self, auth_client, organization, workspace
    ):
        queue_id, item, _ = self._queue_item(auth_client, organization, workspace)
        resp = auth_client.post(
            _review_url(queue_id, item.id), {"action": "comment"}, format="json"
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "Comment text is required" in str(resp.data)


# ===========================================================================
# Filter + blank/unknown skip branches
# ===========================================================================


@pytest.mark.django_db
class TestExportStatusFilter:
    def test_status_filter_limits_export(self, auth_client, organization, workspace):
        queue_id = _create_queue(auth_client, name="Export Filter Q")
        _, r1 = _create_dataset_row(organization, workspace)
        _, r2 = _create_dataset_row(organization, workspace)
        done = _add_item(auth_client, queue_id, r1)
        _add_item(auth_client, queue_id, r2)  # stays pending
        QueueItem.objects.filter(id=done.id).update(
            status=QueueItemStatus.COMPLETED.value
        )

        resp = auth_client.get(_export_url(queue_id), {"status": "completed"})
        assert resp.status_code == status.HTTP_200_OK, resp.data
        result = _result(resp)
        assert [row["item_id"] for row in result] == [str(done.id)]


@pytest.mark.django_db
class TestSubmitBlankValueSkip:
    def test_blank_value_is_skipped(self, auth_client, organization, workspace):
        queue_id = _create_queue(auth_client, name="Submit Blank Q")
        label = _create_label(organization, workspace, name="SB-Label")
        AnnotationQueueLabel.objects.get_or_create(
            queue_id=queue_id, label=label, defaults={"order": 5, "required": False}
        )
        _, row = _create_dataset_row(organization, workspace)
        item = _add_item(auth_client, queue_id, row)

        resp = auth_client.post(
            _submit_url(queue_id, item.id),
            {"annotations": [{"label_id": str(label.id), "value": ""}]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert _result(resp)["submitted"] == 0


@pytest.mark.django_db
class TestImportSkipBranches:
    def _queue_label_item(self, auth_client, organization, workspace):
        queue_id = _create_queue(auth_client, name="Import Skip Q")
        label = _create_label(organization, workspace, name="IS-Label")
        AnnotationQueueLabel.objects.get_or_create(
            queue_id=queue_id, label=label, defaults={"order": 5, "required": False}
        )
        _, row = _create_dataset_row(organization, workspace)
        return queue_id, label, _add_item(auth_client, queue_id, row)

    def test_blank_value_is_skipped(self, auth_client, organization, workspace):
        queue_id, label, item = self._queue_label_item(
            auth_client, organization, workspace
        )
        resp = auth_client.post(
            _import_url(queue_id, item.id),
            {"annotations": [{"label_id": str(label.id), "value": ""}]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert _result(resp)["imported"] == 0

    def test_unknown_label_is_skipped(self, auth_client, organization, workspace):
        queue_id, _, item = self._queue_label_item(
            auth_client, organization, workspace
        )
        resp = auth_client.post(
            _import_url(queue_id, item.id),
            {"annotations": [{"label_id": str(uuid.uuid4()), "value": "Positive"}]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert _result(resp)["imported"] == 0



def _second_org_client():
    """An authenticated client for a DIFFERENT organization (Org B)."""
    from accounts.models.organization import Organization
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from accounts.models.workspace import Workspace, WorkspaceMembership
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    org = Organization.objects.create(name=f"OrgB-{uuid.uuid4().hex[:6]}")
    member = User.objects.create_user(
        email=f"orgb-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Org B Owner",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=member,
        organization=org,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    ws = Workspace.objects.create(
        name="Org B WS",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=member,
    )
    org_mem = OrganizationMembership.no_workspace_objects.filter(
        user=member, organization=org
    ).first()
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=member,
        workspace=ws,
        defaults={
            "role": "Workspace Owner",
            "level": Level.OWNER,
            "is_active": True,
            "organization_membership": org_mem,
        },
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=member)
    client.set_workspace(ws)
    return client


@pytest.mark.django_db
class TestCrossTenantIsolation:
    def test_other_org_cannot_bulk_review(self, auth_client):
        queue_id = _create_queue(auth_client, name="XT Bulk Q")
        client = _second_org_client()
        resp = client.post(
            _bulk_review_url(queue_id),
            {"action": "approve", "item_ids": [str(uuid.uuid4())]},
            format="json",
        )
        assert resp.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        )
        client.stop_workspace_injection()

    def test_other_org_cannot_evaluate_rule(self, auth_client):
        queue_id = _create_queue(auth_client, name="XT Eval Q")
        client = _second_org_client()
        resp = client.post(
            f"{QUEUE_URL}{queue_id}/automation-rules/{uuid.uuid4()}/evaluate/",
            {},
            format="json",
        )
        assert resp.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        )
        client.stop_workspace_injection()

    def test_other_org_cannot_preview_rule(self, auth_client):
        queue_id = _create_queue(auth_client, name="XT Preview Q")
        client = _second_org_client()
        resp = client.get(
            f"{QUEUE_URL}{queue_id}/automation-rules/{uuid.uuid4()}/preview/"
        )
        assert resp.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        )
        client.stop_workspace_injection()
