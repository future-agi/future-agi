"""Fix my agent: starting a fix, the engine's calls, closing it, and taking a candidate."""

import io
import re
import subprocess
import tarfile
from contextlib import ExitStack
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.conf import settings as django_settings
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from simulate.models import (
    AgentFixCandidate,
    AgentFixRun,
    AgentVersion,
    CallExecution,
    HarnessAgentVersionRuntime,
    HarnessSuiteVersion,
    HostedHarnessAttempt,
    HostedHarnessExecution,
    HostedHarnessJob,
)
from simulate.models import (
    TestExecution as SimTestExecution,
)
from simulate.models.chat_message import ChatMessageModel
from simulate.models.test_execution import CallTranscript
from simulate.pydantic_schemas.agent_fix import (
    CandidateUpdate,
    CandidateVerdict,
    CheckFailure,
    CheckRunRequest,
    Ending,
    EngineReport,
    EvalGrading,
    EvalReview,
    EvalWarning,
    FailureOwner,
    FixRequest,
    Surface,
)
from simulate.selectors.agent_fix import (
    TRANSCRIPT_CHARS,
    VERDICT_TIERS,
    _record,
    check_failure,
    check_run_status,
    committed_usd,
    engine_brief,
    eval_review,
    is_retryable,
    launch_options,
    limited_validation,
    ran_at,
    verdict_tiers,
)
from simulate.services import agent_fix_engine as engine
from simulate.services import agent_fix_github
from simulate.services.agent_fix_runs import (
    HELD_BACK_MIN_CASES,
    close_fix_run,
    launch_fix_run,
    make_candidate_default,
    request_stop,
    require_shippable,
    retry_fix_run,
    split_held_back,
)
from simulate.services.alk_simulate_ingestion import _store_alk_chat_messages
from simulate.services.harness_run_evals import queue_evals_for_finished_calls
from simulate.services.harness_versioning.versions import record_authoring
from simulate.services.hosted_harness import (
    HostedHarnessError,
    canonical_digest,
    create_selected_harness_run,
    hash_secret,
    register_attempt,
)
from simulate.services.hosted_harness_gateway import _source_object_key
from simulate.services.hosted_harness_ingestion import ingest_result_receipt
from simulate.services.run_traces_v3 import CallUsage
from simulate.tests.test_harness_agent_versions import _environment, _passing
from simulate.tests.test_harness_amend_archive import NAMES, _key, _read
from simulate.tests.test_harness_environment_regrade import _mapped_eval
from simulate.tests.test_harness_versioning_backfill import (
    _agent_version,
)
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_record import _authoring
from simulate.utils.test_execution_utils import (
    accrue_check_run_eval_cost,
    eval_source,
    is_fix_check_run,
)
from tfc.constants.api_calls import FIX_CHECK_RUN_EVAL_SOURCE
from tfc.settings import settings as default_settings
from tracer.services.clickhouse.client import CHError

pytestmark = pytest.mark.django_db

KEYS = [_key(name) for name in NAMES[:2]]
BASE_AGENT = {"agent.py": b"PROMPT = ''\n"}
AGENT = {"agent.py": b"PROMPT = 'refund first'\n"}
# AGENT's change on BASE_AGENT, as the platform makes it.
PATCH = (
    b"diff --git a/agent.py b/agent.py\nindex fe9af93..3ee06e1 100644\n"
    b"--- a/agent.py\n+++ b/agent.py\n"
    b"@@ -1 +1 @@\n-PROMPT = ''\n+PROMPT = 'refund first'\n"
)
State = AgentFixRun.State
Outcome = AgentFixCandidate.Outcome


@pytest.fixture(autouse=True)
def any_suite_may_launch(settings):
    """These tests run on a two-scenario suite; the floor has its own."""
    settings.AGENT_FIX_MIN_FAILING_CASES = 1


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


def _candidate(fix_run, attempt=1, *, tree=None, base=BASE_AGENT):
    """A candidate added the engine's way on ``base``, with a preflight that passes."""
    with ExitStack() as stack:
        for one in _passing():
            stack.enter_context(one)
        stack.enter_context(
            patch.object(engine, "source_archive", return_value=_tree(base))
        )
        return engine.add_candidate(
            fix_run, attempt=attempt, summary=f"attempt {attempt}", tree=tree or _tree()
        )[0]


def _applied(directory, files, patch_body):
    """``files`` with ``patch_body`` applied by ``git apply``."""
    directory.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=directory, check=True)
    for path, data in files.items():
        (directory / path).parent.mkdir(parents=True, exist_ok=True)
        (directory / path).write_bytes(data)
    subprocess.run(
        ["git", "apply", "--binary", "-"],
        cwd=directory,
        input=patch_body,
        check=True,
        capture_output=True,
    )
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(directory).parts
    }


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


