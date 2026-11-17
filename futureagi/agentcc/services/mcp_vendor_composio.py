"""Composio sessions use minted URLs, not constructed query/path bindings.

Reference v3.1 fetched 2026-10-28. The REST create schema has no `mcp`
request field (that is an SDK option). The MCP key is restricted to the
API origin, per sessions-via-mcp's destination rule, even if a response
contains a different origin. No legacy app.composio.dev exception.
"""

from urllib.parse import urlencode, urlsplit

from agentcc.services.mcp_revocation import Revocation
from agentcc.services.mcp_vendor_http import (
    VendorUnavailable,
    connect_link,
    fetch,
    object_body,
    origin,
    reference,
    revocation_status,
)

BASE = "https://backend.composio.dev"
CONNECT_HOSTS = frozenset({"connect.composio.dev", "backend.composio.dev"})


class Composio:
    def __init__(self, api_key, *, base=BASE):
        self.api_key = api_key
        self.base = base
        self.connected_account_ref = ""

    def request(self, path, *, method="GET", body=None):
        return fetch(
            self.base,
            "/api/v3.1" + path,
            method=method,
            body=body,
            headers={"x-api-key": self.api_key},
        )

    def create(self, subject, toolkits, tools):
        if not toolkits or not tools or set(tools) != set(toolkits):
            raise VendorUnavailable()
        body = object_body(
            self.request(
                "/tool_router/session",
                method="POST",
                body={
                    "user_id": subject,
                    "toolkits": {"enable": toolkits},
                    "tools": {kit: {"enable": tools[kit]} for kit in toolkits},
                    "manage_connections": {"enable": False},
                    "search": {"enable": False},
                    "workbench": {"enable": False},
                    "execute": {"enable_multi_execute": False},
                    "preload": {"tools": "all"},
                },
            ),
            (201,),
        )
        session = reference(body.get("session_id"))
        try:
            if not isinstance(body.get("mcp"), dict):
                raise VendorUnavailable()
            endpoint = body["mcp"].get("url")
            if origin(endpoint) != origin(self.base) or urlsplit(endpoint).query:
                raise VendorUnavailable()
            # Require real direct tools. Do not infer a toolkit from tool prefixes.
            listed = object_body(
                self.request(f"/tool_router/session/{session}/tools?limit=1000")
            )
            items = listed.get("items")
            allowed = {slug for slugs in tools.values() for slug in slugs}
            if not isinstance(items, list) or not items or listed.get("next_cursor"):
                raise VendorUnavailable()
            slugs = {item.get("slug") for item in items if isinstance(item, dict)}
            if (
                not slugs
                or not slugs <= allowed
                or any(
                    not isinstance(s, str) or s.startswith("COMPOSIO_") for s in slugs
                )
            ):
                raise VendorUnavailable()
        except Exception:
            self.offboard(session)
            raise VendorUnavailable() from None
        return session, endpoint

    def pin_account(self, session, toolkit, account):
        # Never let the vendor's "most recent account" default change a live
        # session to a different connected account after authorization.
        response = self.request(
            f"/tool_router/session/{reference(session)}",
            method="PATCH",
            body={"connected_accounts": {reference(toolkit): [reference(account)]}},
        )
        if response.status != 200:
            raise VendorUnavailable()

    def account(self, session, toolkit):
        query = urlencode({"toolkits": toolkit, "is_connected": "true", "limit": 50})
        body = object_body(
            self.request(f"/tool_router/session/{reference(session)}/toolkits?{query}")
        )
        items = body.get("items")
        if not isinstance(items, list) or body.get("next_cursor"):
            raise VendorUnavailable()
        for item in items:
            if not isinstance(item, dict):
                raise VendorUnavailable()
            if (
                item.get("slug") == toolkit
                and item.get("enabled") is True
                and item.get("connected_account")
            ):
                account = item["connected_account"]
                if not isinstance(account, dict) or account.get("status") != "ACTIVE":
                    continue
                return reference(account.get("id"))
        return None

    def connect(self, session, toolkit):
        body = object_body(
            self.request(
                f"/tool_router/session/{reference(session)}/link",
                method="POST",
                body={"toolkit": toolkit},
            ),
            (201,),
        )
        # Persist this handle at connect, not only after the first tool call.
        self.connected_account_ref = reference(body.get("connected_account_id"))
        return connect_link(body.get("redirect_url"), CONNECT_HOSTS)

    def disconnect(self, account):
        try:
            path = "/connected_accounts/" + reference(account)
            response = self.request(path + "/revoke", method="POST")
            result = revocation_status(response.status, (200,))
            if result.outcome == "confirmed":
                body = object_body(response)
                if (
                    not isinstance(body.get("revoked_tokens"), list)
                    or (body.get("connected_account") or {}).get("status") != "REVOKED"
                ):
                    return Revocation("pending", "invalid_response")
            if result.outcome == "pending":
                return result
            deleted = revocation_status(
                self.request(path, method="DELETE").status, (200, 204, 404)
            )
            if deleted.outcome != "confirmed":
                return Revocation("pending", "delete_pending")
            return result
        except VendorUnavailable:
            return Revocation("pending", "vendor_unavailable")

    def offboard(self, session):
        try:
            response = self.request(
                "/tool_router/session/" + reference(session), method="DELETE"
            )
            return revocation_status(response.status, (200, 404))
        except VendorUnavailable:
            return Revocation("pending", "vendor_unavailable")
