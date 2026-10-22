"""One secret value stored at two URL-bound leaves on different origins still
saves.

Every string header of an alert channel or audit sink is a secret leaf, so
``Content-Type: application/json`` on two channels with different hosts is one
value at two origins, and so is one MCP token used for servers on two hosts.
Both leaves read back the same mask whether or not migration 0029 has run
(0029 stores one ciphertext per org and clear value), so every row with such
a value is in this state. The console
then posts that mask back for each leaf (the Org Config editor through
``POST org-configs/``, the MCP page's untouched Edit through
update-mcp-server), and a working setup must still save, whichever leaf comes
first and whether or not 0029 has run.

The origin rule still holds: the mask keeps the secret only for an origin it
is already stored with, so moving one of those leaves to a third host keeps
nothing.

Test values
only, not real credentials.
"""

import importlib
import json

import pytest
from rest_framework import status

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.tests import test_org_config_secrets as base
from agentcc.tests.test_org_config_secrets import (
    GATEWAY,
    MASK,
    ORG_CONFIGS,
    _active,
    _ok,
    _pushed,
    _result,
)

migration = importlib.import_module(
    "agentcc.migrations.0029_encrypt_org_config_secrets"
)

# The secrets suite's fixtures: every push goes to a recording mock, and the
# role clients it makes are stopped after each test.
gateway = base.gateway
_stop_clients = base._stop_clients

HOOK_A = "https://hooks-a.t.example/in"
HOOK_B = "https://hooks-b.t.example/in"
MCP_A = "https://mcp-a.t.example/mcp"
MCP_B = "https://mcp-b.t.example/mcp"
ATTACKER = "https://attacker.t.example/in"
SHARED_TOKEN = "t-shared-tok"

MIGRATED = pytest.mark.parametrize(
    "migrated", [False, True], ids=["unmigrated", "migrated"]
)


class _Apps:
    def get_model(self, app, model):
        return AgentccOrgConfig


def _alerting(v0, v1):
    return {
        "enabled": True,
        "channels": [
            {
                "name": "ops-a",
                "type": "webhook",
                "url": HOOK_A,
                "headers": {"Content-Type": v0},
            },
            {
                "name": "ops-b",
                "type": "webhook",
                "url": HOOK_B,
                "headers": {"Content-Type": v1},
            },
        ],
    }


def _mcp(t0, t1):
    return {
        "enabled": True,
        "servers": {
            "a": {
                "transport": "http",
                "url": MCP_A,
                "auth": {"type": "bearer", "token": t0},
            },
            "b": {
                "transport": "http",
                "url": MCP_B,
                "auth": {"type": "bearer", "token": t1},
            },
        },
    }


def _seed(organization, alerting, mcp, migrated):
    """A stored row with its secrets unencrypted, then migration 0029 when
    ``migrated``."""
    AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=1,
        is_active=True,
        alerting=alerting,
        mcp=mcp,
    )
    if migrated:
        migration.forwards(_Apps(), None)


def _read_active(auth_client):
    return _result(_ok(auth_client.get(f"{ORG_CONFIGS}active/")))


def _editor_save(auth_client):
    """What OrgConfigSection does: read active, post its sections back."""
    body = _read_active(auth_client)
    post = {
        name: body[name]
        for name in ("cache", "routing", "alerting", "mcp", "a2a", "audit")
        if name in body
    }
    post["change_description"] = "t editor save"
    return auth_client.post(ORG_CONFIGS, post, format="json")


def _dialog_save(auth_client, server_id, **changes):
    """What the MCP dialog's Edit sends: the server as read, with ``changes``."""
    server = _read_active(auth_client)["mcp"]["servers"][server_id]
    return auth_client.post(
        f"{GATEWAY}/update-mcp-server/",
        {"server_id": server_id, "config": {**server, **changes}},
        format="json",
    )


def _channels(payload):
    return [
        (channel["url"], channel["headers"]["Content-Type"])
        for channel in payload["alerting"]["channels"]
    ]


def _tokens(payload):
    servers = payload["mcp"]["servers"]
    return {sid: servers[sid]["auth"]["token"] for sid in sorted(servers)}