def _degraded(run, *, to):
    """Finish a check run that really ran at parallelism ``to``."""
    HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(state="completed")
    HostedHarnessAttempt.no_workspace_objects.create(
        job=run,
        attempt_number=1,
        token_hash=hash_secret("a"),
        fence_hash=hash_secret("b"),
        expires_at=timezone.now(),
        effective_parallelism=to,
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


def test_the_floor_leaves_enough_held_back_to_verify_a_fix():
    assert default_settings.AGENT_FIX_MIN_FAILING_CASES == HELD_BACK_MIN_CASES


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

    # Four failing cases, each with two variations that failed too: twelve cases.
    PARENTS = {f"s{i}-v{n}": f"s{i}" for i in range(4) for n in (1, 2)}

    def test_a_case_and_its_variations_are_held_back_together(self):
        failing = {key: ["refunded"] for key in [f"s{i}" for i in range(4)]}
        failing |= {key: ["refunded"] for key in self.PARENTS}

        for seed in ("a", "b", "c", "d", "e", "f"):
            held = split_held_back(seed, failing, self.PARENTS)
            cases = {self.PARENTS.get(key, key) for key in held}
            # A third is four: one case and its two variations fit, a second would not.
            assert len(cases) == 1
            assert sorted(held) == sorted(
                key for key in failing if self.PARENTS.get(key, key) in cases
            )

    def test_a_case_whose_family_alone_fails_a_sub_goal_stays_to_search_on(self):
        failing = {key: ["refunded"] for key in [f"s{i}" for i in range(1, 4)]}
        failing |= {
            key: ["apologised"] if parent == "s0" else ["refunded"]
            for key, parent in self.PARENTS.items()
        } | {"s0": ["apologised"]}

        for seed in ("a", "b", "c", "d", "e", "f"):
            held = split_held_back(seed, failing, self.PARENTS)
            assert held
            assert "s0" not in {self.PARENTS.get(key, key) for key in held}


def test_fewer_cases_than_the_verified_minimum_can_never_hold_back_four():
    from itertools import product

    from simulate.selectors.agent_fix import verified_cases_min

    def most_held_cases(count):
        """The most distinct cases any split holds back, over every way the count's
        cases can keep their variations, at the default floor of twelve."""
        best = 0
        for kept in product(range(4), repeat=count):
            failing, parents = {}, {}
            for case, variations in enumerate(kept):
                failing[f"s{case}"] = ["refunded"]
                for number in range(variations):
                    failing[f"s{case}-v{number}"] = ["refunded"]
                    parents[f"s{case}-v{number}"] = f"s{case}"
            if len(failing) < 12:
                continue
            for seed in ("a", "b", "c", "d"):
                held = split_held_back(seed, failing, parents)
                best = max(best, len({parents.get(key, key) for key in held}))
        return best

    assert verified_cases_min() == 6
    assert most_held_cases(5) < 4
    assert most_held_cases(6) == 4


@pytest.mark.parametrize(
    ("held_back", "parents", "limited"),
    [
        # Four held-back cases from two cases of the run: not enough to verify.
        (["a", "a-v1", "a-v2", "b"], {"a-v1": "a", "a-v2": "a"}, True),
        (["a", "b", "c", "d-v1"], {"d-v1": "d"}, False),
    ],
)
def test_verified_needs_held_back_cases_from_four_different_cases(
    source_run, held_back, parents, limited
):
    fix_run = _fix_run(source_run)
    fix_run.request = {
        **fix_run.request,
        "held_back_scenario_keys": held_back,
        "variation_parents": parents,
    }

    assert limited_validation(fix_run) is limited


def _open_suite_draft(environment):
    """Unsaved scenario changes on the environment's default suite."""
    return HarnessSuiteVersion.no_workspace_objects.create(
        job=environment,
        organization_id=environment.organization_id,
        workspace_id=environment.workspace_id,
        status="draft",
        origin="manual",
        hash="sha256:" + "d" * 64,
        archive_object_key="drafts/suite.tar.gz",
        archive_sha256="0" * 64,
        archive_size=1,
        parent=environment.suite_version,
    )


class TestLaunch:
    def test_too_few_failing_cases_are_refused_and_passing_ones_do_not_count(
        self, source_run, user, settings
    ):
        # One failing case and its three variations cannot reach five.
        settings.AGENT_FIX_MIN_FAILING_CASES = 5

        with pytest.raises(HostedHarnessError) as refused:
            _launch(source_run, user, targets=KEYS)

        assert refused.value.code == "fix_targets_too_few"
        assert refused.value.status_code == 422
        assert not AgentFixRun.no_workspace_objects.exists()

    def test_a_run_under_the_floor_writes_variations_first_instead_of_refusing(
        self, source_run, user, settings
    ):
        # One failing case and its three variations can reach four.
        settings.AGENT_FIX_MIN_FAILING_CASES = 4

        fix_run = _launch(source_run, user)

        request = FixRequest.model_validate(fix_run.request)
        assert (
            request.variations_pending,
            request.target_scenario_keys,
            request.held_back_scenario_keys,
            fix_run.suite_version_id,
        ) == (True, [KEYS[0]], [], None)

    def test_an_open_suite_draft_refuses_a_fix_that_writes_variations(
        self, environment, source_run, user, settings
    ):
        settings.AGENT_FIX_MIN_FAILING_CASES = 4
        _open_suite_draft(environment)

        refused = _refusal(_launch, source_run, user)
        options = launch_options(source_run)

        assert refused == ("fix_suite_draft_open", 409)
        assert not AgentFixRun.no_workspace_objects.exists()
        # The fix itself could start: only the variations it writes first are blocked.
        assert options.blocked is None
        assert options.variations_blocked.code == "fix_suite_draft_open"
        # A fix with enough failing cases writes no variations, so the draft is no matter.
        settings.AGENT_FIX_MIN_FAILING_CASES = 1
        fix_run = _launch(source_run, user)
        assert FixRequest.model_validate(fix_run.request).variations_pending is False

    def test_variations_that_would_not_fit_are_refused(
        self, source_run, user, settings
    ):
        settings.AGENT_FIX_MIN_FAILING_CASES = 4

        # The suite's two scenarios and three variations.
        with patch("simulate.services.agent_fix_runs.CHAT_CHECKPOINT_MAX_SCENARIOS", 4):
            too_many = _refusal(_launch, source_run, user)
        # The failing case and its three variations, once each.
        settings.HARNESS_MAX_EXECUTIONS_PER_RUN = 3
        too_large = _refusal(_launch, source_run, user)

        assert (too_many, too_large) == (
            ("scenario_limit_exceeded", 422),
            ("run_too_large", 422),
        )
        assert not AgentFixRun.no_workspace_objects.exists()

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

    def test_a_retry_keeps_the_baseline_it_paid_for_when_the_setting_changes(
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

        assert _check(retried, label="baseline").id == baseline.id

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

    def test_a_retry_reads_a_check_the_failed_fix_asked_for_again(
        self, source_run, user, temporal, django_capture_on_commit_callbacks
    ):
        failed = _fix_run(source_run, token_hash=hash_secret("t"))
        lost = _check(failed, label="baseline-r1")
        again = _check(failed, label="baseline-r1-again")
        HostedHarnessJob.no_workspace_objects.filter(id=lost.id).update(state="failed")
        HostedHarnessJob.no_workspace_objects.filter(id=again.id).update(
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

        assert _check(retried, label="baseline-r1").id == again.id
        assert not HostedHarnessJob.no_workspace_objects.filter(
            fix_run=retried
        ).exists()


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
        assert engine.add_candidate(fix_run, attempt=1, summary="", tree=b"") == (
            candidate,
            "passed",
        )
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

        with pytest.raises(HostedHarnessError) as caught:
            _candidate(fix_run, tree=_tree(changed), base=base)

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

    def test_the_patch_is_the_tree_s_change_on_the_base(
        self, source_run, storage, tmp_path
    ):
        fix_run = _fix_run(source_run, locked_paths=["prompts/*"])
        kept = {"prompts/system.md": b"be kind\n"}
        base = {
            **BASE_AGENT,
            **kept,
            "old.py": b"x = 1\n",
            "logo.png": b"\x89PNG\x00\x01",
        }
        tree = {**AGENT, **kept, "new.py": b"y = 2\n", "logo.png": b"\x89PNG\x00\x02"}

        candidate = _candidate(fix_run, tree=_tree(tree), base=base)

        patch_body = storage.objects[candidate.patch_object_key]
        assert _applied(tmp_path / "applied", base, patch_body) == tree
        assert b"prompts/system.md" not in patch_body

    def test_refuses_a_tree_over_the_unpacked_limit(self, source_run, settings):
        settings.AGENT_FIX_MAX_TREE_BYTES = 10

        assert _refusal(
            _candidate, _fix_run(source_run), tree=_tree({"big.bin": b"0" * 11})
        ) == ("fix_tree_too_large", 413)

    def test_refuses_a_patch_over_the_limit(self, source_run):
        with patch.object(engine, "MAX_PATCH_BYTES", len(PATCH) - 1):
            assert _refusal(_candidate, _fix_run(source_run)) == (
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

    def test_a_check_run_is_held_at_its_worst_case_until_it_is_sealed_and_graded(
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
        # Sealed, but its run still scoring: its evals may bill yet.
        assert committed_usd(fix_run) == Decimal("2.25")
        SimTestExecution.objects.filter(id=first.test_execution_id).update(
            status=SimTestExecution.ExecutionStatus.COMPLETED
        )
        # The settled run at what it billed; the other still at 2 x $0.50.
        assert committed_usd(fix_run) == Decimal("1.34")
        # A winner's grading opens it again: held until its evals are done.
        SimTestExecution.objects.filter(id=first.test_execution_id).update(
            status=SimTestExecution.ExecutionStatus.EVALUATING
        )
        assert committed_usd(fix_run) == Decimal("2.25")

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


class TestRanAt:
    """Arms compare only at one parallelism, so a false mismatch rejects a working fix."""

    def _asked_for(self, run, *, parallelism=4, admitted=None):
        payload = dict(run.payload)
        payload["runtime"] = {**payload.get("runtime", {}), "parallelism": parallelism}
        if admitted is not None:
            payload["metadata"] = {
                **payload.get("metadata", {}),
                "parallelism_clamped": {
                    "requested": parallelism,
                    "admitted": admitted,
                },
            }
        HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(payload=payload)
        run.refresh_from_db()
        return run

    def test_a_run_with_fewer_trials_than_worlds_is_not_a_lower_parallelism(
        self, source_run
    ):
        run = self._asked_for(_check(_fix_run(source_run)), admitted=1)

        assert ran_at(run) == 4

    def test_a_clamp_that_cost_the_run_worlds_still_shows(self, source_run):
        check = _check(_fix_run(source_run), keys=KEYS, trials=3)

        assert ran_at(self._asked_for(check, admitted=2)) == 2

    def test_a_degrade_still_shows(self, source_run):
        run = self._asked_for(_check(_fix_run(source_run), keys=KEYS, trials=3))
        _degraded(run, to=2)

        assert ran_at(run) == 2


@pytest.mark.parametrize(
    "model",
    [
        "anthropic/claude-opus-5.5",  # Plans.
        "deepseek/deepseek-v4.1-flash",  # Codes.
        "openai/gpt-6.1-sol",  # Reviews.
        "openai/gpt-6-luna",  # Backs up the planner and the coder.
        "anthropic/claude-sonnet-5.5",  # Backs up the reviewer.
    ],
)
def test_the_engine_may_call_every_model_in_its_profile(model):
    """The proxy refuses any model that is not listed, so a fix would die on its first call
    to one, or on the very call that needs a backup."""
    assert model in django_settings.AGENT_FIX_ENGINE_MODELS


def test_a_check_run_s_evals_have_a_source_of_their_own(source_run):
    check = _check(_fix_run(source_run))

    assert eval_source(check.test_execution_id) == FIX_CHECK_RUN_EVAL_SOURCE
    assert eval_source(source_run.test_execution_id) == "simulate"
    assert eval_source(source_run.test_execution_id, "other") == "other"


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

    def test_a_candidate_follows_a_baseline_that_ran_below_the_setting(
        self, source_run, settings
    ):
        settings.AGENT_FIX_CHECK_RUN_PARALLELISM = 4
        fix_run = _fix_run(source_run, token_hash=hash_secret("t"))
        baseline = _check(fix_run, label="baseline", keys=KEYS, trials=3)
        _degraded(baseline, to=1)
        candidate = _candidate(fix_run)

        run = _check(fix_run, label="c1", version_id=candidate.agent_version_id)

        assert baseline.payload["runtime"]["parallelism"] == 4
        assert run.payload["runtime"]["parallelism"] == 1

    def test_a_retry_reuses_a_baseline_that_ran_below_the_setting(
        self, source_run, user, temporal, django_capture_on_commit_callbacks, settings
    ):
        settings.AGENT_FIX_CHECK_RUN_PARALLELISM = 4
        failed = _fix_run(source_run, token_hash=hash_secret("t"))
        baseline = _check(failed, label="baseline")
        _degraded(baseline, to=1)
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
        run = _check(
            _fix_run(source_run, token_hash=hash_secret("t")), keys=KEYS, trials=3
        )
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

    @pytest.mark.parametrize(
        ("cause", "owner"),
        [
            (
                "chat_target_failed: HTTPCallFailure: The read operation timed out",
                FailureOwner.OURS,
            ),
            ("chat_target_failed: ReadTimeout: ", FailureOwner.OURS),
            ("chat_target_failed: ReadTimeout: timed out", FailureOwner.OURS),
            ("chat_target_failed: ConnectTimeout: timed out", FailureOwner.USER),
            (
                "depends_on/depends_on_timeout (agent): agent: started_check did not "
                "pass within 180.0s; last output",
                FailureOwner.OURS,
            ),
            (
                "chat_target_failed: HTTPCallFailure: Completed tool evidence "
                "collection failed: invalid or duplicate tool event identity",
                FailureOwner.USER,
            ),
            (
                "chat_target_failed: HTTPCallFailure: Error code: 400 - Function call "
                "is missing a thought_signature in functionCall parts.",
                FailureOwner.USER,
            ),
            (
                "chat_target_failed: HTTPCallFailure: [Errno 111] Connection refused",
                FailureOwner.USER,
            ),
            (
                "chat_target_failed: HTTPCallFailure: [Errno 110] Connection timed out",
                FailureOwner.USER,
            ),
            (
                "BundleAuthorError: target_runtime_configuration_missing: "
                "environment:LIVEKIT_URL",
                FailureOwner.USER,
            ),
        ],
        ids=[
            "chat-timed-out",
            "chat-timeout-error",
            "agent-slow-to-reply",
            "agent-never-accepted",
            "agent-slow-to-start",
            "duplicate-tool-ids",
            "agent-model-refused",
            "agent-gone",
            "agent-never-accepted-wrapped",
            "setup-missing",
        ],
    )
    def test_a_certification_that_ran_out_of_time_is_ours(self, cause, owner):
        failure = {
            "domain": "environment",
            "code": "authoring_runtime_validation_failed",
            "message": "Generated environment failed real-runtime validation after "
            f"bounded repair: {cause}",
        }

        assert check_failure(failure).owner == owner
        # The same words on another of the environment's failures stay the user's.
        assert (
            check_failure({**failure, "code": "provider_credentials_invalid"}).owner
            == FailureOwner.USER
        )

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
        # A finished run, so the transcripts, its fix's request (whether its agent is
        # traced) and its last attempt are read too.
        HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(
            state="completed"
        )
        run.refresh_from_db()

        # One more: what the fix has committed, which the engine's budget follows.
        with django_assert_num_queries(5):
            status = check_run_status(run)

        assert len(status.records) == status.total == 10


# The engine's reading of a transcript line (rejection.mjs): "<role>: <text>"; a line
# without a role continues the message before it.
ENGINE_ROLE_LINE = re.compile(
    r"^(user|assistant|system|tool_calls|tool_call_result|unknown): ?(.*)$"
)


def _engine_messages(transcript):
    messages = []
    for text in transcript.split("\n"):
        if match := ENGINE_ROLE_LINE.match(text):
            messages.append([match[1], match[2]])
        else:
            assert messages, f"the first line has no role: {text!r}"
            messages[-1][1] += f"\n{text}"
    return [tuple(one) for one in messages]


def _chat(call, *rows):
    """Store a chat trial's rows as hosted ingestion does, one at a time: (role, items) or
    (role, items, tool_calls)."""
    run_test = call.test_execution.run_test
    return [
        ChatMessageModel.no_workspace_objects.create(
            call_execution=call,
            role=role,
            content=items,
            messages=[str(item.get("content") or "") for item in items],
            tool_calls=calls[0] if calls else [],
            session_id=f"alk-chat-{call.id}",
            organization=run_test.organization,
            workspace=run_test.workspace,
        )
        for role, items, *calls in rows
    ]


def _finished_trials(source_run, trials=1, *, traced=False):
    """A finished check run's calls, of a fix whose source run exported the agent's model
    calls when ``traced``."""
    fix_run = _fix_run(source_run)
    if traced:
        fix_run.request = {
            **fix_run.request,
            "observes_models": True,
            "meters_agent_cost": True,
        }
        fix_run.save(update_fields=["request"])
    run = _check(fix_run, trials=trials)
    executions = sorted(_executions(run), key=lambda one: one.trial_index)
    for execution in executions:
        _receipt(execution.call_execution, KEYS[0], {"refunded": True})
    HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(state="completed")
    run.refresh_from_db()
    return run, [execution.call_execution for execution in executions]


LOOKUP = {
    "id": "call-1",
    "type": "function",
    "function": {"name": "lookup", "arguments": '{"order": 7}'},
}


class TestChatTranscripts:
    def test_a_chat_trial_reads_as_role_lines_in_order(self, source_run):
        run, [call] = _finished_trials(source_run)
        _chat(
            call,
            ("user", [{"role": "user", "content": "hi"}]),
            ("assistant", [{"role": "assistant", "content": "hello\nhow can I help"}]),
        )
        # One exchange's rows share a time; its agent row was stored first here.
        answer, ask = _chat(
            call,
            (
                "assistant",
                [
                    {
                        "role": "assistant",
                        "content": "checking",
                        "tool_calls": [LOOKUP],
                    },
                    {
                        "role": "assistant",
                        "kind": "tool_call_result",
                        "content": "paid",
                    },
                    {"role": "assistant", "content": "refunded"},
                ],
            ),
            ("user", [{"role": "user", "content": "refund order 7"}]),
        )
        ChatMessageModel.no_workspace_objects.filter(id__in=[answer.id, ask.id]).update(
            created_at=timezone.now()
        )

        [record] = check_run_status(run).records

        assert record.transcript == (
            "user: hi\n"
            "assistant: hello\n"
            "how can I help\n"
            "user: refund order 7\n"
            "assistant: checking\n"
            'tool_calls: [{"name":"lookup","arguments":{"order":7}}]\n'
            "tool_call_result: paid\n"
            "assistant: refunded"
        )

    def test_a_tool_row_reads_as_a_tool_call_result(self, source_run):
        run, [call] = _finished_trials(source_run)
        _chat(
            call,
            ("assistant", [{"content": "let me look"}], [LOOKUP]),
            ("tool", [{"content": "order 7: paid"}]),
            ("assistant", [{"kind": "tool_calls", "content": 'refund({"order": 7})'}]),
        )

        [record] = check_run_status(run).records

        assert record.transcript == (
            "assistant: let me look\n"
            'tool_calls: [{"name":"lookup","arguments":{"order":7}}]\n'
            "tool_call_result: order 7: paid\n"
            'tool_calls: refund({"order": 7})'
        )

    def test_a_voice_trial_still_reads_its_call_transcript(self, source_run):
        run, [voice, chat] = _finished_trials(source_run, trials=2)
        CallTranscript.objects.create(
            call_execution=voice, speaker_role="user", content="spoken"
        )
        for call in (voice, chat):
            _chat(call, ("user", [{"content": "typed"}]))

        by_trial = {one.trial: one.transcript for one in check_run_status(run).records}

        assert sorted(by_trial.values()) == ["user: spoken", "user: typed"]
        voice_trial = HostedHarnessExecution.no_workspace_objects.get(
            call_execution=voice
        ).trial_index
        assert by_trial[voice_trial] == "user: spoken"

    def test_a_chat_transcript_keeps_the_cap(self, source_run):
        run, [call] = _finished_trials(source_run)
        _chat(
            call,
            ("user", [{"content": "hi"}]),
            ("assistant", [{"content": "a" * TRANSCRIPT_CHARS}]),
        )

        [record] = check_run_status(run).records

        assert record.transcript.startswith("user: hi\nassistant: aaa")
        assert len(record.transcript) == TRANSCRIPT_CHARS

    def test_every_line_reads_with_the_engines_parser(self, source_run):
        run, [call] = _finished_trials(source_run)
        _chat(
            call,
            ("user", [{"content": "hi"}]),
            (
                "assistant",
                [
                    {"content": "two\nlines", "tool_calls": [LOOKUP]},
                    {"kind": "tool_call_result", "content": "paid\nin full"},
                ],
            ),
            ("tool", [{"content": "done"}]),
        )

        [record] = check_run_status(run).records

        assert _engine_messages(record.transcript) == [
            ("user", "hi"),
            ("assistant", "two\nlines"),
            ("tool_calls", '[{"name":"lookup","arguments":{"order":7}}]'),
            ("tool_call_result", "paid\nin full"),
            ("tool_call_result", "done"),
        ]

    def test_many_chat_trials_read_their_messages_in_one_query(
        self, source_run, django_assert_num_queries
    ):
        run, calls = _finished_trials(source_run, trials=5)
        for call in calls:
            _chat(
                call, ("user", [{"content": "hi"}]), ("assistant", [{"content": "ok"}])
            )

        # The executions, the last attempt, the call transcripts, the chat rows, the
        # fix's request and what the fix has committed.
        with django_assert_num_queries(6):
            status = check_run_status(run)

        assert {one.transcript for one in status.records} == {"user: hi\nassistant: ok"}


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

    def test_carries_the_source_runs_failed_trials_with_their_transcripts(
        self, source_run
    ):
        _receipts(
            source_run,
            {
                KEYS[0]: {"refunded": False, "greeted": True},
                KEYS[1]: {"refunded": True},
            },
        )
        failed = _executions(source_run).get(source_scenario__scenario_key=KEYS[0])
        for at, role, text in ((2, "assistant", "no refund"), (1, "user", "refund?")):
            CallTranscript.objects.create(
                call_execution=failed.call_execution,
                speaker_role=role,
                content=text,
                start_time_ms=at,
            )

        [record] = engine_brief(_fix_run(source_run)).source_failures

        assert (record.scenario_key, record.trial) == (KEYS[0], failed.trial_index)
        assert record.sub_goals == {"refunded": False, "greeted": True}
        assert record.reasons["refunded"] == "refunded?"
        assert record.transcript == "user: refund?\nassistant: no refund"

    def test_never_carries_a_held_back_target(self, source_run):
        _receipts(
            source_run, {KEYS[0]: {"refunded": False}, KEYS[1]: {"greeted": False}}
        )
        fix_run = _fix_run(source_run)
        AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
            request={
                **fix_run.request,
                "target_scenario_keys": KEYS,
                "held_back_scenario_keys": [KEYS[0]],
            }
        )
        fix_run.refresh_from_db()

        brief = engine_brief(fix_run)

        assert [one.scenario_key for one in brief.source_failures] == [KEYS[1]]

    def test_carries_a_scenarios_first_judged_failed_trial_only(self, environment):
        # The engine reads one judged trial per scenario, a failing one first.
        run = _run(environment, keys=[KEYS[0]], key="three-trials", trials=3)
        passed, unjudged, failed = sorted(
            _executions(run), key=lambda one: one.trial_index
        )
        for execution, held in (
            (passed, {"refunded": True}),
            (unjudged, {"refunded": False, "greeted": None}),
            (failed, {"refunded": False}),
        ):
            _receipt(execution.call_execution, KEYS[0], held)

        brief = engine_brief(_fix_run(run))

        assert [(one.trial, one.status) for one in brief.source_failures] == [
            (failed.trial_index, "ok")
        ]

    def test_a_fix_with_no_failed_searched_trial_carries_none(self, source_run):
        fix_run = _fix_run(source_run)
        AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
            request={**fix_run.request, "target_scenario_keys": [KEYS[1]]}
        )
        fix_run.refresh_from_db()

        assert engine_brief(fix_run).source_failures == []


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
                    "message": "the engine hit an error it could not recover from",
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

    def _winner(self, source_run):
        """A finished fix's proven candidate, made on the source run's version."""
        fix_run = _fix_run(source_run)
        candidate = _candidate(fix_run)
        AgentFixCandidate.no_workspace_objects.filter(id=candidate.id).update(
            outcome=Outcome.WINNER
        )
        close_fix_run(fix_run.id, State.READY)
        candidate.refresh_from_db()
        return candidate

    def test_a_busy_environment_refuses_the_ship_and_leaves_no_move_for_later(
        self, environment, source_run, user
    ):
        from simulate.services.harness_versioning.rebuild import (
            retry_deferred_activations,
        )

        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=source_run.agent_version_id
        ).update(source_kind="archive")
        first = self._winner(source_run)
        second = self._winner(source_run)
        # An update is adding scenarios: a move now would wait for it.
        payload = dict(environment.payload)
        payload["metadata"] = {**payload["metadata"], "scenario_extend": {"n": 1}}
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )

        with pytest.raises(HostedHarnessError) as refused:
            make_candidate_default(second, user=user)

        assert (refused.value.code, refused.value.status_code) == (
            "fix_environment_busy",
            409,
        )
        # Meanwhile another fix ships; the update ends and deferred moves are retried.
        payload["metadata"].pop("scenario_extend")
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )
        assert make_candidate_default(first, user=user) == "activated"
        retry_deferred_activations(second.agent_version_id)
        environment.refresh_from_db()
        assert environment.agent_version_id == first.agent_version_id

    def test_a_fix_is_not_shipped_over_a_default_that_moved_since_its_base(
        self, environment, source_run, user
    ):
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=source_run.agent_version_id
        ).update(source_kind="archive")
        # Two fixes from the same run: shipping the second would undo the first.
        first = self._winner(source_run)
        second = self._winner(source_run)

        assert make_candidate_default(first, user=user) == "activated"
        with pytest.raises(HostedHarnessError) as refused:
            make_candidate_default(second, user=user)

        assert (refused.value.code, refused.value.status_code) == (
            "fix_base_moved",
            409,
        )
        base = source_run.agent_version.version_number
        now = first.agent_version.version_number
        assert f"made on v{base}" in refused.value.message
        assert f"now v{now}" in refused.value.message
        environment.refresh_from_db()
        assert environment.agent_version_id == first.agent_version_id

    def test_a_fix_is_refused_not_failed_when_the_environment_lost_its_default(
        self, environment, source_run, user
    ):
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=source_run.agent_version_id
        ).update(source_kind="archive")
        winner = self._winner(source_run)
        # The default is SET_NULL: deleting the version leaves the environment none.
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=None
        )

        with pytest.raises(HostedHarnessError) as refused:
            make_candidate_default(winner, user=user)

        assert (refused.value.code, refused.value.status_code) == (
            "fix_base_moved",
            409,
        )
        assert "no default version any more" in refused.value.message

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


