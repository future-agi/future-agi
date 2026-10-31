"""Fix my agent: starting a fix, the engine's calls, closing it, and taking a candidate."""

import io
import tarfile
from contextlib import ExitStack
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import TestCase
from django.utils import timezone

from simulate.models import (
    AgentFixCandidate,
    AgentFixRun,
    AgentVersion,
    HarnessAgentVersionRuntime,
    HostedHarnessAttempt,
    HostedHarnessExecution,
    HostedHarnessJob,
)
from simulate.models.test_execution import CallTranscript
from simulate.pydantic_schemas.agent_fix import (
    CandidateUpdate,
    CandidateVerdict,
    CheckFailure,
    CheckRunRequest,
    Ending,
    EngineReport,
    FailureOwner,
    FixRequest,
    Surface,
)
from simulate.selectors.agent_fix import (
    TRANSCRIPT_CHARS,
    _record,
    check_failure,
    check_run_status,
    committed_usd,
    engine_brief,
    is_retryable,
)
from simulate.services import agent_fix_engine as engine
from simulate.services.agent_fix_runs import (
    close_fix_run,
    launch_fix_run,
    make_candidate_default,
    request_stop,
    retry_fix_run,
    split_held_back,
)
from simulate.services.harness_versioning.versions import record_authoring
from simulate.services.hosted_harness import (
    HostedHarnessError,
    create_selected_harness_run,
    hash_secret,
)
from simulate.services.hosted_harness_gateway import _source_object_key
from simulate.tests.test_harness_agent_versions import _environment, _passing
from simulate.tests.test_harness_amend_archive import NAMES, _key, _read
from simulate.tests.test_harness_versioning_backfill import (
    _agent_version,
)
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_record import _authoring

pytestmark = pytest.mark.django_db

KEYS = [_key(name) for name in NAMES[:2]]
AGENT = {"agent.py": b"PROMPT = 'refund first'\n"}
PATCH = (
    b"diff --git a/agent.py b/agent.py\n--- a/agent.py\n+++ b/agent.py\n"
    b"@@ -1 +1 @@\n-PROMPT = ''\n+PROMPT = 'refund first'\n"
)
State = AgentFixRun.State
Outcome = AgentFixCandidate.Outcome


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True


@pytest.fixture(autouse=True)
def temporal():
    """The workflows the services start and cancel, and the usage check, as mocks."""
    runs, fix_engine = (
        "simulate.services.agent_fix_runs",
        "simulate.services.agent_fix_engine",
    )
    with (
        patch(f"{runs}.start_agent_fix_workflow") as start,
        patch(f"{runs}.cancel_agent_fix_workflow") as cancel,
        patch(f"{runs}.cancel_hosted_harness_gateway_workflow") as cancel_run,
        patch(f"{fix_engine}.start_hosted_harness_gateway_workflow") as start_run,
        patch(f"{fix_engine}.require_harness_call_usage") as usage,
    ):
        yield SimpleNamespace(
            start=start,
            cancel=cancel,
            cancel_run=cancel_run,
            start_run=start_run,
            usage=usage,
        )


@pytest.fixture
def environment(user, workspace, storage):
    """Backfilled to A1/E1/S1: a GitHub agent with two scenarios."""
    return _environment(user, workspace, "fix-env", storage)


@pytest.fixture
def source_run(environment):
    """A finished run: the first scenario failed "refunded", the second held everything."""
    run = _run(environment)
    _receipts(
        run,
        {
            KEYS[0]: {"refunded": False, "greeted": True, "apologised": None},
            KEYS[1]: {"refunded": True},
        },
    )
    return run


def _run(
    environment, keys=KEYS, *, key="source", trials=1, state="completed", **kwargs
):
    run, _ = create_selected_harness_run(
        environment,
        scenario_keys=list(keys),
        trials=trials,
        idempotency_key=key,
        **kwargs,
    )
    HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(state=state)
    run.refresh_from_db()
    return run


def _executions(run):
    return HostedHarnessExecution.no_workspace_objects.filter(job=run).select_related(
        "source_scenario", "call_execution"
    )


def _receipts(run, by_key):
    """Give each call of ``run`` a receipt of its scenario's ``{sub_goal: held}``."""
    for execution in _executions(run):
        key = execution.source_scenario.scenario_key
        _receipt(execution.call_execution, key, by_key[key])


def _receipt(call, key, held):
    call.call_metadata = {
        **(call.call_metadata or {}),
        "hosted_harness_receipt": {
            "scenario_key": key,
            "status": "failed" if False in held.values() else "passed",
            "sub_goals": [
                {"name": name, "held": value, "reason": f"{name}?"}
                for name, value in held.items()
            ],
        },
    }
    call.save(update_fields=["call_metadata"])


def _fix_run(
    source_run, *, state=State.RUNNING, budget_usd=5.0, locked_paths=(), **fields
):
    request = FixRequest(
        target_scenario_keys=[KEYS[0]],
        failing_sub_goals=["refunded"],
        surfaces=[Surface.PROMPT],
        locked_paths=list(locked_paths),
        budget_usd=budget_usd,
        max_minutes=30,
    )
    environment = source_run.environment
    return AgentFixRun.no_workspace_objects.create(
        organization_id=environment.organization_id,
        workspace_id=environment.workspace_id,
        environment=environment,
        source_run=source_run,
        base_agent_version_id=source_run.agent_version_id,
        state=state,
        request=request.model_dump(mode="json"),
        started_at=timezone.now(),
        **fields,
    )


def _launch(source_run, user, targets=(KEYS[0],)):
    return launch_fix_run(
        source_run,
        target_scenario_keys=list(targets),
        surfaces=[Surface.PROMPT],
        locked_paths=[],
        budget_usd=5.0,
        max_minutes=30,
        user=user,
    )


def _tree(files=AGENT):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        for path, data in files.items():
            member = tarfile.TarInfo(f"source/{path}")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return out.getvalue()


def _candidate(fix_run, attempt=1, *, tree=None, patch_body=PATCH):
    """A candidate added the engine's way, with a preflight that passes."""
    with ExitStack() as stack:
        for one in _passing():
            stack.enter_context(one)
        return engine.add_candidate(
            fix_run,
            attempt=attempt,
            summary=f"attempt {attempt}",
            tree=tree or _tree(),
            patch=patch_body,
        )[0]


