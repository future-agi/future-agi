"""Admin registration of a catalogue MCP OAuth app, before account sign-in.

Discovery uses the same resource-bound, SSRF-safe path as sign-in. No token is
minted here. Registration does not require a pre-authentication tool listing:
declared tool restrictions are preserved, and an unfiltered provider uses the
gateway's default access posture after connection. Existing apps are immutable
here: disconnect before changing provider or account mode.

A provider whose OAuth takes only a pre-registered client
(``connector_catalog.oauth2_by_org_app``) registers the organization's own
app as a manual client, for each person's own account. Nothing is discovered
for it: its authorization server is the one its record declares
(``connector_org_app_oauth``).
"""

import copy
from collections.abc import Mapping

from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.services import config_push
from agentcc.services import microsoft_connect_gate as microsoft
from agentcc.services import connector_catalog as catalog
from agentcc.services import connector_oauth_clients as clients
from agentcc.services import connector_org_app_oauth as org_app
from agentcc.services import mcp_credentials as vault
from agentcc.services import mcp_upstream_oauth as oauth
from agentcc.services.mcp_connect_clients import auth_method
from agentcc.services.org_config_writer import (
    is_gateway_synced,
    write_config_version_with,
)


def is_catalog_oauth(entry) -> bool:
    """Only a discovered vendor MCP OAuth record (``mcp_oauth``, or ``oauth2``
    with dcr or cimd, ``catalog.oauth2_by_discovery``), or a per-person server
    of a record that signs in with the organization's own OAuth app and whose
    declared sign-in reads (``connector_org_app_oauth``), bypasses catalogue
    holdback."""
    if not isinstance(entry, Mapping):
        return False
    record = catalog.provider_record(entry)
    discovered = bool(
        record
        and record.get("kind") == "vendor_mcp"
        and (
            record.get("auth", {}).get("kind") == "mcp_oauth"
            or catalog.oauth2_by_discovery(record)
        )
        and record.get("transport", {}).get("kind") == "http"
    )
    return discovered or org_app.declared(entry) is not None


def _org_app_record(app_id, plan) -> bool:
    """Whether ``plan`` is of a provider that signs in only with the
    organization's own OAuth app."""
    record = catalog.load_bundle().providers.get((app_id, plan.card["id"]))
    return catalog.oauth2_by_org_app(record)


def _org_app_entry(plan, url, values) -> dict:
    """The per-person server entry such a plan is added as."""
    entry = {
        "url": url,
        "catalog_ref": plan.catalog_ref,
        "credential": {"mode": "per_user"},
    }
    if values:
        entry["url_variables"] = values
    return entry


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
    manual = has_secret or _org_app_record(app_id, plan)
    return {
        "configured": configured,
        "mode": entry["credential"]["mode"]
        if configured
        else ("per_user" if "user" in plan.modes else "shared"),
        "registration": "manual" if manual else "dcr",
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


def _declared(app_id, plan, url, values):
    """The declared sign-in of a provider that signs in only with the
    organization's own OAuth app, for the server it would be added as."""
    declared = org_app.declared(_org_app_entry(plan, url, values))
    if declared is None:
        raise catalog.ConnectorConflict(
            "oauth_not_configurable",
            "This provider's declared OAuth sign-in cannot be served.",
        )
    return declared


def test_discovery(app_id, data):
    microsoft.require_microsoft_connect_enabled(app_id=app_id)
    _, plan, url, values = _target(app_id, data)
    microsoft.require_microsoft_connect_enabled({"catalog_ref": plan.catalog_ref})
    if _org_app_record(app_id, plan):
        # The declared authorization server, read from the catalog: nothing
        # is fetched, and only the organization's own app signs in.
        declared = _declared(app_id, plan, url, values)
        return {
            "issuer": declared.issuer,
            "dcr_supported": False,
            "pkce_s256": True,
            "redirect_uri": oauth.callback_url(),
        }
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
    from agentcc.mcp_release_gates import require_catalog_connect

    require_catalog_connect()
    microsoft.require_microsoft_connect_enabled(app_id=app_id)
    app, plan, url, values = _target(app_id, data)
    microsoft.require_microsoft_connect_enabled({"catalog_ref": plan.catalog_ref})
    declared = None
    if _org_app_record(app_id, plan):
        if data["registration"] != "manual":
            raise oauth.ConnectRefused(
                "dcr_unavailable",
                f"{app.card['name']} signs in only with your organization's own "
                "OAuth app. Register it as a manual client.",
                409,
            )
        declared = _declared(app_id, plan, url, values)
    if data["registration"] == "manual":
        try:
            microsoft.require_org_client_identity(
                data["client_id"], {"catalog_ref": plan.catalog_ref}, app_id=app_id
            )
        except clients.OrgClientRequired:
            # Refuse before the config writer/push reads any credential or
            # discovers an upstream. The vault repeats the guard on insertion.
            raise oauth.ConnectRefused(
                clients.OrgClientRequired.code, microsoft.ORG_CLIENT_MESSAGE, 409
            ) from None
    mode = data["mode"]
    account_mode = "user" if mode == "per_user" else "agent"
    if account_mode not in plan.modes:
        raise catalog.ConnectorInvalid(
            catalog.MODE_NOT_DECLARED,
            "This provider does not support the selected account mode.",
        )
    _can_add(org, app_id)
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
        require_catalog_connect()
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
        # Runs after local validation/save, still under the writer's org lock.
        # Use the same placement check as push_org_config's _build_payload,
        # before discovery or registration can contact the authorization server.
        # A later transport failure keeps the save and reports a sync warning;
        # an external registration itself cannot be rolled back by the database.
        config_push._mcp_payload(
            str(org.pk),
            config,
            with_upstream_keys=True,
            refuse_unapplied_placements=True,
        )
        if declared is not None:
            # The declared server takes the organization's own app by its
            # declared client authentication; there is nothing to discover.
            vault.create_oauth_client(
                org,
                server_id=app_id,
                client_id=data["client_id"],
                client_secret=data["client_secret"],
                actor=actor,
            )
            return config_push.push_org_config(str(org.pk), config)
        found = oauth.discover(url)
        if data["registration"] == "dcr":
            if not found.registration_endpoint:
                raise oauth.ConnectRefused(
                    "dcr_unavailable",
                    "This authorization server does not support dynamic registration. Use a manual client.",
                    409,
                )
            oauth._registered_client(found)
        else:
            if auth_method(None, found.auth_methods) is None:
                raise oauth.ConnectRefused(
                    "manual_client_unavailable",
                    "This authorization server does not accept a confidential client.",
                    409,
                )
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
