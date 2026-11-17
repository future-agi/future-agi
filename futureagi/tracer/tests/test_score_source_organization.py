"""Scores and notes are listed only in the organization of their source.

Before annotations added from the trace view stayed in the request's
organization, a person who belongs to two organizations could annotate a span
of the second organization's project (B) while their account named the first
(A). The score and the note were kept in A, on an item of a default queue of A
over B's project, under B's label. These tests make those rows directly, as
the cleanup listing counts them, and pin what A's readers list:

- (a) A's scores list, a score read by its id and the scores of one source do
  not list the score, to the person who wrote it or to another member of A;
  the item lists none of its scores or notes in the annotate view, the item's
  annotations or the queue sections of a source; the queue's export, analytics,
  progress and agreement leave the score and the note out, and the export
  carries none of the span's evaluation results;
- (b) a score of A over A's own project lists as before, including a score on
  a span held only in ClickHouse, and an item of A over A's project shows its
  scores and notes as before;
- (c) an item over B's project with a stored preview shows the deleted source
  in the item list, the item read and the annotate view; an item of A with a
  stored preview shows that preview;
- (d) scores of A on a span of a project in another workspace of A list as
  before, including through a queue of the default workspace;
- (e) an item that records no project, in a default queue of A over B's
  project, shows the deleted source in the item read, the item list, the
  annotate view and the export, its stored preview is not returned and the
  export carries no evaluation result for it; an item that records no project
  in A's own default queue shows its preview and its evaluation results as
  before;
- (f) the readers of one item's or one queue's scores and notes check the
  item's and the queue's project by joining them in their own statement, so
  none of them selects from the project table;
- (g) an annotation that makes a default queue's item records the queue's
  project on it, through the helper and through the bulk annotation
  endpoint, and makes no item in a default queue of another organization than
  the one the annotation is made in;
- (h) a queue adds a label of its own organization only: a label of another
  organization is not found, whichever workspace the label was saved with;
- (i) the trace view's annotation values, which read score rows from
  ClickHouse, list no score of A over B's span, a score under a label of A
  included, and list a score of A over A's own span as before.

ClickHouse is faked with one copy of each span, in its own project. Postgres,
the helpers, the serializers and the endpoints run for real.
"""

import json
import uuid
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import status

from accounts.models.workspace import Workspace
from conftest import WorkspaceAwareAPIClient
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueLabel,
    QueueItem,
    QueueItemNote,
)
from model_hub.models.choices import AnnotationQueueStatusChoices
from model_hub.models.score import Score
from model_hub.serializers.annotation_queues import QueueItemSerializer
from model_hub.tests.test_annotation_queues_api import create_workspace_member_user
from model_hub.tests.test_queue_item_source_copy_scope import _Copies
from model_hub.utils.annotation_queue_helpers import (
    QueueSourceAuthorization,
    calculate_agreement,
    preview_payload_for_source,
)
from model_hub.views.annotation_queues import (
    _item_note_rows,
    _latest_item_notes_for_queue_items,
    _scores_for_queue_items,
    _scores_listed_for_queue_item,
)
from model_hub.views.scores import _auto_create_queue_items_for_default_queues
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.observation_span import EvalLogger
from tracer.services.clickhouse.v2.span_reader import SpanScope
from tracer.tests.test_add_annotations_request_organization import (
    _own_queue,
    _project,
    _second_organization,
    _span,
    _star_label,
)
from tracer.tests.test_default_queue_item_render_organization import (
    QUEUE_URL,
    UNAVAILABLE,
    _hold,
    _queue_item,
    _request,
    _saved,
    _shows_copy,
    _stamp_workspace,
)

SCORES_URL = "/model-hub/scores/"
FOR_SOURCE_URL = "/model-hub/scores/for-source/"
QUEUE_FOR_SOURCE_URL = "/model-hub/annotation-queues/for-source/"
BULK_ANNOTATION_URL = "/tracer/bulk-annotation/"
ANNOTATION_VALUES_URL = "/tracer/trace-annotation/get_annotation_values/"

# Where the trace view takes its ClickHouse query service from.
SCORE_ROWS = "tracer.views.annotation.AnalyticsQueryService"

SCORE_NOTE = "score note written in the second organization"
ITEM_NOTE = "item note written in the second organization"
EVAL_EXPLANATION = "evaluation of the span written in the second organization"
OWN_NOTE = "item note written in the first organization"
OWN_EVAL_EXPLANATION = "evaluation of the span written in the first organization"


class _Reader(_Copies):
    """The queue tests' ClickHouse stand-in, with the lean scope read that the
    scores of one source make."""

    def scope_by_ids(self, span_ids, *, project_ids=None):
        wanted = {str(span_id) for span_id in span_ids}
        scopes = {}
        for span in self._scoped(project_ids=project_ids):
            if span.id in wanted:
                scopes.setdefault(
                    span.id,
                    SpanScope(project_id=span.project_id, trace_id=span.trace_id),
                )
        return scopes