def _check(
    fix_run, *, label="base", version_id=None, keys=(KEYS[0],), trials=1, repeat=1
):
    # A check run is dispatched once its transaction commits.
    with TestCase.captureOnCommitCallbacks(execute=True):
        return engine.start_check_run(
            fix_run,
            CheckRunRequest(
                label=label,
                agent_version_id=str(version_id or fix_run.base_agent_version_id),
                scenario_keys=list(keys),
                trials=trials,
                repeat=repeat,
            ),
        )


def _refusal(call, *args, **kwargs):
    with pytest.raises(HostedHarnessError) as caught:
        call(*args, **kwargs)
    return caught.value.code, caught.value.status_code


def _runtime(version_id):
    return HarnessAgentVersionRuntime.no_workspace_objects.get(
        agent_version_id=version_id
    )


def _move_default_suite(environment, user):
    """Make S2 (the first scenario only) the default, so a run's own suite differs."""
    record_authoring(
        environment, _authoring(names=NAMES[:1]), origin="manual", user=user
    )
    environment.refresh_from_db()


class TestHeldBackSplit:
    def test_too_few_failing_cases_hold_none_back(self):
        failing = {f"s{i}": ["refunded"] for i in range(11)}

        assert split_held_back("run", failing) == []

    def test_a_third_of_the_failing_cases_are_held_back_the_same_way_each_time(self):
        failing = {f"s{i}": ["refunded"] for i in range(12)}

        held = split_held_back("run", failing)

        assert len(held) == 4
        assert set(held) <= set(failing)
        assert split_held_back("run", failing) == held

    def test_the_last_case_of_a_failing_sub_goal_stays_to_search_on(self):
        # Only s11 fails apologised: holding it back would leave the worker nothing to fix it on.
        failing = {f"s{i}": ["refunded"] for i in range(11)} | {"s11": ["apologised"]}

        for seed in ("a", "b", "c", "d", "e", "f"):
            held = split_held_back(seed, failing)
            assert len(held) == 4
            assert "s11" not in held

    def test_cases_that_passed_are_neither_held_back_nor_counted(self):
        failing = {f"s{i}": ["refunded"] for i in range(11)} | {"clean": []}

        assert split_held_back("run", failing) == []


