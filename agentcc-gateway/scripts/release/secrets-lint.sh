#!/usr/bin/env bash
# scripts/release/secrets-lint.sh <file>...
# Fails when a shipped gateway config holds a literal value shaped like a
# provider key, so a real key can never ship in config*.yaml or the scenario
# configs (verify-local.sh passes it that set).
#
# Rules (one finding per rule per line):
#   anthropic-key            sk-ant-...
#   openai-style-key         sk-... (20+ key characters; sk-proj-, sk-svcacct-)
#   google-api-key           AIza + 35
#   slack-token              xoxb-... / xoxp-...
#   github-pat               ghp_ + 36
#   github-fine-grained-pat  github_pat_...
#   aws-access-key-id        AKIA... / ASIA... + 16
#   private-key-pem          -----BEGIN ... PRIVATE KEY----- (PGP: ... PRIVATE KEY BLOCK)
#
# It reads YAML values, not comments: a `#` that starts a comment (outside a
# quoted scalar, at the start of a line or after a space) ends what is
# scanned, and lines inside a `|` or `>` block scalar are scanned whole. These
# pass: `${NAME}` references (a default, `${NAME:-literal}`, is still
# scanned), any key whose name ends in `_ref` (with its block scalar), a short
# placeholder such as `sk-...`, and PEM blocks that hold no private key
# (a PUBLIC KEY or CERTIFICATE is not a secret).
#
# Output: one `<file>:<line>: <rule> (<first 4 chars>...)` per finding, never
# the value itself. Exit 0 clean, 1 findings, 2 usage or an unreadable file.
set -euo pipefail

if [ $# -eq 0 ]; then
  echo "usage: secrets-lint.sh <file>..." >&2
  exit 2
fi

exec python3 -I - "$@" <<'PY'
import re
import sys

RULES = [
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-style-key", re.compile(r"(?<![A-Za-z0-9])sk-(?!ant-)[A-Za-z0-9_-]{20,}")),
    ("google-api-key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("slack-token", re.compile(r"xox[bp]-[0-9A-Za-z-]{10,}")),
    ("github-pat", re.compile(r"ghp_[0-9A-Za-z]{36}")),
    ("github-fine-grained-pat", re.compile(r"github_pat_[0-9A-Za-z_]{22,}")),
    ("aws-access-key-id", re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])")),
    ("private-key-pem", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")),
]
ENV_REF = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")
KEY = re.compile(r"^\s*(?:-\s+)?(?:\"([^\"]*)\"|'([^']*)'|([^\s#:\"'][^#:]*?))\s*:(?:\s|$)")
# `key: |`, `- >-`, or a lone indicator line: the value is a block scalar.
BLOCK_START = re.compile(r"(?:^|(?<=:)|(?<=-))\s*[|>][0-9+-]*\s*$")
# A quote opens a quoted scalar only where a scalar can start.
SCALAR_START = set(":-[{,?")


def indent_of(line):
    return len(line) - len(line.lstrip(" "))


def value_part(line, quote):
    """Return (text before any comment, quote state carried to the next line)."""
    out = []
    i = 0
    while i < len(line):
        ch = line[i]
        if quote == '"':
            if ch == "\\":
                out.append(line[i:i + 2])
                i += 2
                continue
            if ch == '"':
                quote = None
        elif quote == "'":
            if ch == "'":
                if line[i + 1:i + 2] == "'":
                    out.append("''")
                    i += 2
                    continue
                quote = None
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        elif ch in "\"'":
            before = "".join(out).rstrip()
            if not before or before[-1] in SCALAR_START:
                quote = ch
        out.append(ch)
        i += 1
    return "".join(out), quote


def scan(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError as err:
        print("secrets-lint: cannot read %s: %s" % (path, err.strerror), file=sys.stderr)
        return None
    findings = []
    quote = None
    block_parent = None  # indent of the line that opened the block scalar
    block_skipped = False  # the block belongs to a *_ref key
    for number, raw in enumerate(lines, 1):
        if block_parent is not None:
            if raw.strip() == "" or indent_of(raw) > block_parent:
                if not block_skipped:
                    findings.extend((number, rule, m) for rule, m in match(raw))
                continue
            block_parent = None
        text, quote = value_part(raw, quote)
        key = KEY.match(text)
        name = next((g for g in key.groups() if g is not None), "") if key else ""
        is_ref = name.strip().endswith("_ref")
        if quote is None and BLOCK_START.search(text):
            block_parent, block_skipped = indent_of(raw), is_ref
        if not is_ref:
            findings.extend((number, rule, m) for rule, m in match(text))
    return findings


def match(text):
    text = ENV_REF.sub("", text)
    for rule, pattern in RULES:
        found = pattern.search(text)
        if found:
            yield rule, found.group(0)


status = 0
for path in sys.argv[1:]:
    findings = scan(path)
    if findings is None:
        status = 2
        continue
    for number, rule, value in findings:
        print("%s:%d: %s (%s...)" % (path, number, rule, value[:4]))
        status = max(status, 1)
sys.exit(status)
PY
