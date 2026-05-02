"""
Unified Scores API Tests.

Tests cover:
- Score CRUD (create, read, list, delete)
- Bulk create scores
- For-source endpoint
- Observation span annotation endpoint → Score
- Auto-complete queue items when all required labels scored
- Backfill management command
- Annotation type value roundtrips
"""

import uuid
from unittest.mock import patch

import pytest
from django.conf import settings as django_settings
from rest_framework import status

from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueLabel,
    ItemAnnotation,
    QueueItem,
)
from model_hub.models.choices import (
    AnnotationQueueStatusChoices,
    AnnotationTypeChoices,
    QueueItemSourceType,
    QueueItemStatus,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from tracer.models.trace_annotation import TraceAnnotation

SCORE_URL = "/model-hub/scores/"
LABEL_URL = "/model-hub/annotations-labels/"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def observe_project(db, organization, workspace):
    from model_hub.models.ai_model import AIModel
    from tracer.models.project import Project

    return Project.objects.create(
        name="Score Test Project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


@pytest.fixture
def trace(db, observe_project):
    from tracer.models.trace import Trace

    return Trace.objects.create(
        project=observe_project,
        name="Test Trace",
        input={"prompt": "hello"},
        output={"response": "world"},
    )


@pytest.fixture
def trace_session(db, observe_project):
    from tracer.models.trace_session import TraceSession

    return TraceSession.objects.create(
        project=observe_project,
        name="Test Session",
    )


@pytest.fixture
def observation_span(db, observe_project, trace):
    from datetime import timedelta

    from django.utils import timezone

    from tracer.models.observation_span import ObservationSpan

    span_id = f"span_{uuid.uuid4().hex[:16]}"
    return ObservationSpan.objects.create(
        id=span_id,
        project=observe_project,
        trace=trace,
        name="Test Span",
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=5),
        end_time=timezone.now(),
        input={"messages": [{"role": "user", "content": "Hello"}]},
        output={"choices": [{"message": {"content": "Hi"}}]},
        model="gpt-4",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        cost=0.001,
        latency_ms=500,
        status="OK",
    )


@pytest.fixture
def star_label(db, organization, workspace, observe_project):
    return AnnotationsLabels.objects.create(
        name="Quality Rating",
        type=AnnotationTypeChoices.STAR.value,
        settings={"no_of_stars": 5},
        organization=organization,
        workspace=workspace,
        project=observe_project,
    )


@pytest.fixture
def thumbs_label(db, organization, workspace, observe_project):
    return AnnotationsLabels.objects.create(
        name="Thumbs Feedback",
        type=AnnotationTypeChoices.THUMBS_UP_DOWN.value,
        settings={},
        organization=organization,
        workspace=workspace,
        project=observe_project,
    )


@pytest.fixture
def categorical_label(db, organization, workspace, observe_project):
    return AnnotationsLabels.objects.create(
        name="Category",
        type=AnnotationTypeChoices.CATEGORICAL.value,
        settings={
            "options": [{"label": "Good"}, {"label": "Bad"}, {"label": "Neutral"}],
            "multi_choice": False,
            "rule_prompt": "",
            "auto_annotate": False,
            "strategy": None,
        },
        organization=organization,
        workspace=workspace,
        project=observe_project,
    )


@pytest.fixture
def text_label(db, organization, workspace, observe_project):
    return AnnotationsLabels.objects.create(
        name="Notes",
        type=AnnotationTypeChoices.TEXT.value,
        settings={"placeholder": "Enter notes", "max_length": 1000, "min_length": 0},
        organization=organization,
        workspace=workspace,
        project=observe_project,
    )


@pytest.fixture
def numeric_label(db, organization, workspace, observe_project):
    return AnnotationsLabels.objects.create(
        name="Accuracy",
        type=AnnotationTypeChoices.NUMERIC.value,
        settings={
            "min": 0,
            "max": 100,
            "step_size": 1,
            "display_type": "slider",
        },
        organization=organization,
        workspace=workspace,
        project=observe_project,
    )


# ---------------------------------------------------------------------------
# 1 – Score CRUD
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateScore:

    def test_create_score_on_observation_span(
        self, auth_client, observation_span, star_label
    ):
        """Create a score on an observation span."""
        payload = {
            "source_type": "observation_span",
            "source_id": observation_span.id,
            "label_id": str(star_label.id),
            "value": {"rating": 4},
        }
        resp = auth_client.post(SCORE_URL, payload, format="json")
        assert resp.status_code == status.HTTP_200_OK, f"Response: {resp.data}"
        result = resp.data["result"]
        assert result["source_type"] == "observation_span"
        assert result["value"] == {"rating": 4}

    def test_create_score_on_trace(self, auth_client, trace, thumbs_label):
        """Create a score on a trace."""
        payload = {
            "source_type": "trace",
            "source_id": str(trace.id),
            "label_id": str(thumbs_label.id),
            "value": {"value": "up"},
        }
        resp = auth_client.post(SCORE_URL, payload, format="json")
        assert resp.status_code == status.HTTP_200_OK

    def test_create_score_on_trace_session(
        self, auth_client, trace_session, star_label
    ):
        """Create a score on a trace session."""
        payload = {
            "source_type": "trace_session",
            "source_id": str(trace_session.id),
            "label_id": str(star_label.id),
            "value": {"rating": 5},
        }
        resp = auth_client.post(SCORE_URL, payload, format="json")
        assert resp.status_code == status.HTTP_200_OK

    def test_upsert_existing_score(self, auth_client, observation_span, star_label):
        """Creating a score with same source+label+annotator updates existing."""
        payload = {
            "source_type": "observation_span",
            "source_id": observation_span.id,
            "label_id": str(star_label.id),
            "value": {"rating": 3},
        }
        resp1 = auth_client.post(SCORE_URL, payload, format="json")
        assert resp1.status_code == status.HTTP_200_OK
        score_id = resp1.data["result"]["id"]

        payload["value"] = {"rating": 5}
        resp2 = auth_client.post(SCORE_URL, payload, format="json")
        assert resp2.status_code == status.HTTP_200_OK
        assert resp2.data["result"]["id"] == score_id
        assert resp2.data["result"]["value"] == {"rating": 5}

        # Only one Score exists
        assert Score.objects.filter(deleted=False).count() == 1

    def test_invalid_source_type(self, auth_client, star_label):
        """Invalid source type returns 400."""
        payload = {
            "source_type": "invalid_type",
            "source_id": str(uuid.uuid4()),
            "label_id": str(star_label.id),
            "value": {"rating": 3},
        }
        resp = auth_client.post(SCORE_URL, payload, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_source_not_found(self, auth_client, star_label):
        """Non-existent source returns 404."""
        payload = {
            "source_type": "trace",
            "source_id": str(uuid.uuid4()),
            "label_id": str(star_label.id),
            "value": {"rating": 3},
        }
        resp = auth_client.post(SCORE_URL, payload, format="json")
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_label_not_found(self, auth_client, observation_span):
        """Non-existent label returns 404."""
        payload = {
            "source_type": "observation_span",
            "source_id": observation_span.id,
            "label_id": str(uuid.uuid4()),
            "value": {"rating": 3},
        }
        resp = auth_client.post(SCORE_URL, payload, format="json")
        assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestBulkCreateScores:

    def test_bulk_create(self, auth_client, observation_span, star_label, thumbs_label):
        """Bulk create multiple scores on one source."""
        payload = {
            "source_type": "observation_span",
            "source_id": observation_span.id,
            "scores": [
                {"label_id": str(star_label.id), "value": {"rating": 4}},
                {"label_id": str(thumbs_label.id), "value": {"value": "up"}},
            ],
        }
        resp = auth_client.post(f"{SCORE_URL}bulk/", payload, format="json")
        assert resp.status_code == status.HTTP_200_OK
        result = resp.data["result"]
        assert len(result["scores"]) == 2
        assert len(result["errors"]) == 0

    def test_bulk_create_partial_failure(
        self, auth_client, observation_span, star_label
    ):
        """Bulk create with one invalid label continues for valid ones."""
        payload = {
            "source_type": "observation_span",
            "source_id": observation_span.id,
            "scores": [
                {"label_id": str(star_label.id), "value": {"rating": 4}},
                {"label_id": str(uuid.uuid4()), "value": {"value": "up"}},
            ],
        }
        resp = auth_client.post(f"{SCORE_URL}bulk/", payload, format="json")
        assert resp.status_code == status.HTTP_200_OK
        result = resp.data["result"]
        assert len(result["scores"]) == 1
        assert len(result["errors"]) == 1


@pytest.mark.django_db
class TestListScores:

    def test_list_scores_empty(self, auth_client):
        """Empty list returns 200."""
        resp = auth_client.get(SCORE_URL)
        assert resp.status_code == status.HTTP_200_OK

    def test_list_scores_filtered_by_source(
        self, auth_client, observation_span, star_label
    ):
        """Filter by source_type and source_id."""
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
            SCORE_URL,
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
            },
        )
        assert resp.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestForSourceEndpoint:

    def test_for_source(self, auth_client, observation_span, star_label, thumbs_label):
        """Get all scores for a specific source."""
        for label, val in [
            (star_label, {"rating": 4}),
            (thumbs_label, {"value": "down"}),
        ]:
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
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
            },
        )
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data["result"]) == 2

    def test_for_source_missing_params(self, auth_client):
        """Missing params returns 400."""
        resp = auth_client.get(f"{SCORE_URL}for-source/")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestDeleteScore:

    def test_soft_delete(self, auth_client, observation_span, star_label):
        """Soft-delete a score."""
        resp = auth_client.post(
            SCORE_URL,
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
                "label_id": str(star_label.id),
                "value": {"rating": 3},
            },
            format="json",
        )
        score_id = resp.data["result"]["id"]

        resp = auth_client.delete(f"{SCORE_URL}{score_id}/")
        assert resp.status_code == status.HTTP_200_OK

        # Score still exists but is soft-deleted
        assert Score.all_objects.filter(pk=score_id).exists()
        assert not Score.all_objects.filter(pk=score_id, deleted=False).exists()


