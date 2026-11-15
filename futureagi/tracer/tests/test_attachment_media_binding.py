"""Explicit binding: real URLs/JWTs/PG, declared CH/storage/parser doubles.

Gateway helpers send a selected credential deliberately to prove server binding.
Django's single-name cookie jar is NOT evidence of browser multi-path concurrency;
reversed browser delivery, two tabs, eviction and player state need integration.
"""

import io
import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.utils import timezone

from accounts.models.auth_token import AuthToken
from accounts.services.token_service import issue_tokens
from tracer.models.attachment import AttachmentMediaSession, AttachmentPlaybackGrant
from tracer.services.attachments import playback
from tracer.tests import test_attachment_api as http

api = http.api
pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def exchange(a, **kwargs):
    response = http.media_exchange(a, **kwargs)
    http.ok(response, 204)
    return response


def resolved(a, occurrence, response, **kwargs):
    return http.ok(
        http.resolve(
            a, occurrence, media_session_id=response[playback.SESSION_HEADER], **kwargs
        ),
        200,
        "ResolveResponse",
    )["items"][0]


def read(path, response, *, method="head", **headers):
    # Explicit pair selection; not a simulation of browser cookie-path storage.
    client = Client(HTTP_HOST="app.example.test")
    if response is not None:
        client.cookies[playback.COOKIE_NAME] = response.cookies[
            playback.COOKIE_NAME
        ].value
    return getattr(client, method)(path, secure=True, **headers)


def denied(response):
    assert response.status_code == 404
    assert response.content == b""
    assert response["Cache-Control"] == "private, no-store"
    for header in ("ETag", "Content-Range", "Content-Disposition"):
        assert header not in response


def test_equal_time_interleaving_keeps_both_exact_pairs(api, monkeypatch):
    _, body, _, _ = http.ready_occurrence(api)
    now = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: now)
    first = exchange(api)
    second = exchange(api)
    a, b = [reply[playback.SESSION_HEADER] for reply in (first, second)]
    assert a != b
    sessions = list(AttachmentMediaSession.objects.filter(pk__in=[a, b]))
    assert len({row.created_at for row in sessions}) == 1
    assert len({row.expires_at for row in sessions}) == 1
    assert all(row.revoked_at is None for row in sessions)
    # Delayed resolve A after exchange B; then resolve B, then A again.
    one = resolved(api, body["occurrence_id"], first)
    two = resolved(api, body["occurrence_id"], second)
    again = resolved(api, body["occurrence_id"], first)
    for item, reply in ((one, first), (two, second), (again, first)):
        assert item["gateway_path"].split("/")[4] == reply[playback.SESSION_HEADER]
        assert read(item["gateway_path"], reply).status_code == 200
        assert (
            read(
                item["gateway_path"], reply, method="get", HTTP_RANGE="bytes=0-3"
            ).status_code
            == 206
        )
    for path, reply in ((one["gateway_path"], second), (two["gateway_path"], first)):
        denied(read(path, reply))
    assert (
        first.cookies[playback.COOKIE_NAME]["path"]
        != second.cookies[playback.COOKIE_NAME]["path"]
    )


@pytest.mark.parametrize(
    "handle",
    [
        None,
        "",
        "latest",
        "A" * 43,
        "0" * 32,
        "00000000-0000-0000-0000-000000000000",
        str(uuid.uuid4()).upper(),
        str(uuid.uuid4()).replace("-", ""),
        str(uuid.uuid4()),
        "../session",
    ],
)
def test_missing_malformed_or_unknown_handle_never_falls_back(api, handle):
    _, body, _, _ = http.ready_occurrence(api)
    good = exchange(api)
    assert resolved(api, body["occurrence_id"], good)["status"] == "ready"
    http.assert_error(
        http.resolve(api, body["occurrence_id"], media_session_id=handle), 404
    )


