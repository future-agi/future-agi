"""Offline reference contracts; no production reads."""

from types import SimpleNamespace
from datetime import datetime, timedelta
import copy
import unittest
from unittest.mock import Mock, patch

import pytest

import observe_trace_id_reference as reference
import replay_observe_filters as replay


def leaf(kind="text", op="in", value=None):
    return {
        "column_id": "tenant.identifier",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": kind,
            "filter_op": op,
            "filter_value": ["0012"] if value is None else value,
        },
    }


def typed_leaf(values, types, *, kind="text", op="in", alias="attribute_value_types"):
    item = leaf(kind, op, values)
    item["filter_config"].update(filter_value=values, **{alias: types})
    return item


class TypedMembershipReferenceTests(unittest.TestCase):
    def test_real_qa_picker_leaves_are_accepted_without_stripping_provenance(self):
        for storage, values in (
            ("string", ["10000001", "0012"]),
            ("number", [0, 12]),
            ("boolean", [False, True]),
        ):
            for op in ("in", "not_in"):
                with self.subTest(storage=storage, op=op):
                    item = replay.raw_leaf({"name": "company_id"}, storage, op, values)
                    original = copy.deepcopy(item)
                    having, params = reference.membership_having([item])
                    self.assertEqual(item, original)
                    self.assertEqual(params[f"ref_value_0_{storage}"], values)
                    self.assertIn("countIf(", having)
                    self.assertEqual(" AND NOT (" in having, op == "not_in")

    def test_homogeneous_and_mixed_operands_keep_explicit_maps_and_strict_types(self):
        for kind, values, types, expected in (
            ("text", ["0012", "ABC"], ["string"] * 2, {"string": ["0012", "abc"]}),
            ("number", [0, 12.5], ["number"] * 2, {"number": [0, 12.5]}),
            ("boolean", [False, True], ["boolean"] * 2, {"boolean": [False, True]}),
            (
                "text",
                ["0012", 12, False],
                ["string", "number", "boolean"],
                {"string": ["0012"], "number": [12], "boolean": [False]},
            ),
        ):
            for alias in ("attribute_value_types", "attributeValueTypes"):
                with self.subTest(kind=kind, alias=alias):
                    item = typed_leaf(values, types, kind=kind, alias=alias)
                    original = copy.deepcopy(item)
                    having, params = reference.membership_having([item])
                    self.assertEqual(item, original)
                    self.assertEqual(having.count("countIf("), 1)
                    self.assertNotIn("JSON", having)
                    for storage, column in (
                        ("string", "attrs_string"),
                        ("number", "attrs_number"),
                        ("boolean", "attrs_bool"),
                    ):
                        self.assertEqual(column in having, storage in expected)
                        if storage in expected:
                            bound = params[f"ref_value_0_{storage}"]
                            self.assertEqual(bound, expected[storage])
                            self.assertEqual(
                                [type(v) for v in bound],
                                [type(v) for v in expected[storage]],
                            )

    def test_negative_membership_has_one_span_presence_and_negates_all_positives(self):
        item = typed_leaf(
            ["blocked", 0, False], ["string", "number", "boolean"], op="not_in"
        )
        having, _ = reference.membership_having([item])
        self.assertEqual(having.count("countIf("), 1)
        self.assertEqual(having.count("NOT ("), 1)
        self.assertTrue(
            having.startswith(
                "countIf((mapContains(attrs_string, %(ref_key_0)s) OR "
                "mapContains(attrs_number, %(ref_key_0)s) OR "
                "mapContains(attrs_bool, %(ref_key_0)s)) AND NOT ("
            )
        )
        self.assertEqual(having.count(" AND has("), 3)
        self.assertTrue(having.endswith(") > 0"))

    def test_every_operand_must_have_nonempty_known_aligned_provenance(self):
        invalid = (
            (["x"], None),
            (["x"], []),
            ([], []),
            (["x"], "string"),
            (["x"], ("string",)),
            (["x"], ["string", "number"]),
            (("x",), ["string"]),
            (None, ["string"]),
            (["x"], [None]),
            (["x"], [""]),
            (["x"], ["json"]),
            (["x"], ["array"]),
            (["x"], ["text"]),
            (["x"], ["String"]),
            (["x"], [["string"]]),
            (["x"], [{"type": "string"}]),
        )
        for values, types in invalid:
            with (
                self.subTest(values=values, types=types),
                self.assertRaises(replay.ReplayError),
            ):
                reference.membership_having([typed_leaf(values, types)])

    def test_mismatched_nested_and_nonfinite_operands_are_not_coerced(self):
        for value, storage in (
            ("12", "number"),
            (12, "string"),
            (True, "number"),
            (False, "number"),
            (0, "boolean"),
            (1, "boolean"),
            ("false", "boolean"),
            ("", "string"),
            (None, "string"),
            (["x"], "string"),
            ({"x": 1}, "number"),
            (float("nan"), "number"),
            (float("inf"), "number"),
            (-float("inf"), "number"),
            (10**400, "number"),
        ):
            with (
                self.subTest(value=value, storage=storage),
                self.assertRaises(replay.ReplayError),
            ):
                reference.membership_having([typed_leaf([value], [storage])])

    def test_unsupported_provenance_operators_and_json_ui_types_fail_closed(self):
        for op in ("equals", "not_equals", "between", "contains", "is_null"):
            with self.subTest(op=op), self.assertRaises(replay.ReplayError):
                reference.membership_having([typed_leaf(["x"], ["string"], op=op)])
        for kind in ("array", "map", "json", "unknown"):
            with self.subTest(kind=kind), self.assertRaises(replay.ReplayError):
                reference.membership_having([typed_leaf(["x"], ["string"], kind=kind)])
        item = typed_leaf(["12"], ["string"])
        item["filter_config"]["attributeValueTypes"] = ["number"]
        with self.assertRaisesRegex(replay.ReplayError, "PROVENANCE_CONFLICT"):
            reference.membership_having([item])

    def test_invalid_provenance_fails_before_any_reference_read(self):
        reader = Mock()
        with self.assertRaises(replay.ReplayError):
            reference.reference_ids(
                reader,
                {"surface": "traces"},
                {},
                [typed_leaf(["12"], ["number"])],
            )
        reader.execute_ch_query.assert_not_called()

    def test_typed_picker_reaches_independent_final_prefix_without_losing_metadata(
        self,
    ):
        for op in ("in", "not_in"):
            for values, types in (
                (["0012"], ["string"]),
                ([12], ["number"]),
                ([False], ["boolean"]),
                (["0012", 12, False], ["string", "number", "boolean"]),
            ):
                with self.subTest(op=op, types=types):
                    item = typed_leaf(values, types, op=op)
                    original = copy.deepcopy(item)
                    reader = Mock()
                    reader.execute_ch_query.side_effect = [
                        SimpleNamespace(data=[{"trace_id": "root"}]),
                        SimpleNamespace(
                            data=[
                                {
                                    "observation_type": "SPAN",
                                    "service_name": "svc",
                                    "span_hour": datetime(2025, 1, 1),
                                    "trace_id": "root",
                                }
                            ]
                        ),
                        SimpleNamespace(data=[{"trace_id": "root"}]),
                    ]
                    ids, evidence = reference.reference_ids(
                        reader,
                        {
                            "surface": "traces",
                            "window": {
                                "start": "2026-08-31T00:00:00Z",
                                "end": "2026-09-01T00:00:00Z",
                            },
                            "request": {"target_rows": 25},
                        },
                        {"project_id": "project"},
                        [item],
                    )
                    self.assertEqual(ids, ["root"])
                    self.assertTrue(evidence["population_exhausted"])
                    self.assertEqual(item, original)
                    calls = reader.execute_ch_query.call_args_list
                    self.assertEqual(len(calls), 3)
                    self.assertIn("FROM spans FINAL", calls[0].args[0])
                    self.assertNotIn("attrs_", calls[0].args[0])
                    self.assertNotIn("ref_start", calls[1].args[0])
                    self.assertIn("WHERE is_deleted=0", calls[2].args[0])
                    self.assertNotIn("ref_start", calls[2].args[0])
                    for storage in types:
                        self.assertIn(f"ref_value_0_{storage}", calls[2].args[1])

    def test_typed_strings_use_wire_lowercasing_without_numeric_casts(self):
        having, params = reference.membership_having(
            [typed_leaf(["İ", "K", "0012"], ["string"] * 3)]
        )
        self.assertEqual(params["ref_value_0_string"], ["i\u0307", "k", "0012"])
        self.assertIn("lowerUTF8(attrs_string", having)
        self.assertNotIn("arrayMap", having)  # Do not lowercase wire values twice.
        self.assertNotIn("attrs_number", having)


