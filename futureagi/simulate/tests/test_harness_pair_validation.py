import json
from unittest.mock import patch

import pytest

from simulate.models import (
    HarnessAgentVersionRuntime,
    HarnessAssembledBundle,
    HarnessNudge,
    HarnessSuiteMember,
    HostedHarnessExecution,
    HostedHarnessJob,
    TestExecution,
)
from simulate.services import agent_fix_runs
from simulate.services.harness_agent_versions import (
    HAND_MOVE_KEY,
    _activate_on,
    _still_waiting,
)
from simulate.services.harness_versioning import assembly, rebuild, validation
from simulate.services.harness_versioning.backfill import effective_runtime
from simulate.services.harness_versioning.versions import move_agent_default
from simulate.services.hosted_harness import (
    HostedHarnessError,
    create_selected_harness_run,
    project_terminal_job_onto_run,
    record_cleanup,
)
from simulate.tests.harness_validation import running_validations, settle_validations
from simulate.tests.test_harness_agent_versions import (
    _a1,
    _create,
)
from simulate.tests.test_harness_agent_versions import _environment as _agent_env
from simulate.tests.test_harness_agent_versions import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_amend_archive import NAMES, _key
from simulate.tests.test_harness_nudges import (  # noqa: F401 - fixture
    started as started,
)
from simulate.tests.test_harness_versioning_assembly import (
    _environment,
    _launch,
    _row,
    _uncache,
)
from simulate.tests.test_harness_versioning_assembly import (  # noqa: F401 - fixture
    gateway as gateway,
)
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = pytest.mark.django_db

START_WORKFLOW = "simulate.temporal.client.start_hosted_harness_gateway_workflow"
FAILED_ACTIONS = {
    "actions": [
        {"action": "issue_refund", "status": "failed", "reason": "reached production"},
        {"action": "lookup_order", "status": "passed", "reason": ""},
        {"action": "cancel_order", "status": "refused", "reason": "policy"},
    ]
}


_UNREAD = validation.unread


@pytest.fixture(autouse=True)
def tools_read(monkeypatch):
    """Every agent version's tools were read, unless a test reads them in its check."""
    monkeypatch.setattr(validation, "unread", lambda agent_version, runtime: False)


@pytest.fixture
def tools_unread(monkeypatch):
    """Agent versions are read only where a stored document says so."""
    monkeypatch.setattr(validation, "unread", _UNREAD)


@pytest.fixture(autouse=True)
def certify_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
    settings.HARNESS_PUBLIC_BASE_URL = "https://platform.example"


def _pending(user, workspace, storage, key):
    """An environment whose default pair is not certified yet."""
    environment = _environment(user, workspace, key, storage)
    _uncache(environment)
    return environment


def _start(environment, *, purpose="recheck", move=None):
    return validation.start_validation(
        environment,
        environment.agent_version,
        environment.environment_version,
        environment.suite_version,
        purpose=purpose,
        move=move,
    )


def _ready(bundle):
    HarnessAssembledBundle._base_manager.filter(id=bundle.id).update(
        state="ready", object_key="harness-bundles/certified.tar.gz"
    )


def _failed(bundle, **error):
    HarnessAssembledBundle._base_manager.filter(id=bundle.id).update(
        state="failed", error={"code": "authoring_runtime_validation_failed", **error}
    )


def _end(job, *, state, code=None):
    """The job's terminal state as the gateway records it."""
    HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
        state=state, failure={"code": code, "message": code} if code else None
    )
    return validation.finish_validation(job.id)


def _bundle(bundle):
    return HarnessAssembledBundle._base_manager.get(id=bundle.id)


def _job_metadata(job):
    job.refresh_from_db()
    return job.payload["metadata"]


class TestStart:
    def test_validation_job_has_fk_pins_and_no_test_execution(
        self, user, workspace, storage
    ):
        environment = _pending(user, workspace, storage, "pv-job")
        executions = TestExecution.no_workspace_objects.count()

        started = _start(environment)

        job = started.job
        assert started.state == "validating"
        assert job.environment_id == environment.id
        assert job.run_test_id == environment.run_test_id
        assert (
            job.agent_version_id,
            job.environment_version_id,
            job.suite_version_id,
            job.assembled_bundle_id,
        ) == (
            environment.agent_version_id,
            environment.environment_version_id,
            environment.suite_version_id,
            _row(environment).id,
        )
        assert job.test_execution_id is None
        assert TestExecution.no_workspace_objects.count() == executions
        assert not HostedHarnessExecution.no_workspace_objects.filter(job=job).exists()
        metadata = job.payload["metadata"]
        assert metadata["certify_only"] is True
        assert metadata["validation_purpose"] == "recheck"
        assert metadata["environment_job_id"] == str(environment.id)
        assert metadata["assembly"]["defaults"] is False
        assert "simulation_only" not in metadata

    def test_payload_strips_build_spend(self, user, workspace, storage):
        environment = _pending(user, workspace, storage, "pv-spend")
        payload = dict(environment.payload)
        payload["metadata"] = {
            **payload["metadata"],
            "harness_spend": {"attempts": {"1": {"usd": 4.0}}},
            "scenario_extend": {"delta": 2},
            "usage_reports": [{"usd": 1}],
        }
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )
        environment.refresh_from_db()

        metadata = _start(environment).job.payload["metadata"]

        assert not {"harness_spend", "scenario_extend", "usage_reports"} & set(metadata)

    def test_legacy_side_row_is_validated(self, user, workspace, storage):
        environment = _pending(user, workspace, storage, "pv-legacy")
        runtime = HarnessAgentVersionRuntime.no_workspace_objects.get(
            agent_version_id=environment.agent_version_id
        )
        assert runtime.preflight_status == "legacy"

        started = _start(environment)

        assert started.bundle.assembly_key == assembly.bundle_key(
            effective_runtime(environment, runtime),
            environment.environment_version,
            environment.suite_version,
        )

    def test_no_side_row_skips_validation(self, user, workspace, storage, settings):
        environment = _pending(user, workspace, storage, "pv-no-side")
        jobs = HostedHarnessJob.no_workspace_objects.count()

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
        assert _start(environment) is None
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=environment.agent_version_id
        ).delete()
        assert _start(environment) is None
        assert HostedHarnessJob.no_workspace_objects.count() == jobs

    def test_a_certified_pair_is_ready_without_a_job(self, user, workspace, storage):
        environment = _environment(user, workspace, "pv-ready", storage)
        jobs = HostedHarnessJob.no_workspace_objects.count()

        started = _start(environment)

        assert (started.state, started.job) == ("ready", None)
        assert HostedHarnessJob.no_workspace_objects.count() == jobs

    def test_recheck_resets_failed_to_pending(self, user, workspace, storage):
        environment = _pending(user, workspace, storage, "pv-recheck")
        _failed(_row(environment), source="validation")

        started = _start(environment)

        assert _bundle(started.bundle).state == "pending"
        assert started.job is not None

    def test_a_running_validation_is_returned_with_the_move(
        self, user, workspace, storage
    ):
        environment = _pending(user, workspace, storage, "pv-twice")
        first = _start(environment)
        move = validation.agent_move(
            "hand", environment, environment.agent_version, user=user
        )

        second = _start(environment, purpose="agent_move", move=move)

        assert second.job.id == first.job.id
        assert _job_metadata(first.job)["validation_move"] == move

    def test_candidate_suite_certifies_with_its_scenario_count(
        self, user, workspace, storage
    ):
        environment = _pending(user, workspace, storage, "pv-candidate")
        job = _start(environment).job
        assert assembly.certify_scenario_count(job) == 2
        HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=job.suite_version_id
        ).delete()
        type(job.suite_version)._base_manager.filter(id=job.suite_version_id).update(
            scenario_count=3
        )

        assert assembly.certify_scenario_count(job) == 3


class TestDispatch:
    def test_the_workflow_starts_on_commit(
        self, user, workspace, storage, django_capture_on_commit_callbacks
    ):
        environment = _pending(user, workspace, storage, "pv-dispatch")

        with patch(START_WORKFLOW) as started:
            with django_capture_on_commit_callbacks(execute=True):
                job = _start(environment).job

        assert started.call_args.args[:2] == (
            str(job.id),
            "https://platform.example",
        )

    def test_dispatch_failure_fails_the_job(
        self, user, workspace, storage, django_capture_on_commit_callbacks
    ):
        environment = _pending(user, workspace, storage, "pv-unscheduled")

        with patch(START_WORKFLOW, side_effect=RuntimeError("temporal down")):
            with django_capture_on_commit_callbacks(execute=True):
                job = _start(environment).job

        job.refresh_from_db()
        assert job.state == "failed"
        assert job.failure["code"] == "scheduler_unavailable"
        assert job.payload["metadata"]["validation_outcome"] == "retry"
        assert _row(environment).state == "pending"


