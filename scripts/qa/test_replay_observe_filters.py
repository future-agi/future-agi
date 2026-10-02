"""Protocol tests for the HTTP replay tool, not production acceptance tests."""

import contextlib
import copy
import http.server
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import replay_observe_filters as replay


SCOPE = {
    "organization_id": "org-test",
    "workspace_id": "workspace-test",
    "project_id": "project-test",
    "user_id": "0010000004",
}
ATTR = {
    "name": "company_id",
    "property_id": "custom_attribute:company_id",
    "resolved_type": "string",
    "observed_types": ["string", "number"],
    "seeds": [
        {"type": "string", "value": "0010000001"},
        {"type": "number", "value": 10000001},
    ],
}


def fixture_plan(surfaces=("traces",), attributes=None, combos=0):
    return replay.make_plan(
        {"scope": SCOPE, "attributes": attributes or [ATTR]},
        replay.utc("2026-09-05T00:00:00Z"),
        surfaces,
        combos,
    )


def case(surface="traces", **overrides):
    item = copy.deepcopy(fixture_plan((surface,))["cases"][0])
    item.update(overrides)
    return item


def page(rows=None, **kwargs):
    return {
        "table": rows or [],
        "metadata": {
            "query_complete": True,
            "query_status": "complete",
            "query_exact": True,
            "ordering_exact": True,
            "has_more": False,
            **kwargs,
        },
    }


class FakeClient:
    def __init__(self, results, build=True):
        self.results = iter(results)
        self.calls = []
        self.build = build

    def request(self, method, path, params, body, deadline):
        self.calls.append(
            (method, path, copy.deepcopy(params), copy.deepcopy(body), deadline)
        )
        return next(self.results), {"build_verified": self.build, "response_bytes": 1}


class ReportedInputTests(unittest.TestCase):
    def test_explicit_predicate_is_not_required_to_equal_a_catalog_suggestion(self):
        plan = replay.make_plan(
            {"scope": SCOPE, "attributes": [ATTR]},
            replay.utc("2026-09-05T00:00:00Z"),
            ("traces",),
            0,
            [
                {
                    "label": "threshold",
                    "filters": [
                        {
                            "name": "company_id",
                            "type": "number",
                            "op": "greater_than",
                            "value": 1,
                        }
                    ],
                }
            ],
        )
        cases = [c for c in plan["cases"] if c["variant"] == "reported:threshold"]
        self.assertEqual(len(cases), 3)
        for case in cases:
            self.assertIsNone(case["blocked"])
            filters = json.loads(case["request"]["params"]["filters"])
            self.assertEqual(filters[1]["filter_config"]["filter_value"], 1)
            self.assertEqual(filters[1]["filter_config"]["col_type"], "SPAN_ATTRIBUTE")
        self.assertEqual(plan["reported_input_count"], 1)

    def test_reproduction_cannot_coerce_number_or_invent_an_attribute(self):
        for name, value in [("company_id", "1"), ("unknown", 1)]:
            with self.subTest(name=name), self.assertRaises(replay.ReplayError):
                replay.make_plan(
                    {"scope": SCOPE, "attributes": [ATTR]},
                    replay.utc("2026-09-05T00:00:00Z"),
                    ("traces",),
                    0,
                    [
                        {
                            "label": "invalid",
                            "filters": [
                                {
                                    "name": name,
                                    "type": "number",
                                    "op": "greater_than",
                                    "value": value,
                                }
                            ],
                        }
                    ],
                )


@contextlib.contextmanager
def server(responses):
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else None
            seen.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "headers": dict(self.headers),
                    "body": json.loads(body) if body else None,
                }
            )
            spec = responses[min(len(seen) - 1, len(responses) - 1)]
            time.sleep(spec.get("delay", 0))
            payload = json.dumps(
                {"status": True, "result": spec.get("result", page())}
            ).encode()
            try:
                self.send_response(spec.get("status", 200))
                self.send_header(
                    "Content-Type", spec.get("content_type", "application/json")
                )
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("X-Test-Build", spec.get("build", "candidate-sha"))
                if spec.get("status") == 302:
                    self.send_header(
                        "Location", "https://untrusted.example/credentials"
                    )
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        do_POST = do_GET

    instance = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=instance.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{instance.server_port}/api/v1", seen
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()


