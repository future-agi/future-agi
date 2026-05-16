"""
Phase 6 – End-to-End Tests for Unified Annotations.

Tests cover:
- 6A: Cross-source score visibility (scores at trace level visible via span query and vice versa)
- 6B: get_annotation_labels_for_project finding labels through Score records
- 6C: Queue creator visibility in for_source endpoint
- 6D: Score value format roundtrips through for-source API
- 6E: Deleted labels excluded from annotation column config
- 6F: Annotation columns in trace listing responses
- 6G: for-source endpoint with multi-source lookup
- 6H: Dual-write from observation_span annotation endpoint
- 6I: Auto-complete queue items
- 6J: Score serializer field verification
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

SCORE_URL = "/model-hub/scores/"
QUEUE_URL = "/model-hub/annotation-queues/"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def observe_project(db, organization, workspace):
    from model_hub.models.ai_model import AIModel
    from tracer.models.project import Project

    return Project.objects.create(
        name="Phase6 E2E Project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


@pytest.fixture
def project_version(db, observe_project):
    from tracer.models.project_version import ProjectVersion

    return ProjectVersion.objects.create(
        project=observe_project,
        name="v1",
        version="v1",
    )


@pytest.fixture
def trace(db, observe_project, project_version):
    from tracer.models.trace import Trace

    return Trace.objects.create(
        project=observe_project,
        project_version=project_version,
        name="E2E Trace",
        input={"prompt": "hello"},
        output={"response": "world"},
    )


@pytest.fixture
def observation_span(db, observe_project, trace):
    from tracer.models.observation_span import ObservationSpan

    span_id = f"e2e_span_{uuid.uuid4().hex[:12]}"
    return ObservationSpan.objects.create(
        id=span_id,
        project=observe_project,
        trace=trace,
        name="E2E Span",
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
def second_user(db, organization):
    from accounts.models.user import User
    from tfc.constants.roles import OrganizationRoles

    return User.objects.create_user(
        email="second@futureagi.com",
        password="testpassword123",
        name="Second User",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )


@pytest.fixture
def second_auth_client(second_user, workspace):
    from conftest import WorkspaceAwareAPIClient

    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=second_user)
    client.set_workspace(workspace)
    yield client
    client.stop_workspace_injection()


@pytest.fixture
def star_label(db, organization, workspace, observe_project):
    return AnnotationsLabels.objects.create(
        name="E2E Quality",
        type=AnnotationTypeChoices.STAR.value,
        settings={"no_of_stars": 5},
        organization=organization,
        workspace=workspace,
        project=observe_project,
    )


@pytest.fixture
def thumbs_label(db, organization, workspace, observe_project):
    return AnnotationsLabels.objects.create(
        name="E2E Thumbs",
        type=AnnotationTypeChoices.THUMBS_UP_DOWN.value,
        settings={},
        organization=organization,
        workspace=workspace,
        project=observe_project,
    )


@pytest.fixture
def categorical_label(db, organization, workspace, observe_project):
    return AnnotationsLabels.objects.create(
        name="E2E Category",
        type=AnnotationTypeChoices.CATEGORICAL.value,
        settings={
            "options": [{"label": "Good"}, {"label": "Bad"}, {"label": "Neutral"}],
            "multi_choice": True,
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
        name="E2E Notes",
        type=AnnotationTypeChoices.TEXT.value,
        settings={"placeholder": "Enter notes", "max_length": 1000, "min_length": 0},
        organization=organization,
        workspace=workspace,
        project=observe_project,
    )


@pytest.fixture
def numeric_label(db, organization, workspace, observe_project):
    return AnnotationsLabels.objects.create(
        name="E2E Accuracy",
        type=AnnotationTypeChoices.NUMERIC.value,
        settings={"min": 0, "max": 100, "step_size": 1, "display_type": "slider"},
        organization=organization,
        workspace=workspace,
        project=observe_project,
    )


@pytest.fixture
def queue_no_project_label(db, organization, workspace):
    """A label with project=NULL, as happens with queue-only labels."""
    return AnnotationsLabels.objects.create(
        name="Queue-Only Label",
        type=AnnotationTypeChoices.STAR.value,
        settings={"no_of_stars": 5},
        organization=organization,
        workspace=workspace,
        project=None,
    )


# ---------------------------------------------------------------------------
# 6A – Cross-source score visibility
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCrossSourceVisibility:
    """Scores created at one level should be visible when querying another."""

    def test_span_score_visible_via_for_source_span(
        self, auth_client, observation_span, star_label
    ):
        """Score on span is visible via for-source?source_type=observation_span."""
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
            f"{SCORE_URL}for-source/",
            {"source_type": "observation_span", "source_id": observation_span.id},
        )
        assert resp.status_code == status.HTTP_200_OK
        scores = resp.data["result"]
        assert len(scores) == 1
        assert scores[0]["value"] == {"rating": 4}

    def test_trace_score_visible_via_for_source_trace(
        self, auth_client, trace, thumbs_label
    ):
        """Score on trace is visible via for-source?source_type=trace."""
        auth_client.post(
            SCORE_URL,
            {
                "source_type": "trace",
                "source_id": str(trace.id),
                "label_id": str(thumbs_label.id),
                "value": {"value": "up"},
            },
            format="json",
        )

        resp = auth_client.get(
            f"{SCORE_URL}for-source/",
            {"source_type": "trace", "source_id": str(trace.id)},
        )
        assert resp.status_code == status.HTTP_200_OK
        scores = resp.data["result"]
        assert len(scores) == 1
        assert scores[0]["value"] == {"value": "up"}

    def test_scores_on_both_trace_and_span_separate_queries(
        self, auth_client, trace, observation_span, star_label, thumbs_label
    ):
        """Scores created at different levels returned separately by their source queries."""
        # Score on trace
        auth_client.post(
            SCORE_URL,
            {
                "source_type": "trace",
                "source_id": str(trace.id),
                "label_id": str(thumbs_label.id),
                "value": {"value": "down"},
            },
            format="json",
        )
        # Score on span
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

        # Query trace-level → should only get the trace score
        resp_trace = auth_client.get(
            f"{SCORE_URL}for-source/",
            {"source_type": "trace", "source_id": str(trace.id)},
        )
        assert resp_trace.status_code == status.HTTP_200_OK
        assert len(resp_trace.data["result"]) == 1
        assert resp_trace.data["result"][0]["label_name"] == "E2E Thumbs"

        # Query span-level → should only get the span score
        resp_span = auth_client.get(
            f"{SCORE_URL}for-source/",
            {"source_type": "observation_span", "source_id": observation_span.id},
        )
        assert resp_span.status_code == status.HTTP_200_OK
        assert len(resp_span.data["result"]) == 1
        assert resp_span.data["result"][0]["label_name"] == "E2E Quality"

    def test_for_source_returns_label_settings(
        self, auth_client, observation_span, categorical_label
    ):
        """for-source endpoint includes label_settings in response."""
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

        resp = auth_client.get(
            f"{SCORE_URL}for-source/",
            {"source_type": "observation_span", "source_id": observation_span.id},
        )
        assert resp.status_code == status.HTTP_200_OK
        score_data = resp.data["result"][0]
        assert "label_settings" in score_data
        assert score_data["label_settings"]["multi_choice"] is True


# ---------------------------------------------------------------------------
# 6B – get_annotation_labels_for_project
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAnnotationLabelsForProject:
    """Labels should be found through Score records even without direct project FK."""

    def test_label_with_project_fk_found(self, db, observe_project, star_label):
        """Labels with direct project FK are returned."""
        from tracer.utils.helper import get_annotation_labels_for_project

        labels = get_annotation_labels_for_project(observe_project.id)
        label_ids = set(labels.values_list("id", flat=True))
        assert star_label.id in label_ids

    def test_label_without_project_fk_found_via_score(
        self,
        db,
        user,
        organization,
        observe_project,
        observation_span,
        queue_no_project_label,
    ):
        """Labels with project=NULL found through Score records on a project's span."""
        from tracer.utils.helper import get_annotation_labels_for_project

        # Initially not found (no project FK, no scores)
        labels_before = get_annotation_labels_for_project(observe_project.id)
        label_ids_before = set(labels_before.values_list("id", flat=True))
        assert queue_no_project_label.id not in label_ids_before

        # Create a Score referencing this label on a span in the project
        Score.objects.create(
            source_type="observation_span",
            observation_span=observation_span,
            label=queue_no_project_label,
            value={"rating": 3},
            score_source="human",
            annotator=user,
            organization=organization,
        )

        # Now it should be found
        labels_after = get_annotation_labels_for_project(observe_project.id)
        label_ids_after = set(labels_after.values_list("id", flat=True))
        assert queue_no_project_label.id in label_ids_after

    def test_label_found_via_trace_score(
        self, db, user, organization, observe_project, trace, queue_no_project_label
    ):
        """Labels found via Score on trace (not span)."""
        from tracer.utils.helper import get_annotation_labels_for_project

        Score.objects.create(
            source_type="trace",
            trace=trace,
            label=queue_no_project_label,
            value={"value": "up"},
            score_source="human",
            annotator=user,
            organization=organization,
        )

        labels = get_annotation_labels_for_project(observe_project.id)
        label_ids = set(labels.values_list("id", flat=True))
        assert queue_no_project_label.id in label_ids

    def test_deleted_labels_excluded(
        self, db, observe_project, organization, workspace
    ):
        """Soft-deleted labels are not returned."""
        from tracer.utils.helper import get_annotation_labels_for_project

        deleted_label = AnnotationsLabels.objects.create(
            name="Deleted Label",
            type=AnnotationTypeChoices.STAR.value,
            settings={"no_of_stars": 5},
            organization=organization,
            workspace=workspace,
            project=observe_project,
            deleted=True,
            deleted_at=timezone.now(),
        )

        labels = get_annotation_labels_for_project(observe_project.id)
        label_ids = set(labels.values_list("id", flat=True))
        assert deleted_label.id not in label_ids

    def test_deleted_score_does_not_surface_label(
        self,
        db,
        user,
        organization,
        observe_project,
        observation_span,
        queue_no_project_label,
    ):
        """A deleted Score should not cause its label to appear in project labels."""
        from tracer.utils.helper import get_annotation_labels_for_project

        Score.objects.create(
            source_type="observation_span",
            observation_span=observation_span,
            label=queue_no_project_label,
            value={"rating": 2},
            score_source="human",
            annotator=user,
            organization=organization,
            deleted=True,
            deleted_at=timezone.now(),
        )

        labels = get_annotation_labels_for_project(observe_project.id)
        label_ids = set(labels.values_list("id", flat=True))
        assert queue_no_project_label.id not in label_ids


