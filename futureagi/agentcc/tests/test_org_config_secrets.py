"""Org-config secrets are stored encrypted, masked to every reader, and sent
to the gateway in clear (NG-01: no-gimmick console-a CA-F2, django-api DA-1).

An org config can hold credentials for systems outside the gateway: the cache
backends' Redis password, S3 secret access key, Azure connection string and
SAS token and the semantic cache's vector-store API keys, the A2A agents'
tokens, the audit and alert webhook headers, and the MCP servers' auth. Any
member of the org, a Viewer included, read them in clear on every org-config
read path and on Settings > Full Config. Here an Owner saves them the way the
console does, and every role reads every path and finds none of them; the
gateway push and the gateway's bulk sync still carry each one in clear.

The MCP secrets are the MCP credential vault's (M14; MCP-APPS-PLAN.md §2.1
ruling 3, one MCP secret store): the console saves a server's through
update-mcp-server and a stored one through /agentcc/mcp/credentials/, the
config holds only a reference to it, and the push adds it from the vault as
``credential.secret``. The rest stay inline, encrypted under ``enc::``.

The semantic cache's settings can no longer be saved (400
semantic_cache_not_supported, docs-agentcc-711 item 4), so its vector-store
keys stand here for a value an earlier release stored: the fixture writes
them into v1, encrypted as the writer stores them, and v2 keeps them.

Test values only, not real credentials.
"""

import json
import secrets
import uuid
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.org_config_secrets import encrypt_config_secrets
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

GATEWAY = "/agentcc/gateways/default"
ORG_CONFIGS = "/agentcc/org-configs/"
MCP_CREDENTIALS = "/agentcc/mcp/credentials/"
MASK = "__encrypted__"
ADMIN_TOKEN = secrets.token_hex(32)

LEVEL_ROLES = {
    Level.ADMIN: OrganizationRoles.ADMIN,
    Level.MEMBER: OrganizationRoles.MEMBER,
    Level.VIEWER: OrganizationRoles.MEMBER_VIEW_ONLY,
}


def _secrets(tag):
    """One distinctive value per secret leaf, keyed by its JSON pointer."""
    return {
        "/cache/redis/password": f"rf18-redis-pw-{tag}",
        "/cache/s3/secret_access_key": f"rf18-s3-sak-{tag}",
        "/cache/azure_blob/connection_string": (
            f"DefaultEndpointsProtocol=https;AccountName=rf18;AccountKey=rf18-az-{tag}"
        ),
        "/cache/azure_blob/sas_token": f"sv=2024-01-01&sig=rf18-sas-{tag}",
        "/cache/semantic/qdrant/api_key": f"rf18-qdrant-{tag}",
        "/cache/semantic/weaviate/api_key": f"rf18-weaviate-{tag}",
        "/cache/semantic/pinecone/api_key": f"rf18-pinecone-{tag}",
        "/a2a/agents/billing/auth/token": f"rf18-a2a-bearer-{tag}",
        "/audit/sinks/0/headers/Authorization": f"Bearer rf18-siem-{tag}",
        "/alerting/channels/0/headers/X-Hook-Token": f"rf18-hook-{tag}",
    }


def _mcp_secrets(tag):
    """One value per MCP secret, keyed by the JSON pointer the push carries it
    at (the vault adds it as ``credential.secret``); the stored config holds
    a reference there, never the value."""
    return {
        "/mcp/servers/gh/credential/secret/value": f"rf18-mcp-bearer-{tag}",
        "/mcp/servers/kv/credential/secret/value": f"rf18-mcp-key-{tag}",
        "/mcp/servers/vault/credential/secret/value": f"rf18-mcp-cred-{tag}",
        "/mcp/managed_tools/credentials/fagi.search/secret/value": (
            f"rf18-managed-{tag}"
        ),
    }


def _inline(s):
    """The pointers of the secrets stored inline (encrypted, read as masks):
    every one but the MCP secrets, which the vault holds."""
    return {pointer: value for pointer, value in s.items() if not _is_mcp(pointer)}


def _is_mcp(pointer):
    return pointer.startswith("/mcp/")


MCP_SERVERS = {
    "gh": "https://mcp.rf18.example/gh/mcp",
    "kv": "https://mcp.rf18.example/kv/mcp",
    "vault": "https://vault.rf18.example/mcp",
}
def _stored_semantic(s):
    """A semantic cache section an earlier release stored, with a key for
    each vector store."""
    return {
        "enabled": True,
        "backend": "qdrant",
        "qdrant": {
            "url": "https://qdrant.rf18.example",
            "collection": "rf18",
            "api_key": s["/cache/semantic/qdrant/api_key"],
        },
        "weaviate": {
            "url": "https://weaviate.rf18.example",
            "api_key": s["/cache/semantic/weaviate/api_key"],
        },
        "pinecone": {
            "url": "https://idx.rf18.example",
            "api_key": s["/cache/semantic/pinecone/api_key"],
        },
    }


