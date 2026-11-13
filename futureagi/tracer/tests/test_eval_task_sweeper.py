"""Unit tests for the scheduled eval-task recovery sweep.

Before this sweep existed a task whose workflow stopped kept its pending
entries forever: the reaper only runs at workflow start, every start is
user-initiated, and ``has_undrained_work`` is only read from inside a running
workflow. These tests pin the recovery contract — what is swept, what is left
alone, and the threshold that keeps the reap from racing a live worker.
"""

import io
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone

from accounts.models.organization import Organization
from model_hub.models.ai_model import AIModel
from model_hub.models.evals_metric import EvalTemplate
from tfc.temporal.eval_tasks import client as eval_task_client
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import (
    EvalTask,
    EvalTaskLogger,
    EvalTaskStatus,
    RowType,
    RunType,
)
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.tasks import eval_task_sweeper as sweeper

# The real starter, bound at import: ``tracer/tests/conftest.py`` replaces it
# with a stub for every test in this package before the test body runs.
_REAL_STARTER = eval_task_client.start_eval_task_workflow_sync

_UNDRAINED = (EvalEntryStatus.PENDING, EvalEntryStatus.RUNNING)


@pytest.fixture(autouse=True)
def only_this_modules_entries(db):
    """Start every test from an empty undrained set.

    The sweep is a system-wide job: it reads every task in the database, so any
    entry another module left behind changes its answer. ``--reuse-db`` is in
    this repo's pytest addopts and a ``transaction=True`` test commits its rows,
    so leftovers survive between sessions. The delete runs inside the test's own
    transaction and rolls back with it, leaving nothing changed for anyone else.
    """
    cache.delete("eval-task-sweep:position:v1")
    EvalLogger.all_objects.filter(status__in=_UNDRAINED).delete()
    EvalTask.all_objects.filter(run_type=RunType.CONTINUOUS).delete()


@pytest.fixture
def sweep_project(db, organization, workspace):
    return Project.objects.create(
        name="Sweep Project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="experiment",
        config=[],
    )


@pytest.fixture
def sweep_config(db, sweep_project, organization, workspace):
    template = EvalTemplate.objects.create(
        name="Sweep Template",
        description="t",
        organization=organization,
        workspace=workspace,
        config={"type": "pass_fail", "criteria": "c"},
    )
    return CustomEvalConfig.objects.create(
        name="Sweep Eval",
        project=sweep_project,
        eval_template=template,
        config={},
        mapping={"input": "input"},
        filters={},
    )


@pytest.fixture
def make_task(db, sweep_project, sweep_config):
    def _make(status=EvalTaskStatus.RUNNING, run_type=RunType.HISTORICAL):
        task = EvalTask.objects.create(
            project=sweep_project,
            name="sweep task",
            filters={},
            sampling_rate=100.0,
            spans_limit=100,
            run_type=run_type,
            status=status,
            row_type=RowType.SPANS,
        )
        task.evals.add(sweep_config)
        return task

    return _make


@pytest.fixture
def make_entry(db, sweep_config):
    def _make(task, status=EvalEntryStatus.PENDING, age_seconds=0, deleted=False):
        trace = Trace.objects.create(project=task.project, name="sw")
        span = ObservationSpan.objects.create(
            id=f"sw-{uuid.uuid4().hex[:12]}",
            project=task.project,
            trace=trace,
            name="s",
            observation_type="llm",
        )
        entry = EvalLogger.objects.create(
            target_type=EvalTargetType.SPAN,
            observation_span=span,
            trace=trace,
            custom_eval_config=sweep_config,
            eval_task_id=str(task.id),
            status=status,
            deleted=deleted,
        )
        # ``updated_at`` is auto_now, so age it with an explicit update.
        EvalLogger.all_objects.filter(id=entry.id).update(
            updated_at=timezone.now() - timedelta(seconds=age_seconds)
        )
        entry.refresh_from_db()
        return entry

    return _make


def client_progressing():
    from tfc.temporal.eval_tasks import client

    return client.WF_PROGRESSING


# How the sweep starts a workflow: coalescing, never terminating, and carrying
# the answer of the describe it was gated on rather than describing again.
_SWEEP_RESTART = {"replace_existing": False, "workflow_confirmed_stopped": True}


@pytest.fixture
def temporal(monkeypatch):
    """Stand in for the Temporal client: record describes and starts."""
    from tfc.temporal.eval_tasks import client

    calls = {"described": [], "started": [], "verdict": client.WF_CLOSED}

    def _describe(task_id):
        calls["described"].append(str(task_id))
        verdict = calls["verdict"]
        if isinstance(verdict, Exception):
            raise verdict
        return verdict

    def _start(task, **kwargs):
        calls["started"].append((str(task.id), kwargs))
        return "wf"

    monkeypatch.setattr(client, "describe_eval_task_workflow_sync", _describe)
    monkeypatch.setattr(client, "start_eval_task_workflow_sync", _start)
    return calls