class MixedNumericReferenceTests(unittest.TestCase):
    def test_first_mandatory_numeric_leaf_is_selected_from_flat_and(self):
        for width in (2, 5, 10):
            items = [leaf("text", "equals", "value") for _ in range(width - 1)]
            numeric = leaf("number", "greater_than", 3)
            items.insert(1, numeric)
            if width > 2:
                items[-1] = leaf("number", "equals", 7)
            with self.subTest(width=width):
                reference.membership_having(items)  # Validate ALL mandatory leaves.
                self.assertEqual(
                    reference.positive_numeric_leaf(items), (numeric, ">", 3)
                )

    def test_ineligible_picker_and_negative_leaves_do_not_hide_later_numeric(self):
        numeric = leaf("number", "equals", 0)
        siblings = [
            typed_leaf(["0012", 12, False], ["string", "number", "boolean"]),
            leaf("number", "not_equals", 1),
            leaf("number", "is_null"),
        ]
        for sibling in siblings:
            with self.subTest(sibling=sibling):
                reference.membership_having([sibling, numeric])
                self.assertEqual(
                    reference.positive_numeric_leaf([sibling, numeric]),
                    (numeric, "=", 0),
                )
                self.assertIsNone(reference.positive_numeric_leaf([sibling]))

    def test_invalid_siblings_and_groups_fail_before_witness_io(self):
        numeric = leaf("number", "greater_than", 3)
        for sibling in (
            typed_leaf(["12"], ["number"]),
            typed_leaf([0], ["boolean"]),
            typed_leaf([False], ["number"]),
            leaf("number", "equals", "12"),
            leaf("map", "equals", {"key": 1}),
            {"operator": "OR", "filters": [numeric, leaf()]},
        ):
            reader = Mock()
            with self.subTest(sibling=sibling):
                with self.assertRaises((replay.ReplayError, KeyError)):
                    reference.reference_ids(
                        reader, {"surface": "traces"}, {}, [numeric, sibling]
                    )
                reader.execute_ch_query.assert_not_called()

    def test_mixed_witness_keeps_every_having_leaf_and_all_history(self):
        items = [
            typed_leaf(
                ["0012", 12, False], ["string", "number", "boolean"], op="not_in"
            ),
            leaf("number", "greater_than", 3),
            leaf("text", "is_null"),
        ]
        original = copy.deepcopy(items)
        reader = Mock()
        reader.execute_ch_query.side_effect = [
            SimpleNamespace(data=[{"trace_id": "raw-only"}, {"trace_id": "positive"}]),
            SimpleNamespace(
                data=[
                    {
                        "observation_type": "SPAN",
                        "service_name": "svc",
                        "span_hour": datetime(2025, 1, 1),
                        "trace_id": trace,
                    }
                    for trace in ("raw-only", "positive")
                ]
            ),
            SimpleNamespace(data=[{"trace_id": "positive"}]),
        ]
        ids, evidence = reference.reference_ids(
            reader,
            {
                "surface": "task_traces",
                "window": {
                    "start": "2026-08-31T00:00:00Z",
                    "end": "2026-09-01T00:00:00Z",
                },
                "request": {"target_rows": 1},
            },
            {"project_id": "project"},
            items,
        )
        self.assertEqual(items, original)
        self.assertEqual(ids, ["positive"])
        self.assertEqual(evidence["raw_witness_population"], 2)
        self.assertEqual(
            evidence["reference_route"], "complete_global_numeric_witness_then_FINAL"
        )
        calls = reader.execute_ch_query.call_args_list
        expected_having, expected_params = reference.membership_having(items)
        self.assertIn(f"AND ({expected_having})", calls[-1].args[0])
        for key, value in expected_params.items():
            self.assertEqual(calls[-1].args[1][key], value)
        self.assertEqual(calls[0].args[1]["ref_numeric_value"], 3)
        for call in calls[:2]:
            self.assertNotIn("ref_start", call.args[0])
            self.assertNotIn("LIMIT", call.args[0])

    def test_mixed_zero_witness_proof_is_exhaustive_not_an_empty_prefix(self):
        items = [
            leaf("text", "not_equals", "blocked"),
            leaf("number", "greater_than", 3),
        ]
        reader = Mock()
        reader.execute_ch_query.return_value = SimpleNamespace(
            data=[{"raw_witness_count": 0}]
        )
        self.assertTrue(
            reference.numeric_absence_proof(
                reader,
                items,
                {"project_id": "project", "ref_start_us": 1, "ref_end_us": 2},
            )
        )
        reader.execute_ch_query.return_value = SimpleNamespace(
            data=[{"raw_witness_count": 1}]
        )
        self.assertFalse(
            reference.numeric_absence_proof(
                reader,
                items,
                {"project_id": "project", "ref_start_us": 1, "ref_end_us": 2},
            )
        )
        self.assertNotIn("LIMIT", reader.execute_ch_query.call_args.args[0])

    def test_native_fixture_quotes_settings_without_rewriting_sql_or_params(self):
        import test_observe_session_reference as fixtures

        engine = SimpleNamespace(
            context=SimpleNamespace(
                server_info=SimpleNamespace(get_timezone=lambda: "UTC")
            ),
            execute=Mock(return_value=[]),
        )
        original_execute = engine.execute
        existing = SimpleNamespace(__wrapped__=lambda *_: iter([engine]))
        with patch.object(fixtures, "engine", existing):
            wrapper = numeric_reference_native.__wrapped__(None, None)
            adapted = next(wrapper)
            try:
                params = {"value": False}
                adapted.execute(
                    "SELECT %(value)s",
                    params,
                    {"max_result_rows": 1000, "result_overflow_mode": "throw"},
                )
                original_execute.assert_called_once_with(
                    "SELECT %(value)s SETTINGS max_result_rows=1000, result_overflow_mode='throw'",
                    params,
                )
                self.assertIs(original_execute.call_args.args[1], params)
                self.assertIs(params["value"], False)
            finally:
                wrapper.close()

    def test_native_uint8_fixture_storage_does_not_change_boolean_requests(self):
        inputs = _mixed_numeric_native_inputs(10)
        self.assertIs(inputs[2][0]["filter_config"]["filter_value"], False)
        for index in (3, 7):
            self.assertIs(inputs[index][0]["filter_config"]["filter_value"][-1], False)
            self.assertEqual(
                inputs[index][0]["filter_config"]["attribute_value_types"][-1],
                "boolean",
            )
        stored = [
            value for _, maps in inputs for value in maps.get("attrs_bool", {}).values()
        ]
        self.assertEqual(stored, [0, 1])
        self.assertTrue(all(type(value) is int for value in stored))


