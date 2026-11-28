"""Opt-in native regression for the TypeSafe harness, not a product fix.

Requires an explicitly owned local gateway and the normal isolated pytest DB.
Exercises REAL provision_key/GatewayClient/Go admin creation, dropping the
successful HTTP result at _request before provision_key can write its Django row.
The Node bridge runs the exact cleanup helper/transport used by the live spec.
"""

import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from accounts.models.organization import Organization
from agentcc.models import AgentccAPIKey
from agentcc.services import auth_bridge
from agentcc.services.gateway_client import GatewayClient, GatewayClientError


@pytest.mark.integration
@pytest.mark.django_db
@pytest.mark.skipif(
    not os.environ.get("TYPESAFE_TEST_GATEWAY_URL"),
    reason="requires an explicitly owned local Go gateway (no live upstream calls)",
)
def test_gateway_commit_response_loss_leaves_no_django_row_and_cleanup_revokes_orphan(
    organization, user, monkeypatch
):
    url = os.environ["TYPESAFE_TEST_GATEWAY_URL"]
    assert urlparse(url).hostname in {"localhost", "127.0.0.1", "agentcc-gateway"}
    token = os.environ["TYPESAFE_TEST_GATEWAY_ADMIN_TOKEN"]
    node = os.environ["TYPESAFE_TEST_NODE"]
    client = GatewayClient(url, token)
    monkeypatch.setattr(auth_bridge, "get_gateway_client", lambda: client)
    name = f"e2e-typesafe-native-{uuid4()}"
    created = []

    def gateway_create(org_id, key_name=name):
        result = client.create_key(key_name, metadata={"org_id": str(org_id)})
        created.append(result["id"])
        return result["id"]

    def inventory():
        # Whitelist cleanup fields; never serialize prefixes, hashes or raw keys.
        return [
            {
                field: key[field]
                for field in ("id", "name", "status", "metadata")
                if field in key
            }
            for key in client.list_keys()["data"]
        ]

    try:
        # Baselines include BOTH a normal Django-backed key and a gateway-only
        # key with the exact future intended name; neither may be revoked.
        baseline, _ = auth_bridge.provision_key(
            name, user=user, organization=organization
        )
        created.append(baseline.gateway_key_id)
        gateway_baseline = gateway_create(organization.id)
        baseline_gateway = inventory()
        other_org = Organization.objects.create(name="TypeSafe native control org")
        cross_org = gateway_create(other_org.id)
        lookalike = gateway_create(organization.id, name + "-unrelated")
        before_rows = set(
            AgentccAPIKey.no_workspace_objects.filter(
                organization=organization
            ).values_list("id", flat=True)
        )
        original_request = client._request

        def lose_successful_create(method, route, **kwargs):
            result = original_request(method, route, **kwargs)
            if method == "POST" and route == "/-/keys":
                created.append(result["id"])
                # Real Go key is already committed; no result gets to Django.
                raise GatewayClientError("injected successful create response loss")
            return result

        with monkeypatch.context() as fault:
            fault.setattr(client, "_request", lose_successful_create)
            with pytest.raises(
                GatewayClientError, match="injected successful create response loss"
            ):
                auth_bridge.provision_key(name, user=user, organization=organization)
        orphan_id = created[-1]
        after_rows = set(
            AgentccAPIKey.no_workspace_objects.filter(
                organization=organization
            ).values_list("id", flat=True)
        )
        assert after_rows == before_rows
        assert not AgentccAPIKey.no_workspace_objects.filter(
            gateway_key_id=orphan_id
        ).exists()
        assert client.get_key(orphan_id)["status"] == "active"
        assert client.get_key(orphan_id)["metadata"]["org_id"] == str(organization.id)

        backend_keys = list(
            AgentccAPIKey.no_workspace_objects.filter(organization=organization).values(
                "id", "name", "status", "gateway_key_id"
            )
        )
        fixture = {
            "options": {
                "apiUrl": "http://localhost:1",
                "gatewayUrl": url,
                "accessToken": "unused",
                "organizationId": str(organization.id),
                "workspaceId": "unused",
                "adminToken": token,
            },
            "name": name,
            "orphanId": orphan_id,
            "backendKeys": backend_keys,
            "baselineGatewayKeys": baseline_gateway,
            "controlIds": [
                baseline.gateway_key_id,
                gateway_baseline,
                cross_org,
                lookalike,
            ],
        }
        script = (
            Path(__file__).resolve().parents[3]
            / "e2e/scripts/typesafe-live-provisioning.mjs"
        )
        result = subprocess.run(
            [node, str(script)],
            input=json.dumps(fixture, default=str),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        # The bridge never outputs inventories; keep even unexpected stack traces
        # private and expose only the assertion's data-free reason to pytest.
        if result.returncode:
            reason = (
                "gateway-only orphan remains active after shared cleanup"
                if "gateway-only orphan remains active" in result.stderr
                else "native cleanup bridge failed; subprocess details withheld"
            )
            pytest.fail(reason, pytrace=False)
        assert client.get_key(orphan_id)["status"] == "revoked"
        assert all(
            client.get_key(key_id)["status"] == "active"
            for key_id in fixture["controlIds"]
        )
        assert (
            set(
                AgentccAPIKey.no_workspace_objects.filter(
                    organization=organization
                ).values_list("id", flat=True)
            )
            == before_rows
        )
    finally:
        # Own test-created IDs only; native regression cleanup is independent of
        # the harness under test, so the RED proof cannot leak its orphan.
        for key_id in created:
            client.revoke_key(key_id)