class PlanTests(unittest.TestCase):
    def test_seeded_variants_cover_every_public_attribute_operator(self):
        contract = replay.read_json(
            Path(__file__).resolve().parents[2] / "api_contracts/filter_contract.json"
        )
        families = {
            **contract["operators"]["spanAttributeAllowed"],
            **contract["operators"]["structuredSpanAttributeAllowed"],
        }
        seeds = {
            "text": "001234",
            "number": 1.5,
            "boolean": False,
            "array": ["001234"],
            "map": {"id": "001234"},
        }
        for kind, allowed in families.items():
            attribute = {
                "name": "field",
                "resolved_type": kind,
                "seeds": [{"type": kind, "value": seeds[kind]}],
            }
            generated = list(replay.variants(attribute))
            operators = {
                leaves[0]["filter_config"]["filter_op"]
                for _, leaves, blocked in generated
                if not blocked
                and leaves[0]["filter_config"]["filter_type"] == replay.TYPES[kind]
            }
            with self.subTest(kind=kind):
                self.assertEqual(operators, set(allowed))
        ranges = [
            leaves[0]["filter_config"]["filter_value"]
            for _, leaves, blocked in replay.variants(
                {
                    "name": "n",
                    "resolved_type": "number",
                    "seeds": [
                        {"type": "number", "value": 9},
                        {"type": "number", "value": 1},
                    ],
                }
            )
            if not blocked
            and leaves[0]["filter_config"]["filter_op"] in ("between", "not_between")
        ]
        self.assertEqual(ranges, [[1, 9], [1, 9]])

    def test_all_types_dates_surfaces_and_real_values(self):
        attrs = [
            ATTR,
            {
                "name": "agent.duration_s",
                "resolved_type": "number",
                "seeds": [{"type": "number", "value": 1.2}],
            },
            {
                "name": "flag",
                "resolved_type": "boolean",
                "seeds": [{"type": "boolean", "value": False}],
            },
            {
                "name": "object",
                "resolved_type": "map",
                "seeds": [{"type": "map", "value": {"key": "v"}}],
            },
            {
                "name": "array",
                "resolved_type": "array",
                "seeds": [{"type": "array", "value": ["x", 1]}],
            },
            {
                "name": "long",
                "resolved_type": "string",
                "seeds": [{"type": "string", "value": "a" * 800}],
            },
        ]
        plan = fixture_plan(replay.SURFACES, attrs)
        self.assertEqual(set(plan["surfaces"]), set(replay.SURFACES))
        self.assertEqual({c["period"] for c in plan["cases"]}, {"7D", "30D", "12M"})
        self.assertEqual(len(plan["cases"]), len({c["id"] for c in plan["cases"]}))
        for attr in attrs:
            self.assertTrue(
                any(c["attributes"] == [attr["name"]] for c in plan["cases"])
            )
        serialized = replay.canonical(plan)
        self.assertIn("0010000001", serialized)
        self.assertIn("a" * 800, serialized)
        self.assertNotIn("force_refresh", serialized)
        for c in plan["cases"]:
            if c["request"]:
                self.assertIn(
                    (c["request"]["method"], c["request"]["path"]), replay.ALLOWED
                )

    def test_deterministic_and_integrity_hashed(self):
        plan = fixture_plan()
        self.assertEqual(plan, fixture_plan())
        self.assertEqual(
            plan["plan_id"],
            replay.digest({k: v for k, v in plan.items() if k != "plan_id"}),
        )

    def test_latest_p0_deadlines_are_predeclared_by_surface(self):
        plan = fixture_plan(replay.SURFACES)
        for c in plan["cases"]:
            expected = (
                5000
                if c["surface"] in replay.LISTS
                else 30000
                if c["period"] == "12M"
                else 15000
            )
            self.assertEqual(c["target_ms"], expected)

    def test_calendar_year_leap_day(self):
        self.assertEqual(
            replay.window(replay.utc("2024-02-29T04:00:00Z"), "12M")["start"],
            "2023-02-28T04:00:00+00:00",
        )
        with self.assertRaises(replay.ReplayError):
            replay.utc("2026-09-04")

    def test_missing_seed_is_not_synthetic_or_pass(self):
        plan = fixture_plan(attributes=[{"name": "sparse", "resolved_type": "number"}])
        self.assertTrue(
            any(c["blocked"] == "OBSERVED_TYPED_VALUE_REQUIRED" for c in plan["cases"])
        )
        c = next(c for c in plan["cases"] if c["blocked"])
        client = FakeClient([])
        self.assertEqual(replay.run_case(client, c, 1)["status"], "BLOCKED_INPUT")
        self.assertEqual(client.calls, [])

    def test_custom_names_do_not_become_native_aliases(self):
        for name in ("created_at", "user_id", "total_cost", "latency"):
            leaf = replay.raw_leaf({"name": name}, "number", "greater_than", 1)
            self.assertEqual(leaf["filter_config"]["col_type"], "SPAN_ATTRIBUTE")

    def test_combinations_up_to_ten_use_distinct_real_attributes(self):
        attrs = [
            {
                "name": f"field{i}",
                "resolved_type": "number" if i % 2 else "string",
                "seeds": [
                    {
                        "type": "number" if i % 2 else "string",
                        "value": i if i % 2 else str(i),
                    }
                ],
            }
            for i in range(15)
        ]
        plan = fixture_plan(attributes=attrs, combos=2)
        combos = [c for c in plan["cases"] if c["variant"].startswith("mixed:")]
        self.assertEqual({len(c["attributes"]) for c in combos}, {2, 5, 10})
        self.assertTrue(
            all(len(c["attributes"]) == len(set(c["attributes"])) for c in combos)
        )
        self.assertTrue(all(not c["blocked"] for c in combos))

    def test_user_scope_and_visible_metric_projection(self):
        project = case("users_project")["request"]["params"]
        workspace = case("users_workspace")["request"]["params"]
        detail = case("user_traces")["request"]["params"]
        self.assertEqual(project["project_id"], SCOPE["project_id"])
        self.assertNotIn("project_id", workspace)
        self.assertIn("total_tokens", json.loads(project["requested_columns"]))
        self.assertEqual(
            json.loads(detail["filters"])[-1]["filter_config"]["filter_value"],
            "0010000004",
        )

    def test_no_unsupported_span_or_session_projection_params(self):
        for surface in ("spans", "sessions", "task_spans", "eval_sessions"):
            self.assertNotIn("attribute_keys", case(surface)["request"]["params"])
        for surface in ("traces", "users_project"):
            self.assertIn("attribute_keys", case(surface)["request"]["params"])

    def test_preview_list_stage_matches_tasks_one_and_eval_fifty_not_execution(self):
        for surface in replay.SURFACES:
            if surface.startswith(("task_", "eval_")):
                req = case(surface)["request"]
                expected = 1 if surface.startswith("task_") else 50
                self.assertEqual(req["params"]["page_size"], expected)
                self.assertEqual(req["target_rows"], expected)
                self.assertEqual(req["method"], "GET")

    def test_graph_namespace_and_dashboard_no_sampling(self):
        for surface, namespace in (
            ("trace_graph", "traces"),
            ("session_graph", "sessions"),
            ("users_graph", "users"),
        ):
            config = case(surface)["request"]["body"]["req_data_config"]
            self.assertEqual(
                config["property_id"], f"system_attribute:{namespace}:latency"
            )
        body = case("dashboard_metric")["request"]["body"]
        self.assertIs(body["allow_sampled"], False)
        self.assertEqual(len(body["metrics"]), 5)
        self.assertEqual(body["project_ids"], [SCOPE["project_id"]])

    def test_nonnumeric_dashboard_values_keep_valid_exact_count_workloads(self):
        for kind, seed in (("string", "a long value " * 60), ("boolean", False)):
            attribute = {
                "name": "dimension",
                "resolved_type": kind,
                "observed_types": [kind],
                "seeds": [{"type": kind, "value": seed}],
            }
            plan = fixture_plan(("dashboard_metric",), [attribute])
            self.assertTrue(plan["cases"])
            blocked = [entry for entry in plan["cases"] if entry["blocked"]]
            self.assertEqual(len(blocked), 6 if kind == "boolean" else 0)
            for entry in plan["cases"]:
                if entry["blocked"]:
                    self.assertIn(entry["variant"], (
                        "boolean:in:multiple", "boolean:not_in:multiple"
                    ))
                    self.assertEqual(entry["blocked"], "OBSERVED_DISTINCT_BOOLEAN_VALUES_REQUIRED")
                    continue
                self.assertIsNone(entry["blocked"])
                body = entry["request"]["body"]
                self.assertIs(body["allow_sampled"], False)
                self.assertEqual(
                    [metric["aggregation"] for metric in body["metrics"]],
                    ["count", "count_distinct"],
                )
                self.assertEqual(
                    {metric["attribute_type"] for metric in body["metrics"]}, {kind}
                )
                self.assertEqual(
                    {metric["attribute_key"] for metric in body["metrics"]},
                    {"dimension"},
                )

    def test_structured_metrics_are_not_coerced_into_text_count_cases(self):
        attribute = {
            "name": "structured",
            "resolved_type": "array",
            "observed_types": ["array"],
            "seeds": [{"type": "array", "value": ["a", "b"]}],
        }
        plan = fixture_plan(("dashboard_metric",), [attribute])
        for entry in plan["cases"]:
            self.assertEqual(entry["blocked"], "STRUCTURED_METRIC_NOT_COVERED")
            self.assertIsNone(entry["request"])


