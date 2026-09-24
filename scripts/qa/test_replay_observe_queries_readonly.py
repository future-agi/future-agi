"""Offline guards only. Never connect to a DB or initialize Django in tests."""

import unittest
import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
import time

import replay_observe_filters as replay
import replay_observe_queries_readonly as queries
from discover_observe_query_inputs import decode_seed


class DashboardOutcomeTests(unittest.TestCase):
    def test_dashboard_uses_public_prepared_groups_and_keeps_statement_layout(self):
        layouts = [((0, 3), (1, 4), (2,)), ((0, 1, 2, 3, 4),),
                   ((0,), (1,), (2,), (3,), (4,))]
        for layout in layouts:
            with self.subTest(layout=layout):
                metrics = [{"id": "same-display-id"} for _ in range(5)]
                prepared = tuple((m, f"single-{i}", {"bind": i}) for i, m in enumerate(metrics))
                groups, raw, normalized = [], [], [None] * 5
                for position, indices in enumerate(layout):
                    group = SimpleNamespace(sql=f"group-{position}", params={"indices": indices},
                        value_columns=tuple(f"v_{i}" for i in indices), indices=indices) if len(indices) > 1 else None
                    groups.append((indices, group))
                    raw.append([{"time_bucket": "day", **{f"v_{i}": i for i in indices}}])
                    for i in indices:
                        normalized[i] = [{"time_bucket": "day", "value": i}] if group else raw[-1]
                builder = SimpleNamespace(metrics=metrics,
                    group_prepared_metric_queries=Mock(return_value=groups),
                    build_compatible_metric_group_query=Mock(side_effect=AssertionError("old API")),
                    metric_group_results=Mock(side_effect=lambda g, rows: (
                        True, [(None, [{"time_bucket": r["time_bucket"], "value": r[f"v_{i}"]} for r in rows])
                               for i in g.indices])))
                serializer = Mock(validated_data={"project_ids": ["project"], "metrics": metrics})
                prepare = Mock(return_value=prepared)
                modules = {
                    "tracer.serializers.dashboard": SimpleNamespace(DashboardQuerySerializer=Mock(return_value=serializer)),
                    "tracer.views.dashboard": SimpleNamespace(
                        _normalize_dashboard_query_filters=lambda body: dict(body),
                        DashboardViewSet=SimpleNamespace(_prepare_metric_queries=prepare)),
                    "tracer.services.clickhouse.v2.query_builders.dashboard": SimpleNamespace(
                        DashboardQueryBuilderV2=Mock(return_value=builder)),
                }
                reader = SimpleNamespace(execute_ch_query=Mock(
                    side_effect=[SimpleNamespace(data=rows) for rows in raw]))
                adapter = queries.CandidateQueries(SimpleNamespace(),
                    {"scope": {"organization_id": "org", "workspace_id": "workspace"}}, ["project"])
                with patch.dict(sys.modules, modules):
                    payload = adapter.dashboard(reader, {"request": {"body": {"unchanged": True}}})
                    self.assertEqual(payload["data"], raw)
                    self.assertEqual(payload["dashboard_metric_rows"], normalized)
                    self.assertEqual(payload["dashboard_value_columns"],
                        groups[0][1].value_columns if len(groups) == 1 else ("value",))
                    self.assertTrue(payload["query_complete"] and payload["query_exact"])
                    self.assertTrue(payload["query_layer_only"])
                    prepare.assert_called_once_with(builder)
                    builder.group_prepared_metric_queries.assert_called_once_with(prepared)
                    builder.build_compatible_metric_group_query.assert_not_called()
                    self.assertEqual([call.args for call in reader.execute_ch_query.call_args_list],
                        [(g.sql, g.params) if g else prepared[indices[0]][1:] for indices, g in groups])
                    self.assertTrue(all(not call.kwargs for call in reader.execute_ch_query.call_args_list))
                    self.assertEqual(builder.metric_group_results.call_count,
                                     sum(group is not None for _, group in groups))
                    reader.execute_ch_query.reset_mock(side_effect=True)
                    reader.execute_ch_query.side_effect = TimeoutError("guard-stop")
                    with self.assertRaisesRegex(TimeoutError, "guard-stop"):
                        adapter.dashboard(reader, {"request": {"body": {}}})
                    reader.execute_ch_query.assert_called_once()

    def test_dashboard_counts_aggregate_rows_separately_from_statements(self):
        for results in ([[]], [[{"value": i} for i in range(257)]], [[{}], [{}, {}]]):
            with self.subTest(statement_lengths=list(map(len, results))):
                adapter = queries.CandidateQueries(
                    SimpleNamespace(safety_seconds=60, verify_trace_ids=False),
                    {"scope": {"project_id": "project"}}, ["project"],
                )
                reader = SimpleNamespace(
                    close=lambda: None, calls=[], deadline=time.monotonic() + 60
                )
                with (
                    patch.object(queries, "ReadOnlyExecutor", return_value=reader),
                    patch.object(queries, "normalize_filters", return_value=[]),
                    patch.object(adapter, "dashboard", return_value={
                        "query_complete": True, "query_exact": True,
                        "query_status": "complete", "data": results,
                    }),
                ):
                    row = adapter.run({
                        "id": "dashboard", "surface": "dashboard_metric",
                        "period": "7D", "target_ms": 15000,
                        "blocked": None, "request": {},
                    })
                self.assertEqual(row["result_rows"], sum(map(len, results)))
                self.assertEqual(row["statement_count"], len(results))
                self.assertEqual(row["status"], "COMPLETE_UNVERIFIED")
                self.assertEqual(row["correctness"], "UNVERIFIED")
                self.assertEqual(row["result_sha256"], replay.digest(results))

    def _run_dashboard_reference(self, *, enabled=True, outcome="match", complete=True, exact=True):
        from test_observe_dashboard_reference import PROJECT, config
        body = config(("avg", "min", "max", "p25", "p50"))
        if outcome == "unsupported":
            body["metrics"][0]["type"] = "system_metric"
        columns = tuple(f"dashboard_metric_value_{i}" for i in range(5))
        grain = {"time_bucket": "2026-09-04", "breakdown_value": "series"}
        actual = [{**grain, **dict(zip(columns, (3.25, 0, 8, 1, 4), strict=True))}]
        expected = [{**grain, **{f"ref_value_{i}": v for i, v in enumerate((3.25, 0, 8, 1, 4))}}]
        if outcome == "mismatch":
            expected[0]["ref_value_4"] = 8
        args = SimpleNamespace(safety_seconds=60, verify_trace_ids=False)
        if enabled is not None:
            args.verify_dashboard_aggregates = enabled
        adapter = queries.CandidateQueries(args, {"scope": {"project_id": PROJECT}}, [PROJECT])
        clock, readers = [100.0], []

        def factory(args, projects, deadline, *, mode):
            reader = SimpleNamespace(mode=mode, deadline=deadline, calls=[], close=Mock())
            if outcome == "close" and mode == "reference_diagnostic":
                reader.close.side_effect = RuntimeError("private-driver-detail-must-not-appear")

            def execute(sql, params, **kwargs):
                self.assertTrue(readers[0].close.called)
                self.assertEqual(mode, "reference_diagnostic")
                self.assertIn("groupArray(tuple(", sql)
                self.assertEqual(params["project_ids"], [PROJECT])
                self.assertEqual(kwargs, {"settings": {}})
                clock[0] += 20
                reader.calls.append({"read_policy_mode": mode})
                if outcome == "error":
                    raise TimeoutError("private-driver-detail-must-not-appear")
                return SimpleNamespace(data=expected)

            reader.execute_ch_query = Mock(side_effect=execute)
            readers.append(reader)
            return reader

        def candidate(*_):
            clock[0] += 2
            return {"query_complete": complete, "query_exact": exact,
                    "query_status": "complete", "data": [actual] * (2 if outcome == "layout" else 1),
                    "dashboard_value_columns": columns}

        with (patch.object(queries, "ReadOnlyExecutor", side_effect=factory),
              patch.object(queries.time, "monotonic", side_effect=lambda: clock[0]),
              patch.object(queries, "normalize_filters", return_value=[]),
              patch.object(adapter, "dashboard", side_effect=candidate)):
            row = adapter.run({"id": "dashboard", "surface": "dashboard_metric", "period": "7D",
                "target_ms": 15000, "blocked": None, "request": {"body": body}})
        return row, readers

    def test_dashboard_reference_default_off_and_incomplete_or_inexact_gate(self):
        for options in ({"enabled": None}, {"enabled": False}, {"complete": False}, {"exact": False}):
            with self.subTest(options=options), patch("observe_dashboard_reference.build_dashboard_reference_query") as build:
                row, readers = self._run_dashboard_reference(**options)
                self.assertNotIn("independent_reference", row)
                self.assertEqual(len(readers), 1)
                build.assert_not_called()

    def test_dashboard_reference_match_mismatch_and_errors_do_not_change_candidate_timing(self):
        for outcome in ("match", "mismatch", "unsupported", "error", "layout", "close"):
            with self.subTest(outcome=outcome):
                row, readers = self._run_dashboard_reference(outcome=outcome)
                evidence = row["independent_reference"]
                self.assertEqual(evidence["status"], {"match": "AGGREGATE_MATCH", "mismatch": "AGGREGATE_MISMATCH"}.get(outcome, "UNVERIFIED"))
                self.assertEqual(row["elapsed_ms"], 2000)
                self.assertTrue(row["complete"] and row["latency_met"])
                self.assertEqual(row["status"], "COMPLETE_UNVERIFIED")
                self.assertEqual(row["correctness"], "UNVERIFIED")
                self.assertEqual(row["statement_count"], 2 if outcome == "layout" else 1)
                self.assertFalse(row["http_e2e"] or row["ui_e2e"] or evidence["qualification"])
                self.assertFalse(evidence["same_transaction_snapshot"])
                self.assertTrue(all(reader.close.called for reader in readers))
                self.assertEqual(len(readers), 1 if outcome in {"unsupported", "layout"} else 2)
                if len(readers) == 2:
                    self.assertEqual(evidence["elapsed_ms"], 20000)
                    self.assertEqual(evidence["queries"], readers[1].calls)
                    self.assertEqual(row["queries"], readers[0].calls)
                self.assertEqual(evidence["full_row_metrics_verified"], outcome == "match")
                if outcome == "mismatch":
                    self.assertEqual(evidence["different_cells"], 1)
                if outcome in {"unsupported", "layout"}:
                    self.assertIn("DASHBOARD_REFERENCE_UNSUPPORTED", evidence["reason"])
                self.assertNotIn("private-driver-detail", replay.canonical(row))

    def test_dashboard_reference_cli_flag_is_explicit_default_off(self):
        with patch.object(queries.argparse.ArgumentParser, "parse_args", autospec=True,
                          side_effect=RuntimeError("stop-before-any-io")) as parse:
            with self.assertRaisesRegex(RuntimeError, "stop-before-any-io"):
                queries.main()
        parser = parse.call_args.args[0]
        self.assertIs(parser.get_default("verify_dashboard_aggregates"), False)
        action = next(a for a in parser._actions if a.dest == "verify_dashboard_aggregates")
        self.assertEqual(action.option_strings, ["--verify-dashboard-aggregates"])
        self.assertIs(action.const, True)


