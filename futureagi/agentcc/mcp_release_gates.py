"""NEW W07 surfaces only. Shared credential storage and tenancy stay live.

Deployment switches are evaluated per request; organization configuration cannot
turn them on. Do not apply the vault switch to encryption, assembly or legacy
update-mcp-server: those paths already implement static authentication.
"""

from django.http import JsonResponse
from rest_framework.exceptions import ValidationError

from agentcc import mcp_features as features

# NEW: minimal off-state copy; site gate review required.
OFF = {
    features.CATALOG: (
        "mcp_catalog_disabled",
        "The MCP catalogue is off on this deployment.",
    ),
    features.CREDENTIAL_VAULT: (
        "mcp_credential_vault_disabled",
        "MCP credential management is off on this deployment.",
    ),
    features.VIRTUAL_SERVERS: (
        "mcp_virtual_servers_disabled",
        "Named MCP virtual servers are off on this deployment.",
    ),
}
CONNECT = (features.CATALOG, features.CREDENTIAL_VAULT, features.VIRTUAL_SERVERS)


def disabled(*names):
    return next((OFF[name] for name in names if not features.enabled(name)), None)


def unavailable(*names):
    refusal = disabled(*names)
    if refusal is None:
        return None
    code, message = refusal
    response = JsonResponse(
        {"status": False, "code": code, "message": message}, status=404
    )
    response["Cache-Control"] = "no-store"
    return response


def route_unavailable(request):
    directory = request.path_info.rstrip("/") + "/"
    if directory.startswith(
        ("/agentcc/connectors/catalog/", "/agentcc/connectors/apps/")
    ):
        # Reads only need catalogue. Any catalogue connection/configuration
        # action needs all three, including providers requiring no secret.
        names = (
            CONNECT
            if request.method not in {"GET", "HEAD", "OPTIONS"}
            else (features.CATALOG,)
        )
        return unavailable(*names)
    if directory.startswith("/agentcc/mcp/credentials/"):
        return unavailable(features.CREDENTIAL_VAULT)
    if (
        directory.startswith("/agentcc/mcp/service/virtual-servers/")
        or directory == "/agentcc/mcp/service/agent-keys/"
    ):
        return unavailable(features.VIRTUAL_SERVERS)
    # Existing static tool previews remain; explicit named previews do not.
    if (
        directory.startswith("/agentcc/gateways/")
        and directory.endswith(("/mcp-served-tools/", "/mcp-tools/"))
        and request.GET.get("vs")
    ):
        return unavailable(features.VIRTUAL_SERVERS)
    return None


def require_catalog_connect():
    # Use the service's typed error contract, including for non-HTTP callers.
    refusal = disabled(*CONNECT)
    if refusal:
        from agentcc.services.connector_catalog import ConnectorConflict

        raise ConnectorConflict(*refusal)


def refuse_disabled_config(value, stored, *, activation=False):
    """Called against locked prior state, before save/push and on activation.

    Unrelated static-server writes retain stored disabled data byte-for-byte.
    Named sections cannot be changed, removed or reactivated while OFF: deleting
    a stored key/default selection could widen it to the compatibility view.
    Key revocation does not rewrite this section and remains available.
    """
    value = value if isinstance(value, dict) else {}
    stored = stored if isinstance(stored, dict) else {}
    # Match MCPOrgConfig's AliasChoices precedence: the snake-case field wins
    # when present (including None/{}), not merely when truthy.
    new_virtual = value.get("virtual_servers", value.get("virtualServers")) or {}
    old_virtual = stored.get("virtual_servers", stored.get("virtualServers")) or {}
    if not features.enabled(features.VIRTUAL_SERVERS) and (
        (activation and new_virtual) or new_virtual != old_virtual
    ):
        code, message = OFF[features.VIRTUAL_SERVERS]
        raise ValidationError(message, code=code)
    if features.enabled(features.CATALOG):
        return
    new_servers = value.get("servers") or {}
    old_servers = stored.get("servers") or {}
    if not isinstance(new_servers, dict) or not isinstance(old_servers, dict):
        return  # Ordinary config validation rejects malformed sections.
    for key, entry in new_servers.items():
        if not isinstance(entry, dict):
            continue
        old = old_servers.get(key)
        tagged = (
            entry.get("catalog_ref")
            or entry.get("catalogRef")
            or (
                isinstance(old, dict)
                and (old.get("catalog_ref") or old.get("catalogRef"))
            )
        )
        if tagged and (activation or entry != old):
            code, message = OFF[features.CATALOG]
            raise ValidationError(message, code=code)