class TestLaunch:
    def test_the_split_reaches_the_request_and_the_engine(self, source_run, user):
        with patch(
            "simulate.services.agent_fix_runs.split_held_back", return_value=[KEYS[0]]
        ) as split:
            fix_run = _launch(source_run, user)

        assert split.call_args.args == (
            str(source_run.id),
            {KEYS[0]: ["refunded"]},
        )
        assert fix_run.request["held_back_scenario_keys"] == [KEYS[0]]
        assert engine_brief(fix_run).request.held_back_scenario_keys == [KEYS[0]]

    def test_proves_the_fix_on_the_targets_failed_sub_goals_and_starts_on_commit(
        self, source_run, user, temporal, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            fix_run = _launch(source_run, user)
            temporal.start.assert_not_called()

        temporal.start.assert_called_once_with(str(fix_run.id))
        assert (fix_run.state, fix_run.base_agent_version_id) == (
            State.QUEUED,
            source_run.agent_version_id,
        )
        # A sub-goal that held or was left undecided is not one to fix.
        assert fix_run.request["failing_sub_goals"] == ["refunded"]

    def test_a_workflow_that_cannot_start_ends_the_fix_and_frees_the_environment(
        self, source_run, user, temporal, django_capture_on_commit_callbacks
    ):
        temporal.start.side_effect = RuntimeError("temporal is down")
        with django_capture_on_commit_callbacks(execute=True):
            _launch(source_run, user)
        failed = AgentFixRun.no_workspace_objects.get()

        temporal.start.side_effect = None
        with django_capture_on_commit_callbacks(execute=True):
            again = _launch(source_run, user)

        assert (failed.state, failed.failure["code"]) == (
            State.FAILED,
            "fix_dispatch_failed",
        )
        assert again.state == State.QUEUED

    def test_launches_from_a_run_where_the_agent_broke_the_conversation(
        self, environment, user
    ):
        run = _run(environment)
        for execution in _executions(run):
            call = execution.call_execution
            call.call_metadata = {
                **(call.call_metadata or {}),
                "hosted_harness_receipt": {
                    "status": "errored",
                    "failure": {"domain": "agent", "code": "target_agent_failed"},
                    "sub_goals": [{"name": "refunded", "held": None}],
                },
            }
            call.save(update_fields=["call_metadata"])

        assert _launch(run, user).request["failing_sub_goals"] == ["refunded"]

    def test_refuses_an_environment_as_the_source(self, environment, user):
        assert _refusal(_launch, environment, user) == ("fix_source_not_a_run", 422)

    @pytest.mark.parametrize(
        "flag", ["HARNESS_VERSIONING_ENABLED", "HARNESS_BUNDLE_CERTIFY_ENABLED"]
    )
    def test_refuses_without_versions_or_builds(self, source_run, user, settings, flag):
        setattr(settings, flag, False)

        assert _refusal(_launch, source_run, user) == ("fix_unavailable", 422)
        assert not AgentFixRun.no_workspace_objects.exists()

    def test_refuses_a_run_that_has_not_finished(self, source_run, user):
        HostedHarnessJob.no_workspace_objects.filter(id=source_run.id).update(
            state=HostedHarnessJob.State.RUNNING
        )
        source_run.refresh_from_db()

        assert _refusal(_launch, source_run, user) == ("fix_source_running", 409)

    @pytest.mark.parametrize("kind", ["provider", "phone", "livekit"])
    def test_refuses_an_agent_whose_source_runs_elsewhere(self, source_run, user, kind):
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=source_run.agent_version_id
        ).update(source_kind=kind)

        assert _refusal(_launch, source_run, user) == ("fix_agent_not_editable", 422)

    def test_refuses_scenarios_the_run_did_not_play(self, source_run, user):
        with pytest.raises(HostedHarnessError) as caught:
            _launch(source_run, user, targets=[KEYS[0], "never-played"])

        assert (caught.value.code, caught.value.status_code) == (
            "fix_targets_unknown",
            400,
        )
        assert "never-played" in caught.value.message

    def test_refuses_scenarios_that_passed(self, source_run, user):
        assert _refusal(_launch, source_run, user, targets=[KEYS[1]]) == (
            "fix_targets_passed",
            422,
        )

    def test_one_fix_at_a_time_per_environment(
        self, source_run, user, temporal, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            first = _launch(source_run, user)
            refused = _refusal(_launch, source_run, user)

        assert refused == ("fix_run_active", 409)
        temporal.start.assert_called_once_with(str(first.id))

        close_fix_run(first.id, State.READY)
        again = _launch(source_run, user)

        assert again.id != first.id


class TestRetry:
    def test_a_failed_fix_starts_again_once_with_its_request(
        self, source_run, user, temporal, django_capture_on_commit_callbacks
    ):
        # Our own outage can end a fix on its deadline; that is retried like any failure.
        failed = _fix_run(
            source_run,
            state=State.FAILED,
            failure={"code": "fix_deadline", "message": "m"},
        )

        with django_capture_on_commit_callbacks(execute=True):
            retried = retry_fix_run(failed, user=user)

        temporal.start.assert_called_once_with(str(retried.id))
        assert (
            retried.state,
            retried.retried_from_id,
            retried.request,
            retried.base_agent_version_id,
        ) == (State.QUEUED, failed.id, failed.request, failed.base_agent_version_id)
        assert not is_retryable(failed)
        assert _refusal(retry_fix_run, failed, user=user) == ("fix_not_retryable", 409)

    @pytest.mark.parametrize(
        "state",
        [
            State.RUNNING,
            State.READY,
            State.NO_IMPROVEMENT,
            State.OUT_OF_BUDGET,
            State.STOPPED,
        ],
    )
    def test_only_a_failed_fix_starts_again(self, source_run, user, state):
        fix_run = _fix_run(source_run, state=state)

        assert not is_retryable(fix_run)
        assert _refusal(retry_fix_run, fix_run, user=user) == ("fix_not_retryable", 409)

    def test_a_retry_reads_what_the_failed_fix_finished_instead_of_paying_again(
        self, source_run, user, temporal, django_capture_on_commit_callbacks
    ):
        failed = _fix_run(source_run, token_hash=hash_secret("t"))
        baseline = _check(failed, label="baseline")
        cut_short = _check(failed, label="c1-target")
        HostedHarnessJob.no_workspace_objects.filter(id=baseline.id).update(
            state="completed"
        )
        # Closing cancels the check run still going.
        close_fix_run(failed.id, State.FAILED, failure={"code": "x", "message": "m"})
        with django_capture_on_commit_callbacks(execute=True):
            retried = retry_fix_run(
                AgentFixRun.no_workspace_objects.get(id=failed.id), user=user
            )
        AgentFixRun.no_workspace_objects.filter(id=retried.id).update(
            state=State.RUNNING
        )
        retried.refresh_from_db()

        assert _check(retried, label="baseline").id == baseline.id
        rerun = _check(retried, label="c1-target")
        assert rerun.id != cut_short.id
        # Only the run that had to run again is the retry's, and only it is paid for.
        assert list(
            HostedHarnessJob.no_workspace_objects.filter(fix_run=retried).values_list(
                "id", flat=True
            )
        ) == [rerun.id]
        # Another trial count is another check: it runs.
        assert _check(retried, label="baseline", trials=2).id != baseline.id

    def test_a_retry_runs_again_a_check_finished_at_another_parallelism(
        self, source_run, user, temporal, django_capture_on_commit_callbacks, settings
    ):
        failed = _fix_run(source_run, token_hash=hash_secret("t"))
        baseline = _check(failed, label="baseline")
        HostedHarnessJob.no_workspace_objects.filter(id=baseline.id).update(
            state="completed"
        )
        close_fix_run(failed.id, State.FAILED, failure={"code": "x", "message": "m"})
        with django_capture_on_commit_callbacks(execute=True):
            retried = retry_fix_run(
                AgentFixRun.no_workspace_objects.get(id=failed.id), user=user
            )
        AgentFixRun.no_workspace_objects.filter(id=retried.id).update(
            state=State.RUNNING
        )
        retried.refresh_from_db()
        settings.AGENT_FIX_CHECK_RUN_PARALLELISM += 1

        assert _check(retried, label="baseline").id != baseline.id

    def test_a_retry_keeps_the_seeds_of_the_fix_it_retries(
        self, source_run, user, temporal, django_capture_on_commit_callbacks
    ):
        failed = _fix_run(source_run, token_hash=hash_secret("t"))
        first = _check(failed, label="c1-target", repeat=2)
        close_fix_run(failed.id, State.FAILED, failure={"code": "x", "message": "m"})
        with django_capture_on_commit_callbacks(execute=True):
            retried = retry_fix_run(
                AgentFixRun.no_workspace_objects.get(id=failed.id), user=user
            )
        AgentFixRun.no_workspace_objects.filter(id=retried.id).update(
            state=State.RUNNING
        )
        retried.refresh_from_db()

        again = _check(retried, label="c1-target", repeat=2)

        assert again.id != first.id
        assert again.seed == first.seed


class TestAddCandidate:
    def test_stores_a_checked_version_that_never_becomes_the_default(
        self, environment, source_run, storage, django_capture_on_commit_callbacks
    ):
        fix_run = _fix_run(source_run)

        with (
            patch(
                "simulate.services.harness_agent_versions._schedule_preflight"
            ) as scheduled,
            django_capture_on_commit_callbacks(execute=True),
        ):
            candidate = _candidate(fix_run)

        environment.refresh_from_db()
        runtime = _runtime(candidate.agent_version_id)
        assert environment.agent_version_id == fix_run.base_agent_version_id
        assert candidate.agent_version_id != fix_run.base_agent_version_id
        assert (runtime.trigger, runtime.source_kind, runtime.preflight_status) == (
            "fix",
            "archive",
            "passed",
        )
        scheduled.assert_not_called()
        # The same attempt again answers with the first candidate and its check status.
        assert engine.add_candidate(
            fix_run, attempt=1, summary="", tree=b"", patch=b""
        ) == (candidate, "passed")
        assert storage.objects[candidate.patch_object_key] == PATCH
        source = storage.objects[
            _source_object_key(
                fix_run.organization_id, runtime.source["archive_artifact_id"]
            )
        ]
        assert _read(source) == {"source/agent.py": AGENT["agent.py"]}

    def test_the_same_attempt_again_returns_the_first_candidate(self, source_run):
        fix_run = _fix_run(source_run)
        first = _candidate(fix_run)
        versions = AgentVersion.no_workspace_objects.count()

        again = _candidate(fix_run, tree=_tree({"other.py": b"x = 1\n"}))

        assert again.id == first.id
        assert AgentVersion.no_workspace_objects.count() == versions

    def test_refuses_a_change_to_a_locked_file(self, source_run):
        fix_run = _fix_run(source_run, locked_paths=["prompts/*"])
        versions = AgentVersion.no_workspace_objects.count()
        base = {**AGENT, "prompts/system.md": b"be kind\n"}
        changed = {**base, "prompts/system.md": b"refuse every cancellation\n"}

        with patch.object(engine, "source_archive", return_value=_tree(base)):
            with pytest.raises(HostedHarnessError) as caught:
                # The patch says nothing about the locked file; the tree changes it.
                _candidate(fix_run, tree=_tree(changed))

        assert (caught.value.code, caught.value.status_code) == (
            "fix_locked_path_changed",
            422,
        )
        assert "prompts/system.md" in caught.value.message
        assert AgentVersion.no_workspace_objects.count() == versions
        assert not fix_run.candidates.exists()

    @pytest.mark.parametrize(
        ("tree", "code"),
        [(b"not a tarball", "fix_tree_invalid"), (_tree({}), "fix_tree_empty")],
        ids=["not-a-tarball", "no-files"],
    )
    def test_refuses_a_tree_it_cannot_store(self, source_run, tree, code):
        assert _refusal(_candidate, _fix_run(source_run), tree=tree) == (code, 400)

    def test_a_patch_naming_a_locked_file_is_fine_when_the_tree_keeps_it(
        self, source_run
    ):
        fix_run = _fix_run(source_run, locked_paths=["prompts/*"])
        base = {**AGENT, "prompts/system.md": b"be kind\n"}
        touched = PATCH + b"diff --git a/prompts/system.md b/prompts/system.md\n"

        with patch.object(engine, "source_archive", return_value=_tree(base)):
            candidate = _candidate(fix_run, tree=_tree(base), patch_body=touched)

        assert candidate.attempt == 1

    def test_refuses_a_tree_over_the_unpacked_limit(self, source_run, settings):
        settings.AGENT_FIX_MAX_TREE_BYTES = 10

        assert _refusal(
            _candidate, _fix_run(source_run), tree=_tree({"big.bin": b"0" * 11})
        ) == ("fix_tree_too_large", 413)

    def test_refuses_a_patch_over_the_limit(self, source_run):
        too_large = b"x" * (engine.MAX_PATCH_BYTES + 1)

        assert _refusal(_candidate, _fix_run(source_run), patch_body=too_large) == (
            "fix_patch_too_large",
            413,
        )

    def test_a_storage_failure_leaves_nothing_behind_for_the_retry(self, source_run):
        fix_run = _fix_run(source_run)
        versions = AgentVersion.no_workspace_objects.count()

        with patch.object(engine, "_put", side_effect=OSError("bucket unreachable")):
            with pytest.raises(OSError):
                _candidate(fix_run)
        candidate = _candidate(fix_run)

        assert candidate.attempt == 1
        assert AgentVersion.no_workspace_objects.count() == versions + 1


@pytest.mark.parametrize(
    "call",
    [
        lambda fix_run: _candidate(fix_run),
        lambda fix_run: _check(fix_run),
        lambda fix_run: engine.apply_report(fix_run, EngineReport(stage="x")),
    ],
    ids=["candidate", "check-run", "report"],
)
@pytest.mark.parametrize("state", [State.QUEUED, State.STOPPING, State.READY])
def test_every_engine_call_refuses_a_fix_that_is_not_running(source_run, call, state):
    fix_run = _fix_run(source_run, state=state)

    assert _refusal(call, fix_run) == ("fix_run_not_running", 409)
    assert not HostedHarnessJob.no_workspace_objects.filter(fix_run=fix_run).exists()


class TestCheckRuns:
    def test_runs_the_base_on_the_versions_the_failed_run_used(
        self, environment, source_run, user, temporal
    ):
        _move_default_suite(environment, user)
        fix_run = _fix_run(source_run)

        run = _check(fix_run)

        assert run.fix_run_id == fix_run.id
        assert (
            run.agent_version_id,
            run.environment_version_id,
            run.suite_version_id,
        ) == (
            source_run.agent_version_id,
            source_run.environment_version_id,
            source_run.suite_version_id,
        )
        assert run.suite_version_id != environment.suite_version_id
        temporal.usage.assert_called_once()
        temporal.start_run.assert_called_once()
        assert temporal.start_run.call_args.args[0] == str(run.id)

    def test_runs_a_candidate_of_this_fix(self, source_run):
        fix_run = _fix_run(source_run)
        candidate = _candidate(fix_run)

        run = _check(
            fix_run, label="candidate-1", version_id=candidate.agent_version_id
        )

        assert (run.agent_version_id, run.fix_run_id) == (
            candidate.agent_version_id,
            fix_run.id,
        )

    def test_refuses_any_other_version_of_the_agent(self, source_run):
        fix_run = _fix_run(source_run)
        sibling = _agent_version(fix_run.base_agent_version.agent_definition)

        assert _refusal(_check, fix_run, version_id=sibling.id) == (
            "fix_version_unknown",
            422,
        )

    def test_the_same_label_returns_the_same_run_without_charging_again(
        self, source_run, settings, temporal
    ):
        settings.AGENT_FIX_WORST_CASE_USD_PER_EXECUTION = "1"
        # Room for one execution: a second charge would be refused.
        fix_run = _fix_run(source_run, budget_usd=1.5)

        first = _check(fix_run)
        again = _check(fix_run)

        assert again.id == first.id
        assert (temporal.usage.call_count, temporal.start_run.call_count) == (1, 1)
        assert (
            HostedHarnessJob.no_workspace_objects.filter(fix_run=fix_run).count() == 1
        )

    def test_a_label_reused_for_other_scenarios_is_refused(self, source_run):
        fix_run = _fix_run(source_run)
        _check(fix_run)

        assert _refusal(_check, fix_run, keys=KEYS) == ("idempotency_conflict", 409)

    @pytest.mark.parametrize(("spent", "allowed"), [("0.50", True), ("0.51", False)])
    def test_the_budget_covers_spend_and_the_worst_case_of_the_check(
        self, source_run, settings, temporal, spent, allowed
    ):
        settings.AGENT_FIX_WORST_CASE_USD_PER_EXECUTION = "1"
        fix_run = _fix_run(source_run, budget_usd=1.5, spent_usd=Decimal(spent))

        if allowed:
            assert _check(fix_run).fix_run_id == fix_run.id
        else:
            assert _refusal(_check, fix_run) == ("fix_budget_exhausted", 402)
            temporal.usage.assert_not_called()
            assert not HostedHarnessJob.no_workspace_objects.filter(
                fix_run=fix_run
            ).exists()

    def test_a_check_run_is_held_at_its_worst_case_until_its_usage_is_sealed(
        self, source_run, settings
    ):
        settings.AGENT_FIX_WORST_CASE_USD_PER_EXECUTION = "0.5"
        fix_run = _fix_run(source_run, spent_usd=Decimal("0.25"))
        first = _check(fix_run, trials=2)
        _check(fix_run, label="both", keys=KEYS)

        assert committed_usd(fix_run) == Decimal("2.25")

        HostedHarnessJob.no_workspace_objects.filter(id=first.id).update(
            fix_cost_usd=Decimal("0.09")
        )
        # The sealed run at what it billed; the other still at 2 x $0.50.
        assert committed_usd(fix_run) == Decimal("1.34")

    def test_a_sealed_check_run_is_priced_from_its_billed_usage(
        self, source_run, monkeypatch
    ):
        from ee.usage.schemas.billing_config import PricingTier
        from ee.usage.services.config import BillingConfig
        from simulate.services import harness_usage

        tiers = {
            "text_sim_tokens": [
                PricingTier(up_to=1_000_000, price_per_unit=Decimal(0)),
                PricingTier(up_to=None, price_per_unit=Decimal("0.000002")),
            ],
            "voice_sim_minutes": [
                PricingTier(up_to=None, price_per_unit=Decimal("0.08"))
            ],
        }
        monkeypatch.setattr(harness_usage, "is_oss", lambda: False)
        monkeypatch.setattr(
            BillingConfig, "get_pricing_tiers", lambda self, key: tiers.get(key, [])
        )
        monkeypatch.setattr(
            harness_usage,
            "harness_consumption",
            lambda job: {
                "text_sim_tokens": 46_000,
                "voice_sim_minutes": 0,
                "ai_credits": 0,
            },
        )
        run = _check(_fix_run(source_run))
        not_a_check = source_run

        harness_usage.price_fix_check_run(run.id)
        harness_usage.price_fix_check_run(not_a_check.id)

        run.refresh_from_db()
        not_a_check.refresh_from_db()
        # 46,000 tokens at the first paid price, $2 per million.
        assert run.fix_cost_usd == Decimal("0.0920")
        assert not_a_check.fix_cost_usd is None

    def test_a_check_run_racing_the_close_is_refused(self, source_run):
        fix_run = _fix_run(source_run)  # as the engine's request loaded it
        close_fix_run(fix_run.id, State.STOPPED)

        assert _refusal(_check, fix_run) == ("fix_run_not_running", 409)

    def test_a_check_run_whose_workflow_cannot_start_fails_at_once(
        self, source_run, temporal
    ):
        temporal.start_run.side_effect = RuntimeError("temporal is down")

        run = _check(_fix_run(source_run))

        run.refresh_from_db()
        assert (run.state, run.failure["code"]) == ("failed", "scheduler_unavailable")
        assert run.test_execution.status == "failed"

    def test_a_refused_usage_check_starts_no_run(self, source_run, temporal):
        fix_run = _fix_run(source_run)
        temporal.usage.side_effect = HostedHarnessError(
            "usage_limit_reached", "out of calls", status_code=402
        )

        assert _refusal(_check, fix_run) == ("usage_limit_reached", 402)
        assert not HostedHarnessJob.no_workspace_objects.filter(
            fix_run=fix_run
        ).exists()
        temporal.start_run.assert_not_called()


class TestCheckRunSeeds:
    def test_a_repeat_has_one_seed_in_both_arms_and_repeats_differ(self, source_run):
        fix_run = _fix_run(source_run, token_hash=hash_secret("t"))
        candidate = _candidate(fix_run)

        base = [_check(fix_run, label=f"b-r{k}", repeat=k) for k in (1, 2)]
        tried = [
            _check(
                fix_run,
                label=f"c-r{k}",
                repeat=k,
                version_id=candidate.agent_version_id,
            )
            for k in (1, 2)
        ]

        assert [run.seed for run in base] == [run.seed for run in tried]
        assert base[0].seed != base[1].seed

    def test_a_check_run_asks_for_the_fix_parallelism(self, source_run, settings):
        settings.AGENT_FIX_CHECK_RUN_PARALLELISM = 3

        run = _check(_fix_run(source_run, token_hash=hash_secret("t")))

        assert run.payload["runtime"]["parallelism"] == 3


class TestCheckRunStatus:
    def test_a_trial_says_how_long_its_conversation_took(self, source_run):
        fix_run = _fix_run(source_run, token_hash=hash_secret("t"))
        run = _check(fix_run)
        call = HostedHarnessExecution.no_workspace_objects.get(job=run).call_execution
        call.call_metadata = {
            "hosted_harness_receipt": {
                "sub_goals": [{"name": "refunded", "held": True}],
                "call": {"duration_ms": 36100},
            }
        }
        call.save(update_fields=["call_metadata"])

        [record] = check_run_status(run).records

        assert record.duration_s == 36.1

    @pytest.mark.parametrize(
        ("attempt", "clamped", "expected"),
        [
            pytest.param(1, None, 1, id="degraded in its attempt"),
            pytest.param(None, None, 3, id="never degraded: as it asked"),
            pytest.param(None, {"requested": 3, "admitted": 2}, 2, id="clamped"),
        ],
    )
    def test_a_finished_run_says_the_parallelism_it_really_had(
        self, source_run, settings, attempt, clamped, expected
    ):
        settings.AGENT_FIX_CHECK_RUN_PARALLELISM = 3
        run = _check(_fix_run(source_run, token_hash=hash_secret("t")))
        assert check_run_status(run).parallelism is None  # Still going.
        run.payload["metadata"]["parallelism_clamped"] = clamped
        HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(
            state="completed", payload=run.payload
        )
        run.refresh_from_db()
        HostedHarnessAttempt.no_workspace_objects.create(
            job=run,
            attempt_number=1,
            token_hash=hash_secret("a"),
            fence_hash=hash_secret("b"),
            expires_at=timezone.now(),
            effective_parallelism=attempt,
        )

        assert check_run_status(run).parallelism == expected

    @pytest.mark.parametrize(
        ("receipt", "status", "error", "sub_goals"),
        [
            (
                {
                    "sub_goals": [
                        {"name": "refunded", "held": False},
                        {"name": "greeted", "held": True},
                    ]
                },
                "ok",
                "",
                {"refunded": False, "greeted": True},
            ),
            (
                {
                    "sub_goals": [
                        {"name": "refunded", "held": None},
                        {"name": "greeted", "held": True},
                    ]
                },
                "error",
                "the judge decided nothing on refunded",
                {"greeted": True},
            ),
            (
                {"status": "infrastructure_error"},
                "error",
                "the trial ended infrastructure_error",
                {},
            ),
            ({}, "error", "the trial ended without a result", {}),
            (
                {
                    "status": "errored",
                    "failure": {"domain": "agent", "code": "target_agent_failed"},
                    "sub_goals": [
                        {"name": "refunded", "held": None},
                        {"name": "greeted", "held": True},
                    ],
                },
                "ok",
                "",
                {"refunded": False, "greeted": True},
            ),
            (
                {
                    "status": "errored",
                    "failure": {"domain": "infrastructure"},
                    "sub_goals": [{"name": "refunded", "held": None}],
                },
                "error",
                "the judge decided nothing on refunded",
                {},
            ),
        ],
        ids=[
            "judged",
            "undecided",
            "no-sub-goals",
            "empty",
            "agent-broke-it",
            "infrastructure-broke-it",
        ],
    )
    def test_a_trial_is_ok_only_when_every_sub_goal_was_judged(
        self, receipt, status, error, sub_goals
    ):
        record = _record("refund-key", 2, receipt, "user: hi")

        assert (record.status, record.error, record.sub_goals) == (
            status,
            error,
            sub_goals,
        )
        assert (record.scenario_key, record.trial, record.transcript) == (
            "refund-key",
            2,
            "user: hi",
        )

    def test_reads_finished_trials_with_their_transcripts(self, source_run):
        fix_run = _fix_run(source_run)
        run = _check(fix_run, trials=2)
        # The second trial has no receipt yet.
        judged, _ = sorted(_executions(run), key=lambda one: one.trial_index)
        _receipt(judged.call_execution, KEYS[0], {"refunded": True})
        for at, role, text in (
            (2, "assistant", "a" * TRANSCRIPT_CHARS),
            (1, "user", "hi"),
        ):
            CallTranscript.objects.create(
                call_execution=judged.call_execution,
                speaker_role=role,
                content=text,
                start_time_ms=at,
            )

        status = check_run_status(run)

        assert (status.total, status.finished) == (2, False)
        [record] = status.records
        assert (record.trial, record.status, record.reasons) == (
            judged.trial_index,
            "ok",
            {"refunded": "refunded?"},
        )
        # While the run goes the engine only counts records: no transcripts yet.
        assert record.transcript == ""

        HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(
            state="completed"
        )
        run.refresh_from_db()
        ended = check_run_status(run)
        assert ended.finished is True
        assert ended.records[0].transcript.startswith("user: hi\nassistant: aaa")
        assert len(ended.records[0].transcript) == TRANSCRIPT_CHARS
        assert ended.failure is None

    @pytest.mark.parametrize(
        ("failure", "owner"),
        [
            (
                {"domain": "environment", "code": "credential_unavailable"},
                FailureOwner.USER,
            ),
            (
                {"domain": "platform_sync", "code": "usage_exhausted"},
                FailureOwner.USER,
            ),
            (
                {"domain": "platform_sync", "code": "usage_check_failed"},
                FailureOwner.OURS,
            ),
            ({"domain": "infrastructure", "code": "call_failed"}, FailureOwner.OURS),
            ({"domain": "simulator", "code": "setup_crashed"}, FailureOwner.OURS),
        ],
        ids=[
            "environment",
            "plan-limit",
            "usage-check-down",
            "infrastructure",
            "simulator",
        ],
    )
    def test_a_failure_is_the_users_only_for_their_environment_or_plan(
        self, failure, owner
    ):
        assert check_failure({**failure, "message": "m"}).owner == owner
        assert check_failure(None) is None

    def test_a_failed_run_and_its_errored_trials_say_who_can_fix_them(self, source_run):
        fix_run = _fix_run(source_run)
        run = _check(fix_run, trials=2)
        broke, errored = sorted(_executions(run), key=lambda one: one.trial_index)
        for execution, failure in (
            (broke, {"domain": "agent", "code": "target_agent_failed"}),
            (errored, {"domain": "platform_sync", "code": "usage_exhausted"}),
        ):
            execution.call_execution.call_metadata = {
                "hosted_harness_receipt": {"status": "errored", "failure": failure}
            }
            execution.call_execution.save(update_fields=["call_metadata"])
        HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(
            state="failed",
            failure={
                "domain": "environment",
                "code": "credential_unavailable",
                "message": "the agent's API key was rejected",
            },
        )
        run.refresh_from_db()

        status = check_run_status(run)

        assert status.failure.model_dump(mode="json") == {
            "owner": "user",
            "code": "credential_unavailable",
            "message": "the agent's API key was rejected",
        }
        by_trial = {record.trial: record.failure for record in status.records}
        # The agent's own failure is its behaviour, judged by the verdict: no excuse.
        assert by_trial[broke.trial_index] is None
        assert by_trial[errored.trial_index].owner == FailureOwner.USER

    def test_many_trials_cost_no_more_queries_than_one(
        self, source_run, django_assert_num_queries
    ):
        run = _check(_fix_run(source_run), keys=KEYS, trials=5)
        for execution in _executions(run):
            key = execution.source_scenario.scenario_key
            _receipt(execution.call_execution, key, {"refunded": True})
            CallTranscript.objects.create(
                call_execution=execution.call_execution,
                speaker_role="user",
                content=key,
            )
        # A finished run, so the transcripts and its last attempt are read too.
        HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(
            state="completed"
        )
        run.refresh_from_db()

        with django_assert_num_queries(3):
            status = check_run_status(run)

        assert len(status.records) == status.total == 10


class TestBrief:
    def test_lists_the_failed_runs_suite_and_the_request(
        self, environment, source_run, user
    ):
        _move_default_suite(environment, user)
        fix_run = _fix_run(source_run)

        brief = engine_brief(fix_run)

        assert [one.key for one in brief.inventory] == KEYS
        assert brief.request == FixRequest.model_validate(fix_run.request)
        assert (
            brief.base_agent_version_id,
            brief.last_attempt,
            brief.committed_usd,
        ) == (str(fix_run.base_agent_version_id), 0, 0.0)
        assert (
            brief.deadline_at
            == (fix_run.started_at + timedelta(minutes=30)).isoformat()
        )

    def test_names_the_environment_as_the_user_sees_it(self, source_run):
        from simulate.services.harness_environment import environment_name

        fix_run = _fix_run(source_run)

        assert engine_brief(fix_run).environment_name == environment_name(
            fix_run.environment
        )

    def test_a_relaunch_numbers_after_the_last_candidate_and_counts_the_spend(
        self, source_run
    ):
        fix_run = _fix_run(source_run, token_hash=hash_secret("t"))
        _candidate(fix_run, attempt=1)
        _candidate(fix_run, attempt=103)
        AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
            spent_usd=Decimal("1.25")
        )
        fix_run.refresh_from_db()

        brief = engine_brief(fix_run)

        assert (brief.last_attempt, brief.committed_usd) == (103, 1.25)


