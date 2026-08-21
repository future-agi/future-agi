"""Platform control-plane client for an ALK sandbox provider."""

from __future__ import annotations

import os
from typing import Any

import requests


class HarnessSandboxUnavailable(RuntimeError):
    pass


class HarnessSandboxClient:
    """Small provider-neutral HTTP client.

    Development points this at ALK's local-process provider. Hosted deployments point the same
    contract at the managed sandbox service; no platform view or UI changes are required.
    """

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = (
            base_url
            or os.getenv("ALK_HARNESS_SANDBOX_URL")
            or "http://host.docker.internal:8788"
        ).rstrip("/")
        self.token = token if token is not None else os.getenv("ALK_HARNESS_SANDBOX_TOKEN")

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health", authenticated=False)

    def list_jobs(self) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/jobs")

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/jobs", json=payload)

    def get(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/jobs/{job_id}")

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/jobs/{job_id}/cancel")

    def _request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        **kwargs: Any,
    ) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        if authenticated and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = requests.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                timeout=(5, 30),
                **kwargs,
            )
        except requests.RequestException as exc:
            raise HarnessSandboxUnavailable(
                f"ALK sandbox is unavailable at {self.base_url}: {exc}"
            ) from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except (ValueError, AttributeError):
                detail = response.text[:500]
            raise HarnessSandboxUnavailable(
                f"ALK sandbox returned {response.status_code}: {detail or 'unknown error'}"
            )
        return response.json()


__all__ = ["HarnessSandboxClient", "HarnessSandboxUnavailable"]
