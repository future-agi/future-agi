"""OpenRouter's key provisioning API, behind three live switches.

A provisioning key creates and manages API keys for FutureAGI's upstream
account: ``POST /keys``, then ``GET``, ``PATCH`` and ``DELETE /keys/{hash}``,
under ``https://openrouter.ai/api/v1``. Each org can get its own key with its
own credit limit (``agentcc.services.managed_upstream_keys`` stores it).

Every call is an outward write or read on the live account, so the client
calls the live API only when all three switches are on:

- ``FUTUREAGI_OPENROUTER_PROVISIONING_KEY`` holds the provisioning key;
- ``MANAGED_UPSTREAM_PROVISIONING_LIVE=1``;
- the caller passes ``live=True`` (the command's ``--live``).

Otherwise the client refuses before it opens a connection, with
``LiveProvisioningRefused`` naming the switches that are off.

Tests and local runs use the agentcc-mockup's provisioning routes instead:
``MANAGED_UPSTREAM_PROVISIONING_MOCK_URL`` (for example
``http://127.0.0.1:18911/api/v1``) must be a loopback URL, so it can never
name the live API, and cannot be combined with ``live=True``. The mock keeps
keys per bearer, ``MANAGED_UPSTREAM_PROVISIONING_MOCK_KEY``.
"""

import ipaddress
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from urllib.parse import quote, urlsplit

import requests

LIVE_BASE_URL = "https://openrouter.ai/api/v1"
PROVISIONING_KEY_ENV = "FUTUREAGI_OPENROUTER_PROVISIONING_KEY"
LIVE_ENV = "MANAGED_UPSTREAM_PROVISIONING_LIVE"
MOCK_URL_ENV = "MANAGED_UPSTREAM_PROVISIONING_MOCK_URL"
MOCK_KEY_ENV = "MANAGED_UPSTREAM_PROVISIONING_MOCK_KEY"
DEFAULT_MOCK_BEARER = "mock-provisioning-key"
LIVE_FLAG = "--live"
REQUEST_TIMEOUT_SECONDS = 15

_UNSET = object()


