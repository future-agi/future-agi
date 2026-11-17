"""RFC 7009 revocation outcomes. Never turn a refusal into confirmation.

A K2 grant (``connector_k2``) is revoked only as the org's own registration
that issued it: the platform client is never resolved for it, even one with
the same client id, and whether or not the runtime is still on. The grant
records this when it is stored (``oauth_org_only``, passed here as
``org_only``), so a later configuration that puts another provider, or none,
under its server id cannot turn it into a platform client's grant. A grant
stored before that column also counts as K2 while its server's current entry
is a pilot's. While that registration is gone, replaced or unreadable,
nothing is sent and the revocation stays pending (Disconnect retries it).
A grant from a vendor MCP server that signs in only with the organization's
own OAuth app (``connector_org_app_oauth``) is revoked the same way, at the
endpoint its provider declares. While that declared server cannot be read,
nothing is discovered or sent and the revocation stays pending.
"""

from dataclasses import dataclass

from agentcc.services import connector_k2, connector_org_app_oauth
from agentcc.services import mcp_connect_clients as clients
from agentcc.services.credential_manager import decrypt_token

# A K2 grant whose issuing org registration is gone or replaced, or cannot
# be read (an Owner or Admin replaces it).
ORG_REGISTRATION_MISSING = "org_registration_missing"
ORG_REGISTRATION_UNREADABLE = "org_registration_unreadable"
# A grant from a server that signs in only with the organization's own OAuth
# app, whose declared sign-in cannot be read now.
DECLARED_SERVER_UNREADABLE = "declared_server_unreadable"


@dataclass(frozen=True)
class Revocation:
    outcome: str
    reason: str = ""


def revoke(
    org,
    server_id,
    issuer,
    client_id,
    encrypted_refresh,
    encrypted_access,
    *,
    org_only=False,
):
    """``org_only``: the grant's own record that the org's registration issued
    it (K2). Its server's current entry can add that, never take it away."""
    from agentcc.services import mcp_upstream_oauth as connect

    try:
        from agentcc.services import microsoft_connect_gate as microsoft

        from agentcc.models.mcp_credential import AgentccMCPCredential
        from agentcc.services.microsoft_client_identity import deny_if_excluded

        entry = connect.server_entry(org.pk, server_id, include_hidden=True)
        microsoft.require_org_client_identity(client_id, entry, server_id=server_id)
        origins = [issuer, (entry or {}).get("url")]
        if not client_id:
            # Legacy grants can have no issuing ID. Use stored provenance, not
            # just mutable current server labels, before any token decryption.
            origins.extend(AgentccMCPCredential.objects.filter(
                organization=org, server_id=server_id, kind="oauth", oauth_client_id="",
            ).values_list("origin", flat=True))
        deny_if_excluded(client_id, origins=origins)
        entry = connect.server_entry(org.pk, server_id)
        declared = connector_k2.pilot(entry) or connector_org_app_oauth.declared(entry)
        if declared is None and connector_org_app_oauth.is_org_app_server(entry):
            # Never send the organization's secret to an endpoint read from
            # metadata in place of the declared one.
            return Revocation("pending", DECLARED_SERVER_UNREADABLE)
        if declared is not None:  # the provider's declared endpoint, or none
            endpoint, auth_methods = declared.revocation_url, None
        else:
            meta = connect.authorization_server_metadata(issuer)
            endpoint = connect.endpoint_of(meta, "revocation_endpoint", issuer)
            auth_methods = meta.get("revocation_endpoint_auth_methods_supported")
        if not endpoint:
            return Revocation("unconfirmed", "unsupported")
        # A K2 grant stays the org registration's after the runtime is off, and
        # after its server's configuration is replaced or removed.
        org_only = (
            org_only or declared is not None or connector_k2.pilot_provider(entry)
        )
        client, ref = clients.issuing(
            org,
            server_id,
            entry,
            client_id,
            auth_methods=auth_methods,
            org_only=org_only,
        )
        if org_only and client.kind != clients.ORG:
            return Revocation("pending", _registration_gone(org, server_id, entry))
        response = clients.post(
            endpoint,
            {
                "token": decrypt_token(encrypted_refresh or encrypted_access),
                "token_type_hint": "refresh_token"
                if encrypted_refresh
                else "access_token",
            },
            client,
            ref,
            max_bytes=connect.METADATA_LIMIT,
        )
        if response is None:
            return Revocation("pending", "transport_failed")
        if response.status == 200:
            return Revocation("confirmed")
        # A definitive refusal is not retryable with this old grant/client.
        # Throttles, timeouts and server failures remain retryable until the
        # member supersedes the account by completing a fresh sign-in.
        terminal = 400 <= response.status < 500 and response.status not in (408, 429)
        return Revocation(
            "unconfirmed" if terminal else "pending", f"http_{response.status}"
        )
    except Exception:
        # No exception text: it may contain an upstream response or credential.
        return Revocation("pending", "revocation_failed")


def _registration_gone(org, server_id, entry) -> str:
    """Why a K2 grant's registration does not resolve; never a platform read."""
    if entry is not None:
        state, _ = connector_k2.registration(org, server_id, entry)
        if state == connector_k2.UNREADABLE:
            return ORG_REGISTRATION_UNREADABLE
    return ORG_REGISTRATION_MISSING
