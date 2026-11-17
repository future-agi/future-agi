"""Place a test account's credential the way the provider's manifest says.

- oauth2 and mcp_oauth: the account's access token as a bearer token;
- api_key: each of auth.credentialParts in its header, query parameter or URL
  variable (AM-C12 a, AM-C20). A secret part and a non-secret customer
  setting take the keys entry's value; a non-secret constant (Close's
  Close-Scope) takes the catalog's value and is never supplied by the entry;
- none: no credential.

Every value the entry gives is used, or the provider is skipped with a named
reason: an input is never silently ignored. Other auth kinds (basic,
service_account, partner, local_setup) are not smoked by this runner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from .keys import MIN_SCANNED_SECRET

TEMPLATE_VAR = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]{0,31})\}")
SUPPORTED_AUTH = ("oauth2", "mcp_oauth", "api_key", "none")
BEARER_AUTH = ("oauth2", "mcp_oauth")
_URL_SAFE = "-._~"

# Headers this client sets itself (the catalog's transport headers,
# manifest/defs.go, plus the 2026-07-28 mirrored headers and its own
# User-Agent): a credential part never names one.
_CLIENT_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "content-type",
        "transfer-encoding",
        "connection",
        "upgrade",
        "te",
        "trailer",
        "keep-alive",
        "accept",
        "accept-encoding",
        "mcp-session-id",
        "mcp-protocol-version",
        "last-event-id",
        "mcp-method",
        "mcp-name",
        "user-agent",
    }
)


class Skip(Exception):
    """The provider cannot be smoked with this entry; reason is a code that
    names fields, never values. The entry (or the manifest) is wrong, so the
    run reports it as a failure."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Unsupported(Skip):
    """The runner does not smoke this auth kind: a neutral skip."""


@dataclass
class Credential:
    headers: dict = field(default_factory=dict)
    query: list = field(default_factory=list)  # (name, value) pairs
    variables: dict = field(default_factory=dict)  # resolved URL variables
    secrets: list = field(default_factory=list)  # values never to print

    def url(self, template: str, with_query: bool = True) -> str:
        """template with its variables filled, plus any query credential."""
        missing = sorted(
            {v for v in TEMPLATE_VAR.findall(template) if v not in self.variables}
        )
        if missing:
            raise Skip("url_variables_missing:" + ",".join(missing))
        url = TEMPLATE_VAR.sub(
            lambda m: quote(self.variables[m.group(1)], safe=_URL_SAFE), template
        )
        if with_query and self.query:
            parts = urlsplit(url)
            extra = urlencode(self.query)
            url = urlunsplit(
                parts._replace(query=f"{parts.query}&{extra}" if parts.query else extra)
            )
        return url

    def scanned_secrets(self) -> list:
        return [s for s in self.secrets if len(s) >= MIN_SCANNED_SECRET]


def _pattern_ok(pattern, value: str) -> bool:
    if not isinstance(pattern, str):
        return False
    try:
        return re.fullmatch(pattern, value) is not None
    except re.error:
        return False


def _declared(provider) -> dict:
    """Every URL variable the provider declares in urlVariables, by name."""
    declared = dict(provider.transport.get("urlVariables") or {})
    oauth = provider.auth.get("oauth2") or {}
    for name, spec in (oauth.get("urlVariables") or {}).items():
        declared.setdefault(name, spec)
    return declared


def _variables(provider, entry, cred: Credential) -> None:
    declared = _declared(provider)
    part_vars = {
        p.get("name")
        for p in provider.auth.get("credentialParts") or []
        if p.get("in") == "url_variable"
    }
    if any(name not in declared for name in entry.url_variables):
        raise Skip(
            "url_variable_unknown"
            if not part_vars & set(entry.url_variables)
            else "url_variable_is_a_credential_part"
        )
    for name, spec in declared.items():
        value = entry.url_variables.get(name, (spec or {}).get("default"))
        if value is None:
            continue
        if not _pattern_ok((spec or {}).get("pattern"), value):
            raise Skip(f"url_variable_invalid:{name}")
        cred.variables[name] = value


def _parts(provider, entry, cred: Credential) -> None:
    parts = provider.auth.get("credentialParts") or []
    names = {p.get("name") for p in parts}
    if any(name not in names for name in entry.parts):
        raise Skip("part_unknown")
    for part in parts:
        name, where, secret = part.get("name"), part.get("in"), bool(part.get("secret"))
        constant = None if secret else part.get("value")
        if constant is not None:
            if name in entry.parts:
                raise Skip(f"part_is_catalog_constant:{name}")
            value = constant
        else:
            value = entry.parts.get(name)
            if not value:
                raise Skip(f"part_missing:{name}")
        if secret:
            cred.secrets.append(value)
        if where == "header":
            if (
                not isinstance(name, str)
                or name.lower() in _CLIENT_HEADERS
                or name.lower().startswith("mcp-param-")
            ):
                raise Skip(f"part_header_reserved:{name}")
            cred.headers[name] = (part.get("prefix") or "") + value
        elif where == "query":
            cred.query.append((name, value))
        elif where == "url_variable":
            cred.variables[name] = value
        else:
            raise Skip(f"part_placement_unsupported:{where}")


def resolve(provider, entry) -> Credential:
    """The credential for provider from its keys entry, or Skip."""
    kind = provider.auth.get("kind")
    if kind not in SUPPORTED_AUTH:
        raise Unsupported(f"auth_kind_unsupported:{kind}")
    if kind != "api_key" and entry.parts:
        raise Skip("parts_given_for_" + kind)
    if kind not in BEARER_AUTH and entry.bearer:
        raise Skip("bearer_given_for_" + kind)
    cred = Credential()
    _variables(provider, entry, cred)
    if kind in BEARER_AUTH:
        if not entry.bearer:
            raise Skip("bearer_missing")
        cred.headers["Authorization"] = "Bearer " + entry.bearer
        cred.secrets.append(entry.bearer)
    elif kind == "api_key":
        _parts(provider, entry, cred)
    return cred