class TestReport:
    def test_a_report_leaves_the_metered_spend_alone(self, source_run):
        # Only the model proxy writes spend; the engine's own figure is not trusted.
        fix_run = _fix_run(source_run, spent_usd=Decimal("2"))

        reported = engine.apply_report(fix_run, EngineReport(stage="coding"))

        assert (reported.spent_usd, reported.stage, reported.state) == (
            Decimal("2"),
            "coding",
            State.RUNNING,
        )
        assert reported.heartbeat_at is not None

    def test_updates_this_fixs_candidates_only(self, source_run):
        fix_run = _fix_run(source_run)
        other = _fix_run(source_run, state=State.READY)
        mine, theirs = (
            AgentFixCandidate.no_workspace_objects.create(
                fix_run=owner,
                attempt=1,
                agent_version_id=owner.base_agent_version_id,
                patch_object_key="k",
            )
            for owner in (fix_run, other)
        )
        verdict = CandidateVerdict(
            accepted=False,
            reason="held-back regressed",
            target_pass_rate=(0.2, 0.6),
            must_pass_within_noise=["patel-clinic-days: patel_days_told"],
        )

        engine.apply_report(
            fix_run,
            EngineReport(
                stage="judging",
                candidates=[
                    CandidateUpdate(
                        candidate_id=str(mine.id), outcome="rejected", verdict=verdict
                    ),
                    CandidateUpdate(candidate_id=str(theirs.id), outcome="winner"),
                ],
            ),
        )

        mine.refresh_from_db()
        theirs.refresh_from_db()
        assert (mine.outcome, mine.verdict) == (
            Outcome.REJECTED,
            verdict.model_dump(mode="json"),
        )
        assert (theirs.outcome, theirs.verdict) == (Outcome.PENDING, None)

    @pytest.mark.parametrize(
        ("ending", "state"),
        [
            (Ending.READY, State.READY),
            (Ending.NO_IMPROVEMENT, State.NO_IMPROVEMENT),
            (Ending.NOT_REPRODUCED, State.NOT_REPRODUCED),
            (Ending.OUT_OF_BUDGET, State.OUT_OF_BUDGET),
            (Ending.FAILED, State.FAILED),
        ],
    )
    def test_an_ending_closes_the_fix_in_its_state(self, source_run, ending, state):
        fix_run = _fix_run(source_run, token_hash=hash_secret("t"))

        closed = engine.apply_report(
            fix_run,
            EngineReport(stage="done", ending=ending, summary="why"),
        )

        closed.refresh_from_db()
        assert closed.state == state
        assert closed.token_hash == ""
        assert closed.finished_at is not None
        assert closed.report["summary"] == "why"
        assert "candidates" not in closed.report
        # Only a failure carries a failure, so the read never shows the engine's error as one.
        assert (closed.failure or {}).get("code") == (
            "fix_engine_failed" if ending == Ending.FAILED else None
        )
        # A report after the ending is refused: the engine exits.
        assert _refusal(engine.apply_report, closed, EngineReport(stage="late")) == (
            "fix_run_not_running",
            409,
        )

    @pytest.mark.parametrize(
        ("failure", "expected"),
        [
            (
                CheckFailure(
                    owner=FailureOwner.USER,
                    code="usage_limit",
                    message="AI credits are paused by your budget",
                ),
                {
                    "code": "usage_limit",
                    "message": "AI credits are paused by your budget",
                },
            ),
            (
                CheckFailure(
                    owner=FailureOwner.OURS,
                    code="model_unavailable",
                    message='404 {"error": "model not found"}',
                ),
                {
                    "code": "fix_engine_failed",
                    "message": "the improvement engine hit an error it could not recover from",
                },
            ),
        ],
        ids=["the-users", "ours"],
    )
    def test_a_failure_says_what_to_fix_only_when_the_user_can(
        self, source_run, failure, expected
    ):
        fix_run = _fix_run(source_run, token_hash=hash_secret("t"))

        closed = engine.apply_report(
            fix_run,
            EngineReport(
                stage="done", ending=Ending.FAILED, summary="raw", failure=failure
            ),
        )

        assert closed.failure == expected

    def test_an_ending_rejects_the_candidates_no_report_names_any_more(
        self, source_run
    ):
        fix_run = _fix_run(source_run, token_hash=hash_secret("t"))
        # Attempt 1 belongs to a launch that died; attempt 101 to the one now ending.
        lost, named = _candidate(fix_run, attempt=1), _candidate(fix_run, attempt=101)

        engine.apply_report(
            fix_run,
            EngineReport(
                stage="done",
                ending=Ending.NO_IMPROVEMENT,
                candidates=[
                    CandidateUpdate(candidate_id=named.id, outcome=Outcome.FINALIST)
                ],
            ),
        )

        lost.refresh_from_db()
        named.refresh_from_db()
        assert (lost.outcome, named.outcome) == (Outcome.REJECTED, Outcome.FINALIST)


