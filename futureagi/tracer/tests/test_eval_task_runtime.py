"""
Eval-task runtime tests.

Drive ``process_eval_task`` end-to-end against real Postgres with the eval
engine, billing layer, and Temporal stubbed via fixtures from conftest.py:
  - ``stub_run_eval``, ``stub_cost_log``: skip the engine + cost layers.
  - ``inline_temporal``: route ``.delay`` -> ``.run_sync`` so dispatch executes inline.
  - ``track_eval_dispatch``: spy on ``.delay`` to assert dispatcher fan-out without running.

These tests pin span-level behaviour plus the trace/session dispatch
counterparts (all three target types live in this file).
"""

import pytest

# Break a pre-existing import cycle: ``tracer.utils.eval_tasks`` imports
# ``tracer.utils.eval``, which imports ``model_hub.tasks.user_evaluation``,
# which loads ``model_hub.tasks.__init__`` -- and that __init__ imports
# from ``tracer.utils.eval_tasks``. In production Django app autoloading
# walks ``model_hub.tasks`` before anyone imports ``tracer.utils.eval_tasks``
# directly, so the cycle resolves. Test files don't get that ordering for
# free; importing ``model_hub.tasks`` first here lets the chain unwind via
# the user_evaluation submodule (which doesn't depend on tracer.utils.eval).
import model_hub.tasks  # noqa: F401, E402

from tracer.models.custom_eval_config import CustomEvalConfig  # noqa: E402
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType  # noqa: E402
from tracer.models.observation_span import EvalLogger  # noqa: E402
from tracer.utils.eval_tasks import process_eval_task  # noqa: E402


