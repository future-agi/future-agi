"""Where the gateway places a pushed MCP credential secret, as Django checks
it at push (AM-C20 item 2; CN7, the CW1 merge).

One rule on both sides: ``check_secret`` (the gateway's credtemplate.Check)
and ``check_catalog_secret`` (its credtemplate.ForCatalogSecret) answer every
vector in
agentcc-gateway/internal/mcp/credtemplate/testdata/placement-vectors.json as
the gateway does (agentcc/tests/test_mcp_credential_placement_vectors.py and
the gateway's TestPlacementVectors read the same file, its catalog fixture
included). A secret the gateway can place is pushed; one it cannot is refused
at push naming the same field, never sent elsewhere or in part.

- An org server's secret (``mcp.servers.<id>.credential.secret``) goes in a
  header, a query parameter or a secret URL variable of the server's url, in
  one part or several (``parts``).
- A managed tool credential (``mcp.managed_tools.credentials.<id>.secret``)
  still goes only in a header (``header_only_problem``; the gateway's
  tenant.UnappliedMCPSecret), since no template call site exists for fagi.*
  servers.

A server added from the connector catalog (its ``catalog_ref``) is placed by
the parts its provider declares (AM-C22, the CW2 preamble; the gateway applies
the same rules at the admin PUT and in the MCP pool): the provider is in the
catalog, at the catalog_ref's major version; every declared secret part is
pushed, with its declared prefix; a declared constant is never pushed (the
gateway adds it from the catalog); no undeclared part is pushed. The catalog
is CN6's Django copy of the bundle the gateway embeds (connector_catalog_parts);
with none, every catalog_ref is refused. Reasons never hold a value. Pure
Python: no Django import.
"""

import re
from typing import Any, NamedTuple
from urllib.parse import unquote, unquote_plus, urlsplit

MAX_PARTS = 8
MAX_VALUE_CHARS = 8192

HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,64}$")
QUERY_NAME = re.compile(r"^[A-Za-z0-9._~-]{1,64}$")
VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")
PREFIX = re.compile(r"[!-~]{1,31} ?")
# A catalog_ref, <app-id>/<provider-id>@<major>.<minor>.<patch> (AM-C9;
# credtemplate.ParseRef), matched whole.
CATALOG_REF = re.compile(
    r"([a-z][a-z0-9-]{1,39})/([a-z][a-z0-9-]{1,23})"
    r"@((?:0|[1-9][0-9]*)[.](?:0|[1-9][0-9]*)[.](?:0|[1-9][0-9]*))"
)
CATALOG_REF_FIELD = "catalog_ref"

# Headers the gateway sets itself on an upstream hop, so a credential part
# never uses one (credtemplate/part.go). The vault refuses them at create;
# the gateway's TestReservedHeadersCoverEveryList holds this list to its own.
_RESERVED_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "connection",
        "content-encoding",
        "content-length",
        "content-type",
        "expect",
        "host",
        "keep-alive",
        "last-event-id",
        "mcp-method",
        "mcp-name",
        "mcp-protocol-version",
        "mcp-session-id",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "traceparent",
        "tracestate",
    }
)
RESERVED_HEADER_PREFIXES = ("mcp-", "x-agentcc-", "x-omega-")

# A {name} placeholder, raw or percent-escaped, as Go sees it in a path or
# query; RAW_VAR is the unescaped form.
VAR = re.compile(r"(?:\{|%7[Bb])([A-Za-z_][A-Za-z0-9_]{0,31})(?:\}|%7[Dd])")
RAW_VAR = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]{0,31})\}")


def reserved_header(name):
    """Whether a credential part may never use the header ``name``."""
    n = name.lower()
    return n in _RESERVED_HEADERS or n.startswith(RESERVED_HEADER_PREFIXES)


def secret_header(typ, header, reference_header):
    """The header the gateway sends a secret that states no placement in
    (MC.4; credtemplate.SecretHeader): Authorization, as a bearer token, for
    type bearer or when neither the secret nor the server credential names a
    header; otherwise the secret's header, or failing that the credential's."""
    header = header or reference_header or ""
    if typ == "bearer" or not header:
        return "Authorization"
    return header