@pytest.fixture
def span_copies(monkeypatch):
    """The faked ClickHouse: ``(version, CHSpan)`` rows the test adds."""
    # The feed queries bind ``get_reader`` when they are first imported. Import
    # them before the stand-in is set, so they keep the real reader and a later
    # test in the same run never meets this file's stand-in through them.
    import tracer.queries.feed  # noqa: F401

    rows = []
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.get_reader", lambda: _Reader(rows)
    )
    return rows


class _ScoreRows:
    """The trace view's ClickHouse query service stand-in. It answers with the
    replicated score rows the real query selects, the scores of the request's
    organization on the span asked for, and keeps the ids it answered with."""

    def __init__(self, scores):
        self.scores = list(scores)
        self.answered = []

    def execute_ch_query(self, query, params, timeout_ms=None, **_):
        rows = [
            {
                "id": str(score.id),
                "label_id": str(score.label_id),
                "value": json.dumps(score.value),
                "annotator_id": str(score.annotator_id),
                "score_source": score.score_source,
                "created_at": score.created_at,
                "updated_at": score.updated_at,
            }
            for score in self.scores
            if str(score.organization_id) == params["organization_id"]
            and str(score.observation_span_id) == params["span_id"]
        ]
        self.answered.extend(row["id"] for row in rows)
        return SimpleNamespace(data=rows)


def _clickhouse_only_span():
    """A span held only in ClickHouse: no Postgres row has its id."""
    return SimpleNamespace(id=f"chonly_{uuid.uuid4().hex[:16]}", trace_id=uuid.uuid4())


def _misfiled_rows(user, organization, workspace, span_copies, *, captured=False):
    """A's default queue over B's project, its item stamped with A, and a score
    and a note in A on it under B's label, as the trace view used to save
    them. Every row passes its model's clean()."""
    second = _second_organization(user)
    copy = _hold(span_copies, second.project, second.span)
    clear_workspace_context()
    queue, item = _queue_item(
        organization,
        workspace,
        second.project,
        second.span,
        user,
        preview=(
            preview_payload_for_source("observation_span", copy) if captured else None
        ),
    )
    _saved(AnnotationQueueLabel(queue=queue, label=second.label))
    score = _saved(
        Score(
            source_type="observation_span",
            observation_span_id=str(second.span.id),
            label=second.label,
            value={"rating": 4},
            annotator=user,
            notes=SCORE_NOTE,
            queue_item=item,
            organization=organization,
            workspace=workspace,
            tracer_project_id=second.project.id,
        )
    )
    note = _saved(
        QueueItemNote(
            queue_item=item,
            annotator=user,
            notes=ITEM_NOTE,
            organization=organization,
            workspace=workspace,
        )
    )
    assert score.organization_id == item.organization_id == organization.id
    assert note.organization_id == queue.organization_id == organization.id
    assert item.project_id == queue.project_id == second.project.id
    assert str(score.tracer_project_id) == str(second.project.id)
    assert second.project.organization_id == second.org.id != organization.id
    assert (item.source_preview is not None) is captured
    return SimpleNamespace(
        second=second, queue=queue, item=item, score=score, note=note
    )


def _own_rows(user, organization, workspace, span_copies, *, clickhouse_only):
    """A's default queue over A's own project, with a score and a note on its
    item, as the trace view saves them now."""
    project = _project(organization, workspace, "First")
    span = _clickhouse_only_span() if clickhouse_only else _span(project, "First")
    _hold(span_copies, project, span)
    label = _star_label(organization, workspace, project)
    clear_workspace_context()
    queue, item = _queue_item(organization, workspace, project, span, user)
    _saved(AnnotationQueueLabel(queue=queue, label=label))
    score = _saved(
        Score(
            source_type="observation_span",
            observation_span_id=str(span.id),
            label=label,
            value={"rating": 4},
            annotator=user,
            queue_item=item,
            organization=organization,
            workspace=workspace,
            tracer_project_id=project.id,
        )
    )
    _saved(
        QueueItemNote(
            queue_item=item,
            annotator=user,
            notes=OWN_NOTE,
            organization=organization,
            workspace=workspace,
        )
    )
    return SimpleNamespace(
        project=project, span=span, label=label, queue=queue, item=item, score=score
    )


def _client(viewer, workspace):
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=viewer)
    client.set_workspace(workspace)
    return client


def _result(response):
    assert response.status_code == status.HTTP_200_OK, response.data
    return response.data["result"]


def _listed_ids(response):
    """The score ids a page or a list of scores names."""
    assert response.status_code == status.HTTP_200_OK, response.data
    if "results" in response.data:
        rows = response.data["results"]
    else:
        rows = response.data["result"]
    return {str(row["id"]) for row in rows}