class UsersRemapCertificateTests(unittest.TestCase):
    def test_cli_default_off(self):
        with patch.object(queries.argparse.ArgumentParser, "parse_args", autospec=True,
                          side_effect=RuntimeError("stop-before-io")) as parse:
            with self.assertRaisesRegex(RuntimeError, "stop-before-io"):
                queries.main()
        parser = parse.call_args.args[0]
        self.assertIs(parser.get_default("verify_finite_users_remap"), False)
        action = next(a for a in parser._actions if a.dest == "verify_finite_users_remap")
        self.assertEqual(action.option_strings, ["--verify-finite-users-remap"])
        self.assertIs(action.const, True)

    def test_adapter_never_configures_by_default_and_enabled_failure_precedes_read(self):
        for enabled in (None, False, True):
            with self.subTest(enabled=enabled):
                args = SimpleNamespace()
                if enabled is not None:
                    args.verify_finite_users_remap = enabled
                plan = {"scope": {"organization_id": "org", "project_id": "project"}}
                if enabled:
                    plan["plan_id"] = "plan"
                manager = Mock()
                manager.list_cursor_payload.return_value.payload = {"ordinary": True}
                users = SimpleNamespace(UsersListManager=Mock(return_value=manager),
                                        V2AnalyticsQueryService=Mock())
                reader = Mock()
                reader._configure_users_remap.side_effect = replay.ReplayError("unsupported")
                case = {"surface": "users_project", "request": {
                    "target_rows": 25, "params": {"requested_columns": "[]"}}}
                adapter = queries.CandidateQueries(args, plan, ["project"])
                with patch.dict(sys.modules, {"tracer.services": SimpleNamespace(users_list_manager=users)}):
                    if enabled:
                        with self.assertRaisesRegex(replay.ReplayError, "unsupported"):
                            adapter.users(reader, case, [])
                        reader._configure_users_remap.assert_called_once_with(manager, case, plan["scope"], "plan")
                        manager.list_cursor_payload.assert_not_called()
                    else:
                        self.assertEqual(adapter.users(reader, case, []), {"ordinary": True})
                        reader._configure_users_remap.assert_not_called()
                        manager.list_cursor_payload.assert_called_once_with(page_size=25)

    def test_finite_origin_identity_and_untruncated_closure_validation(self):
        from uuid import UUID
        project, user, foreign = (str(UUID(int=i)) for i in (1, 2, 3))
        reader = object.__new__(queries.ReadOnlyExecutor)
        reader.client, reader._users_certificate = object(), None
        reader._users_context = queries._UsersRemapContext((project,), (project,), "bindings", "scope")
        origin = SimpleNamespace(row_count=1, columns=["end_user_id", "project_id"],
                                 data=[{"end_user_id": user, "project_id": project}])
        reader._users_result(origin, origin=True, certificate=None, query_id="actual-origin")
        certificate = reader._users_certificate
        self.assertEqual(certificate.ids, (user,))
        origin.data[0]["end_user_id"] = foreign
        self.assertEqual(certificate.ids, (user,))
        closure = SimpleNamespace(columns=["any_id", "survivor_id"], data=[
            {"any_id": str(UUID(int=i + 100)), "survivor_id": user} for i in range(40)])
        reader._users_result(closure, origin=False, certificate=certificate, query_id="remap")
        self.assertEqual(len(closure.data), 40)
        closure.data[-1]["survivor_id"] = foreign
        with self.assertRaisesRegex(replay.ReplayError, "USERS_REMAP_RESULT_INVALID"):
            reader._users_result(closure, origin=False, certificate=certificate, query_id="remap")
        origin.data[0]["project_id"] = foreign
        with self.assertRaisesRegex(replay.ReplayError, "USERS_REMAP_ORIGIN_RESULT_INVALID"):
            reader._users_result(origin, origin=True, certificate=None, query_id="bad-origin")