class ProtocolTests(unittest.TestCase):
    def test_completion_is_not_correctness_pass(self):
        row = replay.run_case(FakeClient([page()]), case(), 1)
        self.assertEqual(row["status"], "COMPLETE_UNVERIFIED")
        self.assertEqual(row["rows"], 0)
        self.assertEqual(row["correctness"], "UNVERIFIED")

    def test_oracle_and_candidate_both_required(self):
        oracle = {
            "result_sha256": replay.digest([]),
            "provenance": "Independent exact reference at frozen window",
        }
        self.assertEqual(
            replay.run_case(FakeClient([page()]), case(), 1, oracle)["status"],
            "API_PASS",
        )
        self.assertEqual(
            replay.run_case(FakeClient([page()], build=False), case(), 1, oracle)[
                "status"
            ],
            "COMPLETE_UNVERIFIED",
        )
        bad = {**oracle, "result_sha256": "0" * 64}
        self.assertEqual(
            replay.run_case(FakeClient([page()]), case(), 1, bad)["reason"],
            "ORACLE_MISMATCH",
        )

    def test_sampling_and_inexact_and_approximate_fail(self):
        for patch in (
            {"query_sampled": True},
            {"query_status": "sampled"},
            {"query_exact": False},
            {"ordering_exact": False},
            {"approximate_fields": ["total_tokens"]},
        ):
            with self.subTest(patch=patch):
                self.assertEqual(
                    replay.run_case(FakeClient([page(**patch)]), case(), 1)["status"],
                    "FAIL",
                )

    def test_missing_metadata_not_silent_empty(self):
        row = replay.run_case(FakeClient([{"table": []}]), case(), 1)
        self.assertEqual(row["reason"], "INCOMPLETE_WITHOUT_CURSOR")

    def test_cursor_follows_immutable_scope_and_shared_deadline(self):
        first = page(
            query_complete=False,
            query_status="degraded",
            has_more=True,
            next_cursor="opaque",
            next_cursor_fingerprint="one",
        )
        client = FakeClient([first, page([{"id": "trace1"}])])
        row = replay.run_case(client, case(), 1, poll_seconds=0)
        self.assertEqual(row["status"], "COMPLETE_UNVERIFIED")
        self.assertEqual(row["requests"], 2)
        self.assertNotIn("page_number", client.calls[1][2])
        self.assertEqual(client.calls[0][2]["filters"], client.calls[1][2]["filters"])
        self.assertEqual(client.calls[0][4], client.calls[1][4])
        self.assertEqual(client.calls[1][2]["cursor"], "opaque")

    def test_repeated_cursor_fingerprint_with_rotating_token(self):
        first = page(
            query_complete=False,
            query_status="degraded",
            has_more=True,
            next_cursor="token1",
            next_cursor_fingerprint="same",
        )
        second = page(
            query_complete=False,
            query_status="degraded",
            has_more=True,
            next_cursor="token2",
            next_cursor_fingerprint="same",
        )
        row = replay.run_case(FakeClient([first, second]), case(), 1, poll_seconds=0)
        self.assertEqual(row["reason"], "REPEATED_CURSOR")

    def test_duplicate_prefix_and_oversized_page_are_errors(self):
        first = page(
            [{"id": "trace1"}],
            query_complete=False,
            query_status="degraded",
            has_more=True,
            next_cursor="next",
        )
        row = replay.run_case(
            FakeClient([first, page([{"id": "trace1"}])]), case(), 1, poll_seconds=0
        )
        self.assertEqual(row["reason"], "DUPLICATE_ROW_ACROSS_CONTINUATIONS")
        row = replay.run_case(
            FakeClient([page([{"id": str(i)} for i in range(26)])]), case(), 1
        )
        self.assertEqual(row["reason"], "PAGE_EXCEEDS_REQUESTED_SIZE")

    def test_transport_overflow_is_buffered_like_the_visible_grid_page(self):
        prefix = [{"id": str(i)} for i in range(2)]
        full = [{"id": str(i)} for i in range(2, 27)]
        first = page(
            prefix,
            query_complete=False,
            query_status="degraded",
            has_more=True,
            next_cursor="next",
        )
        client = FakeClient([first, page(full)])
        row = replay.run_case(client, case(), 1, poll_seconds=0)
        self.assertEqual(row["status"], "COMPLETE_UNVERIFIED")
        self.assertEqual(row["rows"], 25)
        self.assertEqual(row["buffered_overflow_rows"], 2)
        self.assertFalse(row["population_exhausted"])
        self.assertEqual(row["result_sha256"], replay.digest(prefix + full[:23]))
        self.assertEqual(
            client.calls[0][2]["page_size"], client.calls[1][2]["page_size"]
        )

    def test_pending_graph_polls_same_body_then_finishes(self):
        graph = {
            "query_complete": True,
            "query_status": "complete",
            "metric_name": "latency",
            "data": [],
        }
        client = FakeClient(
            [{"query_complete": False, "query_status": "pending"}, graph]
        )
        row = replay.run_case(client, case("trace_graph"), 1, poll_seconds=0)
        self.assertEqual(row["status"], "COMPLETE_UNVERIFIED")
        self.assertEqual(client.calls[0][3], client.calls[1][3])
        self.assertEqual(client.calls[0][4], client.calls[1][4])

    def test_dashboard_child_incomplete_is_not_complete(self):
        response = {
            "query_complete": True,
            "query_status": "complete",
            "metrics": [{"id": "m1", "query_complete": False, "series": []}],
        }
        row = replay.run_case(FakeClient([response]), case("dashboard_filter"), 1)
        self.assertEqual(row["reason"], "DASHBOARD_METRIC_INCOMPLETE")

    def test_wrong_response_shape_fails_without_dumping_customer_data(self):
        row = replay.run_case(
            FakeClient([{"table": "customer secret", "metadata": []}]), case(), 1
        )
        self.assertEqual(row["status"], "FAIL")
        self.assertNotIn("customer secret", replay.canonical(row))

    def test_summary_keeps_not_run_and_unverified_distinct(self):
        plan = fixture_plan()
        row = replay.run_case(FakeClient([page()]), plan["cases"][0], 1)
        summary = replay.summarize(plan, [row])
        self.assertEqual(summary["qualification"], "NOT_QUALIFIED")
        self.assertEqual(summary["attempted"], 1)
        self.assertGreater(summary["not_run"], 1)
        self.assertFalse(summary["ui_e2e_verified"])


