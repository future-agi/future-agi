"""The fe1 service wire format. No models, settings, or cache dependencies.

Verifiers supply a pair-key ring, an audience, and (for requests) a nonce store.
Grant holders are not trusted principals: callers must also check live records.
"""

import base64
import hashlib
import hmac
import re
import time
import uuid

from tfc.envelope_routes import valid_delegated_route

FIELDS = (
    "version",
    "mode",
    "kid",
    "iss",
    "aud",
    "method",
    "route",
    "iat",
    "exp",
    "id",
    "kind",
    "org",
    "workspace",
    "user",
    "via",
    "sub",
    "scope",
    "body",
    "grant",
)
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
HEX_ID = r"[0-9a-f]{32}"
DIGEST = r"[0-9a-f]{64}"
SKEW = 5
PAIR_SETTINGS = {
    ("fa", "fa-relay"): "SECRET_KEY",
    ("fa", "gw-delegated"): "AGENTCC_ADMIN_TOKEN",
    ("gateway", "fa-eval"): "AGENTCC_ADMIN_TOKEN",
    ("fa", "fa-eval-grant"): "SECRET_KEY",
    ("fa", "collector-emit"): "FI_API_SECRET_PEPPER",
    ("fa", "collector-ingest"): "FI_API_SECRET_PEPPER",
}
# audience, subject, scope, via, registered, maximum lifetime in seconds
KINDS = {
    "sandbox": (
        "collector-ingest",
        rf"attempt:{UUID}:{UUID}",
        "traces",
        "person",
        True,
        86400,
    ),
    "platform-emit": (
        "collector-emit",
        r"emit:(provider|voice|sim)",
        "traces",
        "svc",
        False,
        60,
    ),
    "playground": ("fa-eval-grant", rf"chat:{UUID}", "eval", "person", False, 900),
    "tryit": ("fa-eval-grant", rf"chat:{UUID}", "eval", "person", False, 900),
    "guardrail-test": ("fa-eval-grant", rf"chat:{UUID}", "eval", "person", False, 900),
    "batch": ("fa-eval-grant", rf"batch:{UUID}", "eval", "person", True, 86400),
}
REQUEST_SCOPES = {
    "fa-relay": {"relay"},
    "gw-delegated": {"playground", "tryit", "guardrail-test", "batch"},
    "fa-eval": {"eval"},
}
REGISTRY_FIELDS = (
    "kind",
    "kid",
    "aud",
    "org",
    "workspace",
    "user",
    "via",
    "sub",
    "scope",
    "iat",
    "exp",
)


class EnvelopeRejected(ValueError):
    def __init__(self):
        super().__init__("service_envelope_rejected")


def _require(condition):
    if not condition:
        raise EnvelopeRejected()


def _matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value):
    _require(_matches(r"[A-Za-z0-9_-]+", value))
    try:
        result = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError):
        raise EnvelopeRejected() from None
    _require(_b64(result) == value)
    return result


def _key(keys, claims):
    pair = (claims["iss"], claims["aud"])
    _require(pair in PAIR_SETTINGS)
    secret = keys.get((*pair, claims["kid"]))
    _require(isinstance(secret, bytes) and len(secret) >= 32)
    # RFC 5869: SHA-256 extract, then one expand block (32-byte output).
    prk = hmac.digest(b"fe1", secret, "sha256")
    info = f"{pair[0]}>{pair[1]}|{claims['kid']}".encode("ascii")
    return hmac.digest(prk, info + b"\x01", "sha256")


