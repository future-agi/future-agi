"""E2E tests for per-queue Score scoping.

Covers the contract introduced when Score uniqueness was widened to
``(source, label, annotator, queue_item)``:

- Same annotator can score the same label on the same source independently
  per queue; each queue is its own review context.
- ``/scores/bulk/`` and ``/scores/`` accept an explicit ``queue_item_id``;
  when omitted they resolve a default queue for the source's scope
  (auto-creating one if missing).
- ``submit_annotations`` scopes its upsert by queue_item — a second queue
  with the same source no longer overwrites the first queue's value.
- ``for_source`` returns queue-scoped ``existing_scores`` per section so
  Queue A's value doesn't bleed into Queue B's prefill.
- Auto-complete scopes by queue_item — a score filled in Queue A doesn't
  auto-complete Queue B's item even when both require the same label.
- Trace-list annotation aggregate averages across every Score row
  (including the new per-queue duplicates) rather than deduplicating
  by annotator.
"""

import json
import uuid

import pytest
from django.test import TestCase
from rest_framework import status

from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AnnotationQueueLabel,
    AnnotatorRole,
    QueueItem,
    QueueItemNote,
)
from model_hub.models.choices import (
    AnnotationQueueStatusChoices,
    AnnotationTypeChoices,
    QueueItemSourceType,
    QueueItemStatus,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score


# Reuse the fixtures from test_scores_api.py via pytest's collection. They
# live in the same package so importing here would create a circular
# fixture path; instead we replicate the small set we need below.

SCORE_URL = "/model-hub/scores/"
QUEUE_URL = "/model-hub/annotation-queues/"


# ---------------------------------------------------------------------------
# Fixtures (mirror the ones in test_scores_api.py so this file is
# self-contained — pytest can collect either suite independently)
# ---------------------------------------------------------------------------


@pytest.fixture
def observe_project(db, organization, workspace):
    from model_hub.models.ai_model import AIModel
    from tracer.models.project import Project

    return Project.objects.create(
        name="Per-Queue Scoring Project",
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
        name="Root Span",
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=5),
        end_time=timezone.now(),
        input={},
        output={},
        model="gpt-4",
        status="OK",
    )


@pytest.fixture
def star_label(db, organization, workspace, observe_project):
    return AnnotationsLabels.objects.create(
        name="Star wala label",
        type=AnnotationTypeChoices.STAR.value,
        settings={"no_of_stars": 5},
        organization=organization,
        workspace=workspace,
        project=observe_project,
    )


@pytest.fixture
def thumbs_label(db, organization, workspace, observe_project):
    return AnnotationsLabels.objects.create(
        name="Thumbs",
        type=AnnotationTypeChoices.THUMBS_UP_DOWN.value,
        settings={},
        organization=organization,
        workspace=workspace,
        project=observe_project,
    )


def _make_queue(name, organization, workspace, user, project=None, **extra):
    """Helper — create an active queue and make ``user`` a manager."""
    queue = AnnotationQueue.objects.create(
        name=f"{name} {uuid.uuid4().hex[:6]}",
        organization=organization,
        workspace=workspace,
        project=project,
        created_by=user,
        status=AnnotationQueueStatusChoices.ACTIVE.value,
        **extra,
    )
    AnnotationQueueAnnotator.objects.create(
        queue=queue,
        user=user,
        role=AnnotatorRole.MANAGER.value,
    )
    return queue


def _add_item(queue, source_type, source_obj, organization, workspace):
    fk_field = source_type if source_type != "observation_span" else "observation_span"
    return QueueItem.objects.create(
        queue=queue,
        source_type=source_type,
        organization=organization,
        workspace=workspace,
        status=QueueItemStatus.PENDING.value,
        **{fk_field: source_obj},
    )