@pytest.fixture
def observe_eval_task(db, populated_observe_project, eval_template):
    """Historical eval task scoped to ``populated_observe_project``.

    Returns ``{"task": EvalTask, "config": CustomEvalConfig, "project": Project}``.
    """
    project = populated_observe_project["project"]
    config = CustomEvalConfig.objects.create(
        project=project,
        eval_template=eval_template,
        name="Test Span Eval",
        config={"output": "Pass/Fail"},
        mapping={"input": "input", "output": "output"},
        model="turing_large",
    )
    task = EvalTask.objects.create(
        project=project,
        name="Test spans task",
        filters={"project_id": str(project.id)},
        sampling_rate=100.0,
        run_type=RunType.HISTORICAL,
        spans_limit=1000,
        status=EvalTaskStatus.PENDING,
    )
    task.evals.add(config)
    return {"task": task, "config": config, "project": project}


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestProcessEvalTaskSpans:
    """Span-level dispatcher behaviour locked in as a regression net."""

    def test_creates_eval_logger_per_span(
        self,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """One EvalLogger row per (span, eval) pair after a single dispatch tick."""
        task = observe_eval_task["task"]

        process_eval_task._original_func(str(task.id))

        rows = EvalLogger.objects.filter(eval_task_id=str(task.id), deleted=False)
        # 2 sessions * 2 traces * 3 spans = 12 spans, * 1 eval = 12 EvalLogger rows
        assert rows.count() == 12
        assert all(r.observation_span_id is not None for r in rows)
        span_ids = {s.id for s in populated_observe_project["spans"]}
        assert {r.observation_span_id for r in rows} == span_ids

    def test_respects_sampling_rate(
        self,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """sampling_rate=50 with 12 spans -> int(12 * 0.5) = 6 sampled."""
        task = observe_eval_task["task"]
        task.sampling_rate = 50.0
        task.save()

        process_eval_task._original_func(str(task.id))

        count = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False
        ).count()
        assert count == 6

    def test_respects_spans_limit(
        self,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """spans_limit caps how many entities the dispatcher hands out per tick."""
        task = observe_eval_task["task"]
        task.spans_limit = 3
        task.save()

        process_eval_task._original_func(str(task.id))

        count = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False
        ).count()
        assert count == 3

    def test_dedup_on_second_tick(
        self,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """Second tick reuses spanids_processed; no additional EvalLogger rows."""
        task = observe_eval_task["task"]

        process_eval_task._original_func(str(task.id))
        first_count = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False
        ).count()

        process_eval_task._original_func(str(task.id))
        second_count = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False
        ).count()

        assert second_count == first_count


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestEvalTaskStatusTransitions:
    """End-to-end status flow: PENDING -> RUNNING -> COMPLETED / FAILED."""

    def test_pending_to_running_to_completed(
        self,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """First tick dispatches inline; second tick finds drain complete and flips to COMPLETED."""
        task = observe_eval_task["task"]
        assert task.status == EvalTaskStatus.PENDING

        process_eval_task._original_func(str(task.id))
        task.refresh_from_db()
        # Dispatch tick keeps status at RUNNING — the drain check happens on the next tick
        assert task.status == EvalTaskStatus.RUNNING

        process_eval_task._original_func(str(task.id))
        task.refresh_from_db()
        assert task.status == EvalTaskStatus.COMPLETED

    def test_failed_on_dispatch_exception(
        self,
        populated_observe_project,
        observe_eval_task,
        monkeypatch,
    ):
        """Any exception inside the dispatch loop flips status to FAILED."""
        import tracer.utils.eval as eval_module

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated dispatch failure")

        monkeypatch.setattr(
            eval_module.evaluate_observation_span_observe, "delay", _boom
        )

        task = observe_eval_task["task"]
        process_eval_task._original_func(str(task.id))
        task.refresh_from_db()
        assert task.status == EvalTaskStatus.FAILED

    def test_drain_stall_completes_task_with_warning(
        self,
        populated_observe_project,
        observe_eval_task,
        track_eval_dispatch,
        monkeypatch,
        caplog,
    ):
        """Dispatch without execution -> next tick detects stall, logs warning, flips to COMPLETED.

        Note on `failed_spans`: the dispatcher *intends* to surface the stall
        summary on `EvalTask.failed_spans`, but the current code path
        (`tracer/utils/eval_tasks.py:282-329`) saves the summary via a
        `select_for_update`'d ref then immediately calls `eval_task.save()`
        on the stale local reference WITHOUT `update_fields`, clobbering the
        just-written list back to []. This is a pre-existing bug — test pins
        the observable behaviour (status flip + warning log) so future fixes
        that correctly persist `failed_spans` will surface as a deliberate
        update to this test, not a silent regression.
        """
        import logging

        monkeypatch.setattr("tracer.utils.eval_tasks._DRAIN_STALL_SECONDS", 0)

        task = observe_eval_task["task"]
        task.spans_limit = 12
        task.save()

        process_eval_task._original_func(str(task.id))  # tick 1: dispatch only
        assert len(track_eval_dispatch) == 12
        assert EvalLogger.objects.filter(eval_task_id=str(task.id)).count() == 0

        with caplog.at_level(logging.WARNING, logger="tracer.utils.eval_tasks"):
            process_eval_task._original_func(str(task.id))  # tick 2: drain check trips stall

        task.refresh_from_db()
        assert task.status == EvalTaskStatus.COMPLETED
        # The "eval_task_completed_with_drops" warning is the user-observable
        # signal that a stall was detected (logged for SRE / Sentry).
        assert any(
            "eval_task_completed_with_drops" in record.getMessage()
            for record in caplog.records
        ), "expected stall-detection warning in logs"


# ────────────────────────────────────────────────────────────────────────
# Trace + session dispatch + intentional conflation pin
# ────────────────────────────────────────────────────────────────────────


@pytest.fixture
def observe_trace_task(db, populated_observe_project, eval_template):
    """Historical trace-level eval task scoped to ``populated_observe_project``.

    Maps the eval's ``input``/``output`` keys to trace-level fields so the
    new ``_process_trace_mapping`` resolver succeeds without LLM/run_eval
    being involved (engine is stubbed).
    """
    from tracer.models.eval_task import RowType

    project = populated_observe_project["project"]
    config = CustomEvalConfig.objects.create(
        project=project,
        eval_template=eval_template,
        name="Test Trace Eval",
        config={"output": "Pass/Fail"},
        mapping={"input": "input", "output": "output"},
        model="turing_large",
    )
    task = EvalTask.objects.create(
        project=project,
        name="Test traces task",
        filters={"project_id": str(project.id)},
        sampling_rate=100.0,
        run_type=RunType.HISTORICAL,
        spans_limit=1000,
        status=EvalTaskStatus.PENDING,
        row_type=RowType.TRACES,
    )
    task.evals.add(config)
    return {"task": task, "config": config, "project": project}


@pytest.fixture
def observe_session_task(db, populated_observe_project, eval_template):
    """Historical session-level eval task.

    Maps to the trace/span hierarchy via dot notation — ``traces.0.input``
    walks the session's first trace's ``input`` field. This exercises the
    full ``_resolve_session_path`` -> ``_resolve_trace_path`` -> field
    lookup chain. The eval engine itself is stubbed via ``stub_run_eval``,
    so the resolved values just need to be non-MISSING.
    """
    from tracer.models.eval_task import RowType

    project = populated_observe_project["project"]
    config = CustomEvalConfig.objects.create(
        project=project,
        eval_template=eval_template,
        name="Test Session Eval",
        config={"output": "Pass/Fail"},
        mapping={"input": "traces.0.input", "output": "traces.0.output"},
        model="turing_large",
    )
    task = EvalTask.objects.create(
        project=project,
        name="Test sessions task",
        filters={"project_id": str(project.id)},
        sampling_rate=100.0,
        run_type=RunType.HISTORICAL,
        spans_limit=1000,
        status=EvalTaskStatus.PENDING,
        row_type=RowType.SESSIONS,
    )
    task.evals.add(config)
    return {"task": task, "config": config, "project": project}


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestProcessEvalTaskTraces:
    """Trace-level dispatcher: one EvalLogger per (trace, eval_config) pair."""

    def test_creates_one_eval_logger_per_trace(
        self,
        populated_observe_project,
        observe_trace_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """4 traces × 1 eval -> 4 EvalLogger rows, all target_type='trace'."""
        task = observe_trace_task["task"]

        process_eval_task._original_func(str(task.id))

        rows = EvalLogger.objects.filter(eval_task_id=str(task.id), deleted=False)
        # populated_observe_project has 2 sessions × 2 traces × 3 spans = 4 traces
        assert rows.count() == 4
        assert all(r.target_type == "trace" for r in rows)
        # Every row carries trace + observation_span + null trace_session
        assert all(r.trace_id is not None for r in rows)
        assert all(r.observation_span_id is not None for r in rows)
        assert all(r.trace_session_id is None for r in rows)

    def test_anchors_to_root_span(
        self,
        populated_observe_project,
        observe_trace_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """Each trace row's observation_span = that trace's root span (parent_span_id IS NULL)."""
        task = observe_trace_task["task"]
        process_eval_task._original_func(str(task.id))

        rows = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False
        ).select_related("observation_span", "trace")
        for row in rows:
            anchor = row.observation_span
            assert anchor.parent_span_id is None, (
                f"Trace eval anchored to a non-root span ({anchor.id}); "
                f"populated_observe_project's first span per trace is the root."
            )
            # Anchor span must belong to the row's trace
            assert anchor.trace_id == row.trace_id

    def test_falls_back_to_earliest_span_when_no_root(
        self,
        populated_observe_project,
        observe_trace_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """If a trace has no parent_span_id IS NULL span, anchor falls back to earliest."""
        # Strip root status from every span on one trace by setting a
        # parent_span_id (use the second span as parent for the first).
        # Pick traces[0] since populated_observe_project gives us a list.
        from tracer.models.observation_span import ObservationSpan

        trace = populated_observe_project["traces"][0]
        spans = list(trace.observation_spans.order_by("start_time", "id"))
        # Make every span have a parent (the earliest-by-start_time still wins fallback)
        for i, sp in enumerate(spans):
            sp.parent_span_id = spans[(i + 1) % len(spans)].id
            sp.save(update_fields=["parent_span_id"])

        task = observe_trace_task["task"]
        process_eval_task._original_func(str(task.id))

        # The eval row for this trace should anchor on the earliest span
        row = EvalLogger.objects.get(
            eval_task_id=str(task.id),
            trace_id=trace.id,
            deleted=False,
        )
        earliest = min(spans, key=lambda s: s.start_time)
        assert row.observation_span_id == earliest.id

    def test_skips_when_zero_spans(
        self,
        populated_observe_project,
        observe_trace_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """A trace with zero spans -> no EvalLogger row; failed_spans entry instead."""
        from tracer.models.trace import Trace

        # Add a zero-span trace to the project. It won't match the
        # span-filter-driven candidate set used by the dispatcher (no spans
        # → no trace_id in the inner query), so we directly invoke the
        # evaluator with this trace_id to exercise the anchor-miss branch.
        from tracer.utils.eval import evaluate_trace_observe

        empty_trace = Trace.objects.create(
            project=populated_observe_project["project"],
            name="empty trace",
            input={},
            output={},
        )

        task = observe_trace_task["task"]
        config = observe_trace_task["config"]

        result = evaluate_trace_observe._original_func(
            trace_id=str(empty_trace.id),
            custom_eval_config_id=str(config.id),
            eval_task_id=str(task.id),
        )
        assert result is False

        rows = EvalLogger.objects.filter(trace_id=empty_trace.id)
        assert rows.count() == 0

        task.refresh_from_db()
        assert task.failed_spans
        assert any(
            entry.get("trace_id") == str(empty_trace.id) for entry in task.failed_spans
        )

    def test_dedup_on_second_tick(
        self,
        populated_observe_project,
        observe_trace_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """Second tick reuses processed_ids; no additional EvalLogger rows."""
        task = observe_trace_task["task"]

        process_eval_task._original_func(str(task.id))
        first = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False
        ).count()

        process_eval_task._original_func(str(task.id))
        second = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False
        ).count()

        assert second == first

    def test_filter_via_span_attribute(
        self,
        populated_observe_project,
        observe_trace_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """Span-level filter narrows which traces are eligible."""
        task = observe_trace_task["task"]
        # Restrict to traces whose spans include observation_type='llm' —
        # populated_observe_project alternates llm/tool, so all 4 traces
        # have at least one llm span and qualify.
        task.filters = {
            "project_id": str(populated_observe_project["project"].id),
            "observation_type": "llm",
        }
        task.save()

        process_eval_task._original_func(str(task.id))
        rows = EvalLogger.objects.filter(eval_task_id=str(task.id), deleted=False)
        assert rows.count() == 4
        # Tighten: filter by an observation_type that no spans have
        task.refresh_from_db()
        # Reset the dispatch state so we can re-run cleanly
        from tracer.models.eval_task import EvalTaskLogger

        EvalTaskLogger.objects.filter(eval_task=task).update(
            offset=0, spanids_processed=[]
        )
        EvalLogger.objects.filter(eval_task_id=str(task.id)).delete()
        task.filters = {
            "project_id": str(populated_observe_project["project"].id),
            "observation_type": "guardrail",  # no spans of this type
        }
        task.save()
        process_eval_task._original_func(str(task.id))
        rows = EvalLogger.objects.filter(eval_task_id=str(task.id), deleted=False)
        assert rows.count() == 0


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestProcessEvalTaskSessions:
    """Session-level dispatcher: one EvalLogger per (session, eval_config) pair."""

    def test_creates_one_eval_logger_per_session(
        self,
        populated_observe_project,
        observe_session_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """2 sessions × 1 eval -> 2 EvalLogger rows, all target_type='session'."""
        task = observe_session_task["task"]

        process_eval_task._original_func(str(task.id))

        rows = EvalLogger.objects.filter(eval_task_id=str(task.id), deleted=False)
        assert rows.count() == 2
        assert all(r.target_type == "session" for r in rows)

    def test_eval_logger_has_null_span_and_trace(
        self,
        populated_observe_project,
        observe_session_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """Session rows have NULL observation_span and trace, only trace_session set."""
        task = observe_session_task["task"]
        process_eval_task._original_func(str(task.id))

        for row in EvalLogger.objects.filter(eval_task_id=str(task.id), deleted=False):
            assert row.observation_span_id is None
            assert row.trace_id is None
            assert row.trace_session_id is not None

    def test_dedup_on_second_tick(
        self,
        populated_observe_project,
        observe_session_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        task = observe_session_task["task"]

        process_eval_task._original_func(str(task.id))
        first = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False
        ).count()

        process_eval_task._original_func(str(task.id))
        second = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False
        ).count()

        assert second == first


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestRowTypeConflationOnRootSpan:
    """End-to-end pin: span eval and trace eval anchor to the same root span.

    A user querying ``EvalLogger.filter(observation_span_id=root.id)`` after
    running both a span-level and a trace-level task on overlapping data
    should see both rows. The explicit ``target_type='span'`` filter is the
    escape hatch for callers that want strict span semantics.
    """

    def test_span_eval_and_trace_eval_on_same_root_span_both_visible_by_default(
        self,
        populated_observe_project,
        observe_eval_task,
        observe_trace_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        # Run both tasks against the same project.
        process_eval_task._original_func(str(observe_eval_task["task"].id))
        process_eval_task._original_func(str(observe_trace_task["task"].id))

        # Pick one trace's root span. Each trace's first span (sp_idx=0) is
        # its root, per the populated_observe_project fixture.
        traces = populated_observe_project["traces"]
        first_trace = traces[0]
        root = (
            first_trace.observation_spans.filter(parent_span_id__isnull=True)
            .order_by("start_time", "id")
            .first()
        )
        assert root is not None

        rows_default = list(
            EvalLogger.objects.filter(
                observation_span_id=root.id, deleted=False
            )
        )
        target_types = sorted(r.target_type for r in rows_default)
        # At least one span row + one trace row (anchored to this root)
        assert "span" in target_types
        assert "trace" in target_types

        # Explicit span-only filter narrows to span rows
        rows_span_only = list(
            EvalLogger.objects.filter(
                observation_span_id=root.id, deleted=False, target_type="span"
            )
        )
        assert all(r.target_type == "span" for r in rows_span_only)
        assert len(rows_span_only) < len(rows_default)