class BooleanAbsenceReferenceTests(unittest.TestCase):
    def test_complete_zero_only_and_no_population_restrictions(self):
        for value in (False, True):
            for count in (0, 1, 100):
                reader = Mock()
                reader.execute_ch_query.return_value = SimpleNamespace(
                    data=[{"raw_witness_count": count}]
                )
                self.assertEqual(
                    reference.boolean_absence_proof(
                        reader,
                        [leaf("boolean", "equals", value)],
                        {"project_id": "project", "ref_trace_ids": ["must-not-use"]},
                    ),
                    count == 0,
                )
                sql, params = reader.execute_ch_query.call_args.args
                self.assertEqual(
                    params,
                    {
                        "project_id": "project",
                        "ref_absence_key": "tenant.identifier",
                        "ref_absence_value": value,
                    },
                )
                self.assertIs(params["ref_absence_value"], value)
                self.assertIn("mapContains(attrs_bool", sql)
                for forbidden in (
                    "start_time",
                    "parent_span_id",
                    "trace_id",
                    "LIMIT",
                    "FINAL",
                    "is_deleted",
                ):
                    self.assertNotIn(forbidden, sql)

    def test_errors_malformed_counts_and_unsupported_leaves_never_prove_empty(self):
        reader = Mock()
        items, params = [leaf("boolean", "equals", False)], {"project_id": "project"}
        for rows in (
            [],
            [{"raw_witness_count": value} for value in (0, 0)],
            *[[{"raw_witness_count": value}] for value in (None, False, -1, "0", 0.0)],
        ):
            reader.execute_ch_query.return_value = SimpleNamespace(data=rows)
            with self.assertRaisesRegex(
                replay.ReplayError, "REFERENCE_ABSENCE_COUNT_INVALID"
            ):
                reference.boolean_absence_proof(reader, items, params)
        reader.execute_ch_query.side_effect = TimeoutError("incomplete read")
        with self.assertRaises(TimeoutError):
            reference.boolean_absence_proof(reader, items, params)
        for item in (
            leaf("boolean", "not_equals", False),
            leaf("boolean", "equals", 0),
            leaf("boolean", "is_null"),
            leaf("number", "equals", 0),
            typed_leaf([False], ["boolean"]),
        ):
            reader.reset_mock()
            self.assertFalse(reference.boolean_absence_proof(reader, [item], params))
            reader.execute_ch_query.assert_not_called()

    def test_zero_proof_short_circuits_but_positive_witness_does_not(self):
        reader = Mock()
        case = {
            "surface": "traces",
            "request": {"target_rows": 25},
            "window": {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-09-01T00:00:00Z",
            },
        }
        for count in (0, 1):
            reader.execute_ch_query.return_value = SimpleNamespace(
                data=[{"raw_witness_count": count}]
            )
            with (
                patch.object(
                    reference, "complete_scalar_witness_reference", return_value=None
                ),
                patch.object(
                    reference, "root_prefix", side_effect=RuntimeError("normal oracle")
                ) as prefix,
            ):
                if count:
                    with self.assertRaisesRegex(RuntimeError, "normal oracle"):
                        reference.reference_ids(
                            reader,
                            case,
                            {"project_id": "project"},
                            [leaf("boolean", "equals", False)],
                        )
                else:
                    ids, evidence = reference.reference_ids(
                        reader,
                        case,
                        {"project_id": "project"},
                        [leaf("boolean", "equals", False)],
                    )
                    self.assertEqual(ids, [])
                    self.assertEqual(
                        evidence["absence_proof"],
                        "zero_raw_boolean_witnesses_in_entire_project",
                    )
                    prefix.assert_not_called()


