"""Tests for run_entry — executes one claimed entry and records its terminal
status, reusing the per-target_type eval core. Engine + cost are stubbed."""

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from temporalio.common import WorkflowIDConflictPolicy

from model_hub.models.evals_metric import EvalTemplate
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.models.trace_session import TraceSession
from tracer.services.eval_tasks.run_entry import _reseed_eval_clustering, run_entry
from tracer.tests._ch_seed import seed_ch_span, seed_ch_trace_sessions

_TERMINAL = {
    EvalEntryStatus.COMPLETED,
    EvalEntryStatus.ERRORED,
    EvalEntryStatus.SKIPPED,
}


def _span_entry(task, span, config):
    return EvalLogger.objects.create(
        target_type=EvalTargetType.SPAN,
        observation_span=span,
        trace=span.trace,
        custom_eval_config=config,
        eval_task_id=str(task.id),
        status=EvalEntryStatus.RUNNING,
    )


@pytest.mark.integration
@pytest.mark.django_db
class TestRunEntrySpan:
    def test_completed_on_success(
        self,
        observation_span,
        custom_eval_config,
        eval_task,
        stub_run_eval,
        stub_cost_log,
    ):
        entry = _span_entry(eval_task, observation_span, custom_eval_config)
        assert run_entry(entry) == EvalEntryStatus.COMPLETED
        entry.refresh_from_db()
        assert entry.status == EvalEntryStatus.COMPLETED
        assert entry.config_hash and len(entry.config_hash) == 64
        assert entry.error is False

    def test_completed_dispatches_clustering(
        self,
        observation_span,
        custom_eval_config,
        eval_task,
        stub_run_eval,
        stub_cost_log,
    ):
        """Reaching COMPLETED must invoke the clustering hook — the exact seam
        the TH-5978 cutover severed. The hook's own fail/pass logic is unit-
        tested in TestReseedEvalClusteringHook; this pins the *wiring* so
        deleting the call from run_entry fails a test instead of silently
        re-orphaning the trigger."""
        entry = _span_entry(eval_task, observation_span, custom_eval_config)
        with patch(
            "tracer.services.eval_tasks.run_entry._reseed_eval_clustering"
        ) as reseed:
            assert run_entry(entry) == EvalEntryStatus.COMPLETED
        reseed.assert_called_once()
        assert reseed.call_args.args[0].id == entry.id
        assert reseed.call_args.args[1] == custom_eval_config.project_id

    def test_errored_run_does_not_dispatch_clustering(
        self,
        monkeypatch,
        observation_span,
        custom_eval_config,
        eval_task,
        stub_cost_log,
    ):
        """A run that ERRORED produced no eval result, so the hook must not
        fire. Pins the COMPLETED-only gate against an accidental move of the
        dispatch out of the status guard."""
        entry = _span_entry(eval_task, observation_span, custom_eval_config)

        def _boom(*a, **k):
            raise RuntimeError("engine down")

        monkeypatch.setattr("evaluations.engine.run_eval", _boom, raising=False)
        monkeypatch.setattr("evaluations.engine.runner.run_eval", _boom, raising=False)
        with patch(
            "tracer.services.eval_tasks.run_entry._reseed_eval_clustering"
        ) as reseed:
            assert run_entry(entry) == EvalEntryStatus.ERRORED
        reseed.assert_not_called()

    def test_errored_when_engine_raises(
        self,
        monkeypatch,
        observation_span,
        custom_eval_config,
        eval_task,
        stub_cost_log,
    ):
        entry = _span_entry(eval_task, observation_span, custom_eval_config)

        def _boom(*a, **k):
            raise RuntimeError("engine down")

        monkeypatch.setattr("evaluations.engine.run_eval", _boom, raising=False)
        monkeypatch.setattr("evaluations.engine.runner.run_eval", _boom, raising=False)
        assert run_entry(entry) == EvalEntryStatus.ERRORED
        entry.refresh_from_db()
        assert entry.status == EvalEntryStatus.ERRORED
        assert entry.error is True

    def test_skipped_on_missing_attribute(
        self,
        monkeypatch,
        observation_span,
        custom_eval_config,
        eval_task,
        stub_cost_log,
    ):
        from tracer.utils.eval import EvalSkippedMissingAttribute

        entry = _span_entry(eval_task, observation_span, custom_eval_config)

        def _skip(*a, **k):
            raise EvalSkippedMissingAttribute("input", "input", observation_span.id)

        monkeypatch.setattr("tracer.utils.eval._process_mapping", _skip)
        assert run_entry(entry) == EvalEntryStatus.SKIPPED
        entry.refresh_from_db()
        assert entry.status == EvalEntryStatus.SKIPPED
        assert entry.error is False
        assert "input" in (entry.skipped_reason or "")

    def test_noop_on_deleted_entry(
        self,
        observation_span,
        custom_eval_config,
        eval_task,
        stub_run_eval,
        stub_cost_log,
    ):
        entry = _span_entry(eval_task, observation_span, custom_eval_config)
        entry.delete()  # soft-delete mid-run
        assert run_entry(entry) == "deleted"
        assert EvalLogger.objects.filter(id=entry.id).count() == 0  # not resurrected