@pytest.mark.django_db
class TestFindStrandedTasks:
    def test_a_task_with_pending_work_is_a_candidate(self, make_task, make_entry):
        task = make_task()
        make_entry(task)

        assert [str(t.id) for t in sweeper.find_stranded_tasks()] == [str(task.id)]

    def test_a_fully_drained_task_is_not_a_candidate(self, make_task, make_entry):
        task = make_task()
        make_entry(task, status=EvalEntryStatus.COMPLETED)
        make_entry(task, status=EvalEntryStatus.SKIPPED)

        assert sweeper.find_stranded_tasks() == []

    def test_soft_deleted_entries_are_not_undrained_work(self, make_task, make_entry):
        """A Delete & rerun soft-deletes every live entry. Those rows keep
        ``status=pending``, so counting them would restart that task forever."""
        task = make_task()
        make_entry(task, deleted=True)

        assert sweeper.find_stranded_tasks() == []

    def test_an_entry_with_a_blank_task_id_cannot_kill_the_tick(
        self, make_task, make_entry
    ):
        """``eval_task_id`` is a CharField, and rows carrying an empty string
        exist — ``queries/eval_clustering.py`` filters them out by name. An empty
        string reaching the ``id__in`` lookup against a UUID column raises before
        the per-task error handling, and the sweep runs with ``max_retries=0``,
        so one such row would kill every tick forever."""
        task = make_task()
        make_entry(task)
        orphan = make_entry(task)
        EvalLogger.all_objects.filter(id=orphan.id).update(eval_task_id="")

        assert [str(t.id) for t in sweeper.find_stranded_tasks()] == [str(task.id)]

    @pytest.mark.parametrize(
        "status", [EvalTaskStatus.PAUSED, EvalTaskStatus.DELETED, EvalTaskStatus.FAILED]
    )
    def test_paused_deleted_and_failed_tasks_are_left_alone(
        self, make_task, make_entry, status
    ):
        task = make_task(status=status)
        make_entry(task)

        assert sweeper.find_stranded_tasks() == []

    def test_failed_tasks_join_the_sweep_only_when_opted_in(
        self, make_task, make_entry, settings
    ):
        task = make_task(status=EvalTaskStatus.FAILED)
        make_entry(task)

        settings.EVAL_TASK_SWEEP_RECOVER_FAILED = True

        assert [str(t.id) for t in sweeper.find_stranded_tasks()] == [str(task.id)]

    def test_the_longest_stranded_task_is_served_first_and_the_tick_is_capped(
        self, make_task, make_entry
    ):
        oldest = make_task()
        make_entry(oldest, age_seconds=86_400)
        newest = make_task()
        make_entry(newest, age_seconds=1)

        found = sweeper.find_stranded_tasks(limit=1)

        assert [str(t.id) for t in found] == [str(oldest.id)]

    def test_a_non_sweepable_task_cannot_hold_the_tick_slot(
        self, make_task, make_entry
    ):
        """A paused task never drains, so its entries' ``updated_at`` never
        advances and it sorts oldest on every tick for ever. If the per-tick cap
        were applied before the task-status filter it would own a slot
        permanently and the sweepable task behind it would never be reached."""
        parked = make_task(status=EvalTaskStatus.PAUSED)
        make_entry(parked, age_seconds=864_000)
        sweepable = make_task()
        make_entry(sweepable, age_seconds=1)

        found = sweeper.find_stranded_tasks(limit=1)

        assert [str(t.id) for t in found] == [str(sweepable.id)]

    def test_entries_whose_task_is_gone_cannot_hold_the_tick_slot(
        self, make_task, make_entry
    ):
        """Same shape with no task row at all: an entry pointing at an id the
        task table does not carry is never sweepable, so it must not be costed
        against the cap either."""
        orphan_host = make_task()
        orphan = make_entry(orphan_host, age_seconds=864_000)
        EvalLogger.all_objects.filter(id=orphan.id).update(
            eval_task_id=str(uuid.uuid4())
        )
        sweepable = make_task()
        make_entry(sweepable, age_seconds=1)

        found = sweeper.find_stranded_tasks(limit=1)

        assert [str(t.id) for t in found] == [str(sweepable.id)]

    def test_non_sweepable_tasks_cannot_fill_the_whole_cap(self, make_task, make_entry):
        """The starvation is permanent, not a one-slot rounding error: fill the
        cap with tasks the sweep refuses to act on and every tick returns an
        empty candidate list, silently, for as long as they exist."""
        for _ in range(3):
            make_entry(make_task(status=EvalTaskStatus.PAUSED), age_seconds=864_000)
        sweepable = make_task()
        make_entry(sweepable, age_seconds=432_000)

        found = sweeper.find_stranded_tasks(limit=3)

        assert [str(t.id) for t in found] == [str(sweepable.id)]


@pytest.mark.django_db
class TestRecoverTask:
    def test_a_task_with_no_live_workflow_is_restarted(
        self, make_task, make_entry, temporal
    ):
        task = make_task()
        make_entry(task)

        outcome = sweeper.recover_task(task, stale_running_seconds=7_200)

        assert outcome["restarted"] is True
        assert temporal["started"] == [(str(task.id), _SWEEP_RESTART)]

    def test_a_progressing_workflow_is_never_restarted(
        self, make_task, make_entry, temporal
    ):
        from tfc.temporal.eval_tasks.client import WF_PROGRESSING

        task = make_task()
        make_entry(task)
        temporal["verdict"] = WF_PROGRESSING

        outcome = sweeper.recover_task(task, stale_running_seconds=7_200)

        assert outcome["restarted"] is False
        assert temporal["started"] == []

    def test_a_progressing_workflows_entries_are_never_reclaimed(
        self, make_task, make_entry, temporal
    ):
        """The sweep asks before it reaps, so a task something is still draining
        costs one describe and nothing else.

        ``claim_pending_batch`` stamps a whole batch ``RUNNING`` at once and the
        drain runs ``max_concurrent`` of them at a time, so the tail of a batch
        is ``RUNNING`` under a frozen claim stamp for as many waves as the batch
        has — four of them at the shipped 50/10, each bounded only by the
        activity ceiling times its retries. Reaping on a stale *claim* therefore
        requeues an entry whose own activity is still sitting in the queue, and
        that activity then takes the re-claim and pays for the evaluation twice.
        Asking first removes the whole class: the only claim the sweep can
        retire belongs to an execution that is no longer running.
        """
        from tfc.temporal.eval_tasks.client import WF_PROGRESSING

        task = make_task()
        entry = make_entry(task, status=EvalEntryStatus.RUNNING, age_seconds=10_000)
        temporal["verdict"] = WF_PROGRESSING

        outcome = sweeper.recover_task(task, stale_running_seconds=7_200)

        entry.refresh_from_db()
        assert outcome["requeued"] == 0
        assert entry.status == EvalEntryStatus.RUNNING
        assert entry.attempts == 0

    def test_a_stale_running_entry_is_reclaimed_once_nothing_is_draining(
        self, make_task, make_entry, temporal
    ):
        """The reap itself is unchanged for the case it exists for: a workflow
        that stopped leaves entries abandoned in ``running`` and the
        workflow-start reaper cannot reach them until something starts one."""
        task = make_task()
        entry = make_entry(task, status=EvalEntryStatus.RUNNING, age_seconds=10_000)

        outcome = sweeper.recover_task(task, stale_running_seconds=7_200)

        entry.refresh_from_db()
        assert outcome["requeued"] == 1
        assert entry.status == EvalEntryStatus.PENDING
        assert entry.attempts == 1

    def test_a_freshly_claimed_entry_is_not_reclaimed(
        self, make_task, make_entry, temporal
    ):
        task = make_task()
        entry = make_entry(task, status=EvalEntryStatus.RUNNING, age_seconds=60)

        outcome = sweeper.recover_task(task, stale_running_seconds=7_200)

        entry.refresh_from_db()
        assert outcome["requeued"] == 0
        assert entry.status == EvalEntryStatus.RUNNING
        assert entry.attempts == 0

    def test_a_failed_task_is_made_pending_before_its_workflow_restarts(
        self, make_task, make_entry, temporal, settings
    ):
        """``get_eval_task_state_activity`` reports a FAILED task inactive, so a
        restart that left the status alone would exit on its first check."""
        settings.EVAL_TASK_SWEEP_RECOVER_FAILED = True
        task = make_task(status=EvalTaskStatus.FAILED)
        make_entry(task)

        outcome = sweeper.recover_task(task, stale_running_seconds=7_200)

        task.refresh_from_db()
        assert outcome["restarted"] is True
        assert task.status == EvalTaskStatus.PENDING


