import base64
import json
import logging
import time
import uuid
from typing import Any

import requests

from integrations.services.base import BaseIntegrationService, register_service

logger = logging.getLogger(__name__)


class MixpanelService(BaseIntegrationService):
    """Mixpanel API client for exporting Agentcc LLM usage events.

    Credentials dict shape:
        project_token (str): Mixpanel project token (required)
        api_secret (str): Legacy Mixpanel API secret (optional)

    Both credential forms use authenticated /import with strict validation.
    /track cannot establish credential validity or whole-batch acceptance.
    """

    TIMEOUT = 15
    BATCH_SIZE = 500
    TRACK_URL = "https://api.mixpanel.com/track"
    IMPORT_URL = "https://api.mixpanel.com/import"

    def validate_credentials(
        self,
        host_url: str,
        credentials: dict,
        ca_certificate: str | None = None,
    ) -> dict:
        if not credentials.get("project_token"):
            return {"valid": False, "error": "Project token is required."}

        try:
            # Unlike /track, /import authenticates the configured credential.
            # Keep the existing connection-test event, using the export path so
            # validation and subsequent exports check the same authentication.
            self.export_events(
                credentials,
                [
                    {
                        "event": "$agentcc_connection_test",
                        "distinct_id": "agentcc_test",
                        "time": int(time.time()),
                        "properties": {"ip": 0},
                    }
                ],
            )
            return {"valid": True, "projects": [], "total_traces": 0}
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
        except (requests.exceptions.HTTPError, RuntimeError):
            return {
                "valid": False,
                "error": "Mixpanel did not acknowledge the connection test. "
                "Check your credentials and try again.",
            }
        except Exception:
            logger.exception("Unexpected error validating Mixpanel credentials")
            return {
                "valid": False,
                "error": "Mixpanel validation failed. Please try again.",
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

    def export_events(self, credentials: dict, events: list[dict]) -> dict:
        """Return a sent count only after every event is acknowledged.

        /import supports project-token Basic auth as well as legacy secrets.
        Strict validation can ingest part of a batch and return HTTP 400. Raise
        on any rejection or ambiguous acknowledgement so the sync activity
        retains its checkpoint. Stable insert IDs deduplicate replayed events.
        """
        if not events:
            return {"sent": 0}

        credential = credentials.get("api_secret") or credentials.get("project_token")
        if not credential:
            raise ValueError("Mixpanel project token is required.")
        auth_str = base64.b64encode(f"{credential}:".encode()).decode()

        for offset in range(0, len(events), self.BATCH_SIZE):
            batch = [
                self._format_event(event)
                for event in events[offset : offset + self.BATCH_SIZE]
            ]
            resp = requests.post(
                self.IMPORT_URL,
                params={"strict": "1"},
                json=batch,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Basic {auth_str}",
                },
                timeout=self.TIMEOUT,
            )
            self._check_acknowledgement(resp, len(batch))

        return {"sent": len(events)}

    @staticmethod
    def _format_event(event: dict) -> dict:
        payload = {
            "event": event.get("event", "agentcc_request"),
            "properties": {
                "distinct_id": event.get("distinct_id", "agentcc-gateway"),
                "time": event.get("time", int(time.time())),
                **event.get("properties", {}),
            },
        }
        properties = payload["properties"]
        # Authentication belongs in the header, not exported analytics data.
        properties.pop("token", None)
        if "$insert_id" not in properties:
            identity = properties.get("request_id") or json.dumps(
                payload, sort_keys=True, separators=(",", ":")
            )
            properties["$insert_id"] = str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"future-agi:mixpanel:{identity}")
            )
        return payload

    @staticmethod
    def _check_acknowledgement(resp: requests.Response, expected: int) -> None:
        if resp.status_code == 400:
            # The accepted subset is already ingested, not rolled back. Never
            # retry validation failures here, or advance the export checkpoint.
            # Do not expose vendor error strings: they can contain event data.
            try:
                body = resp.json()
            except ValueError:
                body = None
            accepted = (
                body.get("num_records_imported") if isinstance(body, dict) else None
            )
            detail = (
                f"accepted {accepted} of {expected}"
                if type(accepted) is int and 0 <= accepted <= expected
                else "accepted count unknown"
            )
            raise requests.exceptions.HTTPError(
                f"Mixpanel rejected the export batch (HTTP 400; {detail}). "
                "Check event data before retrying.",
                response=resp,
            )

        resp.raise_for_status()
        try:
            body = resp.json()
        except ValueError:
            raise RuntimeError("Invalid Mixpanel import acknowledgement.") from None
        if (
            resp.status_code != 200
            or not isinstance(body, dict)
            or body.get("code") != 200
            or body.get("status") != "OK"
            or type(body.get("num_records_imported")) is not int
            or body["num_records_imported"] != expected
            or body.get("error")
            or body.get("failed_records")
        ):
            raise RuntimeError("Incomplete or invalid Mixpanel import acknowledgement.")


# Self-register on module import
_mixpanel_service = MixpanelService()
register_service("mixpanel", _mixpanel_service)
