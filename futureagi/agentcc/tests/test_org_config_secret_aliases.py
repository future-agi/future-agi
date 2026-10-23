"""The gateway contract's camelCase spellings of the org-config secret fields
are secrets too.

The contract (``agentcc.contracts.gateway_admin``) accepts each cache secret
under a second name as well: ``secretAccessKey`` for S3, ``connectionString``
and ``sasToken`` for Azure (whose section may itself be ``azureBlob``), and
``apiKey`` for the Qdrant, Weaviate and Pinecone semantic caches. A client
that saves one of those spellings through update-config or POST org-configs/
gets it pushed to the gateway like the snake_case one; it is stored
encrypted and read as a mask like the snake_case one, and migration 0029
walks them as well.

Test values only, not real credentials.
"""

import importlib
import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models.org_config import AgentccOrgConfig
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

GATEWAY = "/agentcc/gateways/default"
ORG_CONFIGS = "/agentcc/org-configs/"
MASK = "__encrypted__"

migration = importlib.import_module(
    "agentcc.migrations.0029_encrypt_org_config_secrets"
)


def _alias_cache(tag):
    return {
        "enabled": True,
        "backend": "s3",
        "s3": {"bucket": "t-b", "secretAccessKey": f"t-camel-sak-{tag}"},
        "azureBlob": {
            "container": "test",
            "connectionString": f"t-camel-cs-{tag}",
            "sasToken": f"t-camel-sas-{tag}",
        },
        "semantic": {
            "enabled": True,
            "backend": "qdrant",
            "qdrant": {
                "url": "https://q.t.example",
                "apiKey": f"t-camel-qd-{tag}",
            },
            "weaviate": {
                "url": "https://w.t.example",
                "apiKey": f"t-camel-wv-{tag}",
            },
            "pinecone": {
                "url": "https://p.t.example",
                "apiKey": f"t-camel-pc-{tag}",
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


def _values(tag):
    return {name: f"t-camel-{name}-{tag}" for name in PUSHED}


def _found(text, values):
    return sorted(name for name, value in values.items() if value in text)


@pytest.fixture
def gateway():
    client = MagicMock()
    client.health_check.return_value = {"status": "ok"}
    with (
        patch("agentcc.services.config_push.get_gateway_client", return_value=client),
        patch("agentcc.views.gateway.get_gateway_client", return_value=client),
    ):
        yield client


_CLIENTS = []


@pytest.fixture(autouse=True)
def _stop_clients():
    yield
    while _CLIENTS:
        _CLIENTS.pop().stop_workspace_injection()


def _viewer(organization):
    from accounts.models import User

    user = User.objects.create_user(
        email=f"t-camel-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Camel viewer",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER_VIEW_ONLY,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
        role=OrganizationRoles.MEMBER_VIEW_ONLY,
        level=Level.VIEWER,
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


def _active(organization):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )


def _pushed_cache(gateway):
    return gateway.set_org_config.call_args_list[-1].args[1]["cache"]


def _at(node, path):
    for part in path:
        node = node[part]
    return node


@pytest.mark.integration
@pytest.mark.api
class TestCamelCaseSecretsAreSecrets:
    @pytest.mark.parametrize("path", ["update-config", "org-configs"])
    def test_no_reader_sees_one_and_the_gateway_gets_each(
        self, organization, auth_client, gateway, path
    ):
        tag = uuid.uuid4().hex[:12]
        values = _values(tag)
        if path == "update-config":
            url, body = f"{GATEWAY}/update-config/", {"cache": _alias_cache(tag)}
        else:
            url, body = ORG_CONFIGS, {"cache": _alias_cache(tag)}
        response = auth_client.post(url, body, format="json")
        assert response.status_code == status.HTTP_200_OK, response.content[:800]
        assert _found(response.content.decode(), values) == []

        viewer = _viewer(organization)
        for read in (f"{GATEWAY}/config/", f"{ORG_CONFIGS}active/", ORG_CONFIGS):
            got = viewer.get(read)
            assert got.status_code == status.HTTP_200_OK
            assert _found(got.content.decode(), values) == [], read

        body = viewer.get(f"{ORG_CONFIGS}active/").json()["result"]
        assert sorted(body["secrets_set"]) == sorted(
            [
                "/cache/azureBlob/connectionString",
                "/cache/azureBlob/sasToken",
                "/cache/s3/secretAccessKey",
                "/cache/semantic/pinecone/apiKey",
                "/cache/semantic/qdrant/apiKey",
                "/cache/semantic/weaviate/apiKey",
            ]
        )
        assert body["cache"]["s3"]["secretAccessKey"].startswith(MASK)

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
        auth_client.post(
            f"{GATEWAY}/update-config/", {"cache": _alias_cache(tag)}, format="json"
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
        auth_client.post(
            f"{GATEWAY}/update-config/", {"cache": _alias_cache(tag)}, format="json"
        )
        response = auth_client.post(
            f"{GATEWAY}/update-config/",
            {"cache": {"semantic": {"qdrant": {"url": "https://attacker.example"}}}},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "secret_origin_changed" in response.content.decode()


def test_the_migration_walks_the_camel_case_leaves():
    config = SimpleNamespace(
        organization_id="org-1",
        cache=_alias_cache("m"),
        a2a={},
        audit={},
        alerting={},
        mcp={},
    )
    found = {container[key] for container, key in migration._walk_leaves(config)}
    assert found == set(_values("m").values())
