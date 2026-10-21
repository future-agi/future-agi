import logging
import os
import tempfile
from typing import Any, Optional

import requests

from agentcc.services.url_safety import (
    WEBHOOK_PRIVATE_URL_ERROR,
    ensure_public_http_url,
)
from integrations.services.base import BaseIntegrationService, register_service

logger = logging.getLogger(__name__)


class LangfuseService(BaseIntegrationService):
    """Langfuse API client implementation.

    Langfuse uses project-scoped API keys with HTTP Basic Auth:
    - Username = Public Key (pk-lf-...)
    - Password = Secret Key (sk-lf-...)
    """

    TIMEOUT = 30  # seconds

    # API paths
    API_PATH_PROJECTS = "/api/public/projects"
    API_PATH_TRACES = "/api/public/traces"

    def _get_auth(self, credentials: dict) -> tuple[str, str]:
        return (credentials["public_key"], credentials["secret_key"])

    def _make_request(
        self,
        method: str,
        host_url: str,
        path: str,
        credentials: dict,
        ca_certificate: Optional[str] = None,
        params: Optional[dict] = None,
    ) -> requests.Response:
        url = f"{host_url.rstrip('/')}{path}"
        ensure_public_http_url(
            host_url, WEBHOOK_PRIVATE_URL_ERROR, allow_unresolvable=True
        )
        verify: Any = True
        tmp_path = None

        if ca_certificate and ca_certificate.strip():
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False)
            tmp.write(ca_certificate)
            tmp.close()
            tmp_path = tmp.name
            verify = tmp_path

        try:
            response = requests.request(
                method=method,
                url=url,
                auth=self._get_auth(credentials),
                params=params,
                timeout=self.TIMEOUT,
                verify=verify,
            )
            return response
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def validate_credentials(
        self,
        host_url: str,
        credentials: dict,
        ca_certificate: Optional[str] = None,
    ) -> dict:
        try:
            # Step 1: Validate by fetching project info
            resp = self._make_request(
                "GET",
                host_url,
                self.API_PATH_PROJECTS,
                credentials,
                ca_certificate,
            )

            if resp.status_code == 401:
                return {
                    "valid": False,
                    "error": "Invalid credentials. Please check your public key and secret key.",
                }

            if resp.status_code != 200:
                return {
                    "valid": False,
                    "error": f"Unexpected response from Langfuse (HTTP {resp.status_code}).",
                }

            project_data = resp.json()
            raw_projects = project_data.get("data", [])

            projects = [
                {"id": p.get("id", ""), "name": p.get("name", "Unknown")}
                for p in raw_projects
            ]

            # Step 2: Get aggregate trace count for backfill estimation
            total_traces = 0
            try:
                traces_resp = self._make_request(
                    "GET",
                    host_url,
                    self.API_PATH_TRACES,
                    credentials,
                    ca_certificate,
                    params={"limit": 1},
                )
                if traces_resp.status_code == 200:
                    meta = traces_resp.json().get("meta", {})
                    total_traces = meta.get("totalItems", 0)
            except Exception:
                logger.warning(
                    "Failed to fetch trace count during validation for %s",
                    host_url,
                )

            return {
                "valid": True,
                "projects": projects,
                "total_traces": total_traces,
            }

        except ValueError as exc:
            return {"valid": False, "error": str(exc)}
        except requests.exceptions.ConnectionError:
            return {
                "valid": False,
                "error": f"Could not reach Langfuse at {host_url}. Verify the host URL.",
            }
        except requests.exceptions.Timeout:
            return {
                "valid": False,
                "error": "Connection timed out. Please try again.",
            }
        except Exception as e:
            logger.exception("Unexpected error validating Langfuse credentials")
            return {
                "valid": False,
                "error": f"Validation failed: {str(e)}",
            }

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
        params: dict[str, Any] = {"page": page, "limit": limit}
        if from_timestamp:
            params["fromTimestamp"] = from_timestamp
        if to_timestamp:
            params["toTimestamp"] = to_timestamp

        resp = self._make_request(
            "GET",
            host_url,
            self.API_PATH_TRACES,
            credentials,
            ca_certificate,
            params=params,
        )
        resp.raise_for_status()

        data = resp.json()
        traces = data.get("data", [])
        meta = data.get("meta", {})
        total_items = meta.get("totalItems", 0)
        total_pages = meta.get("totalPages", 1)
        current_page = meta.get("page", page)

        return {
            "traces": traces,
            "has_more": current_page < total_pages,
            "next_page": current_page + 1,
            "total_items": total_items,
        }

    def fetch_trace_detail(
        self,
        host_url: str,
        credentials: dict,
        trace_id: str,
        ca_certificate: Optional[str] = None,
    ) -> dict[str, Any]:
        resp = self._make_request(
            "GET",
            host_url,
            f"{self.API_PATH_TRACES}/{trace_id}",
            credentials,
            ca_certificate,
        )
        resp.raise_for_status()
        return resp.json()


# Self-register on module import
_langfuse_service = LangfuseService()
register_service("langfuse", _langfuse_service)
