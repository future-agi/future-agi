"""HTTP acceptance for all 17 F01 attachment operations, through real URLs.

Real PostgreSQL, real application JWTs (``issue_tokens``) and the production
``LiveAuthority`` session check. Only the ClickHouse span read and object
storage are doubles: the span reader returns a fixed span for the test parent
and storage keeps bytes in memory. These tests do not qualify ClickHouse,
S3 or the decoder sandbox.

Every success body is validated against the vendored F01 schema; every error
body must be exactly ``{code, request_id}`` with a code allowed for its HTTP
class (SDK design 0.5 / BR-6).
"""

import io
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from django.test import Client
from django.utils import timezone

from accounts.models import OrgApiKey, User
from accounts.models.auth_token import AuthToken
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.services.token_service import issue_tokens
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tracer.models.attachment import (
    AttachmentBlob,
    AttachmentMediaSession,
    AttachmentOccurrence,
    AttachmentRecording,
    AttachmentScope,
    AttachmentUpload,
    AttachmentWriteGrant,
    CaptureSession,
)
from tracer.models.project import Project
from tracer.services.attachments import retention, validation, wire
from tracer.services.attachments.authority import SOURCE_GENERATION, LiveAuthority
from tracer.services.attachments.probe import probe
from tracer.services.attachments.storage import ObjectIdentity
from tracer.tests.test_attachment_core import wav_bytes
from tracer.views import attachments as views

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

ORIGIN = "https://app.example.test"
TRACE = "00112233445566778899aabbccddeeff"
SPAN = "0123456789abcdef"
OTHER_SPAN = "fedcba9876543210"
ALLOWED = {
    400: {"unsupported_version"},
    401: {"credential_scope_unsupported", "expired"},
    403: {
        "policy_denied",
        "redaction_unavailable",
        "recording_disabled",
        "correlation_unavailable",
        "credential_scope_unsupported",
    },
    404: {"not_found", "scope_unresolved"},
    409: {"conflict", "deleted", "expired", "policy_denied"},
    413: {"too_large"},
    429: {"throttled"},
    503: {"incomplete"},
}


class MemoryStore:
    """Quarantine + promotion + ranged reads, in memory. Not S3 qualification."""

    def __init__(self):
        self.objects = {}

    def put_quarantine(self, upload_id, stream, length):
        key = f"quarantine/{upload_id}"
        self.objects[key] = stream.read()
        assert len(self.objects[key]) == length
        return ObjectIdentity(key, "q-v1")

    def snapshot(self, identity, output, *, expected_length):
        import hashlib

        data = self.objects[identity.key]
        output.write(data)
        return hashlib.sha256(data).hexdigest()

    def promote(self, snapshot, *, upload_id, expected_length, expected_sha256):
        snapshot.seek(0)
        key = f"original/{upload_id}"
        self.objects[key] = snapshot.read()
        return ObjectIdentity(key, "o-v1")

    def open_range(self, identity, *, start, end, expected_length):
        return io.BytesIO(self.objects[identity.key][start : end + 1])


def span_double(state):
    def read(project_id, span_id):
        if span_id not in state.spans or str(project_id) != str(state.project_id):
            return None
        return SimpleNamespace(
            id=span_id,
            project_id=str(project_id),
            trace_id=TRACE,
            start_time=state.started_at,
            is_deleted=0,
        )

    return read


def assert_error(reply, status, code=None):
    assert reply.status_code == status, (reply.status_code, reply.content[:200])
    body = reply.json()
    assert set(body) == {"code", "request_id"}
    assert wire.is_valid("Error", body)
    assert body["code"] in ALLOWED[status]
    if code is not None:
        assert body["code"] == code
    assert reply["Cache-Control"] == "private, no-store"
    if status == 429:
        assert reply["Retry-After"]
    return body


def ok(reply, status, name=None):
    assert reply.status_code == status, (reply.status_code, reply.content[:300])
    assert reply["Cache-Control"] == "private, no-store"
    if name is None:
        return None
    body = reply.json()
    wire.validate(name, body)
    return body


def member(user, organization, workspace, *, role=OrganizationRoles.OWNER):
    level = Level.OWNER if role == OrganizationRoles.OWNER else Level.VIEWER
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=organization,
        defaults={"role": role, "level": level, "is_active": True},
    )
    workspace_role = (
        "Workspace Owner"
        if role == OrganizationRoles.OWNER
        else OrganizationRoles.WORKSPACE_VIEWER
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "role": workspace_role,
            "level": Level.OWNER
            if role == OrganizationRoles.OWNER
            else Level.WORKSPACE_VIEWER,
            "is_active": True,
            "organization_membership": membership,
        },
    )


@pytest.fixture
def api(project, user, settings, monkeypatch):
    settings.FI_ATTACHMENTS_ENABLED = True
    settings.FI_ATTACHMENTS_MEDIA_ORIGIN = ORIGIN
    settings.ALLOWED_HOSTS = ["app.example.test", "testserver"]
    scope = AttachmentScope.objects.create(
        project=project,
        workspace=project.workspace,
        organization=project.organization,
        enabled=True,
        require_media_redaction=False,
        record_audio=True,
    )
    state = SimpleNamespace(
        project_id=project.id,
        spans={SPAN, OTHER_SPAN},
        started_at=timezone.now() - timedelta(minutes=5),
    )
    store = MemoryStore()
    monkeypatch.setattr(views, "authority", lambda: LiveAuthority(span_double(state)))
    monkeypatch.setattr(views, "storage", lambda: store)
    monkeypatch.setattr(validation, "sandbox_probe", probe)  # parser double
    token = issue_tokens(user)["access"]
    return SimpleNamespace(
        scope=scope,
        project=project,
        user=user,
        token=token,
        state=state,
        store=store,
        client=Client(HTTP_HOST="app.example.test"),
    )


