"""Offline QA harness tests: compiler/transport contracts, not CH engine proof."""

import json
import re
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import probe_long_text_index_contract_readonly as probe


class CompilerFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        probe.initialize()
        from clickhouse_driver import Client

        cls.client = Client("clickhouse.invalid")  # No connection or execute.
        cls.client.connection.context.server_info = SimpleNamespace(
            get_timezone=lambda: "UTC"
        )

    def test_all_actual_compiler_cases_render_without_server_relations(self):
        seen = set()
        for variant in probe.NEEDLES:
            for operation in probe.OPERATIONS:
                with self.subTest(variant=variant, operation=operation):
                    case = probe.build_case(variant, operation)
                    seen.add(case.name)
                    probe.enforce_literal_sources(case.sql)
                    rendered = self.client.substitute_params(
                        case.sql, case.params, self.client.connection.context
                    )
                    self.assertLess(len(rendered.encode()), 256 * 1024)
                    self.assertIn(
                        "GROUP BY observation_type, service_name, toStartOfHour(start_time), trace_id, id",
                        case.sql,
                    )
                    self.assertIn("WHERE latest_is_deleted = 0", case.sql)
                    # The CH25 classifier packs each field into one coherent
                    # physical winner instead of mixing independently tied
                    # argMax values. The typed leaf still feeds that winner.
                    self.assertRegex(
                        case.sql,
                        r"(?s)argMax\(tuple\(.*attrs_string\[.*\), _version\) AS _physical_winner",
                    )
                    self.assertRegex(
                        case.sql, r"_physical_winner\.\d+ AS latest_attr_value_0"
                    )
                    self.assertIn("matching_scalar_trace_identities", case.sql)
                    self.assertNotIn("SETTINGS", case.sql)
                    self.assertNotIn("PREWHERE", case.sql)
                    # Hints are actually evaluated in the implication arm.
                    arm = case.sql.split("raw_witnesses AS (", 1)[1].split(
                        "SELECT 'exact'", 1
                    )[0]
                    self.assertNotIn("indexHint(", arm)
                    self.assertIn(
                        "arrayStringConcat(arrayMap(x -> lower(x), mapValues(attrs_string))) LIKE",
                        arm,
                    )
                    self.assertEqual(
                        "second_in_operand" in case.expected, operation == "in"
                    )
        self.assertEqual(len(seen), probe.EXPECTED_CASE_COUNT)

    def test_structural_fixtures_have_all_six_key_and_late_child_cases(self):
        case = probe.build_case("literal_like", "contains")
        rows = case.params["fixture_rows"]
        self.assertIn("late_child", case.expected)
        self.assertIn("early_child", case.expected)
        for name in ("service_identity", "kind_identity", "hour_identity"):
            self.assertIn(name, case.expected)
        self.assertTrue(any(row[0] == probe.FOREIGN for row in rows))
        for name in ("stale", "tombstone", "missing", "removed_key", "outside_root"):
            self.assertNotIn(name, case.expected)
        self.assertIn("CAST(NULL AS Nullable(UUID)) AS project_version_id", case.sql)
        for op in ("equals", "in"):
            self.assertTrue(probe.build_case("dotted_i", op).expected)

    def test_lowering_all_actual_queries_changes_only_clauses_and_grouping(self):
        # Compare the full generated SQL, not selected snippets: no predicate,
        # key, date, tombstone, ORDER BY, LIMIT or parameter can disappear.
        for variant in probe.NEEDLES:
            for operation in probe.OPERATIONS:
                with self.subTest(variant=variant, operation=operation):
                    with patch.object(
                        probe, "values_where", side_effect=lambda sql: sql
                    ):
                        original = probe.build_case(variant, operation)
                    lowered = probe.build_case(variant, operation)
                    self.assertIn("PREWHERE", original.sql)
                    self.assertEqual(original.params, lowered.params)

                    def predicate_tokens(sql):
                        return re.findall(
                            r"%\(\w+\)s|[^\s()]+",
                            re.sub(r"\b(?:PREWHERE|WHERE|AND)\b", "", sql),
                        )

                    self.assertEqual(
                        predicate_tokens(original.sql), predicate_tokens(lowered.sql)
                    )
                    self.assertEqual(probe.values_where(lowered.sql), lowered.sql)

    def test_sources_fail_closed_when_compiler_adds_database_read(self):
        for sql in (
            "SELECT * FROM spans",
            "SELECT * FROM system.tables",
            "WITH fixture_spans AS (SELECT * FROM spans) SELECT * FROM fixture_spans",
            "SELECT * FROM remote('server', 'table')",
            "DROP TABLE spans",
        ):
            with self.subTest(sql=sql), self.assertRaises((ValueError, RuntimeError)):
                probe.enforce_literal_sources(sql)

    def test_assessment_rejects_each_kind_of_false_negative_and_vacuous_success(self):
        case = probe.build_case("ascii_quotes", "equals")
        perfect = [
            (kind, trace)
            for kind in ("exact", "witness", "seed", "hint")
            for trace in case.expected
        ]
        self.assertTrue(probe.assess(case, perfect)["passed"])
        for omitted in ("witness", "seed", "hint"):
            reduced = [row for row in perfect if row != (omitted, "late_child")]
            result = probe.assess(case, reduced)
            self.assertFalse(result["passed"])
            self.assertEqual(
                result["missing_necessary_witnesses"][omitted], ["late_child"]
            )
        self.assertFalse(probe.assess(case, [])["passed"])
        self.assertFalse(probe.assess(case, perfect + [("exact", "stale")])["passed"])
        with self.assertRaises(ValueError):
            probe.assess(case, [("exact", "private customer value")])

    def test_unicode_variant_is_observed_not_python_casefold_oracle(self):
        case = probe.build_case("dotted_i", "in")
        base = [
            (kind, trace)
            for kind in ("exact", "witness", "seed", "hint")
            for trace in case.expected
        ]
        self.assertTrue(probe.assess(case, base)["passed"])
        observed = base + [
            (kind, "unicode_variant") for kind in ("exact", "witness", "seed", "hint")
        ]
        self.assertTrue(probe.assess(case, observed)["passed"])
        self.assertFalse(
            probe.assess(case, base + [("exact", "unicode_variant")])["passed"]
        )

    def test_known_explain_is_compiler_generated_not_user_sql(self):
        plan = known_plan()
        sql, params = probe.build_known_explain(plan)
        self.assertTrue(sql.startswith("EXPLAIN indexes = 1, json = 1 "))
        self.assertIn("indexHint(arrayStringConcat", sql)
        self.assertIn("matching_scalar_trace_identities", sql)
        self.assertNotIn(probe.NEEDLES["literal_like"], sql)
        self.assertIn(probe.NEEDLES["literal_like"], params.values())
        self.assertNotIn("SETTINGS", sql)
        self.assertIn("PREWHERE", sql)  # Never lower real-table EXPLAIN.
        with self.assertRaises(ValueError):
            probe.build_known_explain({"cases": []})
        plan["cases"][0]["request"]["path"] = "/unrelated"
        with self.assertRaises(ValueError):
            probe.build_known_explain(plan)

    def test_run_uses_one_second_fixtures_and_single_bounded_explain(self):
        case = probe.build_case("literal_like", "contains")
        valid = [
            (kind, trace)
            for kind in ("exact", "witness", "seed", "hint")
            for trace in case.expected
        ]
        client = Mock()
        client.execute.side_effect = (
            [[("expected", "default", "25.test")]]
            + [valid] * probe.EXPECTED_CASE_COUNT
            + [[(json.dumps(index_plan()),)]]
        )
        with (
            patch.object(probe, "build_case", return_value=case),
            patch.object(
                probe,
                "build_known_explain",
                return_value=("EXPLAIN indexes = 1 SELECT 1", {}),
            ),
        ):
            result = probe.run(client, expected_server="expected", explain_plan={})
        self.assertEqual(result["passed"], probe.EXPECTED_CASE_COUNT)
        self.assertEqual(client.execute.call_count, probe.EXPECTED_CASE_COUNT + 2)
        for call in client.execute.call_args_list[: probe.EXPECTED_CASE_COUNT + 1]:
            self.assertEqual(call.kwargs["settings"]["max_execution_time"], 1)
            self.assertEqual(call.kwargs["settings"]["max_threads"], 1)
            self.assertEqual(call.kwargs["settings"]["max_memory_usage"], 32 * 1024**2)
        self.assertEqual(
            client.execute.call_args.kwargs["settings"]["max_execution_time"], 15
        )
        self.assertEqual(client.execute.call_args.kwargs["settings"]["max_threads"], 2)
        self.assertEqual(
            client.execute.call_args.kwargs["settings"]["max_memory_usage"],
            256 * 1024**2,
        )
        self.assertNotIn("CUSTOMER_SECRET", json.dumps(result))

    def test_identity_mismatch_stops_before_any_fixture_or_explain(self):
        client = Mock()
        client.execute.return_value = [("wrong", "default", "25.test")]
        with self.assertRaises(ValueError):
            probe.run(client, expected_server="expected", explain_plan={})
        self.assertEqual(client.execute.call_count, 1)

    def test_optional_explain_error_does_not_fail_successful_fixture_exit(self):
        report = {
            "passed": probe.EXPECTED_CASE_COUNT,
            "executed": probe.EXPECTED_CASE_COUNT,
            "explain_error": {"error_type": "ServerException", "error_code": 159},
        }
        with (
            patch(
                "sys.argv",
                [
                    "probe",
                    "--production-read-only",
                    "--port",
                    "9000",
                    "--expected-server",
                    "expected",
                    "--output",
                    "unused",
                ],
            ),
            patch.object(probe, "initialize"),
            patch.object(probe, "run", return_value=report),
            patch.object(probe, "fingerprint", return_value="synthetic"),
            patch("clickhouse_driver.Client") as client,
            patch("replay_observe_filters.private_write") as write,
            patch("builtins.print"),
        ):
            self.assertEqual(probe.main(), 0)
        client.return_value.disconnect.assert_called_once()
        write.assert_called_once()

    def test_source_change_rejects_even_all_successful_fixture_results(self):
        with (
            patch(
                "sys.argv",
                [
                    "probe",
                    "--production-read-only",
                    "--port",
                    "9000",
                    "--expected-server",
                    "expected",
                    "--output",
                    "unused",
                ],
            ),
            patch.object(probe, "initialize"),
            patch.object(
                probe,
                "run",
                return_value={
                    "passed": probe.EXPECTED_CASE_COUNT,
                    "executed": probe.EXPECTED_CASE_COUNT,
                },
            ),
            patch.object(probe, "fingerprint", side_effect=["before", "after"]),
            patch("clickhouse_driver.Client"),
            patch("replay_observe_filters.private_write") as write,
            patch("builtins.print"),
        ):
            self.assertEqual(probe.main(), 2)
        report = write.call_args.args[1]
        self.assertEqual(report["passed"], 0)
        self.assertEqual(report["rejected_fixture_passes"], probe.EXPECTED_CASE_COUNT)
        self.assertFalse(report["source_unchanged"])
        self.assertEqual(report["reason"], "SOURCE_CHANGED_DURING_RUN_REJECT_RESULTS")


