"""Authority parity with real PG markers and a projection-faithful CH double.

No object-storage, decoder or live ClickHouse qualification is claimed here.
The shared upload fixtures use an explicit parser double. Marker transactions
are committed in the deletion regression; no cleanup is run before the reads.
"""

from copy import copy
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from tracer.models.attachment import AttachmentOccurrence
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession, TraceSessionOverlay
from tracer.services.attachments import (
    authority,
    control_plane,
    manifests,
    occurrences,
    session_deletion,
)
from tracer.tests import test_attachment_control_plane as base

setup = base.setup
unlimited_retention = base.unlimited_retention
pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _marker(scope, session_id, kind="overlay", *, project_id=None):
    project_id = project_id or scope.project_id
    if kind == "pg":
        return TraceSession._base_manager.create(
            id=session_id, project_id=project_id, deleted=True
        )
    return TraceSessionOverlay._base_manager.create(
        trace_session_id=session_id, project_id=project_id, deleted=True
    )


@pytest.fixture
def parity(setup, user, monkeypatch):
    scope, _ = setup
    scope.record_audio = True
    scope.policy_version += 1
    scope.save(update_fields=["record_audio", "policy_version"])
    main = base.occurrence(setup, monkeypatch)
    survivor = base.occurrence_on(setup, monkeypatch, "1111111111111111")
    recording_id = str(uuid4())
    upload, token = base.ready_upload(
        setup,
        monkeypatch,
        recording_id=recording_id,
        segment_index=0,
        purpose="conversation_recording",
    )
    segment = AttachmentOccurrence.objects.select_related("attachment", "scope").get(
        pk=upload.occurrence_id
    )
    body = {
        "schema_version": 1,
        "recording_id": recording_id,
        "timebase": "session_relative_ms",
        "sample_format": "s16le",
        "sample_rate_hz": 16000,
        "channels": 1,
        "codec": "pcm",
        "container": "wav",
        "layout": "mixed",
        "tracks": [
            {
                "track_id": str(uuid4()),
                "channels": [0],
                "direction": "mixed",
                "role": "mixed",
            }
        ],
        "segments": [
            {
                "occurrence_id": str(segment.client_id),
                "index": 0,
                "start_ms": 0,
                "duration_ms": 1000,
            }
        ],
        "gaps": [],
        "playout": [],
        "capture_complete": True,
        "duration_ms": 1000,
    }
    manifests.put_manifest(
        token, scope_id=scope.id, recording_id=recording_id, body=body, key="parity-1"
    )
    session_id, survivor_session = uuid4(), uuid4()
    spans = {
        main.span_id: base.span_for(main, refs=[]),
        survivor.span_id: base.span_for(survivor, refs=[]),
    }
    spans[main.span_id].trace_session_id = session_id
    spans[survivor.span_id].trace_session_id = survivor_session

    def projected(span_ids, *, project_id, columns, trace_ids=None, start_window=None):
        # Honor the actual SELECT projection. Returning the full fixture here
        # would conceal a missing trace_session_id in production SPAN_COLUMNS.
        return [
            {column: getattr(span, column, None) for column in columns}
            for key, span in spans.items()
            if key in span_ids
            and str(span.project_id) == str(project_id)
            and (not trace_ids or str(span.trace_id) in trace_ids)
            and (
                not start_window
                or base._hour(start_window[0])
                <= base._hour(span.start_time)
                <= base._hour(start_window[1])
            )
        ]

    backend = Mock()
    backend.list_by_ids.side_effect = projected
    backend.remap_groups.return_value = {}
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: backend)
    assert control_plane.reconcile_tick()["bound"] == 3
    viewer = base.viewer_for(scope, user)
    reader = base.Reader(viewer, control_plane.read_spans)
    target = base.target_for(scope, main)
    survivor_target = base.target_for(scope, survivor)
    live = base.LiveSession(
        span_reader=lambda project_id, parent: spans.get(parent.span_id)
    )
    parent = occurrences.Parent(main.trace_id, main.span_id)
    assert reader.standalone_recording(object(), target, str(main.client_id))
    assert reader.recording_manifest(object(), target, recording_id) == body
    assert live.source(viewer, scope, parent) is not None
    return SimpleNamespace(
        scope=scope,
        main=main,
        segment=segment,
        survivor=survivor,
        session_id=session_id,
        survivor_session=survivor_session,
        spans=spans,
        backend=backend,
        reader=reader,
        viewer=viewer,
        live=live,
        parent=parent,
        target=target,
        survivor_target=survivor_target,
        recording_id=recording_id,
    )