def _console_patch(s):
    """What the console's Org Config editor saves, through update-config,
    with a secret in every password-style field. The MCP page saves the MCP
    servers and their secrets itself (``_save_mcp``)."""
    return {
        "cache": {
            "enabled": True,
            "backend": "redis",
            "redis": {
                "address": "redis.rf18.example:6379",
                "password": s["/cache/redis/password"],
                "tls": True,
            },
            "s3": {
                "bucket": "rf18-bucket",
                "region": "us-east-1",
                "access_key_id": "AKIARF18EXAMPLE",
                "secret_access_key": s["/cache/s3/secret_access_key"],
            },
            "azure_blob": {
                "container": "rf18",
                "connection_string": s["/cache/azure_blob/connection_string"],
                "sas_token": s["/cache/azure_blob/sas_token"],
            },
        },
        "a2a": {
            "enabled": True,
            "agents": {
                "billing": {
                    "url": "https://agents.rf18.example/billing",
                    "auth": {
                        "type": "bearer",
                        "token": s["/a2a/agents/billing/auth/token"],
                    },
                }
            },
        },
        "audit": {
            "enabled": True,
            "sinks": [
                {
                    "type": "webhook",
                    "url": "https://siem.rf18.example/in",
                    "headers": {
                        "Authorization": s["/audit/sinks/0/headers/Authorization"]
                    },
                }
            ],
        },
        "alerting": {
            "enabled": True,
            "channels": [
                {
                    "name": "ops-hook",
                    "type": "webhook",
                    "url": "https://hooks.rf18.example/alerts",
                    "headers": {
                        "X-Hook-Token": s["/alerting/channels/0/headers/X-Hook-Token"]
                    },
                }
            ],
        },
        "mcp": {"enabled": True},
    }


def _save_mcp(auth_client, m):
    """What the MCP page saves: a server with a typed secret through
    update-mcp-server (the vault stores it, the config gets a reference), a
    server's stored credential and a managed tool's through
    /agentcc/mcp/credentials/."""
    _ok(
        _save_server(
            auth_client,
            "gh",
            {"type": "bearer", "token": m["/mcp/servers/gh/credential/secret/value"]},
        )
    )
    _ok(
        _save_server(
            auth_client,
            "kv",
            {
                "type": "api_key",
                "header": "X-API-Key",
                "key": m["/mcp/servers/kv/credential/secret/value"],
            },
        )
    )
    _ok(_save_server(auth_client, "vault", None))
    for body in (
        {
            "server_id": "vault",
            "kind": "bearer",
            "value": m["/mcp/servers/vault/credential/secret/value"],
        },
        {
            "server_id": "fagi.search",
            "kind": "api_key",
            "header_name": "X-Search-Key",
            "value": m["/mcp/managed_tools/credentials/fagi.search/secret/value"],
        },
    ):
        response = auth_client.post(MCP_CREDENTIALS, body, format="json")
        assert response.status_code == status.HTTP_201_CREATED, response.content[:800]


def _save_server(auth_client, server_id, auth, url=None):
    config = {"transport": "http", "url": url or MCP_SERVERS[server_id]}
    if auth is not None:
        config["auth"] = auth
    return auth_client.post(
        f"{GATEWAY}/update-mcp-server/",
        {"server_id": server_id, "config": config},
        format="json",
    )


def _at(document, pointer):
    node = document
    for part in pointer.strip("/").split("/"):
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


_CLIENTS = []


@pytest.fixture(autouse=True)
def _stop_clients():
    yield
    while _CLIENTS:
        _CLIENTS.pop().stop_workspace_injection()


@pytest.fixture
def gateway():
    """Every push goes to a mock that records what it got; nothing is sent."""
    client = MagicMock()
    client.health_check.return_value = {"status": "ok"}
    with (
        patch("agentcc.services.config_push.get_gateway_client", return_value=client),
        patch("agentcc.views.gateway.get_gateway_client", return_value=client),
    ):
        yield client


def _pushed(gateway):
    return [call.args[1] for call in gateway.set_org_config.call_args_list]


def _role_client(organization, level):
    user = User.objects.create_user(
        email=f"rf18-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="rf18 reader",
        organization=organization,
        organization_role=LEVEL_ROLES[level],
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
        role=LEVEL_ROLES[level],
        level=level,
        is_active=True,
    )
    workspace = Workspace.no_workspace_objects.get(
        organization=organization, is_default=True
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        organization_membership=membership,
        is_active=True,
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    _CLIENTS.append(client)
    return client


def _ok(response):
    assert response.status_code == status.HTTP_200_OK, response.content[:800]
    return response


def _result(response):
    body = response.json()
    return body.get("result", body)


def _active(organization):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )


def _save(auth_client, body):
    return auth_client.post(f"{GATEWAY}/update-config/", body, format="json")


def _found(text, s):
    return sorted(pointer for pointer, value in s.items() if value in text)


@pytest.fixture
def saved(organization, auth_client, gateway):
    """v1 holding only the semantic cache an earlier release stored, v2 with
    every inline secret the console saves, then the MCP page's saves (v3 on),
    whose secrets go to the vault. Returns every secret by pointer (see
    ``_inline`` and ``_mcp_secrets``)."""
    tag = uuid.uuid4().hex[:12]
    s = _secrets(tag)
    m = _mcp_secrets(tag)
    _ok(auth_client.post(ORG_CONFIGS, {"change_description": "rf18 v1"}, format="json"))
    v1 = _active(organization)
    v1.cache = {**(v1.cache or {}), "semantic": _stored_semantic(s)}
    encrypt_config_secrets(v1, None)
    v1.save(update_fields=["cache", "updated_at"])
    _ok(_save(auth_client, _console_patch(s)))
    _save_mcp(auth_client, m)
    return {**s, **m}


def _vault_rows(organization):
    from agentcc.models.mcp_credential import AgentccMCPCredential

    return {
        row.server_id: row
        for row in AgentccMCPCredential.objects.filter(
            organization=organization, status="active"
        )
    }


