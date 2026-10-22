"""Which headers carry a credential, so the request log never keeps their values.

The gateway masks these headers before it sends a request's log entry
(agentcc-gateway/internal/credentials); ingestion masks them again on its
own, whatever the gateway sent, the request-log detail masks them on read,
and the stored-row scrub
(``agentcc_scrub_request_log_credentials``) uses the same rule. Both rules
are copies of the gateway's:

- ``CREDENTIAL_HEADERS``: the named list. Every header the gateway reads its
  key from, every header a provider or guardrail adapter sends a key in, and
  the common credential headers callers send.
- ``looks_like_credential``: a name that reads as a credential. The rule
  leans broad, but a name that only counts or describes tokens
  (prompt_tokens, Tokenizer, X-Token-Count, token_budget) is not a
  credential.

``api_contracts/gateway/credential-headers.json`` is the case table both
sides test against, so the copies cannot drift apart unnoticed.
"""

import re

MASK = "***"

CREDENTIAL_HEADERS = frozenset(
    {
        # Read as the gateway key: Authorization (and its Bearer value),
        # x-api-key, and x-goog-api-key on /v1beta.
        "authorization",
        "x-api-key",
        "x-goog-api-key",
        # HTTP authentication and sessions.
        "proxy-authorization",
        "www-authenticate",
        "proxy-authenticate",
        "cookie",
        "set-cookie",
        # Sent with a provider's or a guardrail service's key.
        "api-key",
        "ocp-apim-subscription-key",
        "x-amz-security-token",
        "x-vault-token",
        "x-secret-key",
        "x-aporia-api-key",
        "lasso-api-key",
        "grayswan-api-key",
        "apikey",
        # Further credential headers.
        "x-auth-token",
        "x-webhook-secret",
    }
)

# Parts of a name that make it read as a credential wherever they appear, in
# a name lower-cased with every underscore made a hyphen. Tokens, keys and
# "auth" are matched by their position instead (looks_like_credential), so
# that a count of tokens or a cache key is not one.
_CREDENTIAL_WORDS = (
    "authorization",
    "authorisation",
    "authenticat",
    "bearer",
    "secret",
    "password",
    "passwd",
    "passphrase",
    "credential",
    "cookie",
    "signature",
    "api-key",
    "apikey",
    "private-key",
    "access-key",
    "auth-key",
    "subscription-key",
)

# Header families whose every member may carry a credential: Portkey's
# forwarding headers carry a Portkey key, a virtual key, provider keys and
# configs holding them.
_CREDENTIAL_PREFIXES = ("x-portkey-",)

# Names that end in "-key" and carry no credential: the gateway's cache key
# and the idempotency key SDKs send.
_PLAIN_KEY_NAMES = frozenset(
    {
        "cache-key",
        "x-cache-key",
        "x-agentcc-cache-key",
        "idempotency-key",
        "x-idempotency-key",
    }
)

# The separators a name's last part follows (an underscore is a hyphen by
# then).
_LAST_PART = re.compile(r"[-.]")


def looks_like_credential(name):
    """Whether ``name`` reads as the name of a credential.

    Besides a credential word anywhere and the Portkey prefix, it reads the
    name's last part (what follows its last hyphen, underscore or dot): one
    that ends in "token" names a token ("tokens", "tokenizer", "token-count"
    and "token_budget" do not); one that is "key" after another part names a
    key, except the plain key names (a bare "key" does not); one that ends in
    "auth" after another part names an authenticator ("auth_type" does not).
    """
    normalized = str(name).lower().replace("_", "-")
    if any(word in normalized for word in _CREDENTIAL_WORDS):
        return True
    if normalized.startswith(_CREDENTIAL_PREFIXES):
        return True
    parts = _LAST_PART.split(normalized)
    last = parts[-1]
    if last.endswith("token"):
        return True
    if len(parts) == 1:
        return False
    if last == "key":
        return normalized not in _PLAIN_KEY_NAMES
    return last.endswith("auth")


def is_credential_header(name):
    """Whether the header named ``name`` carries a credential. Case-insensitive."""
    return str(name).lower() in CREDENTIAL_HEADERS or looks_like_credential(name)


def mask_credential_headers(headers):
    """``headers`` as the request log keeps them.

    The value of every credential header is ``MASK``, so the log shows that
    the header was sent and never what it held. Headers that are not an
    object cannot be checked name by name, so none of them is kept (``None``).
    """
    if headers is None:
        return None
    if not isinstance(headers, dict):
        return None
    return {
        name: MASK if is_credential_header(name) else value
        for name, value in headers.items()
    }
