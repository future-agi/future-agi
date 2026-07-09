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

import pytest
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


def create_same_org_other_workspace_trace(user):
    from accounts.models.workspace import Workspace
    from model_hub.models.ai_model import AIModel
    from tracer.models.project import Project
    from tracer.models.trace import Trace

    hidden_workspace = Workspace.objects.create(
        name="Hidden Score Workspace",
        organization=user.organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    hidden_project = Project.objects.create(
        name="Hidden Score Project",
        organization=user.organization,
        workspace=hidden_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        user=user,
    )
    hidden_trace = Trace.objects.create(
        project=hidden_project,
        name="Hidden Score Trace",
        input={"prompt": "hidden"},
        output={"response": "hidden"},
    )
    return hidden_workspace, hidden_project, hidden_trace


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

    def test_create_rejects_legacy_label_alias(
        self, auth_client, observation_span, star_label
    ):
        payload = {
            "source_type": "observation_span",
            "source_id": observation_span.id,
            "labelId": str(star_label.id),
            "value": {"rating": 3},
        }
        resp = auth_client.post(SCORE_URL, payload, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "labelId" in str(resp.data)

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

    def test_create_rejects_same_org_other_workspace_trace(
        self, auth_client, user, star_label
    ):
        """Known trace ids from another same-org workspace cannot be scored."""
        _, hidden_project, hidden_trace = create_same_org_other_workspace_trace(user)

        resp = auth_client.post(
            SCORE_URL,
            {
                "source_type": "trace",
                "source_id": str(hidden_trace.id),
                "label_id": str(star_label.id),
                "value": {"rating": 4},
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_404_NOT_FOUND, resp.data
        assert not Score.no_workspace_objects.filter(
            trace=hidden_trace,
            deleted=False,
        ).exists()
        assert not AnnotationQueue.no_workspace_objects.filter(
            project=hidden_project,
            is_default=True,
            deleted=False,
        ).exists()


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

    def test_bulk_create_rejects_legacy_nested_label_alias(
        self, auth_client, observation_span, star_label
    ):
        payload = {
            "source_type": "observation_span",
            "source_id": observation_span.id,
            "scores": [
                {"labelId": str(star_label.id), "value": {"rating": 4}},
            ],
        }
        resp = auth_client.post(f"{SCORE_URL}bulk/", payload, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "labelId" in str(resp.data)

    def test_trace_bulk_create_can_save_notes_on_root_span(
        self, auth_client, trace, observation_span, thumbs_label, user
    ):
        """Call drawer labels save on trace while item notes stay on root span."""
        from tracer.models.span_notes import SpanNotes

        payload = {
            "source_type": "trace",
            "source_id": str(trace.id),
            "scores": [
                {"label_id": str(thumbs_label.id), "value": {"value": "up"}},
            ],
            "span_notes": "whole call note",
            "span_notes_source_id": observation_span.id,
        }
        resp = auth_client.post(f"{SCORE_URL}bulk/", payload, format="json")

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert Score.objects.filter(
            trace=trace,
            label=thumbs_label,
            annotator=user,
            deleted=False,
        ).exists()
        assert (
            SpanNotes.objects.get(
                span=observation_span,
                created_by_user=user,
            ).notes
            == "whole call note"
        )

        clear_resp = auth_client.post(
            f"{SCORE_URL}bulk/",
            {**payload, "span_notes": ""},
            format="json",
        )

        assert clear_resp.status_code == status.HTTP_200_OK, clear_resp.data
        assert not SpanNotes.objects.filter(
            span=observation_span,
            created_by_user=user,
        ).exists()

    def test_for_source_does_not_prefill_orphan_span_notes(
        self,
        auth_client,
        observe_project,
        trace,
        observation_span,
        thumbs_label,
        user,
        workspace,
    ):
        """A pre-existing SpanNote on the root span must NOT prefill a
        fresh queue's editable ``existing_notes`` box. The SpanNote
        belongs to a different (or no) queue context — bleeding it into
        every queue containing the same span is the leak the per-queue
        scoping work removes. The note is still surfaced as read-only
        context in the ``span_notes`` list."""
        import json

        from tracer.models.span_notes import SpanNotes

        queue = AnnotationQueue.objects.create(
            name="Trace call queue",
            status=AnnotationQueueStatusChoices.ACTIVE.value,
            project=observe_project,
            organization=user.organization,
            workspace=workspace,
            created_by=user,
        )
        AnnotationQueueLabel.objects.create(queue=queue, label=thumbs_label)
        QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.TRACE.value,
            trace=trace,
            organization=user.organization,
            workspace=workspace,
        )
        SpanNotes.objects.create(
            span=observation_span,
            notes="whole call note",
            created_by_user=user,
            created_by_annotator=user.email,
        )

        resp = auth_client.get(
            "/model-hub/annotation-queues/for-source/",
            {
                "sources": json.dumps(
                    [
                        {
                            "source_type": "trace",
                            "source_id": str(trace.id),
                            "span_notes_source_id": observation_span.id,
                        }
                    ]
                )
            },
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        result = resp.data["result"]
        assert len(result) == 1
        assert result[0]["item"]["source_id"] == str(trace.id)
        # Editable existing_notes is empty for this queue — the SpanNote
        # was not written via this queue's flow.
        assert result[0]["existing_notes"] == ""
        # The requester's own SpanNote is filtered out of the read-only
        # span_notes list to avoid the "you see your note but can't edit
        # it" UX trap — they'd only see this if a *different* user had
        # written a SpanNote on the same span.
        assert not any(
            note.get("notes") == "whole call note" for note in result[0]["span_notes"]
        )
        assert result[0]["span_notes_source_id"] == observation_span.id

    def test_queue_annotate_detail_prefills_and_saves_item_notes(
        self,
        auth_client,
        observe_project,
        trace,
        observation_span,
        thumbs_label,
        user,
        workspace,
    ):
        """Queue workspace whole-item notes use the trace root span."""
        from tracer.models.span_notes import SpanNotes
        from tracer.tests._ch_seed import seed_ch_span

        # The annotate-detail endpoint reads the root span from CH
        # to resolve span_notes_source_id for trace-source items.
        seed_ch_span(observation_span)

        queue = AnnotationQueue.objects.create(
            name="Trace workspace queue",
            status=AnnotationQueueStatusChoices.ACTIVE.value,
            project=observe_project,
            organization=user.organization,
            workspace=workspace,
            created_by=user,
        )
        AnnotationQueueLabel.objects.create(queue=queue, label=thumbs_label)
        item = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.TRACE.value,
            trace=trace,
            organization=user.organization,
            workspace=workspace,
        )
        SpanNotes.objects.create(
            span=observation_span,
            notes="existing whole item note",
            created_by_user=user,
            created_by_annotator=user.email,
        )

        detail_resp = auth_client.get(
            f"/model-hub/annotation-queues/{queue.id}/items/{item.id}/annotate-detail/",
        )

        assert detail_resp.status_code == status.HTTP_200_OK, detail_resp.data
        detail = detail_resp.data["result"]
        # Editable existing_notes is strictly per-queue: pre-existing
        # SpanNote does not prefill this queue's notes box anymore.
        # The requester's own SpanNote is also filtered from the read-only
        # span_notes list (would otherwise be a "see-but-can't-edit"
        # UX trap). It would appear there only if a *different* user
        # had created the SpanNote on the same span.
        assert detail["existing_notes"] == ""
        assert not any(
            note.get("notes") == "existing whole item note"
            for note in detail.get("span_notes", [])
        )
        assert detail["span_notes_source_id"] == observation_span.id

        submit_resp = auth_client.post(
            f"/model-hub/annotation-queues/{queue.id}/items/{item.id}/annotations/submit/",
            {
                "annotations": [
                    {
                        "label_id": str(thumbs_label.id),
                        "value": {"value": "up"},
                    }
                ],
                "item_notes": "updated whole item note",
            },
            format="json",
        )

        assert submit_resp.status_code == status.HTTP_200_OK, submit_resp.data
        assert (
            SpanNotes.objects.get(
                span=observation_span,
                created_by_user=user,
            ).notes
            == "updated whole item note"
        )

    def test_for_source_scores_include_queue_target(
        self,
        auth_client,
        observe_project,
        trace,
        thumbs_label,
        user,
        workspace,
    ):
        """Read-only annotation tables can deep-link back to the queue item."""
        queue = AnnotationQueue.objects.create(
            name="Trace linked score queue",
            status=AnnotationQueueStatusChoices.ACTIVE.value,
            project=observe_project,
            organization=user.organization,
            workspace=workspace,
            created_by=user,
        )
        AnnotationQueueLabel.objects.create(queue=queue, label=thumbs_label)
        item = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.TRACE.value,
            trace=trace,
            organization=user.organization,
            workspace=workspace,
        )

        submit_resp = auth_client.post(
            f"/model-hub/annotation-queues/{queue.id}/items/{item.id}/annotations/submit/",
            {
                "annotations": [
                    {
                        "label_id": str(thumbs_label.id),
                        "value": {"value": "up"},
                    }
                ]
            },
            format="json",
        )
        assert submit_resp.status_code == status.HTTP_200_OK, submit_resp.data

        resp = auth_client.get(
            f"{SCORE_URL}for-source/",
            {"source_type": "trace", "source_id": str(trace.id)},
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        result = resp.data["result"]
        assert len(result) == 1
        assert str(result[0]["queue_item"]) == str(item.id)
        assert result[0]["queue_id"] == str(queue.id)


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

    def test_list_scores_rejects_legacy_source_alias(
        self, auth_client, observation_span
    ):
        resp = auth_client.get(
            SCORE_URL,
            {
                "sourceType": "observation_span",
                "source_id": observation_span.id,
            },
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "sourceType" in str(resp.data)


@pytest.mark.django_db
class TestGeneratedScoreRoutes:
    def test_detail_put_patch_and_list_round_trip(
        self, auth_client, observation_span, star_label
    ):
        created_resp = auth_client.post(
            SCORE_URL,
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
                "label_id": str(star_label.id),
                "value": {"rating": 3},
                "notes": "initial note",
            },
            format="json",
        )
        assert created_resp.status_code == status.HTTP_200_OK, created_resp.data
        score_id = created_resp.data["result"]["id"]

        detail_resp = auth_client.get(f"{SCORE_URL}{score_id}/")
        assert detail_resp.status_code == status.HTTP_200_OK, detail_resp.data
        assert detail_resp.data["id"] == score_id
        assert detail_resp.data["source_type"] == "observation_span"

        list_resp = auth_client.get(
            SCORE_URL,
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
                "label_id": str(star_label.id),
            },
        )
        assert list_resp.status_code == status.HTTP_200_OK, list_resp.data
        assert any(row["id"] == score_id for row in list_resp.data["results"])

        put_resp = auth_client.put(
            f"{SCORE_URL}{score_id}/",
            {
                "value": {"rating": 4},
                "notes": "put note",
                "score_source": "human",
            },
            format="json",
        )
        assert put_resp.status_code == status.HTTP_200_OK, put_resp.data
        assert put_resp.data["value"] == {"rating": 4}
        assert put_resp.data["notes"] == "put note"
        assert put_resp.data["source_type"] == "observation_span"

        patch_resp = auth_client.patch(
            f"{SCORE_URL}{score_id}/",
            {"notes": "patched note"},
            format="json",
        )
        assert patch_resp.status_code == status.HTTP_200_OK, patch_resp.data
        assert patch_resp.data["value"] == {"rating": 4}
        assert patch_resp.data["notes"] == "patched note"

    def test_put_requires_value_and_patch_rejects_source_changes(
        self, auth_client, observation_span, star_label
    ):
        created_resp = auth_client.post(
            SCORE_URL,
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
                "label_id": str(star_label.id),
                "value": {"rating": 3},
            },
            format="json",
        )
        score_id = created_resp.data["result"]["id"]

        put_resp = auth_client.put(
            f"{SCORE_URL}{score_id}/",
            {"notes": "missing value"},
            format="json",
        )
        assert put_resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "value" in str(put_resp.data)

        patch_resp = auth_client.patch(
            f"{SCORE_URL}{score_id}/",
            {"source_type": "trace"},
            format="json",
        )
        assert patch_resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "source_type" in str(patch_resp.data)

    def test_same_org_other_workspace_score_detail_and_update_are_hidden(
        self, auth_client, user, star_label
    ):
        hidden_workspace, _, hidden_trace = create_same_org_other_workspace_trace(user)
        hidden_score = Score.no_workspace_objects.create(
            source_type="trace",
            trace=hidden_trace,
            label=star_label,
            value={"rating": 2},
            annotator=user,
            organization=user.organization,
            workspace=hidden_workspace,
        )

        detail_resp = auth_client.get(f"{SCORE_URL}{hidden_score.id}/")
        assert detail_resp.status_code == status.HTTP_404_NOT_FOUND

        patch_resp = auth_client.patch(
            f"{SCORE_URL}{hidden_score.id}/",
            {"notes": "blocked"},
            format="json",
        )
        assert patch_resp.status_code == status.HTTP_404_NOT_FOUND

        hidden_score.refresh_from_db()
        assert hidden_score.notes in (None, "")


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

    def test_for_source_rejects_legacy_source_alias(
        self, auth_client, observation_span
    ):
        resp = auth_client.get(
            f"{SCORE_URL}for-source/",
            {
                "sourceType": "observation_span",
                "sourceId": observation_span.id,
            },
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "sourceType" in str(resp.data)
        assert "sourceId" in str(resp.data)

    def test_span_scope_uses_lean_projection(
        self, mocker, auth_client, observe_project, observation_span, star_label
    ):
        """The span-note org-scope check must read only project_id/trace_id via
        ``scope_by_ids`` — reading the full span row (``reader.get``) OOMs the
        shared ClickHouse cluster on fat voice spans (code 241). Guard that the
        wide read is never used, and scores still return.
        """
        from unittest.mock import MagicMock

        from tracer.services.clickhouse.v2.span_reader import SpanScope

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

        fake_reader = MagicMock()
        fake_reader.__enter__.return_value = fake_reader
        fake_reader.__exit__.return_value = False
        fake_reader.scope_by_ids.return_value = {
            observation_span.id: SpanScope(
                project_id=str(observe_project.id), trace_id=None
            )
        }
        fake_reader.get.side_effect = AssertionError(
            "scores for-source must not read full span rows (OOMs shared ClickHouse)"
        )
        mocker.patch(
            "tracer.services.clickhouse.v2.get_reader", return_value=fake_reader
        )

        resp = auth_client.get(
            f"{SCORE_URL}for-source/",
            {
                "source_type": "observation_span",
                "source_id": observation_span.id,
            },
        )
        assert resp.status_code == status.HTTP_200_OK
        fake_reader.scope_by_ids.assert_called_once()
        assert len(resp.data["result"]) == 1


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
        """Queue item auto-completes when all required labels are scored
        *in that queue's context*.

        Scores are now per-queue: the caller must pass ``queue_item_id``
        (or score via the queue's submit endpoint) so the Score lands in
        the right queue. An "inline" /scores/ POST without queue_item_id
        attributes the score to the source's default queue, which leaves
        a non-default test queue's item pending — that's the intended
        per-queue isolation, exercised by ``test_no_auto_complete_partial_scoring``.

        Auto-complete still runs in ``transaction.on_commit`` (so a
        side-effect failure can't poison the Score write transaction).
        Pytest's default ``django_db`` mark wraps the test in a
        transaction that rolls back, so on_commit hooks never fire. Wrap
        calls in ``captureOnCommitCallbacks(execute=True)`` to force them.
        """
        from django.test import TestCase

        queue, item = queue_setup

        # Score first label (attribute to the test queue's item explicitly).
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
        item.refresh_from_db()
        assert item.status != QueueItemStatus.COMPLETED.value

        # Score second required label in the same queue → should auto-complete.
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
        span = ObservationSpan.objects.create(
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
        return span

    def test_session_list_includes_score_columns(
        self,
        auth_client,
        observe_project,
        trace_session,
        star_label,
        session_with_span,
    ):
        """Session list API returns annotation metric columns in config."""
        from tracer.tests._ch_seed import seed_ch_span

        # list_sessions reads session aggregation data from CH — seed the
        # span so the session appears in results and triggers config building.
        seed_ch_span(session_with_span)

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


@pytest.mark.django_db
class TestScoreOnCollectorOnlySpan:
    """Annotating a collector-only span (no PG row) resolves via CH fallback, not 404."""

    def _fake_reader(self, monkeypatch, ch_span_id, project, organization, trace):
        from types import SimpleNamespace

        fake = SimpleNamespace(
            id=ch_span_id,
            pk=ch_span_id,
            project_id=str(project.id),
            org_id=str(organization.id),
            trace_id=str(trace.id),
        )

        class _R:
            def get(self, sid):
                return fake if str(sid) == ch_span_id else None

            def close(self):
                pass

        monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: _R())

    def _fake_reader_with_org(self, monkeypatch, ch_span_id, project, org_id, trace):
        """Like _fake_reader but with explicit org_id to exercise the org-scope gate."""
        from types import SimpleNamespace

        fake = SimpleNamespace(
            id=ch_span_id,
            pk=ch_span_id,
            project_id=str(project.id),
            org_id=org_id,
            trace_id=str(trace.id),
        )

        class _R:
            def get(self, sid):
                return fake if str(sid) == ch_span_id else None

            def close(self):
                pass

        monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: _R())

    @pytest.mark.parametrize("foreign_org", ["", str(uuid.uuid4())])
    def test_ch_only_span_denied_cross_org(
        self,
        auth_client,
        observe_project,
        trace,
        organization,
        star_label,
        monkeypatch,
        foreign_org,
    ):
        """CH span with empty/foreign org_id must 404 (the org-scope guard failed OPEN on empty org)."""
        ch_span_id = "ch_" + uuid.uuid4().hex[:16]
        self._fake_reader_with_org(
            monkeypatch, ch_span_id, observe_project, foreign_org, trace
        )
        payload = {
            "source_type": "observation_span",
            "source_id": ch_span_id,
            "label_id": str(star_label.id),
            "value": {"rating": 4},
        }
        resp = auth_client.post(SCORE_URL, payload, format="json")
        assert resp.status_code == status.HTTP_404_NOT_FOUND, resp.content
        assert not Score.objects.filter(observation_span_id=ch_span_id).exists()

    def test_create_score_on_ch_only_span(
        self, auth_client, observe_project, trace, organization, star_label, monkeypatch
    ):
        ch_span_id = "ch_" + uuid.uuid4().hex[:16]
        self._fake_reader(monkeypatch, ch_span_id, observe_project, organization, trace)
        payload = {
            "source_type": "observation_span",
            "source_id": ch_span_id,
            "label_id": str(star_label.id),
            "value": {"rating": 4},
        }
        resp = auth_client.post(SCORE_URL, payload, format="json")
        assert resp.status_code == status.HTTP_200_OK, resp.content  # NOT 404
        s = Score.objects.get(observation_span_id=ch_span_id, deleted=False)
        assert s.source_type == "observation_span"
        # denormalized trace locator stamped so it rolls up to the trace
        assert str(s.trace_id) == str(trace.id)

    def test_bulk_score_and_spannote_on_ch_only_span(
        self, auth_client, observe_project, trace, organization, star_label, monkeypatch
    ):
        from tracer.models.span_notes import SpanNotes

        ch_span_id = "ch_" + uuid.uuid4().hex[:16]
        self._fake_reader(monkeypatch, ch_span_id, observe_project, organization, trace)
        payload = {
            "source_type": "observation_span",
            "source_id": ch_span_id,
            "span_notes": "looks correct",
            "scores": [{"label_id": str(star_label.id), "value": {"rating": 5}}],
        }
        resp = auth_client.post(SCORE_URL + "bulk/", payload, format="json")
        assert resp.status_code == status.HTTP_200_OK, resp.content  # NOT 404/500
        assert Score.objects.filter(
            observation_span_id=ch_span_id, deleted=False
        ).exists()
        # SpanNotes written by id (the FK rejects a CHSpan OBJECT) on the CH span id
        assert SpanNotes.objects.filter(span_id=ch_span_id).exists()


@pytest.mark.django_db
class TestScoreOnCollectorOnlyTrace:
    """Annotating a collector-only trace (no PG row) resolves via CH, not 404.

    TH-6647: the Observe grid stores a source_type=trace item; scoring it must
    CH-resolve the trace (via its root span) and attribute the score to a queue.
    """

    def _fake_trace_reader(self, monkeypatch, trace_id, project):
        from types import SimpleNamespace

        root_span = SimpleNamespace(
            id="ch_" + uuid.uuid4().hex[:16],
            project_id=str(project.id),
            trace_id=str(trace_id),
            parent_span_id="",  # root span
            observation_type="agent",
        )

        class _R:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def list_by_trace(self_inner, tid, *, project_id=None):
                return [root_span] if str(tid) == str(trace_id) else []

            def close(self_inner):
                pass

        monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: _R())

    def test_create_score_on_ch_only_trace(
        self, auth_client, observe_project, organization, star_label, monkeypatch
    ):
        """A collector trace scores end-to-end (200, not the TH-6647 404). Uses the
        default-queue path, so it also exercises the CH `_resolve_default_queue_scope`
        project fallback."""
        trace_id = str(uuid.uuid4())  # collector trace: NO PG row
        self._fake_trace_reader(monkeypatch, trace_id, observe_project)
        payload = {
            "source_type": "trace",
            "source_id": trace_id,
            "label_id": str(star_label.id),
            "value": {"rating": 4},
        }
        resp = auth_client.post(SCORE_URL, payload, format="json")
        assert resp.status_code == status.HTTP_200_OK, resp.content  # NOT 404
        s = Score.objects.get(trace_id=trace_id, deleted=False)
        assert s.source_type == "trace"
        assert s.queue_item_id is not None  # attributed to a queue
