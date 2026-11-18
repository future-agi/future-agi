"""Pure capture reducer oracles; public ingress and CH replay run separately.

The checked-in native fixture supplies literal IDs and source totals. These
unit tests are not certification of a signed-in binary or immutable replay.
"""

import copy
import json
import unittest
from pathlib import Path

from tracer.services.clickhouse.query_builders.trace_detail import (
    compute_trace_capture_coverage,
    compute_trace_summary_and_graph,
)
from tracer.services.clickhouse.query_builders.trace_usage import (
    capture_coverage_from_counts,
    source_summary_sql,
    usage_aggregate_sql,
    usage_coverage_sql,
)
from tracer.utils.normalization import normalize_attributes

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "fi-collector/exporter/clickhouse25exporter/testdata/copilot_1_0_98_otlp.json"
)


def native_tree(*, source_usd=None):
    """Normalized read-side snapshot, not a replacement ingestion converter."""
    payload = json.loads(FIXTURE.read_text())
    batch = payload["resourceSpans"][0]["scopeSpans"][0]
    entries = []
    for raw in batch["spans"]:
        attrs = {}
        for item in raw["attributes"]:
            value = item["value"]
            if "intValue" in value:
                attrs[item["key"]] = int(value["intValue"])
            elif "stringValue" in value:
                attrs[item["key"]] = value["stringValue"]
        if raw["name"] == "invoke_agent" and source_usd is not None:
            attrs["gen_ai.cost.total"] = source_usd
        attrs = normalize_attributes(attrs, batch["scope"])
        prompt = attrs.get("gen_ai.usage.input_tokens", 0)
        completion = attrs.get("gen_ai.usage.output_tokens", 0)
        # Native read snapshots distinguish missing prices. Price estimation
        # and normalization's numeric conversion are covered by their own tests.
        attrs["fi.cost.kind"] = "unknown"
        entries.append(
            {
                "observation_span": {
                    "id": raw["spanId"],
                    "parent_span_id": raw.get("parentSpanId"),
                    "name": raw["name"],
                    "observation_type": "llm" if raw["name"] == "chat" else "agent",
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": prompt + completion,
                    "cost": 0,
                    "span_attributes": attrs,
                },
                "children": [],
            }
        )
    return entries


