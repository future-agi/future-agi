"""
ASGI config for tfc project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/asgi/
"""

import os

# Must set DJANGO_SETTINGS_MODULE before any Django or telemetry imports
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")

# OpenTelemetry instrumentation - must be initialized before Django
# This enables distributed tracing including LLM spans
try:
    from tfc.telemetry import init_telemetry, instrument_for_django

    provider = init_telemetry(component="django-asgi")
    if provider:
        instrument_for_django()
except ImportError as e:
    import logging

    logging.getLogger(__name__).warning(f"Failed to initialize telemetry: {e}")

import django

django.setup()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

# Import necessary modules AFTER setting up Django
from django.core.asgi import get_asgi_application  # noqa: E402

from sockets.routing import websocket_urlpatterns  # noqa: E402
from tfc.asgi_startup import warm_http_urlconf  # noqa: E402
from tfc.middleware.jwt_auth import JWTAuthMiddleware  # noqa: E402

# Django ASGI app for standard HTTP requests
_django_app = get_asgi_application()
# Granian constructs the middleware stack while importing this module, but
# Django leaves ROOT_URLCONF lazy until the first HTTP request. Materialize the
# complete route graph now so a newly spawned worker cannot charge broad view
# imports to its first customer request.
warm_http_urlconf()

# MCP Streamable HTTP app - lazy loaded to avoid import errors if mcp not installed
_mcp_app = None
_mcp_starlette_app = None


def _get_mcp_app():
    global _mcp_app
    if _mcp_app is None:
        try:
            from mcp_server.mcp_app import mcp_streamable_with_auth

            _mcp_app = mcp_streamable_with_auth
        except ImportError:
            import logging

            logging.getLogger(__name__).warning(
                "MCP SDK not installed, Streamable HTTP transport disabled"
            )
            _mcp_app = _django_app  # Fallback to Django
    return _mcp_app


def _get_mcp_starlette_app():
    """Get the MCP Starlette app for lifespan forwarding."""
    global _mcp_starlette_app
    if _mcp_starlette_app is None:
        try:
            from mcp_server.mcp_app import get_mcp_streamable_app

            _mcp_starlette_app = get_mcp_streamable_app()
        except ImportError:
            pass
    return _mcp_starlette_app


# OAuth paths that the MCP SDK client expects at the server root
_OAUTH_PATHS = {
    "/.well-known/oauth-authorization-server",
    "/register",
    "/authorize",
    "/token",
    "/revoke",
}

_oauth_app = None


def _get_oauth_app():
    global _oauth_app
    if _oauth_app is None:
        try:
            from mcp_server.mcp_app import get_mcp_oauth_app

            _oauth_app = get_mcp_oauth_app()
        except ImportError:
            import logging

            logging.getLogger(__name__).warning("MCP OAuth routes not available")
            _oauth_app = _django_app  # Fallback to Django
    return _oauth_app


async def http_router(scope, receive, send):
    """Route HTTP requests to the appropriate ASGI app.

    - OAuth discovery/registration paths -> MCP OAuth Starlette app
    - /mcp -> MCP Streamable HTTP app (with auth middleware)
    - Everything else -> Django ASGI app
    """
    path = scope.get("path", "")
    # Some clients may request endpoints with a trailing slash.
    # Normalize for exact-path comparisons only (keep original for prefix checks).
    normalized_path = path.rstrip("/") or "/"

    # OAuth routes -> MCP OAuth Starlette app
    # Note: MCP clients use RFC 8414 path-aware discovery, e.g.
    # /.well-known/oauth-authorization-server/mcp
    if (
        normalized_path in _OAUTH_PATHS
        or path.startswith("/.well-known/oauth-authorization-server")
        or path.startswith("/.well-known/oauth-protected-resource")
        or path.startswith("/.well-known/openid-configuration")
    ):
        oauth_app = _get_oauth_app()
        # RFC 8414 path-aware discovery: client may request e.g.
        # /.well-known/oauth-authorization-server/mcp
        # The SDK only registers the exact path for auth-server metadata,
        # so strip the suffix. Protected-resource routes already include
        # the full path (e.g. /.well-known/oauth-protected-resource/mcp).
        oauth_scope = dict(scope)
        auth_server_prefix = "/.well-known/oauth-authorization-server"
        if path.startswith(auth_server_prefix) and len(path) > len(auth_server_prefix):
            oauth_scope["path"] = auth_server_prefix
        await oauth_app(oauth_scope, receive, send)
    # MCP Streamable HTTP endpoint -> only the exact /mcp path
    # All sub-paths (/mcp/config/, /mcp/sessions/, /mcp/oauth/, etc.)
    # are Django REST API routes and must go through Django.
    elif path == "/mcp" or path == "/mcp/":
        app = _get_mcp_app()
        await app(scope, receive, send)
    elif path.startswith("/mcp/"):
        await _django_app(scope, receive, send)
    else:
        await _django_app(scope, receive, send)


async def lifespan_handler(scope, receive, send):
    """Handle ASGI lifespan events.

    Forwards lifespan startup/shutdown to the MCP Starlette app so its
    StreamableHTTP session manager gets properly initialized. This prevents
    the 'Task group is not initialized' error.
    """
    mcp_app = _get_mcp_starlette_app()
    if mcp_app is not None:
        # Forward lifespan to MCP Starlette app
        await mcp_app(scope, receive, send)
    else:
        # No MCP app, just acknowledge lifespan
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return


# Channels ProtocolTypeRouter for http + websocket
_channels_app = ProtocolTypeRouter(
    {
        "http": http_router,
        "websocket": JWTAuthMiddleware(URLRouter(websocket_urlpatterns)),
    }
)


async def application(scope, receive, send):
    """Root ASGI application that handles lifespan + delegates to channels."""
    if scope["type"] == "lifespan":
        await lifespan_handler(scope, receive, send)
    else:
        await _channels_app(scope, receive, send)
