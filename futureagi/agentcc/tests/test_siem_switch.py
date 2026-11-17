"""SIEM deployment switch through the real URLconf, middleware and command."""

import json
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.http import Http404
from django.test import Client, RequestFactory
from django.views.defaults import page_not_found
from rest_framework.views import APIView

from agentcc import mcp_features
from agentcc.models.siem import AgentccSIEMDelivery, AgentccSIEMDestination
from agentcc.serializers.contracts import MCPFeaturesSerializer
from agentcc.services import siem_delivery, siem_transport
from agentcc.tests.siem_receiver import Receiver
from agentcc.tests.test_siem_delivery import add_destination, ingest_call
from agentcc.urls_siem import urlpatterns

pytestmark = [pytest.mark.integration, pytest.mark.django_db]
UUID = "8b6cf613-64ac-4b7c-bbdd-bafbf433aaab"
ROUTES = [
    "/agentcc/" + str(route.pattern).replace("<uuid:pk>", UUID) for route in urlpatterns
]


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize(
    "method", ["GET", "POST", "PATCH", "DELETE", "PUT", "HEAD", "OPTIONS", "TRACE"]
)
@pytest.mark.parametrize("authorization", ["", "Bearer invalid"])
def test_off_routes_equal_unknown_before_auth_and_queries(
    path, method, authorization, settings, django_assert_num_queries
):
    settings.DEBUG = False
    settings.AGENTCC_SIEM_EXPORT_ENABLED = False
    client = Client()
    kwargs = {"content_type": "application/json", "HTTP_AUTHORIZATION": authorization}
    unknown = client.generic(
        method, "/agentcc/route-that-does-not-exist/", b"{", **kwargs
    )
    assert unknown.status_code == 404
    with (
        django_assert_num_queries(0),
        patch.object(
            APIView,
            "initialize_request",
            side_effect=AssertionError("DRF reached while OFF"),
        ),
        patch.object(siem_transport, "open_session") as transport,
    ):
        response = client.generic(method, path, b"{", **kwargs)
    assert response.status_code == unknown.status_code
    assert response.content == unknown.content
    # The early refusal retains the canonical builder's normal headers, but
    # intentionally never runs downstream auth/session/CORS middleware. Full
    # unknown-path equivalence with arbitrary credentials is not the contract.
    canonical = page_not_found(RequestFactory().get(path), Http404())
    for name, value in canonical.headers.items():
        assert response[name] == value
    assert response["Cache-Control"] == "no-store"
    assert "Location" not in response
    transport.assert_not_called()


def test_capability_survives_the_status_serializer(settings):
    for enabled in (False, True):
        settings.AGENTCC_SIEM_EXPORT_ENABLED = enabled
        settings.AGENTCC_MCP_TOOL_CALL_EXPLORER_ENABLED = False
        settings.AGENTCC_MCP_TOOL_CALL_DOWNLOAD_ENABLED = False
        features = mcp_features.capabilities()
        assert features.get("siem_export") is enabled
        serializer = MCPFeaturesSerializer(data=features)
        assert serializer.is_valid(), serializer.errors
        assert serializer.data["siem_export"] is enabled


def test_command_keeps_pending_rows_unsent_and_resumes_when_on(
    auth_client, organization, settings, monkeypatch
):
    settings.AGENTCC_SIEM_EXPORT_ENABLED = True
    with Receiver() as receiver:
        add_destination(auth_client, receiver, monkeypatch)
        ingest_call(organization)
        [row] = AgentccSIEMDelivery.objects.all()
        settings.AGENTCC_SIEM_EXPORT_ENABLED = False
        out = StringIO()
        with patch.object(siem_transport, "open_session") as transport:
            call_command("agentcc_siem_deliver", stdout=out)
            assert siem_delivery.enqueue(organization, ["another-event"]) == 0
        transport.assert_not_called()
        assert receiver.requests == []
        assert json.loads(out.getvalue())["skipped"] is True
        row.refresh_from_db()
        assert (row.status, row.attempts) == ("pending", 0)
        assert AgentccSIEMDestination.objects.get().enabled is True
        assert AgentccSIEMDelivery.objects.count() == 1
        settings.AGENTCC_SIEM_EXPORT_ENABLED = True
        call_command("agentcc_siem_deliver", stdout=StringIO())
        assert len(receiver.requests) == 1
        row.refresh_from_db()
        assert row.status == "delivered"
