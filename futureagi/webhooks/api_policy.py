"""The org and workspace webhook policy (DESIGN.md sections 4.9, 6.3 and 9.1).

``allowed_hosts`` holds exact names (``hooks.acme.ai``) or leftmost wildcards (``*.acme.ai``, any depth below, never
the name itself). Empty means any public host. A workspace list must be covered by the org list: a workspace can
narrow, never widen. Both lists apply at once (the core checks each), so an org that narrows later also narrows every
workspace without rewriting it.

When a policy change excludes an existing endpoint, the endpoint becomes ``blocked_by_policy`` (its state row locked
first, and its URL judged as it is under that lock), its deliveries are paused with their sequence numbers, and an
audit entry names the change. No event is dropped: when the policy allows the host again, an admin turns the endpoint
on and resumes the backlog.

Turning ``full_payload_allowed`` off moves every full-mode endpoint of the org to ``thin`` in the same transaction,
each audited, so no new event carries prompt text (section 4.9). Deliveries already queued keep the body
they were made with under the policy of their time.
"""

from __future__ import annotations

import re

from django.db import transaction
from django.utils import timezone

from tfc.constants.levels import Level
from webhooks import audit, config, crypto
from webhooks.api_errors import WebhookApiError
from webhooks.api_locks import lock_state
from webhooks.api_validation import note_write, require_keys
from webhooks.core.addresses import CODE_HOST_NOT_ALLOWED, url_decision
from webhooks.models import Endpoint, Policy
from webhooks.permissions import ADMIN, VIEW, allowed_host_lists
from webhooks.store_settle import pause_backlog

MAX_HOSTS = 100
_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_HOST = re.compile(rf"(\*\.)?{_LABEL}(\.{_LABEL})+")
ORG_FIELDS = (
    "allowed_hosts",
    "members_can_manage",
    "full_payload_allowed",
    "max_endpoints_per_workspace",
    "log_retention_days",
)
WS_FIELDS = ("allowed_hosts", "members_can_manage")


def normalize_hosts(hosts) -> list[str]:
    if not isinstance(hosts, list) or len(hosts) > MAX_HOSTS:
        raise WebhookApiError(
            "WEBHOOK_POLICY_INVALID",
            f"allowed_hosts takes at most {MAX_HOSTS} entries.",
            attr="allowed_hosts",
        )
    out: list[str] = []
    for h in hosts:
        if not isinstance(h, str):
            raise WebhookApiError("WEBHOOK_POLICY_INVALID", attr="allowed_hosts")
        v = h.strip().lower().removesuffix(".")
        try:
            v = v.encode("idna").decode("ascii") if any(ord(c) > 127 for c in v) else v
        except UnicodeError:
            v = ""
        if len(v) > 253 or not _HOST.fullmatch(v):
            raise WebhookApiError(
                "WEBHOOK_POLICY_INVALID",
                f"{h!r} is not a host name or a leftmost wildcard such as *.acme.ai.",
                attr="allowed_hosts",
            )
        if v not in out:
            out.append(v)
    return out


def covered(pattern: str, org_patterns: list[str]) -> bool:
    """Every host ``pattern`` matches is matched by some org pattern."""
    for o in org_patterns:
        if pattern == o:
            return True
        if o.startswith("*."):
            base = o[2:]
            inner = pattern[2:] if pattern.startswith("*.") else pattern
            if inner.endswith("." + base) or (
                pattern.startswith("*.") and inner == base
            ):
                return True
    return False


def represent(p: Policy | None, *, organization_id, workspace_id=None) -> dict:
    org = Policy.objects.filter(
        organization_id=organization_id, workspace__isnull=True
    ).first()
    profile = config.profile()
    base = {
        "url_policy": {
            "http_allowed": not profile.hosted and profile.allow_http,
            "private_addresses_blocked": profile.hosted
            or not (profile.private_cidrs or profile.allowed_private_hosts),
        },
        "scope": "workspace" if workspace_id else "organization",
        "workspace_id": None if workspace_id is None else str(workspace_id),
        "allowed_hosts": list(p.allowed_hosts) if p else [],
        "members_can_manage": p.members_can_manage if p else (workspace_id is not None),
        "updated_at": p.updated_at if p else None,
    }
    if workspace_id is None:
        base.update(
            full_payload_allowed=bool(p and p.full_payload_allowed),
            max_endpoints_per_workspace=p.max_endpoints_per_workspace if p else 50,
            log_retention_days=p.log_retention_days if p else 90,
        )
    lists = allowed_host_lists(organization_id, workspace_id)
    base["effective"] = {
        "org_allowed_hosts": lists.get("org_allowed_hosts", []),
        "workspace_allowed_hosts": lists.get("workspace_allowed_hosts", []),
        "members_can_manage": bool(org and org.members_can_manage)
        and (p is None or workspace_id is None or p.members_can_manage),
    }
    return base


def get_policy(caller, workspace_id=None) -> dict:
    caller.require(VIEW, workspace_id)
    p = Policy.objects.filter(
        organization_id=caller.organization_id,
        **(
            {"workspace_id": workspace_id}
            if workspace_id
            else {"workspace__isnull": True}
        ),
    ).first()
    return represent(
        p, organization_id=caller.organization_id, workspace_id=workspace_id
    )