# ---------------------------------------------------------------------------
# 2 – Observation span annotation endpoint → Score
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestObservationSpanAnnotationCreatesScore:

    def test_add_annotation_creates_score(
        self, auth_client, observation_span, star_label
    ):
        """Annotating via the observation_span endpoint creates a Score."""
        url = "/tracer/observation-span/add_annotations/"
        payload = {
            "observation_span_id": observation_span.id,
            "annotation_values": {
                str(star_label.id): 4,
            },
        }
        resp = auth_client.post(url, payload, format="json")
        assert resp.status_code == status.HTTP_200_OK

        score = Score.objects.filter(
            observation_span=observation_span,
            label=star_label,
            deleted=False,
        ).first()
        assert score is not None
        assert score.source_type == "observation_span"
        assert score.value.get("rating") == 4.0


# ---------------------------------------------------------------------------
# 3 – Auto-complete queue items
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAutoCompleteQueueItems:

    @pytest.fixture
    def queue_setup(
        self,
        db,
        organization,
        workspace,
        observe_project,
        observation_span,
        star_label,
        thumbs_label,
    ):
        """Create a queue with required labels and a queue item."""
        queue = AnnotationQueue.objects.create(
            name="Test Queue",
            organization=organization,
            workspace=workspace,
            status=AnnotationQueueStatusChoices.ACTIVE.value,
        )
        AnnotationQueueLabel.objects.create(
            queue=queue,
            label=star_label,
            required=True,
        )
        AnnotationQueueLabel.objects.create(
            queue=queue,
            label=thumbs_label,
            required=True,
        )
        item = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=observation_span,
            organization=organization,
            status=QueueItemStatus.PENDING.value,
        )
        return queue, item

    def test_auto_complete_when_all_required_scored(
        self,
        auth_client,
        observation_span,
        star_label,
        thumbs_label,
        queue_setup,
    ):
        """Queue item auto-completes when all required labels are scored.

        Auto-complete now runs in ``transaction.on_commit`` (so a side-effect
        failure can't poison the Score write transaction). Pytest's default
        ``django_db`` mark wraps the test in a transaction that rolls back,
        so on_commit hooks never fire. Wrap calls in
        ``captureOnCommitCallbacks(execute=True)`` to force them to run.
        """
        from django.test import TestCase

        queue, item = queue_setup

        # Score first label
        with TestCase.captureOnCommitCallbacks(execute=True):
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
        item.refresh_from_db()
        assert item.status != QueueItemStatus.COMPLETED.value

        # Score second required label → should auto-complete
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

    def test_no_auto_complete_partial_scoring(
        self,
        auth_client,
        observation_span,
        star_label,
        queue_setup,
    ):
        """Queue item stays pending when only some required labels scored."""
        queue, item = queue_setup

        auth_client.post(
            SCORE_URL,
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
                "label_id": str(star_label.id),
                "value": {"rating": 3},
            },
            format="json",
        )
        item.refresh_from_db()
        assert item.status != QueueItemStatus.COMPLETED.value