class ReferenceTests(unittest.TestCase):
    def numeric_reference(self, responses):
        class Reader:
            def __init__(self):
                self.calls = []

            def execute_ch_query(self, sql, params, settings):
                self.calls.append((sql, dict(params), settings))
                result = responses[len(self.calls) - 1]
                if isinstance(result, Exception):
                    raise result
                return SimpleNamespace(data=result)

        reader = Reader()
        result = reference.reference_ids(
            reader,
            {
                "surface": "traces",
                "window": {
                    "start": "2026-08-31T00:00:00Z",
                    "end": "2026-09-01T00:00:00Z",
                },
                "request": {"target_rows": 25},
            },
            {"project_id": "project"},
            [leaf("number", "greater_than", 1)],
        )
        return reader, result

    def test_exhausted_global_numeric_population_proves_empty_without_date_scan(self):
        reader, (ids, evidence) = self.numeric_reference([[]])
        self.assertEqual(ids, [])
        self.assertTrue(evidence["population_exhausted"])
        self.assertEqual(
            evidence["absence_proof"], "zero_raw_numeric_witnesses_globally"
        )
        sql, params, settings = reader.calls[0]
        for forbidden in (
            "LIMIT",
            "FINAL",
            "is_deleted",
            "ref_start",
            "ref_end",
            "ref_trace_ids",
        ):
            self.assertNotIn(forbidden, sql)
        self.assertNotIn("ref_trace_ids", params)
        self.assertEqual(settings["distinct_overflow_mode"], "throw")
        self.assertEqual(settings["result_overflow_mode"], "throw")

    def test_numeric_raw_superset_requires_latest_root_and_all_child_replay(self):
        reader, (ids, evidence) = self.numeric_reference(
            [
                [{"trace_id": "old-version"}, {"trace_id": "live"}],
                [
                    {
                        "observation_type": "SPAN",
                        "service_name": "service",
                        "span_hour": datetime(2025, 1, 1),
                        "trace_id": "old-version",
                    },
                    {
                        "observation_type": "SPAN",
                        "service_name": "service",
                        "span_hour": datetime(2025, 1, 1),
                        "trace_id": "live",
                    },
                    {
                        "observation_type": "TRACE",
                        "service_name": "service",
                        "span_hour": datetime(2026, 8, 31),
                        "trace_id": "live",
                    },
                ],
                [{"trace_id": "live"}],
            ]
        )
        self.assertEqual(ids, ["live"])
        self.assertEqual(evidence["raw_witness_population"], 2)
        self.assertNotIn("ref_start", reader.calls[1][0])
        sql, params, settings = reader.calls[2]
        prewhere = sql.split("PREWHERE", 1)[1].split("WHERE is_deleted", 1)[0]
        self.assertNotIn("ref_start", prewhere)
        self.assertNotIn("attrs_number", prewhere)
        self.assertIn("FROM spans FINAL", sql)
        self.assertIn("WHERE is_deleted=0", sql)
        self.assertIn("HAVING countIf((parent_span_id", sql)
        self.assertIn("countIf(mapContains(attrs_number", sql)
        self.assertIn("ORDER BY root_order_time DESC, trace_id DESC", sql)
        self.assertEqual(params["ref_span_prefixes"][0][2], datetime(2025, 1, 1))
        self.assertEqual(settings["optimize_move_to_prewhere_if_final"], 0)

    def test_numeric_probe_resource_failure_never_proves_absence(self):
        from clickhouse_driver.errors import ErrorCodes, ServerException

        for code in (
            ErrorCodes.TIMEOUT_EXCEEDED,
            ErrorCodes.TOO_MANY_ROWS,
            ErrorCodes.TOO_MANY_BYTES,
        ):
            with self.subTest(code=code):
                reader, (ids, evidence) = self.numeric_reference(
                    [ServerException("budget", code=code), [{"raw_witness_count": 0}]]
                )
                self.assertEqual(len(reader.calls), 2)
                self.assertEqual(ids, [])
                self.assertEqual(
                    evidence["absence_proof"],
                    "zero_raw_numeric_witnesses_in_complete_root_population",
                )
        with self.assertRaisesRegex(ValueError, "broken"):
            self.numeric_reference([ValueError("broken")])
        with self.assertRaises(ServerException):
            self.numeric_reference(
                [ServerException("syntax", code=ErrorCodes.SYNTAX_ERROR)]
            )

    def test_zero_raw_numeric_count_proves_whole_window_absence_only(self):
        class Reader:
            count = 0
            calls = []

            def execute_ch_query(self, sql, params, settings):
                self.calls.append((sql, params))
                return SimpleNamespace(data=[{"raw_witness_count": self.count}])

        reader = Reader()
        params = {"project_id": "project", "ref_start_us": 1, "ref_end_us": 2}
        numeric = [leaf("number", "greater_than", 0.01)]
        self.assertTrue(reference.numeric_absence_proof(reader, numeric, params))
        reader.count = 1  # Could be an old version or a tombstone, NOT a match proof.
        self.assertFalse(reference.numeric_absence_proof(reader, numeric, params))
        sql, bound = reader.calls[0]
        self.assertEqual(sql.count("start_time>="), 1)
        self.assertEqual(sql.count("start_time<"), 1)
        self.assertIn("SELECT trace_id FROM spans", sql)
        self.assertIn("mapContains(attrs_number", sql)
        self.assertEqual(bound["ref_absence_value"], 0.01)
        for forbidden in ("LIMIT", "is_deleted", "FINAL", "ref_trace_ids", "candidate"):
            self.assertNotIn(forbidden, sql)
        for items in ([leaf("number", "is_null")], [leaf()]):
            before = len(reader.calls)
            self.assertFalse(reference.numeric_absence_proof(reader, items, params))
            self.assertEqual(len(reader.calls), before)

    def test_fractional_root_boundaries_do_not_truncate_to_driver_seconds(self):
        self.assertEqual(
            reference.unix_microseconds(replay.utc("1970-01-01T00:00:00.123456Z")),
            123456,
        )

    def test_positive_string_membership_preserves_types_and_parameterizes_values(self):
        having, params = reference.membership_having([leaf()])
        self.assertEqual(params["ref_value_0"], ["0012"])
        self.assertIn("mapContains(attrs_string", having)
        self.assertIn("countIf(", having)
        self.assertNotIn("0012", having)

    def test_numeric_range_and_null_membership_are_separate_any_span_leaves(self):
        having, _ = reference.membership_having(
            [leaf("number", "greater_than", 0.01), leaf("text", "is_null")]
        )
        self.assertIn("attrs_number", having)
        self.assertIn("> %(ref_value_0)s", having)
        self.assertIn("countIf(mapContains(attrs_string, %(ref_key_1)s)) = 0", having)
        self.assertIn(" AND countIf(", having)

    def test_unsupported_and_mistyped_values_do_not_get_a_false_oracle(self):
        for item in (
            leaf("map", "equals", {}),
            leaf("text", "in", [12]),
            leaf("text", "not_in", []),
            leaf("number", "equals", True),
            leaf("number", "equals", float("nan")),
            leaf("boolean", "equals", 1),
            leaf("number", "between", [1]),
            leaf("number", "contains", 1),
        ):
            with self.subTest(item=item), self.assertRaises(replay.ReplayError):
                reference.membership_having([item])

    def test_negative_value_leaves_require_an_existing_differing_span(self):
        for kind, op, value in (
            ("text", "not_equals", "one"),
            ("text", "not_contains", "%_\\"),
            ("number", "not_in", [0, 1]),
            ("boolean", "not_equals", True),
        ):
            with self.subTest(kind=kind, op=op):
                having, _ = reference.membership_having([leaf(kind, op, value)])
                self.assertTrue(having.startswith("countIf(mapContains("))
                self.assertTrue(having.endswith(") > 0"))

    def test_range_bounds_are_inclusive_and_not_silently_reordered(self):
        having, params = reference.membership_having(
            [leaf("number", "between", [4, 1])]
        )
        self.assertIn("BETWEEN %(ref_value_0_low)s AND %(ref_value_0_high)s", having)
        self.assertEqual(params["ref_value_0_low"], 4)
        self.assertEqual(params["ref_value_0_high"], 1)

    def test_text_prefix_and_suffix_are_literal_case_insensitive_functions(self):
        for op, fn in (("starts_with", "startsWith"), ("ends_with", "endsWith")):
            having, params = reference.membership_having([leaf("text", op, "%_\\")])
            self.assertIn(fn + "(lowerUTF8(", having)
            self.assertNotIn(" LIKE ", having)
            self.assertEqual(params["ref_value_0"], "%_\\")

    def test_root_population_is_independent_and_child_history_is_not_date_clipped(self):
        class Reader:
            def __init__(self):
                self.calls = []

            def execute_ch_query(self, sql, params, settings):
                self.calls.append((sql, dict(params), settings))
                if len(self.calls) == 1:
                    return SimpleNamespace(
                        data=[{"trace_id": "new"}, {"trace_id": "older"}]
                    )
                if len(self.calls) == 2:
                    return SimpleNamespace(
                        data=[
                            {
                                "observation_type": "SPAN",
                                "service_name": "service",
                                "span_hour": datetime(2025, 1, 1),
                                "trace_id": "older",
                            },
                            {
                                "observation_type": "TRACE",
                                "service_name": "service",
                                "span_hour": datetime(2026, 8, 1),
                                "trace_id": "new",
                            },
                        ]
                    )
                return SimpleNamespace(data=[{"trace_id": "older"}])

        reader = Reader()
        case = {
            "surface": "traces",
            "window": {"start": "2026-08-31T00:00:00Z", "end": "2026-09-01T00:00:00Z"},
            "request": {"target_rows": 25},
        }
        ids, info = reference.reference_ids(
            reader, case, {"project_id": "project"}, [leaf()]
        )
        self.assertEqual(ids, ["older"])
        self.assertTrue(info["population_exhausted"])
        self.assertNotIn("ref_trace_ids", reader.calls[0][1])
        self.assertEqual(reader.calls[1][1]["ref_trace_ids"], ("new", "older"))
        for sql, params, settings in (reader.calls[0], reader.calls[2]):
            self.assertIn("spans FINAL", sql)
            self.assertIn("project_id=%(project_id)s", sql)
            self.assertEqual(settings["optimize_move_to_prewhere_if_final"], 0)
        discovery = reader.calls[1][0]
        self.assertNotIn("FINAL", discovery)
        self.assertNotIn("is_deleted", discovery)
        self.assertNotIn("ref_start", discovery)
        self.assertNotIn("ref_end", discovery)
        self.assertNotIn("LIMIT", discovery)
        self.assertNotIn("attrs_", discovery)
        exact_sql, exact_params, _ = reader.calls[2]
        self.assertNotIn("ref_start", exact_sql)
        self.assertNotIn("ref_end", exact_sql)
        self.assertEqual(exact_params["ref_span_prefixes"][0][2], datetime(2025, 1, 1))
        self.assertIn(
            "observation_type, service_name, toStartOfHour(start_time), trace_id",
            exact_sql,
        )

    def test_missing_or_foreign_identity_discovery_cannot_prove_empty(self):
        for prefixes in ([], [{"trace_id": "foreign"}]):

            class Reader:
                calls = 0

                def execute_ch_query(self, sql, params, settings):
                    self.calls += 1
                    return SimpleNamespace(
                        data=[{"trace_id": "root"}] if self.calls == 1 else prefixes
                    )

            with (
                self.subTest(prefixes=prefixes),
                self.assertRaisesRegex(
                    replay.ReplayError, "REFERENCE_IDENTITY_DISCOVERY_INCONSISTENT"
                ),
            ):
                reference.reference_ids(
                    Reader(),
                    {
                        "surface": "traces",
                        "window": {
                            "start": "2026-08-31T00:00:00Z",
                            "end": "2026-09-01T00:00:00Z",
                        },
                        "request": {"target_rows": 25},
                    },
                    {"project_id": "project"},
                    [leaf()],
                )

    def test_root_prefix_preserves_empty_intervals_order_and_duplicate_exclusion(self):
        class Reader:
            def __init__(self):
                self.calls = []

            def execute_ch_query(self, sql, params, settings):
                self.calls.append((sql, dict(params)))
                rows = [[], [{"trace_id": "newer"}], [{"trace_id": "older"}]][
                    len(self.calls) - 1
                ]
                return SimpleNamespace(data=rows)

        reader = Reader()
        params = {
            "project_id": "project",
            "ref_start": replay.utc("2026-08-01T00:00:00Z"),
            "ref_end": replay.utc("2026-09-01T00:00:00Z"),
            "ref_root_limit": 2,
        }
        roots = reference.root_prefix(reader, params, {})
        self.assertEqual([r["trace_id"] for r in roots], ["newer", "older"])
        self.assertEqual(reader.calls[0][1]["ref_scan_end"], params["ref_end"])
        for index in (1, 2):
            self.assertEqual(
                reader.calls[index - 1][1]["ref_scan_start"],
                reader.calls[index][1]["ref_scan_end"],
            )
        self.assertIn("NOT IN %(ref_seen_ids)s", reader.calls[2][0])
        self.assertEqual(reader.calls[2][1]["ref_seen_ids"], ("newer",))
        self.assertEqual(reader.calls[2][1]["ref_remaining"], 1)


