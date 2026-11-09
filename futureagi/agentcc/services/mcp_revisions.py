"""Tool revisions and pin approval (M21): the Django side of tool pinning (M20).

The gateway pins each tool of an org's own MCP servers to the revision an
admin accepted, ``mcp.pinning.pins["<server_id>/<tool>"]`` in the org config:
a tool whose definition changed, or a tool a server added later, is held (not
listed, and a call is refused with ``tool_quarantined``) until an admin
accepts it. This module:

- pulls the held revisions from the gateway (``GET /-/mcp/revisions``) into
  ``AgentccMCPToolRevision`` rows, one per revision, with the definition and
  the field-level diff an admin reviews; a pending row the gateway no longer
  reports is superseded;
- accepts a revision: writes its pin through the org config writer (org lock
  first, then the push), so the gateway serves it at once; or rejects it,
  which keeps the tool held;
- pins the tools of a server with no pins yet as the gateway first listed
  them (its baseline), when ``auto_baseline`` is on. A server added from the
  connector catalog is pinned at add instead, to the listing taken then
  (``listing_pins``), in the same config version as the server.

The pins are written only here and by the catalog add; the org config editor
can also edit ``mcp.pinning`` as a whole, which is an admin write too.
"""

import copy
import re
from collections.abc import Iterable, Mapping
from typing import Any

import structlog
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.models import Organization, User
from agentcc.models import AgentccOrgConfig
from agentcc.models.mcp_tool_revision import AgentccMCPToolRevision
from agentcc.services.gateway_admin import mcp as gateway_mcp
from agentcc.services.gateway_admin import mcp_revisions as gateway_revisions
from agentcc.services.org_config_writer import (
    is_gateway_synced,
    lock_org,
    write_config_version_with,
)

logger = structlog.get_logger(__name__)

Status = AgentccMCPToolRevision.Status

# The definition fields a revision covers, in diff order (M20's pin.Fields).
FIELDS = ("name", "title", "description", "inputSchema", "outputSchema", "annotations")
# MCP's tool name rule (connector_tool_listing.TOOL_NAME).
TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
REVISION = re.compile(r"^[0-9a-f]{64}$")
# The most rows one list answers with, newest first.
LIST_LIMIT = 500

REVISION_NOT_FOUND = "mcp_revision_not_found"
REVISION_DECIDED = "mcp_revision_decided"
SERVER_NOT_FOUND = "mcp_server_not_found"


class RevisionError(Exception):
    """A refused decision, with a stable ``code`` and the HTTP ``status``."""

    status = 400

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class RevisionNotFound(RevisionError):
    status = 404


class RevisionConflict(RevisionError):
    status = 409


# --- Pins in an mcp section ---------------------------------------------------


def pinning_settings(mcp: Mapping[str, Any] | None) -> dict[str, Any]:
    """``mcp.pinning`` with the gateway's defaults (quarantine, quarantine,
    auto_baseline on)."""
    pinning = (mcp or {}).get("pinning")
    pinning = pinning if isinstance(pinning, Mapping) else {}
    auto = pinning.get("auto_baseline")
    return {
        "mode": pinning.get("mode") or "quarantine",
        "new_tools": pinning.get("new_tools") or "quarantine",
        "auto_baseline": True if auto is None else auto is True,
    }


def pins_of(mcp: Mapping[str, Any] | None) -> dict[str, str]:
    """A copy of ``mcp.pinning.pins``."""
    pinning = (mcp or {}).get("pinning")
    pins = pinning.get("pins") if isinstance(pinning, Mapping) else None
    return dict(pins) if isinstance(pins, Mapping) else {}


def server_pinned(pins: Mapping[str, str], server_id: str) -> bool:
    prefix = f"{server_id}/"
    return any(key.startswith(prefix) for key in pins)