# The spans a traced agent exported, as the run reader returns them for one run.
TRACED = "simulate.selectors.agent_fix.run_usage"


def _usage(by_call):
    """``run_usage`` for runs whose calls exported ``by_call``; None for an untraced agent."""
    return lambda execution: by_call


class TestMeasuredConversations:
    def test_a_finished_run_s_records_carry_each_call_s_models_spans_and_cost(
        self, source_run
    ):
        run, [priced, unpriced, silent] = _finished_trials(
            source_run, trials=3, traced=True
        )
        usage = {
            str(priced.id): CallUsage(models=["m1", "m2"], llm_spans=3, cost_usd=0.03),
            str(unpriced.id): CallUsage(
                models=["m1"], llm_spans=1, cost_usd=None, unpriced_models=["m1"]
            ),
            str(silent.id): CallUsage(
                models=[], llm_spans=0, cost_usd=None, partial=True
            ),
        }

        with patch(TRACED, _usage(usage)):
            status = check_run_status(run)

        assert [
            (
                one.models,
                one.spans_seen,
                one.agent_cost_usd,
                one.unpriced_models,
                one.instrumentation_partial,
            )
            for one in sorted(status.records, key=lambda one: one.trial)
        ] == [
            (["m1", "m2"], 3, 0.03, [], False),
            (["m1"], 1, None, ["m1"], False),
            ([], 0, None, [], True),
        ]
        assert status.usage_unreadable is False

    def test_a_run_still_going_reads_no_span(self, source_run):
        run, [call] = _finished_trials(source_run, traced=True)
        HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(state="running")
        run.refresh_from_db()

        with patch(TRACED) as read:
            [record] = check_run_status(run).records

        read.assert_not_called()
        assert (record.spans_seen, record.agent_cost_usd) == (0, None)

    def test_an_untraced_fix_reads_no_span(self, source_run):
        run, [call] = _finished_trials(source_run)

        with patch(TRACED) as read:
            [record] = check_run_status(run).records

        read.assert_not_called()
        assert (record.models, record.spans_seen) == ([], 0)

    def test_a_span_read_that_fails_says_so_so_the_engine_never_reads_it_untraced(
        self, source_run
    ):
        run, [call] = _finished_trials(source_run, traced=True)

        with patch(TRACED, side_effect=CHError("down")):
            status = check_run_status(run)

        assert status.usage_unreadable is True
        [record] = status.records
        assert (record.models, record.spans_seen, record.agent_cost_usd) == (
            [],
            0,
            None,
        )

    def test_a_chat_s_reply_latency_is_its_median_reply_after_a_user_turn(
        self, source_run
    ):
        run, [call] = _finished_trials(source_run)
        CallExecution.objects.filter(id=call.id).update(
            simulation_call_type=CallExecution.SimulationCallType.TEXT
        )
        call.refresh_from_db()
        # A transcript as the hosted harness reports one, stored by its ingestion:
        # every row gets its own created_at.
        _store_alk_chat_messages(
            call,
            [
                # The agent's greeting answers no user turn.
                {"speaker_role": "assistant", "content": "Hi!", "start_time_ms": 0},
                {
                    "speaker_role": "user",
                    "content": "Book a visit",
                    "start_time_ms": 1200,
                },
                # A tool call before the answer is time the user still waits.
                {
                    "speaker_role": "tool_calls",
                    "content": "find_slot",
                    "start_time_ms": 3050,
                },
                {
                    "speaker_role": "assistant",
                    "content": "Tuesday 10?",
                    "start_time_ms": 3400,
                },
                {"speaker_role": "user", "content": "Yes", "start_time_ms": 6000},
                {
                    "speaker_role": "assistant",
                    "content": "Booked.",
                    "start_time_ms": 8200,
                },
                {"speaker_role": "user", "content": "Thanks", "start_time_ms": 10000},
                {"speaker_role": "assistant", "content": "Bye", "start_time_ms": 10900},
                # A last turn the agent never answered.
                {
                    "speaker_role": "user",
                    "content": "One more thing",
                    "start_time_ms": 15000,
                },
            ],
        )
        stored = ChatMessageModel.no_workspace_objects.filter(call_execution=call)
        assert stored.values("created_at").distinct().count() == stored.count() == 8

        [record] = check_run_status(run).records

        # Replies of 2200 (the text after the tool call, not the call), 2200 and 900 ms.
        assert record.reply_latency_ms == 2200

    def test_a_voice_call_s_reply_latency_is_its_agent_s_average(self, source_run):
        run, [call] = _finished_trials(source_run)
        CallExecution.objects.filter(id=call.id).update(
            simulation_call_type=CallExecution.SimulationCallType.VOICE,
            avg_agent_latency_ms=820,
        )

        [record] = check_run_status(run).records

        assert record.reply_latency_ms == 820