def _assert_denied(r):
    assert r.reader.list_authorized(object(), (r.target,), per_parent_limit=64) == []
    assert (
        r.reader.standalone_recording(object(), r.target, str(r.main.client_id)) is None
    )
    assert r.reader.recording_manifest(object(), r.target, r.recording_id) is None
    assert r.live.source(r.viewer, r.scope, r.parent) is None
    for row in (r.main, r.segment):
        row.refresh_from_db()
        assert row.bound_at is not None and row.deleted_at is None


def _assert_survivor(r):
    statuses = r.reader.list_authorized(
        object(), (r.target, r.survivor_target), per_parent_limit=64
    )
    assert [s.reference["occurrence_id"] for s in statuses] == [
        str(r.survivor.client_id)
    ]
    assert r.reader.standalone_recording(
        object(), r.survivor_target, str(r.survivor.client_id)
    )


def _assert_late_binding_denied(setup, monkeypatch, r, *, survivor_live):
    pending = base.occurrence(setup, monkeypatch)
    survivor = base.occurrence_on(setup, monkeypatch, r.survivor.span_id)
    counts = control_plane.reconcile_tick()
    assert counts["bound"] == int(survivor_live)
    pending.refresh_from_db()
    survivor.refresh_from_db()
    assert pending.bound_at is None and pending.deleted_at is None
    assert (survivor.bound_at is not None) == survivor_live


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", ["pg", "overlay"])
def test_committed_session_marker_denies_stale_ch_without_cleanup(
    parity, setup, monkeypatch, kind
):
    r = parity
    assert transaction.get_autocommit()
    assert not Trace._base_manager.filter(id=r.main.trace_id).exists()
    with transaction.atomic():
        _marker(r.scope, r.session_id, kind)
    # CH still has the same live parent, and the occurrence has not been swept.
    assert not r.spans[r.main.span_id].is_deleted
    _assert_denied(r)
    _assert_survivor(r)
    _assert_late_binding_denied(setup, monkeypatch, r, survivor_live=True)


@pytest.mark.parametrize("kind", ["pg", "overlay"])
@pytest.mark.parametrize("marker_is_survivor", [True, False])
def test_remapped_marker_denies_all_metadata_and_binding(
    parity, setup, monkeypatch, kind, marker_is_survivor
):
    r = parity
    other = uuid4()
    _marker(r.scope, other, kind)
    survivor = str(other if marker_is_survivor else r.session_id)
    r.backend.remap_groups.return_value = {
        str(r.session_id): survivor,
        str(other): survivor,
    }
    _assert_denied(r)
    _assert_survivor(r)
    _assert_late_binding_denied(setup, monkeypatch, r, survivor_live=True)


@pytest.mark.parametrize("outage", ["markers", "remap", "spans"])
def test_unavailable_authority_never_uses_stale_pg_rows(
    parity, setup, monkeypatch, outage
):
    r = parity
    if outage == "markers":
        monkeypatch.setattr(
            session_deletion,
            "deleted_sessions",
            Mock(side_effect=RuntimeError("unavailable")),
        )
    elif outage == "remap":
        _marker(r.scope, uuid4())
        r.backend.remap_groups.side_effect = RuntimeError("unavailable")
    else:
        r.backend.list_by_ids.side_effect = RuntimeError("unavailable")
        r.live.span_reader = Mock(side_effect=RuntimeError("unavailable"))
    _assert_denied(r)
    _assert_late_binding_denied(setup, monkeypatch, r, survivor_live=False)


def test_foreign_project_marker_and_remap_do_not_deny_this_project(parity):
    r = parity
    other = uuid4()
    _marker(r.scope, other, project_id=uuid4())
    r.backend.remap_groups.return_value = {
        str(r.session_id): str(other),
        str(other): str(other),
    }
    r.backend.remap_groups.reset_mock()
    assert (
        len(r.reader.list_authorized(object(), (r.target,), per_parent_limit=64)) == 2
    )
    assert r.reader.standalone_recording(object(), r.target, str(r.main.client_id))
    assert r.reader.recording_manifest(object(), r.target, r.recording_id)
    assert r.live.source(r.viewer, r.scope, r.parent)
    r.backend.remap_groups.assert_not_called()


@pytest.mark.parametrize("field", ["project_id", "trace_id", "id"])
@pytest.mark.parametrize("deleted", [False, True])
def test_foreign_source_cannot_authorize_or_expire_true_parent(
    parity, setup, monkeypatch, field, deleted
):
    r = parity
    bad = copy(r.spans[r.main.span_id])
    setattr(bad, field, "2222222222222222" if field == "id" else uuid4())
    bad.is_deleted = deleted
    # Deliberately bypass CH filtering: defense in depth must validate fields,
    # not trust a dict key or a requested project as proof of returned scope.
    spans = {(str(r.main.trace_id), r.main.span_id): bad}
    r.reader.span_reader = lambda *args, **kwargs: spans
    r.live.span_reader = lambda *args: bad
    _assert_denied(r)
    pending = base.occurrence(setup, monkeypatch)
    counts = control_plane.reconcile_tick(span_reader=lambda *args, **kwargs: spans)
    assert counts["bound"] == 0 and counts["pending"] == 1
    pending.refresh_from_db()
    assert pending.bound_at is None and pending.deleted_at is None


