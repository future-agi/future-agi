"""W1h: legacy notes need their own PG anchor, not a colliding CH locator.

Real ORM, authorization and endpoints; only the ClickHouse reader is replaced.
Defect assertions have fixed messages. Positive controls are explicitly named.
Database RED/GREEN execution belongs to the integration venue, not this writer.
"""

import json
import uuid
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from conftest import WorkspaceAwareAPIClient
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueLabel,
    QueueItem,
    QueueItemNote,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.tests.test_annotation_queues_api import create_workspace_member_user
from model_hub.tests.test_queue_item_source_copy_scope import _Copies, _copy
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.span_notes import SpanNotes
from tracer.models.trace import Trace

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]

OWNERS = (
    "local",
    "same_workspace",
    "sibling",
    "foreign",
    "null_workspace",
    "deleted",
    "orphan",
    "missing",
)


class _NoteCopies(_Copies):
    """Include the lean CH projections used by default-queue discovery."""

    def scope_by_ids(self, span_ids, *, project_ids=None):
        found = {}
        for span in self._scoped(project_ids=project_ids):
            if span.id in span_ids:
                found.setdefault(span.id, span)
        return found

    def root_ids_by_trace_ids(self, trace_ids, *, project_ids=None):
        found = {}
        for span in self._scoped(project_ids=project_ids):
            if span.trace_id in trace_ids:
                found.setdefault(span.trace_id, (span.id, span.project_id))
        return found


@pytest.fixture
def w1h_world(organization, workspace, user):
    clear_workspace_context()
    foreign_org = Organization.objects.create(name="W1H foreign organization")
    foreign_workspace = Workspace.no_workspace_objects.create(
        name="W1H foreign workspace",
        organization=foreign_org,
        created_by=user,
        is_active=True,
        is_default=True,
    )
    sibling_workspace = Workspace.no_workspace_objects.create(
        name="W1H sibling workspace",
        organization=organization,
        created_by=user,
        is_active=True,
        is_default=False,
    )
    projects = {}
    for name, org, ws in (
        ("local", organization, workspace),
        ("same_workspace", organization, workspace),
        ("sibling", organization, sibling_workspace),
        ("foreign", foreign_org, foreign_workspace),
        ("null_workspace", organization, None),
    ):
        projects[name] = Project.no_workspace_objects.create(
            name=f"W1H {name} project",
            organization=org,
            workspace=ws,
            model_type="GenerativeLLM",
            trace_type="observe",
        )
    queue = AnnotationQueue.no_workspace_objects.create(
        name="W1H queue",
        status="active",
        organization=organization,
        workspace=workspace,
        project=projects["local"],
        created_by=user,
    )
    label = AnnotationsLabels.no_workspace_objects.create(
        name="W1H quality",
        type="text",
        organization=organization,
        workspace=workspace,
        settings={"placeholder": "", "min_length": 0, "max_length": 1000},
    )
    AnnotationQueueLabel.no_workspace_objects.create(queue=queue, label=label)
    author = create_workspace_member_user(organization, workspace)
    assert user.can_access_workspace(workspace), "SETUP_AQ-XWS-W1H: local actor"
    assert not user.can_access_workspace(foreign_workspace), (
        "SETUP_AQ-XWS-W1H: foreign organization is inaccessible"
    )
    assert projects["null_workspace"].workspace_id is None, (
        "SETUP_AQ-XWS-W1H: null-workspace project is not backfilled"
    )
    return SimpleNamespace(
        queue=queue, projects=projects, user=user, author=author, label=label
    )


