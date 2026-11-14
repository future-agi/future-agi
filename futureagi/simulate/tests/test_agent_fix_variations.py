"""Fix my agent on a run with too few failing cases: the fix's first step has the builder
write variations of each into a suite version private to the fix, keeps those the base
version fails, and leaves the environment's suite, drafts and dataset as they were."""

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import OperationalError
from django.test import TestCase
from django.utils import timezone

from model_hub.models.develop_dataset import Row
from simulate.models import (
    AgentFixRun,
    HarnessSuiteMember,
    HarnessSuiteVersion,
    HostedHarnessConversationEvent,
    HostedHarnessConversationLease,
    HostedHarnessConversationMessage,
    HostedHarnessJob,
    HostedHarnessScenario,
)
from simulate.pydantic_schemas.agent_fix import (
    CheckRunRequest,
    FixRequest,
    FixStep,
    Surface,
)
from simulate.selectors.agent_fix import (
    VARIATIONS_LABEL,
    engine_brief,
    fix_deadline,
    inventory,
    variations_blocker,
    variations_minutes,
)
from simulate.services import agent_fix_runs
from simulate.services import agent_fix_variations as variations
from simulate.services.agent_fix_agents import agent_kind
from simulate.services.agent_fix_engine import start_check_run
from simulate.services.agent_fix_runs import (
    close_fix_run,
    launch_fix_run,
    retry_fix_run,
)
from simulate.services.agent_fix_sweep import sweep_lost_fix_runs
from simulate.services.harness_versioning import archives, drafts
from simulate.services.harness_versioning.read import list_versions
from simulate.services.harness_versioning.versions import move_default
from simulate.services.hosted_harness import (
    HostedHarnessError,
    VersionPicks,
    create_selected_harness_run,
)
from simulate.services.hosted_harness_conversation import (
    promote_conversation_checkpoint,
    publish_conversation_workspace,
)
from simulate.temporal.types.agent_fix import VariationsStep
from simulate.tests.test_agent_fix_services import (  # noqa: F401 - fixture
    KEYS,
    _move_default_suite,
    _receipts,
    _run,
    temporal,
)
from simulate.tests.test_harness_agent_versions import _environment
from simulate.tests.test_harness_amend_archive import NAMES
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_drafts import _lease
from simulate.tests.test_harness_versioning_record import _authoring, _scenario
from simulate.tests.test_hosted_harness_conversation import (
    _put_conversation_checkpoint,
)

pytestmark = pytest.mark.django_db

PARENT = KEYS[0]
# The failing case and its three variations just reach it.
FLOOR = 4


@pytest.fixture(autouse=True)
def floor(settings):
    settings.AGENT_FIX_MIN_FAILING_CASES = FLOOR
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
    settings.HARNESS_PUBLIC_BASE_URL = "https://platform.example.test"


@pytest.fixture(autouse=True)
def builder():
    """The builder's runtime, started for its message."""
    with patch(
        "simulate.tasks.hosted_harness_conversation.schedule_conversation_runtime"
    ) as schedule:
        yield schedule


@pytest.fixture
def environment(user, workspace, storage):
    """Two scenarios that each grade "refunded"."""
    documents = [_scenario(name, sub_goals=["refunded"]) for name in NAMES[:2]]
    body = _authoring(
        list(NAMES[:2]),
        **{
            "scenarios.json": json.dumps(documents),
            **{
                f"scenarios/{one['name']}/scenario.json": json.dumps(one)
                for one in documents
            },
        },
    )
    return _environment(user, workspace, "variations-env", storage, body=body)


@pytest.fixture
def source_run(environment):
    """The first scenario failed "refunded"; the second held."""
    run = _run(environment)
    _receipts(run, {KEYS[0]: {"refunded": False}, KEYS[1]: {"refunded": True}})
    return run


@pytest.fixture
def fix_run(source_run, user):
    """Launched, and asked the builder for its variations."""
    fix_run = launch_fix_run(
        source_run,
        target_scenario_keys=[PARENT],
        surfaces=[Surface.PROMPT],
        locked_paths=[],
        budget_usd=5.0,
        max_minutes=30,
        user=user,
    )
    assert variations.advance(str(fix_run.id)) == VariationsStep.WRITING
    fix_run.refresh_from_db()
    return fix_run


def _command(fix_run):
    return HostedHarnessConversationMessage.no_workspace_objects.get(
        conversation__job_id=fix_run.environment_id,
        client_request_id=f"fix-{fix_run.id}-variations",
    )


