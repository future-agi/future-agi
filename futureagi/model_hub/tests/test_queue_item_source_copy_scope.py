"""A queue item renders its own copy of a trace / span, never another tenant's.

A trace / span id can exist in several projects and organizations (a replay, a
re-import, a voice provider account shared by several projects: on dev span
``8de1de7a697559a6`` is held by 20 projects in 8 organizations). The queue
render paths read the source from ClickHouse by id. Read by bare id, they
showed whichever copy the read met first — on dev, 30 items of one queue
rendered another workspace's project copy. They now read the item's own
project (``QueueItem.project``), or for an item added before that was recorded,
the newest copy in the item's organization / workspace.

The ClickHouse reader is faked with per-project copies; Postgres scope, the
views, serializers and helpers run for real.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from conftest import create_categorical_label
from model_hub.models.ai_model import AIModel
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueLabel,
    QueueItem,
    QueueItemNote,
)
from model_hub.models.choices import AnnotationQueueStatusChoices
from model_hub.models.score import Score
from model_hub.serializers.annotation_queues import QueueItemSerializer
from model_hub.utils.annotation_queue_helpers import (
    CollectorSourceCache,
    resolve_source_content,
    resolve_source_preview,
)
from model_hub.views.annotation_queues import _span_notes_target_for_queue_item
from tracer.models.project import Project
from tracer.models.span_notes import SpanNotes
from tracer.services.clickhouse.v2.span_reader import CHSpan

QUEUE_URL = "/model-hub/annotation-queues/"

# Held by the caller's project and a foreign organization's project.
TRACE_X = str(uuid.uuid4())
SPAN_X = "8de1de7a697559a6"
# Also held by a second project of the caller's organization.
TRACE_Y = str(uuid.uuid4())
SPAN_Y = "0d54f84ac36455d6"


def _project(organization, workspace, name):
    return Project.objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


def _copy(project, *, trace_id, span_id, second):
    """One project's copy of a root span; its text names the project."""
    label = project.name
    return CHSpan(
        id=span_id,
        project_id=str(project.id),
        trace_id=trace_id,
        parent_span_id="",
        name=f"{label} copy",
        observation_type="conversation",
        operation_name="",
        start_time=datetime(2026, 9, 22, 16, 27, second, tzinfo=UTC),
        end_time=None,
        latency_ms=1000,
        model="",
        provider="",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        cost=0.0,
        status="OK",
        status_message="",
        org_id=str(project.organization_id),
        project_version_id=None,
        end_user_id=None,
        trace_session_id=None,
        prompt_version_id=None,
        prompt_label_id=None,
        custom_eval_config_id=None,
        input=f'"{label} input"',
        output=f'"{label} output"',
        tags="[]",
        span_events="[]",
        metadata="{}",
        resource_attrs="{}",
        attributes_extra="{}",
    )


class _ClickHouseDown(Exception):
    pass


