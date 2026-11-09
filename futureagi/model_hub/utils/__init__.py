from urllib.parse import urlparse

import requests
import structlog
from django.conf import settings

from accounts.models import OrgApiKey
from tfc.ee_loader import is_cloud_env

logger = structlog.get_logger(__name__)


def is_forbidden_vendor_endpoint(url):
    """True for a futureagi.com URL on an install that is not Future AGI Cloud.
    Such an install must never send its org's API key and secret there."""
    host = (urlparse(url).hostname or "").rstrip(".").lower()
    on_vendor_host = host == "futureagi.com" or host.endswith(".futureagi.com")
    return on_vendor_host and not is_cloud_env(settings.CLOUD_DEPLOYMENT)


def _relay_validation_fields(response):
    """Report only known field names, never response text or message contents."""
    if response is None or response.status_code != 400:
        return []
    try:
        body = response.json()
    except (ValueError, TypeError):
        return []
    if not isinstance(body, dict):
        return []
    details = body.get("details") or body.get("result")
    if not isinstance(details, dict):
        return []
    return sorted(
        set(details) & {"message", "uuid", "send_to_uuid", "non_field_errors"}
    )


# Function to send message to websocket to avoid RunTimeError
def call_websocket(organization_id, message, send_to_uuid=False, uuid=None):
    response = None
    try:
        url = settings.WEBSOCKET_ENDPOINT
        if is_forbidden_vendor_endpoint(url):
            # The relay authenticates with the org's system API key and secret.
            logger.error("websocket_relay_refused_vendor_endpoint")
            return {
                "status": "error",
                "message": "WEBSOCKET_ENDPOINT must point at this install's backend",
            }
        payload = {"message": message, "send_to_uuid": send_to_uuid}
        if send_to_uuid:
            payload["uuid"] = str(uuid) if uuid is not None else None

        # Key lookup/creation is part of best-effort notification too: failures
        # must not replace an already-computed usage refusal or streaming result.
        try:
            org_key = OrgApiKey.no_workspace_objects.get(
                organization__id=organization_id, type="system", enabled=True
            )
        except OrgApiKey.DoesNotExist:
            org_key = OrgApiKey.no_workspace_objects.create(
                organization_id=organization_id, type="system"
            )
        if not org_key.api_key or not org_key.secret_key:
            logger.error("websocket_relay_missing_credentials")
            return {"status": "error", "message": "Failed to generate API keys"}
        response = requests.post(
            url,
            json=payload,
            timeout=60,
            headers={
                "X-Api-Key": org_key.api_key,
                "X-Secret-Key": org_key.secret_key,
            },
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.JSONDecodeError:
        logger.error(
            "websocket_relay_invalid_response",
            status_code=response.status_code if response is not None else None,
        )
        return {"status": "error", "message": "Invalid response format"}
    except Exception as exc:
        logger.error(
            "websocket_relay_failed",
            error_type=type(exc).__name__,
            status_code=response.status_code if response is not None else None,
            validation_fields=_relay_validation_fields(response),
        )
        return {"status": "error", "message": "Unable to send message to Websocket"}