class TestLaunchConstraints:
    def _source_usage(self, source_run, by_call):
        calls = [one.call_execution_id for one in _executions(source_run)]
        return {str(call): usage for call, usage in zip(calls, by_call, strict=False)}

    def test_the_form_allows_the_models_the_run_s_spans_show(self, source_run):
        usage = self._source_usage(
            source_run,
            [
                CallUsage(
                    models=["vertex_ai/gemini-3.8-flash"], llm_spans=2, cost_usd=0.01
                ),
                CallUsage(
                    models=["gpt-5-mini", "vertex_ai/gemini-3.8-flash"],
                    llm_spans=1,
                    cost_usd=0.0,
                ),
            ],
        )
        with patch(TRACED, _usage(usage)):
            defaults = launch_options(source_run).defaults

        assert (
            defaults.allowed_models,
            defaults.agent_cost_metered,
            defaults.reply_latency_margin_pct,
            defaults.cost_margin_pct,
        ) == (["gpt-5-mini", "vertex_ai/gemini-3.8-flash"], True, 25, 25)

    def test_an_untraced_agent_s_form_allows_no_model(self, source_run):
        with patch(TRACED, _usage(None)):
            defaults = launch_options(source_run).defaults

        assert (defaults.allowed_models, defaults.agent_cost_metered) == ([], False)

    def test_a_form_whose_spans_name_no_model_still_says_their_cost_is_measured(
        self, source_run
    ):
        usage = self._source_usage(
            source_run, [CallUsage(models=[], llm_spans=2, cost_usd=0.01)]
        )
        with patch(TRACED, _usage(usage)):
            defaults = launch_options(source_run).defaults

        assert (
            defaults.allowed_models,
            defaults.agent_cost_metered,
            defaults.agent_calls_unmeasured,
        ) == ([], True, False)

    def test_a_form_whose_every_call_went_unmeasured_says_their_cost_is_unknown(
        self, source_run
    ):
        # Every model call went through something the sandbox can't read: spans say
        # so, and none carries a cost.
        partial = CallUsage(models=[], llm_spans=0, cost_usd=None, partial=True)
        every = self._source_usage(source_run, [partial, partial])
        some = self._source_usage(
            source_run, [partial, CallUsage(models=[], llm_spans=1, cost_usd=0.01)]
        )

        def unmeasured(usage):
            with patch(TRACED, _usage(usage)):
                defaults = launch_options(source_run).defaults
            return defaults.agent_cost_metered, defaults.agent_calls_unmeasured

        # One measured call still measures the agent's cost.
        assert (unmeasured(every), unmeasured(some), unmeasured(None)) == (
            (True, True),
            (True, False),
            (False, False),
        )

    def test_a_form_whose_run_s_traces_cannot_be_read_says_so_instead_of_untraced(
        self, source_run
    ):
        with patch(TRACED, side_effect=CHError("down")):
            options = launch_options(source_run)

        assert (options.blocked.code, options.blocked.message) == (
            "fix_traces_unreadable",
            "could not read this run's traces; try again",
        )
        assert options.defaults.allowed_models == []

    def test_a_launch_whose_traces_cannot_be_read_is_refused_to_be_tried_again(
        self, source_run, user
    ):
        with patch(TRACED, side_effect=CHError("down")):
            with pytest.raises(HostedHarnessError) as refused:
                _launch(source_run, user)

        assert (
            refused.value.code,
            refused.value.status_code,
            refused.value.retryable,
        ) == ("fix_traces_unreadable", 503, True)
        assert not AgentFixRun.no_workspace_objects.exists()

    def test_a_traced_agent_s_fix_is_locked_to_its_models_and_measures_its_calls(
        self, source_run, user
    ):
        usage = self._source_usage(
            source_run, [CallUsage(models=["m1"], llm_spans=2, cost_usd=0.01)]
        )
        with patch(TRACED, _usage(usage)):
            fix_run = _launch(source_run, user)

        request = engine_brief(fix_run).request
        assert (
            request.allowed_models,
            request.observes_models,
            request.meters_agent_cost,
            request.reply_latency_margin_pct,
            request.cost_margin_pct,
            request.cost_limit_usd,
        ) == (["m1"], True, True, 25, 25, None)

    def test_an_untraced_agent_s_fix_measures_nothing_it_cannot_see(
        self, source_run, user
    ):
        with patch(TRACED, _usage(None)):
            fix_run = _launch(source_run, user)

        request = FixRequest.model_validate(fix_run.request)
        assert (
            request.allowed_models,
            request.observes_models,
            request.meters_agent_cost,
        ) == ([], False, False)

    def test_spans_that_name_no_model_meter_cost_but_lock_nothing(
        self, source_run, user
    ):
        usage = self._source_usage(
            source_run, [CallUsage(models=[], llm_spans=2, cost_usd=0.01)]
        )
        with patch(TRACED, _usage(usage)):
            fix_run = _launch(source_run, user)

        request = FixRequest.model_validate(fix_run.request)
        assert (request.observes_models, request.meters_agent_cost) == (False, True)

    def test_the_user_s_models_and_limits_are_kept(self, source_run, user):
        usage = self._source_usage(
            source_run, [CallUsage(models=["m1"], llm_spans=2, cost_usd=0.01)]
        )
        with patch(TRACED, _usage(usage)):
            fix_run = launch_fix_run(
                source_run,
                target_scenario_keys=[KEYS[0]],
                surfaces=[Surface.PROMPT],
                locked_paths=[],
                budget_usd=5.0,
                max_minutes=30,
                user=user,
                allowed_models=["m1", "m2", "m1"],
                reply_latency_margin_pct=10,
                cost_margin_pct=50,
                cost_limit_usd=0.02,
            )

        request = FixRequest.model_validate(fix_run.request)
        assert (
            request.allowed_models,
            request.reply_latency_margin_pct,
            request.cost_margin_pct,
            request.cost_limit_usd,
        ) == (["m1", "m2"], 10, 50, 0.02)

    def test_a_traced_agent_s_fix_that_leaves_out_a_model_its_run_called_is_refused(
        self, source_run, user
    ):
        usage = self._source_usage(
            source_run,
            [CallUsage(models=["m1", "vertex_ai/m2"], llm_spans=2, cost_usd=0.01)],
        )

        def launch(allowed):
            return launch_fix_run(
                source_run,
                target_scenario_keys=[KEYS[0]],
                surfaces=[Surface.PROMPT],
                locked_paths=[],
                budget_usd=5.0,
                max_minutes=30,
                user=user,
                allowed_models=allowed,
            )

        with patch(TRACED, _usage(usage)):
            with pytest.raises(HostedHarnessError) as refused:
                launch(["m1"])
            assert not AgentFixRun.no_workspace_objects.exists()
            # A provider prefix does not count, as in the engine's lock.
            fix_run = launch(["m1", "M2"])

        assert (refused.value.code, refused.value.status_code) == (
            "fix_models_exclude_source",
            400,
        )
        assert refused.value.message.endswith(": vertex_ai/m2")
        assert FixRequest.model_validate(fix_run.request).allowed_models == ["m1", "M2"]

    def test_a_traced_agent_s_fix_with_no_model_allowed_is_refused(
        self, source_run, user
    ):
        usage = self._source_usage(
            source_run, [CallUsage(models=["m1"], llm_spans=2, cost_usd=0.01)]
        )
        with patch(TRACED, _usage(usage)):
            refused = _refusal(
                launch_fix_run,
                source_run,
                target_scenario_keys=[KEYS[0]],
                surfaces=[Surface.PROMPT],
                locked_paths=[],
                budget_usd=5.0,
                max_minutes=30,
                user=user,
                allowed_models=[],
            )

        assert refused == ("fix_models_empty", 400)
        assert not AgentFixRun.no_workspace_objects.exists()