def _builder_save(environment, numbers=(1, 2, 3), *, extra=()):
    """The workspace the builder saves: the environment's own scenarios, the variations
    it was asked for (with sub-goals of its own choosing), and anything else it wrote."""
    key = environment.payload["metadata"]["authoring_object_key"]
    files = archives.read_archive(archives.get_archive(key))
    documents = json.loads(files["scenarios.json"])
    for written in [*(f"{PARENT}-var{n}" for n in numbers), *extra]:
        document = {
            "name": written,
            "scenario_key": written,
            "folder": f"scenarios/{written}",
            "instruction": f"another caller, {written}",
            "tests": "t",
            "sub_goals": ["greeted"],
        }
        documents.append(document)
        files[f"scenarios/{written}/scenario.json"] = json.dumps(document).encode()
        files[f"scenarios/{written}/ready.py"] = b"def ready(world):\n    return 1\n"
    files["scenarios.json"] = json.dumps(documents).encode()
    return archives.pack_archive(files)


def _turn_ended(command, sequence):
    HostedHarnessConversationEvent.no_workspace_objects.get_or_create(
        event_id=f"turn-{command.id}",
        defaults={
            "conversation": command.conversation,
            "sequence": sequence,
            "kind": "turn_completed",
            "payload": {"command_message_id": str(command.id), "outcome": "success"},
            "digest": "sha256:" + "e" * 64,
            "emitted_at": timezone.now(),
        },
    )


def _land(fix_run, storage, body, *, sequence=1):
    """The builder's turn finished: its checkpoint is promoted as any chat turn's is."""
    command = _command(fix_run)
    _turn_ended(command, 1000 + sequence)
    key, digest = _put_conversation_checkpoint(command.conversation, storage, body)
    return promote_conversation_checkpoint(
        command.conversation,
        object_key=key,
        digest=digest,
        sequence=sequence,
        expected_revision=command.payload["_environment_revision"],
        command=command,
    )


def _private(fix_run):
    fix_run.refresh_from_db()
    return fix_run.suite_version


def _named(fix_run, number):
    return f"{PARENT}-fix{fix_run.id.hex[:8]}-v{number}"


def _base_run(fix_run):
    """The first step's run of the base version, started by its next look."""
    with TestCase.captureOnCommitCallbacks(execute=True):
        assert variations.advance(str(fix_run.id)) == VariationsStep.CHECKING
    return HostedHarnessJob.no_workspace_objects.get(
        fix_run=fix_run, fix_label=VARIATIONS_LABEL
    )


def _finish(run, failed):
    """The base version's run ends; the parent and the variations in ``failed`` fail."""
    _receipts(
        run,
        {
            key: {"refunded": not (key == PARENT or key in failed)}
            for key in run.payload["metadata"]["selected_scenario_keys"]
        },
    )
    HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(state="completed")


class TestTheBuilderMessage:
    def test_names_each_failing_case_and_needs_nobody_to_confirm(
        self, fix_run, builder
    ):
        command = _command(fix_run)

        assert command.payload["from_platform"] is True
        assert command.payload["_origin"] == "fix"
        assert command.payload["_fix_run_id"] == str(fix_run.id)
        assert f"1. {PARENT}: refunded" in command.content
        assert "<parent key>-var1, <parent key>-var2, <parent key>-var3" in (
            command.content
        )
        # The builder's memory outlives its workspace: it must not answer from it.
        assert "even if you remember writing variations before" in command.content
        # ALK refuses a scenario whose coverage and sub-goals both equal another's, so
        # a variation keeps the failure's levels and moves to a cell of its own.
        assert "coverage cell of its own" in command.content
        assert "interface, interaction or counterparty" in command.content
        assert "sub_goals, use_case, max_turns" in command.content
        assert "sub_goals, use_case, coverage" not in command.content
        assert fix_run.step == FixStep.VARIATIONS
        builder.assert_called_once()
        # Asked once, however often the step is looked at.
        assert variations.advance(str(fix_run.id)) == VariationsStep.WRITING
        assert (
            HostedHarnessConversationMessage.no_workspace_objects.filter(
                client_request_id=command.client_request_id
            ).count()
            == 1
        )

    def test_a_builder_turn_that_failed_ends_the_fix_with_its_reason(self, fix_run):
        command = _command(fix_run)
        HostedHarnessConversationEvent.no_workspace_objects.create(
            event_id="turn-1",
            conversation=command.conversation,
            sequence=1,
            kind="turn_completed",
            payload={"command_message_id": str(command.id), "outcome": "failed"},
            digest="sha256:" + "e" * 64,
            emitted_at=timezone.now(),
        )

        assert variations.advance(str(fix_run.id)) == VariationsStep.ENDED
        fix_run.refresh_from_db()
        assert (fix_run.state, fix_run.failure["code"]) == (
            AgentFixRun.State.FAILED,
            "fix_variations_failed",
        )

    def test_an_error_a_later_look_would_hit_again_ends_the_fix(
        self, source_run, user, settings
    ):
        settings.HARNESS_PUBLIC_BASE_URL = "http://platform.example.test"
        fix_run = launch_fix_run(
            source_run,
            target_scenario_keys=[PARENT],
            surfaces=[Surface.PROMPT],
            locked_paths=[],
            budget_usd=5.0,
            max_minutes=30,
            user=user,
        )

        assert variations.advance(str(fix_run.id)) == VariationsStep.ENDED

        fix_run.refresh_from_db()
        assert (fix_run.state, fix_run.failure["code"]) == (
            AgentFixRun.State.FAILED,
            "fix_variations_failed",
        )
        assert not HostedHarnessConversationMessage.no_workspace_objects.filter(
            client_request_id=f"fix-{fix_run.id}-variations"
        ).exists()

    def test_a_builder_turn_that_saved_nothing_ends_the_fix_soon_after(self, fix_run):
        command = _command(fix_run)
        _turn_ended(command, 1)

        # Its save may still be on its way.
        assert variations.advance(str(fix_run.id)) == VariationsStep.WRITING
        HostedHarnessConversationEvent.no_workspace_objects.filter(
            event_id=f"turn-{command.id}"
        ).update(emitted_at=timezone.now() - timedelta(minutes=5))

        assert variations.advance(str(fix_run.id)) == VariationsStep.ENDED
        fix_run.refresh_from_db()
        assert (fix_run.state, fix_run.failure["code"]) == (
            AgentFixRun.State.FAILED,
            "fix_variations_failed",
        )


