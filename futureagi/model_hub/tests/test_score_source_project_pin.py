"""Scores written from a detail drawer land on the copy the drawer showed.

A trace / span id can exist in several projects: a replay or re-import of the
same corpus, or a voice provider account shared by several projects (on dev one
call is held by 20 projects across ~10 organizations). ``/model-hub/scores/bulk/``
takes the drawer's ``project_id`` and writes the score to that project's copy.
Without it the source resolves to the newest copy inside the caller's projects,
never to another organization's copy (which the tenant gate then refused as
"Source not found"). A pin outside the caller's scope answers like a missing
source.

The read side is the twin: ``/model-hub/scores/for-source/`` keys on the bare id,
so the drawer of one copy listed every copy's scores. It takes the same pin and
lists only that project's scores; a pin outside the caller's scope answers like a
source with no scores.

The ClickHouse reader is faked with per-project copies; Postgres scope, the view
and the resolver run for real.
"""

import uuid
from types import SimpleNamespace

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from tracer.models.project import Project
from tracer.models.span_notes import SpanNotes
from tracer.services.clickhouse.v2.span_reader import SpanScope

BULK_URL = "/model-hub/scores/bulk/"
FOR_SOURCE_URL = "/model-hub/scores/for-source/"
SPAN_ID = "0d54f84ac36455d6"


def _project(organization, workspace, name):
    return Project.objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


class _Copies:
    """``get_reader()`` stand-in over per-project copies of one root span.

    Honours the real reader's scope kwargs and answers newest write first, as
    its SQL does; an unscoped ``get`` returns the newest copy of any tenant.
    """

    def __init__(self, copies):
        self.copies = copies

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _scoped(self, project_id=None, project_ids=None):
        rows = [
            row
            for row in self.copies
            if (not project_id or row.project_id == str(project_id))
            and (project_ids is None or row.project_id in project_ids)
        ]
        return sorted(rows, key=lambda row: row.version, reverse=True)

    def get(self, span_id, *, project_id=None, project_ids=None):
        rows = [
            row
            for row in self._scoped(project_id, project_ids)
            if row.id == str(span_id)
        ]
        return rows[0] if rows else None

    def newest_trace_project(self, trace_id, project_ids):
        rows = [
            row
            for row in self._scoped(project_ids=project_ids)
            if row.trace_id == str(trace_id)
        ]
        return rows[0].project_id if rows else None

    def roots_by_trace_ids(self, trace_ids, *, project_id=None, **_):
        return [
            row
            for row in self._scoped(project_id)
            if row.trace_id in {str(trace_id) for trace_id in trace_ids}
        ]

    def scope_by_ids(self, span_ids, *, project_ids=None):
        # Unscoped, the real read keeps one arbitrary copy per id; the newest
        # write of any tenant (here another organization's) stands in for it.
        wanted = {str(span_id) for span_id in span_ids}
        scopes = {}
        for row in self._scoped(project_ids=project_ids):
            if row.id in wanted:
                scopes.setdefault(
                    row.id, SpanScope(project_id=row.project_id, trace_id=row.trace_id)
                )
        return scopes


@pytest.fixture
def replayed_span(user, workspace, monkeypatch):
    """One root span copied into two of the caller's projects and a foreign one;
    the foreign copy is the newest write overall."""

    older = _project(user.organization, workspace, "older")
    newer = _project(user.organization, workspace, "newer")
    empty = _project(user.organization, workspace, "empty")
    foreign_org = Organization.objects.create(name=f"Foreign {uuid.uuid4().hex[:8]}")
    foreign_workspace = Workspace.no_workspace_objects.create(
        name="Foreign Workspace",
        organization=foreign_org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    foreign = _project(foreign_org, foreign_workspace, "foreign")
    trace_id = str(uuid.uuid4())
    copies = [
        SimpleNamespace(
            id=SPAN_ID,
            pk=SPAN_ID,
            project_id=str(project.id),
            org_id=str(project.organization_id),
            trace_id=trace_id,
            parent_span_id="",
            observation_type="conversation",
            status="OK",
            version=version,
        )
        for project, version in ((older, 10), (newer, 20), (foreign, 30))
    ]
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.get_reader", lambda: _Copies(copies)
    )
    label = AnnotationsLabels.objects.create(
        name="Quality Rating",
        type=AnnotationTypeChoices.STAR.value,
        settings={"no_of_stars": 5},
        organization=user.organization,
        workspace=workspace,
        project=older,
    )
    return SimpleNamespace(
        older=older,
        newer=newer,
        empty=empty,
        foreign=foreign,
        trace_id=trace_id,
        label=label,
    )