class _Copies:
    """``get_reader()`` stand-in over per-project copies of trace roots / spans.

    Honours the real reader's scope kwargs, and orders as its SQL does where it
    orders: roots by ``(trace_id, start_time, id)``, scoped point reads and the
    newest-project reads newest write first. Where the real read keeps an
    arbitrary copy per id (a bare ``FINAL ... LIMIT 1``, a batch read merged
    into a dict), the newest write of any tenant — here another
    organization's — stands in for it.
    """

    def __init__(self, copies, *, down=False):
        self.copies = copies  # [(version, CHSpan)]
        self.down = down

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _scoped(self, project_id=None, project_ids=None):
        if self.down:
            raise _ClickHouseDown("clickhouse unavailable")
        pids = None if project_ids is None else {str(pid) for pid in project_ids}
        rows = [
            (version, span)
            for version, span in self.copies
            if (not project_id or span.project_id == str(project_id))
            and (pids is None or span.project_id in pids)
        ]
        return [span for _, span in sorted(rows, key=lambda row: -row[0])]

    def get(self, span_id, *, project_id=None, project_ids=None):
        rows = [s for s in self._scoped(project_id, project_ids) if s.id == span_id]
        return rows[0] if rows else None

    def newest_trace_project(self, trace_id, project_ids):
        return self.newest_trace_projects([trace_id], project_ids).get(trace_id)

    def newest_trace_projects(self, trace_ids, project_ids):
        newest = {}
        for span in self._scoped(project_ids=project_ids):
            if span.trace_id in trace_ids:
                newest.setdefault(span.trace_id, span.project_id)
        return newest

    def newest_span_projects(self, span_ids, project_ids):
        newest = {}
        for span in self._scoped(project_ids=project_ids):
            if span.id in span_ids:
                newest.setdefault(span.id, span.project_id)
        return newest

    def roots_by_trace_ids(self, trace_ids, *, project_id=None, **_):
        rows = [s for s in self._scoped(project_id) if s.trace_id in trace_ids]
        return sorted(rows, key=lambda s: (s.trace_id, s.start_time, s.id))

    def list_by_ids(self, span_ids, *, project_id=None, **_):
        rows = [s for s in self._scoped(project_id) if s.id in span_ids]
        return list(reversed(rows))  # merged into a dict, the newest write wins


@pytest.fixture
def copies(user, workspace, monkeypatch):
    """The caller's project ``own`` and a second project ``other`` in the same
    organization, and ``foreign`` in another organization. ``foreign`` holds the
    newest and earliest-starting copy of every id, ``other`` the next newest."""
    own = _project(user.organization, workspace, "own")
    other = _project(user.organization, workspace, "other")
    foreign_org = Organization.objects.create(name=f"Foreign {uuid.uuid4().hex[:8]}")
    foreign_workspace = Workspace.no_workspace_objects.create(
        name="Foreign Workspace",
        organization=foreign_org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    foreign = _project(foreign_org, foreign_workspace, "foreign")
    projects = {"own": own, "other": other, "foreign": foreign}
    # project -> (write version, start second)
    order = {"own": (10, 9), "other": (20, 5), "foreign": (30, 1)}

    rows = [
        (
            order[name][0],
            _copy(
                projects[name],
                trace_id=trace_id,
                span_id=span_id,
                second=order[name][1],
            ),
        )
        for trace_id, span_id, holders in (
            (TRACE_X, SPAN_X, ("own", "foreign")),
            (TRACE_Y, SPAN_Y, ("own", "other", "foreign")),
        )
        for name in holders
    ]
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.get_reader", lambda: _Copies(rows)
    )
    return SimpleNamespace(rows=rows, **projects)


def _queue(auth_client, name):
    label_id = create_categorical_label(auth_client, name=f"{name} Label")
    response = auth_client.post(
        QUEUE_URL, {"name": name, "label_ids": [str(label_id)]}, format="json"
    )
    assert "id" in response.data, response.content
    return response.data["id"]


# (source_type, source id, item project, copy the item must render)
UNATTRIBUTED = [
    # Added before QueueItem.project was recorded: the newest copy in the
    # caller's organization, never the newer foreign one.
    ("trace", TRACE_X, None, "own"),
    ("observation_span", SPAN_X, None, "own"),
    ("trace", TRACE_Y, None, "other"),
    ("observation_span", SPAN_Y, None, "other"),
]
ATTRIBUTED = [
    # Recorded project: that project's copy even when another is newer.
    ("trace", TRACE_X, "own", "own"),
    ("observation_span", SPAN_X, "own", "own"),
    ("trace", TRACE_Y, "own", "own"),
    ("observation_span", SPAN_Y, "own", "own"),
]


