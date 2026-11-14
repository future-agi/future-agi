"""Timing-only gates: fake clock/import/client, no Django setup or DB/socket."""
import builtins
from contextlib import ExitStack
import json
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import observe_candidate_hydration as hydration
import replay_observe_queries_readonly as q


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target in ("socket.socket", "socket.create_connection", "subprocess.Popen"):
            self.stack.enter_context(patch(target, side_effect=AssertionError("external IO forbidden")))
        self.now = 100.0
        self.stack.enter_context(patch.object(q.time, "monotonic", side_effect=lambda: self.now))
        self.rows = [{"project_id": "p", "trace_id": "t", "input": "unchanged"}]
        self.builder = SimpleNamespace(
            content_root_identities_for_rows=lambda rows: [("p", "t")],
            build_content_query=lambda *a, **k: ("SELECT content", {}),
            build_span_attributes_query=lambda *a, **k: ("SELECT attributes", {}),
            content_root_rows_match=lambda rows, content: rows == content,
        )
        self.view = ModuleType("tracer.views.trace")
        self.view._decode_projected_trace_attribute_value = json.loads
        merge = ModuleType("tracer.services.clickhouse.v2.span_selectors")
        merge.merge_content_rows = lambda rows, content, **kw: None
        self.stack.enter_context(patch.dict(q.sys.modules, {merge.__name__: merge}))
        self.stack.enter_context(patch.object(q, "normalize_filters", return_value=[]))

    def advance(self, seconds):
        self.now += seconds

    def run_candidate(self, *, warm=False, import_error=False, timed=True):
        args = SimpleNamespace(safety_seconds=60, verify_trace_ids=False)
        adapter = q.CandidateQueries(args, {"scope": {}}, ["p"])
        readers, imports = [], []

        def factory(args, projects, deadline, *, mode):
            self.advance(.25)
            reader = SimpleNamespace(deadline=deadline, calls=[])

            def execute(sql, params):
                self.advance(.2)
                reader.calls.append({"elapsed_ms": 200.0, "server_elapsed_ms": 100.0})
                return SimpleNamespace(data=self.rows if sql == "SELECT content" else [
                    {"project_id": "p", "trace_id": "t", "attribute_key": "flag",
                     "attribute_value_json": "false"},
                ])

            reader.execute_ch_query = execute
            reader.close = Mock(side_effect=lambda: self.advance(.05))
            readers.append(reader)
            return reader

        original_import = builtins.__import__

        def importing(name, *args, **kwargs):
            if name == "tracer.views.trace":
                imports.append(name)
                if not warm:
                    self.advance(5.4)
                if import_error:
                    raise ImportError("private import error must not be logged")
                return self.view
            return original_import(name, *args, **kwargs)

        def entity(reader, case, filters):
            return hydration.hydrate_page(reader, self.builder,
                {"query_complete": True, "query_exact": True,
                 "ordering_exact": True, "table": self.rows},
                entity="traces", request={"params": {"attribute_keys": '["flag"]'}},
                timing=reader.local_phase if timed else None)

        with patch.dict(q.sys.modules):
            q.sys.modules.pop("tracer.views.trace", None)
            if warm:
                q.sys.modules["tracer.views.trace"] = self.view
            with (patch.object(q, "ReadOnlyExecutor", side_effect=factory),
                  patch.object(adapter, "entity_list", side_effect=entity),
                  patch.object(builtins, "__import__", side_effect=importing)):
                row = adapter.run({"id": "case", "surface": "traces", "period": "7D",
                    "target_ms": 5000, "blocked": None, "request": {}})
        return row, readers[0], imports

    def test_cold_import_stays_inside_total_and_does_not_earn_pass(self):
        row, reader, imports = self.run_candidate()
        self.assertEqual(row["elapsed_ms"], 6100.0)  # init + import + two reads + close
        self.assertFalse(row["latency_met"])
        self.assertEqual(imports, ["tracer.views.trace"])
        phases = row["local_timing_phases"]
        self.assertEqual([p["name"] for p in phases], ["reader_initialization", "trace_view_lazy_import"])
        self.assertEqual([p["elapsed_ms"] for p in phases], [250.0, 5400.0])
        self.assertEqual((phases[1]["start_ms"], phases[1]["end_ms"]), (250.0, 5650.0))
        self.assertFalse(phases[1]["module_already_loaded"])
        self.assertTrue(all(p["completed"] for p in phases))
        self.assertEqual(sum(c["elapsed_ms"] for c in reader.calls), 400.0)
        self.assertEqual(row["correctness"], "UNVERIFIED")
        self.assertFalse(row["http_e2e"] or row["ui_e2e"])
        self.assertFalse(row["query_layer_coverage"]["full_public_request"])
        self.assertEqual(row["query_layer_coverage"]["execution"], "serial_read_only_not_public_parallel_scheduling")

    def test_cached_import_is_observed_in_place_not_skipped(self):
        cold, _, _ = self.run_candidate()
        warm, _, imports = self.run_candidate(warm=True)
        self.assertEqual(imports, ["tracer.views.trace"])
        self.assertEqual(warm["elapsed_ms"], 700.0)
        self.assertEqual(warm["local_timing_phases"][1]["elapsed_ms"], 0.0)
        self.assertTrue(warm["local_timing_phases"][1]["module_already_loaded"])
        for key in ("result_sha256", "query_layer_coverage", "hydration_phases", "correctness"):
            self.assertEqual(cold[key], warm[key])

    def test_failed_import_records_duration_and_preserves_error(self):
        row, reader, _ = self.run_candidate(import_error=True)
        self.assertEqual(row["elapsed_ms"], 5700.0)
        self.assertEqual(row["exception_class"], "ImportError")
        self.assertEqual(row["status"], "ERROR")
        self.assertFalse(row["complete"] or row["latency_met"])
        self.assertFalse(row["local_timing_phases"][1]["completed"])
        self.assertEqual(row["local_timing_phases"][1]["elapsed_ms"], 5400.0)
        self.assertEqual(reader.calls, [])
        self.assertNotIn("private import error", json.dumps(row))
        reader.close.assert_called_once()

    def test_optional_callback_changes_neither_payload_nor_total(self):
        timed, _, _ = self.run_candidate()
        untimed, _, _ = self.run_candidate(timed=False)
        for key in ("elapsed_ms", "latency_met", "result_sha256", "query_layer_coverage", "hydration_phases"):
            self.assertEqual(timed[key], untimed[key])
        self.assertEqual(len(untimed["local_timing_phases"]), 1)

    def test_real_executor_offsets_retain_existing_client_and_server_boundaries(self):
        class DriverError(Exception):
            code = 999

        error_module = ModuleType("clickhouse_driver.errors")
        error_module.Error = DriverError
        service = ModuleType("tracer.services.clickhouse.query_service")
        service.QueryResult = SimpleNamespace(from_clickhouse_rows=lambda *a: self.advance(.03))
        budget = ModuleType("tracer.services.clickhouse.read_budget")
        budget.ReadDeadlineExceeded = RuntimeError
        policy = ModuleType("tracer.services.clickhouse.application_read_policy")
        policy.application_read_settings = lambda settings: settings
        modules = {m.__name__: m for m in (error_module, service, budget, policy)}
        for fails in (False, True):
            with self.subTest(fails=fails), patch.dict(q.sys.modules, modules):
                self.now = 102.0
                reader = object.__new__(q.ReadOnlyExecutor)
                reader._users_certificate = reader._users_context = None
                reader._users_origin_expected = False
                reader.projects, reader.mode, reader.calls = ["p"], "candidate", []
                reader.prefix, reader.args, reader.deadline = "test", None, 160.0
                reader.timing_origin = 100.0

                def execute(sql, params, **kwargs):
                    self.advance(.2)
                    if fails:
                        raise DriverError()
                    return [(1,)], [("value", "UInt8")]

                reader.client = SimpleNamespace(execute=Mock(side_effect=execute),
                    last_query=SimpleNamespace(progress=SimpleNamespace(rows=1, bytes=8, elapsed_ns=100000000)))
                with (patch.object(q, "validate_select", side_effect=lambda query, *a: query),
                      patch.object(q, "diagnostic_read_settings", return_value={"unchanged": 1})):
                    if fails:
                        with self.assertRaises(DriverError):
                            reader.execute_ch_query("SELECT 1", {})
                    else:
                        reader.execute_ch_query("SELECT 1", {})
                record = reader.calls[0]
                self.assertEqual(record["elapsed_ms"], 200.0)
                self.assertEqual((record["candidate_client_start_ms"], record["candidate_client_end_ms"]), (2000.0, 2200.0))
                self.assertEqual(record["limits"], {"unchanged": 1})
                reader.client.execute.assert_called_once()
                if not fails:
                    self.assertEqual(record["server_elapsed_ms"], 100.0)
                    self.assertAlmostEqual(self.now, 102.23)  # conversion stays outside old execute timer


if __name__ == "__main__":
    unittest.main()