@pytest.mark.django_db
class TestSweepActivity:
    def test_the_sweep_reports_counts_and_restarts_what_it_found(
        self, make_task, make_entry, temporal
    ):
        task = make_task()
        make_entry(task)

        result = sweeper.sweep_stranded_eval_tasks._original_func()

        assert result["candidates"] == 1
        assert result["restarted"] == 1
        assert result["errors"] == 0
        assert temporal["started"] == [(str(task.id), _SWEEP_RESTART)]

    def test_a_healthy_draining_task_is_a_candidate_and_is_reported_as_one(
        self, make_task, make_entry, temporal
    ):
        """``candidates`` is the sweep's working set, not a count of stranded
        tasks.

        ``find_stranded_tasks`` selects every sweepable task holding undrained
        entries, which on a working fleet is mostly tasks draining normally.
        Before the describe moved in front of the reap each one was reaped and
        restarted, so the count really did mean "acted on"; now a candidate
        with a live workflow costs one describe and nothing else. An operator
        reading ``candidates`` as stranded tasks would see a false alarm on
        every busy tick, so the tick says how many were merely progressing and
        the difference is what it acted on.
        """
        healthy = make_task()
        make_entry(healthy)
        temporal["verdict"] = client_progressing()

        result = sweeper.sweep_stranded_eval_tasks._original_func()

        assert result["candidates"] == 1
        assert result["progressing"] == 1
        assert result["candidates"] - result["progressing"] == 0
        assert (result["restarted"], result["entries_requeued"]) == (0, 0)
        assert temporal["started"] == []

    def test_an_unreachable_temporal_does_not_restart_anything(
        self, make_task, make_entry, temporal
    ):
        """A describe that fails for any reason other than NOT_FOUND is the
        service being unreachable, not an answer. Restarting blind on it would
        terminate healthy workflows across the fleet."""
        task = make_task()
        make_entry(task)
        temporal["verdict"] = RuntimeError("temporal unreachable")

        result = sweeper.sweep_stranded_eval_tasks._original_func()

        assert result["errors"] == 1
        assert result["restarted"] == 0
        assert temporal["started"] == []

    def test_a_describe_that_cannot_answer_leaves_the_entries_untouched(
        self, make_task, make_entry, temporal
    ):
        """An unreachable Temporal is not an answer, and the tick's counts have
        to match what it really did.

        Reaping before asking made both halves wrong at once: the entries of a
        task whose describe raised were requeued and an attempt spent on them,
        and then the exception discarded the outcome, so the very line the
        runbook tells an operator to watch reported ``entries_requeued: 0``
        for a reap that had already happened.
        """
        task = make_task()
        entry = make_entry(task, status=EvalEntryStatus.RUNNING, age_seconds=10_000)
        temporal["verdict"] = RuntimeError("temporal unreachable")

        result = sweeper.sweep_stranded_eval_tasks._original_func()

        entry.refresh_from_db()
        assert result["errors"] == 1
        assert result["entries_requeued"] == 0
        assert entry.status == EvalEntryStatus.RUNNING
        assert entry.attempts == 0

    def test_a_restart_that_fails_still_reports_the_reap_it_performed(
        self, make_task, make_entry, temporal, monkeypatch
    ):
        """A describe answers, the reap writes, and only then the start fails.

        Round 2 closed the half where the describe itself raises — nothing is
        written there, so reporting nothing is correct. This is the other half:
        the rows really were requeued and really did spend an attempt, and
        discarding the outcome made ``entries_requeued: 0`` a lie in the one
        line the runbook tells an operator to watch. The entries are recovered
        by the next tick either way; the count is what was wrong.
        """
        from tfc.temporal.eval_tasks import client

        task = make_task()
        entry = make_entry(task, status=EvalEntryStatus.RUNNING, age_seconds=10_000)

        def _start_refused(_task, **_kwargs):
            raise RuntimeError("temporal refused the start")

        monkeypatch.setattr(client, "start_eval_task_workflow_sync", _start_refused)

        result = sweeper.sweep_stranded_eval_tasks._original_func()

        entry.refresh_from_db()
        assert (entry.status, entry.attempts) == (EvalEntryStatus.PENDING, 1)
        assert result["entries_requeued"] == 1
        assert result["errors"] == 1
        assert result["restarted"] == 0

    def test_the_sweep_can_be_turned_off_without_a_deploy_of_its_own(
        self, make_task, make_entry, temporal, settings
    ):
        """A Temporal pause is the immediate lever but not a durable one:
        ``register_temporal_schedules`` runs on every backend container start
        and rebuilds each schedule's state from config, so the pause is undone
        by the next deploy, restart or scale-up. The setting is the rollback
        that survives one, and it has to report itself — an operator must be
        able to tell a disabled sweep from a fleet with nothing stranded."""
        task = make_task()
        make_entry(task)

        settings.EVAL_TASK_SWEEP_MAX_TASKS = 0
        result = sweeper.sweep_stranded_eval_tasks._original_func()

        assert result["disabled"] is True
        assert result["candidates"] == 0
        assert temporal["described"] == []
        assert temporal["started"] == []

    def test_one_tasks_failure_does_not_cost_the_others_their_recovery(
        self, make_task, make_entry, temporal, monkeypatch
    ):
        from tfc.temporal.eval_tasks import client

        first = make_task()
        make_entry(first, age_seconds=86_400)
        second = make_task()
        make_entry(second, age_seconds=1)

        def _describe(task_id):
            if str(task_id) == str(first.id):
                raise RuntimeError("boom")
            return client.WF_CLOSED

        monkeypatch.setattr(client, "describe_eval_task_workflow_sync", _describe)

        result = sweeper.sweep_stranded_eval_tasks._original_func()

        assert result["errors"] == 1
        assert result["restarted"] == 1
        assert [started[0] for started in temporal["started"]] == [str(second.id)]