def set_pins(mcp: dict, pins: Mapping[str, str]) -> None:
    """Set ``mcp.pinning.pins`` (in place) to ``pins``, less the pins of a
    server the section no longer has."""
    servers = mcp.get("servers")
    servers = servers if isinstance(servers, Mapping) else {}
    kept = {key: rev for key, rev in pins.items() if key.split("/", 1)[0] in servers}
    pinning = mcp.get("pinning")
    pinning = dict(pinning) if isinstance(pinning, Mapping) else {}
    pinning["pins"] = kept
    mcp["pinning"] = pinning


def listing_pins(server_id: str, tools: Iterable[Any]) -> dict[str, str] | None:
    """The pins of a catalog add's listing (CN-ADD): each listed tool at the
    revision the gateway listed it at. A tool whose name breaks MCP's rule is
    never exposed and is left out (were the gateway to list it, it would be
    held as new). None when a tool has no revision (a gateway built before
    revisions): then nothing is pinned at add, and the gateway pins the
    server's first listing instead (auto_baseline)."""
    pins = {}
    for tool in tools:
        name, revision = getattr(tool, "name", ""), getattr(tool, "revision", "")
        if not TOOL_NAME.fullmatch(name or ""):
            continue
        if not REVISION.fullmatch(revision or ""):
            return None
        pins[f"{server_id}/{name}"] = revision
    return pins


def pin_listing(mcp: dict, server_id: str, tools: Iterable[Any]) -> bool:
    """Pin ``server_id``'s tools in ``mcp`` (in place) to the add's listing,
    replacing any pin a removed server of the same id left. Returns whether
    it pinned (``listing_pins``)."""
    pins = listing_pins(server_id, tools)
    if pins is None:
        return False
    current = {
        key: rev
        for key, rev in pins_of(mcp).items()
        if not key.startswith(f"{server_id}/")
    }
    current.update(pins)
    set_pins(mcp, current)
    return True


# --- Pull ---------------------------------------------------------------------


def _active_mcp(org: Organization) -> dict:
    config = (
        AgentccOrgConfig.no_workspace_objects.filter(
            organization=org, is_active=True, deleted=False
        )
        .only("mcp")
        .first()
    )
    mcp = config.mcp if config is not None else None
    return mcp if isinstance(mcp, dict) else {}


def _servers(mcp: Mapping[str, Any]) -> dict:
    servers = mcp.get("servers")
    return servers if isinstance(servers, dict) else {}


def pull(org: Organization, *, baseline: bool = True) -> dict[str, Any]:
    """Bring the org's rows in line with the gateway: one row per held
    revision the gateway reports (new rows ``pending``), each pending row it
    no longer reports ``superseded``. With ``baseline``, a server with no pins
    yet is pinned as the gateway first listed it (``auto_baseline``).

    Raises ``GatewayClientError`` when the gateway cannot answer; nothing is
    changed then."""
    mcp = _active_mcp(org)
    records = gateway_revisions.list_revisions(str(org.pk)) if _servers(mcp) else []
    reported = set()
    for record in records:
        reported.add((record["server_id"], record["tool"], record["revision"]))
        _upsert(org, record)
    stale = AgentccMCPToolRevision.objects.filter(
        organization=org, status=Status.PENDING
    )
    superseded = [
        row.pk
        for row in stale
        if (row.server_id, row.tool, row.revision) not in reported
    ]
    if superseded:
        AgentccMCPToolRevision.objects.filter(pk__in=superseded).update(
            status=Status.SUPERSEDED
        )
    baselined = _baseline(org, mcp) if baseline else []
    return {
        "reported": len(records),
        "superseded": len(superseded),
        "baselined": baselined,
    }