class TestThePrivateSuite:
    def test_holds_the_run_s_suite_and_the_renamed_variations_with_their_case_s_sub_goals(
        self, environment, source_run, fix_run, storage
    ):
        default = environment.suite_version_id
        live = set(
            HostedHarnessScenario.no_workspace_objects.filter(
                job=environment
            ).values_list("scenario_key", flat=True)
        )

        _land(fix_run, storage, _builder_save(environment, extra=["fresh-idea"]))

        version = _private(fix_run)
        names = [_named(fix_run, n) for n in (1, 2, 3)]
        assert (
            version.fix_run_id,
            version.status,
            version.version_number,
            version.origin,
            version.parent_id,
            version.authored_against_id,
        ) == (
            fix_run.id,
            "active",
            None,
            "fix",
            source_run.suite_version_id,
            source_run.environment_version_id,
        )
        assert version.documents["variations"] == dict.fromkeys(names, PARENT)
        members = dict(
            HarnessSuiteMember.no_workspace_objects.filter(
                suite_version=version
            ).values_list("scenario_key", "content")
        )
        # Not what the builder wrote besides, and each with its case's sub-goals.
        assert set(members) == {*KEYS, *names}
        assert all(members[name]["sub_goals"] == ["refunded"] for name in names)
        stored = archives.read_archive(archives.get_archive(version.archive_object_key))
        document = json.loads(stored[f"scenarios/{names[0]}/scenario.json"])
        assert (document["name"], document["sub_goals"]) == (names[0], ["refunded"])
        assert f"scenarios/{PARENT}-var1/scenario.json" not in stored
        # The environment's suite, scenarios and drafts are as they were.
        environment.refresh_from_db()
        assert environment.suite_version_id == default
        assert drafts.open_draft(environment, "suite") is None
        assert (
            set(
                HostedHarnessScenario.no_workspace_objects.filter(
                    job=environment
                ).values_list("scenario_key", flat=True)
            )
            == live
        )

    def test_takes_only_the_variations_whatever_else_the_workspace_holds(
        self, environment, source_run, fix_run, storage
    ):
        # The builder works on the environment's unsaved changes, if a user opened some:
        # here an edit to an existing scenario and a new one.
        body = _builder_save(environment, extra=["fresh-idea"])
        files = archives.read_archive(body)
        documents = json.loads(files["scenarios.json"])
        for one in documents:
            if one.get("scenario_key") == KEYS[1]:
                one["instruction"] = "an unsaved edit"
        files["scenarios.json"] = json.dumps(documents).encode()

        _land(fix_run, storage, archives.pack_archive(files))

        source = dict(
            HarnessSuiteMember.no_workspace_objects.filter(
                suite_version_id=source_run.suite_version_id
            ).values_list("scenario_key", "content")
        )
        private = dict(
            HarnessSuiteMember.no_workspace_objects.filter(
                suite_version=_private(fix_run)
            ).values_list("scenario_key", "content")
        )
        assert {key: private[key] for key in source} == source
        assert "fresh-idea" not in private

    def test_finds_a_variation_whose_key_the_builder_had_to_suffix(
        self, environment, fix_run, storage
    ):
        # A key the environment retired comes back as "<key>-2"; its name stays.
        files = archives.read_archive(_builder_save(environment))
        documents = json.loads(files["scenarios.json"])
        for one in documents:
            if one["scenario_key"].startswith(f"{PARENT}-var"):
                one["name"] = one["scenario_key"].replace("-", "_")
                one["scenario_key"] = f"{one['scenario_key']}-2"
        files["scenarios.json"] = json.dumps(documents).encode()

        _land(fix_run, storage, archives.pack_archive(files))

        assert set(_private(fix_run).documents["variations"]) == {
            _named(fix_run, n) for n in (1, 2, 3)
        }

    def test_is_listed_nowhere_picked_by_no_other_run_and_never_the_default(
        self, environment, fix_run, storage
    ):
        _land(fix_run, storage, _builder_save(environment))
        version = _private(fix_run)
        environment.refresh_from_db()

        listed = [one["id"] for one in list_versions(environment)["suite"]]
        with pytest.raises(HostedHarnessError) as picked:
            create_selected_harness_run(
                environment,
                scenario_keys=[PARENT],
                trials=1,
                idempotency_key="someone-else",
                picks=VersionPicks(suite_version_id=version.id),
            )
        with pytest.raises(HostedHarnessError) as moved:
            move_default(
                environment,
                "suite",
                version.id,
                expected_default_id=environment.suite_version_id,
            )

        assert str(version.id) not in listed
        assert picked.value.code == moved.value.code == "version_not_pickable"
        # The fix's own check runs run it.
        options = agent_kind(environment).run_options(
            fix_run, str(fix_run.base_agent_version_id)
        )
        assert options["picks"].suite_version_id == version.id

    def test_the_chat_s_sandbox_holding_the_variations_never_saves_a_draft(
        self, environment, fix_run, storage
    ):
        conversation = _command(fix_run).conversation
        token = drafts.compose_workspace(environment, conversation).token

        _land(fix_run, storage, _builder_save(environment))

        environment.refresh_from_db()
        published = environment.payload["metadata"]["conversation_checkpoints"][
            str(conversation.id)
        ]
        assert drafts.expected_for(published, token) is None

    def test_a_save_while_the_builder_works_neither_lands_nor_ends_the_fix(
        self, environment, fix_run, storage
    ):
        command = _command(fix_run)
        conversation = command.conversation
        token = drafts.compose_workspace(environment, conversation).token
        # The builder saves its workspace before it has written anything.
        key, digest = _put_conversation_checkpoint(
            conversation, storage, _builder_save(environment, ())
        )
        # The turn is running: its command was delivered to the builder.
        HostedHarnessConversationMessage.no_workspace_objects.filter(
            id=command.id
        ).update(state="delivered")
        conversation.latest_workspace_object_key = key
        conversation.latest_workspace_digest = digest
        conversation.save(
            update_fields=["latest_workspace_object_key", "latest_workspace_digest"]
        )

        published = publish_conversation_workspace(conversation, digest=digest)

        assert (published["applied"], published["code"]) == (
            False,
            "fix_variations_not_final",
        )
        fix_run.refresh_from_db()
        assert (fix_run.state, fix_run.suite_version_id) == (
            AgentFixRun.State.RUNNING,
            None,
        )
        assert drafts.open_draft(environment, "suite") is None
        environment.refresh_from_db()
        checkpoint = environment.payload["metadata"]["conversation_checkpoints"][
            str(conversation.id)
        ]
        assert drafts.expected_for(checkpoint, token) is None

    def test_is_made_once_however_often_the_save_lands(
        self, environment, fix_run, storage
    ):
        _land(fix_run, storage, _builder_save(environment))
        first = _private(fix_run)
        _land(fix_run, storage, _builder_save(environment, (1, 2)), sequence=2)

        assert _private(fix_run).id == first.id
        assert (
            HarnessSuiteVersion.no_workspace_objects.filter(fix_run=fix_run).count()
            == 1
        )

    def test_a_save_without_variations_ends_the_fix(
        self, environment, fix_run, storage
    ):
        _land(fix_run, storage, _builder_save(environment, (), extra=["fresh-idea"]))

        fix_run.refresh_from_db()
        assert (fix_run.state, fix_run.failure["code"], fix_run.suite_version_id) == (
            AgentFixRun.State.FAILED,
            "fix_variations_failed",
            None,
        )


