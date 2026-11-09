"""Run actual reader/manager definitions over synthetic rows, with sockets blocked.

This is not a Django query compiler or database test. The separate ORM tests
exercise the real Subquery, reverse managers, row locks and unique constraints.
"""

import ast
import runpy
import unittest
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

support = runpy.run_path(
    str(Path(__file__).with_name("test_background_analysis_lifecycle_offline.py"))
)
ROOT, load, module = support["ROOT"], support["load"], support["module"]


class Expr:
    def __init__(self, evaluate):
        self.evaluate = evaluate

    def __or__(self, other):
        return Expr(lambda row: self.evaluate(row) or other.evaluate(row))

    def __invert__(self):
        return Expr(lambda row: not self.evaluate(row))


class Ref:
    def __init__(self, key):
        self.key = key


def field(row, key):
    for part in key.split("__"):
        row = getattr(row, part)
    return row


class Rows:
    def __init__(self, rows, filters=None, expressions=()):
        self.rows, self.filters, self.expressions = rows, filters or {}, expressions

    def selected(self, outer=None):
        def match(row):
            for key, value in self.filters.items():
                parts = key.split("__")
                operation = parts.pop() if parts[-1] in ("in", "isnull") else "eq"
                actual = field(row, "__".join(parts))
                if isinstance(value, Ref):
                    value = field(outer, value.key)
                if operation == "in" and str(actual) not in list(map(str, value)):
                    return False
                if operation == "isnull" and (actual is None) != value:
                    return False
                if operation == "eq" and str(actual) != str(value):
                    return False
            return all(expr.evaluate(row) for expr in self.expressions)

        return [r for r in self.rows if match(r)]

    def __iter__(self):
        return iter(self.selected())

    def __getitem__(self, key):
        return self.selected()[key]

    def __bool__(self):
        return bool(self.selected())

    def filter(self, *expressions, **filters):
        return Rows(
            self.rows, {**self.filters, **filters}, self.expressions + expressions
        )

    def aggregate(self, **expressions):
        return {
            name: len(self.selected())
            if op == "count"
            else sum(getattr(r, column) for r in self.selected())
            for name, (op, column) in expressions.items()
        }

    def values(self, key):
        return [getattr(r, key) for r in self.selected()]

    def values_list(self, key, flat=False):
        return self.values(key)

    def first(self):
        rows = self.selected()
        return rows[0] if rows else None

    def exists(self):
        return bool(self.selected())

    def all(self):
        return self

    def none(self):
        return Rows([])

    def select_related(self, *args):
        return self

    prefetch_related = select_related
    order_by = select_related
    distinct = select_related


def fake_models():
    return SimpleNamespace(
        QuerySet=object,
        Subquery=lambda rows: rows,
        OuterRef=Ref,
        Exists=lambda query: Expr(lambda outer: bool(query.selected(outer))),
        Q=lambda **filters: Expr(lambda row: Rows([row]).filter(**filters).exists()),
    )


class BaseManager:
    @classmethod
    def from_queryset(cls, query):
        return cls

    def get_queryset(self):
        return Rows(self.rows)

    def __getattr__(self, name):
        return getattr(self.get_queryset(), name)


