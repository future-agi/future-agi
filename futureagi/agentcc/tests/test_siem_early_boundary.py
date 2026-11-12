"""Executable no-DB boundary checks; full installed-stack proof is separate.

Run as a standalone unittest file for LOCAL_FOCUSED evidence. This imports the
actual MCP middleware (no AST extraction or replacement middleware). The reduced
Django stack includes real CommonMiddleware and DRF; it is not certification of
the application's installed auth/EE-key stack or recognized-key fixtures.
"""

import os
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from django.conf import settings

if __name__ == "__main__":
    os.environ["FUTURE_AGI_LICENSE_URL"] = "http://127.0.0.1:9"
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    settings.configure(
        DEBUG=False,
        SECRET_KEY="isolated-siem-boundary-test",
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=["testserver"],
        APPEND_SLASH=True,
        TEMPLATES=[],
        REST_FRAMEWORK={"UNAUTHENTICATED_USER": None},
        MIDDLEWARE=[
            "agentcc.mcp_features.MCPSubfeatureMiddleware",
            "django.middleware.common.CommonMiddleware",
        ],
    )
    import django

    django.setup()

from django.http import Http404, HttpResponse
from django.test import Client, RequestFactory, SimpleTestCase, override_settings
from django.urls import path
from django.views.defaults import page_not_found
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from agentcc.mcp_features import MCPSubfeatureMiddleware

UUID = "8b6cf613-64ac-4b7c-bbdd-bafbf433aaab"
PATTERNS = [
    "siem/destinations/test-connection/",
    "siem/destinations/",
    "siem/destinations/<uuid:pk>/",
    "siem/destinations/<uuid:pk>/rotate-secret/",
    "siem/deliveries/",
    "siem/deliveries/<uuid:pk>/retry/",
    "siem/deliveries/<uuid:pk>/discard/",
]
ROUTES = ["/agentcc/" + p.replace("<uuid:pk>", UUID) for p in PATTERNS]
LOOKALIKES = [
    "/agentcc/siem/",
    "/agentcc/siem/destinations-extra/",
    "/agentcc/siem/destinations/not-a-uuid/",
    f"/agentcc/siem/destinations/{UUID.upper()}/",
    f"/agentcc/siem/deliveries/{UUID}/",
    "/agentcc/siem/destinations//",
    "/agentcc/siem/destinations/test-connection/extra/",
    f"/agentcc/siem/deliveries/{UUID}/retry/extra/",
    "/agentcc/siem/deliveries/retry/",
    "/agentcc/siem/deliveries//",
    "/other/agentcc/siem/destinations/",
    "/agentcc/Siem/destinations/",
    "/agentcc/traces/",
]


class AuthenticatedProbe(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, **kwargs):
        return HttpResponse("ON-only probe")


urlpatterns = [path("agentcc/" + p, AuthenticatedProbe.as_view()) for p in PATTERNS]
urlpatterns += [path("unrelated/", lambda request: HttpResponse("unrelated"))]


@override_settings(
    DEBUG=False,
    APPEND_SLASH=True,
    ROOT_URLCONF=__name__,
    MIDDLEWARE=[
        "agentcc.mcp_features.MCPSubfeatureMiddleware",
        "django.middleware.common.CommonMiddleware",
    ],
    REST_FRAMEWORK={"UNAUTHENTICATED_USER": None},
)
class SIEMEarlyBoundaryTests(SimpleTestCase):
    def setUp(self):
        # Same fail-closed socket sentinel as test_siem_switch_pure, extended to
        # DNS: the boundary must not even attempt name resolution.
        for name in ("socket", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
            guard = patch.object(socket, name, side_effect=AssertionError("network"))
            guard.start()
            self.addCleanup(guard.stop)

    @override_settings(AGENTCC_SIEM_EXPORT_ENABLED=False)
    def test_off_boundary_refuses_before_downstream_without_database(self):
        expected = page_not_found(RequestFactory().get("/unknown/"), Http404())
        for route in ROUTES:
            for url in (route, route[:-1]):
                for auth in ("", "Bearer invalid", "Bearer sk-agentcc-invalid"):
                    with self.subTest(url=url, auth=auth):
                        request = RequestFactory().get(url, HTTP_AUTHORIZATION=auth)
                        with patch.object(
                            APIView,
                            "initialize_request",
                            side_effect=AssertionError("DRF reached while OFF"),
                        ):
                            response = Client().get(url, HTTP_AUTHORIZATION=auth)
                        self.assertEqual(response.status_code, 404)
                        self.assertEqual(response.content, expected.content)
                        for key, value in expected.headers.items():
                            self.assertEqual(response[key], value)
                        self.assertEqual(response["Cache-Control"], "no-store")
                        self.assertNotIn("Location", response)
                        self.assertFalse(response.cookies)
                        self.assertEqual(request.path_info, url)

    @override_settings(AGENTCC_SIEM_EXPORT_ENABLED=False)
    def test_off_exact_match_does_not_consume_lookalikes(self):
        seen = []

        def downstream(request):
            seen.append(request.path_info)
            return HttpResponse(status=418)

        middleware = MCPSubfeatureMiddleware(downstream)
        for url in LOOKALIKES:
            with self.subTest(url=url):
                response = middleware(RequestFactory().get(url))
                self.assertEqual(response.status_code, 418)
        self.assertEqual(seen, LOOKALIKES)

    @override_settings(AGENTCC_SIEM_EXPORT_ENABLED=True)
    def test_on_reaches_real_drf_and_retains_common_redirect(self):
        original = APIView.initialize_request
        with patch.object(
            APIView, "initialize_request", autospec=True, side_effect=original
        ) as initialize:
            for route in ROUTES:
                with self.subTest(route=route):
                    before = initialize.call_count
                    self.assertIn(Client().get(route).status_code, (401, 403))
                    self.assertEqual(initialize.call_count, before + 1)
                    response = Client().get(route[:-1])
                    self.assertEqual(response.status_code, 301)
                    self.assertEqual(response["Location"], route)
                    self.assertEqual(initialize.call_count, before + 1)

    @override_settings(AGENTCC_SIEM_EXPORT_ENABLED=False)
    def test_unrelated_common_redirect_is_unchanged(self):
        response = Client().get("/unrelated")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/unrelated/")
        self.assertEqual(Client().get("/unrelated/").status_code, 200)

    @override_settings(
        AGENTCC_SIEM_EXPORT_ENABLED=False,
        AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED=False,
        AGENTCC_MCP_CLIENT_OAUTH_ENABLED=False,
        AGENTCC_MCP_TOOL_CALL_EXPLORER_ENABLED=False,
    )
    def test_other_mcp_gates_keep_their_response_contract(self):
        for url in ("/agentcc/mcp/connect/example/", "/agentcc/mcp-oauth/token/"):
            response = Client().get(url)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["code"], "not_found")
            self.assertEqual(response["Cache-Control"], "no-store")
        response = Client().get("/agentcc/mcp/tool-calls/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "mcp_tool_call_explorer_disabled")


if __name__ == "__main__":
    unittest.main(verbosity=2)
