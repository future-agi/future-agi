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

async def http_router(scope, receive, send):
    """Route HTTP to Django; MCP is served by the standalone Node service."""
    await _django_app(scope, receive, send)


async def lifespan_handler(scope, receive, send):
    """Acknowledge ASGI lifespan for the Django/channels application."""
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