def auth(token):
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def key(prefix="k"):
    return {"HTTP_IDEMPOTENCY_KEY": f"{prefix}-{uuid.uuid4().hex}"}


def post(a, path, body, headers):
    import json

    return a.client.post(
        path, data=json.dumps(body), content_type="application/json", **headers
    )


def bootstrap(a, *, token=None, sid=None, nonce=None, **overrides):
    body = {
        "schema_versions": [1],
        "project_name": a.project.name,
        "project_type": a.project.trace_type,
        "capture_session_id": sid or str(uuid.uuid4()),
        "correlation_nonce": nonce or str(uuid.uuid4()),
        **overrides,
    }
    reply = post(
        a,
        "/tracer/v1/attachment-sessions",
        body,
        {**auth(token or a.token), **key("bs")},
    )
    return reply, body


def upload_request(a, session_body, **overrides):
    a.scope.refresh_from_db()
    body = {
        "schema_version": 1,
        "attachment_id": str(uuid.uuid4()),
        "occurrence_id": str(uuid.uuid4()),
        "association": {
            "capture_session_id": session_body["capture_session_id"],
            "correlation_nonce": session_body["correlation_nonce"],
            "parent": {"trace_id": TRACE, "span_id": SPAN},
        },
        "policy_version": str(a.scope.policy_version),
        "deletion_generation": a.scope.deletion_generation,
        "mime_type": "audio/wav",
        "modality": "audio",
        "direction": "mixed",
        "source_kind": "Bytes",
        "declared_bytes": len(wav_bytes()),
    }
    body.update(overrides)
    return body


def base(a, project=None):
    return f"/tracer/v1/projects/{(project or a.project).id}/attachments"


def initialize(a, *, token=None, session_body=None, idem=None, **overrides):
    if session_body is None:
        reply, session_body = bootstrap(a, token=token)
        ok(reply, 200, "BootstrapResponse")
    body = upload_request(a, session_body, **overrides)
    headers = {**auth(token or a.token)}
    headers["HTTP_IDEMPOTENCY_KEY"] = (
        idem or f"up-{uuid.UUID(body['occurrence_id']).hex}"
    )
    reply = post(a, f"{base(a)}/uploads", body, headers)
    return reply, body, session_body, headers


def put_content(a, upload, payload=None, *, capability=None, length=None):
    payload = wav_bytes() if payload is None else payload
    headers = {
        "HTTP_AUTHORIZATION": f"Bearer {capability or upload['write_capability']}"
    }
    if length is not None:
        headers["CONTENT_LENGTH"] = str(length)
    return a.client.put(
        f"{base(a)}/uploads/{upload['upload_id']}/content",
        data=payload,
        content_type="application/octet-stream",
        **headers,
    )


def complete(a, upload, *, path="complete", capability=None):
    a.scope.refresh_from_db()
    return post(
        a,
        f"{base(a)}/uploads/{upload['upload_id']}/{path}",
        {
            "policy_version": str(a.scope.policy_version),
            "deletion_generation": a.scope.deletion_generation,
        },
        {
            "HTTP_AUTHORIZATION": f"Bearer {capability or upload['write_capability']}",
            **key("cp"),
        },
    )


def ready_occurrence(a, **overrides):
    """Upload, validate and bind one WAV: the readable occurrence fixture."""
    reply, body, session_body, _ = initialize(a, **overrides)
    upload = ok(reply, 201, "UploadResponse")
    ok(put_content(a, upload), 200, "StatusResponse")
    ok(complete(a, upload), 202, "StatusResponse")
    row = AttachmentUpload.objects.get(pk=upload["upload_id"])
    validation.validate(row.id, scope_id=a.scope.id, storage=a.store)
    occurrence = AttachmentOccurrence.objects.get(pk=row.occurrence_id)
    viewer_authority = LiveAuthority(span_double(a.state))
    from tracer.services.attachments.occurrences import Parent

    parent = Parent(occurrence.trace_id, occurrence.span_id)
    source = viewer_authority.source(
        SimpleNamespace(user_id=a.user.id), a.scope, parent
    )
    assert source.generation == SOURCE_GENERATION
    retention.bind_retention(
        scope_id=a.scope.id, occurrence_id=occurrence.id, source=source
    )
    return upload, body, session_body, occurrence


def media_exchange(a, token=None, origin=ORIGIN, **extra):
    headers = {**auth(token or a.token), **extra}
    if origin is not None:
        headers["HTTP_ORIGIN"] = origin
    # Like the app's client: a POST with no body at all.
    return a.client.post(
        "/tracer/v1/attachment-media-sessions",
        data="",
        content_type="application/json",
        secure=True,
        **headers,
    )


def resolve(a, occurrence_id, *, span=SPAN, representation="original", token=None):
    return post(
        a,
        f"{base(a)}/resolve",
        {
            "items": [
                {
                    "occurrence_id": str(occurrence_id),
                    "parent": {"trace_id": TRACE, "span_id": span},
                    "representation": representation,
                }
            ]
        },
        auth(token or a.token),
    )


def other_tenant():
    organization = Organization.objects.create(name="Other tenant")
    stranger = User.objects.create_user(
        email=f"other-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Other",
        organization=organization,
        organization_role=OrganizationRoles.OWNER,
    )
    workspace = Workspace.objects.create(
        name="Other",
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=stranger,
    )
    member(stranger, organization, workspace)
    return SimpleNamespace(
        user=stranger,
        organization=organization,
        workspace=workspace,
        token=issue_tokens(stranger)["access"],
    )


# 1. bootstrapAttachmentSession -------------------------------------------------