@pytest.mark.integration
@pytest.mark.api
class TestNoReaderSeesAStoredSecret:
    @pytest.mark.parametrize(
        "level", [Level.VIEWER, Level.MEMBER, Level.ADMIN], ids=str
    )
    def test_every_read_path_masks_every_secret(
        self, organization, auth_client, gateway, saved, level
    ):
        s = saved
        active = _active(organization)
        v1 = AgentccOrgConfig.no_workspace_objects.get(
            organization=organization, version=1, deleted=False
        )
        client = _role_client(organization, level)
        paths = {
            "gateways/default/config (Full Config)": f"{GATEWAY}/config/",
            "org-configs list (versions)": ORG_CONFIGS,
            "org-configs active": f"{ORG_CONFIGS}active/",
            "org-configs retrieve": f"{ORG_CONFIGS}{active.id}/",
            "org-configs diff": f"{ORG_CONFIGS}{active.id}/diff/?compare_to={v1.id}",
            "org-configs diff (reverse)": f"{ORG_CONFIGS}{v1.id}/diff/?compare_to={active.id}",
        }
        for label, path in paths.items():
            response = _ok(client.get(path))
            leaked = _found(response.content.decode(), s)
            assert leaked == [], f"{label}: {leaked}"

    def test_the_owner_sees_no_secret_either(
        self, organization, auth_client, gateway, saved
    ):
        active = _active(organization)
        for path in (
            f"{GATEWAY}/config/",
            ORG_CONFIGS,
            f"{ORG_CONFIGS}active/",
            f"{ORG_CONFIGS}{active.id}/",
        ):
            leaked = _found(_ok(auth_client.get(path)).content.decode(), saved)
            assert leaked == [], f"{path}: {leaked}"

    def test_write_responses_carry_no_secret(
        self, organization, auth_client, gateway, saved
    ):
        # POST org-configs/ and activate both answer with the version.
        created = _ok(
            auth_client.post(
                ORG_CONFIGS,
                {"budgets": {"enabled": False}, "change_description": "rf18 v3"},
                format="json",
            )
        )
        assert _found(created.content.decode(), saved) == []
        v2 = AgentccOrgConfig.no_workspace_objects.get(
            organization=organization, version=2, deleted=False
        )
        activated = _ok(auth_client.post(f"{ORG_CONFIGS}{v2.id}/activate/"))
        assert _found(activated.content.decode(), saved) == []

    def test_each_secret_reads_as_a_mask_with_a_set_flag(
        self, organization, auth_client, gateway, saved
    ):
        viewer = _role_client(organization, Level.VIEWER)
        inline = _inline(saved)
        vault = _vault_rows(organization)
        for path in (f"{GATEWAY}/config/", f"{ORG_CONFIGS}active/"):
            body = _result(_ok(viewer.get(path)))
            assert sorted(body["secrets_set"]) == sorted(inline), path
            for pointer in inline:
                value = _at(body, pointer)
                assert isinstance(value, str) and value.startswith(MASK), (
                    path,
                    pointer,
                    value,
                )
            # An MCP secret is not in the config at all: the server reads as
            # a reference to its vault credential, with no secret and no mask.
            for server_id in ("gh", "kv", "vault"):
                server = _at(body, f"/mcp/servers/{server_id}")
                assert "auth" not in server, (path, server_id)
                assert server["credential"] == {
                    "mode": "shared",
                    "credential_id": str(vault[server_id].pk),
                    "state": "active",
                }, (path, server_id)
            assert _at(body, "/mcp/managed_tools/credentials/fagi.search") == {
                "mode": "shared",
                "credential_id": str(vault["fagi.search"].pk),
                "state": "active",
            }, path
            assert MASK not in json.dumps(body["mcp"]), path
            # Non-secret settings still read as stored.
            assert _at(body, "/cache/redis/address") == "redis.rf18.example:6379"
            assert _at(body, "/cache/s3/access_key_id") == "AKIARF18EXAMPLE"
            assert _at(body, "/a2a/agents/billing/auth/type") == "bearer"
            assert _at(body, "/mcp/servers/kv/url") == MCP_SERVERS["kv"]

    def test_a_rotated_secret_shows_as_changed_in_the_diff_without_its_value(
        self, organization, auth_client, gateway, saved
    ):
        before = _active(organization)
        _ok(_save(auth_client, {"cache": {"redis": {"password": "rf18-rotated-pw"}}}))
        after = _active(organization)
        diff = _result(
            _ok(
                auth_client.get(f"{ORG_CONFIGS}{after.id}/diff/?compare_to={before.id}")
            )
        )
        assert "rf18-rotated-pw" not in json.dumps(diff)
        change = diff["cache"]["changed"]["redis"]
        assert change["old"]["password"].startswith(MASK)
        assert change["new"]["password"].startswith(MASK)
        assert change["old"]["password"] != change["new"]["password"]
        # An untouched secret is not a change.
        assert "s3" not in diff["cache"]["changed"]