class ProvisioningError(Exception):
    """A provisioning call failed or was refused. Never carries a key."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class LiveProvisioningRefused(ProvisioningError):
    """The live API was asked for without all three switches on."""

    def __init__(self, missing):
        self.missing = tuple(missing)
        super().__init__(
            "Live OpenRouter provisioning needs " + ", ".join(self.missing) + "."
        )


class ProvisioningConfigError(ProvisioningError):
    """The provisioning target is misconfigured."""


@dataclass(frozen=True)
class ProvisioningRequest:
    method: str
    path: str
    body: dict | None = None

    def describe(self, base_url: str) -> str:
        """The request as one line, for a dry run. It holds no secret."""
        line = f"{self.method} {base_url}{self.path}"
        if self.body is not None:
            line += " " + json.dumps(self.body, sort_keys=True)
        return line


@dataclass(frozen=True)
class ProvisionedKey:
    """A key the API just created. ``key`` is returned this once only."""

    key: str = field(repr=False)
    key_hash: str
    name: str
    limit_usd: Decimal | None


@dataclass(frozen=True)
class ProvisioningTarget:
    base_url: str
    bearer: str = field(repr=False)
    live: bool


def create_key_request(name: str, limit_usd: Decimal | None) -> ProvisioningRequest:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("A provisioned key needs a name")
    body = {"name": name}
    if limit_usd is not None:
        # Left out, not sent as null, when there is no limit: the field is
        # optional upstream.
        body["limit"] = _usd(limit_usd)
    return ProvisioningRequest("POST", "/keys", body)


def get_key_request(key_hash: str) -> ProvisioningRequest:
    return ProvisioningRequest("GET", _key_path(key_hash))


def update_key_request(
    key_hash: str, *, limit_usd=_UNSET, disabled: bool | None = None
) -> ProvisioningRequest:
    body = {}
    if limit_usd is not _UNSET:
        body["limit"] = _usd(limit_usd)
    if disabled is not None:
        body["disabled"] = bool(disabled)
    if not body:
        raise ValueError("update_key needs limit_usd or disabled")
    return ProvisioningRequest("PATCH", _key_path(key_hash), body)


def delete_key_request(key_hash: str) -> ProvisioningRequest:
    return ProvisioningRequest("DELETE", _key_path(key_hash))


def resolve_target(
    *, live: bool, environ: Mapping[str, str] | None = None
) -> ProvisioningTarget:
    """Where provisioning calls go: the loopback mock when its URL is set,
    else the live API, which needs all three switches."""
    env = os.environ if environ is None else environ
    mock_url = env.get(MOCK_URL_ENV, "").strip()
    if mock_url:
        if live:
            raise ProvisioningConfigError(
                f"{LIVE_FLAG} cannot be used while {MOCK_URL_ENV} is set: unset it "
                "for a live call."
            )
        _require_loopback(mock_url)
        bearer = env.get(MOCK_KEY_ENV, "").strip() or DEFAULT_MOCK_BEARER
        return ProvisioningTarget(mock_url.rstrip("/"), bearer, live=False)
    provisioning_key = env.get(PROVISIONING_KEY_ENV, "").strip()
    missing = []
    if not provisioning_key:
        missing.append(PROVISIONING_KEY_ENV)
    if env.get(LIVE_ENV, "").strip() != "1":
        missing.append(f"{LIVE_ENV}=1")
    if not live:
        missing.append(LIVE_FLAG)
    if missing:
        raise LiveProvisioningRefused(missing)
    return ProvisioningTarget(LIVE_BASE_URL, provisioning_key, live=True)


class OpenRouterProvisioningClient:
    """Calls the provisioning API at the target ``resolve_target`` picks.

    Constructing it resolves the target, so a client never exists for the
    live API unless all three switches were on.
    """

    def __init__(
        self,
        *,
        live: bool = False,
        environ: Mapping[str, str] | None = None,
        session: requests.Session | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ):
        self.target = resolve_target(live=live, environ=environ)
        self._session = session if session is not None else requests.Session()
        self._timeout = timeout

    @property
    def base_url(self) -> str:
        return self.target.base_url

    def create_key(self, name: str, limit_usd: Decimal | None = None) -> ProvisionedKey:
        response = self.send(create_key_request(name, limit_usd))
        data = response.get("data") if isinstance(response, dict) else None
        key = response.get("key") if isinstance(response, dict) else None
        if (
            not isinstance(key, str)
            or not key
            or not isinstance(data, dict)
            or not isinstance(data.get("hash"), str)
            or not data["hash"]
        ):
            raise ProvisioningError("POST /keys returned no key and hash")
        return ProvisionedKey(
            key=key,
            key_hash=data["hash"],
            name=data.get("name") or name,
            limit_usd=_decimal_usd(data.get("limit")),
        )

    def get_key(self, key_hash: str) -> dict:
        return _data(self.send(get_key_request(key_hash)))

    def update_key(
        self, key_hash: str, *, limit_usd=_UNSET, disabled: bool | None = None
    ) -> dict:
        return _data(
            self.send(
                update_key_request(key_hash, limit_usd=limit_usd, disabled=disabled)
            )
        )

    def delete_key(self, key_hash: str) -> dict:
        return self.send(delete_key_request(key_hash))

    def send(self, request: ProvisioningRequest) -> dict:
        label = f"{request.method} {request.path}"
        try:
            response = self._session.request(
                request.method,
                self.target.base_url + request.path,
                json=request.body,
                headers={"Authorization": f"Bearer {self.target.bearer}"},
                timeout=self._timeout,
                # A redirect would carry the bearer to another host.
                allow_redirects=False,
            )
        except requests.RequestException as e:
            raise ProvisioningError(f"{label} failed: {type(e).__name__}") from e
        if response.status_code >= 300:
            raise ProvisioningError(
                f"{label} returned {response.status_code}{_upstream_message(response)}",
                status_code=response.status_code,
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            raise ProvisioningError(
                f"{label} returned a body that is not JSON"
            ) from None


def _require_loopback(url: str) -> None:
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if parts.scheme not in ("http", "https") or not loopback or parts.username:
        raise ProvisioningConfigError(
            f"{MOCK_URL_ENV} must be an http URL on a loopback address."
        )


def _key_path(key_hash: str) -> str:
    if not isinstance(key_hash, str) or not key_hash:
        raise ValueError("A key hash is required")
    return "/keys/" + quote(key_hash, safe="")


def _usd(limit_usd):
    if limit_usd is None:
        return None
    amount = Decimal(str(limit_usd))
    if not amount.is_finite() or amount < 0:
        raise ValueError("limit_usd must be a non-negative amount")
    return float(amount)


def _decimal_usd(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _data(response) -> dict:
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, dict):
        raise ProvisioningError("the provisioning API returned no data object")
    return data


def _upstream_message(response) -> str:
    try:
        error = response.json().get("error") or {}
        message = error.get("message") if isinstance(error, dict) else None
    except (ValueError, AttributeError):
        message = None
    if not isinstance(message, str) or not message:
        return ""
    return ": " + message[:200]