def _upsert(org: Organization, record: Mapping[str, Any]) -> AgentccMCPToolRevision:
    """One row per reported revision, created pending. A row an admin decided
    keeps its decision. The accepted definition the gateway sends with it is
    kept on the accepted revision's row, so its diff outlives the gateway's
    memory."""
    previous = record.get("previous_revision") or ""
    previous_definition = record.get("previous_definition")
    if previous and isinstance(previous_definition, dict):
        _remember(
            org, record["server_id"], record["tool"], previous, previous_definition
        )
    diff = record.get("diff") or []
    if not diff and previous:
        accepted = _definition_of(org, record["server_id"], record["tool"], previous)
        if accepted:
            diff = definition_diff(accepted, record["definition"])
    first_seen = parse_datetime(record.get("first_seen_at") or "") or timezone.now()
    lookup = {
        "organization": org,
        "server_id": record["server_id"],
        "tool": record["tool"],
        "revision": record["revision"],
    }
    defaults = {
        "definition": record["definition"],
        "previous_revision": previous,
        "diff": diff,
        "first_seen_at": first_seen,
    }
    try:
        with transaction.atomic():
            row, created = AgentccMCPToolRevision.objects.get_or_create(
                **lookup, defaults=defaults
            )
    except IntegrityError:  # a concurrent pull created it first
        row, created = AgentccMCPToolRevision.objects.get(**lookup), False
    if not created and row.status == Status.PENDING:
        changed = {
            k: v
            for k, v in defaults.items()
            if k != "first_seen_at" and getattr(row, k) != v
        }
        if changed:
            for key, value in changed.items():
                setattr(row, key, value)
            row.save(update_fields=list(changed))
    if created:
        logger.info(
            "mcp_tool_revision_held",
            org_id=str(org.pk),
            server_id=row.server_id,
            tool=row.tool,
            revision=row.revision,
            reason=reason_of(row),
        )
    return row


def _remember(org, server_id, tool, revision, definition) -> None:
    """Keep the definition of an accepted revision (a row decided ``approved``
    by the add or the baseline when no admin decided it)."""
    lookup = {
        "organization": org,
        "server_id": server_id,
        "tool": tool,
        "revision": revision,
    }
    try:
        with transaction.atomic():
            row, created = AgentccMCPToolRevision.objects.get_or_create(
                **lookup,
                defaults={"definition": definition, "status": Status.APPROVED},
            )
    except IntegrityError:
        row, created = AgentccMCPToolRevision.objects.get(**lookup), False
    if not created and not row.definition:
        row.definition = definition
        row.save(update_fields=["definition"])


def _definition_of(org, server_id, tool, revision) -> dict | None:
    row = (
        AgentccMCPToolRevision.objects.filter(
            organization=org, server_id=server_id, tool=tool, revision=revision
        )
        .only("definition")
        .first()
    )
    return row.definition if row is not None and row.definition else None


def definition_diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict]:
    """The fields that differ, in ``FIELDS`` order, as mcp-revision-v1 diff
    items; a side that lacks the field has no value."""
    out = []
    for field in FIELDS:
        if before.get(field) != after.get(field):
            item = {"field": field}
            if field in before:
                item["before"] = before[field]
            if field in after:
                item["after"] = after[field]
            out.append(item)
    return out


def _baseline(org: Organization, mcp: Mapping[str, Any]) -> list[str]:
    """Pin each server that has no pins yet to the tools the gateway lists
    for it now (GET /-/mcp/tools: each tool's revision, none held), in one
    config version. Returns the servers pinned."""
    settings = pinning_settings(mcp)
    if settings["mode"] == "off" or not settings["auto_baseline"]:
        return []
    pins = pins_of(mcp)
    targets = {sid for sid in _servers(mcp) if not server_pinned(pins, sid)}
    if not targets:
        return []
    found: dict[str, dict[str, str]] = {}
    for tool in gateway_mcp.list_tools(str(org.pk)):
        server_id, name, revision = (
            tool.get("server_id"),
            tool.get("upstream_name"),
            tool.get("revision"),
        )
        if (
            server_id in targets
            and isinstance(name, str)
            and TOOL_NAME.fullmatch(name)
            and isinstance(revision, str)
            and REVISION.fullmatch(revision)
            and tool.get("quarantined") is not True
        ):
            found.setdefault(server_id, {})[f"{server_id}/{name}"] = revision
    if not found:
        return []
    pinned: list[str] = []

    def updater(config: AgentccOrgConfig) -> None:
        section = copy.deepcopy(config.mcp) if isinstance(config.mcp, dict) else {}
        current = pins_of(section)
        for server_id, server_pins in sorted(found.items()):
            # Re-checked under the org lock: an accept may have pinned it.
            if server_id in _servers(section) and not server_pinned(current, server_id):
                current.update(server_pins)
                pinned.append(server_id)
        set_pins(section, current)
        config.mcp = section

    write_config_version_with(
        org,
        updater,
        description=(
            "Pin the tools of MCP servers "
            + ", ".join(sorted(found))
            + " as the gateway first listed them"
        ),
    )
    logger.info("mcp_tools_baselined", org_id=str(org.pk), servers=pinned)
    return pinned