@pytest.mark.parametrize(
    "change", ["foreign_user", "foreign_access", "foreign_org", "revoked", "expired"]
)
def test_supplied_row_must_match_identity_and_liveness(api, change):
    _, body, _, _ = http.ready_occurrence(api)
    good = exchange(api)
    handle = good[playback.SESSION_HEADER]
    item = resolved(api, body["occurrence_id"], good)
    row = AttachmentMediaSession.objects.get(pk=handle)
    if change == "foreign_user":
        stranger = http.other_tenant()
        AttachmentMediaSession.objects.filter(pk=handle).update(user=stranger.user)
    elif change == "foreign_access":
        AttachmentMediaSession.objects.filter(pk=handle).update(
            application_session_id=uuid.uuid4()
        )
    elif change == "foreign_org":
        stranger = http.other_tenant()
        AttachmentMediaSession.objects.filter(pk=handle).update(
            organization=stranger.organization
        )
    elif change == "revoked":
        AttachmentMediaSession.objects.filter(pk=handle).update(
            revoked_at=timezone.now()
        )
    else:
        AttachmentMediaSession.objects.filter(pk=handle).update(
            expires_at=timezone.now()
        )
    http.assert_error(
        http.resolve(api, body["occurrence_id"], media_session_id=handle), 404
    )
    denied(read(item["gateway_path"], good))
    # A denied supplied row is never replaced by a newer valid row.
    newer = exchange(api)
    assert resolved(api, body["occurrence_id"], newer)["status"] == "ready"
    http.assert_error(
        http.resolve(api, body["occurrence_id"], media_session_id=str(row.id)), 404
    )


def test_same_user_new_access_row_cannot_resolve_old_handle(api):
    _, body, _, _ = http.ready_occurrence(api)
    old = exchange(api)
    old_item = resolved(api, body["occurrence_id"], old)
    token = issue_tokens(api.user)["access"]
    new = exchange(api, token=token)
    http.assert_error(
        http.resolve(
            api,
            body["occurrence_id"],
            media_session_id=old[playback.SESSION_HEADER],
            token=token,
        ),
        404,
    )
    new_item = resolved(api, body["occurrence_id"], new, token=token)
    assert read(old_item["gateway_path"], old).status_code == 200
    assert read(new_item["gateway_path"], new).status_code == 200
    # Logout of this newer access row does not change inherited login-family policy.
    session = AttachmentMediaSession.objects.get(pk=new[playback.SESSION_HEADER])
    AuthToken.objects.filter(pk=session.application_session_id).update(is_active=False)
    denied(read(new_item["gateway_path"], new))
    assert read(old_item["gateway_path"], old).status_code == 200


def test_authorized_other_organization_is_not_a_project_binding(api):
    _, body, _, _ = http.ready_occurrence(api)
    first = exchange(api)
    item = resolved(api, body["occurrence_id"], first)
    other = http.other_tenant()
    http.member(api.user, other.organization, other.workspace)
    second = exchange(api, HTTP_X_ORGANIZATION_ID=str(other.organization.id))
    row = AttachmentMediaSession.objects.get(pk=second[playback.SESSION_HEADER])
    assert row.organization_id == other.organization.id
    http.assert_error(
        http.resolve(
            api, body["occurrence_id"], media_session_id=second[playback.SESSION_HEADER]
        ),
        404,
    )
    assert read(item["gateway_path"], first).status_code == 200


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "https://evil.example.test",
        "http://app.example.test",
        "https://app.example.test/",
        "null",
    ],
)
def test_resolve_requires_exact_origin(api, origin):
    _, body, _, _ = http.ready_occurrence(api)
    good = exchange(api)
    http.assert_error(
        http.resolve(
            api,
            body["occurrence_id"],
            media_session_id=good[playback.SESSION_HEADER],
            origin=origin,
        ),
        404,
    )


def test_exchange_and_resolve_refuse_ambient_application_cookie(api):
    _, body, _, _ = http.ready_occurrence(api)
    good = exchange(api)
    api.client.force_login(api.user)
    api.client.cookies["access_token"] = api.token
    headers = {
        "HTTP_ORIGIN": http.ORIGIN,
        "HTTP_X_ATTACHMENT_MEDIA_SESSION": good[playback.SESSION_HEADER],
    }
    request = {
        "items": [
            {
                "occurrence_id": body["occurrence_id"],
                "parent": {"trace_id": http.TRACE, "span_id": http.SPAN},
                "representation": "original",
            }
        ]
    }
    for path, data in (
        ("/tracer/v1/attachment-media-sessions", {}),
        (f"{http.base(api)}/resolve", request),
    ):
        http.assert_error(http.post(api, path, data, headers), 401)


