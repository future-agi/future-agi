"""Independent scalar oracle truth tables; constant-only local engine queries.

No application compiler, production connection, server table, or DDL is used.
These establish operator semantics only, not production timing/latest-state
qualification. Run with an explicit chdb runtime; absence is a disclosed skip.
"""

import json
from types import SimpleNamespace
import unittest

from clickhouse_driver.util.escape import escape_params

import observe_trace_id_reference as reference

try:
    import chdb
except ImportError:
    chdb = None


def leaf(kind, operation, value=None, key="key"):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": kind,
            "filter_op": operation,
            "filter_value": value,
        },
    }


def typed_leaf(values, types, operation="in", *, kind="text", key="key"):
    item = leaf(kind, operation, values, key)
    item["filter_config"]["attribute_value_types"] = types
    return item


@unittest.skipIf(chdb is None, "explicit local chdb runtime not available")
class ScalarReferenceEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.supports_lower_utf8 = True
        try:
            chdb.query("SELECT lowerUTF8('ABC')", "JSON")
        except RuntimeError as exc:
            if "Code: 46." in str(exc) and "lowerUTF8" in str(exc):
                # Keep numeric/boolean engine evidence usable without claiming
                # unsupported text expressions were executed successfully.
                cls.supports_lower_utf8 = False
                return
            raise

    # 'both' proves NOT EXISTS(positive) would be wrong for negative values.
    # The zero/false and missing maps prove default lookups are not presence.
    rows = [
        ("missing", [], [], [], [], [], []),
        ("equal", ["key"], ["AbC%_\\"], ["key"], [2.0], ["key"], [True]),
        ("different", ["key"], ["XYZ"], ["key"], [5.0], ["key"], [False]),
        ("both", ["key"], ["AbC%_\\"], ["key"], [2.0], ["key"], [True]),
        ("both", ["key"], ["XYZ"], ["key"], [5.0], ["key"], [False]),
        ("zero", ["key"], [""], ["key"], [0.0], ["key"], [False]),
        ("split", ["a"], ["yes"], [], [], [], []),
        ("split", [], [], ["b"], [7.0], [], []),
    ]

    # Separate fixture population: original scalar truth tables stay unchanged.
    # Each tuple is one already-latest live span, not a Python match simulation.
    typed_rows = [
        ("missing", [], [], [], [], [], []),
        ("empty_string", ["key"], [""], [], [], [], []),
        ("text_one", ["key"], ["1"], [], [], [], []),
        ("number_one", [], [], ["key"], [1.0], [], []),
        ("boolean_true", [], [], [], [], ["key"], [True]),
        ("text_zero", ["key"], ["0"], [], [], [], []),
        ("number_zero", [], [], ["key"], [0.0], [], []),
        ("boolean_false", [], [], [], [], ["key"], [False]),
        ("text_other", ["key"], ["other"], [], [], [], []),
        ("number_other", [], [], ["key"], [2.0], [], []),
        ("same_conflict", ["key"], ["1"], ["key"], [2.0], ["key"], [False]),
        ("same_conflict_reverse", ["key"], ["other"], ["key"], [1.0], ["key"], [False]),
        ("same_matches", ["key"], ["1"], ["key"], [1.0], ["key"], [True]),
        ("both", [], [], ["key"], [1.0], [], []),
        ("both", [], [], ["key"], [2.0], [], []),
        ("split", ["a"], ["yes"], [], [], [], []),
        ("split", [], [], ["b"], [7.0], [], []),
    ]

    def query(self, filters, *, rows=None):
        having, params = reference.membership_having(filters)
        if "lowerUTF8" in having and not self.supports_lower_utf8:
            self.skipTest("local engine lacks lowerUTF8; use full engine probe")
        context = SimpleNamespace(server_info=SimpleNamespace(timezone="UTC"))
        sql = (
            """
            SELECT trace_id FROM (
                SELECT t.1 AS trace_id,
                    CAST(mapFromArrays(t.2,t.3), 'Map(String,String)') attrs_string,
                    CAST(mapFromArrays(t.4,t.5), 'Map(String,Float64)') attrs_number,
                    CAST(mapFromArrays(t.6,t.7), 'Map(String,Bool)') attrs_bool
                FROM (SELECT arrayJoin(%(fixture_rows)s) t)
            ) GROUP BY trace_id HAVING """
            + having
            + " ORDER BY trace_id"
        )
        sql = sql % escape_params(
            {**params, "fixture_rows": self.rows if rows is None else rows}, context
        )
        result = json.loads(str(chdb.query(sql, "JSON")))
        return [row["trace_id"] for row in result["data"]]

    def test_literal_and_negative_text_operators(self):
        cases = [
            ("equals", "abc%_\\", ["both", "equal"]),
            ("not_equals", "abc%_\\", ["both", "different", "zero"]),
            ("in", ["abc%_\\", "xyz"], ["both", "different", "equal"]),
            ("not_in", ["abc%_\\", "xyz"], ["zero"]),
            ("contains", "%_\\", ["both", "equal"]),
            ("not_contains", "%_\\", ["both", "different", "zero"]),
            ("starts_with", "abc", ["both", "equal"]),
            ("ends_with", "%_\\", ["both", "equal"]),
            ("is_null", None, ["missing", "split"]),
            ("is_not_null", None, ["both", "different", "equal", "zero"]),
        ]
        for op, value, expected in cases:
            with self.subTest(op=op):
                self.assertEqual(self.query([leaf("text", op, value)]), expected)

    def test_numeric_ranges_membership_and_missing_zero(self):
        cases = [
            ("equals", 0, ["zero"]),
            ("not_equals", 2, ["both", "different", "zero"]),
            ("in", [0, 2], ["both", "equal", "zero"]),
            ("not_in", [0, 2], ["both", "different"]),
            ("greater_than", 2, ["both", "different"]),
            ("greater_than_or_equal", 2, ["both", "different", "equal"]),
            ("less_than", 2, ["zero"]),
            ("less_than_or_equal", 2, ["both", "equal", "zero"]),
            ("between", [0, 2], ["both", "equal", "zero"]),
            ("not_between", [0, 2], ["both", "different"]),
            ("between", [2, 0], []),
            ("not_between", [2, 0], ["both", "different", "equal", "zero"]),
        ]
        for op, value, expected in cases:
            with self.subTest(op=op, value=value):
                self.assertEqual(self.query([leaf("number", op, value)]), expected)

    def test_boolean_missing_is_not_false(self):
        for op, value, expected in [
            ("equals", False, ["both", "different", "zero"]),
            ("not_equals", False, ["both", "equal"]),
            ("in", [True, False], ["both", "different", "equal", "zero"]),
            ("not_in", [True], ["both", "different", "zero"]),
        ]:
            with self.subTest(op=op):
                self.assertEqual(self.query([leaf("boolean", op, value)]), expected)

    def test_cross_type_leaves_can_match_on_different_spans(self):
        self.assertEqual(
            self.query(
                [
                    leaf("text", "equals", "yes", key="a"),
                    leaf("number", "greater_than", 6, key="b"),
                ]
            ),
            ["split"],
        )
        self.assertEqual(
            self.query(
                [
                    leaf("text", "equals", "ABC%_\\"),
                    leaf("number", "greater_than", 3),
                ]
            ),
            ["both"],
        )

    def test_typed_homogeneous_numbers_do_not_match_numeric_strings_or_booleans(self):
        for values, positive, negative in (
            (
                [1],
                ["number_one", "same_conflict_reverse", "same_matches", "both"],
                ["number_zero", "number_other", "same_conflict", "both"],
            ),
            (
                [0, 1],
                [
                    "number_one",
                    "number_zero",
                    "same_conflict_reverse",
                    "same_matches",
                    "both",
                ],
                ["number_other", "same_conflict", "both"],
            ),
        ):
            for op, expected in (("in", positive), ("not_in", negative)):
                with self.subTest(op=op, values=values):
                    self.assertEqual(
                        self.query(
                            [
                                typed_leaf(
                                    values, ["number"] * len(values), op, kind="number"
                                )
                            ],
                            rows=self.typed_rows,
                        ),
                        sorted(expected),
                    )

    def test_typed_homogeneous_booleans_do_not_match_zero_one_or_missing_maps(self):
        for values, positive, negative in (
            (
                [True],
                ["boolean_true", "same_matches"],
                ["boolean_false", "same_conflict", "same_conflict_reverse"],
            ),
            (
                [False],
                ["boolean_false", "same_conflict", "same_conflict_reverse"],
                ["boolean_true", "same_matches"],
            ),
            (
                [True, False],
                [
                    "boolean_true",
                    "boolean_false",
                    "same_conflict",
                    "same_conflict_reverse",
                    "same_matches",
                ],
                [],
            ),
        ):
            for op, expected in (("in", positive), ("not_in", negative)):
                with self.subTest(op=op, values=values):
                    self.assertEqual(
                        self.query(
                            [
                                typed_leaf(
                                    values,
                                    ["boolean"] * len(values),
                                    op,
                                    kind="boolean",
                                )
                            ],
                            rows=self.typed_rows,
                        ),
                        sorted(expected),
                    )

    def test_typed_homogeneous_strings_do_not_match_raw_numbers(self):
        for values, positive, negative in (
            (
                ["1"],
                ["text_one", "same_conflict", "same_matches"],
                ["empty_string", "text_zero", "text_other", "same_conflict_reverse"],
            ),
            (
                ["0", "OTHER"],
                ["text_zero", "text_other", "same_conflict_reverse"],
                ["empty_string", "text_one", "same_conflict", "same_matches"],
            ),
        ):
            for op, expected in (("in", positive), ("not_in", negative)):
                with self.subTest(op=op, values=values):
                    self.assertEqual(
                        self.query(
                            [typed_leaf(values, ["string"] * len(values), op)],
                            rows=self.typed_rows,
                        ),
                        sorted(expected),
                    )

    def test_mixed_same_span_not_in_requires_no_selected_type_positive(self):
        for op, expected in (
            (
                "in",
                [
                    "text_one",
                    "number_one",
                    "boolean_true",
                    "same_conflict",
                    "same_conflict_reverse",
                    "same_matches",
                    "both",
                ],
            ),
            (
                "not_in",
                [
                    "empty_string",
                    "text_zero",
                    "number_zero",
                    "boolean_false",
                    "text_other",
                    "number_other",
                    "both",
                ],
            ),
        ):
            with self.subTest(op=op):
                self.assertEqual(
                    self.query(
                        [
                            typed_leaf(
                                ["1", 1, True], ["string", "number", "boolean"], op
                            )
                        ],
                        rows=self.typed_rows,
                    ),
                    sorted(expected),
                )

    def test_mixed_zero_false_presence_and_same_key_conflicting_maps(self):
        for op, expected in (
            (
                "in",
                [
                    "number_zero",
                    "boolean_false",
                    "same_conflict",
                    "same_conflict_reverse",
                ],
            ),
            (
                "not_in",
                ["number_one", "boolean_true", "number_other", "same_matches", "both"],
            ),
        ):
            with self.subTest(op=op):
                self.assertEqual(
                    self.query(
                        [typed_leaf([0, False], ["number", "boolean"], op)],
                        rows=self.typed_rows,
                    ),
                    sorted(expected),
                )

    def test_typed_positive_and_negative_leaves_can_match_different_spans(self):
        self.assertEqual(
            self.query(
                [
                    typed_leaf([1], ["number"], "in"),
                    typed_leaf([1], ["number"], "not_in"),
                ],
                rows=self.typed_rows,
            ),
            ["both"],
        )

    def test_mixed_positive_and_negative_leaves_are_not_trace_level_negation(self):
        self.assertEqual(
            self.query(
                [
                    typed_leaf(["1", 1, True], ["string", "number", "boolean"], "in"),
                    typed_leaf(
                        ["1", 1, True], ["string", "number", "boolean"], "not_in"
                    ),
                ],
                rows=self.typed_rows,
            ),
            ["both"],
        )

    def test_typed_cross_key_conjunction_preserves_each_independent_leaf(self):
        for forbidden, expected in (([0], ["split"]), ([7], [])):
            with self.subTest(forbidden=forbidden):
                self.assertEqual(
                    self.query(
                        [
                            typed_leaf(["yes"], ["string"], key="a"),
                            typed_leaf(forbidden, ["number"], "not_in", key="b"),
                        ],
                        rows=self.typed_rows,
                    ),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
