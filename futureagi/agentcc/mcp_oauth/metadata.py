"""The authorization server metadata (RFC 8414), served path-inserted at
``/.well-known/oauth-authorization-server/agentcc/mcp-oauth`` and
``/.well-known/openid-configuration/agentcc/mcp-oauth``, and appended at
``/agentcc/mcp-oauth/.well-known/openid-configuration`` for clients that
look there.

PKCE is S256 only; ``plain`` is never advertised or accepted. The redirect
carries ``iss`` (RFC 9207).
"""

from agentcc.mcp_oauth import config

AUTH_METHODS = ["none", "client_secret_post", "client_secret_basic"]


def authorization_server_metadata() -> dict:
    return {
        "issuer": config.issuer(),
        "authorization_endpoint": config.endpoint("authorize"),
        "token_endpoint": config.endpoint("token"),
        "jwks_uri": config.endpoint("jwks.json"),
        "registration_endpoint": config.endpoint("register"),
        "revocation_endpoint": config.endpoint("revoke"),
        "scopes_supported": list(config.SCOPE_ORDER),
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": list(AUTH_METHODS),
        "revocation_endpoint_auth_methods_supported": list(AUTH_METHODS),
        "authorization_response_iss_parameter_supported": True,
        "client_id_metadata_document_supported": True,
    }
