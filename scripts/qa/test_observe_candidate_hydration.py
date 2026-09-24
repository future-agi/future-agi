"""Replay page-content contracts with real public merge/identity helpers.

No database socket or production query is opened by these tests.
"""

from datetime import datetime, timezone
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import observe_candidate_hydration as hydration
import probe_long_text_index_contract_readonly as setup
import replay_observe_filters as replay

PROJECT = "11111111-1111-4111-8111-111111111111"
WHEN = datetime(2026, 9, 1, 12, 30, tzinfo=timezone.utc)


class Reader:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def execute_ch_query(self, sql, params, **kwargs):
        self.calls.append((sql, params))
        return SimpleNamespace(data=next(self.responses))


class HydrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup.initialize()  # Disables startup network and ORM connections.

    def row(self, entity):
        row = {
            "project_id": PROJECT,
            "trace_id": "trace",
            "id": "span",
            "start_time": WHEN,
            "observation_type": "SPAN",
            "service_name": "",
            "_version": 3,
        }
        if entity == "traces":
            row.update(
                root_span_id="span",
                _root_service_name="",
                _root_version=3,
                _root_observation_type="SPAN",
                _root_start_hour=WHEN.replace(minute=0),
            )
        return row

    def builder(self, entity):
        from tracer.services.clickhouse.v2.query_builders.span_list import (
            SpanListQueryBuilderV2,
        )
        from tracer.services.clickhouse.v2.query_builders.trace_list import (
            TraceListQueryBuilderV2,
        )

        return (
            SpanListQueryBuilderV2 if entity == "spans" else TraceListQueryBuilderV2
        )(project_id=PROJECT, filters=[])

    def run_page(self, entity, content, attributes=None, keys=None):
        builder = self.builder(entity)
        row = self.row(entity)
        reader = Reader([content] + ([] if attributes is None else [attributes]))
        with (
            patch.object(
                builder, "build_content_query", return_value=("SELECT content", {})
            ),
            patch.object(
                builder,
                "build_span_attributes_query",
                return_value=("SELECT attributes", {}),
                create=True,
            ),
        ):
            result = hydration.hydrate_page(
                reader,
                builder,
                {"query_complete": True, "table": [row]},
                entity=entity,
                request={"params": {"attribute_keys": json.dumps(keys or [])}},
            )
        return reader, result

    def test_span_and_trace_content_is_loaded_and_hashed_without_changing_identity(
        self,
    ):
        for entity in ("traces", "spans"):
            with self.subTest(entity=entity):
                content = {
                    **self.row(entity),
                    "input": "full input",
                    "output": "full output",
                    "attrs_string": {"company_id": "0012"},
                    "attrs_number": {"duration": 1.25},
                }
                reader, result = self.run_page(entity, [content])
                self.assertEqual(len(reader.calls), 1)
                self.assertEqual(result["table"][0]["input"], "full input")
                self.assertEqual(
                    result["table"][0]["attrs_string"]["company_id"], "0012"
                )
                self.assertTrue(result["query_layer_coverage"]["content"])
                self.assertFalse(result["query_layer_coverage"]["full_public_request"])
                self.assertEqual(result["table"][0]["start_time"], WHEN)

    def test_trace_metadata_is_preserved_in_complete_raw_result(self):
        for metadata in ('{"revision":"new","flag":false}', "{}", None):
            with self.subTest(metadata=metadata):
                reader, result = self.run_page(
                    "traces", [{**self.row("traces"), "metadata": metadata}]
                )
                self.assertEqual(len(reader.calls), 1)
                self.assertIn("metadata", result["table"][0])
                self.assertEqual(result["table"][0]["metadata"], metadata)
                self.assertFalse(result["query_layer_coverage"]["full_public_request"])

    def test_missing_duplicate_foreign_or_changed_content_never_counts_as_complete(
        self,
    ):
        for entity in ("traces", "spans"):
            row = self.row(entity)
            version_key = "_root_version" if entity == "traces" else "_version"
            for content in (
                [],
                [row, row],
                [{**row, version_key: 4}],
                [{**row, "start_time": WHEN.replace(minute=31)}],
                [{**row, "project_id": "22222222-2222-4222-8222-222222222222"}],
            ):
                with (
                    self.subTest(entity=entity, content=content),
                    self.assertRaises((replay.ReplayError, ValueError)),
                ):
                    self.run_page(entity, content)

    def test_requested_trace_attributes_preserve_json_types_and_long_values(self):
        values = {
            "string_id": "0012",
            "numeric_id": 12,
            "flag": False,
            "long": "x" * 1001,
        }
        attributes = [
            {
                "project_id": PROJECT,
                "trace_id": "trace",
                "attribute_key": key,
                "attribute_value_json": json.dumps(value),
            }
            for key, value in values.items()
        ]
        reader, result = self.run_page(
            "traces", [self.row("traces")], attributes, list(values)
        )
        self.assertEqual(
            [sql for sql, _ in reader.calls], ["SELECT content", "SELECT attributes"]
        )
        self.assertEqual(
            result["table"][0]["replay_requested_attributes"],
            {key: [value] for key, value in values.items()},
        )
        self.assertEqual(
            result["hydration_phases"],
            [
                {"name": "content", "rows": 1},
                {"name": "requested_attributes", "rows": 4},
            ],
        )

    def test_unrequested_key_foreign_trace_and_duplicate_attribute_are_rejected(self):
        good = {
            "project_id": PROJECT,
            "trace_id": "trace",
            "attribute_key": "a",
            "attribute_value_json": "12",
        }
        for attributes in (
            [{**good, "attribute_key": "foreign"}],
            [{**good, "trace_id": "foreign"}],
            [good, good],
        ):
            with (
                self.subTest(attributes=attributes),
                self.assertRaises(replay.ReplayError),
            ):
                self.run_page("traces", [self.row("traces")], attributes, ["a", "b"])

    def test_empty_and_incomplete_pages_do_not_start_content_queries(self):
        for payload in (
            {"query_complete": True, "table": []},
            {"query_complete": False, "table": [self.row("spans")]},
        ):
            reader = Reader([])
            result = hydration.hydrate_page(
                reader, self.builder("spans"), payload, entity="spans", request={}
            )
            self.assertEqual(reader.calls, [])
            self.assertEqual(result["query_complete"], payload["query_complete"])

    def test_malformed_requested_keys_do_not_silently_skip_attribute_loading(self):
        for raw in ("{", '"key"', '[""]', "[12]"):
            with self.subTest(raw=raw), self.assertRaises(replay.ReplayError):
                hydration.requested_attribute_keys({"params": {"attribute_keys": raw}})

    def test_session_routes_include_every_hydration_phase_without_changing_selection(self):
        from tracer.services.clickhouse.v2.query_builders.session_list import SessionListQueryBuilderV2

        builder = SessionListQueryBuilderV2(project_id=PROJECT, filters=[])
        rows = [{"session_id": "22222222-2222-4222-8222-222222222222"}]
        payload = {"query_complete": True, "table": rows, "has_more": True}
        phases = [[{"total_tokens": 17}], [{"input": "full"}], [{"long": "K" * 1001}]]
        reader = Reader(phases)
        with patch.object(reader, "execute_ch_query", wraps=reader.execute_ch_query) as execute:
            result = hydration.hydrate_session_queries(reader, builder, payload, remaining_ms=lambda: 1234)
            self.assertEqual([c.kwargs for c in execute.call_args_list], [{"timeout_ms": 1234}] * 3)
        self.assertIs(result["table"], rows)
        self.assertTrue(result["has_more"])
        self.assertEqual(list(result["query_phases"]), ["metrics", "content", "attributes"])
        self.assertEqual(list(result["query_phases"].values()), phases)
        self.assertTrue(result["serial_enrichment_not_http_or_pg_overlay"])
        for sql, params in reader.calls:
            self.assertIn("project_id", sql)
            self.assertIn(rows[0]["session_id"], str(params))

    def test_session_empty_incomplete_and_failed_hydration_are_not_false_page_passes(self):
        builder = SimpleNamespace()
        for payload in ({"query_complete": False, "table": [{"session_id": "a"}]},
                        {"query_complete": True, "table": []}):
            reader = Reader([])
            result = hydration.hydrate_session_queries(reader, builder, payload, remaining_ms=lambda: 1)
            self.assertEqual(reader.calls, [])
            self.assertEqual(result["query_complete"], payload["query_complete"])
        def method(ids):
            return "SELECT hydration", {"ids": ids}

        builder = SimpleNamespace(build_page_metrics_query=method, build_content_query=method,
                                  build_span_attributes_query=method)
        with self.assertRaises(StopIteration):
            hydration.hydrate_session_queries(Reader([]), builder,
                {"query_complete": True, "table": [{"session_id": "a"}]}, remaining_ms=lambda: 1)


    def test_session_entity_dispatch_hydrates_once_under_public_route_policy(self):
        import replay_observe_queries_readonly as queries
        from tracer.tests.test_session_positive_witness_page import builder, leaf
        for kind, preferred in (("text", True), ("mixed", True), ("number", False), ("boolean", False)):
            filters = [leaf("company", ["alpha"], "text", "in")]
            if kind in {"mixed", "number"}:
                filters.append(leaf("other", False, "boolean") if kind == "mixed" else leaf("other", 7))
            elif kind == "boolean":
                filters = [leaf("flag", True, "boolean")]
            rows = [{"session_id": "22222222-2222-4222-8222-222222222222"}]
            payload = {"query_complete": True, "table": rows, "has_more": False}
            reader = Reader(([] if preferred else [rows]) + [[{}], [{}], [{}]])
            reader.remaining_read_ms = lambda: 60000
            adapter = queries.CandidateQueries(SimpleNamespace(relational_metadata=None),
                {"scope": {"project_id": PROJECT}}, [PROJECT])
            with self.subTest(kind=kind), patch.object(queries, "collect_selector_page", return_value=payload) as bounded, \
                    patch.object(hydration, "hydrate_session_queries", wraps=hydration.hydrate_session_queries) as hydrate:
                result = adapter.entity_list(reader, {"surface": "sessions", "request": {"target_rows": 25}},
                                             builder(*filters).filters)
            self.assertEqual(bounded.call_count, int(preferred))
            self.assertEqual(hydrate.call_count, 1)
            self.assertEqual(len(reader.calls), 3 if preferred else 4)
            self.assertEqual(result["table"], rows)
            self.assertEqual(list(result["query_phases"]), ["metrics", "content", "attributes"])
            self.assertEqual(result["session_order_mode"], "uuid_string" if preferred else "uuid")


if __name__ == "__main__":
    unittest.main()