class TestTheStepsTime:
    """The first step's cap follows the time its run is allowed, not a fixed one."""

    def _runtime(self, job, seconds):
        payload = {**job.payload, "runtime": {**job.payload["runtime"]}}
        payload["runtime"]["max_duration_seconds"] = seconds
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(payload=payload)

    @pytest.mark.parametrize(
        ("environment_seconds", "minutes"),
        # The builder's 60, the run's own limit, and the 15-minute grace. The failing case
        # and its three variations take at least 4 x 6 minutes.
        [(600, 60 + 24 + 15), (3 * 3600, 60 + 180 + 15)],
    )
    def test_before_its_run_it_is_the_limit_that_run_will_get(
        self, environment, fix_run, environment_seconds, minutes
    ):
        self._runtime(environment, environment_seconds)
        fix_run.refresh_from_db()

        assert variations_minutes(fix_run) == minutes
        assert variations.step_minutes(str(fix_run.id), VariationsStep.WRITING) == 60
        assert (
            variations.step_minutes(str(fix_run.id), VariationsStep.CHECKING) == minutes
        )
        assert variations.step_minutes(str(fix_run.id), VariationsStep.WAITING) == 0

    def test_once_its_run_exists_it_is_that_run_s_limit_and_the_fix_s_deadline_follows(
        self, environment, fix_run, storage
    ):
        _land(fix_run, storage, _builder_save(environment))
        run = _base_run(fix_run)
        # A voice suite's run may be allowed far more than the old fixed 90 minutes.
        self._runtime(run, 4 * 3600)
        fix_run.refresh_from_db()

        assert variations_minutes(fix_run) == 60 + 240 + 15
        start = fix_run.started_at or fix_run.created_at
        assert fix_deadline(fix_run) == start + timedelta(minutes=30 + 60 + 240 + 15)