@pytest.mark.integration
@pytest.mark.api
class TestOneValueAtTwoOriginsStillSaves:
    @MIGRATED
    def test_control_distinct_values_editor_save(
        self, organization, auth_client, gateway, migrated
    ):
        _seed(
            organization,
            _alerting("application/json", "text/plain"),
            _mcp("t-tok-a", "t-tok-b"),
            migrated,
        )
        _ok(_editor_save(auth_client))
        payload = _pushed(gateway)[-1]
        assert _channels(payload) == [
            (HOOK_A, "application/json"),
            (HOOK_B, "text/plain"),
        ]
        assert _tokens(payload) == {"a": "t-tok-a", "b": "t-tok-b"}

    @MIGRATED
    def test_editor_save_same_header_value_two_hosts(
        self, organization, auth_client, gateway, migrated
    ):
        _seed(
            organization,
            _alerting("application/json", "application/json"),
            {},
            migrated,
        )
        _ok(_editor_save(auth_client))
        assert _channels(_pushed(gateway)[-1]) == [
            (HOOK_A, "application/json"),
            (HOOK_B, "application/json"),
        ]

    @MIGRATED
    def test_editor_save_same_mcp_token_two_hosts(
        self, organization, auth_client, gateway, migrated
    ):
        _seed(organization, {}, _mcp(SHARED_TOKEN, SHARED_TOKEN), migrated)
        _ok(_editor_save(auth_client))
        assert _tokens(_pushed(gateway)[-1]) == {"a": SHARED_TOKEN, "b": SHARED_TOKEN}

    @MIGRATED
    @pytest.mark.parametrize("server_id", ["a", "b"])
    def test_mcp_dialog_untouched_save_same_token_two_hosts(
        self, organization, auth_client, gateway, migrated, server_id
    ):
        _seed(organization, {}, _mcp(SHARED_TOKEN, SHARED_TOKEN), migrated)
        _ok(_dialog_save(auth_client, server_id))
        servers = _active(organization).mcp["servers"]
        assert servers["a"]["auth"]["token"].startswith("enc::")
        assert servers["b"]["auth"]["token"].startswith("enc::")
        assert _tokens(_pushed(gateway)[-1]) == {"a": SHARED_TOKEN, "b": SHARED_TOKEN}

    @MIGRATED
    def test_removing_the_other_channel_keeps_the_remaining_ones_secret(
        self, organization, auth_client, gateway, migrated
    ):
        # The Alerting tab's delete: channel ops-b goes, ops-a is posted back
        # as read, its mask included.
        _seed(
            organization,
            _alerting("application/json", "application/json"),
            {},
            migrated,
        )
        alerting = _read_active(auth_client)["alerting"]
        alerting["channels"] = alerting["channels"][:1]
        _ok(auth_client.post(ORG_CONFIGS, {"alerting": alerting}, format="json"))
        assert _channels(_pushed(gateway)[-1]) == [(HOOK_A, "application/json")]

    @MIGRATED
    def test_a_reordered_channel_list_keeps_each_channels_secret(
        self, organization, auth_client, gateway, migrated
    ):
        # Each channel moves to the other's index, so each slot's own stored
        # leaf is bound to the other host; the same secret is stored for the
        # slot's host at the other leaf, so it is kept.
        _seed(
            organization,
            _alerting("application/json", "application/json"),
            {},
            migrated,
        )
        alerting = _read_active(auth_client)["alerting"]
        alerting["channels"] = alerting["channels"][::-1]
        _ok(auth_client.post(ORG_CONFIGS, {"alerting": alerting}, format="json"))
        assert _channels(_pushed(gateway)[-1]) == [
            (HOOK_B, "application/json"),
            (HOOK_A, "application/json"),
        ]