def test_bootstrap_returns_server_policy_and_renews(api):
    reply, body = bootstrap(api)
    document = ok(reply, 200, "BootstrapResponse")
    assert document["scope"]["project_id"] == str(api.project.id)
    assert document["policy"]["allowed_modalities"] == [
        "audio",
        "document",
        "image",
        "video",
    ]
    assert document["policy"]["record_video"] is False
    assert document["policy"]["record_audio"] is True
    again, _ = bootstrap(
        api, sid=body["capture_session_id"], nonce=body["correlation_nonce"]
    )
    renewed = ok(again, 200, "BootstrapResponse")
    assert renewed["expires_at"] >= document["expires_at"]
    assert CaptureSession.objects.filter(scope=api.scope).count() == 1
    conflict, _ = bootstrap(api, sid=body["capture_session_id"])
    assert_error(conflict, 409, "conflict")


def test_bootstrap_credentials_and_feature_gate(api, settings):
    body = {
        "schema_versions": [1],
        "project_name": api.project.name,
        "project_type": api.project.trace_type,
        "capture_session_id": str(uuid.uuid4()),
        "correlation_nonce": str(uuid.uuid4()),
    }
    path = "/tracer/v1/attachment-sessions"
    assert_error(post(api, path, body, key()), 401, "credential_scope_unsupported")
    assert_error(post(api, path, body, auth(api.token)), 400, "unsupported_version")
    assert_error(
        post(api, path, body, {**auth("fiac1.forged.capability"), **key()}), 401
    )
    assert_error(
        post(api, path, {**body, "extra": 1}, {**auth(api.token), **key()}), 400
    )
    assert_error(
        post(api, path, {**body, "schema_versions": [2]}, {**auth(api.token), **key()}),
        400,
    )
    AttachmentScope.objects.filter(pk=api.scope.id).update(
        enabled=False, policy_version=2
    )
    assert_error(
        post(api, path, body, {**auth(api.token), **key()}), 403, "policy_denied"
    )
    settings.FI_ATTACHMENTS_ENABLED = False
    assert_error(post(api, path, body, {**auth(api.token), **key()}), 404)
    assert not CaptureSession.objects.exists()


def test_bootstrap_explicit_workspace_never_falls_back(api):
    stranger = other_tenant()
    for workspace in (str(uuid.uuid4()), str(stranger.workspace.id)):
        reply, _ = bootstrap(api, workspace_id=workspace)
        assert_error(reply, 404, "scope_unresolved")
    reply, _ = bootstrap(api, project_type="Observe")
    assert_error(reply, 404, "scope_unresolved")
    reply, _ = bootstrap(api, workspace_id=str(api.project.workspace_id))
    ok(reply, 200, "BootstrapResponse")


# 2. initializeAttachmentUpload --------------------------------------------------


def test_initialize_issues_capability_and_renews_on_replay(api):
    reply, body, session_body, headers = initialize(api)
    first = ok(reply, 201, "UploadResponse")
    assert first["reserved_bytes"] == len(wav_bytes())
    replay = ok(post(api, f"{base(api)}/uploads", body, headers), 201, "UploadResponse")
    # BR-1: same job and reservation, fresh capability, later-or-equal expiry.
    assert {k: replay[k] for k in ("upload_id", "attachment_id", "occurrence_id")} == {
        k: first[k] for k in ("upload_id", "attachment_id", "occurrence_id")
    }
    assert replay["write_capability"] != first["write_capability"]
    assert replay["expires_at"] >= first["expires_at"]
    changed = {**body, "declared_bytes": body["declared_bytes"] - 1}
    assert_error(post(api, f"{base(api)}/uploads", changed, headers), 409, "conflict")
    capability_as_identity = {
        **headers,
        "HTTP_AUTHORIZATION": f"Bearer {first['write_capability']}",
    }
    assert_error(post(api, f"{base(api)}/uploads", body, capability_as_identity), 401)


def travel(monkeypatch, seconds):
    """Moves ``django.utils.timezone.now`` forward; JWT checks use wall time."""
    start = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: start + timedelta(seconds=seconds))


def test_expired_capability_is_renewed_and_the_upload_finishes(api, monkeypatch):
    """T-RN-05/T-RN-07: 401 after capability expiry -> one replay -> fresh one."""
    reply, body, _, headers = initialize(api)
    first = ok(reply, 201, "UploadResponse")
    row = AttachmentUpload.objects.get(pk=first["upload_id"])
    # The job outlives its first 300 s capability.
    assert row.expires_at > datetime.fromisoformat(first["expires_at"])
    travel(monkeypatch, 301)
    assert_error(put_content(api, first), 401, "expired")
    renewed = ok(
        post(api, f"{base(api)}/uploads", body, headers), 201, "UploadResponse"
    )
    same = ("upload_id", "attachment_id", "occurrence_id", "reserved_bytes")
    assert {k: renewed[k] for k in same} == {k: first[k] for k in same}
    assert renewed["write_capability"] != first["write_capability"]
    assert renewed["expires_at"] > first["expires_at"]
    # The old capability stays dead; the renewed one carries the transfer.
    assert_error(put_content(api, first), 401, "expired")
    ok(put_content(api, renewed), 200, "StatusResponse")
    assert ok(complete(api, renewed), 202, "StatusResponse")["state"] == "validating"
    status = api.client.get(
        f"{base(api)}/uploads/{renewed['upload_id']}/status",
        HTTP_AUTHORIZATION=f"Bearer {renewed['write_capability']}",
    )
    assert ok(status, 200, "StatusResponse")["state"] == "validating"
    assert AttachmentUpload.objects.filter(scope=api.scope).count() == 1


