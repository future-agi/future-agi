"""K2 catalogue planning and adding, through the existing per-person OAuth flow.

The operator chooses the runtime, the manifest chooses the authorization
server, and the organization supplies its own registration. No client secret
or grant is put into a catalogue card. No unauthenticated probe or fictional
live baseline is used: the initial virtual server allows only the manifest's
explicit read-only tools. The ordinary OAuth start/callback/revoke routes own
all grants, including registration replacement and gateway eviction.

Known issue K2-ARCADE-1: Arcade is not offered in the catalogue. G14b's
``internal/mcp/vendors/arcade.go`` accepts only a customer-supplied gateway
with an EMPTY catalog_ref, MCP OAuth and an attested fixed tool list; its
registry is an inert token adapter, not a catalogue registration/sign-in
implementation. Do not invent vendor endpoints or advertise a dead card.
Enabling this requires authoritative customer-gateway admission and wiring
through the existing MCP OAuth start/revoke lifecycle, with end-to-end proof.
"""

import copy
from dataclasses import replace

from agentcc.services import connector_catalog as catalog
from agentcc.services import connector_k2 as k2
from agentcc.services import mcp_connections
from agentcc.services import microsoft_connect_gate as microsoft
from agentcc.services import mcp_upstream_oauth as oauth
from agentcc.services.org_config_writer import (
    is_gateway_synced,
    write_config_version_with,
)


def plan_for(app, provider):
    """An explicitly served pilot only; None leaves the usual refusal in place."""
    partner = provider.get("auth", {}).get("partner", {})
    if app.get("id") == "arcade" or partner.get("platform") == "arcade":
        # K2-ARCADE-1 above. A generic/unauthenticated manifest must not bypass
        # the adapter's explicit exclusion of catalogue-backed gateways.
        return catalog.Unavailable(
            "connector_arcade_catalogue_unavailable",
            "Arcade catalogue sign-in is not served; its customer-gateway "
            "adapter requires admission outside the catalogue",
        )
    base = k2.runtime_url()
    if base is None:
        return None
    entry = {
        "url": f"{base}/{app['id']}/mcp",
        "catalog_ref": f"{app['id']}/{provider['id']}@{provider['version']}",
        "credential": {"mode": "per_user"},
    }
    declared = k2.pilot(entry, provider=provider)
    if declared is None:
        return None
    # Reuse the ordinary planner's lifecycle, era, tools and placement checks.
    # Only the qualified pilot's runtime and authentication differ.
    projected = copy.deepcopy(provider)
    projected.update(kind="vendor_mcp", auth={"kind": "none"}, accountModes=["agent"])
    projected["transport"] = {
        "kind": "http",
        "url": entry["url"],
        "variant": "streamable",
    }
    plan = catalog._plan(app, projected)
    if isinstance(plan, catalog.Unavailable):
        return plan
    variables = catalog._url_variables(
        "",
        provider["auth"]["oauth2"].get("urlVariables", {}),
    )
    if isinstance(variables, catalog.Unavailable):
        return variables
    credential = catalog.CredentialPlan(
        mode="per_user", kind="oauth", label=f"{app['name']} account"
    )
    card = {
        **plan.card,
        "kind": "rest",
        "account_modes": ["user"],
        "credential": credential.card(),
        "url_variables": [
            {
                "name": v.name,
                "description": v.description,
                "pattern": v.pattern,
                "default": v.default,
            }
            for v in variables.values()
        ],
    }
    return replace(
        plan,
        kind="rest",
        declared_modes=("user",),
        modes=("user",),
        credential=credential,
        url_variables=variables,
        card=card,
    )


def server_entry(plan, values=None):
    return {
        "url": plan.url_template,
        "transport": plan.transport,
        "era": plan.era,
        "catalog_ref": plan.catalog_ref,
        "credential": {"mode": "per_user"},
        "url_variables": values or {},
    }


def provider_card(org, plan):
    """Org-dependent state is computed per request, never cached in the bundle."""
    entry = server_entry(plan)
    app_id = plan.catalog_ref.split("/", 1)[0]
    if microsoft.blocked(entry, app_id=app_id):
        return {
            **plan.card,
            "connect": {
                "served": False,
                "state": "unavailable",
                "reason": microsoft.CODE,
            },
        }
    declared = k2.pilot(entry)
    if declared is None:
        return None
    if not k2.gateway_serves(entry):
        # The gateway refuses every call to this pilot: keep the card, read
        # Coming soon, offer no sign-in. No registration is read.
        return {
            **plan.card,
            "connect": {
                "served": False,
                "state": "unavailable",
                "reason": k2.COMING_SOON,
            },
        }
    state, _ = k2.registration(org, app_id, entry)
    ready = state == k2.REGISTERED
    return {
        **plan.card,
        "connect": {
            "served": True,
            "state": "ready" if ready else "registration_required",
            "reason": ""
            if ready
            else (
                "oauth_client_unreadable"
                if state == k2.UNREADABLE
                else "org_oauth_app_required"
            ),
            "own_app": {
                "client_group": declared.client_group,
                "redirect_uri": oauth.callback_url(),
                "required": True,
            },
        },
    }


def _require_registration(org, app_id, entry):
    state, _ = k2.registration(org, app_id, entry)
    if state == k2.UNREADABLE:
        raise catalog.ConnectorConflict(
            "connector_oauth_org_client_unreadable",
            "The organization's OAuth app cannot be read; an Owner or Admin replaces it.",
        )
    if state != k2.REGISTERED:
        declared = k2.pilot(entry)
        raise catalog.ConnectorConflict(
            k2.ORG_CLIENT_REQUIRED,
            k2.required_message(app_id, declared.client_group),
        )