@pytest.mark.parametrize(
    "change",
    [
        "route",
        "cookie",
        "grant",
        "occurrence",
        "missing_cookie",
        "legacy_name",
        "duplicate_name",
        "old_route",
    ],
)
def test_gateway_requires_three_way_binding_and_ignores_legacy_wire(api, change):
    _, body, _, _ = http.ready_occurrence(api)
    first = exchange(api)
    one = resolved(api, body["occurrence_id"], first)
    second = exchange(api)
    two = resolved(api, body["occurrence_id"], second)
    path = one["gateway_path"]
    parts = path.split("/")
    reply = first
    headers = {}
    if change == "route":
        parts[4] = second[playback.SESSION_HEADER]
    elif change == "cookie":
        reply = second
    elif change == "grant":
        parts[6] = two["gateway_path"].split("/")[6]
    elif change == "occurrence":
        parts[5] = str(uuid.uuid4())
    elif change == "missing_cookie":
        reply = None
    elif change == "legacy_name":
        reply = None
        headers["HTTP_COOKIE"] = (
            f"fi_media_session={first.cookies[playback.COOKIE_NAME].value}"
        )
    elif change == "duplicate_name":
        headers["HTTP_COOKIE"] = "; ".join(
            f"{playback.COOKIE_NAME}={r.cookies[playback.COOKIE_NAME].value}"
            for r in (second, first)
        )
    else:
        parts.pop(4)
    reply = read("/".join(parts), reply, **headers)
    if change == "old_route":
        # Removed route, not a compatibility gateway; Django's generic 404.
        assert reply.status_code == 404
    else:
        denied(reply)


@pytest.mark.parametrize("change", ["revoked", "expired"])
def test_original_access_row_denies_resolve_headers_range_conditionals_and_stream(
    api, change
):
    _, body, _, _ = http.ready_occurrence(api)
    first = exchange(api)
    item = resolved(api, body["occurrence_id"], first)
    head = read(item["gateway_path"], first)
    stream = read(item["gateway_path"], first, method="get")
    row = AttachmentMediaSession.objects.get(pk=first[playback.SESSION_HEADER])
    query = AuthToken.objects.filter(pk=row.application_session_id)
    if change == "revoked":
        query.update(is_active=False)
    else:
        query.update(last_used_at=timezone.now() - timedelta(days=3))
    http.assert_error(
        http.resolve(api, body["occurrence_id"], media_session_id=str(row.id)), 401
    )
    http.assert_error(http.media_exchange(api), 401)
    denied(read(item["gateway_path"], first))
    denied(read(item["gateway_path"], first, method="get", HTTP_RANGE="bytes=0-3"))
    denied(read(item["gateway_path"], first, HTTP_IF_NONE_MATCH=head["ETag"]))
    assert b"".join(stream.streaming_content) == b""


def test_rotation_overlap_does_not_extend_old_grant_or_session(api, monkeypatch):
    _, body, _, _ = http.ready_occurrence(api)
    start = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: start)
    first = exchange(api)
    one = resolved(api, body["occurrence_id"], first)
    old_deadline = AttachmentMediaSession.objects.get(
        pk=first[playback.SESSION_HEADER]
    ).expires_at
    old_stream = read(one["gateway_path"], first, method="get")
    monkeypatch.setattr(timezone, "now", lambda: start + timedelta(seconds=30))
    second = exchange(api)
    two = resolved(api, body["occurrence_id"], second)
    assert b"".join(old_stream.streaming_content) == http.wav_bytes()
    assert read(one["gateway_path"], first).status_code == 200
    assert read(two["gateway_path"], second).status_code == 200
    pending = read(one["gateway_path"], first, method="get")
    monkeypatch.setattr(timezone, "now", lambda: start + timedelta(seconds=61))
    denied(read(one["gateway_path"], first))
    assert b"".join(pending.streaming_content) == b""
    assert read(two["gateway_path"], second).status_code == 200
    assert (
        AttachmentMediaSession.objects.get(pk=first[playback.SESSION_HEADER]).expires_at
        == old_deadline
    )
    monkeypatch.setattr(timezone, "now", lambda: old_deadline)
    http.assert_error(
        http.resolve(
            api, body["occurrence_id"], media_session_id=first[playback.SESSION_HEADER]
        ),
        404,
    )