@pytest.mark.django_db
class TestFreshlyAbandonedClaims:
    """A stopped workflow whose only leftovers are claims too young to reclaim.

    The sweep's own reap applies ``EVAL_TASK_SWEEP_STALE_RUNNING_SECONDS``;
    the workflow it starts reaps with ``ReapInput``'s 600 s. An entry
    abandoned ``RUNNING`` less than 600 s ago passes neither, so a restart
    claims nothing, cannot finalize, and used to mark the task FAILED — which
    the sweep then excluded for good, because failed recovery is opt-in. The
    sweep now defers such a task, leaving it RUNNING and sweepable, and
    restarts it on the first tick that finds the claim reclaimable.
    """

    @pytest.mark.parametrize("age_seconds", [60, 300, 599])
    def test_a_task_holding_only_fresh_claims_is_deferred_then_recovered(
        self, make_task, make_entry, temporal, age_seconds
    ):
        task = make_task()
        entry = make_entry(
            task, status=EvalEntryStatus.RUNNING, age_seconds=age_seconds
        )

        outcome = sweeper.recover_task(task, stale_running_seconds=7_200)

        task.refresh_from_db()
        entry.refresh_from_db()
        assert outcome.get("deferred") is True, outcome
        assert outcome["restarted"] is False
        assert temporal["started"] == []
        assert task.status == EvalTaskStatus.RUNNING
        assert (entry.status, entry.attempts) == (EvalEntryStatus.RUNNING, 0)
        assert [t.id for t in sweeper.find_stranded_tasks(limit=10)] == [task.id]

        # A later tick, once the claim is past the restarted workflow's reap.
        EvalLogger.all_objects.filter(id=entry.id).update(
            updated_at=timezone.now() - timedelta(seconds=601)
        )
        outcome = sweeper.recover_task(task, stale_running_seconds=7_200)

        assert not outcome.get("deferred")
        assert outcome["restarted"] is True
        assert temporal["started"] == [(str(task.id), _SWEEP_RESTART)]

    @pytest.mark.parametrize("age_seconds", [601, 1_000])
    def test_a_claim_the_restarted_workflow_can_reclaim_restarts_at_once(
        self, make_task, make_entry, temporal, age_seconds
    ):
        task = make_task()
        make_entry(task, status=EvalEntryStatus.RUNNING, age_seconds=age_seconds)

        outcome = sweeper.recover_task(task, stale_running_seconds=7_200)

        assert not outcome.get("deferred")
        assert outcome["restarted"] is True

    def test_pending_work_beside_a_fresh_claim_is_restarted(
        self, make_task, make_entry, temporal
    ):
        """Claimable work is reason enough to restart; the workflow itself waits
        out the fresh claim before it finalizes."""
        task = make_task()
        make_entry(task)
        make_entry(task, status=EvalEntryStatus.RUNNING, age_seconds=60)

        outcome = sweeper.recover_task(task, stale_running_seconds=7_200)

        assert not outcome.get("deferred")
        assert outcome["restarted"] is True

    def test_a_reap_that_leaves_nothing_undrained_restarts_to_finalize(
        self, make_task, make_entry, temporal
    ):
        """Poisoning the last stale entry leaves no undrained work, so the task
        drops out of the candidate set; only a restart can finalize it."""
        task = make_task()
        entry = make_entry(task, status=EvalEntryStatus.RUNNING, age_seconds=10_000)
        EvalLogger.all_objects.filter(id=entry.id).update(attempts=3)

        outcome = sweeper.recover_task(task, stale_running_seconds=7_200)

        assert outcome["failed"] == 1
        assert not outcome.get("deferred")
        assert outcome["restarted"] is True

    def test_an_opted_in_failed_task_is_not_flipped_while_deferred(
        self, make_task, make_entry, temporal, settings
    ):
        settings.EVAL_TASK_SWEEP_RECOVER_FAILED = True
        task = make_task(status=EvalTaskStatus.FAILED)
        make_entry(task, status=EvalEntryStatus.RUNNING, age_seconds=60)

        outcome = sweeper.recover_task(task, stale_running_seconds=7_200)

        task.refresh_from_db()
        assert outcome.get("deferred") is True, outcome
        assert task.status == EvalTaskStatus.FAILED
        assert temporal["started"] == []

    def test_the_tick_reports_deferred_tasks(self, make_task, make_entry, temporal):
        task = make_task()
        make_entry(task, status=EvalEntryStatus.RUNNING, age_seconds=60)

        result = sweeper.sweep_stranded_eval_tasks._original_func()

        assert result["candidates"] == 1
        assert result.get("deferred") == 1, result
        assert result["restarted"] == 0
        assert result["errors"] == 0


@pytest.mark.django_db
class TestSweepFailureDiagnostics:
    """An absorbed per-task failure is logged with its cause and traceback;
    ``errors`` alone says something failed and never why."""

    def _failure_line(self, records):
        [line] = [r for r in records if r["event"] == "eval_task_sweep_task_failed"]
        return line

    def test_a_describe_failure_is_logged_with_its_traceback(
        self, make_task, make_entry, temporal
    ):
        import structlog

        task = make_task()
        make_entry(task)
        failure = RuntimeError("temporal unreachable")
        temporal["verdict"] = failure

        with structlog.testing.capture_logs() as records:
            result = sweeper.sweep_stranded_eval_tasks._original_func()

        line = self._failure_line(records)
        assert result["errors"] == 1
        assert line["log_level"] == "warning"
        assert line["task_id"] == str(task.id)
        assert line["error_type"] == "RuntimeError"
        assert line["exc_info"] is failure
        assert failure.__traceback__ is not None

    def test_a_restart_failure_is_logged_with_its_cause_not_the_wrapper(
        self, make_task, make_entry, temporal, monkeypatch
    ):
        import structlog

        from tfc.temporal.eval_tasks import client

        task = make_task()
        make_entry(task)
        failure = RuntimeError("temporal refused the start")

        def _start_refused(_task, **_kwargs):
            raise failure

        monkeypatch.setattr(client, "start_eval_task_workflow_sync", _start_refused)

        with structlog.testing.capture_logs() as records:
            result = sweeper.sweep_stranded_eval_tasks._original_func()

        line = self._failure_line(records)
        assert result["errors"] == 1
        assert line["task_id"] == str(task.id)
        assert line["error_type"] == "RuntimeError"
        assert line["exc_info"] is failure


@pytest.mark.django_db
class TestTheRestartRunsOnTheSweepsOwnDescribe:
    """The restarted run's first reap needs to know that nothing owns the task,
    and the sweep has just asked. Driven through the real starter, with only
    the Temporal RPCs doubled: the answer is handed over rather than asked for
    again, so a restart costs one describe, and no second call can fail and
    drop the run to the blind floor after the deferral check admitted it on
    ``RESTART_REAP_SECONDS``."""

    def _real_starter(self, monkeypatch, describe):
        starts = []

        def _start(**kwargs):
            starts.append(kwargs)
            return SimpleNamespace(id=kwargs["workflow_id"])

        client = eval_task_client
        monkeypatch.setattr(client, "start_eval_task_workflow_sync", _REAL_STARTER)
        monkeypatch.setattr(client, "describe_eval_task_workflow_sync", describe)
        monkeypatch.setattr(client, "start_workflow_sync", _start)
        return starts

    def test_a_restart_costs_one_describe(self, make_task, make_entry, monkeypatch):
        from tfc.temporal.eval_tasks import client

        described = []

        def _describe(task_id):
            described.append(str(task_id))
            return client.WF_CLOSED

        starts = self._real_starter(monkeypatch, _describe)
        task = make_task()
        make_entry(task, status=EvalEntryStatus.RUNNING, age_seconds=1_000)

        outcome = sweeper.recover_task(task, stale_running_seconds=7_200)

        assert outcome["restarted"] is True
        assert described == [str(task.id)]
        [start] = starts
        assert start["workflow_input"].workflow_confirmed_stopped is True

    def test_the_run_reaps_at_the_threshold_the_deferral_check_admitted(
        self, make_task, make_entry, monkeypatch
    ):
        from tfc.temporal.eval_tasks import client
        from tfc.temporal.eval_tasks.types import ReapInput
        from tracer.services.eval_tasks.reaper import effective_stale_seconds
        from tracer.services.eval_tasks.recovery import RESTART_REAP_SECONDS

        answers = iter([client.WF_CLOSED])

        def _describe(_task_id):
            # The sweep's describe answers; any later one fails in transit.
            try:
                return next(answers)
            except StopIteration:
                raise RuntimeError("transient Temporal RPC failure") from None

        starts = self._real_starter(monkeypatch, _describe)
        task = make_task()
        # Past the restarted run's 600 s, inside the sweep's own 7,200 s: the
        # deferral check admits the restart only because the run will reclaim
        # this claim.
        make_entry(task, status=EvalEntryStatus.RUNNING, age_seconds=1_000)

        outcome = sweeper.recover_task(task, stale_running_seconds=7_200)

        assert outcome["restarted"] is True
        [start] = starts
        carried = start["workflow_input"].workflow_confirmed_stopped
        first_reap = effective_stale_seconds(
            ReapInput.older_than_seconds, workflow_confirmed_stopped=carried
        )
        assert first_reap == RESTART_REAP_SECONDS


