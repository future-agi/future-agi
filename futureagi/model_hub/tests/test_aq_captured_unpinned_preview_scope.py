"""W1l: a live collector copy cannot authorize different captured bytes.

Only the collector reader is stubbed. Real ORM authorization, page-cache
construction and serializers run unchanged. RED/GREEN execution belongs to the
integration venue; the writer performs syntax checks only.
"""

import json
import uuid
from types import SimpleNamespace

import pytest

from accounts.models.workspace import Workspace
from model_hub.models.annotation_queues import AnnotationQueue, QueueItem
from model_hub.serializers.annotation_queues import QueueItemSerializer
from model_hub.tests.test_queue_item_source_copy_scope import _Copies, _copy
from model_hub.utils.annotation_queue_helpers import (
    CollectorSourceCache,
    QueueSourceAuthorization,
    preview_payload_for_source,
    queue_item_source_is_available,
)
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.project import Project

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


@pytest.fixture
def captured_world(organization, workspace, user, monkeypatch):
    clear_workspace_context()
    foreign_workspace = Workspace.no_workspace_objects.create(
        name="W1L foreign workspace",
        organization=organization,
        created_by=user,
        is_active=True,
        is_default=False,
    )
    projects = {
        name: Project.no_workspace_objects.create(
            name=f"W1L {name}",
            organization=organization,
            workspace=ws,
            model_type="GenerativeLLM",
            trace_type="observe",
        )
        for name, ws in (("local", workspace), ("foreign", foreign_workspace))
    }
    queue = AnnotationQueue.no_workspace_objects.create(
        name="W1L queue",
        organization=organization,
        workspace=workspace,
        created_by=user,
        status="active",
    )
    rows, sessions, reads = [], {}, []

    def get_reader():
        reads.append("spans")
        return _Copies(rows)

    def resolve_session_fields(session_ids, *, project_id=None, **_):
        reads.append("sessions")
        return {
            sid: fields
            for sid, fields in sessions.items()
            if sid in session_ids
            and (project_id is None or fields["project_id"] == str(project_id))
        }

    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", get_reader)
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.trace_session_dict_reader.resolve_session_fields",
        resolve_session_fields,
    )
    assert user.can_access_workspace(workspace), (
        "SETUP_AQ-XWS-W1L: explicit viewer can access the queue workspace"
    )
    assert projects["local"].workspace_id != projects["foreign"].workspace_id, (
        "SETUP_AQ-XWS-W1L: colliding projects have different workspaces"
    )
    return SimpleNamespace(
        queue=queue,
        projects=projects,
        rows=rows,
        sessions=sessions,
        reads=reads,
        request=SimpleNamespace(
            user=user, organization=organization, workspace=workspace
        ),
    )


def _source(world, source_type, project_name, source_id):
    project = world.projects[project_name]
    if source_type == "trace_session":
        return SimpleNamespace(
            id=source_id, project_id=str(project.pk), name=f"{project_name} session"
        )
    span = _copy(
        project,
        trace_id=source_id if source_type == "trace" else str(uuid.uuid4()),
        span_id=source_id
        if source_type == "observation_span"
        else uuid.uuid4().hex[:16],
        second=1,
    )
    return SimpleNamespace(root_span=span) if source_type == "trace" else span


def _stage(world, source_type, source):
    if source_type == "trace_session":
        world.sessions[source.id] = {
            "project_id": source.project_id,
            "display_name": source.name,
        }
    else:
        span = source.root_span if source_type == "trace" else source
        world.rows.append((len(world.rows) + 1, span))


def _item(world, source_type, source_id, preview, *, pinned=False):
    item = QueueItem.no_workspace_objects.create(
        queue=world.queue,
        organization_id=world.queue.organization_id,
        workspace_id=world.queue.workspace_id,
        project=world.projects["local"] if pinned else None,
        source_type=source_type,
        source_preview=preview,
        **{f"{source_type}_id": source_id},
    )
    auth = QueueSourceAuthorization.for_items([item], request=world.request)
    assert auth.project_ids(item) == {str(world.projects["local"].pk)}, (
        "SETUP_AQ-XWS-W1L: real authorization must grant the nonempty exact queue set"
    )
    assert bool(item.project_id) == pinned, (
        "SETUP_AQ-XWS-W1L: persisted project pin matches the test case"
    )
    return item