class BoundReaders(unittest.TestCase):
    def setUp(self):
        def denied(*args, **kwargs):
            raise AssertionError("network forbidden")

        for target in (
            "socket.create_connection",
            "socket.socket.connect",
            "socket.socket.connect_ex",
            "socket.getaddrinfo",
        ):
            p = patch(target, denied)
            p.start()
            self.addCleanup(p.stop)
        self.receipts = Rows(
            [
                SimpleNamespace(
                    status="completed",
                    result_analysis_id="G1",
                    project_id="p",
                    trace_id="trace",
                )
            ]
        )
        self.old = SimpleNamespace(
            pk="G0",
            id="G0",
            trace_id="trace",
            trace=SimpleNamespace(id="trace"),
            project="p",
            deleted=False,
        )
        self.good = SimpleNamespace(
            pk="G1",
            id="G1",
            trace_id="trace",
            trace=self.old.trace,
            project="p",
            deleted=False,
        )
        self.late = SimpleNamespace(
            pk="late-G0",
            id="late-G0",
            trace_id="trace",
            trace=self.old.trace,
            project="p",
            deleted=False,
        )
        self.modules = {
            "tracer.models.background_trace_analysis_state": module(
                "state",
                BackgroundTraceAnalysisState=SimpleNamespace(objects=self.receipts),
            ),
        }
        p = patch.dict("sys.modules", self.modules)
        p.start()
        self.addCleanup(p.stop)
        self.binding = load(
            "tracer/services/analysis_result_binding.py",
            {
                "BaseModelManager": BaseManager,
                "AllObjectsManager": BaseManager,
                "NoWorkspaceFilterManager": BaseManager,
                "models": fake_models(),
                "contextmanager": contextmanager,
                "_attempt": ContextVar("test_attempt", default=None),
            },
        )
        for row in (self.old, self.good, self.late):
            row.project_id, row.total_errors = "p", 1
        self.analyses = self.manager(
            "TraceErrorAnalysis", [self.late, self.old, self.good]
        )
        self.details = self.manager(
            "TraceErrorDetail",
            [
                SimpleNamespace(id="old", analysis_id="G0", analysis=self.old),
                SimpleNamespace(id="new", analysis_id="G1", analysis=self.good),
                SimpleNamespace(id="late", analysis_id="late-G0", analysis=self.late),
            ],
        )

    def install_snapshot(self, detail_filter=None):
        # Execute the real snapshot module against raw rows (not a bound reader).
        for detail in self.details.rows:
            detail.deleted = False
        raw_details = Rows(self.details.rows)
        if detail_filter is not None:
            raw_details = SimpleNamespace(filter=detail_filter)
        state = SimpleNamespace(
            get_state=lambda *a, **k: (
                SimpleNamespace(**vars(self.receipts.rows[0]))
                if self.receipts.rows
                else None
            )
        )
        snapshot = load(
            "tracer/services/analysis_result_snapshot.py",
            {
                "transaction": SimpleNamespace(atomic=nullcontext),
            },
        )
        p = patch.dict(
            "sys.modules",
            {
                "tracer.services.analysis_result_snapshot": module(
                    "snapshot", **vars(snapshot)
                ),
                "tracer.models.trace_error_analysis": module(
                    "models",
                    TraceErrorAnalysis=SimpleNamespace(
                        objects=self.analyses, _base_manager=Rows(self.analyses.rows)
                    ),
                    TraceErrorDetail=SimpleNamespace(_base_manager=raw_details),
                    AnalysisErrorGroup=SimpleNamespace(_base_manager=Rows([])),
                ),
                "tracer.queries": module("queries", background_analysis_state=state),
            },
        )
        p.start()
        self.addCleanup(p.stop)
        return snapshot

    def manager(self, name, rows):
        manager = self.binding.CompletedAnalysisManager()
        manager.model = type(name, (), {})
        manager.rows = rows
        return manager

    def reader_method(self, name):
        self.install_snapshot()
        tree = ast.parse((ROOT / "tracer/queries/error_analysis.py").read_text())
        owner = next(
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "TraceErrorAnalysisDB"
        )
        method = next(n for n in owner.body if getattr(n, "name", None) == name)
        ns = {
            "TraceErrorAnalysis": SimpleNamespace(objects=self.analyses),
            "TraceErrorDetail": SimpleNamespace(objects=self.details),
            "QuerySet": object,
            "logger": Mock(),
            "Any": object,
        }
        ns.update(
            Count=lambda field: ("count", field), Sum=lambda field: ("sum", field)
        )
        # Annotation forward evaluation is not part of the query contract.
        method.returns = None
        exec(compile(ast.Module(body=[method], type_ignores=[]), name, "exec"), ns)
        return ns[name]

    def test_feed_uses_bound_manager_not_late_aborted_row(self):
        self.install_snapshot()
        feed_tree = ast.parse((ROOT / "tracer/queries/feed.py").read_text())
        get = next(
            n for n in feed_tree.body if getattr(n, "name", None) == "get_deep_analysis"
        )
        ns = {
            "DeepAnalysisResponse": SimpleNamespace,
            "TraceErrorAnalysis": SimpleNamespace(objects=self.analyses),
            "TraceErrorDetail": SimpleNamespace(objects=self.details),
            "_cluster_qs_for_access": lambda *a: SimpleNamespace(exists=lambda: True),
            "_cluster_has_trace": lambda *a: True,
            "background_analysis_state": SimpleNamespace(
                get_state=lambda *a, **k: SimpleNamespace(
                    status="completed", result_analysis_id="G1"
                )
            ),
            "deep_analysis_state": SimpleNamespace(status=lambda *a, **k: "done"),
            "_build_root_causes": lambda details: [d.id for d in details],
            "_build_recommendations": lambda *a: [],
        }
        for detail in self.details.rows:
            detail.urgency_to_fix = detail.immediate_fix = ""
        exec(compile(ast.Module(body=[get], type_ignores=[]), "feed", "exec"), ns)
        response = ns[get.name]("cluster", "trace")
        self.assertEqual(response.status, "done")
        self.assertEqual(response.root_causes, ["new"])

    def test_nonfeed_drawer_query_uses_only_completed_result(self):
        result = self.reader_method("get_analysis_by_trace_id")(None, "trace")
        self.assertIs(result, self.good)
        self.receipts.rows[0].status = "aborted"
        self.assertIsNone(self.reader_method("get_analysis_by_trace_id")(None, "trace"))

    def test_falcon_cluster_details_and_scores_use_same_bound_result(self):
        scores = list(self.reader_method("get_analyses_for_traces")(None, ["trace"]))
        details = list(
            self.reader_method("get_error_details_for_traces")(None, ["trace"])
        )
        self.assertEqual([a.id for a in scores], ["G1"])
        self.assertEqual([d.id for d in details], ["new"])

    def test_project_analyzed_set_excludes_aborted_and_pending(self):
        tree = ast.parse(
            (ROOT / "ai_tools/tools/tracing/analyze_project_traces.py").read_text()
        )
        expression = next(
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "already_analyzed"
                for t in n.targets
            )
        )
        code = compile(ast.Expression(expression), "project-analyzed-set", "eval")
        ns = {
            "TraceErrorAnalysis": SimpleNamespace(objects=self.analyses),
            "project": "p",
        }
        self.assertEqual(eval(code, ns), {"trace"})
        for status in ("aborted", "running", "retry_pending"):
            self.receipts.rows[0].status = status
            self.assertEqual(eval(code, ns), set())

    def test_embedding_ingestion_does_not_dispatch_for_aborted_partial(self):
        self.receipts.rows[0].status = "aborted"
        # The actual method returns before importing any embedding/provider code.
        ingest = self.reader_method("ingest_trace_error_embeddings")
        self.assertEqual(ingest(None, "trace"), 0)

    def test_task_report_totals_are_current_results_not_historical_attempt_counters(
        self,
    ):
        totals = self.reader_method("get_bound_result_totals")
        self.assertEqual(
            totals(None, "p"), {"total_traces_analyzed": 1, "total_errors_found": 1}
        )
        self.receipts.rows[0].status = "aborted"
        self.assertEqual(
            totals(None, "p"), {"total_traces_analyzed": 0, "total_errors_found": 0}
        )

    def test_reports_and_reverse_managers_filter_unbound_rows(self):
        for name in ("TraceErrorDetail", "AnalysisErrorGroup"):
            manager = self.manager(name, self.details.rows)
            self.assertEqual([r.id for r in manager.all()], ["new"])
            self.receipts.rows[0].status = "running"
            self.assertEqual(list(manager.all()), [])
            self.receipts.rows[0].status = "completed"


if __name__ == "__main__":
    unittest.main()