def _finished_and_sealed(run):
    """A check run whose calls finished with their own evals done, sealed and settled."""
    for execution in _executions(run):
        call = execution.call_execution
        call.status = CallExecution.CallStatus.COMPLETED
        call.call_metadata = {
            **(call.call_metadata or {}),
            "eval_started": True,
            "eval_completed": True,
        }
        call.save(update_fields=["status", "call_metadata"])
    HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(
        fix_cost_usd=Decimal("0.01")
    )
    SimTestExecution.objects.filter(id=run.test_execution_id).update(
        status=SimTestExecution.ExecutionStatus.COMPLETED
    )


class TestWinnerGrading:
    """A fix's check runs are graded only once it has a winner, and then only the runs its
    warnings compare: the baseline and the winner's final check."""

    def _fix(self, source_run):
        fix_run = _fix_run(source_run)
        candidate = _candidate(fix_run)
        runs = {
            "base": _check(fix_run, label="baseline-r1"),
            "screen": _check(
                fix_run, label="c1-target-r1", version_id=candidate.agent_version_id
            ),
            "final": _check(
                fix_run, label="c1-finalist-r1", version_id=candidate.agent_version_id
            ),
        }
        for run in runs.values():
            _finished_and_sealed(run)
        return fix_run, candidate, runs, _mapped_eval(source_run)

    def _won(self, fix_run, candidate):
        with (
            patch("simulate.services.harness_run_evals._dispatch_one") as dispatched,
            TestCase.captureOnCommitCallbacks(execute=True),
        ):
            engine.apply_report(
                fix_run,
                EngineReport(stage="Select", winner_candidate_id=str(candidate.id)),
            )
        return dispatched

    def test_a_winner_grades_the_baseline_and_its_final_check_and_holds_them(
        self, source_run, settings
    ):
        settings.AGENT_FIX_WORST_CASE_USD_PER_EXECUTION = "0.5"
        settings.AGENT_FIX_WORST_CASE_USD_PER_EVAL = "0.25"
        fix_run, candidate, runs, config = self._fix(source_run)
        assert committed_usd(fix_run) == Decimal("0.03")

        dispatched = self._won(fix_run, candidate)

        graded = {
            str(execution.call_execution_id)
            for key in ("base", "final")
            for execution in _executions(runs[key])
        }
        assert {call.args[0] for call in dispatched.call_args_list} == graded
        assert {tuple(call.args[1]) for call in dispatched.call_args_list} == {
            (str(config.id),)
        }
        # The graded runs are held at their worst case, an eval included, until their
        # evals are done; the screen stays at what it billed.
        assert committed_usd(fix_run) == Decimal("0.01") + 2 * Decimal("0.75")
        assert eval_review(fix_run).grading == EvalGrading.PENDING

    def test_a_winner_is_graded_on_every_billed_eval_not_only_the_first(
        self, source_run
    ):
        # Queueing one config reopens a call's eval side, so grading config by config
        # left every config but the first skipped as "pending" (proof 3, 12 Nov).
        fix_run, candidate, runs, first = self._fix(source_run)
        second = _mapped_eval(
            source_run, name="Accuracy", template_name="accuracy_regrade"
        )

        dispatched = self._won(fix_run, candidate)

        graded = {
            str(execution.call_execution_id)
            for key in ("base", "final")
            for execution in _executions(runs[key])
        }
        # One job per call carrying both configs: two jobs for one call each save
        # the call's whole eval_outputs and drop the other's verdict (slot 6, 12 Nov:
        # 177 of 576 verdicts lost when each config was its own job).
        assert len(dispatched.call_args_list) == len(graded)
        assert {
            call.args[0]: sorted(call.args[1]) for call in dispatched.call_args_list
        } == {call_id: sorted([str(first.id), str(second.id)]) for call_id in graded}

    def test_a_failed_hand_off_unstamps_every_config_of_the_calls_it_did_not_reach(
        self, source_run
    ):
        fix_run, _, _, first = self._fix(source_run)
        second = _mapped_eval(
            source_run, name="Accuracy", template_name="accuracy_regrade"
        )
        run = _check(fix_run, label="baseline-r2", keys=KEYS)
        _finished_and_sealed(run)
        calls = sorted(str(e.call_execution_id) for e in _executions(run))
        assert len(calls) == 2

        def _hand_off(*args, **kwargs):
            if kwargs["args"] == (calls[1],):
                raise RuntimeError("broker unreachable")

        with (
            patch(
                "simulate.services.test_executor._run_simulate_evaluations_task.apply_async",
                side_effect=_hand_off,
            ) as hand_off,
            TestCase.captureOnCommitCallbacks(execute=True),
            transaction.atomic(),
        ):
            queue_evals_for_finished_calls(run.test_execution, [first, second])

        assert [call.kwargs["args"][0] for call in hand_off.call_args_list] == calls[:2]
        assert sorted(
            hand_off.call_args_list[0].kwargs["kwargs"]["eval_config_ids"]
        ) == sorted([str(first.id), str(second.id)])
        reached = CallExecution.objects.get(id=calls[0])
        assert set(reached.call_metadata["eval_queued"]) == {
            str(first.id),
            str(second.id),
        }
        for call_id in calls[1:]:
            call = CallExecution.objects.get(id=call_id)
            assert not set(call.call_metadata.get("eval_queued") or {}) & {
                str(first.id),
                str(second.id),
            }
            assert str(first.id) not in (call.eval_outputs or {})
            assert str(second.id) not in (call.eval_outputs or {})
            assert call.call_metadata["eval_completed"] is True

    def test_a_fix_with_no_winner_pays_for_no_evals(self, source_run):
        fix_run, candidate, _, _ = self._fix(source_run)

        with patch(
            "simulate.services.agent_fix_engine.queue_evals_for_finished_calls"
        ) as queue:
            engine.apply_report(
                fix_run,
                EngineReport(
                    stage="Select",
                    candidates=[
                        CandidateUpdate(
                            candidate_id=str(candidate.id), outcome="rejected"
                        )
                    ],
                ),
            )

        queue.assert_not_called()
        assert eval_review(fix_run).grading == EvalGrading.NONE

    def test_the_winner_ships_only_once_grading_is_no_longer_pending(self, source_run):
        fix_run, candidate, runs, config = self._fix(source_run)
        self._won(fix_run, candidate)
        AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(state=State.READY)
        winner = AgentFixCandidate.no_workspace_objects.get(id=candidate.id)

        assert _refusal(require_shippable, winner, override_reason="") == (
            "fix_grading_pending",
            409,
        )

        # One job was lost: the evals did not check all of it, and it ships anyway.
        for key in ("base", "final"):
            CallExecution.objects.filter(
                id__in=[one.call_execution_id for one in _executions(runs[key])]
            ).update(eval_outputs={str(config.id): {"status": "timed_out"}})
            SimTestExecution.objects.filter(id=runs[key].test_execution_id).update(
                status=SimTestExecution.ExecutionStatus.COMPLETED
            )
        assert eval_review(fix_run).grading == EvalGrading.INCOMPLETE
        require_shippable(
            AgentFixCandidate.no_workspace_objects.get(id=candidate.id),
            override_reason="",
        )


