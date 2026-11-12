"""Revoking a refresh-token family must end its access tokens on ``/mcp``.

When a refresh token that was already rotated is presented again, both token
routes revoke the token's whole family (RFC 9700 section 4.14.2): either the
client or an attacker holds a stolen copy. The access tokens that family has
already issued, including the one the thief got from the stolen rotation, must
stop working on Streamable HTTP ``/mcp`` at the same moment, not when they
expire up to an hour later. Revoking a refresh token at the SDK ``/revoke``
route revokes its family too, so it ends every access token of that chain.

Covers both token routes: ``/mcp/oauth/token/`` (DRF view, families in the
database) and the MCP SDK root ``/token`` (families in the cache).
"""

import pytest
from asgiref.sync import sync_to_async
from django.core.cache import cache

from mcp_server.models.oauth_client import MCPOAuthClient
from mcp_server.models.oauth_code import MCPOAuthCode
from mcp_server.oauth_utils import (
    decrypt_oauth_token,
    generate_oauth_token,
    hash_client_secret,
)
from mcp_server.tests.test_streamable_oauth_revocation import (
    _assert_invalid_token,
    _oauth_http,
    _post_initialize,
    _sdk_issue_tokens,
    _sdk_register,
)
from tfc.middleware.workspace_context import set_workspace_context

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

REDIRECT = "https://example.com/callback"
SECRET = "synthetic-family-secret"


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _relaxed_throttles(settings):
    # These tests exercise token revocation, not throttling.
    settings.MCP_OAUTH_TOKEN_IP_THROTTLE_RATE = "1000/min"
    settings.MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE = "1000/min"
    settings.MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT = 1000


# ── DRF route: /mcp/oauth/token/ ─────────────────────────────────────


def _post_token(payload):
    from rest_framework.test import APIClient

    return APIClient().post(
        "/mcp/oauth/token/", payload, format="json", REMOTE_ADDR="198.51.100.40"
    )


def _drf_authorize_sync(user, workspace, client_id, code):
    client = MCPOAuthClient.objects.filter(client_id=client_id).first()
    if client is None:
        client = MCPOAuthClient.objects.create(
            client_id=client_id,
            client_secret_hash=hash_client_secret(SECRET),
            name=client_id,
            redirect_uris=[REDIRECT],
        )
    set_workspace_context(
        workspace=workspace, organization=user.organization, user=user
    )
    MCPOAuthCode.objects.create(
        code=code,
        client=client,
        user=user,
        organization=user.organization,
        workspace=workspace,
        redirect_uri=REDIRECT,
        scope=["context"],
    )
    response = _post_token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": SECRET,
            "redirect_uri": REDIRECT,
        }
    )
    assert response.status_code == 200, response.data
    return response.data


def _drf_refresh_sync(refresh_token, client_id):
    return _post_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": SECRET,
        }
    )


drf_authorize = sync_to_async(_drf_authorize_sync)
drf_refresh = sync_to_async(_drf_refresh_sync)


async def test_drf_reuse_ends_the_access_token_from_the_stolen_rotation(
    user, workspace
):
    issued = await drf_authorize(user, workspace, "family-drf", "family-drf-code")

    # A thief rotates the stolen refresh token first.
    stolen = await drf_refresh(issued["refresh_token"], "family-drf")
    assert stolen.status_code == 200, stolen.data
    thief_access = stolen.data["access_token"]
    assert (await _post_initialize(thief_access)).status_code == 200

    # The legitimate client then presents the same token: reuse.
    replay = await drf_refresh(issued["refresh_token"], "family-drf")
    assert replay.status_code == 400, replay.data
    assert replay.data["error"] == "invalid_grant"

    _assert_invalid_token(await _post_initialize(thief_access))
    # The family's earlier access token is ended too.
    _assert_invalid_token(await _post_initialize(issued["access_token"]))


async def test_drf_rotation_keeps_every_access_token_of_the_family_working(
    user, workspace
):
    """Positive control: rotation alone revokes nothing."""
    issued = await drf_authorize(user, workspace, "family-drf-ok", "family-ok-code")
    first = await drf_refresh(issued["refresh_token"], "family-drf-ok")
    assert first.status_code == 200, first.data
    second = await drf_refresh(first.data["refresh_token"], "family-drf-ok")
    assert second.status_code == 200, second.data

    for access in (
        issued["access_token"],
        first.data["access_token"],
        second.data["access_token"],
    ):
        assert (await _post_initialize(access)).status_code == 200


async def test_drf_access_tokens_name_their_refresh_family(user, workspace):
    issued = await drf_authorize(user, workspace, "family-drf-id", "family-id-code")
    refreshed = await drf_refresh(issued["refresh_token"], "family-drf-id")
    assert refreshed.status_code == 200, refreshed.data

    first = decrypt_oauth_token(issued["access_token"])
    second = decrypt_oauth_token(refreshed.data["access_token"])
    assert first["refresh_family_id"]
    assert second["refresh_family_id"] == first["refresh_family_id"]


async def test_drf_reuse_in_one_family_leaves_other_families_working(
    user, workspace
):
    victim = await drf_authorize(user, workspace, "family-drf-a", "family-a-code")
    other = await drf_authorize(user, workspace, "family-drf-b", "family-b-code")

    rotated = await drf_refresh(victim["refresh_token"], "family-drf-a")
    assert rotated.status_code == 200, rotated.data
    replay = await drf_refresh(victim["refresh_token"], "family-drf-a")
    assert replay.status_code == 400, replay.data

    _assert_invalid_token(await _post_initialize(rotated.data["access_token"]))
    assert (await _post_initialize(other["access_token"])).status_code == 200