def test_expired_capability_upload_can_still_be_aborted(api, monkeypatch):
    """A stuck pending segment is renewed, aborted, and its index is reused."""
    reply, session_body = bootstrap(api)
    ok(reply, 200, "BootstrapResponse")
    recording_id = str(uuid.uuid4())
    reply, body, _, headers = initialize(
        api, session_body=session_body, recording_id=recording_id, segment_index=0
    )
    first = ok(reply, 201, "UploadResponse")
    travel(monkeypatch, 301)
    assert_error(complete(api, first, path="abort"), 401, "expired")
    renewed = ok(
        post(api, f"{base(api)}/uploads", body, headers), 201, "UploadResponse"
    )
    aborted = ok(complete(api, renewed, path="abort"), 200, "StatusResponse")
    assert aborted["state"] == "deleted"
    assert AttachmentUpload.objects.get(pk=first["upload_id"]).state == "deleted"
    # BR-4: the segment index is free for a new occurrence of the recording.
    again, _, _, _ = initialize(
        api, session_body=session_body, recording_id=recording_id, segment_index=0
    )
    ok(again, 201, "UploadResponse")
    # A settled job is never renewed again.
    assert_error(post(api, f"{base(api)}/uploads", body, headers), 409, "conflict")


def test_renewal_stops_at_the_upload_horizon(api, monkeypatch):
    reply, body, _, headers = initialize(api)
    first = ok(reply, 201, "UploadResponse")
    travel(monkeypatch, 301)
    # Session still live; only the job's own lifetime has passed.
    AttachmentUpload.objects.filter(pk=first["upload_id"]).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert_error(post(api, f"{base(api)}/uploads", body, headers), 409, "expired")
    assert AttachmentUpload.objects.get(pk=first["upload_id"]).state == (
        "pending_upload"
    )


def test_renewed_capability_never_outlives_the_upload_horizon(api):
    reply, body, _, headers = initialize(api)
    first = ok(reply, 201, "UploadResponse")
    horizon = timezone.now() + timedelta(seconds=30)
    AttachmentUpload.objects.filter(pk=first["upload_id"]).update(expires_at=horizon)
    renewed = ok(
        post(api, f"{base(api)}/uploads", body, headers), 201, "UploadResponse"
    )
    assert datetime.fromisoformat(renewed["expires_at"]) == horizon


@pytest.mark.parametrize(
    "scope_change,overrides,status,code",
    [
        ({"require_media_redaction": True}, {}, 403, "redaction_unavailable"),
        (
            {"record_audio": False},
            {"recording_id": str(uuid.uuid4()), "segment_index": 0},
            403,
            "recording_disabled",
        ),
        # Declared types the server cannot validate are refused, never stored.
        (
            {},
            {"modality": "file", "mime_type": "application/octet-stream"},
            403,
            "policy_denied",
        ),
        ({}, {"mime_type": "audio/L16"}, 403, "policy_denied"),
        # Not in the F01 MIME enum at all: refused by the schema.
        (
            {},
            {"modality": "image", "mime_type": "image/gif"},
            400,
            "unsupported_version",
        ),
        (
            {},
            {"modality": "image", "mime_type": "image/svg+xml"},
            400,
            "unsupported_version",
        ),
        # Only audio forms a recording, and its segments stay PCM WAV.
        (
            {},
            {
                "modality": "video",
                "mime_type": "video/mp4",
                "recording_id": str(uuid.uuid4()),
                "segment_index": 0,
            },
            403,
            "recording_disabled",
        ),
        (
            {},
            {
                "mime_type": "audio/ogg",
                "recording_id": str(uuid.uuid4()),
                "segment_index": 0,
            },
            403,
            "policy_denied",
        ),
        ({}, {"source_kind": "RemoteUrl"}, 403, "policy_denied"),
        ({}, {"policy_version": "999"}, 409, "expired"),
        ({}, {"declared_bytes": 9 * 1024 * 1024}, 413, "too_large"),
        (
            {},
            {"modality": "audio", "mime_type": "image/png"},
            400,
            "unsupported_version",
        ),
    ],
)
def test_initialize_privacy_denials(api, scope_change, overrides, status, code):
    reply, session_body = bootstrap(api)
    ok(reply, 200, "BootstrapResponse")
    if scope_change:
        current = AttachmentScope.objects.get(pk=api.scope.id)
        for field, value in scope_change.items():
            setattr(current, field, value)
        current.policy_version += 1
        current.save()
        if "require_media_redaction" in scope_change or "record_audio" in scope_change:
            # Policy changes end the old session; bootstrap again under them.
            reply, session_body = bootstrap(api)
            ok(reply, 200, "BootstrapResponse")
    reply, _, _, _ = initialize(api, session_body=session_body, **overrides)
    assert_error(reply, status, code)
    assert not AttachmentUpload.objects.exists()


def test_initialize_needs_writers_own_session(api):
    reply, session_body = bootstrap(api)
    ok(reply, 200, "BootstrapResponse")
    forged = {**session_body, "correlation_nonce": str(uuid.uuid4())}
    reply, _, _, _ = initialize(api, session_body=forged)
    assert_error(reply, 403, "correlation_unavailable")


def test_tenant_and_role_isolation_on_writes(api):
    stranger = other_tenant()
    reply, _, _, _ = initialize(api, token=None)
    ok(reply, 201, "UploadResponse")
    reply, session_body = bootstrap(api)
    body = upload_request(api, session_body)
    # A stranger cannot tell this project exists.
    assert_error(
        post(api, f"{base(api)}/uploads", body, {**auth(stranger.token), **key("up")}),
        404,
        "not_found",
    )
    # A foreign project id is indistinguishable from a missing one.
    foreign = Project.no_workspace_objects.create(
        name="foreign",
        trace_type="experiment",
        organization=stranger.organization,
        workspace=stranger.workspace,
        model_type=api.project.model_type,
    )
    assert_error(
        post(api, f"{base(api, foreign)}/uploads", body, {**auth(api.token), **key()}),
        404,
        "not_found",
    )
    assert_error(
        post(
            api,
            f"/tracer/v1/projects/{uuid.uuid4()}/attachments/uploads",
            body,
            {**auth(api.token), **key()},
        ),
        404,
        "not_found",
    )
    viewer = User.objects.create_user(
        email=f"viewer-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Viewer",
        organization=api.project.organization,
        organization_role=OrganizationRoles.MEMBER_VIEW_ONLY,
    )
    member(
        viewer,
        api.project.organization,
        api.project.workspace,
        role=OrganizationRoles.MEMBER_VIEW_ONLY,
    )
    reply, _ = bootstrap(api, token=issue_tokens(viewer)["access"])
    assert_error(reply, 403, "credential_scope_unsupported")


