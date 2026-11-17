"""Django as the OAuth 2.1 authorization server for MCP clients (M24, M24x).

A person pastes one gateway MCP URL into Claude, Cursor or VS Code; the client
discovers this server, registers (pre-registered, a client ID metadata
document, or dynamic registration), signs the person in through the Future AGI
console, asks for consent naming the organization and the scopes, and gets a
15-minute ES256 access token bound to that one gateway resource (RFC 8707),
plus a refresh token that rotates on every use.

Modules, one concern each:

- ``config``: issuer, resource bases, lifetimes and scopes.
- ``metadata``: the RFC 8414 document.
- ``resources``: which gateway resources a person may ask for, per org.
- ``clients``: client resolution and redirect URI rules.
- ``cimd`` and ``dcr``: the two self-registration paths.
- ``authorize``: the authorization request, the pending consent and the code.
- ``tokens``: the code and refresh grants and the access-token claims.
- ``keys``: signing keys, rotation, JWKS and the push to the gateway.
- ``revocation``: RFC 7009 and consent revocation.
- ``views``: the HTTP surface.
"""
