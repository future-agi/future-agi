"""B8 fixtures added in conformance 1.1.0 and the b8/fixtures.json rules block.

They pin where EE (Node, Ajv) and FA (Python, jsonschema) would otherwise answer differently: strict UTF-8 and no
NaN/Infinity literals at parse, the exact protocol rule, lone surrogates and non-finite numbers in webhook_context
(b8/1.1), and the 1,024-byte context size measured with the canonical serializer, not a language's own dump.
"""

from __future__ import annotations

import copy
import json

from . import reference as R
from .common import CONFORMANCE_VERSION, check

PLACEHOLDER = "@@VALUE@@"


def _with_context_value(n1: dict, raw_value: bytes) -> bytes:
    """The section 12.1 notice with webhook_context {"note": <raw_value>}, raw_value spliced in as JSON text, so the
    file can hold what a serializer would never write (a lone surrogate escape, 1e400, NaN, a bad UTF-8 byte)."""
    t = copy.deepcopy(n1)
    t["run"]["webhook_context"] = {"ticket": "48213", "note": PLACEHOLDER}
    raw = R.canonical(t)
    token = json.dumps(PLACEHOLDER).encode()
    check(raw.count(token) == 1, "placeholder")
    return raw.replace(token, raw_value)


def _context_of(n1: dict, ctx: dict) -> dict:
    t = copy.deepcopy(n1)
    t["run"]["webhook_context"] = ctx
    return t


def _sizes(ctx: dict) -> dict:
    """The canonical size and the sizes the usual wrong measures give."""
    return {
        "canonical": len(R.canonical(ctx)),
        "python_dumps_ascii": len(json.dumps(ctx, separators=(",", ":")).encode()),
        "python_dumps_utf8": len(
            json.dumps(ctx, separators=(",", ":"), ensure_ascii=False).encode()
        ),
        "code_points": len(R.canonical(ctx).decode("utf-8")),
        "utf16_units": len(R.canonical(ctx).decode("utf-8").encode("utf-16-le")) // 2,
    }


def b8_more_fixtures(add, n1: dict) -> None:
    accepted = {"status": 202}

    def bad(field):
        return {"status": 400, "code": "invalid_field", "field": field}

    # Parse: strict UTF-8, and JSON as RFC 8259 defines it (Python's json accepts NaN; JSON.parse does not).
    add(
        "invalid",
        "not-utf8",
        _with_context_value(n1, b'"caf\xff"'),
        bad(""),
        "a 0xFF byte inside a string: not UTF-8, so not JSON (decode strictly; never replace with U+FFFD)",
    )
    add(
        "invalid",
        "nan-literal",
        _with_context_value(n1, b"NaN"),
        bad(""),
        "a NaN literal is not JSON (Python's json.loads accepts it unless parse_constant refuses)",
    )

    # Protocol: 503 only for a well-formed omega-deploy-run-event/v<N> with N != 1, or an event string v1 does not
    # know; anything else about protocol or event is the schema's (400).
    for name, value, note in [
        ("protocol-missing", None, "no protocol key"),
        (
            "protocol-malformed",
            "omega-deploy-run-event/v1.1",
            "not omega-deploy-run-event/v<N>, so not an unknown major: 400",
        ),
        (
            "protocol-v01",
            "omega-deploy-run-event/v01",
            "a major has no leading zero: 400",
        ),
        ("protocol-not-a-string", 1, "a number, not a string: 400"),
    ]:
        t = copy.deepcopy(n1)
        if value is None:
            del t["protocol"]
        else:
            t["protocol"] = value
        add("invalid", name, t, bad("protocol"), note, "schema")
    t = copy.deepcopy(n1)
    t["event"] = 1
    add(
        "invalid",
        "event-not-a-string",
        t,
        bad("event"),
        "an event that is not a string is malformed, not unknown: 400",
        "schema",
    )

    # b8/1.1: webhook_context strings are valid Unicode and its numbers finite, checked before the schema.
    add(
        "invalid",
        "webhook-context-lone-surrogate",
        _with_context_value(n1, b'"\\ud800"'),
        bad("run.webhook_context"),
        "b8/1.1: a lone surrogate escape; JSON-valid, but no UTF-8 body can carry it, so the event could never be built",
        "context",
    )
    add(
        "invalid",
        "webhook-context-non-finite",
        _with_context_value(n1, b"1e400"),
        bad("run.webhook_context"),
        "b8/1.1: 1e400 parses to Infinity in both languages; the event body cannot carry it",
        "context",
    )

    # The 1,024-byte rule, measured as the UTF-8 length of the canonical (JSON.stringify) text.
    base = {"a": "é" * 256, "b": "😀" * 100}
    exact = {**base, "c": "x" * 90}
    over = {**base, "c": "x" * 91}
    s_exact, s_over = _sizes(exact), _sizes(over)
    check(
        s_exact["canonical"] == 1024 and s_exact["python_dumps_ascii"] > 1024,
        "non-ASCII 1024",
    )
    check(
        s_over["canonical"] == 1025
        and s_over["code_points"] < 1024
        and s_over["utf16_units"] < 1024,
        "non-ASCII 1025",
    )
    add(
        "valid",
        "run-completed-context-1024-bytes-non-ascii",
        _context_of(n1, exact),
        accepted,
        f"webhook_context of exactly 1,024 bytes of raw UTF-8 ('é' and an emoji); measured with \\u escapes it would "
        f"be {s_exact['python_dumps_ascii']} bytes",
    )
    add(
        "invalid",
        "webhook-context-1025-bytes-non-ascii",
        _context_of(n1, over),
        bad("run.webhook_context"),
        f"1,025 bytes of raw UTF-8 but only {s_over['code_points']} code points ({s_over['utf16_units']} UTF-16 "
        f"units): a measure in characters would wrongly accept it",
        "semantic",
    )
    pad = {"a": "x" * 256, "b": "x" * 256, "c": "x" * 256}
    f_exact = {**pad, "r": 1.5e-7, "d": "x" * 0}
    f_exact["d"] = "x" * (1024 - len(R.canonical(f_exact)))
    f_over = {**pad, "r": 0.00001, "d": "x" * 0}
    f_over["d"] = "x" * (1025 - len(R.canonical(f_over)))
    fe, fo = _sizes(f_exact), _sizes(f_over)
    check(fe["canonical"] == 1024 and fe["python_dumps_utf8"] == 1025, "float 1024")
    check(fo["canonical"] == 1025 and fo["python_dumps_utf8"] == 1023, "float 1025")
    add(
        "valid",
        "run-completed-context-1024-bytes-float",
        _context_of(n1, f_exact),
        accepted,
        "exactly 1,024 bytes with 1.5e-7 written as JSON.stringify does; Python's json.dumps writes 1.5e-07, "
        "1,025 bytes",
    )
    add(
        "invalid",
        "webhook-context-1025-bytes-float",
        _context_of(n1, f_over),
        bad("run.webhook_context"),
        "1,025 bytes with 0.00001 as JSON.stringify writes it; Python's json.dumps writes 1e-05, 1,023 bytes",
        "semantic",
    )


