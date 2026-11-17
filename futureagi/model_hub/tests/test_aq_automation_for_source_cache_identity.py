"""W1j: page caches retain each authorized project's colliding collector copy.

Only the ClickHouse reader is replaced. ORM ownership, cache construction,
render helpers, the joined note gate and HTTP endpoints execute for real.
RED/GREEN execution belongs to the integration venue, not this writer.
"""

import json
import uuid
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from model_hub.models.annotation_queues import AnnotationQueue, QueueItem
from model_hub.tests.test_annotation_queues_api import create_workspace_member_user
from model_hub.tests.test_aq_automation_span_notes_scope import _NoteCopies
from model_hub.tests.test_queue_item_source_copy_scope import _copy
from model_hub.utils.annotation_queue_helpers import (
    CollectorSourceCache,
    resolve_source_content,
    resolve_source_preview,
)
from model_hub.views.annotation_queues import _span_notes_target_for_queue_item
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.span_notes import SpanNotes
from tracer.models.trace import Trace

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


@pytest.fixture
def identity_world(organization, workspace, user):
    clear_workspace_context()
    projects, queues = {}, {}
    for name in ("A", "B"):
        projects[name] = Project.no_workspace_objects.create(
            name=f"W1J project {name}",
            organization=organization,
            workspace=workspace,
            model_type="GenerativeLLM",
            trace_type="observe",
        )
        queues[name] = AnnotationQueue.no_workspace_objects.create(
            name=f"W1J queue {name}",
            status="active",
            organization=organization,
            workspace=workspace,
            project=projects[name],
            created_by=user,
        )
    author = create_workspace_member_user(organization, workspace)
    assert user.can_access_workspace(workspace) and author.pk != user.pk, (
        "SETUP_AQ-XWS-W1J: viewer has workspace access and note author differs"
    )
    return SimpleNamespace(
        projects=projects,
        queues=queues,
        author=author,
        request=SimpleNamespace(
            user=user, organization=organization, workspace=workspace
        ),
    )


def _collision(world, monkeypatch, source_type, first, *, unpinned_b=False):
    trace_id, span_id = str(uuid.uuid4()), uuid.uuid4().hex[:16]
    items, spans = {}, {}
    for name in ("A", "B"):
        queue = world.queues[name]
        queue.name = f"W1J {'0' if name == first else '1'} queue {name}"
        queue.save(update_fields=["name"])
        spans[name] = _copy(
            world.projects[name],
            trace_id=trace_id,
            span_id=span_id,
            second=1 if name == "A" else 2,
        )
        items[name] = QueueItem.no_workspace_objects.create(
            queue=queue,
            organization_id=queue.organization_id,
            workspace_id=queue.workspace_id,
            project=None if name == "B" and unpinned_b else world.projects[name],
            source_type=source_type,
            **{f"{source_type}_id": trace_id if source_type == "trace" else span_id},
        )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.get_reader",
        lambda: _NoteCopies([(1, spans["A"]), (2, spans["B"])]),
    )
    assert items["A"].queue_id != items["B"].queue_id, (
        "SETUP_AQ-XWS-W1J: colliding locators belong to distinct accessible queues"
    )
    return SimpleNamespace(items=items, spans=spans, trace_id=trace_id, span_id=span_id)


def _for_source(client, case, source_type):
    response = client.get(
        "/model-hub/annotation-queues/for-source/",
        {
            "sources": json.dumps(
                [
                    {
                        "source_type": source_type,
                        "source_id": (
                            case.trace_id if source_type == "trace" else case.span_id
                        ),
                        "span_notes_source_id": case.span_id,
                    }
                ]
            )
        },
    )
    assert response.status_code == 200, (
        "SETUP_AQ-XWS-W1J: unpinned for-source request succeeds"
    )
    rows = response.data.get("result", response.data)
    assert {row["queue"]["id"] for row in rows} == {
        str(item.queue_id) for item in case.items.values()
    }, "SETUP_AQ-XWS-W1J: both accessible queues are returned"
    return {row["queue"]["id"]: row for row in rows}


def _note_shape(row):
    return row["span_notes"], row["existing_notes"]


