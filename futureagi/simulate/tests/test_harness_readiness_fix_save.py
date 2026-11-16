"""Run readiness Fix autosave: Validate and Save of the builder's drafts, the re-check
of the new defaults, the verdict, the endings that did not save, and the manual-Save
guard while a fix waits on the person."""

import json
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from simulate.models import (
    HarnessEnvironmentVersion,
    HarnessNudge,
    HarnessSuiteVersion,
    HostedHarnessConversation,
    HostedHarnessJob,
    ReadinessFixRun,
)
from simulate.services import harness_readiness
from simulate.services.harness_readiness import fix_driver, fix_runs, fix_save
from simulate.services.harness_versioning import (
    archives,
    candidate_promotion,
    candidate_sets,
    candidates,
    drafts,
    validation,
)
from simulate.services.harness_versioning.assembly import CERTIFICATE, SEALED_MANIFEST
from simulate.services.hosted_harness import HostedHarnessError
from simulate.tests.test_harness_readiness import (
    FAILED_PROBE,
    PASSED,
    _certify,
    _pinned,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixtures
    readiness_on as readiness_on,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_readiness_fix_driver import (
    REFUND,
    _completed,
    _dispatched,
    _feed,
    _publish,
    _started,
)
from simulate.tests.test_harness_readiness_fix_driver import (  # noqa: F401 - fixture
    tasks as tasks,
)
from simulate.tests.test_harness_readiness_fix_run import (
    _broken,
    _chat,
    _closed_run,
    _draft,
)
from simulate.tests.test_harness_readiness_fix_run import (  # noqa: F401 - fixture
    fix_on as fix_on,
)
from simulate.tests.test_harness_versioning_assembly import _sealed_authoring
from simulate.tests.test_hosted_harness_conversation import (
    _put_conversation_checkpoint,
)

pytestmark = pytest.mark.django_db

ROWS = ("state", "tool_routing")
DRAFT_UNSAVED = "draft_unsaved"


def _settled(user, workspace, storage, capture, key, rows=ROWS):
    """A run whose one command's turn wrote the environment draft and settled."""
    from simulate.services.hosted_harness_gateway import (
        with_authoring_basis,
        with_draft_basis,
    )

    environment, run, message, conversation = _dispatched(
        user, workspace, storage, key, rows
    )
    token = drafts.compose_workspace(environment, conversation).token
    body = with_draft_basis(
        with_authoring_basis(
            _sealed_authoring(contract=REFUND),
            message.payload["_environment_revision"],
        ),
        token,
    )
    _key, digest = _put_conversation_checkpoint(conversation, storage, body)
    sequence = _feed(
        conversation,
        capture,
        _started(message),
        ("checkpoint_committed", {"digest": digest, "size": len(body)}),
        _completed(message),
    )
    with capture(execute=True):
        _publish(conversation, sequence)
    run.refresh_from_db()
    assert run.commands[0]["outcome"]["wrote"] is True
    environment.refresh_from_db()
    return environment, run


def _check(environment, capture=None):
    if capture is None:
        fix_driver.check(environment.id)
        return
    with capture(execute=True):
        fix_driver.check(environment.id)


def _validating(user, workspace, storage, capture, key, rows=ROWS):
    """A settled run whose Validate started: ``saving``, phase ``validating``."""
    environment, run = _settled(user, workspace, storage, capture, key, rows)
    _check(environment)
    run.refresh_from_db()
    assert (run.state, run.autosave["phase"]) == ("saving", "validating")
    return environment, run


def _set_versions(environment):
    environment.refresh_from_db()
    entry = candidate_sets.live_set(environment)
    return (
        HarnessEnvironmentVersion._base_manager.get(id=entry["environment_version_id"]),
        HarnessSuiteVersion._base_manager.get(id=entry["suite_version_id"]),
    )


def _certify_set(environment, storage, *, actions=FAILED_PROBE):
    """The validation job certified the candidate pairing: ``state`` reads Supported,
    ``tool_routing`` stays blocked on the failed probe."""
    environment_version, suite_version = _set_versions(environment)
    pairing = _pinned(
        environment,
        environment_version=environment_version,
        suite_version=suite_version,
    )
    assert (
        harness_readiness.pair_bundle(pairing).assembly_key
        == candidate_sets.live_set(environment)["assembly_key"]
    )
    _certify(environment, storage, pairing=pairing, checks=PASSED, actions=actions)
    # Sealed as a real bundle is, so the save's proof carry-over can replay it.
    object_key = harness_readiness.pair_bundle(pairing).object_key
    storage.objects[object_key] = archives.pack_archive(
        {
            **archives.read_archive(storage.objects[object_key]),
            SEALED_MANIFEST: json.dumps({"files": []}).encode(),
            CERTIFICATE: json.dumps({"status": "passed"}).encode(),
        }
    )
    # The job that certified it has ended.
    HostedHarnessJob.no_workspace_objects.filter(
        id=candidate_sets.live_set(environment)["job_id"]
    ).update(state=HostedHarnessJob.State.COMPLETED)


def _age(run, key, delta):
    run.refresh_from_db()
    run.autosave = {
        **run.autosave,
        key: (timezone.now() - delta).isoformat(),
    }
    run.save(update_fields=["autosave"])


def _lines(run):
    run.refresh_from_db()
    return {line["row_id"]: (line["state"], line["reason"]) for line in run.lines}


def _states(run):
    return {row: state for row, (state, _reason) in _lines(run).items()}


def _versions(environment):
    return (
        HarnessEnvironmentVersion._base_manager.filter(
            job=environment, status="active"
        ).count(),
        HarnessSuiteVersion._base_manager.filter(
            job=environment, status="active"
        ).count(),
    )


def _recheck_job():
    return (
        HostedHarnessJob.no_workspace_objects.filter(
            payload__metadata__certify_only=True,
            payload__metadata__validation_purpose="recheck",
        )
        .order_by("-created_at")
        .first()
    )


def _ask_open(conversation, message_id=None):
    """A question nobody answered is open in the chat."""
    HostedHarnessConversation.no_workspace_objects.filter(id=conversation.id).update(
        state=HostedHarnessConversation.State.WAITING_FOR_USER,
        blocking_input={"message_id": str(message_id or uuid.uuid4())},
    )


def _refused(code):
    return HostedHarnessError(code, code, status_code=409)


def _assert_unsaved(run, environment, code):
    """Ended not saved while the draft holds the changes."""
    run.refresh_from_db()
    environment.refresh_from_db()
    body = fix_runs.serialize(run, environment)
    assert (run.state, run.error["code"], body["draft"], body["saved"]) == (
        "done",
        code,
        True,
        None,
    )
    assert run.finished_at is not None
    assert {state for state, _reason in _lines(run).values()} == {"waiting_on_you"}
    assert {reason["code"] for _state, reason in _lines(run).values()} == {
        DRAFT_UNSAVED
    }


# --- the save ---------------------------------------------------------------------


class TestSaved:
    def test_settled_runs_validate_save_and_end_on_the_verdict(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _settled(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-happy"
        )
        base = (environment.environment_version_id, environment.suite_version_id)
        before = _versions(environment)

        _check(environment)

        run.refresh_from_db()
        assert run.state == "saving"
        assert run.autosave["phase"] == "validating"
        assert (
            run.autosave["name"] == "Fix: Seeded world state, Controlled tool routing"
        )
        wanted = run.autosave["candidates"]
        assert wanted == {
            "environment_version_id": str(_set_versions(environment)[0].id),
            "suite_version_id": str(_set_versions(environment)[1].id),
        }
        # Waits while the validation job runs.
        _check(environment)
        run.refresh_from_db()
        assert (run.state, run.autosave["phase"]) == ("saving", "validating")

        _certify_set(environment, storage)
        with (
            patch.object(
                candidate_promotion,
                "promote_candidates",
                wraps=candidate_promotion.promote_candidates,
            ) as promote,
            patch.object(fix_save.reports, "recheck") as recheck,
        ):
            _check(environment, django_capture_on_commit_callbacks)

        run.refresh_from_db()
        environment.refresh_from_db()
        assert promote.call_args.kwargs["idempotency_key"] == f"fix-{run.id}-save"
        assert promote.call_args.kwargs["name"] == run.autosave["name"]
        # Validate certified this assembly and no tools wait to be read: no re-check.
        recheck.assert_not_called()
        assert run.autosave["recheck"] == "skipped"
        assert run.state == "done"
        assert run.error is None
        assert (environment.environment_version_id, environment.suite_version_id) != (
            base
        )
        assert (
            str(run.result_environment_version_id),
            str(run.result_suite_version_id),
        ) == (
            str(environment.environment_version_id),
            str(environment.suite_version_id),
        )
        assert _versions(environment)[0] == before[0] + 1
        assert _states(run) == {"state": "fixed", "tool_routing": "could_not_fix"}
        assert _lines(run)["tool_routing"][1]["code"]
        report = harness_readiness.readiness_report(
            harness_readiness.resolve_pairing(environment)
        )
        assert run.finished_at == parse_datetime(report["computed_at"])
        body = fix_runs.serialize(run, environment)
        assert body["draft"] is False
        # Only the moved piece is listed.
        assert body["saved"]["environment"]["id"] == str(
            environment.environment_version_id
        )
        # The fix drafted the environment only: the suite default stays.
        assert body["saved"]["suite"] is None
        assert environment.suite_version_id == base[1]
        assert run.autosave["agent_version_id"] == str(environment.agent_version_id)
        assert drafts.open_draft(environment, "environment") is None

    def test_the_run_is_saved_once_when_the_run_update_is_lost(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-replay"
        )
        _certify_set(environment, storage)
        before = _versions(environment)

        with patch.object(
            fix_save, "_checking_values", side_effect=RuntimeError("lost")
        ):
            _check(environment)

        # The save and the run's update commit together: neither happened.
        run.refresh_from_db()
        assert (run.state, run.autosave["phase"]) == ("saving", "validating")
        assert _versions(environment) == before

        _check(environment)
        run.refresh_from_db()
        environment.refresh_from_db()
        assert run.state == "done"
        assert _versions(environment)[0] == before[0] + 1
        # The same key answers the same Save; no second version.
        again = candidate_promotion.promote_candidates(
            environment,
            run.autosave["candidates"],
            expected_review_digest=None,
            name=run.autosave["name"],
            idempotency_key=f"fix-{run.id}-save",
            user=user,
        )
        assert again["saved"]["environment"]["id"] == str(
            run.result_environment_version_id
        )
        assert _versions(environment)[0] == before[0] + 1

    def test_a_save_by_the_person_is_the_runs_save(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-user"
        )
        _certify_set(environment, storage)
        candidate_promotion.promote_candidates(
            environment,
            run.autosave["candidates"],
            expected_review_digest=None,
            name="Mine",
            idempotency_key="person-1",
            user=user,
        )
        environment.refresh_from_db()

        _check(environment)

        run.refresh_from_db()
        assert run.state == "done"
        assert run.error is None
        assert run.autosave["saved_by"] == "user"
        assert str(run.result_environment_version_id) == str(
            environment.environment_version_id
        )
        assert fix_runs.serialize(run, environment)["saved"]["environment"]

    def test_saving_moves_updated_at(self, user, workspace, storage):
        environment = _broken(user, workspace, storage, "fs-touch")
        run = _closed_run(environment, "checking", lines={"state": "fixing"})
        changed = run.state_changed_at
        before = fix_runs.snapshot(run)
        run.result_environment_version_id = environment.environment_version_id

        assert fix_runs.store(run, before)
        run.refresh_from_db()
        assert run.state_changed_at > changed

    def test_a_rebuild_card_the_save_raises_blocks_nothing(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-card"
        )
        _certify_set(environment, storage)
        _check(environment, django_capture_on_commit_callbacks)
        run.refresh_from_db()
        assert run.state == "done"

        # D9: a card a save raises (here when no suite proof carried over) is shown
        # unchanged; a pending card is not in flight, so a new fix on the row still
        # blocked is taken.
        environment.refresh_from_db()
        HarnessNudge.no_workspace_objects.get_or_create(
            job=environment,
            target="suite",
            state="pending",
            defaults={
                "organization_id": environment.organization_id,
                "workspace_id": environment.workspace_id,
            },
        )
        again = fix_runs.start_or_append(
            environment,
            group="builder",
            rows=["tool_routing"],
            idempotency_key=uuid.uuid4().hex,
            picks={},
            user=user,
        )
        assert again.id != run.id
        assert again.state == "queued"
        assert HarnessNudge.no_workspace_objects.get(job=environment).state == "pending"


class TestKeysFix:
    def test_an_agent_moved_mid_run_is_validated_again_and_saved(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        from simulate.models import HarnessAgentVersionRuntime
        from simulate.services.harness_versioning.versions import move_agent_default
        from simulate.tests.test_harness_agent_versions import _a1, _create
        from simulate.tests.test_harness_readiness import _read_tools

        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fk-move"
        )
        a1 = _a1(environment)
        a2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v2"})
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            preflight_status="passed", prompt_hash="sha256:" + "f" * 64
        )
        _read_tools(a2, [])
        # Certified with the defaults already, so the move happens at once.
        _certify(environment, storage, agent=a2)
        move_agent_default(environment, a2.id, expected_default_id=a1.id, user=user)
        environment.refresh_from_db()
        assert environment.agent_version_id == a2.id

        _check(environment)

        # The candidates read superseded: validated once more, with the new agent.
        run.refresh_from_db()
        assert (run.state, run.autosave["phase"]) == ("saving", "validating")
        assert run.autosave["revalidated"] is True
        environment.refresh_from_db()
        assert candidate_sets.live_set(environment)["agent_version_id"] == str(a2.id)
        _certify_set(environment, storage)

        _check(environment, django_capture_on_commit_callbacks)

        run.refresh_from_db()
        environment.refresh_from_db()
        assert (run.state, run.error) == ("done", None)
        assert run.autosave["agent_version_id"] == str(a2.id)
        assert str(run.result_environment_version_id) == str(
            environment.environment_version_id
        )
        assert _states(run) == {"state": "fixed", "tool_routing": "could_not_fix"}


class TestRecheck:
    def test_a_recheck_runs_when_tools_wait_to_be_read(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-read"
        )
        _certify_set(environment, storage)

        with patch.object(validation, "needs_reading", return_value=True):
            _check(environment)

        run.refresh_from_db()
        assert (run.state, run.autosave["recheck"]) == ("checking", "started")
        job = (
            HostedHarnessJob.no_workspace_objects.filter(
                payload__metadata__certify_only=True,
                payload__metadata__validation_purpose="recheck",
            )
            .order_by("-created_at")
            .first()
        )
        assert job is not None
        # The clock started before the re-check.
        assert parse_datetime(run.autosave["since"]) <= job.created_at
        _check(environment)
        run.refresh_from_db()
        assert run.state == "checking"

        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
            state=HostedHarnessJob.State.COMPLETED
        )
        _check(environment)
        run.refresh_from_db()
        assert run.state == "done"
        assert _states(run) == {"state": "fixed", "tool_routing": "could_not_fix"}

    def test_a_recheck_past_its_deadline_still_ends_saved(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-late"
        )
        _certify_set(environment, storage)
        with patch.object(validation, "needs_reading", return_value=True):
            _check(environment)
        _age(run, "since", fix_save.CHECK_GIVE_UP + timedelta(minutes=1))

        _check(environment)

        run.refresh_from_db()
        assert run.state == "done"
        assert run.result_environment_version_id is not None
        assert _states(run) == {"state": "fixed", "tool_routing": "could_not_fix"}
        assert _lines(run)["tool_routing"][1]["code"] == "check_unavailable"

    def test_a_refused_recheck_gives_the_verdict_at_once(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-refused"
        )
        _certify_set(environment, storage)
        with (
            patch.object(validation, "needs_reading", return_value=True),
            patch.object(
                fix_save.reports, "recheck", side_effect=_refused("usage_blocked")
            ),
        ):
            _check(environment)

        # The re-check could not run: the verdict cannot tell, as past the deadline.
        run.refresh_from_db()
        assert (run.state, run.autosave["recheck"]) == ("done", "unavailable")
        assert _states(run) == {"state": "fixed", "tool_routing": "could_not_fix"}
        assert _lines(run)["tool_routing"][1]["code"] == "check_unavailable"

    def test_a_check_already_reading_tools_is_waited_on(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-reading"
        )
        _certify_set(environment, storage)
        with patch.object(fix_save, "_checking", return_value=False):
            _check(environment)
        environment.refresh_from_db()
        with patch.object(validation, "needs_reading", return_value=True):
            harness_readiness.recheck(harness_readiness.resolve_pairing(environment))
        job = _recheck_job()

        _check(environment)

        run.refresh_from_db()
        assert (run.state, run.autosave["recheck"]) == ("checking", "started")
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
            state=HostedHarnessJob.State.COMPLETED
        )
        _check(environment)
        run.refresh_from_db()
        assert run.state == "done"

    def test_the_verdict_reads_what_the_fix_saved_after_the_person_saves_again(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-mine"
        )
        _certify_set(environment, storage)
        with patch.object(validation, "needs_reading", return_value=True):
            _check(environment)
        run.refresh_from_db()
        assert run.state == "checking"
        environment.refresh_from_db()
        saved = (run.result_environment_version_id, run.result_suite_version_id)
        # The person saves edits of their own: the defaults move off the fix's.
        draft = _draft(environment.environment_version)
        drafts.save_drafts(
            environment,
            {
                "environment": drafts.SaveRequest(
                    expected_parent_id=draft.parent_id,
                    expected_revision=draft.revision,
                )
            },
            idempotency_key=uuid.uuid4().hex,
            user=user,
        )
        environment.refresh_from_db()
        assert environment.environment_version_id != saved[0]
        HostedHarnessJob.no_workspace_objects.filter(id=_recheck_job().id).update(
            state=HostedHarnessJob.State.COMPLETED
        )

        _check(environment)

        run.refresh_from_db()
        assert run.state == "done"
        assert (run.result_environment_version_id, run.result_suite_version_id) == (
            saved
        )
        # Read from the fix's certified versions, not the person's new default.
        assert _states(run) == {"state": "fixed", "tool_routing": "could_not_fix"}
        assert _lines(run)["tool_routing"][1]["code"] != "check_unavailable"

    def test_a_saved_run_whose_versions_are_gone_still_ends_done(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-gone-v"
        )
        _certify_set(environment, storage)
        with patch.object(validation, "needs_reading", return_value=True):
            _check(environment)
        _age(
            run,
            "failing_since",
            fix_driver.CHECK_FAILING_GIVE_UP + timedelta(minutes=1),
        )

        with (
            patch.object(fix_driver, "_check", side_effect=RuntimeError("boom")),
            patch.object(
                fix_save,
                "saved_pairing",
                side_effect=HarnessEnvironmentVersion.DoesNotExist,
            ),
        ):
            fix_driver.check(environment.id)

        run.refresh_from_db()
        assert (run.state, run.error) == ("done", None)
        assert _lines(run)["state"][1]["code"] == "check_unavailable"

    def test_a_saved_run_whose_check_keeps_failing_ends_done(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-broken"
        )
        _certify_set(environment, storage)
        with patch.object(validation, "needs_reading", return_value=True):
            _check(environment)
        _age(
            run,
            "failing_since",
            fix_driver.CHECK_FAILING_GIVE_UP + timedelta(minutes=1),
        )

        with patch.object(fix_driver, "_check", side_effect=RuntimeError("boom")):
            fix_driver.check(environment.id)

        run.refresh_from_db()
        environment.refresh_from_db()
        assert (run.state, run.error) == ("done", None)
        assert fix_runs.serialize(run, environment)["saved"]["environment"]
        assert _states(run) == {"state": "fixed", "tool_routing": "could_not_fix"}
        assert _lines(run)["tool_routing"][1]["code"] == "check_unavailable"