class LongTextReferenceTests(unittest.TestCase):
    needle = "Metadata literal_%\\ KÉ " + "x" * 140 + " 1000000001"
    case = {
        "surface": "traces",
        "window": {
            "start": "2026-08-31T00:00:00.123456Z",
            "end": "2026-09-07T00:00:00.654321Z",
        },
        "request": {"target_rows": 25},
    }

    class Reader:
        def __init__(self, responses):
            self.calls = []
            self.responses = iter(responses)

        def execute_ch_query(self, sql, params, settings):
            self.calls.append((sql, dict(params), dict(settings)))
            response = next(self.responses)
            if isinstance(response, Exception):
                raise response
            return SimpleNamespace(data=response)

    def run_reference(self, responses, item=None):
        reader = self.Reader(responses)
        result = reference.reference_ids(
            reader,
            self.case,
            {"project_id": "project"},
            [item or leaf("text", "contains", self.needle)],
        )
        return reader, result

    def test_complete_empty_text_witness_proves_only_complete_root_population_absence(
        self,
    ):
        reader, (ids, evidence) = self.run_reference([[]])
        self.assertEqual(ids, [])
        self.assertTrue(evidence["population_exhausted"])
        self.assertEqual(evidence["raw_witness_population"], 0)
        self.assertEqual(
            evidence["absence_proof"],
            "zero_raw_text_witnesses_in_complete_root_population",
        )
        sql, params, settings = reader.calls[0]
        self.assertEqual(sql.count("SELECT"), 2)
        self.assertEqual(sql.count("project_id=%(project_id)s"), 2)
        self.assertEqual(sql.count("start_time>="), 1)
        self.assertEqual(sql.count("start_time<"), 1)
        self.assertIn("trace_id IN (", sql)
        self.assertIn("WHERE parent_span_id IS NULL OR parent_span_id=''", sql)
        for forbidden in (
            "LIMIT",
            "FINAL",
            "is_deleted",
            "SAMPLE",
            "ref_trace_ids",
            "candidate",
        ):
            self.assertNotIn(forbidden, sql)
        self.assertNotIn("ref_trace_ids", params)
        self.assertNotIn(self.needle, sql)
        self.assertEqual(params["ref_text_value"], self.needle)
        self.assertEqual(params["ref_text_digit_pattern_0"], "%1000000001%")
        self.assertEqual(params["ref_start_us"] % 1000000, 123456)
        self.assertEqual(params["ref_end_us"] % 1000000, 654321)
        self.assertEqual(settings["max_execution_time"], 2)
        self.assertEqual(settings["max_bytes_to_read"], 1024**3)
        self.assertEqual(settings["max_result_rows"], 1000)
        self.assertEqual(settings["max_rows_in_distinct"], 1000)
        for name in ("read", "result", "distinct", "timeout"):
            self.assertEqual(settings[f"{name}_overflow_mode"], "throw")

    def test_all_supported_operations_use_exact_key_value_test_in_addition_to_anchor(
        self,
    ):
        second = "another " + "y" * 140 + " 12345678"
        for op, value in (
            ("equals", self.needle),
            ("contains", self.needle),
            ("in", [self.needle, second]),
        ):
            with self.subTest(op=op):
                reader, (_, evidence) = self.run_reference(
                    [[]], leaf("string", op, value)
                )
                sql, params, _ = reader.calls[0]
                self.assertIn("AND mapContains(attrs_string, %(ref_text_key)s)", sql)
                self.assertIn(
                    "arrayStringConcat(arrayMap(x -> lower(x), mapValues(attrs_string))) LIKE",
                    sql,
                )
                self.assertIn("lowerUTF8(attrs_string[%(ref_text_key)s])", sql)
                if op == "in":
                    self.assertIn(
                        " OR ",
                        sql.split("WHERE (", 1)[1].split("AND mapContains", 1)[0],
                    )
                    self.assertIn(
                        "has(arrayMap(x -> lowerUTF8(x), %(ref_text_values)s)", sql
                    )
                    self.assertEqual(params["ref_text_values"], value)
                    self.assertEqual(params["ref_text_digit_pattern_1"], "%12345678%")
                    self.assertEqual(evidence["digit_anchor_count"], 2)
                elif op == "contains":
                    self.assertIn("positionUTF8(", sql)
                    self.assertIn("lowerUTF8(%(ref_text_value)s)) > 0", sql)
                else:
                    self.assertIn("= lowerUTF8(%(ref_text_value)s)", sql)
                self.assertNotIn("attributes_extra", sql)
                self.assertNotIn("attrs_number", sql)

    def test_text_replay_discovers_all_history_then_checks_latest_root_and_order(self):
        witnesses = [
            "old-value",
            "tombstone",
            "reparented-root",
            "timestamp-corrected",
            "z-live",
            "a-live",
        ]
        prefixes = [
            {
                "observation_type": "SPAN",
                "service_name": "service-a",
                "span_hour": datetime(2025, 1, 1),
                "trace_id": trace_id,
            }
            for trace_id in witnesses
        ] + [
            {
                "observation_type": "TRACE",
                "service_name": "service-b",
                "span_hour": datetime(2026, 9, 6),
                "trace_id": "z-live",
            },
            {
                "observation_type": "SPAN",
                "service_name": "service-c",
                "span_hour": datetime(2026, 9, 8),
                "trace_id": "a-live",
            },
        ]
        reader, (ids, evidence) = self.run_reference(
            [
                [{"trace_id": value} for value in witnesses],
                prefixes,
                [{"trace_id": "z-live"}, {"trace_id": "a-live"}],
            ]
        )
        self.assertEqual(ids, ["z-live", "a-live"])
        self.assertEqual(evidence["raw_witness_population"], 6)
        self.assertEqual(
            evidence["reference_route"], "complete_root_scoped_text_witness_then_FINAL"
        )
        self.assertEqual(reader.calls[1][1]["ref_trace_ids"], tuple(witnesses))
        discovery_sql = reader.calls[1][0]
        for forbidden in (
            "ref_start",
            "ref_end",
            "LIMIT",
            "FINAL",
            "is_deleted",
            "attrs_string",
        ):
            self.assertNotIn(forbidden, discovery_sql)
        final_sql, params, settings = reader.calls[2]
        self.assertEqual(len(params["ref_span_prefixes"]), len(prefixes))
        self.assertIn("FROM spans FINAL", final_sql)
        prewhere = final_sql.split("PREWHERE", 1)[1].split("WHERE is_deleted", 1)[0]
        self.assertIn(
            "observation_type, service_name, toStartOfHour(start_time), trace_id",
            prewhere,
        )
        for forbidden in ("ref_start", "ref_end", "parent_span_id", "attrs_string"):
            self.assertNotIn(forbidden, prewhere)
        self.assertIn("WHERE is_deleted=0", final_sql)
        self.assertIn(
            "HAVING countIf((parent_span_id IS NULL OR parent_span_id='')", final_sql
        )
        self.assertIn("countIf(mapContains(attrs_string", final_sql)
        self.assertIn("ORDER BY root_order_time DESC, trace_id DESC", final_sql)
        self.assertEqual(final_sql.count("LIMIT"), 1)
        self.assertIn("LIMIT %(ref_target_rows)s", final_sql)
        self.assertNotIn("digit_pattern", final_sql)
        self.assertEqual(settings["use_skip_indexes_if_final"], 0)
        self.assertEqual(settings["optimize_move_to_prewhere"], 0)
        self.assertEqual(settings["optimize_move_to_prewhere_if_final"], 0)
        self.assertEqual(
            settings["enable_optimize_predicate_expression_to_final_subquery"], 0
        )
        self.assertEqual(settings["query_plan_merge_expressions"], 0)

    def test_digit_anchors_are_necessary_under_unicode_case_and_literal_wildcard_fixtures(
        self,
    ):
        # Test the independent implication, not an imported application anchor.
        fixtures = [
            ("Prefix KELVIN 123456 _%\\ suffix", "prefix kelvin 123456 _%\\ SUFFIX"),
            ("ÉCOLE 987654", "école 987654"),
            ("ID_%\\_00001234", "id_%\\_00001234"),
            ("İstanbul 456789", "İSTANBUL 456789"),
        ]
        for needle, stored in fixtures:
            with self.subTest(needle=needle):
                self.assertEqual(needle.lower(), stored.lower())
                (pattern,) = reference.text_digit_ngram_patterns([needle])
                digit_run = pattern[1:-1]
                self.assertTrue(digit_run.isascii() and digit_run.isdigit())
                self.assertGreaterEqual(len(digit_run), 4)
                ascii_lower_stored = stored.translate(
                    str.maketrans(
                        "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"
                    )
                )
                self.assertIn(digit_run, ascii_lower_stored)
        self.assertEqual(reference.literal_like_pattern("12%_\\34"), r"%12\%\_\\34%")
        self.assertEqual(
            reference.text_digit_ngram_patterns(["0000", "0000"]), ("%0000%",)
        )

    def test_wildcards_letters_and_non_ascii_digits_are_not_safe_digit_anchors(self):
        for needles in (
            [],
            ["KELVIN ASCIIletters"],
            ["١٢٣٤٥٦"],
            ["１２３４５６"],
            ["123%456_789\\012"],
            ["1234", "no safe anchor"],
        ):
            with self.subTest(needles=needles):
                self.assertIsNone(reference.text_digit_ngram_patterns(needles))

    def test_no_safe_anchor_or_ineligible_shape_never_runs_complete_probe(self):
        for items in (
            [leaf("text", "contains", "x" * 150)],
            [leaf("text", "in", [self.needle, "x" * 150])],
            [leaf("text", "equals", "1234")],
            [leaf("text", "contains", "")],
            [leaf("text", "in", [])],
            [leaf("text", "in", [self.needle, ""])],
            [leaf("text", "not_contains", self.needle)],
            [leaf("text", "is_null", self.needle)],
            [leaf("number", "equals", 0)],
            [leaf("map", "contains", {"key": self.needle})],
            [leaf("text", "contains", self.needle)] * 2,
            [
                {
                    "column_id": "metadata",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "contains",
                        "filter_value": self.needle,
                    },
                }
            ],
        ):
            reader = Mock()
            with self.subTest(items=items):
                self.assertIsNone(
                    reference.complete_text_witness_reference(
                        reader, items, {}, "1", {}
                    )
                )
                reader.execute_ch_query.assert_not_called()

    def test_long_threshold_uses_utf8_bytes_without_truncation(self):
        self.assertIsNone(
            reference.positive_long_text_leaf(
                [leaf("text", "equals", "x" * 124 + "1234")]
            )
        )
        self.assertIsNotNone(
            reference.positive_long_text_leaf(
                [leaf("text", "equals", "é" * 63 + "1234")]
            )
        )
        reader, _ = self.run_reference(
            [[]], leaf("text", "contains", "x" * 600 + "1234")
        )
        self.assertEqual(reader.calls[0][1]["ref_text_value"], "x" * 600 + "1234")

    def test_invalid_storage_provenance_fails_before_any_probe_or_prefix(self):
        for key, values in (
            ("attribute_value_types", ["number"]),
            ("attributeValueTypes", ["json"]),
        ):
            item = leaf("text", "in", [self.needle])
            item["filter_config"][key] = values
            reader = Mock()
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(
                    replay.ReplayError,
                    "REFERENCE_(VALUE_TYPE_MISMATCH|STORAGE_PROVENANCE_TYPE)",
                ),
            ):
                reference.reference_ids(
                    reader, self.case, {"project_id": "project"}, [item]
                )
            reader.execute_ch_query.assert_not_called()

    def test_valid_typed_long_text_uses_final_prefix_not_string_only_shortcut(self):
        for types, values in (
            (["string"], [self.needle]),
            (["string", "number"], [self.needle, 12]),
        ):
            reader = Mock()
            item = typed_leaf(values, types)
            with self.subTest(types=types):
                self.assertIsNone(
                    reference.complete_text_witness_reference(
                        reader, [item], {}, "1", {}
                    )
                )
                reader.execute_ch_query.assert_not_called()

    def test_probe_budget_failures_remain_inconclusive(self):
        from clickhouse_driver.errors import ErrorCodes, ServerException

        for code in (
            ErrorCodes.TIMEOUT_EXCEEDED,
            ErrorCodes.TOO_MANY_ROWS,
            ErrorCodes.TOO_MANY_BYTES,
            ErrorCodes.TOO_MANY_ROWS_OR_BYTES,
            ErrorCodes.MEMORY_LIMIT_EXCEEDED,
            ErrorCodes.SET_SIZE_LIMIT_EXCEEDED,
            ErrorCodes.LIMIT_EXCEEDED,
        ):
            reader = self.Reader([ServerException("budget", code=code)])
            with self.subTest(code=code):
                result = reference.complete_text_witness_reference(
                    reader,
                    [leaf("text", "contains", self.needle)],
                    {},
                    "1",
                    {},
                )
                self.assertIsNone(result)
                self.assertEqual(len(reader.calls), 1)

    def test_failed_probe_preserves_insufficient_prefix_not_empty_result(self):
        from clickhouse_driver.errors import ErrorCodes, ServerException

        reader = self.Reader(
            [ServerException("budget", code=ErrorCodes.TIMEOUT_EXCEEDED)] + [[]] * 5
        )
        roots = [{"trace_id": f"root-{index}"} for index in range(250)]
        with (
            patch.object(reference, "root_prefix", return_value=roots) as prefix,
            patch.object(reference, "span_coordinates", return_value=()) as coordinates,
            self.assertRaisesRegex(
                replay.ReplayError, "REFERENCE_PREFIX_INSUFFICIENT_NOT_EMPTY"
            ),
        ):
            reference.reference_ids(
                reader,
                self.case,
                {"project_id": "project"},
                [leaf("text", "contains", self.needle)],
            )
        prefix.assert_called_once()
        self.assertEqual(coordinates.call_count, 5)
        self.assertEqual(len(reader.calls), 6)

    def test_programming_errors_and_invalid_populations_are_not_absence(self):
        from clickhouse_driver.errors import ErrorCodes, ServerException

        for failure in (
            ValueError("broken"),
            RuntimeError("broken"),
            ServerException("syntax", code=ErrorCodes.SYNTAX_ERROR),
        ):
            with (
                self.subTest(failure=type(failure).__name__),
                self.assertRaises(type(failure)),
            ):
                self.run_reference([failure])
        for rows in (
            None,
            [{"trace_id": "duplicate"}] * 2,
            [{"trace_id": str(i)} for i in range(1001)],
        ):
            with (
                self.subTest(size=len(rows) if rows else None),
                self.assertRaises(replay.ReplayError),
            ):
                self.run_reference([rows])