# ---------------------------------------------------------------------------
# 4 – Session scores in list endpoint
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSessionScores:

    @pytest.fixture
    def session_with_span(self, db, observe_project, trace_session):
        from datetime import timedelta

        from django.utils import timezone

        from tracer.models.observation_span import ObservationSpan
        from tracer.models.trace import Trace

        t = Trace.objects.create(
            project=observe_project,
            session=trace_session,
            name="Session Trace",
            input={"prompt": "hi"},
            output={"response": "hey"},
        )
        span_id = f"session_span_{uuid.uuid4().hex[:10]}"
        ObservationSpan.objects.create(
            id=span_id,
            project=observe_project,
            trace=t,
            name="Root Span",
            observation_type="llm",
            start_time=timezone.now() - timedelta(seconds=5),
            end_time=timezone.now(),
            input={"hello": "world"},
            output={"result": "ok"},
            latency_ms=500,
            status="OK",
            cost=0.001,
            total_tokens=15,
            prompt_tokens=10,
            completion_tokens=5,
        )
        return t

    def test_session_list_includes_score_columns(
        self,
        auth_client,
        observe_project,
        trace_session,
        star_label,
        session_with_span,
    ):
        """Session list API returns annotation metric columns in config."""
        # Create a score on the session
        auth_client.post(
            SCORE_URL,
            {
                "source_type": "trace_session",
                "source_id": str(trace_session.id),
                "label_id": str(star_label.id),
                "value": {"rating": 5},
            },
            format="json",
        )

        resp = auth_client.get(
            "/tracer/trace-session/list_sessions/",
            {"project_id": str(observe_project.id)},
        )
        assert resp.status_code == status.HTTP_200_OK
        result = resp.data["result"]
        config = result["config"]

        # Find annotation metric column
        annotation_cols = [
            c for c in config if c.get("group_by") == "Annotation Metrics"
        ]
        assert len(annotation_cols) >= 1
        assert annotation_cols[0]["id"] == str(star_label.id)
        assert (
            annotation_cols[0]["annotation_label_type"]
            == AnnotationTypeChoices.STAR.value
        )