def known_plan():
    filters = probe.filters_for("contains", probe.NEEDLES["literal_like"])
    filters[0]["filter_config"]["filter_value"] = [
        probe.START.isoformat(),
        probe.END.isoformat(),
    ]
    return {
        "cases": [
            {
                "id": probe.KNOWN_CASE,
                "request": {
                    "path": "/tracer/trace/list_traces_of_session/",
                    "params": {
                        "project_id": probe.PROJECT,
                        "filters": json.dumps(filters),
                    },
                },
            }
        ]
    }


def index_plan():
    return [
        {
            "Plan": {
                "Node Type": "ReadFromMergeTree",
                "private": "CUSTOMER_SECRET",
                "Indexes": [
                    {
                        "Name": "idx_attrs_str_ngram",
                        "Condition": "CUSTOMER_SECRET",
                        "Keys": ["CUSTOMER_SECRET"],
                        "Initial Parts": 100,
                        "Selected Parts": 3,
                        "Initial Granules": 1000,
                        "Selected Granules": 25,
                    },
                    {"Name": "CUSTOMER_SECRET 'literal'", "Initial Parts": 1},
                ],
            }
        }
    ]


class RedactionTests(unittest.TestCase):
    def test_only_index_names_and_numeric_parts_granules_escape(self):
        self.assertEqual(
            probe.sanitized_indexes(index_plan()),
            [
                {
                    "name": "idx_attrs_str_ngram",
                    "Initial Parts": 100,
                    "Selected Parts": 3,
                    "Initial Granules": 1000,
                    "Selected Granules": 25,
                }
            ],
        )
        self.assertEqual(probe.sanitized_indexes({"Query": "CUSTOMER_SECRET"}), [])

    def test_raw_server_errors_never_escape(self):
        error = RuntimeError("CUSTOMER_SECRET SELECT password")
        error.code = 159
        self.assertEqual(
            probe.safe_error(error), {"error_type": "RuntimeError", "error_code": 159}
        )

    def test_158_is_not_a_json_parse_error_and_only_whitelisted_limit_escapes(self):
        from clickhouse_driver.errors import ErrorCodes, ServerException

        self.assertEqual(ErrorCodes.TOO_MANY_ROWS, 158)
        for setting in ("max_rows_to_read", "max_rows_to_read_leaf"):
            error = ServerException(
                f"Limit for rows (controlled by '{setting}' setting) exceeded. CUSTOMER_SECRET",
                code=158,
            )
            self.assertEqual(
                probe.safe_error(error),
                {
                    "error_type": "ServerException",
                    "error_code": 158,
                    "error_name": "TOO_MANY_ROWS",
                    "limit_setting": setting,
                },
            )
        self.assertEqual(
            probe.safe_error(ServerException("CUSTOMER_SECRET", code=158)),
            {
                "error_type": "ServerException",
                "error_code": 158,
                "error_name": "TOO_MANY_ROWS",
            },
        )
        self.assertNotEqual(
            probe.safe_error(json.JSONDecodeError("private", "secret", 0))[
                "error_code"
            ],
            158,
        )

    def test_hint_extraction_checks_balancing(self):
        self.assertEqual(
            probe.actual_hint_expression(
                "indexHint(f(x) LIKE %(a)s OR f(x) LIKE %(b)s) AND rest"
            ),
            "f(x) LIKE %(a)s OR f(x) LIKE %(b)s",
        )
        for witness in ("1", "indexHint(unclosed("):
            with self.assertRaises(ValueError):
                probe.actual_hint_expression(witness)


