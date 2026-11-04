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
from simulate.tests.test_harness_agent_versions import _a1, _create
from simulate.tests.test_harness_agent_versions import _environment as _agent_env
from simulate.tests.test_harness_agent_versions import (
    scheduled as scheduled,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_amend_archive import NAMES, _key
from simulate.tests.test_harness_nudges import (
    started as started,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_versioning_assembly import (
    _environment,
    _launch,
    _row,
    _uncache,
)
from simulate.tests.test_harness_versioning_assembly import (
    gateway as gateway,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_versioning_backfill import (
    storage as storage,  # noqa: F401 - fixture
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

    def test_exit_0_not_ready_is_certify_incomplete(
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
        assert row.state == "failed"
        assert row.error["source"] == "validation"

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

        assert _end(job, state="failed", code="certify_incomplete") == "failed"
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

    @pytest.mark.parametrize("moved", ["agent", "environment", "suite"])
    def test_success_guard_checks_three_defaults(self, user, workspace, storage, moved):
        environment, a1, a2 = _with_a2(user, workspace, storage, f"pv-guard-{moved}")
        move = validation.agent_move("hand", environment, a2, user=user)
        pair = [environment.environment_version_id, environment.suite_version_id]
        if moved == "agent":
            move["from_agent_version_id"] = str(a2.id)
        else:
            pair[0 if moved == "environment" else 1] = a1.id

        outcome = validation.complete_move(environment, move, pair=tuple(pair))

        environment.refresh_from_db()
        assert outcome == "validating"
        assert environment.agent_version_id == a1.id

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

        assert _end(job, state="failed", code="certify_incomplete") == "failed"

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