# ---------------------------------------------------------------------------
# 1. Uniqueness — same (source, label, annotator) across queues
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.integration
class TestPerQueueUniqueness:
    """Each (source, label, annotator, queue_item) is its own row."""

    def test_two_queues_keep_independent_scores(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        trace,
        star_label,
    ):
        queue_a = _make_queue("Queue A", organization, workspace, user, observe_project)
        queue_b = _make_queue("Queue B", organization, workspace, user, observe_project)
        AnnotationQueueLabel.objects.create(queue=queue_a, label=star_label)
        AnnotationQueueLabel.objects.create(queue=queue_b, label=star_label)
        item_a = _add_item(
            queue_a, QueueItemSourceType.TRACE.value, trace, organization, workspace
        )
        item_b = _add_item(
            queue_b, QueueItemSourceType.TRACE.value, trace, organization, workspace
        )

        # Score 5 in queue A
        resp_a = auth_client.post(
            f"{QUEUE_URL}{queue_a.id}/items/{item_a.id}/annotations/submit/",
            {"annotations": [{"label_id": str(star_label.id), "value": {"rating": 5}}]},
            format="json",
        )
        assert resp_a.status_code == status.HTTP_200_OK

        # Score 2 in queue B for the same annotator + same source + same label
        resp_b = auth_client.post(
            f"{QUEUE_URL}{queue_b.id}/items/{item_b.id}/annotations/submit/",
            {"annotations": [{"label_id": str(star_label.id), "value": {"rating": 2}}]},
            format="json",
        )
        assert resp_b.status_code == status.HTTP_200_OK

        # Both rows survive — the pre-revamp constraint would have collapsed
        # them into one.
        rows = list(
            Score.objects.filter(
                trace=trace,
                label=star_label,
                annotator=user,
                deleted=False,
            ).order_by("queue_item_id")
        )
        assert len(rows) == 2
        by_item = {row.queue_item_id: row.value for row in rows}
        assert by_item[item_a.id] == {"rating": 5}
        assert by_item[item_b.id] == {"rating": 2}

    def test_resubmit_same_queue_updates_existing_row(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        trace,
        star_label,
    ):
        queue = _make_queue("Queue X", organization, workspace, user, observe_project)
        AnnotationQueueLabel.objects.create(queue=queue, label=star_label)
        item = _add_item(
            queue, QueueItemSourceType.TRACE.value, trace, organization, workspace
        )

        auth_client.post(
            f"{QUEUE_URL}{queue.id}/items/{item.id}/annotations/submit/",
            {"annotations": [{"label_id": str(star_label.id), "value": {"rating": 3}}]},
            format="json",
        )
        auth_client.post(
            f"{QUEUE_URL}{queue.id}/items/{item.id}/annotations/submit/",
            {"annotations": [{"label_id": str(star_label.id), "value": {"rating": 5}}]},
            format="json",
        )

        rows = list(
            Score.objects.filter(
                trace=trace, label=star_label, annotator=user, deleted=False
            )
        )
        assert len(rows) == 1
        assert rows[0].value == {"rating": 5}
        assert rows[0].queue_item_id == item.id