class TestLaunch:
    def test_a_validation_runs_only_the_certify_command(
        self, user, workspace, storage, gateway
    ):
        environment = _pending(user, workspace, storage, "pv-command")
        job = _start(environment).job

        _launch(gateway, job)

        command = gateway.client.sandbox.process.session_request.command
        assert command == assembly.certify_command()
        certify = json.loads(
            gateway.client.sandbox.fs.uploads[assembly.CERTIFY_JOB_PATH]
        )
        assert certify["scenario_count"] == 2

    def test_ready_before_launch_completes_without_sandbox(
        self, user, workspace, storage, gateway
    ):
        environment = _pending(user, workspace, storage, "pv-hit")
        job = _start(environment).job
        _ready(_row(environment))

        attempt = _launch(gateway, job)

        assert gateway.client.params is None
        assert attempt.terminal_stage == "completed"
        assert job.state == "completed"

    def test_launch_hit_completes_without_retry(
        self, user, workspace, storage, gateway, monkeypatch
    ):
        environment = _pending(user, workspace, storage, "pv-hit-poll")
        job = _start(environment).job
        _ready(_row(environment))
        attempt = _launch(gateway, job)

        def never(_attempt):
            raise AssertionError("a settled attempt has no sandbox to inspect")

        monkeypatch.setattr(gateway, "inspect", never)
        polled = gateway.reconcile_completed(attempt)

        assert polled.state == "completed"
        assert polled.current_attempt_number == 1


def _exit(gateway, monkeypatch, code, files=None):
    """The guest exits ``code``; the sandbox holds ``files``; cleanup is verified."""
    monkeypatch.setattr(
        gateway,
        "inspect",
        lambda _: {"exit_code": code, "logs": "", "process_logs": ""},
    )
    files = files or {}
    monkeypatch.setattr(
        gateway.client.sandbox.fs,
        "download_file",
        lambda path, timeout=None: files[path],
    )
    monkeypatch.setattr(
        gateway,
        "_delete_and_record",
        lambda attempt, *, retry_pending=False: record_cleanup(
            attempt.id,
            provider_ref=str(attempt.provider_ref),
            verified_absent=True,
            retry_pending=retry_pending,
        ),
    )


class TestReconcile:
    def test_exit_0_ready_completes(
        self,
        user,
        workspace,
        storage,
        gateway,
        monkeypatch,
        django_capture_on_commit_callbacks,
    ):
        environment = _pending(user, workspace, storage, "pv-exit0")
        job = _start(environment).job
        attempt = _launch(gateway, job)
        _ready(_row(environment))
        _exit(gateway, monkeypatch, 0)

        with django_capture_on_commit_callbacks(execute=True):
            done = gateway.reconcile_completed(attempt)

        assert done.state == "completed"
        assert _job_metadata(job)["validation_outcome"] == "ready"

    def test_exit_0_not_ready_is_an_unavailable_check_not_a_failed_pair(
        self,
        user,
        workspace,
        storage,
        gateway,
        monkeypatch,
        django_capture_on_commit_callbacks,
    ):
        environment = _pending(user, workspace, storage, "pv-incomplete")
        job = _start(environment).job
        attempt = _launch(gateway, job)
        _exit(gateway, monkeypatch, 0)

        with django_capture_on_commit_callbacks(execute=True):
            done = gateway.reconcile_completed(attempt)

        assert (done.state, done.failure["code"]) == ("failed", "certify_incomplete")
        row = _row(environment)
        assert row.state == "pending"
        assert row.error["last_attempt"]["code"] == "check_unavailable"
        assert _job_metadata(job)["validation_outcome"] == "unavailable"

    def test_exit_78_records_failed_actions_for_every_miss(
        self, user, workspace, storage, gateway, monkeypatch
    ):
        from simulate.tests.test_harness_versioning_assembly import _off_defaults
        from simulate.tests.test_harness_versioning_runs import _run

        environment = _pending(user, workspace, storage, "pv-78-run")
        run = _off_defaults(_run(environment))
        attempt = _launch(gateway, run)
        _exit(
            gateway,
            monkeypatch,
            78,
            {assembly.ACTION_CERTIFICATION_PATH: json.dumps(FAILED_ACTIONS).encode()},
        )

        gateway.reconcile_completed(attempt)

        row = _row(environment)
        assert row.state == "failed"
        assert row.error["failed_actions"] == [
            {"action": "issue_refund", "reason": "reached production"}
        ]
        # An ordinary run's failure does not gate the next run.
        assert "source" not in row.error

    def test_exit_78_without_the_report_keeps_the_message_only(
        self, user, workspace, storage, gateway, monkeypatch
    ):
        environment = _pending(user, workspace, storage, "pv-78-bare")
        job = _start(environment).job
        attempt = _launch(gateway, job)
        _exit(gateway, monkeypatch, 78)

        gateway.reconcile_completed(attempt)

        assert "failed_actions" not in _row(environment).error

    def test_end_hook_keeps_failed_actions(
        self,
        user,
        workspace,
        storage,
        gateway,
        monkeypatch,
        django_capture_on_commit_callbacks,
    ):
        environment = _pending(user, workspace, storage, "pv-78-validation")
        job = _start(environment).job
        attempt = _launch(gateway, job)
        _exit(
            gateway,
            monkeypatch,
            78,
            {assembly.ACTION_CERTIFICATION_PATH: json.dumps(FAILED_ACTIONS).encode()},
        )

        with django_capture_on_commit_callbacks(execute=True):
            gateway.reconcile_completed(attempt)

        assert _job_metadata(job)["validation_outcome"] == "failed"
        error = _row(environment).error
        assert error["source"] == "validation"
        assert error["failed_actions"] == [
            {"action": "issue_refund", "reason": "reached production"}
        ]


class TestTerminalPaths:
    @pytest.mark.parametrize(
        "end",
        ["cleanup_completed", "cleanup_failed", "launch_failure_projection"],
    )
    def test_every_terminal_path_ends_validation(
        self, user, workspace, storage, end, django_capture_on_commit_callbacks
    ):
        from simulate.services.hosted_harness import register_attempt

        environment = _pending(user, workspace, storage, f"pv-end-{end}")
        job = _start(environment).job
        attempt = register_attempt(
            job.id, endpoint_base_url="https://platform.example"
        ).attempt

        with django_capture_on_commit_callbacks(execute=True):
            if end == "launch_failure_projection":
                job.state = HostedHarnessJob.State.FAILED
                job.failure = {"code": "hosted_launch_failed"}
                job.save(update_fields=["state", "failure"])
                project_terminal_job_onto_run(job, job.updated_at)
            else:
                completed = end == "cleanup_completed"
                if completed:
                    _ready(_row(environment))
                attempt.terminal_stage = "completed" if completed else "failed"
                attempt.terminal_failure = (
                    None if completed else {"code": "sandbox_disappeared"}
                )
                attempt.save()
                record_cleanup(attempt.id, provider_ref="", verified_absent=True)

        expected = "ready" if end == "cleanup_completed" else "retry"
        assert _job_metadata(job)["validation_outcome"] == expected

    def test_cancel_before_attempt_ends_validation(
        self, user, workspace, storage, gateway, django_capture_on_commit_callbacks
    ):
        environment = _pending(user, workspace, storage, "pv-cancel")
        job = _start(environment).job

        with django_capture_on_commit_callbacks(execute=True):
            canceled = gateway.cancel(job, reason="user")

        assert canceled.state == "canceled"
        assert _job_metadata(job)["validation_outcome"] == "retry"

    def test_infrastructure_ending_resets_to_pending_not_blocked(
        self, user, workspace, storage
    ):
        environment = _pending(user, workspace, storage, "pv-infra")
        job = _start(environment).job

        assert _end(job, state="failed", code="sandbox_disappeared") == "retry"

        row = _row(environment)
        assert row.state == "pending"
        assert row.error["last_attempt"]["code"] == "sandbox_disappeared"
        from simulate.tests.test_harness_versioning_runs import _run

        assert _run(environment).assembled_bundle_id == row.id

    def test_a_job_is_settled_once(self, user, workspace, storage):
        environment = _pending(user, workspace, storage, "pv-once")
        job = _start(environment).job

        assert (
            _end(job, state="failed", code="authoring_runtime_validation_failed")
            == "failed"
        )
        assert validation.finish_validation(job.id) is None


