"""PostgreSQL + HTTP response components, with explicit source/session/storage doubles.

These do not claim real-login, ClickHouse authority, decoder isolation or S3
qualification. Negative controls mutate the same previously-readable fixture.
"""

import io
import json
import time
import uuid
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.test import RequestFactory
from django.utils import timezone

from accounts.models import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tracer.models.attachment import (
    AttachmentAuditEvent,
    AttachmentMediaSession,
    AttachmentOccurrence,
    AttachmentPlaybackGrant,
    AttachmentScope,
    CaptureSession,
)
from tracer.services.attachments import (
    occurrences,
    playback,
    retention,
    uploads,
    validation,
)
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.probe import probe
from tracer.tests import test_attachment_core as core
from tracer.tests.test_attachment_core import ImmutableStore, initialized, wav_bytes
from tracer.views.attachment_media import exchange_response, media_response

setup = core.setup

pytestmark = [pytest.mark.django_db, pytest.mark.unit]
ORIGIN = "https://app.example.test"


class AuthorityDouble:
    def __init__(self, viewer, state):
        self.viewer, self.state, self.live = viewer, state, True

    def session_is_live(self, viewer):
        return self.live and viewer == self.viewer

    def source(self, viewer, scope, parent):
        return self.state if parent == self.state.parent else None


class RangeStoreDouble:
    def __init__(self, payload):
        self.payload, self.bodies, self.calls = payload, [], []

    def open_range(self, identity, **kwargs):
        self.calls.append((identity, kwargs))
        body = io.BytesIO(self.payload[kwargs["start"] : kwargs["end"] + 1])
        self.bodies.append(body)
        return body


@pytest.fixture
def recording(setup, user, monkeypatch):
    scope, _ = setup
    upload, token, _, _ = initialized(setup)
    store = ImmutableStore()
    uploads.transfer(
        token,
        scope_id=scope.id,
        upload_id=upload.id,
        chunks=[wav_bytes()],
        storage=store,
    )
    uploads.complete(token, scope_id=scope.id, upload_id=upload.id)
    monkeypatch.setattr(
        validation, "sandbox_probe", probe
    )  # Explicit parser double, NOT sandbox qualification.
    blob = validation.validate(upload.id, scope_id=scope.id, storage=store)
    row = upload.occurrence
    parent = occurrences.Parent(row.trace_id, row.span_id)
    state = occurrences.SourceState(
        scope.id,
        parent,
        3,
        timezone.now() - timedelta(hours=1),
        timezone.now() + timedelta(hours=1),
        True,
        True,
    )
    retention.bind_retention(scope_id=scope.id, occurrence_id=row.id, source=state)
    viewer = occurrences.Viewer(
        user.id,
        uuid.uuid4(),
        scope.organization_id,
        scope.workspace_id,
        scope.project_id,
    )
    authority = AuthorityDouble(viewer, state)
    session, cookie = playback.exchange(viewer, authority)
    (result,) = playback.resolve(
        viewer,
        authority,
        [
            {
                "occurrence_id": str(row.client_id),
                "parent": {"trace_id": str(parent.trace_id), "span_id": parent.span_id},
                "representation": "original",
            }
        ],
    )
    return SimpleNamespace(
        scope=scope,
        upload=upload,
        row=row,
        parent=parent,
        viewer=viewer,
        authority=authority,
        session=session,
        cookie=cookie,
        grant=result["gateway_path"].rsplit("/", 1)[1],
        blob=blob,
        store=RangeStoreDouble(wav_bytes()),
    )


def response(r, *, method="GET", cookie=True, grant=None, **headers):
    request = RequestFactory().generic(
        method, "/tracer/v1/attachment-media/test", secure=True, **headers
    )
    if cookie:
        request.COOKIES[playback.COOKIE_NAME] = r.cookie if cookie is True else cookie
    return media_response(
        request,
        occurrence_id=r.row.client_id,
        grant_token=grant or r.grant,
        authority=r.authority,
        storage=r.store,
        origin=ORIGIN,
    )


def assert_readable(r):
    reply = response(r)
    assert reply.status_code == 200
    assert b"".join(reply.streaming_content) == wav_bytes()
    # Exhaustion closes storage. HttpResponse.close() also fires request_finished,
    # which would close pytest's enclosing transactional DB connection here.


