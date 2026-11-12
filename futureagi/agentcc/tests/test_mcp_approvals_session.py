"""Console decisions never inherit the legacy system-key chosen-user path."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.urls import resolve
from rest_framework.test import APIRequestFactory, force_authenticate

from agentcc.services.gateway_client import GatewayClient

pytestmark = pytest.mark.django_db


@override_settings(AGENTCC_MCP_APPROVALS_ENABLED=True)
@pytest.mark.parametrize("kind", ["system", "mcp", "user"])
def test_human_decisions_require_console_authentication(user, organization, kind):
    route = "/agentcc/mcp/approvals/sealed/decision/"
    request = APIRequestFactory().post(route, {"decision": "approve"}, format="json")
    request.organization = organization
    request.org_api_key = SimpleNamespace(type=kind, user_id=user.pk)
    force_authenticate(request, user=user)
    match = resolve(route)
    with patch.object(GatewayClient, "_request") as transport:
        response = match.func(request, **match.kwargs)
    assert response.status_code == 403
    transport.assert_not_called()
