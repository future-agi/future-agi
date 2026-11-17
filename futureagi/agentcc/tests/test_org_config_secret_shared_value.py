"""One secret value stored at two URL-bound leaves on different origins still
saves, and the origin rule holds for it (NG-01; the D-A line's RF18 follow-up
918f63a740..ecd5ebc440, ported to the release line).

Every string header of an alert channel or audit sink is a secret leaf, so
``Content-Type: application/json`` on two channels with different hosts is one
value at two origins, and so is one MCP token used for servers on two hosts.
Both leaves read back the same mask: a legacy clear row masks by the clear
value's fingerprint, and migration 0047 reuses one ciphertext per (org, clear
value), so every migrated row with such a value is in this state. The console
then posts that mask back for each leaf (the Org Config editor through
``POST org-configs/``, the MCP page's untouched Edit through
update-mcp-server), and a working setup saves, whichever leaf comes first and
whether or not 0047 has run.

The MCP secrets are the MCP credential vault's now (one MCP secret store): migration 0056 moves a release row's inline
MCP tokens, clear or NG-01 ciphertext, into the vault before the new code
serves, and the config keeps a reference. So the MCP rows below seed the same
release rows, run 0056, and check the same console actions against the vault:
one token used for servers on two hosts still saves and pushes from each
server's own credential, and a save that moves a server to another host does
not take its credential along (M14 IR-9).

The origin rule: a mask keeps a secret only for an origin it is already stored
with. A URL whose origin cannot be read (it does not parse) names none, so a
secret is kept there only while that URL is left exactly as stored, and a leaf
stored at such a URL vouches for no other host.

Test values only, not real credentials.
"""

import importlib
import json

import pytest
from django.apps import apps as django_apps
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
    "agentcc.migrations.0047_encrypt_org_config_secrets"
)

# The secrets suite's fixtures: every push goes to a recording mock, and the
# role clients it makes are stopped after each test.
gateway = base.gateway
_stop_clients = base._stop_clients

HOOK_A = "https://hooks-a.rf18r.example/in"
HOOK_B = "https://hooks-b.rf18r.example/in"
MCP_A = "https://mcp-a.rf18r.example/mcp"
MCP_B = "https://mcp-b.rf18r.example/mcp"
AGENT_A = "https://agent-a.rf18r.example/a2a"
AGENT_B = "https://agent-b.rf18r.example/a2a"
THIRD = "https://third.rf18r.example/in"
THIRD_MCP = "https://third.rf18r.example/mcp"
THIRD_HOST = "third.rf18r.example"
SHARED_TOKEN = "rf18r-shared-tok"

MIGRATED = pytest.mark.parametrize(
    "migrated", [False, True], ids=["legacy", "migrated"]
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


def _a2a(t0, t1):
    return {
        "enabled": True,
        "agents": {
            "a": {"url": AGENT_A, "auth": {"type": "bearer", "token": t0}},
            "b": {"url": AGENT_B, "auth": {"type": "bearer", "token": t1}},
        },
    }


def _seed(organization, alerting, mcp, migrated, a2a=None):
    """A pre-deploy row (secrets in clear, as the old code stored them), then
    migration 0047 when ``migrated``, then migration 0056, which moves the
    row's MCP secrets into the vault, then 0057's binding of each moved
    credential to its server's origin (the deploy runs them all
    before the new code serves, and the push sends an unbound credential
    nowhere)."""
    AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=1,
        is_active=True,
        alerting=alerting,
        mcp=mcp,
        a2a=a2a or {},
    )
    if migrated:
        migration.forwards(_Apps(), None)
    if mcp:
        importlib.import_module(
            "agentcc.migrations.0056_mcp_secrets_to_vault"
        ).forwards(django_apps, None)
        importlib.import_module(
            "agentcc.migrations.0057_mcp_credential_origin"
        ).bind_sent_credentials(django_apps, None)


def _read_active(auth_client):
    return _result(_ok(auth_client.get(f"{ORG_CONFIGS}active/")))


def _editor_save(auth_client):
    """What the Org Config editor does: read active, post its sections back."""
    body = _read_active(auth_client)
    post = {
        name: body[name]
        for name in ("cache", "routing", "alerting", "mcp", "a2a", "audit")
        if name in body
    }
    post["change_description"] = "rf18r editor save"
    return auth_client.post(ORG_CONFIGS, post, format="json")