class ScalarSpanRendererTests(unittest.TestCase):
    def test_ordinary_leaf_preserves_default_trace_sql_and_binds(self):
        item = leaf("number", "not_between", [4, 1])
        core = (
            "mapContains(attrs_number, %(ref_key_0)s) AND "
            "(NOT (attrs_number[%(ref_key_0)s] BETWEEN "
            "%(ref_value_0_low)s AND %(ref_value_0_high)s))"
        )
        expected_params = {
            "ref_key_0": "tenant.identifier",
            "ref_value_0_low": 4,
            "ref_value_0_high": 1,
        }
        for mode in ({}, {"scalar_span": False}):
            self.assertEqual(
                reference.membership_having([item], **mode),
                (f"countIf({core}) > 0", expected_params),
            )
        self.assertEqual(
            reference.membership_having([item], scalar_span=True),
            (f"({core})", expected_params),
        )

    def test_null_and_presence_render_at_requested_grain(self):
        for kind, column in (
            ("text", "attrs_string"),
            ("number", "attrs_number"),
            ("boolean", "attrs_bool"),
        ):
            for op in ("is_null", "is_not_null"):
                with self.subTest(kind=kind, op=op):
                    item = leaf(kind, op)
                    presence = f"mapContains({column}, %(ref_key_0)s)"
                    expected = {"ref_key_0": "tenant.identifier"}
                    self.assertEqual(
                        reference.membership_having([item]),
                        (
                            f"countIf({presence}) {'= 0' if op == 'is_null' else '> 0'}",
                            expected,
                        ),
                    )
                    self.assertEqual(
                        reference.membership_having([item], scalar_span=True),
                        (
                            (
                                f"NOT ({presence})"
                                if op == "is_null"
                                else f"({presence})"
                            ),
                            expected,
                        ),
                    )

    def test_typed_negative_keeps_one_span_presence_and_all_positive_exclusions(self):
        item = typed_leaf([0, False], ["number", "boolean"], op="not_in")
        core = (
            "(mapContains(attrs_number, %(ref_key_0)s) OR mapContains(attrs_bool, %(ref_key_0)s)) AND NOT "
            "((mapContains(attrs_number, %(ref_key_0)s) AND has(%(ref_value_0_number)s, attrs_number[%(ref_key_0)s])) OR "
            "(mapContains(attrs_bool, %(ref_key_0)s) AND has(%(ref_value_0_boolean)s, attrs_bool[%(ref_key_0)s])))"
        )
        expected = {
            "ref_key_0": "tenant.identifier",
            "ref_value_0_number": [0],
            "ref_value_0_boolean": [False],
        }
        self.assertEqual(
            reference.membership_having([item]), (f"countIf({core}) > 0", expected)
        )
        self.assertEqual(
            reference.membership_having([item], scalar_span=True),
            (f"({core})", expected),
        )

    def test_conjunction_is_row_local_only_when_explicitly_requested(self):
        items = [leaf("number", "equals", 1), leaf("boolean", "is_null")]
        ordinary = "mapContains(attrs_number, %(ref_key_0)s) AND (attrs_number[%(ref_key_0)s] = %(ref_value_0)s)"
        missing = "mapContains(attrs_bool, %(ref_key_1)s)"
        trace_sql, trace_params = reference.membership_having(items)
        span_sql, span_params = reference.membership_having(items, scalar_span=True)
        self.assertEqual(
            trace_sql, f"countIf({ordinary}) > 0 AND countIf({missing}) = 0"
        )
        self.assertEqual(span_sql, f"({ordinary}) AND NOT ({missing})")
        self.assertEqual(trace_params, span_params)