@pytest.mark.integration
@pytest.mark.api
class TestStoredEncrypted:
    def test_no_secret_is_stored_in_clear(
        self, organization, auth_client, gateway, saved
    ):
        for config in AgentccOrgConfig.all_objects.filter(organization=organization):
            stored = json.dumps(
                {
                    section: getattr(config, section)
                    for section in ("cache", "a2a", "audit", "alerting", "mcp")
                }
            )
            assert _found(stored, saved) == [], config.version
        active = _active(organization)
        sections = {
            section: getattr(active, section)
            for section in ("cache", "a2a", "audit", "alerting", "mcp")
        }
        for pointer in _inline(saved):
            assert _at(sections, pointer).startswith("enc::"), pointer
        # The MCP secrets are in the vault, encrypted, and nowhere in the
        # config: no version holds one, in clear or as enc:: ciphertext.
        from agentcc.services.credential_manager import decrypt_token

        for config in AgentccOrgConfig.all_objects.filter(organization=organization):
            assert "enc::" not in json.dumps(config.mcp), config.version
        vault = _vault_rows(organization)
        for pointer, value in saved.items():
            if not _is_mcp(pointer):
                continue
            parts = pointer.split("/")
            server_id = parts[4] if parts[2] == "managed_tools" else parts[3]
            row = vault[server_id]
            assert row.encrypted_secret.startswith("enc::"), pointer
            assert value not in row.encrypted_secret, pointer
            assert decrypt_token(row.encrypted_secret) == value, pointer


@pytest.mark.integration
@pytest.mark.api
class TestTheGatewayGetsEverySecret:
    def test_the_push_carries_every_secret_in_clear(
        self, organization, auth_client, gateway, saved
    ):
        payload = _pushed(gateway)[-1]
        for pointer, value in saved.items():
            assert _at(payload, pointer) == value, pointer
        assert MASK not in json.dumps(payload)
        assert "enc::" not in json.dumps(payload)

    def test_the_bulk_sync_carries_every_secret_in_clear(
        self, organization, auth_client, gateway, saved
    ):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {ADMIN_TOKEN}")
        with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN):
            response = _ok(client.get(f"{ORG_CONFIGS}bulk/"))
        payload = response.json()["result"][str(organization.id)]
        for pointer, value in saved.items():
            assert _at(payload, pointer) == value, pointer

    def test_activating_an_older_version_pushes_its_own_secrets(
        self, organization, auth_client, gateway, saved
    ):
        v2 = _active(organization)
        _ok(_save(auth_client, {"cache": {"redis": {"password": "rf18-v3-pw"}}}))
        assert _at(_pushed(gateway)[-1], "/cache/redis/password") == "rf18-v3-pw"

        _ok(auth_client.post(f"{ORG_CONFIGS}{v2.id}/activate/"))

        payload = _pushed(gateway)[-1]
        assert _at(payload, "/cache/redis/password") == saved["/cache/redis/password"]