def _dialog_save(auth_client, server_id, **changes):
    """What the MCP dialog's Edit sends: the server as read, with ``changes``."""
    server = _read_active(auth_client)["mcp"]["servers"][server_id]
    return auth_client.post(
        f"{GATEWAY}/update-mcp-server/",
        {"server_id": server_id, "config": {**server, **changes}},
        format="json",
    )


def _refused_for_origin(response):
    """A 400 that refuses to keep a secret for this URL. POST org-configs/
    answers with the code ``secret_origin_changed``; update-mcp-server, whose
    secrets are the vault's, answers with M14 IR-9's sentence (the stored
    credential is not sent to another origin) under the generic 400 code."""
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    body = response.content.decode()
    assert any(
        text in body
        for text in (
            "secret_origin_changed",
            "a host it was not stored with",
            "mcp_credential_origin_changed",
            "moves the server to another origin",
        )
    ), body


def _channels(payload):
    return [
        (channel["url"], channel["headers"]["Content-Type"])
        for channel in payload["alerting"]["channels"]
    ]


def _tokens(payload):
    """Each MCP server's token as the push carries it: from the vault, as
    ``credential.secret``."""
    servers = payload["mcp"]["servers"]
    return {
        sid: servers[sid]["credential"]["secret"]["value"] for sid in sorted(servers)
    }


def _held_by_the_vault(organization, server_ids):
    """Each server's stored entry holds a reference to its own active vault
    credential and no secret; returns the credential ids."""
    from agentcc.models.mcp_credential import AgentccMCPCredential

    servers = _active(organization).mcp["servers"]
    ids = {}
    for server_id in server_ids:
        entry = servers[server_id]
        assert "auth" not in entry, server_id
        reference = entry["credential"]
        assert set(reference) == {"mode", "credential_id", "state"}, server_id
        assert (reference["mode"], reference["state"]) == ("shared", "active")
        row = AgentccMCPCredential.objects.get(pk=reference["credential_id"])
        assert (row.organization_id, row.server_id) == (organization.id, server_id)
        ids[server_id] = reference["credential_id"]
    assert len(set(ids.values())) == len(ids)
    return ids


def _agent_tokens(payload):
    agents = payload["a2a"]["agents"]
    return {name: agents[name]["auth"]["token"] for name in sorted(agents)}


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
            _mcp("rf18r-tok-a", "rf18r-tok-b"),
            migrated,
        )
        _ok(_editor_save(auth_client))
        payload = _pushed(gateway)[-1]
        assert _channels(payload) == [
            (HOOK_A, "application/json"),
            (HOOK_B, "text/plain"),
        ]
        assert _tokens(payload) == {"a": "rf18r-tok-a", "b": "rf18r-tok-b"}

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
        _held_by_the_vault(organization, ["a", "b"])
        assert _tokens(_pushed(gateway)[-1]) == {"a": SHARED_TOKEN, "b": SHARED_TOKEN}

    @MIGRATED
    def test_editor_save_same_a2a_token_two_hosts(
        self, organization, auth_client, gateway, migrated
    ):
        _seed(organization, {}, {}, migrated, a2a=_a2a(SHARED_TOKEN, SHARED_TOKEN))
        _ok(_editor_save(auth_client))
        assert _agent_tokens(_pushed(gateway)[-1]) == {
            "a": SHARED_TOKEN,
            "b": SHARED_TOKEN,
        }

    @MIGRATED
    @pytest.mark.parametrize("server_id", ["a", "b"])
    def test_mcp_dialog_untouched_save_same_token_two_hosts(
        self, organization, auth_client, gateway, migrated, server_id
    ):
        _seed(organization, {}, _mcp(SHARED_TOKEN, SHARED_TOKEN), migrated)
        before = _held_by_the_vault(organization, ["a", "b"])
        _ok(_dialog_save(auth_client, server_id))
        # Each server keeps its own credential, and the config no secret.
        assert _held_by_the_vault(organization, ["a", "b"]) == before
        assert "enc::" not in json.dumps(_active(organization).mcp)
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
            _alerting("rf18r-shared-hook", "rf18r-shared-hook"),
            {},
            migrated,
        )
        before = _active(organization).version
        pushes = len(_pushed(gateway))
        alerting = _read_active(auth_client)["alerting"]
        assert alerting["channels"][0]["headers"]["Content-Type"].startswith(MASK)
        alerting["channels"][0]["url"] = THIRD
        response = auth_client.post(ORG_CONFIGS, {"alerting": alerting}, format="json")
        _refused_for_origin(response)
        assert _active(organization).version == before
        assert len(_pushed(gateway)) == pushes
        assert THIRD_HOST not in json.dumps(_pushed(gateway))

    @MIGRATED
    @pytest.mark.parametrize("server_id", ["a", "b"])
    def test_an_mcp_server_moved_to_a_third_host_keeps_nothing(
        self, organization, auth_client, gateway, migrated, server_id
    ):
        _seed(organization, {}, _mcp(SHARED_TOKEN, SHARED_TOKEN), migrated)
        before = _active(organization).version
        response = _dialog_save(auth_client, server_id, url=THIRD_MCP)
        _refused_for_origin(response)
        assert _active(organization).version == before
        assert THIRD_HOST not in json.dumps(_pushed(gateway))


