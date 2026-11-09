"""Admin registration of a catalogue MCP OAuth app, before account sign-in.

Discovery uses the same resource-bound, SSRF-safe path as sign-in. No token is
minted here. Registration does not require a pre-authentication tool listing:
declared tool restrictions are preserved, and an unfiltered provider uses the
gateway's default access posture after connection. Existing apps are immutable
here: disconnect before changing provider or account mode.
"""

import copy
from collections.abc import Mapping

from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.services import config_push
from agentcc.services import connector_catalog as catalog
from agentcc.services import connector_oauth_clients as clients
from agentcc.services import mcp_credentials as vault
from agentcc.services import mcp_upstream_oauth as oauth
from agentcc.services.mcp_connect_clients import auth_method
from agentcc.services.org_config_writer import (
    is_gateway_synced,
    write_config_version_with,
)


def is_catalog_oauth(entry) -> bool:
    """Only the supported vendor MCP OAuth record bypasses catalogue holdback."""
    if not isinstance(entry, Mapping):
        return False
    record = catalog.provider_record(entry)
    return bool(
        record
        and record.get("kind") == "vendor_mcp"
        and record.get("auth", {}).get("kind") == "mcp_oauth"
        and record.get("transport", {}).get("kind") == "http"
    )


def _plan(app_id, provider_id=None):
    app = catalog.load_bundle().apps.get(app_id)
    if app is None:
        raise catalog.ConnectorNotFound(catalog.APP_NOT_FOUND, "No such catalogue app.")
    plan = app.plans.get(provider_id or app.default_provider)
    if provider_id is None and (
        not isinstance(plan, catalog.Plan) or plan.credential.kind != "oauth"
    ):
        plan = next(
            (
                p
                for p in app.plans.values()
                if isinstance(p, catalog.Plan) and p.credential.kind == "oauth"
            ),
            None,
        )
    if not isinstance(plan, catalog.Plan) or plan.credential.kind != "oauth":
        raise catalog.ConnectorConflict(
            "oauth_not_configurable",
            "This provider does not support MCP OAuth configuration.",
        )
    return app, plan


def configuration(org, app_id):
    app, plan = _plan(app_id)
    entry = catalog._servers(catalog._active_mcp(org)).get(app_id)
    configured = bool(
        entry and catalog._catalog_app(entry) == app_id and is_catalog_oauth(entry)
    )
    has_secret = (
        configured
        and AgentccMCPCredential.objects.filter(
            organization=org,
            server_id=clients.org_client_key(server_id=app_id),
            kind=AgentccMCPCredential.Kind.OAUTH_CLIENT,
            status=AgentccMCPCredential.Status.ACTIVE,
        ).exists()
    )
    return {
        "configured": configured,
        "mode": entry["credential"]["mode"]
        if configured
        else ("per_user" if "user" in plan.modes else "shared"),
        "registration": "manual" if has_secret else "dcr",
        "has_secret": bool(has_secret),
        "discovery_url": entry["url"] if configured else plan.url_template,
        "redirect_uri": oauth.callback_url(),
    }


def _target(app_id, data):
    app, plan = _plan(app_id, data.get("provider"))
    url, values = catalog.render_url(
        plan.url_template, plan.url_variables, data.get("url_variables", {}), None
    )
    # Never discover an unrelated AS supplied in place of the catalogue resource.
    if data["discovery_url"] != url:
        raise catalog.ConnectorInvalid(
            "discovery_url_mismatch",
            "Discovery URL must match this provider's MCP server URL and URL variables.",
        )
    return app, plan, url, values


def test_discovery(app_id, data):
    _, _, url, _ = _target(app_id, data)
    found = oauth.discover(url)
    return {
        "issuer": found.issuer,
        "dcr_supported": bool(found.registration_endpoint),
        "pkce_s256": True,
        "redirect_uri": oauth.callback_url(),
    }


def _can_add(org, app_id):
    mcp = catalog._active_mcp(org)
    catalog._refuse_add(mcp, app_id)
    # Registration must never implicitly switch MCP on.
    if mcp.get("enabled") is not True:
        raise catalog.ConnectorConflict(
            catalog.MCP_DISABLED,
            "Enable MCP for this organization before configuring OAuth.",
        )


def configure(org, app_id, data, actor):
    app, plan, url, values = _target(app_id, data)
    mode = data["mode"]
    account_mode = "user" if mode == "per_user" else "agent"
    if account_mode not in plan.modes:
        raise catalog.ConnectorInvalid(
            catalog.MODE_NOT_DECLARED,
            "This provider does not support the selected account mode.",
        )
    _can_add(org, app_id)
    found = oauth.discover(url)
    if data["registration"] == "dcr":
        if not found.registration_endpoint:
            raise oauth.ConnectRefused(
                "dcr_unavailable",
                "This authorization server does not support dynamic registration. Use a manual client.",
                409,
            )
    elif auth_method(None, found.auth_methods) is None:
        raise oauth.ConnectRefused(
            "manual_client_unavailable",
            "This authorization server does not accept a confidential client.",
            409,
        )

    server = {
        "url": url,
        "transport": plan.transport,
        "era": plan.era,
        "catalog_ref": plan.catalog_ref,
        # kind is a shared-credential discriminator. Per-user OAuth is
        # identified by catalog_ref, not a kind the config writer refuses.
        "credential": {"mode": mode, **({"kind": "oauth"} if mode == "shared" else {})},
    }
    if values:
        server["url_variables"] = values
    if plan.timeout_ms is not None:
        server["timeout_ms"] = plan.timeout_ms
    if plan.forward_headers:
        server["forward_headers"] = list(plan.forward_headers)

    def update(config):
        # The writer serializes this check and the client insertion on the org.
        _can_add(org, app_id)
        mcp = copy.deepcopy(config.mcp)
        mcp["servers"] = {**catalog._servers(mcp), app_id: server}
        mcp["virtual_servers"] = {
            **catalog._virtual_servers(mcp),
            app_id: catalog._virtual_server(app.card, list(plan.tools)),
        }
        config.mcp = mcp

    def push(config):
        # Runs after local config validation and save, inside its transaction.
        # A refused config never registers a client at the authorization server.
        # Registration/vault failure rolls the config back before a gateway push.
        if data["registration"] == "dcr":
            oauth._registered_client(found)
        else:
            vault.create_oauth_client(
                org,
                server_id=app_id,
                client_id=data["client_id"],
                client_secret=data["client_secret"],
                actor=actor,
            )
        return config_push.push_org_config(str(org.pk), config)

    config, warnings = write_config_version_with(
        org,
        update,
        user=actor,
        description=f"Configure MCP OAuth app {app_id}",
        push=push,
    )
    return {
        **configuration(org, app_id),
        "version": config.version,
        "gateway_synced": is_gateway_synced(warnings),
    }
