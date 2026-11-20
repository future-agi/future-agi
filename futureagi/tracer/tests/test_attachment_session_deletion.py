"""Real PG and product DELETE; CH/storage/parser are explicit component doubles.

No PG Trace rows: collector sessions must deny existing grants immediately,
including on cleanup failure, and later erase only their own media.
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.utils import timezone

from accounts.models import Organization
from accounts.models.workspace import Workspace
from tracer.models.attachment import AttachmentOutbox
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession, TraceSessionOverlay
from tracer.services.attachments import lifecycle, occurrences, playback, retention
from tracer.services.attachments.authority import LiveAuthority
from tracer.services.attachments.interfaces import ObjectIdentity
from tracer.services.attachments.session_deletion import sweep_deleted_sessions
from tracer.tests import test_attachment_core as core
from tracer.tests import test_attachment_lifecycle as life
from tracer.tests import test_attachment_playback as media

setup = core.setup
parser_probe = life.parser_probe
pytestmark = [pytest.mark.django_db, pytest.mark.unit]


@pytest.fixture
def collector_recording(setup, user, monkeypatch, parser_probe):
    scope, _ = setup
    store = life.ErasableStore()
    upload = life._ready_upload(setup, store)
    row = upload.occurrence
    session_id = uuid.uuid4()
    span = SimpleNamespace(
        id=row.span_id,
        trace_id=row.trace_id,
        project_id=scope.project_id,
        trace_session_id=session_id,
        is_deleted=False,
        start_time=timezone.now() - timedelta(minutes=1),
    )
    viewer = occurrences.Viewer(
        user.id,
        uuid.uuid4(),
        scope.organization_id,
        scope.workspace_id,
        scope.project_id,
    )
    authority = LiveAuthority(span_reader=lambda *args: span)
    # Only application-session authentication is doubled; source authorization
    # uses the production reader and durable session deletion fence.
    monkeypatch.setattr(
        authority, "session_is_live", lambda candidate: candidate == viewer
    )
    parent = occurrences.Parent(row.trace_id, row.span_id)
    state = authority.source(viewer, scope, parent)
    assert state is not None
    retention.bind_retention(scope_id=scope.id, occurrence_id=row.id, source=state)
    session, cookie = playback.exchange(viewer, authority)
    items = [
        {
            "occurrence_id": str(row.client_id),
            "parent": {"trace_id": str(row.trace_id), "span_id": row.span_id},
            "representation": "original",
        }
    ]
    grant = playback.resolve(
        viewer, authority, items, media_session_id=str(session.id)
    )[0]["gateway_path"].rsplit("/", 1)[1]
    reader = Mock()
    reader.remap_groups.return_value = {}
    reader.list_by_ids.return_value = [
        {
            "id": row.span_id,
            "project_id": str(scope.project_id),
            "trace_id": str(row.trace_id),
            "trace_session_id": str(session_id),
        }
    ]
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
    writer = Mock()
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.curated_writer._get_client", lambda: writer
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.curated_writer._reset_client", lambda: None
    )
    # Keep _resolve_ch_session_fields real: its org/workspace gate must still run.
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.trace_session_dict_reader.resolve_session_fields",
        lambda ids: {
            str(session_id): {
                "project_id": str(scope.project_id),
                "external_session_id": "sdk-session",
            }
        },
    )
    asset = upload.attachment
    asset.refresh_from_db()
    assert asset.verified_blob_id is not None
    identity = ObjectIdentity(
        asset.storage_binding_id,
        "serving",
        asset.verified_storage_key,
        asset.verified_storage_version,
    )
    return SimpleNamespace(
        scope=scope,
        upload=upload,
        row=row,
        session_id=session_id,
        span=span,
        viewer=viewer,
        authority=authority,
        parent=parent,
        items=items,
        session=session,
        cookie=cookie,
        grant=grant,
        reader=reader,
        writer=writer,
        store=media.RangeStoreDouble(core.wav_bytes(), identity),
        objects=store,
    )


@pytest.mark.parametrize("pg_session", [False, True])
def test_collector_session_delete_denies_and_erases(
    collector_recording, auth_client, pg_session
):
    r = collector_recording
    if pg_session:
        TraceSession.objects.create(
            id=r.session_id, project_id=r.scope.project_id, name="SDK session"
        )
    assert not Trace._base_manager.filter(id=r.row.trace_id).exists()
    assert media.response(r, HTTP_RANGE="bytes=0-3").status_code == 206
    assert media.response(r, method="HEAD").status_code == 200
    response = auth_client.delete(f"/tracer/trace-session/{r.session_id}/")
    assert response.status_code == 204
    assert (
        playback.resolve(
            r.viewer, r.authority, r.items, media_session_id=str(r.session.id)
        )[0]["status"]
        == "not_found"
    )
    assert media.response(r, HTTP_RANGE="bytes=0-3").status_code == 404
    assert media.response(r, method="HEAD").status_code == 404
    r.row.refresh_from_db()
    assert r.row.deleted_at is not None
    assert AttachmentOutbox.objects.filter(
        upload=r.upload, event="erase", processed_at=None
    ).exists()
    # Denial precedes physical erasure, not the other way around.
    assert r.objects.objects
    assert lifecycle.drain_outbox(storage=r.objects)["erase_done"] == 1
    assert r.objects.objects == {}
    assert not Trace._base_manager.filter(id=r.row.trace_id).exists()


def test_cleanup_outage_cannot_reauthorize_and_sweep_retries(
    collector_recording, auth_client
):
    r = collector_recording
    r.reader.list_by_ids.side_effect = RuntimeError("CH read unavailable")
    r.writer.insert.side_effect = RuntimeError("CH write unavailable")
    assert (
        auth_client.delete(f"/tracer/trace-session/{r.session_id}/").status_code == 204
    )
    r.row.refresh_from_db()
    assert (
        r.row.deleted_at is None
    )  # Deliberate failed cleanup, not evidence of erasure.
    assert r.objects.objects
    assert (
        playback.resolve(
            r.viewer, r.authority, r.items, media_session_id=str(r.session.id)
        )[0]["status"]
        == "not_found"
    )
    assert media.response(r, HTTP_RANGE="bytes=0-3").status_code == 404
    assert media.response(r, method="HEAD").status_code == 404
    r.reader.list_by_ids.side_effect = None
    assert lifecycle.sweep()["session_occurrences_deleted"] == 1
    assert lifecycle.drain_outbox(storage=r.objects)["erase_done"] == 1
    assert not r.objects.objects


def test_foreign_project_marker_does_not_deny_or_erase(collector_recording):
    r = collector_recording
    TraceSessionOverlay._base_manager.create(
        project_id=uuid.uuid4(), trace_session_id=r.session_id, deleted=True
    )
    assert r.authority.source(r.viewer, r.scope, r.parent) is not None
    assert sweep_deleted_sessions(project_id=r.scope.project_id) == 0
    assert media.response(r, method="HEAD").status_code == 200
    r.row.refresh_from_db()
    assert r.row.deleted_at is None


def test_session_membership_is_exact_and_other_call_survives(
    collector_recording, auth_client
):
    r = collector_recording
    # A live, different session, even for the same project's parent, is not the
    # deleted session. Both the read fence and erase selection must honor it.
    other_session = uuid.uuid4()
    r.span.trace_session_id = other_session
    r.reader.list_by_ids.return_value[0]["trace_session_id"] = str(other_session)
    assert (
        auth_client.delete(f"/tracer/trace-session/{r.session_id}/").status_code == 204
    )
    assert media.response(r, method="HEAD").status_code == 200
    assert sweep_deleted_sessions(project_id=r.scope.project_id) == 0
    r.row.refresh_from_db()
    assert r.row.deleted_at is None
    assert not AttachmentOutbox.objects.filter(event="erase").exists()


@pytest.mark.parametrize("tenant", ["unknown", "other_org", "other_workspace"])
def test_inaccessible_session_delete_does_not_create_marker(
    collector_recording, auth_client, monkeypatch, tenant, user
):
    r = collector_recording
    project_id = uuid.uuid4()
    if tenant != "unknown":
        org = (
            Organization.objects.create(name="Other session tenant")
            if tenant == "other_org"
            else r.scope.organization
        )
        workspace = Workspace.objects.create(
            name="Other session workspace",
            organization=org,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        project = Project.objects.create(
            name="Other SDK project",
            organization=org,
            workspace=workspace,
            model_type=r.scope.project.model_type,
            trace_type="observe",
        )
        project_id = project.id
    # Keep the real tenant gate: even an existing foreign project must be 404.
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.trace_session_dict_reader.resolve_session_fields",
        lambda ids: {str(r.session_id): {"project_id": str(project_id)}},
    )
    assert (
        auth_client.delete(f"/tracer/trace-session/{r.session_id}/").status_code == 404
    )
    assert not TraceSessionOverlay._base_manager.filter(
        trace_session_id=r.session_id
    ).exists()
    r.writer.insert.assert_not_called()
    r.reader.list_by_ids.assert_not_called()
    assert not AttachmentOutbox.objects.filter(event="erase").exists()
    r.row.refresh_from_db()
    assert r.row.deleted_at is None
    assert media.response(r, method="HEAD").status_code == 200


def test_deleted_survivor_denies_and_erases_new_session_id(collector_recording):
    r = collector_recording
    survivor = uuid.uuid4()
    TraceSessionOverlay._base_manager.create(
        project_id=r.scope.project_id, trace_session_id=survivor, deleted=True
    )
    r.reader.remap_groups.return_value = {
        str(r.session_id): str(survivor),
        str(survivor): str(survivor),
    }
    assert media.response(r, method="HEAD").status_code == 404
    assert sweep_deleted_sessions(project_id=r.scope.project_id) == 1
    assert lifecycle.drain_outbox(storage=r.objects)["erase_done"] == 1
    assert not r.objects.objects


def test_remap_failure_denies_without_erasing(collector_recording):
    r = collector_recording
    TraceSessionOverlay._base_manager.create(
        project_id=r.scope.project_id, trace_session_id=uuid.uuid4(), deleted=True
    )
    r.reader.remap_groups.side_effect = RuntimeError("remap unavailable")
    assert media.response(r, method="HEAD").status_code == 404
    assert lifecycle.sweep()["session_occurrences_deleted_error"] == 1
    r.row.refresh_from_db()
    assert r.row.deleted_at is None and r.objects.objects


def test_orphan_scan_checks_session_marker_before_advancing_shared_cursor(
    collector_recording,
):
    r = collector_recording
    TraceSessionOverlay._base_manager.create(
        project_id=r.scope.project_id, trace_session_id=r.session_id, deleted=True
    )
    # A live CH span must not let the orphan scanner continually advance this
    # row past the separate session sweep's bounded cursor without cleanup.
    counts = lifecycle.sweep_orphans(
        now=timezone.now() + timedelta(hours=25),
        span_reader=lambda project_id, ids: {(r.row.trace_id, r.row.span_id)},
    )
    assert counts["session_occurrences_deleted"] == 1
    r.row.refresh_from_db()
    assert r.row.deleted_at is not None
    assert lifecycle.drain_outbox(storage=r.objects)["erase_done"] == 1
    assert not r.objects.objects


def test_cleanup_checks_trace_identity_not_only_span_id(
    collector_recording, auth_client
):
    r = collector_recording
    r.reader.list_by_ids.return_value[0]["trace_id"] = str(uuid.uuid4())
    assert (
        auth_client.delete(f"/tracer/trace-session/{r.session_id}/").status_code == 204
    )
    r.row.refresh_from_db()
    assert r.row.deleted_at is None
    assert not AttachmentOutbox.objects.filter(event="erase").exists()
    # Read-time check still has the exact authoritative source identity.
    assert media.response(r, method="HEAD").status_code == 404