class TestSettle:
    def test_variations_the_base_version_fails_become_targets_and_the_fix_s_time_starts(
        self, environment, fix_run, storage
    ):
        _land(fix_run, storage, _builder_save(environment))
        names = [_named(fix_run, n) for n in (1, 2, 3)]
        run = _base_run(fix_run)
        _finish(run, failed=names)
        started = fix_run.started_at

        assert run.payload["metadata"]["trials"] == 1
        assert run.payload["metadata"]["selected_scenario_keys"] == [PARENT, *names]
        assert variations.advance(str(fix_run.id)) == VariationsStep.DONE

        fix_run.refresh_from_db()
        request = FixRequest.model_validate(fix_run.request)
        assert request.variations_pending is False
        assert request.target_scenario_keys == [PARENT, *names]
        assert request.variation_parents == dict.fromkeys(names, PARENT)
        assert request.failing_sub_goals == ["refunded"]
        assert fix_run.started_at > started
        # The engine's brief: the fix's suite, and each variation's failing trial.
        brief = engine_brief(fix_run)
        assert {one.key for one in brief.inventory} == {*KEYS, *names}
        assert {one.scenario_key for one in brief.source_failures} == {PARENT, *names}
        assert variations.advance(str(fix_run.id)) == VariationsStep.DONE

    def test_too_few_failing_even_so_ends_with_its_reason_and_keeps_what_it_found(
        self, environment, fix_run, storage
    ):
        _land(fix_run, storage, _builder_save(environment))
        kept = _named(fix_run, 2)
        _finish(_base_run(fix_run), failed=[kept])

        assert variations.advance(str(fix_run.id)) == VariationsStep.ENDED

        fix_run.refresh_from_db()
        assert fix_run.state == AgentFixRun.State.TOO_FEW_CASES
        assert fix_run.failure["code"] == "fix_targets_too_few"
        assert fix_run.failure["message"] == (
            "The run had 1 failing case. The builder wrote 3 variations of them, and 1"
            " failed on the current version: 2 failing cases, and an epoch needs 4."
        )
        assert fix_run.request["variation_parents"] == {kept: PARENT}
        assert fix_run.suite_version.fix_run_id == fix_run.id
        # A variation the base version passed is no case of the fix.
        assert {one.key for one in inventory(fix_run)} == {*KEYS, kept}

    def test_the_dataset_rows_its_run_added_are_removed_when_the_fix_ends(
        self, environment, fix_run, storage
    ):
        dataset = environment.run_test.scenarios.get().dataset
        before = set(dataset.row_set.filter(deleted=False).values_list("id", flat=True))
        _land(fix_run, storage, _builder_save(environment))
        _base_run(fix_run)
        bound = list(
            HostedHarnessScenario._base_manager.filter(
                job=environment, scenario_key__startswith=f"{PARENT}-fix"
            )
        )
        added = {one.dataset_row_id for one in bound}
        assert len(added) == 3 and None not in added

        close_fix_run(fix_run.id, AgentFixRun.State.STOPPED)

        assert (
            set(dataset.row_set.filter(deleted=False).values_list("id", flat=True))
            == before
        )
        assert Row.all_objects.filter(id__in=added, deleted=True).count() == 3
        scenario = environment.run_test.scenarios.get()
        assert scenario.metadata["persona_count"] == len(before)
        # Unbound: a retry's run of the same variations binds rows of its own.
        assert not HostedHarnessScenario._base_manager.filter(
            id__in=[one.id for one in bound], scenario__isnull=False
        ).exists()

    def test_the_environment_s_shared_scenario_stays_whatever_a_variation_holds(
        self, environment, fix_run, storage
    ):
        _land(fix_run, storage, _builder_save(environment))
        _base_run(fix_run)
        shared = environment.run_test.scenarios.get()
        # Bound to the shared scenario without a row of its own.
        HostedHarnessScenario._base_manager.filter(
            job=environment, scenario_key=_named(fix_run, 1)
        ).update(dataset_row=None)

        close_fix_run(fix_run.id, AgentFixRun.State.STOPPED)

        shared.refresh_from_db()
        assert shared.deleted is False

    def test_too_few_is_decided_and_closed_together_through_an_outage(
        self, environment, fix_run, storage
    ):
        _land(fix_run, storage, _builder_save(environment))
        _finish(_base_run(fix_run), failed=[_named(fix_run, 2)])

        with (
            patch.object(
                variations, "close_fix_run", side_effect=OperationalError("gone")
            ),
            pytest.raises(OperationalError),
        ):
            variations.advance(str(fix_run.id))

        # The next look decides again: the engine never starts below the floor.
        assert variations.advance(str(fix_run.id)) == VariationsStep.ENDED
        fix_run.refresh_from_db()
        assert fix_run.state == AgentFixRun.State.TOO_FEW_CASES

    def test_a_legacy_environment_s_scenarios_for_the_variations_are_removed(
        self, environment, fix_run, storage
    ):
        from simulate.services.alk_simulate_ingestion import (
            _create_persona_scenarios_legacy,
        )

        _land(fix_run, storage, _builder_save(environment))
        _base_run(fix_run)
        run_test = environment.run_test
        shared = run_test.scenarios.get()
        # Built before grouped datasets: each persona got a scenario of its own.
        [legacy] = _create_persona_scenarios_legacy(
            environment.organization,
            environment.workspace,
            run_test.agent_definition,
            [{"name": "caller", "situation": "a variation"}],
        )
        run_test.scenarios.add(legacy)
        HostedHarnessScenario._base_manager.filter(
            job=environment, scenario_key=_named(fix_run, 1)
        ).update(scenario=legacy, dataset_row=None)

        close_fix_run(fix_run.id, AgentFixRun.State.STOPPED)

        legacy.refresh_from_db()
        shared.refresh_from_db()
        assert (legacy.deleted, shared.deleted) == (True, False)
        assert not run_test.scenarios.filter(id=legacy.id).exists()

    def test_a_retry_of_a_fix_that_failed_in_its_first_step_reuses_what_it_finished(
        self, environment, fix_run, storage, user, builder
    ):
        _land(fix_run, storage, _builder_save(environment))
        variations_run = _base_run(fix_run)
        _finish(variations_run, failed=[_named(fix_run, n) for n in (1, 2, 3)])
        # The step ends before it settles: its cap, say.
        close_fix_run(
            fix_run.id,
            AgentFixRun.State.FAILED,
            failure={"code": "fix_variations_timeout", "message": "m"},
        )
        builder.reset_mock()

        retried = retry_fix_run(
            AgentFixRun.no_workspace_objects.get(id=fix_run.id), user=user
        )

        assert FixRequest.model_validate(retried.request).variations_pending is True
        assert retried.suite_version_id == _private(fix_run).id
        assert variations.advance(str(retried.id)) == VariationsStep.DONE
        retried.refresh_from_db()
        # No builder message and no second run of the base version: both are reused.
        builder.assert_not_called()
        assert not HostedHarnessJob.no_workspace_objects.filter(
            fix_run=retried
        ).exists()
        assert retried.request["variation_parents"] == {
            _named(fix_run, n): PARENT for n in (1, 2, 3)
        }

    def test_a_retry_of_a_fix_that_failed_while_the_builder_wrote_asks_again(
        self, fix_run, user
    ):
        close_fix_run(
            fix_run.id,
            AgentFixRun.State.FAILED,
            failure={"code": "fix_variations_timeout", "message": "m"},
        )

        retried = retry_fix_run(
            AgentFixRun.no_workspace_objects.get(id=fix_run.id), user=user
        )

        assert variations.advance(str(retried.id)) == VariationsStep.WRITING
        assert _command(retried).payload["_fix_run_id"] == str(retried.id)