def test_missing_ch_parent_does_not_fall_back_to_pg(parity, setup, monkeypatch):
    r = parity
    TraceSession.objects.create(id=r.session_id, project_id=r.scope.project_id)
    del r.spans[r.main.span_id]
    _assert_denied(r)
    _assert_survivor(r)
    _assert_late_binding_denied(setup, monkeypatch, r, survivor_live=True)


def test_malformed_session_denies_only_its_parent(parity, setup, monkeypatch):
    r = parity
    r.spans[r.main.span_id].trace_session_id = "not-a-session-uuid"
    _assert_denied(r)
    _assert_survivor(r)
    _assert_late_binding_denied(setup, monkeypatch, r, survivor_live=True)


def test_unknown_retention_denies_all_paths(parity, setup, monkeypatch):
    import tfc.ee_gates

    def unavailable(*args, **kwargs):
        raise tfc.ee_gates.RetentionPolicyUnavailable()

    monkeypatch.setattr(tfc.ee_gates, "trace_retention_cutoff", unavailable)
    _assert_denied(parity)
    _assert_late_binding_denied(setup, monkeypatch, parity, survivor_live=False)


def test_marker_and_remap_queries_are_project_batched(parity):
    r = parity
    _marker(r.scope, uuid4())  # Force remap resolution but leave both sources live.
    counts = []
    for targets in ((r.target,), (r.target, r.survivor_target)):
        r.backend.reset_mock()
        with CaptureQueriesContext(connection) as queries:
            statuses = r.reader.list_authorized(object(), targets, per_parent_limit=64)
        assert len(statuses) == len(targets) + 1  # main also has its recording segment
        counts.append(len(queries))
        assert all(
            '"tracer_attachmentrecording"."manifest"' not in q["sql"] for q in queries
        )
        r.backend.list_by_ids.assert_called_once()
        r.backend.remap_groups.assert_called_once()
        table, ids = r.backend.remap_groups.call_args.args
        assert table == "trace_session_id_remap"
        assert set(ids) == (
            {str(r.session_id)}
            if len(targets) == 1
            else {str(r.session_id), str(r.survivor_session)}
        )
        assert "trace_session_id" in r.backend.list_by_ids.call_args.kwargs["columns"]
    assert counts[0] == counts[1]


def test_binding_marker_and_remap_queries_are_project_batched(
    parity, setup, monkeypatch
):
    r = parity
    _marker(r.scope, uuid4())
    base.occurrence(setup, monkeypatch)
    base.occurrence_on(setup, monkeypatch, r.survivor.span_id)
    r.backend.reset_mock()
    assert control_plane.reconcile_tick()["bound"] == 2
    r.backend.list_by_ids.assert_called_once()
    r.backend.remap_groups.assert_called_once_with(
        "trace_session_id_remap", {str(r.session_id), str(r.survivor_session)}
    )


def test_failed_batch_lookup_is_not_retried_for_each_source(parity, monkeypatch):
    r = parity
    lookup = Mock(side_effect=RuntimeError("unavailable"))
    monkeypatch.setattr(session_deletion, "deleted_sessions", lookup)
    assert (
        r.reader.list_authorized(
            object(), (r.target, r.survivor_target), per_parent_limit=64
        )
        == []
    )
    lookup.assert_called_once_with(str(r.scope.project_id))


def test_batch_cache_never_leaks_to_next_read_or_live_gateway_recheck(
    parity, monkeypatch
):
    r = parity
    shared = Mock(wraps=authority.source_state)
    monkeypatch.setattr(authority, "source_state", shared)
    assert r.reader.list_authorized(object(), (r.target,), per_parent_limit=64)
    shared.assert_called()
    shared.reset_mock()
    assert r.live.source(r.viewer, r.scope, r.parent)
    shared.assert_called_once()
    _marker(r.scope, r.session_id)
    # Reuse the same reader and live authority instances, not fresh objects.
    _assert_denied(r)


def _same_span_rows(setup, monkeypatch):
    rows = []
    for _ in range(2):
        current, _ = base.core.session(setup)
        association = base.core.upload_body(setup[0], current)["association"]
        association["parent"]["trace_id"] = uuid4().hex
        rows.append(
            base.occurrence(
                setup, monkeypatch, current=current, association=association
            )
        )
    return rows