def _bulk(auth_client, replayed_span, source_type, **extra):
    source_id = SPAN_ID if source_type == "observation_span" else replayed_span.trace_id
    return auth_client.post(
        BULK_URL,
        {
            "source_type": source_type,
            "source_id": source_id,
            "scores": [
                {"label_id": str(replayed_span.label.id), "value": {"rating": 4}}
            ],
            **extra,
        },
        format="json",
    )


SOURCE_TYPES = ["observation_span", "trace"]


@pytest.mark.django_db
@pytest.mark.parametrize("source_type", SOURCE_TYPES)
def test_pinned_score_lands_on_the_pinned_copy(auth_client, replayed_span, source_type):
    older = replayed_span.older.id

    response = _bulk(auth_client, replayed_span, source_type, project_id=str(older))

    assert response.status_code == status.HTTP_200_OK, response.content
    score = Score.objects.get(label=replayed_span.label, deleted=False)
    assert str(score.tracer_project_id) == str(older)


@pytest.mark.django_db
@pytest.mark.parametrize("source_type", SOURCE_TYPES)
def test_unpinned_score_resolves_to_the_newest_copy_in_scope(
    auth_client, replayed_span, source_type
):
    response = _bulk(auth_client, replayed_span, source_type)

    assert response.status_code == status.HTTP_200_OK, response.content
    score = Score.objects.get(label=replayed_span.label, deleted=False)
    # The foreign organization's copy is newer, but never a candidate.
    assert str(score.tracer_project_id) == str(replayed_span.newer.id)


@pytest.mark.django_db
@pytest.mark.parametrize("source_type", SOURCE_TYPES)
@pytest.mark.parametrize("pin", ["foreign", "unknown", "empty"])
def test_pin_outside_scope_or_without_the_id_answers_like_a_missing_source(
    auth_client, replayed_span, source_type, pin
):
    project_id = {
        "foreign": str(replayed_span.foreign.id),
        "unknown": str(uuid.uuid4()),
        "empty": str(replayed_span.empty.id),
    }[pin]
    missing = auth_client.post(
        BULK_URL,
        {
            "source_type": source_type,
            "source_id": "missing"
            if source_type == "observation_span"
            else str(uuid.uuid4()),
            "scores": [
                {"label_id": str(replayed_span.label.id), "value": {"rating": 4}}
            ],
        },
        format="json",
    )

    response = _bulk(auth_client, replayed_span, source_type, project_id=project_id)

    assert missing.status_code == status.HTTP_404_NOT_FOUND
    assert (response.status_code, response.data["code"]) == (
        missing.status_code,
        missing.data["code"],
    )
    assert "Source not found" in response.data["detail"]
    assert not Score.objects.filter(label=replayed_span.label).exists()


def _for_source(auth_client, replayed_span, source_type, **extra):
    source_id = SPAN_ID if source_type == "observation_span" else replayed_span.trace_id
    return auth_client.get(
        FOR_SOURCE_URL, {"source_type": source_type, "source_id": source_id, **extra}
    )