class HttpTests(unittest.TestCase):
    def test_real_http_serialization_auth_scope_build_and_api_prefix(self):
        with server([{"result": page()}]) as (url, seen):
            client = replay.Client(
                url, SCOPE, "Bearer local-test-token", "X-Test-Build", "candidate-sha"
            )
            row = replay.run_case(client, case(), 1)
        self.assertEqual(row["status"], "COMPLETE_UNVERIFIED")
        self.assertTrue(row["candidate_verified"])
        req = seen[0]
        self.assertTrue(
            req["path"].startswith("/api/v1/tracer/trace/list_traces_of_session/")
        )
        self.assertEqual(req["headers"]["Authorization"], "Bearer local-test-token")
        self.assertEqual(req["headers"]["X-Workspace-Id"], SCOPE["workspace_id"])
        filters = json.loads(parse_qs(urlsplit(req["path"]).query)["filters"][0])
        self.assertEqual(filters[1]["column_id"], "company_id")
        self.assertNotIn("local-test-token", replay.canonical(row))

    def test_actual_post_only_read_graph_endpoint(self):
        with server(
            [
                {
                    "result": {
                        "data": [],
                        "query_complete": True,
                        "query_status": "complete",
                    }
                }
            ]
        ) as (url, seen):
            row = replay.run_case(
                replay.Client(url, SCOPE, "Bearer test"), case("trace_graph"), 1
            )
        self.assertEqual(row["status"], "COMPLETE_UNVERIFIED")
        self.assertEqual(seen[0]["method"], "POST")
        self.assertEqual(seen[0]["body"]["project_id"], SCOPE["project_id"])

    def test_redirect_and_mutation_and_remote_plain_http_refused(self):
        for url in (
            "http://example.com",
            "https://user:password@example.com",
            "https://example.com?token=x",
        ):
            with self.assertRaises(replay.ReplayError):
                replay.Client(url, SCOPE, "Bearer test")
        with server([{"status": 302}]) as (url, seen):
            client = replay.Client(url, SCOPE, "Bearer test")
            self.assertEqual(
                replay.run_case(client, case(), 1)["reason"], "REDIRECT_REFUSED"
            )
            with self.assertRaises(replay.ReplayError):
                client.request(
                    "POST", "/tracer/task/create/", {}, {}, time.monotonic() + 1
                )
            self.assertEqual(len(seen), 1)

    def test_build_mismatch_and_http_failures(self):
        for spec, reason in (
            ({"build": "old-build"}, "CANDIDATE_BUILD_MISMATCH_OR_MISSING"),
            ({"status": 503}, "HTTP_503"),
            ({"content_type": "text/html"}, "NON_JSON_RESPONSE"),
        ):
            with server([spec]) as (url, _):
                client = replay.Client(
                    url, SCOPE, "Bearer test", "X-Test-Build", "candidate-sha"
                )
                self.assertEqual(replay.run_case(client, case(), 1)["reason"], reason)

    def test_actual_http_wall_deadline(self):
        with server([{"delay": 0.2}]) as (url, _):
            row = replay.run_case(
                replay.Client(url, SCOPE, "Bearer test"), case(), 0.04
            )
        self.assertEqual(row["reason"], "ACTION_DEADLINE")
        self.assertEqual(row["status"], "SAFETY_STOP")
        self.assertLess(row["elapsed_ms"], 200)

    def test_slow_correct_completion_is_not_aborted_at_latency_target(self):
        item = case(target_ms=5)
        oracle = {"result_sha256": replay.digest([]), "provenance": "offline fixture"}
        with server([{"delay": 0.03, "result": page()}]) as (url, _):
            client = replay.Client(
                url, SCOPE, "Bearer test", "X-Test-Build", "candidate-sha"
            )
            row = replay.run_case(client, item, 1, oracle)
        self.assertEqual(row["status"], "API_PASS")
        self.assertEqual(row["correctness"], "ORACLE_MATCH")
        self.assertGreater(row["elapsed_ms"], item["target_ms"])
        self.assertFalse(row["latency_met"])
        self.assertEqual(row["performance"], "TARGET_NOT_MET")

    def test_ledger_private_and_resumable_target_bound(self):
        with (
            tempfile.TemporaryDirectory() as root,
            server([{"result": page()}]) as (url, seen),
        ):
            plan_path = str(Path(root) / "plan.json")
            ledger = str(Path(root) / "results.jsonl")
            replay.private_write(plan_path, fixture_plan())
            args = [
                "run",
                "--base-url",
                url,
                "--plan",
                plan_path,
                "--ledger",
                ledger,
                "--candidate-label",
                "unit-test-only",
                "--max-cases",
                "1",
                "--delay-seconds",
                ".1",
            ]
            with (
                mock.patch.dict(
                    os.environ, {"OBSERVE_REPLAY_AUTHORIZATION": "Bearer test"}
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                replay.main(args + ["--summary", str(Path(root) / "summary1.json")])
                replay.main(args + ["--summary", str(Path(root) / "summary2.json")])
            rows = [json.loads(line) for line in Path(ledger).read_text().splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertNotEqual(rows[0]["case_id"], rows[1]["case_id"])
            self.assertEqual(len(seen), 2)
            self.assertEqual(Path(ledger).stat().st_mode & 0o777, 0o600)
            with self.assertRaises(replay.ReplayError):
                replay.load_ledger(ledger, "different-target")

    def test_catalog_discovery_records_missing_seed_coverage(self):
        activation = {
            "catalog_epoch": 1,
            "catalog_revision": 3,
            "activation_fingerprint": "f" * 64,
        }
        catalog = {
            **activation,
            "query_complete": True,
            "query_exact": True,
            "has_more": False,
            "metrics": [
                {
                    "name": "field",
                    "property_id": "custom_attribute:field",
                    "type": "number",
                    "attribute_types": ["number"],
                }
            ],
        }
        values = {
            **activation,
            "query_complete": True,
            "has_more": True,
            "values": [{"type": "number", "value": 4}],
        }
        with server([{"result": catalog}, {"result": values}]) as (url, seen):
            manifest = replay.discover(
                replay.Client(url, SCOPE, "Bearer test"), SCOPE, 1
            )
        self.assertTrue(manifest["catalog_complete"])
        self.assertFalse(manifest["attributes"][0]["values_complete"])
        self.assertEqual(
            manifest["attributes"][0]["seeds"], [{"type": "number", "value": 4}]
        )
        self.assertEqual(len(seen), 2)

    def test_catalog_epoch_drift_is_not_mixed_into_manifest(self):
        common = {
            "catalog_epoch": 1,
            "catalog_revision": 3,
            "activation_fingerprint": "f" * 64,
            "query_complete": True,
            "query_exact": True,
            "metrics": [],
            "has_more": True,
            "next_cursor": "next",
        }
        with server(
            [
                {"result": common},
                {"result": {**common, "catalog_epoch": 2, "has_more": False}},
            ]
        ) as (url, _):
            with self.assertRaisesRegex(replay.ReplayError, "ACTIVATION_CHANGED"):
                replay.discover(replay.Client(url, SCOPE, "Bearer test"), SCOPE, 1)


if __name__ == "__main__":
    unittest.main()