@pytest.mark.integration
@pytest.mark.api
class TestTheOriginRuleStillHoldsForASharedValue:
    @MIGRATED
    def test_a_channel_moved_to_a_third_host_keeps_nothing(
        self, organization, auth_client, gateway, migrated
    ):
        _seed(
            organization,
            _alerting("t-shared-hook", "t-shared-hook"),
            {},
            migrated,
        )
        before = _active(organization).version
        pushes = len(_pushed(gateway))
        alerting = _read_active(auth_client)["alerting"]
        assert alerting["channels"][0]["headers"]["Content-Type"].startswith(MASK)
        alerting["channels"][0]["url"] = ATTACKER
        response = auth_client.post(ORG_CONFIGS, {"alerting": alerting}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "secret_origin_changed" in response.content.decode()
        assert _active(organization).version == before
        assert len(_pushed(gateway)) == pushes
        assert "attacker.t.example" not in json.dumps(_pushed(gateway))

    @MIGRATED
    @pytest.mark.parametrize("server_id", ["a", "b"])
    def test_an_mcp_server_moved_to_a_third_host_keeps_nothing(
        self, organization, auth_client, gateway, migrated, server_id
    ):
        _seed(organization, {}, _mcp(SHARED_TOKEN, SHARED_TOKEN), migrated)
        before = _active(organization).version
        response = _dialog_save(
            auth_client, server_id, url="https://attacker.t.example/mcp"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "secret_origin_changed" in response.content.decode()
        assert _active(organization).version == before
        assert "attacker.t.example" not in json.dumps(_pushed(gateway))


# URLs the origin rule cannot read: a port out of range, an unbalanced IPv6
# bracket. The contract's MCP url is a plain string, so either can be stored.
BAD_PORT_URL = "https://mcp-c.t.example:99999/mcp"
BAD_BRACKET_URL = "https://[mcp-c.t.example/mcp"
ATTACKER_MCP = "https://attacker.t.example/mcp"


def _mcp_with_unreadable_url(token, url):
    """Servers a and b as in ``_mcp`` with ``token``, and server c, holding
    the same token, at ``url``."""
    mcp = _mcp(token, token)
    mcp["servers"]["c"] = {
        "transport": "http",
        "url": url,
        "auth": {"type": "bearer", "token": token},
    }
    return mcp


@pytest.mark.integration
@pytest.mark.api
class TestAURLThatDoesNotParseVouchesForNoOrigin:
    """A URL the origin rule cannot read names no origin. A kept secret is not
    moved to one, and a leaf stored at one does not vouch for the host a mask
    is pasted next to. A URL left exactly as stored still keeps its secret."""

    @MIGRATED
    @pytest.mark.parametrize(
        "bad_url", [BAD_PORT_URL, BAD_BRACKET_URL], ids=["port", "bracket"]
    )
    def test_a_server_moved_to_a_url_that_does_not_parse_keeps_nothing(
        self, organization, auth_client, gateway, migrated, bad_url
    ):
        _seed(organization, {}, _mcp(SHARED_TOKEN, SHARED_TOKEN), migrated)
        before = _active(organization).version
        pushes = len(_pushed(gateway))
        response = _dialog_save(auth_client, "b", url=bad_url)
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "secret_origin_changed" in response.content.decode()
        assert _active(organization).version == before
        assert len(_pushed(gateway)) == pushes

    @MIGRATED
    @pytest.mark.parametrize(
        "bad_url", [BAD_PORT_URL, BAD_BRACKET_URL], ids=["port", "bracket"]
    )
    def test_a_stored_url_that_does_not_parse_vouches_for_no_host(
        self, organization, auth_client, gateway, migrated, bad_url
    ):
        _seed(
            organization, {}, _mcp_with_unreadable_url(SHARED_TOKEN, bad_url), migrated
        )
        mask = _read_active(auth_client)["mcp"]["servers"]["c"]["auth"]["token"]
        assert mask.startswith(MASK)
        before = _active(organization).version
        response = auth_client.post(
            f"{GATEWAY}/update-mcp-server/",
            {
                "server_id": "d",
                "config": {
                    "transport": "http",
                    "url": ATTACKER_MCP,
                    "auth": {"type": "bearer", "token": mask},
                },
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "secret_origin_changed" in response.content.decode()
        assert _active(organization).version == before
        assert "attacker.t.example" not in json.dumps(_pushed(gateway))

    @MIGRATED
    @pytest.mark.parametrize(
        "bad_url", [BAD_PORT_URL, BAD_BRACKET_URL], ids=["port", "bracket"]
    )
    def test_a_url_that_does_not_parse_left_as_stored_keeps_its_secret(
        self, organization, auth_client, gateway, migrated, bad_url
    ):
        _seed(
            organization, {}, _mcp_with_unreadable_url(SHARED_TOKEN, bad_url), migrated
        )
        _ok(_editor_save(auth_client))
        _ok(_dialog_save(auth_client, "c"))
        payload = _pushed(gateway)[-1]
        assert payload["mcp"]["servers"]["c"]["url"] == bad_url
        assert _tokens(payload) == {
            "a": SHARED_TOKEN,
            "b": SHARED_TOKEN,
            "c": SHARED_TOKEN,
        }


@pytest.mark.integration
@pytest.mark.api
class TestASecretWhoseURLIsClearedIsKeptForNoHost:
    """A secret whose bound URL is cleared (blank, or the key removed) is kept:
    with no URL it is sent to no host, so clearing a URL never locks a save.
    It is then stored with no URL, so a URL added again, its old host or any
    other, needs the secret entered again (the rule for a secret stored with no URL)."""

    @MIGRATED
    @pytest.mark.parametrize("cleared", ["blank", "removed"])
    def test_a_cleared_url_keeps_the_secret_and_a_url_added_again_does_not(
        self, organization, auth_client, gateway, migrated, cleared
    ):
        _seed(organization, {}, _mcp("t-tok-a", "t-tok-b"), migrated)
        body = _read_active(auth_client)
        server = body["mcp"]["servers"]["b"]
        assert server["auth"]["token"].startswith(MASK)
        if cleared == "blank":
            server["url"] = ""
        else:
            del server["url"]
        _ok(auth_client.post(ORG_CONFIGS, {"mcp": body["mcp"]}, format="json"))
        assert (
            _active(organization)
            .mcp["servers"]["b"]["auth"]["token"]
            .startswith("enc::")
        )
        payload = _pushed(gateway)[-1]
        assert not payload["mcp"]["servers"]["b"].get("url")
        assert _tokens(payload) == {"a": "t-tok-a", "b": "t-tok-b"}

        before = _active(organization).version
        pushes = len(_pushed(gateway))
        for url in (MCP_B, ATTACKER_MCP):
            body = _read_active(auth_client)
            body["mcp"]["servers"]["b"]["url"] = url
            response = auth_client.post(
                ORG_CONFIGS, {"mcp": body["mcp"]}, format="json"
            )
            assert response.status_code == status.HTTP_400_BAD_REQUEST, (
                url,
                response.content,
            )
            assert "secret_origin_changed" in response.content.decode()
        assert _active(organization).version == before
        assert len(_pushed(gateway)) == pushes
        assert "attacker.t.example" not in json.dumps(_pushed(gateway))