def header_only_problem(secret, reference_header=None):
    """``(field, reason)`` for a secret a header-only path cannot apply as it
    states, or None: a managed tool credential's rule (the gateway's
    tenant.UnappliedMCPSecret). It refuses ``parts``, ``in`` other than
    header, and a ``name`` under a header placement that is not the header the
    secret is sent in, compared case-insensitively."""
    if not isinstance(secret, dict):
        return None
    if secret.get("parts"):
        return (
            "parts",
            "a credential in more than one part is not applied to a managed tool "
            "server, so it is refused, never sent in part",
        )
    placement = secret.get("in")
    if placement not in (None, "header"):
        return (
            "in",
            f'"{placement}" is a placement a managed tool server does not apply: '
            "it sends a pushed MCP credential only in a header, so the credential "
            "is refused, never sent as a bearer header",
        )
    name = secret.get("name")
    if name:
        header = secret_header(
            secret.get("type"), secret.get("header"), reference_header
        )
        if not isinstance(name, str) or name.lower() != header.lower():
            return (
                "name",
                f'"{name}" is not the header the gateway sends the credential in '
                f"({header}): a header placement's name must be its header",
            )
    return None


def _check_value(v):
    if not isinstance(v, str) or v == "":
        return "is empty"
    if len(v) > MAX_VALUE_CHARS:
        return "is longer than 8192 characters"
    if v.strip() != v:
        return "has surrounding spaces"
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in v):
        return "has a control character"
    return None


def _check_part(where, name, value, prefix=""):
    """(sub-field, reason) for one part's own fields, or None
    (credtemplate's checkPart)."""
    if where == "header":
        if not isinstance(name, str) or not HEADER_NAME.fullmatch(name):
            return "name", "is not an HTTP header name of at most 64 characters"
        if reserved_header(name):
            return (
                "name",
                "is a header the gateway sets itself, so a credential never uses it",
            )
        if prefix and not PREFIX.fullmatch(prefix):
            return (
                "prefix",
                "is not 1 to 31 printable characters, then at most one space",
            )
    elif where == "query":
        if not isinstance(name, str) or not QUERY_NAME.fullmatch(name):
            return "name", "is not a query parameter name"
    elif where == "url_variable":
        if not isinstance(name, str) or not VAR_NAME.fullmatch(name):
            return "name", "is not a URL variable name"
    else:
        return "in", "is not header, query or url_variable"
    if prefix and where != "header":
        return "prefix", "only a header part has a prefix"
    reason = _check_value(value)
    return ("value", reason) if reason else None


class _Part(NamedTuple):
    """One part of a credential and where it goes (credtemplate.Part), with
    the fields a refusal names it by (credtemplate's fieldNames)."""

    where: str
    name: str
    value: Any
    at: str = ""  # "" for the secret's own part, "parts[i]." for the others
    name_field: str = "name"  # the field holding the part's name
    prefix: str = ""  # "Bearer " before the value of an Authorization part
    fixed: str = ""  # every field of a catalog constant: "catalog_ref"

    def field(self, sub):
        if self.fixed:
            return self.fixed
        return self.at + (self.name_field if sub == "name" else sub)

    def key(self):
        """Header names compare case-insensitively; query and variable names
        exactly."""
        return (self.where, self.name.lower() if self.where == "header" else self.name)


def _first_part(secret, reference_header):
    """The secret's own part as (in, name, value) and the field its name is
    reported under, or (field, reason)."""
    typ = secret.get("type")
    if typ not in ("bearer", "api_key"):
        return "type", "is not bearer or api_key"
    where = secret.get("in") or ""
    name = secret.get("name") or ""
    header = secret.get("header") or ""
    value = secret.get("value")
    if where == "" or (where == "header" and not name):
        h = secret_header(typ, header, reference_header)
        if name and name.lower() != h.lower():
            return (
                "name",
                "is not the header the credential is sent in: a header placement's "
                "name is its header",
            )
        return ("header", h, value), "header"
    if where == "header":
        if header and header.lower() != name.lower():
            return "header", "is not name: in header carries its header in both"
        if not header and reference_header and reference_header.lower() != name.lower():
            return "name", "is not the credential's header"
        if typ == "bearer" and name.lower() != "authorization":
            return (
                "name",
                "is not Authorization: a bearer credential goes only in Authorization",
            )
        return ("header", name, value), "name"
    if where in ("query", "url_variable"):
        if typ != "api_key":
            return (
                "type",
                "is not api_key: only an api_key credential goes in a query "
                "parameter or a URL variable",
            )
        if header:
            return (
                "header",
                "is set, but this placement places the credential by name, never in "
                "a header",
            )
        if not name:
            return "name", "is required"
        return (where, name, value), "name"
    return "in", "is not header, query or url_variable"