def test_own_bound_occurrence_and_safe_metadata(recording):
    r = recording
    assert_readable(r)
    page = occurrences.list_occurrences(
        r.viewer,
        r.authority,
        trace_id=str(r.parent.trace_id),
        parent_span_id=r.parent.span_id,
    )
    items = page["items"]
    assert items[0]["reference"]["occurrence_id"] == str(r.row.client_id)
    assert items[0]["reference"]["mime_type"] == "audio/wav"
    encoded = json.dumps(items)
    for secret in (
        r.blob.storage_key,
        r.blob.sha256,
        r.blob.storage_version,
        r.cookie,
        r.grant,
    ):
        assert secret not in encoded
    assert items[0]["expires_at"] == r.authority.state.expires_at.isoformat()


@pytest.mark.parametrize(
    "change",
    [
        "organization_id",
        "workspace_id",
        "project_id",
        "user_id",
        "application_session_id",
    ],
)
def test_forged_viewer_tuple_is_not_found(recording, change):
    r = recording
    assert_readable(r)
    forged = replace(r.viewer, **{change: uuid.uuid4()})
    with pytest.raises(AttachmentError, match="not_found"):
        occurrences.get_occurrence(forged, r.authority, r.row.client_id, r.parent)


def test_mixed_batch_checks_parent_per_item(recording):
    r = recording
    parent = {"trace_id": str(r.parent.trace_id), "span_id": r.parent.span_id}
    wrong = {**parent, "span_id": "1234567890abcdef"}
    items = playback.resolve(
        r.viewer,
        r.authority,
        [
            {
                "occurrence_id": str(r.row.client_id),
                "parent": wrong,
                "representation": "original",
            },
            {
                "occurrence_id": str(r.row.client_id),
                "parent": parent,
                "representation": "original",
            },
            {
                "occurrence_id": str(uuid.uuid4()),
                "parent": wrong,
                "representation": "original",
            },
            {
                "occurrence_id": str(r.row.client_id),
                "parent": parent,
                "representation": "peaks",
            },
        ],
    )
    # A batch never inherits the first item's authorization, in either order.
    assert items[1]["status"] == "ready"
    assert [items[i]["status"] for i in (0, 2, 3)] == ["not_found"] * 3
    assert all(set(items[i]) == {"occurrence_id", "status"} for i in (0, 2, 3))


@pytest.mark.parametrize(
    "change",
    [
        "logout",
        "membership",
        "inactive_user",
        "deleted",
        "scope_generation",
        "source_generation",
        "source_denied",
        "unknown_retention",
        "source_expired",
        "project_switch",
        "null_workspace",
        "unbound",
        "policy",
        "capture_revoked",
    ],
)
def test_warm_grant_never_bypasses_current_authority(recording, change):
    r = recording
    assert_readable(r)
    head = response(r, method="HEAD")
    if change == "logout":
        r.authority.live = False
    elif change == "membership":
        OrganizationMembership.no_workspace_objects.filter(
            user_id=r.viewer.user_id
        ).update(is_active=False)
    elif change == "inactive_user":
        User.objects.filter(pk=r.viewer.user_id).update(is_active=False)
    elif change == "deleted":
        retention.delete_occurrence(
            r.viewer, r.authority, occurrence_id=r.row.client_id, parent=r.parent
        )
    elif change == "scope_generation":
        retention.fence_scope(scope_id=r.scope.id)
    elif change == "source_generation":
        r.authority.state = replace(r.authority.state, generation=4)
    elif change == "source_denied":
        r.authority.state = replace(r.authority.state, readable=False)
    elif change == "unknown_retention":
        r.authority.state = replace(r.authority.state, expires_at=None)
    elif change == "source_expired":
        r.authority.state = replace(
            r.authority.state, expires_at=timezone.now() - timedelta(seconds=1)
        )
    elif change == "project_switch":
        r.authority.viewer = replace(r.viewer, project_id=uuid.uuid4())
    elif change == "null_workspace":
        r.authority.viewer = replace(r.viewer, workspace_id=None)
    elif change == "unbound":
        AttachmentOccurrence.objects.filter(pk=r.row.id).update(bound_at=None)
    elif change == "policy":
        AttachmentScope.objects.filter(pk=r.scope.id).update(
            require_media_redaction=True, policy_version=2
        )
    elif change == "capture_revoked":
        CaptureSession.objects.filter(pk=r.row.capture_session_id).update(
            revoked_at=timezone.now()
        )
    before = len(r.store.calls)
    for method, headers in [
        ("HEAD", {}),
        ("GET", {"HTTP_RANGE": "bytes=0-9"}),
        ("GET", {"HTTP_IF_NONE_MATCH": head["ETag"]}),
    ]:
        reply = response(r, method=method, **headers)
        assert reply.status_code == 404
        assert reply["Cache-Control"] == "private, no-store"
        assert "ETag" not in reply and "Content-Range" not in reply
    assert len(r.store.calls) == before