class NativeCaptureCoverageTests(unittest.TestCase):
    def test_original_root_only_then_late_children_keeps_source_nonadditive(self):
        for usd in (None, 17):
            with self.subTest(source_usd=usd):
                entries = native_tree(source_usd=usd)
                root = entries[0]
                original_root = copy.deepcopy(root)
                raw = root["observation_span"]["span_attributes"]
                self.assertEqual(
                    raw["fi.source.summary.gen_ai.usage.input_tokens"], 42925
                )
                self.assertEqual(
                    raw["fi.source.summary.gen_ai.usage.output_tokens"], 146
                )
                if usd is not None:
                    self.assertEqual(raw["fi.source.summary.gen_ai.cost.total"], 17)
                summary, graph = compute_trace_summary_and_graph([root])
                self.assertEqual(summary["total_tokens"], 0)
                self.assertEqual(summary["total_cost"], 0)
                self.assertEqual(
                    [node["id"] for node in graph["nodes"]], ["0000000000000001"]
                )
                self.assertEqual(
                    compute_trace_capture_coverage([root]),
                    {
                        "scope": "observed_requests",
                        "source_summary_present": True,
                        "request_count": 0,
                        "coverage_unknown": True,
                    },
                )
                root["children"] = entries[1:]
                summary, graph = compute_trace_summary_and_graph([root])
                self.assertEqual(summary["total_prompt_tokens"], 42925)
                self.assertEqual(summary["total_completion_tokens"], 146)
                self.assertEqual(summary["total_tokens"], 43071)
                self.assertIsNone(summary["total_cost"])
                self.assertEqual(
                    compute_trace_capture_coverage([root]),
                    {
                        "scope": "observed_requests",
                        "source_summary_present": True,
                        "request_count": 3,
                        "coverage_unknown": True,
                    },
                )
                self.assertEqual(
                    [node["id"] for node in graph["nodes"]],
                    [
                        "0000000000000001",
                        "0000000000000002",
                        "0000000000000003",
                        "0000000000000004",
                        "0000000000000005",
                    ],
                )
                self.assertEqual(
                    root["observation_span"], original_root["observation_span"]
                )
                # Re-reading an identical selected snapshot is pure. The CH
                # companion test proves physical duplicate replay selection.
                self.assertEqual(
                    compute_trace_summary_and_graph(copy.deepcopy([root])),
                    (summary, graph),
                )

    def test_equal_siblings_are_two_requests_not_identity_proof(self):
        root = native_tree(source_usd=17)[0]
        root["children"] = [
            {
                "observation_span": {
                    "id": span_id,
                    "name": "chat",
                    "observation_type": "llm",
                    "total_tokens": 120,
                    "cost": 0,
                },
                "children": [],
            }
            for span_id in ("a", "b")
        ]
        self.assertEqual(compute_trace_capture_coverage([root])["request_count"], 2)
        self.assertTrue(compute_trace_capture_coverage([root])["coverage_unknown"])
        self.assertEqual(
            compute_trace_summary_and_graph([root])[0]["total_tokens"], 240
        )

    def test_fully_priced_children_still_do_not_certify_capture(self):
        entries = native_tree(source_usd=17)
        root = entries[0]
        root["children"] = entries[1:]
        for index, entry in enumerate(root["children"]):
            span = entry["observation_span"]
            span["span_attributes"]["fi.cost.kind"] = "provider_reported"
            span["cost"] = 1.25 if index == 0 else 0
        summary, _ = compute_trace_summary_and_graph([root])
        self.assertEqual(summary["total_tokens"], 43071)
        self.assertEqual(summary["total_cost"], 1.25)
        self.assertEqual(
            compute_trace_capture_coverage([root]),
            {
                "scope": "observed_requests",
                "source_summary_present": True,
                "request_count": 3,
                "coverage_unknown": True,
            },
        )

    def test_nonadditive_llm_does_not_become_a_request_or_usage(self):
        root = native_tree(source_usd=17)[0]
        root["observation_span"].update(
            observation_type="llm", total_tokens=43071, cost=17
        )
        coverage = compute_trace_capture_coverage([root])
        self.assertEqual(coverage["request_count"], 0)
        summary, _ = compute_trace_summary_and_graph([root])
        self.assertEqual(summary["total_tokens"], 0)
        self.assertEqual(summary["total_cost"], 0)

    def test_legacy_zero_and_unflagged_non_llm_usage_are_unchanged(self):
        for cost in (0, 1.25):
            tree = [
                {
                    "observation_span": {
                        "id": "legacy",
                        "observation_type": "agent",
                        "cost": cost,
                        "total_tokens": 120,
                    },
                    "children": [],
                }
            ]
            self.assertIsNone(compute_trace_capture_coverage(tree))
            summary, _ = compute_trace_summary_and_graph(tree)
            self.assertEqual(summary["total_cost"], cost)
            self.assertEqual(summary["total_tokens"], 120)
        self.assertIsNone(compute_trace_capture_coverage([]))

    def test_unknown_profile_is_not_reclassified_as_native_summary(self):
        attrs = normalize_attributes(
            {
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.usage.input_tokens": 42925,
                "gen_ai.usage.output_tokens": 146,
            },
            {"name": "github.copilot", "version": "unqualified"},
        )
        tree = [
            {
                "observation_span": {
                    "id": "legacy",
                    "observation_type": "agent",
                    "span_attributes": attrs,
                    "total_tokens": 43071,
                    "cost": 0,
                },
                "children": [],
            }
        ]
        self.assertIsNone(compute_trace_capture_coverage(tree))
        self.assertEqual(
            compute_trace_summary_and_graph(tree)[0]["total_tokens"], 43071
        )

    def test_read_metadata_never_copies_raw_source_or_event_content(self):
        root = native_tree(source_usd=17)[0]
        root["observation_span"]["span_attributes"]["arbitrary.event.text"] = (
            "private source content"
        )
        result = compute_trace_capture_coverage([root])
        self.assertEqual(
            set(result),
            {
                "scope",
                "source_summary_present",
                "request_count",
                "coverage_unknown",
            },
        )
        self.assertNotIn("private source content", json.dumps(result))
        self.assertNotIn("42925", json.dumps(result))
        self.assertIsNone(
            capture_coverage_from_counts(source_summary_present=False, request_count=3)
        )

    def test_legacy_camel_case_tree_is_supported(self):
        root = native_tree()[0]
        root["observationSpan"] = root.pop("observation_span")
        self.assertEqual(compute_trace_capture_coverage([root])["request_count"], 0)

    def test_shared_sql_api_is_additive_and_keeps_two_flag_tuple(self):
        flags = usage_coverage_sql(attrs="attrs_string", prefix="live.")
        self.assertEqual(
            flags,
            "tuple(attrs_string['fi.usage.contribution'] = 'none', "
            "attrs_string['fi.cost.kind'] = 'unknown' AND "
            "(live.total_tokens > 0 OR live.prompt_tokens > 0 OR live.completion_tokens > 0 "
            "OR live.observation_type = 'llm' OR attrs_string['fi.usage.scope'] = 'turn'))",
        )
        default = usage_aggregate_sql(coverage="flags")
        self.assertNotIn("usage_source_summary_present", default)
        self.assertNotIn("s.10", default)
        with_capture = usage_aggregate_sql(
            coverage="flags", source_summary="source_witness"
        )
        self.assertIn("arrayExists(s -> s.10, usage_spans)", with_capture)
        self.assertIn(
            "arrayCount(s -> s.4 = 'llm', billable_usage_spans)", with_capture
        )
        self.assertNotIn("attrs_string", with_capture)
        self.assertEqual(
            source_summary_sql(attrs="s.attrs_string"),
            "(s.attrs_string['fi.usage.contribution'] = 'none' "
            "AND s.attrs_string['fi.usage.scope'] = 'turn')",
        )


if __name__ == "__main__":
    unittest.main()
