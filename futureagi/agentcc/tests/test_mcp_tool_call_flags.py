"""W14 NEW-only explorer/download gates; these tests never require a database.

The middleware must refuse before auth, queryset/count, audit or streaming.
Existing call API scenario tests separately exercise the enabled view bodies.
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from agentcc import mcp_features
from agentcc.serializers.contracts import MCPFeaturesSerializer

EXPLORER = "AGENTCC_MCP_TOOL_CALL_EXPLORER_ENABLED"
DOWNLOAD = "AGENTCC_MCP_TOOL_CALL_DOWNLOAD_ENABLED"
ROOT = "/agentcc/mcp/tool-calls/"


def respond(path):
    downstream = Mock(return_value=HttpResponse(status=204))
    response = mcp_features.MCPSubfeatureMiddleware(downstream)(
        RequestFactory().get(path)
    )
    return response, downstream


def test_source_defaults_off(settings):
    assert getattr(settings, EXPLORER, None) is False
    assert getattr(settings, DOWNLOAD, None) is False


@pytest.mark.parametrize("explorer", [False, True])
@pytest.mark.parametrize("download", [False, True])
@pytest.mark.parametrize(
    "path",
    [
        ROOT,
        ROOT + "46/",
        ROOT + "export/?export_format=csv",
        ROOT + "export/?export_format=json",
    ],
)
def test_api_matrix(explorer, download, path):
    is_download = "export/" in path
    allowed = explorer and (not is_download or download)
    with override_settings(**{EXPLORER: explorer, DOWNLOAD: download}):
        response, downstream = respond(path)
    if allowed:
        assert response.status_code == 204
        downstream.assert_called_once()
    else:
        assert response.status_code == 404
        code = (
            "mcp_tool_call_download_disabled"
            if explorer
            else "mcp_tool_call_explorer_disabled"
        )
        assert json.loads(response.content)["code"] == code
        assert response["Cache-Control"] == "no-store"
        downstream.assert_not_called()


@pytest.mark.parametrize("value", [None, False, "true", 1, True])
def test_capabilities_require_literal_boolean_and_download_depends_on_explorer(value):
    with override_settings(**{EXPLORER: value, DOWNLOAD: True}):
        features = mcp_features.capabilities()
        assert features["tool_call_explorer"] is (value is True)
        assert features["tool_call_download"] is (value is True)
        serializer = MCPFeaturesSerializer(data=features)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data == features
    with override_settings(**{EXPLORER: True, DOWNLOAD: value}):
        assert mcp_features.capabilities()["tool_call_download"] is (value is True)
        if value is not True:
            response, downstream = respond(ROOT + "export/")
            assert response.status_code == 404
            downstream.assert_not_called()


def test_missing_switches_fail_closed():
    with patch.object(mcp_features, "settings", SimpleNamespace()):
        assert mcp_features.capabilities()["tool_call_explorer"] is False
        assert mcp_features.capabilities()["tool_call_download"] is False
        response, downstream = respond(ROOT)
        assert response.status_code == 404
        downstream.assert_not_called()


@pytest.mark.parametrize(
    "path",
    [
        ROOT.rstrip("/"),
        ROOT + "46",
        ROOT + "export",
    ],
)
def test_slashless_paths_are_refused_before_redirects(path):
    with override_settings(**{EXPLORER: False, DOWNLOAD: False}):
        response, downstream = respond(path)
    assert response.status_code == 404
    downstream.assert_not_called()


@pytest.mark.parametrize(
    "path",
    [
        "/agentcc/gateways/gw-1/mcp-status/",
        "/agentcc/gateways/gw-1/mcp-tools/",
        "/agentcc/gateways/gw-1/mcp-served-tools/",
        "/agentcc/gateways/gw-1/test-mcp-tool/",
        "/agentcc/gateways/gw-1/update-mcp-server/",
        "/agentcc/gateways/gw-1/remove-mcp-server/",
        "/mcp/oauth/authorize/",
        "/mcp/oauth/token/",
        "/agentcc/request-logs/export/",
        "/agentcc/siem/destinations/",
        "/agentcc/mcp/tool-calls-ingest/",
        ROOT + "not-a-call/",
    ],
)
def test_pre_existing_surfaces_and_unrelated_paths_unchanged(path):
    # This row isolates W14. SIEM has its own independent deployment switch.
    with override_settings(
        **{EXPLORER: False, DOWNLOAD: False}, AGENTCC_SIEM_EXPORT_ENABLED=True
    ):
        response, downstream = respond(path)
    assert response.status_code == 204
    downstream.assert_called_once()


@pytest.mark.parametrize(
    "view_name,args",
    [
        ("MCPToolCallListView", ()),
        ("MCPToolCallDetailView", (46,)),
        ("MCPToolCallExportView", ()),
    ],
)
def test_direct_view_off_does_not_touch_querysets_or_export(view_name, args):
    from agentcc.views import mcp_tool_calls

    view = getattr(mcp_tool_calls, view_name)()
    with (
        override_settings(**{EXPLORER: False, DOWNLOAD: True}),
        patch.object(view, "_base_queryset") as base,
        patch.object(view, "_filtered_queryset") as filtered,
        patch.object(mcp_tool_calls.export, "record_export") as audit,
    ):
        response = view.get(RequestFactory().get(ROOT), *args)
    assert response.status_code == 404
    base.assert_not_called()
    filtered.assert_not_called()
    audit.assert_not_called()


@pytest.mark.parametrize(
    "view_name,args,download",
    [
        ("MCPToolCallListView", (), False),
        ("MCPToolCallDetailView", (46,), False),
        ("MCPToolCallExportView", (), True),
    ],
)
def test_direct_view_on_reaches_existing_body_without_a_database(
    view_name, args, download
):
    from agentcc.views import mcp_tool_calls

    class ReachedExistingBody(Exception):
        pass

    view = getattr(mcp_tool_calls, view_name)()
    request = RequestFactory().get(ROOT)
    request.query_params = {}
    with (
        override_settings(**{EXPLORER: True, DOWNLOAD: download}),
        patch.object(view, "_base_queryset", side_effect=ReachedExistingBody),
        patch.object(view, "_filtered_queryset", side_effect=ReachedExistingBody),
        pytest.raises(ReachedExistingBody),
    ):
        view.get(request, *args)


def test_direct_download_off_with_explorer_on_does_no_io():
    from agentcc.views.mcp_tool_calls import MCPToolCallExportView

    view = MCPToolCallExportView()
    with (
        override_settings(**{EXPLORER: True, DOWNLOAD: False}),
        patch.object(view, "_filtered_queryset") as queryset,
    ):
        response = view.get(RequestFactory().get(ROOT + "export/"))
    assert response.status_code == 404
    assert json.loads(response.content)["code"] == "mcp_tool_call_download_disabled"
    queryset.assert_not_called()
