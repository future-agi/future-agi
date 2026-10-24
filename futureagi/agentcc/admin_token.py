"""The admin-token policy, shared with the gateway.

The gateway refuses to start on an admin token anyone could guess
(agentcc-gateway/internal/config/admin_token.go). Django's gateway admin
endpoints accept the same credential and hold it to the same policy: a
token the gateway would refuse counts as no token, and every admin request
is denied.

The lists are the gateway's, written once on each side.
agentcc/tests/test_admin_token_parity.py checks the two copies are equal and
that both give the same verdict on
agentcc-gateway/internal/config/testdata/admin_token_cases.json.
"""

import os

import structlog

logger = structlog.get_logger(__name__)

# Local development and the E2E stack only, as on the gateway: an admin token
# the policy refuses is accepted anyway, with a warning.
ENV_ALLOW_INSECURE_ADMIN_TOKEN = "AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN"

MIN_ADMIN_TOKEN_LENGTH = 32
MIN_ADMIN_TOKEN_DISTINCT_CHARS = 8

# Defaults published in this repository or its docs; a token that
# contains one is refused.
PUBLISHED_ADMIN_TOKENS = (
    "agentcc-admin-secret",
    "local-dev-only-admin-token-replace-me",
    "local-dev-only-shared-secret-replace-me",
    "your-secret-admin-token",
    "test-admin-secret",
    "fi-internal-dev-key-change-in-prod",
    "test-webhook-secret",
)

# Refused when the whole token is one of them.
COMMON_ADMIN_TOKENS = frozenset(
    {
        "admin",
        "admin-token",
        "admin-secret",
        "secret",
        "password",
        "token",
        "test",
        "changeme",
        "default",
    }
)

# Words that mark a value someone was meant to replace.
PLACEHOLDER_ADMIN_TOKEN_MARKERS = (
    "changeme",
    "change-me",
    "change_me",
    "change-in-prod",
    "replaceme",
    "replace-me",
    "replace_me",
    "your-secret",
    "your_secret",
    "your-token",
    "your_token",
    "your-admin",
    "your_admin",
    "placeholder",
    "example",
    "not-for-production",
    "dev-only",
)


def admin_token_problem(token):
    """What is wrong with ``token`` as an admin credential, or "" when it is
    acceptable. Never quotes the token."""
    t = (token or "").strip()
    lower = t.lower()
    if not t:
        return "is empty"
    if any(published in lower for published in PUBLISHED_ADMIN_TOKENS):
        return "is or contains a default published with the gateway"
    if lower in COMMON_ADMIN_TOKENS:
        return "is a default anyone would try"
    if any(marker in lower for marker in PLACEHOLDER_ADMIN_TOKEN_MARKERS):
        return "is a placeholder (it contains a word such as changeme or replace-me)"
    if len(t) < MIN_ADMIN_TOKEN_LENGTH:
        return f"is shorter than {MIN_ADMIN_TOKEN_LENGTH} characters"
    if len(set(t)) < MIN_ADMIN_TOKEN_DISTINCT_CHARS:
        return "has too few distinct characters to be random"
    return ""


def insecure_admin_token_allowed(value=None):
    """Whether the dev-only opt-in is on. The gateway reads it with Go's
    strconv.ParseBool, so the same values turn it on here: 1, t and true in
    any case."""
    raw = os.environ.get(ENV_ALLOW_INSECURE_ADMIN_TOKEN, "") if value is None else value
    return raw.strip().lower() in {"1", "t", "true"}


_warned = set()


def _warn_once(event, problem):
    if (event, problem) in _warned:
        return
    _warned.add((event, problem))
    logger.warning(event, problem=problem, env=ENV_ALLOW_INSECURE_ADMIN_TOKEN)


def usable_admin_token(token, allow_insecure):
    """``token`` when Django may accept it on its admin endpoints, else "".

    An empty token is never accepted. A token the policy refuses is accepted
    only with the dev-only opt-in; either way the first request after start
    logs a warning that names the problem, never the token."""
    if not token:
        return ""
    problem = admin_token_problem(token)
    if not problem:
        return token
    if allow_insecure:
        _warn_once(
            "AGENTCC_ADMIN_TOKEN is insecure and accepted on the gateway admin "
            "endpoints only because the dev-only opt-in is set; local "
            "development only",
            problem,
        )
        return token
    _warn_once(
        "AGENTCC_ADMIN_TOKEN is insecure: the gateway admin endpoints deny "
        "every request until it is at least 32 random characters "
        "(openssl rand -hex 32), the same value as the gateway's",
        problem,
    )
    return ""