@pytest.mark.parametrize("source_type", ["observation_span", "trace"])
@pytest.mark.parametrize("first", ["A", "B"])
def test_for_source_keeps_each_pg_owners_note_in_both_queue_orders(
    identity_world, auth_client, monkeypatch, source_type, first
):
    world = identity_world
    case = _collision(world, monkeypatch, source_type, first)
    pg_trace = Trace.no_workspace_objects.create(
        id=case.trace_id, project=world.projects["A"], name="W1J PG trace"
    )
    pg_span = ObservationSpan.no_workspace_objects.create(
        id=case.span_id,
        project=world.projects["A"],
        trace=pg_trace,
        name="W1J PG span",
        observation_type="conversation",
        status="OK",
    )
    note = SpanNotes.no_workspace_objects.create(
        span=pg_span,
        notes="W1J authorized legacy note",
        created_by_user=world.author,
        created_by_annotator=world.author.email,
    )
    retained, detail_shapes, refused, note_query_counts = [], [], [], []
    # Exercise both PG owners within each name-order case. At the base, the
    # earlier queue loses its note whichever project currently owns the PG row.
    for owner in ("A", "B"):
        project = world.projects[owner]
        Trace.no_workspace_objects.filter(pk=pg_trace.pk).update(project=project)
        ObservationSpan.no_workspace_objects.filter(pk=pg_span.pk).update(
            project=project
        )
        item = case.items[owner]
        detail = auth_client.get(
            f"/model-hub/annotation-queues/{item.queue_id}/items/{item.pk}/annotate-detail/"
        )
        assert detail.status_code == 200, "SETUP_AQ-XWS-W1J: detail succeeds"
        detail_shape = _note_shape(detail.data.get("result", detail.data))
        assert (
            len(detail_shape[0]) == 1
            and detail_shape[0][0]["notes"] == note.notes
            and detail_shape[0][0]["annotator_id"] == str(world.author.pk)
            and detail_shape[1] == ""
        ), "AQ-XWS-W1J: positive control retains the single-queue authorized note"
        with CaptureQueriesContext(connection) as queries:
            rows = _for_source(auth_client, case, source_type)
        retained.append(_note_shape(rows[str(item.queue_id)]))
        detail_shapes.append(detail_shape)
        other = "B" if owner == "A" else "A"
        refused.append(_note_shape(rows[str(case.items[other].queue_id)]))
        note_query_counts.append(
            sum(
                SpanNotes._meta.db_table in query["sql"]
                for query in queries.captured_queries
            )
        )
    # A real foreign-to-item PG anchor and an absent PG anchor must expose the
    # same withheld note shape, even though both have live authorized CH copies.
    missing_case = _collision(world, monkeypatch, source_type, first)
    assert not ObservationSpan.all_objects.filter(pk=missing_case.span_id).exists(), (
        "SETUP_AQ-XWS-W1J: missing PG anchor is genuinely absent"
    )
    missing = _for_source(auth_client, missing_case, source_type)
    assert all(shape == ([], "") for shape in refused) and all(
        _note_shape(row) == ([], "") for row in missing.values()
    ), "AQ-XWS-W1J: foreign and nonexistent PG anchors have identical withheld notes"
    assert note_query_counts == [1, 1], (
        "AQ-XWS-W1J: positive control keeps one joined legacy-note query per page"
    )
    assert retained == detail_shapes, (
        "AQ-XWS-W1J: page cache must retain each PG-owning project's legacy note in either queue order"
    )


@pytest.mark.parametrize("source_type", ["observation_span", "trace"])
@pytest.mark.parametrize("first", ["A", "B"])
@pytest.mark.parametrize("unpinned_b", [False, True], ids=["pinned", "legacy-newest"])
def test_mixed_page_consumers_select_the_items_own_project(
    identity_world, monkeypatch, source_type, first, unpinned_b
):
    world = identity_world
    case = _collision(world, monkeypatch, source_type, first, unpinned_b=unpinned_b)
    page = [case.items[first], case.items["B" if first == "A" else "A"]]
    cache = CollectorSourceCache.for_items(page, request=world.request)
    actual = {}
    for name, item in case.items.items():
        content = resolve_source_content(item, ch_cache=cache, request=world.request)
        preview = resolve_source_preview(item, ch_cache=cache, request=world.request)
        target = _span_notes_target_for_queue_item(
            item, ch_cache=cache, request=world.request
        )
        actual[name] = (
            world.projects[name].name in str(content),
            world.projects[name].name in str(preview),
            getattr(target, "project_id", None),
        )
    assert actual == {
        name: (True, True, str(project.pk)) for name, project in world.projects.items()
    }, (
        "AQ-XWS-W1J: content preview and notes target must select each item's authorized copy"
    )
