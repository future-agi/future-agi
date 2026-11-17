"""signing.json and signing-v1.json (DESIGN.md sections 5.2-5.6 and 22.2)."""

from __future__ import annotations

import base64

from . import reference as R
from .cases_signing_rules import signing_rules
from .cases_signing_verify import verify_cases
from .common import (
    CONFORMANCE_VERSION,
    DESIGN_5_6_ID,
    DOC_FACTS,
    SECRET_A,
    SECRET_B,
    SECRET_C,
    b64,
    body_fields,
    check,
    dump,
    sha,
)

DESIGN_5_6_BODY = (
    b'{"id":"evt_01JBF3Q7Z8X4N6R2T5V9W0YB1C","type":"prompt.label.changed","api_version":"v1",'
    b'"timestamp":"2026-10-22T09:59:58.120Z","organization_id":"11111111-1111-4111-8111-111111111111",'
    b'"workspace_id":"22222222-2222-4222-8222-222222222222","data":{"action":"moved","label":'
    b'{"id":"33333333-3333-4333-8333-333333333333","name":"Production","type":"system"}}}'
)


def signing_files(delivered: dict) -> dict[str, bytes]:
    body = DESIGN_5_6_BODY
    f = DOC_FACTS["design_5_6"]
    t = f["t"]
    check(len(body) == f["bytes"] and sha(body) == f["sha256"], "design 5.6 body")
    sa, sb = (
        R.sign(SECRET_A, DESIGN_5_6_ID, t, body),
        R.sign(SECRET_B, DESIGN_5_6_ID, t, body),
    )
    check(sa == f["sig_a"] and sb == f["sig_b"], "design 5.6 signatures")
    rev1 = body.replace(b"prompt.label.changed", b"prompt.label.moved")
    g = DOC_FACTS["design_5_6_rev1"]
    r1a, r1b = (
        R.sign(SECRET_A, DESIGN_5_6_ID, t, rev1),
        R.sign(SECRET_B, DESIGN_5_6_ID, t, rev1),
    )
    check(
        len(rev1) == g["bytes"]
        and sha(rev1).startswith(g["sha256_prefix"])
        and sha(rev1).endswith(g["sha256_suffix"])
        and r1a.startswith(g["sig_a_prefix"])
        and r1a.endswith(g["sig_a_suffix"])
        and r1b.startswith(g["sig_b_prefix"])
        and r1b.endswith(g["sig_b_suffix"]),
        "rev 1 vectors reproduce",
    )
    tampered = bytearray(body)
    tampered[10] ^= 0x01
    tampered = bytes(tampered)
    overlap = f"v1,{sa} v1,{sb}"
    b8_body = base64.b64decode(delivered["body_bytes_b64"])
    b8_t = delivered["timestamp"]
    b8_id = delivered["msg_id"]

    verify = verify_cases(
        body=body,
        t=t,
        sa=sa,
        sb=sb,
        tampered=tampered,
        overlap=overlap,
        b8_body=b8_body,
        b8_t=b8_t,
        b8_id=b8_id,
        delivered=delivered,
    )

    sign_cases = []

    def scase(cid, secrets, msg_id, ts, b, event_type, attempt, seq, test, note):
        sig = R.signature_header(secrets, msg_id, ts, b)
        sign_cases.append(
            {
                "id": cid,
                "secrets": secrets,
                "msg_id": msg_id,
                "timestamp": ts,
                "body_bytes_b64": b64(b),
                "attempt": {
                    "event_type": event_type,
                    "delivery_id": delivery_id,
                    "n": attempt,
                    "sequence": seq,
                    "test": test,
                },
                "expected": {
                    "signatures": [s.split(",", 1)[1] for s in sig.split(" ")],
                    "webhook_signature": sig,
                    "headers": R.attempt_headers(
                        msg_id=msg_id,
                        timestamp=ts,
                        signature=sig,
                        event_type=event_type,
                        delivery_id=delivery_id,
                        attempt=attempt,
                        sequence=seq,
                        test=test,
                    ),
                },
                "note": note,
            }
        )

    delivery_id = "dlv_01JBF3Q8A1B2C3D4E5F6G7H8J9"
    scase(
        "design-5-6-a",
        [SECRET_A],
        DESIGN_5_6_ID,
        t,
        body,
        "prompt.label.changed",
        1,
        41,
        False,
        "DESIGN.md section 5.6, secret A",
    )
    scase(
        "design-5-6-b",
        [SECRET_B],
        DESIGN_5_6_ID,
        t,
        body,
        "prompt.label.changed",
        1,
        41,
        False,
        "DESIGN.md section 5.6, secret B",
    )
    scase(
        "design-5-6-overlap",
        [SECRET_A, SECRET_B],
        DESIGN_5_6_ID,
        t,
        body,
        "prompt.label.changed",
        1,
        41,
        False,
        "rotation overlap: newest (active) first",
    )
    scase(
        "retry-fresh-t",
        [SECRET_A],
        DESIGN_5_6_ID,
        t + 3605,
        body,
        "prompt.label.changed",
        4,
        41,
        False,
        "a retry: same id and body, t of this attempt, new signature",
    )
    scase(
        "test-delivery",
        [SECRET_A],
        "evt_test_01JBF3Q9C0D1E2F3G4H5J6K7M8",
        t,
        body.replace(DESIGN_5_6_ID.encode(), b"evt_test_01JBF3Q9C0D1E2F3G4H5J6K7M8"),
        "prompt.label.changed",
        1,
        None,
        True,
        "a test delivery: X-FutureAGI-Test, no sequence header",
    )
    delivery_id = "dlv_01M5HJ08XP18DJRFAEBXG730MK"
    scase(
        "b8-12-5",
        [SECRET_A],
        b8_id,
        b8_t,
        b8_body,
        "deployment.run.completed",
        1,
        214,
        False,
        "B8-EVENT-CONTRACT.md section 12.5",
    )

    secret_cases = []
    for cid, s in [
        ("a-32-bytes", SECRET_A),
        ("24-bytes", "whsec_" + b64(bytes(range(24)))),
        ("64-bytes", "whsec_" + b64(bytes(range(64)))),
        ("23-bytes", "whsec_" + b64(bytes(range(23)))),
        ("65-bytes", "whsec_" + b64(bytes(range(65)))),
        ("no-prefix", SECRET_A[6:]),
        ("not-base64", "whsec_not*base64*at*all*0000000000000000000"),
        ("unpadded", SECRET_A.rstrip("=")),
    ]:
        try:
            key = R.secret_key(s)
            secret_cases.append(
                {
                    "id": cid,
                    "secret": s,
                    "valid": True,
                    "key_bytes": len(key),
                    "key_hex": key.hex(),
                }
            )
        except R.SecretInvalid as e:
            secret_cases.append(
                {"id": cid, "secret": s, "valid": False, "reason": str(e)}
            )

    rules = signing_rules()
    signing = {
        "conformance_version": CONFORMANCE_VERSION,
        "file": "signing",
        "rules": rules,
        "secrets": {"A": SECRET_A, "B": SECRET_B, "C": SECRET_C},
        "secret_format": secret_cases,
        "sign": sign_cases,
        "verify": verify,
    }
    signing_v1 = {
        "conformance_version": CONFORMANCE_VERSION,
        "file": "signing-v1",
        "source": "DESIGN.md section 5.6 (frozen in WH-0). The FA core, the SDK and the B8 fixtures carry these "
        "values byte for byte (WT-01).",
        "scheme": "standard-webhooks-v1",
        "secret_a": SECRET_A,
        "secret_a_bytes": "0x00..0x1f",
        "secret_b": SECRET_B,
        "secret_b_bytes": "0x20..0x3f",
        "secret_c": SECRET_C,
        "secret_c_bytes": "0x40..0x5f",
        "msg_id": DESIGN_5_6_ID,
        "timestamp": t,
        "timestamp_iso": f["t_iso"],
        **body_fields(body),
        "signature_a": sa,
        "signature_b": sb,
        "overlap_header": overlap,
        "must_pass": [
            {"secret": "A", "now": t},
            {"secret": "B", "now": t},
            {"secret": "A", "now": t + 300},
            {"secret": "A", "header": "overlap", "now": t},
            {"secret": "B", "header": "overlap", "now": t},
        ],
        "must_fail": [
            {
                "secret": "A",
                "body": "byte 10 (0-based) XOR 0x01",
                "body_bytes_b64": b64(tampered),
            },
            {"secret": "A", "now": t + 301},
            {"secret": "C", "now": t},
        ],
        "rev1_reproduction": {
            "body_type": "prompt.label.moved",
            "bytes": len(rev1),
            "sha256": sha(rev1),
            "signature_a": r1a,
            "signature_b": r1b,
        },
        "b8_delivered": {
            "source": "B8-EVENT-CONTRACT.md section 12.5",
            "msg_id": b8_id,
            "timestamp": b8_t,
            "bytes": delivered["bytes"],
            "sha256": delivered["sha256"],
            "signature_a": delivered["webhook_signature"][3:],
            "fixture": "b8/delivered/run-completed.json",
        },
    }
    return {"signing.json": dump(signing), "signing-v1.json": dump(signing_v1)}