class TestRunGate:
    def test_only_validation_failures_gate_runs(self, user, workspace, storage):
        from simulate.tests.test_harness_versioning_runs import _run

        environment = _pending(user, workspace, storage, "pv-gate")
        _failed(_row(environment))
        assert _run(environment, key="ordinary").assembled_bundle_id

        _failed(_row(environment), source="validation")
        with pytest.raises(HostedHarnessError) as refused:
            _run(environment, key="after-validation")
        assert refused.value.code == "pair_failed"

    def test_a_validating_pair_is_refused(self, user, workspace, storage):
        from simulate.tests.test_harness_versioning_runs import _run

        environment = _pending(user, workspace, storage, "pv-gate-live")
        _start(environment)

        with pytest.raises(HostedHarnessError) as refused:
            _run(environment)
        assert refused.value.code == "pair_validating"

    def test_gate_runs_before_run_assembly_resets(self, user, workspace, storage):
        environment = _pending(user, workspace, storage, "pv-gate-order")
        _failed(_row(environment), source="validation")

        with pytest.raises(HostedHarnessError):
            create_selected_harness_run(
                environment,
                scenario_keys=[_key(NAMES[0])],
                trials=1,
                idempotency_key="gated",
            )

        assert _row(environment).state == "failed"


def _with_a2(user, workspace, storage, key):
    """An environment on legacy A1, with A2 checked and pickable but not its default;
    their pairing is not certified."""
    environment = _agent_env(user, workspace, key, storage)
    _uncache(environment)
    a1 = _a1(environment)
    a2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v2"})
    HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
        preflight_status="passed"
    )
    return environment, a1, a2


def _move_job(environment, a2):
    return HostedHarnessJob.no_workspace_objects.get(
        environment=environment,
        payload__metadata__validation_move__agent_version_id=str(a2.id),
    )


def _nudge(environment, a1, a2, user):
    return HarnessNudge.no_workspace_objects.create(
        job=environment,
        organization=environment.organization,
        target="environment",
        trigger_agent_version=a2,
        answered_by=user,
        draft_snapshot={"answered_default_agent_id": str(a1.id)},
    )


def _activations(version):
    report = HarnessAgentVersionRuntime.no_workspace_objects.get(
        agent_version=version
    ).preflight_report
    return {
        one["environment_id"]: one["outcome"] for one in report.get("activation") or []
    }


class TestAgentMoves:
    @pytest.mark.parametrize("caller", ["activation", "hand", "no_change"])
    def test_agent_move_paths_validate_first(self, user, workspace, storage, caller):
        environment, a1, a2 = _with_a2(user, workspace, storage, f"pv-move-{caller}")

        if caller == "activation":
            assert _activate_on(environment.id, a2, explicit=True, user=user) == (
                "validating"
            )
        elif caller == "hand":
            _moved, started = move_agent_default(
                environment, a2.id, expected_default_id=a1.id, user=user
            )
            assert started.state == "validating"
        else:
            summary = rebuild._no_change_move(
                environment, _nudge(environment, a1, a2, user)
            )
            assert summary == {
                "agent_default_moved": False,
                "agent_not_moved": "validating",
            }

        environment.refresh_from_db()
        assert environment.agent_version_id == a1.id
        job = _move_job(environment, a2)
        assert job.payload["metadata"]["validation_move"]["caller"] == caller
        assert job.agent_version_id == a2.id

    def test_a_landing_lands_for_the_current_agent_then_validates(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        settings,
        django_capture_on_commit_callbacks,
    ):
        from simulate.tests.test_harness_versioning_rebuild import _offered, _turn

        settings.HARNESS_VERSIONING_NUDGES_ENABLED = True

        # The offer is set up with moves made at once; only the landing validates.
        with patch.object(validation, "can_validate", return_value=False):
            environment, nudge, a2, a3 = _offered(
                user,
                workspace,
                storage,
                "pv-land",
                auth_client,
                django_capture_on_commit_callbacks,
            )
        e1 = environment.environment_version_id
        _uncache(environment)
        with patch(START_WORKFLOW):
            nudge = _turn(environment, nudge, storage)

        assert nudge.state == "applied", (nudge.error, nudge.summary)
        assert environment.environment_version_id != e1
        assert environment.agent_version_id == a2.id
        assert nudge.summary["agent_not_moved"] == "validating"
        job = _move_job(environment, a3)
        assert job.payload["metadata"]["validation_move"]["caller"] == "land"
        assert job.environment_version_id == environment.environment_version_id

    def test_a_ready_pairing_moves_at_once(self, user, workspace, storage):
        environment, a1, a2 = _with_a2(user, workspace, storage, "pv-move-ready")
        _ready(
            validation.start_validation(
                environment,
                a2,
                environment.environment_version,
                environment.suite_version,
                purpose="recheck",
            ).bundle
        )

        assert _activate_on(environment.id, a2, explicit=True, user=user) == (
            "activated"
        )
        environment.refresh_from_db()
        assert environment.agent_version_id == a2.id

    def test_the_hand_move_route_answers_202_with_the_validation(
        self, user, workspace, storage, auth_client
    ):
        environment, a1, a2 = _with_a2(user, workspace, storage, "pv-move-route")

        response = auth_client.post(
            f"/simulate/api/harness-environments/{environment.id}"
            f"/versions/agent/{a2.id}/default/",
            {"expected_default_id": str(a1.id)},
            format="json",
        )

        assert response.status_code == 202, response.content
        body = response.json()
        assert body["validation"]["state"] == "validating"
        assert body["validation"]["purpose"] == "agent_move"
        assert {"agent", "environment", "suite"} <= set(body["versions"])
        [waiting] = [
            one for one in body["versions"]["agent"] if one["id"] == str(a2.id)
        ]
        assert waiting["activation_outcome"] == "validating"

    @pytest.mark.parametrize("caller", ["activation", "hand", "no_change", "land"])
    def test_after_move_steps_replayed_per_caller(
        self, user, workspace, storage, caller
    ):
        environment, a1, a2 = _with_a2(user, workspace, storage, f"pv-after-{caller}")
        nudge = _nudge(environment, a1, a2, user)
        move = validation.agent_move(
            caller, environment, a2, user=user, nudge=nudge, explicit=False
        )

        with patch(
            "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
        ) as nudged:
            outcome = validation.complete_move(
                environment,
                move,
                pair=(environment.environment_version_id, environment.suite_version_id),
            )

        environment.refresh_from_db()
        assert outcome == "activated"
        assert environment.agent_version_id == a2.id
        assert _activations(a2)[str(environment.id)] == "activated"
        assert nudged.called == (caller in ("activation", "hand"))
        hand_moved = (environment.payload["metadata"].get(HAND_MOVE_KEY) or {}).get(
            "to"
        )
        assert (hand_moved == str(a2.id)) == (caller != "activation")
        stamped = environment.environment_version.agent_version_id == a2.id
        assert stamped == (caller == "no_change")

    @pytest.mark.parametrize(
        ("moved", "expected"),
        [
            ("agent", "superseded"),
            ("environment", "validating"),
            ("suite", "validating"),
        ],
    )
    def test_success_guard_checks_three_defaults(
        self, user, workspace, storage, moved, expected
    ):
        environment, a1, a2 = _with_a2(user, workspace, storage, f"pv-guard-{moved}")
        move = validation.agent_move("hand", environment, a2, user=user)
        pair = [environment.environment_version_id, environment.suite_version_id]
        if moved == "agent":
            move["from_agent_version_id"] = str(a2.id)
        else:
            pair[0 if moved == "environment" else 1] = a1.id

        outcome = validation.complete_move(environment, move, pair=tuple(pair))

        environment.refresh_from_db()
        assert outcome == expected
        assert environment.agent_version_id == a1.id
        if moved == "agent":
            # A newer choice of agent stands: nothing validates the older one again.
            assert not running_validations(environment)

    def test_superseded_move_revalidates_once(self, user, workspace, storage):
        environment, a1, a2 = _with_a2(user, workspace, storage, "pv-superseded")
        move = validation.agent_move("hand", environment, a2, user=user)
        stale = (a1.id, environment.suite_version_id)

        assert validation.complete_move(environment, move, pair=stale) == "validating"
        again = _move_job(environment, a2).payload["metadata"]["validation_move"]
        assert again["revalidated"] is True

        assert validation.complete_move(environment, again, pair=stale) == "superseded"
        assert (
            HostedHarnessJob.no_workspace_objects.filter(
                payload__metadata__validation_move__agent_version_id=str(a2.id)
            ).count()
            == 1
        )

    def test_failed_pairing_keeps_the_old_agent_and_records_why(
        self, user, workspace, storage
    ):
        environment, a1, a2 = _with_a2(user, workspace, storage, "pv-move-failed")
        _activate_on(environment.id, a2, explicit=True, user=user)
        job = _move_job(environment, a2)

        assert (
            _end(job, state="failed", code="authoring_runtime_validation_failed")
            == "failed"
        )

        environment.refresh_from_db()
        assert environment.agent_version_id == a1.id
        assert _activations(a2)[str(environment.id)] == "pair_failed"

    def test_a_passed_validation_moves_the_default(self, user, workspace, storage):
        environment, a1, a2 = _with_a2(user, workspace, storage, "pv-move-passed")
        move_agent_default(environment, a2.id, expected_default_id=a1.id, user=user)
        job = _move_job(environment, a2)
        _ready(job.assembled_bundle)

        assert _end(job, state="completed") == "ready"

        environment.refresh_from_db()
        assert environment.agent_version_id == a2.id

    def test_validating_counts_as_waiting(self, user, workspace, storage):
        environment, a1, a2 = _with_a2(user, workspace, storage, "pv-waiting")
        runtime = HarnessAgentVersionRuntime.no_workspace_objects.get(agent_version=a2)
        runtime.agent_payload = {
            **(runtime.agent_payload or {}),
            "requested_by_environments": [str(environment.id)],
        }
        runtime.save(update_fields=["agent_payload"])
        _activate_on(environment.id, a2, explicit=True, user=user)
        runtime.preflight_report = {
            "activation": [
                {"environment_id": str(environment.id), "outcome": "validating"}
            ]
        }
        runtime.save(update_fields=["preflight_report"])

        assert _still_waiting(runtime, by_environment="another") is True
        HostedHarnessJob.no_workspace_objects.filter(
            id=_move_job(environment, a2).id
        ).update(state="failed")
        assert _still_waiting(runtime, by_environment="another") is False

    def test_fix_candidate_default_accepts_validating(self):
        from types import SimpleNamespace

        candidate = SimpleNamespace(fix_run=SimpleNamespace(environment=None))

        with (
            patch.object(agent_fix_runs, "require_shippable"),
            patch.object(agent_fix_runs, "record_override") as recorded,
            patch.object(agent_fix_runs, "agent_kind") as kind,
        ):
            kind.return_value.make_default.return_value = "validating"
            outcome = agent_fix_runs.make_candidate_default(candidate, user=None)

        assert outcome == "validating"
        recorded.assert_called_once()