class TestCheckRunEvalCost:
    def test_an_eval_s_bill_is_counted_on_its_check_run_only(self, source_run):
        fix_run = _fix_run(source_run)
        run = _check(fix_run)

        accrue_check_run_eval_cost(run.test_execution_id, 250)
        accrue_check_run_eval_cost(run.test_execution_id, Decimal("12.5"))
        accrue_check_run_eval_cost(source_run.test_execution_id, 999)

        run.refresh_from_db()
        source_run.refresh_from_db()
        assert run.fix_eval_cost_usd == Decimal("2.6250")
        assert source_run.fix_eval_cost_usd == 0

    def test_a_deleted_check_run_stays_its_fix_s_and_its_eval_bill_is_counted(
        self, source_run
    ):
        fix_run = _fix_run(source_run)
        run = _check(fix_run)
        HostedHarnessJob.all_objects.filter(id=run.id).update(
            deleted=True, deleted_at=timezone.now()
        )

        assert is_fix_check_run(run.test_execution_id)
        assert eval_source(run.test_execution_id) == FIX_CHECK_RUN_EVAL_SOURCE
        accrue_check_run_eval_cost(run.test_execution_id, 40)
        assert HostedHarnessJob.all_objects.get(id=run.id).fix_eval_cost_usd == Decimal(
            "0.4000"
        )

    def test_a_graded_run_s_reserve_covers_each_eval_a_winner_s_grading_may_run(
        self, source_run, settings, temporal
    ):
        settings.AGENT_FIX_WORST_CASE_USD_PER_EXECUTION = "1"
        settings.AGENT_FIX_WORST_CASE_USD_PER_EVAL = "0.25"
        fix_run = _fix_run(source_run, budget_usd=1.5, spent_usd=Decimal("0.5"))
        _mapped_eval(source_run)

        # $0.50 spent and $1.25 for the baseline: past the $1.50 limit with its eval in.
        assert _refusal(_check, fix_run, label="baseline-r1") == (
            "fix_budget_exhausted",
            402,
        )
        brief = engine_brief(fix_run)
        assert (
            brief.worst_case_usd_per_execution,
            brief.worst_case_usd_per_graded_execution,
        ) == (1.0, 1.25)

    def test_a_screen_holds_its_simulation_only_and_a_graded_run_its_evals_too(
        self, source_run, settings
    ):
        settings.AGENT_FIX_WORST_CASE_USD_PER_EXECUTION = "0.5"
        settings.AGENT_FIX_WORST_CASE_USD_PER_EVAL = "0.25"
        fix_run = _fix_run(source_run)
        candidate = _candidate(fix_run)
        _mapped_eval(source_run)

        _check(fix_run, label="c1-target-r1", version_id=candidate.agent_version_id)
        assert committed_usd(fix_run) == Decimal("0.5")
        _check(fix_run, label="variations")
        assert committed_usd(fix_run) == Decimal("1.0")
        _check(fix_run, label="baseline-r1")
        _check(fix_run, label="c1-finalist-r1", version_id=candidate.agent_version_id)
        assert committed_usd(fix_run) == Decimal("2.5")

    def test_a_small_limit_still_takes_a_screen_its_evals_never_grade(
        self, source_run, settings, temporal
    ):
        """Before evals counted, a screen reserved only its simulation; it still does."""
        settings.AGENT_FIX_WORST_CASE_USD_PER_EXECUTION = "0.5"
        settings.AGENT_FIX_WORST_CASE_USD_PER_EVAL = "0.25"
        fix_run = _fix_run(source_run, budget_usd=0.6)
        candidate = _candidate(fix_run)
        _mapped_eval(source_run)

        assert _refusal(_check, fix_run, label="baseline-r1") == (
            "fix_budget_exhausted",
            402,
        )
        screen = _check(
            fix_run, label="c1-target-r1", version_id=candidate.agent_version_id
        )
        assert screen.fix_run_id == fix_run.id


class TestPullRequestEvals:
    def _body(self, source_run, review, outcome=Outcome.WINNER):
        from simulate.services import agent_fix_github

        fix_run = _fix_run(source_run)
        candidate = _candidate(fix_run)
        # A pull request comes from a finished fix.
        AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(state=State.READY)
        AgentFixCandidate.no_workspace_objects.filter(id=candidate.id).update(
            outcome=outcome
        )
        candidate.refresh_from_db()
        with patch.object(agent_fix_github, "eval_review", return_value=review):
            return agent_fix_github._description(candidate, b"")[1]

    def test_the_body_names_each_eval_the_winner_does_worse_on(self, source_run):
        warning = EvalWarning(
            name="Politeness",
            pass_rate=0.25,
            base_pass_rate=1.0,
            trials=12,
            base_trials=12,
        )
        body = self._body(
            source_run,
            EvalReview(grading=EvalGrading.DONE, warnings=[warning], compared=1),
        )
        assert "**Platform evals this change does worse on.**" in body
        assert "They do not pick the winner" in body
        assert (
            "- Politeness: 25% passed (12 trials), 100% on the current version (12 trials)"
            in body
        )

    def test_the_body_says_when_no_eval_did_worse_or_grading_did_not_finish(
        self, source_run
    ):
        assert "Platform evals: none did worse than the current version." in self._body(
            source_run, EvalReview(grading=EvalGrading.DONE, compared=1)
        )
        assert (
            "**Platform evals did not finish on this candidate,** so their results are missing."
            " Review it closely before you merge."
            in self._body(
                source_run, EvalReview(grading=EvalGrading.INCOMPLETE, compared=1)
            )
        )

    def test_the_body_says_nothing_about_evals_none_ran_or_graded(self, source_run):
        for review, outcome in (
            (EvalReview(grading=EvalGrading.DONE, compared=0), Outcome.WINNER),
            (EvalReview(grading=EvalGrading.NONE), Outcome.FINALIST),
        ):
            assert "Platform evals" not in self._body(source_run, review, outcome)


def _diff(*files: tuple[str, int, int]) -> bytes:
    """A unified diff changing each (path, added, removed) file."""
    lines = []
    for path, added, removed in files:
        lines += [
            f"diff --git a/{path} b/{path}",
            f"--- a/{path}",
            f"+++ b/{path}",
            f"@@ -1,{removed} +1,{added} @@",
            # A removed line that itself starts with dashes is still a removed line.
            *(f"--- old line {n}" for n in range(removed)),
            *(f"+new line {n}" for n in range(added)),
        ]
    return ("\n".join(lines) + "\n").encode()


_TIERS = [
    {"name": "static", "status": "passed", "detail": "Lint and types pass."},
    {"name": "target", "status": "passed", "detail": "0/8 → 5/8 | 2 repeats"},
    {"name": "held-back", "status": "passed", "detail": "1/4 → 3/4"},
    {"name": "must-pass", "status": "passed", "detail": "All 6 held."},
    {"name": "repeats", "status": "passed", "detail": "3 of 3 repeats agree."},
    {"name": "ceilings", "status": "passed", "detail": "Within the limits."},
]
_CAUSES = [
    "The agent books the date the caller first said, not the one they corrected to.",
    "It never reads the booking back before it confirms.",
]
_PATCH = _diff(
    ("concierge/agents.py", 12, 3), ("prompts/booking.md", 4, 1), ("config.yaml", 1, 1)
)


class TestVerdictTiers:
    """An older engine's cost and latency check, for an agent whose cost is not metered,
    judged the reply time alone: it reads as that reply time and a cost unknown."""

    _LATENCY = {"name": "reply latency", "detail": ""}
    _COST = {"name": "agent cost", "status": "skipped", "detail": ""}

    @pytest.mark.parametrize(
        ("status", "reads", "parts"),
        [
            ("passed", "skipped", [{**_LATENCY, "status": "passed"}, _COST]),
            ("failed", "failed", [{**_LATENCY, "status": "failed"}, _COST]),
            # A skipped one may not have measured the reply time either.
            ("skipped", "skipped", [_COST]),
        ],
    )
    def test_an_unmetered_agents_older_ceilings_read_as_reply_time_and_cost_unknown(
        self, status, reads, parts
    ):
        old = {"name": "ceilings", "status": status, "detail": "latency 3.2s"}
        static = {"name": "static", "status": "passed"}

        tiers = verdict_tiers({"tiers": [static, old]}, {})

        assert tiers == [
            static,
            {
                "name": "ceilings",
                "status": reads,
                "detail": "latency 3.2s",
                "parts": parts,
            },
        ]

    def test_a_metered_agents_check_and_a_newer_engines_parts_stay_as_sent(self):
        old = {"name": "ceilings", "status": "passed", "detail": "cost within"}
        newer = {
            **old,
            "status": "skipped",
            "parts": [
                {"name": "agent cost", "status": "skipped", "detail": "untraced"}
            ],
        }

        assert verdict_tiers({"tiers": [old]}, {"meters_agent_cost": True}) == [old]
        assert verdict_tiers({"tiers": [newer]}, {}) == [newer]
        assert verdict_tiers(None, None) == []


