import uuid

import pytest

from mcp_server.api_executor import APIExecutionError, MCPRequestContext, executor
from mcp_server.generated_registry import registry


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_executor_calls_existing_django_api(user, workspace):
    context = MCPRequestContext(
        user=user,
        organization=user.organization,
        workspace=workspace,
    )

    result = await executor.execute(registry.get("whoami"), {}, context)

    assert str(result["id"]) == str(user.id)


def test_executor_renders_and_quotes_path_parameters():
    assert (
        executor._render_path("/things/{id}/", ["id"], {"id": "a/b"})
        == "/things/a%2Fb/"
    )


def test_executor_rejects_missing_path_parameter():
    with pytest.raises(APIExecutionError, match="Missing required path parameter"):
        executor._render_path("/things/{id}/", ["id"], {})


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_executor_returns_api_not_found_as_tool_error(user, workspace):
    context = MCPRequestContext(
        user=user,
        organization=user.organization,
        workspace=workspace,
    )

    with pytest.raises(APIExecutionError) as exc_info:
        await executor.execute(
            registry.get("get_prompt_template"), {"id": str(uuid.uuid4())}, context
        )

    # Preserve the endpoint's existing status contract rather than translating
    # errors in the MCP layer.
    assert exc_info.value.status_code == 400