def _snapshot(p: Policy | None, fields) -> dict:
    return {} if p is None else {f: getattr(p, f) for f in fields}


def put_policy(caller, data: dict, workspace_id=None) -> dict:
    org_id = caller.organization_id
    if workspace_id is None:
        if caller.org_level < Level.ADMIN or caller.pinned_workspace_id:
            raise WebhookApiError(
                "WEBHOOK_FORBIDDEN",
                "Only org admins can change the organization's webhook policy.",
            )
        fields = ORG_FIELDS
    else:
        caller.require(ADMIN, workspace_id)
        fields = WS_FIELDS
    if "allowed_hosts" in data:
        data = {**data, "allowed_hosts": normalize_hosts(data["allowed_hosts"])}
        if workspace_id is not None:
            org_hosts = allowed_host_lists(org_id, None).get("org_allowed_hosts", [])
            wide = [
                h
                for h in data["allowed_hosts"]
                if org_hosts and not covered(h, org_hosts)
            ]
            if wide:
                raise WebhookApiError(
                    "WEBHOOK_POLICY_INVALID",
                    f"{', '.join(wide)} is outside the organization's allowed hosts ({', '.join(org_hosts)}).",
                    attr="allowed_hosts",
                )
    with transaction.atomic():
        note_write(caller, workspace_id)
        p, created = Policy.objects.select_for_update().get_or_create(
            organization_id=org_id,
            workspace_id=workspace_id,
            defaults={"members_can_manage": workspace_id is not None},
        )
        before = {} if created else _snapshot(p, fields)
        for f in fields:
            if f in data:
                setattr(p, f, data[f])
        p.save()
        after = _snapshot(p, fields)
        audit.record(
            organization=caller.organization,
            action=audit.POLICY_UPDATED,
            target=p.id,
            actor=caller.user,
            changes=audit.diff(before, after),
            metadata={
                "scope": "workspace" if workspace_id else "organization",
                "workspace_id": workspace_id,
            },
        )
        downgraded = []
        if workspace_id is None and not p.full_payload_allowed:
            downgraded = downgrade_full(caller, p.id)
    blocked = []
    if "allowed_hosts" in data:
        blocked = reapply(caller, p.id, workspace_id)
    out = represent(p, organization_id=org_id, workspace_id=workspace_id)
    out["blocked_endpoint_ids"] = blocked
    out["downgraded_endpoint_ids"] = downgraded
    return out


def downgrade_full(caller, policy_id: str) -> list[str]:
    """Every full-mode endpoint of the org becomes ``thin`` (inside the policy's transaction), audited as an update
    with the reason. Returns their ids."""
    out = []
    ids = Endpoint.objects.filter(
        organization_id=caller.organization_id, deleted=False, payload_mode="full"
    ).order_by("id")
    for ep_id in list(ids.values_list("id", flat=True)):
        lock_state(ep_id)
        if Endpoint.objects.filter(id=ep_id, payload_mode="full").update(
            payload_mode="thin", updated_at=timezone.now()
        ):
            audit.record(
                organization=caller.organization,
                action=audit.ENDPOINT_UPDATED,
                target=ep_id,
                actor=caller.user,
                changes={"payload_mode": {"before": "full", "after": "thin"}},
                metadata={"reason": "full_payload_allowed_off", "policy_id": policy_id},
            )
            out.append(ep_id)
    return out


def reapply(caller, policy_id: str, workspace_id=None) -> list[str]:
    """Turn every enabled endpoint the new lists exclude to ``blocked_by_policy`` and pause its backlog."""
    qs = Endpoint.objects.filter(
        organization_id=caller.organization_id,
        deleted=False,
        status="enabled",
        kind="http",
    )
    if workspace_id is not None:
        qs = qs.filter(workspace_id=workspace_id)
    ids = list(qs.order_by("id").values_list("id", flat=True))
    if not ids:
        return []
    require_keys()
    profile, now = config.profile(), timezone.now()
    out = []
    for ep_id in ids:
        changed = 0
        with transaction.atomic():
            lock_state(ep_id)
            # The row as it is under the lock: a URL change or a turn-on that committed since the list was read is
            # judged here, never its old copy.
            ep = (
                Endpoint.objects.select_for_update()
                .filter(id=ep_id, deleted=False, status="enabled")
                .first()
            )
            if ep is None:
                continue
            lists = allowed_host_lists(ep.organization_id, ep.workspace_id)
            d = url_decision(
                crypto.decrypt(ep.url_encrypted) or "",
                profile,
                config.public_suffix,
                lists,
            )
            if d.allow or d.code != CODE_HOST_NOT_ALLOWED:
                continue
            changed = Endpoint.objects.filter(id=ep.id, status="enabled").update(
                status="blocked_by_policy", disabled_reason="policy", disabled_at=now
            )
            if changed:
                audit.record(
                    organization=caller.organization,
                    action=audit.ENDPOINT_BLOCKED,
                    target=ep.id,
                    actor=caller.user,
                    changes={
                        "status": {"before": "enabled", "after": "blocked_by_policy"}
                    },
                    metadata={
                        "policy_id": policy_id,
                        "rule": d.rule,
                        "host": ep.url_host,
                    },
                )
        if changed:
            pause_backlog(ep.id)
            out.append(ep.id)
    return out