class PreviewReferenceGateTests(unittest.TestCase):
    def test_scalar_reference_accepts_previews_but_not_unscoped_user_detail(self):
        import observe_trace_id_reference as reference

        for surface in ("traces", "task_traces", "eval_traces"):
            with (
                self.subTest(surface=surface),
                patch.object(reference, "membership_having", return_value=("1", {})),
                patch.object(reference, "complete_scalar_witness_reference", return_value=None),
                patch.object(reference, "complete_text_witness_reference", return_value=None),
                patch.object(reference, "numeric_absence_proof", return_value=True),
            ):
                ids, info = reference.reference_ids(None, {
                    "surface": surface, "request": {"target_rows": 1},
                    "window": {"start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"},
                }, {"project_id": "project"}, [])
                self.assertEqual(ids, [])
                self.assertTrue(info["population_exhausted"])
        with self.assertRaisesRegex(replay.ReplayError, "REFERENCE_SURFACE_NOT_SUPPORTED"):
            reference.reference_ids(None, {"surface": "user_traces"}, {}, [])

    def test_trace_preview_workloads_run_independent_check_at_requested_size(self):
        import observe_trace_id_reference as reference

        for surface, size in (("traces", 25), ("task_traces", 1), ("eval_traces", 50)):
            with self.subTest(surface=surface):
                adapter = queries.CandidateQueries(
                    SimpleNamespace(safety_seconds=60, verify_trace_ids=True),
                    {"scope": {"project_id": "project"}}, ["project"],
                )
                case = {
                    "id": surface, "surface": surface, "period": "7D",
                    "target_ms": 5000, "blocked": None,
                    "request": {"target_rows": size},
                }
                reader = SimpleNamespace(close=lambda: None, calls=[], deadline=float("inf"))
                with (
                    patch.object(queries, "ReadOnlyExecutor", return_value=reader),
                    patch.object(queries, "normalize_filters", return_value=[]),
                    patch.object(adapter, "entity_list", return_value={
                        "query_complete": True, "query_exact": True,
                        "table": [{"trace_id": "matching-trace"}],
                    }),
                    patch.object(reference, "reference_ids", return_value=(
                        ["matching-trace"], {"population_exhausted": True},
                    )) as oracle,
                ):
                    row = adapter.run(case)
                oracle.assert_called_once_with(reader, case, adapter.plan["scope"], [])
                self.assertEqual(oracle.call_args.args[1]["request"]["target_rows"], size)
                self.assertEqual(row["independent_reference"]["status"], "ID_ORDER_MATCH")
                self.assertFalse(row["independent_reference"]["full_row_metrics_verified"])
                self.assertEqual(row["correctness"], "UNVERIFIED")


