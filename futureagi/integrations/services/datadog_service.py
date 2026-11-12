import logging
from typing import Any

import requests

from integrations.services.base import BaseIntegrationService, register_service
from integrations.services.endpoints import endpoint_override
from integrations.services.export_failures import validation_failure

logger = logging.getLogger(__name__)

# Datadog site region → API domain mapping
DATADOG_SITES = {
    "us1": "datadoghq.com",
    "us3": "us3.datadoghq.com",
    "us5": "us5.datadoghq.com",
    "eu1": "datadoghq.eu",
    "ap1": "ap1.datadoghq.com",
    "us1-fed": "ddog-gov.com",
}


class DatadogService(BaseIntegrationService):
    """Datadog API client for exporting Agentcc metrics, traces, and logs.

    Credentials dict shape:
        api_key (str): Datadog API key (required)
        site (str): Datadog site region key, e.g. "us1" (required)
        app_key (str): Datadog Application key (optional)
    """

    TIMEOUT = 15

    def _get_site_domain(self, credentials: dict) -> str:
        site = credentials.get("site", "us1")
        return DATADOG_SITES.get(site, "datadoghq.com")

    def api_host(self, credentials: dict) -> str:
        """The Datadog API host of the connection's site."""
        return f"https://api.{self._get_site_domain(credentials)}"

    def _api_base(self, credentials: dict) -> str:
        """The Datadog API host of the connection's site, or the override."""
        return endpoint_override("INTEGRATIONS_DATADOG_URL") or self.api_host(
            credentials
        )

    def _logs_base(self, credentials: dict) -> str:
        """The Datadog log intake host of the connection's site, or the override."""
        return endpoint_override("INTEGRATIONS_DATADOG_URL") or (
            f"https://http-intake.logs.{self._get_site_domain(credentials)}"
        )

    def validate_credentials(
        self,
        host_url: str,
        credentials: dict,
        ca_certificate: str | None = None,
    ) -> dict:
        try:
            api_key = credentials.get("api_key", "")
            if not api_key:
                return {"valid": False, "error": "API key is required."}

            url = f"{self._api_base(credentials)}/api/v1/validate"

            headers = {"DD-API-KEY": api_key}
            app_key = credentials.get("app_key", "")
            if app_key:
                headers["DD-APPLICATION-KEY"] = app_key

            resp = requests.get(url, headers=headers, timeout=self.TIMEOUT)

            if resp.status_code == 200:
                return {
                    "valid": True,
                    "projects": [],
                    "total_traces": 0,
                }

            if resp.status_code == 403:
                return {
                    "valid": False,
                    "error": "Invalid API key. Please check your Datadog API key.",
                }

            return {
                "valid": False,
                "error": f"Unexpected response from Datadog (HTTP {resp.status_code}).",
            }

        except requests.exceptions.ConnectionError:
            return {
                "valid": False,
                "error": "Could not reach Datadog API. Check your network and site region.",
            }
        except requests.exceptions.Timeout:
            return {
                "valid": False,
                "error": "Connection to Datadog timed out. Please try again.",
            }
        except Exception as e:
            return validation_failure("datadog", e, logger)

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
        """Not used for Datadog — Datadog is an export target, not a trace source."""
        return {"traces": [], "has_more": False, "next_page": 1, "total_items": 0}

    def fetch_trace_detail(
        self,
        host_url: str,
        credentials: dict,
        trace_id: str,
        ca_certificate: str | None = None,
    ) -> dict[str, Any]:
        """Not used for Datadog — Datadog is an export target, not a trace source."""
        return {}

    # --- Export methods (called by sync tasks) ---

    def export_logs(self, credentials: dict, logs: list[dict]) -> dict:
        """Send logs to Datadog Log Intake API.

        Args:
            credentials: Datadog credentials with api_key and site.
            logs: List of log dicts, each with: message, service, ddtags, etc.

        Returns:
            dict with status and count of logs sent.
        """
        if not logs:
            return {"sent": 0}

        api_key = credentials.get("api_key", "")
        url = f"{self._logs_base(credentials)}/api/v2/logs"

        headers = {
            "DD-API-KEY": api_key,
            "Content-Type": "application/json",
        }

        # Format logs for Datadog
        dd_logs = []
        for log in logs:
            dd_log = {
                "ddsource": "agentcc",
                "service": "agentcc-gateway",
                "hostname": "agentcc",
                "message": log.get("message", ""),
                "ddtags": log.get("ddtags", ""),
            }
            # Merge optional fields
            for key in ("status", "date"):
                if log.get(key):
                    dd_log[key] = log[key]
            # Nest custom attributes under the log for Datadog faceting.
            if log.get("attributes"):
                dd_log["attributes"] = log["attributes"]
            dd_logs.append(dd_log)

        resp = requests.post(url, json=dd_logs, headers=headers, timeout=self.TIMEOUT)
        resp.raise_for_status()
        return {"sent": len(dd_logs)}

    def export_metrics(self, credentials: dict, series: list[dict]) -> dict:
        """Send custom metrics to Datadog Metrics API.

        Args:
            credentials: Datadog credentials with api_key and site.
            series: List of metric series dicts in Datadog v2 format.

        Returns:
            dict with status.
        """
        if not series:
            return {"sent": 0}

        api_key = credentials.get("api_key", "")
        url = f"{self._api_base(credentials)}/api/v2/series"

        headers = {
            "DD-API-KEY": api_key,
            "Content-Type": "application/json",
        }

        resp = requests.post(
            url, json={"series": series}, headers=headers, timeout=self.TIMEOUT
        )
        resp.raise_for_status()
        return {"sent": len(series)}


# Self-register on module import
_datadog_service = DatadogService()
register_service("datadog", _datadog_service)