def _a3(environment, a1, *, certified=False):
    """A third checked version; ``certified``: its pairing with the defaults is too."""
    a3 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v3"})
    HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a3).update(
        preflight_status="passed"
    )
    if certified:
        _ready(
            validation.start_validation(
                environment,
                a3,
                environment.environment_version,
                environment.suite_version,
                purpose="recheck",
            ).bundle
        )
    return a3


class TestNewerChoiceWins:
    def test_an_older_waiting_move_never_overrides_a_later_hand_move(
        self, user, workspace, storage
    ):
        environment, a1, a2 = _with_a2(user, workspace, storage, "pv-newer-direct")
        move_agent_default(environment, a2.id, expected_default_id=a1.id, user=user)
        waiting = _move_job(environment, a2)
        a3 = _a3(environment, a1, certified=True)

        move_agent_default(environment, a3.id, expected_default_id=a1.id, user=user)
        _ready(waiting.assembled_bundle)
        _end(waiting, state="completed")

        environment.refresh_from_db()
        assert environment.agent_version_id == a3.id
        assert _activations(a2)[str(environment.id)] == "superseded"

    def test_of_two_waiting_moves_the_later_one_lands(self, user, workspace, storage):
        environment, a1, a2 = _with_a2(user, workspace, storage, "pv-newer-waiting")
        a3 = _a3(environment, a1)
        move_agent_default(environment, a2.id, expected_default_id=a1.id, user=user)
        move_agent_default(environment, a3.id, expected_default_id=a1.id, user=user)

        settle_validations(environment)

        environment.refresh_from_db()
        assert environment.agent_version_id == a3.id
        assert _activations(a2)[str(environment.id)] == "superseded"

    def test_keeping_the_default_by_hand_drops_a_waiting_move(
        self, user, workspace, storage
    ):
        environment, a1, a2 = _with_a2(user, workspace, storage, "pv-newer-keep")
        move_agent_default(environment, a2.id, expected_default_id=a1.id, user=user)

        move_agent_default(environment, a1.id, expected_default_id=a1.id, user=user)
        settle_validations(environment)

        environment.refresh_from_db()
        assert environment.agent_version_id == a1.id

    def test_a_background_move_turned_down_meanwhile_does_not_land(
        self, user, workspace, storage
    ):
        environment, a1, a2 = _with_a2(user, workspace, storage, "pv-newer-turned")
        move = validation.agent_move("activation", environment, a2, explicit=False)
        runtime = HarnessAgentVersionRuntime.no_workspace_objects.get(agent_version=a2)
        runtime.agent_payload = {
            **(runtime.agent_payload or {}),
            "requested_at": {str(environment.id): "2026-01-01T00:00:00+00:00"},
        }
        runtime.save(update_fields=["agent_payload"])
        # Moved by hand after it was asked for, while it validated.
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload={
                **environment.payload,
                "metadata": {
                    **environment.payload["metadata"],
                    HAND_MOVE_KEY: {
                        "at": "2999-01-01T00:00:00+00:00",
                        "to": str(a1.id),
                    },
                },
            }
        )
        environment.refresh_from_db()

        outcome = validation.complete_move(
            environment,
            move,
            pair=(environment.environment_version_id, environment.suite_version_id),
        )

        environment.refresh_from_db()
        assert outcome == "default_moved_by_hand"
        assert environment.agent_version_id == a1.id

    def test_a_refused_move_after_validating_is_an_outcome_not_an_error(
        self, user, workspace, storage
    ):
        environment, a1, a2 = _with_a2(user, workspace, storage, "pv-move-refused")
        move = validation.agent_move("hand", environment, a2, user=user)

        with patch(
            "simulate.services.harness_versioning.versions.move_agent",
            side_effect=HostedHarnessError(
                "rollback_unavailable", "no bundle", status_code=409
            ),
        ):
            outcome = validation.complete_move(
                environment,
                move,
                pair=(environment.environment_version_id, environment.suite_version_id),
            )

        environment.refresh_from_db()
        assert outcome == "rollback_unavailable"
        assert environment.agent_version_id == a1.id

    def test_a_move_onto_a_validation_that_just_settled_starts_a_fresh_one(
        self, user, workspace, storage
    ):
        environment = _pending(user, workspace, storage, "pv-carry-race")
        settled = _start(environment).job
        _end(settled, state="failed", code="sandbox_disappeared")
        move = validation.agent_move(
            "hand", environment, environment.agent_version, user=user
        )

        # Found running, then settled before the move was carried onto it.
        with patch.object(validation, "live_validation", return_value=settled):
            started = _start(environment, purpose="agent_move", move=move)

        assert started.job.id != settled.id
        assert _job_metadata(started.job)["validation_move"] == move
        assert "validation_move" not in _job_metadata(settled)


class TestRetriedMoves:
    def test_an_infrastructure_ending_validates_the_move_once_more(
        self, user, workspace, storage
    ):
        environment, a1, a2 = _with_a2(user, workspace, storage, "pv-retry-move")
        move_agent_default(environment, a2.id, expected_default_id=a1.id, user=user)
        first = _move_job(environment, a2)

        assert _end(first, state="failed", code="sandbox_disappeared") == "retry"

        [again] = running_validations(environment)
        assert again.id != first.id
        assert _job_metadata(again)["validation_move"]["restarts"] == 1
        assert _activations(a2)[str(environment.id)] == "validating"
        settle_validations(environment)
        environment.refresh_from_db()
        assert environment.agent_version_id == a2.id

    def test_a_move_whose_retry_also_ends_early_is_asked_again(
        self, user, workspace, storage
    ):
        from simulate.services.harness_agent_changes import _settled

        environment, a1, a2 = _with_a2(user, workspace, storage, "pv-retry-twice")
        _activate_on(environment.id, a2, explicit=True, user=user)
        _end(_move_job(environment, a2), state="failed", code="sandbox_disappeared")

        [again] = running_validations(environment)
        _end(again, state="failed", code="sandbox_disappeared")

        assert not running_validations(environment)
        assert _activations(a2)[str(environment.id)] == "validation_retry"
        assert not _settled({"outcome": "validation_retry"})
        assert _settled({"outcome": "validation_retry", "attempts": 3})