def _parts(secret, reference_header):
    """The pushed parts, the secret's own first, each checked
    (credtemplate.Parts): [_Part], or (field, reason)."""
    first = _first_part(secret, reference_header)
    if isinstance(first[0], str):
        return first
    (where, name, value), name_field = first
    bad = _check_part(where, name, value)
    if bad:
        sub, reason = bad
        return (name_field if sub == "name" else sub), reason
    # A value in Authorization goes as "Bearer <value>". After its own
    # checks, the first part's name is reported under "header" only when
    # the secret states no placement (credtemplate's pushedFields).
    prefix = "Bearer " if where == "header" and name.lower() == "authorization" else ""
    out = [
        _Part(where, name, value, "", "name" if secret.get("in") else "header", prefix)
    ]
    extra = secret.get("parts") or []
    if len(extra) > MAX_PARTS - 1:
        return "parts", "has more than 7 entries: a credential has at most 8 parts"
    for i, p in enumerate(extra):
        at = f"parts[{i}]."
        pin, pname, pvalue = p.get("in"), p.get("name"), p.get("value")
        if (
            pin == "header"
            and isinstance(pname, str)
            and pname.lower() in ("authorization", "proxy-authorization")
        ):
            return (
                at + "name",
                "only the credential's first part, which has a type, goes there",
            )
        bad = _check_part(pin, pname, pvalue)
        if bad:
            return at + bad[0], bad[1]
        out.append(_Part(pin, pname, pvalue, at))
    return out


def _url_problem(url, parts):
    """(field, reason) for a server url the parts cannot be placed in, or
    None."""
    if not isinstance(url, str) or "#" in url:
        return "url", "has a fragment, which is never sent"
    split = urlsplit(url)
    netloc = split.netloc
    if split.scheme not in ("http", "https"):
        return "url", "is not an http or https URL"
    if not netloc or any(c in netloc for c in "{}%"):
        return "url", "has no host, or a {variable} in the host, port or user info"
    if "@" in netloc:
        return "url", "has user info"
    host_port = netloc.rsplit("]", 1)[-1]
    if ":" in host_port:
        port = host_port.rsplit(":", 1)[1]
        if port and not port.isdigit():
            return "url", "does not parse as a URL"
    path, query = split.path, split.query
    in_path = VAR.findall(path)
    if len(RAW_VAR.findall(unquote(path))) != len(in_path):
        return "url", "spells a {variable} with escapes inside its name"
    variables = {}
    for part in parts:
        if part.where == "url_variable":
            variables.setdefault(part.name, part)
    used = set()
    for name in in_path + VAR.findall(query):
        if name not in variables:
            return "url", "has a placeholder that no credential part fills"
        used.add(name)
    for name, part in variables.items():
        if name not in used:
            return (
                part.field("name"),
                "names a variable the server's URL does not hold in its path or query",
            )
    for seg in path.split("/"):
        names = VAR.findall(seg)
        if names:
            rendered = unquote(VAR.sub(lambda m: "\0" + m.group(1) + "\0", seg))
            rendered = re.sub(
                r"\0(\w+)\0", lambda m: variables[m.group(1)].value, rendered
            )
            if rendered in (".", ".."):
                return (
                    variables[names[0]].field("value"),
                    "would make the path segment . or ..",
                )
    for seg in [s for s in query.split("&") if s]:
        key = unquote_plus(seg.split("=", 1)[0])
        for part in parts:
            if part.where == "query" and part.name == key:
                return (
                    part.field("name"),
                    "is a query parameter the server's URL already sets",
                )
    return None


def _placed_problem(url, parts):
    """(field, reason) for parts the gateway cannot place on ``url``
    (credtemplate's compile: their number, a second part under one name, and
    the url), or None."""
    if not parts or len(parts) > MAX_PARTS:
        return "parts", "a credential has 1 to 8 parts"
    seen = set()
    for part in parts:
        if part.key() in seen:
            return part.field("name"), "is a second part under the same name"
        seen.add(part.key())
    return _url_problem(url, parts)


def check_secret(url, secret, reference_header=""):
    """None when the gateway places an org server's ``secret`` for ``url``
    exactly as stated, else (field, reason): field relative to the pushed
    MCPCredentialSecret ("in", "name", "parts[1].name", ...) or "url"."""
    if not isinstance(secret, dict):
        return None
    parts = _parts(secret, reference_header or "")
    if isinstance(parts, tuple):
        return parts
    return _placed_problem(url, parts)


def _major(version):
    return version.split(".", 1)[0]