# ---------------------------------------------------------------------------
# 6C – Queue creator visibility in for_source
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestQueueCreatorVisibility:
    """Queue creators should see their queues in for_source even if not an annotator."""

    @pytest.fixture
    def creator_queue(
        self,
        db,
        user,
        organization,
        workspace,
        observe_project,
        star_label,
        observation_span,
    ):
        """Queue created by the test user (not default, user not annotator)."""
        queue = AnnotationQueue.objects.create(
            name="Creator Queue",
            organization=organization,
            workspace=workspace,
            project=observe_project,
            created_by=user,
            status=AnnotationQueueStatusChoices.ACTIVE.value,
            is_default=False,
        )
        AnnotationQueueLabel.objects.create(
            queue=queue, label=star_label, required=True
        )
        QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=observation_span,
            organization=organization,
            status=QueueItemStatus.PENDING.value,
        )
        return queue

    def test_creator_sees_own_queue(self, auth_client, observation_span, creator_queue):
        """User who created a queue sees it in for_source response."""
        resp = auth_client.get(
            f"{QUEUE_URL}for-source/",
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
            },
        )
        assert resp.status_code == status.HTTP_200_OK
        queue_ids = [entry["queue"]["id"] for entry in resp.data["result"]]
        assert str(creator_queue.id) in queue_ids

    def test_non_creator_non_annotator_does_not_see_queue(
        self, second_auth_client, observation_span, creator_queue
    ):
        """User who is NOT creator/annotator does NOT see the queue."""
        resp = second_auth_client.get(
            f"{QUEUE_URL}for-source/",
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
            },
        )
        assert resp.status_code == status.HTTP_200_OK
        queue_ids = [entry["queue"]["id"] for entry in resp.data["result"]]
        assert str(creator_queue.id) not in queue_ids

    def test_annotator_sees_queue(
        self, second_auth_client, second_user, observation_span, creator_queue
    ):
        """User added as annotator sees the queue."""
        AnnotationQueueAnnotator.objects.create(
            queue=creator_queue,
            user=second_user,
        )
        resp = second_auth_client.get(
            f"{QUEUE_URL}for-source/",
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
            },
        )
        assert resp.status_code == status.HTTP_200_OK
        queue_ids = [entry["queue"]["id"] for entry in resp.data["result"]]
        assert str(creator_queue.id) in queue_ids

    @pytest.fixture
    def default_queue(
        self,
        db,
        organization,
        workspace,
        observe_project,
        thumbs_label,
        observation_span,
    ):
        """Default queue visible to all org members."""
        queue = AnnotationQueue.objects.create(
            name="Default Queue",
            organization=organization,
            workspace=workspace,
            project=observe_project,
            is_default=True,
            status=AnnotationQueueStatusChoices.ACTIVE.value,
        )
        AnnotationQueueLabel.objects.create(
            queue=queue, label=thumbs_label, required=False
        )
        QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=observation_span,
            organization=organization,
            status=QueueItemStatus.PENDING.value,
        )
        return queue

    def test_default_queue_visible_to_all(
        self, second_auth_client, observation_span, default_queue
    ):
        """Default queues visible to any org member."""
        resp = second_auth_client.get(
            f"{QUEUE_URL}for-source/",
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
            },
        )
        assert resp.status_code == status.HTTP_200_OK
        queue_ids = [entry["queue"]["id"] for entry in resp.data["result"]]
        assert str(default_queue.id) in queue_ids


