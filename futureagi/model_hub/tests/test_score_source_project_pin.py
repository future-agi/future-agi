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
source with no scores. Rows written before the project was recorded (a NULL
``Score.tracer_project_id`` / ``QueueItem.project``) were never attributed to a
copy, so every in-scope copy keeps them.

The ClickHouse reader is faked with per-project copies; Postgres scope, the view
and the resolver run for real.
"""

import uuid
from types import SimpleNamespace

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueLabel,
    AnnotationQueueStatusChoices,
    QueueItem,
    QueueItemNote,
)
from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from tfc.constants.roles import OrganizationRoles
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

    def root_ids_by_trace_ids(self, trace_ids, project_ids=None):
        # The real read treats an empty project list as unscoped, as here.
        wanted = {str(trace_id) for trace_id in trace_ids}
        roots = {}
        for row in self._scoped(project_ids=project_ids or None):
            if row.trace_id in wanted:
                roots.setdefault(row.trace_id, (row.id, row.project_id))
        return roots


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
        # In scope, so it would still list unattributed (NULL-project) scores;
        # only the copies' attributed scores are seeded here.
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


def _legacy_score(user, replayed_span, source_type):
    """A score written before ``tracer_project_id`` was populated: never
    attributed to a copy (dev: 371 of 380 live span scores, 84 of 179 trace)."""
    source = (
        {"observation_span_id": SPAN_ID}
        if source_type == "observation_span"
        else {"trace_id": replayed_span.trace_id}
    )
    return Score.objects.create(
        source_type=source_type,
        label=replayed_span.label,
        value={"rating": 2},
        annotator=user,
        organization=user.organization,
        workspace=replayed_span.older.workspace,
        **source,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("source_type", SOURCE_TYPES)
def test_pinned_read_keeps_legacy_scores_without_a_project(
    auth_client, user, replayed_span, source_type
):
    score_ids = _score_on_each_copy(auth_client, replayed_span, source_type)
    legacy = _legacy_score(user, replayed_span, source_type)

    response = _for_source(
        auth_client,
        replayed_span,
        source_type,
        project_id=str(replayed_span.older.id),
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    assert sorted(row["id"] for row in response.data["result"]) == sorted(
        [score_ids[replayed_span.older], str(legacy.id)]
    )


@pytest.mark.django_db
@pytest.mark.parametrize("source_type", SOURCE_TYPES)
@pytest.mark.parametrize("pin", ["foreign", "unknown"])
def test_pinned_read_outside_scope_hides_legacy_scores_too(
    auth_client, user, replayed_span, source_type, pin
):
    _legacy_score(user, replayed_span, source_type)
    project_id = {
        "foreign": str(replayed_span.foreign.id),
        "unknown": str(uuid.uuid4()),
    }[pin]

    response = _for_source(
        auth_client, replayed_span, source_type, project_id=project_id
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    assert response.data["result"] == []


@pytest.mark.django_db
@pytest.mark.parametrize("source_type", SOURCE_TYPES)
def test_pinned_span_read_hides_another_copys_queue_note(
    auth_client, user, replayed_span, source_type
):
    # The caller notes the newer copy from its drawer: a span note, or a call
    # note on the trace (its span notes live on the root span).
    extra = {"span_notes": "newer copy: refund was issued"}
    if source_type == "trace":
        extra["span_notes_source_id"] = SPAN_ID
    response = _bulk(
        auth_client,
        replayed_span,
        source_type,
        project_id=str(replayed_span.newer.id),
        **extra,
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    # A queue note from before QueueItem.project existed, by another annotator:
    # never attributed to a copy, so every copy's drawer keeps it.
    colleague = User.objects.create_user(
        email=f"colleague-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Colleague",
        organization=user.organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    legacy_queue = AnnotationQueue.objects.create(
        name="Legacy review",
        status=AnnotationQueueStatusChoices.ACTIVE.value,
        organization=user.organization,
        workspace=replayed_span.older.workspace,
        created_by=user,
    )
    legacy_item = QueueItem.objects.create(
        queue=legacy_queue,
        source_type="observation_span",
        observation_span_id=SPAN_ID,
        organization=user.organization,
        workspace=replayed_span.older.workspace,
    )
    QueueItemNote.objects.create(
        queue_item=legacy_item,
        annotator=colleague,
        notes="legacy: escalated to billing",
        organization=user.organization,
        workspace=replayed_span.older.workspace,
    )

    def notes(project):
        response = _for_source(
            auth_client, replayed_span, "observation_span", project_id=str(project.id)
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        return sorted(note["notes"] for note in response.data["span_notes"])

    # The newer copy's note must not reach the older copy's drawer, neither as
    # a queue note nor through its legacy SpanNotes mirror.
    assert notes(replayed_span.older) == ["legacy: escalated to billing"]
    assert notes(replayed_span.newer) == [
        "legacy: escalated to billing",
        "newer copy: refund was issued",
    ]


# ---------------------------------------------------------------------------
# Queue sections and queue-item saves in a pinned drawer.
#
# The drawer's Annotate sidebar lists the queues holding the source through
# ``/model-hub/annotation-queues/for-source/``, which keyed on the bare source
# id: with the trace held by two projects, the older copy's drawer showed the
# newer copy's default queue beside its own, prefilled with that copy's score.
# Saving in that section sent the other copy's queue item under this drawer's
# pin; the save found the other copy's score through its queue item and moved
# it into this project (``tracer_project_id`` older <- newer), leaving the
# older copy with two scores for the label and the newer copy with none.
# ---------------------------------------------------------------------------

QUEUE_FOR_SOURCE_URL = "/model-hub/annotation-queues/for-source/"
SCORES_URL = "/model-hub/scores/"


def _save(auth_client, replayed_span, source_type, project, rating, **extra):
    """Save the label from ``project``'s drawer; returns the response."""
    source_id = SPAN_ID if source_type == "observation_span" else replayed_span.trace_id
    return auth_client.post(
        BULK_URL,
        {
            "source_type": source_type,
            "source_id": source_id,
            "project_id": str(project.id),
            "scores": [
                {
                    "label_id": str(replayed_span.label.id),
                    "value": {"rating": rating},
                }
            ],
            **extra,
        },
        format="json",
    )