def test_default_deny_adapter_and_flag(recording, settings):
    r = recording
    assert_readable(r)
    with pytest.raises(AttachmentError):
        occurrences.get_occurrence(
            r.viewer, occurrences.DenyAuthority(), r.row.client_id, r.parent
        )
    settings.FI_ATTACHMENTS_ENABLED = False
    assert response(r).status_code == 404


def test_different_media_session_cannot_use_stolen_grant(recording):
    r = recording
    assert_readable(r)
    _, other_cookie = playback.exchange(r.viewer, r.authority)
    assert response(r, cookie=other_cookie).status_code == 404
    assert response(r, cookie=False).status_code == 404
    assert response(r, grant="a" * 43).status_code == 404
    assert response(r, grant="signed-url-canary").status_code == 404


def test_revoke_session_and_expired_grant(recording):
    r = recording
    AttachmentPlaybackGrant.objects.filter(
        token_digest=playback.digest(r.grant)
    ).update(expires_at=timezone.now())
    assert response(r).status_code == 404
    AttachmentPlaybackGrant.objects.filter(
        token_digest=playback.digest(r.grant)
    ).update(expires_at=timezone.now() + timedelta(seconds=30))
    assert response(r, method="HEAD").status_code == 200
    playback.revoke_application_session(
        user_id=r.viewer.user_id, application_session_id=r.viewer.application_session_id
    )
    assert response(r, method="HEAD").status_code == 404


@pytest.mark.parametrize(
    "headers",
    [
        {"HTTP_ORIGIN": "https://evil.example.test"},
        {"HTTP_SEC_FETCH_SITE": "cross-site"},
        {"HTTP_SEC_FETCH_SITE": "same-site"},
    ],
)
def test_cross_site_embedding_denies(recording, headers):
    assert response(recording, **headers).status_code == 404
    assert recording.store.calls == []


@pytest.mark.parametrize(
    "range_value,start,end",
    [
        ("bytes=0-9", 0, 9),
        ("bytes=44-", 44, 1003),
        ("bytes=-10", 994, 1003),
        ("bytes=990-9999", 990, 1003),
    ],
)
def test_range_seek_and_bounded_read(recording, range_value, start, end):
    r = recording
    reply = response(r, HTTP_RANGE=range_value)
    assert reply.status_code == 206
    assert reply["Content-Range"] == f"bytes {start}-{end}/{len(wav_bytes())}"
    assert b"".join(reply.streaming_content) == wav_bytes()[start : end + 1]
    assert all(body.closed for body in r.store.bodies)
    assert r.store.calls[0][0].version == r.blob.storage_version
    assert reply["X-Content-Type-Options"] == "nosniff"
    assert reply["Cross-Origin-Resource-Policy"] == "same-origin"


@pytest.mark.parametrize(
    "value",
    ["bytes=3-1", "bytes=-0", "bytes=99999-", "bytes=0-1,3-4", "bytes=-", "x" * 129],
)
def test_bad_range_returns_no_bytes_after_auth(recording, value):
    reply = response(recording, HTTP_RANGE=value)
    assert reply.status_code == 416
    assert recording.store.calls == []


def test_authorized_head_conditional_and_if_range(recording):
    r = recording
    head = response(r, method="HEAD", HTTP_RANGE="bytes=0-9")
    assert head.status_code == 200 and head.content == b""
    assert head["Content-Length"] == str(len(wav_bytes()))
    assert r.store.calls == []
    assert response(r, HTTP_IF_NONE_MATCH=head["ETag"]).status_code == 304
    assert response(r, HTTP_IF_NONE_MATCH="W/" + head["ETag"]).status_code == 304
    reply = response(r, HTTP_RANGE="bytes=0-9", HTTP_IF_RANGE='"stale"')
    assert reply.status_code == 200
    reply.close()
    assert r.store.calls == []  # Closing before first iteration does not open storage.


def test_stream_revocation_brackets_read_and_cancellation(recording, monkeypatch):
    from tracer.views import attachment_media

    r = recording
    monkeypatch.setattr(attachment_media, "CHUNK_BYTES", 64)
    reply = response(r)
    chunks = iter(reply.streaming_content)
    assert len(next(chunks)) == 64
    started = time.monotonic()
    r.authority.live = False
    assert list(chunks) == []
    assert (
        time.monotonic() - started < 5
    )  # Component latency, not deployed network bound.
    assert all(b.closed for b in r.store.bodies)
    r.authority.live = True
    reply = response(r)
    next(iter(reply.streaming_content))
    reply.close()
    assert all(b.closed for b in r.store.bodies)


