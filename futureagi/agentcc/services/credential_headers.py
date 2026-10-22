"""Which headers carry a credential, so the request log never keeps their values.

The gateway masks these headers before it sends a request's log entry
(agentcc-gateway/internal/credentials); ingestion masks them again on its own,
whatever the gateway sent, and the stored-row scrub
(``agentcc_scrub_request_log_credentials``) uses the same rule. Both rules are
copies of the gateway's:

- ``CREDENTIAL_HEADERS``: the named list. Every header the gateway reads its
  key from, every header a provider or guardrail adapter sends a key in, and
  the common credential headers callers send.
- ``looks_like_credential``: a name that reads as a credential (a token, a
  secret, a password, an API key, a cookie, a vendor's key or auth header). The
  match is deliberately broad: it errs towards masking a header that holds
  nothing secret. "token" is read in every word of a name (x-access-token,
  X-Access-Token-V2, slack_token_prod); only a count of tokens (prompt_tokens,
  X-Token-Count, token_budget) and a tokenizer are left out. "key" and "auth"
  are read by the name's last words, so a lookup key (Idempotency-Key,
  cache_key) and auth_type are not taken for a credential.

``api_contracts/gateway/credential-headers.json`` is the case table both
sides test against, so the copies cannot drift apart unnoticed.
"""

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
        # Common token and webhook-secret headers.
        "x-auth-token",
        "x-webhook-secret",
    }
)

# Parts of a name that make it read as a credential, in a name lower-cased
# with every underscore made a hyphen. "token" is read by _names_a_token,
# which leaves out a count of tokens and a tokenizer; "key" and "auth" by the
# name's last words (_last_words_name_a_credential), which leaves out a
# lookup key and auth-type.
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


# The words before a final "key" that name a lookup key rather than a
# credential: x-agentcc-cache-key, Idempotency-Key.
_PLAIN_KEY_QUALIFIERS = frozenset(
    {
        "cache",
        "idempotency",
        "dedup",
        "dedupe",
        "deduplication",
        "partition",
        "sort",
        "routing",
        "lookup",
        "primary",
        "foreign",
        "public",
    }
)


# The words that, right after "token", make a name a count of tokens rather
# than a token: X-Token-Count, token_budget, X-Token-Limit, token_usage.
_TOKEN_COUNT_WORDS = frozenset(
    {
        "count",
        "budget",
        "limit",
        "usage",
        "used",
        "remaining",
        "total",
        "max",
        "min",
    }
)


def _names_a_token(name):
    """Whether ``name`` names a token: "token" anywhere in it, in any word
    (x-access-token, X-Access-Token-V2, slack-token-prod, X-CSRFToken,
    X-TokenValue). It leaves out only a count of tokens ("token" followed by
    "s", as in prompt-tokens, or by "-" and a word in _TOKEN_COUNT_WORDS, as in
    X-Token-Count and token-budget) and a tokenizer ("token" followed by "iz":
    Tokenizer, tokenize)."""
    start = 0
    while True:
        found = name.find("token", start)
        if found < 0:
            return False
        start = found + len("token")
        rest = name[start:]
        if rest.startswith(("s", "iz")):
            continue
        if rest.startswith("-") and rest[1:].split("-", 1)[0] in _TOKEN_COUNT_WORDS:
            continue
        return True


def _last_words_name_a_credential(name):
    """Whether the last words of ``name`` (split at "-") name a credential
    ("token" is _names_a_token's): a last word "auth" (X-Auth,
    Helicone-Auth); a last word "key" after a word that does not name a
    lookup key (X-OpenAI-Key, x-key). A bare "key" is a caller's
    dimension."""
    words = name.split("-")
    last = words[-1]
    if last == "auth":
        return True
    return last == "key" and len(words) > 1 and words[-2] not in _PLAIN_KEY_QUALIFIERS


def looks_like_credential(name):
    """Whether ``name`` reads as the name of a credential."""
    normalized = str(name).lower().replace("_", "-")
    return (
        any(word in normalized for word in _CREDENTIAL_WORDS)
        # Portkey's headers carry its key, a virtual key or a config that can
        # hold a provider's key inline.
        or normalized.startswith("x-portkey-")
        or _names_a_token(normalized)
        or _last_words_name_a_credential(normalized)
    )


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
