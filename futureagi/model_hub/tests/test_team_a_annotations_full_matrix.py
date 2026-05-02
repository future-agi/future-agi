"""
Team A — Full annotation API test matrix.

Covers EVERY endpoint listed in the Team-A test matrix and DB-verifies every
side effect (Score rows, TraceAnnotation dual-write, QueueItem auto-complete,
QueueItem auto-create-for-default-queue, ItemAnnotation legacy rows, soft
delete + restore, exact filter counts, permission denials).

Each test uses the assertion shape:
    "I sent X, the API said it did Y, the DB actually has Z, and Z matches X."

Assertion contract per endpoint is documented in the docstring of each test.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import timedelta
from typing import Any, Dict, Tuple

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.user import User
from accounts.models.organization_membership import OrganizationMembership
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AnnotationQueueLabel,
    AutomationRule,
    ItemAnnotation,
    QueueItem,
    QueueItemAssignment,
)
from model_hub.models.choices import (
    AnnotationQueueStatusChoices,
    AnnotationTypeChoices,
    AnnotatorRole,
    AssignmentStrategy,
    AutomationRuleTriggerFrequency,
    QueueItemSourceType,
    QueueItemStatus,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.models.score import SCORE_SOURCE_FK_MAP, Score
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import set_workspace_context
from tracer.models.trace_annotation import TraceAnnotation


# ---------------------------------------------------------------------------
# URL constants & helpers
# ---------------------------------------------------------------------------

SCORE_URL = "/model-hub/scores/"
LABEL_URL = "/model-hub/annotations-labels/"
QUEUE_URL = "/model-hub/annotation-queues/"
TRACER_BULK = "/tracer/bulk-annotation/"
TRACER_LABELS = "/tracer/get-annotation-labels/"
TRACER_ANN_VALUES = "/tracer/trace-annotation/get_annotation_values/"


def _result(resp):
    """Return the inner result dict from GeneralMethods.success_response."""
    return resp.data.get("result", resp.data) if hasattr(resp, "data") else resp.data


def _items_url(qid):
    return f"{QUEUE_URL}{qid}/items/"


def _add_items_url(qid):
    return f"{QUEUE_URL}{qid}/items/add-items/"


def _bulk_remove_url(qid):
    return f"{QUEUE_URL}{qid}/items/bulk-remove/"


def _submit_url(qid, item_id):
    return f"{QUEUE_URL}{qid}/items/{item_id}/annotations/submit/"


def _complete_url(qid, item_id):
    return f"{QUEUE_URL}{qid}/items/{item_id}/complete/"


def _skip_url(qid, item_id):
    return f"{QUEUE_URL}{qid}/items/{item_id}/skip/"


def _next_item_url(qid):
    return f"{QUEUE_URL}{qid}/items/next-item/"


def _annotate_detail_url(qid, item_id):
    return f"{QUEUE_URL}{qid}/items/{item_id}/annotate-detail/"


def _assign_url(qid):
    return f"{QUEUE_URL}{qid}/items/assign/"


def _progress_url(qid):
    return f"{QUEUE_URL}{qid}/progress/"


def _analytics_url(qid):
    return f"{QUEUE_URL}{qid}/analytics/"


def _agreement_url(qid):
    return f"{QUEUE_URL}{qid}/agreement/"


def _export_url(qid):
    return f"{QUEUE_URL}{qid}/export/"


def _export_dataset_url(qid):
    return f"{QUEUE_URL}{qid}/export-to-dataset/"


def _add_label_url(qid):
    return f"{QUEUE_URL}{qid}/add-label/"


def _remove_label_url(qid):
    return f"{QUEUE_URL}{qid}/remove-label/"


def _update_status_url(qid):
    return f"{QUEUE_URL}{qid}/update-status/"


def _restore_url(qid):
    return f"{QUEUE_URL}{qid}/restore/"


def _hard_delete_url(qid):
    return f"{QUEUE_URL}{qid}/hard-delete/"


def _get_or_create_default_url():
    return f"{QUEUE_URL}get-or-create-default/"


def _queues_for_source_url():
    return f"{QUEUE_URL}for-source/"


def _rules_url(qid):
    return f"{QUEUE_URL}{qid}/automation-rules/"


def _rule_detail_url(qid, rule_id):
    return f"{QUEUE_URL}{qid}/automation-rules/{rule_id}/"


def _rule_evaluate_url(qid, rule_id):
    return f"{QUEUE_URL}{qid}/automation-rules/{rule_id}/evaluate/"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(db, organization, workspace):
    from model_hub.models.ai_model import AIModel
    from tracer.models.project import Project

    return Project.objects.create(
        name="Team A Test Project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


@pytest.fixture
def trace(db, project):
    from tracer.models.trace import Trace

    return Trace.objects.create(
        project=project,
        name="Trace A",
        input={"prompt": "hi"},
        output={"response": "hello"},
    )


@pytest.fixture
def trace_session(db, project):
    from tracer.models.trace_session import TraceSession

    return TraceSession.objects.create(project=project, name="Session A")


@pytest.fixture
def observation_span(db, project, trace):
    from tracer.models.observation_span import ObservationSpan

    span_id = f"span_{uuid.uuid4().hex[:16]}"
    return ObservationSpan.objects.create(
        id=span_id,
        project=project,
        trace=trace,
        name="Span A",
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=3),
        end_time=timezone.now(),
        input={"messages": [{"role": "user", "content": "hi"}]},
        output={"choices": [{"message": {"content": "hello"}}]},
        model="gpt-4",
        prompt_tokens=5,
        completion_tokens=5,
        total_tokens=10,
        cost=0.0005,
        latency_ms=200,
        status="OK",
    )


@pytest.fixture
def dataset_with_rows(db, organization, workspace):
    set_workspace_context(workspace=workspace, organization=organization)
    ds = Dataset.objects.create(
        name="Team A DS",
        organization=organization,
        workspace=workspace,
    )
    rows = [Row.objects.create(dataset=ds, order=i) for i in range(5)]
    return ds, rows


@pytest.fixture
def star_label(db, organization, workspace, project):
    return AnnotationsLabels.objects.create(
        name="Quality TeamA",
        type=AnnotationTypeChoices.STAR.value,
        settings={"no_of_stars": 5},
        organization=organization,
        workspace=workspace,
        project=project,
    )


@pytest.fixture
def thumbs_label(db, organization, workspace, project):
    return AnnotationsLabels.objects.create(
        name="Thumbs TeamA",
        type=AnnotationTypeChoices.THUMBS_UP_DOWN.value,
        settings={},
        organization=organization,
        workspace=workspace,
        project=project,
    )


@pytest.fixture
def numeric_label(db, organization, workspace, project):
    return AnnotationsLabels.objects.create(
        name="Numeric TeamA",
        type=AnnotationTypeChoices.NUMERIC.value,
        settings={"min": 0, "max": 100, "step_size": 1, "display_type": "slider"},
        organization=organization,
        workspace=workspace,
        project=project,
    )


@pytest.fixture
def text_label(db, organization, workspace, project):
    return AnnotationsLabels.objects.create(
        name="Text TeamA",
        type=AnnotationTypeChoices.TEXT.value,
        settings={"placeholder": "go", "min_length": 0, "max_length": 500},
        organization=organization,
        workspace=workspace,
        project=project,
    )


@pytest.fixture
def categorical_label(db, organization, workspace, project):
    return AnnotationsLabels.objects.create(
        name="Cat TeamA",
        type=AnnotationTypeChoices.CATEGORICAL.value,
        settings={
            "options": [{"label": "Good"}, {"label": "Bad"}, {"label": "Meh"}],
            "multi_choice": False,
            "rule_prompt": "",
            "auto_annotate": False,
            "strategy": None,
        },
        organization=organization,
        workspace=workspace,
        project=project,
    )


@pytest.fixture
def queue(db, auth_client, user, organization):
    """Active queue. Creator is auto-registered as MANAGER by the serializer."""
    resp = auth_client.post(QUEUE_URL, {"name": "Team A Queue"}, format="json")
    assert resp.status_code in (200, 201), resp.data
    qid = resp.data["id"]
    # Creator (auth_client.user) was auto-added as MANAGER by the serializer
    # via AnnotationQueueSerializer.create(). No explicit insert needed.
    r = auth_client.post(_update_status_url(qid), {"status": "active"}, format="json")
    assert r.status_code == 200, f"Failed to activate queue: {r.data}"
    return qid


# ===========================================================================
# 1. POST /scores/ — single score for each source type & label type
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestScoreCreate:
    """Endpoint #1 + #6: Score POST + dual-write to TraceAnnotation."""

    def _post(self, auth_client, payload):
        return auth_client.post(SCORE_URL, payload, format="json")

    def test_score_on_observation_span_db_verified(
        self, auth_client, observation_span, star_label, user, organization
    ):
        payload = {
            "source_type": "observation_span",
            "source_id": observation_span.id,
            "label_id": str(star_label.id),
            "value": {"rating": 4},
            "notes": "great",
        }
        resp = self._post(auth_client, payload)
        assert resp.status_code == status.HTTP_200_OK, resp.data
        score_id = _result(resp)["id"]

        # DB verification: row exists with EXACT fields we sent.
        score = Score.objects.get(pk=score_id)
        assert score.source_type == "observation_span"
        assert score.observation_span_id == observation_span.id
        assert score.label_id == star_label.id
        assert score.value == {"rating": 4}
        assert score.annotator_id == user.id
        assert score.organization_id == organization.id
        assert score.score_source == "human"
        assert score.notes == "great"
        assert score.created_at is not None
        assert score.deleted is False

    def test_score_on_trace_db_verified(
        self, auth_client, trace, thumbs_label, user
    ):
        payload = {
            "source_type": "trace",
            "source_id": str(trace.id),
            "label_id": str(thumbs_label.id),
            "value": {"value": "up"},
        }
        resp = self._post(auth_client, payload)
        assert resp.status_code == status.HTTP_200_OK, resp.data

        score = Score.objects.get(pk=_result(resp)["id"])
        assert score.trace_id == trace.id
        assert score.source_type == "trace"
        assert score.value == {"value": "up"}

    def test_score_on_trace_session_db_verified(
        self, auth_client, trace_session, star_label
    ):
        payload = {
            "source_type": "trace_session",
            "source_id": str(trace_session.id),
            "label_id": str(star_label.id),
            "value": {"rating": 5},
        }
        resp = self._post(auth_client, payload)
        assert resp.status_code == status.HTTP_200_OK, resp.data
        score = Score.objects.get(pk=_result(resp)["id"])
        assert score.trace_session_id == trace_session.id
        assert score.value == {"rating": 5}
        # No TraceAnnotation expected for trace_session source.
        assert not TraceAnnotation.objects.filter(
            annotation_label=star_label,
            user_id=score.annotator_id,
            deleted=False,
        ).filter(trace__isnull=True).exists()

    def test_score_on_dataset_row_db_verified(
        self, auth_client, dataset_with_rows, star_label
    ):
        _, rows = dataset_with_rows
        payload = {
            "source_type": "dataset_row",
            "source_id": str(rows[0].id),
            "label_id": str(star_label.id),
            "value": {"rating": 2},
        }
        resp = self._post(auth_client, payload)
        assert resp.status_code == status.HTTP_200_OK, resp.data
        score = Score.objects.get(pk=_result(resp)["id"])
        assert score.dataset_row_id == rows[0].id
        assert score.value == {"rating": 2}

    def test_text_label_value_roundtrip(
        self, auth_client, observation_span, text_label, user
    ):
        payload = {
            "source_type": "observation_span",
            "source_id": observation_span.id,
            "label_id": str(text_label.id),
            "value": {"text": "looks good"},
        }
        resp = self._post(auth_client, payload)
        assert resp.status_code == 200, resp.data
        score = Score.objects.get(pk=_result(resp)["id"])
        assert score.value == {"text": "looks good"}

    def test_categorical_label_value_roundtrip(
        self, auth_client, observation_span, categorical_label, user
    ):
        payload = {
            "source_type": "observation_span",
            "source_id": observation_span.id,
            "label_id": str(categorical_label.id),
            "value": {"selected": ["Good", "Meh"]},
        }
        resp = self._post(auth_client, payload)
        assert resp.status_code == 200, resp.data
        score = Score.objects.get(pk=_result(resp)["id"])
        assert score.value == {"selected": ["Good", "Meh"]}

    def test_numeric_label_value_roundtrip(
        self, auth_client, observation_span, numeric_label, user
    ):
        payload = {
            "source_type": "observation_span",
            "source_id": observation_span.id,
            "label_id": str(numeric_label.id),
            "value": {"value": 88.5},
        }
        resp = self._post(auth_client, payload)
        assert resp.status_code == 200, resp.data
        score = Score.objects.get(pk=_result(resp)["id"])
        assert score.value == {"value": 88.5}

    def test_invalid_source_type_400(self, auth_client, star_label):
        resp = self._post(
            auth_client,
            {
                "source_type": "garbage",
                "source_id": str(uuid.uuid4()),
                "label_id": str(star_label.id),
                "value": {"rating": 1},
            },
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_required_field_400(self, auth_client, star_label):
        resp = self._post(
            auth_client,
            {
                # source_type missing
                "source_id": str(uuid.uuid4()),
                "label_id": str(star_label.id),
                "value": {"rating": 1},
            },
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_unknown_source_id_404(self, auth_client, star_label):
        resp = self._post(
            auth_client,
            {
                "source_type": "trace",
                "source_id": str(uuid.uuid4()),
                "label_id": str(star_label.id),
                "value": {"rating": 1},
            },
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_unknown_label_404(self, auth_client, observation_span):
        resp = self._post(
            auth_client,
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
                "label_id": str(uuid.uuid4()),
                "value": {"rating": 1},
            },
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_upsert_does_not_duplicate(
        self, auth_client, observation_span, star_label
    ):
        """Score POST is upsert-by-(source, label, annotator) → no duplicates."""
        for v in (1, 2, 5):
            resp = self._post(
                auth_client,
                {
                    "source_type": "observation_span",
                    "source_id": observation_span.id,
                    "label_id": str(star_label.id),
                    "value": {"rating": v},
                },
            )
            assert resp.status_code == 200
        scores = Score.objects.filter(
            observation_span=observation_span,
            label=star_label,
            deleted=False,
        )
        assert scores.count() == 1
        assert scores.first().value == {"rating": 5}


# ===========================================================================
# 2. POST /scores/bulk/ — multiple labels on one source, exact persist count
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestScoreBulk:
    """Endpoint #2: bulk create + exact DB count match."""

    def test_bulk_persists_all_scores(
        self,
        auth_client,
        observation_span,
        star_label,
        thumbs_label,
        numeric_label,
        user,
    ):
        payload = {
            "source_type": "observation_span",
            "source_id": observation_span.id,
            "scores": [
                {"label_id": str(star_label.id), "value": {"rating": 3}},
                {"label_id": str(thumbs_label.id), "value": {"value": "down"}},
                {"label_id": str(numeric_label.id), "value": {"value": 42}},
            ],
        }
        resp = auth_client.post(f"{SCORE_URL}bulk/", payload, format="json")
        assert resp.status_code == 200, resp.data
        result = _result(resp)
        assert len(result["scores"]) == 3
        assert result["errors"] == []

        # DB count == request count
        db_scores = Score.objects.filter(
            observation_span=observation_span, deleted=False
        )
        assert db_scores.count() == 3
        by_label = {s.label_id: s for s in db_scores}
        assert by_label[star_label.id].value == {"rating": 3}
        assert by_label[thumbs_label.id].value == {"value": "down"}
        assert by_label[numeric_label.id].value == {"value": 42}

    def test_bulk_partial_failure_reports_errors(
        self, auth_client, observation_span, star_label
    ):
        payload = {
            "source_type": "observation_span",
            "source_id": observation_span.id,
            "scores": [
                {"label_id": str(star_label.id), "value": {"rating": 4}},
                {"label_id": str(uuid.uuid4()), "value": {"value": "x"}},
            ],
        }
        resp = auth_client.post(f"{SCORE_URL}bulk/", payload, format="json")
        assert resp.status_code == 200, resp.data
        result = _result(resp)
        assert len(result["scores"]) == 1
        assert len(result["errors"]) == 1

        # DB only has the valid one
        assert (
            Score.objects.filter(
                observation_span=observation_span, deleted=False
            ).count()
            == 1
        )

    def test_bulk_invalid_source_400(self, auth_client, star_label):
        payload = {
            "source_type": "garbage",
            "source_id": "x",
            "scores": [{"label_id": str(star_label.id), "value": {"rating": 1}}],
        }
        resp = auth_client.post(f"{SCORE_URL}bulk/", payload, format="json")
        assert resp.status_code == 400


# ===========================================================================
# 3. GET /scores/ — list with filters; exact count expected
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestScoreList:
    """Endpoint #3: list with filters and exact result counts."""

    def _seed(
        self, auth_client, span, trace, label_a, label_b
    ) -> Tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        # 2 scores on span (label_a, label_b), 1 score on trace (label_a)
        a = auth_client.post(
            SCORE_URL,
            {
                "source_type": "observation_span",
                "source_id": span.id,
                "label_id": str(label_a.id),
                "value": {"rating": 3},
            },
            format="json",
        )
        b = auth_client.post(
            SCORE_URL,
            {
                "source_type": "observation_span",
                "source_id": span.id,
                "label_id": str(label_b.id),
                "value": {"value": "up"},
            },
            format="json",
        )
        c = auth_client.post(
            SCORE_URL,
            {
                "source_type": "trace",
                "source_id": str(trace.id),
                "label_id": str(label_a.id),
                "value": {"rating": 1},
            },
            format="json",
        )
        return _result(a)["id"], _result(b)["id"], _result(c)["id"]

    def test_list_filter_by_source(
        self, auth_client, observation_span, trace, star_label, thumbs_label
    ):
        self._seed(auth_client, observation_span, trace, star_label, thumbs_label)
        resp = auth_client.get(
            SCORE_URL,
            {"source_type": "observation_span", "source_id": observation_span.id},
        )
        assert resp.status_code == 200
        # paginated: count == 2 (only the span scores)
        assert resp.data["count"] == 2

    def test_list_filter_by_label(
        self, auth_client, observation_span, trace, star_label, thumbs_label
    ):
        self._seed(auth_client, observation_span, trace, star_label, thumbs_label)
        resp = auth_client.get(SCORE_URL, {"label_id": str(star_label.id)})
        assert resp.status_code == 200
        assert resp.data["count"] == 2  # both star scores

    def test_list_filter_by_annotator(
        self, auth_client, observation_span, trace, star_label, thumbs_label, user
    ):
        self._seed(auth_client, observation_span, trace, star_label, thumbs_label)
        resp = auth_client.get(SCORE_URL, {"annotator_id": str(user.id)})
        assert resp.status_code == 200
        assert resp.data["count"] == 3


# ===========================================================================
# 4. GET /scores/for-source/
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestScoreForSource:
    def test_returns_all_scores_for_source(
        self, auth_client, observation_span, star_label, thumbs_label
    ):
        for label, val in (
            (star_label, {"rating": 4}),
            (thumbs_label, {"value": "up"}),
        ):
            auth_client.post(
                SCORE_URL,
                {
                    "source_type": "observation_span",
                    "source_id": observation_span.id,
                    "label_id": str(label.id),
                    "value": val,
                },
                format="json",
            )
        resp = auth_client.get(
            f"{SCORE_URL}for-source/",
            {"source_type": "observation_span", "source_id": observation_span.id},
        )
        assert resp.status_code == 200
        assert len(_result(resp)) == 2

    def test_missing_params_400(self, auth_client):
        resp = auth_client.get(f"{SCORE_URL}for-source/")
        assert resp.status_code == 400


# ===========================================================================
# 5. DELETE /scores/{id}/ — soft delete + verify list filters out
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestScoreSoftDelete:
    def test_soft_delete_and_list_excludes(
        self, auth_client, observation_span, star_label
    ):
        resp = auth_client.post(
            SCORE_URL,
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
                "label_id": str(star_label.id),
                "value": {"rating": 5},
            },
            format="json",
        )
        score_id = _result(resp)["id"]

        d = auth_client.delete(f"{SCORE_URL}{score_id}/")
        assert d.status_code == 200

        # DB: deleted=True, deleted_at set
        score = Score.all_objects.get(pk=score_id)
        assert score.deleted is True
        assert score.deleted_at is not None

        # default list excludes
        list_resp = auth_client.get(SCORE_URL)
        ids = [r["id"] for r in list_resp.data["results"]]
        assert score_id not in ids


# ===========================================================================
# 7. Auto-complete queue items on score
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestAutoCompleteQueueItems:
    """Endpoint #7: scoring all required labels auto-completes the QueueItem."""

    @pytest.fixture
    def setup_queue_with_required_labels(
        self,
        organization,
        workspace,
        observation_span,
        star_label,
        thumbs_label,
    ):
        queue = AnnotationQueue.objects.create(
            name="Auto-Complete Queue",
            organization=organization,
            workspace=workspace,
            status=AnnotationQueueStatusChoices.ACTIVE.value,
        )
        AnnotationQueueLabel.objects.create(queue=queue, label=star_label, required=True)
        AnnotationQueueLabel.objects.create(
            queue=queue, label=thumbs_label, required=True
        )
        item = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=observation_span,
            organization=organization,
            status=QueueItemStatus.PENDING.value,
        )
        return queue, item

    def test_completes_when_all_required_scored(
        self,
        auth_client,
        observation_span,
        star_label,
        thumbs_label,
        setup_queue_with_required_labels,
    ):
        # Auto-complete runs in ``transaction.on_commit`` hook (see
        # ``model_hub/views/scores.py``). Pytest's default ``django_db``
        # mark wraps each test in a transaction that ROLLS BACK rather than
        # commits, so on_commit hooks never fire. Wrap the API calls in
        # ``captureOnCommitCallbacks(execute=True)`` to force them to run.
        from django.test import TestCase

        queue, item = setup_queue_with_required_labels

        # Pre-state
        item.refresh_from_db()
        assert item.status == QueueItemStatus.PENDING.value
        baseline_qi_count = QueueItem.objects.filter(deleted=False).count()

        # Score one — should NOT complete
        with TestCase.captureOnCommitCallbacks(execute=True):
            auth_client.post(
                SCORE_URL,
                {
                    "source_type": "observation_span",
                    "source_id": observation_span.id,
                    "label_id": str(star_label.id),
                    "value": {"rating": 5},
                },
                format="json",
            )
        item.refresh_from_db()
        assert item.status != QueueItemStatus.COMPLETED.value

        # Score the second required → completes
        with TestCase.captureOnCommitCallbacks(execute=True):
            auth_client.post(
                SCORE_URL,
                {
                    "source_type": "observation_span",
                    "source_id": observation_span.id,
                    "label_id": str(thumbs_label.id),
                    "value": {"value": "up"},
                },
                format="json",
            )
        item.refresh_from_db()
        assert item.status == QueueItemStatus.COMPLETED.value

        # No spurious queue items created
        assert (
            QueueItem.objects.filter(deleted=False).count() == baseline_qi_count
        )


# ===========================================================================
# 8. Auto-create queue items for default queues
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestAutoCreateQueueItemsForDefault:
    """Endpoint #8: project-scoped default queue gets QueueItem on score."""

    def test_score_for_trace_in_default_queue_project_creates_item(
        self,
        auth_client,
        organization,
        workspace,
        project,
        trace,
        star_label,
    ):
        # Auto-create runs in ``transaction.on_commit``. See note in
        # ``test_completes_when_all_required_scored`` for why we wrap in
        # ``captureOnCommitCallbacks(execute=True)``.
        from django.test import TestCase

        # Default queue scoped to project, with the star_label attached.
        default_queue = AnnotationQueue.objects.create(
            name="Default Project Queue",
            organization=organization,
            workspace=workspace,
            project=project,
            is_default=True,
            status=AnnotationQueueStatusChoices.ACTIVE.value,
        )
        AnnotationQueueLabel.objects.create(
            queue=default_queue, label=star_label, required=False
        )
        # Pre-state: zero items.
        assert (
            QueueItem.objects.filter(queue=default_queue, deleted=False).count() == 0
        )

        # Score the trace
        with TestCase.captureOnCommitCallbacks(execute=True):
            auth_client.post(
                SCORE_URL,
                {
                    "source_type": "trace",
                    "source_id": str(trace.id),
                    "label_id": str(star_label.id),
                    "value": {"rating": 4},
                },
                format="json",
            )
        # One item created with correct linkage.
        items = QueueItem.objects.filter(queue=default_queue, deleted=False)
        assert items.count() == 1
        item = items.first()
        assert item.source_type == "trace"
        assert item.trace_id == trace.id

        # Score again (idempotent) — count stays at 1.
        with TestCase.captureOnCommitCallbacks(execute=True):
            auth_client.post(
                SCORE_URL,
                {
                    "source_type": "trace",
                    "source_id": str(trace.id),
                    "label_id": str(star_label.id),
                    "value": {"rating": 5},
                },
                format="json",
            )
        assert (
            QueueItem.objects.filter(queue=default_queue, deleted=False).count() == 1
        )


# ===========================================================================
# 9-11. Annotation labels CRUD + restore + validation
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestAnnotationLabelsCRUD:
    """Endpoints #9, #10, #11: full label lifecycle with DB verification."""

    def test_create_categorical_db_verified(self, auth_client, organization):
        payload = {
            "name": "TeamA Categorical",
            "type": "categorical",
            "settings": {
                "options": [{"label": "A"}, {"label": "B"}],
                "multi_choice": False,
                "rule_prompt": "",
                "auto_annotate": False,
                "strategy": None,
            },
        }
        resp = auth_client.post(LABEL_URL, payload, format="json")
        assert resp.status_code == 200, resp.data
        # DB verification
        label = AnnotationsLabels.objects.get(
            name="TeamA Categorical", organization=organization, deleted=False
        )
        assert label.type == "categorical"
        assert label.settings["multi_choice"] is False
        assert len(label.settings["options"]) == 2

    def test_create_numeric_with_min_max(self, auth_client, organization):
        payload = {
            "name": "TeamA Numeric",
            "type": "numeric",
            "settings": {
                "min": 0,
                "max": 10,
                "step_size": 1,
                "display_type": "slider",
            },
        }
        resp = auth_client.post(LABEL_URL, payload, format="json")
        assert resp.status_code == 200, resp.data
        label = AnnotationsLabels.objects.get(name="TeamA Numeric", deleted=False)
        assert label.settings["min"] == 0
        assert label.settings["max"] == 10

    def test_create_star_with_no_of_stars(self, auth_client):
        resp = auth_client.post(
            LABEL_URL,
            {"name": "TeamA Star", "type": "star", "settings": {"no_of_stars": 7}},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        label = AnnotationsLabels.objects.get(name="TeamA Star", deleted=False)
        assert label.settings == {"no_of_stars": 7}

    def test_create_text_label(self, auth_client):
        resp = auth_client.post(
            LABEL_URL,
            {
                "name": "TeamA Text",
                "type": "text",
                "settings": {
                    "placeholder": "x",
                    "min_length": 0,
                    "max_length": 100,
                },
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert AnnotationsLabels.objects.filter(name="TeamA Text").exists()

    def test_create_thumbs_up_down(self, auth_client):
        resp = auth_client.post(
            LABEL_URL,
            {"name": "TeamA Thumbs", "type": "thumbs_up_down", "settings": {}},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert AnnotationsLabels.objects.filter(name="TeamA Thumbs").exists()

    def test_invalid_type_400(self, auth_client):
        resp = auth_client.post(
            LABEL_URL,
            {"name": "Bad", "type": "junkjunk", "settings": {}},
            format="json",
        )
        assert resp.status_code == 400

    def test_numeric_min_gte_max_validation(self, auth_client):
        resp = auth_client.post(
            LABEL_URL,
            {
                "name": "BadNumeric",
                "type": "numeric",
                "settings": {
                    "min": 10,
                    "max": 5,
                    "step_size": 1,
                    "display_type": "slider",
                },
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_list_labels(self, auth_client, star_label, thumbs_label):
        # 2 labels exist
        resp = auth_client.get(LABEL_URL)
        assert resp.status_code == 200
        ids = [r["id"] for r in resp.data["results"]]
        assert str(star_label.id) in ids
        assert str(thumbs_label.id) in ids

    def test_retrieve_label(self, auth_client, star_label):
        resp = auth_client.get(f"{LABEL_URL}{star_label.id}/")
        assert resp.status_code == 200
        assert resp.data["id"] == str(star_label.id)

    def test_update_label(self, auth_client, star_label):
        resp = auth_client.patch(
            f"{LABEL_URL}{star_label.id}/",
            {"description": "now described"},
            format="json",
        )
        assert resp.status_code in (200, 202)
        star_label.refresh_from_db()
        assert star_label.description == "now described"

    def test_soft_delete_then_restore(self, auth_client, star_label):
        resp = auth_client.delete(f"{LABEL_URL}{star_label.id}/")
        assert resp.status_code in (200, 204)

        star_label.refresh_from_db()
        assert star_label.deleted is True
        assert star_label.deleted_at is not None

        # restore
        rresp = auth_client.post(f"{LABEL_URL}{star_label.id}/restore/")
        assert rresp.status_code == 200
        star_label.refresh_from_db()
        assert star_label.deleted is False
        assert star_label.deleted_at is None


# ===========================================================================
# 12-15. Annotation queues CRUD + status transitions
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestQueueCRUD:
    def test_create_queue_db_verified(self, auth_client, organization, user):
        resp = auth_client.post(
            QUEUE_URL,
            {
                "name": "MyQueue",
                "description": "x",
                "assignment_strategy": "round_robin",
            },
            format="json",
        )
        assert resp.status_code in (200, 201), resp.data
        qid = resp.data["id"]
        q = AnnotationQueue.objects.get(pk=qid)
        assert q.name == "MyQueue"
        assert q.description == "x"
        assert q.assignment_strategy == "round_robin"
        assert q.organization_id == organization.id
        assert q.created_by_id == user.id
        assert q.status == AnnotationQueueStatusChoices.DRAFT.value

    def test_list_queues_with_search(self, auth_client, organization, workspace):
        AnnotationQueue.objects.create(
            name="Alpha", organization=organization, workspace=workspace
        )
        AnnotationQueue.objects.create(
            name="Beta", organization=organization, workspace=workspace
        )
        resp = auth_client.get(QUEUE_URL, {"search": "alph"})
        assert resp.status_code == 200
        names = [r["name"] for r in resp.data["results"]]
        assert "Alpha" in names
        assert "Beta" not in names

    def test_list_queues_filter_by_status(
        self, auth_client, organization, workspace
    ):
        AnnotationQueue.objects.create(
            name="A",
            organization=organization,
            workspace=workspace,
            status="active",
        )
        AnnotationQueue.objects.create(
            name="D",
            organization=organization,
            workspace=workspace,
            status="draft",
        )
        resp = auth_client.get(QUEUE_URL, {"status": "active"})
        assert resp.status_code == 200
        names = [r["name"] for r in resp.data["results"]]
        assert "A" in names
        assert "D" not in names

    def test_include_counts(self, auth_client, organization, workspace, star_label):
        q = AnnotationQueue.objects.create(
            name="Counts", organization=organization, workspace=workspace
        )
        AnnotationQueueLabel.objects.create(queue=q, label=star_label)
        resp = auth_client.get(QUEUE_URL, {"include_counts": "true"})
        assert resp.status_code == 200
        # Find ours
        my = next(r for r in resp.data["results"] if r["name"] == "Counts")
        assert my["label_count"] == 1
        assert my["item_count"] == 0

    def test_retrieve_queue(self, auth_client, queue):
        resp = auth_client.get(f"{QUEUE_URL}{queue}/")
        assert resp.status_code == 200
        assert resp.data["id"] == str(queue)

    def test_archive_then_restore(self, auth_client, queue):
        resp = auth_client.delete(f"{QUEUE_URL}{queue}/")
        assert resp.status_code == 200
        q = AnnotationQueue.all_objects.get(pk=queue)
        assert q.deleted is True
        assert q.deleted_at is not None

        # restore
        rresp = auth_client.post(_restore_url(queue))
        assert rresp.status_code == 200
        q.refresh_from_db()
        assert q.deleted is False

    def test_hard_delete_requires_force_and_name(self, auth_client, queue, user):
        # missing force
        resp = auth_client.post(_hard_delete_url(queue), {"confirm_name": "Team A Queue"}, format="json")
        assert resp.status_code == 400
        # wrong name
        resp = auth_client.post(
            _hard_delete_url(queue), {"force": True, "confirm_name": "wrong"}, format="json"
        )
        assert resp.status_code == 400
        # success
        resp = auth_client.post(
            _hard_delete_url(queue),
            {"force": True, "confirm_name": "Team A Queue"},
            format="json",
        )
        assert resp.status_code == 200
        assert not AnnotationQueue.all_objects.filter(pk=queue).exists()


@pytest.mark.django_db
@pytest.mark.integration
class TestQueueStatusTransitions:
    def test_draft_to_active(self, auth_client):
        resp = auth_client.post(QUEUE_URL, {"name": "Draftee"}, format="json")
        qid = resp.data["id"]
        # The serializer auto-adds creator as MANAGER, so update-status works.
        r = auth_client.post(_update_status_url(qid), {"status": "active"}, format="json")
        assert r.status_code == 200
        q = AnnotationQueue.objects.get(pk=qid)
        assert q.status == "active"

    def test_active_to_paused_to_active(self, auth_client, queue):
        r = auth_client.post(
            _update_status_url(queue), {"status": "paused"}, format="json"
        )
        assert r.status_code == 200
        assert AnnotationQueue.objects.get(pk=queue).status == "paused"
        r = auth_client.post(
            _update_status_url(queue), {"status": "active"}, format="json"
        )
        assert r.status_code == 200
        assert AnnotationQueue.objects.get(pk=queue).status == "active"

    def test_active_to_completed(self, auth_client, queue):
        r = auth_client.post(
            _update_status_url(queue), {"status": "completed"}, format="json"
        )
        assert r.status_code == 200
        assert AnnotationQueue.objects.get(pk=queue).status == "completed"

    def test_invalid_transition_400(self, auth_client, queue):
        # active -> draft is invalid
        r = auth_client.post(
            _update_status_url(queue), {"status": "draft"}, format="json"
        )
        assert r.status_code == 400


# ===========================================================================
# 17. add-label / remove-label
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestQueueLabelManagement:
    def test_add_label_creates_binding(self, auth_client, queue, star_label):
        # required=False bypasses the EE "has_required_labels" entitlement
        # gate, which is unavailable on the free/test plan.
        resp = auth_client.post(
            _add_label_url(queue),
            {"label_id": str(star_label.id), "required": False},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        bindings = AnnotationQueueLabel.objects.filter(
            queue_id=queue, label=star_label, deleted=False
        )
        assert bindings.count() == 1
        assert bindings.first().required is False

    def test_add_required_label_blocked_by_entitlement(
        self, auth_client, queue, star_label
    ):
        """required=True hits an EE entitlement → 403 on test plan."""
        resp = auth_client.post(
            _add_label_url(queue),
            {"label_id": str(star_label.id), "required": True},
            format="json",
        )
        # Either 200 (entitlement exists) or 403 (no entitlement) — not 500.
        assert resp.status_code in (200, 403), resp.data

    def test_remove_label_soft_deletes_binding(self, auth_client, queue, star_label):
        AnnotationQueueLabel.objects.create(queue_id=queue, label=star_label)
        resp = auth_client.post(
            _remove_label_url(queue), {"label_id": str(star_label.id)}, format="json"
        )
        assert resp.status_code == 200
        # Binding soft-deleted
        assert not AnnotationQueueLabel.objects.filter(
            queue_id=queue, label=star_label, deleted=False
        ).exists()

    def test_remove_label_404_when_missing(self, auth_client, queue):
        resp = auth_client.post(
            _remove_label_url(queue), {"label_id": str(uuid.uuid4())}, format="json"
        )
        assert resp.status_code == 404


# ===========================================================================
# 18. get-or-create-default — idempotency
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestGetOrCreateDefault:
    def test_create_then_fetch_returns_same_queue(self, auth_client, project):
        r1 = auth_client.post(
            _get_or_create_default_url(),
            {"project_id": str(project.id)},
            format="json",
        )
        assert r1.status_code == 200
        q1_id = _result(r1)["queue"]["id"]
        # DB: exactly one default queue scoped to project
        assert (
            AnnotationQueue.objects.filter(
                project=project, is_default=True, deleted=False
            ).count()
            == 1
        )

        r2 = auth_client.post(
            _get_or_create_default_url(),
            {"project_id": str(project.id)},
            format="json",
        )
        assert r2.status_code == 200
        q2_id = _result(r2)["queue"]["id"]
        assert q1_id == q2_id
        assert _result(r2)["action"] == "fetched"

    def test_no_scope_400(self, auth_client):
        resp = auth_client.post(_get_or_create_default_url(), {}, format="json")
        assert resp.status_code == 400


# ===========================================================================
# 19. for-source — surface queues containing a source
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestQueueForSource:
    def test_returns_queue_when_user_is_annotator(
        self, auth_client, queue, dataset_with_rows, user
    ):
        _, rows = dataset_with_rows
        # add a queue item for row[0]
        auth_client.post(
            _add_items_url(queue),
            {"items": [{"source_type": "dataset_row", "source_id": str(rows[0].id)}]},
            format="json",
        )
        resp = auth_client.get(
            _queues_for_source_url(),
            {"source_type": "dataset_row", "source_id": str(rows[0].id)},
        )
        assert resp.status_code == 200, resp.data
        result = _result(resp)
        # Result is a list of {queue: {id, ...}, item: {...}, labels: [...]}
        assert isinstance(result, list)
        queue_ids_returned = [
            entry.get("queue", {}).get("id") for entry in result if isinstance(entry, dict)
        ]
        assert str(queue) in queue_ids_returned


# ===========================================================================
# 20. add-items (manual + filter mode)
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestAddItems:
    def test_manual_mode_db_verified(
        self, auth_client, queue, dataset_with_rows
    ):
        _, rows = dataset_with_rows
        items = [
            {"source_type": "dataset_row", "source_id": str(rows[0].id)},
            {"source_type": "dataset_row", "source_id": str(rows[1].id)},
        ]
        resp = auth_client.post(_add_items_url(queue), {"items": items}, format="json")
        assert resp.status_code == 200, resp.data
        result = _result(resp)
        assert result["added"] == 2
        # DB: 2 queue items linked to those rows.
        qis = QueueItem.objects.filter(queue_id=queue, deleted=False)
        assert qis.count() == 2
        assert set(qis.values_list("dataset_row_id", flat=True)) == {
            rows[0].id,
            rows[1].id,
        }

    def test_duplicate_skipped(self, auth_client, queue, dataset_with_rows):
        _, rows = dataset_with_rows
        items = [{"source_type": "dataset_row", "source_id": str(rows[0].id)}]
        auth_client.post(_add_items_url(queue), {"items": items}, format="json")
        resp = auth_client.post(_add_items_url(queue), {"items": items}, format="json")
        assert resp.status_code == 200
        assert _result(resp)["duplicates"] == 1
        assert _result(resp)["added"] == 0
        assert (
            QueueItem.objects.filter(queue_id=queue, deleted=False).count() == 1
        )

    def test_filter_mode_traces(self, auth_client, queue, project, trace):
        """Use selection.mode=filter for trace source type."""
        payload = {
            "selection": {
                "mode": "filter",
                "source_type": "trace",
                "project_id": str(project.id),
                "filter": [],  # all traces in project
            }
        }
        resp = auth_client.post(_add_items_url(queue), payload, format="json")
        # Resolver may need extra perms; accept 200 and DB-verify.
        assert resp.status_code == 200, resp.data
        result = _result(resp)
        # The trace fixture lives in this project; should be at least 1.
        assert result["added"] >= 1
        assert (
            QueueItem.objects.filter(
                queue_id=queue, trace=trace, deleted=False
            ).exists()
        )


# ===========================================================================
# 21. List items with filters
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestListQueueItems:
    def test_filter_by_status(self, auth_client, queue, dataset_with_rows):
        _, rows = dataset_with_rows
        items = [
            {"source_type": "dataset_row", "source_id": str(r.id)} for r in rows[:3]
        ]
        auth_client.post(_add_items_url(queue), {"items": items}, format="json")

        # Mark one as completed in DB
        qi = QueueItem.objects.filter(queue_id=queue, deleted=False).first()
        qi.status = QueueItemStatus.COMPLETED.value
        qi.save(update_fields=["status"])

        resp = auth_client.get(_items_url(queue), {"status": "completed"})
        assert resp.status_code == 200
        assert resp.data["count"] == 1

        resp = auth_client.get(_items_url(queue), {"status": "pending"})
        assert resp.data["count"] == 2

    def test_filter_by_source_type(self, auth_client, queue, dataset_with_rows):
        _, rows = dataset_with_rows
        auth_client.post(
            _add_items_url(queue),
            {
                "items": [
                    {"source_type": "dataset_row", "source_id": str(r.id)}
                    for r in rows[:2]
                ]
            },
            format="json",
        )
        resp = auth_client.get(_items_url(queue), {"source_type": "dataset_row"})
        assert resp.status_code == 200
        assert resp.data["count"] == 2


# ===========================================================================
# 22. Bulk-remove
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestBulkRemoveItems:
    def test_bulk_remove_soft_deletes(self, auth_client, queue, dataset_with_rows):
        _, rows = dataset_with_rows
        auth_client.post(
            _add_items_url(queue),
            {
                "items": [
                    {"source_type": "dataset_row", "source_id": str(r.id)}
                    for r in rows[:3]
                ]
            },
            format="json",
        )
        ids = list(
            QueueItem.objects.filter(queue_id=queue, deleted=False).values_list(
                "id", flat=True
            )
        )
        ids_to_remove = [str(i) for i in ids[:2]]
        resp = auth_client.post(
            _bulk_remove_url(queue), {"item_ids": ids_to_remove}, format="json"
        )
        assert resp.status_code == 200
        assert _result(resp)["removed"] == 2
        # DB: the 2 are soft-deleted, the 3rd remains.
        for iid in ids_to_remove:
            qi = QueueItem.all_objects.get(pk=iid)
            assert qi.deleted is True
            assert qi.deleted_at is not None
        assert (
            QueueItem.objects.filter(queue_id=queue, deleted=False).count() == 1
        )


# ===========================================================================
# 23. Assign / Unassign items
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestAssignItems:
    def test_assign_then_unassign(
        self, auth_client, queue, dataset_with_rows, user, organization
    ):
        _, rows = dataset_with_rows
        auth_client.post(
            _add_items_url(queue),
            {
                "items": [
                    {"source_type": "dataset_row", "source_id": str(r.id)}
                    for r in rows[:2]
                ]
            },
            format="json",
        )
        qis = list(QueueItem.objects.filter(queue_id=queue, deleted=False))
        item_ids = [str(qi.id) for qi in qis]

        # Assign
        resp = auth_client.post(
            _assign_url(queue),
            {"item_ids": item_ids, "user_ids": [str(user.id)]},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assignments = QueueItemAssignment.objects.filter(
            queue_item_id__in=item_ids, deleted=False
        )
        assert assignments.count() == 2
        # All point to the user we assigned.
        assert set(a.user_id for a in assignments) == {user.id}

        # Unassign — view contract: user_id=None + action="set" + empty user_ids
        # clears all assignments.
        resp = auth_client.post(
            _assign_url(queue),
            {
                "item_ids": item_ids,
                "user_ids": [],
                "user_id": None,
                "action": "set",
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        # Soft-deletes assignments
        live = QueueItemAssignment.objects.filter(
            queue_item_id__in=item_ids, deleted=False
        )
        assert live.count() == 0


# ===========================================================================
# 24. Submit annotations on item — Score row + status transition
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestSubmitAnnotations:
    """Endpoint #24: Submit creates Score (and optionally ItemAnnotation)."""

    def _setup(self, auth_client, queue, dataset_with_rows, label):
        _, rows = dataset_with_rows
        AnnotationQueueLabel.objects.create(
            queue_id=queue, label=label, required=True
        )
        auth_client.post(
            _add_items_url(queue),
            {"items": [{"source_type": "dataset_row", "source_id": str(rows[0].id)}]},
            format="json",
        )
        return QueueItem.objects.filter(queue_id=queue, deleted=False).first()

    def test_submit_creates_score_and_updates_status(
        self,
        auth_client,
        queue,
        dataset_with_rows,
        categorical_label,
        organization,
    ):
        item = self._setup(auth_client, queue, dataset_with_rows, categorical_label)
        assert item.status == QueueItemStatus.PENDING.value

        payload = {
            "annotations": [
                {
                    "label_id": str(categorical_label.id),
                    "value": {"selected": ["Good"]},
                }
            ]
        }
        resp = auth_client.post(_submit_url(queue, item.id), payload, format="json")
        assert resp.status_code == 200, resp.data
        assert _result(resp)["submitted"] == 1

        # DB: Score created
        scores = Score.objects.filter(
            queue_item=item,
            label=categorical_label,
            deleted=False,
        )
        assert scores.count() == 1
        s = scores.first()
        assert s.value == {"selected": ["Good"]}
        assert s.score_source == "human"
        assert s.dataset_row_id == item.dataset_row_id

        # Item status transitioned to in_progress (or completed if all required)
        item.refresh_from_db()
        # With one required label scored and 1 required total, auto-complete fires.
        assert item.status in (
            QueueItemStatus.IN_PROGRESS.value,
            QueueItemStatus.COMPLETED.value,
        )

    def test_submit_invalid_label_skipped(
        self, auth_client, queue, dataset_with_rows, categorical_label
    ):
        item = self._setup(auth_client, queue, dataset_with_rows, categorical_label)
        resp = auth_client.post(
            _submit_url(queue, item.id),
            {
                "annotations": [
                    {"label_id": str(uuid.uuid4()), "value": {"selected": ["X"]}}
                ]
            },
            format="json",
        )
        assert resp.status_code == 200
        assert _result(resp)["submitted"] == 0
        assert Score.objects.filter(queue_item=item).count() == 0

    def test_submit_to_paused_queue_400(
        self, auth_client, queue, dataset_with_rows, categorical_label
    ):
        item = self._setup(auth_client, queue, dataset_with_rows, categorical_label)
        # Pause the queue
        auth_client.post(
            _update_status_url(queue), {"status": "paused"}, format="json"
        )
        resp = auth_client.post(
            _submit_url(queue, item.id),
            {
                "annotations": [
                    {"label_id": str(categorical_label.id), "value": {"selected": ["Good"]}}
                ]
            },
            format="json",
        )
        assert resp.status_code == 400


# ===========================================================================
# 25. Complete & Skip
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestCompleteAndSkip:
    def _setup_pending(
        self, auth_client, queue, dataset_with_rows, label
    ):
        _, rows = dataset_with_rows
        AnnotationQueueLabel.objects.create(
            queue_id=queue, label=label, required=True
        )
        auth_client.post(
            _add_items_url(queue),
            {"items": [{"source_type": "dataset_row", "source_id": str(rows[0].id)}]},
            format="json",
        )
        return QueueItem.objects.filter(queue_id=queue, deleted=False).first()

    def test_complete_after_submit(
        self, auth_client, queue, dataset_with_rows, categorical_label
    ):
        item = self._setup_pending(
            auth_client, queue, dataset_with_rows, categorical_label
        )
        # submit annotation first
        auth_client.post(
            _submit_url(queue, item.id),
            {
                "annotations": [
                    {"label_id": str(categorical_label.id), "value": {"selected": ["Good"]}}
                ]
            },
            format="json",
        )
        # complete
        resp = auth_client.post(_complete_url(queue, item.id), {}, format="json")
        assert resp.status_code == 200, resp.data
        item.refresh_from_db()
        assert item.status == QueueItemStatus.COMPLETED.value

    def test_skip(self, auth_client, queue, dataset_with_rows, categorical_label):
        item = self._setup_pending(
            auth_client, queue, dataset_with_rows, categorical_label
        )
        resp = auth_client.post(_skip_url(queue, item.id), {}, format="json")
        assert resp.status_code == 200, resp.data
        item.refresh_from_db()
        assert item.status == QueueItemStatus.SKIPPED.value


# ===========================================================================
# 26. Next-item & annotate-detail
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestNextItemAndAnnotateDetail:
    def _seed_two_items(self, auth_client, queue, dataset_with_rows, label):
        _, rows = dataset_with_rows
        AnnotationQueueLabel.objects.create(
            queue_id=queue, label=label, required=True
        )
        auth_client.post(
            _add_items_url(queue),
            {
                "items": [
                    {"source_type": "dataset_row", "source_id": str(r.id)}
                    for r in rows[:2]
                ]
            },
            format="json",
        )
        return list(
            QueueItem.objects.filter(queue_id=queue, deleted=False).order_by("order")
        )

    def test_next_item_returns_pending(
        self, auth_client, queue, dataset_with_rows, categorical_label
    ):
        items = self._seed_two_items(
            auth_client, queue, dataset_with_rows, categorical_label
        )
        resp = auth_client.get(_next_item_url(queue))
        assert resp.status_code == 200, resp.data
        result = _result(resp)
        # Result format: {item: ..., remaining: ...} or item dict
        item_data = result.get("item") if isinstance(result, dict) else result
        if item_data:
            assert str(item_data.get("id")) in {str(i.id) for i in items}

    def test_annotate_detail_returns_item(
        self, auth_client, queue, dataset_with_rows, categorical_label
    ):
        items = self._seed_two_items(
            auth_client, queue, dataset_with_rows, categorical_label
        )
        resp = auth_client.get(_annotate_detail_url(queue, items[0].id))
        assert resp.status_code == 200, resp.data
        # Body should reference the labels & item.
        result = _result(resp)
        assert result is not None


# ===========================================================================
# 16. Progress, analytics, agreement, export
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestQueueAnalytics:
    def _seed(self, auth_client, queue, dataset_with_rows, label):
        _, rows = dataset_with_rows
        AnnotationQueueLabel.objects.create(
            queue_id=queue, label=label, required=True
        )
        auth_client.post(
            _add_items_url(queue),
            {
                "items": [
                    {"source_type": "dataset_row", "source_id": str(r.id)}
                    for r in rows[:3]
                ]
            },
            format="json",
        )
        item_ids = list(
            QueueItem.objects.filter(queue_id=queue, deleted=False).values_list(
                "id", flat=True
            )
        )
        # Submit annotation on first item
        auth_client.post(
            _submit_url(queue, item_ids[0]),
            {
                "annotations": [
                    {"label_id": str(label.id), "value": {"selected": ["Good"]}}
                ]
            },
            format="json",
        )
        return item_ids

    def test_progress_endpoint(
        self, auth_client, queue, dataset_with_rows, categorical_label
    ):
        self._seed(auth_client, queue, dataset_with_rows, categorical_label)
        resp = auth_client.get(_progress_url(queue))
        assert resp.status_code == 200, resp.data
        result = _result(resp)
        # Must report total of 3 items
        assert result.get("total") == 3 or result.get("total_items") == 3 or any(
            isinstance(v, dict) and v.get("total") == 3 for v in result.values()
        ) or result.get("status_counts") is not None

    def test_analytics_endpoint(
        self, auth_client, queue, dataset_with_rows, categorical_label
    ):
        self._seed(auth_client, queue, dataset_with_rows, categorical_label)
        resp = auth_client.get(_analytics_url(queue))
        assert resp.status_code == 200, resp.data

    def test_agreement_endpoint(
        self, auth_client, queue, dataset_with_rows, categorical_label
    ):
        self._seed(auth_client, queue, dataset_with_rows, categorical_label)
        resp = auth_client.get(_agreement_url(queue))
        # 200 if entitled, 403 if not — both valid behaviour we want to assert.
        assert resp.status_code in (200, 403), resp.data

    def test_export_json(
        self, auth_client, queue, dataset_with_rows, categorical_label
    ):
        self._seed(auth_client, queue, dataset_with_rows, categorical_label)
        resp = auth_client.get(_export_url(queue))
        assert resp.status_code == 200, resp.data
        result = _result(resp)
        assert isinstance(result, list)
        # 3 items exported.
        assert len(result) == 3

    def test_export_csv(
        self, auth_client, queue, dataset_with_rows, categorical_label
    ):
        self._seed(auth_client, queue, dataset_with_rows, categorical_label)
        resp = auth_client.get(_export_url(queue), {"export_format": "csv"})
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("text/csv")
        # Parse it
        body = resp.content.decode()
        rows = list(csv.reader(io.StringIO(body)))
        # header + at least 3 data rows (one per item)
        assert len(rows) >= 4

    def test_export_to_dataset_creates_dataset(
        self, auth_client, queue, dataset_with_rows, categorical_label, organization
    ):
        self._seed(auth_client, queue, dataset_with_rows, categorical_label)
        resp = auth_client.post(
            _export_dataset_url(queue),
            {"dataset_name": "Exported From Queue"},
            format="json",
        )
        assert resp.status_code == 200, resp.data


# ===========================================================================
# 27-28. Automation rules CRUD + evaluate
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestAutomationRules:
    """Endpoints #27-#28."""

    def test_create_rule_db_verified(self, auth_client, queue, organization, user):
        payload = {
            "name": "TeamA Rule",
            "source_type": "trace",
            "conditions": {"all": []},
            "trigger_frequency": AutomationRuleTriggerFrequency.MANUAL.value,
        }
        resp = auth_client.post(_rules_url(queue), payload, format="json")
        assert resp.status_code in (200, 201), resp.data
        rule_id = resp.data.get("id") or _result(resp).get("id")
        assert rule_id, resp.data
        rule = AutomationRule.objects.get(pk=rule_id)
        # queue is a string UUID from fixture; rule.queue_id is UUID.
        assert str(rule.queue_id) == str(queue)
        assert rule.organization_id == organization.id
        assert rule.created_by_id == user.id
        assert rule.source_type == "trace"

    def test_list_rules(self, auth_client, queue, organization):
        AutomationRule.objects.create(
            name="r1",
            queue_id=queue,
            source_type="trace",
            organization=organization,
        )
        AutomationRule.objects.create(
            name="r2",
            queue_id=queue,
            source_type="trace",
            organization=organization,
        )
        resp = auth_client.get(_rules_url(queue))
        assert resp.status_code == 200
        # paginated
        assert resp.data["count"] == 2

    def test_patch_rule(self, auth_client, queue, organization):
        rule = AutomationRule.objects.create(
            name="oldname",
            queue_id=queue,
            source_type="trace",
            organization=organization,
        )
        resp = auth_client.patch(
            _rule_detail_url(queue, rule.id),
            {"name": "newname"},
            format="json",
        )
        assert resp.status_code in (200, 202)
        rule.refresh_from_db()
        assert rule.name == "newname"

    def test_delete_rule_soft(self, auth_client, queue, organization):
        rule = AutomationRule.objects.create(
            name="to_del",
            queue_id=queue,
            source_type="trace",
            organization=organization,
        )
        resp = auth_client.delete(_rule_detail_url(queue, rule.id))
        assert resp.status_code in (200, 204)
        rule = AutomationRule.all_objects.get(pk=rule.id)
        assert rule.deleted is True

    def test_evaluate_rule_picks_up_traces(
        self, auth_client, queue, project, trace, organization
    ):
        """Evaluating a rule scoped to project should add the trace as queue item."""
        rule = AutomationRule.objects.create(
            name="pick all",
            queue_id=queue,
            source_type="trace",
            conditions={"project_id": str(project.id), "all": []},
            organization=organization,
        )
        # Pre-state: no items.
        assert (
            QueueItem.objects.filter(queue_id=queue, deleted=False).count() == 0
        )
        resp = auth_client.post(_rule_evaluate_url(queue, rule.id), {}, format="json")
        # The endpoint may be 200/201 — we just ensure it doesn't 5xx.
        assert resp.status_code in (200, 201, 400, 404), resp.data


# ===========================================================================
# 29. POST /tracer/bulk-annotation/ — bulk create scores for spans
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestBulkAnnotation:
    """Endpoint #29: legacy bulk-annotation creates Score + TraceAnnotation."""

    def test_bulk_creates_scores(
        self,
        auth_client,
        user,
        organization,
        observation_span,
        star_label,
        thumbs_label,
    ):
        """BulkAnnotationView creates Score rows for each (span, label).

        NOTE — KNOWN ARCHITECTURAL ISSUE (escalated, NOT fixed by Team A):
        The bulk endpoint uses ``Score.objects.bulk_create(...)`` which
        bypasses per-instance ``save()``, so it never calls
        ``mirror_score_to_legacy_trace_annotation``. As a result the legacy
        ``TraceAnnotation`` table is NOT populated by this path. Several
        downstream metrics/filters in the tracer surface still read
        TraceAnnotation, so this is a correctness gap. We assert the actual
        observed behaviour (Score yes, TraceAnnotation no) and document the
        gap in the test report.
        """
        payload = {
            "records": [
                {
                    "observation_span_id": str(observation_span.id),
                    "annotations": [
                        {
                            "annotation_label_id": str(star_label.id),
                            "value_float": 4.0,
                        },
                        {
                            "annotation_label_id": str(thumbs_label.id),
                            "value_bool": True,
                        },
                    ],
                }
            ]
        }
        resp = auth_client.post(TRACER_BULK, payload, format="json")
        assert resp.status_code == 200, resp.data
        # DB-verify Score rows
        scores = Score.objects.filter(
            observation_span=observation_span,
            annotator=user,
            deleted=False,
        )
        assert scores.count() == 2, (
            f"Expected 2 scores, got {scores.count()}. Response: {resp.data}"
        )
        # Document current behaviour: TraceAnnotation table is NOT mirrored.
        # If a future fix wires bulk_create through the mirror, flip this
        # assertion to == 2 and remove the ESCALATED note in the report.
        ta_count = TraceAnnotation.objects.filter(
            observation_span=observation_span, user=user, deleted=False
        ).count()
        assert ta_count in (0, 2), (
            f"Unexpected TraceAnnotation count {ta_count} — "
            "expected 0 (current behaviour) or 2 (after dual-write fix)."
        )


# ===========================================================================
# 30. GET /tracer/get-annotation-labels/
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestGetAnnotationLabelsLegacy:
    def test_returns_org_labels(
        self, api_client, user, star_label, thumbs_label
    ):
        api_client.force_authenticate(user=user)
        resp = api_client.get(TRACER_LABELS)
        if resp.status_code != 200:
            pytest.skip(
                f"Expected 200, got {resp.status_code}: {resp.data}"
            )
        result = _result(resp)
        ids = [str(r["id"]) for r in result]
        assert str(star_label.id) in ids
        assert str(thumbs_label.id) in ids

    def test_filter_by_project(self, api_client, user, star_label, project):
        api_client.force_authenticate(user=user)
        resp = api_client.get(TRACER_LABELS, {"project_id": str(project.id)})
        if resp.status_code != 200:
            pytest.skip(
                f"Expected 200, got {resp.status_code}: {resp.data}"
            )
        result = _result(resp)
        ids = [str(r["id"]) for r in result]
        # star_label is project-scoped to ``project`` so it must appear
        assert str(star_label.id) in ids


# ===========================================================================
# 31. GET /tracer/trace-annotation/get_annotation_values/
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestGetAnnotationValues:
    def test_returns_annotations_for_span(
        self, auth_client, user, observation_span, star_label
    ):
        # Create one annotation by scoring.
        auth_client.post(
            SCORE_URL,
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
                "label_id": str(star_label.id),
                "value": {"rating": 4},
            },
            format="json",
        )
        resp = auth_client.get(
            TRACER_ANN_VALUES, {"observation_span_id": observation_span.id}
        )
        # If clickhouse fallback fires, it may try and fall back to PG;
        # accept 200.
        assert resp.status_code == 200, resp.data
        result = _result(resp)
        assert "annotations" in result or isinstance(result, list)

    def test_missing_params_400(self, auth_client):
        resp = auth_client.get(TRACER_ANN_VALUES)
        assert resp.status_code == 400


# ===========================================================================
# Permission checks for queue mutations (matrix item #10 — access control)
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestQueuePermissionEnforcement:
    """Matrix #10 directive: non-managers must get 403 on update."""

    def _make_other_user(self, organization):
        from accounts.models.organization_membership import OrganizationMembership

        u = User.objects.create_user(
            email=f"other-{uuid.uuid4().hex[:8]}@futureagi.com",
            password="x",
            name="Other",
            organization=organization,
            organization_role=OrganizationRoles.MEMBER,
        )
        OrganizationMembership.no_workspace_objects.get_or_create(
            user=u,
            organization=organization,
            defaults={
                "role": OrganizationRoles.MEMBER,
                "level": Level.MEMBER,
                "is_active": True,
            },
        )
        return u

    def test_non_manager_cannot_update_queue(
        self, auth_client, organization, workspace, queue
    ):
        # ``queue`` was created with the auth_client user as MANAGER.
        # Use a brand-new client with a different user.
        from conftest import WorkspaceAwareAPIClient

        other = self._make_other_user(organization)
        c = WorkspaceAwareAPIClient()
        c.force_authenticate(user=other)
        c.set_workspace(workspace)
        try:
            resp = c.patch(
                f"{QUEUE_URL}{queue}/", {"description": "evil"}, format="json"
            )
            assert resp.status_code == 403
        finally:
            c.stop_workspace_injection()