class TestFailedLanding:
    def test_a_failed_landing_puts_the_environment_version_back(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        settings,
        django_capture_on_commit_callbacks,
    ):
        from simulate.tests.test_harness_versioning_rebuild import _offered, _turn

        settings.HARNESS_VERSIONING_NUDGES_ENABLED = True
        with patch.object(validation, "can_validate", return_value=False):
            environment, nudge, a2, a3 = _offered(
                user,
                workspace,
                storage,
                "pv-land-failed",
                auth_client,
                django_capture_on_commit_callbacks,
            )
        e1 = environment.environment_version_id
        _uncache(environment)
        with patch(START_WORKFLOW):
            _turn(environment, nudge, storage)
        environment.refresh_from_db()
        assert environment.environment_version_id != e1

        assert settle_validations(environment, passed=False) == ["failed"]

        environment.refresh_from_db()
        assert (environment.agent_version_id, environment.environment_version_id) == (
            a2.id,
            e1,
        )
        assert _activations(a3)[str(environment.id)] == "pair_failed"


class TestNotUserRuns:
    def test_a_validation_job_is_not_a_run_anywhere_a_user_looks(
        self, user, workspace, storage
    ):
        from simulate.selectors.runs import (
            user_environment_runs,
            user_executions,
            user_runs,
        )
        from simulate.services.harness_environment import annotate_for_list

        environment = _pending(user, workspace, storage, "pv-not-a-run")
        executions = user_executions().count()

        job = _start(environment).job

        assert not user_environment_runs(environment).exists()
        assert not HostedHarnessJob.no_workspace_objects.filter(
            user_runs(), id=job.id
        ).exists()
        listed = annotate_for_list(
            HostedHarnessJob.no_workspace_objects.filter(id=environment.id)
        ).get()
        assert listed.simulation_run_count == 0
        assert user_executions().count() == executions


class TestReconcileWithoutSandbox:
    def test_a_finished_run_whose_attempt_never_got_a_sandbox_is_left_as_is(
        self, user, workspace, storage, gateway, monkeypatch
    ):
        from simulate.services.hosted_harness import register_attempt
        from simulate.tests.test_harness_versioning_runs import _run

        environment = _environment(user, workspace, "pv-no-sandbox", storage)
        run = _run(environment)
        attempt = register_attempt(
            run.id, endpoint_base_url="https://platform.example"
        ).attempt
        HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(
            state=HostedHarnessJob.State.CANCELED
        )

        def never(_attempt):
            raise AssertionError("an attempt with no sandbox has nothing to inspect")

        monkeypatch.setattr(gateway, "inspect", never)
        polled = gateway.reconcile_completed(attempt)

        assert polled.state == "canceled"


def _age(job, minutes, *, launched=None):
    """The check was asked for ``minutes`` ago and its sandbox launched ``launched``
    minutes ago (as long ago when absent)."""
    from datetime import timedelta

    from django.utils import timezone

    from simulate.models import HostedHarnessAttempt

    now = timezone.now()
    HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
        created_at=now - timedelta(minutes=minutes)
    )
    HostedHarnessAttempt.no_workspace_objects.filter(job_id=job.id).update(
        created_at=now - timedelta(minutes=minutes if launched is None else launched)
    )
    job.refresh_from_db()


class TestDeadline:
    def test_a_check_past_its_deadline_is_stopped_and_fails_the_pairing(
        self,
        user,
        workspace,
        storage,
        gateway,
        monkeypatch,
        django_capture_on_commit_callbacks,
    ):
        from simulate.tests.test_harness_versioning_runs import _run

        environment = _pending(user, workspace, storage, "pv-deadline")
        job = _start(environment).job
        attempt = _launch(gateway, job)
        monkeypatch.setattr(
            gateway,
            "_delete_and_record",
            lambda attempt, **_: record_cleanup(
                attempt.id, provider_ref=str(attempt.provider_ref), verified_absent=True
            ),
        )
        _age(job, 46)

        with django_capture_on_commit_callbacks(execute=True):
            done = gateway.reconcile_completed(attempt)

        assert done.state == "canceled"
        assert done.cancel_reason == validation.CHECK_TIMED_OUT
        assert _job_metadata(job)["validation_outcome"] == "failed"
        row = _row(environment)
        assert (row.state, row.error["code"], row.error["source"]) == (
            "failed",
            "validation_timed_out",
            "validation",
        )
        with pytest.raises(HostedHarnessError) as refused:
            _run(environment, key="after-timeout")
        assert refused.value.code == "pair_failed"
        assert refused.value.details["reason_code"] == "validation_timed_out"
        assert "did not finish within 45 minutes" in refused.value.message
        again = _start(environment)
        assert again.state == "validating" and again.job.id != job.id

    def test_a_check_within_its_deadline_keeps_running(self, user, workspace, storage):
        environment = _pending(user, workspace, storage, "pv-in-time")
        job = _start(environment).job
        _age(job, 30)

        assert validation.overdue(job) is False
        _age(job, 46)
        assert validation.overdue(job) is True

    def test_time_queued_before_the_sandbox_launched_does_not_count(
        self, user, workspace, storage, gateway
    ):
        environment = _pending(user, workspace, storage, "pv-queued")
        job = _start(environment).job
        _launch(gateway, job)
        _age(job, 60, launched=10)

        assert validation.overdue(job) is False
        _age(job, 60, launched=46)
        assert validation.overdue(job) is True

    def test_a_user_cancel_is_not_a_timed_out_check(self, user, workspace, storage):
        environment = _pending(user, workspace, storage, "pv-user-cancel")
        job = _start(environment).job
        _age(job, 46)
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
            cancel_reason="user_canceled", cancel_requested_at=job.created_at
        )
        job.refresh_from_db()

        assert validation.overdue(job) is False


class TestRefusals:
    def test_a_validating_refusal_says_when_to_retry(self, user, workspace, storage):
        from simulate.tests.test_harness_versioning_runs import _run

        environment = _pending(user, workspace, storage, "pv-retry-after")
        job = _start(environment).job
        _age(job, 5)

        with pytest.raises(HostedHarnessError) as refused:
            _run(environment)

        body = refused.value.as_dict()
        assert body["error"] == "pair_validating" and body["retryable"] is True
        assert 39 * 60 <= body["retry_after"] <= 40 * 60
        assert body["check"]["job_id"] == str(job.id)
        assert "in 40 minutes" in body["message"]

    def test_a_failed_refusal_names_the_reason_and_the_actions(
        self, user, workspace, storage
    ):
        from simulate.tests.test_harness_versioning_runs import _run

        environment = _pending(user, workspace, storage, "pv-failed-reason")
        _failed(
            _row(environment),
            source="validation",
            message="Traceback: internal detail",
            failed_actions=[{"action": "issue_refund", "status": "failed"}],
        )

        with pytest.raises(HostedHarnessError) as refused:
            _run(environment)

        body = refused.value.as_dict()
        assert body["error"] == "pair_failed" and body["retryable"] is False
        assert body["reason_code"] == "authoring_runtime_validation_failed"
        assert "issue_refund" in body["reason"]
        assert "Traceback" not in body["message"]


class TestOneCheckPerBundle:
    def test_each_check_of_a_bundle_has_its_own_fixed_key(
        self, user, workspace, storage
    ):
        environment = _pending(user, workspace, storage, "pv-key")
        first = _start(environment)
        bundle = first.bundle

        assert first.job.idempotency_key == f"validation:{bundle.id}:1"
        assert _start(environment).job.id == first.job.id
        _end(first.job, state="failed", code="certify_incomplete")
        second = _start(environment)
        assert second.job.idempotency_key == f"validation:{bundle.id}:2"

    def test_a_passed_result_for_the_same_hash_is_reused(
        self, user, workspace, storage
    ):
        environment = _pending(user, workspace, storage, "pv-reuse")
        first = _start(environment)
        _ready(first.bundle)
        _end(first.job, state="completed")

        again = _start(environment)
        assert again.ready and again.job is None
        assert (
            HostedHarnessJob.no_workspace_objects.filter(
                assembled_bundle_id=first.bundle.id,
                payload__metadata__certify_only=True,
            ).count()
            == 1
        )


class TestCheckLogs:
    def test_a_finished_move_logs_its_pairing_hash_result_and_duration(
        self, user, workspace, storage
    ):
        from structlog.testing import capture_logs

        environment = _pending(user, workspace, storage, "pv-logs")
        job = _start(environment).job
        _age(job, 3)

        with capture_logs() as logs:
            _end(job, state="failed", code="authoring_runtime_validation_failed")

        finished = next(
            one for one in logs if one["event"] == "harness_pair_validation_finished"
        )
        assert finished["environment_id"] == str(environment.id)
        assert finished["agent_version_id"] == str(environment.agent_version_id)
        assert finished["environment_version_id"] == str(
            environment.environment_version_id
        )
        assert finished["assembly_key"] == _row(environment).assembly_key
        assert finished["outcome"] == "failed"
        assert finished["duration_seconds"] >= 180


