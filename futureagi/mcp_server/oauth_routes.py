"""Compatibility for the pinned MCP SDK's OAuth revocation form."""

from mcp.server.auth.handlers.revoke import RevocationHandler
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.streamable_http import MCP_PROTOCOL_VERSION_HEADER
from mcp.server.transport_security import (
    DEFAULT_MAX_REQUEST_BODY_SIZE,
    RequestBodyLimitMiddleware,
)
from starlette.datastructures import FormData
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route, request_response


class _OptionalSecretRevocationHandler(RevocationHandler):
    async def handle(self, request):
        # MCP 1.30's RevocationRequest declares client_secret nullable but still
        # required. Public clients and Basic-auth clients legitimately omit it.
        # Normalize only the parsed form, not the original body or auth headers.
        form = await request.form()
        if "client_secret" not in form:
            request._form = FormData([*form.multi_items(), ("client_secret", "")])
        # The SDK still authenticates the client and checks token ownership.
        # An empty secret cannot authenticate a confidential client.
        return await super().handle(request)


def revocation_route(provider):
    handler = _OptionalSecretRevocationHandler(provider, ClientAuthenticator(provider))
    # Retain the SDK route's body limit, CORS behavior and method restrictions.
    endpoint = CORSMiddleware(
        RequestBodyLimitMiddleware(
            request_response(handler.handle), DEFAULT_MAX_REQUEST_BODY_SIZE
        ),
        allow_origins=["*"],
        allow_methods=["POST", "OPTIONS"],
        allow_headers=[MCP_PROTOCOL_VERSION_HEADER],
    )
    return Route("/revoke", endpoint=endpoint, methods=["POST", "OPTIONS"])
