"""
Eval-task runtime tests.

Drive ``process_eval_task`` end-to-end against real Postgres with the eval
engine, billing layer, and Temporal stubbed via fixtures from conftest.py:
  - ``stub_run_eval``, ``stub_cost_log``: skip the engine + cost layers.
  - ``inline_temporal``: route ``.delay`` -> ``.run_sync`` so dispatch executes inline.
  - ``track_eval_dispatch``: spy on ``.delay`` to assert dispatcher fan-out without running.

These tests pin the current span-level behaviour. PR4 will add trace+session
counterparts (those tests live in this same file).
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
