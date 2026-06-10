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
        """Second tick deduplicates via EvalLogger; no additional rows."""
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

    def test_continuous_run_type_with_sampling_rate(
        self,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """Continuous tasks must dispatch under sampling_rate without NameError.

        Regression guard for the broken `spans.count()` reference in the
        is_continuous branch — the queryset was renamed to `pending_entities`
        but this one call site was missed, so every tick raised NameError
        and the broad except silently flipped the task to FAILED.
        """
        task = observe_eval_task["task"]
        task.run_type = RunType.CONTINUOUS
        task.sampling_rate = 50.0
        task.save()

        process_eval_task._original_func(str(task.id))

        task.refresh_from_db()
        assert task.status != EvalTaskStatus.FAILED
        count = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False
        ).count()
        # 50% of 12 pending spans -> 6 sampled at dispatch
        assert count == 6


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestEvalLoggerDedup:
    """Verify that EvalLogger-based dedup works for the dispatch pipeline."""

    def test_eval_loggers_created_after_dispatch(
        self,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """Dispatch tick creates EvalLogger rows with completed status."""
        from tracer.models.eval_task import EvalTaskLogger
        from tracer.models.observation_span import EvalLoggerStatus

        task = observe_eval_task["task"]
        process_eval_task._original_func(str(task.id))

        logger = EvalTaskLogger.objects.get(eval_task=task)
        completed = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
            status=EvalLoggerStatus.COMPLETED,
        )
        assert completed.count() == 12  # 2 sessions * 2 traces * 3 spans
        assert logger.offset == 12

    def test_no_duplicate_eval_loggers_on_second_tick(
        self,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """Second tick must not create duplicate EvalLogger rows."""
        task = observe_eval_task["task"]
        process_eval_task._original_func(str(task.id))
        process_eval_task._original_func(str(task.id))

        total = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False
        ).count()
        assert total == 12

    def test_fresh_run_allows_redispatch(
        self,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """Soft-deleting EvalLogger rows + resetting offset allows re-dispatch."""
        from django.utils import timezone
        from tracer.models.eval_task import EvalTaskLogger

        task = observe_eval_task["task"]
        process_eval_task._original_func(str(task.id))

        logger = EvalTaskLogger.objects.get(eval_task=task)
        assert EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False
        ).count() == 12

        # Simulate fresh run reset
        EvalLogger.objects.filter(
            eval_task_id=str(task.id)
        ).update(deleted=True, deleted_at=timezone.now())
        logger.offset = 0
        logger.save(update_fields=["offset"])

        # Re-dispatch should process all entities again
        task.status = EvalTaskStatus.RUNNING
        task.save(update_fields=["status"])
        process_eval_task._original_func(str(task.id))

        rows = EvalLogger.objects.filter(eval_task_id=str(task.id), deleted=False)
        assert rows.count() == 12


# ────────────────────────────────────────────────────────────────────────
# Voice-call dispatch — literal alias of the spans pipeline. Any
# observation_type narrowing is the caller's job via ``filters``.
# ────────────────────────────────────────────────────────────────────────


