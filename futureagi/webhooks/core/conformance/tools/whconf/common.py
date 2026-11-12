"""Constants and helpers shared by the case builders: the conformance version, the documents' byte-exact facts, the test secrets and ids, and the JSON writers."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

CONFORMANCE_VERSION = "1.4.0"

ROOT = Path(__file__).resolve().parents[2]

SECRET_A = "whsec_AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="

SECRET_B = "whsec_ICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8="

SECRET_C = "whsec_QEFCQ0RFRkdISUpLTE1OT1BRUlNUVVZXWFlaW1xdXl8="

ORG = "11111111-1111-4111-8111-111111111111"

WS = "22222222-2222-4222-8222-222222222222"

WS2 = "44444444-4444-4444-8444-444444444444"

PROJECT = "8d3e5f10-2a4b-4c6d-8e9f-0a1b2c3d4e5f"

# The webhook-id of the DESIGN.md section 5.6 signing vector.
DESIGN_5_6_ID = "evt_01JBF3Q7Z8X4N6R2T5V9W0YB1C"

DOC_FACTS = {
    "design_5_6": {
        "bytes": 354,
        "sha256": "ac7e28475e9235c1b98d946d2a765d7fa2a23cfef772e04595e9be8c80fd11ee",
        "sig_a": "h44L1CBrM2Suy84SL8vIpjMw9eJ7NfkC+Avik5ObsHc=",
        "sig_b": "vmoA63RoGzXfAx8e81YfT8hMnQHugHS2+xXSTUN1Vcw=",
        "t": 1792663200,
        "t_iso": "2026-10-22T10:00:00Z",
    },
    "design_5_6_rev1": {
        "bytes": 352,
        "sha256_prefix": "e7fb8b71",
        "sha256_suffix": "eca59",
        "sig_a_prefix": "3FNJPSXj",
        "sig_a_suffix": "UCI=",
        "sig_b_prefix": "iAGgdMsk",
        "sig_b_suffix": "C/Y=",
    },
    "b8_12_1": {
        "bytes": 745,
        "sha256": "040256445075186f045ed16266ef7cc2b4a3678ce8b2be6892060dfd4be0f0f1",
    },
    "b8_12_4": {
        "bytes": 586,
        "sha256": "f4f3c7c27a8fbcea84de3480c52d0be96c9bfaa77666f23c3ddf24609670c469",
    },
    "b8_12_5": {
        "bytes": 1415,
        "sha256": "4c9fa8f5522c936a8308bf01decbd1568966efbb1deacbc03ad6bb9211a1b7ed",
        "t": 1792664415,
        "sig_a": "HYzAQwCNM0KEuW/OEXXlN/vKbkPawb4S6CuIDuyRXho=",
    },
}


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def body_fields(b: bytes) -> dict:
    return {
        "body_bytes_b64": b64(b),
        "body_text": b.decode("utf-8"),
        "bytes": len(b),
        "sha256": sha(b),
    }


def dump(obj) -> bytes:
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def check(cond, msg):
    if not cond:
        raise SystemExit(f"DOC FACT MISMATCH: {msg}")


# Prompt-side ids and link bases used by the payload and fan-out cases.
P1 = "6c0e4b0a-1d2e-4f3a-8b4c-5d6e7f8a9b0c"
V7 = "9b1d2c3e-4f5a-4b6c-8d7e-9f0a1b2c3d4e"
V6 = "47aa5b6c-7d8e-4f9a-8b0c-1d2e3f4a5b6c"
L_PROD = "33333333-3333-4333-8333-333333333333"
L_STAGE = "55555555-5555-4555-8555-555555555555"
ASHA = "a51f6e7d-8c9b-4a0f-9e1d-2c3b4a5f6e7d"
APP = "https://app.example.test"
API = "https://api.example.test"