def _cases(world, monkeypatch, source_type, *, pinned=True, note_author=None):
    """Every locator has a local CH copy; PG ownership varies independently."""
    cases, copies = {}, []
    for index, owner in enumerate(OWNERS):
        span = _copy(
            world.projects["local"],
            trace_id=str(uuid.uuid4()),
            span_id=uuid.uuid4().hex[:16],
            second=index,
        )
        copies.append((index, span))
        if owner not in {"missing", "orphan"}:
            project = world.projects["local" if owner == "deleted" else owner]
            trace = Trace.no_workspace_objects.create(
                id=span.trace_id, project=project, name=f"W1H {owner} trace"
            )
            pg_span = ObservationSpan.no_workspace_objects.create(
                id=span.id,
                project=project,
                trace=trace,
                name=f"W1H {owner} span",
                observation_type="conversation",
                status="OK",
            )
            if owner == "deleted":
                ObservationSpan.no_workspace_objects.filter(pk=pg_span.pk).update(
                    deleted=True
                )
            if owner == "foreign":
                assert project.organization_id != world.queue.organization_id, (
                    "SETUP_AQ-XWS-W1H: foreign PG anchor collides with local CH id"
                )
        else:
            assert not ObservationSpan.all_objects.filter(pk=span.id).exists(), (
                "SETUP_AQ-XWS-W1H: absent PG anchor really is absent"
            )
        note = None
        if owner != "missing" and note_author is not None:
            note = SpanNotes.no_workspace_objects.create(
                span_id=span.id,
                notes=f"W1H {owner} legacy text",
                created_by_user=note_author,
                created_by_annotator=note_author.email,
            )
        item = QueueItem.no_workspace_objects.create(
            queue=world.queue,
            organization_id=world.queue.organization_id,
            workspace_id=world.queue.workspace_id,
            project=world.projects["local"] if pinned else None,
            source_type=source_type,
            **{
                f"{source_type}_id": (
                    span.trace_id if source_type == "trace" else span.id
                )
            },
        )
        cases[owner] = SimpleNamespace(span=span, item=item, note=note)
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.get_reader", lambda: _NoteCopies(copies)
    )
    return cases


def _read_notes(client, case, entrypoint):
    item, span = case.item, case.span
    if entrypoint == "annotate-detail":
        response = client.get(
            f"/model-hub/annotation-queues/{item.queue_id}/items/{item.pk}/annotate-detail/"
        )
        assert response.status_code == 200, "SETUP_AQ-XWS-W1H: detail succeeds"
        body = response.data.get("result", response.data)
        assert "deleted" not in body["item"]["source_content"], (
            "SETUP_AQ-XWS-W1H: authorized CH source remains available"
        )
    else:
        response = client.get(
            "/model-hub/annotation-queues/for-source/",
            {
                "sources": json.dumps(
                    [
                        {
                            "source_type": item.source_type,
                            "source_id": (
                                span.trace_id
                                if item.source_type == "trace"
                                else span.id
                            ),
                            "span_notes_source_id": span.id,
                        }
                    ]
                )
            },
        )
        assert response.status_code == 200, "SETUP_AQ-XWS-W1H: for-source succeeds"
        rows = response.data.get("result", response.data)
        body = next(row for row in rows if row["queue"]["id"] == str(item.queue_id))
    assert body["span_notes_source_id"] == span.id, (
        "SETUP_AQ-XWS-W1H: response refers to the requested local CH copy"
    )
    return body["span_notes"], body["existing_notes"]


@pytest.mark.parametrize("source_type", ["observation_span", "trace"])
@pytest.mark.parametrize("entrypoint", ["annotate-detail", "for-source"])
@pytest.mark.parametrize("pinned", [True, False])
def test_legacy_notes_require_their_own_exact_pg_anchor(
    w1h_world, auth_client, monkeypatch, source_type, entrypoint, pinned
):
    world = w1h_world
    cases = _cases(
        world, monkeypatch, source_type, pinned=pinned, note_author=world.author
    )
    shapes = {
        owner: _read_notes(auth_client, case, entrypoint)
        for owner, case in cases.items()
    }
    local_notes, local_editable = shapes["local"]
    assert (
        len(local_notes) == 1
        and local_notes[0]["notes"] == "W1H local legacy text"
        and local_notes[0]["annotator_id"] == str(world.author.pk)
        and local_notes[0]["annotator"] == world.author.email
        and local_editable == ""
    ), "AQ-XWS-W1H: positive control retains same-project legacy text and author"
    assert all(
        shapes[owner] == shapes["missing"] == ([], "") for owner in OWNERS[1:]
    ), (
        "AQ-XWS-W1H: legacy readers must identically withhold foreign deleted and missing PG anchors"
    )


