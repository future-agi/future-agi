"""signing.json: the verify cases, each with its reason id (DESIGN.md sections 5.4-5.6)."""

from __future__ import annotations

from . import reference as R
from .common import DESIGN_5_6_ID, SECRET_A, SECRET_B, SECRET_C, b64


def verify_cases(
    *, body, t, sa, sb, tampered, overlap, b8_body, b8_t, b8_id, delivered
) -> list[dict]:
    def hdr(msg_id, ts, sig):
        return {
            "webhook-id": msg_id,
            "webhook-timestamp": str(ts),
            "webhook-signature": sig,
        }

    verify_cases = []

    def vcase(cid, secret, headers, b, now, note):
        res = R.verify(secret, headers, b, now)
        verify_cases.append(
            {
                "id": cid,
                "secret": secret,
                "headers": headers,
                "body_bytes_b64": b64(b),
                "now": now,
                "expected": res,
                "note": note,
            }
        )

    vcase(
        "a-ok", SECRET_A, hdr(DESIGN_5_6_ID, t, "v1," + sa), body, t, "secret A, at t"
    )
    vcase(
        "b-ok", SECRET_B, hdr(DESIGN_5_6_ID, t, "v1," + sb), body, t, "secret B, at t"
    )
    vcase(
        "overlap-a",
        SECRET_A,
        hdr(DESIGN_5_6_ID, t, overlap),
        body,
        t + 10,
        "rotation overlap: the receiver holds A",
    )
    vcase(
        "overlap-b",
        SECRET_B,
        hdr(DESIGN_5_6_ID, t, overlap),
        body,
        t + 10,
        "rotation overlap: the receiver holds B",
    )
    vcase(
        "overlap-c-fails",
        SECRET_C,
        hdr(DESIGN_5_6_ID, t, overlap),
        body,
        t,
        "a third secret matches neither entry",
    )
    vcase(
        "tampered-byte-10",
        SECRET_A,
        hdr(DESIGN_5_6_ID, t, "v1," + sa),
        tampered,
        t,
        "body byte 10 (0-based) XOR 0x01",
    )
    vcase(
        "wrong-secret", SECRET_C, hdr(DESIGN_5_6_ID, t, "v1," + sa), body, t, "secret C"
    )
    vcase(
        "t-plus-300-ok",
        SECRET_A,
        hdr(DESIGN_5_6_ID, t, "v1," + sa),
        body,
        t + 300,
        "now = t + 300 passes",
    )
    vcase(
        "t-plus-301-stale",
        SECRET_A,
        hdr(DESIGN_5_6_ID, t, "v1," + sa),
        body,
        t + 301,
        "now = t + 301 fails",
    )
    vcase(
        "t-minus-300-ok",
        SECRET_A,
        hdr(DESIGN_5_6_ID, t, "v1," + sa),
        body,
        t - 300,
        "t 300 s in the future passes",
    )
    vcase(
        "t-minus-301-future",
        SECRET_A,
        hdr(DESIGN_5_6_ID, t, "v1," + sa),
        body,
        t - 301,
        "t 301 s in the future fails",
    )
    vcase(
        "other-id",
        SECRET_A,
        hdr("evt_01JBF3Q7Z8X4N6R2T5V9W0YB1D", t, "v1," + sa),
        body,
        t,
        "a signature from another webhook-id (a header from another request)",
    )
    vcase(
        "other-t",
        SECRET_A,
        hdr(DESIGN_5_6_ID, t + 1, "v1," + sa),
        body,
        t,
        "the signature was made for t, sent as t + 1",
    )
    vcase(
        "unknown-version-ignored",
        SECRET_A,
        hdr(DESIGN_5_6_ID, t, f"v1a,AAAA v1,{sa}"),
        body,
        t,
        "an unknown version entry is ignored",
    )
    vcase(
        "only-unknown-version",
        SECRET_A,
        hdr(DESIGN_5_6_ID, t, f"v2,{sa}"),
        body,
        t,
        "a matching value under an unknown version does not count",
    )
    vcase(
        "header-names-any-case",
        SECRET_A,
        {
            "Webhook-Id": DESIGN_5_6_ID,
            "WEBHOOK-TIMESTAMP": str(t),
            "Webhook-Signature": "v1," + sa,
        },
        body,
        t,
        "header names are case-insensitive",
    )
    vcase(
        "missing-signature-header",
        SECRET_A,
        {"webhook-id": DESIGN_5_6_ID, "webhook-timestamp": str(t)},
        body,
        t,
        "no webhook-signature header",
    )
    vcase(
        "timestamp-not-integer",
        SECRET_A,
        hdr(DESIGN_5_6_ID, "1792663200.5", "v1," + sa),
        body,
        t,
        "a timestamp that is not an integer",
    )
    vcase(
        "b8-delivered-ok",
        SECRET_A,
        hdr(b8_id, b8_t, delivered["webhook_signature"]),
        b8_body,
        b8_t + 2,
        "B8-EVENT-CONTRACT.md section 12.5",
    )
    vcase(
        "b8-delivered-tampered",
        SECRET_A,
        hdr(b8_id, b8_t, delivered["webhook_signature"]),
        b8_body.replace(b'"usd_micros":4127', b'"usd_micros":4128'),
        b8_t,
        "one changed digit in the cost",
    )
    return verify_cases
