from urllib.parse import urlparse

import requests
import structlog
from django.conf import settings

from tfc.ee_loader import is_cloud_env

logger = structlog.get_logger(__name__)


def is_forbidden_vendor_endpoint(url):
    """A self-hosted deployment must never send its internal signature to Cloud."""
    host = (urlparse(url).hostname or "").rstrip(".").lower()
    on_vendor_host = host == "futureagi.com" or host.endswith(".futureagi.com")
    return on_vendor_host and not is_cloud_env(settings.CLOUD_DEPLOYMENT)


def call_websocket(organization_id, message, send_to_uuid=False, uuid=None):
    """Publish from a sync worker without creating or looking up any API key."""
    from tfc.channel_layers import publish_worker_message
    from tfc.websocket_relay import sign_relay

    try:
        if send_to_uuid and not uuid:
            raise ValueError("A stream UUID is required")
        if settings.CHANNEL_LAYER_BACKEND in {"redis", "rabbitmq"}:
            publish_worker_message(
                organization_id, message, stream=uuid if send_to_uuid else None
            )
            return {"status": True, "result": "Message sent to websocket"}
        if settings.CHANNEL_LAYER_BACKEND != "memory":
            raise ValueError("Unsupported channel layer")
        url = settings.WEBSOCKET_ENDPOINT
        if is_forbidden_vendor_endpoint(url):
            logger.error("websocket_relay_refused_vendor_endpoint")
            return {
                "status": "error",
                "message": "WEBSOCKET_ENDPOINT must point at this install's backend",
            }
        payload = {
            "organization_id": str(organization_id),
            "message": message,
            "send_to_uuid": send_to_uuid,
            "uuid": uuid,
        }
        body, headers = sign_relay(payload, url)
        response = requests.post(
            url, data=body, headers=headers, timeout=60, allow_redirects=False
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        # Request exceptions can embed URLs or payloads; never log envelopes.
        logger.warning(
            "websocket_delivery_failed", organization_id=str(organization_id)
        )
        return {"status": "error", "message": "Unable to send message to websocket"}