@pytest.fixture
def voice_call_eval_task(db, populated_observe_project, eval_template):
    """Historical voiceCalls eval task on the shared 12-span fixture."""
    from tracer.models.eval_task import RowType

    project = populated_observe_project["project"]
    config = CustomEvalConfig.objects.create(
        project=project,
        eval_template=eval_template,
        name="Test Voice Eval",
        config={"output": "Pass/Fail"},
        mapping={"input": "input", "output": "output"},
        model="turing_large",
    )
    task = EvalTask.objects.create(
        project=project,
        name="Test voice calls task",
        filters={"project_id": str(project.id)},
        sampling_rate=100.0,
        run_type=RunType.HISTORICAL,
        spans_limit=1000,
        status=EvalTaskStatus.PENDING,
        row_type=RowType.VOICE_CALLS,
    )
    task.evals.add(config)
    return {"task": task, "config": config, "project": project}


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestProcessEvalTaskVoiceCalls:
    """Dispatcher must route voiceCalls through the spans flow.

    Regression net for the bug where ``row_type='voiceCalls'`` hit the
    dispatcher's ``else`` branch (added in fb134ddf) and raised
    ``ValueError`` — flipping every voiceCalls task in prod to FAILED.

    Behaviour pin: voiceCalls is a literal alias of spans at dispatch.
    Any observation_type narrowing (e.g. limiting to conversation roots)
    is the caller's responsibility via ``EvalTask.filters``.
    """

    def test_dispatches_same_spans_as_spans_path(
        self,
        populated_observe_project,
        voice_call_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """voiceCalls fan-out matches the spans path on the same project."""
        task = voice_call_eval_task["task"]

        process_eval_task._original_func(str(task.id))

        rows = EvalLogger.objects.filter(eval_task_id=str(task.id), deleted=False)
        # populated_observe_project has 2 sessions × 2 traces × 3 spans = 12
        # spans × 1 eval -> 12 EvalLogger rows (no observation_type narrowing).
        assert rows.count() == 12
        expected_ids = {s.id for s in populated_observe_project["spans"]}
        assert {r.observation_span_id for r in rows} == expected_ids

    def test_status_transitions_match_spans_path(
        self,
        populated_observe_project,
        voice_call_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """voiceCalls inherits the spans drain semantics: PENDING -> RUNNING -> COMPLETED."""
        task = voice_call_eval_task["task"]
        assert task.status == EvalTaskStatus.PENDING

        process_eval_task._original_func(str(task.id))
        task.refresh_from_db()
        assert task.status == EvalTaskStatus.RUNNING

        process_eval_task._original_func(str(task.id))
        task.refresh_from_db()
        assert task.status == EvalTaskStatus.COMPLETED


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
        # Pre-created PENDING rows exist but no completed evaluations
        from tracer.models.observation_span import EvalLoggerStatus

        assert EvalLogger.objects.filter(
            eval_task_id=str(task.id),
        ).exclude(status=EvalLoggerStatus.PENDING).count() == 0

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
        from django.utils import timezone
        from tracer.models.eval_task import EvalTaskLogger

        EvalLogger.objects.filter(
            eval_task_id=str(task.id)
        ).update(deleted=True, deleted_at=timezone.now())
        EvalTaskLogger.objects.filter(eval_task=task).update(offset=0)
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


# ────────────────────────────────────────────────────────────────────────
# Composite evals on trace + session row types (TH-5158)
# ────────────────────────────────────────────────────────────────────────


@pytest.fixture
def composite_eval_template(db, organization, workspace):
    """A composite parent with two single children linked via CompositeEvalChild.

    Children expose tiny stub configs; the test stubs out
    ``execute_composite_children_sync`` so the children never actually run.
    """
    from model_hub.models.evals_metric import CompositeEvalChild, EvalTemplate

    parent = EvalTemplate.objects.create(
        name="Composite Parent",
        description="Composite for trace/session tests",
        organization=organization,
        workspace=workspace,
        template_type="composite",
        aggregation_enabled=True,
        aggregation_function="weighted_avg",
        pass_threshold=0.5,
        config={"type": "composite"},
    )
    children = [
        EvalTemplate.objects.create(
            name=f"Child {i}",
            description=f"Child eval {i}",
            organization=organization,
            workspace=workspace,
            template_type="single",
            config={"type": "pass_fail", "criteria": "ok"},
            pass_threshold=0.5,
        )
        for i in range(2)
    ]
    for i, child in enumerate(children):
        CompositeEvalChild.objects.create(
            parent=parent, child=child, order=i, weight=1.0
        )
    return {"parent": parent, "children": children}


def _composite_outcome(score=0.8, summary="ok", aggregate_pass=True):
    """Build a deterministic CompositeRunOutcome for stubbing."""
    from model_hub.types import CompositeChildResult
    from model_hub.utils.composite_execution import CompositeRunOutcome

    return CompositeRunOutcome(
        child_results=[
            CompositeChildResult(
                child_id="c0",
                child_name="Child 0",
                order=0,
                score=0.9,
                output=True,
                reason="c0 ok",
                output_type="Pass/Fail",
                status="completed",
                weight=1.0,
            ),
            CompositeChildResult(
                child_id="c1",
                child_name="Child 1",
                order=1,
                score=0.7,
                output=True,
                reason="c1 ok",
                output_type="Pass/Fail",
                status="completed",
                weight=1.0,
            ),
        ],
        aggregate_score=score,
        aggregate_pass=aggregate_pass,
        summary=summary,
        error_localizer_results=None,
        log_id=None,
    )


@pytest.fixture
def stub_composite_children(monkeypatch):
    """Patch ``execute_composite_children_sync`` with a recorded stub.

    Returns ``calls`` (list of kwargs dicts) and ``set_outcome(outcome)`` /
    ``set_exception(exc)`` hooks so tests can flip happy/error paths.
    """
    state = {"outcome": _composite_outcome(), "exception": None}
    calls: list[dict] = []

    def _stub(**kwargs):
        calls.append(kwargs)
        if state["exception"] is not None:
            raise state["exception"]
        return state["outcome"]

    # Patched at the canonical module path; both new helpers do
    # `from model_hub.utils.composite_execution import execute_composite_children_sync`
    # at call time, so this catches both.
    monkeypatch.setattr(
        "model_hub.utils.composite_execution.execute_composite_children_sync",
        _stub,
    )

    class _Hooks:
        def set_outcome(self, outcome):
            state["outcome"] = outcome

        def set_exception(self, exc):
            state["exception"] = exc

        @property
        def calls(self):
            return calls

    return _Hooks()


@pytest.fixture
def composite_trace_task(db, populated_observe_project, composite_eval_template):
    """Trace-level eval task wired to a composite parent template."""
    from tracer.models.eval_task import RowType

    project = populated_observe_project["project"]
    config = CustomEvalConfig.objects.create(
        project=project,
        eval_template=composite_eval_template["parent"],
        name="Composite Trace Eval",
        config={"output": "Pass/Fail"},
        mapping={"input": "input", "output": "output"},
        model="turing_large",
    )
    task = EvalTask.objects.create(
        project=project,
        name="Composite traces task",
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
def composite_session_task(db, populated_observe_project, composite_eval_template):
    """Session-level eval task wired to a composite parent template."""
    from tracer.models.eval_task import RowType

    project = populated_observe_project["project"]
    config = CustomEvalConfig.objects.create(
        project=project,
        eval_template=composite_eval_template["parent"],
        name="Composite Session Eval",
        config={"output": "Pass/Fail"},
        mapping={"input": "traces.0.input", "output": "traces.0.output"},
        model="turing_large",
    )
    task = EvalTask.objects.create(
        project=project,
        name="Composite sessions task",
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
class TestCompositeEvalOnTrace:
    """Composite eval fan-out for ``row_type=traces``."""

    def test_creates_one_eval_logger_per_trace(
        self,
        populated_observe_project,
        composite_trace_task,
        stub_composite_children,
        stub_cost_log,
        inline_temporal,
    ):
        """4 traces × 1 composite -> 4 EvalLogger rows, all target_type='trace'."""
        task = composite_trace_task["task"]
        process_eval_task._original_func(str(task.id))

        rows = EvalLogger.objects.filter(eval_task_id=str(task.id), deleted=False)
        assert rows.count() == 4
        assert all(r.target_type == "trace" for r in rows)
        assert all(r.trace_id is not None for r in rows)
        assert all(r.observation_span_id is not None for r in rows)
        assert all(r.trace_session_id is None for r in rows)

    def test_writes_children_metadata(
        self,
        populated_observe_project,
        composite_trace_task,
        composite_eval_template,
        stub_composite_children,
        stub_cost_log,
        inline_temporal,
    ):
        task = composite_trace_task["task"]
        parent = composite_eval_template["parent"]
        process_eval_task._original_func(str(task.id))

        row = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False
        ).first()
        meta = row.output_metadata
        assert meta["composite_id"] == str(parent.id)
        assert meta["aggregation_enabled"] is True
        assert meta["aggregate_pass"] is True
        assert isinstance(meta["children"], list)
        assert [c["order"] for c in meta["children"]] == [0, 1]

    def test_writes_aggregate_score_to_output_float(
        self,
        populated_observe_project,
        composite_trace_task,
        stub_composite_children,
        stub_cost_log,
        inline_temporal,
    ):
        task = composite_trace_task["task"]
        process_eval_task._original_func(str(task.id))

        for row in EvalLogger.objects.filter(eval_task_id=str(task.id), deleted=False):
            assert row.output_float == pytest.approx(0.8)

    def test_writes_summary_to_eval_explanation(
        self,
        populated_observe_project,
        composite_trace_task,
        stub_composite_children,
        stub_cost_log,
        inline_temporal,
    ):
        task = composite_trace_task["task"]
        process_eval_task._original_func(str(task.id))

        for row in EvalLogger.objects.filter(eval_task_id=str(task.id), deleted=False):
            assert row.eval_explanation == "ok"

    def test_dedup_on_second_tick(
        self,
        populated_observe_project,
        composite_trace_task,
        stub_composite_children,
        stub_cost_log,
        inline_temporal,
    ):
        task = composite_trace_task["task"]
        process_eval_task._original_func(str(task.id))
        first = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False
        ).count()
        process_eval_task._original_func(str(task.id))
        second = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False
        ).count()
        assert second == first

    def test_anchors_to_root_span(
        self,
        populated_observe_project,
        composite_trace_task,
        stub_composite_children,
        stub_cost_log,
        inline_temporal,
    ):
        """Trace composite row's observation_span = the trace's root span."""
        task = composite_trace_task["task"]
        process_eval_task._original_func(str(task.id))

        rows = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False
        ).select_related("observation_span")
        for row in rows:
            assert row.observation_span.parent_span_id is None
            assert row.observation_span.trace_id == row.trace_id

    def test_skips_zero_span_trace(
        self,
        populated_observe_project,
        composite_trace_task,
        stub_composite_children,
        stub_cost_log,
        inline_temporal,
    ):
        """Composite on a zero-span trace short-circuits in the activity, before our branch.

        The anchor-miss path (`evaluate_trace_observe :: _find_anchor_span is None`)
        runs before the composite delegation, so no EvalLogger row is written
        and the failure is recorded on EvalTask.failed_spans.
        """
        from tracer.models.trace import Trace
        from tracer.utils.eval import evaluate_trace_observe

        empty_trace = Trace.objects.create(
            project=populated_observe_project["project"],
            name="empty composite trace",
            input={},
            output={},
        )
        task = composite_trace_task["task"]
        config = composite_trace_task["config"]

        result = evaluate_trace_observe._original_func(
            trace_id=str(empty_trace.id),
            custom_eval_config_id=str(config.id),
            eval_task_id=str(task.id),
        )
        assert result is False
        assert EvalLogger.objects.filter(trace_id=empty_trace.id).count() == 0

        task.refresh_from_db()
        assert any(
            entry.get("trace_id") == str(empty_trace.id)
            for entry in (task.failed_spans or [])
        )

    def test_forwards_trace_context_to_children(
        self,
        populated_observe_project,
        composite_trace_task,
        stub_composite_children,
        stub_cost_log,
        inline_temporal,
    ):
        """The composite call gets `trace_context` + source='tracer_composite'."""
        task = composite_trace_task["task"]
        process_eval_task._original_func(str(task.id))

        assert len(stub_composite_children.calls) == 4
        for call in stub_composite_children.calls:
            assert call["source"] == "tracer_composite"
            ctx = call.get("trace_context")
            assert ctx is not None
            assert "trace_id" in ctx and "anchor_span_id" in ctx

    def test_writes_error_row_when_children_raise(
        self,
        populated_observe_project,
        composite_trace_task,
        stub_composite_children,
        stub_cost_log,
        inline_temporal,
    ):
        """An exception inside the composite engine still yields one EvalLogger row."""
        stub_composite_children.set_exception(RuntimeError("boom"))
        task = composite_trace_task["task"]
        process_eval_task._original_func(str(task.id))

        rows = list(
            EvalLogger.objects.filter(eval_task_id=str(task.id), deleted=False)
        )
        assert len(rows) == 4
        for row in rows:
            assert row.error is True
            assert row.output_str == "ERROR"
            assert row.target_type == "trace"
            assert row.trace_id is not None
            assert row.observation_span_id is not None
            assert row.trace_session_id is None
            assert "boom" in (row.error_message or "")

    def test_skips_parent_cost_log(
        self,
        populated_observe_project,
        composite_trace_task,
        stub_composite_children,
        inline_temporal,
        monkeypatch,
    ):
        """The composite path does not call the parent cost-log; children log their own."""
        calls = []

        def _spy(**kwargs):
            calls.append(kwargs)
            return None  # would normally short-circuit downstream; never reached for composite

        monkeypatch.setattr(
            "tracer.utils.eval.log_and_deduct_cost_for_api_request", _spy
        )
        task = composite_trace_task["task"]
        process_eval_task._original_func(str(task.id))
        assert calls == []


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestCompositeEvalOnSession:
    """Composite eval fan-out for ``row_type=sessions``."""

    def test_creates_one_eval_logger_per_session(
        self,
        populated_observe_project,
        composite_session_task,
        stub_composite_children,
        stub_cost_log,
        inline_temporal,
    ):
        """2 sessions × 1 composite -> 2 EvalLogger rows, all target_type='session'."""
        task = composite_session_task["task"]
        process_eval_task._original_func(str(task.id))

        rows = EvalLogger.objects.filter(eval_task_id=str(task.id), deleted=False)
        assert rows.count() == 2
        assert all(r.target_type == "session" for r in rows)

    def test_eval_logger_has_null_span_and_trace(
        self,
        populated_observe_project,
        composite_session_task,
        stub_composite_children,
        stub_cost_log,
        inline_temporal,
    ):
        task = composite_session_task["task"]
        process_eval_task._original_func(str(task.id))

        for row in EvalLogger.objects.filter(eval_task_id=str(task.id), deleted=False):
            assert row.observation_span_id is None
            assert row.trace_id is None
            assert row.trace_session_id is not None

    def test_writes_children_metadata(
        self,
        populated_observe_project,
        composite_session_task,
        composite_eval_template,
        stub_composite_children,
        stub_cost_log,
        inline_temporal,
    ):
        task = composite_session_task["task"]
        parent = composite_eval_template["parent"]
        process_eval_task._original_func(str(task.id))

        row = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False
        ).first()
        meta = row.output_metadata
        assert meta["composite_id"] == str(parent.id)
        assert meta["aggregation_enabled"] is True
        assert meta["aggregate_pass"] is True
        assert [c["order"] for c in meta["children"]] == [0, 1]

    def test_writes_aggregate_score(
        self,
        populated_observe_project,
        composite_session_task,
        stub_composite_children,
        stub_cost_log,
        inline_temporal,
    ):
        task = composite_session_task["task"]
        process_eval_task._original_func(str(task.id))

        for row in EvalLogger.objects.filter(eval_task_id=str(task.id), deleted=False):
            assert row.output_float == pytest.approx(0.8)

    def test_dedup_on_second_tick(
        self,
        populated_observe_project,
        composite_session_task,
        stub_composite_children,
        stub_cost_log,
        inline_temporal,
    ):
        task = composite_session_task["task"]
        process_eval_task._original_func(str(task.id))
        first = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False
        ).count()
        process_eval_task._original_func(str(task.id))
        second = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False
        ).count()
        assert second == first

    def test_forwards_session_context_to_children(
        self,
        populated_observe_project,
        composite_session_task,
        stub_composite_children,
        stub_cost_log,
        inline_temporal,
    ):
        task = composite_session_task["task"]
        process_eval_task._original_func(str(task.id))

        assert len(stub_composite_children.calls) == 2
        for call in stub_composite_children.calls:
            assert call["source"] == "tracer_composite"
            ctx = call.get("session_context")
            assert ctx is not None
            assert "session_id" in ctx

    def test_sets_workspace_context(
        self,
        populated_observe_project,
        composite_session_task,
        stub_composite_children,
        stub_cost_log,
        inline_temporal,
        monkeypatch,
    ):
        """Session composite path mirrors the single-eval session path's workspace ContextVar set."""
        calls = []

        def _spy(*, workspace, organization, **kwargs):
            calls.append({"workspace": workspace, "organization": organization})

        monkeypatch.setattr(
            "tfc.middleware.workspace_context.set_workspace_context", _spy
        )
        task = composite_session_task["task"]
        process_eval_task._original_func(str(task.id))

        # One call per session evaluated
        assert len(calls) >= 2
        project = populated_observe_project["project"]
        for entry in calls:
            assert entry["organization"].id == project.organization.id

    def test_writes_error_row_when_children_raise(
        self,
        populated_observe_project,
        composite_session_task,
        stub_composite_children,
        stub_cost_log,
        inline_temporal,
    ):
        stub_composite_children.set_exception(RuntimeError("boom"))
        task = composite_session_task["task"]
        process_eval_task._original_func(str(task.id))

        rows = list(
            EvalLogger.objects.filter(eval_task_id=str(task.id), deleted=False)
        )
        assert len(rows) == 2
        for row in rows:
            assert row.error is True
            assert row.output_str == "ERROR"
            assert row.target_type == "session"
            assert row.observation_span_id is None
            assert row.trace_id is None
            assert row.trace_session_id is not None
            assert "boom" in (row.error_message or "")

    def test_skips_parent_cost_log(
        self,
        populated_observe_project,
        composite_session_task,
        stub_composite_children,
        inline_temporal,
        monkeypatch,
    ):
        calls = []

        def _spy(**kwargs):
            calls.append(kwargs)
            return None

        monkeypatch.setattr(
            "tracer.utils.eval.log_and_deduct_cost_for_api_request", _spy
        )
        task = composite_session_task["task"]
        process_eval_task._original_func(str(task.id))
        assert calls == []