class TestClose:
    def test_closing_cancels_open_check_runs_and_sets_aside_unproven_candidates(
        self, source_run, temporal
    ):
        fix_run = _fix_run(source_run)
        candidates = {
            outcome: _candidate(fix_run, attempt)
            for attempt, outcome in enumerate(Outcome, 1)
        }
        open_run, finished_run = _check(fix_run), _check(fix_run, label="done")
        HostedHarnessJob.no_workspace_objects.filter(id=finished_run.id).update(
            state="completed"
        )

        engine.apply_report(
            fix_run,
            EngineReport(
                stage="done",
                ending=Ending.READY,
                candidates=[
                    CandidateUpdate(candidate_id=str(candidates[one].id), outcome=one)
                    for one in (Outcome.REJECTED, Outcome.FINALIST)
                ],
                winner_candidate_id=str(candidates[Outcome.WINNER].id),
            ),
        )

        open_run.refresh_from_db()
        finished_run.refresh_from_db()
        assert (open_run.state, open_run.cancel_reason) == (
            "cleaning_up",
            "fix_ended",
        )
        assert finished_run.state == "completed"
        temporal.cancel_run.assert_called_once_with(str(open_run.id))
        assert {
            outcome: (
                AgentFixCandidate.no_workspace_objects.get(id=one.id).outcome,
                _runtime(one.agent_version_id).preflight_status,
            )
            for outcome, one in candidates.items()
        } == {
            # Named by no report at the ending: it lost.
            Outcome.PENDING: (Outcome.REJECTED, "discarded"),
            Outcome.REJECTED: (Outcome.REJECTED, "discarded"),
            Outcome.FINALIST: (Outcome.FINALIST, "passed"),
            Outcome.WINNER: (Outcome.WINNER, "passed"),
        }

    def test_closing_twice_keeps_the_first_ending(self, source_run, temporal):
        fix_run = _fix_run(source_run)
        _check(fix_run)
        first = close_fix_run(fix_run.id, State.STOPPED)

        again = close_fix_run(
            fix_run.id, State.FAILED, failure={"code": "late", "message": "m"}
        )

        assert (again.state, again.finished_at, again.failure) == (
            State.STOPPED,
            first.finished_at,
            None,
        )
        # The cleanup runs again and is safe to repeat.
        assert temporal.cancel_run.call_count == 2

    def test_a_close_that_failed_halfway_finishes_on_the_next(self, source_run):
        fix_run = _fix_run(source_run)
        open_run = _check(fix_run)
        with patch(
            "simulate.services.agent_fix_runs.request_cancellation",
            side_effect=RuntimeError("database hiccup"),
        ):
            with pytest.raises(RuntimeError):
                close_fix_run(fix_run.id, State.STOPPED)

        close_fix_run(fix_run.id, State.FAILED)

        open_run.refresh_from_db()
        assert open_run.cancel_reason == "fix_ended"
        assert (
            AgentFixRun.no_workspace_objects.get(id=fix_run.id).state == State.STOPPED
        )

    def test_a_cancel_that_fails_does_not_stop_the_close(self, source_run, temporal):
        fix_run = _fix_run(source_run)
        open_run = _check(fix_run)
        temporal.cancel_run.side_effect = RuntimeError("temporal is down")

        closed = close_fix_run(
            fix_run.id,
            State.FAILED,
            failure={"code": "fix_engine_died", "message": "m"},
        )

        open_run.refresh_from_db()
        assert (closed.state, closed.failure["code"]) == (
            State.FAILED,
            "fix_engine_died",
        )
        assert open_run.state == "cleaning_up"