def test_the_sweep_is_actually_scheduled_and_its_activity_is_registered():
    """A recovery job nobody runs is the defect it is meant to fix. The schedule
    and the activity registration are separate wires — pin both."""
    from tfc.temporal.common.registry import TEMPORAL_ACTIVITY_MODULES
    from tfc.temporal.schedules.tracer import TRACER_SCHEDULES

    scheduled = {
        config.schedule_id: config
        for config in TRACER_SCHEDULES
        if config.schedule_id == "sweep-stranded-eval-tasks"
    }
    config = scheduled["sweep-stranded-eval-tasks"]

    assert config.activity_name == sweeper.sweep_stranded_eval_tasks._activity_name
    assert config.queue == "tasks_s"
    assert 0 < config.interval_seconds <= 600
    assert "tracer.tasks.eval_task_sweeper" in TEMPORAL_ACTIVITY_MODULES


def test_the_mirrored_run_entry_ceiling_matches_the_workflow():
    """The settings module declares the workflow's run-entry ceiling and retry
    count by value, because the workflow module cannot be imported at
    settings-load time. Every bound below is derived from those two constants,
    so this is the test that makes the copy honest: change the workflow and
    this fails rather than the bounds silently describing a ceiling that moved.
    """
    from tfc.settings.runtime_setting_specs import (
        RUN_ENTRY_CEILING_SECONDS,
        RUN_ENTRY_MAX_ATTEMPTS,
    )
    from tfc.temporal.eval_tasks.workflows import (
        _RUN_ENTRY_TIMEOUT,
        RUN_ENTRY_RETRY_POLICY,
    )

    assert RUN_ENTRY_CEILING_SECONDS == _RUN_ENTRY_TIMEOUT.total_seconds()
    assert RUN_ENTRY_MAX_ATTEMPTS == RUN_ENTRY_RETRY_POLICY.maximum_attempts


def test_sweep_stale_threshold_exceeds_a_live_entrys_longest_run():
    """The scheduled reap never runs beside a progressing workflow — the sweep
    asks first — but it can still meet one run: an activity already in flight on
    a worker for an execution that has since closed. Its threshold must stay
    above the longest such a run can live, the workflow's run-entry
    start-to-close ceiling (times its retry attempts, kept as headroom: a closed
    execution dispatches no retries). Below that bound the sweep requeues an
    entry a worker is still evaluating and spends one of its three reclaims.
    This bounds the sweep's own reap only: the workflow it restarts reaps at
    ``RESTART_REAP_SECONDS`` (600 s) whatever the setting says, on the answer
    of the sweep's own describe carried into the start, and the claim-epoch
    fence is what keeps that reap's rows correct.

    Asserted against the spec's **minimum**, not against the live setting. The
    setting resolves from the process environment through ``load_numeric_settings``
    and is bounded only by the spec, so a test that reads the running value
    passes on the default and says nothing about the range an operator can
    actually configure.
    """
    from tfc.settings.runtime_setting_specs import RUNTIME_NUMERIC_SETTING_SPECS
    from tfc.temporal.eval_tasks.workflows import (
        _RUN_ENTRY_TIMEOUT,
        RUN_ENTRY_RETRY_POLICY,
    )

    longest_run = (
        _RUN_ENTRY_TIMEOUT.total_seconds() * RUN_ENTRY_RETRY_POLICY.maximum_attempts
    )
    spec = RUNTIME_NUMERIC_SETTING_SPECS["EVAL_TASK_SWEEP_STALE_RUNNING_SECONDS"]

    assert spec.minimum > longest_run
    assert spec.default > longest_run


def test_thresholds_are_derived_and_the_wait_cap_clears_the_blind_floor():
    """A historical drain that cannot finalize waits ``_FINALIZE_WAIT`` and reaps
    again, and fails the task after ``_MAX_IDLE_FINALIZE_WAITS`` waits that
    reclaim nothing. A restart that could not confirm the old execution stopped
    reaps at the blind floor, so its claims only become reclaimable once they
    are older than ``MIN_STALE_RUNNING_SECONDS``. If the waits run out first,
    the drain fails a task it would have recovered, and FAILED is outside the
    sweep's default scope — the stranding round 6 closed, back with no signal.

    The floor is derived from the run-entry ceiling, so raising that ceiling
    (to 40 minutes the floor is 7,201 s, past twelve waits of 600 s) has to fail
    here. The cap stays a literal on purpose: it decides between sleeping and
    raising, so changing it while executions sit in the wait is a
    non-determinism error, and deriving it from the ceiling would turn a
    timeout change into a workflow change. A change to the cap needs its own
    ``workflow.patched`` marker; this test is the guard on the ceiling.
    """
    from tfc.temporal.eval_tasks.types import ReapInput
    from tfc.temporal.eval_tasks.workflows import (
        _FINALIZE_WAIT,
        _MAX_IDLE_FINALIZE_WAITS,
        _RUN_ENTRY_TIMEOUT,
        RUN_ENTRY_RETRY_POLICY,
    )
    from tracer.services.eval_tasks.reaper import (
        MIN_STALE_RUNNING_SECONDS,
        effective_stale_seconds,
    )

    blind = effective_stale_seconds(
        ReapInput.older_than_seconds, workflow_confirmed_stopped=False
    )
    confirmed = effective_stale_seconds(
        ReapInput.older_than_seconds, workflow_confirmed_stopped=True
    )
    # Recomputed from the workflow's own ceiling, not the settings mirror, so
    # moving ``_RUN_ENTRY_TIMEOUT`` alone cannot slip past this.
    longest_run = (
        _RUN_ENTRY_TIMEOUT.total_seconds() * RUN_ENTRY_RETRY_POLICY.maximum_attempts
    )
    longest_wait = _MAX_IDLE_FINALIZE_WAITS * _FINALIZE_WAIT.total_seconds()

    assert blind == MIN_STALE_RUNNING_SECONDS > longest_run
    assert longest_wait > blind
    # A confirmed-stopped restart reclaims after a single wait.
    assert _FINALIZE_WAIT.total_seconds() >= confirmed


