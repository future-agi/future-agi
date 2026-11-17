"""The G16 frozen privacy corpus, for SIEM export's regression tests.

A port of the gateway's independentPrivacyCases (internal/mcp/
independent_privacy_test.go) and TestScalarPrivacyShape, plus shape-fitting
carriers of the same synthetic credentials: the field-name, encoded and URL
forms the gateway's span scalar sanitizer redacts (G16-SIEM round 2). Every
value is built at run time from a fixed phrase, as the gateway's are, so no
credential-shaped literal is stored here. A fixture is (group, name, value,
needles): no needle may appear in an export, and no value but a control's.
"""

import base64
import json
from urllib.parse import quote, quote_plus


def _raw_url(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _raw_std(text):
    return base64.b64encode(text.encode()).decode().rstrip("=")


def _std(text):
    return base64.b64encode(text.encode()).decode()


def _pct(text):
    return "".join(f"%{byte:02X}" for byte in text.encode())


def _go_json(value):
    """encoding/json.Marshal: sorted keys, compact, <, > and & escaped."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    for char, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):
        text = text.replace(char, escaped)
    return text


OPAQUE = _raw_url("independent-synthetic-fixture-" * 2)
BASIC = _std("fixture-user:" + OPAQUE)
PROVIDER = "sk-" + OPAQUE[:32]
JWT = (
    _raw_url('{"alg":"HS256","typ":"JWT"}')
    + "."
    + _raw_url('{"sub":"fixture"}')
    + "."
    + OPAQUE
)
FIELDS = (
    "AUTHORIZATION",
    "Proxy-Authorization",
    "Cookie",
    "Set-Cookie",
    "X-API-Key",
    "X_Auth_42",
    "Ocp-Apim-Subscription-Key",
    "X-Amz-Security-Token",
    "X-Hub-Signature-256",
    "CF-Access-Jwt-Assertion",
    "clientSecret",
    "privateKey",
    "refreshToken",
    "sessionId",
    "credential",
    "passWord",
    "X.C_o-o k/i/e99",
    "Assertion",
    "Subscription",
    "api_key",
    "passwd",
    "pwd",
    "passphrase",
    "clientPassphrase",
    "jwk",
    "p%61ssword",
    "%61%70%69%5F%6B%65%79",
)
CONTROLS = (
    "tc_01M4ZF0A9KQ2V7W3X5Y8Z1B4C6",
    "fagi.web_search",
    "run_R2",
    "researcher",
    "vs_01M4ZEB9E0J3AADR85Z4T4428X",
    "2026-07-28",
    "fagi.web",
    "deep-research",
    "agent:planner/v2",
    "tool_call_01",
    "tools/call",
    "-32603",
    "agentcc-gateway",
    "alpha.beta:read",
    "42",
)


def _frozen():
    """independentPrivacyCases: (name, JSON value, raw message or None, needles)."""
    safe = "safe-control"
    cases = []

    def add(name, value, *needles):
        message = value.get("message") if isinstance(value, dict) else None
        cases.append((name, value, message, needles))

    def text(name, value, *needles):
        add(name, {"message": value, "safe": safe}, *needles)

    for i, field in enumerate(FIELDS):
        add(f"field-{i:02d}", {"headers": {field: OPAQUE}, "safe": safe}, OPAQUE)
    add(
        "nested-pairs",
        {
            "headers": [["Authorization", BASIC], ["Content-Type", "text/plain"]],
            "safe": safe,
        },
        BASIC,
    )
    add(
        "nested-name-values",
        {"headers": [{"name": "Authorization", "values": [BASIC]}], "safe": safe},
        BASIC,
    )
    for name, key in (("nested-header-value", "header"), ("nested-key-value", "key")):
        add(
            name,
            {
                "headers": [{key: "Authorization", "value": "Basic " + BASIC}],
                "safe": safe,
            },
            BASIC,
        )
    add("jwk-private", {"jwk": {"kty": "oct", "k": OPAQUE}, "safe": safe}, OPAQUE)
    encoded_basic = _std("Authorization: Basic " + BASIC)
    texts = (
        "Authorization: Basic " + BASIC,
        "Cookie: SID=" + OPAQUE + "; Path=/",
        "Set-Cookie: SID=" + OPAQUE + "; HttpOnly; Secure",
        "Content-Type: text/plain\r\nAuthorization: Basic "
        + BASIC
        + "\r\nAccept: application/json",
        "Authorization: Basic\r\n " + BASIC + "\r\nAccept: application/json",
        "Authorization:\n\tBasic " + BASIC + "\nAccept: application/json",
        "Cookie: SID=\r\n\t" + OPAQUE + "\r\nAccept: text/plain",
        "X-API-Key:\r\n " + OPAQUE + "\r\nAccept: text/plain",
        "X-API-Key = '" + OPAQUE + "' safe=retained",
        'X-API-Key = "' + OPAQUE + ' with spaces"; safe=retained',
        "request failed: Authorization: Basic " + BASIC,
        "request failed:\nSet-Cookie: SID=" + OPAQUE,
        "X_API_KEY=" + OPAQUE + "; safe=retained",
        "'Authorization': 'Basic " + BASIC + "'",
        "api%5Fkey%3D" + _pct(OPAQUE),
        _pct("Authorization: Basic " + BASIC),
        encoded_basic,
        "event " + JWT,
        "provider " + PROVIDER,
    )
    needles = (OPAQUE, BASIC, PROVIDER, _pct(OPAQUE), _pct(BASIC), encoded_basic)
    for i, value in enumerate(texts):
        text(f"text-{i:02d}", value, *needles)
    nested = '{"Authorization":"Basic ' + BASIC + '","safe":"retained"}'
    for i in range(4):
        text(f"json-in-json-{i}", "envelope: " + nested, BASIC)
        nested = _go_json(nested)
    urls = (
        "https://user:" + OPAQUE + "@service.invalid/path",
        "//" + OPAQUE + "@service.invalid/path",
        "https://service.invalid/path?api_key=" + OPAQUE + "&safe=1",
        "https://service.invalid/path?%61%70%69%5f%6b%65%79=" + OPAQUE,
        "https://service.invalid/path?%2561%2570%2569%255f%256b%2565%2579=" + OPAQUE,
        "https://service.invalid/path?message="
        + quote_plus("Authorization: Basic " + BASIC, safe=""),
        "https://service.invalid/path?message="
        + quote_plus("Authorization: Basic\r\n " + BASIC, safe=""),
        "https://service.invalid/path?payload=" + quote_plus(nested, safe=""),
        "https://service.invalid/path#Cookie:SID=" + OPAQUE,
        "https://service.invalid/" + PROVIDER,
        "https://" + PROVIDER + ".service.invalid/path",
        "https://service.invalid/path?" + PROVIDER + "=safe",
        "https://service.invalid/" + _pct(PROVIDER),
        "https://service.invalid/" + quote(_pct(PROVIDER), safe=":@&=+$"),
        "https://service.invalid/path?next="
        + quote_plus("https://user:" + OPAQUE + "@service.invalid/path", safe=""),
        "see (https://user:"
        + OPAQUE
        + "@service.invalid/path?session="
        + OPAQUE
        + ") for details",
    )
    url_needles = (
        OPAQUE,
        BASIC,
        PROVIDER,
        _pct(PROVIDER),
        quote(_pct(PROVIDER), safe=":@&=+$"),
    )
    for i, value in enumerate(urls):
        text(f"url-{i:02d}", value, *url_needles)
    add(
        "url-map-key",
        {"https://user:" + OPAQUE + "@service.invalid/path": "retained", "safe": safe},
        OPAQUE,
    )
    add(
        "error-envelope",
        {
            "error": {
                "code": -32000,
                "message": "failed; Authorization: Basic " + BASIC,
                "data": {"headers": {"Cookie": OPAQUE}},
            },
            "safe": safe,
        },
        OPAQUE,
        BASIC,
    )
    add(
        "stream-content-chunks",
        {
            "content": [
                {"type": "text", "text": "Authorization: Basic "},
                {"type": "text", "text": BASIC},
            ],
            "safe": safe,
        },
        BASIC,
    )
    add(
        "stream-string-chunks",
        {"chunks": ["Cookie: SID=", OPAQUE], "safe": safe},
        OPAQUE,
    )
    envelope = _std('{"Authorization":"Basic ' + BASIC + '"}')
    add(
        "encoded-envelope",
        {"encoding": "base64", "body": envelope, "safe": safe},
        envelope,
    )
    add(
        "event-link-baggage",
        {
            "_meta": {
                "events": [{"name": OPAQUE}],
                "links": [{"attributes": {"private": OPAQUE}}],
            },
            "safe": safe,
        },
        OPAQUE,
    )
    return cases


def _shape():
    """TestScalarPrivacyShape's unapproved scalars."""
    opaque = _raw_url("synthetic-scalar-fixture-" * 2)
    values = (
        '{"message":"' + opaque + '"}',
        "Authorization: Basic\r\n " + opaque,
        "hello\n" + opaque,
        "a" * 257,
        "name with spaces",
        "https://" + "sk-" + opaque[:32] + ".invalid",
        "password=" + opaque,
        _std("Authorization: Basic " + opaque),
        "tool_call_01\n",
    )
    return [(f"shape-{i:02d}", value, (value,)) for i, value in enumerate(values)]


def _derived():
    """Shape-fitting carriers of the corpus' credentials: a field name and a
    colon, encoded headers and keys, credential-named URL paths, a JWT whose
    header does not start with eyJ."""
    out = []
    for i, field in enumerate(FIELDS):
        out.append((f"colon-{i:02d}", field + ":" + OPAQUE, (OPAQUE,)))
    for i, field in enumerate(("cookie", "Session", "jwt", "auth", "Bearer")):
        out.append((f"colon-short-{i}", field + ":" + OPAQUE[:24], (OPAQUE[:24],)))
    plain = {
        "basic": "Authorization: Basic " + BASIC,
        "provider": PROVIDER,
        "cookie": "Cookie: SID=" + OPAQUE,
        "apikey": "X-API-Key: " + OPAQUE,
        "secret": "clientSecret:" + OPAQUE,
    }
    for name, text in plain.items():
        for kind, encode in (("b64url", _raw_url), ("b64std", _raw_std)):
            value = encode(text)
            out.append((f"{kind}-{name}", value, (value, OPAQUE, BASIC, PROVIDER)))
            out.append(
                (
                    f"url-path-{kind}-{name}",
                    "https://service.invalid/" + value,
                    (value, OPAQUE, BASIC, PROVIDER),
                )
            )
        twice = _raw_url(_raw_url(text))
        out.append((f"b64url2-{name}", twice, (twice, OPAQUE, BASIC, PROVIDER)))
    spaced = (
        _raw_url(' {"alg":"HS256","typ":"JWT"}')
        + "."
        + _raw_url('{"sub":"fixture"}')
        + "."
        + OPAQUE
    )
    out.append(("jwt-spaced-header", spaced, (spaced, OPAQUE)))
    out.append(("jwt", JWT, (JWT, OPAQUE)))
    out.append(("provider", PROVIDER, (PROVIDER,)))
    out.append(("provider-colon", "key:" + PROVIDER, (PROVIDER,)))
    for name in ("Cookie", "session", "token", "jwk"):
        out.append(
            (
                f"url-path-{name.lower()}",
                f"https://service.invalid/{name}:{OPAQUE}",
                (OPAQUE,),
            )
        )
    out.append(("path-colon", f"tools/clientSecret:{OPAQUE}", (OPAQUE,)))
    return out


def fixtures():
    """Every fixture as a dict: group, name, value, needles."""
    out = []
    for name, value, message, needles in _frozen():
        out.append(
            {
                "group": "frozen",
                "name": name,
                "value": _go_json(value),
                "needles": needles,
            }
        )
        if message is not None:
            out.append(
                {
                    "group": "frozen-raw",
                    "name": name + "-raw",
                    "value": message,
                    "needles": needles,
                }
            )
    for group, items in (("shape", _shape()), ("derived", _derived())):
        for name, value, needles in items:
            out.append(
                {"group": group, "name": name, "value": value, "needles": needles}
            )
    for i, value in enumerate(CONTROLS):
        out.append(
            {
                "group": "control",
                "name": f"control-{i:02d}",
                "value": value,
                "needles": (),
            }
        )
    return out


def leaked(text, needles):
    """Whether any needle appears in ``text`` raw, JSON-escaped or CSV-quoted."""
    for needle in needles:
        if not needle:
            continue
        forms = {needle, json.dumps(needle)[1:-1], needle.replace('"', '""')}
        if any(form in text for form in forms):
            return True
    return False