def _render(world, item, mode):
    context = {"request": world.request}
    if mode == "list":
        return QueueItemSerializer([item], many=True, context=context).data[0][
            "source_preview"
        ]
    return QueueItemSerializer(item, context=context).data["source_preview"]


def _withheld(source_type):
    return {"type": source_type, "deleted": True, "error": "queue_source_unavailable"}


def _foreign_collision(world, source_type):
    source_id = uuid.uuid4().hex
    local = _source(world, source_type, "local", source_id)
    foreign = _source(world, source_type, "foreign", source_id)
    _stage(world, source_type, local)
    _stage(world, source_type, foreign)
    preview = preview_payload_for_source(source_type, foreign)
    if source_type == "observation_span":
        # Producer spans have no pid; exercise the generic attribution contract
        # with an explicit foreign stamp, without changing the capture builder.
        preview["project_id"] = str(world.projects["foreign"].pk)
    item = _item(world, source_type, source_id, preview)
    cache = CollectorSourceCache.for_items([item], request=world.request)
    resolved = (
        cache.trace_root(source_id, item=item)
        if source_type == "trace"
        else cache.span(source_id, item=item)
    )
    assert resolved is not None and resolved.project_id == str(
        world.projects["local"].pk
    ), "SETUP_AQ-XWS-W1L: the same locator resolves to the authorized live copy"
    world.reads.clear()
    return item, cache


def test_foreign_trace_snapshot_cannot_borrow_live_authority(captured_world):
    world = captured_world
    item, _ = _foreign_collision(world, "trace")
    preview = _render(world, item, "single")
    assert preview == _withheld("trace"), (
        "AQ-XWS-W1L: AQ-AUTOMATION-R4-UO-01 foreign snapshot must be withheld "
        "despite an authorized colliding live trace"
    )
    assert world.reads == [], (
        "AQ-XWS-W1L: AQ-AUTOMATION-R4-UO-01 foreign attribution must not read collector"
    )


def test_captured_gate_cannot_borrow_live_authority(captured_world):
    world = captured_world
    item, cache = _foreign_collision(world, "trace")
    assert not queue_item_source_is_available(
        item, request=world.request, ch_cache=cache, captured=True
    ), "AQ-XWS-W1L: AQ-AUTOMATION-R4-UO-01 captured gate must reject foreign bytes"


@pytest.mark.parametrize("source_type", ["trace", "observation_span"])
def test_foreign_snapshot_and_missing_live_source_have_identical_shape(
    captured_world, source_type
):
    world = captured_world
    foreign, _ = _foreign_collision(world, source_type)
    missing_id = uuid.uuid4().hex
    missing_preview = preview_payload_for_source(
        source_type, _source(world, source_type, "foreign", missing_id)
    )
    missing_preview.pop("project_id", None)
    missing = _item(world, source_type, missing_id, missing_preview)
    foreign_payload = _render(world, foreign, "single")
    missing_payload = _render(world, missing, "single")
    assert json.dumps(foreign_payload, sort_keys=True) == json.dumps(
        missing_payload, sort_keys=True
    ), (
        "AQ-XWS-W1L: AQ-AUTOMATION-R4-UO-01 foreign snapshot and unattributable "
        "missing live source must have byte-identical withheld dictionaries"
    )
    assert foreign_payload == _withheld(source_type), (
        "AQ-XWS-W1L: CONTROL withheld response contract is unchanged"
    )


@pytest.mark.parametrize("source_type", ["trace", "trace_session"])
@pytest.mark.parametrize("mode", ["list", "single"])
def test_attributed_unpinned_capture_is_visible_without_collector_reads(
    captured_world, source_type, mode
):
    world = captured_world
    source_id = uuid.uuid4().hex
    local = _source(world, source_type, "local", source_id)
    _stage(world, source_type, local)
    preview = preview_payload_for_source(source_type, local)
    preview["name"] = "W1L historical authorized snapshot"
    item = _item(world, source_type, source_id, preview)
    actual = _render(world, item, mode)
    assert (actual, world.reads) == (preview, []), (
        "AQ-XWS-W1L: AQ-AUTOMATION-R4-UO-02 attributed unpinned capture must "
        "render the snapshot in list and single with zero collector reads"
    )