@pytest.mark.django_db
class TestIdleContinuousRecovery:
    @pytest.mark.parametrize("status", [EvalTaskStatus.PENDING, EvalTaskStatus.RUNNING])
    @pytest.mark.parametrize(
        "terminal", [None, EvalEntryStatus.COMPLETED, EvalEntryStatus.ERRORED]
    )
    @pytest.mark.parametrize(
        "verdict", [eval_task_client.WF_CLOSED, eval_task_client.WF_ABSENT]
    )
    def test_idle_or_never_dispatched_tasks_are_recovered(
        self, make_task, make_entry, temporal, status, terminal, verdict
    ):
        task = make_task(status=status, run_type=RunType.CONTINUOUS)
        entry = make_entry(task, status=terminal) if terminal else None
        before = (entry.status, entry.updated_at, entry.attempts) if entry else None
        temporal["verdict"] = verdict

        result = sweeper.sweep_stranded_eval_tasks._original_func()

        assert result["candidates"] == 1
        assert result["idle_continuous_candidates"] == 1
        assert result["backlog_candidates"] == 0
        assert result["restarted"] == 1
        assert temporal["started"] == [(str(task.id), _SWEEP_RESTART)]
        task.refresh_from_db()
        assert task.continuous_cursor is None
        assert task.start_time == task.created_at
        if entry:
            entry.refresh_from_db()
            assert (entry.status, entry.updated_at, entry.attempts) == before

    @pytest.mark.parametrize(
        "verdict", [eval_task_client.WF_PROGRESSING, RuntimeError("unavailable")]
    )
    def test_idle_live_or_unknown_workflow_is_left_untouched(
        self, make_task, temporal, verdict
    ):
        task = make_task(run_type=RunType.CONTINUOUS)
        temporal["verdict"] = verdict
        result = sweeper.sweep_stranded_eval_tasks._original_func()
        assert result["candidates"] == 1
        assert result["restarted"] == 0
        assert temporal["started"] == []
        task.refresh_from_db()
        assert task.start_time is None
        assert result["errors"] == int(isinstance(verdict, Exception))

    @pytest.mark.parametrize(
        "status",
        [
            EvalTaskStatus.PAUSED,
            EvalTaskStatus.DELETED,
            EvalTaskStatus.FAILED,
            EvalTaskStatus.COMPLETED,
        ],
    )
    def test_idle_inactive_tasks_are_excluded(self, make_task, temporal, status):
        make_task(status=status, run_type=RunType.CONTINUOUS)
        assert sweeper.find_stranded_tasks() == []

    def test_soft_deleted_idle_task_is_excluded(self, make_task):
        task = make_task(run_type=RunType.CONTINUOUS)
        EvalTask.all_objects.filter(id=task.id).update(deleted=True)
        assert sweeper.find_stranded_tasks() == []

    def test_idle_failed_recovery_is_opt_in(self, make_task, temporal, settings):
        task = make_task(status=EvalTaskStatus.FAILED, run_type=RunType.CONTINUOUS)
        assert sweeper.find_stranded_tasks() == []
        settings.EVAL_TASK_SWEEP_RECOVER_FAILED = True
        result = sweeper.sweep_stranded_eval_tasks._original_func()
        assert result["restarted"] == 1
        task.refresh_from_db()
        assert task.status == EvalTaskStatus.RUNNING
        assert task.start_time == task.created_at

    @pytest.mark.parametrize("with_backlog", [False, True])
    def test_idle_ring_reaches_every_task_even_when_all_are_live(
        self, make_task, make_entry, temporal, settings, with_backlog
    ):
        idle = [make_task(run_type=RunType.CONTINUOUS) for _ in range(5)]
        backlog = make_task() if with_backlog else None
        if backlog:
            make_entry(backlog)
        temporal["verdict"] = eval_task_client.WF_PROGRESSING
        settings.EVAL_TASK_SWEEP_MAX_TASKS = 2
        for _ in idle:
            result = sweeper.sweep_stranded_eval_tasks._original_func()
            assert result["candidates"] <= 2
            if backlog:
                assert result["backlog_candidates"] == 1
        assert {str(t.id) for t in idle} <= set(temporal["described"])

    def test_one_slot_budget_alternates_idle_and_backlog(
        self, make_task, make_entry, temporal, settings
    ):
        idle = make_task(run_type=RunType.CONTINUOUS)
        backlog = make_task()
        make_entry(backlog)
        settings.EVAL_TASK_SWEEP_MAX_TASKS = 1
        temporal["verdict"] = eval_task_client.WF_PROGRESSING
        for _ in range(2):
            assert sweeper.sweep_stranded_eval_tasks._original_func()["candidates"] == 1
        assert temporal["described"] == [str(backlog.id), str(idle.id)]

    def test_idle_tasks_cannot_starve_oldest_backlog(self, make_task, make_entry):
        for _ in range(6):
            make_task(run_type=RunType.CONTINUOUS)
        oldest = make_task()
        make_entry(oldest, age_seconds=10000)
        for _ in range(5):
            make_entry(make_task())
        found = sweeper.find_stranded_tasks(limit=4)
        assert len(found) == 4
        assert len({t.id for t in found}) == 4
        assert [t.id for t in found if t.sweep_candidate_kind == "backlog"][
            0
        ] == oldest.id
        assert sum(t.sweep_candidate_kind == "idle_continuous" for t in found) == 2

    @pytest.mark.parametrize(
        "status", [EvalTaskStatus.PAUSED, EvalTaskStatus.DELETED, EvalTaskStatus.FAILED]
    )
    def test_lifecycle_change_during_describe_prevents_entry_mutation(
        self, make_task, make_entry, temporal, monkeypatch, status
    ):
        task = make_task(run_type=RunType.CONTINUOUS)
        entry = make_entry(task, status=EvalEntryStatus.RUNNING, age_seconds=10000)

        def describe(task_id):
            EvalTask.no_workspace_objects.filter(id=task_id).update(status=status)
            return eval_task_client.WF_CLOSED

        monkeypatch.setattr(
            eval_task_client, "describe_eval_task_workflow_sync", describe
        )
        result = sweeper.sweep_stranded_eval_tasks._original_func()
        entry.refresh_from_db()
        assert (entry.status, entry.attempts) == (EvalEntryStatus.RUNNING, 0)
        assert result["skipped"] == 1
        assert temporal["started"] == []

    def test_soft_delete_during_describe_prevents_reap(
        self, make_task, make_entry, temporal, monkeypatch
    ):
        task = make_task(run_type=RunType.CONTINUOUS)
        entry = make_entry(task, status=EvalEntryStatus.RUNNING, age_seconds=10000)

        def describe(task_id):
            EvalTask.all_objects.filter(id=task_id).update(deleted=True)
            return eval_task_client.WF_CLOSED

        monkeypatch.setattr(
            eval_task_client, "describe_eval_task_workflow_sync", describe
        )
        result = sweeper.sweep_stranded_eval_tasks._original_func()
        entry.refresh_from_db()
        assert (entry.status, entry.attempts) == (EvalEntryStatus.RUNNING, 0)
        assert result["skipped"] == 1
        assert temporal["started"] == []

    def test_pause_after_reap_prevents_dispatch(self, make_task, temporal, monkeypatch):
        from tracer.services.eval_tasks import recovery

        task = make_task(run_type=RunType.CONTINUOUS)

        def deferred_check(*args, **kwargs):
            EvalTask.no_workspace_objects.filter(id=task.id).update(
                status=EvalTaskStatus.PAUSED
            )
            return False

        monkeypatch.setattr(
            recovery, "only_unreclaimable_claims_remain", deferred_check
        )
        result = sweeper.sweep_stranded_eval_tasks._original_func()
        assert result["skipped"] == 1
        assert temporal["started"] == []
        task.refresh_from_db()
        assert task.start_time is None

    def test_dispatch_failure_remains_discoverable_without_moving_floor(
        self, make_task, temporal, monkeypatch
    ):
        task = make_task(status=EvalTaskStatus.PENDING, run_type=RunType.CONTINUOUS)
        floor = timezone.now() - timedelta(days=2)
        cursor = floor + timedelta(hours=1)
        EvalTask.no_workspace_objects.filter(id=task.id).update(
            start_time=floor, continuous_cursor=cursor
        )

        def start(*args, **kwargs):
            raise RuntimeError("dispatch unavailable")

        monkeypatch.setattr(eval_task_client, "start_eval_task_workflow_sync", start)
        for _ in range(2):
            result = sweeper.sweep_stranded_eval_tasks._original_func()
            assert result["errors"] == 1
            assert result["candidates"] == 1
            task.refresh_from_db()
            assert task.start_time == floor
            assert task.continuous_cursor == cursor

    def test_continuous_restart_uses_real_coalescing_starter(
        self, make_task, monkeypatch
    ):
        from temporalio.common import WorkflowIDConflictPolicy

        task = make_task(status=EvalTaskStatus.PENDING, run_type=RunType.CONTINUOUS)
        starts = TestTheRestartRunsOnTheSweepsOwnDescribe()._real_starter(
            monkeypatch, lambda _: eval_task_client.WF_ABSENT
        )
        outcome = sweeper.recover_task(task, stale_running_seconds=7200)
        assert outcome["restarted"]
        [start] = starts
        assert start["workflow_id"] == f"eval-task-{task.id}"
        assert start["id_conflict_policy"] == WorkflowIDConflictPolicy.USE_EXISTING
        assert start["workflow_input"].workflow_confirmed_stopped

    def test_pause_immediately_before_dispatch_wins(
        self, make_task, temporal, monkeypatch
    ):
        task = make_task(status=EvalTaskStatus.PENDING, run_type=RunType.CONTINUOUS)
        manager = EvalTask.no_workspace_objects
        original_filter = manager.filter

        def pause_before_final_read(*args, **kwargs):
            if kwargs == {"id": task.id, "status": EvalTaskStatus.RUNNING}:
                EvalTask.all_objects.filter(id=task.id).update(
                    status=EvalTaskStatus.PAUSED
                )
            return original_filter(*args, **kwargs)

        monkeypatch.setattr(manager, "filter", pause_before_final_read)
        result = sweeper.sweep_stranded_eval_tasks._original_func()
        assert result["skipped"] == 1
        assert temporal["started"] == []
        task.refresh_from_db()
        assert task.status == EvalTaskStatus.PAUSED

    def test_lost_rotation_cache_does_not_disable_recovery(
        self, make_task, temporal, monkeypatch
    ):
        def unavailable(*args, **kwargs):
            raise RuntimeError("cache unavailable")

        make_task(run_type=RunType.CONTINUOUS)
        monkeypatch.setattr(cache, "get", unavailable)
        monkeypatch.setattr(cache, "set", unavailable)
        result = sweeper.sweep_stranded_eval_tasks._original_func()
        assert result["restarted"] == 1