def _score_on_each_copy(auth_client, replayed_span, source_type):
    """One score on the older copy and one on the newer copy, as the drawers of
    the two projects write them; returns ``{project: score id}``."""
    for project in (replayed_span.older, replayed_span.newer):
        response = _bulk(
            auth_client, replayed_span, source_type, project_id=str(project.id)
        )
        assert response.status_code == status.HTTP_200_OK, response.content
    return {
        project: str(
            Score.objects.get(
                label=replayed_span.label, tracer_project_id=project.id, deleted=False
            ).id
        )
        for project in (replayed_span.older, replayed_span.newer)
    }


@pytest.mark.django_db
@pytest.mark.parametrize("source_type", SOURCE_TYPES)
def test_pinned_read_lists_only_the_pinned_copys_scores(
    auth_client, replayed_span, source_type
):
    score_ids = _score_on_each_copy(auth_client, replayed_span, source_type)

    for project, score_id in score_ids.items():
        response = _for_source(
            auth_client, replayed_span, source_type, project_id=str(project.id)
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        assert [row["id"] for row in response.data["result"]] == [score_id]


@pytest.mark.django_db
@pytest.mark.parametrize("source_type", SOURCE_TYPES)
def test_unpinned_read_still_lists_every_copy_in_scope(
    auth_client, replayed_span, source_type
):
    score_ids = _score_on_each_copy(auth_client, replayed_span, source_type)

    response = _for_source(auth_client, replayed_span, source_type)

    assert response.status_code == status.HTTP_200_OK, response.content
    assert sorted(row["id"] for row in response.data["result"]) == sorted(
        score_ids.values()
    )


@pytest.mark.django_db
@pytest.mark.parametrize("source_type", SOURCE_TYPES)
@pytest.mark.parametrize("pin", ["foreign", "unknown", "empty"])
def test_pinned_read_outside_scope_or_without_scores_answers_like_no_scores(
    auth_client, replayed_span, source_type, pin
):
    _score_on_each_copy(auth_client, replayed_span, source_type)
    project_id = {
        "foreign": str(replayed_span.foreign.id),
        "unknown": str(uuid.uuid4()),
        "empty": str(replayed_span.empty.id),
    }[pin]
    unscored = auth_client.get(
        FOR_SOURCE_URL,
        {
            "source_type": source_type,
            "source_id": "unscored"
            if source_type == "observation_span"
            else str(uuid.uuid4()),
        },
    )

    response = _for_source(
        auth_client, replayed_span, source_type, project_id=project_id
    )

    assert unscored.status_code == status.HTTP_200_OK
    assert unscored.data["result"] == []
    assert (response.status_code, response.data) == (
        unscored.status_code,
        unscored.data,
    )


@pytest.mark.django_db
def test_pinned_span_read_reads_notes_from_the_pinned_copy(
    auth_client, user, replayed_span
):
    # Unscoped, the span's scope comes back as the foreign organization's copy
    # and the caller's own notes were hidden; the pin reads its own copy.
    SpanNotes.objects.create(
        span_id=SPAN_ID,
        notes="checked the refund policy",
        created_by_user=user,
        created_by_annotator=user.email,
    )

    response = _for_source(
        auth_client,
        replayed_span,
        "observation_span",
        project_id=str(replayed_span.older.id),
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    assert [note["notes"] for note in response.data["span_notes"]] == [
        "checked the refund policy"
    ]


@pytest.mark.django_db
def test_pin_leaves_session_scores_unfiltered(auth_client, user, replayed_span):
    # A session id is derived from (project, name), so it cannot repeat across
    # projects, and session scores written before tracer_project_id existed
    # carry none (dev: 56 of 56). The pin only narrows trace / span reads.
    session_id = uuid.uuid4()
    score = Score.objects.create(
        source_type="trace_session",
        trace_session_id=session_id,
        label=replayed_span.label,
        value={"rating": 3},
        annotator=user,
        organization=user.organization,
        workspace=replayed_span.older.workspace,
    )

    response = auth_client.get(
        FOR_SOURCE_URL,
        {
            "source_type": "trace_session",
            "source_id": str(session_id),
            "project_id": str(replayed_span.older.id),
        },
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    assert [row["id"] for row in response.data["result"]] == [str(score.id)]
