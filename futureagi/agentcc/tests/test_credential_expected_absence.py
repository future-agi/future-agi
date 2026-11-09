"""Missing scoped credentials are 404s; database and serializer faults are not."""

from unittest.mock import patch
from uuid import uuid4

import pytest
import structlog
from django.db import OperationalError
from rest_framework.exceptions import PermissionDenied, ValidationError
from structlog.testing import capture_logs

from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.tests import test_provider_credential_api as credential_fixtures
from agentcc.views import provider_credential

pytestmark = [pytest.mark.integration, pytest.mark.api]
secondary_org_client = credential_fixtures.secondary_org_client
secondary_org_context = credential_fixtures.secondary_org_context


def request_credential(client, action, credential_id):
    path = f"/agentcc/provider-credentials/{credential_id}/"
    if action == "rotate":
        return client.post(
            path + "rotate/", {"credentials": {"api_key": "synthetic"}}, format="json"
        )
    return getattr(client, action)(path, {}, format="json")


@pytest.mark.parametrize("action", ["get", "patch", "put", "delete", "rotate"])
@pytest.mark.parametrize("absence", ["missing", "deleted", "cross_tenant"])
def test_expected_absence_is_404_without_error(
    action, absence, user, secondary_org_context, secondary_org_client, monkeypatch
):
    org, _ = secondary_org_context
    credential_id = uuid4()
    if absence != "missing":
        credential = AgentccProviderCredential.no_workspace_objects.create(
            organization=user.organization if absence == "cross_tenant" else org,
            provider_name="synthetic",
            encrypted_credentials=b"not-decrypted-by-this-flow",
            deleted=absence == "deleted",
        )
        credential_id = credential.id
    monkeypatch.setattr(
        provider_credential,
        "logger",
        structlog.get_logger(provider_credential.__name__),
    )
    with capture_logs() as records:
        response = request_credential(secondary_org_client, action, credential_id)
    assert response.status_code == 404, response.content
    assert not [r for r in records if r["log_level"] in ("error", "critical")]
    assert response.json()["message"] == "Provider credential not found"


@pytest.mark.parametrize("action", ["get", "patch", "put", "delete", "rotate"])
@pytest.mark.parametrize(
    "error,status",
    [
        (OperationalError("synthetic database fault"), 503),
        (RuntimeError("synthetic bug"), 500),
    ],
)
def test_unexpected_lookup_fault_is_server_error_and_logged(
    action, error, status, secondary_org_client, caplog
):
    secondary_org_client.raise_request_exception = False
    with patch.object(
        provider_credential.AgentccProviderCredentialViewSet,
        "get_object",
        side_effect=error,
    ):
        response = request_credential(secondary_org_client, action, uuid4())
    assert response.status_code == status
    assert any(record.levelname == "ERROR" for record in caplog.records)


@pytest.mark.parametrize(
    "error,status",
    [(PermissionDenied("denied"), 403), (ValidationError("invalid"), 400)],
)
def test_drf_client_errors_keep_their_status(error, status, secondary_org_client):
    with patch.object(
        provider_credential.AgentccProviderCredentialViewSet,
        "get_object",
        side_effect=error,
    ):
        response = request_credential(secondary_org_client, "get", uuid4())
    assert response.status_code == status
