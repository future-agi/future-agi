"""Credential-shaped values and emails, found before anything is printed or
written.

The shapes are the release secrets lint's eight
(agentcc-gateway/scripts/release/secrets-lint.sh), plus what the cassette
scrubber (cmd/agentcc-cassette/cassette/secretshape.go) and the catalog
validator (internal/connectorcatalog/manifest/secrets.go) add: Stripe keys,
JWTs and Google OAuth client ids and secrets.
"""

from __future__ import annotations

import re

SHAPES = (
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-style-key", re.compile(r"(?<![A-Za-z0-9])sk-(?!ant-)[A-Za-z0-9_-]{20,}")),
    ("google-api-key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("slack-token", re.compile(r"xox[bp]-[0-9A-Za-z-]{10,}")),
    ("github-pat", re.compile(r"ghp_[0-9A-Za-z]{36}")),
    ("github-fine-grained-pat", re.compile(r"github_pat_[0-9A-Za-z_]{22,}")),
    (
        "aws-access-key-id",
        re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])"),
    ),
    (
        "private-key-pem",
        re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"),
    ),
    ("stripe-secret-key", re.compile(r"[sr]k_(?:live|test)_[0-9A-Za-z]{16,}")),
    (
        "jwt",
        re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ),
    (
        "google-oauth-client-id",
        re.compile(r"[0-9]{6,}-[a-z0-9]{32}[.]apps[.]googleusercontent[.]com"),
    ),
    ("google-oauth-client-secret", re.compile(r"GOCSPX-[A-Za-z0-9_-]{28}")),
)

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")

# The cassette scrubber's email placeholder: not an address.
PLACEHOLDER_EMAIL = re.compile(r"^user-[0-9]+@example\.com$")


def find_shapes(text: str) -> list[str]:
    """The names of the credential shapes in text; never the values."""
    return [name for name, pattern in SHAPES if pattern.search(text)]


def find_emails(text: str) -> int:
    """How many email addresses text holds, scrubber placeholders aside."""
    return sum(
        1 for m in EMAIL.finditer(text) if not PLACEHOLDER_EMAIL.match(m.group(0))
    )


def find_values(text: str, values) -> int:
    """How many of the given secret values text holds."""
    return sum(1 for v in values if v and v in text)