# URLs the origin rule cannot read: a port out of range, an unbalanced IPv6
# bracket, and a bracketed host that is not an IP address (Python's urlsplit
# refuses it; another parser may read the name inside the brackets). An MCP
# server's url, the A2A agent's url and the semantic cache's url are plain
# strings in the contract, so any of these can be stored.
BAD_PORT_URL = "https://mcp-c.rf18r.example:99999/mcp"
BAD_BRACKET_URL = "https://[mcp-c.rf18r.example/mcp"
BRACKETED_HOST_URL = "https://[third.rf18r.example]/mcp"
BAD_URLS = pytest.mark.parametrize(
    "bad_url",
    [BAD_PORT_URL, BAD_BRACKET_URL, BRACKETED_HOST_URL],
    ids=["port", "bracket", "bracketed-host"],
)


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
    is pasted next to. A URL left exactly as stored still keeps its secret,
    which the push sends nowhere (it is bound to no origin)."""

    @MIGRATED
    @BAD_URLS
    def test_an_agent_moved_to_a_url_that_does_not_parse_keeps_nothing(
        self, organization, auth_client, gateway, migrated, bad_url
    ):
        # The A2A agent's url has no URL check on save, so the origin rule
        # alone decides whether its token goes with it.
        _seed(organization, {}, {}, migrated, a2a=_a2a(SHARED_TOKEN, "rf18r-b"))
        before = _active(organization).version
        pushes = len(_pushed(gateway))
        a2a = _read_active(auth_client)["a2a"]
        assert a2a["agents"]["a"]["auth"]["token"].startswith(MASK)
        a2a["agents"]["a"]["url"] = bad_url
        response = auth_client.post(ORG_CONFIGS, {"a2a": a2a}, format="json")
        _refused_for_origin(response)
        assert _active(organization).version == before
        assert len(_pushed(gateway)) == pushes

    @MIGRATED
    @BAD_URLS
    def test_an_mcp_server_moved_to_a_url_that_does_not_parse_keeps_nothing(
        self, organization, auth_client, gateway, migrated, bad_url
    ):
        # Refused either by the MCP url check (a URL it cannot read) or by the
        # origin rule (a port out of range passes the url check).
        _seed(organization, {}, _mcp(SHARED_TOKEN, SHARED_TOKEN), migrated)
        before = _active(organization).version
        pushes = len(_pushed(gateway))
        response = _dialog_save(auth_client, "b", url=bad_url)
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert _active(organization).version == before
        assert len(_pushed(gateway)) == pushes

    @MIGRATED
    @BAD_URLS
    def test_a_stored_url_that_does_not_parse_vouches_for_no_host(
        self, organization, auth_client, gateway, migrated, bad_url
    ):
        # After the move a reader sees server c's credential reference, never
        # a mask of its token. Naming that credential for a new server on
        # another host does not send the token there: the vault owns
        # credential_id.
        _seed(
            organization, {}, _mcp_with_unreadable_url(SHARED_TOKEN, bad_url), migrated
        )
        server = _read_active(auth_client)["mcp"]["servers"]["c"]
        assert "auth" not in server
        assert MASK not in json.dumps(server)
        before = _active(organization).version
        response = auth_client.post(
            f"{GATEWAY}/update-mcp-server/",
            {
                "server_id": "d",
                "config": {
                    "transport": "http",
                    "url": THIRD_MCP,
                    "credential": dict(server["credential"]),
                },
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert _active(organization).version == before
        assert THIRD_HOST not in json.dumps(_pushed(gateway))

    @MIGRATED
    @BAD_URLS
    def test_an_agent_url_that_does_not_parse_vouches_for_no_host(
        self, organization, auth_client, gateway, migrated, bad_url
    ):
        a2a = _a2a(SHARED_TOKEN, "rf18r-b")
        a2a["agents"]["c"] = {
            "url": bad_url,
            "auth": {"type": "bearer", "token": SHARED_TOKEN},
        }
        _seed(organization, {}, {}, migrated, a2a=a2a)
        body = _read_active(auth_client)["a2a"]
        mask = body["agents"]["c"]["auth"]["token"]
        body["agents"]["d"] = {
            "url": "https://third.rf18r.example/a2a",
            "auth": {"type": "bearer", "token": mask},
        }
        before = _active(organization).version
        response = auth_client.post(ORG_CONFIGS, {"a2a": body}, format="json")
        _refused_for_origin(response)
        assert _active(organization).version == before
        assert THIRD_HOST not in json.dumps(_pushed(gateway))

    @MIGRATED
    @BAD_URLS
    def test_a_url_that_does_not_parse_left_as_stored_keeps_its_secret(
        self, organization, auth_client, gateway, migrated, bad_url
    ):
        """Saved unchanged, by the editor and by the MCP dialog, server c keeps
        its credential: both saves answer 200, and c still references the same
        active vault credential, which still holds the token. The push sends a
        credential only to its server at the origin it is bound to, and a URL
        that does not parse has none, so c is pushed
        with no secret while a and b get theirs. A parser that reads such a
        URL differently could otherwise send the token to another host."""
        from agentcc.models.mcp_credential import AgentccMCPCredential
        from agentcc.services.credential_manager import decrypt_token

        _seed(
            organization, {}, _mcp_with_unreadable_url(SHARED_TOKEN, bad_url), migrated
        )
        held = _held_by_the_vault(organization, ["a", "b", "c"])
        _ok(_editor_save(auth_client))
        _ok(_dialog_save(auth_client, "c"))
        assert _held_by_the_vault(organization, ["a", "b", "c"]) == held
        row = AgentccMCPCredential.objects.get(pk=held["c"])
        assert decrypt_token(row.encrypted_secret) == SHARED_TOKEN
        assert row.origin == ""
        servers = _pushed(gateway)[-1]["mcp"]["servers"]
        assert servers["c"]["url"] == bad_url
        assert "secret" not in servers["c"].get("credential", {})
        assert SHARED_TOKEN not in json.dumps(servers["c"])
        assert {
            sid: servers[sid]["credential"]["secret"]["value"] for sid in ("a", "b")
        } == {"a": SHARED_TOKEN, "b": SHARED_TOKEN}


@pytest.mark.integration
@pytest.mark.api
class TestASecretWhoseURLIsClearedIsKeptForNoHost:
    """A secret whose bound URL is cleared (blank, or the key removed) is kept:
    with no URL it is sent to no host, so clearing a URL never locks a save.
    It is then stored with no URL, so a URL added again, its old host or any
    other, needs the secret entered again (the rule for a secret stored with
    no URL)."""

    @MIGRATED
    @pytest.mark.parametrize("cleared", ["blank", "removed"])
    def test_a_cleared_url_keeps_the_secret_and_a_url_added_again_does_not(
        self, organization, auth_client, gateway, migrated, cleared
    ):
        _seed(organization, {}, {}, migrated, a2a=_a2a("rf18r-tok-a", "rf18r-tok-b"))
        body = _read_active(auth_client)
        agent = body["a2a"]["agents"]["b"]
        assert agent["auth"]["token"].startswith(MASK)
        if cleared == "blank":
            agent["url"] = ""
        else:
            del agent["url"]
        _ok(auth_client.post(ORG_CONFIGS, {"a2a": body["a2a"]}, format="json"))
        assert (
            _active(organization)
            .a2a["agents"]["b"]["auth"]["token"]
            .startswith("enc::")
        )
        payload = _pushed(gateway)[-1]
        assert not payload["a2a"]["agents"]["b"].get("url")
        assert _agent_tokens(payload) == {"a": "rf18r-tok-a", "b": "rf18r-tok-b"}

        before = _active(organization).version
        pushes = len(_pushed(gateway))
        for url in (AGENT_B, "https://third.rf18r.example/a2a"):
            body = _read_active(auth_client)
            body["a2a"]["agents"]["b"]["url"] = url
            response = auth_client.post(
                ORG_CONFIGS, {"a2a": body["a2a"]}, format="json"
            )
            _refused_for_origin(response)
        assert _active(organization).version == before
        assert len(_pushed(gateway)) == pushes
        assert THIRD_HOST not in json.dumps(_pushed(gateway))