def test_headers_and_persistence_expose_only_nonsecret_handle_and_expiry(api):
    _, body, _, _ = http.ready_occurrence(api)
    reply = exchange(api)
    handle = reply[playback.SESSION_HEADER]
    row = AttachmentMediaSession.objects.get(pk=handle)
    cookie = reply.cookies[playback.COOKIE_NAME]
    assert reply[playback.EXPIRY_HEADER] == row.expires_at.isoformat()
    assert cookie["path"] == playback.session_path(handle)
    assert cookie["domain"] == "" and cookie["secure"] and cookie["httponly"]
    assert (
        cookie["samesite"] == "Strict" and cookie["max-age"] == playback.SESSION_SECONDS
    )
    assert reply.content == b"" and reply["Cache-Control"] == "private, no-store"
    # Middleware may echo the exact approved origin, never a wildcard.
    assert reply.get("Access-Control-Allow-Origin") in (None, http.ORIGIN)
    assert row.token_digest == playback.digest(cookie.value)
    public = repr(dict(reply.items())) + str(
        resolved(api, body["occurrence_id"], reply)
    )
    assert (
        cookie.value not in public
        and row.token_digest not in public
        and api.token not in public
    )
    # The raw cookie secret is never accepted in the non-secret selector header.
    http.assert_error(
        http.resolve(api, body["occurrence_id"], media_session_id=cookie.value), 404
    )


def test_exchange_admission_is_bounded_without_revoking_valid_pairs(api, monkeypatch):
    _, body, _, _ = http.ready_occurrence(api)
    first = exchange(api)
    item = resolved(api, body["occurrence_id"], first)
    cache = Mock()
    monkeypatch.setattr(playback, "cache", cache)
    count = AttachmentMediaSession.objects.count()
    for value in (playback.EXCHANGES_PER_MINUTE + 1, "invalid"):
        cache.incr.return_value = value
        http.assert_error(http.media_exchange(api), 429)
    cache.incr.side_effect = RuntimeError("cache unavailable")
    http.assert_error(http.media_exchange(api), 429)
    assert AttachmentMediaSession.objects.count() == count
    assert read(item["gateway_path"], first).status_code == 200


def test_cutover_is_gated_one_time_and_renamed_legacy_cookie_cannot_work(
    api, settings, monkeypatch
):
    _, body, _, _ = http.ready_occurrence(api)
    legacy = exchange(api)
    item = resolved(api, body["occurrence_id"], legacy)
    cutoff = timezone.now()
    args = {
        "issued_through": cutoff.isoformat(),
        "quiesced": True,
        "stdout": io.StringIO(),
    }
    with pytest.raises(CommandError):
        call_command("cutover_attachment_media_sessions", apply=True, **args)
    settings.FI_ATTACHMENTS_ENABLED = False
    with pytest.raises(CommandError):
        call_command(
            "cutover_attachment_media_sessions",
            issued_through=cutoff.isoformat(),
            apply=True,
        )
    call_command("cutover_attachment_media_sessions", **args)
    assert (
        AttachmentMediaSession.objects.get(
            pk=legacy[playback.SESSION_HEADER]
        ).revoked_at
        is None
    )
    call_command("cutover_attachment_media_sessions", apply=True, **args)
    settings.FI_ATTACHMENTS_ENABLED = True
    # Existing digest-only rows are wire-neutral: this is the renamed legacy credential.
    denied(read(item["gateway_path"], legacy))
    http.assert_error(
        http.resolve(
            api, body["occurrence_id"], media_session_id=legacy[playback.SESSION_HEADER]
        ),
        404,
    )
    monkeypatch.setattr(timezone, "now", lambda: cutoff + timedelta(seconds=1))
    current = exchange(api)
    good = resolved(api, body["occurrence_id"], current)
    settings.FI_ATTACHMENTS_ENABLED = False
    call_command("cutover_attachment_media_sessions", apply=True, **args)
    settings.FI_ATTACHMENTS_ENABLED = True
    assert read(good["gateway_path"], current).status_code == 200
    assert (
        AttachmentMediaSession.objects.get(
            pk=current[playback.SESSION_HEADER]
        ).revoked_at
        is None
    )
    assert AttachmentPlaybackGrant.objects.filter(
        media_session_id=legacy[playback.SESSION_HEADER]
    ).exists()