def test_revocation_during_storage_read_discards_chunk(recording):
    r = recording

    class RevokeOnRead(io.BytesIO):
        def read(self, size):
            data = super().read(size)
            r.authority.live = False
            return data

    body = RevokeOnRead(wav_bytes())
    r.store.open_range = Mock(return_value=body)
    assert b"".join(response(r).streaming_content) == b""
    assert body.closed


def test_exchange_requires_exact_origin_and_sets_narrow_cookie(recording, settings):
    r = recording
    settings.ALLOWED_HOSTS = ["app.example.test"]
    factory = RequestFactory()

    def request(origin=ORIGIN, **extra):
        headers = {"HTTP_HOST": "app.example.test", **extra}
        if origin is not None:
            headers["HTTP_ORIGIN"] = origin
        return factory.post(
            "/tracer/v1/attachment-media-sessions", secure=True, **headers
        )

    count = AttachmentMediaSession.objects.count()
    for req in (
        request(origin=None),
        request(origin="https://evil.example.test"),
        request(HTTP_SEC_FETCH_SITE="cross-site"),
    ):
        assert (
            exchange_response(req, r.viewer, r.authority, origin=ORIGIN).status_code
            == 404
        )
    r.authority.live = False
    assert (
        exchange_response(request(), r.viewer, r.authority, origin=ORIGIN).status_code
        == 404
    )
    r.authority.live = True
    assert AttachmentMediaSession.objects.count() == count
    # Bearer-authenticated: no ambient cookie credential, so no CSRF token needed.
    reply = exchange_response(request(), r.viewer, r.authority, origin=ORIGIN)
    assert reply.status_code == 204
    cookie = reply.cookies[playback.COOKIE_NAME]
    assert cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "Strict"
    assert cookie["path"] == playback.MEDIA_PATH and cookie["domain"] == ""
    assert "Access-Control-Allow-Origin" not in reply


def test_viewer_can_read_but_cannot_delete(recording):
    r = recording
    OrganizationMembership.no_workspace_objects.filter(user_id=r.viewer.user_id).update(
        role=OrganizationRoles.MEMBER_VIEW_ONLY, level=Level.VIEWER
    )
    WorkspaceMembership.no_workspace_objects.filter(user_id=r.viewer.user_id).update(
        role=OrganizationRoles.WORKSPACE_VIEWER, level=Level.WORKSPACE_VIEWER
    )
    assert_readable(r)
    with pytest.raises(AttachmentError, match="not_found"):
        retention.delete_occurrence(
            r.viewer, r.authority, occurrence_id=r.row.client_id, parent=r.parent
        )
    assert_readable(r)


def test_lists_hide_child_and_no_foreign_scope_cursor(recording):
    r = recording
    args = {"trace_id": str(r.parent.trace_id), "parent_span_id": r.parent.span_id}
    page = occurrences.list_occurrences(r.viewer, r.authority, **args)
    assert len(page["items"]) == 1 and page["next_cursor"] is None
    r.authority.state = replace(r.authority.state, readable=False)
    assert occurrences.list_occurrences(r.viewer, r.authority, **args) == {
        "items": [],
        "next_cursor": None,
    }
    with pytest.raises(AttachmentError):
        occurrences.list_occurrences(r.viewer, r.authority, cursor="forged", **args)
    r.authority.state = replace(r.authority.state, readable=True)
    # Cursors are F01 Tokens bound to this viewer, session, scope and parent.
    cursor = occurrences.encode_cursor(["other"], r.row.id)
    assert len(cursor) <= 128 and cursor.replace("-", "").replace("_", "").isalnum()
    with pytest.raises(AttachmentError, match="not_found"):
        occurrences.list_occurrences(r.viewer, r.authority, cursor=cursor, **args)


def test_grants_and_audits_do_not_store_capability_secrets(recording):
    r = recording
    payload = json.dumps(list(AttachmentAuditEvent.objects.values()), default=str)
    assert (
        r.cookie not in payload
        and r.grant not in payload
        and r.blob.sha256 not in payload
    )
    assert r.blob.storage_key not in payload and r.blob.storage_version not in payload
    assert AttachmentMediaSession.objects.get(pk=r.session.id).token_digest != r.cookie
