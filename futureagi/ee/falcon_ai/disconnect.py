"""Falcon disconnect: erase all authentication state, then revoke if advertised."""

from urllib.parse import urlsplit

from ee.falcon_ai.credentials import decrypt_falcon_token
from integrations.services.disconnect import (
    _failed,
    _no_token,
    _unsupported,
)

CREDENTIAL_FIELDS = (
    "auth_header_value",
    "oauth_client_secret",
    "oauth_access_token",
    "oauth_refresh_token",
    "oauth_code_verifier",
    "oauth_state",
)


def capture_mcp_revocation(connector):
    if connector.auth_type != "oauth":
        return "mcp", _unsupported
    try:
        metadata = connector.oauth_server_metadata or {}
        endpoint = metadata.get("revocation_endpoint")
        if not endpoint:
            return "mcp", _unsupported
        parts = urlsplit(endpoint)
        # Never send a credential to plaintext HTTP or through a URL carrying
        # userinfo, a query, or a fragment. The transport also pins/guards DNS
        # and disables redirects, including redirects to a different provider.
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
        ):
            return "mcp", _failed
        refresh = connector.oauth_refresh_token
        token = decrypt_falcon_token(refresh or connector.oauth_access_token)
        if not token:
            return "mcp", _no_token
        client_id = connector.oauth_client_id
        secret = decrypt_falcon_token(connector.oauth_client_secret)
        hint = "refresh_token" if refresh else "access_token"
        # Registration uses client_secret_post; support an AS advertising only
        # Basic too. An unsupported client-auth method fails without disclosure.
        methods = metadata.get("revocation_endpoint_auth_methods_supported")
        method = "client_secret_post"
        if methods is not None:
            if "client_secret_post" not in methods:
                if "client_secret_basic" in methods:
                    method = "client_secret_basic"
                elif not secret and "none" in methods:
                    method = "none"
                else:
                    return "mcp", _failed
    except Exception:
        return "mcp", _failed

    def revoke():
        from ee.falcon_ai.oauth_client import HTTP_TIMEOUT
        from tfc.utils.ssrf_guard import guarded_httpx_client

        data = {"token": token, "token_type_hint": hint, "client_id": client_id}
        kwargs = {}
        if method == "client_secret_basic":
            kwargs["auth"] = (client_id, secret)
        elif secret:
            data["client_secret"] = secret
        with guarded_httpx_client(timeout=HTTP_TIMEOUT) as client:
            response = client.post(endpoint, data=data, **kwargs)
        # RFC 7009 succeeds even for an already-invalid token. Do not parse or
        # log the body: a provider can echo tokens there, including on failure.
        return (
            "REVOCATION_SUCCEEDED"
            if response.status_code == 200
            else "REVOCATION_FAILED"
        )

    return "mcp", revoke


def disconnect_mcp(connector):
    from ee.falcon_ai.oauth_registration import save_connector

    save_connector(
        connector,
        {
            **dict.fromkeys(CREDENTIAL_FIELDS, ""),
            "oauth_client_id": "",
            "oauth_server_metadata": {},
            "oauth_token_expires_at": None,
            "is_verified": False,
            "last_error": "",
        },
        erase=capture_mcp_revocation,
    )
