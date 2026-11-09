"""Pipedream Connect: header binding; no assumption of provider-grant revocation.

The HTTP client accepts a loopback base for synthetic tests. Runtime wiring
only uses the documented origin; request bodies cannot choose this base.
"""

from datetime import timedelta
from urllib.parse import urlencode

from django.utils import timezone

from agentcc.services.credential_manager import decrypt_token, encrypt_token
from agentcc.services.mcp_revocation import Revocation
from agentcc.services.mcp_vendor_http import (
    VendorUnavailable,
    connect_link,
    fetch,
    object_body,
    reference,
    revocation_status,
)

BASE = "https://api.pipedream.com"
CONNECT_HOSTS = frozenset({"pipedream.com", "connect.pipedream.com"})


class Pipedream:
    def __init__(
        self,
        client_id,
        client_secret,
        project_id,
        environment,
        *,
        base=BASE,
        binding=None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.project = reference(project_id)
        if environment not in ("development", "production"):
            raise VendorUnavailable()
        self.environment = environment
        self.base = base
        self.binding = binding
        self._bearer = None
        self.expires_at = None

    def bearer(self):
        row = self.binding
        now = timezone.now()
        if (
            row
            and row.encrypted_bearer
            and row.bearer_version == row.credential.version
            and row.bearer_expires_at
            and row.bearer_expires_at > now
        ):
            self.expires_at = row.bearer_expires_at
            return decrypt_token(row.encrypted_bearer)
        if self._bearer and self.expires_at > now:
            return self._bearer
        body = object_body(
            fetch(
                self.base,
                "/v1/oauth/token",
                method="POST",
                body={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
            )
        )
        value = body.get("access_token")
        if (
            not isinstance(value, str)
            or not value
            or any(c in value for c in "\r\n\x00")
        ):
            raise VendorUnavailable()
        self._bearer = value
        # Vendor documentation promises one hour. Never cache beyond 55 minutes.
        self.expires_at = now + timedelta(minutes=55)
        if row:
            row.encrypted_bearer = encrypt_token(value)
            row.bearer_expires_at = self.expires_at
            row.bearer_version = row.credential.version
            row.save(
                update_fields=[
                    "encrypted_bearer",
                    "bearer_expires_at",
                    "bearer_version",
                ]
            )
        return value

    def request(self, path, *, method="GET", body=None):
        return fetch(
            self.base,
            f"/v1/connect/{self.project}" + path,
            method=method,
            body=body,
            headers={
                "Authorization": "Bearer " + self.bearer(),
                "x-pd-environment": self.environment,
            },
        )

    def account(self, subject, app, pinned=""):
        query = urlencode({"external_user_id": subject, "app": app, "limit": 100})
        cursor = None
        for _ in range(10):
            body = object_body(
                self.request(
                    "/accounts?"
                    + query
                    + ("&" + urlencode({"after": cursor}) if cursor else "")
                )
            )
            rows = body.get("data")
            if not isinstance(rows, list):
                raise VendorUnavailable()
            for account in rows:
                if not isinstance(account, dict):
                    raise VendorUnavailable()
                app_info = account.get("app") or {}
                if not isinstance(app_info, dict):
                    raise VendorUnavailable()
                if (
                    account.get("external_id") == subject
                    and app_info.get("name_slug") == app
                    and account.get("healthy") is True
                    and account.get("dead") is not True
                    and (not pinned or account.get("id") == pinned)
                ):
                    return reference(account.get("id"))
            info = body.get("page_info") or {}
            if not isinstance(info, dict) or any(
                type(info.get(field, 0)) is not int
                for field in ("total_count", "count")
            ):
                raise VendorUnavailable()
            next_cursor = info.get("end_cursor")
            if next_cursor is not None and not isinstance(next_cursor, str):
                raise VendorUnavailable()
            if (
                not rows
                or not next_cursor
                or next_cursor == cursor
                or info.get("total_count", 0) <= info.get("count", len(rows))
            ):
                return None
            cursor = next_cursor
        raise VendorUnavailable()

    def connect(self, subject):
        body = object_body(
            self.request(
                "/tokens",
                method="POST",
                body={"external_user_id": subject, "expires_in": 600},
            ),
            (200, 201),
        )
        return connect_link(body.get("connect_link_url"), CONNECT_HOSTS)

    def disconnect(self, account):
        try:
            status = self.request(
                "/accounts/" + reference(account), method="DELETE"
            ).status
            result = revocation_status(status, (204,))
            # A removed partner record does not prove the app's OAuth grant was revoked.
            if result.outcome == "confirmed":
                return Revocation("unconfirmed", "not_documented")
            return result
        except VendorUnavailable:
            return Revocation("pending", "vendor_unavailable")

    def offboard(self, subject):
        try:
            status = self.request(
                "/users/" + reference(subject), method="DELETE"
            ).status
            result = revocation_status(status, (204,))
            return (
                Revocation("unconfirmed", "not_documented")
                if result.outcome == "confirmed"
                else result
            )
        except VendorUnavailable:
            return Revocation("pending", "vendor_unavailable")