def viewer_token(a):
    viewer = User.objects.create_user(
        email=f"viewer-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Viewer",
        organization=a.project.organization,
        organization_role=OrganizationRoles.MEMBER_VIEW_ONLY,
    )
    member(
        viewer,
        a.project.organization,
        a.project.workspace,
        role=OrganizationRoles.MEMBER_VIEW_ONLY,
    )
    assert not viewer.can_write_to_workspace(a.project.workspace)
    return issue_tokens(viewer)["access"]


def test_read_only_bootstrap_creates_no_rows(api):
    token = viewer_token(api)
    projects = Project.no_workspace_objects.count()
    scopes = AttachmentScope.objects.count()
    # A new project name: refused before ingestion's project creation runs.
    name = f"viewer-made-{uuid.uuid4().hex[:6]}"
    reply, _ = bootstrap(api, token=token, project_name=name)
    assert_error(reply, 403, "credential_scope_unsupported")
    # An existing project without an attachment scope: no scope row either.
    bare = Project.no_workspace_objects.create(
        name=f"bare-{uuid.uuid4().hex[:6]}",
        trace_type=api.project.trace_type,
        organization=api.project.organization,
        workspace=api.project.workspace,
        model_type=api.project.model_type,
    )
    reply, _ = bootstrap(api, token=token, project_name=bare.name)
    assert_error(reply, 403, "credential_scope_unsupported")
    assert Project.no_workspace_objects.count() == projects + 1  # only ``bare``
    assert not Project.no_workspace_objects.filter(name=name).exists()
    assert AttachmentScope.objects.count() == scopes
    assert not CaptureSession.objects.exists()


def test_ungranted_ingest_key_bootstrap_creates_no_rows(api):
    ingest = OrgApiKey.no_workspace_objects.create(
        type="ingest",
        organization_id=api.scope.organization_id,
        workspace_id=api.scope.workspace_id,
        simulation_agent_id=uuid.uuid4(),
        api_key=uuid.uuid4().hex,
        secret_key=uuid.uuid4().hex,
    )
    name = f"ingest-made-{uuid.uuid4().hex[:6]}"
    body = {
        "schema_versions": [1],
        "project_name": name,
        "project_type": api.project.trace_type,
        "capture_session_id": str(uuid.uuid4()),
        "correlation_nonce": str(uuid.uuid4()),
    }
    headers = {"HTTP_X_API_KEY": ingest.api_key, "HTTP_X_SECRET_KEY": ingest.secret_key}
    reply = post(api, "/tracer/v1/attachment-sessions", body, {**headers, **key()})
    assert_error(reply, 403, "credential_scope_unsupported")
    assert not Project.no_workspace_objects.filter(name=name).exists()
    assert not AttachmentScope.objects.filter(project__name=name).exists()


def test_writer_bootstrap_of_a_new_name_creates_a_disabled_scope(api):
    """Positive control: an eligible writer still gets ingestion's project."""
    name = f"writer-made-{uuid.uuid4().hex[:6]}"
    reply, _ = bootstrap(api, project_name=name)
    assert_error(reply, 403, "policy_denied")
    project = Project.no_workspace_objects.get(name=name)
    scope = AttachmentScope.objects.get(project=project)
    assert scope.enabled is False
    assert not CaptureSession.objects.filter(scope=scope).exists()


def test_ingest_key_needs_explicit_grant(api):
    ingest = OrgApiKey.no_workspace_objects.create(
        type="ingest",
        organization_id=api.scope.organization_id,
        workspace_id=api.scope.workspace_id,
        simulation_agent_id=uuid.uuid4(),
        api_key=uuid.uuid4().hex,
        secret_key=uuid.uuid4().hex,
    )
    headers = {"HTTP_X_API_KEY": ingest.api_key, "HTTP_X_SECRET_KEY": ingest.secret_key}
    body = {
        "schema_versions": [1],
        "project_name": api.project.name,
        "project_type": api.project.trace_type,
        "capture_session_id": str(uuid.uuid4()),
        "correlation_nonce": str(uuid.uuid4()),
    }
    path = "/tracer/v1/attachment-sessions"
    assert_error(
        post(api, path, body, {**headers, **key()}), 403, "credential_scope_unsupported"
    )
    AttachmentWriteGrant.objects.create(
        scope=api.scope,
        api_key=ingest,
        granted_by=api.user,
        enabled=True,
        expires_at=timezone.now() + timedelta(minutes=5),
    )
    ok(post(api, path, body, {**headers, **key()}), 200, "BootstrapResponse")
    upload = upload_request(api, body)
    ok(
        post(api, f"{base(api)}/uploads", upload, {**headers, **key("up")}),
        201,
        "UploadResponse",
    )
    # An ingest key is never a viewer.
    assert_error(
        api.client.get(f"{base(api)}/occurrences", {"trace_id": TRACE}, **headers),
        403,
        "credential_scope_unsupported",
    )


# 3-6. content, complete, abort, status -------------------------------------------