def _token(environment, fix_run):
    """The chat workspace's draft basis now: a save from a sandbox holding an older
    token is refused."""
    return drafts.compose_workspace(environment, _command(fix_run).conversation).token


def _outdated(environment, fix_run, token):
    environment.refresh_from_db()
    checkpoint = environment.payload["metadata"]["conversation_checkpoints"][
        str(_command(fix_run).conversation_id)
    ]
    return drafts.expected_for(checkpoint, token) is None


def _bound(environment, fix_run):
    return HostedHarnessScenario._base_manager.filter(
        job=environment,
        scenario_key__startswith=f"{PARENT}-fix{fix_run.id.hex[:8]}",
        scenario__isnull=False,
    )


class TestWhatAFixLeavesBehind:
    def test_a_fix_stopped_while_its_message_waits_withdraws_it_and_outdates_the_chat(
        self, environment, fix_run
    ):
        token = _token(environment, fix_run)

        # The patch outlives the commit callbacks, which run as the capture ends.
        with (
            patch("simulate.tasks.harness_nudges.schedule_sandbox_stop") as stop,
            TestCase.captureOnCommitCallbacks(execute=True),
        ):
            close_fix_run(fix_run.id, AgentFixRun.State.STOPPED)

        assert _command(fix_run).state == HostedHarnessConversationMessage.State.FAILED
        assert _outdated(environment, fix_run, token)
        # Not started: no sandbox ran it.
        stop.assert_not_called()

    def test_a_fix_stopped_while_the_builder_runs_its_turn_ends_that_turn(
        self, environment, fix_run
    ):
        command = _command(fix_run)
        HostedHarnessConversationMessage.no_workspace_objects.filter(
            id=command.id
        ).update(state=HostedHarnessConversationMessage.State.DELIVERED)
        lease = _lease(command.conversation)

        # The patch outlives the commit callbacks, which run as the capture ends.
        with (
            patch("simulate.tasks.harness_nudges.schedule_sandbox_stop") as stop,
            TestCase.captureOnCommitCallbacks(execute=True),
        ):
            close_fix_run(fix_run.id, AgentFixRun.State.STOPPED)

        lease.refresh_from_db()
        assert lease.state == HostedHarnessConversationLease.State.EXPIRED
        stop.assert_called_once_with(str(command.conversation_id), lease.provider_ref)
        assert _command(fix_run).state == HostedHarnessConversationMessage.State.FAILED

    def test_a_failed_builder_turn_outdates_the_chat_s_workspace(
        self, environment, fix_run
    ):
        command = _command(fix_run)
        token = _token(environment, fix_run)
        HostedHarnessConversationEvent.no_workspace_objects.create(
            event_id="turn-failed",
            conversation=command.conversation,
            sequence=1,
            kind="turn_completed",
            payload={"command_message_id": str(command.id), "outcome": "failed"},
            digest="sha256:" + "e" * 64,
            emitted_at=timezone.now(),
        )

        assert variations.advance(str(fix_run.id)) == VariationsStep.ENDED

        # Whatever it wrote before it failed never reaches a draft.
        assert _outdated(environment, fix_run, token)

    def test_an_older_fix_s_late_close_leaves_its_retry_s_rows(
        self, environment, fix_run, storage, user
    ):
        _land(fix_run, storage, _builder_save(environment))
        _finish(_base_run(fix_run), failed=[_named(fix_run, n) for n in (1, 2, 3)])
        close_fix_run(
            fix_run.id,
            AgentFixRun.State.FAILED,
            failure={"code": "fix_variations_timeout", "message": "m"},
        )
        retried = retry_fix_run(
            AgentFixRun.no_workspace_objects.get(id=fix_run.id), user=user
        )
        assert variations.advance(str(retried.id)) == VariationsStep.DONE
        # The retry runs the same variations: it binds rows of its own.
        with TestCase.captureOnCommitCallbacks(execute=True):
            start_check_run(
                AgentFixRun.no_workspace_objects.get(id=retried.id),
                CheckRunRequest(
                    label="screen-1",
                    agent_version_id=retried.base_agent_version_id,
                    scenario_keys=[_named(fix_run, n) for n in (1, 2, 3)],
                    trials=1,
                ),
            )
        rows = {one.dataset_row_id for one in _bound(environment, fix_run)}
        assert len(rows) == 3

        # The first fix's close runs again, late.
        close_fix_run(fix_run.id, AgentFixRun.State.FAILED)

        assert {one.dataset_row_id for one in _bound(environment, fix_run)} == rows
        assert not Row.all_objects.filter(id__in=rows, deleted=True).exists()

    def test_rows_a_failed_cleanup_left_are_dropped_by_the_sweep(
        self, environment, fix_run, storage
    ):
        _land(fix_run, storage, _builder_save(environment))
        _base_run(fix_run)
        rows = {one.dataset_row_id for one in _bound(environment, fix_run)}
        with patch.object(agent_fix_runs, "Row") as row:
            row.all_objects.filter.side_effect = OperationalError("gone")
            close_fix_run(fix_run.id, AgentFixRun.State.STOPPED)
        assert _bound(environment, fix_run).count() == 3

        counts = sweep_lost_fix_runs()

        assert counts["variation_rows_left"] == 0
        assert not _bound(environment, fix_run).exists()
        assert Row.all_objects.filter(id__in=rows, deleted=True).count() == 3


