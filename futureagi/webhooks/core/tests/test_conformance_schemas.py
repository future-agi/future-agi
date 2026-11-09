"""WH-0 checks of the frozen JSON Schemas against the fixtures and payload vectors. No Django.

Needs ``jsonschema`` (in FA's requirements) and nothing else. The intake order here follows B8-EVENT-CONTRACT.md
sections 4.2 and 5.1 and the rules block of ``b8/fixtures.json``: parse, protocol and event known, identity deny list,
context values (b8/1.1), schema, semantic rules. EE's route and FA's intake must give the same code, field
and step for every file in ``b8/fixtures.json``. This oracle is written from those rules, not from ``tools/whconf``.
"""

from __future__ import annotations

import json
import math
import re
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

CONF = Path(__file__).resolve().parents[1] / "conformance"
SCHEMAS = {
    p: json.loads(p.read_text(encoding="utf-8"))
    for p in sorted((CONF / "schemas").rglob("*.json"))
}
REGISTRY = Registry().with_resources(
    (s["$id"], Resource.from_contents(s, default_specification=DRAFT202012))
    for s in SCHEMAS.values()
)
BY_ID = {s["$id"]: s for s in SCHEMAS.values()}
NOTICE_ID = "urn:futureagi:omega-deploy-run-event:v1:notice"
KNOWN_EVENTS = ("run.completed", "run.failed")


def validator(schema_id: str) -> Draft202012Validator:
    return Draft202012Validator(
        BY_ID[schema_id],
        registry=REGISTRY,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


def load(name: str) -> dict:
    return json.loads((CONF / name).read_text(encoding="utf-8"))


def ms(ts: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"[0-9]+", ts))


def es_number(x: float) -> str:
    """ECMAScript Number::toString (what JSON.stringify writes), from Python's shortest round-trip digits."""
    if not math.isfinite(x):
        raise ValueError("not finite")
    if x == 0:
        return "0"
    if x < 0:
        return "-" + es_number(-x)
    mantissa, _, exp = repr(x).partition("e")
    whole, _, frac = mantissa.partition(".")
    digits = (whole + frac).lstrip("0")
    point = int(exp or 0) + len(whole) - (len(whole + frac) - len(digits))
    digits = digits.rstrip("0")
    k, n = len(digits), point
    if k <= n <= 21:
        return digits + "0" * (n - k)
    if 0 < n <= 21:
        return digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return "0." + "0" * -n + digits
    e = n - 1
    return (
        (digits[0] + ("." + digits[1:] if k > 1 else ""))
        + "e"
        + ("+" if e >= 0 else "-")
        + str(abs(e))
    )


def es_json(v) -> str:
    """JSON.stringify of a parsed JSON value: compact, raw UTF-8 strings, ECMAScript numbers."""
    if v is None or isinstance(v, bool):
        return json.dumps(v)
    if isinstance(v, int) and abs(v) < 2**53:
        return str(v)
    if isinstance(v, int | float):
        return es_number(float(v))
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ",".join(es_json(x) for x in v) + "]"
    return (
        "{"
        + ",".join(
            json.dumps(k, ensure_ascii=False) + ":" + es_json(x) for k, x in v.items()
        )
        + "}"
    )


def _refuse_constant(name: str):
    raise ValueError(f"{name} is not JSON")


def _lone_surrogate(s: str) -> bool:
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in s)


def bad(field: str) -> dict:
    return {"status": 400, "code": "invalid_field", "field": field}


def schema_errors(doc) -> list:
    return list(validator(NOTICE_ID).iter_errors(doc))


