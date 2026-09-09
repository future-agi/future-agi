"""Generic in-process execution of generated MCP tools through Django APIs."""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from asgiref.sync import async_to_sync, sync_to_async
from django.urls import Resolver404, resolve
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.authentication import APIKeyAuthentication
from mcp_server.generated_registry import GeneratedTool
from tfc.middleware.workspace_context import workspace_context


class APIExecutionError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 500, data: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.data = data


@dataclass(frozen=True)
class MCPRequestContext:
    user: Any
    organization: Any
    workspace: Any


class DjangoAPIExecutor:
    """Map generated arguments to a Django route and execute its DRF view.

    Targets come exclusively from the reviewed generated manifest. The MCP
    caller cannot provide an arbitrary URL or HTTP method.
    """

    def __init__(self) -> None:
        self._request_factory = APIRequestFactory()

    async def execute(
        self,
        tool: GeneratedTool,
        arguments: dict[str, Any],
        context: MCPRequestContext,
    ) -> Any:
        return await sync_to_async(self.execute_sync, thread_sensitive=True)(
            tool, arguments, context
        )

    def execute_sync(
        self,
        tool: GeneratedTool,
        arguments: dict[str, Any],
        context: MCPRequestContext,
    ) -> Any:
        method = tool.request["method"].upper()
        parameter_map = tool.request["parameters"]
        path = self._render_path(
            tool.request["path"], parameter_map.get("path", []), arguments
        )
        query = {
            name: arguments[name]
            for name in parameter_map.get("query", [])
            if name in arguments and arguments[name] is not None
        }
        body = {
            name: arguments[name]
            for name in parameter_map.get("body", [])
            if name in arguments
        }
        request_path = path
        if query:
            request_path = f"{path}?{urlencode(query, doseq=True)}"

        request_kwargs = {
            "HTTP_X_ORGANIZATION_ID": str(context.organization.id),
            "HTTP_X_WORKSPACE_ID": (
                str(context.workspace.id) if context.workspace is not None else ""
            ),
        }
        if body:
            request = self._request_factory.generic(
                method,
                request_path,
                data=json.dumps(body),
                content_type="application/json",
                **request_kwargs,
            )
        else:
            request = self._request_factory.generic(
                method,
                request_path,
                **request_kwargs,
            )
        try:
            match = resolve(path)
        except Resolver404 as exc:
            raise APIExecutionError(
                f"Generated API route no longer resolves: {method} {path}"
            ) from exc
        request.resolver_match = match

        # Reuse the existing workspace membership and write-access checks, then
        # inject the already authenticated MCP user for normal DRF permissions.
        APIKeyAuthentication()._set_workspace_context(request, context.user)
        force_authenticate(request, user=context.user)

        with workspace_context(
            workspace=context.workspace,
            organization=context.organization,
            user=context.user,
        ):
            response = match.func(request, *match.args, **match.kwargs)
            if inspect.isawaitable(response):

                async def await_response():
                    return await response

                response = async_to_sync(await_response)()

        return self._normalize_response(tool, response)

    @staticmethod
    def _render_path(
        path_template: str, path_parameters: list[str], arguments: dict[str, Any]
    ) -> str:
        path = path_template
        for name in path_parameters:
            if name not in arguments or arguments[name] is None:
                raise APIExecutionError(
                    f"Missing required path parameter: {name}", status_code=400
                )
            path = path.replace(f"{{{name}}}", quote(str(arguments[name]), safe=""))
        if "{" in path or "}" in path:
            raise APIExecutionError(
                f"Unable to resolve generated API path: {path_template}",
                status_code=400,
            )
        return path

    @staticmethod
    def _normalize_response(tool: GeneratedTool, response: Any) -> Any:
        if isinstance(response, Response):
            data = response.data
            status_code = response.status_code
        else:
            status_code = getattr(response, "status_code", 500)
            try:
                data = json.loads(response.content)
            except (AttributeError, json.JSONDecodeError, TypeError):
                data = getattr(response, "content", None)

        if status_code >= 400:
            message = "Django API request failed"
            if isinstance(data, dict):
                message = (
                    data.get("message")
                    or data.get("detail")
                    or data.get("error")
                    or message
                )
            raise APIExecutionError(str(message), status_code=status_code, data=data)

        unwrap = tool.response.get("unwrap")
        if unwrap and isinstance(data, dict) and unwrap in data:
            return data[unwrap]
        return data


executor = DjangoAPIExecutor()
