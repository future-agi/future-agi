"""Secret-shaped values: the rules of the catalog loader's scan
(agentcc-gateway/internal/connectorcatalog/manifest/secrets.go), so a spec,
an overlay or an output the generator handles is refused for the same
shapes the loader refuses. A finding names the rule, never the value."""

from __future__ import annotations

import re

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-style-key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("google-api-key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("slack-token", re.compile(r"xox[bp]-[0-9A-Za-z-]{10,}")),
    ("github-pat", re.compile(r"ghp_[0-9A-Za-z]{36}")),
    ("github-fine-grained-pat", re.compile(r"github_pat_[0-9A-Za-z_]{22,}")),
    ("aws-access-key-id", re.compile(r"(AKIA|ASIA)[0-9A-Z]{16}")),
    (
        "private-key-pem",
        re.compile(r"-----BEGIN ([A-Z0-9]+ )*PRIVATE KEY( BLOCK)?-----"),
    ),
    (
        "google-oauth-client-id",
        re.compile(r"[0-9]{6,}-[a-z0-9]{32}[.]apps[.]googleusercontent[.]com"),
    ),
    ("google-oauth-client-secret", re.compile(r"GOCSPX-[A-Za-z0-9_-]{28}")),
]


def _alnum(c: str) -> bool:
    return c.isascii() and c.isalnum()


def _upper_or_digit(c: str) -> bool:
    return c.isascii() and (c.isupper() or c.isdigit())


def _boundary_ok(rule: str, s: str, i: int, j: int) -> bool:
    if rule == "openai-style-key":
        return (i == 0 or not _alnum(s[i - 1])) and not s.startswith("sk-ant-", i)
    if rule == "aws-access-key-id":
        return (i == 0 or not _upper_or_digit(s[i - 1])) and (
            j == len(s) or not _upper_or_digit(s[j])
        )
    return True


def find_secret(text: str) -> str:
    """The first rule a key shape in text matches, or ""."""
    for name, pattern in _RULES:
        start = 0
        while True:
            m = pattern.search(text, start)
            if m is None:
                break
            if _boundary_ok(name, text, m.start(), m.end()):
                return name
            start = m.start() + 1
    return ""


def find_secret_line(text: str) -> tuple[str, int]:
    """The first rule matched and its 1-based line, or ("", 0)."""
    for n, line in enumerate(text.split("\n"), start=1):
        rule = find_secret(line)
        if rule:
            return rule, n
    return "", 0