def intake(raw: bytes) -> tuple[dict, str]:
    """The answer EE and FA give for a notice body (status, code, field), and the step that gives it."""
    try:
        doc = json.loads(raw.decode("utf-8"), parse_constant=_refuse_constant)
    except ValueError:
        return bad(""), "parse"
    if not isinstance(doc, dict):
        return bad(""), "parse"
    proto, event = doc.get("protocol"), doc.get("event")
    major = (
        re.fullmatch(r"omega-deploy-run-event/v([1-9][0-9]*)", proto)
        if isinstance(proto, str)
        else None
    )
    if (major and major.group(1) != "1") or (
        isinstance(event, str) and event not in KNOWN_EVENTS
    ):
        return {"status": 503, "code": "protocol_unsupported"}, "protocol"
    for key in ("deployment", "organization_id", "workspace_id", "project_id", "relay"):
        if key in doc:
            return {
                "status": 400,
                "code": "identity_not_allowed",
                "field": key,
            }, "identity"
    run = doc.get("run")
    if isinstance(run, dict):
        for key in ("deployment_id", "organization_id", "workspace_id", "project_id"):
            if key in run:
                return {
                    "status": 400,
                    "code": "identity_not_allowed",
                    "field": "run." + key,
                }, "identity"
        ctx = run.get("webhook_context")
        if isinstance(ctx, dict) and any(
            _lone_surrogate(k)
            or (isinstance(v, str) and _lone_surrogate(v))
            or (isinstance(v, float) and not math.isfinite(v))
            for k, v in ctx.items()
        ):
            return bad("run.webhook_context"), "context"
    errors = schema_errors(doc)
    if errors:
        err = best_match(errors)
        path = [str(p) for p in err.absolute_path]
        if err.validator == "required":
            path.append(next(k for k in err.validator_value if k not in err.instance))
        return bad(".".join(path)), "schema"
    if (doc["event"] == "run.completed") != (run["status"] == "succeeded"):
        return {
            "status": 400,
            "code": "event_status_mismatch",
            "field": "event",
        }, "semantic"
    if (run["error"] is None) != (run["status"] == "succeeded"):
        return bad("run.error"), "semantic"
    if run["started_at"] is not None:
        if ms(run["created_at"]) > ms(run["started_at"]):
            return bad("run.started_at"), "semantic"
        if ms(run["started_at"]) > ms(run["ended_at"]):
            return bad("run.ended_at"), "semantic"
    if ms(run["created_at"]) > ms(run["ended_at"]):
        return bad("run.ended_at"), "semantic"
    hook = run["webhook"]
    if isinstance(hook, str) and hook.startswith("https://"):
        authority = hook[len("https://") :].split("/", 1)[0]
        if "@" in authority or "#" in hook:
            return bad("run.webhook"), "semantic"
    ctx = run["webhook_context"]
    if ctx is not None and len(es_json(ctx).encode("utf-8")) > 1024:
        return bad("run.webhook_context"), "semantic"
    return {"status": 202}, "accepted"


class SchemaFiles(unittest.TestCase):
    def test_every_schema_is_a_valid_2020_12_schema_with_a_unique_id(self):
        ids = [s["$id"] for s in SCHEMAS.values()]
        self.assertEqual(len(ids), len(set(ids)))
        for path, schema in SCHEMAS.items():
            with self.subTest(schema=path.name):
                self.assertEqual(
                    schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
                )
                Draft202012Validator.check_schema(schema)

    def test_the_expected_schema_set(self):
        names = sorted(p.relative_to(CONF / "schemas").as_posix() for p in SCHEMAS)
        self.assertEqual(
            names,
            [
                "b8/accepted.v1.schema.json",
                "b8/error.v1.schema.json",
                "b8/notice.v1.schema.json",
                "events/deployment.run.completed.v1.schema.json",
                "events/deployment.run.failed.v1.schema.json",
                "events/deployment.run.v1.schema.json",
                "events/envelope.v1.schema.json",
                "events/prompt.label.changed.v1.schema.json",
                "events/prompt.version.created.v1.schema.json",
            ],
        )