# ---------------------------------------------------------------------------
# 2. Score writes — explicit queue_item_id and default-queue fallback
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.integration
class TestScoreWritesResolveQueue:
    """/scores/ and /scores/bulk/ require a queue context for every row."""

    def test_bulk_with_explicit_queue_item_attributes_correctly(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        trace,
        star_label,
    ):
        queue = _make_queue("Queue", organization, workspace, user, observe_project)
        AnnotationQueueLabel.objects.create(queue=queue, label=star_label)
        item = _add_item(
            queue, QueueItemSourceType.TRACE.value, trace, organization, workspace
        )

        resp = auth_client.post(
            f"{SCORE_URL}bulk/",
            {
                "source_type": "trace",
                "source_id": str(trace.id),
                "queue_item_id": str(item.id),
                "scores": [
                    {"label_id": str(star_label.id), "value": {"rating": 4}},
                ],
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        score = Score.objects.get(trace=trace, label=star_label, annotator=user)
        assert score.queue_item_id == item.id
        assert score.value == {"rating": 4}

    def test_bulk_rejects_queue_item_for_a_different_source(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        trace,
        star_label,
    ):
        """Explicit queue_item_id must match the submitted source.

        Otherwise a trace-A score can be attached to trace-B's queue item and
        then appear in the wrong queue's history/review context.
        """
        from tracer.models.trace import Trace

        other_trace = Trace.objects.create(
            project=observe_project,
            name="Other Trace",
            input={"prompt": "other"},
            output={"response": "other"},
        )
        queue = _make_queue("Queue", organization, workspace, user, observe_project)
        AnnotationQueueLabel.objects.create(queue=queue, label=star_label)
        other_item = _add_item(
            queue,
            QueueItemSourceType.TRACE.value,
            other_trace,
            organization,
            workspace,
        )

        resp = auth_client.post(
            f"{SCORE_URL}bulk/",
            {
                "source_type": "trace",
                "source_id": str(trace.id),
                "queue_item_id": str(other_item.id),
                "scores": [
                    {"label_id": str(star_label.id), "value": {"rating": 4}},
                ],
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert not Score.objects.filter(
            trace=trace,
            label=star_label,
            annotator=user,
            deleted=False,
        ).exists()

    def test_bulk_without_queue_item_falls_back_to_default_queue(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        trace,
        star_label,
    ):
        # No default queue exists yet for the project — the bulk write
        # should auto-create one and attach a queue item to it.
        assert not AnnotationQueue.objects.filter(
            project=observe_project, is_default=True, deleted=False
        ).exists()

        resp = auth_client.post(
            f"{SCORE_URL}bulk/",
            {
                "source_type": "trace",
                "source_id": str(trace.id),
                "scores": [
                    {"label_id": str(star_label.id), "value": {"rating": 4}},
                ],
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        default_queue = AnnotationQueue.objects.get(
            project=observe_project, is_default=True, deleted=False
        )
        default_item = QueueItem.objects.get(
            queue=default_queue, trace=trace, deleted=False
        )
        score = Score.objects.get(trace=trace, label=star_label, annotator=user)
        assert score.queue_item_id == default_item.id
        membership = AnnotationQueueAnnotator.objects.get(
            queue=default_queue,
            user=user,
            deleted=False,
        )
        assert membership.role == AnnotatorRole.MANAGER.value
        assert set(membership.normalized_roles) == {
            AnnotatorRole.MANAGER.value,
            AnnotatorRole.REVIEWER.value,
            AnnotatorRole.ANNOTATOR.value,
        }

    def test_bulk_without_queue_item_reuses_existing_default_queue(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        trace,
        star_label,
    ):
        existing_default = _make_queue(
            "Default - existing",
            organization,
            workspace,
            user,
            observe_project,
            is_default=True,
        )

        resp = auth_client.post(
            f"{SCORE_URL}bulk/",
            {
                "source_type": "trace",
                "source_id": str(trace.id),
                "scores": [
                    {"label_id": str(star_label.id), "value": {"rating": 1}},
                ],
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        # No new default queue created — the existing one was reused.
        assert (
            AnnotationQueue.objects.filter(
                project=observe_project, is_default=True, deleted=False
            ).count()
            == 1
        )
        score = Score.objects.get(trace=trace, label=star_label, annotator=user)
        assert score.queue_item.queue_id == existing_default.id


# ---------------------------------------------------------------------------
# 3. Reads — per-queue existing_scores isolation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.integration
class TestForSourceIsolation:
    """``for-source`` shows each queue its own value, never a sibling queue's."""

    def test_existing_scores_are_scoped_per_queue(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        trace,
        star_label,
    ):
        queue_a = _make_queue("Queue A", organization, workspace, user, observe_project)
        queue_b = _make_queue("Queue B", organization, workspace, user, observe_project)
        AnnotationQueueLabel.objects.create(queue=queue_a, label=star_label)
        AnnotationQueueLabel.objects.create(queue=queue_b, label=star_label)
        item_a = _add_item(
            queue_a, QueueItemSourceType.TRACE.value, trace, organization, workspace
        )
        item_b = _add_item(
            queue_b, QueueItemSourceType.TRACE.value, trace, organization, workspace
        )

        # Score 5 in queue A only.
        auth_client.post(
            f"{QUEUE_URL}{queue_a.id}/items/{item_a.id}/annotations/submit/",
            {"annotations": [{"label_id": str(star_label.id), "value": {"rating": 5}}]},
            format="json",
        )

        sources = [{"source_type": "trace", "source_id": str(trace.id)}]
        resp = auth_client.get(
            f"{QUEUE_URL}for-source/",
            {"sources": json.dumps(sources)},
        )
        assert resp.status_code == status.HTTP_200_OK
        result = resp.data["result"]
        by_queue = {entry["queue"]["id"]: entry for entry in result}

        # Queue A sees its own 5 stars; Queue B is empty even though the
        # same source + label exist there.
        assert by_queue[str(queue_a.id)]["existing_scores"][str(star_label.id)] == {
            "rating": 5
        }
        assert by_queue[str(queue_b.id)]["existing_scores"] == {}
        # item_b is intentionally referenced to silence unused-var lint —
        # the test only asserts on queue B's prefill being empty.
        assert item_b.queue_id == queue_b.id

    def test_multi_source_query_surfaces_queues_at_every_level(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        trace,
        observation_span,
        trace_session,
        star_label,
        thumbs_label,
    ):
        # Wire the trace to a session so the trace_session source resolves.
        trace.session = trace_session
        trace.save(update_fields=["session"])

        trace_queue = _make_queue(
            "Trace Q", organization, workspace, user, observe_project
        )
        span_queue = _make_queue(
            "Span Q", organization, workspace, user, observe_project
        )
        session_queue = _make_queue(
            "Session Q", organization, workspace, user, observe_project
        )
        AnnotationQueueLabel.objects.create(queue=trace_queue, label=star_label)
        AnnotationQueueLabel.objects.create(queue=span_queue, label=thumbs_label)
        AnnotationQueueLabel.objects.create(queue=session_queue, label=star_label)

        _add_item(
            trace_queue,
            QueueItemSourceType.TRACE.value,
            trace,
            organization,
            workspace,
        )
        _add_item(
            span_queue,
            QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span,
            organization,
            workspace,
        )
        _add_item(
            session_queue,
            QueueItemSourceType.TRACE_SESSION.value,
            trace_session,
            organization,
            workspace,
        )

        sources = [
            {"source_type": "trace", "source_id": str(trace.id)},
            {"source_type": "observation_span", "source_id": observation_span.id},
            {"source_type": "trace_session", "source_id": str(trace_session.id)},
        ]
        resp = auth_client.get(
            f"{QUEUE_URL}for-source/", {"sources": json.dumps(sources)}
        )
        assert resp.status_code == status.HTTP_200_OK
        returned = {entry["queue"]["id"] for entry in resp.data["result"]}
        assert str(trace_queue.id) in returned
        assert str(span_queue.id) in returned
        assert str(session_queue.id) in returned


# ---------------------------------------------------------------------------
# 4. Auto-complete scoping
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.integration
class TestAutoCompleteIsQueueScoped:
    """A score in Queue A no longer auto-completes Queue B's item."""

    def test_score_in_queue_a_does_not_complete_queue_b(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        trace,
        star_label,
    ):
        queue_a = _make_queue("Queue A", organization, workspace, user, observe_project)
        queue_b = _make_queue("Queue B", organization, workspace, user, observe_project)
        AnnotationQueueLabel.objects.create(
            queue=queue_a, label=star_label, required=True
        )
        AnnotationQueueLabel.objects.create(
            queue=queue_b, label=star_label, required=True
        )
        item_a = _add_item(
            queue_a, QueueItemSourceType.TRACE.value, trace, organization, workspace
        )
        item_b = _add_item(
            queue_b, QueueItemSourceType.TRACE.value, trace, organization, workspace
        )

        with TestCase.captureOnCommitCallbacks(execute=True):
            resp = auth_client.post(
                f"{SCORE_URL}bulk/",
                {
                    "source_type": "trace",
                    "source_id": str(trace.id),
                    "queue_item_id": str(item_a.id),
                    "scores": [
                        {"label_id": str(star_label.id), "value": {"rating": 5}},
                    ],
                },
                format="json",
            )
            assert resp.status_code == status.HTTP_200_OK

        item_a.refresh_from_db()
        item_b.refresh_from_db()
        # Queue A's item auto-completes — its only required label was scored
        # in its own context. Queue B's item stays pending because its
        # required label has no score in *its* context.
        assert item_a.status == QueueItemStatus.COMPLETED.value
        assert item_b.status == QueueItemStatus.PENDING.value

    def test_filling_queue_b_separately_completes_queue_b(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        trace,
        star_label,
    ):
        queue_a = _make_queue("Queue A", organization, workspace, user, observe_project)
        queue_b = _make_queue("Queue B", organization, workspace, user, observe_project)
        AnnotationQueueLabel.objects.create(
            queue=queue_a, label=star_label, required=True
        )
        AnnotationQueueLabel.objects.create(
            queue=queue_b, label=star_label, required=True
        )
        item_a = _add_item(
            queue_a, QueueItemSourceType.TRACE.value, trace, organization, workspace
        )
        item_b = _add_item(
            queue_b, QueueItemSourceType.TRACE.value, trace, organization, workspace
        )

        with TestCase.captureOnCommitCallbacks(execute=True):
            auth_client.post(
                f"{SCORE_URL}bulk/",
                {
                    "source_type": "trace",
                    "source_id": str(trace.id),
                    "queue_item_id": str(item_a.id),
                    "scores": [
                        {"label_id": str(star_label.id), "value": {"rating": 5}},
                    ],
                },
                format="json",
            )
        with TestCase.captureOnCommitCallbacks(execute=True):
            auth_client.post(
                f"{SCORE_URL}bulk/",
                {
                    "source_type": "trace",
                    "source_id": str(trace.id),
                    "queue_item_id": str(item_b.id),
                    "scores": [
                        {"label_id": str(star_label.id), "value": {"rating": 2}},
                    ],
                },
                format="json",
            )

        item_a.refresh_from_db()
        item_b.refresh_from_db()
        assert item_a.status == QueueItemStatus.COMPLETED.value
        assert item_b.status == QueueItemStatus.COMPLETED.value


# ---------------------------------------------------------------------------
# 5. Trace-list aggregate — averages across every per-queue Score row
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.integration
class TestTraceListAggregate:
    """The annotation_map aggregator now averages across per-queue duplicates."""

    def test_star_label_averages_across_three_queues(
        self,
        organization,
        workspace,
        user,
        observe_project,
        trace,
        star_label,
    ):
        """5 + 2 + 2 across three queues → display value 3."""
        from tracer.views.trace import _build_annotation_map_from_scores_pg

        queues = [
            _make_queue(f"Q{idx}", organization, workspace, user, observe_project)
            for idx in range(3)
        ]
        items = []
        for q in queues:
            AnnotationQueueLabel.objects.create(queue=q, label=star_label)
            items.append(
                _add_item(
                    q,
                    QueueItemSourceType.TRACE.value,
                    trace,
                    organization,
                    workspace,
                )
            )

        for item, rating in zip(items, [5, 2, 2]):
            Score.objects.create(
                trace=trace,
                label=star_label,
                annotator=user,
                queue_item=item,
                source_type="trace",
                value={"rating": rating},
                organization=organization,
                workspace=workspace,
            )

        amap = _build_annotation_map_from_scores_pg(
            [str(trace.id)],
            [str(star_label.id)],
            label_types={str(star_label.id): "star"},
        )
        entry = amap[str(trace.id)][str(star_label.id)]
        assert entry["score"] == 3  # int((5+2+2)/3)
        # The per-annotator breakdown also averages within an annotator's queues.
        anno = entry["annotators"][str(user.id)]
        assert anno["score"] == pytest.approx(3.0)

    def test_thumbs_counts_every_queue(
        self,
        organization,
        workspace,
        user,
        observe_project,
        trace,
        thumbs_label,
    ):
        """Three queues, two thumbs-up and one thumbs-down → 2/1 counts."""
        from tracer.views.trace import _build_annotation_map_from_scores_pg

        queues = [
            _make_queue(f"Q{idx}", organization, workspace, user, observe_project)
            for idx in range(3)
        ]
        items = []
        for q in queues:
            AnnotationQueueLabel.objects.create(queue=q, label=thumbs_label)
            items.append(
                _add_item(
                    q,
                    QueueItemSourceType.TRACE.value,
                    trace,
                    organization,
                    workspace,
                )
            )

        for item, val in zip(items, ["up", "up", "down"]):
            Score.objects.create(
                trace=trace,
                label=thumbs_label,
                annotator=user,
                queue_item=item,
                source_type="trace",
                value={"value": val},
                organization=organization,
                workspace=workspace,
            )

        amap = _build_annotation_map_from_scores_pg(
            [str(trace.id)],
            [str(thumbs_label.id)],
            label_types={str(thumbs_label.id): "thumbs_up_down"},
        )
        entry = amap[str(trace.id)][str(thumbs_label.id)]
        # Pre-fix this would have been 1/1 because the annotator key was
        # overwritten on each iteration.
        assert entry["thumbs_up"] == 2
        assert entry["thumbs_down"] == 1


# ---------------------------------------------------------------------------
# 6. Backfill commands — legacy orphan rows become queue-scoped
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.integration
class TestQueueScopedBackfillCommands:
    """Management commands keep legacy data visible under the new contract."""

    def test_orphan_score_backfill_attaches_to_default_queue_item(
        self,
        organization,
        workspace,
        user,
        observe_project,
        trace,
        star_label,
    ):
        from django.core.management import call_command

        score = Score.objects.create(
            trace=trace,
            label=star_label,
            annotator=user,
            source_type=QueueItemSourceType.TRACE.value,
            value={"rating": 4},
            organization=organization,
            workspace=workspace,
            queue_item=None,
        )

        call_command("backfill_orphan_score_queue_items", batch_size=10)

        score.refresh_from_db()
        assert score.queue_item is not None
        assert score.queue_item.trace_id == trace.id
        assert score.queue_item.queue.is_default is True
        membership = AnnotationQueueAnnotator.objects.get(
            queue=score.queue_item.queue,
            user=user,
            deleted=False,
        )
        assert AnnotatorRole.MANAGER.value in membership.normalized_roles

    def test_span_note_backfill_mirrors_to_queue_item_note(
        self,
        organization,
        workspace,
        user,
        observation_span,
    ):
        from django.core.management import call_command
        from tracer.models.span_notes import SpanNotes

        SpanNotes.objects.create(
            span=observation_span,
            notes="legacy whole-item note",
            created_by_user=user,
            created_by_annotator=user.email,
        )

        call_command("backfill_span_notes_to_queue_items", batch_size=10)

        note = QueueItemNote.objects.get(
            annotator=user,
            notes="legacy whole-item note",
            deleted=False,
        )
        assert note.queue_item.source_type == QueueItemSourceType.OBSERVATION_SPAN.value
        assert note.queue_item.observation_span_id == observation_span.id
        assert note.queue_item.queue.is_default is True