class TestARunTheEnvironmentMovedOnFrom:
    def test_is_refused_variations_at_launch(self, environment, source_run, user):
        _move_default_suite(environment, user)

        refused = variations_blocker(
            HostedHarnessJob.no_workspace_objects.get(id=source_run.id)
        )

        assert refused.code == "fix_variations_source_moved"

    def test_ends_a_fix_before_the_builder_is_asked(
        self, environment, source_run, user
    ):
        fix_run = launch_fix_run(
            source_run,
            target_scenario_keys=[PARENT],
            surfaces=[Surface.PROMPT],
            locked_paths=[],
            budget_usd=5.0,
            max_minutes=30,
            user=user,
        )
        _move_default_suite(environment, user)

        assert variations.advance(str(fix_run.id)) == VariationsStep.ENDED

        fix_run.refresh_from_db()
        assert fix_run.failure["code"] == "fix_variations_source_moved"
        assert not HostedHarnessConversationMessage.no_workspace_objects.filter(
            client_request_id=f"fix-{fix_run.id}-variations"
        ).exists()

    def test_ends_a_fix_whose_environment_moved_while_the_builder_wrote(
        self, environment, fix_run, storage, user
    ):
        body = _builder_save(environment)
        _move_default_suite(environment, user)

        with TestCase.captureOnCommitCallbacks(execute=True):
            _land(fix_run, storage, body)

        fix_run.refresh_from_db()
        assert (fix_run.state, fix_run.suite_version_id) == (
            AgentFixRun.State.FAILED,
            None,
        )
        assert fix_run.failure["code"] == "fix_variations_source_moved"