def pull_all() -> dict[str, int]:
    """``pull`` for every org whose active config has MCP servers (the
    schedule). An org whose gateway call fails is skipped and logged."""
    configs = AgentccOrgConfig.no_workspace_objects.filter(
        is_active=True, deleted=False
    ).select_related("organization")
    pulled = failed = 0
    for config in configs.iterator():
        mcp = config.mcp if isinstance(config.mcp, dict) else {}
        if mcp.get("enabled") is not True or not _servers(mcp):
            continue
        try:
            pull(config.organization)
            pulled += 1
        except Exception:
            failed += 1
            logger.exception(
                "mcp_tool_revision_pull_failed", org_id=str(config.organization_id)
            )
    return {"pulled": pulled, "failed": failed}


# --- Reads --------------------------------------------------------------------


def reason_of(row: AgentccMCPToolRevision) -> str:
    """changed (it replaces an accepted revision) or new."""
    return "changed" if row.previous_revision else "new"


def rows_for(org: Organization, status: str | None) -> list[AgentccMCPToolRevision]:
    rows = AgentccMCPToolRevision.objects.filter(organization=org).select_related(
        "decided_by"
    )
    if status:
        rows = rows.filter(status=status)
    return list(rows.order_by("-first_seen_at", "-pk")[:LIST_LIMIT])


def describe(row: AgentccMCPToolRevision) -> dict[str, Any]:
    """A row as the API answers it: the definition an admin reviews, the
    accepted one it would replace (when known) and what changed."""
    previous = (
        _definition_of(
            row.organization_id, row.server_id, row.tool, row.previous_revision
        )
        if row.previous_revision
        else None
    )
    return {
        "id": row.pk,
        "server_id": row.server_id,
        "tool": row.tool,
        "revision": row.revision,
        "previous_revision": row.previous_revision or None,
        "reason": reason_of(row),
        "status": row.status,
        "definition": row.definition,
        "previous_definition": previous,
        "diff": row.diff,
        "first_seen_at": row.first_seen_at,
        "decided_by": row.decided_by.email if row.decided_by else None,
        "decided_at": row.decided_at,
    }


# --- Decisions ----------------------------------------------------------------


def _locked_row(org: Organization, revision_id: int) -> AgentccMCPToolRevision:
    row = (
        AgentccMCPToolRevision.objects.select_for_update()
        .filter(organization=org, pk=revision_id)
        .first()
    )
    if row is None:
        raise RevisionNotFound(REVISION_NOT_FOUND, "MCP tool revision not found.")
    return row


def _write_pins(
    org: Organization, pins: Mapping[str, str], *, actor: User, description: str
):
    """Write ``pins`` into the active version through the org config writer
    (which pushes it). Raises ``RevisionConflict`` for a server the config no
    longer has."""

    def updater(config: AgentccOrgConfig) -> None:
        section = copy.deepcopy(config.mcp) if isinstance(config.mcp, dict) else {}
        servers = _servers(section)
        current = pins_of(section)
        for key, revision in pins.items():
            server_id = key.split("/", 1)[0]
            if server_id not in servers:
                raise RevisionConflict(
                    SERVER_NOT_FOUND,
                    f"The org has no MCP server {server_id} any more, so its tool "
                    "cannot be accepted.",
                )
            current[key] = revision
        set_pins(section, current)
        config.mcp = section

    return write_config_version_with(org, updater, description=description, user=actor)


def _decide(rows, status: str, actor: User) -> None:
    now = timezone.now()
    for row in rows:
        row.status, row.decided_by, row.decided_at = status, actor, now
        row.save(update_fields=["status", "decided_by", "decided_at"])