@pytest.mark.parametrize("source_type", ["observation_span", "trace"])
@pytest.mark.parametrize("operation", ["create", "update", "clear"])
def test_note_mirror_refuses_foreign_and_absent_anchors_identically(
    w1h_world, auth_client, monkeypatch, source_type, operation
):
    world = w1h_world
    cases = _cases(
        world,
        monkeypatch,
        source_type,
        note_author=world.user if operation != "create" else None,
    )
    before = {
        owner: list(
            SpanNotes.no_workspace_objects.filter(span_id=case.span.id).values_list(
                "id", "notes", "created_by_user_id"
            )
        )
        for owner, case in cases.items()
    }
    responses, after = {}, {}
    text = "" if operation == "clear" else "W1H submitted queue note"
    for owner, case in cases.items():
        response = auth_client.post(
            f"/model-hub/annotation-queues/{world.queue.pk}/items/{case.item.pk}/annotations/submit/",
            {
                "annotations": [
                    {"label_id": str(world.label.pk), "value": {"text": "accepted"}}
                ],
                "item_notes": text,
            },
            format="json",
        )
        responses[owner] = (response.status_code, response.data)
        after[owner] = list(
            SpanNotes.no_workspace_objects.filter(span_id=case.span.id).values_list(
                "id", "notes", "created_by_user_id"
            )
        )
        queue_text = list(
            QueueItemNote.no_workspace_objects.filter(
                queue_item=case.item, annotator=world.user
            ).values_list("notes", flat=True)
        )
        assert queue_text == ([text] if text else []), (
            "AQ-XWS-W1H: positive control always preserves the queue-owned note"
        )
    assert responses["local"][0] == 200 and all(
        value == responses["local"] for value in responses.values()
    ), "AQ-XWS-W1H: positive control mirror refusal does not change the submit response"
    assert (
        after["local"] == []
        if operation == "clear"
        else len(after["local"]) == 1 and after["local"][0][1] == text
    ), "AQ-XWS-W1H: positive control mirrors only onto the authorized PG span"
    assert all(after[owner] == before[owner] for owner in OWNERS[1:]), (
        "AQ-XWS-W1H: mirror create update and clear must not touch foreign deleted or missing PG anchors"
    )


@pytest.mark.parametrize("entrypoint", ["annotate-detail", "for-source"])
def test_own_note_filter_and_queue_note_precedence_are_preserved(
    w1h_world, auth_client, monkeypatch, entrypoint
):
    world = w1h_world
    case = _cases(world, monkeypatch, "observation_span", note_author=world.author)[
        "local"
    ]
    SpanNotes.no_workspace_objects.create(
        span_id=case.span.id,
        notes="W1H viewer's legacy note",
        created_by_user=world.user,
        created_by_annotator=world.user.email,
    )
    QueueItemNote.no_workspace_objects.create(
        queue_item=case.item,
        annotator=world.author,
        notes="W1H queue-owned replacement",
        organization_id=world.queue.organization_id,
        workspace_id=world.queue.workspace_id,
    )
    notes, editable = _read_notes(auth_client, case, entrypoint)
    assert [note["notes"] for note in notes] == [
        "W1H queue-owned replacement"
    ] and editable == "", (
        "AQ-XWS-W1H: positive control retains viewer filtering and queue-note precedence"
    )


