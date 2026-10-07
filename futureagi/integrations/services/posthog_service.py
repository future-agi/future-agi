import logging
from typing import Any, Optional

import requests

from integrations.services.base import BaseIntegrationService, register_service

logger = logging.getLogger(__name__)

DEFAULT_POSTHOG_HOST = "https://us.i.posthog.com"


class PostHogService(BaseIntegrationService):
    """PostHog API client for exporting Agentcc LLM usage events.

    Credentials dict shape:
        api_key (str): PostHog project API key (required)
    Host URL:
        PostHog cloud: https://us.i.posthog.com or https://eu.i.posthog.com
        Self-hosted: user-provided host URL
    """

    TIMEOUT = 15
    default_host_url = DEFAULT_POSTHOG_HOST

    def validate_credentials(
        self,
        host_url: str,
        credentials: dict,
        ca_certificate: Optional[str] = None,
    ) -> dict:
        try:
            api_key = credentials.get("api_key", "")
            if not api_key:
                return {"valid": False, "error": "API key is required."}

            host = (host_url or DEFAULT_POSTHOG_HOST).rstrip("/")
            url = f"{host}/decide/?v=3"

            resp = requests.post(
                url,
                json={
                    "api_key": api_key,
                    "distinct_id": "agentcc_connection_test",
                },
                timeout=self.TIMEOUT,
            )

            if resp.status_code == 200:
                return {
                    "valid": True,
                    "projects": [],
                    "total_traces": 0,
                }

            if resp.status_code in (401, 403):
                return {
                    "valid": False,
                    "error": "Invalid API key. Please check your PostHog project API key.",
                }

            return {
                "valid": False,
                "error": f"Unexpected response from PostHog (HTTP {resp.status_code}).",
            }

        except requests.exceptions.ConnectionError:
            return {
                "valid": False,
                "error": "Could not reach PostHog API. Check your host URL and network.",
            }
        except requests.exceptions.Timeout:
            return {
                "valid": False,
                "error": "Connection to PostHog timed out. Please try again.",
            }
        except Exception as e:
            logger.exception("Unexpected error validating PostHog credentials")
            return {"valid": False, "error": f"Validation failed: {str(e)}"}

    def fetch_traces(
        self,
        host_url: str,
        credentials: dict,
        from_timestamp: Optional[str] = None,
        to_timestamp: Optional[str] = None,
        page: int = 1,
        limit: int = 100,
        ca_certificate: Optional[str] = None,
    ) -> dict:
        """Not used — PostHog is an export target, not a trace source."""
        return {"traces": [], "has_more": False, "next_page": 1, "total_items": 0}

    def fetch_trace_detail(
        self,
        host_url: str,
        credentials: dict,
        trace_id: str,
        ca_certificate: Optional[str] = None,
    ) -> dict[str, Any]:
        """Not used — PostHog is an export target, not a trace source."""
        return {}

    # --- Export methods (called by sync tasks) ---

    def export_events(
        self, host_url: str, credentials: dict, events: list[dict]
    ) -> dict:
        """Send events to PostHog Batch API.

        Args:
            host_url: PostHog host URL.
            credentials: PostHog credentials with api_key.
            events: List of event dicts, each with: event, properties, timestamp, distinct_id.

        Returns:
            dict with status and count of events sent.
        """
        if not events:
            return {"sent": 0}

        api_key = credentials.get("api_key", "")
        host = (host_url or DEFAULT_POSTHOG_HOST).rstrip("/")
        url = f"{host}/batch/"

        batch = []
        for event in events:
            batch.append(
                {
                    "event": event.get("event", "agentcc_request"),
                    "properties": {
                        "token": api_key,
                        **event.get("properties", {}),
                    },
                    "timestamp": event.get("timestamp"),
                    "distinct_id": event.get("distinct_id", "agentcc-gateway"),
                }
            )

        resp = requests.post(
            url,
            json={"api_key": api_key, "batch": batch},
            headers={"Content-Type": "application/json"},
            timeout=self.TIMEOUT,
        )
        resp.raise_for_status()
        return {"sent": len(batch)}


# Self-register on module import
_posthog_service = PostHogService()
register_service("posthog", _posthog_service)