@pytest.mark.parametrize("source_type", ["trace", "observation_span", "trace_session"])
@pytest.mark.parametrize("mode", ["list", "single"])
@pytest.mark.parametrize("missing_pid", ["absent", "null"])
def test_unattributable_capture_renders_only_authorized_live_bytes(
    captured_world, source_type, mode, missing_pid
):
    world = captured_world
    source_id = uuid.uuid4().hex
    local = _source(world, source_type, "local", source_id)
    _stage(world, source_type, local)
    stored = preview_payload_for_source(
        source_type, _source(world, source_type, "foreign", source_id)
    )
    if missing_pid == "absent":
        stored.pop("project_id", None)
    else:
        stored["project_id"] = None
    item = _item(world, source_type, source_id, stored)
    expected = preview_payload_for_source(source_type, local)
    assert expected != stored, (
        "SETUP_AQ-XWS-W1L: stored foreign text differs from live authorized text"
    )
    actual = _render(world, item, mode)
    assert actual == expected, (
        "AQ-XWS-W1L: AQ-AUTOMATION-R4-UO-02 unattributable capture must render "
        "authorized LIVE bytes in list and single, never the stored snapshot"
    )


@pytest.mark.parametrize("mode", ["list", "single"])
def test_unattributable_foreign_and_missing_span_are_identically_withheld(
    captured_world, mode
):
    world = captured_world
    items = []
    for exists in (True, False):
        source_id = uuid.uuid4().hex
        foreign = _source(world, "observation_span", "foreign", source_id)
        if exists:
            _stage(world, "observation_span", foreign)
        items.append(
            _item(
                world,
                "observation_span",
                source_id,
                preview_payload_for_source("observation_span", foreign),
            )
        )
    assert [_render(world, item, mode) for item in items] == [
        _withheld("observation_span"),
        _withheld("observation_span"),
    ], "AQ-XWS-W1L: CONTROL foreign and nonexistent live spans must look identical"


@pytest.mark.parametrize("source_type", ["trace", "observation_span", "trace_session"])
@pytest.mark.parametrize("mode", ["list", "single"])
@pytest.mark.parametrize("pid_state", ["matching", "absent", "foreign"])
def test_pinned_capture_branch_is_unchanged(
    captured_world, source_type, mode, pid_state
):
    world = captured_world
    source_id = uuid.uuid4().hex
    preview = preview_payload_for_source(
        source_type, _source(world, source_type, "local", source_id)
    )
    if pid_state == "absent":
        preview.pop("project_id", None)
    else:
        project = world.projects["foreign" if pid_state == "foreign" else "local"]
        preview["project_id"] = str(project.pk)
    item = _item(world, source_type, source_id, preview, pinned=True)
    actual = _render(world, item, mode)
    expected = _withheld(source_type) if pid_state == "foreign" else preview
    assert (actual, world.reads) == (expected, []), (
        "AQ-XWS-W1L: CONTROL pinned capture identity branch and zero-read budget "
        "must remain unchanged"
    )


def test_ambiguous_direct_cache_cannot_resurrect_unattributable_snapshot(
    captured_world,
):
    world = captured_world
    source_id = uuid.uuid4().hex
    local = _source(world, "observation_span", "local", source_id)
    foreign = _source(world, "observation_span", "foreign", source_id)
    item = _item(
        world,
        "observation_span",
        source_id,
        preview_payload_for_source("observation_span", foreign),
    )
    cache = CollectorSourceCache(
        spans={
            (local.project_id, source_id): local,
            (foreign.project_id, source_id): foreign,
        },
        source_authorization=QueueSourceAuthorization.for_items(
            [item], request=world.request
        ),
    )
    assert cache.span(source_id, item=item) is None, (
        "SETUP_AQ-XWS-W1L: directly supplied ambiguous copies have no item binding"
    )
    actual = QueueItemSerializer(
        item, context={"request": world.request, "ch_source_cache": cache}
    ).data["source_preview"]
    assert actual == _withheld("observation_span"), (
        "AQ-XWS-W1L: CONTROL ambiguous live copies cannot resurrect stored bytes"
    )