class ContinuationTests(unittest.TestCase):
    def test_preview_workload_rejects_old_one_row_eval_before_io(self):
        for component, size in (("task", 1), ("eval", 50)):
            for entity in ("spans", "traces", "sessions"):
                surface = f"{component}_{entity}"
                case = {
                    "surface": surface,
                    "request": {
                        "method": "GET",
                        "path": replay.LISTS[surface],
                        "params": {"page_size": size, "cursor_mode": True},
                        "target_rows": size,
                    },
                }
                queries.validate_preview_workload(case)
                case["request"]["target_rows"] = 1 if component == "eval" else 50
                with self.assertRaisesRegex(
                    replay.ReplayError, "PREVIEW_WORKLOAD_MISMATCH"
                ):
                    queries.validate_preview_workload(case)

    def page(self, ids, complete=False, checkpoint=None):
        return SimpleNamespace(
            rows=[{"id": i, "start_time": datetime(2026, 9, 1)} for i in ids],
            complete=complete,
            has_more=not complete,
            attempts=[],
            error_code=None if complete else "scan_budget_exceeded",
            continuation_slice_start=None,
            continuation_slice_end=checkpoint,
            continuation_before_start_time=None,
            continuation_before_id=None,
        )

    def test_resume_checkpoint_without_changing_requested_transport_size(self):
        seen = []
        pages = iter(
            [
                self.page(["a"], checkpoint=datetime(2026, 9, 2)),
                self.page(["b", "c"], complete=True),
            ]
        )

        def read(state):
            seen.append(dict(state))
            return next(pages)

        result = queries.collect_selector_page(
            read, SimpleNamespace(), "id", 2, lambda: 60000
        )
        self.assertTrue(result["query_complete"])
        self.assertEqual([r["id"] for r in result["table"]], ["a", "b"])
        self.assertEqual(result["buffered_overflow_rows"], 1)
        self.assertEqual(seen[1]["cursor_order_token"], "a")
        self.assertEqual(seen[1]["continuation_slice_end"], datetime(2026, 9, 2))

    def test_no_checkpoint_does_not_retry_or_claim_empty(self):
        result = queries.collect_selector_page(
            lambda _: self.page([]), SimpleNamespace(), "id", 2, lambda: 60000
        )
        self.assertFalse(result["query_complete"])
        self.assertEqual(result["transport_pages"], 1)

    def test_repeated_empty_checkpoint_is_rejected(self):
        with self.assertRaisesRegex(replay.ReplayError, "CHECKPOINT_DID_NOT_ADVANCE"):
            queries.collect_selector_page(
                lambda _: self.page([], checkpoint=datetime(2026, 9, 2)),
                SimpleNamespace(),
                "id",
                2,
                lambda: 60000,
            )

    def test_type_preservation_for_catalog_inputs(self):
        self.assertEqual(
            decode_seed("string", '"00012"'), {"type": "string", "value": "00012"}
        )
        self.assertEqual(decode_seed("number", "12"), {"type": "number", "value": 12})
        self.assertEqual(decode_seed("array", '"0012"')["value"], ["0012"])
        self.assertEqual(decode_seed("array", "true")["value"], [True])
        for kind, raw in (
            ("number", "true"),
            ("number", '"12"'),
            ("boolean", "1"),
            ("number", "NaN"),
        ):
            with (
                self.subTest(kind=kind, raw=raw),
                self.assertRaises((replay.ReplayError, ValueError)),
            ):
                decode_seed(kind, raw)