@pytest.mark.django_db
class TestCreateDispatchFailure:
    """A create reported as failed must leave nothing the sweep can activate."""

    NAME = "dispatch-failed create"

    def _create(self, auth_client, project, config):
        return auth_client.post(
            "/tracer/eval-task/",
            {
                "project": str(project.id),
                "name": self.NAME,
                "run_type": RunType.CONTINUOUS,
                "row_type": RowType.SPANS,
                "sampling_rate": 100,
                "evals": [str(config.id)],
            },
            format="json",
        )

    def test_a_retry_after_a_failed_create_is_the_only_task_evaluated(
        self,
        auth_client,
        sweep_project,
        sweep_config,
        make_entry,
        temporal,
        monkeypatch,
    ):
        landed = []

        def dispatch_outage(task, **kwargs):
            # A start can reach Temporal and materialize before the client
            # sees the error; whatever it wrote is withdrawn too.
            landed.append(make_entry(task))
            raise RuntimeError("synthetic Temporal outage")

        monkeypatch.setattr(
            "tracer.views.eval_task.start_eval_task_workflow_sync", dispatch_outage
        )
        failed = self._create(auth_client, sweep_project, sweep_config)

        assert failed.status_code == 400
        assert failed.json()["message"] == "Evaluation task could not be created"
        [ghost] = EvalTask.all_objects.filter(project=sweep_project, name=self.NAME)
        assert (ghost.deleted, ghost.status) == (True, EvalTaskStatus.DELETED)
        assert EvalLogger.all_objects.get(id=landed[0].id).deleted

        temporal["verdict"] = eval_task_client.WF_ABSENT
        result = sweeper.sweep_stranded_eval_tasks._original_func()
        assert result["candidates"] == 0, result
        assert temporal["started"] == []

        monkeypatch.setattr(
            "tracer.views.eval_task.start_eval_task_workflow_sync",
            lambda task, **kwargs: f"eval-task-{task.id}",
        )
        retry = self._create(auth_client, sweep_project, sweep_config)

        assert retry.status_code == 200, retry.content
        retried = str(retry.json()["result"]["id"])
        live = EvalTask.objects.filter(project=sweep_project, name=self.NAME)
        assert [str(task.id) for task in live] == [retried]
        sweeper.sweep_stranded_eval_tasks._original_func()
        assert temporal["started"] == [(retried, _SWEEP_RESTART)]


def _legacy_failed_create(make_task, *, age_minutes=120):
    """A row a failed create left live before create withdrew its own row."""
    task = make_task(status=EvalTaskStatus.PENDING, run_type=RunType.CONTINUOUS)
    EvalTask.all_objects.filter(id=task.id).update(
        created_at=timezone.now() - timedelta(minutes=age_minutes)
    )
    task.refresh_from_db()
    return task


def _withdraw_command(*args):
    out = io.StringIO()
    call_command("withdraw_failed_create_eval_tasks", *args, stdout=out)
    return out.getvalue()


def _tally(output, label="total"):
    [line] = [row for row in output.splitlines() if row.startswith(f"{label}: ")]
    pairs = (pair.split("=") for pair in line.split(": ", 1)[1].split())
    return {key: int(value) for key, value in pairs}