def _validate(c):
    _require(set(c) == set(FIELDS) and all(isinstance(v, str) for v in c.values()))
    _require(c["version"] == "fe1" and c["mode"] in {"request", "grant"})
    _require(_matches(r"k[0-9]{1,3}", c["kid"]))
    _require((c["iss"], c["aud"]) in PAIR_SETTINGS)
    _require(all(_matches(r"0|[1-9][0-9]{0,10}", c[f]) for f in ("iat", "exp")))
    _require(int(c["exp"]) > int(c["iat"]))
    _require(_matches(HEX_ID, c["id"]) and _matches(UUID, c["org"]))
    _require(all(_matches(rf"{UUID}|-", c[f]) for f in ("workspace", "user")))
    if c["mode"] == "grant":
        _require(c["iss"] == "fa" and c["kind"] in KINDS)
        aud, sub, scope, via, _, ttl = KINDS[c["kind"]]
        _require(c["aud"] == aud and c["scope"] == scope and c["via"] == via)
        _require(_matches(sub, c["sub"]))
        _require(all(c[f] == "-" for f in ("method", "route", "body", "grant")))
        _require((c["user"] == "-") == (via == "svc"))
        _require(int(c["exp"]) - int(c["iat"]) <= ttl)
        return
    _require(c["aud"] in REQUEST_SCOPES and c["scope"] in REQUEST_SCOPES[c["aud"]])
    _require(c["kind"] == c["sub"] == "-")
    _require(c["method"] in {"GET", "POST", "PUT", "PATCH", "DELETE"})
    _require(_matches(r"/[A-Za-z0-9_/-]*", c["route"]) and "//" not in c["route"])
    _require(_matches(DIGEST, c["body"]))
    _require(int(c["exp"]) - int(c["iat"]) == 60)
    if c["aud"] == "gw-delegated":
        _require(valid_delegated_route(c["scope"], c["method"], c["route"]))
    if c["aud"] == "fa-relay":
        _require(c["via"] == "svc" and c["user"] == c["grant"] == "-")
    else:
        _require(c["user"] != "-")
        if c["via"].startswith("grant:"):
            _require(
                _matches(rf"grant:{HEX_ID}", c["via"]) and _matches(DIGEST, c["grant"])
            )
        else:
            _require(
                c["aud"] == "fa-eval" and _matches(r"key:[A-Za-z0-9_-]{1,64}", c["via"])
            )
            _require(c["grant"] == "-")


def canonical(claims):
    _validate(claims)
    return "\n".join(claims[f] for f in FIELDS).encode("utf-8")


def _encode(claims, keys):
    raw = canonical(claims)
    token = f"fe1.{_b64(raw)}.{_b64(hmac.digest(_key(keys, claims), raw, 'sha256'))}"
    _require(len(token) <= 2048)
    return token


def _decode(token):
    _require(isinstance(token, str) and len(token) <= 2048)
    parts = token.split(".")
    _require(len(parts) == 3 and parts[0] == "fe1")
    raw, mac = _unb64(parts[1]), _unb64(parts[2])
    _require(len(mac) == 32)
    try:
        lines = raw.decode("utf-8").split("\n")
    except UnicodeError:
        raise EnvelopeRejected() from None
    _require(len(lines) == len(FIELDS))
    c = dict(zip(FIELDS, lines, strict=True))
    _require(canonical(c) == raw)
    return c, raw, mac


def _verify(token, *, keys, audience, mode, now):
    c, raw, mac = _decode(token)
    _require(c["mode"] == mode and c["aud"] == audience)
    _require(hmac.compare_digest(mac, hmac.digest(_key(keys, c), raw, "sha256")))
    _require(int(c["iat"]) - SKEW <= now <= int(c["exp"]))
    return c


def _claims(*, mode, kid, issuer, audience, org, workspace, user, iat, exp, identifier):
    c = dict.fromkeys(FIELDS, "-")
    c.update(
        version="fe1",
        mode=mode,
        kid=kid,
        iss=issuer,
        aud=audience,
        org=str(org),
        workspace=str(workspace or "-"),
        user=str(user or "-"),
        iat=str(iat),
        exp=str(exp),
        id=identifier or uuid.uuid4().hex,
    )
    return c


def issue_grant(
    *,
    keys,
    kind,
    org,
    sub,
    workspace="-",
    user="-",
    ttl,
    now=None,
    identifier=None,
    kid="k1",
):
    _require(kind in KINDS and type(ttl) is int)
    aud, _, scope, via, _, _ = KINDS[kind]
    iat = int(time.time() if now is None else now)
    c = _claims(
        mode="grant",
        kid=kid,
        issuer="fa",
        audience=aud,
        org=org,
        workspace=workspace,
        user=user,
        iat=iat,
        exp=iat + ttl,
        identifier=identifier,
    )
    c.update(kind=kind, sub=sub, scope=scope, via=via)
    token = _encode(c, keys)
    return token, c