def _supersede_others(org, accepted: list[AgentccMCPToolRevision]) -> None:
    """Every other pending or accepted revision of an accepted tool."""
    for row in accepted:
        AgentccMCPToolRevision.objects.filter(
            organization=org,
            server_id=row.server_id,
            tool=row.tool,
            status__in=[Status.PENDING, Status.APPROVED],
        ).exclude(pk=row.pk).update(status=Status.SUPERSEDED)


def approve(org: Organization, revision_id: int, actor: User) -> dict[str, Any]:
    """Accept one held revision: its pin is written and pushed, so the
    gateway serves the tool as it is now. A pending or rejected revision can
    be accepted; an accepted or superseded one is ``mcp_revision_decided``."""
    with transaction.atomic():
        lock_org(org.pk)
        row = _locked_row(org, revision_id)
        if row.status not in (Status.PENDING, Status.REJECTED):
            raise RevisionConflict(
                REVISION_DECIDED, f"This revision is already {row.status}."
            )
        config, warnings = _write_pins(
            org,
            {f"{row.server_id}/{row.tool}": row.revision},
            actor=actor,
            description=(
                f"Accept MCP tool {row.server_id}/{row.tool} at revision "
                f"{row.revision[:12]}"
            ),
        )
        _decide([row], Status.APPROVED, actor)
        _supersede_others(org, [row])
    logger.info(
        "mcp_tool_revision_approved",
        org_id=str(org.pk),
        server_id=row.server_id,
        tool=row.tool,
        revision=row.revision,
        actor_id=str(actor.pk),
        version=config.version,
    )
    return {
        **describe(row),
        "version": config.version,
        "gateway_synced": is_gateway_synced(warnings),
    }


def reject(org: Organization, revision_id: int, actor: User) -> dict[str, Any]:
    """Reject one pending revision: the pin stays as it is, so the tool stays
    held (and a revert to the accepted definition is served again)."""
    with transaction.atomic():
        lock_org(org.pk)
        row = _locked_row(org, revision_id)
        if row.status != Status.PENDING:
            raise RevisionConflict(
                REVISION_DECIDED, f"This revision is already {row.status}."
            )
        _decide([row], Status.REJECTED, actor)
    logger.info(
        "mcp_tool_revision_rejected",
        org_id=str(org.pk),
        server_id=row.server_id,
        tool=row.tool,
        revision=row.revision,
        actor_id=str(actor.pk),
    )
    return describe(row)


def approve_baseline(org: Organization, server_id: str, actor: User) -> dict[str, Any]:
    """Accept every pending revision of one server at once (a server whose
    tools are all new, such as one added while auto_baseline is off), in one
    config version. Nothing pending writes nothing."""
    with transaction.atomic():
        lock_org(org.pk)
        rows = list(
            AgentccMCPToolRevision.objects.select_for_update()
            .filter(organization=org, server_id=server_id, status=Status.PENDING)
            .order_by("tool", "-first_seen_at")
        )
        latest: dict[str, AgentccMCPToolRevision] = {}
        for row in rows:
            latest.setdefault(row.tool, row)
        if not latest:
            return {
                "server_id": server_id,
                "approved": [],
                "version": None,
                "gateway_synced": None,
            }
        config, warnings = _write_pins(
            org,
            {f"{server_id}/{tool}": row.revision for tool, row in latest.items()},
            actor=actor,
            description=f"Accept the {len(latest)} held tools of MCP server {server_id}",
        )
        accepted = list(latest.values())
        _decide(accepted, Status.APPROVED, actor)
        _supersede_others(org, accepted)
    logger.info(
        "mcp_tool_revisions_baseline_approved",
        org_id=str(org.pk),
        server_id=server_id,
        tools=sorted(latest),
        actor_id=str(actor.pk),
        version=config.version,
    )
    return {
        "server_id": server_id,
        "approved": [describe(row) for row in accepted],
        "version": config.version,
        "gateway_synced": is_gateway_synced(warnings),
    }
