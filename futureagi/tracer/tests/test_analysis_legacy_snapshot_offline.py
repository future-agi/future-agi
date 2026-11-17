"""r3b4 regressions for historical reads, interactive tools, abort and retry.
Real committed definitions execute against minimal stores; row-lock probe models
PostgreSQL's documented lock retention, not a claim of actual PostgreSQL testing.
"""

import ast
import runpy
import socket
import sys
import unittest
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID

import pytest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]


def deny(*a, **k):
    raise AssertionError("network forbidden")


@pytest.fixture(autouse=True)
def _deny_network(monkeypatch):
    # Collection must not poison sockets for other modules in the same session.
    for attr in ("create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, attr, deny)
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)


def source(path):
    return (ROOT / path).read_text()


def definition(path, name, ns, owner=None):
    tree = ast.parse(source(path))
    body = tree.body
    if owner:
        body = next(
            n for n in body if isinstance(n, ast.ClassDef) and n.name == owner
        ).body
    node = next(n for n in body if getattr(n, "name", None) == name)
    node.decorator_list = []
    if isinstance(node, ast.FunctionDef):
        node.returns = None
        for arg in node.args.args + node.args.kwonlyargs:
            arg.annotation = None
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), ns)
    return ns[name]


support = runpy.run_path(
    str(ROOT / "tracer/tests/test_background_analysis_lifecycle_offline.py")
)
readers = runpy.run_path(
    str(ROOT / "tracer/tests/test_analysis_bound_readers_offline.py")
)
module = support["module"]


class AdditionalReaders(readers["BoundReaders"]):
    def test_retry_between_status_and_details_keeps_complete_snapshot(self):
        # Actual binding managers see G1 completed during the first read. A
        # competing force retry starts only after the GET's status re-read.
        for d in self.details.rows:
            d.urgency_to_fix = d.immediate_fix = ""
        direct_details = self.details
        receipt = self.receipts.rows[0]

        def filter_with_retry(**kwargs):
            receipt.status = "running"
            receipt.result_analysis_id = None
            return readers["Rows"](direct_details.rows).filter(**kwargs)

        ns = {
            "DeepAnalysisResponse": SimpleNamespace,
            "TraceErrorAnalysis": SimpleNamespace(objects=self.analyses),
            "TraceErrorDetail": SimpleNamespace(
                objects=SimpleNamespace(filter=filter_with_retry)
            ),
            "_cluster_qs_for_access": lambda *a: SimpleNamespace(exists=lambda: True),
            "_cluster_has_trace": lambda *a: True,
            "background_analysis_state": SimpleNamespace(
                get_state=lambda *a, **k: receipt
            ),
            "deep_analysis_state": SimpleNamespace(
                status=lambda *a, **k: (
                    "done" if receipt.status == "completed" else "running"
                )
            ),
            "_build_root_causes": lambda details: [d.id for d in details],
            "_build_recommendations": lambda *a: [],
        }
        self.install_snapshot(detail_filter=filter_with_retry)
        fn = definition("tracer/queries/feed.py", "get_deep_analysis", ns)
        response = fn("cluster", "trace")
        self.assertEqual(receipt.status, "running")
        self.assertEqual(response.status, "done")
        self.assertEqual(response.root_causes, ["new"])

    def test_drawer_pins_children_when_retry_starts_after_parent_read(self):
        method = self.reader_method("get_analysis_by_trace_id")

        def retry(**kwargs):
            self.receipts.rows[0].status = "running"
            self.receipts.rows[0].result_analysis_id = None
            return readers["Rows"](self.details.rows).filter(**kwargs)

        self.install_snapshot(detail_filter=retry)
        analysis = method(None, "trace")
        self.assertIs(analysis, self.good)
        self.assertEqual([d.id for d in analysis._analysis_snapshot_details], ["new"])
        self.assertEqual(self.receipts.rows[0].status, "running")

    def test_cluster_pins_scores_and_details_when_retry_starts_between_queries(self):
        def retry(**kwargs):
            self.receipts.rows[0].status = "running"
            self.receipts.rows[0].result_analysis_id = None
            return readers["Rows"](self.details.rows).filter(**kwargs)

        snapshot = self.install_snapshot(detail_filter=retry)
        details, analyses = snapshot.cluster_snapshot(["trace"])
        self.assertEqual([a.id for a in analyses], ["G1"])
        self.assertEqual([d.id for d in details], ["new"])
        self.assertEqual(self.receipts.rows[0].status, "running")


