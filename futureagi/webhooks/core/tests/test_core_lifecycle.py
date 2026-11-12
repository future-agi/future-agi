"""Retention windows and member erasure (``webhooks.core.lifecycle``; DESIGN.md sections 7.5 and 13.4, P1).
Standard library only."""

from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path

from webhooks.core import lifecycle, payloads

CONF = Path(__file__).resolve().parents[1] / "conformance"
T0 = 1_792_663_200_000
DAY = 86_400_000


def _people(obj) -> list[dict]:
    """Every person field (``*_by``) that holds a name."""
    out: list[dict] = []

    def walk(v, key=""):
        if isinstance(v, dict):
            if key.endswith("_by") and isinstance(v.get("name"), str):
                out.append(v)
            for k, x in v.items():
                walk(x, k)
        elif isinstance(v, list):
            for x in v:
                walk(x, key)

    walk(obj)
    return out


class ErasureTests(unittest.TestCase):
    def _bodies(self):
        cases = json.loads((CONF / "payloads.json").read_text())["build"]
        return [base64.b64decode(c["expected"]["body_bytes_b64"]) for c in cases]

    def test_canonical_round_trip_keeps_every_vector_body(self):
        bodies = self._bodies()
        self.assertGreaterEqual(len(bodies), 20)
        for b in bodies:
            self.assertEqual(payloads.canonical(json.loads(b)), b)

    def test_erasing_a_person_changes_only_their_name(self):
        changed = 0
        for b in self._bodies():
            people = _people(json.loads(b))
            for p in people:
                new, names = lifecycle.erase_body(b, str(p["id"]))
                self.assertIsNotNone(new)
                self.assertEqual(names, [p["name"]] * names.count(p["name"]))
                self.assertNotIn(
                    json.dumps(p["name"])[1:-1].encode(),
                    new.replace(b'"name":null', b""),
                )
                self.assertEqual(new.replace(b'"name":null', b"")[:20], b[:20])
                changed += 1
        self.assertGreater(changed, 0, "the vectors name at least one person")

    def test_a_body_that_never_names_the_person_is_left_alone(self):
        b = payloads.canonical(
            {
                "id": "evt_1",
                "data": {
                    "label": {"id": "u1", "name": "L"},
                    "changed_by": {"id": "u1", "name": "Asha"},
                },
            }
        )
        self.assertEqual(lifecycle.erase_body(b, "u2"), (None, []))
        new, names = lifecycle.erase_body(b, "u1")
        self.assertEqual(
            new,
            b'{"id":"evt_1","data":{"label":{"id":"u1","name":"L"},"changed_by":{"id":"u1","name":null}}}',
        )
        self.assertEqual(names, ["Asha"])
        # Already erased: nothing to write.
        self.assertEqual(lifecycle.erase_body(new, "u1"), (None, []))

    def test_scrub_names(self):
        self.assertEqual(
            lifecycle.scrub_names("hi Asha Rao, Asha", ["Asha", "Asha Rao"]), "hi , "
        )
        self.assertEqual(lifecycle.scrub_names("a b", ["a"]), "a b")
        self.assertIsNone(lifecycle.scrub_names(None, ["x"]))


class CutoffTests(unittest.TestCase):
    def test_cutoffs(self):
        c = lifecycle.cutoffs(T0, None)
        self.assertEqual(c["body"], T0 - 30 * DAY)
        self.assertEqual(c["zero_delivery_event"], T0 - 7 * DAY)
        self.assertEqual(c["log"], T0 - 90 * DAY)
        self.assertEqual(c["listen_idle"], T0 - DAY)
        self.assertEqual(lifecycle.cutoffs(T0, 3)["log"], T0 - 7 * DAY)
        self.assertEqual(lifecycle.cutoffs(T0, 999)["log"], T0 - 365 * DAY)
        self.assertEqual(lifecycle.cutoffs(T0, 30, scale=0.0001)["log"], T0 - 259_200)


if __name__ == "__main__":
    unittest.main()