# ---------------------------------------------------------------------------
# 6D – Score value format roundtrips through for-source
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestScoreValueFormatRoundtrip:
    """All annotation type values survive create→for-source roundtrip."""

    def _create_and_fetch(self, auth_client, source_type, source_id, label, value):
        auth_client.post(
            SCORE_URL,
            {
                "source_type": source_type,
                "source_id": source_id,
                "label_id": str(label.id),
                "value": value,
            },
            format="json",
        )
        resp = auth_client.get(
            f"{SCORE_URL}for-source/",
            {"source_type": source_type, "source_id": source_id},
        )
        assert resp.status_code == status.HTTP_200_OK
        return resp.data["result"]

    def test_star_value_roundtrip(self, auth_client, observation_span, star_label):
        scores = self._create_and_fetch(
            auth_client,
            "observation_span",
            observation_span.id,
            star_label,
            {"rating": 3},
        )
        assert scores[0]["value"] == {"rating": 3}
        assert scores[0]["label_type"] == AnnotationTypeChoices.STAR.value

    def test_thumbs_value_roundtrip(self, auth_client, observation_span, thumbs_label):
        scores = self._create_and_fetch(
            auth_client,
            "observation_span",
            observation_span.id,
            thumbs_label,
            {"value": "down"},
        )
        assert scores[0]["value"] == {"value": "down"}

    def test_categorical_multi_value_roundtrip(
        self, auth_client, observation_span, categorical_label
    ):
        scores = self._create_and_fetch(
            auth_client,
            "observation_span",
            observation_span.id,
            categorical_label,
            {"selected": ["Good", "Neutral"]},
        )
        assert scores[0]["value"] == {"selected": ["Good", "Neutral"]}

    def test_text_value_roundtrip(self, auth_client, observation_span, text_label):
        scores = self._create_and_fetch(
            auth_client,
            "observation_span",
            observation_span.id,
            text_label,
            {"text": "Looks great!"},
        )
        assert scores[0]["value"] == {"text": "Looks great!"}

    def test_numeric_value_roundtrip(
        self, auth_client, observation_span, numeric_label
    ):
        scores = self._create_and_fetch(
            auth_client,
            "observation_span",
            observation_span.id,
            numeric_label,
            {"value": 72.5},
        )
        assert scores[0]["value"] == {"value": 72.5}

    def test_score_with_notes_roundtrip(
        self, auth_client, observation_span, star_label
    ):
        """Notes are preserved in for-source response."""
        auth_client.post(
            SCORE_URL,
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
                "label_id": str(star_label.id),
                "value": {"rating": 5},
                "notes": "Excellent response quality",
            },
            format="json",
        )
        resp = auth_client.get(
            f"{SCORE_URL}for-source/",
            {"source_type": "observation_span", "source_id": observation_span.id},
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["result"][0]["notes"] == "Excellent response quality"


# ---------------------------------------------------------------------------
# 6E – Deleted labels excluded from annotation columns
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeletedLabelsExcludedFromConfig:
    """Soft-deleted labels should not appear as annotation metric columns."""

    def test_deleted_label_not_in_trace_list_config(
        self,
        auth_client,
        observe_project,
        project_version,
        star_label,
        trace,
        observation_span,
    ):
        """Deleted labels don't appear in trace listing column config."""
        # Create a score so the label is associated with the project
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

        # Soft-delete the label
        star_label.deleted = True
        star_label.deleted_at = timezone.now()
        star_label.save(update_fields=["deleted", "deleted_at"])

        resp = auth_client.get(
            "/tracer/trace/list_traces/",
            {"project_version_id": str(project_version.id)},
        )
        assert resp.status_code == status.HTTP_200_OK
        config = resp.data["result"].get("column_config", [])
        annotation_cols = [
            c for c in config if c.get("group_by") == "Annotation Metrics"
        ]
        annotation_label_ids = [c["id"] for c in annotation_cols]
        assert str(star_label.id) not in annotation_label_ids


# ---------------------------------------------------------------------------
# 6F – Annotation columns in trace listing
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAnnotationColumnsInTraceListing:
    """Verify annotation metric columns appear correctly in trace list config."""

    def test_annotation_column_appears_after_scoring(
        self,
        auth_client,
        observe_project,
        project_version,
        star_label,
        trace,
        observation_span,
    ):
        """After scoring, the label appears as an annotation metric column."""
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

        resp = auth_client.get(
            "/tracer/trace/list_traces/",
            {"project_version_id": str(project_version.id)},
        )
        assert resp.status_code == status.HTTP_200_OK
        config = resp.data["result"].get("column_config", [])
        annotation_cols = [
            c for c in config if c.get("group_by") == "Annotation Metrics"
        ]
        assert len(annotation_cols) >= 1
        col = next(c for c in annotation_cols if c["id"] == str(star_label.id))
        assert col["annotation_label_type"] == AnnotationTypeChoices.STAR.value

    def test_multiple_labels_create_multiple_columns(
        self,
        auth_client,
        observe_project,
        project_version,
        star_label,
        thumbs_label,
        categorical_label,
        trace,
        observation_span,
    ):
        """Multiple scored labels create multiple annotation columns."""
        for label, value in [
            (star_label, {"rating": 3}),
            (thumbs_label, {"value": "up"}),
            (categorical_label, {"selected": ["Good"]}),
        ]:
            auth_client.post(
                SCORE_URL,
                {
                    "source_type": "observation_span",
                    "source_id": observation_span.id,
                    "label_id": str(label.id),
                    "value": value,
                },
                format="json",
            )

        resp = auth_client.get(
            "/tracer/trace/list_traces/",
            {"project_version_id": str(project_version.id)},
        )
        assert resp.status_code == status.HTTP_200_OK
        config = resp.data["result"].get("column_config", [])
        annotation_cols = [
            c for c in config if c.get("group_by") == "Annotation Metrics"
        ]
        assert len(annotation_cols) >= 3


# ---------------------------------------------------------------------------
# 6G – for-source endpoint with multi-source lookup
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestForSourceMultiSource:
    """The for-source endpoint supports multi-source JSON array parameter."""

    def test_multi_source_returns_queues_for_both(
        self,
        auth_client,
        user,
        organization,
        workspace,
        observe_project,
        trace,
        observation_span,
        star_label,
    ):
        """for-source with sources JSON array returns queues matching any source."""
        import json

        queue = AnnotationQueue.objects.create(
            name="Multi-Source Queue",
            organization=organization,
            workspace=workspace,
            project=observe_project,
            created_by=user,
            status=AnnotationQueueStatusChoices.ACTIVE.value,
            is_default=False,
        )
        AnnotationQueueLabel.objects.create(
            queue=queue, label=star_label, required=True
        )
        # Queue item on the trace
        QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.TRACE.value,
            trace=trace,
            organization=organization,
            status=QueueItemStatus.PENDING.value,
        )

        sources = json.dumps(
            [
                {"source_type": "trace", "source_id": str(trace.id)},
                {"source_type": "observation_span", "source_id": observation_span.id},
            ]
        )
        resp = auth_client.get(
            f"{QUEUE_URL}for-source/",
            {"sources": sources},
        )
        assert resp.status_code == status.HTTP_200_OK
        queue_ids = [entry["queue"]["id"] for entry in resp.data["result"]]
        assert str(queue.id) in queue_ids


