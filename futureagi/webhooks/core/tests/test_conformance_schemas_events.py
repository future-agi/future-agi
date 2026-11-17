"""WH-0 checks of the frozen v1 event schemas against the payload vectors. No Django.

Needs ``jsonschema`` (in FA's requirements) and nothing else. Every body in ``payloads.json`` and the delivered B8 event
validates against its type schema and the envelope, and the type schemas refuse what DESIGN.md section 14 forbids.
"""

from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

CONF = Path(__file__).resolve().parents[1] / "conformance"
SCHEMAS = [
    json.loads(p.read_text(encoding="utf-8"))
    for p in sorted((CONF / "schemas").rglob("*.json"))
]
REGISTRY = Registry().with_resources(
    (s["$id"], Resource.from_contents(s, default_specification=DRAFT202012))
    for s in SCHEMAS
)
BY_ID = {s["$id"]: s for s in SCHEMAS}


def validator(schema_id: str) -> Draft202012Validator:
    return Draft202012Validator(
        BY_ID[schema_id],
        registry=REGISTRY,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


def load(name: str) -> dict:
    return json.loads((CONF / name).read_text(encoding="utf-8"))


class EventPayloads(unittest.TestCase):
    def test_every_payload_vector_validates_against_its_type_schema(self):
        for c in load("payloads.json")["build"]:
            with self.subTest(case=c["id"]):
                doc = json.loads(base64.b64decode(c["expected"]["body_bytes_b64"]))
                errors = list(validator(c["schema"]).iter_errors(doc))
                self.assertEqual(errors, [], [e.message for e in errors])
                self.assertEqual(
                    list(
                        validator("urn:futureagi:webhooks:v1:envelope").iter_errors(doc)
                    ),
                    [],
                )

    def test_the_delivered_b8_event_validates(self):
        d = load("b8/delivered/run-completed.json")
        doc = json.loads(base64.b64decode(d["body_bytes_b64"]))
        self.assertEqual(
            list(
                validator(
                    "urn:futureagi:webhooks:v1:deployment.run.completed"
                ).iter_errors(doc)
            ),
            [],
        )

    def test_type_schemas_refuse_what_they_must(self):
        cases = {
            c["id"]: json.loads(base64.b64decode(c["expected"]["body_bytes_b64"]))
            for c in load("payloads.json")["build"]
        }
        completed = validator("urn:futureagi:webhooks:v1:deployment.run.completed")
        failed = validator("urn:futureagi:webhooks:v1:deployment.run.failed")
        plc = validator("urn:futureagi:webhooks:v1:prompt.label.changed")
        pvc = validator("urn:futureagi:webhooks:v1:prompt.version.created")
        self.assertNotEqual(
            list(failed.iter_errors(cases["b8-12-5"])),
            [],
            "a succeeded run is not a failed event",
        )
        self.assertNotEqual(
            list(completed.iter_errors(cases["b8-12-4-cancelled-queued"])), []
        )
        moved = json.loads(json.dumps(cases["plc-moved"]))
        moved["data"]["from_version"] = None
        self.assertNotEqual(
            list(plc.iter_errors(moved)), [], "moved needs from_version"
        )
        replaced = json.loads(json.dumps(cases["plc-removed-replaced"]))
        replaced["data"]["action"] = "moved"
        self.assertNotEqual(
            list(plc.iter_errors(replaced)), [], "cause replaced is a removal"
        )
        email = json.loads(json.dumps(cases["pvc-thin"]))
        email["data"]["version"]["created_by"]["email"] = "asha@example.com"
        self.assertNotEqual(
            list(pvc.iter_errors(email)), [], "a person carries no email"
        )
        extra = json.loads(json.dumps(cases["pvc-full"]))
        extra["data"]["content"]["tools"] = [{"name": "search"}]
        self.assertNotEqual(
            list(pvc.iter_errors(extra)), [], "full mode is an allowlist"
        )
        bad_id = json.loads(json.dumps(cases["pvc-thin"]))
        bad_id["id"] = "evt_1"
        self.assertNotEqual(list(pvc.iter_errors(bad_id)), [])


if __name__ == "__main__":
    unittest.main()
