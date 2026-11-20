"""Actual reader -> manifest gate -> Voice projection, with synthetic ORM/authority IO.

No mocked recording_manifest return and no SQL/concurrent-revocation claim.
"""

import copy
import dataclasses
import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest
from django.db.models import Q

ROOT = Path(__file__).resolve().parent


def helpers(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = helpers(
    "_review_manifest_base", "test_attachment_simplify_representations_values.py"
)
life = helpers("_review_manifest_io", "test_attachment_simplify_lifecycle_behavior.py")
media_helpers = helpers(
    "_review_manifest_services",
    "test_attachment_simplify_review_representations_media.py",
)
service = media_helpers.service


class Query(life.Query):
    def filter(self, *args, **kwargs):
        # Django UUIDFields coerce UUID strings in filter predicates.
        kwargs = {
            key: UUID(value) if isinstance(value, str) and len(value) == 36 else value
            for key, value in kwargs.items()
        }
        return Query(super().filter(*args, **kwargs).rows)

    def select_related(self, *args):
        return self

    def first(self):
        return next(iter(self.rows), None)

    def values_list(self, name, flat=False):
        assert flat
        result = []
        for row in self.rows:
            for part in name.split("__"):
                row = getattr(row, part)
            result.append(row)
        return result


@pytest.fixture
def integrated(service):
    now = datetime.now(UTC)
    scope = NS(
        id=uuid4(),
        project_id=uuid4(),
        organization_id=uuid4(),
        workspace_id=uuid4(),
        enabled=True,
        policy_version=1,
        deletion_generation=0,
    )
    scope.workspace = NS(
        id=scope.workspace_id, is_active=True, organization_id=scope.organization_id
    )
    user = NS(
        id=uuid4(), pk=None, is_active=True, can_read_from_workspace=lambda w: True
    )
    user.pk = user.id
    project = NS(
        pk=scope.project_id,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        deleted=False,
    )
    viewer = NS(
        user_id=user.id,
        application_session_id=uuid4(),
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
    )
    timezone = NS(now=lambda: now, is_naive=lambda dt: dt.tzinfo is None)
    models = NS(
        AttachmentScope=NS(objects=Query([scope])),
        Project=NS(no_workspace_objects=Query([project])),
        User=NS(objects=Query([user])),
    )
    occurrence_env = life.load(
        "occurrences",
        {
            "Parent",
            "not_found",
            "scope_for",
            "source_for",
            "check_readable",
            "metadata",
        },
        dataclass=dataclasses.dataclass,
        trace_uuid=service.c.trace_uuid,
        span_id=service.c.span_id,
        settings=NS(FI_ATTACHMENTS_ENABLED=True),
        AttachmentError=service.c.AttachmentError,
        AttachmentScope=models.AttachmentScope,
        Project=models.Project,
        User=models.User,
        AttachmentState=NS(READY="ready", DELETED="deleted"),
        timezone=timezone,
        session_policy=lambda session: session.policy,
        admit_audio=lambda *a, **kw: None,
        PLACEMENT_FIELDS=service.c.PLACEMENT_FIELDS,
    )
    occurrences = NS(
        **{k: v for k, v in occurrence_env.items() if not k.startswith("__")}
    )
    parent = occurrences.Parent(uuid4(), "a" * 16)
    source = NS(
        scope_id=scope.id,
        parent=parent,
        readable=True,
        writable=True,
        generation=1,
        started_at=now - timedelta(hours=2),
        expires_at=now + timedelta(days=10),
    )
    manifest = base.manifest()
    manifest["segments"] = [
        {
            "occurrence_id": str(uuid4()),
            "index": i,
            "start_ms": i * 1000,
            "duration_ms": 1000,
        }
        for i in range(120)
    ]
    manifest["duration_ms"] = 120000
    recording = NS(
        client_id=UUID(manifest["recording_id"]),
        scope=scope,
        trace_id=parent.trace_id,
        span_id=parent.span_id,
        deleted_at=None,
        deletion_generation=0,
        manifest=manifest,
        capture_complete=True,
        state="ready",
        published_at=now,
    )
    rows, uploads = [], []
    for segment in manifest["segments"]:
        asset = NS(
            client_id=uuid4(),
            state="ready",
            deleted_at=None,
            scope_id=scope.id,
            policy_version=1,
            deletion_generation=0,
            source_kind="Bytes",
            modality="audio",
            mime_type="audio/wav",
        )
        session = NS(scope_id=scope.id, revoked_at=None, policy=NS())
        row = NS(
            id=uuid4(),
            client_id=UUID(segment["occurrence_id"]),
            scope=scope,
            scope_id=scope.id,
            trace_id=parent.trace_id,
            span_id=parent.span_id,
            recording=recording,
            recording_id=recording.client_id,
            attachment=asset,
            capture_session=session,
            bound_at=now,
            deleted_at=None,
            expires_at=source.expires_at,
            source_expires_at=source.expires_at,
            source_started_at=source.started_at,
            source_generation=1,
            policy_version=1,
            deletion_generation=0,
            direction="mixed",
            purpose="conversation_recording",
            capture_complete=True,
        )
        for key in service.c.PLACEMENT_FIELDS:
            setattr(row, key, None)
        rows.append(row)
        uploads.append(
            NS(scope=scope, recording=recording, state="ready", occurrence=row)
        )
    row_model = NS(objects=Query(rows))
    manifest_env = life.load(
        "manifests",
        {"read_manifest"},
        timezone=timezone,
        AttachmentError=service.c.AttachmentError,
        Parent=occurrences.Parent,
        scope_for=occurrences.scope_for,
        source_for=occurrences.source_for,
        check_readable=occurrences.check_readable,
        not_found=occurrences.not_found,
        AttachmentOccurrence=row_model,
        AttachmentRecording=NS(objects=Query([recording])),
        AttachmentUpload=NS(objects=Query(uploads)),
        AttachmentState=NS(READY="ready"),
        INTERNAL_SOURCES=service.c.INTERNAL_SOURCES,
        MANIFEST_UPLOAD_STATES=("pending_upload", "validating", "ready"),
    )
    source_map = {(parent.trace_id, parent.span_id): source}
    application = NS(live=True)

    def live_authority(**kw):
        return NS(session_is_live=lambda _: application.live)

    reads = sys.modules["tracer.services.attachments.reads"]
    env = life.load(
        "control_plane",
        {"_BatchSources", "_MemoAuthority", "_read_source_spans", "ControlPlaneReader"},
        settings=NS(FI_ATTACHMENTS_ENABLED=True),
        AttachmentError=service.c.AttachmentError,
        read_spans=lambda *a, **kw: {},
        Retention=lambda: None,
        occurrences=occurrences,
        authorize_parent_batch=lambda **kw: source_map,
        LiveAuthority=live_authority,
        Project=models.Project,
        principal=NS(identity=lambda r: viewer, project_viewer=lambda i, p: viewer),
        AttachmentOccurrence=row_model,
        Q=Q,
        MAX_READ_ROWS=10000,
        AuthorizedStatus=reads.AuthorizedStatus,
        StatusPage=reads.StatusPage,
        manifests=NS(read_manifest=manifest_env["read_manifest"]),
        timezone=timezone,
    )
    # _MemoAuthority keys viewers; use an immutable real-shaped viewer.
    from collections import namedtuple

    viewer = namedtuple("Viewer", viewer.__dict__)(**viewer.__dict__)
    reader = env["ControlPlaneReader"]()
    request = NS(organization=NS(id=scope.organization_id), workspace=scope.workspace)

    def project_result():
        result = {
            "project_id": str(scope.project_id),
            "observation_span": [
                {"trace_id": str(parent.trace_id), "id": parent.span_id}
            ],
        }
        service.v.enrich_voice_detail(request=request, result=result, reader=reader)
        return result

    return NS(
        rows=rows,
        row_model=row_model,
        manifest=manifest,
        recording=recording,
        source=source,
        source_map=source_map,
        application=application,
        project=project_result,
        reader=reader,
        request=request,
        scope=scope,
        parent=parent,
        now=now,
    )


def test_full_reader_and_projection_exceed_metadata_page(integrated):
    result = integrated.project()
    assert len(result["observation_span"][0]["attachments"]) == 64
    projection = result["recording_attachment"]
    assert projection["state"] == "ready"
    assert len(projection["manifest"]["segments"]) == 120
    assert projection["manifest"] == integrated.manifest


@pytest.mark.parametrize(
    "denial",
    [
        "missing",
        "deleted",
        "expired",
        "source_expired",
        "unbound",
        "session_revoked",
        "policy",
        "asset_policy",
        "generation",
        "source_generation",
        "source_started",
        "wrong_parent",
        "asset_deleted",
        "asset_pending",
        "wrong_recording",
    ],
)
def test_later_denial_denies_whole_manifest_not_gap(integrated, denial):
    row = integrated.rows[100]
    before = copy.deepcopy(integrated.manifest)
    if denial == "missing":
        integrated.row_model.objects.rows.remove(row)
    elif denial == "deleted":
        row.deleted_at = integrated.now
    elif denial == "expired":
        row.expires_at = integrated.now
    elif denial == "source_expired":
        row.source_expires_at = integrated.now
    elif denial == "unbound":
        row.bound_at = None
    elif denial == "session_revoked":
        row.capture_session.revoked_at = integrated.now
    elif denial == "policy":
        row.policy_version = 0
    elif denial == "asset_policy":
        row.attachment.policy_version = 0
    elif denial == "generation":
        row.deletion_generation = 99
    elif denial == "source_generation":
        row.source_generation = 99
    elif denial == "source_started":
        row.source_started_at -= timedelta(seconds=1)
    elif denial == "wrong_parent":
        row.span_id = "b" * 16
    elif denial == "asset_deleted":
        row.attachment.deleted_at = integrated.now
    elif denial == "asset_pending":
        row.attachment.state = "validating"
    else:
        row.recording = NS(**{**vars(integrated.recording), "client_id": uuid4()})
    result = integrated.project()
    assert len(result["observation_span"][0]["attachments"]) == 64
    projection = result["recording_attachment"]
    assert projection["state"] != "ready"
    assert "manifest" not in projection
    assert integrated.manifest == before


@pytest.mark.parametrize(
    "denial", ["source_missing", "source_denied", "application_logout"]
)
def test_current_source_and_application_authority(integrated, denial):
    if denial == "source_missing":
        integrated.source_map.clear()
    elif denial == "source_denied":
        integrated.source.readable = False
    else:
        integrated.application.live = False
    assert "recording_attachment" not in integrated.project()


def test_persisted_capture_gap_preserved_without_reindex(integrated):
    last = integrated.manifest["segments"].pop()
    integrated.manifest["gaps"] = [
        {
            "start_ms": last["start_ms"],
            "duration_ms": last["duration_ms"],
            "reason": "queue_full",
        }
    ]
    integrated.manifest["capture_complete"] = False
    integrated.recording.capture_complete = False
    before = copy.deepcopy(integrated.manifest)
    result = integrated.project()
    assert result["recording_attachment"]["state"] == "ready"
    assert result["recording_attachment"]["manifest"] == before
