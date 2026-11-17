"""Push-only credential lifetimes and grant revisions, read from the org's
vault without secrets."""

import copy

from django.utils import timezone

from agentcc.models.mcp_credential import AgentccMCPCredential


def with_current_lifetimes(org_id, mcp):
    """Overlay authoritative state/deadline/revision without changing config
    history.

    The gateway enforces the deadline independently of refresh schedules. A
    lapsed reference is never made active by an older vault/config snapshot.
    A shared static secret carries its slot's grant revision, which only the
    vault issues: a stored config never supplies one.
    """
    merged = copy.deepcopy(mcp)
    servers = merged.get("servers") or {}
    refs = {
        name: server.get("credential")
        for name, server in servers.items()
        if isinstance(server, dict) and isinstance(server.get("credential"), dict)
    }
    # Match the contract's accepted spelling order, like config_push.
    from agentcc.services.mcp_credentials import _managed_tools, _reference_id

    managed = _managed_tools(merged) or {}
    refs.update(
        (name, ref)
        for name, ref in (managed.get("credentials") or {}).items()
        if isinstance(ref, dict)
    )
    if not refs:
        return merged
    for ref in refs.values():
        ref.pop("revision", None)
    rows = AgentccMCPCredential.objects.filter(
        organization_id=org_id,
        scope=AgentccMCPCredential.Scope.SHARED,
        server_id__in=refs,
    ).only("id", "server_id", "status", "token_expires_at", "kind", "grant_revision")
    now = timezone.now()
    for row in rows:
        ref = refs[row.server_id]
        if str(row.pk) != _reference_id(ref):
            continue
        state = row.status
        # W4 OAuth tokens refresh through the just-in-time endpoint. Their
        # access-token deadline is not the shared account's grant lifetime.
        if (
            row.kind != AgentccMCPCredential.Kind.OAUTH
            and row.token_expires_at is not None
        ):
            ref["expires_at"] = row.token_expires_at.isoformat()
            if state == "active" and now >= row.token_expires_at:
                state = "expired"
        else:
            ref.pop("expires_at", None)
        if row.kind != AgentccMCPCredential.Kind.OAUTH and row.grant_revision:
            ref["revision"] = row.grant_revision
        if ref.get("state", "active") == "active":
            ref["state"] = state
        if ref.get("state") != "active":
            ref.pop("secret", None)
    return merged