class TestPullRequestDescription:
    """The pull request's title says what the whole change does, and its body lays out
    the verdict, the files, why, what could still go wrong, and where it came from."""

    @pytest.fixture(autouse=True)
    def _frontend(self, settings):
        settings.FRONTEND_BASE_URL = "https://app.example"

    def _described(
        self,
        source_run,
        *,
        held_back=4,
        diagnosis=None,
        verdict=None,
        outcome=Outcome.WINNER,
        override_reason="",
        report=None,
        plan=None,
        evidence=None,
        patch_body=_PATCH,
        request_fields=None,
    ):
        fix_run = _fix_run(source_run, diagnosis=diagnosis, report=report, plan=plan)
        request = {
            **fix_run.request,
            "held_back_scenario_keys": [f"held-{n}" for n in range(held_back)],
            **(request_fields or {}),
        }
        if evidence is not None:
            request["evidence"] = evidence
        candidate = _candidate(fix_run)
        # A pull request comes from a finished fix.
        AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
            state=State.READY, request=request
        )
        AgentFixCandidate.no_workspace_objects.filter(id=candidate.id).update(
            outcome=outcome,
            verdict=verdict
            or {
                "accepted": True,
                "reason": "passed every tier",
                "target_pass_rate": [0, 0.625],
                "held_back_pass_rate": [0.25, 0.75],
                "tiers": _TIERS,
            },
            override_reason=override_reason,
            summary="Changed only prompts (booking.md).",
        )
        candidate = AgentFixCandidate.no_workspace_objects.select_related(
            "fix_run"
        ).get(id=candidate.id)
        none_graded = EvalReview(grading=EvalGrading.NONE)
        with patch.object(agent_fix_github, "eval_review", return_value=none_graded):
            return candidate, *agent_fix_github._description(candidate, patch_body)

    def test_a_verified_fix_reads_each_check_as_the_fix_page_says_it(self, source_run):
        candidate, title, body = self._described(
            source_run,
            diagnosis={"causes": _CAUSES},
            plan={"must_pass": 6, "protected": 2},
            request_fields={"meters_agent_cost": True},
        )

        assert title == "Epoch: fix 2 diagnosed causes in 3 files"
        # The fix page's headline: the pass rates and the must-pass cases it broke.
        assert (
            "Verified by Epoch: held-back pass rate 25% → 75%, target pass rate"
            " 0% → 63%, broke none\n\n### Verdict" in body
        )
        # The engine's own reason is not product text: the checks say it in words.
        assert "passed every tier" not in body
        assert "### Verdict: Verified" in body
        # The fix page's words for each check, from the verdict's own numbers.
        table, decided = body.split("<details><summary>How it was decided</summary>")
        for row in (
            "| Change is valid | Passed | Applies cleanly and edits only allowed files. |",
            "| Target scenarios | Passed | 0% → 63%, a clear gain. |",
            "| Held-back scenarios | Passed | 25% → 75% on scenarios the epoch never"
            " saw, a clear gain. |",
            "| Must-pass scenarios | Passed | None of the 6 must-pass scenarios broke,"
            " including the 2 you protected. |",
            "| Holds over repeats | Passed | Held up over the repeat runs. |",
            # A metered agent's older check judged its cost too, so it stays whole.
            "| Reply time and cost | Passed | Within the reply-time and cost limits. |",
        ):
            assert row in table
        assert "| target |" not in body and "| passed |" not in body
        # The engine's statistics are folded below, never in the table.
        assert "0/8 → 5/8" not in table
        assert "- Target scenarios: 0/8 → 5/8 | 2 repeats" in decided
        assert "The held-back pass rate above comes from the repeat runs." in decided
        # The platform did not measure the calls itself, so nothing says how it does.
        assert "Measured automatically" not in body
        # The feature is Epoch wherever the user reads it.
        assert "Fix my agent" not in title + body
        assert "### What changed" in body
        assert "| `concierge/agents.py` | +12 | −3 |" in body
        assert "| `prompts/booking.md` | +4 | −1 |" in body
        assert "### Why" in body and f"- {_CAUSES[1]}" in body
        assert "### What could still go wrong\n\nNothing the checks flagged." in body
        assert "### Evidence" not in body
        # The engine's summary is labelled as its own, on the trial the page names.
        assert (
            "<details><summary>Notes from Epoch on the last change of Candidate 1"
            "</summary>" in body
        )
        run = candidate.fix_run
        assert (
            "[Open this epoch on Future AGI](https://app.example/dashboard/simulate/"
            f"environments/{run.environment_id}/improvements/{run.id})" in body
        )

    @pytest.mark.parametrize(
        ("causes", "paths", "marks", "title"),
        [
            pytest.param(
                6,
                ["instructions.md"],
                "",
                "Epoch: fix 6 diagnosed causes in instructions.md",
                id="one-file",
            ),
            pytest.param(
                2,
                ["agent.py", "prompts/booking.md", "config.yaml"],
                "",
                "Epoch: fix 2 diagnosed causes in 3 files",
                id="many-files",
            ),
            pytest.param(
                2,
                [f"agents/{'scheduling_' * 6}instructions.md"],
                "",
                "Epoch: fix 2 diagnosed causes in 1 file",
                id="long-file-name",
            ),
            pytest.param(
                1,
                ["prompts/booking.md"],
                "",
                "Epoch: fix 1 diagnosed cause in booking.md",
                id="one-cause",
            ),
            pytest.param(
                0, ["prompts/booking.md"], "", "Epoch: change booking.md", id="no-cause"
            ),
            pytest.param(
                0,
                ["agent.py", "config.yaml"],
                "",
                "Epoch: change 2 files",
                id="no-cause-files",
            ),
            pytest.param(
                2,
                ["agent.py", "config.yaml", "prompts/booking.md"],
                "limited validation, known regression",
                "Epoch (limited validation, known regression): fix 2 causes in 3 files",
                id="both-marks",
            ),
        ],
    )
    def test_a_title_says_how_many_diagnosed_causes_it_fixes_and_where(
        self, causes, paths, marks, title
    ):
        diagnosis = {"causes": [f"Cause {n}." for n in range(causes)]}
        candidate = SimpleNamespace(fix_run=SimpleNamespace(diagnosis=diagnosis))
        files = [(path, 1, 0) for path in paths]

        made = agent_fix_github._title(candidate, files, marks)

        # Counts and a file name only: never a cause's words, never a cut sentence.
        assert (made, len(made) <= 72) == (title, True)

    def test_the_e2e_winner_reads_one_held_back_rate_and_an_unknown_cost(
        self, source_run
    ):
        # The verdict of the winner behind the real pull request, as the engine sent it:
        # an older engine, so its cost and latency check has no parts.
        verdict = {
            "accepted": True,
            "reason": "passed every tier; protected cases: 5 of 5 held",
            "target_pass_rate": [0.0278, 0.5833],
            "held_back_pass_rate": [0.0, 0.5556],
            "must_pass_regressions": [],
            "must_pass_within_noise": [],
            "tiers": [
                {
                    "name": "static",
                    "status": "passed",
                    "detail": "6 checks passed, no model call",
                },
                {
                    "name": "target",
                    "status": "passed",
                    "detail": "58% pass vs baseline 3%; noise allows up to 29%"
                    " (p < 0.001, bar 0.3)",
                },
                {
                    "name": "held-back",
                    "status": "passed",
                    "detail": "50% pass vs baseline 0%; noise allows up to 17%"
                    " (p = 0.016, bar 0.3)",
                },
                {
                    "name": "must-pass",
                    "status": "passed",
                    "detail": "always passed: 0 of 28 down; chance predicts 0.4, noise"
                    " allows up to 3; sometimes passed: 0 of 7 failed every run; chance"
                    " predicts 1.0, noise allows up to 4; protected cases: 5 of 5 held",
                },
                {
                    "name": "repeats",
                    "status": "passed",
                    "detail": "56% pass vs baseline 0%; noise allows up to 39%"
                    " (p < 0.001, bar 0.05); finalist repeats 50%, 67%, 50%, baseline"
                    " repeats 0%, 0%, 0%",
                },
                {
                    "name": "ceilings",
                    "status": "passed",
                    "detail": "cost not measurable: the environment does not meter the"
                    " agent's own model calls; latency 32.6s (baseline 55.4s, ceiling"
                    " 3600s)",
                },
            ],
        }

        _, title, body = self._described(
            source_run,
            held_back=6,
            diagnosis={"causes": [f"Cause {n}." for n in range(6)]},
            plan={"must_pass": 6, "protected": 5, "held_back": 6, "repeats": 3},
            verdict=verdict,
            patch_body=_diff(("instructions.md", 28, 14)),
        )

        assert title == "Epoch: fix 6 diagnosed causes in instructions.md"
        table, decided = body.split("<details><summary>How it was decided</summary>")
        # The headline and its row quote the verdict's one held-back rate.
        assert "held-back pass rate 0% → 56%" in table
        assert (
            "| Held-back scenarios | Passed | 0% → 56% on scenarios the epoch never saw,"
            " a clear gain. |" in table
        )
        assert "50%" not in table
        # An unmetered agent's cost was never measured: it never reads passed.
        assert (
            "| Reply time and cost | Couldn't measure |  |\n"
            "| ↳ Reply time | Passed | Within the limit. |\n"
            "| ↳ Agent's cost | Cost unknown | This agent's model calls weren't"
            " measured. Epoch measures calls made through the OpenAI or Anthropic"
            " Python SDK on its own. For other agents, add traceAI tracing. |" in table
        )
        assert "Measured automatically" not in body
        # The statistics stay folded, with the engine's numbers as it sent them.
        assert "p < 0.001" not in table and "chance predicts" not in table
        assert "- Held-back scenarios: 50% pass vs baseline 0%" in decided
        assert "The held-back pass rate above comes from the repeat runs." in decided

    def test_the_measured_line_shows_only_when_the_platform_measured_the_calls(
        self, source_run
    ):
        ceilings = {
            "name": "ceilings",
            "status": "passed",
            "detail": "latency within; cost within",
            "parts": [
                {"name": "reply latency", "status": "passed", "detail": "1.2 s"},
                {"name": "agent cost", "status": "passed", "detail": "$0.002"},
            ],
        }
        verdict = {
            "accepted": True,
            "reason": "passed every tier",
            "target_pass_rate": [0, 0.625],
            "held_back_pass_rate": [0.25, 0.75],
            "tiers": [*_TIERS[:-1], ceilings],
        }

        def body(**fields):
            AgentFixRun.no_workspace_objects.all().delete()
            return self._described(
                source_run,
                verdict=verdict,
                request_fields={"meters_agent_cost": True, **fields},
            )[2]

        measured = body(measured_automatically=True)
        traced = body()

        assert (
            "| ↳ Agent's cost | Passed | Within the limit. |\n\nMeasured automatically:"
            " model, tokens and timing only. Prompts and replies are never recorded."
            in measured
        )
        # A cost from the agent's own traces is measured, but not by the platform.
        assert "| ↳ Agent's cost | Passed | Within the limit. |" in traced
        assert "Measured automatically" not in traced

    def test_a_body_lists_the_first_50_files_and_counts_the_rest(self, source_run):
        many = _diff(*((f"agent/module_{n}.py", 1, 0) for n in range(120)))

        _, _, body = self._described(
            source_run, diagnosis={"causes": _CAUSES}, patch_body=many
        )

        assert "| `agent/module_49.py` | +1 | −0 |" in body
        assert "module_50.py" not in body
        assert "\n\nand 70 more files" in body

    def test_a_body_too_long_for_github_is_cut_at_a_line_and_keeps_the_link(
        self, source_run
    ):
        # GitHub refuses a body over 65,536 characters, after the branch is pushed.
        causes = [f"Cause {n}: " + "the agent repeats itself " * 400 for n in range(20)]

        candidate, _, body = self._described(source_run, diagnosis={"causes": causes})

        assert len(body) <= 60_000
        assert (
            "*Cut to fit GitHub's size limit. The epoch's page has all of it.*" in body
        )
        run = candidate.fix_run
        assert f"/improvements/{run.id})" in body
        assert body.endswith("Opened by Epoch on Future AGI.")
        assert body.count("<details>") == body.count("</details>")

    def test_a_limited_fix_without_a_diagnosis_is_titled_by_the_files_it_changes(
        self, source_run
    ):
        flaky = [
            {"key": "case-2", "sub_goal": "refunded", "passed": 2, "runs": 3},
            {"key": "case-2", "sub_goal": "polite", "passed": 1, "runs": 3},
            {
                "key": "case-4",
                "name": "Refund a late order",
                "sub_goal": "apologised",
                "passed": 1,
                "runs": 3,
            },
        ]
        ceilings = {
            "name": "ceilings",
            "status": "skipped",
            "detail": "Cost not measured.",
            "parts": [
                {
                    "name": "reply latency",
                    "status": "passed",
                    "detail": "1.2 s of 1.5 s",
                },
                {"name": "agent cost", "status": "skipped"},
            ],
        }
        verdict = {
            "accepted": True,
            "reason": "passed every tier",
            "target_pass_rate": [0, 0.625],
            "tiers": [
                *_TIERS[:-1],
                ceilings,
                {"name": "model-lock", "status": "skipped"},
            ],
        }
        _, title, body = self._described(
            source_run,
            held_back=2,
            report={"flaky_protected": flaky},
            verdict=verdict,
        )

        assert title == "Epoch (limited validation): change 3 files"
        # How many were held back, and how many it takes.
        assert body.startswith(
            "**Limited validation.** Only 2 failing cases were held back from the epoch to"
            " verify it; it takes 4 or more."
        )
        assert "Checked by Epoch" in body and "Verified by" not in body
        assert "### Verdict: Limited validation" in body
        assert "### Why" not in body
        # A check that could not measure says so, and an untraced agent's cost says why.
        assert "| Reply time and cost | Couldn't measure |  |" in body
        assert "| ↳ Reply time | Passed | Within the limit. |" in body
        assert (
            "| ↳ Agent's cost | Cost unknown | This agent's model calls weren't"
            " measured. Epoch measures calls made through the OpenAI or Anthropic"
            " Python SDK on its own. For other agents, add traceAI tracing. |" in body
        )
        assert (
            "| Model unchanged | Couldn't measure | This agent sends no traces. The"
            " change names no new model. |" in body
        )
        # The engine's own words for each part stay folded below.
        assert (
            "- Reply time and cost: Cost not measured.\n  - Reply time: 1.2 s of 1.5 s"
            in body
        )
        # No cost was measured, so nothing says how one is.
        assert "Measured automatically" not in body
        # A protected case reads once, by its name, and by its key only without one.
        assert (
            "- Protected case “case-2” is flaky on the current version, so this epoch"
            " wasn't held to it: its “refunded” check passed 2 of 3 runs, its “polite”"
            " check passed 1 of 3 runs." in body
        )
        assert body.count("Protected case “case-2”") == 1
        assert "- Protected case “Refund a late order” is flaky" in body
        assert "case-4" not in body

    def test_every_check_the_engine_records_reads_as_the_fix_page_names_it(self):
        _check_label = agent_fix_github._check_label

        assert set(VERDICT_TIERS) - {"model-lock"} == set(
            agent_fix_github._CHECK_LABELS
        )
        assert _check_label("model-lock", {"allowed_models": ["a"]}) == (
            "Model unchanged"
        )
        assert _check_label("model-lock", {"allowed_models": ["a", "b"]}) == (
            "Approved models"
        )
        # A check a newer engine adds still reads as words.
        assert _check_label("tool_budget", {}) == "Tool budget"

    def test_an_overridden_fix_names_its_regression_and_the_users_reason(
        self, source_run
    ):
        tiers = [
            {**tier, "status": "failed", "detail": "1 of 6 regressed."}
            if tier["name"] == "must-pass"
            else tier
            for tier in _TIERS
        ]
        _, title, body = self._described(
            source_run,
            diagnosis={"causes": _CAUSES[:1]},
            outcome=Outcome.REJECTED,
            override_reason="The regression is a test we are retiring.",
            plan={"held_back": 4},
            verdict={
                "accepted": False,
                "reason": "a must-pass check regressed",
                "target_pass_rate": [0, 0.625],
                "must_pass_regressions": ["case-1: refunded"],
                "must_pass_within_noise": ["case-3: polite"],
                "tiers": tiers,
            },
        )

        assert title == "Epoch (known regression): fix 1 diagnosed cause in 3 files"
        assert body.startswith("**Known regression.**")
        # The case and its check apart, and who chose to open it anyway.
        assert "- Must-pass case “case-1”: its “refunded” check" in body
        assert "It was opened anyway, for this reason:" in body
        assert "> The regression is a test we are retiring." in body
        assert "Checked by Epoch: target pass rate 0% → 63%, broke 1" in body
        assert "### Verdict: Known regression" in body
        assert "| Must-pass scenarios | Failed | 1 must-pass scenario broke. |" in body
        assert "- Must-pass scenarios: 1 of 6 regressed." in body
        assert (
            "- Must-pass case “case-3”: its “polite” check passed less often, within"
            " normal noise, so it was not counted." in body
        )

    def test_a_fix_from_evidence_counts_each_imports_sources_and_never_names_them(
        self, source_run
    ):
        evidence = [
            {
                "import_id": "6d1f0b52-0b7a-4c4e-9a43-2b1a8f0c1d11",
                "source_kind": "session",
                "source_ids": ["session-with-a-customer-name", "another-session"],
                "scenario_keys": ["evidence-0a1b2c3d4e5f", "evidence-0a1b2c3d4e60"],
            },
            {
                "import_id": "7e2f0b52-0b7a-4c4e-9a43-2b1a8f0c1d12",
                "source_kind": "dataset",
                "source_ids": ["row-1", "row-2", "row-3"],
                "scenario_keys": ["evidence-1", "evidence-2", "evidence-3"],
            },
            {"source_kind": "a-kind-from-later", "scenario_keys": ["evidence-4"]},
        ]
        _, _, body = self._described(
            source_run, diagnosis={"causes": _CAUSES}, evidence=evidence
        )

        # The import page's words for each kind of source.
        assert (
            "### Evidence\n\nThis epoch started from imported evidence:\n\n"
            "- 2 sessions from Observe\n"
            "- 3 dataset rows\n"
            "- 1 imported case"
        ) in body
        # Never an import's id, nor its sources.
        assert "6d1f0b52" not in body and "row-1" not in body
        assert "session-with-a-customer-name" not in body


