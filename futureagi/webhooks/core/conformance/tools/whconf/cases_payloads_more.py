"""payloads.json cases added in conformance 1.1.0: the commit-message scrubber (scrub, then cut), the order of
customer-chosen context keys, and how FA reads the X-Omega-Deployment-Name header."""

from __future__ import annotations

import copy

from . import reference as R
from .cases_b8_notices import b8_build_input
from .common import check


def more_build_cases(add, *, n1, pvc, plc, thin, full, run_schema) -> None:
    schema_pvc = "urn:futureagi:webhooks:v1:prompt.version.created"
    schema_plc = "urn:futureagi:webhooks:v1:prompt.label.changed"

    straddle = copy.deepcopy(pvc)
    straddle["event_id"] = "evt_" + R.ulid(1792662067201, "pvc-straddle")
    straddle["version"]["commit_message"] = (
        "y" * 196 + "sk-proj-abcdefghijkl" + " was rotated"
    )
    cut, truncated = R._cut(straddle["version"]["commit_message"], "thin")
    check(cut == "y" * 196 + "[red" and truncated, "scrub before cut")
    add(
        "pvc-thin-secret-straddles-cut",
        "prompt.version.created",
        straddle,
        thin,
        schema_pvc,
        "scrub, then cut: the key at code points 196-215 becomes [redacted] first, so the 200-code-point cut keeps "
        "'[red' and no part of the key (cutting first would leave 'sk-p', too short to match, in the body)",
    )

    masked = copy.deepcopy(plc)
    masked["event_id"] = "evt_" + R.ulid(1792663000005, "plc-masked")
    masked["revision"] = 14
    masked["to_version"]["commit_message"] = (
        "Rotate whsec_AAECAwQFBgcICQoL and sk-ant-api03-abcdefgh; sk-1234567 is a ticket, not a key"
    )
    add(
        "plc-full-commit-secrets-masked",
        "prompt.label.changed",
        masked,
        full,
        schema_plc,
        "full mode masks too: each secret shape becomes [redacted] in place (the message is kept, not dropped); "
        "sk- with only 7 characters after it is not a secret shape",
    )

    keys = copy.deepcopy(n1)
    keys["run"]["webhook_context"] = {
        "b": 1,
        "10": "ten",
        "2": "two",
        "a": True,
        "01": "zero-one",
        "4294967294": "largest index",
        "4294967295": "not an index",
    }
    add(
        "b8-context-key-order",
        "deployment.run",
        b8_build_input(
            keys, "keys", fa={"event_id": "evt_" + R.ulid(1792664415001, "evt:keys")}
        ),
        None,
        run_schema["completed"],
        "customer keys in the order JSON.parse gives them: array indexes (2, 10, 4294967294) ascending first, then "
        "the rest as received (01 and 4294967295 are not indexes)",
    )


def header_cases() -> list[dict]:
    """X-Omega-Deployment-Name, as FA reads it before it builds data.deployment.name (contract sections 4.3, 7.1)."""
    out = []
    for cid, value, note in [
        ("plain", "support-agent", ""),
        ("utf8", "support%20agent%20%E2%9C%93", "percent-encoded UTF-8"),
        ("lower-case-hex", "caf%c3%a9", "hex digits in either case"),
        ("plus-is-literal", "a+b", "'+' is not a space"),
        ("raw-space", "support agent", "a raw space is visible ASCII"),
        ("lone-percent", "100%", "a '%' without two hex digits stays as it is"),
        ("percent-one-hex-digit", "50%2", ""),
        ("percent-not-hex", "a%zz", ""),
        ("bom-kept", "%EF%BB%BFname", "a decoded U+FEFF is kept, not stripped"),
        ("invalid-utf8", "%FF", ""),
        ("truncated-sequence", "caf%C3", ""),
        ("encoded-surrogate", "%ED%A0%80", "an encoded surrogate is not UTF-8"),
        ("overlong", "%C0%AF", "an overlong encoding is not UTF-8"),
        (
            "raw-non-ascii",
            "café",
            "a raw byte above 0x7E (frameworks hand header bytes over as Latin-1)",
        ),
        ("control-character", "a\tb", ""),
    ]:
        try:
            expected = {"name": R._pct_decode(value)}
        except R.InvalidHeader:
            expected = {
                "status": 400,
                "code": "invalid_header",
                "field": "X-Omega-Deployment-Name",
            }
        out.append({"id": cid, "value": value, "expected": expected, "note": note})
    return out