def test_for_source_legacy_notes_use_one_query_for_the_page(
    w1h_world, auth_client, monkeypatch
):
    world = w1h_world
    case = _cases(world, monkeypatch, "observation_span", note_author=world.author)[
        "local"
    ]
    for index in range(3):
        queue = AnnotationQueue.no_workspace_objects.create(
            name=f"W1H extra queue {index}",
            status="active",
            organization_id=world.queue.organization_id,
            workspace_id=world.queue.workspace_id,
            project=world.projects["local"],
            created_by=world.user,
        )
        QueueItem.no_workspace_objects.create(
            queue=queue,
            organization_id=queue.organization_id,
            workspace_id=queue.workspace_id,
            project=world.projects["local"],
            source_type="observation_span",
            observation_span_id=case.span.id,
        )
    with CaptureQueriesContext(connection) as queries:
        response = auth_client.get(
            "/model-hub/annotation-queues/for-source/",
            {"source_type": "observation_span", "source_id": case.span.id},
        )
    assert response.status_code == 200, "SETUP_AQ-XWS-W1H: multi-queue page succeeds"
    rows = response.data.get("result", response.data)
    assert len(rows) == 4 and all(len(row["span_notes"]) == 1 for row in rows), (
        "SETUP_AQ-XWS-W1H: every queue has the same authorized legacy note"
    )
    note_queries = [
        query
        for query in queries.captured_queries
        if SpanNotes._meta.db_table in query["sql"]
    ]
    assert len(note_queries) == 1, (
        "AQ-XWS-W1H: legacy note ownership must use one joined query per page not per note or queue"
    )


def test_for_source_cannot_replace_the_authorized_trace_notes_target(
    w1h_world, auth_client, monkeypatch
):
    world = w1h_world
    cases = _cases(world, monkeypatch, "trace", note_author=world.author)
    assert len(_read_notes(auth_client, cases["local"], "for-source")[0]) == 1, (
        "AQ-XWS-W1H: positive control retains the trace's authorized root notes"
    )
    response = auth_client.get(
        "/model-hub/annotation-queues/for-source/",
        {
            "sources": json.dumps(
                [
                    {
                        "source_type": "trace",
                        "source_id": cases["local"].span.trace_id,
                        "span_notes_source_id": cases["same_workspace"].span.id,
                    }
                ]
            )
        },
    )
    assert response.status_code == 200, "SETUP_AQ-XWS-W1H: explicit target resolves"
    rows = response.data.get("result", response.data)
    row = next(row for row in rows if row["queue"]["id"] == str(world.queue.pk))
    assert row["span_notes"] == [], (
        "AQ-XWS-W1H: client-supplied notes ids cannot substitute another PG anchor"
    )


def test_for_source_default_without_item_has_no_legacy_note_authority(
    w1h_world, auth_client, monkeypatch
):
    world = w1h_world
    case = _cases(world, monkeypatch, "observation_span", note_author=world.author)[
        "local"
    ]
    default = AnnotationQueue.no_workspace_objects.create(
        name="W1H default without item",
        status="active",
        organization_id=world.queue.organization_id,
        workspace_id=world.queue.workspace_id,
        project=world.projects["local"],
        created_by=world.user,
        is_default=True,
    )
    assert not QueueItem.no_workspace_objects.filter(queue=default).exists(), (
        "SETUP_AQ-XWS-W1H: default discovery has no queue-item source binding"
    )
    response = auth_client.get(
        "/model-hub/annotation-queues/for-source/",
        {"source_type": "observation_span", "source_id": case.span.id},
    )
    assert response.status_code == 200, "SETUP_AQ-XWS-W1H: default discovery succeeds"
    rows = {
        row["queue"]["id"]: row for row in response.data.get("result", response.data)
    }
    assert (
        str(default.pk) in rows and len(rows[str(world.queue.pk)]["span_notes"]) == 1
    ), "SETUP_AQ-XWS-W1H: both bound and unbound queue entries are returned"
    assert rows[str(default.pk)]["span_notes"] == [], (
        "AQ-XWS-W1H: default discovery without an authorized item must withhold legacy notes"
    )