@pytest.fixture
def items(auth_client, copies, user, workspace):
    """``{queue_id: [(item, expected copy)]}``; items carry no captured
    preview, so every render reads ClickHouse. A queue holds a source once, so
    each case list gets its own queue."""
    queues = {}
    for name, cases in (("Unattributed", UNATTRIBUTED), ("Attributed", ATTRIBUTED)):
        queue_id = _queue(auth_client, f"{name} Queue")
        queues[queue_id] = [
            (
                QueueItem.objects.create(
                    queue_id=queue_id,
                    source_type=source_type,
                    organization=user.organization,
                    workspace=workspace,
                    project=getattr(copies, project) if project else None,
                    order=order,
                    **{
                        "trace_id"
                        if source_type == "trace"
                        else "observation_span_id": source_id
                    },
                ),
                expected,
            )
            for order, (source_type, source_id, project, expected) in enumerate(cases)
        ]
    return queues


def _assert_copy(payload, expected):
    assert payload.get("deleted") is not True, payload
    assert payload["name"] == f"{expected} copy", payload
    assert "foreign" not in str(payload)


@pytest.mark.django_db
def test_single_item_reads_render_the_items_copy(items):
    """``QueueItemSerializer(item)`` (next-item / skip / complete / review
    navigation) and the uncached content / preview read one item at a time."""
    for cases in items.values():
        for item, expected in cases:
            fresh = QueueItem.objects.get(pk=item.pk)
            _assert_copy(QueueItemSerializer(fresh).data["source_preview"], expected)
            _assert_copy(resolve_source_preview(fresh), expected)
            _assert_copy(resolve_source_content(fresh), expected)


@pytest.mark.django_db
def test_page_cache_renders_each_items_copy(items):
    """The batched page read (items list, exports, annotate-detail)."""
    for cases in items.values():
        expected_by_pk = {item.pk: expected for item, expected in cases}
        page = list(QueueItem.objects.filter(pk__in=expected_by_pk))
        cache = CollectorSourceCache.for_items(page)
        for item in page:
            expected = expected_by_pk[item.pk]
            _assert_copy(resolve_source_preview(item, ch_cache=cache), expected)
            _assert_copy(resolve_source_content(item, ch_cache=cache), expected)


@pytest.mark.django_db
def test_endpoints_render_the_callers_copy(auth_client, items):
    for queue_id, cases in items.items():
        listed = auth_client.get(f"{QUEUE_URL}{queue_id}/items/", {"limit": 50})
        assert listed.status_code == status.HTTP_200_OK, listed.content
        previews = {row["id"]: row["source_preview"] for row in listed.data["results"]}
        assert len(previews) == len(cases)

        for item, expected in cases:
            _assert_copy(previews[str(item.pk)], expected)
            detail = auth_client.get(
                f"{QUEUE_URL}{queue_id}/items/{item.pk}/annotate-detail/"
            )
            assert detail.status_code == status.HTTP_200_OK, detail.content
            body = detail.data.get("result", detail.data)["item"]
            _assert_copy(body["source_content"], expected)
            _assert_copy(body["source_preview"], expected)


@pytest.mark.django_db
def test_item_notes_target_is_the_items_copy(items, copies):
    """Submitting whole-item notes writes them on the returned span."""
    for cases in items.values():
        for item, expected in cases:
            target = _span_notes_target_for_queue_item(
                QueueItem.objects.get(pk=item.pk)
            )
            assert target is not None
            assert target.project_id == str(getattr(copies, expected).id)


@pytest.mark.django_db
def test_id_held_only_by_another_organization_renders_deleted(
    auth_client, copies, user, workspace
):
    trace_id = str(uuid.uuid4())
    copies.rows.append(
        (40, _copy(copies.foreign, trace_id=trace_id, span_id="lone", second=0))
    )
    item = QueueItem.objects.create(
        queue_id=_queue(auth_client, "Foreign Only Queue"),
        source_type="trace",
        organization=user.organization,
        workspace=workspace,
        trace_id=trace_id,
        order=1,
    )

    assert resolve_source_preview(item) == {"type": "trace", "deleted": True}
    cache = CollectorSourceCache.for_items([item])
    assert resolve_source_content(item, ch_cache=cache) == {
        "type": "trace",
        "deleted": True,
    }


def _clickhouse_down(monkeypatch, copies):
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.get_reader",
        lambda: _Copies(copies.rows, down=True),
    )


