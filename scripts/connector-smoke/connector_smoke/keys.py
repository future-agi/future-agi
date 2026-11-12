"""The keys document: test-account credentials from the CI secret store.

The runner reads it from the CONNECTOR_SMOKE_KEYS environment variable, never
from a file in the repository or the command line:

    {
      "format": "connector-smoke-keys/v1",
      "providers": {
        "<app-id>/<provider-id>": {
          "testAccount": "fa-ci-linear",          # required: names the account
          "bearer": "...",                        # oauth2 or mcp_oauth: the account's access token
          "parts": {"<credential part name>": "..."},   # api_key: a value per part
          "urlVariables": {"<variable>": "..."},  # transport URL variables
          "sandbox": false,                       # true: a sandbox tenant, where writes may run
          "arguments": {"<tool>": {...}}          # live arguments for a tool call
        }
      }
    }

Every entry must name its test account: the runner records nothing for an
account it cannot name. Parsing is strict (unknown fields are refused), and no
error message ever quotes a value.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import secretscan

KEYS_ENV = "CONNECTOR_SMOKE_KEYS"
FORMAT = "connector-smoke-keys/v1"

# CN2's test-account label (cmd/agentcc-cassette/cassette/format.go): a name
# for the account, never its login, email or credential.
TEST_ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,62}$")
PROVIDER_KEY = re.compile(r"^([a-z][a-z0-9-]{1,39})/([a-z][a-z0-9-]{1,23})$")
VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")
TOOL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
ENTRY_FIELDS = frozenset(
    {"testAccount", "bearer", "parts", "urlVariables", "sandbox", "arguments"}
)

# A secret shorter than this is not searched for in free text (it would match
# ordinary words); it is still never printed, by construction.
MIN_SCANNED_SECRET = 8


class KeysError(ValueError):
    """The keys document is malformed. The message names a field, never a value."""


@dataclass(frozen=True)
class KeyEntry:
    app: str
    provider: str
    test_account: str
    bearer: str | None = None
    parts: dict = field(default_factory=dict)
    url_variables: dict = field(default_factory=dict)
    sandbox: bool = False
    arguments: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Keys:
    entries: dict  # "<app>/<provider>" -> KeyEntry

    def entry(self, app: str, provider: str) -> KeyEntry | None:
        return self.entries.get(f"{app}/{provider}")


def _header_safe(value) -> bool:
    return (
        isinstance(value, str)
        and value != ""
        and len(value) <= 8192
        and not any(ord(c) < 0x20 or ord(c) == 0x7F for c in value)
    )


def _entry(key: str, raw) -> KeyEntry:
    at = f"providers.{key}"
    m = PROVIDER_KEY.match(key)
    if not m:
        raise KeysError(f"{at}: the key must be <app-id>/<provider-id>")
    if not isinstance(raw, dict):
        raise KeysError(f"{at}: must be an object")
    unknown = sorted(set(raw) - ENTRY_FIELDS)
    if unknown:
        raise KeysError(f"{at}: unknown field(s) {', '.join(unknown)}")
    label = raw.get("testAccount")
    if not isinstance(label, str) or not TEST_ACCOUNT.match(label):
        raise KeysError(
            f"{at}.testAccount: required, and must match {TEST_ACCOUNT.pattern} "
            "(a name for the test account, not an email or a credential)"
        )
    if secretscan.find_shapes(label):
        raise KeysError(f"{at}.testAccount: is shaped like a credential")
    bearer = raw.get("bearer")
    if bearer is not None and not _header_safe(bearer):
        raise KeysError(
            f"{at}.bearer: must be a non-empty string without control characters"
        )
    parts = raw.get("parts", {})
    if not isinstance(parts, dict):
        raise KeysError(f"{at}.parts: must be an object of part name to value")
    for name, value in parts.items():
        if not (1 <= len(name) <= 64) or not _header_safe(value):
            raise KeysError(
                f"{at}.parts: each part needs a name of 1-64 characters and a non-empty value"
            )
    url_vars = raw.get("urlVariables", {})
    if not isinstance(url_vars, dict):
        raise KeysError(
            f"{at}.urlVariables: must be an object of variable name to value"
        )
    for name, value in url_vars.items():
        if not VAR_NAME.match(name) or not _header_safe(value) or len(value) > 253:
            raise KeysError(
                f"{at}.urlVariables: each variable needs a valid name and a value of 1-253 characters"
            )
    sandbox = raw.get("sandbox", False)
    if not isinstance(sandbox, bool):
        raise KeysError(f"{at}.sandbox: must be true or false")
    arguments = raw.get("arguments", {})
    if not isinstance(arguments, dict):
        raise KeysError(f"{at}.arguments: must be an object of tool name to arguments")
    for tool, args in arguments.items():
        if not TOOL_NAME.match(tool) or not isinstance(args, dict):
            raise KeysError(
                f"{at}.arguments: each entry needs a tool name and an arguments object"
            )
    if label in ([bearer] + list(parts.values())):
        raise KeysError(f"{at}.testAccount: must not be the credential")
    return KeyEntry(
        app=m.group(1),
        provider=m.group(2),
        test_account=label,
        bearer=bearer,
        parts=dict(parts),
        url_variables=dict(url_vars),
        sandbox=sandbox,
        arguments=dict(arguments),
    )


def parse(text: str | None) -> Keys:
    """Parse a keys document; empty or absent means no keys."""
    if text is None or text.strip() == "":
        return Keys(entries={})
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        raise KeysError("not valid JSON") from None
    if not isinstance(doc, dict):
        raise KeysError("must be a JSON object")
    unknown = sorted(set(doc) - {"format", "providers"})
    if unknown:
        raise KeysError(f"unknown field(s) {', '.join(unknown)}")
    if doc.get("format") != FORMAT:
        raise KeysError(f"format must be {FORMAT}")
    providers = doc.get("providers")
    if not isinstance(providers, dict):
        raise KeysError("providers: must be an object")
    return Keys(entries={key: _entry(key, raw) for key, raw in providers.items()})