async def test_drf_superseded_family_keeps_its_access_token_until_expiry(
    user, workspace
):
    """A new authorization supersedes the client's earlier family. That is
    routine (re-connecting), not a compromise, so the earlier access token
    keeps its remaining lifetime, as before families existed."""
    first = await drf_authorize(user, workspace, "family-drf-re", "family-re-1")
    second = await drf_authorize(user, workspace, "family-drf-re", "family-re-2")

    assert (await _post_initialize(first["access_token"])).status_code == 200
    assert (await _post_initialize(second["access_token"])).status_code == 200


async def test_drf_reuse_detected_after_supersession_still_ends_the_family(
    user, workspace
):
    """Replaying a rotated token of a superseded family is still reuse: the
    thief's access token from that family must end."""
    first = await drf_authorize(user, workspace, "family-drf-sr", "family-sr-1")
    stolen = await drf_refresh(first["refresh_token"], "family-drf-sr")
    assert stolen.status_code == 200, stolen.data
    thief_access = stolen.data["access_token"]
    await drf_authorize(user, workspace, "family-drf-sr", "family-sr-2")
    assert (await _post_initialize(thief_access)).status_code == 200

    replay = await drf_refresh(first["refresh_token"], "family-drf-sr")
    assert replay.status_code == 400, replay.data

    _assert_invalid_token(await _post_initialize(thief_access))


async def test_drf_access_token_issued_before_families_keeps_working(
    user, workspace
):
    """Tokens minted before this change carry no family and stay valid for
    the rest of their (at most one hour) lifetime."""
    await sync_to_async(MCPOAuthClient.objects.create)(
        client_id="family-drf-legacy",
        client_secret_hash=hash_client_secret(SECRET),
        name="legacy",
        redirect_uris=[REDIRECT],
    )
    token, _ = await sync_to_async(generate_oauth_token)(
        user.id, user.organization_id, workspace.id, "family-drf-legacy", ["context"]
    )
    assert "refresh_family_id" not in decrypt_oauth_token(token)

    assert (await _post_initialize(token)).status_code == 200


# ── MCP SDK route: root /token ───────────────────────────────────────


async def _sdk_refresh(http, client, refresh_token):
    return await http.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
        },
    )


async def test_sdk_reuse_ends_the_access_token_from_the_stolen_rotation(
    user, workspace
):
    async with _oauth_http() as http:
        client = await _sdk_register(http)
        issued = await _sdk_issue_tokens(http, client, user, workspace)

        stolen = await _sdk_refresh(http, client, issued["refresh_token"])
        assert stolen.status_code == 200, stolen.text
        thief_access = stolen.json()["access_token"]
        assert (await _post_initialize(thief_access)).status_code == 200

        replay = await _sdk_refresh(http, client, issued["refresh_token"])
        assert replay.status_code == 400, replay.text
        assert replay.json()["error"] == "invalid_grant"

    _assert_invalid_token(await _post_initialize(thief_access))
    _assert_invalid_token(await _post_initialize(issued["access_token"]))


async def test_sdk_rotation_keeps_every_access_token_of_the_family_working(
    user, workspace
):
    """Positive control: rotation alone revokes nothing."""
    async with _oauth_http() as http:
        client = await _sdk_register(http)
        issued = await _sdk_issue_tokens(http, client, user, workspace)
        first = await _sdk_refresh(http, client, issued["refresh_token"])
        assert first.status_code == 200, first.text
        second = await _sdk_refresh(http, client, first.json()["refresh_token"])
        assert second.status_code == 200, second.text

    for access in (
        issued["access_token"],
        first.json()["access_token"],
        second.json()["access_token"],
    ):
        assert (await _post_initialize(access)).status_code == 200


async def test_sdk_revoking_a_refresh_token_ends_every_access_token_of_its_chain(
    user, workspace
):
    """``/revoke`` of a refresh token revokes its family, so access tokens
    from earlier rotations end too, not only the one issued with it."""
    async with _oauth_http() as http:
        client = await _sdk_register(http)
        issued = await _sdk_issue_tokens(http, client, user, workspace)
        rotated = await _sdk_refresh(http, client, issued["refresh_token"])
        assert rotated.status_code == 200, rotated.text
        assert (await _post_initialize(issued["access_token"])).status_code == 200

        revoked = await http.post(
            "/revoke",
            data={
                "token": rotated.json()["refresh_token"],
                "token_type_hint": "refresh_token",
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
            },
        )
        assert revoked.status_code == 200, revoked.text

    _assert_invalid_token(await _post_initialize(rotated.json()["access_token"]))
    _assert_invalid_token(await _post_initialize(issued["access_token"]))


async def test_sdk_reuse_in_one_family_leaves_other_grants_working(
    user, workspace
):
    async with _oauth_http() as http:
        client = await _sdk_register(http)
        victim = await _sdk_issue_tokens(http, client, user, workspace)
        other = await _sdk_issue_tokens(http, client, user, workspace)

        rotated = await _sdk_refresh(http, client, victim["refresh_token"])
        assert rotated.status_code == 200, rotated.text
        replay = await _sdk_refresh(http, client, victim["refresh_token"])
        assert replay.status_code == 400, replay.text

    _assert_invalid_token(await _post_initialize(rotated.json()["access_token"]))
    assert (await _post_initialize(other["access_token"])).status_code == 200