class HistoricalReaders(readers["BoundReaders"]):
    def setUp(self):
        super().setUp()
        self.receipts.rows.clear()
        self.analyses.rows[:] = [self.good]
        self.details.rows[:] = [d for d in self.details.rows if d.analysis_id == "G1"]

    def test_historical_judge_analysis_and_details_remain_visible(self):
        self.test_feed_uses_bound_manager_not_late_aborted_row()
        self.assertEqual([a.id for a in self.analyses.all()], ["G1"])
        self.assertEqual([d.id for d in self.details.all()], ["new"])
        self.assertIs(
            self.reader_method("get_analysis_by_trace_id")(None, "trace"), self.good
        )
        self.assertEqual(
            [
                a.id
                for a in self.reader_method("get_analyses_for_traces")(None, ["trace"])
            ],
            ["G1"],
        )
        self.assertEqual(
            [
                d.id
                for d in self.reader_method("get_error_details_for_traces")(
                    None, ["trace"]
                )
            ],
            ["new"],
        )


class LegacyToolPublication(support["LifecycleTests"]):
    def test_interactive_findings_then_scores_never_create_lifecycle(self):
        ns = {
            "BaseModelManager": readers["BaseManager"],
            "AllObjectsManager": readers["BaseManager"],
            "NoWorkspaceFilterManager": readers["BaseManager"],
            "models": SimpleNamespace(QuerySet=object),
            "transaction": SimpleNamespace(atomic=nullcontext),
            "_attempt": ContextVar("independent-tool", default=None),
            "UUID": UUID,
        }
        support["load"]("tracer/services/analysis_result_binding.py", ns)
        for name in ("attempt_scope", "locked_attempt", "legacy_attempt"):
            ns[name] = contextmanager(ns[name])
        binding = SimpleNamespace(**ns)
        project = SimpleNamespace(pk=self.project)
        projects = Mock()
        projects.filter.return_value.first.return_value = project

        @contextmanager
        def reader():
            yield SimpleNamespace(
                root_ids_by_trace_ids=lambda ids: {self.trace: ("root", self.project)}
            )

        result_type = SimpleNamespace(
            validation_error=lambda msg: SimpleNamespace(is_error=True, content=msg),
            not_found=lambda *a: SimpleNamespace(is_error=True),
        )
        fn = definition(
            "ai_tools/tools/tracing/submit_trace_finding.py",
            "execute",
            {"ToolResult": result_type},
            owner="SubmitTraceFindingTool",
        )
        saved = []

        def own(params, context):
            saved.append(SimpleNamespace(overall_score=None, total_errors=1))
            return SimpleNamespace(is_error=False, data={"analysis_status": "running"})

        fake_self = SimpleNamespace(_execute_owned=own)
        fake_context = SimpleNamespace(organization="org")
        params = SimpleNamespace(trace_id=self.trace)
        with patch.dict(
            "sys.modules",
            {
                "tracer.services.analysis_result_binding": module(
                    "binding", **vars(binding)
                ),
                "tracer.models.background_trace_analysis_state": module(
                    "state",
                    BackgroundTraceAnalysisState=SimpleNamespace(objects=self.store),
                ),
                "tracer.models.project": module(
                    "project", Project=SimpleNamespace(objects=projects)
                ),
                "tracer.services.clickhouse.v2": module("ch", get_reader=reader),
            },
        ):
            result = fn(fake_self, params, fake_context)
            self.assertFalse(result.is_error)
            self.assertIsNone(self.state.get_state(self.trace))
            self.assertIsNone(saved[0].overall_score)
            self.assertEqual(self.poll.status(self.trace, has_analysis=True), "done")
            second = fn(fake_self, params, fake_context)
            self.assertFalse(second.is_error)
            third = fn(fake_self, params, fake_context)
            self.assertFalse(third.is_error)
            score = definition(
                "ai_tools/tools/tracing/submit_trace_scores.py",
                "execute",
                {"ToolResult": result_type, "logger": Mock()},
                owner="SubmitTraceScoresTool",
            )
            result = score(fake_self, params, fake_context)
            self.assertFalse(result.is_error)
            self.assertEqual(len(saved), 4)
            self.assertIsNone(self.state.get_state(self.trace))


if __name__ == "__main__":
    suite = unittest.TestSuite()
    for cls, name in (
        (
            HistoricalReaders,
            "test_historical_judge_analysis_and_details_remain_visible",
        ),
        (
            AdditionalReaders,
            "test_retry_between_status_and_details_keeps_complete_snapshot",
        ),
        (
            AdditionalReaders,
            "test_drawer_pins_children_when_retry_starts_after_parent_read",
        ),
        (
            AdditionalReaders,
            "test_cluster_pins_scores_and_details_when_retry_starts_between_queries",
        ),
        (
            LegacyToolPublication,
            "test_interactive_findings_then_scores_never_create_lifecycle",
        ),
    ):
        suite.addTest(cls(name))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())