_CHECK = "Generated environment failed real-runtime validation after bounded repair: "
# The cause the gateway recorded for ALK's own fingerprint check failing the seed.
WORLD_SEED = (
    "ProcessRuntimeError: seed/seed_failed (world-db): canonical World IR could not be "
    "compiled or applied: source_model_fingerprint_mismatch"
)


def _guest(error, agent=(), phase="runtime"):
    """The guest's entrypoint log as ALK writes it for a failed check: the phase line,
    the agent's output after it, then the final error and the output again."""
    output = "".join(f"{line}\n" for line in agent)
    return (
        "certify-only: source model rebuilt for this agent version's source\n"
        f"runtime validation: {phase}: {error}\n{output}"
        "runtime validation: repair failed (RuntimeValidationError)\n"
        f"RuntimeValidationError: {error}\n{output}"
    )


def _failure(error, agent=(), phase="runtime", tail=None):
    return {
        "code": "authoring_runtime_validation_failed",
        "message": _CHECK + error,
        "details": {
            "guest_log_tail": _guest(error, agent, phase) if tail is None else tail
        },
    }


_AGENT_CRASH = (
    "ProcessRuntimeError: depends_on/spawn_failed (agent): agent: exited before"
    " readiness probe passed; last output:"
)


class TestCheckUnavailable:
    @pytest.mark.parametrize(
        ("error", "cause_code"),
        [
            # ALK's own fingerprint check failed the canonical seed.
            (WORLD_SEED, "seed_failed"),
            # The agent's own migration or seed content, applied to world-db.
            (
                "seed/seed_failed (world-db): 0042_orders.sql: exited 1: syntax error",
                None,
            ),
            (
                "seed/seed_failed (world-db): canonical World IR could not be compiled or"
                " applied: source_model_fingerprint_mismatch, unknown_column",
                None,
            ),
            ("baseline/seed_failed (world-db): world-db: sentinel check failed", None),
            # Infrastructure at every raise site in ALK.
            (
                "render/store_statement_failed (redis): redis: store rejected a command",
                "store_statement_failed",
            ),
            ("reset/store_statement_failed: copy failed", "store_statement_failed"),
            (
                "admission/resource_capacity_unknown: both bounds are unavailable",
                "resource_capacity_unknown",
            ),
            (
                "admission/resource_capacity_unavailable: no world fits",
                "resource_capacity_unavailable",
            ),
            # ALK bugs.
            (
                "render/internal_unknown_placeholder (agent): {{X}}",
                "internal_unknown_placeholder",
            ),
            # A process ALK adds to the world, by its exact name.
            (
                "depends_on/depends_on_timeout (world-db): not ready",
                "depends_on_timeout",
            ),
            ("spawn/spawn_failed (world-db): exited 1", "spawn_failed"),
            ("spawn/spawn_failed (tool-proxy): exited 1", "spawn_failed"),
            ("build/build_failed (mcp-toolbox): exited 1", "build_failed"),
            # The agent's own processes and services, whatever they are called.
            (
                "depends_on/depends_on_timeout (agent): agent: started_check did not pass"
                " within 180s; last output:",
                None,
            ),
            ("spawn/spawn_failed (world-api): exited 1", None),
            ("spawn/spawn_failed (tool-proxy-v2): exited 1", None),
            (_AGENT_CRASH, None),
            ("build/build_failed (agent): pip failed", None),
            ("RuntimeError: boom render/store_statement_failed: x", None),
            ("the agent did not answer the test message", None),
            ("", None),
        ],
    )
    def test_only_a_failure_alk_owns_is_ours(self, error, cause_code):
        assert validation.platform_failure(_failure(error)) == cause_code

    @pytest.mark.parametrize(
        "printed",
        [
            "runtime validation: infrastructure: x",
            "ProcessRuntimeError: render/store_statement_failed: x",
            "RuntimeValidationError: a/internal_bug: x",
            "ProcessRuntimeError: spawn/spawn_failed (world-db): x",
            f"runtime validation: runtime: {WORLD_SEED}",
        ],
    )
    def test_what_the_agent_prints_is_never_read(self, printed):
        failure = _failure(_AGENT_CRASH, agent=["Starting agent", printed])

        assert validation.platform_failure(failure) is None

    def test_only_the_first_phase_line_counts_and_only_before_the_agent_s_output(self):
        forged = _guest(_AGENT_CRASH, ["x"]).replace(
            "certify-only",
            f"x; last output:\nruntime validation: runtime: {WORLD_SEED}\n",
        )
        # A repaired phase's error can carry the agent's replies; a later line is unknown.
        repaired = _guest(WORLD_SEED, phase="scenarios")
        cut = "x" * 8000 + _guest(WORLD_SEED)

        for tail in (forged, repaired, cut):
            assert validation.platform_failure(_failure(WORLD_SEED, tail=tail)) is None
        assert validation.platform_failure(_failure(WORLD_SEED, tail="")) is None

    def test_an_infrastructure_phase_is_the_agent_s(self):
        certificate = (
            "ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"
        )
        alone = _failure(certificate, phase="infrastructure")
        # ALK retries this phase: a later attempt can be the agent's own crash.
        then_crash = _failure(
            _AGENT_CRASH,
            tail=(
                f"runtime validation: infrastructure: {certificate}\n"
                + _guest(_AGENT_CRASH, ["KeyError: 'PORT'"])
            ),
        )
        # The agent's own migration printing a certificate error through psql.
        migration = _failure(
            "ProcessRuntimeError: seed/seed_failed (world-db): 0007.sql: exited 1:"
            " CERTIFICATE_VERIFY_FAILED",
            phase="infrastructure",
        )
        # The agent failed its own TLS at boot: ALK sets the phase from its output.
        agent_tls = _failure(
            _AGENT_CRASH,
            agent=["ssl.SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED]"],
            phase="infrastructure",
        )
        world = _failure(
            "spawn/spawn_failed (world-db): exited 1", phase="infrastructure"
        )

        for failure in (alone, then_crash, migration, agent_tls, world):
            assert validation.platform_failure(failure) is None

    def test_a_failed_action_probe_is_the_agent_s(self):
        assert (
            validation.platform_failure(
                _failure(WORLD_SEED), [{"action": "issue_refund"}]
            )
            is None
        )
        assert validation.platform_failure(None) is None

    def test_a_test_world_failure_holds_no_run_back_and_reads_unavailable(
        self, user, workspace, storage
    ):
        from structlog.testing import capture_logs

        from simulate.services import harness_readiness
        from simulate.tests.test_harness_versioning_runs import _run

        environment = _pending(user, workspace, storage, "pv-ours")
        job = _start(environment).job
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
            state="failed",
            failure=_failure(WORLD_SEED),
        )

        with capture_logs() as logs:
            assert validation.finish_validation(job.id) == "unavailable"

        row = _row(environment)
        assert row.state == "pending"
        assert row.error["last_attempt"]["cause"] == "seed_failed"
        view = harness_readiness.readiness_report(
            harness_readiness.resolve_pairing(environment)
        )["validation"]
        assert view["state"] == "check_unavailable"
        assert view["error"]["reason"] == validation.UNAVAILABLE_REASON
        assert "source_model" not in str(view)
        assert any(
            one["event"] == "harness_pair_check_unavailable"
            and one["cause"] == "seed_failed"
            and one["environment_id"] == str(environment.id)
            for one in logs
        )
        assert _run(environment, key="after-ours").id

    def test_the_gateway_never_marks_a_test_world_failure_failed(
        self, user, workspace, storage
    ):
        environment = _pending(user, workspace, storage, "pv-ours-mark")
        job = _start(environment).job
        payload = dict(job.payload)
        payload["metadata"]["assembly"]["outcome"] = "miss"
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(payload=payload)
        job.refresh_from_db()

        assembly.mark_failed(job, _failure(WORLD_SEED))

        assert _row(environment).state == "pending"
        assembly.mark_failed(job, _failure(_AGENT_CRASH))
        assert _row(environment).state == "failed"

    def test_an_ordinary_run_s_test_world_failure_still_fails_its_bundle(
        self, user, workspace, storage
    ):
        environment = _pending(user, workspace, storage, "pv-ours-run")
        job = _start(environment).job
        payload = dict(job.payload)
        payload["metadata"]["assembly"]["outcome"] = "miss"
        del payload["metadata"]["certify_only"]
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(payload=payload)
        job.refresh_from_db()

        assembly.mark_failed(job, _failure(WORLD_SEED))

        row = _row(environment)
        assert row.state == "failed"
        assert row.error["run_id"] == str(job.id)

    def test_an_agent_failure_still_fails_the_pair_and_refuses_runs(
        self, user, workspace, storage
    ):
        from simulate.tests.test_harness_versioning_runs import _run

        environment = _pending(user, workspace, storage, "pv-agent")
        job = _start(environment).job
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
            state="failed",
            failure={
                "code": "authoring_runtime_validation_failed",
                "message": _CHECK + "spawn/spawn_failed (agent): ModuleNotFoundError",
            },
        )

        assert validation.finish_validation(job.id) == "failed"
        with pytest.raises(HostedHarnessError) as refused:
            _run(environment, key="after-agent")
        assert refused.value.code == "pair_failed"

    def test_a_move_whose_check_was_unavailable_lands_as_before_checks(
        self, user, workspace, storage
    ):
        environment, a1, a2 = _with_a2(user, workspace, storage, "pv-ours-move")
        move_agent_default(environment, a2.id, expected_default_id=a1.id, user=user)
        job = _move_job(environment, a2)
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
            state="failed",
            failure=_failure(WORLD_SEED),
        )

        assert validation.finish_validation(job.id) == "unavailable"

        environment.refresh_from_db()
        assert environment.agent_version_id == a2.id