# ---------------------------------------------------------------------------
# 6H – Dual-write: observation_span annotation creates Score
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDualWriteObservationSpan:
    """Scoring via observation_span annotation endpoint creates Score + TraceAnnotation."""

    def test_obs_span_annotation_creates_score_with_correct_type(
        self, auth_client, observation_span, star_label
    ):
        """Annotating via observation_span endpoint → Score with correct source_type."""
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

    def test_obs_span_annotation_visible_in_for_source(
        self, auth_client, observation_span, star_label
    ):
        """Score created via obs_span annotation is visible via for-source."""
        url = "/tracer/observation-span/add_annotations/"
        auth_client.post(
            url,
            {
                "observation_span_id": observation_span.id,
                "annotation_values": {str(star_label.id): 3},
            },
            format="json",
        )

        resp = auth_client.get(
            f"{SCORE_URL}for-source/",
            {"source_type": "observation_span", "source_id": observation_span.id},
        )
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data["result"]) == 1
        assert resp.data["result"][0]["value"]["rating"] == 3.0


# ---------------------------------------------------------------------------
# 6I – Auto-complete queue items via cross-source scoring
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAutoCompleteViaScoring:
    """Queue items auto-complete when all required labels are scored."""

    @pytest.fixture
    def queue_with_items(
        self,
        db,
        user,
        organization,
        workspace,
        observe_project,
        observation_span,
        star_label,
        thumbs_label,
    ):
        queue = AnnotationQueue.objects.create(
            name="AutoComplete Queue",
            organization=organization,
            workspace=workspace,
            project=observe_project,
            created_by=user,
            status=AnnotationQueueStatusChoices.ACTIVE.value,
        )
        AnnotationQueueLabel.objects.create(
            queue=queue, label=star_label, required=True
        )
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

    def test_partial_scoring_does_not_complete(
        self, auth_client, observation_span, star_label, queue_with_items
    ):
        """Scoring only some required labels keeps item pending."""
        queue, item = queue_with_items
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

    def test_all_required_labels_auto_completes(
        self, auth_client, observation_span, star_label, thumbs_label, queue_with_items
    ):
        """Scoring all required labels in this queue's context auto-completes
        the queue item. Per-queue uniqueness means we must attribute the
        score to *this* queue_item explicitly — otherwise the /scores/ POST
        lands in the source's default queue and our test queue stays
        pending."""
        from django.test import TestCase

        queue, item = queue_with_items
        with TestCase.captureOnCommitCallbacks(execute=True):
            auth_client.post(
                SCORE_URL,
                {
                    "source_type": "observation_span",
                    "source_id": observation_span.id,
                    "label_id": str(star_label.id),
                    "value": {"rating": 4},
                    "queue_item_id": str(item.id),
                },
                format="json",
            )
        with TestCase.captureOnCommitCallbacks(execute=True):
            auth_client.post(
                SCORE_URL,
                {
                    "source_type": "observation_span",
                    "source_id": observation_span.id,
                    "label_id": str(thumbs_label.id),
                    "value": {"value": "up"},
                    "queue_item_id": str(item.id),
                },
                format="json",
            )
        item.refresh_from_db()
        assert item.status == QueueItemStatus.COMPLETED.value


# ---------------------------------------------------------------------------
# 6J – Score serializer includes annotator info and label metadata
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestScoreSerializerFields:
    """for-source response includes annotator info and label metadata."""

    def test_annotator_name_and_email(self, auth_client, observation_span, star_label):
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
            f"{SCORE_URL}for-source/",
            {"source_type": "observation_span", "source_id": observation_span.id},
        )
        assert resp.status_code == status.HTTP_200_OK
        score = resp.data["result"][0]
        # resp.data uses snake_case keys (pre-rendered Python dict)
        assert score["annotator_name"] == "Test User"
        assert score["annotator_email"].endswith("@futureagi.com")
        assert score["label_name"] == "E2E Quality"
        assert score["label_id"] is not None

    def test_score_source_field(self, auth_client, observation_span, star_label):
        """score_source field defaults to 'human'."""
        auth_client.post(
            SCORE_URL,
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
                "label_id": str(star_label.id),
                "value": {"rating": 2},
            },
            format="json",
        )
        resp = auth_client.get(
            f"{SCORE_URL}for-source/",
            {"source_type": "observation_span", "source_id": observation_span.id},
        )
        assert resp.status_code == status.HTTP_200_OK
        score = resp.data["result"][0]
        assert score["score_source"] == "human"