@pytest.mark.integration
@pytest.mark.api
class TestKeepOnBlank:
    def test_a_blank_keeps_the_stored_secret(
        self, organization, auth_client, gateway, saved
    ):
        before = _active(organization).cache["redis"]["password"]
        _ok(
            _save(
                auth_client,
                {"cache": {"redis": {"password": "", "pool_size": 20}}},
            )
        )
        assert _active(organization).cache["redis"]["password"] == before
        payload = _pushed(gateway)[-1]
        assert _at(payload, "/cache/redis/password") == saved["/cache/redis/password"]
        assert _at(payload, "/cache/redis/pool_size") == 20

    def test_sending_back_what_a_reader_got_keeps_every_secret(
        self, organization, auth_client, gateway, saved
    ):
        # A client that saves the sections it read (the masks included), as
        # POST org-configs/ takes them, changes nothing it did not edit.
        read = _result(_ok(auth_client.get(f"{ORG_CONFIGS}active/")))
        body = {
            section: read[section] for section in ("cache", "a2a", "audit", "alerting")
        }
        body["cache"]["default_ttl"] = 900
        _ok(auth_client.post(ORG_CONFIGS, body, format="json"))
        payload = _pushed(gateway)[-1]
        for pointer, value in saved.items():
            assert _at(payload, pointer) == value, pointer
        assert payload["cache"]["default_ttl"] == 900

    def test_a_list_saved_back_with_masks_keeps_each_items_own_secret(
        self, organization, auth_client, gateway, saved
    ):
        # Two audit sinks at one origin with different headers; the first is
        # removed, so the second moves to index 0 and must keep its own value.
        _ok(
            _save(
                auth_client,
                {
                    "audit": {
                        "sinks": [
                            {
                                "type": "webhook",
                                "url": "https://siem.rf18.example/in",
                                "headers": {"Authorization": "Bearer rf18-first"},
                            },
                            {
                                "type": "webhook",
                                "url": "https://siem.rf18.example/in",
                                "headers": {"Authorization": "Bearer rf18-second"},
                            },
                        ]
                    }
                },
            )
        )
        read = _result(_ok(auth_client.get(f"{ORG_CONFIGS}active/")))
        sinks = read["audit"]["sinks"]
        _ok(_save(auth_client, {"audit": {"sinks": [sinks[1]]}}))
        payload = _pushed(gateway)[-1]
        assert payload["audit"]["sinks"] == [
            {
                "type": "webhook",
                "url": "https://siem.rf18.example/in",
                "headers": {"Authorization": "Bearer rf18-second"},
            }
        ]

    def test_a_new_value_replaces_and_null_removes(
        self, organization, auth_client, gateway, saved
    ):
        _ok(
            _save(
                auth_client,
                {
                    "cache": {"s3": {"secret_access_key": "rf18-new-sak"}},
                    "a2a": {"agents": {"billing": {"auth": {"token": None}}}},
                },
            )
        )
        payload = _pushed(gateway)[-1]
        assert _at(payload, "/cache/s3/secret_access_key") == "rf18-new-sak"
        assert "token" not in payload["a2a"]["agents"]["billing"]["auth"]
        body = _result(_ok(auth_client.get(f"{ORG_CONFIGS}active/")))
        assert "/a2a/agents/billing/auth/token" not in body["secrets_set"]
        assert "rf18-new-sak" not in json.dumps(body)

    def test_a_mask_with_nothing_stored_behind_it_is_refused(
        self, organization, auth_client, gateway, saved
    ):
        before = _active(organization).version
        response = _save(
            auth_client,
            {
                "a2a": {
                    "agents": {
                        "newcomer": {
                            "url": "https://agents.rf18.example/new",
                            "auth": {"type": "bearer", "token": MASK},
                        }
                    }
                }
            },
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "secret_not_kept" in response.content.decode()
        assert _active(organization).version == before

    def test_a_ciphertext_sent_in_is_a_new_value_not_another_secret(
        self, organization, auth_client, gateway, saved
    ):
        # A value that looks like stored ciphertext, copied from another field,
        # must not make the push decrypt that other field's secret here.
        stolen = _active(organization).cache["redis"]["password"]
        _ok(_save(auth_client, {"cache": {"s3": {"secret_access_key": stolen}}}))
        payload = _pushed(gateway)[-1]
        assert _at(payload, "/cache/s3/secret_access_key") == stolen
        assert (
            _at(payload, "/cache/s3/secret_access_key")
            != saved["/cache/redis/password"]
        )


@pytest.mark.integration
@pytest.mark.api
class TestKeepingNeverFollowsAnOriginChange:
    @pytest.mark.parametrize(
        "token", [None, "", "mask"], ids=["untouched", "blank", "mask"]
    )
    def test_an_agent_moved_to_another_host_does_not_keep_its_token(
        self, organization, auth_client, gateway, saved, token
    ):
        before = _active(organization).version
        agent = {"url": "https://attacker.example/collect"}
        if token == "mask":
            # The real mask a reader is shown, round-tripped (not a bare stem).
            read = _result(_ok(auth_client.get(f"{ORG_CONFIGS}active/")))
            token = _at(read, "/a2a/agents/billing/auth/token")
            assert token.startswith(MASK)
        if token is not None:
            agent["auth"] = {"type": "bearer", "token": token}
        response = _save(auth_client, {"a2a": {"agents": {"billing": agent}}})
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "secret_origin_changed" in response.content.decode()
        assert _active(organization).version == before
        assert "attacker.example" not in json.dumps(_pushed(gateway))

    def test_a_path_change_on_the_same_origin_keeps_the_token(
        self, organization, auth_client, gateway, saved
    ):
        _ok(
            _save(
                auth_client,
                {
                    "a2a": {
                        "agents": {"billing": {"url": "https://agents.rf18.example/v2"}}
                    }
                },
            )
        )
        payload = _pushed(gateway)[-1]
        assert (
            _at(payload, "/a2a/agents/billing/auth/token")
            == saved["/a2a/agents/billing/auth/token"]
        )

    def test_a_moved_secret_is_saved_when_entered_again(
        self, organization, auth_client, gateway, saved
    ):
        _ok(
            _save(
                auth_client,
                {
                    "a2a": {
                        "agents": {
                            "billing": {
                                "url": "https://other.rf18.example/billing",
                                "auth": {"type": "bearer", "token": "rf18-reentered"},
                            }
                        }
                    }
                },
            )
        )
        payload = _pushed(gateway)[-1]
        assert _at(payload, "/a2a/agents/billing/auth/token") == "rf18-reentered"

    def test_a_vector_store_moved_to_another_host_does_not_keep_its_key(
        self, organization, auth_client, gateway, saved
    ):
        response = _save(
            auth_client,
            {"cache": {"semantic": {"pinecone": {"url": "https://attacker.example"}}}},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "secret_origin_changed" in response.content.decode()

    def test_redis_moved_to_another_host_does_not_keep_its_password(
        self, organization, auth_client, gateway, saved
    ):
        response = _save(
            auth_client, {"cache": {"redis": {"address": "attacker.example:6379"}}}
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "secret_origin_changed" in response.content.decode()

    def test_an_audit_sink_moved_to_another_host_does_not_keep_its_headers(
        self, organization, auth_client, gateway, saved
    ):
        read = _result(_ok(auth_client.get(f"{ORG_CONFIGS}active/")))
        sink = {**read["audit"]["sinks"][0], "url": "https://attacker.example/in"}
        response = _save(auth_client, {"audit": {"sinks": [sink]}})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "secret_origin_changed" in response.content.decode()

    def test_an_mcp_server_moved_to_another_host_does_not_keep_its_token(
        self, organization, auth_client, gateway, saved
    ):
        response = auth_client.post(
            f"{GATEWAY}/update-mcp-server/",
            {
                "server_id": "gh",
                "config": {
                    "transport": "http",
                    "url": "https://attacker.example/mcp",
                    "auth": {"type": "bearer", "token": ""},
                },
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "origin" in response.content.decode()
        assert "attacker.example" not in json.dumps(_pushed(gateway))

    def test_an_unbound_secret_mask_cannot_be_attached_to_an_attacker_url(
        self, organization, auth_client, gateway, saved
    ):
        # A reader sees the mask of a secret stored with no bound URL (the S3
        # key, the Azure strings, a managed-tool secret). Pasting it into a
        # URL-bound slot aimed at a host they control must not keep it there:
        # a secret that was never bound to a host is not attached to one on a
        # keep. Otherwise the gateway would decrypt the S3 secret and send it
        # to the attacker as a bearer token.
        read = _result(_ok(auth_client.get(f"{ORG_CONFIGS}active/")))
        s3_mask = _at(read, "/cache/s3/secret_access_key")
        assert s3_mask.startswith(MASK)
        before = _active(organization).version
        response = _save(
            auth_client,
            {
                "a2a": {
                    "agents": {
                        "exfil": {
                            "url": "https://attacker.example/collect",
                            "auth": {"type": "bearer", "token": s3_mask},
                        }
                    }
                }
            },
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "secret_origin_changed" in response.content.decode()
        assert _active(organization).version == before
        # The save was refused, so nothing reached the attacker's host. (The
        # S3 secret still reaches the gateway through its own S3 field from the
        # valid setup save; the invariant is that it never goes to the url the
        # attacker pasted it next to.)
        assert "attacker.example" not in json.dumps(_pushed(gateway))

    def test_an_azure_string_mask_cannot_be_attached_to_an_audit_sink(
        self, organization, auth_client, gateway, saved
    ):
        read = _result(_ok(auth_client.get(f"{ORG_CONFIGS}active/")))
        azure_mask = _at(read, "/cache/azure_blob/connection_string")
        response = _save(
            auth_client,
            {
                "audit": {
                    "sinks": [
                        {
                            "type": "webhook",
                            "url": "https://attacker.example/in",
                            "headers": {"Authorization": azure_mask},
                        }
                    ]
                }
            },
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "secret_origin_changed" in response.content.decode()
        assert "attacker.example" not in json.dumps(_pushed(gateway))

    @pytest.mark.parametrize("held", ["managed", "server"])
    def test_a_stored_mcp_credential_cannot_be_attached_to_another_server(
        self, organization, auth_client, gateway, saved, held
    ):
        # A reader sees an MCP credential's reference (never a mask: the vault
        # holds the secret). Naming that credential for a new server aimed at
        # a host they control must not send its secret there: the vault owns
        # credential_id, and a client cannot set it.
        read = _result(_ok(auth_client.get(f"{ORG_CONFIGS}active/")))
        reference = (
            _at(read, "/mcp/managed_tools/credentials/fagi.search")
            if held == "managed"
            else _at(read, "/mcp/servers/gh/credential")
        )
        before = _active(organization).version
        response = auth_client.post(
            f"{GATEWAY}/update-mcp-server/",
            {
                "server_id": "exfil",
                "config": {
                    "transport": "http",
                    "url": "https://attacker.example/mcp",
                    "credential": dict(reference),
                },
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert _active(organization).version == before
        assert "attacker.example" not in json.dumps(_pushed(gateway))

    def test_a_secret_put_in_an_mcp_credential_is_refused(
        self, organization, auth_client, gateway, saved
    ):
        # credential.secret is push-only: update-mcp-server never stores one,
        # whatever value it carries (a typed secret goes in auth).
        read = _result(_ok(auth_client.get(f"{ORG_CONFIGS}active/")))
        s3_mask = _at(read, "/cache/s3/secret_access_key")
        response = auth_client.post(
            f"{GATEWAY}/update-mcp-server/",
            {
                "server_id": "exfil",
                "config": {
                    "transport": "http",
                    "url": "https://attacker.example/mcp",
                    "credential": {
                        "mode": "shared",
                        "secret": {"type": "bearer", "value": s3_mask},
                    },
                },
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "attacker.example" not in json.dumps(_pushed(gateway))


@pytest.mark.integration
@pytest.mark.api
class TestTheMCPServerDialog:
    def test_a_blank_secret_keeps_the_stored_one(
        self, organization, auth_client, gateway, saved
    ):
        # The console's Edit MCP Server dialog replaces the whole entry; a
        # blank token keeps the stored one while the url keeps its origin.
        _ok(
            auth_client.post(
                f"{GATEWAY}/update-mcp-server/",
                {
                    "server_id": "gh",
                    "config": {
                        "transport": "http",
                        "url": "https://mcp.rf18.example/gh/v2/mcp",
                        "auth": {"type": "bearer", "token": ""},
                    },
                },
                format="json",
            )
        )
        payload = _pushed(gateway)[-1]
        assert (
            _at(payload, "/mcp/servers/gh/credential/secret/value")
            == saved["/mcp/servers/gh/credential/secret/value"]
        )
        assert (
            _at(payload, "/mcp/servers/gh/url") == "https://mcp.rf18.example/gh/v2/mcp"
        )


@pytest.mark.integration
@pytest.mark.api
class TestPostOrgConfigsRefusesPlaintextMCPSecrets:
    """Item 3: the release line refuses a plaintext MCP server secret put
    straight into the config on POST org-configs/, the way the integration
    line's vault does (refuse_mcp_secrets). MCP credentials are set through
    update-mcp-server, which stores them write-only."""

    @pytest.mark.parametrize(
        "mcp",
        [
            {
                "enabled": True,
                "servers": {
                    "gh": {
                        "transport": "http",
                        "url": "https://mcp.rf18.example/gh/mcp",
                        "auth": {"type": "bearer", "token": "rf18-new-clear"},
                    }
                },
            },
            {
                "enabled": True,
                "servers": {
                    "kv": {
                        "transport": "http",
                        "url": "https://mcp.rf18.example/kv/mcp",
                        "auth": {
                            "type": "api_key",
                            "header": "X-Key",
                            "key": "rf18-new-clear",
                        },
                    }
                },
            },
            {
                "enabled": True,
                "servers": {
                    "vault": {
                        "transport": "http",
                        "url": "https://vault.rf18.example/mcp",
                        "credential": {
                            "mode": "shared",
                            "secret": {"type": "bearer", "value": "rf18-new-clear"},
                        },
                    }
                },
            },
            {
                "enabled": True,
                "managed_tools": {
                    "credentials": {
                        "fagi.search": {
                            "mode": "shared",
                            "secret": {
                                "type": "api_key",
                                "header": "X-K",
                                "value": "rf18-new-clear",
                            },
                        }
                    }
                },
            },
        ],
        ids=["bearer", "api_key", "credential", "managed"],
    )
    def test_a_new_clear_mcp_secret_is_refused(self, auth_client, gateway, mcp):
        response = auth_client.post(
            ORG_CONFIGS, {"mcp": mcp, "change_description": "rf18 mcp"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "secret_in_config" in response.content.decode()

    def test_an_mcp_server_without_a_secret_is_accepted(self, auth_client, gateway):
        response = auth_client.post(
            ORG_CONFIGS,
            {
                "mcp": {
                    "enabled": True,
                    "servers": {
                        "open": {
                            "transport": "http",
                            "url": "https://mcp.rf18.example/open/mcp",
                        }
                    },
                },
                "change_description": "rf18 mcp no secret",
            },
            format="json",
        )
        assert response.status_code in (200, 201), response.content


@pytest.mark.integration
@pytest.mark.api
class TestLegacyClearRowsBeforeTheMigration:
    """A row saved before migration 0047, still holding a secret in clear, is
    never shown to a reader, is kept (and encrypted) on a blank or a read-back
    mask, and the gateway still gets the clear value."""

    def _seed(self, organization):
        return AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            cache={
                "enabled": True,
                "backend": "redis",
                "redis": {
                    "address": "redis.rf18.example:6379",
                    "password": "legacy-pw",
                },
            },
            a2a={
                "enabled": True,
                "agents": {
                    "b": {
                        "url": "https://agents.rf18.example/b",
                        "auth": {"type": "bearer", "token": "legacy-a2a"},
                    }
                },
            },
        )

    def test_a_reader_sees_a_mask_not_the_legacy_clear_value(
        self, organization, auth_client, gateway
    ):
        self._seed(organization)
        viewer = _role_client(organization, Level.VIEWER)
        body = _result(_ok(viewer.get(f"{ORG_CONFIGS}active/")))
        assert "legacy-pw" not in json.dumps(body)
        assert _at(body, "/cache/redis/password").startswith(MASK)
        assert "/cache/redis/password" in body["secrets_set"]

    def test_a_blank_save_keeps_and_encrypts_the_legacy_value(
        self, organization, auth_client, gateway
    ):
        self._seed(organization)
        _ok(_save(auth_client, {"cache": {"redis": {"password": "", "pool_size": 11}}}))
        active = _active(organization)
        assert active.cache["redis"]["password"].startswith("enc::")
        assert "legacy-pw" not in json.dumps(active.cache)
        payload = _pushed(gateway)[-1]
        assert _at(payload, "/cache/redis/password") == "legacy-pw"

    def test_a_read_back_mask_keeps_the_legacy_value(
        self, organization, auth_client, gateway
    ):
        self._seed(organization)
        read = _result(_ok(auth_client.get(f"{ORG_CONFIGS}active/")))
        # Round-trip the a2a section (masks and all) through POST org-configs/.
        _ok(auth_client.post(ORG_CONFIGS, {"a2a": read["a2a"]}, format="json"))
        payload = _pushed(gateway)[-1]
        assert _at(payload, "/a2a/agents/b/auth/token") == "legacy-a2a"
        assert (
            _active(organization)
            .a2a["agents"]["b"]["auth"]["token"]
            .startswith("enc::")
        )

    def test_a_legacy_secret_moved_to_another_host_is_not_kept(
        self, organization, auth_client, gateway
    ):
        self._seed(organization)
        response = _save(
            auth_client, {"cache": {"redis": {"address": "attacker.example:6379"}}}
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "secret_origin_changed" in response.content.decode()


def _other_key_token(clear):
    """A well-formed ``enc::`` Fernet token under a key this deployment does
    not hold: stored ciphertext that cannot be read."""
    from cryptography.fernet import Fernet

    blob = Fernet(Fernet.generate_key()).encrypt(json.dumps({"v": clear}).encode())
    return "enc::" + blob.decode()


class TestThePushTellsCiphertextFromAClearValue:
    """Only a value that decrypts to a stored blob is ciphertext. Any other
    ``enc::`` string is a clear value saved before migration 0047 and goes to
    the gateway as it is; a well-formed token that cannot be decrypted is
    dropped and logged, never sent."""

    def test_a_clear_value_that_starts_with_the_prefix_is_sent_as_it_is(self):
        from agentcc.services.org_config_secrets import decrypt_payload_secrets

        payload = {
            "cache": {"redis": {"address": "r:6379", "password": "enc::hunter2"}}
        }
        decrypt_payload_secrets(payload)
        assert payload["cache"]["redis"].get("password") == "enc::hunter2"

    def test_stored_ciphertext_is_decrypted_and_an_unreadable_one_dropped(self):
        from agentcc.services.org_config_secrets import (
            _encrypt,
            decrypt_payload_secrets,
        )

        payload = {
            "cache": {
                "redis": {"address": "r:6379", "password": _encrypt("rf18-pw")},
                "s3": {"secret_access_key": _other_key_token("rf18-lost")},
            }
        }
        decrypt_payload_secrets(payload)
        assert payload["cache"]["redis"]["password"] == "rf18-pw"
        assert "secret_access_key" not in payload["cache"]["s3"]


@pytest.mark.integration
@pytest.mark.api
class TestALegacyClearValueThatLooksEncrypted:
    """A secret saved before migration 0047 may be any string, one beginning
    with ``enc::`` included. It is a clear value: kept and encrypted on a blank
    or a read-back mask, and the gateway still gets it."""

    LEGACY = "enc::hunter2"

    def _seed(self, organization, value=LEGACY):
        return AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            cache={
                "enabled": True,
                "backend": "redis",
                "redis": {"address": "redis.rf18.example:6379", "password": value},
            },
        )

    def test_the_push_before_any_save_carries_it(self, organization, gateway):
        from agentcc.services.config_push import _build_payload

        payload = _build_payload(organization.id, self._seed(organization))
        assert payload["cache"]["redis"].get("password") == self.LEGACY

    def test_a_blank_save_keeps_and_encrypts_it(
        self, organization, auth_client, gateway
    ):
        from agentcc.services.org_config_secrets import _decrypt

        self._seed(organization)
        _ok(_save(auth_client, {"cache": {"redis": {"password": "", "pool_size": 11}}}))
        stored = _active(organization).cache["redis"]["password"]
        assert stored != self.LEGACY
        assert _decrypt(stored) == self.LEGACY
        assert _pushed(gateway)[-1]["cache"]["redis"].get("password") == self.LEGACY

    def test_a_read_back_mask_keeps_and_encrypts_it(
        self, organization, auth_client, gateway
    ):
        from agentcc.services.org_config_secrets import _decrypt

        self._seed(organization)
        read = _result(_ok(auth_client.get(f"{ORG_CONFIGS}active/")))
        mask = _at(read, "/cache/redis/password")
        assert mask.startswith(MASK)
        _ok(_save(auth_client, {"cache": {"redis": {"password": mask}}}))
        assert _decrypt(_active(organization).cache["redis"]["password"]) == self.LEGACY
        assert _pushed(gateway)[-1]["cache"]["redis"].get("password") == self.LEGACY

    def test_an_unreadable_stored_ciphertext_is_kept_as_it_is(
        self, organization, auth_client, gateway
    ):
        # A token this deployment cannot decrypt is still stored ciphertext:
        # a keep leaves it verbatim (never wrapped again into a value the push
        # would then send), and the push drops it.
        token = _other_key_token("rf18-lost")
        self._seed(organization, token)
        _ok(_save(auth_client, {"cache": {"redis": {"password": "", "pool_size": 11}}}))
        assert _active(organization).cache["redis"]["password"] == token
        assert "password" not in _pushed(gateway)[-1]["cache"]["redis"]


@pytest.mark.integration
@pytest.mark.api
class TestTheContractDeclaresEveryReadMember:
    """``secrets_set`` is part of both org-config read shapes, so it is a
    declared, read-only member of the serializers the OpenAPI contract and the
    generated client are built from, not a key added only at runtime."""

    def test_the_org_config_read_declares_secrets_set_read_only(self):
        from rest_framework import serializers

        from agentcc.serializers.org_config import AgentccOrgConfigSerializer

        field = AgentccOrgConfigSerializer().fields.get("secrets_set")
        assert isinstance(field, serializers.ListField), field
        assert field.read_only
        assert isinstance(field.child, serializers.CharField)

    def test_the_gateway_config_result_declares_secrets_set(self):
        from rest_framework import serializers

        from agentcc.serializers.contracts import GatewayConfigResultSerializer

        field = GatewayConfigResultSerializer().fields.get("secrets_set")
        assert isinstance(field, serializers.ListField), field
        assert isinstance(field.child, serializers.CharField)

    def test_every_member_both_reads_return_is_declared(
        self, organization, auth_client, gateway, saved
    ):
        from agentcc.serializers.contracts import GatewayConfigResultSerializer
        from agentcc.serializers.org_config import AgentccOrgConfigSerializer

        declared = set(AgentccOrgConfigSerializer().fields)
        body = _result(_ok(auth_client.get(f"{ORG_CONFIGS}active/")))
        assert set(body) - declared == set()
        assert body["secrets_set"] == sorted(_inline(saved))

        declared = set(GatewayConfigResultSerializer().fields)
        body = _result(_ok(auth_client.get(f"{GATEWAY}/config/")))
        assert set(body) - declared == set()
        assert body["secrets_set"] == sorted(_inline(saved))


@pytest.mark.integration
@pytest.mark.api
class TestOnlyTheReadMaskIsAPlaceholder:
    """A placeholder is the bare stem or the exact mask a read returns (the
    stem and a 16-hex fingerprint). Any other string, one that merely starts
    with the stem included, is a secret value the console can now type."""

    def test_a_value_that_starts_with_the_stem_is_saved_as_a_value(
        self, organization, auth_client, gateway, saved
    ):
        from agentcc.services.org_config_secrets import _decrypt

        value = "__encrypted__rf18-my-own-token"
        _ok(_save(auth_client, {"cache": {"s3": {"secret_access_key": value}}}))
        stored = _active(organization).cache["s3"]["secret_access_key"]
        assert _decrypt(stored) == value
        assert _at(_pushed(gateway)[-1], "/cache/s3/secret_access_key") == value

    @pytest.mark.parametrize(
        "placeholder", [MASK, MASK + "0123456789abcdef"], ids=["stem", "mask"]
    )
    def test_a_placeholder_with_nothing_behind_it_is_still_refused(
        self, organization, auth_client, gateway, saved, placeholder
    ):
        before = _active(organization).version
        response = _save(
            auth_client, {"cache": {"s3": {"secret_access_key": placeholder}}}
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "secret_not_kept" in response.content.decode()
        assert _active(organization).version == before