def _template(settings, build):
    settings.HOSTED_SANDBOX_PROVIDER = "e2b"
    settings.ALK_E2B_TEMPLATE_REFERENCE = "alk-hosted"
    settings.ALK_E2B_TEMPLATE_BUILD_ID = build


def _checks(environment):
    return HostedHarnessJob.no_workspace_objects.filter(
        assembled_bundle_id=_row(environment).id, payload__metadata__certify_only=True
    ).count()


def _move(environment):
    return _start(environment, purpose="agent_move")


def _settle_unavailable(job, failure):
    HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
        state="failed", failure=failure
    )
    assert validation.finish_validation(job.id) == "unavailable"


class TestUnavailableReuse:
    def test_a_repeating_cause_is_reused_by_a_move_until_the_template_changes(
        self, user, workspace, storage, settings
    ):
        _template(settings, "b1")
        environment = _pending(user, workspace, storage, "pv-reuse")
        job = _move(environment).job
        _settle_unavailable(job, _failure(WORLD_SEED))

        again = _move(environment)

        assert again.state == "check_unavailable" and again.ready
        assert again.job is None and _checks(environment) == 1
        assert again.as_view()["error"] == {
            "code": "check_unavailable",
            "reason": validation.UNAVAILABLE_REASON,
        }
        assert again.as_view()["job_id"] == str(job.id)
        _template(settings, "b2")
        assert _move(environment).state == "validating"
        assert _checks(environment) == 2

    def test_an_alk_bug_is_reused_too(self, user, workspace, storage, settings):
        _template(settings, "b1")
        environment = _pending(user, workspace, storage, "pv-reuse-internal")
        bug = "ProcessRuntimeError: render/internal_invariant_violated: x"
        _settle_unavailable(_move(environment).job, _failure(bug))

        assert _move(environment).state == "check_unavailable"
        assert _checks(environment) == 1

    def test_a_re_check_always_checks_afresh(self, user, workspace, storage, settings):
        _template(settings, "b1")
        environment = _pending(user, workspace, storage, "pv-reuse-recheck")
        _settle_unavailable(_move(environment).job, _failure(WORLD_SEED))

        assert _start(environment, purpose="recheck").state == "validating"
        assert _checks(environment) == 2

    def test_the_template_is_the_one_the_check_launched_on(
        self, user, workspace, storage, settings
    ):
        _template(settings, "old")
        environment = _pending(user, workspace, storage, "pv-reuse-launch")
        job = _move(environment).job
        _template(settings, "fixed")
        _settle_unavailable(job, _failure(WORLD_SEED))

        assert _row(environment).error["last_attempt"]["template"] == "alk-hosted:old"
        assert _move(environment).state == "validating"
        assert _checks(environment) == 2

    def test_a_move_lands_on_a_reused_result_without_a_check(
        self, user, workspace, storage, settings
    ):
        _template(settings, "b1")
        environment, a1, a2 = _with_a2(user, workspace, storage, "pv-reuse-move")
        move_agent_default(environment, a2.id, expected_default_id=a1.id, user=user)
        job = _move_job(environment, a2)
        _settle_unavailable(job, _failure(WORLD_SEED))
        environment.refresh_from_db()
        move_agent_default(environment, a1.id, expected_default_id=a2.id, user=user)
        environment.refresh_from_db()

        move_agent_default(environment, a2.id, expected_default_id=a1.id, user=user)

        environment.refresh_from_db()
        assert environment.agent_version_id == a2.id
        assert (
            HostedHarnessJob.no_workspace_objects.filter(
                assembled_bundle_id=job.assembled_bundle_id,
                payload__metadata__certify_only=True,
            ).count()
            == 1
        )

    def test_any_other_cause_checks_again(self, user, workspace, storage, settings):
        _template(settings, "b1")
        environment = _pending(user, workspace, storage, "pv-reuse-capacity")
        capacity = "ProcessRuntimeError: admission/resource_capacity_unavailable: no world fits"
        _settle_unavailable(_move(environment).job, _failure(capacity))
        assert "template" not in _row(environment).error["last_attempt"]

        assert _move(environment).state == "validating"
        assert _checks(environment) == 2


def _reading(tools):
    from simulate.tests.test_agent_setup_service import source_agent

    document = source_agent(tools=tools)
    document["behaviour"][0]["tools"] = [tool["name"] for tool in tools]
    return json.dumps({"sdk_version": "0.2.3", "agent": document}).encode()


def _not_built_with(environment):
    """The environment version names no agent it was built with, so its default agent
    is not known to be that one and its own tools are what readiness compares."""
    type(environment.environment_version)._base_manager.filter(
        id=environment.environment_version_id
    ).update(agent_version=None)
    environment.refresh_from_db()
    return environment


def _runtime(environment):
    from simulate.models import HarnessAgentVersionRuntime

    return effective_runtime(
        environment,
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version=environment.agent_version
        ).first(),
    )


_EMAIL = [{"name": "send_email", "kind": "http", "parameters": None, "endpoint": None}]


@pytest.mark.usefixtures("tools_unread")
class TestAgentReading:
    def test_a_version_is_unread_until_a_document_is_stored(
        self, user, workspace, storage
    ):
        from simulate.services.agent_setup import store_agent

        environment = _pending(user, workspace, storage, "pv-unread")
        agent = environment.agent_version
        runtime = _runtime(environment)
        assert validation.unread(agent, runtime)

        document = json.loads(_reading(_EMAIL))
        store_agent(
            agent_version_id=agent.id,
            agent=document["agent"],
            sdk_version=document["sdk_version"],
        )

        assert not validation.unread(agent, runtime)

    def test_a_recheck_of_an_unread_version_reads_it_before_certifying(
        self, user, workspace, storage, gateway
    ):
        environment = _not_built_with(
            _pending(user, workspace, storage, "pv-read-first")
        )
        job = _start(environment).job

        _launch(gateway, job)

        assert _job_metadata(job)["agent_reading"] == "before_certify"
        command = gateway.client.sandbox.process.session_request.command
        assert command == assembly.agent_reading_command() + assembly.certify_command()

    def test_a_default_move_never_reads(self, user, workspace, storage, gateway):
        environment = _not_built_with(
            _pending(user, workspace, storage, "pv-move-no-read")
        )
        move = validation.agent_move(
            "hand", environment, environment.agent_version, user=None
        )
        job = _start(environment, purpose="agent_move", move=move).job

        _launch(gateway, job)

        assert "agent_reading" not in _job_metadata(job)
        command = gateway.client.sandbox.process.session_request.command
        assert command == assembly.certify_command()

    def test_a_certified_unread_pairing_starts_a_reading_only_check(
        self, user, workspace, storage, gateway
    ):
        environment = _not_built_with(
            _environment(user, workspace, "pv-read-only", storage)
        )
        assert _row(environment).state == "ready"

        started = _start(environment)
        attempt = _launch(gateway, started.job)

        assert started.state == "validating"
        assert _job_metadata(started.job)["agent_reading"] == "only"
        assert attempt.terminal_stage != "completed"
        command = gateway.client.sandbox.process.session_request.command
        assert command == assembly.agent_reading_command() + "true"

    def test_the_read_document_is_stored_and_the_check_settles(
        self,
        user,
        workspace,
        storage,
        gateway,
        monkeypatch,
        django_capture_on_commit_callbacks,
    ):
        from simulate.services.agent_setup import stored_agent

        environment = _not_built_with(
            _pending(user, workspace, storage, "pv-read-store")
        )
        job = _start(environment).job
        attempt = _launch(gateway, job)
        _ready(_row(environment))
        _exit(gateway, monkeypatch, 0, {assembly.AGENT_READING_PATH: _reading(_EMAIL)})

        with django_capture_on_commit_callbacks(execute=True):
            done = gateway.reconcile_completed(attempt)

        assert done.state == "completed"
        stored = stored_agent(environment.agent_version_id)
        assert [tool["name"] for tool in stored["tools"]] == ["send_email"]

    def test_a_failed_certify_still_stores_the_reading(
        self, user, workspace, storage, gateway, monkeypatch
    ):
        from simulate.services.agent_setup import stored_agent

        environment = _not_built_with(_pending(user, workspace, storage, "pv-read-78"))
        job = _start(environment).job
        attempt = _launch(gateway, job)
        _exit(gateway, monkeypatch, 78, {assembly.AGENT_READING_PATH: _reading(_EMAIL)})

        gateway.reconcile_completed(attempt)

        assert stored_agent(environment.agent_version_id) is not None

    def test_no_document_leaves_the_version_unread_and_settles(
        self,
        user,
        workspace,
        storage,
        gateway,
        monkeypatch,
        django_capture_on_commit_callbacks,
    ):
        environment = _not_built_with(
            _environment(user, workspace, "pv-read-none", storage)
        )
        started = _start(environment)
        attempt = _launch(gateway, started.job)
        _exit(gateway, monkeypatch, 0)

        with django_capture_on_commit_callbacks(execute=True):
            done = gateway.reconcile_completed(attempt)

        assert done.state == "completed"
        assert _job_metadata(started.job)["validation_outcome"] == "ready"
        assert _row(environment).state == "ready"
        assert validation.unread(environment.agent_version, _runtime(environment))

    def test_a_check_without_reading_never_stores_a_document(
        self, user, workspace, storage, gateway, monkeypatch
    ):
        from simulate.services.agent_setup import stored_agent

        environment = _not_built_with(
            _pending(user, workspace, storage, "pv-no-read-store")
        )
        move = validation.agent_move(
            "hand", environment, environment.agent_version, user=None
        )
        job = _start(environment, purpose="agent_move", move=move).job
        attempt = _launch(gateway, job)
        _ready(_row(environment))
        _exit(gateway, monkeypatch, 0, {assembly.AGENT_READING_PATH: _reading(_EMAIL)})

        gateway.reconcile_completed(attempt)

        assert stored_agent(environment.agent_version_id) is None

    def test_the_agent_the_environment_was_built_with_is_never_read(
        self, user, workspace, storage, gateway
    ):
        environment = _pending(user, workspace, storage, "pv-built-with")
        job = _start(environment).job

        _launch(gateway, job)

        assert "agent_reading" not in _job_metadata(job)
        command = gateway.client.sandbox.process.session_request.command
        assert command == assembly.certify_command()


