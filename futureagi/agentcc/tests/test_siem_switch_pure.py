"""Small no-database checks, runnable as an isolated unittest script.

Execute the source base class with the real DRF lifecycle and Django 404
handler, replacing only model/service globals. Full URLconf/middleware and
persisted-row coverage lives in test_siem_switch.py and the SIEM API suites.
"""

import ast
import io
import json
import os
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from django.conf import settings

if __name__ == "__main__":
    settings.configure(
        DEBUG=False,
        SECRET_KEY="isolated-siem-test",
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=["testserver"],
        TEMPLATES=[],
        REST_FRAMEWORK={"UNAUTHENTICATED_USER": None},
    )
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from django.core.management.base import BaseCommand
from django.http import Http404
from django.test import Client, RequestFactory, override_settings
from django.views.defaults import page_not_found
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from agentcc import mcp_features
from agentcc.services.siem_settings import export_enabled

urlpatterns = []
ROOT = Path(__file__).resolve().parents[1]


def source_nodes(relative, names, namespace):
    path = ROOT / relative
    tree = ast.parse(path.read_text())
    nodes = [node for node in tree.body if getattr(node, "name", None) in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


class SIEMSwitchPureTests(unittest.TestCase):
    def setUp(self):
        self.no_socket = patch.object(
            socket, "socket", side_effect=AssertionError("network")
        )
        self.no_socket.start()
        self.addCleanup(self.no_socket.stop)

    def base_view(self):
        namespace = source_nodes(
            "views/siem.py",
            {"_SIEMView"},
            {
                "APIView": APIView,
                "IsAuthenticated": IsAuthenticated,
                "SIEMPermission": IsAuthenticated,
                "GeneralMethods": Mock,
                "export_enabled": export_enabled,
                "Http404": Http404,
                "page_not_found": page_not_found,
            },
        )
        return namespace["_SIEMView"]

    def test_off_dispatch_reuses_unknown_route_bytes_before_drf(self):
        unknown = Client().get("/agentcc/unknown-route/")
        for method in (
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "OPTIONS",
            "HEAD",
            "TRACE",
        ):
            with (
                self.subTest(method=method),
                override_settings(AGENTCC_SIEM_EXPORT_ENABLED=False),
            ):
                request = RequestFactory().generic(
                    method, "/agentcc/siem/destinations/", b"{"
                )
                with patch.object(
                    APIView,
                    "initialize_request",
                    side_effect=AssertionError("DRF reached while OFF"),
                ):
                    response = self.base_view().as_view()(request)
                self.assertEqual(response.status_code, unknown.status_code)
                self.assertEqual(response.content, unknown.content)
                self.assertEqual(
                    dict(response.headers),
                    {**dict(unknown.headers), "Cache-Control": "no-store"},
                )

    def test_on_dispatch_reaches_existing_drf_lifecycle(self):
        with (
            override_settings(AGENTCC_SIEM_EXPORT_ENABLED=True),
            patch.object(
                APIView,
                "initialize_request",
                side_effect=RuntimeError("existing lifecycle"),
            ),
            self.assertRaisesRegex(RuntimeError, "existing lifecycle"),
        ):
            self.base_view().as_view()(
                RequestFactory().get("/agentcc/siem/destinations/")
            )

    def test_every_siem_route_inherits_the_guard(self):
        tree = ast.parse((ROOT / "views/siem.py").read_text())
        classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
        routes = ast.parse((ROOT / "urls_siem.py").read_text())
        names = {
            n.value.id
            for n in ast.walk(routes)
            if isinstance(n, ast.Attribute) and n.attr == "as_view"
        }
        self.assertEqual(len(names), 7)
        for name in names:
            while name != "_SIEMView":
                node = classes[name]
                self.assertFalse(
                    any(getattr(n, "name", None) == "dispatch" for n in node.body)
                )
                name = node.bases[0].id

    def test_capability_uses_the_same_switch_and_is_independent(self):
        for enabled in (False, True):
            for explorer in (False, True):
                with (
                    self.subTest(siem=enabled, explorer=explorer),
                    override_settings(
                        AGENTCC_SIEM_EXPORT_ENABLED=enabled,
                        AGENTCC_MCP_TOOL_CALL_EXPLORER_ENABLED=explorer,
                        AGENTCC_MCP_TOOL_CALL_DOWNLOAD_ENABLED=explorer,
                    ),
                ):
                    self.assertIs(
                        mcp_features.capabilities().get("siem_export"), enabled
                    )

    def test_default_off_and_environment_fallback(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            override_settings(AGENTCC_SIEM_EXPORT_ENABLED=None),
        ):
            self.assertFalse(export_enabled())
            self.assertIs(mcp_features.capabilities().get("siem_export"), False)
        with (
            patch.dict(os.environ, {"AGENTCC_SIEM_EXPORT_ENABLED": "true"}),
            override_settings(AGENTCC_SIEM_EXPORT_ENABLED=False),
        ):
            self.assertFalse(export_enabled())
            self.assertIs(mcp_features.capabilities().get("siem_export"), False)

    def test_command_and_enqueue_do_no_io_while_off(self):
        model = Mock()
        model.objects.filter.side_effect = AssertionError("queryset reached")
        namespace = source_nodes(
            "services/siem_delivery.py",
            {"deliver_due", "enqueue"},
            {
                "export_enabled": export_enabled,
                "AgentccSIEMDestination": model,
                "AgentccSIEMDelivery": model,
                "timezone": Mock(),
            },
        )
        namespace = source_nodes(
            "management/commands/agentcc_siem_deliver.py",
            {"Command"},
            {
                **namespace,
                "BaseCommand": BaseCommand,
                "json": json,
            },
        )
        out = io.StringIO()
        with override_settings(AGENTCC_SIEM_EXPORT_ENABLED=False):
            namespace["Command"](stdout=out).handle(limit=100)
            self.assertEqual(namespace["enqueue"](object(), ["event-1"]), 0)
        self.assertEqual(
            json.loads(out.getvalue()),
            {
                "delivered": 0,
                "failed": 0,
                "lease_lost": 0,
                "skipped": True,
            },
        )
        model.objects.filter.assert_not_called()
        namespace["timezone"].now.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