def test_content_complete_status_and_replay(api):
    reply, _, _, _ = initialize(api)
    upload = ok(reply, 201, "UploadResponse")
    assert_error(put_content(api, upload, capability=api.token), 401)
    assert_error(put_content(api, upload, wav_bytes()[:-1]), 400)
    status = ok(put_content(api, upload), 200, "StatusResponse")
    assert status == {
        "upload_id": upload["upload_id"],
        "state": "pending_upload",
        "reason": "queued",
    }
    # BR-3: identical replay succeeds; different bytes conflict.
    ok(put_content(api, upload), 200, "StatusResponse")
    altered = bytearray(wav_bytes())
    altered[-1] ^= 1
    assert_error(put_content(api, upload, bytes(altered)), 409, "conflict")
    path = f"{base(api)}/uploads/{upload['upload_id']}"
    no_key = post(
        api,
        f"{path}/complete",
        {"policy_version": "1", "deletion_generation": 0},
        {"HTTP_AUTHORIZATION": f"Bearer {upload['write_capability']}"},
    )
    assert_error(no_key, 400)
    stale = post(
        api,
        f"{path}/complete",
        {"policy_version": "77", "deletion_generation": 0},
        {"HTTP_AUTHORIZATION": f"Bearer {upload['write_capability']}", **key()},
    )
    assert_error(stale, 409, "expired")
    done = ok(complete(api, upload), 202, "StatusResponse")
    assert done["state"] == "validating"
    status = api.client.get(
        f"{path}/status", HTTP_AUTHORIZATION=f"Bearer {upload['write_capability']}"
    )
    assert ok(status, 200, "StatusResponse")["state"] == "validating"
    # A capability is bound to its own upload.
    reply, _, _, _ = initialize(api)
    other = ok(reply, 201, "UploadResponse")
    assert_error(
        api.client.get(
            f"{path}/status", HTTP_AUTHORIZATION=f"Bearer {other['write_capability']}"
        ),
        404,
        "not_found",
    )


def test_abort_releases_and_segment_index_is_reusable(api):
    reply, session_body = bootstrap(api)
    ok(reply, 200, "BootstrapResponse")
    recording_id = str(uuid.uuid4())
    first, _, _, _ = initialize(
        api, session_body=session_body, recording_id=recording_id, segment_index=0
    )
    first = ok(first, 201, "UploadResponse")
    duplicate, _, _, _ = initialize(
        api, session_body=session_body, recording_id=recording_id, segment_index=0
    )
    assert_error(duplicate, 409, "conflict")
    aborted = ok(complete(api, first, path="abort"), 200, "StatusResponse")
    assert aborted["state"] == "deleted"
    ok(complete(api, first, path="abort"), 200, "StatusResponse")
    # BR-4: the index is free again after abort, for a NEW occurrence.
    again, _, _, _ = initialize(
        api, session_body=session_body, recording_id=recording_id, segment_index=0
    )
    ok(again, 201, "UploadResponse")


# 7-8. imports --------------------------------------------------------------------


def test_imports_refuse_without_fetching(api):
    body = {
        "mode": "completed_recording",
        "recording": {
            "schema_version": 1,
            "recording_id": str(uuid.uuid4()),
            "association": {
                "capture_session_id": str(uuid.uuid4()),
                "correlation_nonce": str(uuid.uuid4()),
                "parent": {"trace_id": TRACE, "span_id": SPAN},
            },
            "policy_version": "1",
            "deletion_generation": 0,
            "mime_type": "audio/wav",
            "modality": "audio",
            "direction": "mixed",
            "source_kind": "ProviderFile",
            "declared_bytes": 1000,
            "declared_duration_ms": 1000,
        },
        "source": {
            "kind": "ProviderFile",
            "connection_id": str(uuid.uuid4()),
            "registered_output_id": str(uuid.uuid4()),
        },
    }
    wire.validate("ImportRequest", body)
    assert_error(
        post(api, f"{base(api)}/imports", body, {**auth(api.token), **key("im")}),
        403,
        "policy_denied",
    )
    assert_error(
        post(api, f"{base(api)}/imports", {"source": {}}, {**auth(api.token), **key()}),
        400,
    )
    assert_error(post(api, f"{base(api)}/imports", body, key()), 401)
    assert_error(
        api.client.get(f"{base(api)}/imports/{uuid.uuid4()}/status", **auth(api.token)),
        404,
        "not_found",
    )


# 9-13. occurrences, link, resolve, delete, manifest ----------------------------------


def test_list_link_resolve_delete_through_live_authority(api):
    upload, body, _, occurrence = ready_occurrence(api)
    page = ok(
        api.client.get(
            f"{base(api)}/occurrences",
            {"trace_id": TRACE, "span_id": SPAN},
            **auth(api.token),
        ),
        200,
        "OccurrencePage",
    )
    assert [item["reference"]["occurrence_id"] for item in page["items"]] == [
        body["occurrence_id"]
    ]
    linked_id = str(uuid.uuid4())
    link = {
        "source_occurrence_id": body["occurrence_id"],
        "source_parent": {"trace_id": TRACE, "span_id": SPAN},
        "target_parent": {"trace_id": TRACE, "span_id": OTHER_SPAN},
        "occurrence_id": linked_id,
        "policy_version": str(api.scope.policy_version),
        "deletion_generation": api.scope.deletion_generation,
    }
    linked = ok(
        post(api, f"{base(api)}/occurrences", link, {**auth(api.token), **key()}),
        201,
        "Occurrence",
    )
    assert linked["parent"]["span_id"] == OTHER_SPAN
    # Resolve needs a media session first, then issues a same-origin grant.
    first = ok(resolve(api, body["occurrence_id"]), 200, "ResolveResponse")
    assert first["items"][0]["status"] == "not_found"
    ok(media_exchange(api), 204)
    resolved = ok(resolve(api, body["occurrence_id"]), 200, "ResolveResponse")
    assert resolved["items"][0]["status"] == "ready"
    wrong_parent = ok(
        resolve(api, body["occurrence_id"], span=OTHER_SPAN), 200, "ResolveResponse"
    )
    assert wrong_parent["items"][0]["status"] == "not_found"
    for representation in ("channel", "peaks", "thumbnail"):
        if representation == "channel":
            continue
        reply = ok(
            resolve(api, body["occurrence_id"], representation=representation),
            200,
            "ResolveResponse",
        )
        assert reply["items"][0]["status"] == "not_found"
    delete = api.client.delete(
        f"{base(api)}/occurrences/{body['occurrence_id']}?trace_id={TRACE}&span_id={SPAN}",
        **auth(api.token),
    )
    ok(delete, 204)
    gone = ok(resolve(api, body["occurrence_id"]), 200, "ResolveResponse")
    assert gone["items"][0]["status"] == "not_found"
    # The linked survivor is still readable.
    survivor = ok(resolve(api, linked_id, span=OTHER_SPAN), 200, "ResolveResponse")
    assert survivor["items"][0]["status"] == "ready"