def check_catalog_secret(url, secret, reference_header, catalog_ref, catalog):
    """``check_secret`` for a server added from the catalog: None when the
    gateway places ``secret`` for ``url`` by the parts the provider
    ``catalog_ref`` names declares in ``catalog`` (a
    connector_catalog_parts.CatalogParts; None is no catalog), else (field,
    reason), field as check_secret's or "catalog_ref". The gateway's
    credtemplate.ForCatalogSecret, refusal for refusal: the pushed parts are
    checked first; then no catalog, a catalog_ref that does not parse, a
    provider the catalog does not have, or another major version; then each
    declared part in declared order (a secret part not pushed, or pushed with
    another prefix; a constant pushed, or with no value or a malformed one);
    then a pushed part the provider does not declare; then the parts as
    placed, constants included, on the url. With no catalog_ref it is
    check_secret."""
    if not catalog_ref:
        return check_secret(url, secret, reference_header)
    if not isinstance(secret, dict):
        return None
    pushed = _parts(secret, reference_header or "")
    if isinstance(pushed, tuple):
        return pushed
    if catalog is None:
        return (
            CATALOG_REF_FIELD,
            "names a catalog provider, but no connector catalog is loaded, so the "
            "parts it declares cannot be applied",
        )
    match = CATALOG_REF.fullmatch(catalog_ref) if isinstance(catalog_ref, str) else None
    if match is None:
        return (
            CATALOG_REF_FIELD,
            "is not <app-id>/<provider-id>@<major>.<minor>.<patch>",
        )
    app, provider, version = match.groups()
    found = catalog.credential_parts(app, provider)
    if found is None:
        return (
            CATALOG_REF_FIELD,
            f"names {app}/{provider}, which the connector catalog does not have",
        )
    loaded, declared = found
    if _major(loaded) != _major(version):
        return (
            CATALOG_REF_FIELD,
            f"is version {version}, and the catalog has {loaded}, another major "
            "version",
        )
    if declared:
        pushed = _resolve(pushed, declared, f"{app}/{provider}")
        if isinstance(pushed, tuple):
            return pushed
    return _placed_problem(url, pushed)


def _resolve(pushed, declared, ref):
    """The pushed parts matched to the declared ones, with the declared
    constants added, in declared order (credtemplate's resolve): [_Part], or
    (field, reason)."""
    matched = [False] * len(pushed)
    out = []
    for d in declared:
        constant = _Part(
            d.where, d.name, d.value, prefix=d.prefix, fixed=CATALOG_REF_FIELD
        )
        i = next((n for n, p in enumerate(pushed) if p.key() == constant.key()), -1)
        if d.secret and i < 0:
            return (
                "parts",
                f"does not carry the secret part {d.where} {d.name} that {ref} "
                "declares, so the credential would be sent in part",
            )
        if d.secret:
            if pushed[i].prefix != d.prefix:
                return (
                    pushed[i].field("name"),
                    f"is sent with the prefix {pushed[i].prefix!r}, and {ref} "
                    f"declares {d.prefix!r}",
                )
            matched[i] = True
            out.append(pushed[i])
        elif i >= 0:
            return (
                pushed[i].field("name"),
                f"is a constant part that {ref} supplies, which is never pushed",
            )
        elif d.value == "":
            return (
                CATALOG_REF_FIELD,
                f"{ref} declares the non-secret part {d.where} {d.name} with no "
                "value, which nothing supplies",
            )
        else:
            bad = _check_part(d.where, d.name, d.value, d.prefix)
            if bad:
                return (
                    CATALOG_REF_FIELD,
                    f"{ref} declares the constant part {d.where} {d.name}, whose "
                    f"{bad[0]} {bad[1]}",
                )
            out.append(constant)
    for n, ok in enumerate(matched):
        if not ok:
            return pushed[n].field("name"), f"is a part {ref} does not declare"
    return out


def applies(secret, catalog_ref=None):
    """Whether the gateway places an org server's secret by a credential
    template (credtemplate.Applies): it states a placement (in, name or
    parts), or the server was added from the catalog. Any other secret keeps
    the gateway's header path (secret_header), unchanged and unchecked, as
    before CN7."""
    return bool(
        secret.get("in") or secret.get("name") or secret.get("parts") or catalog_ref
    )


def pushed_placement_problem(
    secret, server_url, reference_header=None, catalog_ref=None, catalog=None
):
    """``(field, reason)`` for a pushed secret the gateway cannot place, or
    None, as the gateway's admin PUT decides it: an org server's
    (``server_url`` its url, ``catalog_ref`` its catalog_ref, ``catalog`` the
    connector catalog's parts) by ``check_catalog_secret`` when ``applies``,
    else unchanged; a managed tool credential's (``server_url`` None) by
    ``header_only_problem``. A field naming the server's url or catalog_ref is
    "url" or "catalog_ref"; every other field is under the secret. With no
    ``catalog``, a catalog_ref is refused (the gateway's rule with no
    catalog loaded)."""
    if not isinstance(secret, dict):
        return None
    if server_url is None:
        return header_only_problem(secret, reference_header)
    if not applies(secret, catalog_ref):
        return None
    return check_catalog_secret(
        server_url, secret, reference_header or "", catalog_ref, catalog
    )