# ---------------------------------------------------------------------------
# 5 – Backfill management command
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBackfillCommand:

    def test_backfill_trace_annotations(
        self,
        db,
        user,
        organization,
        workspace,
        observation_span,
        star_label,
        thumbs_label,
    ):
        """Backfill converts TraceAnnotation → Score."""
        from django.core.management import call_command

        # Create legacy TraceAnnotation records
        TraceAnnotation.objects.create(
            observation_span=observation_span,
            trace=observation_span.trace,
            annotation_label=star_label,
            annotation_value_float=4.0,
            user=user,
            updated_by=str(user.id),
        )
        TraceAnnotation.objects.create(
            observation_span=observation_span,
            trace=observation_span.trace,
            annotation_label=thumbs_label,
            annotation_value_bool=True,
            user=user,
            updated_by=str(user.id),
        )

        assert Score.objects.count() == 0

        call_command("backfill_scores", source="trace")

        scores = Score.objects.filter(deleted=False)
        assert scores.count() == 2

        star_score = scores.get(label=star_label)
        assert star_score.value == {"rating": 4.0}
        assert star_score.source_type == "observation_span"
        assert star_score.annotator == user

        thumbs_score = scores.get(label=thumbs_label)
        assert thumbs_score.value == {"value": "up"}

    def test_backfill_item_annotations(
        self,
        db,
        user,
        organization,
        workspace,
        observe_project,
        observation_span,
        star_label,
    ):
        """Backfill converts ItemAnnotation → Score."""
        from django.core.management import call_command

        queue = AnnotationQueue.objects.create(
            name="Backfill Queue",
            organization=organization,
            workspace=workspace,
            status=AnnotationQueueStatusChoices.ACTIVE.value,
        )
        qi = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=observation_span,
            organization=organization,
            status=QueueItemStatus.PENDING.value,
        )
        ItemAnnotation.objects.create(
            queue_item=qi,
            annotator=user,
            label=star_label,
            value={"rating": 3},
            score_source="human",
            organization=organization,
            workspace=workspace,
        )

        assert Score.objects.count() == 0

        call_command("backfill_scores", source="item")

        scores = Score.objects.filter(deleted=False)
        assert scores.count() == 1
        score = scores.first()
        assert score.value == {"rating": 3}
        assert score.queue_item == qi

    def test_backfill_dry_run(
        self, db, user, organization, workspace, observation_span, star_label
    ):
        """Dry run does not create any Score records."""
        from django.core.management import call_command

        TraceAnnotation.objects.create(
            observation_span=observation_span,
            trace=observation_span.trace,
            annotation_label=star_label,
            annotation_value_float=4.0,
            user=user,
            updated_by=str(user.id),
        )

        call_command("backfill_scores", source="trace", dry_run=True)
        assert Score.objects.count() == 0

    def test_backfill_idempotent(
        self, db, user, organization, workspace, observation_span, star_label
    ):
        """Running backfill twice doesn't duplicate scores."""
        from django.core.management import call_command

        TraceAnnotation.objects.create(
            observation_span=observation_span,
            trace=observation_span.trace,
            annotation_label=star_label,
            annotation_value_float=4.0,
            user=user,
            updated_by=str(user.id),
        )

        call_command("backfill_scores", source="trace")
        assert Score.objects.filter(deleted=False).count() == 1

        call_command("backfill_scores", source="trace")
        assert Score.objects.filter(deleted=False).count() == 1