def _queue_sections(client, span_id):
    return client.get(
        QUEUE_FOR_SOURCE_URL,
        {
            "sources": json.dumps(
                [{"source_type": "observation_span", "source_id": str(span_id)}]
            )
        },
    )


def _section_of(response, queue):
    sections = [
        section
        for section in _result(response)
        if section["queue"]["id"] == str(queue.id)
    ]
    assert len(sections) == 1, response.data
    return sections[0]


@pytest.mark.django_db
@pytest.mark.integration
class TestMisfiledScoreIsNotListed:
    @pytest.mark.parametrize("viewer", ["author", "member"])
    def test_scores_list_and_for_source_leave_out_a_misfiled_score(
        self, auth_client, user, organization, workspace, span_copies, viewer
    ):
        own = _own_rows(
            user, organization, workspace, span_copies, clickhouse_only=False
        )
        misfiled = _misfiled_rows(user, organization, workspace, span_copies)
        client = auth_client
        if viewer == "member":
            # A member of the first organization only.
            member = create_workspace_member_user(organization, workspace)
            client = _client(member, workspace)
        clear_workspace_context()
        span_id = str(misfiled.second.span.id)

        everything = client.get(SCORES_URL)
        listed = _listed_ids(everything)
        assert str(own.score.id) in listed, "control: A's own score is listed"
        assert str(misfiled.score.id) not in listed
        by_source = client.get(
            SCORES_URL, {"source_type": "observation_span", "source_id": span_id}
        )
        assert _listed_ids(by_source) == set()
        for_source = client.get(
            FOR_SOURCE_URL, {"source_type": "observation_span", "source_id": span_id}
        )
        assert _listed_ids(for_source) == set()
        own_for_source = client.get(
            FOR_SOURCE_URL,
            {"source_type": "observation_span", "source_id": str(own.span.id)},
        )
        assert _listed_ids(own_for_source) == {str(own.score.id)}
        read = client.get(f"{SCORES_URL}{misfiled.score.id}/")
        assert read.status_code == status.HTTP_404_NOT_FOUND, read.data
        # Nothing of the second organization's span or of the score's text.
        for response in (everything, by_source, for_source, read):
            text = str(response.data)
            assert span_id not in text, response.data
            assert SCORE_NOTE not in text, response.data

    def test_trace_view_annotation_values_list_no_score_over_another_organization(
        self, auth_client, user, organization, workspace, span_copies, monkeypatch
    ):
        misfiled = _misfiled_rows(user, organization, workspace, span_copies)
        span_id = str(misfiled.second.span.id)
        # The misfiled score is under the second organization's label, which
        # a request of the first organization does not find. A second score
        # on the same item, under a label of the first organization, is one
        # the trace view shows a value for.
        own_label = _star_label(organization, workspace, None)
        clear_workspace_context()
        under_own_label = _saved(
            Score(
                source_type="observation_span",
                observation_span_id=span_id,
                label=own_label,
                value={"rating": 2},
                annotator=user,
                queue_item=misfiled.item,
                organization=organization,
                workspace=workspace,
                tracer_project_id=misfiled.second.project.id,
            )
        )
        filed = {str(misfiled.score.id), str(under_own_label.id)}
        score_rows = _ScoreRows([misfiled.score, under_own_label])
        monkeypatch.setattr(SCORE_ROWS, lambda: score_rows)

        response = auth_client.get(
            ANNOTATION_VALUES_URL, {"observation_span_id": span_id}
        )

        # Control: ClickHouse answered with both scores.
        assert set(score_rows.answered) == filed
        assert _result(response)["annotations"] == []
        for score_id in filed:
            assert score_id not in str(response.data), response.data

    def test_misfiled_item_lists_none_of_its_scores_or_notes(
        self, auth_client, user, organization, workspace, span_copies
    ):
        misfiled = _misfiled_rows(user, organization, workspace, span_copies)
        clear_workspace_context()
        item_url = f"{QUEUE_URL}{misfiled.queue.pk}/items/{misfiled.item.pk}/"

        # The person who wrote them opens the item in the first organization.
        detail = auth_client.get(f"{item_url}annotate-detail/")
        body = _result(detail)
        assert body["annotations"] == []
        assert body["existing_notes"] == ""
        assert body["span_notes"] == []
        annotations = auth_client.get(f"{item_url}annotations/")
        assert _result(annotations) == []
        sections = _queue_sections(auth_client, misfiled.second.span.id)
        section = _section_of(sections, misfiled.queue)
        assert section["existing_scores"] == {}
        assert section["existing_label_notes"] == {}
        assert section["existing_notes"] == ""
        assert section["span_notes"] == []
        for response in (detail, annotations, sections):
            text = str(response.data)
            assert SCORE_NOTE not in text, response.data
            assert ITEM_NOTE not in text, response.data
            assert str(misfiled.score.id) not in text, response.data

    def test_queue_export_and_analytics_leave_out_a_misfiled_score_and_note(
        self, auth_client, user, organization, workspace, span_copies
    ):
        misfiled = _misfiled_rows(user, organization, workspace, span_copies)
        # B's evaluation of its span. Evaluation rows name the span and trace
        # only, no organization, so the export must leave them out for this
        # item as it leaves out the source.
        EvalLogger.objects.create(
            observation_span_id=str(misfiled.second.span.id),
            trace_id=misfiled.second.span.trace_id,
            eval_type_id="toxicity",
            output_float=0.9,
            eval_explanation=EVAL_EXPLANATION,
        )
        clear_workspace_context()
        queue_url = f"{QUEUE_URL}{misfiled.queue.pk}/"

        answer = auth_client.get(f"{queue_url}export/")
        exported = _result(answer)
        assert [row["item_id"] for row in exported] == [str(misfiled.item.id)]
        assert exported[0]["annotations"] == []
        assert exported[0]["item_notes"] == ""
        assert exported[0]["evals"] == {}
        assert EVAL_EXPLANATION not in json.dumps(answer.data, default=str)
        analytics = _result(auth_client.get(f"{queue_url}analytics/"))
        assert analytics["label_distribution"] == {}
        assert analytics["annotator_performance"] == []
        progress = _result(auth_client.get(f"{queue_url}progress/"))
        assert progress["annotator_stats"] == []
        assert calculate_agreement(misfiled.queue)["labels"] == {}
        # The readers export-to-dataset and the export fields share.
        item = QueueItem.no_workspace_objects.get(pk=misfiled.item.pk)
        by_item = _scores_for_queue_items([item], [misfiled.second.label.id])
        assert by_item == {item.id: []}
        assert _latest_item_notes_for_queue_items([item]) == {}