@pytest.fixture
def numeric_reference_native(tmp_path, record_property):
    # Reuse the independent QA RMT/Reader, not application SQL or candidate IDs.
    from clickhouse_driver.util.escape import escape_params
    from test_observe_session_reference import engine as existing

    for engine in existing.__wrapped__(tmp_path, record_property):
        execute = engine.execute

        def quoted_execute(sql, params=None, settings=None):
            if settings:
                literals = escape_params(settings, engine.context)
                sql += " SETTINGS " + ", ".join(
                    f"{name}={value}" for name, value in literals.items()
                )
            return execute(sql, params)

        engine.execute = quoted_execute
        yield engine


def _mixed_numeric_native_inputs(width):
    def item(key, kind, op, value):
        return {**leaf(kind, op, value), "column_id": key}

    picker = {
        **typed_leaf(["0012", 12, False], ["string", "number", "boolean"]),
        "column_id": "picker",
    }
    negative_picker = {
        **typed_leaf(
            ["blocked", 0, False], ["string", "number", "boolean"], op="not_in"
        ),
        "column_id": "negative-picker",
    }
    return [
        (
            item("negative", "text", "not_equals", "blocked"),
            {"attrs_string": {"negative": "allowed"}},
        ),
        (item("anchor", "number", "greater_than", 3), {"attrs_number": {"anchor": 7}}),
        (
            item("boolean", "boolean", "equals", False),
            {"attrs_bool": {"boolean": 0}},
        ),
        (picker, {"attrs_string": {"picker": "0012"}}),
        (
            item("literal", "text", "equals", "KEEP"),
            {"attrs_string": {"literal": "keep"}},
        ),
        (
            item("negative-number", "number", "not_in", [0, 2]),
            {"attrs_number": {"negative-number": 3}},
        ),
        (item("missing", "text", "is_null", None), {}),
        (
            negative_picker,
            {
                "attrs_string": {"negative-picker": "safe"},
                "attrs_number": {"negative-picker": 7},
                "attrs_bool": {"negative-picker": 1},
            },
        ),
        (
            item("threshold", "number", "greater_than_or_equal", 4),
            {"attrs_number": {"threshold": 4}},
        ),
        (
            item("prefix", "text", "starts_with", "pre"),
            {"attrs_string": {"prefix": "prefix"}},
        ),
    ][:width]