@pytest.mark.integration
@pytest.mark.django_db
class TestRunEntryTraceAndSession:
    def test_trace_dispatch_reaches_terminal(
        self,
        project,
        trace,
        custom_eval_config,
        eval_task,
        stub_run_eval,
        stub_cost_log,
    ):
        root = ObservationSpan.objects.create(
            id=f"root-{uuid.uuid4().hex[:8]}",
            project=project,
            trace=trace,
            name="root",
            observation_type="llm",
            parent_span_id="",
            input={"prompt": "hi"},
        )
        seed_ch_span(root)
        entry = EvalLogger.objects.create(
            target_type=EvalTargetType.TRACE,
            observation_span=root,
            trace=trace,
            custom_eval_config=custom_eval_config,
            eval_task_id=str(eval_task.id),
            status=EvalEntryStatus.RUNNING,
        )
        assert run_entry(entry) in _TERMINAL
        entry.refresh_from_db()
        assert entry.status in _TERMINAL  # dispatch ran end-to-end


_APPLY_ASYNC = "tracer.tasks.eval_clustering.cluster_eval_results_task.apply_async"
_PID = "11111111-1111-1111-1111-111111111111"


@pytest.mark.unit
class TestReseedEvalClusteringHook:
    """The clustering trigger. This is the exact link the TH-5978 cutover
    orphaned; an untested trigger is what let the regression ship silently."""

    def test_dispatches_on_failure(self):
        entry = EvalLogger(
            target_type=EvalTargetType.SPAN, output_bool=False, eval_explanation="bad"
        )
        with patch(_APPLY_ASYNC) as m:
            _reseed_eval_clustering(entry, _PID)
        m.assert_called_once()
        assert m.call_args.kwargs["task_id"] == f"eval-cluster-{_PID}"
        # The conflict policy is the load-bearing half of the coalescing
        # contract: without it a per-eval trigger burst either stampedes one
        # drain per eval or errors outright on the duplicate workflow id.
        assert (
            m.call_args.kwargs["id_conflict_policy"]
            == WorkflowIDConflictPolicy.USE_EXISTING
        )

    def test_dispatches_on_float_below_one(self):
        entry = EvalLogger(
            target_type=EvalTargetType.SPAN, output_float=0.4, eval_explanation="meh"
        )
        with patch(_APPLY_ASYNC) as m:
            _reseed_eval_clustering(entry, _PID)
        m.assert_called_once()

    def test_dispatches_on_template_mapped_choice_score(self):
        template = EvalTemplate(
            config={"output": "choices"},
            choice_scores={"Good": 1.0, "Bad": 0.0},
            pass_threshold=0.5,
        )
        config = CustomEvalConfig(eval_template=template)
        entry = EvalLogger(
            target_type=EvalTargetType.SPAN,
            custom_eval_config=config,
            output_str='{"choice": "Bad"}',
            output_str_list=["Bad"],
            eval_explanation="bad",
        )
        with patch(_APPLY_ASYNC) as m:
            _reseed_eval_clustering(entry, _PID)
        m.assert_called_once()

    def test_no_dispatch_on_pass(self):
        entry = EvalLogger(
            target_type=EvalTargetType.SPAN, output_bool=True, eval_explanation="good"
        )
        with patch(_APPLY_ASYNC) as m:
            _reseed_eval_clustering(entry, _PID)
        m.assert_not_called()

    def test_no_dispatch_without_explanation(self):
        entry = EvalLogger(
            target_type=EvalTargetType.SPAN, output_bool=False, eval_explanation=""
        )
        with patch(_APPLY_ASYNC) as m:
            _reseed_eval_clustering(entry, _PID)
        m.assert_not_called()

    def test_dispatch_failure_is_swallowed(self):
        """A clustering-dispatch hiccup must never fail an eval that already
        produced a result (fail-open)."""
        entry = EvalLogger(
            target_type=EvalTargetType.SPAN, output_bool=False, eval_explanation="bad"
        )
        with patch(_APPLY_ASYNC, side_effect=RuntimeError("temporal down")):
            _reseed_eval_clustering(entry, _PID)  # must not raise

    def test_session_dispatch_reaches_terminal(
        self,
        observe_project,
        custom_eval_config,
        eval_task,
        stub_run_eval,
        stub_cost_log,
    ):
        session = TraceSession.objects.create(project=observe_project, name="sess")
        seed_ch_trace_sessions([session])  # forced CH reads the curated session.
        entry = EvalLogger.objects.create(
            target_type=EvalTargetType.SESSION,
            trace_session=session,
            custom_eval_config=custom_eval_config,
            eval_task_id=str(eval_task.id),
            status=EvalEntryStatus.RUNNING,
        )
        assert run_entry(entry) in _TERMINAL
        entry.refresh_from_db()
        assert entry.status in _TERMINAL