# --- not saved --------------------------------------------------------------------


class TestNotSaved:
    def test_no_changes_fails_with_the_verdict(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run, message, conversation = _dispatched(
            user, workspace, storage, "fs-nothing", ROWS
        )
        sequence = _feed(
            conversation,
            django_capture_on_commit_callbacks,
            _started(message),
            _completed(message),
        )
        with django_capture_on_commit_callbacks(execute=True):
            _publish(conversation, sequence)

        _check(environment)

        run.refresh_from_db()
        environment.refresh_from_db()
        assert (run.state, run.error["code"]) == ("failed", "no_changes")
        assert fix_runs.serialize(run, environment)["draft"] is False
        assert set(_states(run).values()) == {"could_not_fix"}
        report = harness_readiness.readiness_report(
            harness_readiness.resolve_pairing(environment)
        )
        assert run.finished_at == parse_datetime(report["computed_at"])

    def test_validation_unavailable(
        self,
        user,
        workspace,
        storage,
        tasks,
        settings,
        django_capture_on_commit_callbacks,
    ):
        environment, run = _settled(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-off"
        )
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False

        _check(environment)

        _assert_unsaved(run, environment, "validation_unavailable")

    def test_a_stale_draft(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _settled(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-stale"
        )
        with patch.object(fix_save, "is_stale", return_value=True):
            _check(environment)

        _assert_unsaved(run, environment, "draft_stale")
        # The lines say what to do with a stale draft, not "review and save".
        assert {reason["message"] for _state, reason in _lines(run).values()} == {
            fix_save._MESSAGES["draft_stale"]
        }

    @pytest.mark.parametrize(
        "code,ended",
        [
            ("review_undecided", "review_undecided"),
            ("change_depends_on_rejected", "review_undecided"),
            ("draft_stale", "draft_stale"),
            ("rebuild_running", "rebuild_running"),
            ("scenario_key_taken", "validation_refused"),
        ],
    )
    def test_validate_refusals_end_unsaved(
        self,
        user,
        workspace,
        storage,
        tasks,
        django_capture_on_commit_callbacks,
        code,
        ended,
    ):
        environment, run = _settled(
            user, workspace, storage, django_capture_on_commit_callbacks, f"fs-{code}"
        )
        with patch.object(candidates, "validate_drafts", side_effect=_refused(code)):
            _check(environment)

        _assert_unsaved(run, environment, ended)
        run.refresh_from_db()
        assert run.error.get("code_detail") == (
            code if ended == "validation_refused" else None
        )

    @pytest.mark.parametrize("code", ["review_stale", "draft_changed"])
    def test_validate_retries_a_moved_review_once(
        self,
        user,
        workspace,
        storage,
        tasks,
        django_capture_on_commit_callbacks,
        code,
    ):
        environment, run = _settled(
            user, workspace, storage, django_capture_on_commit_callbacks, f"fs-{code}"
        )
        with patch.object(candidates, "validate_drafts", side_effect=_refused(code)):
            _check(environment)
            run.refresh_from_db()
            assert (run.state, run.autosave["validate_tries"]) == ("saving", 1)
            _check(environment)

        _assert_unsaved(run, environment, code)

    @pytest.mark.parametrize("code", ["chat_working", "environment_busy"])
    def test_validate_waits_for_a_busy_chat_then_gives_up(
        self,
        user,
        workspace,
        storage,
        tasks,
        django_capture_on_commit_callbacks,
        code,
    ):
        environment, run = _settled(
            user, workspace, storage, django_capture_on_commit_callbacks, f"fs-{code}"
        )
        with patch.object(candidates, "validate_drafts", side_effect=_refused(code)):
            _check(environment)
            _check(environment)
            run.refresh_from_db()
            assert (run.state, run.autosave["phase"]) == ("saving", "validate")
            _age(run, "waiting_since", fix_save.SAVE_WAIT_GIVE_UP + timedelta(1))
            _check(environment)

        _assert_unsaved(run, environment, code)

    def test_a_working_chat_holds_validate(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _settled(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-busy"
        )
        with (
            patch.object(drafts, "chat_busy", return_value=True),
            patch.object(candidates, "validate_drafts") as validate,
        ):
            _check(environment)
            validate.assert_not_called()
            _age(run, "waiting_since", fix_save.SAVE_WAIT_GIVE_UP + timedelta(1))
            _check(environment)

        _assert_unsaved(run, environment, "chat_working")

    @pytest.mark.parametrize(
        "state,ended",
        [
            ({"state": "failed", "error": {"message": "seed failed"}}, None),
            ({"state": "check_unavailable"}, "validation_unavailable"),
        ],
    )
    def test_validation_outcomes_end_unsaved(
        self,
        user,
        workspace,
        storage,
        tasks,
        django_capture_on_commit_callbacks,
        state,
        ended,
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-out"
        )
        with patch.object(candidate_sets, "validation_state", return_value=state):
            _check(environment)

        _assert_unsaved(run, environment, ended or "validation_failed")
        if ended is None:
            assert run.error["message"] == "seed failed"

    def test_a_validation_ended_early_is_validated_again_then_fails(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-retry"
        )
        with (
            patch.object(
                candidate_sets, "validation_state", return_value={"state": "retry"}
            ),
            patch.object(
                candidates, "validate_drafts", wraps=candidates.validate_drafts
            ),
        ):
            _check(environment)
            run.refresh_from_db()
            assert (run.autosave["phase"], run.autosave["validate_tries"]) == (
                "validating",
                2,
            )
            _check(environment)

        _assert_unsaved(run, environment, "validation_failed")

    def test_moved_defaults_are_validated_again_once(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-moved"
        )
        with (
            patch.object(
                candidate_sets,
                "validation_state",
                return_value={"state": "superseded"},
            ),
            patch.object(
                candidates, "validate_drafts", wraps=candidates.validate_drafts
            ) as validate,
        ):
            _check(environment)

        # Validated once more, then given up.
        assert validate.call_count == 1
        _assert_unsaved(run, environment, "defaults_moved")
        assert run.autosave["revalidated"] is True

    def test_a_validation_past_its_deadline(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-slow"
        )
        _age(run, "since", fix_save.CHECK_GIVE_UP + timedelta(minutes=1))

        _check(environment)

        _assert_unsaved(run, environment, "validation_timed_out")

    def test_a_replaced_candidate_set_is_draft_changed(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-set"
        )
        run.autosave = {
            **run.autosave,
            "candidates": {"environment_version_id": "x", "suite_version_id": "y"},
        }
        run.save(update_fields=["autosave"])

        _check(environment)

        _assert_unsaved(run, environment, "draft_changed")

    def test_drafts_discarded_meanwhile_fail_the_run(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-gone"
        )
        for piece in ("environment", "suite"):
            drafts.discard_draft(environment, piece, user=user)

        _check(environment)

        # The fix's turn wrote the draft: it was discarded, not "no changes".
        run.refresh_from_db()
        assert (run.state, run.error["code"]) == ("failed", "draft_discarded")
        assert {reason["code"] for _state, reason in _lines(run).values()} == {
            "draft_discarded"
        }
        assert fix_runs.serialize(run, environment)["draft"] is False

    def test_a_draft_gone_during_validate_is_discarded(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _settled(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-vanish"
        )

        def discarded(*_args, **_kwargs):
            drafts.discard_draft(environment, "environment", user=user)
            raise _refused("draft_not_found")

        with patch.object(candidates, "validate_drafts", side_effect=discarded):
            _check(environment)

        run.refresh_from_db()
        assert (run.state, run.error["code"]) == ("failed", "draft_discarded")

    def test_a_save_that_moved_nothing_is_no_changes(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-same"
        )
        _certify_set(environment, storage)

        def same(locked, *_args, **_kwargs):
            # The drafts held the defaults' own content: set aside, nothing moved.
            for piece in ("environment", "suite"):
                drafts.discard_draft(locked, piece, user=user)
            return {}

        with patch.object(candidate_promotion, "promote_candidates", side_effect=same):
            _check(environment)

        run.refresh_from_db()
        assert (run.state, run.error["code"]) == ("failed", "no_changes")
        assert run.result_environment_version_id is None
        assert run.finished_at is not None
        assert fix_runs.serialize(run, environment)["saved"] is None

    def test_a_draft_saved_with_nothing_to_move_is_no_changes(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        from simulate.services.harness_versioning.draft_pieces import (
            discard_version,
        )

        environment, run = _settled(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-unmoved"
        )
        # A save set the draft aside as the default it equals: nothing moved.
        discard_version(
            drafts.open_draft(environment, "environment"),
            user=user,
            saved_version_id=str(environment.environment_version_id),
        )

        _check(environment)

        run.refresh_from_db()
        assert (run.state, run.error["code"]) == ("failed", "no_changes")

    def test_a_retry_after_a_wait_clears_the_wait(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _settled(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-clear"
        )
        with patch.object(
            candidates, "validate_drafts", side_effect=_refused("chat_working")
        ):
            _check(environment)
        run.refresh_from_db()
        assert run.autosave["waiting_since"]
        with patch.object(
            candidates, "validate_drafts", side_effect=_refused("review_stale")
        ):
            _check(environment)

        run.refresh_from_db()
        assert run.state == "saving"
        assert run.autosave["waiting_since"] is None

    def test_a_real_validate_refusal_ends_unsaved(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _settled(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-real"
        )
        HarnessNudge.no_workspace_objects.create(
            job=environment,
            organization_id=environment.organization_id,
            workspace_id=environment.workspace_id,
            target="suite",
            state="queued",
        )

        with patch.object(
            candidates, "validate_drafts", wraps=candidates.validate_drafts
        ) as validate:
            _check(environment)

        assert validate.call_count == 1
        _assert_unsaved(run, environment, "rebuild_running")

    def test_a_save_by_the_person_before_validate_is_the_runs_save(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _settled(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-before"
        )
        draft = drafts.open_draft(environment, "environment")
        drafts.save_drafts(
            environment,
            {
                "environment": drafts.SaveRequest(
                    expected_parent_id=draft.parent_id,
                    expected_revision=draft.revision,
                )
            },
            idempotency_key=uuid.uuid4().hex,
            user=user,
        )
        environment.refresh_from_db()

        _check(environment)

        run.refresh_from_db()
        assert run.state in ("checking", "done")
        assert run.autosave["saved_by"] == "user"
        assert (run.error or {}).get("code") != "no_changes"
        assert str(run.result_environment_version_id) == str(
            environment.environment_version_id
        )

    def test_an_append_before_the_save_keeps_the_run_fixing(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _settled(
            user,
            workspace,
            storage,
            django_capture_on_commit_callbacks,
            "fs-append",
            rows=("state",),
        )
        fix_runs.start_or_append(
            environment,
            group="builder",
            rows=["tool_routing"],
            idempotency_key=uuid.uuid4().hex,
            picks={},
            user=user,
        )

        with patch.object(candidates, "validate_drafts") as validate:
            _check(environment)

        validate.assert_not_called()
        run.refresh_from_db()
        assert run.state == "fixing"
        assert run.autosave is None
        assert [one["state"] for one in run.commands] == ["settled", "dispatched"]

    def test_a_discard_while_waiting_on_the_person_ends_discarded(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _settled(
            user, workspace, storage, django_capture_on_commit_callbacks, "fs-wdisc"
        )
        ReadinessFixRun.no_workspace_objects.filter(id=run.id).update(
            state="waiting_on_you"
        )
        conversation = HostedHarnessConversation.no_workspace_objects.get(
            job=environment
        )
        _ask_open(conversation)
        draft = drafts.open_draft(environment, "environment")
        assert _code(environment, draft, user, "save") == "fix_waiting"
        # Discard stays allowed under the guard.
        assert drafts.discard_draft(environment, "environment", user=user)
        # The question goes away (the turn was stopped).
        HostedHarnessConversation.no_workspace_objects.filter(
            id=conversation.id
        ).update(state=HostedHarnessConversation.State.WARM_IDLE, blocking_input={})

        _check(environment)

        run.refresh_from_db()
        assert (run.state, run.error["code"]) == ("failed", "draft_discarded")

    @pytest.mark.parametrize(
        "code",
        [
            "not_validated",
            "draft_changed",
            "review_stale",
            "rebuild_running",
            "idempotency_key_reused",
        ],
    )
    def test_promote_refusals_end_unsaved(
        self,
        user,
        workspace,
        storage,
        tasks,
        django_capture_on_commit_callbacks,
        code,
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, f"fp-{code}"
        )
        _certify_set(environment, storage)
        with patch.object(
            candidate_promotion, "promote_candidates", side_effect=_refused(code)
        ):
            _check(environment)

        _assert_unsaved(run, environment, code)

    def test_promote_waits_for_a_busy_chat(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fp-busy"
        )
        _certify_set(environment, storage)
        with patch.object(
            candidate_promotion,
            "promote_candidates",
            side_effect=_refused("chat_working"),
        ):
            _check(environment)
            run.refresh_from_db()
            assert run.state == "saving" and run.autosave["waiting_since"]
        _check(environment)

        run.refresh_from_db()
        assert run.state == "done" and run.error is None

    def test_promote_on_moved_defaults_validates_again_once(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fp-moved"
        )
        _certify_set(environment, storage)
        with (
            patch.object(
                candidate_promotion,
                "promote_candidates",
                side_effect=_refused("defaults_moved"),
            ) as promote,
            patch.object(
                candidates, "validate_drafts", wraps=candidates.validate_drafts
            ) as validate,
        ):
            _check(environment)

        assert (validate.call_count, promote.call_count) == (1, 2)
        _assert_unsaved(run, environment, "defaults_moved")
        assert run.autosave["revalidated"] is True


class TestQuestions:
    def _asked(self, environment, *, ago=timedelta(0)):
        """The person's own question is open in the environment's chat."""
        from simulate.models import HostedHarnessConversationMessage as Message

        conversation = HostedHarnessConversation.no_workspace_objects.get(
            job=environment
        )
        asked = Message.no_workspace_objects.filter(conversation=conversation).first()
        Message.no_workspace_objects.filter(id=asked.id).update(
            created_at=timezone.now() - ago
        )
        HostedHarnessConversation.no_workspace_objects.filter(
            id=conversation.id
        ).update(
            state=HostedHarnessConversation.State.WAITING_FOR_USER,
            blocking_input={"message_id": str(asked.id)},
        )

    def test_an_open_question_holds_the_save(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _settled(
            user, workspace, storage, django_capture_on_commit_callbacks, "fq-open"
        )
        self._asked(environment)

        with patch.object(candidates, "validate_drafts") as validate:
            _check(environment)

        validate.assert_not_called()
        run.refresh_from_db()
        assert run.state == "fixing"
        assert run.autosave is None

    def test_a_question_left_a_day_ends_the_run(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _settled(
            user, workspace, storage, django_capture_on_commit_callbacks, "fq-day"
        )
        self._asked(
            environment, ago=fix_driver.FIX_QUESTION_GIVE_UP + timedelta(minutes=1)
        )

        _check(environment)

        _assert_unsaved(run, environment, "question_unanswered")


# --- fixed in vN ------------------------------------------------------------------


class TestResult:
    def test_the_base_pairing_shows_the_result_and_the_saved_one_the_progress(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fr-result"
        )
        _certify_set(environment, storage)
        _check(environment)
        run.refresh_from_db()
        environment.refresh_from_db()
        assert run.state == "done"

        base = harness_readiness.resolve_pairing(
            environment,
            environment_version_id=run.base_environment_version_id,
            suite_version_id=run.base_suite_version_id,
        )
        rows = {
            row["id"]: row
            for row in fix_runs.with_progress(
                base, harness_readiness.readiness_report(base)
            )["rows"]
        }
        # Fixed in vN, beside the base pairing's own still-blocked row.
        assert rows["state"]["status"] != "supported"
        assert "fix_progress" not in rows["state"]
        assert rows["state"]["fix_result"]["state"] == "fixed"
        assert rows["state"]["fix_result"]["environment"]["id"] == str(
            environment.environment_version_id
        )
        assert rows["tool_routing"]["fix_result"]["state"] == "could_not_fix"

        saved = harness_readiness.resolve_pairing(environment)
        rows = {
            row["id"]: row
            for row in fix_runs.with_progress(
                saved, harness_readiness.readiness_report(saved)
            )["rows"]
        }
        assert rows["state"]["fix_progress"]["state"] == "fixed"
        assert rows["tool_routing"]["fix_progress"]["state"] == "could_not_fix"
        assert "fix_result" not in rows["state"]

    def test_fixed_is_never_shown_on_a_row_still_blocked(
        self, user, workspace, storage, tasks, django_capture_on_commit_callbacks
    ):
        environment, run = _validating(
            user, workspace, storage, django_capture_on_commit_callbacks, "fr-blocked"
        )
        _certify_set(environment, storage)
        _check(environment)
        run.refresh_from_db()
        for line in run.lines:
            line["state"] = "fixed"
        run.save(update_fields=["lines"])
        environment.refresh_from_db()

        saved = harness_readiness.resolve_pairing(environment)
        rows = {
            row["id"]: row
            for row in fix_runs.with_progress(
                saved, harness_readiness.readiness_report(saved)
            )["rows"]
        }
        assert rows["tool_routing"]["status"] != "supported"
        assert "fix_progress" not in rows["tool_routing"]
        assert rows["state"]["fix_progress"]["state"] == "fixed"


# --- manual Save while a fix waits on the person ----------------------------------


def _waiting(user, workspace, storage, key, state="waiting_on_you", *, asked=True):
    environment = _broken(user, workspace, storage, key)
    draft = _draft(environment.environment_version)
    run = _closed_run(environment, state, lines={"state": "waiting_on_you"})
    ReadinessFixRun.no_workspace_objects.filter(id=run.id).update(finished_at=None)
    if asked:
        _chat(environment)
        _ask_open(HostedHarnessConversation.no_workspace_objects.get(job=environment))
    return environment, draft


def _manual(environment, draft, user, path):
    if path == "save":
        return drafts.save_drafts(
            environment,
            {
                "environment": drafts.SaveRequest(
                    expected_parent_id=draft.parent_id,
                    expected_revision=draft.revision,
                )
            },
            idempotency_key=uuid.uuid4().hex,
            user=user,
        )
    if path == "validate":
        return candidates.validate_drafts(
            environment,
            expected_revisions={"environment": draft.revision},
            expected_review_digest=None,
            user=user,
        )
    return candidate_promotion.promote_candidates(
        environment,
        {
            "environment_version_id": str(draft.id),
            "suite_version_id": str(environment.suite_version_id),
        },
        expected_review_digest=None,
        name="Mine",
        idempotency_key=uuid.uuid4().hex,
        user=user,
    )


def _code(environment, draft, user, path):
    try:
        _manual(environment, draft, user, path)
    except HostedHarnessError as exc:
        return exc.code
    return None


PATHS = ["save", "validate", "promote"]


class TestFixWaiting:
    @pytest.mark.parametrize("path", PATHS)
    def test_refused_while_a_fix_waits_on_the_person(
        self, user, workspace, storage, path
    ):
        environment, draft = _waiting(user, workspace, storage, f"fw-{path}")

        assert _code(environment, draft, user, path) == "fix_waiting"

    @pytest.mark.parametrize("path", PATHS)
    def test_not_refused_while_the_fix_saves(self, user, workspace, storage, path):
        environment, draft = _waiting(
            user, workspace, storage, f"fw-saving-{path}", state="saving"
        )

        assert _code(environment, draft, user, path) != "fix_waiting"

    @pytest.mark.parametrize("path", PATHS)
    def test_not_refused_once_its_question_is_gone(
        self, user, workspace, storage, path
    ):
        environment, draft = _waiting(
            user, workspace, storage, f"fw-gone-{path}", asked=False
        )

        assert _code(environment, draft, user, path) != "fix_waiting"

    @pytest.mark.parametrize("path", PATHS)
    def test_not_refused_while_fix_is_off(
        self, user, workspace, storage, settings, path
    ):
        environment, draft = _waiting(user, workspace, storage, f"fw-off-{path}")
        settings.HARNESS_READINESS_FIX_ENABLED = False

        assert _code(environment, draft, user, path) != "fix_waiting"
