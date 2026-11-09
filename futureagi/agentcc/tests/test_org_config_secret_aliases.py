"""Every spelling the gateway contract accepts for an org-config secret is a
secret too (NG-01; the D-A line's test_org_config_secret_aliases.py, ported
to the release line).

The contract (``agentcc.contracts.gateway_admin``) accepts each cache secret
under a second name as well: ``secretAccessKey`` for S3, ``connectionString``
and ``sasToken`` for Azure (whose section may itself be ``azureBlob``), and
``apiKey`` for the Qdrant, Weaviate and Pinecone semantic caches; and the MCP
section's ``managed_tools`` as ``managedTools``. The config writer stores a
section as it was sent, so a secret saved under any of those spellings, or
under both spellings of a section, is stored encrypted, read as a mask and
sent to the gateway in clear like the snake_case one; migration 0050 encrypts
the ones stored before. A managed tool's secret, under either spelling, is
the MCP credential vault's (MCP-APPS-PLAN.md §2.1 ruling 3): a config write
refuses it, and migration 0056 moves a release row's into the vault.

The semantic cache's settings can no longer be saved (400
semantic_cache_not_supported, docs-agentcc-711 item 4): its camelCase keys
stand here for a value an earlier release stored, written into v1 encrypted
as the writer stores them.

Test values only, not real credentials.
"""

import importlib
import json
import uuid
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps
from rest_framework import status

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import org_config_secrets
from agentcc.tests import test_org_config_secrets as base
from agentcc.tests.test_org_config_secrets import (
    GATEWAY,
    MASK,
    ORG_CONFIGS,
    _active,
    _ok,
    _pushed,
    _result,
    _role_client,
)
from tfc.constants.levels import Level

# The secrets suite's fixtures: every push goes to a recording mock, and the
# role clients it makes are stopped after each test.
gateway = base.gateway
_stop_clients = base._stop_clients


def _alias_cache(tag):
    return {
        "enabled": True,
        "backend": "s3",
        "s3": {"bucket": "rf18-b", "secretAccessKey": f"rf18-camel-sak-{tag}"},
        "azureBlob": {
            "container": "rf18",
            "connectionString": f"rf18-camel-cs-{tag}",
            "sasToken": f"rf18-camel-sas-{tag}",
        },
        "semantic": {
            "enabled": True,
            "backend": "qdrant",
            "qdrant": {
                "url": "https://q.rf18.example",
                "apiKey": f"rf18-camel-qd-{tag}",
            },
            "weaviate": {
                "url": "https://w.rf18.example",
                "apiKey": f"rf18-camel-wv-{tag}",
            },
            "pinecone": {
                "url": "https://p.rf18.example",
                "apiKey": f"rf18-camel-pc-{tag}",
            },
        },
    }


# Where each camelCase secret lands in the payload the gateway gets (the
# contract dumps the snake_case names).
PUSHED = {
    "sak": ("s3", "secret_access_key"),
    "cs": ("azure_blob", "connection_string"),
    "sas": ("azure_blob", "sas_token"),
    "qd": ("semantic", "qdrant", "api_key"),
    "wv": ("semantic", "weaviate", "api_key"),
    "pc": ("semantic", "pinecone", "api_key"),
}

ALIAS_POINTERS = [
    "/cache/azureBlob/connectionString",
    "/cache/azureBlob/sasToken",
    "/cache/s3/secretAccessKey",
    "/cache/semantic/pinecone/apiKey",
    "/cache/semantic/qdrant/apiKey",
    "/cache/semantic/weaviate/apiKey",
]


def _values(tag):
    return {name: f"rf18-camel-{name}-{tag}" for name in PUSHED}


def _without_semantic(cache):
    return {key: value for key, value in cache.items() if key != "semantic"}


def _seed_stored_semantic(organization, tag):
    """v1 holding the semantic cache an earlier release stored, encrypted as
    the writer stores it."""
    config = AgentccOrgConfig(
        organization=organization,
        version=1,
        is_active=True,
        cache={"semantic": _alias_cache(tag)["semantic"]},
    )
    org_config_secrets.encrypt_config_secrets(config, None)
    config.save()


