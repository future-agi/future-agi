"""Generation, registry and actual executor agree on the narrow header boundary."""

from uuid import uuid4

import pytest
from django.urls import path
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from mcp_server.api_executor import APIExecutionError, MCPRequestContext, executor
from mcp_server.generated_registry import GeneratedTool, GeneratedToolRegistryError
from mcp_server.tool_generation import ToolGenerationError, _build_input_schema


class HeaderEcho(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        return Response(
            {
                "operation": request.headers.get("Idempotency-Key"),
                "resolution": request.headers.get("X-Context-Resolution"),
                "workspace": request.headers.get("X-Workspace-Id"),
            }
        )


urlpatterns = [path("header-boundary/", HeaderEcho.as_view())]


def tool_value(names):
    input_schema, mapping = _build_input_schema(
        [
            {
                "name": name,
                "in": "header",
                "type": "string",
                "format": "uuid",
                "required": True,
            }
            for name in names
        ],
        {},
        method="post",
    )
    return {
        "name": "test_header_boundary",
        "description": "Synthetic transport fixture",
        "group": "skills",
        "inputSchema": input_schema,
        "annotations": {"readOnlyHint": False},
        "request": {
            "method": "POST",
            "path": "/header-boundary/",
            "parameters": mapping,
        },
        "response": {},
        "source": {},
    }


@pytest.mark.parametrize(
    "name",
    [
        "Authorization",
        "X-Api-Key",
        "X-Secret-Key",
        "X-Workspace-Id",
        "X-Organization-Id",
        "Cookie",
        "idempotency-key",
    ],
)
def test_generator_rejects_unapproved_headers(name):
    with pytest.raises(ToolGenerationError, match="forbidden"):
        tool_value([name])


def test_registry_rejects_forged_mapping():
    value = tool_value(["Idempotency-Key"])
    value["request"]["parameters"]["header"] = ["Authorization"]
    with pytest.raises(GeneratedToolRegistryError):
        GeneratedTool.from_dict(value)


@pytest.mark.django_db
def test_executor_preserves_caller_keys_across_retries(
    settings, user, workspace, organization
):
    settings.ROOT_URLCONF = __name__
    tool = GeneratedTool.from_dict(
        tool_value(["Idempotency-Key", "X-Context-Resolution"])
    )
    arguments = {"Idempotency-Key": str(uuid4()), "X-Context-Resolution": str(uuid4())}
    context = MCPRequestContext(
        user=user, organization=organization, workspace=workspace
    )
    first = executor.execute_sync(tool, arguments, context)
    second = executor.execute_sync(tool, arguments, context)
    assert (
        first
        == second
        == {
            "operation": arguments["Idempotency-Key"],
            "resolution": arguments["X-Context-Resolution"],
            "workspace": str(workspace.pk),
        }
    )


@pytest.mark.django_db
def test_executor_revalidates_mapping_even_if_registry_was_bypassed(
    user, workspace, organization
):
    tool = GeneratedTool.from_dict(tool_value(["Idempotency-Key"]))
    tool.request["parameters"]["header"] = ["Authorization"]
    with pytest.raises(APIExecutionError, match="Invalid caller header"):
        executor.execute_sync(
            tool,
            {"Idempotency-Key": str(uuid4())},
            MCPRequestContext(
                user=user, organization=organization, workspace=workspace
            ),
        )