@pytest.mark.parametrize("source_type", ["trace", "observation_span"])
def test_queue_without_workspace_shows_stored_preview_and_legacy_note(
    w1h_world, auth_client, monkeypatch, source_type
):
    world = w1h_world
    case = _cases(world, monkeypatch, source_type, note_author=world.author)["local"]
    stored = {
        "type": source_type,
        "project_id": str(world.projects["local"].pk),
        "input_preview": "stored preview of a queue with no workspace",
    }
    # Queues and items made before workspaces existed have none.
    AnnotationQueue.no_workspace_objects.filter(pk=world.queue.pk).update(
        workspace=None
    )
    QueueItem.no_workspace_objects.filter(queue=world.queue).update(workspace=None)
    QueueItem.no_workspace_objects.filter(pk=case.item.pk).update(source_preview=stored)
    item = QueueItem.no_workspace_objects.select_related("queue").get(pk=case.item.pk)
    assert item.queue.workspace_id is None and item.workspace_id is None, (
        "setup: the queue and its item have no workspace"
    )
    assert world.projects["local"].workspace.is_default, (
        "setup: the item's project is in the organization's default workspace"
    )
    assert case.note is not None and case.note.created_by_user_id != world.user.pk, (
        "setup: another annotator wrote the legacy note the viewer reads"
    )
    items_url = f"/model-hub/annotation-queues/{world.queue.pk}/items/"
    detail = auth_client.get(f"{items_url}{item.pk}/annotate-detail/")
    assert detail.status_code == 200, "setup: the annotate view must be readable"
    body = detail.data.get("result", detail.data)
    # The annotate view always reads the source live, never the stored capture.
    preview = body["item"]["source_preview"]
    assert "deleted" not in preview and "error" not in preview, (
        "an item on a queue with no workspace must show its live source"
    )
    assert "deleted" not in body["item"]["source_content"], (
        "the annotate view must read the source on a queue with no workspace"
    )
    assert [note["notes"] for note in body["span_notes"]] == [case.note.notes], (
        "a legacy note on an item of a queue with no workspace must show"
    )
    listing = auth_client.get(items_url, {"limit": 100, "page": 1})
    assert listing.status_code == 200, "setup: the item list must be readable"
    entries = {entry["id"]: entry for entry in listing.json()["results"]}
    assert entries[str(item.pk)]["source_preview"] == stored, (
        "the item list must return the stored preview of a queue with no workspace"
    )


# The CH copy is always in the same-organization project with no workspace.
# Each case names where the unbound queue sits, whose project with no
# workspace holds the PG anchor span, and whether the legacy mirror changes.
_NO_WORKSPACE_ANCHOR_CASES = {
    "default-workspace-queue": ("default", "same", True),
    "queue-without-workspace": ("none", "same", True),
    "non-default-workspace-queue": ("non-default", "same", False),
    "default-workspace-queue-other-organization-anchor": ("default", "other", False),
    "queue-without-workspace-other-organization-anchor": ("none", "other", False),
}


def _span_note_rows_for(span_id):
    return list(
        SpanNotes.no_workspace_objects.filter(span_id=span_id).values_list(
            "id", "notes", "created_by_user_id"
        )
    )