def _found(text, values):
    return sorted(name for name, value in values.items() if value in text)


def _pushed_cache(gateway):
    return _pushed(gateway)[-1]["cache"]


def _at(node, path):
    for part in path:
        node = node[part]
    return node


def _managed_tools(value):
    return {
        "credentials": {
            "fagi.search": {
                "mode": "shared",
                "secret": {"type": "api_key", "header": "X-Key", "value": value},
            }
        }
    }


@pytest.mark.integration
@pytest.mark.api
class TestCamelCaseSecretsAreSecrets:
    @pytest.mark.parametrize("path", ["update-config", "org-configs"])
    def test_no_reader_sees_one_and_the_gateway_gets_each(
        self, organization, auth_client, gateway, path
    ):
        tag = uuid.uuid4().hex[:12]
        values = _values(tag)
        _seed_stored_semantic(organization, tag)
        cache = _without_semantic(_alias_cache(tag))
        if path == "update-config":
            url = f"{GATEWAY}/update-config/"
        else:
            url = ORG_CONFIGS
            # POST org-configs replaces the section: the stored semantic
            # cache goes back as it was read, masks included.
            read = _result(_ok(auth_client.get(f"{ORG_CONFIGS}active/")))
            cache["semantic"] = read["cache"]["semantic"]
        response = auth_client.post(url, {"cache": cache}, format="json")
        assert response.status_code == status.HTTP_200_OK, response.content[:800]
        assert _found(response.content.decode(), values) == []

        viewer = _role_client(organization, Level.VIEWER)
        for read in (f"{GATEWAY}/config/", f"{ORG_CONFIGS}active/", ORG_CONFIGS):
            got = viewer.get(read)
            assert got.status_code == status.HTTP_200_OK
            assert _found(got.content.decode(), values) == [], read

        body = _result(_ok(viewer.get(f"{ORG_CONFIGS}active/")))
        assert sorted(body["secrets_set"]) == ALIAS_POINTERS
        assert body["cache"]["s3"]["secretAccessKey"].startswith(MASK)
        assert body["cache"]["azureBlob"]["sasToken"].startswith(MASK)

        stored = json.dumps(_active(organization).cache)
        assert _found(stored, values) == []

        cache = _pushed_cache(gateway)
        for name, where in PUSHED.items():
            assert _at(cache, where) == values[name], name

    def test_a_blank_keeps_a_camel_case_secret(
        self, organization, auth_client, gateway
    ):
        tag = uuid.uuid4().hex[:12]
        values = _values(tag)
        _ok(
            auth_client.post(
                f"{GATEWAY}/update-config/",
                {"cache": _without_semantic(_alias_cache(tag))},
                format="json",
            )
        )
        response = auth_client.post(
            f"{GATEWAY}/update-config/",
            {"cache": {"s3": {"secretAccessKey": "", "region": "eu-west-1"}}},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.content[:800]
        cache = _pushed_cache(gateway)
        assert cache["s3"]["secret_access_key"] == values["sak"]
        assert cache["s3"]["region"] == "eu-west-1"

    def test_a_vector_store_moved_with_a_camel_case_key_keeps_nothing(
        self, organization, auth_client, gateway
    ):
        tag = uuid.uuid4().hex[:12]
        _seed_stored_semantic(organization, tag)
        _ok(
            auth_client.post(
                f"{GATEWAY}/update-config/",
                {"cache": _without_semantic(_alias_cache(tag))},
                format="json",
            )
        )
        before = _active(organization).version
        response = auth_client.post(
            f"{GATEWAY}/update-config/",
            {"cache": {"semantic": {"qdrant": {"url": "https://third.rf18.example"}}}},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "secret_origin_changed" in response.content.decode()
        assert _active(organization).version == before

    def test_both_spellings_of_a_section_are_secret(
        self, organization, auth_client, gateway
    ):
        # The contract reads azure_blob when both are sent and drops
        # azureBlob; the writer still stores both as sent, so neither is
        # stored or shown in clear.
        tag = uuid.uuid4().hex[:12]
        cache = {
            "azure_blob": {"container": "c", "connection_string": f"rf18-snake-{tag}"},
            "azureBlob": {"container": "c", "connectionString": f"rf18-camel-{tag}"},
        }
        _ok(
            auth_client.post(
                f"{GATEWAY}/update-config/", {"cache": cache}, format="json"
            )
        )
        values = {"snake": f"rf18-snake-{tag}", "camel": f"rf18-camel-{tag}"}
        assert _found(json.dumps(_active(organization).cache), values) == []
        viewer = _role_client(organization, Level.VIEWER)
        body = _ok(viewer.get(f"{ORG_CONFIGS}active/"))
        assert _found(body.content.decode(), values) == []
        assert sorted(_result(body)["secrets_set"]) == [
            "/cache/azureBlob/connectionString",
            "/cache/azure_blob/connection_string",
        ]
        assert (
            _pushed_cache(gateway)["azure_blob"]["connection_string"] == values["snake"]
        )

    def test_a_managed_tool_secret_under_either_spelling_is_secret(
        self, organization, auth_client, gateway
    ):
        # update-config stores no MCP secret under either spelling: the vault
        # holds them (secret_in_config), and nothing is written.
        tag = uuid.uuid4().hex[:12]
        values = {"snake": f"rf18-mt-snake-{tag}", "camel": f"rf18-mt-camel-{tag}"}
        mcp = {
            "enabled": True,
            "managed_tools": _managed_tools(values["snake"]),
            "managedTools": _managed_tools(values["camel"]),
        }
        response = auth_client.post(
            f"{GATEWAY}/update-config/", {"mcp": mcp}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "secret_in_config" in response.content.decode()
        assert _found(response.content.decode(), values) == []
        stored = AgentccOrgConfig.no_workspace_objects.filter(
            organization=organization
        ).values_list("mcp", flat=True)
        assert _found(json.dumps(list(stored)), values) == []

    def test_a_release_rows_managed_tool_secret_under_either_spelling_moves(
        self, organization, auth_client, gateway
    ):
        # A release row holds both spellings encrypted inline (0047, 0050).
        # 0056 moves them: the contract reads managed_tools first, so its
        # value is the one stored, both entries reference it, and a reader
        # sees neither value nor a mask.
        from agentcc.models.mcp_credential import AgentccMCPCredential
        from agentcc.services.credential_manager import decrypt_token

        tag = uuid.uuid4().hex[:12]
        values = {"snake": f"rf18-mt-snake-{tag}", "camel": f"rf18-mt-camel-{tag}"}
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            mcp={
                "enabled": True,
                "managed_tools": _managed_tools(
                    org_config_secrets._encrypt(values["snake"])
                ),
                "managedTools": _managed_tools(
                    org_config_secrets._encrypt(values["camel"])
                ),
            },
        )
        importlib.import_module(
            "agentcc.migrations.0056_mcp_secrets_to_vault"
        ).forwards(django_apps, None)

        row = AgentccMCPCredential.objects.get(
            organization=organization, server_id="fagi.search"
        )
        assert (decrypt_token(row.encrypted_secret), row.status) == (
            values["snake"],
            "active",
        )
        mcp = _active(organization).mcp
        for spelling in ("managed_tools", "managedTools"):
            assert mcp[spelling]["credentials"]["fagi.search"] == {
                "mode": "shared",
                "credential_id": str(row.pk),
                "state": "active",
            }, spelling
        viewer = _role_client(organization, Level.VIEWER)
        body = _ok(viewer.get(f"{ORG_CONFIGS}active/"))
        assert _found(body.content.decode(), values) == []
        assert MASK not in json.dumps(_result(body)["mcp"])
        assert _result(body)["secrets_set"] == []

    def test_post_org_configs_refuses_a_new_clear_camel_case_managed_tool_secret(
        self, organization, auth_client, gateway
    ):
        # POST org-configs/ stores no new MCP secret (secret_in_config), under
        # either spelling of the managed-tools section.
        tag = uuid.uuid4().hex[:12]
        mcp = {
            "enabled": True,
            "managed_tools": {"enabled": False},
            "managedTools": _managed_tools(f"rf18-mt-camel-{tag}"),
        }
        response = auth_client.post(ORG_CONFIGS, {"mcp": mcp}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "secret_in_config" in response.content.decode()
        assert f"rf18-mt-camel-{tag}" not in json.dumps(
            list(
                AgentccOrgConfig.no_workspace_objects.filter(
                    organization=organization
                ).values_list("mcp", flat=True)
            )
        )


def _every_spelling_config():
    tag = "m"
    cache = _alias_cache(tag)
    cache["azure_blob"] = {"connection_string": "rf18-snake-cs", "sas_token": "s"}
    cache["s3"]["secret_access_key"] = "rf18-snake-sak"
    cache["semantic"]["qdrant"]["api_key"] = "rf18-snake-qd"
    return SimpleNamespace(
        organization_id="org-1",
        cache=cache,
        a2a={"agents": {"x": {"url": "https://a.example", "auth": {"token": "t"}}}},
        audit={"sinks": [{"url": "https://s.example", "headers": {"A": "h"}}]},
        alerting={"channels": [{"url": "https://c.example", "headers": {"B": "h"}}]},
        mcp={
            "servers": {"s": {"url": "https://m.example", "auth": {"token": "m"}}},
            "managed_tools": _managed_tools("mt-snake"),
            "managedTools": _managed_tools("mt-camel"),
        },
    )


def test_migration_0050_walks_every_leaf_the_service_reads():
    # 0050 walks every leaf under every spelling (a leaf 0047 encrypted is
    # skipped as ciphertext), and reverses only the leaves 0047 misses.
    m47 = importlib.import_module("agentcc.migrations.0047_encrypt_org_config_secrets")
    m48 = importlib.import_module(
        "agentcc.migrations.0050_encrypt_org_config_secret_aliases"
    )
    config = _every_spelling_config()
    sections = {
        name: getattr(config, name) for name in org_config_secrets.SECRET_SECTIONS
    }
    service = sorted(
        (id(slot._container), slot._key)
        for slot in org_config_secrets.iter_secret_slots(sections)
    )
    walked = sorted((id(c), k) for c, k in m48._walk_leaves(config))
    assert walked == service
    reached = {(id(c), k) for c, k in m47._walk_leaves(config)}
    assert reached < set(walked)  # 0047 missed some, and reached nothing else
    added = sorted((id(c), k) for c, k in m48._alias_leaves(config))
    assert added == sorted(set(walked) - reached)
    # secretAccessKey, azureBlob's two keys, three apiKey, managedTools' secret
    assert len(added) == 7


def test_migration_0050_walks_the_camel_case_leaves():
    m48 = importlib.import_module(
        "agentcc.migrations.0050_encrypt_org_config_secret_aliases"
    )
    config = SimpleNamespace(
        organization_id="org-1",
        cache=_alias_cache("m"),
        a2a={},
        audit={},
        alerting={},
        mcp={"managedTools": _managed_tools("mt"), "managed_tools": {}},
    )
    found = {container[key] for container, key in m48._walk_leaves(config)}
    assert found == set(_values("m").values()) | {"mt"}


class _Apps:
    def get_model(self, app, model):
        return AgentccOrgConfig


@pytest.mark.integration
def test_migration_0050_encrypts_a_stored_camel_case_secret_and_is_reversible(
    organization, auth_client, gateway
):
    m48 = importlib.import_module(
        "agentcc.migrations.0050_encrypt_org_config_secret_aliases"
    )
    tag = uuid.uuid4().hex[:12]
    values = _values(tag)
    row = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, cache=_alias_cache(tag)
    )
    m48.forwards(_Apps(), None)
    row.refresh_from_db()
    assert _found(json.dumps(row.cache), values) == []
    assert row.cache["s3"]["secretAccessKey"].startswith("enc::")
    first = json.dumps(row.cache, sort_keys=True)
    m48.forwards(_Apps(), None)  # idempotent
    row.refresh_from_db()
    assert json.dumps(row.cache, sort_keys=True) == first
    _ok(auth_client.post(f"{GATEWAY}/reload/", {}, format="json"))
    cache = _pushed_cache(gateway)
    for name, where in PUSHED.items():
        assert _at(cache, where) == values[name], name
    m48.backwards(_Apps(), None)
    row.refresh_from_db()
    assert row.cache["s3"]["secretAccessKey"] == values["sak"]