def test_viewer_routes_refuse_capabilities_keys_and_strangers(api):
    upload, body, _, _ = ready_occurrence(api)
    stranger = other_tenant()
    for headers, status in (
        ({"HTTP_AUTHORIZATION": f"Bearer {upload['write_capability']}"}, 401),
        (auth(stranger.token), 404),
        ({}, 401),
    ):
        assert_error(
            api.client.get(f"{base(api)}/occurrences", {"trace_id": TRACE}, **headers),
            status,
        )
        assert_error(
            post(
                api,
                f"{base(api)}/resolve",
                {
                    "items": [
                        {
                            "occurrence_id": body["occurrence_id"],
                            "parent": {"trace_id": TRACE, "span_id": SPAN},
                            "representation": "original",
                        }
                    ]
                },
                headers,
            ),
            status,
        )
    assert_error(
        api.client.get(
            f"{base(api)}/occurrences",
            {"trace_id": TRACE, "cursor": "forged:cursor"},
            **auth(api.token),
        ),
        400,
    )


def test_manifest_write_replay_and_read(api):
    reply, session_body = bootstrap(api)
    ok(reply, 200, "BootstrapResponse")
    recording_id = str(uuid.uuid4())
    segments = []
    for index in range(2):
        upload, body, _, occurrence = ready_occurrence(
            api,
            session_body=session_body,
            recording_id=recording_id,
            segment_index=index,
        )
        segments.append((upload, body))
    track = str(uuid.uuid4())
    manifest = {
        "schema_version": 1,
        "recording_id": recording_id,
        "timebase": "session_relative_ms",
        "sample_format": "s16le",
        "sample_rate_hz": 24000,
        "channels": 2,
        "codec": "pcm",
        "container": "wav",
        "layout": "stereo",
        "tracks": [
            {
                "track_id": track,
                "channels": [0, 1],
                "direction": "mixed",
                "role": "mixed",
            }
        ],
        "segments": [
            {
                "occurrence_id": segments[0][1]["occurrence_id"],
                "index": 0,
                "start_ms": 0,
                "duration_ms": 10,
            },
            {
                "occurrence_id": segments[1][1]["occurrence_id"],
                "index": 1,
                "start_ms": 10,
                "duration_ms": 10,
            },
        ],
        "gaps": [],
        "playout": [],
        "capture_complete": True,
        "duration_ms": 20,
    }
    path = f"{base(api)}/recordings/{recording_id}/manifest"
    # BR-7: the second segment's capability still writes after its own complete.
    capability = segments[1][0]["write_capability"]
    headers = {
        "HTTP_AUTHORIZATION": f"Bearer {capability}",
        "HTTP_IDEMPOTENCY_KEY": f"mf-{uuid.UUID(recording_id).hex}-1",
    }
    import json

    def put(body, hdrs):
        return api.client.put(
            path, data=json.dumps(body), content_type="application/json", **hdrs
        )

    ok(put(manifest, headers), 204)
    ok(put(manifest, headers), 204)  # BR-5: identical replay.
    changed = {
        **manifest,
        "playout": [{"start_ms": 0, "duration_ms": 5, "evidence": "played"}],
    }
    assert_error(put(changed, headers), 409, "conflict")
    foreign = {
        **manifest,
        "segments": [
            {**manifest["segments"][0], "occurrence_id": str(uuid.uuid4())},
            manifest["segments"][1],
        ],
    }
    assert_error(
        put(foreign, {**headers, "HTTP_IDEMPOTENCY_KEY": "mf-x-2"}), 409, "conflict"
    )
    assert_error(
        put(manifest, {**headers, "HTTP_AUTHORIZATION": f"Bearer {api.token}"}), 401
    )
    # A capability without this recording cannot write it.
    reply, _, _, _ = initialize(api)
    plain = ok(reply, 201, "UploadResponse")
    assert_error(
        put(
            manifest,
            {**headers, "HTTP_AUTHORIZATION": f"Bearer {plain['write_capability']}"},
        ),
        404,
    )
    query = {"trace_id": TRACE, "span_id": SPAN}
    read = ok(api.client.get(path, query, **auth(api.token)), 200, "RecordingManifest")
    assert read["capture_complete"] is True and len(read["segments"]) == 2
    assert_error(
        api.client.get(
            path, {"trace_id": TRACE, "span_id": OTHER_SPAN}, **auth(api.token)
        ),
        404,
    )
    # Deleting one segment's occurrence turns it into an incomplete gap.
    api.client.delete(
        f"{base(api)}/occurrences/{segments[0][1]['occurrence_id']}?trace_id={TRACE}&span_id={SPAN}",
        **auth(api.token),
    )
    read = ok(api.client.get(path, query, **auth(api.token)), 200, "RecordingManifest")
    assert read["capture_complete"] is False and len(read["segments"]) == 1
    assert read["gaps"] == [{"start_ms": 0, "duration_ms": 10, "reason": "incomplete"}]
    assert (
        AttachmentRecording.objects.get(client_id=recording_id).manifest_revision == 1
    )


# 14-17. media session and gateway ----------------------------------------------------


def gateway(a, path, method="get", cookie=None, **headers):
    if cookie is not None:
        a.client.cookies["fi_media_session"] = cookie
    return getattr(a.client, method)(path, secure=True, **headers)


