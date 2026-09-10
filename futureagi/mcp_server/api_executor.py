"""Generic in-process execution of generated MCP tools through Django APIs."""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from asgiref.sync import async_to_sync, sync_to_async
from django.urls import Resolver404, get_resolver, resolve
from jsonschema.exceptions import ValidationError
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.authentication import APIKeyAuthentication
from mcp_server.generated_registry import GeneratedTool
from mcp_server.response_limits import ResponseTooLargeError, bounded_response
from tfc.middleware.workspace_context import (
    get_current_workspace,
    workspace_context,
)


def ensure_urlconf_loaded() -> None:
    """Import the URLconf while no request workspace is in scope.

    The first route resolution in a process imports every view module, and
    class-level querysets such as ``queryset = Model.objects.all()`` are built
    during that import. Django's HTTP path clears the workspace context before
    resolving; the MCP path binds the caller's workspace first, so a cold worker
    whose first request is an MCP call would pin those querysets to one tenant.
    """
    if get_current_workspace() is not None:
        raise RuntimeError("URLconf must be loaded before a workspace is bound")
    # Accessing the patterns imports the root URLconf and every included module.
    _ = get_resolver().url_patterns


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
        try:
            tool.validator.validate(arguments)
        except ValidationError as exc:
            raise APIExecutionError(
                f"Invalid tool arguments: {exc.message}", status_code=400
            ) from exc
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
        try:
            authentication = APIKeyAuthentication()
            authentication._set_workspace_context(request, context.user)
            # Some legacy GET APIs create a workbench draft. Their catalog
            # write classification must not bypass Django's write-role check.
            if (
                method in {"GET", "HEAD", "OPTIONS"}
                and not tool.annotations["readOnlyHint"]
                and request.workspace
                and not authentication._can_write_to_workspace(
                    context.user, request.workspace
                )
            ):
                raise PermissionDenied("Write access denied to this workspace")
        except APIException as exc:
            raise APIExecutionError(
                str(exc.detail), status_code=exc.status_code, data=exc.detail
            ) from exc
        force_authenticate(request, user=context.user)

        with workspace_context(
            workspace=request.workspace,
            organization=request.organization,
            user=context.user,
        ):
            response = match.func(request, *match.args, **match.kwargs)
            if inspect.isawaitable(response):

                async def await_response():
                    return await response

                response = async_to_sync(await_response)()

        result = self._normalize_response(tool, response)
        try:
            return bounded_response(result)
        except ResponseTooLargeError as exc:
            if not tool.annotations["readOnlyHint"]:
                # The API has already committed this write. Preserve its receipt
                # so clients do not retry a successful mutation after a size error.
                receipt = {
                    "_mcp": {
                        "truncated": True,
                        "message": (
                            "The operation succeeded. Its response exceeded the MCP size limit; "
                            "use the API to retrieve the full resource."
                        ),
                    }
                }
                if isinstance(result, dict):
                    for key, value in result.items():
                        if (
                            (key == "id" or key.endswith("_id") or key == "status")
                            and (
                                value is None
                                or isinstance(value, (str, int, float, bool))
                            )
                            and len(str(value)) <= 200
                        ):
                            receipt[key] = value
                return receipt
            raise APIExecutionError(str(exc), status_code=413) from exc

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