STEPS = ["parse", "protocol", "identity", "context", "schema", "semantic", "accepted"]

RULES = {
    "parse": "Decode the body as strict UTF-8 (no BOM, no invalid byte, no encoded surrogate; never substitute "
    "U+FFFD) and parse it as RFC 8259 JSON (no NaN, Infinity or -Infinity literal). Failure, or a value that is not "
    "an object: 400 invalid_field, field ''.",
    "protocol": "503 protocol_unsupported (EE: 503 relay_unavailable, reason protocol_unsupported) when either "
    "protocol is a JSON string matching ^omega-deploy-run-event/v([1-9][0-9]*)$ whose major is not 1, or event is "
    "a JSON string other than run.completed and run.failed. Nothing else answers 503: an absent protocol, one that "
    "is not a string, or any other text (omega-deploy-run-event/v01, /v1.1, 'v1 ') goes on to the schema, which "
    "answers 400 invalid_field 'protocol'; likewise an event that is absent or not a string gives 'event'.",
    "identity": "the deny list: top level deployment, organization_id, workspace_id, project_id, relay, then in run "
    "deployment_id, organization_id, workspace_id, project_id; the first present, in that order, is the field of "
    "400 identity_not_allowed.",
    "context": "b8/1.1, checked before the schema so the answer does not depend on a validator's number handling "
    "(Ajv's default strictNumbers refuses Infinity inside the schema step): when run is an object and "
    "run.webhook_context is an object, a key or string value holding a lone surrogate (a \\ud800-\\udfff escape "
    "not part of a pair), or a number value that is not finite (1e400 parses to Infinity), is 400 invalid_field "
    "'run.webhook_context'.",
    "schema": "notice.v1.schema.json. field is the path of the failing value, dot-joined (for a required "
    "failure the missing key is appended; inside a oneOf, the value that fails). field is normative only when the "
    "notice has exactly one schema error, as every fixture here has: with several, jsonschema's best_match and "
    "Ajv may name different ones, and EE and FA may differ.",
    "semantic": "in contract order: event_status_mismatch (field event); run.error null iff succeeded; "
    "created_at <= started_at <= ended_at when started_at is set and created_at <= ended_at always, reporting the "
    "later field; run.webhook in URL form with no userinfo and no fragment; run.webhook_context at most 1,024 "
    "bytes, measured as the UTF-8 length of the context written by the canonical serializer of payloads.json "
    "(JSON.stringify: raw UTF-8, never \\u escapes; numbers as ECMAScript writes them). Not Python's json.dumps, "
    "not characters or UTF-16 units.",
    "accepted": "202 with the answer of accepted.v1.schema.json.",
}


def fixtures_file(index: list[dict]) -> dict:
    steps = {f["step"] for f in index}
    check(steps <= set(STEPS), f"unknown step in {steps}")
    return {
        "conformance_version": CONFORMANCE_VERSION,
        "file": "b8-fixtures",
        "contract": "b8/1.1",
        "contract_note": "b8/1 plus one additive rule (rules.context: webhook_context values are valid Unicode and "
        "finite) and three clarifications that change no b8/1 answer: strict UTF-8 and RFC 8259 at parse, the exact "
        "protocol rule, and the canonical measure of the 1,024-byte rule",
        "schema": "schemas/b8/notice.v1.schema.json",
        "order": "parse (invalid_field ''), protocol major and event known (else 503 protocol_unsupported), identity "
        "deny list (identity_not_allowed), context values (b8/1.1, invalid_field run.webhook_context), schema "
        "(invalid_field with the failing path), semantic rules in contract order; each fixture names its step",
        "steps": STEPS,
        "rules": RULES,
        "fixtures": index,
    }