@pytest.mark.django_db
def test_item_notes_target_fails_closed_when_clickhouse_errors(
    items, copies, monkeypatch
):
    """The notes target feeds the SpanNotes write in submit: a ClickHouse error
    must raise (the submit fails and the annotator retries), never read as "no
    target" and drop the whole-item note. Attributed items read the roots / spans
    of their project; unattributed ones first read the newest-copy project."""
    _clickhouse_down(monkeypatch, copies)
    for cases in items.values():
        for item, _expected in cases:
            with pytest.raises(_ClickHouseDown):
                _span_notes_target_for_queue_item(QueueItem.objects.get(pk=item.pk))


@pytest.mark.django_db
def test_submit_fails_closed_when_the_notes_target_read_errors(
    auth_client, items, copies, user, monkeypatch
):
    queue_id, cases = next(iter(items.items()))
    item = next(item for item, _ in cases if str(item.trace_id) == TRACE_X)
    AnnotationQueue.objects.filter(pk=queue_id).update(
        status=AnnotationQueueStatusChoices.ACTIVE.value
    )
    label_id = AnnotationQueueLabel.objects.get(queue_id=queue_id).label_id
    url = f"{QUEUE_URL}{queue_id}/items/{item.pk}/annotations/submit/"
    payload = {
        "annotations": [{"label_id": str(label_id), "value": "A"}],
        "item_notes": "whole item note",
    }

    _clickhouse_down(monkeypatch, copies)
    with pytest.raises(_ClickHouseDown):
        auth_client.post(url, payload, format="json")
    assert not Score.no_workspace_objects.filter(queue_item=item).exists()
    assert not QueueItemNote.no_workspace_objects.filter(queue_item=item).exists()
    assert not SpanNotes.objects.filter(created_by_user=user).exists()

    # The retry, with ClickHouse back, writes the note on the item's own copy.
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.get_reader", lambda: _Copies(copies.rows)
    )
    response = auth_client.post(url, payload, format="json")
    assert response.status_code == status.HTTP_200_OK, response.content
    note = SpanNotes.objects.get(created_by_user=user)
    assert (note.span_id, note.notes) == (SPAN_X, "whole item note")


@pytest.mark.django_db
def test_item_notes_target_is_none_when_the_id_is_not_live_in_scope(
    auth_client, copies, user, workspace
):
    """A genuine miss (the id is held only by another organization) still
    resolves to no target, without raising."""
    trace_id = str(uuid.uuid4())
    copies.rows.append(
        (40, _copy(copies.foreign, trace_id=trace_id, span_id="lone", second=0))
    )
    for project in (None, copies.own):
        item = QueueItem.objects.create(
            queue_id=_queue(auth_client, f"Miss Queue {bool(project)}"),
            source_type="trace",
            organization=user.organization,
            workspace=workspace,
            project=project,
            trace_id=trace_id,
            order=1,
        )
        assert _span_notes_target_for_queue_item(item) is None


@pytest.mark.django_db
def test_render_paths_fail_open_when_clickhouse_errors(
    auth_client, items, copies, monkeypatch
):
    """Previews, content and annotate-detail (a read; its notes list is display
    only) still render the ``deleted`` sentinel on a ClickHouse error."""
    _clickhouse_down(monkeypatch, copies)
    for queue_id, cases in items.items():
        page = list(QueueItem.objects.filter(pk__in=[item.pk for item, _ in cases]))
        cache = CollectorSourceCache.for_items(page)
        for item in page:
            assert resolve_source_preview(item).get("deleted") is True
            assert resolve_source_content(item, ch_cache=cache).get("deleted") is True
            detail = auth_client.get(
                f"{QUEUE_URL}{queue_id}/items/{item.pk}/annotate-detail/"
            )
            assert detail.status_code == status.HTTP_200_OK, detail.content
            body = detail.data.get("result", detail.data)["item"]
            assert body["source_content"].get("deleted") is True
