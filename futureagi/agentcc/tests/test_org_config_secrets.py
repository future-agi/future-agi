"""Org-config secrets are stored encrypted, masked to every reader, and sent
to the gateway in clear.

An org config can hold credentials for systems outside the gateway: the cache
backends' Redis password, S3 secret access key, Azure connection string and
SAS token and the semantic cache's vector-store API keys, the A2A agents'
tokens, the audit and alert webhook headers, and the MCP servers' auth. Here an Owner
saves them the way the console does, and every role reads every path and finds none
of them; the gateway push and the gateway's bulk sync carry each one in clear.

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
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

GATEWAY = "/agentcc/gateways/default"
ORG_CONFIGS = "/agentcc/org-configs/"
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
        "/cache/redis/password": f"cfg-redis-pw-{tag}",
        "/cache/s3/secret_access_key": f"cfg-s3-sak-{tag}",
        "/cache/azure_blob/connection_string": (
            f"DefaultEndpointsProtocol=https;AccountName=cfg;AccountKey=cfg-az-{tag}"
        ),
        "/cache/azure_blob/sas_token": f"sv=2024-01-01&sig=cfg-sas-{tag}",
        "/cache/semantic/qdrant/api_key": f"cfg-qdrant-{tag}",
        "/cache/semantic/weaviate/api_key": f"cfg-weaviate-{tag}",
        "/cache/semantic/pinecone/api_key": f"cfg-pinecone-{tag}",
        "/a2a/agents/billing/auth/token": f"cfg-a2a-bearer-{tag}",
        "/audit/sinks/0/headers/Authorization": f"Bearer cfg-siem-{tag}",
        "/alerting/channels/0/headers/X-Hook-Token": f"cfg-hook-{tag}",
        "/mcp/servers/gh/auth/token": f"cfg-mcp-bearer-{tag}",
        "/mcp/servers/kv/auth/key": f"cfg-mcp-key-{tag}",
        "/mcp/servers/vault/credential/secret/value": f"cfg-mcp-cred-{tag}",
        "/mcp/managed_tools/credentials/fagi.search/secret/value": (
            f"cfg-managed-{tag}"
        ),
    }


def _console_patch(s):
    """What the console's Org Config editor and MCP pages save, through
    update-config, with a secret in every password-style field."""
    return {
        "cache": {
            "enabled": True,
            "backend": "redis",
            "redis": {
                "address": "redis.cfg.example:6379",
                "password": s["/cache/redis/password"],
                "tls": True,
            },
            "s3": {
                "bucket": "cfg-bucket",
                "region": "us-east-1",
                "access_key_id": "AKIACFGEXAMPLE",
                "secret_access_key": s["/cache/s3/secret_access_key"],
            },
            "azure_blob": {
                "container": "cfg",
                "connection_string": s["/cache/azure_blob/connection_string"],
                "sas_token": s["/cache/azure_blob/sas_token"],
            },
            "semantic": {
                "enabled": True,
                "backend": "qdrant",
                "qdrant": {
                    "url": "https://qdrant.cfg.example",
                    "collection": "cfg",
                    "api_key": s["/cache/semantic/qdrant/api_key"],
                },
                "weaviate": {
                    "url": "https://weaviate.cfg.example",
                    "api_key": s["/cache/semantic/weaviate/api_key"],
                },
                "pinecone": {
                    "url": "https://idx.cfg.example",
                    "api_key": s["/cache/semantic/pinecone/api_key"],
                },
            },
        },
        "a2a": {
            "enabled": True,
            "agents": {
                "billing": {
                    "url": "https://agents.cfg.example/billing",
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
                    "url": "https://siem.cfg.example/in",
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
                    "url": "https://hooks.cfg.example/alerts",
                    "headers": {
                        "X-Hook-Token": s["/alerting/channels/0/headers/X-Hook-Token"]
                    },
                }
            ],
        },
        "mcp": {
            "enabled": True,
            "servers": {
                "gh": {
                    "transport": "http",
                    "url": "https://mcp.cfg.example/gh/mcp",
                    "auth": {
                        "type": "bearer",
                        "token": s["/mcp/servers/gh/auth/token"],
                    },
                },
                "kv": {
                    "transport": "http",
                    "url": "https://mcp.cfg.example/kv/mcp",
                    "auth": {
                        "type": "api_key",
                        "header": "X-API-Key",
                        "key": s["/mcp/servers/kv/auth/key"],
                    },
                },
                "vault": {
                    "transport": "http",
                    "url": "https://vault.cfg.example/mcp",
                    "credential": {
                        "mode": "shared",
                        "secret": {
                            "type": "bearer",
                            "value": s["/mcp/servers/vault/credential/secret/value"],
                        },
                    },
                },
            },
            "managed_tools": {
                "credentials": {
                    "fagi.search": {
                        "mode": "shared",
                        "secret": {
                            "type": "api_key",
                            "header": "X-Search-Key",
                            "value": s[
                                "/mcp/managed_tools/credentials/fagi.search/secret/value"
                            ],
                        },
                    }
                }
            },
        },
    }


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
        email=f"cfg-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="cfg reader",
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
    """v1 with no secrets, then v2 with every secret the console saves."""
    tag = uuid.uuid4().hex[:12]
    s = _secrets(tag)
    _ok(auth_client.post(ORG_CONFIGS, {"change_description": "cfg v1"}, format="json"))
    _ok(_save(auth_client, _console_patch(s)))
    return s


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
                {"budgets": {"enabled": False}, "change_description": "cfg v3"},
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
        for path in (f"{GATEWAY}/config/", f"{ORG_CONFIGS}active/"):
            body = _result(_ok(viewer.get(path)))
            assert sorted(body["secrets_set"]) == sorted(saved), path
            for pointer in saved:
                value = _at(body, pointer)
                assert isinstance(value, str) and value.startswith(MASK), (
                    path,
                    pointer,
                    value,
                )
            # Non-secret settings still read as stored.
            assert _at(body, "/cache/redis/address") == "redis.cfg.example:6379"
            assert _at(body, "/cache/s3/access_key_id") == "AKIACFGEXAMPLE"
            assert _at(body, "/a2a/agents/billing/auth/type") == "bearer"
            assert _at(body, "/mcp/servers/kv/auth/header") == "X-API-Key"

    def test_a_rotated_secret_shows_as_changed_in_the_diff_without_its_value(
        self, organization, auth_client, gateway, saved
    ):
        before = _active(organization)
        _ok(_save(auth_client, {"cache": {"redis": {"password": "cfg-rotated-pw"}}}))
        after = _active(organization)
        diff = _result(
            _ok(
                auth_client.get(f"{ORG_CONFIGS}{after.id}/diff/?compare_to={before.id}")
            )
        )
        assert "cfg-rotated-pw" not in json.dumps(diff)
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
        for pointer in saved:
            assert _at(sections, pointer).startswith("enc::"), pointer


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
        _ok(_save(auth_client, {"cache": {"redis": {"password": "cfg-v3-pw"}}}))
        assert _at(_pushed(gateway)[-1], "/cache/redis/password") == "cfg-v3-pw"

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
                                "url": "https://siem.cfg.example/in",
                                "headers": {"Authorization": "Bearer cfg-first"},
                            },
                            {
                                "type": "webhook",
                                "url": "https://siem.cfg.example/in",
                                "headers": {"Authorization": "Bearer cfg-second"},
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
                "url": "https://siem.cfg.example/in",
                "headers": {"Authorization": "Bearer cfg-second"},
            }
        ]

    def test_a_new_value_replaces_and_null_removes(
        self, organization, auth_client, gateway, saved
    ):
        _ok(
            _save(
                auth_client,
                {
                    "cache": {"s3": {"secret_access_key": "cfg-new-sak"}},
                    "a2a": {"agents": {"billing": {"auth": {"token": None}}}},
                },
            )
        )
        payload = _pushed(gateway)[-1]
        assert _at(payload, "/cache/s3/secret_access_key") == "cfg-new-sak"
        assert "token" not in payload["a2a"]["agents"]["billing"]["auth"]
        body = _result(_ok(auth_client.get(f"{ORG_CONFIGS}active/")))
        assert "/a2a/agents/billing/auth/token" not in body["secrets_set"]
        assert "cfg-new-sak" not in json.dumps(body)

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
                            "url": "https://agents.cfg.example/new",
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
                        "agents": {"billing": {"url": "https://agents.cfg.example/v2"}}
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
                                "url": "https://other.cfg.example/billing",
                                "auth": {"type": "bearer", "token": "cfg-reentered"},
                            }
                        }
                    }
                },
            )
        )
        payload = _pushed(gateway)[-1]
        assert _at(payload, "/a2a/agents/billing/auth/token") == "cfg-reentered"

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
        # A mask of a secret stored with no bound URL (the S3 key, the Azure strings, a
        # managed-tool secret) is kept only where it is stored: a URL-bound slot never
        # takes it on a keep.
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
        # The save is refused, so nothing reaches that host. The S3 secret
        # reaches the gateway only through its own S3 field.
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

    def test_a_managed_tool_secret_mask_cannot_be_attached_to_an_mcp_server(
        self, organization, auth_client, gateway, saved
    ):
        read = _result(_ok(auth_client.get(f"{ORG_CONFIGS}active/")))
        managed_mask = _at(
            read, "/mcp/managed_tools/credentials/fagi.search/secret/value"
        )
        response = auth_client.post(
            f"{GATEWAY}/update-mcp-server/",
            {
                "server_id": "exfil",
                "config": {
                    "transport": "http",
                    "url": "https://attacker.example/mcp",
                    "credential": {
                        "mode": "shared",
                        "secret": {"type": "bearer", "value": managed_mask},
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
                        "url": "https://mcp.cfg.example/gh/v2/mcp",
                        "auth": {"type": "bearer", "token": ""},
                    },
                },
                format="json",
            )
        )
        payload = _pushed(gateway)[-1]
        assert (
            _at(payload, "/mcp/servers/gh/auth/token")
            == saved["/mcp/servers/gh/auth/token"]
        )
        assert (
            _at(payload, "/mcp/servers/gh/url") == "https://mcp.cfg.example/gh/v2/mcp"
        )


@pytest.mark.integration
@pytest.mark.api
class TestPostOrgConfigsRefusesPlaintextMCPSecrets:
    """POST org-configs/ refuses a plaintext MCP server secret put straight into the
    config (refuse_mcp_secrets). MCP credentials are set through update-mcp-server,
    which stores them write-only."""

    @pytest.mark.parametrize(
        "mcp",
        [
            {
                "enabled": True,
                "servers": {
                    "gh": {
                        "transport": "http",
                        "url": "https://mcp.cfg.example/gh/mcp",
                        "auth": {"type": "bearer", "token": "cfg-new-clear"},
                    }
                },
            },
            {
                "enabled": True,
                "servers": {
                    "kv": {
                        "transport": "http",
                        "url": "https://mcp.cfg.example/kv/mcp",
                        "auth": {
                            "type": "api_key",
                            "header": "X-Key",
                            "key": "cfg-new-clear",
                        },
                    }
                },
            },
            {
                "enabled": True,
                "servers": {
                    "vault": {
                        "transport": "http",
                        "url": "https://vault.cfg.example/mcp",
                        "credential": {
                            "mode": "shared",
                            "secret": {"type": "bearer", "value": "cfg-new-clear"},
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
                                "value": "cfg-new-clear",
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
            ORG_CONFIGS, {"mcp": mcp, "change_description": "cfg mcp"}, format="json"
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
                            "url": "https://mcp.cfg.example/open/mcp",
                        }
                    },
                },
                "change_description": "cfg mcp no secret",
            },
            format="json",
        )
        assert response.status_code in (200, 201), response.content


@pytest.mark.integration
@pytest.mark.api
class TestRowsMigration0047HasNotRewritten:
    """A row migration 0047 has not rewritten is never shown to a reader, is kept (and
    encrypted) on a blank or a read-back mask, and the gateway gets its value."""

    def _seed(self, organization):
        return AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            cache={
                "enabled": True,
                "backend": "redis",
                "redis": {
                    "address": "redis.cfg.example:6379",
                    "password": "legacy-pw",
                },
            },
            a2a={
                "enabled": True,
                "agents": {
                    "b": {
                        "url": "https://agents.cfg.example/b",
                        "auth": {"type": "bearer", "token": "legacy-a2a"},
                    }
                },
            },
        )

    def test_a_reader_sees_a_mask_not_the_stored_value(
        self, organization, auth_client, gateway
    ):
        self._seed(organization)
        viewer = _role_client(organization, Level.VIEWER)
        body = _result(_ok(viewer.get(f"{ORG_CONFIGS}active/")))
        assert "legacy-pw" not in json.dumps(body)
        assert _at(body, "/cache/redis/password").startswith(MASK)
        assert "/cache/redis/password" in body["secrets_set"]

    def test_a_blank_save_keeps_and_encrypts_the_stored_value(
        self, organization, auth_client, gateway
    ):
        self._seed(organization)
        _ok(_save(auth_client, {"cache": {"redis": {"password": "", "pool_size": 11}}}))
        active = _active(organization)
        assert active.cache["redis"]["password"].startswith("enc::")
        assert "legacy-pw" not in json.dumps(active.cache)
        payload = _pushed(gateway)[-1]
        assert _at(payload, "/cache/redis/password") == "legacy-pw"

    def test_a_read_back_mask_keeps_the_stored_value(
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

    def test_a_stored_secret_moved_to_another_host_is_not_kept(
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
    """Only a value that decrypts to a stored blob is ciphertext. Any other ``enc::``
    string is a value migration 0047 has not encrypted and goes to the gateway as it
    is; a well-formed token that cannot be decrypted is dropped and logged, never sent."""

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
                "redis": {"address": "r:6379", "password": _encrypt("cfg-pw")},
                "s3": {"secret_access_key": _other_key_token("cfg-lost")},
            }
        }
        decrypt_payload_secrets(payload)
        assert payload["cache"]["redis"]["password"] == "cfg-pw"
        assert "secret_access_key" not in payload["cache"]["s3"]


@pytest.mark.integration
@pytest.mark.api
class TestAnUnmigratedValueThatLooksEncrypted:
    """A value migration 0047 has not encrypted may be any string, one beginning with
    ``enc::`` included. It is kept and encrypted on a blank or a read-back mask, and
    the gateway gets it."""

    LEGACY = "enc::hunter2"

    def _seed(self, organization, value=LEGACY):
        return AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            cache={
                "enabled": True,
                "backend": "redis",
                "redis": {"address": "redis.cfg.example:6379", "password": value},
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
        token = _other_key_token("cfg-lost")
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
        assert body["secrets_set"] == sorted(saved)

        declared = set(GatewayConfigResultSerializer().fields)
        body = _result(_ok(auth_client.get(f"{GATEWAY}/config/")))
        assert set(body) - declared == set()
        assert body["secrets_set"] == sorted(saved)


@pytest.mark.integration
@pytest.mark.api
class TestOnlyTheReadMaskIsAPlaceholder:
    """A placeholder is the bare stem or the exact mask a read returns (the
    stem and a 16-hex fingerprint). Any other string, one that merely starts
    with the stem included, is a secret value the console can type."""

    def test_a_value_that_starts_with_the_stem_is_saved_as_a_value(
        self, organization, auth_client, gateway, saved
    ):
        from agentcc.services.org_config_secrets import _decrypt

        value = "__encrypted__cfg-my-own-token"
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