def _default_item_on_each_copy(auth_client, replayed_span, source_type):
    """Each copy's drawer scores the label (older 2, newer 5): each copy's
    default queue gets an item and a score. Returns ``{project: item}``."""
    items = {}
    for project, rating in ((replayed_span.older, 2), (replayed_span.newer, 5)):
        response = _save(auth_client, replayed_span, source_type, project, rating)
        assert response.status_code == status.HTTP_200_OK, response.content
        items[project] = QueueItem.objects.get(
            queue__project=project, queue__is_default=True, deleted=False
        )
        # The save's on-commit hook attaches the label to the default queue;
        # the test transaction never commits, so attach it here.
        AnnotationQueueLabel.objects.create(
            queue=items[project].queue, label=replayed_span.label
        )
    return items


def _queues_for_drawer(auth_client, replayed_span, **extra):
    """The sidebar request of a trace drawer: the trace and its root span."""
    import json

    sources = [
        {"source_type": "trace", "source_id": replayed_span.trace_id},
        {"source_type": "observation_span", "source_id": SPAN_ID},
    ]
    response = auth_client.get(
        QUEUE_FOR_SOURCE_URL, {"sources": json.dumps(sources), **extra}
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    return response.data["result"]


def _score_of(item):
    return Score.objects.get(queue_item=item, deleted=False)


@pytest.mark.django_db
@pytest.mark.parametrize("source_type", SOURCE_TYPES)
def test_unpinned_queue_read_lists_every_copys_queue(
    auth_client, replayed_span, source_type
):
    # What the drawer asked before it sent its project: both copies' default
    # queues, each prefilled with its own copy's score.
    items = _default_item_on_each_copy(auth_client, replayed_span, source_type)

    entries = _queues_for_drawer(auth_client, replayed_span)

    label = str(replayed_span.label.id)
    assert sorted(
        (entry["item"]["id"], entry["existing_scores"][label]["rating"])
        for entry in entries
    ) == sorted(
        [
            (str(items[replayed_span.older].id), 2),
            (str(items[replayed_span.newer].id), 5),
        ]
    )


@pytest.mark.django_db
@pytest.mark.parametrize("source_type", SOURCE_TYPES)
def test_pinned_queue_read_lists_only_the_pinned_copys_queue(
    auth_client, replayed_span, source_type
):
    items = _default_item_on_each_copy(auth_client, replayed_span, source_type)
    label = str(replayed_span.label.id)

    for project, rating in ((replayed_span.older, 2), (replayed_span.newer, 5)):
        entries = _queues_for_drawer(
            auth_client, replayed_span, project_id=str(project.id)
        )

        assert [
            (entry["item"]["id"], entry["existing_scores"][label]["rating"])
            for entry in entries
        ] == [(str(items[project].id), rating)]


def _legacy_queue_item(user, replayed_span, source_type):
    """An item of a queue with no project, added before ``QueueItem.project``
    existed: never attributed to a copy."""
    queue = AnnotationQueue.objects.create(
        name="Legacy review",
        status=AnnotationQueueStatusChoices.ACTIVE.value,
        organization=user.organization,
        workspace=replayed_span.older.workspace,
        created_by=user,
    )
    source = (
        {"observation_span_id": SPAN_ID}
        if source_type == "observation_span"
        else {"trace_id": replayed_span.trace_id}
    )
    return QueueItem.objects.create(
        queue=queue,
        source_type=source_type,
        organization=user.organization,
        workspace=replayed_span.older.workspace,
        **source,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("source_type", SOURCE_TYPES)
def test_pinned_queue_read_keeps_legacy_items_without_a_project(
    auth_client, user, replayed_span, source_type
):
    items = _default_item_on_each_copy(auth_client, replayed_span, source_type)
    legacy = _legacy_queue_item(user, replayed_span, source_type)

    for project in (replayed_span.older, replayed_span.newer):
        entries = _queues_for_drawer(
            auth_client, replayed_span, project_id=str(project.id)
        )

        assert sorted(entry["item"]["id"] for entry in entries) == sorted(
            [str(items[project].id), str(legacy.id)]
        )


@pytest.mark.django_db
@pytest.mark.parametrize("pin", ["foreign", "unknown"])
def test_pinned_queue_read_outside_scope_lists_no_queue(
    auth_client, user, replayed_span, pin
):
    _default_item_on_each_copy(auth_client, replayed_span, "trace")
    _legacy_queue_item(user, replayed_span, "trace")
    project_id = {
        "foreign": str(replayed_span.foreign.id),
        "unknown": str(uuid.uuid4()),
    }[pin]

    assert _queues_for_drawer(auth_client, replayed_span, project_id=project_id) == []


@pytest.mark.django_db
def test_pinned_queue_read_offers_the_pinned_copys_default_queue_without_an_item(
    auth_client, user, replayed_span
):
    # Only the newer copy has been annotated; the older copy's default queue
    # exists (it holds other traces) but has no item for this one yet. Its
    # drawer offers its own default queue, never the newer copy's.
    _save(auth_client, replayed_span, "trace", replayed_span.newer, 5)
    older_default = AnnotationQueue.objects.create(
        name="Default - older",
        is_default=True,
        project=replayed_span.older,
        status=AnnotationQueueStatusChoices.ACTIVE.value,
        organization=user.organization,
        workspace=replayed_span.older.workspace,
        created_by=user,
    )

    entries = _queues_for_drawer(
        auth_client, replayed_span, project_id=str(replayed_span.older.id)
    )

    assert [(entry["queue"]["id"], entry["item"]) for entry in entries] == [
        (str(older_default.id), None)
    ]


def _assert_untouched(score, *, rating, project):
    score.refresh_from_db()
    assert (score.value, str(score.tracer_project_id), score.deleted) == (
        {"rating": rating},
        str(project.id),
        False,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("source_type", SOURCE_TYPES)
def test_save_with_another_copys_queue_item_is_refused(
    auth_client, replayed_span, source_type
):
    items = _default_item_on_each_copy(auth_client, replayed_span, source_type)
    newer_score = _score_of(items[replayed_span.newer])

    # The older copy's drawer saves in the newer copy's queue section.
    response = _save(
        auth_client,
        replayed_span,
        source_type,
        replayed_span.older,
        4,
        queue_item_id=str(items[replayed_span.newer].id),
    )

    assert response.status_code == status.HTTP_409_CONFLICT, response.content
    assert response.data["code"] == "score_project_mismatch"
    _assert_untouched(newer_score, rating=5, project=replayed_span.newer)
    assert Score.objects.filter(label=replayed_span.label, deleted=False).count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize("source_type", SOURCE_TYPES)
def test_single_create_with_another_copys_queue_item_is_refused(
    auth_client, replayed_span, source_type
):
    # POST /scores/ takes no pin: the source resolves to the newest copy in
    # scope (the newer project), so the older copy's queue item is foreign.
    items = _default_item_on_each_copy(auth_client, replayed_span, source_type)
    older_score = _score_of(items[replayed_span.older])

    response = auth_client.post(
        SCORES_URL,
        {
            "source_type": source_type,
            "source_id": SPAN_ID
            if source_type == "observation_span"
            else replayed_span.trace_id,
            "label_id": str(replayed_span.label.id),
            "value": {"rating": 4},
            "queue_item_id": str(items[replayed_span.older].id),
        },
        format="json",
    )

    assert response.status_code == status.HTTP_409_CONFLICT, response.content
    assert response.data["code"] == "score_project_mismatch"
    _assert_untouched(older_score, rating=2, project=replayed_span.older)


@pytest.mark.django_db
@pytest.mark.parametrize("source_type", SOURCE_TYPES)
def test_save_with_own_queue_item_updates_own_score(
    auth_client, replayed_span, source_type
):
    items = _default_item_on_each_copy(auth_client, replayed_span, source_type)
    older_score = _score_of(items[replayed_span.older])
    newer_score = _score_of(items[replayed_span.newer])

    response = _save(
        auth_client,
        replayed_span,
        source_type,
        replayed_span.older,
        4,
        queue_item_id=str(items[replayed_span.older].id),
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    _assert_untouched(older_score, rating=4, project=replayed_span.older)
    _assert_untouched(newer_score, rating=5, project=replayed_span.newer)


@pytest.mark.django_db
@pytest.mark.parametrize("source_type", SOURCE_TYPES)
def test_save_through_a_legacy_item_is_attributed_to_the_pin(
    auth_client, user, replayed_span, source_type
):
    legacy = _legacy_queue_item(user, replayed_span, source_type)

    response = _save(
        auth_client,
        replayed_span,
        source_type,
        replayed_span.older,
        3,
        queue_item_id=str(legacy.id),
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    _assert_untouched(_score_of(legacy), rating=3, project=replayed_span.older)


@pytest.mark.django_db
@pytest.mark.parametrize("source_type", SOURCE_TYPES)
def test_save_through_a_legacy_item_never_moves_another_copys_score(
    auth_client, user, replayed_span, source_type
):
    # A queue item with no project is shared by every copy, but its score was
    # written from the newer copy's drawer and belongs to that copy.
    legacy = _legacy_queue_item(user, replayed_span, source_type)
    response = _save(
        auth_client,
        replayed_span,
        source_type,
        replayed_span.newer,
        5,
        queue_item_id=str(legacy.id),
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    newer_score = _score_of(legacy)

    response = _save(
        auth_client,
        replayed_span,
        source_type,
        replayed_span.older,
        2,
        queue_item_id=str(legacy.id),
    )

    assert response.status_code == status.HTTP_409_CONFLICT, response.content
    assert response.data["code"] == "score_project_mismatch"
    _assert_untouched(newer_score, rating=5, project=replayed_span.newer)