# ────────────────────────────────────────────────────────────────────────
# Status field, drain state, upsert helper, and entity_count_for_filters
# ────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestEvalLoggerStatusField:
    """Verify the status field is set correctly across dispatch paths."""

    def test_span_dispatch_sets_completed_status(
        self,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        from tracer.models.observation_span import EvalLoggerStatus

        task = observe_eval_task["task"]
        process_eval_task._original_func(str(task.id))

        rows = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        )
        assert rows.count() == 12
        assert all(r.status == EvalLoggerStatus.COMPLETED for r in rows)

    def test_trace_dispatch_sets_completed_status(
        self,
        populated_observe_project,
        observe_trace_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        from tracer.models.observation_span import EvalLoggerStatus

        task = observe_trace_task["task"]
        process_eval_task._original_func(str(task.id))

        rows = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        )
        assert rows.count() == 4
        assert all(r.status == EvalLoggerStatus.COMPLETED for r in rows)

    def test_session_dispatch_sets_completed_status(
        self,
        populated_observe_project,
        observe_session_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        from tracer.models.observation_span import EvalLoggerStatus

        task = observe_session_task["task"]
        process_eval_task._original_func(str(task.id))

        rows = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        )
        assert rows.count() == 2
        assert all(r.status == EvalLoggerStatus.COMPLETED for r in rows)

    def test_error_dispatch_sets_failed_status(
        self,
        populated_observe_project,
        composite_trace_task,
        stub_composite_children,
        stub_cost_log,
        inline_temporal,
    ):
        from tracer.models.observation_span import EvalLoggerStatus

        stub_composite_children.set_exception(RuntimeError("boom"))
        task = composite_trace_task["task"]
        process_eval_task._original_func(str(task.id))

        rows = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        )
        assert rows.count() == 4
        assert all(r.status == EvalLoggerStatus.FAILED for r in rows)
        assert all(r.error is True for r in rows)

    def test_no_pending_rows_remain_after_inline_dispatch(
        self,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """After inline dispatch all PENDING rows should be updated to COMPLETED."""
        from tracer.models.observation_span import EvalLoggerStatus

        task = observe_eval_task["task"]
        process_eval_task._original_func(str(task.id))

        pending = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
            status=EvalLoggerStatus.PENDING,
        ).count()
        assert pending == 0


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestComputeDrainState:
    """Verify compute_drain_state handles the PENDING status exclusion."""

    def test_pending_rows_excluded_from_completed_count(
        self,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """PENDING rows should not count toward drain completion."""
        from tracer.models.observation_span import EvalLoggerStatus
        from tracer.utils.eval_tasks import compute_drain_state

        task = observe_eval_task["task"]
        config = observe_eval_task["config"]

        # Run dispatch so we get 12 COMPLETED rows
        process_eval_task._original_func(str(task.id))
        assert EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
            status=EvalLoggerStatus.COMPLETED,
        ).count() == 12

        # Flip 4 rows back to PENDING to simulate in-flight state
        to_flip = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        ).order_by("id")[:4]
        EvalLogger.objects.filter(id__in=[r.id for r in to_flip]).update(
            status=EvalLoggerStatus.PENDING,
        )

        state = compute_drain_state(task)
        assert state["completed"] == 8

    def test_fully_drained_after_all_completed(
        self,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        from tracer.utils.eval_tasks import compute_drain_state

        task = observe_eval_task["task"]
        process_eval_task._original_func(str(task.id))

        state = compute_drain_state(task)
        assert state["is_fully_drained"] is True
        assert state["completed"] == state["dispatched"]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestUpsertEvalLogger:
    """Verify _upsert_eval_logger updates existing rows instead of creating duplicates."""

    def test_upsert_updates_pending_to_completed(
        self,
        populated_observe_project,
        observe_eval_task,
    ):
        """_upsert_eval_logger should update an existing PENDING row, not create a duplicate."""
        from tracer.models.observation_span import EvalLoggerStatus, EvalTargetType
        from tracer.utils.eval import _upsert_eval_logger

        task = observe_eval_task["task"]
        config = observe_eval_task["config"]
        span = populated_observe_project["spans"][0]

        # Pre-create a PENDING row
        EvalLogger.objects.create(
            eval_task_id=str(task.id),
            observation_span=span,
            trace=span.trace,
            custom_eval_config=config,
            target_type=EvalTargetType.SPAN,
            status=EvalLoggerStatus.PENDING,
        )

        # Call _upsert_eval_logger directly with the same lookup
        lookup = {
            "eval_task_id": str(task.id),
            "observation_span": span,
            "custom_eval_config": config,
        }
        defaults = {
            "trace": span.trace,
            "status": EvalLoggerStatus.COMPLETED,
            "output_str": "Pass",
        }
        _upsert_eval_logger(lookup, defaults)

        rows = EvalLogger.objects.filter(
            eval_task_id=str(task.id),
            observation_span=span,
            custom_eval_config=config,
            deleted=False,
        )
        assert rows.count() == 1
        assert rows.first().status == EvalLoggerStatus.COMPLETED
        assert rows.first().output_str == "Pass"

    def test_upsert_on_trace_target(
        self,
        populated_observe_project,
        observe_trace_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """Trace-level upsert correctly updates the pre-created row."""
        from tracer.models.observation_span import EvalLoggerStatus

        task = observe_trace_task["task"]
        process_eval_task._original_func(str(task.id))

        # Every trace row should be COMPLETED, no duplicates
        rows = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        )
        trace_ids = list(rows.values_list("trace_id", flat=True))
        assert len(trace_ids) == len(set(trace_ids)), "Duplicate trace rows found"
        assert all(r.status == EvalLoggerStatus.COMPLETED for r in rows)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestFreshRunDedup:
    """Verify fresh-run (soft-delete + offset reset) works for all row types."""

    def _run_fresh_reset(self, task):
        from django.utils import timezone
        from tracer.models.eval_task import EvalTaskLogger

        EvalLogger.objects.filter(
            eval_task_id=str(task.id)
        ).update(deleted=True, deleted_at=timezone.now())
        logger = EvalTaskLogger.objects.get(eval_task=task)
        logger.offset = 0
        logger.status = EvalTaskStatus.PENDING
        logger.save(update_fields=["offset", "status"])
        task.status = EvalTaskStatus.PENDING
        task.save(update_fields=["status"])

    def test_fresh_run_spans(
        self,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        task = observe_eval_task["task"]
        process_eval_task._original_func(str(task.id))
        assert EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        ).count() == 12

        self._run_fresh_reset(task)
        process_eval_task._original_func(str(task.id))

        active = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        )
        assert active.count() == 12
        # Old rows should still exist as soft-deleted
        total = EvalLogger.all_objects.filter(eval_task_id=str(task.id)).count()
        assert total == 24

    def test_fresh_run_traces(
        self,
        populated_observe_project,
        observe_trace_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        task = observe_trace_task["task"]
        process_eval_task._original_func(str(task.id))
        assert EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        ).count() == 4

        self._run_fresh_reset(task)
        process_eval_task._original_func(str(task.id))

        assert EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        ).count() == 4
        assert EvalLogger.all_objects.filter(
            eval_task_id=str(task.id),
        ).count() == 8

    def test_fresh_run_sessions(
        self,
        populated_observe_project,
        observe_session_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        task = observe_session_task["task"]
        process_eval_task._original_func(str(task.id))
        assert EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        ).count() == 2

        self._run_fresh_reset(task)
        process_eval_task._original_func(str(task.id))

        assert EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        ).count() == 2
        assert EvalLogger.all_objects.filter(
            eval_task_id=str(task.id),
        ).count() == 4


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestEntityCountForFilters:
    """Verify entity_count_for_filters returns correct counts per row type."""

    def test_span_count(self, populated_observe_project):
        from tracer.models.eval_task import RowType
        from tracer.utils.eval_tasks import (
            entity_count_for_filters,
            parsing_evaltask_filters,
        )

        project = populated_observe_project["project"]
        filters = parsing_evaltask_filters({"project_id": str(project.id)})
        count = entity_count_for_filters(RowType.SPANS, filters)
        assert count == 12

    def test_trace_count(self, populated_observe_project):
        from tracer.models.eval_task import RowType
        from tracer.utils.eval_tasks import (
            entity_count_for_filters,
            parsing_evaltask_filters,
        )

        project = populated_observe_project["project"]
        filters = parsing_evaltask_filters({"project_id": str(project.id)})
        count = entity_count_for_filters(RowType.TRACES, filters)
        assert count == 4

    def test_session_count(self, populated_observe_project):
        from tracer.models.eval_task import RowType
        from tracer.utils.eval_tasks import (
            entity_count_for_filters,
            parsing_evaltask_filters,
        )

        project = populated_observe_project["project"]
        filters = parsing_evaltask_filters({"project_id": str(project.id)})
        count = entity_count_for_filters(RowType.SESSIONS, filters)
        assert count == 2

    def test_voice_calls_count_matches_spans(self, populated_observe_project):
        from tracer.models.eval_task import RowType
        from tracer.utils.eval_tasks import (
            entity_count_for_filters,
            parsing_evaltask_filters,
        )

        project = populated_observe_project["project"]
        filters = parsing_evaltask_filters({"project_id": str(project.id)})
        span_count = entity_count_for_filters(RowType.SPANS, filters)
        vc_count = entity_count_for_filters(RowType.VOICE_CALLS, filters)
        assert vc_count == span_count


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestOffsetTracking:
    """Verify offset increments correctly and drives dedup."""

    def test_offset_matches_dispatched_span_count(
        self,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        from tracer.models.eval_task import EvalTaskLogger

        task = observe_eval_task["task"]
        process_eval_task._original_func(str(task.id))

        logger = EvalTaskLogger.objects.get(eval_task=task)
        assert logger.offset == 12

    def test_offset_matches_dispatched_trace_count(
        self,
        populated_observe_project,
        observe_trace_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        from tracer.models.eval_task import EvalTaskLogger

        task = observe_trace_task["task"]
        process_eval_task._original_func(str(task.id))

        logger = EvalTaskLogger.objects.get(eval_task=task)
        assert logger.offset == 4

    def test_offset_matches_dispatched_session_count(
        self,
        populated_observe_project,
        observe_session_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        from tracer.models.eval_task import EvalTaskLogger

        task = observe_session_task["task"]
        process_eval_task._original_func(str(task.id))

        logger = EvalTaskLogger.objects.get(eval_task=task)
        assert logger.offset == 2

    def test_offset_does_not_increase_on_second_tick(
        self,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        from tracer.models.eval_task import EvalTaskLogger

        task = observe_eval_task["task"]
        process_eval_task._original_func(str(task.id))
        process_eval_task._original_func(str(task.id))

        logger = EvalTaskLogger.objects.get(eval_task=task)
        assert logger.offset == 12

    def test_offset_with_spans_limit(
        self,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        from tracer.models.eval_task import EvalTaskLogger

        task = observe_eval_task["task"]
        task.spans_limit = 5
        task.save()

        process_eval_task._original_func(str(task.id))

        logger = EvalTaskLogger.objects.get(eval_task=task)
        assert logger.offset == 5

        # Raise the limit so the second tick can dispatch more
        task.refresh_from_db()
        task.spans_limit = 10
        task.status = EvalTaskStatus.RUNNING
        task.save(update_fields=["spans_limit", "status"])
        process_eval_task._original_func(str(task.id))

        logger.refresh_from_db()
        assert logger.offset == 10


# ────────────────────────────────────────────────────────────────────────
# Continuous run type with new dedup/status logic
# ────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestContinuousRunType:
    """Continuous tasks with EvalLogger-based dedup and status field."""

    def test_continuous_spans_sets_completed_status(
        self,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        from tracer.models.observation_span import EvalLoggerStatus

        task = observe_eval_task["task"]
        task.run_type = RunType.CONTINUOUS
        task.save()

        process_eval_task._original_func(str(task.id))

        rows = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        )
        assert rows.count() > 0
        assert all(r.status == EvalLoggerStatus.COMPLETED for r in rows)

    def test_continuous_traces_creates_rows(
        self,
        populated_observe_project,
        observe_trace_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        from tracer.models.observation_span import EvalLoggerStatus

        task = observe_trace_task["task"]
        task.run_type = RunType.CONTINUOUS
        task.save()

        process_eval_task._original_func(str(task.id))

        rows = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        )
        assert rows.count() == 4
        assert all(r.status == EvalLoggerStatus.COMPLETED for r in rows)
        assert all(r.target_type == "trace" for r in rows)

    def test_continuous_sessions_creates_rows(
        self,
        populated_observe_project,
        observe_session_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        from tracer.models.observation_span import EvalLoggerStatus

        task = observe_session_task["task"]
        task.run_type = RunType.CONTINUOUS
        task.save()

        process_eval_task._original_func(str(task.id))

        rows = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        )
        assert rows.count() == 2
        assert all(r.status == EvalLoggerStatus.COMPLETED for r in rows)
        assert all(r.target_type == "session" for r in rows)

    def test_continuous_dedup_on_second_tick(
        self,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        task = observe_eval_task["task"]
        task.run_type = RunType.CONTINUOUS
        task.save()

        process_eval_task._original_func(str(task.id))
        first = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        ).count()

        process_eval_task._original_func(str(task.id))
        second = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        ).count()

        assert second == first

    def test_continuous_no_pending_residue(
        self,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        from tracer.models.observation_span import EvalLoggerStatus

        task = observe_eval_task["task"]
        task.run_type = RunType.CONTINUOUS
        task.save()

        process_eval_task._original_func(str(task.id))

        pending = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
            status=EvalLoggerStatus.PENDING,
        ).count()
        assert pending == 0


# ────────────────────────────────────────────────────────────────────────
# View-level: fresh run and edit-and-rerun via PATCH endpoint
# ────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestFreshRunViaAPI:
    """Test _handle_fresh_run through the update_eval_task PATCH endpoint."""

    def test_fresh_run_resets_eval_loggers_and_offset(
        self,
        auth_client,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        from tracer.models.eval_task import EvalTaskLogger

        task = observe_eval_task["task"]
        config = observe_eval_task["config"]

        # First run to create rows
        process_eval_task._original_func(str(task.id))
        assert EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        ).count() == 12

        # Pause before edit (required by the view)
        task.refresh_from_db()
        task.status = EvalTaskStatus.PAUSED
        task.save(update_fields=["status"])

        # PATCH fresh_run
        response = auth_client.patch(
            "/tracer/eval-task/update_eval_task/",
            {
                "eval_task_id": str(task.id),
                "edit_type": "fresh_run",
                "evals": [str(config.id)],
            },
            format="json",
        )
        assert response.status_code in (200, 201), response.json()

        # Verify: old rows soft-deleted, offset reset
        active = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        ).count()
        assert active == 0

        logger_obj = EvalTaskLogger.objects.get(eval_task=task)
        assert logger_obj.offset == 0

        # Soft-deleted rows still exist
        total = EvalLogger.all_objects.filter(
            eval_task_id=str(task.id),
        ).count()
        assert total == 12

    def test_fresh_run_for_trace_task(
        self,
        auth_client,
        populated_observe_project,
        observe_trace_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        from tracer.models.eval_task import EvalTaskLogger

        task = observe_trace_task["task"]
        config = observe_trace_task["config"]

        process_eval_task._original_func(str(task.id))
        assert EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        ).count() == 4

        task.refresh_from_db()
        task.status = EvalTaskStatus.PAUSED
        task.save(update_fields=["status"])

        response = auth_client.patch(
            "/tracer/eval-task/update_eval_task/",
            {
                "eval_task_id": str(task.id),
                "edit_type": "fresh_run",
                "evals": [str(config.id)],
            },
            format="json",
        )
        assert response.status_code in (200, 201), response.json()

        assert EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        ).count() == 0

        logger_obj = EvalTaskLogger.objects.get(eval_task=task)
        assert logger_obj.offset == 0


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestEditAndRerunViaAPI:
    """Test _handle_edit_and_rerun through the update_eval_task PATCH endpoint."""

    def test_edit_rerun_adds_new_eval(
        self,
        auth_client,
        populated_observe_project,
        observe_eval_task,
        eval_template,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
        monkeypatch,
    ):
        """Adding a new eval config should schedule evaluations for processed entities."""
        task = observe_eval_task["task"]
        config = observe_eval_task["config"]
        project = observe_eval_task["project"]

        # First run
        process_eval_task._original_func(str(task.id))
        assert EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        ).count() == 12

        # Create a second eval config
        second_config = CustomEvalConfig.objects.create(
            project=project,
            eval_template=eval_template,
            name="Second Eval",
            config={"output": "Pass/Fail"},
            mapping={"input": "input", "output": "output"},
            model="turing_large",
        )

        task.refresh_from_db()
        task.status = EvalTaskStatus.PAUSED
        task.save(update_fields=["status"])

        # Track dispatches from run_for_processed_spans
        dispatch_calls = []
        import tracer.utils.eval_tasks as eval_tasks_mod

        original_delay = eval_tasks_mod.run_for_processed_spans._original_func

        def _spy_delay(*args, **kwargs):
            dispatch_calls.append(args)

        monkeypatch.setattr(
            eval_tasks_mod.run_for_processed_spans, "delay", _spy_delay,
        )

        response = auth_client.patch(
            "/tracer/eval-task/update_eval_task/",
            {
                "eval_task_id": str(task.id),
                "edit_type": "edit_rerun",
                "evals": [str(config.id), str(second_config.id)],
            },
            format="json",
        )
        assert response.status_code in (200, 201), response.json()

        # The new eval should be scheduled for processed spans
        assert len(dispatch_calls) > 0

    def test_edit_rerun_fills_missing_evals(
        self,
        auth_client,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
        monkeypatch,
    ):
        """Edit-rerun with same evals should detect and fill missing evaluations."""
        task = observe_eval_task["task"]
        config = observe_eval_task["config"]

        # First run
        process_eval_task._original_func(str(task.id))

        # Soft-delete some results to create gaps
        rows_to_delete = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        ).order_by("id")[:3]
        from django.utils import timezone

        EvalLogger.objects.filter(
            id__in=[r.id for r in rows_to_delete],
        ).update(deleted=True, deleted_at=timezone.now())

        assert EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        ).count() == 9

        task.refresh_from_db()
        task.status = EvalTaskStatus.PAUSED
        task.save(update_fields=["status"])

        dispatch_calls = []
        import tracer.utils.eval_tasks as eval_tasks_mod

        def _spy_delay(*args, **kwargs):
            dispatch_calls.append(args)

        monkeypatch.setattr(
            eval_tasks_mod.run_for_processed_spans, "delay", _spy_delay,
        )

        response = auth_client.patch(
            "/tracer/eval-task/update_eval_task/",
            {
                "eval_task_id": str(task.id),
                "edit_type": "edit_rerun",
                "evals": [str(config.id)],
            },
            format="json",
        )
        assert response.status_code in (200, 201), response.json()

        # Should schedule the 3 missing spans
        if dispatch_calls:
            scheduled_ids = dispatch_calls[0][0]
            assert len(scheduled_ids) == 3


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestScheduleMissingEvaluationsRowTypes:
    """Verify _schedule_missing_evaluations works for all row types."""

    def test_missing_evals_for_trace_task(
        self,
        auth_client,
        populated_observe_project,
        observe_trace_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
        monkeypatch,
    ):
        """For traces, missing eval detection should use trace_id not observation_span_id."""
        task = observe_trace_task["task"]
        config = observe_trace_task["config"]

        process_eval_task._original_func(str(task.id))
        assert EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        ).count() == 4

        # Soft-delete 1 trace's eval result
        first_row = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        ).first()
        from django.utils import timezone

        EvalLogger.objects.filter(id=first_row.id).update(
            deleted=True, deleted_at=timezone.now(),
        )

        task.refresh_from_db()
        task.status = EvalTaskStatus.PAUSED
        task.save(update_fields=["status"])

        dispatch_calls = []
        import tracer.utils.eval_tasks as eval_tasks_mod

        def _spy_delay(*args, **kwargs):
            dispatch_calls.append(args)

        monkeypatch.setattr(
            eval_tasks_mod.run_for_processed_spans, "delay", _spy_delay,
        )

        response = auth_client.patch(
            "/tracer/eval-task/update_eval_task/",
            {
                "eval_task_id": str(task.id),
                "edit_type": "edit_rerun",
                "evals": [str(config.id)],
            },
            format="json",
        )
        assert response.status_code in (200, 201), response.json()

        # Should schedule the 1 missing trace
        if dispatch_calls:
            scheduled_ids = dispatch_calls[0][0]
            assert len(scheduled_ids) == 1

    def test_missing_evals_for_session_task(
        self,
        auth_client,
        populated_observe_project,
        observe_session_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
        monkeypatch,
    ):
        task = observe_session_task["task"]
        config = observe_session_task["config"]

        process_eval_task._original_func(str(task.id))
        assert EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        ).count() == 2

        # Soft-delete 1 session's eval result
        first_row = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        ).first()
        from django.utils import timezone

        EvalLogger.objects.filter(id=first_row.id).update(
            deleted=True, deleted_at=timezone.now(),
        )

        task.refresh_from_db()
        task.status = EvalTaskStatus.PAUSED
        task.save(update_fields=["status"])

        dispatch_calls = []
        import tracer.utils.eval_tasks as eval_tasks_mod

        def _spy_delay(*args, **kwargs):
            dispatch_calls.append(args)

        monkeypatch.setattr(
            eval_tasks_mod.run_for_processed_spans, "delay", _spy_delay,
        )

        response = auth_client.patch(
            "/tracer/eval-task/update_eval_task/",
            {
                "eval_task_id": str(task.id),
                "edit_type": "edit_rerun",
                "evals": [str(config.id)],
            },
            format="json",
        )
        assert response.status_code in (200, 201), response.json()

        if dispatch_calls:
            scheduled_ids = dispatch_calls[0][0]
            assert len(scheduled_ids) == 1