class B8Fixtures(unittest.TestCase):
    def test_every_fixture_gives_its_listed_answer_at_its_step(self):
        fx = load("b8/fixtures.json")
        index = fx["fixtures"]
        self.assertGreaterEqual(sum(f["kind"] == "valid" for f in index), 12)
        self.assertGreaterEqual(sum(f["kind"] == "invalid" for f in index), 26)
        for f in index:
            with self.subTest(fixture=f["file"]):
                self.assertIn(f["step"], fx["steps"])
                self.assertEqual(
                    intake((CONF / f["file"]).read_bytes()), (f["expect"], f["step"])
                )

    def test_a_schema_answer_comes_from_exactly_one_schema_error(self):
        # field is normative only for single-error notices; jsonschema's best_match and Ajv may differ otherwise.
        for f in load("b8/fixtures.json")["fixtures"]:
            if f["step"] == "schema":
                with self.subTest(fixture=f["file"]):
                    doc = json.loads((CONF / f["file"]).read_bytes())
                    self.assertEqual(len(schema_errors(doc)), 1)

    def test_the_context_size_is_the_canonical_length(self):
        # Typed in: Python's json.dumps would measure these contexts differently, and wrongly.
        sizes = {
            "b8/valid/run-completed-context-1024-bytes.json": 1024,
            "b8/valid/run-completed-context-1024-bytes-non-ascii.json": 1024,
            "b8/valid/run-completed-context-1024-bytes-float.json": 1024,
            "b8/invalid/webhook-context-1025-bytes.json": 1025,
            "b8/invalid/webhook-context-1025-bytes-non-ascii.json": 1025,
            "b8/invalid/webhook-context-1025-bytes-float.json": 1025,
        }
        for name, size in sizes.items():
            with self.subTest(fixture=name):
                ctx = json.loads((CONF / name).read_bytes())["run"]["webhook_context"]
                self.assertEqual(len(es_json(ctx).encode("utf-8")), size)
        float_ctx = json.loads(
            (CONF / "b8/invalid/webhook-context-1025-bytes-float.json").read_bytes()
        )["run"]["webhook_context"]
        self.assertEqual(
            len(json.dumps(float_ctx, separators=(",", ":"), ensure_ascii=False)), 1023
        )

    def test_es_number_matches_javascript(self):
        # Typed in from Node's JSON.stringify.
        for value, text in [
            (1.0, "1"),
            (0.00001, "0.00001"),
            (1.5e-7, "1.5e-7"),
            (1e21, "1e+21"),
            (1e16, "10000000000000000"),
            (2.0**60, "1152921504606847000"),
            (0.1 + 0.2, "0.30000000000000004"),
            (-0.0, "0"),
            (123.456, "123.456"),
            (5e-324, "5e-324"),
            (1.7976931348623157e308, "1.7976931348623157e+308"),
        ]:
            self.assertEqual(es_number(value), text, value)

    def test_valid_fixtures_pass_the_schema_and_cover_the_contract_list(self):
        statuses, kinds, forms = set(), set(), set()
        for p in sorted((CONF / "b8" / "valid").glob("*.json")):
            doc = json.loads(p.read_bytes())
            self.assertEqual(list(validator(NOTICE_ID).iter_errors(doc)), [], p.name)
            statuses.add(doc["run"]["status"])
            kinds.add(doc["run"]["trigger"]["kind"])
            hook = doc["run"]["webhook"]
            forms.add(
                "none"
                if hook is None
                else ("id" if hook.startswith("whep_") else "url")
            )
        self.assertTrue({"succeeded", "failed", "cancelled", "interrupted"} <= statuses)
        self.assertTrue({"api", "cron", "studio", "batch"} <= kinds)
        self.assertEqual(forms, {"none", "id", "url"})

    def test_answers_from_the_contract_validate(self):
        accepted = validator("urn:futureagi:omega-deploy-run-event:v1:accepted")
        error = validator("urn:futureagi:omega-deploy-run-event:v1:error")
        for body in (
            {
                "event_id": "evt_01M5HJ03YMFHNNMJ9R4WB0BX73",
                "duplicate": False,
                "webhook": "targeted",
            },
            {
                "event_id": "evt_01M5HJ03YMFHNNMJ9R4WB0BX73",
                "duplicate": True,
                "webhook": "targeted",
            },
        ):
            self.assertEqual(list(accepted.iter_errors(body)), [])
        self.assertNotEqual(
            list(
                accepted.iter_errors(
                    {"event_id": "evt_x", "duplicate": False, "webhook": "x"}
                )
            ),
            [],
        )
        for body in (
            {
                "error": {
                    "code": "notice_conflict",
                    "message": "This notice id was already accepted with a different body.",
                }
            },
            {
                "error": {
                    "code": "relay_unavailable",
                    "message": "The Future AGI deployment link is not ready.",
                    "reason": "not_linked",
                }
            },
        ):
            self.assertEqual(list(error.iter_errors(body)), [])


if __name__ == "__main__":
    unittest.main()
