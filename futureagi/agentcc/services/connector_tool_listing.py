"""A catalog provider's live tool listing, taken when an org adds it (CN-ADD,
MCP-APPS-PLAN section 2.3).

The listing is the provider's ``tools/list``, taken through the gateway's
connection probe (M17, ``POST /-/mcp/probe``) with the org's own credential.
The gateway uses that credential for the one probe and never stores or logs
it, and the SSRF guard covers the call. The probe reports each tool's name,
title and description, and the four MCP behaviour hints exactly as the
upstream sent them (mcp-probe-v1's ``tools``, MCPProbeTool).

This module reads that result strictly:
- a hint counts only as the JSON boolean the probe passes on;
- a name is usable only when it follows MCP's tool name rule;
- a result whose ``tools`` do not match ``tool_count`` is refused.

It never sees the credential's value except to pass it to the probe, and it
never logs or returns it. The posture of each tool is the catalog service's
(``connector_catalog.listed_tool_policy``).
"""

import re
from dataclasses import dataclass
from typing import Any

from agentcc.services.gateway_admin import mcp_probe
from agentcc.services.gateway_client import GatewayClientError

# MCP's tool name rule, the gateway's toolNameRegex (internal/mcp/types.go).
# A name outside it is never written to a virtual server, where "*" is a glob.
TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
# The probe's code for an upstream that answered 401 or 403 (mcp/probe.go).
_AUTH_REQUIRED = "auth_required"

# Refusal codes. 400: the provider refused the org's credential.
CREDENTIAL_REJECTED = "connector_credential_rejected"
# 502: no listing could be taken.
LISTING_FAILED = "connector_tool_listing_failed"
LISTING_NOT_SUPPORTED = "connector_tool_listing_not_supported"
GATEWAY_UNREACHABLE = "gateway_unreachable"
# The deployment runs with MCP off: the gateway registers no /-/mcp/* route,
# the probe's among them, and answers 404 (mcp_admin's GATEWAY_MCP_DISABLED,
# the code the console's MCP reads give).
GATEWAY_MCP_DISABLED = "gateway_mcp_disabled"


class ListingError(Exception):
    """Why no listing was taken, with a stable ``code`` and the HTTP
    ``status`` the API answers. The message names no credential value."""

    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class ListedTool:
    """One listed tool. Each ``*_hint`` is the upstream's boolean, or None
    when it sent none. The properties apply MCP's defaults for a missing
    hint: not read-only, destructive unless read-only, not idempotent, open
    world."""

    name: str
    title: str
    description: str
    read_only_hint: bool | None
    destructive_hint: bool | None
    idempotent_hint: bool | None
    open_world_hint: bool | None
    # The tool's revision as the gateway listed it (M20), "" when the gateway
    # reports none: the add pins the tool to it (mcp_revisions.pin_listing).
    revision: str = ""

    @property
    def valid_name(self) -> bool:
        return bool(TOOL_NAME.fullmatch(self.name))

    @property
    def read_only(self) -> bool:
        return self.read_only_hint is True

    @property
    def destructive(self) -> bool:
        # MCP reads destructiveHint only for a tool that is not read-only. A
        # read-only tool that still says destructive is taken at its word.
        if self.read_only:
            return self.destructive_hint is True
        return self.destructive_hint is not False

    @property
    def idempotent(self) -> bool:
        return self.idempotent_hint is True

    @property
    def open_world(self) -> bool:
        return self.open_world_hint is not False


@dataclass(frozen=True)
class Listing:
    """The first listing of a connector, its baseline: the tools in the
    order listed, and the gateway's ``tools_hash`` of them (M17's canonical
    form, which M20 pinning reuses)."""

    tools: tuple[ListedTool, ...]
    tools_hash: str
    era: str

    @property
    def tool_count(self) -> int:
        return len(self.tools)


def list_tools(
    org_id: str, url: str, *, transport: str, auth: dict[str, str] | None
) -> Listing:
    """List the tools of the server at ``url`` through the probe, with
    ``auth`` (a vault secret's push form for a header or a query parameter,
    or None). Raises ``ListingError``."""
    try:
        result = mcp_probe.probe(org_id, url, transport=transport, auth=auth)
    except mcp_probe.ProbeRefused as e:
        # The gateway's own message about the request, never its body.
        raise ListingError(
            LISTING_FAILED,
            f"the gateway refused to list the provider's tools: {e}",
            502,
        ) from None
    except GatewayClientError as e:
        if e.status_code == 404:
            raise ListingError(
                GATEWAY_MCP_DISABLED,
                "MCP is turned off for this deployment, so no app can be added.",
                404,
            ) from None
        raise ListingError(
            GATEWAY_UNREACHABLE,
            "The gateway could not list the provider's tools.",
            502,
        ) from None
    if result.get("ok") is not True:
        error = result.get("error")
        if error == _AUTH_REQUIRED:
            raise ListingError(
                CREDENTIAL_REJECTED,
                "the provider refused the credential (HTTP 401 or 403) when the "
                "gateway listed its tools; check it and add the app again",
                400,
            )
        reason = error if isinstance(error, str) and error else "no reason given"
        raise ListingError(
            LISTING_FAILED,
            f"the gateway could not list the provider's tools ({reason})",
            502,
        )
    tools = result.get("tools")
    if not isinstance(tools, list):
        raise ListingError(
            LISTING_NOT_SUPPORTED,
            "this gateway's probe does not report the tools it lists, so the "
            "connector's tools cannot be set; deploy a gateway built from the "
            "same commit as this Django build, or a later one",
            502,
        )
    tools_hash = result.get("tools_hash")
    if result.get("tool_count") != len(tools) or not (
        isinstance(tools_hash, str) and _SHA256.fullmatch(tools_hash)
    ):
        raise ListingError(
            LISTING_FAILED,
            "the gateway's listing does not hold one entry per tool, or has no "
            "tools_hash",
            502,
        )
    era = result.get("era")
    return Listing(
        tools=tuple(_listed(tool) for tool in tools),
        tools_hash=tools_hash,
        era=era if isinstance(era, str) else "",
    )


def _listed(tool: Any) -> ListedTool:
    tool = tool if isinstance(tool, dict) else {}
    return ListedTool(
        name=_string(tool.get("name")),
        title=_string(tool.get("title")),
        description=_string(tool.get("description")),
        read_only_hint=_hint(tool.get("read_only_hint")),
        destructive_hint=_hint(tool.get("destructive_hint")),
        idempotent_hint=_hint(tool.get("idempotent_hint")),
        open_world_hint=_hint(tool.get("open_world_hint")),
        revision=_revision(tool.get("revision")),
    )


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _hint(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _revision(value: Any) -> str:
    return value if isinstance(value, str) and _SHA256.fullmatch(value) else ""