class TestACaseWithoutVariations:
    @pytest.fixture
    def source_run(self, environment):
        """Both scenarios failed "refunded"."""
        run = _run(environment)
        _receipts(run, {key: {"refunded": False} for key in KEYS})
        return run

    @pytest.fixture
    def fix_run(self, source_run, user):
        fix_run = launch_fix_run(
            source_run,
            target_scenario_keys=list(KEYS),
            surfaces=[Surface.PROMPT],
            locked_paths=[],
            budget_usd=5.0,
            max_minutes=30,
            user=user,
        )
        assert variations.advance(str(fix_run.id)) == VariationsStep.WRITING
        fix_run.refresh_from_db()
        return fix_run

    def test_is_counted_and_the_fix_goes_on_with_the_others(
        self, environment, fix_run, storage
    ):
        # Variations of the first case only.
        _land(fix_run, storage, _builder_save(environment))
        names = [_named(fix_run, n) for n in (1, 2, 3)]
        assert _private(fix_run).summary == (
            "3 variations of 1 of 2 failing cases, for an epoch"
        )
        _finish(_base_run(fix_run), failed=names)

        assert variations.advance(str(fix_run.id)) == VariationsStep.DONE

        fix_run.refresh_from_db()
        assert fix_run.request["target_scenario_keys"] == [*KEYS, *names]

    def test_names_it_in_the_reason_when_too_few_failed(
        self, environment, fix_run, storage
    ):
        _land(fix_run, storage, _builder_save(environment))
        _finish(_base_run(fix_run), failed=[_named(fix_run, 1)])

        assert variations.advance(str(fix_run.id)) == VariationsStep.ENDED

        fix_run.refresh_from_db()
        assert fix_run.state == AgentFixRun.State.TOO_FEW_CASES
        assert fix_run.failure["message"] == (
            "The run had 2 failing cases (1 of them got no usable variation). The"
            " builder wrote 3 variations of them, and 1 failed on the current version:"
            " 3 failing cases, and an epoch needs 4."
        )