@pytest.mark.django_db
class TestWithdrawFailedCreateCommand:
    """Rows dev left live after a failed create: continuous, PENDING, never
    started, and their owner was told "Evaluation task could not be created".
    The idle sweep would activate each from its creation time, so the rollout
    withdraws them with this command before the sweep is enabled."""

    def test_dry_run_counts_per_organization_and_writes_nothing(
        self, make_task, temporal
    ):
        from tracer.selectors.eval_tasks.failed_creates import (
            FAILED_CREATE_PREDICATE,
        )

        ghost = _legacy_failed_create(make_task)
        other_org = Organization.objects.create(name="Synthetic other org")
        other_project = Project.objects.create(
            name="Synthetic other project",
            organization=other_org,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="experiment",
            config=[],
        )
        other = EvalTask.objects.create(
            project=other_project,
            name="synthetic other task",
            run_type=RunType.CONTINUOUS,
            status=EvalTaskStatus.PENDING,
            row_type=RowType.SPANS,
        )
        EvalTask.all_objects.filter(id=other.id).update(
            created_at=timezone.now() - timedelta(hours=3)
        )
        temporal["verdict"] = eval_task_client.WF_ABSENT

        out = _withdraw_command()

        assert f"Predicate: {FAILED_CREATE_PREDICATE}" in out
        for organization_id in (ghost.project.organization_id, other_org.id):
            tally = _tally(out, f"organization {organization_id}")
            assert (tally["matched"], tally["would_withdraw"]) == (1, 1)
        assert _tally(out) == {
            "matched": 2,
            "would_withdraw": 2,
            "withdrawn": 0,
            "workflow_on_record": 0,
            "temporal_unreachable": 0,
            "changed_since_read": 0,
        }
        assert "Dry run: nothing was written" in out
        for name in (
            ghost.name,
            ghost.project.name,
            ghost.project.organization.name,
            other.name,
            other_project.name,
            other_org.name,
        ):
            assert name not in out
        live = EvalTask.objects.filter(
            id__in=[ghost.id, other.id], status=EvalTaskStatus.PENDING
        )
        assert live.count() == 2

    def test_apply_withdraws_like_list_delete_and_a_rerun_matches_nothing(
        self, make_task, temporal
    ):
        ghost = _legacy_failed_create(make_task)
        temporal["verdict"] = eval_task_client.WF_ABSENT

        out = _withdraw_command("--apply")

        assert _tally(out)["withdrawn"] == 1
        row = EvalTask.all_objects.get(id=ghost.id)
        assert (row.deleted, row.status) == (True, EvalTaskStatus.DELETED)
        assert row.deleted_at is not None
        assert not EvalTask.objects.filter(id=ghost.id).exists()

        again = _withdraw_command("--apply")

        assert _tally(again)["matched"] == 0
        assert EvalTask.all_objects.get(id=ghost.id).deleted_at == row.deleted_at

    @pytest.mark.parametrize(
        ("case", "skipped_as"),
        [
            ("start_time", None),
            ("continuous_cursor", None),
            ("end_time", None),
            ("running", None),
            ("paused", None),
            ("historical", None),
            ("completed_result", None),
            ("soft_deleted_result", None),
            ("task_logger", None),
            ("created_moments_ago", None),
            ("live_workflow", "workflow_on_record"),
            ("closed_workflow", "workflow_on_record"),
            ("temporal_unreachable", "temporal_unreachable"),
        ],
    )
    def test_a_task_that_ever_started_or_may_still_start_is_never_touched(
        self, make_task, make_entry, temporal, case, skipped_as
    ):
        task = _legacy_failed_create(make_task)
        rows = EvalTask.all_objects.filter(id=task.id)
        temporal["verdict"] = eval_task_client.WF_ABSENT
        entry = None
        stamp = timezone.now() - timedelta(minutes=30)
        if case in ("start_time", "continuous_cursor", "end_time"):
            rows.update(**{case: stamp})
        elif case == "running":
            rows.update(status=EvalTaskStatus.RUNNING)
        elif case == "paused":
            rows.update(status=EvalTaskStatus.PAUSED)
        elif case == "historical":
            rows.update(run_type=RunType.HISTORICAL)
        elif case == "completed_result":
            entry = make_entry(task, status=EvalEntryStatus.COMPLETED)
        elif case == "soft_deleted_result":
            entry = make_entry(task, deleted=True)
        elif case == "task_logger":
            EvalTaskLogger.objects.create(eval_task=task, status=EvalTaskStatus.RUNNING)
        elif case == "created_moments_ago":
            rows.update(created_at=timezone.now())
        elif case == "live_workflow":
            temporal["verdict"] = eval_task_client.WF_PROGRESSING
        elif case == "closed_workflow":
            temporal["verdict"] = eval_task_client.WF_CLOSED
        else:
            temporal["verdict"] = RuntimeError("synthetic Temporal outage")
        before = rows.values().get()
        entry_before = (entry.deleted, entry.status) if entry else None

        out = _withdraw_command("--apply")

        tally = _tally(out)
        assert tally["withdrawn"] == 0
        if skipped_as:
            assert (tally["matched"], tally[skipped_as]) == (1, 1)
        else:
            assert tally["matched"] == 0
        assert rows.values().get() == before
        if entry:
            entry = EvalLogger.all_objects.get(id=entry.id)
            assert (entry.deleted, entry.status) == entry_before

    def test_a_row_started_after_the_describe_is_skipped(
        self, make_task, temporal, monkeypatch
    ):
        """A sweep or edit that starts the row between the describe and the
        write stamps it, and the locked re-read refuses it."""
        task = _legacy_failed_create(make_task)

        def describe_then_start(task_id):
            EvalTask.no_workspace_objects.filter(id=task_id).update(
                status=EvalTaskStatus.RUNNING, start_time=timezone.now()
            )
            return eval_task_client.WF_ABSENT

        monkeypatch.setattr(
            eval_task_client, "describe_eval_task_workflow_sync", describe_then_start
        )

        out = _withdraw_command("--apply")

        assert (_tally(out)["changed_since_read"], _tally(out)["withdrawn"]) == (1, 0)
        task.refresh_from_db()
        assert (task.deleted, task.status) == (False, EvalTaskStatus.RUNNING)

    def test_the_sweep_does_not_activate_a_withdrawn_legacy_row(
        self, make_task, temporal
    ):
        ghost = _legacy_failed_create(make_task)
        started = make_task(status=EvalTaskStatus.RUNNING, run_type=RunType.CONTINUOUS)
        EvalTask.all_objects.filter(id=started.id).update(
            start_time=timezone.now() - timedelta(hours=1)
        )
        temporal["verdict"] = eval_task_client.WF_ABSENT

        _withdraw_command("--apply")
        result = sweeper.sweep_stranded_eval_tasks._original_func()

        assert result["idle_continuous_candidates"] == 1, result
        assert temporal["started"] == [(str(started.id), _SWEEP_RESTART)]
        row = EvalTask.all_objects.get(id=ghost.id)
        assert (row.deleted, row.status, row.start_time) == (
            True,
            EvalTaskStatus.DELETED,
            None,
        )