class ValuesWhereTests(unittest.TestCase):
    def test_nested_queries_union_and_or_precedence(self):
        sql = """WITH a AS (
            SELECT id FROM fixture_spans PREWHERE project = 1 OR project = 2
            WHERE id IN (SELECT id FROM fixture_spans PREWHERE time >= 1
                         WHERE parent IS NULL OR parent = '') OR id = 7
            GROUP BY id HAVING count() > 0
        ) SELECT id FROM a WHERE id > 0
        UNION ALL SELECT id FROM fixture_spans PREWHERE time >= 2 ORDER BY id LIMIT 4"""
        expected = """WITH a AS (
            SELECT id FROM fixture_spans WHERE ( project = 1 OR project = 2
            ) AND ( id IN (SELECT id FROM fixture_spans WHERE ( time >= 1
                         ) AND ( parent IS NULL OR parent = '') ) OR id = 7
            ) GROUP BY id HAVING count() > 0
        ) SELECT id FROM a WHERE id > 0
        UNION ALL SELECT id FROM fixture_spans WHERE ( time >= 2 ) ORDER BY id LIMIT 4"""
        self.assertEqual(probe.values_where(sql).split(), expected.split())

    def test_quoted_text_comments_placeholders_and_query_end_are_opaque(self):
        sql = r"""select 'PREWHERE ( WHERE )', 'a\'WHERE', "WHERE", `PREWHERE`
            FROM fixture_spans prewhere x = %(WHERE)s /* WHERE ) SELECT */
            where y = 'it''s PREWHERE' -- WHERE )"""
        expected = (
            r"""select 'PREWHERE ( WHERE )', 'a\'WHERE', "WHERE", `PREWHERE`
            FROM fixture_spans WHERE ( x = %(WHERE)s /* WHERE ) SELECT */
            ) AND ( y = 'it''s PREWHERE' -- WHERE )"""
            + "\n)"
        )
        self.assertEqual(probe.values_where(sql), expected)
        self.assertEqual(probe.values_where("SELECT 1 WHERE 1"), "SELECT 1 WHERE 1")
        self.assertEqual(
            probe.values_where("SELECT 1 PREWHERE 1;"), "SELECT 1 WHERE ( 1) ;"
        )

    def test_malformed_scopes_fail_closed(self):
        for sql in (
            "SELECT (1",
            "SELECT 1)",
            "SELECT 1 PREWHERE 1 PREWHERE 2",
            "SELECT 1 PREWHERE 1 WHERE 2 WHERE 3",
        ):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                probe.values_where(sql)


if __name__ == "__main__":
    unittest.main()
