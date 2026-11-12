"""Microsoft connection policy, separate from custody of the platform client.

The connect setting is fail-closed and permits only org-owned registrations.
Lifting platform exclusion requires a reviewed code change whose commit cites
an owner rotation record. No environment variable or setting can lift it.
"""
from collections.abc import Mapping

from agentcc import mcp_features
from agentcc.services.microsoft_client_identity import microsoft_origin

MICROSOFT_CONNECT_ENABLED = "MICROSOFT_CONNECT_ENABLED"
MICROSOFT_PLATFORM_CLIENT_LIFTED = False
CODE = "microsoft_coming_soon"
MESSAGE = "Microsoft connections are coming soon."
# The refusal for a partner binding set up before the hold, which it now stops.
BINDING_MESSAGE = (
    "This connection stopped working after an update and cannot be used until "
    "Microsoft connections are available; remove it or keep it for later."
)
# The refusal for a grant with no recorded issuing client from a Microsoft
# host or issuer (``unproven_microsoft_grant``), which the hold now stops: it
# needs a new sign-in, which the hold also stops from starting.
GRANT_MESSAGE = (
    "This connection stopped working after an update and cannot be connected "
    "again yet; Disconnect removes it."
)
ORG_CLIENT_MESSAGE = "This connection requires the organization's own OAuth client."


def _name(value):
    return isinstance(value, str) and (
        value == "microsoft" or value.startswith("microsoft-")
    )


def _added_by_url(entry):
    """A server the organization added by URL: a URL and no catalogue
    reference key at all (a malformed or retired reference is still one)."""
    return (
        "catalog_ref" not in entry
        and "catalogRef" not in entry
        and isinstance(entry.get("url"), str)
    )


def is_microsoft(
    entry=None, *, app_id=None, client_group=None, server_id=None, issuer=None
):
    """Match declared group and app identity, including retired catalogue refs.

    Never discover a provider or read credentials to classify it. A server
    added by URL is Microsoft when its URL host, or the OAuth issuer stored
    for it (``issuer``), is a Microsoft host (the hosts ``deny_if_excluded``
    reads); its id is the organization's own name for it and matches nothing.
    For any other entry, a catalogue one, ``{}`` or none, server ids stay an
    additional conservative match, not a substitute for the catalogue.
    """
    if client_group == "microsoft" or _name(app_id):
        return True
    if not isinstance(entry, Mapping):
        return _name(server_id)
    if _name(entry.get("app")) or entry.get("clientGroup") == "microsoft":
        return True
    auth = entry.get("auth")
    spec = auth.get("oauth2") if isinstance(auth, Mapping) else None
    if isinstance(spec, Mapping) and spec.get("clientGroup") == "microsoft":
        return True
    if _added_by_url(entry):
        return microsoft_origin(entry["url"]) or microsoft_origin(issuer)
    if _name(server_id):
        return True
    ref = entry.get("catalog_ref") or entry.get("catalogRef")
    if not isinstance(ref, str) or not ref:
        return False
    if _name(ref.split("/", 1)[0]):
        return True
    from agentcc.services.connector_catalog import provider_record

    record = provider_record(entry)
    # A provider record has no catalogue reference; do not recurse on input.
    if not isinstance(record, Mapping):
        return False
    auth = record.get("auth")
    spec = auth.get("oauth2") if isinstance(auth, Mapping) else None
    return _name(record.get("app")) or (
        isinstance(spec, Mapping) and spec.get("clientGroup") == "microsoft"
    )


def blocked(entry=None, **identity):
    return is_microsoft(entry, **identity) and not mcp_features.enabled(
        MICROSOFT_CONNECT_ENABLED
    )


def require_microsoft_connect_enabled(entry=None, **identity):
    if blocked(entry, **identity):
        # Lazy to avoid the connect/client/vault import cycle.
        from agentcc.services.mcp_upstream_oauth import ConnectRefused

        raise ConnectRefused(CODE, MESSAGE, 409)


def platform_allowed(entry=None, **identity):
    """Independent of MICROSOFT_CONNECT_ENABLED, even when that gate is ON."""
    return MICROSOFT_PLATFORM_CLIENT_LIFTED is True or not is_microsoft(
        entry, **identity
    )


def require_org_client_identity(client_id, entry=None, **identity):
    """A current org row cannot turn the excluded platform ID into an org app.

    Check before reading any secret, including when re-resolving a legacy
    token/state. The gate-OFF branch must not even read identity metadata.
    """
    require_microsoft_connect_enabled(entry, **identity)
    from agentcc.services.microsoft_client_identity import deny_if_excluded

    deny_if_excluded(client_id)
