"""Platform control-plane client for an ALK sandbox provider."""

from __future__ import annotations

import os
from typing import Any

import requests
import structlog

from simulate.services.harness_refusals import refusal_sentence

logger = structlog.get_logger(__name__)


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
        return self._request("POST", "/v1/jobs", json=payload, connect=True)

    def preflight(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/preflight", json=payload, connect=True)

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
            connect=True,
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
            connect=True,
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
        connect: bool = False,
        **kwargs: Any,
    ) -> Any:
        """``connect`` marks the connect path (submit, preflight, the two uploads): only its
        refusals become the platform's sentences (ADR-010). Elsewhere a job's read, cancel or
        adjust refusal is fixed kit text that is already right, and is kept."""
        headers = dict(kwargs.pop("headers", {}))
        sends_token = bool(authenticated and self.token)
        if sends_token:
            # Checked before the call: the HTTP stack's own errors for a bad header value
            # quote the header, and a token with a trailing newline gets past requests' check.
            if not _valid_header_token(self.token):
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
            if response.status_code < 500:
                raise HarnessSandboxRejected(
                    response.status_code,
                    _refusal_message(detail) if connect else _kit_text(detail),
                )
            # The sandbox has no token configured: our deployment's fault, not an outage.
            if str(detail or "").strip() == "sandbox_token_not_configured":
                raise HarnessSandboxUnavailable(
                    refusal_sentence("sandbox_token_not_configured")
                )
            # A refusal the sandbox's own configuration causes (kit #353): our bug, not
            # an outage, so it reads as the shared our-side sentence with its code.
            configuration = _configuration_refusals(detail)
            if configuration:
                raise HarnessSandboxUnavailable(_refusal_message(configuration))
            # Not a refusal: the sandbox can't do it right now. The page shows a fixed
            # sentence; the kit's own text goes to the server log only (ADR-010).
            logger.warning(
                "harness_sandbox_unavailable",
                status_code=response.status_code,
                kit_detail=str(detail)[:500],
            )
            raise HarnessSandboxUnavailable(
                f"The agent sandbox couldn't take this right now "
                f"(HTTP {response.status_code}). Try again in a few minutes."
            )
        return response.json()


def _kit_text(detail: Any) -> str:
    """The sandbox's own refusal text, for a refusal off the connect path: each message of a
    validation list without pydantic's "Value error, " prefix, or the detail as it is."""
    if not isinstance(detail, list):
        return str(detail or "").strip() or "unknown error"
    messages = []
    for error in detail:
        message = error.get("msg") if isinstance(error, dict) else error
        message = str(message or "").strip().removeprefix("Value error, ").strip()
        if message:
            messages.append(message.rstrip("."))
    return ". ".join(messages) + "." if messages else "unknown error"


def _refusal_message(detail: Any) -> str:
    """The sandbox's refusal as text for the page.

    Its request validation answers with FastAPI's list of errors; their
    messages are read, without pydantic's "Value error, " prefix. Each becomes
    the platform's own sentence (``harness_refusals``, ADR-010): the kit's text
    never reaches the page, so a sentence two errors share is shown once.
    """
    if not isinstance(detail, list):
        return refusal_sentence(str(detail or "").strip())
    sentences: list[str] = []
    for error in detail:
        message = error.get("msg") if isinstance(error, dict) else error
        message = str(message or "").strip().removeprefix("Value error, ").strip()
        if message:
            sentence = refusal_sentence(message).rstrip(".")
            if sentence not in sentences:
                sentences.append(sentence)
    return ". ".join(sentences) + "." if sentences else refusal_sentence("")


def _configuration_refusals(detail: Any) -> list[dict]:
    """The entries of a 5xx body the sandbox marks as its own configuration refusals."""
    if not isinstance(detail, list):
        return []
    return [
        error
        for error in detail
        if isinstance(error, dict) and error.get("type") == "sandbox_configuration"
    ]


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