@pytest.mark.django_db
@pytest.mark.integration
class TestScoresOfTheOrganizationReadAsBefore:
    def test_scores_of_the_organization_list_as_before(
        self, auth_client, user, organization, workspace, span_copies
    ):
        in_postgres = _own_rows(
            user, organization, workspace, span_copies, clickhouse_only=False
        )
        clickhouse_only = _own_rows(
            user, organization, workspace, span_copies, clickhouse_only=True
        )
        member = create_workspace_member_user(organization, workspace)
        clear_workspace_context()

        for client in (auth_client, _client(member, workspace)):
            listed = _listed_ids(client.get(SCORES_URL))
            assert {str(in_postgres.score.id), str(clickhouse_only.score.id)} <= listed
            for rows in (in_postgres, clickhouse_only):
                source = {"source_type": "observation_span", "source_id": rows.span.id}
                expected = {str(rows.score.id)}
                assert _listed_ids(client.get(SCORES_URL, source)) == expected
                assert _listed_ids(client.get(FOR_SOURCE_URL, source)) == expected
                pinned = client.get(
                    FOR_SOURCE_URL, {**source, "project_id": str(rows.project.id)}
                )
                assert _listed_ids(pinned) == expected
                read = client.get(f"{SCORES_URL}{rows.score.id}/")
                assert read.status_code == status.HTTP_200_OK, read.data

    @pytest.mark.parametrize(
        "clickhouse_only",
        [
            pytest.param(False, id="span-in-postgres"),
            pytest.param(True, id="span-in-clickhouse-only"),
        ],
    )
    def test_trace_view_annotation_values_list_the_organizations_own_score(
        self,
        auth_client,
        user,
        organization,
        workspace,
        span_copies,
        monkeypatch,
        clickhouse_only,
    ):
        own = _own_rows(
            user, organization, workspace, span_copies, clickhouse_only=clickhouse_only
        )
        clear_workspace_context()
        score_rows = _ScoreRows([own.score])
        monkeypatch.setattr(SCORE_ROWS, lambda: score_rows)

        response = auth_client.get(
            ANNOTATION_VALUES_URL, {"observation_span_id": str(own.span.id)}
        )

        # Control: ClickHouse answered with the score.
        assert score_rows.answered == [str(own.score.id)]
        annotations = _result(response)["annotations"]
        assert [row["id"] for row in annotations] == [str(own.score.id)]
        assert annotations[0]["annotation_label_id"] == str(own.label.id)
        assert annotations[0]["annotation_value"] == 4

    def test_item_of_the_organization_shows_its_scores_and_notes_as_before(
        self, auth_client, user, organization, workspace, span_copies
    ):
        rows = _own_rows(
            user, organization, workspace, span_copies, clickhouse_only=True
        )
        clear_workspace_context()
        item_url = f"{QUEUE_URL}{rows.queue.pk}/items/{rows.item.pk}/"
        queue_url = f"{QUEUE_URL}{rows.queue.pk}/"

        body = _result(auth_client.get(f"{item_url}annotate-detail/"))
        assert [row["id"] for row in body["annotations"]] == [str(rows.score.id)]
        assert body["existing_notes"] == OWN_NOTE
        assert OWN_NOTE in [note["notes"] for note in body["span_notes"]]
        annotations = _result(auth_client.get(f"{item_url}annotations/"))
        assert [row["id"] for row in annotations] == [str(rows.score.id)]
        section = _section_of(_queue_sections(auth_client, rows.span.id), rows.queue)
        assert section["existing_scores"] == {str(rows.label.id): {"rating": 4}}
        assert section["existing_notes"] == OWN_NOTE

        analytics = _result(auth_client.get(f"{queue_url}analytics/"))
        assert list(analytics["label_distribution"]) == [str(rows.label.id)]
        performers = [row["user_id"] for row in analytics["annotator_performance"]]
        assert performers == [str(user.id)]
        progress = _result(auth_client.get(f"{queue_url}progress/"))
        stats = [
            (row["user_id"], row["annotations_count"])
            for row in progress["annotator_stats"]
        ]
        assert stats == [(str(user.id), 1)]
        agreement = calculate_agreement(rows.queue)
        assert list(agreement["labels"]) == [str(rows.label.id)]
        item = QueueItem.no_workspace_objects.get(pk=rows.item.pk)
        exported = _scores_for_queue_items([item], [rows.label.id])[item.id]
        assert [score.id for score in exported] == [rows.score.id]
        assert _latest_item_notes_for_queue_items([item]) == {item.id: OWN_NOTE}

    def test_scores_on_a_project_in_another_workspace_of_the_organization(
        self, auth_client, user, organization, workspace, span_copies
    ):
        # Same organization: the project lives in a second workspace, and the
        # scores were written from the default workspace, one through the
        # project's own default queue and one through a queue of the default
        # workspace that holds the same span.
        other_workspace = Workspace.no_workspace_objects.create(
            name=f"Other Workspace {uuid.uuid4().hex[:8]}",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        project = _project(organization, other_workspace, "Shared")
        span = _clickhouse_only_span()
        _hold(span_copies, project, span)
        label = _star_label(organization, other_workspace, project)
        clear_workspace_context()
        default_queue, default_item = _queue_item(
            organization, other_workspace, project, span, user
        )
        shared_queue = _stamp_workspace(
            _saved(
                AnnotationQueue(
                    name=f"Shared review {uuid.uuid4().hex[:8]}",
                    status=AnnotationQueueStatusChoices.ACTIVE.value,
                    organization=organization,
                    workspace=workspace,
                    created_by=user,
                )
            ),
            workspace,
        )
        shared_item = _stamp_workspace(
            _saved(
                QueueItem(
                    queue=shared_queue,
                    source_type="observation_span",
                    observation_span_id=str(span.id),
                    organization=organization,
                    workspace=workspace,
                    project=project,
                    order=1,
                )
            ),
            workspace,
        )
        scores = []
        for queue, item, rating in (
            (default_queue, default_item, 3),
            (shared_queue, shared_item, 5),
        ):
            _saved(AnnotationQueueLabel(queue=queue, label=label))
            scores.append(
                _saved(
                    Score(
                        source_type="observation_span",
                        observation_span_id=str(span.id),
                        label=label,
                        value={"rating": rating},
                        annotator=user,
                        queue_item=item,
                        organization=organization,
                        workspace=workspace,
                        tracer_project_id=project.id,
                    )
                )
            )
        assert project.workspace_id == other_workspace.id != workspace.id
        assert default_queue.workspace_id == other_workspace.id
        assert shared_queue.workspace_id == shared_item.workspace_id == workspace.id
        member = create_workspace_member_user(organization, workspace)
        clear_workspace_context()

        expected = {str(score.id) for score in scores}
        source = {"source_type": "observation_span", "source_id": span.id}
        for client in (auth_client, _client(member, workspace)):
            assert expected <= _listed_ids(client.get(SCORES_URL))
            assert _listed_ids(client.get(SCORES_URL, source)) == expected
            assert _listed_ids(client.get(FOR_SOURCE_URL, source)) == expected

        shared_url = f"{QUEUE_URL}{shared_queue.pk}/"
        analytics = _result(auth_client.get(f"{shared_url}analytics/"))
        assert list(analytics["label_distribution"]) == [str(label.id)]
        item = QueueItem.no_workspace_objects.get(pk=shared_item.pk)
        exported = _scores_for_queue_items([item], [label.id])[item.id]
        assert [score.id for score in exported] == [scores[1].id]


@pytest.mark.django_db
@pytest.mark.integration
class TestStoredPreview:
    def test_stored_preview_of_a_misfiled_item_shows_the_deleted_source(
        self, auth_client, user, organization, workspace, span_copies
    ):
        misfiled = _misfiled_rows(
            user, organization, workspace, span_copies, captured=True
        )
        clear_workspace_context()
        queue, item = misfiled.queue, misfiled.item

        request = _request(user, organization, workspace)
        fresh = QueueItem.no_workspace_objects.get(pk=item.pk)
        serialized = QueueItemSerializer(fresh, context={"request": request}).data
        assert serialized["source_preview"] == UNAVAILABLE
        listed = auth_client.get(f"{QUEUE_URL}{queue.pk}/items/", {"limit": 50})
        assert listed.status_code == status.HTTP_200_OK, listed.data
        previews = [row["source_preview"] for row in listed.data["results"]]
        assert previews == [UNAVAILABLE]
        detail = auth_client.get(
            f"{QUEUE_URL}{queue.pk}/items/{item.pk}/annotate-detail/"
        )
        body = _result(detail)["item"]
        assert body["source_preview"] == UNAVAILABLE
        assert body["source_content"] == UNAVAILABLE
        name = misfiled.second.project.name
        for response in (listed, detail):
            for marker in (f"{name} copy", f"{name} input"):
                assert marker not in str(response.data), (marker, response.data)

    def test_stored_preview_of_an_item_of_the_organization_shows_that_preview(
        self, auth_client, user, organization, workspace, span_copies
    ):
        project = _project(organization, workspace, "First")
        span = _span(project, "First")
        copy = _hold(span_copies, project, span)
        # The stored preview differs from the live copy, so the answer shows
        # which of the two was returned.
        stored = {
            **preview_payload_for_source("observation_span", copy),
            "name": "stored name of the first organization's span",
        }
        clear_workspace_context()
        queue, item = _queue_item(
            organization, workspace, project, span, user, preview=stored
        )
        assert item.source_preview == stored

        request = _request(user, organization, workspace)
        fresh = QueueItem.no_workspace_objects.get(pk=item.pk)
        serialized = QueueItemSerializer(fresh, context={"request": request}).data
        assert serialized["source_preview"] == stored
        listed = auth_client.get(f"{QUEUE_URL}{queue.pk}/items/", {"limit": 50})
        assert listed.status_code == status.HTTP_200_OK, listed.data
        previews = [row["source_preview"] for row in listed.data["results"]]
        assert previews == [stored]


def _item_recording_no_project(organization, workspace, project, span, user, preview):
    """A default queue of ``organization`` over ``project`` holding ``span`` on
    an item that records no project, as an annotation made a default queue's
    item before such an item recorded its queue's project. The item keeps
    ``preview`` as its stored preview."""
    queue, item = _queue_item(
        organization, workspace, project, span, user, preview=preview
    )
    QueueItem.no_workspace_objects.filter(pk=item.pk).update(project=None)
    item.refresh_from_db()
    assert item.project_id is None
    assert queue.project_id == project.id
    assert item.source_preview == preview
    return queue, item


@pytest.mark.django_db
@pytest.mark.integration
class TestItemThatRecordsNoProject:
    def test_item_over_another_organizations_project_shows_the_deleted_source(
        self, auth_client, user, organization, workspace, span_copies
    ):
        second = _second_organization(user)
        copy = _hold(span_copies, second.project, second.span)
        # The first organization has a project and a span of its own, so the
        # item has projects in which its source is looked for.
        own_project = _project(organization, workspace, "First")
        _hold(span_copies, own_project, _span(own_project, "First"))
        # The second organization's evaluation of its span.
        EvalLogger.objects.create(
            observation_span_id=str(second.span.id),
            trace_id=second.span.trace_id,
            eval_type_id="toxicity",
            output_float=0.9,
            eval_explanation=EVAL_EXPLANATION,
        )
        clear_workspace_context()
        queue, item = _item_recording_no_project(
            organization,
            workspace,
            second.project,
            second.span,
            user,
            preview_payload_for_source("observation_span", copy),
        )
        assert queue.organization_id == item.organization_id == organization.id
        assert second.project.organization_id == second.org.id != organization.id

        # An item that records no project is looked for in the projects of
        # its queue's organization, and its source is in none of them.
        request = _request(user, organization, workspace)
        fresh = QueueItem.no_workspace_objects.get(pk=item.pk)
        reach = QueueSourceAuthorization.for_items([fresh], request=request)
        assert reach.project_ids(fresh) == {str(own_project.id)}
        serialized = QueueItemSerializer(fresh, context={"request": request}).data
        assert serialized["source_preview"] == UNAVAILABLE

        item_url = f"{QUEUE_URL}{queue.pk}/items/"
        listed = auth_client.get(item_url, {"limit": 50})
        assert listed.status_code == status.HTTP_200_OK, listed.data
        previews = [row["source_preview"] for row in listed.data["results"]]
        assert previews == [UNAVAILABLE]
        detail = auth_client.get(f"{item_url}{item.pk}/annotate-detail/")
        body = _result(detail)["item"]
        assert body["source_preview"] == UNAVAILABLE
        assert body["source_content"] == UNAVAILABLE
        answer = auth_client.get(f"{QUEUE_URL}{queue.pk}/export/")
        exported = _result(answer)
        assert [row["item_id"] for row in exported] == [str(item.id)]
        assert exported[0]["source"] == UNAVAILABLE
        assert exported[0]["evals"] == {}
        assert EVAL_EXPLANATION not in json.dumps(answer.data, default=str)
        name = second.project.name
        for response in (listed, detail, answer):
            for marker in (f"{name} copy", f"{name} input"):
                assert marker not in str(response.data), (marker, response.data)

    def test_item_in_the_organizations_own_default_queue_reads_as_before(
        self, auth_client, user, organization, workspace, span_copies
    ):
        project = _project(organization, workspace, "First")
        span = _span(project, "First")
        copy = _hold(span_copies, project, span)
        # The stored preview is the one the live copy gives, so the answer is
        # the same whether the stored or the live one is returned.
        stored = preview_payload_for_source("observation_span", copy)
        EvalLogger.objects.create(
            observation_span_id=str(span.id),
            trace_id=span.trace_id,
            eval_type_id="toxicity",
            output_float=0.9,
            eval_explanation=OWN_EVAL_EXPLANATION,
        )
        clear_workspace_context()
        queue, item = _item_recording_no_project(
            organization, workspace, project, span, user, stored
        )

        request = _request(user, organization, workspace)
        fresh = QueueItem.no_workspace_objects.get(pk=item.pk)
        reach = QueueSourceAuthorization.for_items([fresh], request=request)
        assert reach.project_ids(fresh) == {str(project.id)}
        serialized = QueueItemSerializer(fresh, context={"request": request}).data
        assert serialized["source_preview"] == stored

        item_url = f"{QUEUE_URL}{queue.pk}/items/"
        listed = auth_client.get(item_url, {"limit": 50})
        assert listed.status_code == status.HTTP_200_OK, listed.data
        previews = [row["source_preview"] for row in listed.data["results"]]
        assert previews == [stored]
        detail = auth_client.get(f"{item_url}{item.pk}/annotate-detail/")
        body = _result(detail)["item"]
        assert body["source_preview"] == stored
        _shows_copy(body["source_content"], project)
        exported = _result(auth_client.get(f"{QUEUE_URL}{queue.pk}/export/"))
        assert [row["item_id"] for row in exported] == [str(item.id)]
        _shows_copy(exported[0]["source"], project)
        assert list(exported[0]["evals"]) == ["toxicity"]
        toxicity = exported[0]["evals"]["toxicity"]
        assert toxicity["explanation"] == OWN_EVAL_EXPLANATION


@pytest.mark.django_db
@pytest.mark.integration
class TestQueueReadersJoinTheirProjects:
    def test_queue_score_and_note_reads_do_not_select_from_the_project_table(
        self, user, organization, workspace, span_copies
    ):
        rows = _own_rows(
            user, organization, workspace, span_copies, clickhouse_only=True
        )
        clear_workspace_context()
        item = QueueItem.no_workspace_objects.get(pk=rows.item.pk)

        # The readers the export and the item's views share.
        with CaptureQueriesContext(connection) as captured:
            exported = _scores_for_queue_items([item], [rows.label.id])[item.id]
            listed = list(_scores_listed_for_queue_item(item))
            latest_notes = _latest_item_notes_for_queue_items([item])
            note_rows = _item_note_rows(item)

        assert [score.id for score in exported] == [rows.score.id]
        assert [score.id for score in listed] == [rows.score.id]
        assert latest_notes == {item.id: OWN_NOTE}
        assert [note.notes for note in note_rows] == [OWN_NOTE]
        statements = [query["sql"] for query in captured.captured_queries]
        assert statements, "control: the reads were captured"
        # A check that joins the project names it after JOIN; one that looks
        # it up in a subquery selects from it.
        selecting = [sql for sql in statements if 'FROM "tracer_project"' in sql]
        assert selecting == [], selecting


def _default_queue(organization, workspace, project, user, label):
    """An active default queue of ``organization`` over ``project`` that
    carries ``label`` and holds no item, saved with ``workspace``."""
    queue = _stamp_workspace(
        _saved(
            AnnotationQueue(
                name=f"Default - {project.name}",
                is_default=True,
                status=AnnotationQueueStatusChoices.ACTIVE.value,
                organization=organization,
                workspace=workspace,
                project=project,
                created_by=user,
            )
        ),
        workspace,
    )
    _saved(AnnotationQueueLabel(queue=queue, label=label))
    return queue


def _bulk_annotate(client, span, label, rating):
    return client.post(
        BULK_ANNOTATION_URL,
        {
            "records": [
                {
                    "observation_span_id": span.id,
                    "annotations": [
                        {"annotation_label_id": str(label.id), "value_float": rating}
                    ],
                }
            ]
        },
        format="json",
    )


def _annotate_by(route, client, span, label, organization):
    """Annotate ``span`` under ``label`` in ``organization`` by ``route``: the
    helper that makes the default queues' items, called with the request's
    organization as the endpoints call it, or the bulk annotation endpoint,
    which saves a score and then calls the helper."""
    if route == "helper":
        # Called outside a request: no workspace narrows the managers.
        clear_workspace_context()
        _auto_create_queue_items_for_default_queues(
            "observation_span", span, [label.id], organization
        )
        return
    response = _bulk_annotate(client, span, label, 4)
    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data["result"]["annotations_created"] == 1, response.data
    clear_workspace_context()


@pytest.mark.django_db
@pytest.mark.integration
class TestDefaultQueueItemMadeOnAnnotation:
    """A project holds one default queue, so each case arranges its own."""

    @pytest.mark.parametrize("route", ["helper", "bulk-annotation"])
    def test_own_default_queue_gets_an_item_recording_its_project(
        self, auth_client, user, organization, workspace, route
    ):
        project = _project(organization, workspace, "First")
        span = _span(project, "First")
        label = _star_label(organization, workspace, project)
        clear_workspace_context()
        queue = _default_queue(organization, workspace, project, user, label)

        _annotate_by(route, auth_client, span, label, organization)

        item = QueueItem.no_workspace_objects.get(queue=queue)
        assert item.observation_span_id == span.id
        assert item.organization_id == organization.id
        assert item.project_id == project.id

    @pytest.mark.parametrize("route", ["helper", "bulk-annotation"])
    def test_another_organizations_default_queue_over_the_project_gets_no_item(
        self, auth_client, user, organization, workspace, route
    ):
        second = _second_organization(user)
        project = _project(organization, workspace, "First")
        span = _span(project, "First")
        label = _star_label(organization, workspace, project)
        clear_workspace_context()
        # The project's one default queue is the second organization's, and
        # carries the label. It is saved with the workspace the annotation
        # is made in, so a read narrowed by workspace alone still finds it.
        queue = _default_queue(second.org, workspace, project, user, label)
        assert queue.organization_id == second.org.id != project.organization_id
        assert queue.workspace_id == workspace.id

        _annotate_by(route, auth_client, span, label, organization)

        assert not QueueItem.no_workspace_objects.filter(queue=queue).exists()
        assert not QueueItem.no_workspace_objects.filter(
            observation_span_id=span.id
        ).exists()


def _add_label(client, queue, label):
    return client.post(
        f"{QUEUE_URL}{queue.pk}/add-label/", {"label_id": str(label.id)}, format="json"
    )


@pytest.mark.django_db
@pytest.mark.integration
class TestQueueAddsOnlyLabelsOfItsOrganization:
    @pytest.mark.parametrize(
        "label_workspace",
        [
            pytest.param("its-own", id="label-in-its-own-workspace"),
            pytest.param("the-queue's", id="label-in-the-queues-workspace"),
        ],
    )
    def test_label_of_another_organization_is_not_found(
        self, auth_client, user, organization, workspace, label_workspace
    ):
        second = _second_organization(user)
        clear_workspace_context()
        queue = _own_queue(organization, workspace, user)
        label = second.label
        if label_workspace == "the-queue's":
            # Still the second organization's label: only the workspace it
            # was saved with is the queue's.
            _stamp_workspace(label, workspace)
        assert label.organization_id == second.org.id != queue.organization_id

        response = _add_label(auth_client, queue, label)

        assert response.status_code == status.HTTP_404_NOT_FOUND, response.data
        assert "Label not found" in str(response.data)
        clear_workspace_context()
        assert not AnnotationQueueLabel.no_workspace_objects.filter(
            queue=queue
        ).exists()

    def test_label_of_the_queues_organization_is_added_as_before(
        self, auth_client, user, organization, workspace
    ):
        label = _star_label(organization, workspace, None)
        clear_workspace_context()
        queue = _own_queue(organization, workspace, user)

        result = _result(_add_label(auth_client, queue, label))

        assert result["created"] is True
        assert result["label"]["id"] == str(label.id)
        clear_workspace_context()
        added = AnnotationQueueLabel.no_workspace_objects.filter(queue=queue)
        assert [row.label_id for row in added] == [label.id]