def add(org, app_id, app, plan, *, mode, values, actor, credential):
    from agentcc.mcp_release_gates import require_catalog_connect

    require_catalog_connect()
    if microsoft.blocked(server_entry(plan), app_id=app_id):
        raise catalog.ConnectorConflict(microsoft.CODE, microsoft.MESSAGE)
    if mode != "user":
        raise catalog.ConnectorInvalid(
            catalog.MODE_NOT_DECLARED,
            "This provider connects each person's own account.",
        )
    if credential is not None:
        raise catalog.ConnectorInvalid(
            catalog.CREDENTIAL_NOT_TAKEN,
            "This provider takes an OAuth sign-in, not a key.",
        )
    # Validate all declared variables without requiring a DNS-only runtime:
    # the operator may deliberately use a loopback/private runtime endpoint.
    unknown = set(values) - set(plan.url_variables)
    if unknown:
        raise catalog.ConnectorInvalid(
            catalog.URL_VARIABLE_UNKNOWN, "Unknown URL variable."
        )
    resolved = {}
    for name, variable in plan.url_variables.items():
        value = values.get(name, variable.default)
        if (
            not isinstance(value, str)
            or not catalog._URL_VARIABLE_VALUE.fullmatch(value)
            or not variable.regex.fullmatch(value)
        ):
            raise catalog.ConnectorInvalid(
                catalog.URL_VARIABLE_INVALID,
                f"url_variables.{name} does not match its declared pattern.",
            )
        resolved[name] = value
    entry = server_entry(plan, resolved)
    if k2.pilot(entry) is None or not k2.gateway_serves(entry):
        raise catalog.ConnectorConflict(
            catalog.OAUTH_NOT_BUILT, "This sign-in is not served."
        )
    _require_registration(org, app_id, entry)
    virtual = catalog._virtual_server(app.card, list(plan.tools))
    if not virtual["tools"]["include"]:
        raise catalog.ConnectorConflict(
            catalog.NO_TOOL_ALLOWED, "The manifest has no allowed read-only tools."
        )

    def updater(config):
        require_catalog_connect()
        mcp = copy.deepcopy(config.mcp) if isinstance(config.mcp, dict) else {}
        catalog._refuse_add(mcp, app_id)
        _require_registration(org, app_id, entry)
        mcp["enabled"] = True
        mcp["servers"] = {**catalog._servers(mcp), app_id: copy.deepcopy(entry)}
        mcp["virtual_servers"] = {
            **catalog._virtual_servers(mcp),
            app_id: copy.deepcopy(virtual),
        }
        config.mcp = mcp

    config, warnings = write_config_version_with(
        org,
        updater,
        description=f"Add OAuth connector {plan.catalog_ref}; manifest read-only tools",
        user=actor,
    )
    own = _own_connections(org, actor, app_id)
    return {
        "app": app_id,
        "provider": plan.card["id"],
        "catalog_ref": plan.catalog_ref,
        "server_id": app_id,
        "mode": "user",
        "url": entry["url"],
        "url_variables": resolved,
        "credential": {**plan.credential.card(), "connected": False},
        "virtual_server": {
            "slug": app_id,
            "id": config.mcp["virtual_servers"][app_id]["id"],
        },
        "tools": plan.card["tools"],
        "mcp_enabled": config.mcp.get("enabled") is True,
        "version": config.version,
        "gateway_synced": is_gateway_synced(warnings),
        "start_url": own[0]["start_url"] if own else None,
    }


def _own_connections(org, user, server_id=None):
    """Org-key catalogue readers have no individual account to start or expose.

    The lifecycle service also rechecks membership, so handle its typed refusal
    rather than doing a separate check which could race membership removal.
    Other failures must remain visible, not become an invented empty result.
    """
    try:
        return mcp_connections.own_connections(org, user, server_id)
    except oauth.ConnectRefused as refused:
        if refused.code != "not_member":
            raise
        return []


def decorate_servers(
    org, user, servers, *, static_server_ids=(), shared_oauth_server_ids=(),
):
    """Use OAuth readiness without replacing personal static credential state.

    The lifecycle service lists OAuth grants only. Absence from that list must
    not disconnect an active personal bearer, API-key or header credential.
    The caller derives the exceptions from its org/user-scoped active rows.
    """
    oauth_servers = []
    for rows in servers.values():
        for server in rows:
            personal = (server["credential_mode"] == "per_user"
                        and server["server_id"] not in static_server_ids)
            shared = (server["credential_mode"] == "shared"
                      and server["server_id"] in shared_oauth_server_ids)
            if (personal or shared) and microsoft.blocked(
                server, server_id=server["server_id"]
            ):
                # Keep the config/grant and cleanup affordances. Active custody
                # does not mean the gate will deliver this account's token.
                server.update(
                    connected=False, start_url=None,
                    start={"state": "unavailable", "reason": microsoft.CODE},
                )
            elif personal:
                oauth_servers.append(server)
    if not oauth_servers:
        return
    own = {row["server_id"]: row for row in _own_connections(org, user)}
    for server in oauth_servers:
        row = own.get(server["server_id"])
        server["connected"] = bool(row and row["connected"])
        server["start_url"] = row["start_url"] if row else None