def registry_equal(claims, row):
    """Compare claims-only registry rows; never accept a missing registered row."""
    _require(row is not None and row.get("revoked_at") is None)
    _require(str(row.get("jti")) == claims["id"])
    for field in REGISTRY_FIELDS:
        value = row.get(field)
        _require(value is not None and str(value) == claims[field])
    if claims["kind"] == "sandbox":
        _, agent, attempt = claims["sub"].split(":")
        _require(
            str(row.get("agent_id")) == agent and str(row.get("attempt_id")) == attempt
        )
    if claims["kind"] == "batch":
        _require(str(row.get("batch_ref")) == claims["sub"].split(":")[1])


def verify_grant(token, *, keys, audience, public_id=None, row=None, now=None):
    c = _verify(
        token,
        keys=keys,
        audience=audience,
        mode="grant",
        now=time.time() if now is None else now,
    )
    if public_id is not None:
        _require(public_id == "fit_" + c["id"])
    if KINDS[c["kind"]][4]:
        registry_equal(c, row)
    return c


def verify_liveness(claims, live):
    """Common adapter contract. Missing facts fail closed, including on cache hits."""
    _require(live.get("org_active") is True)
    if claims["workspace"] != "-":
        _require(live.get("workspace_active") is True)
    if claims["via"] == "person":
        _require(live.get("user_active") is True and live.get("org_member") is True)
        if claims["workspace"] != "-":
            _require(live.get("workspace_member") is True)
    if claims["kind"] == "sandbox":
        _, agent, attempt = claims["sub"].split(":")
        _require(
            live.get("agent_active") is True and live.get("attempt_active") is True
        )
        _require(live.get("agent_id") == agent and live.get("attempt_id") == attempt)
        _require(live.get("agent_org") == claims["org"])
        _require(live.get("agent_workspace") == claims["workspace"])


def derive_grant(row, *, keys):
    """Recreate exact token bytes, including expired/revoked rows, for redaction."""
    c = _claims(
        mode="grant",
        kid=row["kid"],
        issuer="fa",
        audience=row["aud"],
        org=row["org"],
        workspace=row["workspace"],
        user=row["user"],
        iat=row["iat"],
        exp=row["exp"],
        identifier=str(row["jti"]),
    )
    c.update({f: str(row[f]) for f in ("kind", "via", "sub", "scope")})
    return _encode(c, keys)


def _bind_grant(c, grant):
    if c["via"].startswith("grant:"):
        _require(grant is not None)
        g, _, _ = _decode(grant)  # the gateway need not possess FA's grant key
        _require(g["mode"] == "grant" and g["aud"] == "fa-eval-grant")
        _require(c["via"] == "grant:" + g["id"])
        _require(c["grant"] == hashlib.sha256(grant.encode("ascii")).hexdigest())
        _require(all(c[f] == g[f] for f in ("org", "workspace", "user")))
        if c["aud"] == "gw-delegated":
            _require(c["scope"] == g["kind"])
    else:
        _require(grant is None and c["grant"] == "-")


def sign_request(
    *,
    keys,
    issuer,
    audience,
    method,
    route,
    body,
    org,
    via,
    scope,
    workspace="-",
    user="-",
    grant=None,
    now=None,
    identifier=None,
    kid="k1",
):
    iat = int(time.time() if now is None else now)
    c = _claims(
        mode="request",
        kid=kid,
        issuer=issuer,
        audience=audience,
        org=org,
        workspace=workspace,
        user=user,
        iat=iat,
        exp=iat + 60,
        identifier=identifier,
    )
    c.update(
        method=method,
        route=route,
        via=via,
        scope=scope,
        body=hashlib.sha256(body).hexdigest(),
        grant=hashlib.sha256(grant.encode("ascii")).hexdigest() if grant else "-",
    )
    _bind_grant(c, grant)
    return _encode(c, keys)


def verify_request(
    token, *, keys, audience, method, route, body, nonces, grant=None, now=None
):
    now = time.time() if now is None else now
    c = _verify(token, keys=keys, audience=audience, mode="request", now=now)
    _require(c["method"] == method and c["route"] == route)
    _require(c["body"] == hashlib.sha256(body).hexdigest())
    _bind_grant(c, grant)
    _require(nonces.consume(c["aud"], c["id"], int(c["iat"]), int(c["exp"]), now))
    return c