def _install_span_table(monkeypatch, rows):
    table = [base.span_for(row) for row in rows]

    def projected(ids, *, project_id, columns, trace_ids=None, **kwargs):
        return [
            {column: getattr(span, column, None) for column in columns}
            for span in table
            if span.id in ids
            and span.project_id == str(project_id)
            and (not trace_ids or span.trace_id in trace_ids)
        ]

    backend = Mock()
    backend.list_by_ids.side_effect = projected
    # The old unqualified point lookup returns just the first of the two rows.
    backend.get.side_effect = lambda *args, **kwargs: table[0]
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: backend)
    return table, backend


def test_reused_span_id_binds_and_reads_both_exact_parents(setup, user, monkeypatch):
    rows = _same_span_rows(setup, monkeypatch)
    table, backend = _install_span_table(monkeypatch, rows)
    scope = setup[0]
    viewer = base.viewer_for(scope, user)
    reader = base.Reader(viewer, control_plane.read_spans)
    targets = tuple(base.target_for(scope, row) for row in rows)
    assert control_plane.reconcile_tick()["bound"] == 2
    backend.list_by_ids.reset_mock()
    assert {
        s.target for s in reader.list_authorized(object(), targets, per_parent_limit=64)
    } == set(targets)
    backend.list_by_ids.assert_called_once()
    monkeypatch.setattr(
        authority.LiveAuthority, "session_is_live", lambda self, v: v == viewer
    )
    live = authority.LiveAuthority()
    for row in rows:
        parent = occurrences.Parent(row.trace_id, row.span_id)
        assert live.source(viewer, scope, parent).parent == parent
        assert (
            occurrences.get_occurrence(viewer, live, row.client_id, parent, ready=True)[
                0
            ].pk
            == row.pk
        )
        backend.list_by_ids.reset_mock()
        items = occurrences.list_occurrences(viewer, live, trace_id=str(row.trace_id))[
            "items"
        ]
        assert [item["reference"]["occurrence_id"] for item in items] == [
            str(row.client_id)
        ]
        backend.list_by_ids.assert_called_once()
    table.pop()
    assert (
        base.LiveSession().source(
            viewer, scope, occurrences.Parent(rows[1].trace_id, rows[1].span_id)
        )
        is None
    )
    assert {
        s.target for s in reader.list_authorized(object(), targets, per_parent_limit=64)
    } == {targets[0]}
    assert not occurrences.list_occurrences(
        viewer, live, trace_id=str(rows[1].trace_id)
    )["items"]
    with pytest.raises(base.AttachmentError, match="not_found"):
        occurrences.get_occurrence(
            viewer,
            live,
            rows[1].client_id,
            occurrences.Parent(rows[1].trace_id, rows[1].span_id),
        )


def test_reused_span_id_survives_repeated_orphan_sweeps(setup, monkeypatch):
    from tracer.services.attachments import lifecycle

    rows = _same_span_rows(setup, monkeypatch)
    scope = setup[0]
    # Prepare two independently bound parents (no batch-reader dependency).
    for row in rows:
        spans = base.Spans()
        spans.put(scope.project_id, base.span_for(row))
        assert control_plane.reconcile_tick(span_reader=spans)["bound"] == 1
    _, backend = _install_span_table(monkeypatch, rows)
    now = base.timezone.now()
    for step in (2, 4):
        counts = lifecycle.sweep_orphans(
            now=now + step * lifecycle.orphan_grace(),
            pg_reader=lambda *_: (set(), set()),
        )
        assert counts == {"checked": 2}
        for row in rows:
            row.refresh_from_db()
            assert row.source_missing_since is None and row.deleted_at is None
    checked = {row.pk: row.source_checked_at for row in rows}
    backend.list_by_ids.side_effect = RuntimeError("reader unavailable")
    assert lifecycle.sweep_orphans(
        now=now + 6 * lifecycle.orphan_grace(),
        pg_reader=lambda *_: pytest.fail("an unavailable reader is not a miss"),
    ) == {"read_failed": 1}
    for row in rows:
        row.refresh_from_db()
        assert row.source_checked_at == checked[row.pk]
        assert row.source_missing_since is None and row.deleted_at is None


def test_public_capture_completion_matches_both_enriched_forms(parity):
    r = parity
    page = occurrences.list_occurrences(
        r.viewer, r.live, trace_id=str(r.main.trace_id)
    )["items"]
    flags = {
        item["reference"]["occurrence_id"]: item["capture_complete"] for item in page
    }
    enriched = r.reader.list_authorized(object(), (r.target,), per_parent_limit=64)
    for row in (r.main, r.segment):
        assert flags[str(row.client_id)] is True
        assert (
            next(
                s.capture_complete
                for s in enriched
                if s.reference["occurrence_id"] == str(row.client_id)
            )
            is True
        )


def test_manifest_parent_read_is_trace_qualified(parity):
    r = parity
    assert r.reader.recording_manifest(object(), r.target, r.recording_id)
    assert r.backend.list_by_ids.call_args.kwargs["trace_ids"] == [str(r.main.trace_id)]
