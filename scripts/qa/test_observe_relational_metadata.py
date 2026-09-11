import copy
import unittest
from pathlib import Path

import replay_observe_filters as replay
from observe_relational_metadata import SnapshotMetadata, annotation_properties
from test_replay_observe_filters import SCOPE
from verify_annotation_mirror_readonly import compare_scores
from observe_annotation_reference import (
    annotation_entity_ids,
    score_matches,
    reference_span_ids,
    span_identity,
)
from types import SimpleNamespace
from datetime import datetime, timezone


def document():
    project = SCOPE["project_id"]
    return {
        "scope": SCOPE,
        "authorized_projects": [project],
        "captured_at": "2026-09-04T00:00:00Z",
        "transaction_read_only": True,
        "eval_config_inventory": [],
        "annotation_inventory": [
            {
                "label_id": "label",
                "name": "Text",
                "type": "text",
                "project_ids": [project],
            }
        ],
        "annotation_scores_snapshot": [score()],
    }


def score():
    return {
        "score_id": "score",
        "project_id": SCOPE["project_id"],
        "label_id": "label",
        "value": {"text": "50%_done"},
        "annotator_id": "annotator",
        "trace_id": "trace",
        "span_id": None,
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-02T00:00:00Z",
        "deleted": False,
    }


class RealMetadataTests(unittest.TestCase):
    def test_span_identity_preserves_trace_project_and_microseconds(self):
        row = {
            "project_id": "p",
            "trace_id": "t",
            "id": "s",
            "start_time": "2026-09-01T00:00:00.123456Z",
            "observation_type": "SPAN",
            "service_name": "service",
            "_version": 9,
        }
        a = span_identity(row)
        self.assertEqual(a[:3], ["p", "t", "s"])
        self.assertEqual(a[3] % 3600000000, 0)
        self.assertEqual(a[4:6], ["SPAN", "service"])
        self.assertEqual(a[6] % 1000000, 123456)
        self.assertEqual(a[7], "9")
        self.assertEqual(
            a,
            span_identity({**row, "start_time": datetime(2026, 9, 1, 0, 0, 0, 123456)}),
        )
        corrected = span_identity(
            {**row, "start_time": "2026-09-01T00:10:00Z", "_version": 10}
        )
        self.assertEqual(a[:6], corrected[:6])
        self.assertNotEqual(a, corrected)
        for change in (
            {"project_id": "foreign"},
            {"trace_id": "other"},
            {"id": "other"},
            {"start_time": "2026-09-01T01:00:00Z"},
            {"observation_type": "GENERATION"},
            {"service_name": "other"},
        ):
            self.assertNotEqual(a[:6], span_identity({**row, **change})[:6])
        for key in (
            "project_id",
            "trace_id",
            "id",
            "observation_type",
            "service_name",
            "_version",
        ):
            with self.subTest(missing=key), self.assertRaises(replay.ReplayError):
                span_identity({k: v for k, v in row.items() if k != key})
        for version in (True, -1, "9", None):
            with self.subTest(version=version), self.assertRaises(replay.ReplayError):
                span_identity({**row, "_version": version})
        for timestamp in (None, 123, True, "invalid"):
            with (
                self.subTest(timestamp=timestamp),
                self.assertRaisesRegex(
                    replay.ReplayError, "REFERENCE_SPAN_TIMESTAMP_INVALID"
                ),
            ):
                span_identity({**row, "start_time": timestamp})
        self.assertEqual(
            a,
            span_identity({**row, "start_time": "2026-08-31T17:00:00.123456-07:00"}),
        )

    def test_span_reference_uses_score_roots_not_candidate_ids_or_siblings(self):
        doc = document()
        doc["annotation_scores_complete"] = True
        cfg = {
            "filter_type": "text",
            "filter_op": "equals",
            "col_type": "ANNOTATION",
            "filter_value": "50%_done",
        }
        root = {
            "project_id": SCOPE["project_id"],
            "trace_id": "trace",
            "id": "root",
            "start_time": datetime(2026, 9, 1, tzinfo=timezone.utc),
            "parent_span_id": "",
            "observation_type": "SPAN",
            "service_name": "root-service",
            "_version": 2,
        }
        captured = []

        class Reader:
            def execute_ch_query(self, sql, params, **kwargs):
                captured.append((sql, dict(params)))
                return SimpleNamespace(data=[root])

        actual, info = reference_span_ids(
            Reader(),
            {
                "window": {
                    "start": "2026-08-01T00:00:00Z",
                    "end": "2026-09-05T00:00:00Z",
                },
                "request": {"target_rows": 25},
            },
            SCOPE,
            [{"column_id": "label", "filter_config": cfg}],
            doc,
        )
        self.assertEqual(actual, [span_identity(root)])
        self.assertEqual(captured[0][1]["ref_trace_ids"], ("trace",))
        self.assertEqual(
            captured[-1][1]["ref_span_coordinates"], (tuple(span_identity(root)[:6]),)
        )
        child_same_external_ids = {
            **root,
            "service_name": "child-service",
            "parent_span_id": "parent",
        }
        self.assertNotIn(
            tuple(span_identity(child_same_external_ids)[:6]),
            captured[-1][1]["ref_span_coordinates"],
        )
        self.assertNotIn("ref_span_pairs", captured[-1][1])
        self.assertIn("toStartOfHour(start_time)", captured[-1][0])
        self.assertIn("observation_type DESC,service_name DESC", captured[-1][0])
        self.assertEqual(
            info["span_evidence_contract"], "CH25_six_part_identity_winner_order.v2"
        )
        self.assertTrue(info["population_exhausted"])
        self.assertTrue(
            all("FINAL" in sql and "candidate" not in sql for sql, _ in captured)
        )

    def test_independent_text_oracle_uses_literal_metacharacters(self):
        for op, value, expected in (
            ("contains", "%_", True),
            ("contains", "_absent", False),
            ("not_contains", "%_", False),
            ("starts_with", "50%", True),
            ("ends_with", "_done", True),
            ("equals", "50%_done", True),
            ("not_equals", "different", True),
            ("in", ["50%_done"], True),
            ("not_in", ["different"], True),
            ("is_null", None, True),
        ):
            with self.subTest(op=op, value=value):
                self.assertEqual(
                    score_matches(
                        score(),
                        {
                            "filter_config": {
                                "filter_type": "text",
                                "filter_op": op,
                                "filter_value": value,
                            }
                        },
                    ),
                    expected,
                )

    def test_negative_annotator_cannot_be_guessed_as_a_per_row_complement(self):
        with self.assertRaises(replay.ReplayError):
            score_matches(
                score(),
                {
                    "filter_config": {
                        "filter_type": "annotator",
                        "filter_op": "not_equals",
                        "filter_value": "another",
                    }
                },
            )

    def test_negative_annotator_excludes_the_entity_even_with_another_author(self):
        rows = [
            score(),
            {**score(), "annotator_id": "different"},
            {**score(), "trace_id": "other", "annotator_id": "different"},
            {**score(), "trace_id": None, "span_id": "span"},
        ]
        leaf = {
            "filter_config": {
                "filter_type": "annotator",
                "filter_op": "not_in",
                "filter_value": ["annotator"],
            }
        }
        self.assertEqual(
            annotation_entity_ids(rows, leaf, lambda ids: ["span-parent"]), {"other"}
        )
        leaf["filter_config"]["filter_op"] = "in"
        self.assertEqual(
            annotation_entity_ids(rows, leaf, lambda ids: ["span-parent"]),
            {"trace", "span-parent"},
        )

    def test_negative_choices_require_a_score_without_any_selected_value(self):
        leaf = {
            "filter_config": {
                "filter_type": "categorical",
                "filter_op": "not_in",
                "filter_value": ["a", "b"],
            }
        }
        for selected, matched in (
            (["a", "other"], False),
            (["b"], False),
            (["other"], True),
            ([], True),
        ):
            with self.subTest(selected=selected):
                self.assertEqual(
                    score_matches({**score(), "value": {"selected": selected}}, leaf),
                    matched,
                )
        # There is no entity without a live Score, even for a negative value.
        self.assertEqual(annotation_entity_ids([], leaf, lambda ids: []), set())
        rows = [
            {**score(), "value": {"selected": ["a"]}},
            {**score(), "value": {"selected": ["other"]}},
        ]
        self.assertEqual(annotation_entity_ids(rows, leaf, lambda ids: []), {"trace"})

    def test_replay_covers_every_general_public_type_operator(self):
        contract = replay.read_json(
            Path(__file__).resolve().parents[2] / "api_contracts/filter_contract.json"
        )
        values = {
            "text": "a_%",
            "number": 1.5,
            "boolean": False,
            "datetime": "2026-09-01T00:00:00Z",
            "categorical": ["a"],
            "thumbs": "up",
            "annotator": "actor",
            "array": ["a"],
        }
        for kind, allowed in contract["operators"]["filterTypeAllowed"].items():
            with self.subTest(kind=kind):
                prop = {
                    "name": "field",
                    "column_id": "label",
                    "col_type": "ANNOTATION",
                    "observed_types": [kind],
                    "seeds": [{"type": kind, "value": values[kind]}],
                }
                actual = {
                    leaves[0]["filter_config"]["filter_op"]
                    for _, leaves, blocked in replay.variants(prop)
                    if not blocked
                }
                self.assertEqual(actual, set(allowed))

    def test_unscoped_or_foreign_metadata_fails(self):
        data = document()
        for change in ({"transaction_read_only": False}, {"authorized_projects": []}):
            with self.assertRaises(replay.ReplayError):
                SnapshotMetadata({**data, **change}, SCOPE, [SCOPE["project_id"]])

    def test_display_names_and_observed_user_filter_are_not_metadata_ownership(self):
        data = document()
        data["scope"] = {key: value for key, value in SCOPE.items() if key != "user_id"}
        plan_scope = {**SCOPE, "project_label": "Project", "customer_label": "Customer"}
        before = copy.deepcopy(data)
        metadata = SnapshotMetadata(data, plan_scope, data["authorized_projects"])
        self.assertEqual(metadata.document, before)
        self.assertEqual(metadata.fingerprint, replay.digest(before))
        self.assertEqual(metadata.scope["user_id"], SCOPE["user_id"])

    def test_metadata_ownership_fields_cannot_be_missing_or_different(self):
        for key in ("organization_id", "workspace_id", "project_id"):
            for replacement in (None, "", "foreign"):
                with self.subTest(key=key, replacement=replacement):
                    data = document()
                    data["scope"] = {**SCOPE, key: replacement}
                    with self.assertRaises(replay.ReplayError):
                        SnapshotMetadata(data, SCOPE, data["authorized_projects"])
            data = document()
            data["scope"] = {k: v for k, v in SCOPE.items() if k != key}
            with self.assertRaises(replay.ReplayError):
                SnapshotMetadata(data, SCOPE, data["authorized_projects"])

    def test_unknown_scope_fields_are_not_silently_ignored(self):
        data = document()
        with self.assertRaises(replay.ReplayError):
            SnapshotMetadata(
                data, {**SCOPE, "tenant_id": "foreign"}, data["authorized_projects"]
            )

    def test_label_scope_is_not_union_of_all_projects(self):
        data = document()
        data["authorized_projects"].append("second")
        metadata = SnapshotMetadata(data, SCOPE, data["authorized_projects"])
        self.assertEqual(metadata.label_map(["second"]), {"second": []})
        with self.assertRaises(replay.ReplayError):
            metadata.labels_for_project("foreign")

    def test_property_inputs_exclude_deleted_and_other_project_scores(self):
        data = document()
        data["annotation_scores_snapshot"] += [
            {**score(), "deleted": True, "value": {"text": "deleted"}},
            {**score(), "project_id": "foreign", "value": {"text": "foreign"}},
        ]
        props = annotation_properties(data, SCOPE["project_id"])
        self.assertEqual(props[0]["seeds"], [{"type": "text", "value": "50%_done"}])
        self.assertEqual(props[1]["column_id"], "label**annotator")

    def test_special_annotation_kinds_have_value_and_presence_coverage(self):
        for kind, value in (
            ("categorical", ["a", "b"]),
            ("thumbs", "up"),
            ("annotator", "actor"),
        ):
            prop = {
                "name": "annotation",
                "column_id": "label",
                "col_type": "ANNOTATION",
                "observed_types": [kind],
                "seeds": [{"type": kind, "value": value}],
            }
            variants = list(replay.variants(prop))
            self.assertTrue(all(not blocked for _, _, blocked in variants))
            ops = {leaves[0]["filter_config"]["filter_op"] for _, leaves, _ in variants}
            self.assertTrue(
                {"equals", "not_equals", "in", "not_in", "is_null", "is_not_null"}
                <= ops
            )
            self.assertTrue(
                all(
                    leaves[0]["filter_config"]["filter_type"] == kind
                    for _, leaves, _ in variants
                )
            )

    def test_mirror_comparison_ignores_serialization_not_semantic_changes(self):
        mirrored = {
            **score(),
            "value": '{"text":"50%_done"}',
            "span_id": "",
            "deleted": 0,
            "created_at": "2026-09-01T00:00:00+00:00",
        }
        self.assertEqual(compare_scores([score()], [mirrored])["status"], "MATCH")
        for changed in (
            {"value": {"text": "changed"}},
            {"deleted": True},
            {"project_id": "foreign"},
        ):
            self.assertEqual(
                compare_scores([score()], [{**mirrored, **changed}])["changed_records"],
                1,
            )

    def test_absent_extra_duplicate_rows_cannot_pass(self):
        self.assertEqual(compare_scores([score()], [])["missing_ids"], 1)
        self.assertEqual(compare_scores([], [score()])["extra_ids"], 1)
        with self.assertRaises(replay.ReplayError):
            compare_scores([score(), copy.deepcopy(score())], [])


if __name__ == "__main__":
    unittest.main()
