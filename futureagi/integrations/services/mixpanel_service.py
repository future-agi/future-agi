import base64
import hashlib
import logging
import time
import uuid
from typing import Any

import requests

from integrations.services.base import BaseIntegrationService, register_service
from integrations.services.endpoints import endpoint_override
from integrations.services.export_failures import validation_failure

logger = logging.getLogger(__name__)


class MixpanelRejected(Exception):
    """Mixpanel accepted none of the events in a batch."""


def _insert_id(request_id: str | None) -> str:
    """The event's $insert_id: Mixpanel deduplicates on it.

    Derived from the request id, so the same request log sent twice (each
    sync overlaps the previous one by a minute) counts once; at most 36
    alphanumeric characters, as Mixpanel requires.
    """
    if not request_id:
        return uuid.uuid4().hex
    return hashlib.sha256(str(request_id).encode()).hexdigest()[:32]


def _basic_auth(api_secret: str) -> str:
    """The project secret as the Basic auth username, with an empty password."""
    return "Basic " + base64.b64encode(f"{api_secret}:".encode()).decode()


class MixpanelService(BaseIntegrationService):
    """Mixpanel API client for exporting Agentcc LLM usage events.

    Credentials dict shape:
        project_token (str): Mixpanel project token (required)
        api_secret (str): Mixpanel project secret (optional). With it, events
            go to /import (https://docs.mixpanel.com/reference/import-events),
            strict, which accepts events of any age; without it, to /track
            (https://docs.mixpanel.com/reference/track-event), which accepts
            the last 5 days.
    """

    TIMEOUT = 15
    API_HOST = "https://api.mixpanel.com"

    def _url(self, path: str) -> str:
        """The Mixpanel ingestion URL for `path`, on the real host or the override."""
        return (
            f"{endpoint_override('INTEGRATIONS_MIXPANEL_URL') or self.API_HOST}{path}"
        )

    def validate_credentials(
        self,
        host_url: str,
        credentials: dict,
        ca_certificate: str | None = None,
    ) -> dict:
        try:
            project_token = credentials.get("project_token", "")
            if not project_token:
                return {"valid": False, "error": "Project token is required."}

            # A test event through the API the export will use.
            payload = [
                {
                    "event": "$agentcc_connection_test",
                    "properties": {
                        "token": project_token,
                        "distinct_id": "agentcc_test",
                        "time": int(time.time()),
                        "$insert_id": _insert_id(None),
                    },
                }
            ]
            api_secret = credentials.get("api_secret", "")
            if api_secret:
                return self._validate_import(api_secret, payload)

            resp = requests.post(
                self._url("/track"),
                json=payload,
                timeout=self.TIMEOUT,
            )

            # Mixpanel /track returns "1" on success, "0" on failure
            if resp.status_code == 200 and resp.text.strip() == "1":
                return {
                    "valid": True,
                    "projects": [],
                    "total_traces": 0,
                }

            if resp.status_code == 200 and resp.text.strip() == "0":
                return {
                    "valid": False,
                    "error": "Invalid project token. Please check your Mixpanel project token.",
                }

            return {
                "valid": False,
                "error": f"Unexpected response from Mixpanel (HTTP {resp.status_code}).",
            }

        except requests.exceptions.ConnectionError:
            return {
                "valid": False,
                "error": "Could not reach Mixpanel API. Check your network.",
            }
        except requests.exceptions.Timeout:
            return {
                "valid": False,
                "error": "Connection to Mixpanel timed out. Please try again.",
            }
        except Exception as e:
            return validation_failure("mixpanel", e, logger)

    def _validate_import(self, api_secret: str, payload: list[dict]) -> dict:
        """The test event through /import, authenticated by the project secret."""
        resp = requests.post(
            self._url("/import"),
            params={"strict": "1"},
            json=payload,
            headers={"Authorization": _basic_auth(api_secret)},
            timeout=self.TIMEOUT,
        )
        if resp.status_code == 200:
            return {"valid": True, "projects": [], "total_traces": 0}
        if resp.status_code in (401, 403):
            return {
                "valid": False,
                "error": "Invalid API secret. Please check your Mixpanel project secret.",
            }
        return {
            "valid": False,
            "error": f"Unexpected response from Mixpanel /import (HTTP {resp.status_code}).",
        }

    def fetch_traces(
        self,
        host_url: str,
        credentials: dict,
        from_timestamp: str | None = None,
        to_timestamp: str | None = None,
        page: int = 1,
        limit: int = 100,
        ca_certificate: str | None = None,
    ) -> dict:
        """Not used — Mixpanel is an export target, not a trace source."""
        return {"traces": [], "has_more": False, "next_page": 1, "total_items": 0}

    def fetch_trace_detail(
        self,
        host_url: str,
        credentials: dict,
        trace_id: str,
        ca_certificate: str | None = None,
    ) -> dict[str, Any]:
        """Not used — Mixpanel is an export target, not a trace source."""
        return {}

    # --- Export methods (called by sync tasks) ---

    def export_events(self, credentials: dict, events: list[dict]) -> dict:
        """Send events to Mixpanel.

        Uses /import (strict, with the project secret as Basic auth) if
        api_secret is provided, otherwise /track (with project_token in each
        event). Raises when Mixpanel takes none of them: /import answers a
        non-2xx status, /track answers anything but 1.

        Args:
            credentials: Mixpanel credentials with project_token and optional api_secret.
            events: List of event dicts with: event, properties, timestamp, distinct_id.

        Returns:
            dict with count of events sent.
        """
        if not events:
            return {"sent": 0}

        project_token = credentials.get("project_token", "")
        api_secret = credentials.get("api_secret", "")

        batch = []
        for event in events:
            properties = event.get("properties", {})
            batch.append(
                {
                    "event": event.get("event", "agentcc_request"),
                    "properties": {
                        "token": project_token,
                        "distinct_id": event.get("distinct_id", "agentcc-gateway"),
                        "time": event.get("time", int(time.time())),
                        "$insert_id": _insert_id(properties.get("request_id")),
                        **properties,
                    },
                }
            )

        if api_secret:
            # /import accepts events of any age; strict answers 400 when any
            # event fails Mixpanel's validation.
            resp = requests.post(
                self._url("/import"),
                params={"strict": "1"},
                json=batch,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": _basic_auth(api_secret),
                },
                timeout=self.TIMEOUT,
            )
            resp.raise_for_status()
            return {"sent": len(batch)}

        # /track with the token in each event: it answers 1 when it took the
        # batch and 0 when it took none of it.
        resp = requests.post(
            self._url("/track"),
            json=batch,
            timeout=self.TIMEOUT,
        )
        resp.raise_for_status()
        answer = resp.text.strip()
        if answer != "1":
            raise MixpanelRejected(
                f"Mixpanel /track accepted none of the events in a batch of "
                f"{len(batch)} (it answered {answer[:20]!r})."
            )
        return {"sent": len(batch)}


# Self-register on module import
_mixpanel_service = MixpanelService()
register_service("mixpanel", _mixpanel_service)