def test_media_session_and_gateway_bytes(api):
    upload, body, _, _ = ready_occurrence(api)
    assert_error(media_exchange(api, origin=None), 404)
    assert_error(media_exchange(api, origin="https://evil.example.test"), 404)
    exchange = media_exchange(api)
    ok(exchange, 204)
    cookie = exchange.cookies["fi_media_session"]
    assert cookie["httponly"] and cookie["secure"] and cookie["samesite"] == "Strict"
    assert cookie["path"] == "/tracer/v1/attachment-media/"
    item = ok(resolve(api, body["occurrence_id"]), 200, "ResolveResponse")["items"][0]
    path = item["gateway_path"]
    full = gateway(api, path)
    assert full.status_code == 200 and b"".join(full.streaming_content) == wav_bytes()
    assert full["Cache-Control"] == "private, no-store"
    partial = gateway(api, path, HTTP_RANGE="bytes=0-9")
    assert partial.status_code == 206
    assert b"".join(partial.streaming_content) == wav_bytes()[:10]
    head = gateway(api, path, method="head")
    assert head.status_code == 200 and head["Content-Length"] == str(len(wav_bytes()))
    assert gateway(api, path, HTTP_IF_NONE_MATCH=head["ETag"]).status_code == 304
    assert gateway(api, path, HTTP_RANGE="bytes=99999-").status_code == 416
    # A copied grant under another (or no) media session fails.
    api.client.cookies.pop("fi_media_session")
    assert gateway(api, path).status_code == 404
    other = media_exchange(api).cookies["fi_media_session"].value
    assert gateway(api, path, cookie=other).status_code == 404


def test_gateway_dies_with_application_session_and_egress_limit(api, monkeypatch):
    upload, body, _, _ = ready_occurrence(api)
    ok(media_exchange(api), 204)
    path = ok(resolve(api, body["occurrence_id"]), 200, "ResolveResponse")["items"][0][
        "gateway_path"
    ]
    assert gateway(api, path, method="head").status_code == 200
    from tracer.services.attachments import egress

    # Pin the one-second window and spend this viewer's whole budget in it.
    monkeypatch.setattr(egress, "time", SimpleNamespace(time=lambda: 1_000_000.0))
    egress.consume(str(api.user.id), egress.LIMITS["playback_bytes_per_second"])
    limited = gateway(api, path)
    assert limited.status_code == 429 and limited["Retry-After"]
    assert limited["Cache-Control"] == "private, no-store"
    with pytest.raises(egress.EgressDenied):
        egress.consume(str(api.user.id), 1)
    # Another viewer's budget is separate.
    egress.consume(str(uuid.uuid4()), 1)
    # Logout deactivates the AuthToken row: the next request is denied.
    AuthToken.objects.filter(user=api.user, auth_type="access").update(is_active=False)
    assert gateway(api, path, method="head").status_code == 404
    assert AttachmentMediaSession.objects.exists() and AttachmentBlob.objects.exists()


def test_routes_are_dark_when_disabled(api, settings):
    settings.FI_ATTACHMENTS_ENABLED = False
    paths = [
        ("post", "/tracer/v1/attachment-sessions"),
        ("post", "/tracer/v1/attachment-media-sessions"),
        ("post", f"{base(api)}/uploads"),
        ("put", f"{base(api)}/uploads/{uuid.uuid4()}/content"),
        ("post", f"{base(api)}/uploads/{uuid.uuid4()}/complete"),
        ("post", f"{base(api)}/uploads/{uuid.uuid4()}/abort"),
        ("get", f"{base(api)}/uploads/{uuid.uuid4()}/status"),
        ("post", f"{base(api)}/imports"),
        ("get", f"{base(api)}/imports/{uuid.uuid4()}/status"),
        ("get", f"{base(api)}/occurrences"),
        ("post", f"{base(api)}/occurrences"),
        ("delete", f"{base(api)}/occurrences/{uuid.uuid4()}"),
        ("post", f"{base(api)}/resolve"),
        ("put", f"{base(api)}/recordings/{uuid.uuid4()}/manifest"),
        ("get", f"{base(api)}/recordings/{uuid.uuid4()}/manifest"),
    ]
    for method, path in paths:
        assert_error(getattr(api.client, method)(path, **auth(api.token)), 404)
    for method in ("get", "head"):
        reply = getattr(api.client, method)(
            f"/tracer/v1/attachment-media/{uuid.uuid4()}/{'a' * 43}", secure=True
        )
        assert reply.status_code == 404


def test_abort_never_deletes_a_completed_segment(api):
    reply, session_body = bootstrap(api)
    ok(reply, 200, "BootstrapResponse")
    recording_id = str(uuid.uuid4())
    upload, body, _, occurrence = ready_occurrence(
        api, session_body=session_body, recording_id=recording_id, segment_index=0
    )
    # A failed renewal must not let the SDK's abort erase a manifest segment.
    assert_error(complete(api, upload, path="abort"), 409, "conflict")
    row = AttachmentUpload.objects.get(pk=upload["upload_id"])
    assert row.state == "ready"
    assert not AttachmentOccurrence.objects.get(pk=occurrence.pk).deleted_at


def test_saml_session_revocation_ends_media_access(api, monkeypatch):
    from accounts.saml_credential import SamlCredentialInvalid
    from tracer.services.attachments.occurrences import Viewer

    token = AuthToken.objects.filter(user=api.user, auth_type="access").first()
    viewer = Viewer(
        api.user.id,
        token.id,
        api.scope.organization_id,
        api.scope.workspace_id,
        api.scope.project_id,
    )
    authority = LiveAuthority(span_double(api.state))
    assert authority.session_is_live(viewer)
    AuthToken.objects.filter(pk=token.pk).update(auth_origin="saml")
    calls = []

    def revoked(row, user_id, *, now, transport):
        calls.append(row["id"])
        raise SamlCredentialInvalid("idp_generation")

    monkeypatch.setattr("accounts.saml_credential.validate_saml_credential", revoked)
    assert not authority.session_is_live(viewer)
    assert calls == [token.pk]