# ---------------------------------------------------------------------------
# 6 – All annotation types value roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAnnotationTypeRoundtrip:
    """Verify all 5 annotation types create correct Score values."""

    def test_star_roundtrip(self, auth_client, observation_span, star_label):
        auth_client.post(
            SCORE_URL,
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
                "label_id": str(star_label.id),
                "value": {"rating": 3},
            },
            format="json",
        )
        score = Score.objects.get(label=star_label, deleted=False)
        assert score.value == {"rating": 3}

    def test_numeric_roundtrip(self, auth_client, observation_span, numeric_label):
        auth_client.post(
            SCORE_URL,
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
                "label_id": str(numeric_label.id),
                "value": {"value": 85.5},
            },
            format="json",
        )
        score = Score.objects.get(label=numeric_label, deleted=False)
        assert score.value == {"value": 85.5}

    def test_thumbs_up_roundtrip(self, auth_client, observation_span, thumbs_label):
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
        score = Score.objects.get(label=thumbs_label, deleted=False)
        assert score.value == {"value": "up"}

    def test_thumbs_down_roundtrip(self, auth_client, observation_span, thumbs_label):
        auth_client.post(
            SCORE_URL,
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
                "label_id": str(thumbs_label.id),
                "value": {"value": "down"},
            },
            format="json",
        )
        score = Score.objects.get(label=thumbs_label, deleted=False)
        assert score.value == {"value": "down"}

    def test_categorical_roundtrip(
        self, auth_client, observation_span, categorical_label
    ):
        auth_client.post(
            SCORE_URL,
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
                "label_id": str(categorical_label.id),
                "value": {"selected": ["Good", "Neutral"]},
            },
            format="json",
        )
        score = Score.objects.get(label=categorical_label, deleted=False)
        assert score.value == {"selected": ["Good", "Neutral"]}

    def test_text_roundtrip(self, auth_client, observation_span, text_label):
        auth_client.post(
            SCORE_URL,
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
                "label_id": str(text_label.id),
                "value": {"text": "This response is very helpful"},
            },
            format="json",
        )
        score = Score.objects.get(label=text_label, deleted=False)
        assert score.value == {"text": "This response is very helpful"}