@pytest.mark.parametrize("value", [False, True])
def test_native_boolean_absence_covers_old_children_and_missing_keys(
    numeric_reference_native, value
):
    from test_observe_session_reference import OTHER_PROJECT, PROJECT, START

    reader = numeric_reference_native
    key = "tenant.identifier"
    filters = [leaf("boolean", "equals", value)]
    params = {"project_id": PROJECT}
    reader.insert(1)  # Missing is not the Boolean Map's default False value.
    reader.insert(2, attrs_number={key: int(value)}, attrs_string={key: str(value)})
    reader.insert(3, project_id=OTHER_PROJECT, attrs_bool={key: int(value)})
    assert reference.boolean_absence_proof(reader, filters, params)
    # Any raw witness makes this proof inconclusive, even an old, deleted child
    # well outside the request time. Latest-state replay must decide that case.
    fields = {
        "start_time": START - timedelta(days=400),
        "parent_span_id": "root",
        "attrs_bool": {key: int(value)},
        "is_deleted": 1,
    }
    reader.insert(4, **fields)
    reader.insert(4, **{**fields, "attrs_bool": {}, "_version": 2})
    assert not reference.boolean_absence_proof(reader, filters, params)
    case = {
        "surface": "traces",
        "window": {
            "start": START.isoformat(),
            "end": (START + timedelta(days=7)).isoformat(),
        },
        "request": {"target_rows": 25},
    }
    for positive in (False, True):
        if positive:
            reader.insert(5, attrs_bool={key: int(value)})
        ids, evidence = reference.reference_ids(reader, case, params, filters)
        assert ids == (["trace-5"] if positive else [])
        assert (
            evidence["reference_route"] == "complete_global_boolean_witness_then_FINAL"
        )
        assert evidence["population_exhausted"]
        assert evidence["raw_witness_population"] == (2 if positive else 1)


@pytest.mark.parametrize(
    "width,surface", [(2, "traces"), (5, "task_traces"), (10, "eval_traces")]
)
def test_native_mixed_numeric_reference(
    numeric_reference_native, width, surface, record_property
):
    from test_observe_session_reference import OTHER_PROJECT, PROJECT, START

    engine = numeric_reference_native
    inputs = _mixed_numeric_native_inputs(width)
    end = START + timedelta(minutes=20)
    variants = [
        "positive-a",
        "positive-b",
        "bad-negative",
        "missing-negative",
        "stale-numeric",
        "deleted-root",
        "before-window",
        "after-window",
        "parent-changed",
    ]
    if width >= 5:
        variants.append("typed-wrong")
    if width == 10:
        variants.extend(["typed-negative-conflict", "absence-violated"])
    for index, trace in enumerate(variants, 100):
        root = {
            "trace_id": trace,
            "id": "root",
            "start_time": START + timedelta(minutes=10),
        }
        engine.insert(index, **root)
        for child, (_, maps) in enumerate(inputs):
            # Distinct children outside BOTH ends of the root-only window.
            timestamp = START + timedelta(days=-400 if child % 2 else 2)
            fields = {
                "trace_id": trace,
                "id": f"child-{child}",
                "parent_span_id": "root",
                "start_time": timestamp,
                **maps,
            }
            if child == 0 and trace == "bad-negative":
                fields["attrs_string"] = {"negative": "blocked"}
            if child == 0 and trace == "missing-negative":
                fields["attrs_string"] = {}
            if child == 3 and trace == "positive-b":
                fields.update(attrs_string={}, attrs_bool={"picker": 0})
            if child == 3 and trace == "typed-wrong":
                # None matches its declared storage branch; casting would lie.
                fields.update(
                    attrs_string={"picker": "12"},
                    attrs_number={"picker": 0},
                    attrs_bool={"picker": 1},
                )
            if child == 7 and trace == "typed-negative-conflict":
                fields["attrs_bool"] = {"negative-picker": 0}
            if child == 6 and trace == "absence-violated":
                fields["attrs_string"] = {"missing": "present-outside-root-window"}
            engine.insert(index, **fields)
            if child == 1 and trace == "stale-numeric":
                engine.insert(
                    index, **{**fields, "_version": 2, "attrs_number": {"anchor": 0}}
                )
            if child == 0 and trace == "positive-a":
                # A latest differing span is still required for a negative leaf.
                engine.insert(
                    index,
                    **{
                        **fields,
                        "id": "equal-sibling",
                        "attrs_string": {"negative": "blocked"},
                    },
                )
        replacement = {
            "positive-a": {"start_time": START + timedelta(minutes=5)},
            "deleted-root": {"is_deleted": 1},
            "before-window": {"start_time": START - timedelta(microseconds=1)},
            "after-window": {"start_time": end},
            "parent-changed": {"parent_span_id": "other-root"},
        }.get(trace)
        if replacement:
            engine.insert(index, **{**root, "_version": 2, **replacement})
    engine.insert(
        100,
        project_id=OTHER_PROJECT,
        trace_id="positive-a",
        id="root",
        _version=99,
        is_deleted=1,
    )

    engine.calls.clear()
    filters = [item for item, _ in inputs]
    ids, evidence = reference.reference_ids(
        engine,
        {
            "surface": surface,
            "window": {"start": START.isoformat(), "end": end.isoformat()},
            "request": {"target_rows": 25},
        },
        {"project_id": PROJECT},
        filters,
    )
    # Literal independent fixture expectations, not application/candidate output.
    assert ids == ["positive-b", "positive-a"]
    assert evidence["reference_route"] == "complete_global_numeric_witness_then_FINAL"
    assert evidence["population_exhausted"] is True
    assert evidence["raw_witness_population"] == len(variants) > len(ids)
    assert "absence_proof" not in evidence
    assert len(engine.calls) == 3
    assert engine.calls[0][1]["ref_numeric_key"] == "anchor"
    sql, params, _ = engine.calls[-1]
    for index, (item, _) in enumerate(inputs):
        assert params[f"ref_key_{index}"] == item["column_id"]
        assert f"%(ref_key_{index})s" in sql
    assert "FROM spans FINAL" in sql and "WHERE is_deleted=0" in sql
    record_property("independent_positive_rows", len(ids))
    record_property("raw_witness_counterexamples", len(variants) - len(ids))


if __name__ == "__main__":
    unittest.main()
