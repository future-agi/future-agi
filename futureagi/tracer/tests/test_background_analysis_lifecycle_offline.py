"""Socket-blocked probes of actual lifecycle/entry definitions, not DB evidence.

Run: python3 -I <this file>. MigrationExecutor coverage lives in the sibling
Django test and must run separately on the approved database runner.
"""

import ast
import unittest
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
STATE = "tracer/queries/background_analysis_state.py"
MIGRATION = "0121_background_trace_analysis_state"


def load(path, namespace):
    tree = ast.parse((ROOT / path).read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    for node in nodes:
        if isinstance(node, ast.FunctionDef):
            node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec"), namespace)
    return SimpleNamespace(**namespace)


def module(name, **attrs):
    mod = ModuleType(name)
    mod.__dict__.update(attrs)
    return mod


class Query:
    def __init__(self, store, filters=None):
        self.store = store
        self.filters = filters or {}

    def select_for_update(self):
        return self

    def filter(self, **filters):
        return Query(self.store, {**self.filters, **filters})

    def matches(self, row):
        for key, value in self.filters.items():
            field, _, lookup = key.partition("__")
            actual = getattr(row, field)
            if lookup == "in":
                if str(actual) not in [str(v) for v in value]:
                    return False
            elif str(actual) != str(value):
                return False
        return True

    def first(self):
        self.store.reads += 1
        return next(
            (SimpleNamespace(**vars(r)) for r in self.store.rows if self.matches(r)),
            None,
        )

    def update(self, **values):
        self.store.predicates.append(self.filters)
        matches = [r for r in self.store.rows if self.matches(r)]
        for row in matches:
            vars(row).update(values)
        return len(matches)


class Store(Query):
    def __init__(self):
        super().__init__(self)
        self.rows = []
        self.reads = 0
        self.predicates = []

    def get_or_create(self, defaults, **identity):
        row = self.filter(**identity).first()
        if row:
            return row, False
        row = SimpleNamespace(
            id=uuid4(), reason_code="", result_analysis_id=None, **identity, **defaults
        )
        self.rows.append(row)
        return SimpleNamespace(**vars(row)), True


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        def denied(*a, **kw):
            raise AssertionError("Offline lifecycle tests forbid network")

        for target in (
            "socket.create_connection",
            "socket.socket.connect",
            "socket.socket.connect_ex",
            "socket.getaddrinfo",
        ):
            p = patch(target, denied)
            p.start()
            self.addCleanup(p.stop)
        self.store = Store()
        self.trace, self.project = str(uuid4()), str(uuid4())
        self.state = load(
            STATE,
            {
                "BackgroundTraceAnalysisState": SimpleNamespace(objects=self.store),
                "uuid4": uuid4,
                "timezone": SimpleNamespace(now=lambda: datetime.now(UTC)),
            },
        )
        self.cache = Mock()
        self.cache.get.return_value = None
        self.poll = load(
            "tracer/queries/deep_analysis_state.py",
            {
                "cache": self.cache,
                "_TTL_SECONDS": 3600,
                "background_analysis_state": self.state,
            },
        )
        self.results = Mock()
        self.results.filter.return_value.exists.return_value = True
        self.modules = {
            "tracer.services.analysis_result_lock": module(
                "lock", result_lock=lambda *a: nullcontext()
            ),
            "django.db": module(
                "django.db", transaction=SimpleNamespace(atomic=nullcontext)
            ),
            "tracer.models.trace_error_analysis": module(
                "results",
                TraceErrorAnalysis=SimpleNamespace(_base_manager=self.results),
            ),
            "tracer.queries": module(
                "tracer.queries",
                background_analysis_state=self.state,
                deep_analysis_state=self.poll,
            ),
        }
        p = patch.dict("sys.modules", self.modules)
        p.start()
        self.addCleanup(p.stop)

    def begin(self):
        return self.state.begin(self.project, self.trace)

    def abort(self):
        generation = self.begin()
        self.assertTrue(self.state.admit(self.project, self.trace, generation))
        self.assertTrue(
            self.state.abort(
                self.project, self.trace, generation, "analysis_access_revoked"
            )
        )
        return generation

    def test_partial_findings_and_cache_eviction_never_mean_completed(self):
        self.abort()
        for cache_value in ("analysis_access_revoked", None):
            self.cache.get.return_value = cache_value
            self.assertEqual(self.poll.status(self.trace, has_analysis=True), "failed")
            self.assertEqual(
                self.poll.failure_code(self.trace), "analysis_access_revoked"
            )
        self.cache.get.assert_not_called()

    def test_retry_uses_new_generation_and_stays_running_until_completion(self):
        old = self.abort()
        new = self.begin()
        self.assertNotEqual(new, old)
        self.assertEqual(self.state.get_state(self.trace).status, "retry_pending")
        self.assertEqual(self.poll.status(self.trace, has_analysis=True), "running")
        self.assertIsNone(self.begin())
        self.assertTrue(self.state.admit(self.project, self.trace, new))
        self.assertEqual(self.poll.status(self.trace, has_analysis=True), "running")
        self.assertTrue(self.state.complete(self.project, self.trace, new))
        self.assertEqual(self.poll.status(self.trace, has_analysis=True), "done")

    def test_late_finalizer_cannot_resurrect_abort_or_overwrite_new_generation(self):
        old = self.abort()
        self.assertFalse(self.state.complete(self.project, self.trace, old))
        new = self.begin()
        self.assertTrue(self.state.admit(self.project, self.trace, new))
        self.assertFalse(self.state.complete(self.project, self.trace, old))
        self.assertFalse(
            self.state.abort(
                self.project, self.trace, old, "analysis_deadline_exceeded"
            )
        )
        self.assertEqual(self.state.get_state(self.trace).status, "running")
        self.assertTrue(self.state.complete(self.project, self.trace, new))
        self.assertFalse(
            self.state.abort(
                self.project, self.trace, new, "analysis_deadline_exceeded"
            )
        )

    def test_worker_admission_and_every_update_are_generation_status_fenced(self):
        old = self.abort()
        new = self.begin()
        self.assertFalse(self.state.admit(self.project, self.trace, old))
        self.assertTrue(self.state.admit(self.project, self.trace, new))
        self.assertFalse(self.state.admit(self.project, self.trace, new))
        self.assertFalse(self.state.admit(str(uuid4()), self.trace, new))
        for predicate in self.store.predicates:
            self.assertIn("generation", predicate)
            self.assertIn("status", predicate)
            self.assertIn("project_id", predicate)
            self.assertIn("trace_id", predicate)

    def test_legacy_completion_and_project_scoping(self):
        self.assertEqual(self.poll.status(self.trace, has_analysis=True), "done")
        self.abort()
        self.assertIsNone(self.state.get_state(self.trace, project_ids=[str(uuid4())]))
        self.assertIsNone(self.state.get_state(self.trace, project_ids=[]))

    def feed(self):
        analyses = Mock()
        analyses.filter.return_value.exists.return_value = True
        analyses.filter.return_value.order_by.return_value.first.return_value = (
            SimpleNamespace()
        )
        queued = Mock()
        p = patch.dict(
            "sys.modules",
            {
                "tracer.services.analysis_result_snapshot": module(
                    "snapshot",
                    trace_snapshot=lambda trace_id, project_ids=None: (
                        self.state.get_state(trace_id, project_ids=project_ids),
                        None,
                    ),
                ),
                "tracer.tasks": module(
                    "tracer.tasks",
                    run_deep_analysis_on_demand=SimpleNamespace(delay=queued),
                ),
                # Lifecycle dispatch now consults the switch; pin it ON here.
                "tfc.capabilities.background_analysis": module(
                    "gate", background_analysis_enabled=lambda: True
                ),
            },
        )
        p.start()
        self.addCleanup(p.stop)
        tree = ast.parse((ROOT / "tracer/queries/feed.py").read_text())
        funcs = [
            n
            for n in tree.body
            if getattr(n, "name", None)
            in ("get_deep_analysis", "dispatch_deep_analysis")
        ]
        ns = {
            "DeepAnalysisResponse": SimpleNamespace,
            "DeepAnalysisDispatchResponse": SimpleNamespace,
            "TraceErrorAnalysis": SimpleNamespace(objects=analyses),
            "deep_analysis_state": self.poll,
            "background_analysis_state": self.state,
            "_cluster_qs_for_access": lambda *a: SimpleNamespace(exists=lambda: True),
            "_cluster_has_trace": lambda *a: True,
        }
        exec(compile(ast.Module(body=funcs, type_ignores=[]), "feed.py", "exec"), ns)
        return SimpleNamespace(**ns), analyses, queued

    def test_real_feed_retry_get_and_second_post_never_publish_partial_findings(self):
        old = self.abort()
        feed, analyses, queued = self.feed()
        response = feed.get_deep_analysis("cluster", self.trace, [self.project])
        self.assertEqual(response.status, "failed")
        analyses.filter.assert_not_called()  # durable row BEFORE findings
        response = feed.dispatch_deep_analysis("cluster", self.trace, [self.project])
        self.assertEqual(response.status, "running")
        new = self.state.get_state(self.trace).generation
        self.assertNotEqual(str(old), str(new))
        self.assertEqual(queued.call_args.kwargs["generation"], str(new))
        self.assertEqual(queued.call_args.kwargs["project_id"], self.project)
        self.assertTrue(queued.call_args.args[1])
        self.assertEqual(
            feed.get_deep_analysis("cluster", self.trace, [self.project]).status,
            "running",
        )
        self.assertEqual(
            feed.dispatch_deep_analysis("cluster", self.trace, [self.project]).status,
            "running",
        )
        queued.assert_called_once()

    def test_dispatch_persists_generation_before_queue_and_aborts_failed_dispatch(self):
        dispatch = load(
            "tracer/services/background_analysis_dispatch.py",
            {"background_analysis_state": self.state},
        )
        payloads = []

        def apply_async(args=None, kwargs=None, **options):
            row = self.state.get_state(self.trace)
            self.assertEqual(row.status, "retry_pending")
            self.assertEqual(kwargs["generations"][self.trace], str(row.generation))
            payloads.append(kwargs)
            return "queued"

        task = SimpleNamespace(apply_async=apply_async)
        wrapped = dispatch.durable_background_dispatch(task)
        self.assertEqual(
            wrapped.delay(self.project, [self.trace], requesting_user_id="actor"),
            "queued",
        )
        self.assertEqual(payloads[0]["requesting_user_id"], "actor")
        generation = self.state.get_state(self.trace).generation
        self.state.abort(
            self.project,
            self.trace,
            generation,
            "analysis_failed",
            expected_status="retry_pending",
        )
        task = SimpleNamespace(apply_async=Mock(side_effect=RuntimeError("queue down")))
        wrapped = dispatch.durable_background_dispatch(task)
        with self.assertRaises(RuntimeError):
            wrapped.delay(self.project, [self.trace])
        self.assertEqual(self.state.get_state(self.trace).status, "aborted")

    def test_worker_owns_completion_only_after_finalizer_returns(self):
        from contextlib import nullcontext

        p = patch.dict(
            "sys.modules",
            {
                "tfc.middleware.workspace_context": module(
                    "workspace", workspace_context=lambda *a: nullcontext()
                )
            },
        )
        p.start()
        self.addCleanup(p.stop)
        ns = {
            "background_analysis_state": self.state,
            "attempt_scope": lambda *a: nullcontext(),
        }
        tree = ast.parse((ROOT / "tracer/tasks/error_analysis.py").read_text())
        worker = next(
            n
            for n in tree.body
            if getattr(n, "name", None) == "_falcon_analyze_single_trace"
        )
        inner = next(
            n
            for n in tree.body
            if getattr(n, "name", None) == "_falcon_analyze_trace_in_scope"
        )
        self.assertNotIn("clear_background_running", ast.unparse(inner))
        self.assertNotIn("background_analysis_state.complete", ast.unparse(inner))
        generation = self.begin()

        def finalize(*a):
            self.assertEqual(self.state.get_state(self.trace).status, "running")
            self.state.abort(
                self.project, self.trace, generation, "analysis_deadline_exceeded"
            )
            return {"success": True, "trace_id": self.trace}

        ns["_falcon_analyze_trace_in_scope"] = finalize
        exec(
            compile(ast.Module(body=[worker], type_ignores=[]), "worker.py", "exec"), ns
        )
        result = ns["_falcon_analyze_single_trace"](
            self.trace, self.project, generation=generation
        )
        self.assertFalse(result["success"])
        self.assertEqual(self.state.get_state(self.trace).status, "aborted")
        ns["_falcon_analyze_trace_in_scope"] = Mock()
        ns["_falcon_analyze_single_trace"](
            self.trace, self.project, generation=generation
        )
        ns["_falcon_analyze_trace_in_scope"].assert_not_called()


class CacheExpiryRegressionTests(unittest.TestCase):
    def test_actual_poll_does_not_turn_partial_findings_into_done_after_eviction(self):
        cache = Mock()
        cache.get.return_value = None
        state = SimpleNamespace(
            get_state=lambda *a, **kw: SimpleNamespace(
                status="aborted", reason_code="analysis_access_revoked"
            )
        )
        poll = load(
            "tracer/queries/deep_analysis_state.py",
            {
                "cache": cache,
                "_TTL_SECONDS": 3600,
                "background_analysis_state": state,
            },
        )
        self.assertEqual(poll.status(str(uuid4()), has_analysis=True), "failed")


class MigrationShapeTests(unittest.TestCase):
    def test_one_additive_reversible_create_model_only(self):
        path = ROOT / "tracer/migrations" / f"{MIGRATION}.py"
        self.assertTrue(path.exists(), "additive lifecycle migration missing")
        tree = ast.parse(path.read_text())
        migration = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        assignments = {
            n.targets[0].id: n.value
            for n in migration.body
            if isinstance(n, ast.Assign)
        }
        self.assertEqual(
            ast.literal_eval(assignments["dependencies"]),
            [("tracer", "0120_merge_sharedlink_and_agent_observe")],
        )
        operations = assignments["operations"].elts
        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].func.attr, "CreateModel")
        source = path.read_text()
        self.assertIn("tracer_background_trace_analysis_state", source)
        self.assertIn("UniqueConstraint", source)
        self.assertNotIn('to="tracer.trace"', source)


if __name__ == "__main__":
    unittest.main()
