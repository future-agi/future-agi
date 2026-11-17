import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

import requests

from integrations.services.base import BaseIntegrationService, register_service
from integrations.services.export_failures import validation_failure

logger = logging.getLogger(__name__)


class PagerDutyService(BaseIntegrationService):
    """PagerDuty Events API v2 client for routing Agentcc alerts.

    Credentials dict shape:
        routing_key (str): PagerDuty Events API v2 integration/routing key (required)
    """

    TIMEOUT = 15
    EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"
    CHANGE_URL = "https://events.pagerduty.com/v2/change/enqueue"

    def validate_credentials(
        self,
        host_url: str,
        credentials: dict,
        ca_certificate: str | None = None,
    ) -> dict:
        try:
            routing_key = credentials.get("routing_key", "")
            if not routing_key:
                return {"valid": False, "error": "Routing key is required."}

            # Send a change event as a lightweight validation
            payload = {
                "routing_key": routing_key,
                "payload": {
                    "summary": "Agentcc connection test",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "source": "agentcc-gateway",
                },
            }

            resp = requests.post(
                self.CHANGE_URL,
                json=payload,
                timeout=self.TIMEOUT,
            )

            if resp.status_code == 202:
                return {
                    "valid": True,
                    "projects": [],
                    "total_traces": 0,
                }

            if resp.status_code in (400, 429):
                return {
                    "valid": False,
                    "error": f"PagerDuty refused validation (HTTP {resp.status_code}). Check the routing key or retry later.",
                }

            return {
                "valid": False,
                "error": f"Unexpected response from PagerDuty (HTTP {resp.status_code}).",
            }

        except requests.exceptions.ConnectionError:
            return {
                "valid": False,
                "error": "Could not reach PagerDuty API. Check your network.",
            }
        except requests.exceptions.Timeout:
            return {
                "valid": False,
                "error": "Connection to PagerDuty timed out. Please try again.",
            }
        except Exception as e:
            return validation_failure("pagerduty", e, logger)

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
        """Not used — PagerDuty is an alert target, not a trace source."""
        return {"traces": [], "has_more": False, "next_page": 1, "total_items": 0}

    def fetch_trace_detail(
        self,
        host_url: str,
        credentials: dict,
        trace_id: str,
        ca_certificate: str | None = None,
    ) -> dict[str, Any]:
        """Not used — PagerDuty is an alert target, not a trace source."""
        return {}

    # --- Alert methods (called by sync tasks / webhook handlers) ---

    def trigger_alert(
        self,
        credentials: dict,
        alert_type: str,
        summary: str,
        severity: str = "warning",
        details: dict | None = None,
        org_id: str = "",
    ) -> dict:
        """Trigger a PagerDuty alert via Events API v2.

        Args:
            credentials: PagerDuty credentials with routing_key.
            alert_type: Type of alert (e.g. "error_rate_spike", "cost_threshold").
            summary: Human-readable alert summary.
            severity: One of "critical", "error", "warning", "info".
            details: Custom details dict.
            org_id: Organization ID for dedup key generation.

        Returns:
            dict with status and dedup_key.
        """
        routing_key = credentials.get("routing_key", "")
        dedup_key = hashlib.sha256(f"{alert_type}:{org_id}".encode()).hexdigest()[:32]

        payload = {
            "routing_key": routing_key,
            "event_action": "trigger",
            "dedup_key": dedup_key,
            "payload": {
                "summary": summary,
                "source": "agentcc-gateway",
                "severity": severity,
                "custom_details": details or {},
            },
        }

        resp = requests.post(self.EVENTS_URL, json=payload, timeout=self.TIMEOUT)
        resp.raise_for_status()
        return {"dedup_key": dedup_key, "status": "triggered"}

    def resolve_alert(
        self,
        credentials: dict,
        alert_type: str,
        org_id: str = "",
    ) -> dict:
        """Resolve a previously triggered PagerDuty alert.

        Args:
            credentials: PagerDuty credentials with routing_key.
            alert_type: Same alert_type used to trigger.
            org_id: Same org_id used to trigger.

        Returns:
            dict with status.
        """
        routing_key = credentials.get("routing_key", "")
        dedup_key = hashlib.sha256(f"{alert_type}:{org_id}".encode()).hexdigest()[:32]

        payload = {
            "routing_key": routing_key,
            "event_action": "resolve",
            "dedup_key": dedup_key,
        }

        resp = requests.post(self.EVENTS_URL, json=payload, timeout=self.TIMEOUT)
        resp.raise_for_status()
        return {"dedup_key": dedup_key, "status": "resolved"}


# Self-register on module import
_pagerduty_service = PagerDutyService()
register_service("pagerduty", _pagerduty_service)