class TestStop:
    def test_a_running_fix_stops_and_its_workflow_is_signalled_on_commit(
        self, source_run, temporal, django_capture_on_commit_callbacks
    ):
        fix_run = _fix_run(source_run)

        with django_capture_on_commit_callbacks(execute=True):
            stopped = request_stop(fix_run)
            temporal.cancel.assert_not_called()

        assert stopped.state == State.STOPPING
        temporal.cancel.assert_called_once_with(str(fix_run.id))

    def test_without_a_workflow_to_signal_the_fix_is_closed_here(
        self, source_run, temporal, django_capture_on_commit_callbacks
    ):
        temporal.cancel.side_effect = RuntimeError("workflow not found")
        fix_run = _fix_run(source_run)

        with django_capture_on_commit_callbacks(execute=True):
            request_stop(fix_run)

        fix_run.refresh_from_db()
        assert fix_run.state == State.STOPPED

    def test_an_ended_fix_is_left_as_it_is(
        self, source_run, temporal, django_capture_on_commit_callbacks
    ):
        fix_run = _fix_run(source_run, state=State.READY)

        with django_capture_on_commit_callbacks(execute=True):
            assert request_stop(fix_run).state == State.READY

        temporal.cancel.assert_not_called()


class TestMakeDefault:
    def test_a_proven_candidate_becomes_the_default_once_the_fix_ended(
        self, environment, source_run, user
    ):
        # An uploaded agent: a GitHub agent changes through its repository instead.
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=source_run.agent_version_id
        ).update(source_kind="archive")
        fix_run = _fix_run(source_run)
        candidate = _candidate(fix_run)
        engine.apply_report(
            fix_run,
            EngineReport(
                stage="done",
                ending=Ending.READY,
                winner_candidate_id=str(candidate.id),
            ),
        )
        candidate.refresh_from_db()

        outcome = make_candidate_default(candidate, user=user)

        environment.refresh_from_db()
        assert outcome == "activated"
        assert environment.agent_version_id == candidate.agent_version_id
        assert make_candidate_default(candidate, user=user) == "already_default"

    @pytest.mark.parametrize(
        ("state", "outcome", "refusal"),
        [
            (State.RUNNING, Outcome.WINNER, ("fix_run_active", 409)),
            (State.STOPPING, Outcome.FINALIST, ("fix_run_active", 409)),
            (State.READY, Outcome.PENDING, ("fix_candidate_not_proven", 422)),
            (State.READY, Outcome.REJECTED, ("fix_candidate_not_proven", 422)),
            (State.READY, Outcome.FINALIST, ("fix_candidate_not_proven", 422)),
        ],
    )
    def test_refuses_an_unproven_candidate_or_a_fix_still_running(
        self, environment, source_run, user, state, outcome, refusal
    ):
        fix_run = _fix_run(source_run, state=state)
        candidate = AgentFixCandidate.no_workspace_objects.create(
            fix_run=fix_run,
            attempt=1,
            agent_version_id=fix_run.base_agent_version_id,
            patch_object_key="k",
            outcome=outcome,
        )
        default = environment.agent_version_id

        assert _refusal(make_candidate_default, candidate, user=user) == refusal
        environment.refresh_from_db()
        assert environment.agent_version_id == default


def test_the_base_source_is_fetched_once_per_fix(source_run):
    fix_run = _fix_run(source_run)
    body = _tree()
    with patch("simulate.services.agent_fix_agents.HostedSourceAcquirer") as acquirer:
        acquirer.return_value.acquire.return_value = (body, "abc123")
        first = engine.source_archive(fix_run)
        again = engine.source_archive(fix_run)

    assert first == again == body
    assert acquirer.return_value.acquire.call_count == 1
