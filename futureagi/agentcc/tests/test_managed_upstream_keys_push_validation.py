"""Per-org managed upstream keys are checked before they are pushed.

The managed upstream key service returns `{provider_id: {api_key,
key_hash, label}}` (managed-upstream-keys-push-v1). Each entry outside that
contract is dropped and logged by provider id and reason only, so the org's
config validates, the bulk sync keeps the org and no key reaches a log.
"""

import json
import secrets
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient
from structlog.testing import capture_logs

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import config_push

SERVICE = "agentcc.services.managed_upstream_keys"
# A random token: IsAdminToken refuses a short, placeholder or published one
# (agentcc/admin_token.py), as the gateway does at start-up.
ADMIN_TOKEN = secrets.token_hex(32)

# Test values, not real keys.
VALID = {
    "openrouter": {
        "api_key": "valid-upstream-key-test-value",
        "key_hash": "kh_3b9e",
        "label": "acme-prod",
    },
    "groq": {"api_key": "second-valid-key-test-value", "key_hash": "kh_77a1"},
}
INVALID = {
    "empty-key": {"api_key": "", "key_hash": "kh_1", "label": "x"},
    "empty-hash": {"api_key": "leak-empty-hash-test-value", "key_hash": ""},
    "missing-hash": {"api_key": "leak-missing-hash-test-value"},
    "number-key": {"api_key": 12345678, "key_hash": "kh_2"},
    "label-number": {
        "api_key": "leak-label-number-test-value",
        "key_hash": "kh_3",
        "label": 7,
    },
    "extra-field": {
        "api_key": "leak-extra-field-test-value",
        "key_hash": "kh_4",
        "secret": "leak-extra-secret-test-value",
    },
    "camel-case": {"apiKey": "leak-camel-test-value", "keyHash": "kh_5"},
    "not-an-object": "leak-not-an-object-test-value",
    "": {"api_key": "leak-empty-provider-test-value", "key_hash": "kh_6"},
}
LEAKS = [
    "leak-empty-hash-test-value",
    "leak-missing-hash-test-value",
    "12345678",
    "leak-label-number-test-value",
    "leak-extra-field-test-value",
    "leak-extra-secret-test-value",
    "leak-camel-test-value",
    "leak-not-an-object-test-value",
    "leak-empty-provider-test-value",
]


@pytest.fixture
def upstream_keys():
    """Install a stand-in for the upstream key service; set `.result` or `.error`."""
    service = types.ModuleType(SERVICE)
    service.result = {}
    service.error = None

    def assemble_for_push(org_id):
        if service.error is not None:
            raise service.error
        return service.result

    service.assemble_for_push = assemble_for_push
    with patch.dict(sys.modules, {SERVICE: service}):
        yield service


@pytest.fixture
def managed_config(organization):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=1,
        is_active=True,
        managed_models={"enabled": True},
        routing={"strategy": "round_robin"},
        cache={"enabled": True, "default_ttl": 60},
    )


def _logged(logs):
    return [(log["event"], log.get("provider_id"), log.get("reason")) for log in logs]


@pytest.mark.integration
class TestUpstreamKeysValidatedBeforePush:
    def test_invalid_upstream_key_entries_dropped_values_never_logged(
        self, organization, managed_config, upstream_keys, caplog
    ):
        upstream_keys.result = {**VALID, **INVALID}

        with capture_logs() as logs:
            payload = config_push._build_payload(str(organization.id), managed_config)

        assert payload["managed_models"]["upstream_keys"] == VALID
        assert payload["managed_models"]["enabled"] is True
        # The rest of the org's config pushes.
        assert payload["routing"]["strategy"] == "round_robin"
        assert payload["cache"]["enabled"] is True

        dropped = {
            provider: reason
            for event, provider, reason in _logged(logs)
            if event == "managed_upstream_key_dropped"
        }
        assert set(dropped) == set(INVALID)
        assert all(dropped.values())
        for leak in LEAKS:
            assert leak not in repr(logs)
            assert leak not in caplog.text

    def test_bulk_sync_keeps_an_org_with_a_malformed_key(
        self, organization, managed_config, upstream_keys, caplog
    ):
        upstream_keys.result = {**VALID, "empty-hash": INVALID["empty-hash"]}
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {ADMIN_TOKEN}")

        with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN):
            response = client.get("/agentcc/org-configs/bulk/")

        assert response.status_code == status.HTTP_200_OK
        served = response.json()["result"][str(organization.id)]
        assert served["managed_models"]["upstream_keys"] == VALID
        assert "leak-empty-hash-test-value" not in caplog.text

    def test_push_sends_the_valid_keys(
        self, organization, managed_config, upstream_keys
    ):
        upstream_keys.result = {**VALID, "camel-case": INVALID["camel-case"]}
        client = MagicMock()

        with patch.object(config_push, "get_gateway_client", return_value=client):
            assert (
                config_push.push_org_config(str(organization.id), managed_config)
                is True
            )

        sent = client.set_org_config.call_args.args[1]
        assert sent["managed_models"]["upstream_keys"] == VALID

    def test_a_result_that_is_not_an_object_pushes_no_keys(
        self, organization, managed_config, upstream_keys
    ):
        upstream_keys.result = [VALID["openrouter"]]

        with capture_logs() as logs:
            payload = config_push._build_payload(str(organization.id), managed_config)

        assert "upstream_keys" not in payload["managed_models"]
        assert "valid-upstream-key-test-value" not in repr(logs)
        assert any(log["event"] == "managed_upstream_keys_invalid" for log in logs)

    def test_a_failing_service_is_logged_by_type_only(
        self, organization, managed_config, upstream_keys, caplog
    ):
        upstream_keys.error = RuntimeError("key leak-in-message-test-value rejected")

        with capture_logs() as logs:
            payload = config_push._build_payload(str(organization.id), managed_config)

        assert "upstream_keys" not in payload["managed_models"]
        assert payload["routing"]["strategy"] == "round_robin"
        failed = [
            log
            for log in logs
            if log["event"] == "managed_upstream_keys_assemble_failed"
        ]
        assert failed and failed[0]["error_type"] == "RuntimeError"
        assert "leak-in-message-test-value" not in repr(logs)
        assert "leak-in-message-test-value" not in caplog.text
        assert "leak-in-message-test-value" not in json.dumps(payload)