class TestCheckRunReceipts:
    def test_a_check_run_s_hosted_receipt_starts_no_grading(self, source_run):
        """A fix grades its check runs only once it has a winner: a check run's hosted
        receipt queues no eval and no tool-call judge though its run test selects one, and
        closes its call's eval side so a winner's grading can take it."""
        fix_run = _fix_run(source_run)
        _mapped_eval(source_run)
        run = _check(fix_run)
        attempt = register_attempt(
            run.id, endpoint_base_url="https://harness.example.test"
        ).attempt
        execution = _executions(run).first()
        body = {
            "schema_version": "futureagi.harness-result.v1",
            "job_id": str(run.id),
            "attempt_id": str(attempt.id),
            "attempt_number": attempt.attempt_number,
            "scenario_key": execution.execution_key,
            "scenario_id": str(execution.source_scenario.scenario_id),
            "scenario_attempt": 1,
            "world_index": None,
            "status": "passed",
            "sub_goals": [],
            "evaluations": [],
            "call": None,
            "failure": None,
        }
        body["digest"] = canonical_digest(body)

        with (
            patch(
                "simulate.services.alk_simulate_ingestion._dispatch_evaluations_once"
            ) as dispatch,
            TestCase.captureOnCommitCallbacks(execute=True),
        ):
            ingest_result_receipt(attempt, body)

        dispatch.assert_not_called()
        call = CallExecution.objects.get(id=execution.call_execution_id)
        assert call.status == CallExecution.CallStatus.COMPLETED
        assert (
            call.call_metadata.get("eval_started"),
            call.call_metadata.get("eval_completed"),
        ) == (True, True)