@pytest.mark.parametrize(
    "cutoff",
    ["nonsense", "2026-99-99", "2026-01-01T00:00:00", "2999-01-01T00:00:00+00:00"],
)
def test_cutover_refuses_unsafe_cutoffs(settings, cutoff):
    settings.FI_ATTACHMENTS_ENABLED = False
    with pytest.raises(CommandError):
        call_command(
            "cutover_attachment_media_sessions",
            issued_through=cutoff,
            quiesced=True,
            apply=True,
        )


def test_foreign_authenticated_user_cannot_select_another_users_handle(api):
    _, body, _, _ = http.ready_occurrence(api)
    first = exchange(api)
    stranger = http.other_tenant()
    # Even membership in the same project organization is not session ownership.
    http.member(stranger.user, api.scope.organization, api.scope.workspace)
    http.assert_error(
        http.resolve(
            api,
            body["occurrence_id"],
            media_session_id=first[playback.SESSION_HEADER],
            token=stranger.token,
        ),
        404,
    )
    assert resolved(api, body["occurrence_id"], first)["status"] == "ready"


def test_original_access_revocation_inside_storage_read_discards_chunk(api):
    _, body, _, _ = http.ready_occurrence(api)
    first = exchange(api)
    item = resolved(api, body["occurrence_id"], first)
    row = AttachmentMediaSession.objects.get(pk=first[playback.SESSION_HEADER])

    class RevokeOnRead(io.BytesIO):
        def read(self, size):
            chunk = super().read(size)
            AuthToken.objects.filter(pk=row.application_session_id).update(
                is_active=False
            )
            return chunk

    body = RevokeOnRead(http.wav_bytes())
    api.store.open_range = Mock(return_value=body)
    stream = read(item["gateway_path"], first, method="get")
    assert stream.status_code == 200
    assert b"".join(stream.streaming_content) == b""
    assert body.closed
    denied(read(item["gateway_path"], first))


def test_wire_response_matches_occurrence_not_session_segment():
    from tracer.services.attachments import wire
    from tracer.services.attachments.contract import AttachmentError

    session, occurrence = str(uuid.uuid4()), str(uuid.uuid4())
    item = {
        "occurrence_id": occurrence,
        "status": "ready",
        "gateway_path": f"/tracer/v1/attachment-media/{session}/{occurrence}/{'a' * 43}",
        "expires_at": "2030-01-01T00:00:00+00:00",
    }
    wire.validate("ResolveResponse", {"items": [item]})
    with pytest.raises(AttachmentError, match="invalid_request"):
        wire.validate(
            "ResolveResponse", {"items": [{**item, "occurrence_id": session}]}
        )


def test_media_cutover_needs_no_new_migration_and_graph_has_no_conflicts():
    from django.db import connection
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(connection)
    assert not loader.detect_conflicts()
    # Downstream ingest/recordings branches extend the existing fence migration.
    # The protocol cutover adds no column or competing leaf; do not pin the
    # backend-only leaf in a test that also runs on those integrated branches.
    leaves = loader.graph.leaf_nodes("tracer")
    assert len(leaves) == 1
    assert ("tracer", "0137_attachment_fences") in loader.graph.forwards_plan(leaves[0])
    assert "wire_version" not in {
        field.name for field in AttachmentMediaSession._meta.fields
    }


def test_cutover_is_only_registered_as_an_explicit_operator_command(monkeypatch):
    from model_hub.apps import (
        STARTUP_SAFE_MANAGEMENT_COMMANDS,
        explicit_management_mutation_authorized,
        guarded_management_command,
    )

    command = "cutover_attachment_media_sessions"
    argv = [
        "manage.py",
        command,
        "--quiesced",
        "--issued-through",
        "2030-01-01T00:00:00Z",
    ]
    assert command not in STARTUP_SAFE_MANAGEMENT_COMMANDS
    assert guarded_management_command(argv) == command
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", "true")
    monkeypatch.setenv("ENV_TYPE", "staging")
    monkeypatch.setenv("STARTUP_DB_MUTATION_MODE", "disabled")
    monkeypatch.setenv("SERVICE_TYPE", "backend")
    assert not explicit_management_mutation_authorized(argv)
    monkeypatch.setenv("STARTUP_DB_MUTATION_MODE", "operator")
    assert not explicit_management_mutation_authorized(argv)
    monkeypatch.setenv("SERVICE_TYPE", "bootstrap")
    assert explicit_management_mutation_authorized(argv)
    assert not explicit_management_mutation_authorized(["manage.py", "shell"])
