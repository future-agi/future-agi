"""Pinned-dev locking and attachment hooks together, with real PostgreSQL locks.

CH, storage and media-session authentication use the explicit component doubles
from test_attachment_session_deletion; these are not SDK/browser acceptance.
"""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from django.db import DatabaseError, close_old_connections, connection, transaction
from rest_framework.exceptions import ValidationError

from tracer.models.attachment import AttachmentOutbox
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession, TraceSessionOverlay
from tracer.services.attachments import playback, session_deletion
from tracer.tests.test_attachment_session_deletion import (
    collector_recording,
    parser_probe,
    setup,
)
from tracer.views.trace_session import _soft_delete_trace_session_tree

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_reassigned_session_cannot_tombstone_attachment(collector_recording, project, monkeypatch):
    r = collector_recording
    session = TraceSession.no_workspace_objects.create(id=r.session_id, project_id=r.scope.project_id)
    TraceSession.no_workspace_objects.filter(pk=session.pk).update(project=project)
    sweep = Mock()
    tombstone = Mock()
    monkeypatch.setattr(session_deletion, "sweep_deleted_sessions", sweep)
    monkeypatch.setattr("tracer.services.attachments.retention.tombstone_deleted_traces", tombstone)
    with pytest.raises(ValidationError, match="ownership changed"):
        _soft_delete_trace_session_tree([session])
    sweep.assert_not_called()
    tombstone.assert_not_called()
    r.row.refresh_from_db()
    session.refresh_from_db()
    assert not session.deleted and r.row.deleted_at is None
    assert not AttachmentOutbox.objects.filter(event="erase").exists()
    assert playback.resolve(r.viewer, r.authority, r.items)[0]["status"] == "ready"


@pytest.mark.parametrize("foreign_trace", [False, True])
def test_pg_trace_hook_uses_only_locked_same_project_parents(
    collector_recording, project, monkeypatch, foreign_trace
):
    r = collector_recording
    session = TraceSession.no_workspace_objects.create(id=r.session_id, project_id=r.scope.project_id)
    parent = Trace.no_workspace_objects.create(
        id=r.row.trace_id, session=session,
        project_id=project.id if foreign_trace else r.scope.project_id,
    )
    # The PG hook must work without the separate CH session sweep repairing it.
    monkeypatch.setattr(session_deletion, "sweep_deleted_sessions", lambda **kwargs: 0)
    _soft_delete_trace_session_tree([session])
    parent.refresh_from_db()
    r.row.refresh_from_db()
    assert parent.deleted is not foreign_trace
    assert (r.row.deleted_at is not None) is not foreign_trace
    assert AttachmentOutbox.objects.filter(event="erase").exists() is not foreign_trace
    # The session marker denies grants independently of physical cleanup.
    assert playback.resolve(r.viewer, r.authority, r.items)[0]["status"] == "not_found"


@pytest.mark.django_db(transaction=True)
def test_project_lock_covers_session_marker_and_attachment_cleanup(collector_recording, monkeypatch):
    r = collector_recording
    session = TraceSession.no_workspace_objects.create(id=r.session_id, project_id=r.scope.project_id)
    real_sweep = session_deletion.sweep_deleted_sessions
    observed = []

    def competing_project_writer():
        close_old_connections()
        try:
            with transaction.atomic():
                Project._base_manager.select_for_update(nowait=True).get(pk=r.scope.project_id)
            return "unlocked"
        except DatabaseError as exc:
            return getattr(exc.__cause__, "sqlstate", None) or getattr(exc.__cause__, "pgcode", None)
        finally:
            connection.close()

    def checked_sweep(**kwargs):
        assert TraceSession._base_manager.get(pk=session.pk).deleted
        with ThreadPoolExecutor(max_workers=1) as pool:
            observed.append(pool.submit(competing_project_writer).result(timeout=5))
        return real_sweep(**kwargs)

    monkeypatch.setattr(session_deletion, "sweep_deleted_sessions", checked_sweep)
    _soft_delete_trace_session_tree([session])
    assert observed == ["55P03"]  # PostgreSQL lock_not_available, not a timing guess.
    r.row.refresh_from_db()
    assert r.row.deleted_at is not None
    assert playback.resolve(r.viewer, r.authority, r.items)[0]["status"] == "not_found"


def test_late_failure_rolls_back_marker_and_attachment_tombstone(collector_recording, monkeypatch):
    r = collector_recording
    session = TraceSession.no_workspace_objects.create(id=r.session_id, project_id=r.scope.project_id)
    parent = Trace.no_workspace_objects.create(id=r.row.trace_id, session=session, project_id=r.scope.project_id)
    monkeypatch.setattr("tracer.services.analysis_retention.retire_analysis_sources", Mock(side_effect=DatabaseError("late failure")))
    with pytest.raises(DatabaseError, match="late failure"):
        _soft_delete_trace_session_tree([session])
    session.refresh_from_db()
    parent.refresh_from_db()
    r.row.refresh_from_db()
    assert not session.deleted and not parent.deleted and r.row.deleted_at is None
    assert not AttachmentOutbox.objects.filter(event="erase").exists()
    assert playback.resolve(r.viewer, r.authority, r.items)[0]["status"] == "ready"


def test_ch_overlay_owner_race_never_erases_media(collector_recording, auth_client, project):
    r = collector_recording
    TraceSessionOverlay._base_manager.create(trace_session_id=r.session_id, project=project)
    assert auth_client.delete(f"/tracer/trace-session/{r.session_id}/").status_code == 400
    assert not TraceSessionOverlay._base_manager.get(trace_session_id=r.session_id).deleted
    assert not AttachmentOutbox.objects.filter(event="erase").exists()
    r.writer.insert.assert_not_called()
    r.row.refresh_from_db()
    assert r.row.deleted_at is None