@pytest.mark.django_db
class TestOneClaimIsOneEvaluation:
    """A reclaim must end the run it took the entry from.

    A worker can be holding an entry the reaper has already requeued and another
    worker has re-claimed: the workflow-start reaper requeues whatever a
    previous, closed execution abandoned, and an activity of that execution can
    still be running on a worker. ``RUNNING`` alone cannot tell those two runs
    apart — the row is ``RUNNING`` again under the new claim, so the old run's
    writes land on it. The claim's ``updated_at`` stamp is the epoch that can.

    The fence is a write fence: it is taken when the run starts and refuses
    every write made under a stamp the row no longer carries. It is not, and
    cannot be, a check that the claim the *activity was scheduled under* is
    still the row's — the activity is handed an entry id and nothing else. What
    keeps that gap from being reachable is upstream: the scheduled sweep asks
    Temporal before it reaps and leaves a progressing workflow's entries alone,
    so no reaper requeues an entry whose activity is still queued for a live
    execution (``tracer/tests/test_eval_task_sweeper.py``).
    """

    @staticmethod
    def _claimed_entry(eval_task, observation_span, custom_eval_config, *, age=0):
        from django.utils import timezone

        entry = _span_entry(eval_task, observation_span, custom_eval_config)
        if age:
            EvalLogger.all_objects.filter(id=entry.id).update(
                updated_at=timezone.now() - timedelta(seconds=age)
            )
            entry.refresh_from_db()
        return entry

    def test_a_stale_workers_result_does_not_land_on_the_reclaimed_row(
        self, observation_span, custom_eval_config, eval_task, monkeypatch
    ):
        """The full production seam: a worker whose run has outlived the stale
        threshold is still inside its evaluation when the sweep reaps its entry
        and another worker re-claims it. The first worker's result must be
        refused, or the entry ends holding an abandoned run's verdict under an
        abandoned run's config hash and the eval is paid for twice. The reap
        here uses a zero-second threshold so the test states the outcome rather
        than waiting two hours for it."""
        from tracer.services.eval_tasks.entries import (
            claim_pending_batch,
            persist_eval_result,
            writing_onto_entry,
        )
        from tracer.services.eval_tasks.reaper import reap_stale_running

        entry = self._claimed_entry(
            eval_task, observation_span, custom_eval_config, age=10_800
        )

        def _reaped_then_reclaimed(target, *_a, **_k):
            # The sweep ticks while this worker is inside its evaluation.
            assert reap_stale_running(
                eval_task, older_than_seconds=0, max_attempts=3
            ) == (1, 0)
            assert len(claim_pending_batch(eval_task, 1)) == 1
            # ... and only now does the abandoned run write its result, through
            # the same engine-write seam the real _run_for_target opens.
            with writing_onto_entry(target.id):
                persist_eval_result(
                    {"eval_explanation": "stale worker result", "error": False}
                )

        reseeded = []
        monkeypatch.setattr(
            "tracer.services.eval_tasks.run_entry._run_for_target",
            _reaped_then_reclaimed,
        )
        monkeypatch.setattr(
            "tracer.services.eval_tasks.run_entry._reseed_eval_clustering",
            lambda *a, **k: reseeded.append(1),
        )

        # The refused write is the run's outcome, so it has to be what the run
        # reports. Returning a terminal status here would tell the workflow --
        # and the operator reading ``eval_task_entry_run`` -- that this entry
        # completed, when the row is another claim's and holds no result of
        # ours; the clustering seed would likewise fire on that row's state.
        assert run_entry(entry) == "reclaimed"

        assert reseeded == []
        entry.refresh_from_db()
        assert entry.status == EvalEntryStatus.RUNNING
        assert entry.eval_explanation != "stale worker result"

    def test_a_terminalization_refused_mid_run_is_reported_as_a_reclaim(
        self, observation_span, custom_eval_config, eval_task, monkeypatch
    ):
        """Same for the failure branches: an eval that raised under a claim the
        row no longer carries has produced nothing for this entry, so it cannot
        report ``errored`` either -- and must not spend one of the entry's three
        reclaims on a row it does not own."""
        from tracer.services.eval_tasks.entries import claim_pending_batch
        from tracer.services.eval_tasks.reaper import reap_stale_running

        entry = self._claimed_entry(
            eval_task, observation_span, custom_eval_config, age=10_800
        )

        def _reaped_then_raised(*_a, **_k):
            assert reap_stale_running(
                eval_task, older_than_seconds=0, max_attempts=3
            ) == (1, 0)
            assert len(claim_pending_batch(eval_task, 1)) == 1
            raise ValueError("eval blew up")

        monkeypatch.setattr(
            "tracer.services.eval_tasks.run_entry._run_for_target",
            _reaped_then_raised,
        )

        assert run_entry(entry) == "reclaimed"

        entry.refresh_from_db()
        assert entry.status == EvalEntryStatus.RUNNING
        assert entry.error_message != "eval blew up"

    def test_an_entry_requeued_before_its_run_started_is_not_evaluated(
        self, observation_span, custom_eval_config, eval_task, monkeypatch
    ):
        """``run_eval_entry_activity`` has no schedule-to-start timeout, so a
        claimed entry can wait in the queue past the stale threshold, be
        requeued, and only then reach a worker. Evaluating it is a pure double
        charge: the entry belongs to whoever re-claimed it."""
        entry = self._claimed_entry(eval_task, observation_span, custom_eval_config)
        EvalLogger.all_objects.filter(id=entry.id).update(
            status=EvalEntryStatus.PENDING
        )
        ran = []
        monkeypatch.setattr(
            "tracer.services.eval_tasks.run_entry._run_for_target",
            lambda *a, **k: ran.append(1),
        )

        assert run_entry(entry) == "reclaimed"

        assert ran == []
        entry.refresh_from_db()
        assert entry.status == EvalEntryStatus.PENDING

    def test_the_sweep_cannot_reclaim_an_entry_a_worker_is_evaluating(
        self, observation_span, custom_eval_config, eval_task, monkeypatch
    ):
        """``claim_pending_batch`` stamps a whole batch ``RUNNING`` at once and
        the drain runs it a few at a time, so the tail of a batch can sit
        claimed for hours before its run begins. The run re-stamps
        ``updated_at`` when it actually starts, so a reap that meets a started
        run measures that run rather than the claim it came from — which is
        what the pinned threshold invariant models. (The claimed-but-unstarted
        tail is out of the sweep's reach for a different reason: its workflow is
        progressing, and the sweep asks before it reaps.)"""
        from tracer.services.eval_tasks.reaper import reap_stale_running

        entry = self._claimed_entry(
            eval_task, observation_span, custom_eval_config, age=10_800
        )
        reaped = []

        monkeypatch.setattr(
            "tracer.services.eval_tasks.run_entry._run_for_target",
            lambda *a, **k: reaped.append(
                reap_stale_running(eval_task, older_than_seconds=7_200, max_attempts=3)
            ),
        )

        run_entry(entry)

        assert reaped == [(0, 0)]