class ReadOnlyGuardTests(unittest.TestCase):
    def test_runtime_profile_binds_eval_source_without_exposing_other_strings(self):
        profiles = []
        for table in ("tracer_eval_logger", "tracer_eval_logger_v2"):
            settings = SimpleNamespace(
                CH25_EVAL_LOGGER_TABLE=table,
                CLICKHOUSE_APPLICATION_READ_MAX_MEMORY_USAGE=36 * 1024**3,
                CH25_PASSWORD="private-value-must-not-appear",
                DASHBOARD_SECRET="private-value-must-not-appear",
            )
            with patch.dict(
                sys.modules, {"django.conf": SimpleNamespace(settings=settings)}
            ):
                profile = queries.candidate_runtime_profile()
            self.assertEqual(profile["CH25_EVAL_LOGGER_TABLE"], table)
            self.assertNotIn("private-value", replay.canonical(profile))
            self.assertEqual(
                profile["CLICKHOUSE_APPLICATION_READ_MAX_MEMORY_USAGE"], 36 * 1024**3
            )
            profiles.append(profile)
        self.assertNotEqual(replay.digest(profiles[0]), replay.digest(profiles[1]))

    def test_runtime_profile_rejects_unknown_eval_source_without_echoing_it(self):
        settings = SimpleNamespace(CH25_EVAL_LOGGER_TABLE="unknown-private-target")
        with (
            patch.dict(
                sys.modules, {"django.conf": SimpleNamespace(settings=settings)}
            ),
            self.assertRaisesRegex(
                replay.ReplayError, "^UNSUPPORTED_EVAL_SOURCE$"
            ) as caught,
        ):
            queries.candidate_runtime_profile()
        self.assertNotIn("private", str(caught.exception))

    def test_reference_diagnostics_preserve_smaller_caps_and_force_throw(self):
        original = {
            "max_execution_time": 2,
            "max_execution_time_leaf": 0.125,
            "max_memory_usage": 1024**3,
            "max_bytes_to_read": 1024**3,
            "max_bytes_to_read_leaf": 512 * 1024**2,
            "max_rows_to_read": 1234,
            "max_result_rows": 1000,
            "max_result_bytes": 1024**2,
            "max_rows_in_distinct": 1000,
            "max_bytes_in_distinct": 2 * 1024**2,
            "max_threads": 1,
            "read_overflow_mode": "break",
            "result_overflow_mode": "break",
            "timeout_overflow_mode": "break",
            "distinct_overflow_mode": "break",
        }
        before = original.copy()
        guarded = queries.diagnostic_read_settings(
            original,
            remaining_ms=60000,
            args=SimpleNamespace(threads=2, read_gib=8),
            preserve_caller_caps=True,
        )
        for name, value in original.items():
            self.assertEqual(
                guarded[name], "throw" if name.endswith("overflow_mode") else value
            )
        self.assertEqual(original, before)
        self.assertEqual(guarded["readonly"], 2)
        self.assertEqual(guarded["use_query_cache"], 0)

    def test_reference_defaults_and_larger_requests_never_exceed_run_guards(self):
        for original in (
            {},
            {"max_execution_time": 0, "max_result_rows": 0, "max_bytes_to_read": 0},
            {
                "max_execution_time": 120,
                "max_execution_time_leaf": 120,
                "max_memory_usage": 36 * 1024**3,
                "max_bytes_to_read": 99 * 1024**3,
                "max_bytes_to_read_leaf": 99 * 1024**3,
                "max_result_rows": 999999,
                "max_result_bytes": 99 * 1024**2,
                "max_threads": 8,
            },
        ):
            with self.subTest(original=original):
                guarded = queries.diagnostic_read_settings(
                    original,
                    remaining_ms=125,
                    args=SimpleNamespace(threads=2, read_gib=8),
                    preserve_caller_caps=True,
                )
                self.assertEqual(guarded["max_execution_time"], 0.125)
                self.assertEqual(guarded["max_execution_time_leaf"], 0.125)
                self.assertEqual(guarded["max_memory_usage"], 4 * 1024**3)
                self.assertEqual(guarded["max_bytes_to_read"], 8 * 1024**3)
                self.assertEqual(guarded["max_bytes_to_read_leaf"], 8 * 1024**3)
                self.assertEqual(guarded["max_result_rows"], 100001)
                self.assertEqual(guarded["max_result_bytes"], 32 * 1024**2)
                self.assertEqual(guarded["max_threads"], 2)

    def test_reference_timeout_argument_can_only_narrow_the_remaining_wall(self):
        for timeout_ms, remaining_ms, expected in (
            (50, 60000, 0.05),
            (5000, 125, 0.125),
        ):
            with self.subTest(timeout_ms=timeout_ms):
                guarded = queries.diagnostic_read_settings(
                    {"max_execution_time": 2},
                    remaining_ms=remaining_ms,
                    timeout_ms=timeout_ms,
                    args=SimpleNamespace(threads=2, read_gib=8),
                    preserve_caller_caps=True,
                )
                self.assertEqual(guarded["max_execution_time"], expected)
                self.assertEqual(guarded["max_execution_time_leaf"], expected)

    def test_malformed_reference_timeout_is_not_silently_widened(self):
        for value in (-1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                queries.diagnostic_read_settings(
                    {"max_execution_time": value},
                    remaining_ms=60000,
                    args=SimpleNamespace(threads=2, read_gib=8),
                    preserve_caller_caps=True,
                )

    def test_source_fingerprint_includes_legacy_transport_and_all_oracle_helpers(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [
                root / "futureagi/tracer/client.py",
                root / "futureagi/tfc/utils/clickhouse.py",
                root / "futureagi/model_hub/utils/eval_playground_span_context.py",
                root / "scripts/qa/extra_reference.py",
                root / "api_contracts/filter_contract.json",
                root / "futureagi/tracer/contracts/filter_contract.json",
            ]
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("original", encoding="utf-8")
            with patch.object(queries, "ROOT", root):
                original = queries.source_fingerprint()
                for path in paths:
                    path.write_text("changed", encoding="utf-8")
                    self.assertNotEqual(queries.source_fingerprint(), original)
                    path.write_text("original", encoding="utf-8")
                    self.assertEqual(queries.source_fingerprint(), original)
                    path.unlink()
                    self.assertNotEqual(queries.source_fingerprint(), original)
                    path.write_text("original", encoding="utf-8")
                    self.assertEqual(queries.source_fingerprint(), original)

    def test_diagnostic_guards_cannot_be_disabled_by_unlimited_application_policy(self):
        original = {
            "max_execution_time": 0,
            "max_memory_usage": 36 * 1024**3,
            "max_bytes_to_read": 0,
            "max_result_rows": 0,
            "max_result_bytes": 0,
            "max_threads": 4,
        }
        guarded = queries.diagnostic_read_settings(
            original, remaining_ms=1234, args=SimpleNamespace(threads=2, read_gib=8)
        )
        self.assertEqual(guarded["max_execution_time"], 1.234)
        self.assertEqual(guarded["max_execution_time_leaf"], 1.234)
        self.assertEqual(guarded["max_memory_usage"], 4 * 1024**3)
        self.assertEqual(guarded["max_threads"], 2)
        self.assertEqual(guarded["max_result_rows"], 100001)
        self.assertEqual(guarded["max_result_bytes"], 32 * 1024**2)
        self.assertEqual(guarded["max_bytes_to_read"], 8 * 1024**3)
        self.assertEqual(guarded["max_bytes_to_read_leaf"], 8 * 1024**3)
        self.assertEqual(guarded["readonly"], 2)
        self.assertEqual(original["max_result_rows"], 0)
        for name in ("max_memory_usage", "max_threads"):
            guarded = queries.diagnostic_read_settings(
                {name: 0},
                remaining_ms=1234,
                args=SimpleNamespace(threads=2, read_gib=8),
            )
            self.assertGreater(guarded[name], 0)

    def test_authorized_project_aliases_are_bound_by_sql_not_parameter_name(self):
        for clause, params in (
            ("project_id=toUUID(%(attr_pid)s)", {"attr_pid": "project"}),
            ("project_id IN %(attr_pids)s", {"attr_pids": ["project"]}),
        ):
            sql = "SELECT id FROM spans WHERE " + clause
            queries.validate_select(sql, params, ["project"])
            with self.assertRaises(replay.ReplayError):
                queries.validate_select(sql, params, ["other"])

    def test_parameterized_select_scoped_to_authorized_project(self):
        sql = "SELECT id FROM spans WHERE project_id = %(project_id)s"
        self.assertEqual(
            queries.validate_select(sql, {"project_id": "project"}, ["project"]), sql
        )

    def test_scope_cannot_widen(self):
        sql = "SELECT id FROM spans WHERE project_id IN %(project_ids)s"
        for params in ({"project_ids": ["project", "other"]}, {}, {"project_id": None}):
            with self.subTest(params=params), self.assertRaises(replay.ReplayError):
                queries.validate_select(sql, params, ["project"])

    def test_write_and_unsafe_table_functions_refused(self):
        unsafe = [
            "INSERT INTO spans SELECT * FROM spans WHERE project_id=%(project_id)s",
            "SELECT * FROM spans WHERE project_id=%(project_id)s; DROP TABLE spans",
            "SELECT * FROM remote('external',spans) WHERE project_id=%(project_id)s",
            "SELECT * FROM spans WHERE project_id=%(project_id)s INTO OUTFILE '/tmp/data'",
            "SELECT * FROM spans WHERE project_id=%(project_id)s SETTINGS readonly=0",
            "SELECT * FROM spans WHERE project_id=%(project_id)s SETTINGS read_overflow_mode='break'",
        ]
        for sql in unsafe:
            with self.subTest(sql=sql), self.assertRaises(replay.ReplayError):
                queries.validate_select(sql, {"project_id": "project"}, ["project"])

    def test_filters_in_parameters_do_not_trigger_sql_keyword_guard(self):
        queries.validate_select(
            "SELECT id FROM spans WHERE project_id=%(project_id)s AND name=%(value)s",
            {"project_id": "project", "value": "DELETE user from JSON file"},
            ["project"],
        )


class OutcomeTests(unittest.TestCase):
    def test_session_reference_uses_independent_population_and_explicit_order(self):
        import observe_session_reference as reference

        for surface in ("sessions", "task_sessions", "eval_sessions", "user_sessions"):
            for order in ("uuid", "uuid_string"):
                for outcome in ("match", "mismatch", "error"):
                    with self.subTest(surface=surface, order=order, outcome=outcome):
                        adapter = queries.CandidateQueries(
                            SimpleNamespace(safety_seconds=60, verify_trace_ids=True),
                            {"scope": {"project_id": "project"}},
                            ["project"],
                        )
                        reader = SimpleNamespace(
                            close=lambda: None, calls=[], deadline=time.monotonic() + 60
                        )
                        expected = "a" if outcome == "match" else "b"
                        oracle_result = (
                            {
                                "side_effect": replay.ReplayError(
                                    "REFERENCE_UNSUPPORTED_OR_ABORTED"
                                )
                            }
                            if outcome == "error"
                            else {
                                "return_value": {
                                    "pages": [[{"session_id": expected}], []],
                                    "contract": reference.CONTRACT,
                                    "order_mode": order,
                                }
                            }
                        )
                        filters = [
                            {
                                "column_id": "key",
                                "filter_config": {
                                    "col_type": "SPAN_ATTRIBUTE",
                                    "filter_type": "number",
                                    "filter_op": "greater_than",
                                    "filter_value": 1,
                                },
                            }
                        ]
                        case = {
                            "id": "case",
                            "surface": surface,
                            "period": "7D",
                            "target_ms": 5000,
                            "blocked": None,
                            "window": {
                                "start": "2026-08-29T00:00:00Z",
                                "end": "2026-09-05T00:00:00Z",
                            },
                            "request": {"target_rows": 25},
                        }
                        with (
                            patch.object(
                                queries, "ReadOnlyExecutor", return_value=reader
                            ),
                            patch.object(
                                queries, "normalize_filters", return_value=filters
                            ),
                            patch.object(
                                adapter,
                                "entity_list",
                                return_value={
                                    "query_complete": True,
                                    "query_exact": True,
                                    "table": [{"session_id": "a"}],
                                    "session_order_mode": order,
                                },
                            ),
                            patch.object(
                                reference, "reference_session_pages", **oracle_result
                            ) as oracle,
                        ):
                            row = adapter.run(case)
                        self.assertEqual(
                            oracle.call_args.kwargs,
                            {
                                "project_ids": ["project"],
                                "authorized_project_ids": ["project"],
                                "start": case["window"]["start"],
                                "end": case["window"]["end"],
                                "filters": filters,
                                "page_size": 25,
                                "order_mode": order,
                            },
                        )
                        evidence = row["independent_reference"]
                        self.assertEqual(
                            evidence["kind"], "independent_scalar_session_ID_order"
                        )
                        self.assertEqual(
                            evidence["status"],
                            {
                                "match": "ID_ORDER_MATCH",
                                "mismatch": "ID_ORDER_MISMATCH",
                                "error": "UNVERIFIED",
                            }[outcome],
                        )
                        self.assertEqual(row["status"], "COMPLETE_UNVERIFIED")
                        self.assertEqual(row["correctness"], "UNVERIFIED")
                        self.assertFalse(evidence["full_row_metrics_verified"])
                        if outcome != "error":
                            self.assertFalse(evidence["pagination_verified"])
                            self.assertEqual(evidence["candidate_pages_verified"], 1)

    def test_span_and_preview_references_compare_full_identity_without_promoting_qualification(
        self,
    ):
        import observe_span_reference as reference

        for surface in ("spans", "task_spans", "eval_spans"):
            for outcome in ("match", "mismatch", "error"):
                with self.subTest(surface=surface, outcome=outcome):
                    adapter = queries.CandidateQueries(
                        SimpleNamespace(safety_seconds=60, verify_trace_ids=True),
                        {"scope": {"project_id": "project"}},
                        ["project"],
                    )
                    reader = SimpleNamespace(
                        close=lambda: None, calls=[], deadline=time.monotonic() + 60
                    )
                    oracle_options = (
                        {"side_effect": replay.ReplayError("REFERENCE_READ_ABORTED")}
                        if outcome == "error"
                        else {
                            "return_value": (
                                [
                                    "physical-winner"
                                    if outcome == "match"
                                    else "other-winner"
                                ],
                                {},
                            )
                        }
                    )
                    with (
                        patch.object(queries, "ReadOnlyExecutor", return_value=reader),
                        patch.object(queries, "normalize_filters", return_value=[]),
                        patch.object(
                            adapter,
                            "entity_list",
                            return_value={
                                "query_complete": True,
                                "query_exact": True,
                                "table": [{"id": "span"}],
                            },
                        ),
                        patch.object(
                            reference, "reference_span_ids", **oracle_options
                        ) as oracle,
                        patch.object(
                            reference, "span_identity", return_value="physical-winner"
                        ),
                    ):
                        row = adapter.run(
                            {
                                "id": "case",
                                "surface": surface,
                                "period": "7D",
                                "target_ms": 5000,
                                "blocked": None,
                                "request": {},
                            }
                        )
                    self.assertEqual(oracle.call_count, 1)
                    self.assertEqual(oracle.call_args.args[1]["surface"], surface)
                    self.assertEqual(row["status"], "COMPLETE_UNVERIFIED")
                    self.assertEqual(row["correctness"], "UNVERIFIED")
                    self.assertEqual(
                        row["independent_reference"]["kind"],
                        "independent_scalar_span_identity_order",
                    )
                    self.assertEqual(
                        row["independent_reference"]["status"],
                        {
                            "match": "ID_ORDER_MATCH",
                            "mismatch": "ID_ORDER_MISMATCH",
                            "error": "UNVERIFIED",
                        }[outcome],
                    )
                    self.assertFalse(
                        row["independent_reference"]["full_row_metrics_verified"]
                    )

    def test_completed_candidate_uses_a_separate_explicit_reference_mode(self):
        import observe_trace_id_reference as reference

        adapter = queries.CandidateQueries(
            SimpleNamespace(safety_seconds=60, verify_trace_ids=True),
            {"scope": {"project_id": "project"}},
            ["project"],
        )
        readers = []

        def reader_factory(args, projects, deadline, *, mode):
            reader = SimpleNamespace(
                close=lambda: None, calls=[], deadline=deadline, mode=mode
            )
            readers.append(reader)
            return reader

        with (
            patch.object(queries, "ReadOnlyExecutor", side_effect=reader_factory),
            patch.object(queries, "normalize_filters", return_value=[]),
            patch.object(
                adapter,
                "entity_list",
                return_value={
                    "query_complete": True,
                    "query_exact": True,
                    "query_status": "complete",
                    "table": [{"trace_id": "a"}],
                },
            ),
            patch.object(
                reference, "reference_ids", return_value=(["a"], {})
            ) as oracle,
        ):
            row = adapter.run(
                {
                    "id": "case",
                    "surface": "traces",
                    "period": "7D",
                    "target_ms": 5000,
                    "blocked": None,
                    "request": {},
                }
            )
        self.assertEqual(
            [reader.mode for reader in readers], ["candidate", "reference_diagnostic"]
        )
        self.assertIs(oracle.call_args.args[0], readers[1])
        self.assertEqual(row["read_policy_mode"], "candidate")
        self.assertEqual(
            row["independent_reference"]["read_policy_mode"], "reference_diagnostic"
        )
        self.assertEqual(row["independent_reference"]["status"], "ID_ORDER_MATCH")

    def test_full_plan_counts_untested_blocked_stale_and_duplicate_cases(self):
        cases = [
            {
                "id": str(i),
                "surface": "traces",
                "period": "7D",
                "attributes": [f"attr{i}"],
                "blocked": "NO_INPUT" if i == 3 else None,
            }
            for i in range(4)
        ]
        common = {
            "plan_id": "plan",
            "source_sha256": "current",
            "status": "COMPLETE_UNVERIFIED",
            "latency_met": True,
        }
        rows = [
            {
                **common,
                "case_id": "0",
                "independent_reference": {"status": "ID_ORDER_MATCH"},
            },
            {
                **common,
                "case_id": "0",
                "independent_reference": {"status": "ID_ORDER_MATCH"},
            },
            {**common, "case_id": "1", "source_sha256": "old"},
        ]
        summary = queries.qualification_summary(
            {"plan_id": "plan", "cases": cases}, rows, "current"
        )
        self.assertEqual(summary["totals"]["planned"], 4)
        self.assertEqual(summary["totals"]["executed"], 1)
        self.assertEqual(summary["totals"]["UNTESTED"], 2)
        self.assertEqual(summary["totals"]["BLOCKED_INPUT"], 1)
        self.assertEqual(summary["totals"]["identity_order_verified"], 1)
        self.assertEqual(summary["attributes_executed"], 1)
        self.assertEqual(summary["historical_source_attempts_excluded"], 1)
        self.assertEqual(summary["qualification"], "NOT_QUALIFIED")
        self.assertFalse(summary["full_row_independent_oracle"])
        with self.assertRaisesRegex(replay.ReplayError, "PLAN_MISMATCH"):
            queries.qualification_summary(
                {"plan_id": "other", "cases": cases}, rows, "current"
            )

    def test_completed_identity_mismatch_is_visible_even_if_fast(self):
        plan = {
            "plan_id": "plan",
            "cases": [
                {
                    "id": "case",
                    "attributes": ["a"],
                    "surface": "traces",
                    "period": "12M",
                    "blocked": None,
                }
            ],
        }
        row = {
            "plan_id": "plan",
            "source_sha256": "current",
            "case_id": "case",
            "status": "COMPLETE_UNVERIFIED",
            "latency_met": True,
            "independent_reference": {"status": "ID_ORDER_MISMATCH"},
        }
        summary = queries.qualification_summary(plan, [row], "current")
        self.assertEqual(summary["totals"]["identity_order_mismatch"], 1)
        self.assertEqual(summary["qualification"], "NOT_QUALIFIED")

    def test_complete_slow_response_is_not_failed_or_cut_off_at_target(self):
        for exact in (True, False):
            with self.subTest(exact=exact):
                adapter = queries.CandidateQueries(
                    SimpleNamespace(safety_seconds=60, verify_trace_ids=False), {}, []
                )
                reader = SimpleNamespace(
                    close=lambda: None, calls=[], deadline=time.monotonic() + 60
                )

                def response(*args):
                    time.sleep(0.01)
                    return {
                        "query_complete": True,
                        "query_exact": exact,
                        "query_status": "complete",
                        "table": [{"trace_id": "a"}],
                    }

                with (
                    patch.object(queries, "ReadOnlyExecutor", return_value=reader),
                    patch.object(queries, "normalize_filters", return_value=[]),
                    patch.object(adapter, "entity_list", side_effect=response),
                ):
                    row = adapter.run(
                        {
                            "id": "case",
                            "surface": "traces",
                            "period": "7D",
                            "target_ms": 1,
                            "blocked": None,
                            "request": {},
                        }
                    )
                self.assertTrue(row["complete"])
                self.assertFalse(row["latency_met"])
                self.assertGreater(row["elapsed_ms"], row["target_ms"])
                self.assertEqual(
                    row["status"], "COMPLETE_UNVERIFIED" if exact else "INEXACT"
                )


class ExecutorPolicyTests(unittest.TestCase):
    def run_executor(self, mode, *, failure=None):
        """Stub adapters at import boundaries: no Django startup or DB sockets."""
        args = SimpleNamespace(
            host="unused.invalid",
            port=1,
            database="test",
            safety_seconds=60,
            threads=2,
            read_gib=8,
        )
        driver = Mock()
        driver.execute.return_value = ([("id",)], [("trace_id", "String")])
        driver.last_query.progress = SimpleNamespace(rows=1, bytes=16, elapsed_ns=1000)
        if failure is not None:
            driver.execute.side_effect = failure
        normalize = Mock(
            return_value={
                "max_execution_time": 0,
                "max_result_rows": 0,
                "max_bytes_to_read": 0,
                "max_rows_in_distinct": 0,
                "max_memory_usage": 36 * 1024**3,
            }
        )
        result_class = SimpleNamespace(from_clickhouse_rows=Mock(return_value="result"))
        modules = {
            "tracer.services.clickhouse.application_read_policy": SimpleNamespace(
                application_read_settings=normalize
            ),
            "tracer.services.clickhouse.query_service": SimpleNamespace(
                QueryResult=result_class
            ),
            "tracer.services.clickhouse.read_budget": SimpleNamespace(
                ReadDeadlineExceeded=TimeoutError
            ),
        }
        supplied = {
            "max_execution_time": 2,
            "max_result_rows": 1000,
            "max_bytes_to_read": 1024**3,
            "max_rows_in_distinct": 1000,
            "max_bytes_in_distinct": 1024**2,
            "distinct_overflow_mode": "break",
        }
        original = supplied.copy()
        with (
            patch.dict(sys.modules, modules),
            patch("clickhouse_driver.Client", return_value=driver),
            patch.object(
                queries.ReadOnlyExecutor, "remaining_read_ms", return_value=60000
            ),
        ):
            reader = queries.ReadOnlyExecutor(
                args, ["project"], time.monotonic() + 60, mode=mode
            )
            try:

                def call():
                    return reader.execute_ch_query(
                        "SELECT trace_id FROM spans WHERE project_id=%(project_id)s",
                        {"project_id": "project"},
                        timeout_ms=500,
                        settings=supplied,
                    )

                if failure is None:
                    self.assertEqual(call(), "result")
                else:
                    with self.assertRaises(type(failure)) as caught:
                        call()
                    self.assertIs(caught.exception, failure)
            finally:
                reader.close()
        self.assertEqual(supplied, original)
        driver.disconnect.assert_called_once_with()
        return reader, driver, normalize

    def test_reference_bypasses_application_normalization_and_records_actual_limits(
        self,
    ):
        reader, driver, normalize = self.run_executor("reference_diagnostic")
        normalize.assert_not_called()
        limits = driver.execute.call_args.kwargs["settings"]
        self.assertEqual(limits["max_execution_time"], 0.5)
        self.assertEqual(limits["max_result_rows"], 1000)
        self.assertEqual(limits["max_rows_in_distinct"], 1000)
        self.assertEqual(limits["max_bytes_in_distinct"], 1024**2)
        self.assertEqual(limits["distinct_overflow_mode"], "throw")
        self.assertEqual(limits["max_bytes_to_read"], 1024**3)
        self.assertEqual(limits["max_memory_usage"], 4 * 1024**3)
        self.assertEqual(reader.calls[0]["read_policy_mode"], "reference_diagnostic")
        self.assertIsNone(reader.calls[0]["application_read_settings"])
        self.assertEqual(reader.calls[0]["limits"], limits)

    def test_candidate_still_normalizes_before_outer_diagnostic_guards(self):
        candidate = object.__new__(queries.ReadOnlyExecutor)
        candidate.mode = "candidate"
        reference = object.__new__(queries.ReadOnlyExecutor)
        reference.mode = "reference_diagnostic"
        self.assertFalse(candidate.supports_bounded_speculative_reads)
        self.assertTrue(reference.supports_bounded_speculative_reads)
        reader, driver, normalize = self.run_executor("candidate")
        normalize.assert_called_once()
        limits = driver.execute.call_args.kwargs["settings"]
        self.assertEqual(limits["max_execution_time"], 60)
        self.assertEqual(limits["max_execution_time_leaf"], 60)
        self.assertEqual(limits["max_bytes_to_read"], 8 * 1024**3)
        self.assertEqual(limits["max_result_rows"], 100001)
        self.assertEqual(limits["max_rows_in_distinct"], 0)
        self.assertEqual(reader.calls[0]["read_policy_mode"], "candidate")
        self.assertEqual(
            reader.calls[0]["application_read_settings"], normalize.return_value
        )

    def test_reference_errors_propagate_with_mode_and_limits_in_ledger(self):
        from clickhouse_driver.errors import ErrorCodes, ServerException

        for code in (
            ErrorCodes.TIMEOUT_EXCEEDED,
            ErrorCodes.TOO_MANY_ROWS,
            ErrorCodes.SYNTAX_ERROR,
        ):
            with self.subTest(code=code):
                failure = ServerException("offline", code=code)
                reader, driver, normalize = self.run_executor(
                    "reference_diagnostic", failure=failure
                )
                self.assertEqual(reader.calls[0]["error_code"], code)
                self.assertEqual(
                    reader.calls[0]["read_policy_mode"], "reference_diagnostic"
                )
                self.assertEqual(
                    reader.calls[0]["limits"]["max_rows_in_distinct"], 1000
                )
                driver.execute.assert_called_once()
                normalize.assert_not_called()

    def test_invalid_mode_fails_before_driver_construction(self):
        with patch("clickhouse_driver.Client") as client:
            with self.assertRaisesRegex(ValueError, "read policy mode"):
                queries.ReadOnlyExecutor(None, [], 0, mode="unknown")
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