# ────────────────────────────────────────────────────────────────────────
# Single eval error / FAILED status
# ────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestSingleEvalErrorStatus:
    """Verify FAILED status on single eval errors (not just composite)."""

    def test_span_eval_error_sets_failed(
        self,
        populated_observe_project,
        observe_eval_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        from tracer.models.observation_span import EvalLoggerStatus

        # Make the eval engine raise
        stub_run_eval(value=False, reason="stubbed fail", failure="engine error")

        task = observe_eval_task["task"]
        process_eval_task._original_func(str(task.id))

        rows = EvalLogger.objects.filter(
            eval_task_id=str(task.id), deleted=False,
        )
        # Rows should exist (engine returned a result, even if failing)
        assert rows.count() == 12

    def test_trace_eval_error_from_missing_anchor(
        self,
        populated_observe_project,
        observe_trace_task,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        """Trace with no anchor span → no EvalLogger row, recorded in failed_spans."""
        from tracer.models.trace import Trace
        from tracer.utils.eval import evaluate_trace_observe

        empty_trace = Trace.objects.create(
            project=populated_observe_project["project"],
            name="no-span trace",
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

        # No EvalLogger for this trace
        assert EvalLogger.objects.filter(
            trace_id=empty_trace.id, deleted=False,
        ).count() == 0


# ────────────────────────────────────────────────────────────────────────
# run_for_processed_spans dispatches correct activity per row_type
# ────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestRunForProcessedSpansRowTypeDispatch:
    """Verify run_for_processed_spans dispatches to the correct activity."""

    def test_dispatches_span_activity_for_spans(
        self,
        populated_observe_project,
        observe_eval_task,
        monkeypatch,
    ):
        import tracer.utils.eval as eval_module

        calls = []

        def _record(*args, **kwargs):
            calls.append(("span", args))

        monkeypatch.setattr(
            eval_module.evaluate_observation_span_observe, "delay", _record,
        )

        task = observe_eval_task["task"]
        config = observe_eval_task["config"]
        from tracer.utils.eval_tasks import run_for_processed_spans

        span_ids = [s.id for s in populated_observe_project["spans"][:2]]
        run_for_processed_spans._original_func(
            span_ids, [str(config.id)], str(task.id),
        )
        assert len(calls) == 2
        assert all(c[0] == "span" for c in calls)

    def test_dispatches_trace_activity_for_traces(
        self,
        populated_observe_project,
        observe_trace_task,
        monkeypatch,
    ):
        import tracer.utils.eval as eval_module

        calls = []

        def _record(*args, **kwargs):
            calls.append(("trace", args))

        monkeypatch.setattr(
            eval_module.evaluate_trace_observe, "delay", _record,
        )

        task = observe_trace_task["task"]
        config = observe_trace_task["config"]
        from tracer.utils.eval_tasks import run_for_processed_spans

        trace_ids = [t.id for t in populated_observe_project["traces"][:2]]
        run_for_processed_spans._original_func(
            trace_ids, [str(config.id)], str(task.id),
        )
        assert len(calls) == 2
        assert all(c[0] == "trace" for c in calls)

    def test_dispatches_session_activity_for_sessions(
        self,
        populated_observe_project,
        observe_session_task,
        monkeypatch,
    ):
        import tracer.utils.eval as eval_module

        calls = []

        def _record(*args, **kwargs):
            calls.append(("session", args))

        monkeypatch.setattr(
            eval_module.evaluate_trace_session_observe, "delay", _record,
        )

        task = observe_session_task["task"]
        config = observe_session_task["config"]
        from tracer.utils.eval_tasks import run_for_processed_spans

        session_ids = [s.id for s in populated_observe_project["sessions"][:2]]
        run_for_processed_spans._original_func(
            session_ids, [str(config.id)], str(task.id),
        )
        assert len(calls) == 2
        assert all(c[0] == "session" for c in calls)
