"""Platform control-plane client for an ALK sandbox provider."""

from __future__ import annotations

import os
from typing import Any

import requests


class HarnessSandboxUnavailable(RuntimeError):
    pass


class HarnessSandboxRejected(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code


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
        self.token = (
            token if token is not None else os.getenv("ALK_HARNESS_SANDBOX_TOKEN")
        )

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health", authenticated=False)

    def list_jobs(self) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/jobs")

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/jobs", json=payload)

    def preflight(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/preflight", json=payload)

    def upload_source(
        self, files: list[Any], paths: list[str], name: str
    ) -> dict[str, Any]:
        multipart = [
            (
                "files",
                (
                    str(path).rsplit("/", 1)[-1],
                    uploaded.file,
                    getattr(uploaded, "content_type", None)
                    or "application/octet-stream",
                ),
            )
            for uploaded, path in zip(files, paths, strict=True)
        ]
        data = [("paths", path) for path in paths]
        data.append(("name", name))
        return self._request(
            "POST",
            "/v1/sources",
            files=multipart,
            data=data,
            timeout=(5, 300),
        )

    def upload_secret_file(
        self, uploaded: Any, environment_name: str
    ) -> dict[str, Any]:
        """Transfer a credential file directly to the execution provider.

        The platform does not deserialize, log or place its contents in a JSON payload. The
        provider returns an opaque reference that can be attached to one harness job.
        """
        return self._request(
            "POST",
            "/v1/secret-files",
            files={
                "file": (
                    "credential",
                    uploaded.file,
                    getattr(uploaded, "content_type", None)
                    or "application/octet-stream",
                )
            },
            data={"environment_name": environment_name},
            timeout=(5, 60),
        )

    def get(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/jobs/{job_id}")

    def rerun(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/v1/jobs/{job_id}/rerun", json=payload)

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/jobs/{job_id}/cancel")

    def adjust(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/v1/jobs/{job_id}/adjust", json=payload)

    def _request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        **kwargs: Any,
    ) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        sends_token = bool(authenticated and self.token)
        if sends_token:
            # Checked before the call: the HTTP stack's own errors for a bad header value
            # quote the header, and a token with a trailing newline gets past requests' check.
            if not _valid_header_token(self.token or ""):
                raise HarnessSandboxUnavailable(
                    "ALK sandbox token is not a valid HTTP header value"
                ) from None
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = requests.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                timeout=kwargs.pop("timeout", (5, 30)),
                **kwargs,
            )
        except requests.RequestException as exc:
            raise HarnessSandboxUnavailable(
                f"ALK sandbox is unavailable at {self.base_url}: {exc}"
            ) from exc
        if response.status_code == 401:
            # The frontend treats any 401 as an expired session, so a token
            # mismatch with the sandbox must reach the browser as a 503. A 403 is the
            # sandbox's own refusal (a path outside its allowed roots) and passes through.
            if sends_token:
                reason = "rejected the platform's token"
            elif authenticated:
                reason = "requires a token, and ALK_HARNESS_SANDBOX_TOKEN is not set"
            else:
                reason = "refused an unauthenticated request"
            raise HarnessSandboxUnavailable(
                f"ALK sandbox {reason} ({response.status_code})"
            )
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except (ValueError, AttributeError):
                detail = response.text[:500]
            message = _refusal_message(detail) or "unknown error"
            if response.status_code < 500:
                raise HarnessSandboxRejected(response.status_code, message)
            raise HarnessSandboxUnavailable(
                f"ALK sandbox returned {response.status_code}: {message}"
            )
        return response.json()


# The sentence the page shows for a code the ALK sandbox refuses a request with.
KIT_REFUSALS = {
    # Ours to fix, not the user's: the Connect page sends no commit, and nothing
    # resolves the branch to one yet (TH-10408).
    "github_commit_sha_required": (
        "We couldn't find the commit to run for this GitHub source. This is a "
        "problem on our side, not with your repository."
    ),
}


def _refusal_message(detail: Any) -> str:
    """The sandbox's refusal as text for the page.

    Its request validation answers with FastAPI's list of errors; their
    messages are read, without pydantic's "Value error, " prefix, and a known
    code becomes its sentence. Any other detail is used as it is.
    """
    if not isinstance(detail, list):
        text = str(detail or "").strip()
        return KIT_REFUSALS.get(text, text)
    sentences = []
    for error in detail:
        message = error.get("msg") if isinstance(error, dict) else error
        message = str(message or "").strip().removeprefix("Value error, ").strip()
        if message:
            sentences.append(KIT_REFUSALS.get(message, message).rstrip("."))
    return ". ".join(sentences) + "." if sentences else ""


def _valid_header_token(token: str) -> bool:
    """A token that fits in an Authorization header as-is: Latin-1, no line breaks, and no
    leading or trailing space."""
    if token != token.strip() or "\r" in token or "\n" in token:
        return False
    try:
        token.encode("latin-1")
    except UnicodeEncodeError:
        return False
    return True


__all__ = [
    "HarnessSandboxClient",
    "HarnessSandboxRejected",
    "HarnessSandboxUnavailable",
]