@pytest.mark.parametrize("source_type", ["trace", "observation_span"])
@pytest.mark.parametrize("operation", ["create", "update", "clear"])
@pytest.mark.parametrize("case", list(_NO_WORKSPACE_ANCHOR_CASES))
def test_note_mirror_writes_an_anchor_without_workspace_only_within_queue_reach(
    w1h_world, auth_client, monkeypatch, case, operation, source_type
):
    world = w1h_world
    placement, anchor_organization, mirrored = _NO_WORKSPACE_ANCHOR_CASES[case]
    copy_project = world.projects["null_workspace"]
    sibling_workspace = world.projects["sibling"].workspace
    queue_workspace_id = {
        "default": world.queue.workspace_id,
        "none": None,
        "non-default": sibling_workspace.pk,
    }[placement]
    # An unbound queue: the workspace rule alone decides which projects it reaches.
    AnnotationQueue.no_workspace_objects.filter(pk=world.queue.pk).update(
        project=None, workspace_id=queue_workspace_id
    )
    world.queue.refresh_from_db()
    anchor_project = copy_project
    if anchor_organization == "other":
        anchor_project = Project.no_workspace_objects.create(
            name="foreign project with no workspace",
            organization_id=world.projects["foreign"].organization_id,
            workspace=None,
            model_type="GenerativeLLM",
            trace_type="observe",
        )
        # Saving may stamp an ambient workspace; old projects have none.
        Project.no_workspace_objects.filter(pk=anchor_project.pk).update(workspace=None)
        anchor_project.refresh_from_db()
    span = _copy(
        copy_project,
        trace_id=str(uuid.uuid4()),
        span_id=uuid.uuid4().hex[:16],
        second=0,
    )
    trace = Trace.no_workspace_objects.create(
        id=span.trace_id, project=anchor_project, name="no-workspace trace"
    )
    ObservationSpan.no_workspace_objects.create(
        id=span.id,
        project=anchor_project,
        trace=trace,
        name="no-workspace span",
        observation_type="conversation",
        status="OK",
    )
    if operation != "create":
        SpanNotes.no_workspace_objects.create(
            span_id=span.id,
            notes="earlier legacy text",
            created_by_user=world.user,
            created_by_annotator=world.user.email,
        )
    locator = span.trace_id if source_type == "trace" else span.id
    item = QueueItem.no_workspace_objects.create(
        queue=world.queue,
        organization_id=world.queue.organization_id,
        workspace_id=queue_workspace_id,
        project=copy_project,
        source_type=source_type,
        **{f"{source_type}_id": locator},
    )
    QueueItem.no_workspace_objects.filter(pk=item.pk).update(
        workspace_id=queue_workspace_id
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.get_reader", lambda: _NoteCopies([(0, span)])
    )
    assert copy_project.workspace_id is None and anchor_project.workspace_id is None, (
        "setup: the CH copy and the PG anchor are in projects with no workspace"
    )
    same_organization = anchor_project.organization_id == world.queue.organization_id
    assert same_organization == (anchor_organization == "same"), (
        "setup: the PG anchor is in the intended organization"
    )
    assert world.queue.project_id is None, "setup: the queue is unbound"
    assert world.queue.workspace_id == queue_workspace_id, (
        "setup: the queue sits in the intended workspace"
    )
    client = auth_client
    if placement == "non-default":
        # A queue in a non-default workspace is opened from that workspace.
        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=world.user)
        client.set_workspace(sibling_workspace)
    before = _span_note_rows_for(span.id)
    assert len(before) == (0 if operation == "create" else 1), (
        "setup: the viewer's earlier legacy note exists only for update and clear"
    )
    text = "" if operation == "clear" else "submitted queue note"
    url = f"/model-hub/annotation-queues/{world.queue.pk}/items/{item.pk}/"

    response = client.post(
        url + "annotations/submit/",
        {
            "annotations": [
                {"label_id": str(world.label.pk), "value": {"text": "accepted"}}
            ],
            "item_notes": text,
        },
        format="json",
    )

    assert response.status_code == 200, response.data
    queue_text = list(
        QueueItemNote.no_workspace_objects.filter(
            queue_item=item, annotator=world.user
        ).values_list("notes", flat=True)
    )
    assert queue_text == ([text] if text else []), (
        "positive control: the queue-owned note is saved whatever the mirror does"
    )
    after = _span_note_rows_for(span.id)
    if not mirrored:
        assert after == before, (
            "the legacy mirror must not change an anchor outside the queue's reach"
        )
    elif operation == "clear":
        assert after == [], (
            "clearing the note must remove the viewer's legacy note on an anchor "
            "with no workspace"
        )
    else:
        written = [(notes, author) for _, notes, author in after]
        assert written == [(text, world.user.pk)], (
            "the legacy mirror must write the note on an anchor with no workspace"
        )
        if operation == "update":
            assert after[0][0] == before[0][0], (
                "the viewer's existing legacy note must be updated in place"
            )
