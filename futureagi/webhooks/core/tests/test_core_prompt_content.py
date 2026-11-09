"""XP-06: full payload mode's 64 KB content cut (DESIGN.md section 14.1). No Django.

The inputs are the frozen ``payloads.json`` prompt cases with their message text made large, so every assertion is
about the cut alone: a body that fits is byte-identical to the vector, and a body that does not fit is cut to the
largest per-text code-point cap that fits, with ``content_truncated: true`` right after ``content``. A body that cannot
fit even with every text cut to nothing has ``content_omitted: "too_large"`` instead of its content.
"""

from __future__ import annotations

import base64
import copy
import json
import unittest

from webhooks.core import payloads, prompt_content
from webhooks.core.tests.test_conformance_schemas_events import load, validator

CASES = {c["id"]: c for c in load("payloads.json")["build"]}
FULL = {"payload_mode": "full", "include_people_names": True}
THIN = {"payload_mode": "thin", "include_people_names": True}
BUILD = {
    "prompt.version.created": payloads.build_prompt_version_created,
    "prompt.label.changed": payloads.build_prompt_label_changed,
}


def _input(case_id: str, texts: list[str] | None = None) -> dict:
    inp = copy.deepcopy(CASES[case_id]["input"])
    if texts is not None:
        inp["content"] = {
            "messages": [
                {"role": "system" if i == 0 else "user", "text": t}
                for i, t in enumerate(texts)
            ],
            "variables": ["question"],
            "model": "gpt-4o-mini",
        }
    return inp


def _body(builder: str, inp: dict, variant: dict) -> bytes:
    return payloads.render(BUILD[builder](inp, variant))


class FullModeCut(unittest.TestCase):
    def test_bodies_that_fit_are_the_frozen_vectors_byte_for_byte(self):
        for cid in ("pvc-full", "pvc-full-truncated", "pvc-full-content-secret"):
            with self.subTest(case=cid):
                c = CASES[cid]
                body = _body(c["builder"], _input(cid), c["variant"])
                self.assertEqual(
                    body, base64.b64decode(c["expected"]["body_bytes_b64"])
                )
                self.assertNotIn(b"content_truncated", body)

    def test_an_oversized_version_body_is_cut_to_the_largest_cap_that_fits(self):
        big = ["You answer refund questions. " * 3000, "{{question}} " * 4000]
        self._check_cut("prompt.version.created", "pvc-full", big)

    def test_an_oversized_label_body_is_cut_the_same_way(self):
        inp_case = "plc-moved"
        big = ["Ünïcödé 文字 " * 9000]
        self._check_cut("prompt.label.changed", inp_case, big)

    def _check_cut(self, builder: str, case_id: str, texts: list[str]):
        inp = _input(case_id, texts)
        body = _body(builder, inp, FULL)
        self.assertLessEqual(len(body), payloads.MAX_BODY_BYTES)
        doc = json.loads(body)
        data = doc["data"]
        keys = list(data)
        self.assertEqual(keys[keys.index("content") + 1], "content_truncated")
        self.assertIs(data["content_truncated"], True)
        self.assertEqual(keys[-1], "content_truncated")
        cut = [m["text"] for m in data["content"]["messages"]]
        cap = max(len(t) for t in cut)
        self.assertLess(cap, max(len(t) for t in texts))
        for original, got in zip(texts, cut, strict=True):
            self.assertEqual(got, original[:cap])
        # The cap is the largest that fits: one more code point per text does not.
        env = BUILD[builder](inp, FULL)
        bigger = copy.deepcopy(env)
        bigger["data"]["content"] = prompt_content.cut(
            prompt_content.allowlisted(inp["content"]), cap + 1
        )
        self.assertGreater(len(payloads.canonical(bigger)), payloads.MAX_BODY_BYTES)
        # Everything other than the texts is unchanged.
        self.assertEqual(data["content"]["variables"], ["question"])
        self.assertEqual(data["content"]["model"], "gpt-4o-mini")
        errors = list(validator(CASES[case_id]["schema"]).iter_errors(doc))
        self.assertEqual(errors, [], [e.message for e in errors])

    def test_thin_mode_never_carries_content_so_it_is_never_cut(self):
        inp = _input("pvc-full", ["x" * 200_000])
        body = _body("prompt.version.created", inp, THIN)
        self.assertNotIn(b'"content', body)
        self.assertEqual(
            body, base64.b64decode(CASES["pvc-thin"]["expected"]["body_bytes_b64"])
        )

    def test_a_secret_shape_in_oversized_content_omits_it_instead_of_cutting(self):
        inp = _input("pvc-full", ["x" * 200_000 + " sk-abcdefghijkl"])
        doc = json.loads(_body("prompt.version.created", inp, FULL))
        self.assertEqual(doc["data"]["content_omitted"], "secret_shape")
        self.assertNotIn("content", doc["data"])
        self.assertNotIn("content_truncated", doc["data"])

    def test_a_body_that_cannot_fit_even_with_empty_texts_omits_the_content_as_too_large(
        self,
    ):
        """The endpoint still gets the event (a raise here would fail the event for every endpoint at fan-out)."""
        for builder, cid in (
            ("prompt.version.created", "pvc-full"),
            ("prompt.label.changed", "plc-moved"),
        ):
            with self.subTest(builder=builder):
                inp = _input(cid, ["a" * 5000])
                inp["content"]["variables"] = ["v" * 100] * 1000  # 100 KB of names
                body = _body(builder, inp, FULL)
                self.assertLessEqual(len(body), payloads.MAX_BODY_BYTES)
                doc = json.loads(body)
                data = doc["data"]
                self.assertEqual(list(data)[-1], "content_omitted")
                self.assertEqual(data["content_omitted"], "too_large")
                self.assertNotIn("content", data)
                self.assertNotIn("content_truncated", data)
                # The frozen schema (conformance 1.1.0) knows only "secret_shape": "too_large" is the one additive
                # value this needs, and nothing else in the body is off-schema. The handoff to the WH-0 owner (the
                # case, the reference change and the schema enum, conformance 1.5.0) admits it; then nothing is.
                check = validator(CASES[cid]["schema"])
                admits = "too_large" in json.dumps(check.schema)
                self.assertEqual(
                    [list(e.absolute_path) for e in check.iter_errors(doc)],
                    [] if admits else [["data", "content_omitted"]],
                )
                # Everything else is the body the vectors pin, unchanged.
                fit = _input(cid, ["a"])
                expected = json.loads(_body(builder, fit, FULL))["data"]
                expected.pop("content")
                data.pop("content_omitted")
                self.assertEqual(data, expected)

    def test_fit_returns_the_same_envelope_when_it_fits(self):
        env = {"data": {"content": {"messages": [{"role": "u", "text": "hi"}]}}}
        self.assertIs(prompt_content.fit_body(env, lambda e: 10, 64), env)
        no_content = {"data": {"prompt": {}}}
        self.assertIs(
            prompt_content.fit_body(no_content, lambda e: 999, 64), no_content
        )


if __name__ == "__main__":
    unittest.main()