def _recorded(domain, *, code="spawn_failed", process="tool-proxy", message=None):
    return {
        "domain": domain,
        "phase": "runtime",
        "stage": "spawn",
        "code": code,
        "process": process,
        "message": message or f"spawn/{code} ({process}): exited 1",
    }


class TestFailureRecord:
    """ALK's own record of why a check failed decides whose failure it is."""

    @pytest.mark.parametrize(
        ("domain", "expected"),
        [("world", "spawn_failed"), ("harness", "spawn_failed"), ("agent", None)],
    )
    def test_the_record_decides_whatever_the_log_says(self, domain, expected):
        # The log alone would read the opposite way for each.
        logged = _AGENT_CRASH if domain != "agent" else WORLD_SEED
        failure = _failure(logged)
        failure["details"]["harness_failure"] = _recorded(domain)

        assert validation.platform_failure(failure) == expected

    def test_without_a_record_the_log_is_read_as_before(self):
        assert validation.platform_failure(_failure(WORLD_SEED)) == "seed_failed"
        assert validation.platform_failure(_failure(_AGENT_CRASH)) is None

    def test_a_failed_probe_is_the_agents_even_with_a_record_of_ours(self):
        failure = _failure(WORLD_SEED)
        failure["details"]["harness_failure"] = _recorded("world")

        assert (
            validation.platform_failure(failure, [{"action": "issue_refund"}]) is None
        )

    def test_the_sandboxs_record_is_kept_and_its_line_replaces_the_logs(
        self, user, workspace, storage, gateway, monkeypatch
    ):
        environment = _pending(user, workspace, storage, "pv-record")
        job = _start(environment).job
        attempt = _launch(gateway, job)
        forged = "ProcessRuntimeError: render/internal_forged (world-db): forged by the agent"
        monkeypatch.setattr(
            gateway,
            "inspect",
            lambda _: {"exit_code": 78, "logs": forged, "process_logs": ""},
        )
        record = _recorded(
            "agent", process="agent", message="spawn/spawn_failed (agent): exited 1"
        )
        files = {assembly.FAILURE_RECORD_PATH: json.dumps(record).encode()}
        monkeypatch.setattr(
            gateway.client.sandbox.fs,
            "download_file",
            lambda path, timeout=None: files[path],
        )
        monkeypatch.setattr(
            gateway,
            "_delete_and_record",
            lambda attempt, *, retry_pending=False: record_cleanup(
                attempt.id,
                provider_ref=str(attempt.provider_ref),
                verified_absent=True,
                retry_pending=retry_pending,
            ),
        )

        gateway.reconcile_completed(attempt)

        attempt.refresh_from_db()
        failure = attempt.terminal_failure
        assert failure["details"]["harness_failure"] == record
        assert failure["message"].endswith("spawn/spawn_failed (agent): exited 1")
        assert "forged" not in failure["message"]

    def test_an_unreadable_record_is_none(self):
        class Files:
            def download_file(self, path, timeout=None):
                return b'{"domain": "ours"}'

        class Sandbox:
            fs = Files()

        assert assembly.failure_record(Sandbox()) is None

    def test_a_failed_pairing_shows_alks_line_and_never_the_agents_output(
        self, user, workspace, storage
    ):
        from simulate.services import harness_readiness

        environment = _pending(user, workspace, storage, "pv-record-shown")
        job = _start(environment).job
        failure = _failure(_AGENT_CRASH)
        failure["details"]["harness_failure"] = _recorded(
            "agent", process="agent", message="spawn/spawn_failed (agent): exited 1"
        )
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
            state="failed", failure=failure
        )

        assert validation.finish_validation(job.id) == "failed"
        report = harness_readiness.readiness_report(
            harness_readiness.resolve_pairing(environment)
        )

        assert report["validation"]["error"]["excerpt"] == (
            "spawn/spawn_failed (agent): exited 1"
        )
        start = next(row for row in report["rows"] if row["id"] == "start")
        assert {
            "check": "failed_check",
            "result": "spawn/spawn_failed (agent): exited 1",
        } in start["evidence"]

    def test_the_gateway_keeps_the_excerpt_on_the_failed_bundle(
        self, user, workspace, storage
    ):
        environment = _pending(user, workspace, storage, "pv-record-mark")
        job = _start(environment).job
        payload = dict(job.payload)
        payload["metadata"]["assembly"]["outcome"] = "miss"
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(payload=payload)
        job.refresh_from_db()
        failure = _failure(_AGENT_CRASH)
        failure["details"]["harness_failure"] = _recorded(
            "agent", process="agent", message="spawn/spawn_failed (agent): exited 1"
        )

        assembly.mark_failed(job, failure)

        row = _row(environment)
        assert row.state == "failed"
        assert row.error["excerpt"] == "spawn/spawn_failed (agent): exited 1"


class TestToolRoutingKept:
    def test_a_completed_check_keeps_the_observed_routing_on_its_bundle(
        self,
        user,
        workspace,
        storage,
        gateway,
        monkeypatch,
        django_capture_on_commit_callbacks,
    ):
        environment = _pending(user, workspace, storage, "pv-routing")
        job = _start(environment).job
        attempt = _launch(gateway, job)
        _ready(_row(environment))
        observed = {
            "tools": [{"tool": "lookup_order", "routed": True, "calls": 1}],
            "unobserved": [{"tool": "issue_refund", "reason": "changes_state"}],
        }
        _exit(
            gateway,
            monkeypatch,
            0,
            {assembly.TOOL_ROUTING_PATH: json.dumps(observed).encode()},
        )

        with django_capture_on_commit_callbacks(execute=True):
            gateway.reconcile_completed(attempt)

        assert _row(environment).certificate["tool_routing"] == observed

    def test_without_observations_the_certificate_is_unchanged(
        self, user, workspace, storage, gateway, monkeypatch
    ):
        environment = _pending(user, workspace, storage, "pv-routing-none")
        job = _start(environment).job
        attempt = _launch(gateway, job)
        _ready(_row(environment))
        _exit(gateway, monkeypatch, 0)

        gateway.reconcile_completed(attempt)

        assert "tool_routing" not in (_row(environment).certificate or {})
