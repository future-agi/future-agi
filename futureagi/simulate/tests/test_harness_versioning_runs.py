import hashlib
import json

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from simulate.models import (
    HarnessAgentVersionRuntime,
    HarnessSuiteMember,
    HostedHarnessConversationEvent,
    HostedHarnessConversationMessage,
    HostedHarnessExecution,
    HostedHarnessJob,
    HostedHarnessScenario,
    RunTest,
)
from simulate.models import TestExecution as Execution
from simulate.models.test_execution import CallExecution
from simulate.services.harness_scenarios import (
    authored_scenarios_for_calls,
    index_scenarios,
)
from simulate.services.harness_versioning import archives
from simulate.services.harness_versioning.backfill import backfill_environment
from simulate.services.harness_versioning.versions import record_authoring
from simulate.services.hosted_harness import (
    HostedHarnessError,
    _scenario_edits,
    create_selected_harness_run,
)
from simulate.tests.test_harness_amend_archive import NAMES, _key
from simulate.tests.test_harness_versioning_backfill import (
    _agent_version,
    _definition,
    _legacy,
    _RealisticStorage,
)
from simulate.tests.test_harness_versioning_record import (
    _authoring,
    _resealed_manifest,
    _save_drafts,
)

pytestmark = pytest.mark.django_db


# Copied from fi.alk.harness.job._SECRET_NAMES: the sandbox refuses a job whose fields
# carry a value under one of these names. Keep the two in step.
SECRET_NAMES = {
    "api_key",
    "api_secret",
    "authorization",
    "credential",
    "credentials",
    "password",
    "private_key",
    "secret",
    "token",
}


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True


@pytest.fixture
def storage(monkeypatch):
    from tfc.utils import storage_client

    fake = _RealisticStorage()
    monkeypatch.setattr(storage_client, "_client", fake)
    return fake


def _scenario(name, **fields):
    return {
        "name": name,
        "scenario_key": _key(name),
        "instruction": f"call about {name}",
        "tests": "t",
        **fields,
    }


def _suite_archive(documents):
    """An authoring tree holding these scenario documents, packed like a freeze."""
    files = {
        "contract.json": json.dumps({"agent": "support", "tools": []}).encode(),
        "sub_goals.json": json.dumps(
            {"sub_goals": [{"name": "refunded", "what": "a refund was issued"}]}
        ).encode(),
        "schema.sql": b"create table orders(id int);",
        "scenarios.json": json.dumps(documents).encode(),
        "runtime-validation.json": b'{"status": "passed"}',
    }
    for document in documents:
        folder = f"scenarios/{document['name']}"
        files[f"{folder}/scenario.json"] = json.dumps(document).encode()
        files[f"{folder}/ready.py"] = b"def ready(world):\n    return True\n"
    # A chat reseals the bundle for the scenarios it holds.
    files["environment-bundle/manifest.json"] = _resealed_manifest(
        files, files=[], scenarios=[one.get("scenario_key") for one in documents]
    ).encode()
    return archives.pack_archive(files)


def _versioned(user, workspace, key, storage, *, definition=None, body=None):
    """A backfilled environment: E1, S1 and A1 are its defaults."""
    if definition is None:
        definition = _definition(user, workspace)
        _agent_version(definition)
    environment = _legacy(
        user, workspace, key, storage, definition=definition, body=body
    )
    backfill_environment(environment.id)
    environment.refresh_from_db()
    assert environment.environment_version_id is not None
    return environment


def _run(environment, keys=None, *, trials=1, key="r"):
    run, created = create_selected_harness_run(
        environment,
        scenario_keys=keys or [_key(NAMES[0])],
        trials=trials,
        idempotency_key=key,
    )
    assert created
    run.refresh_from_db()
    return run


def _secret_paths(value, path=()):
    """Where the sandbox would refuse ``value``, by fi.alk.harness.job's own rule."""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            current = (*path, str(key))
            if str(key).lower().replace("-", "_") in SECRET_NAMES and item not in (
                None,
                "",
                {},
                [],
            ):
                found.append(".".join(current))
            found += _secret_paths(item, current)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found += _secret_paths(item, (*path, str(index)))
    return found


def _chat_turn(conversation, storage, archive, sequence, command):
    """A finished chat turn whose last checkpoint is ``archive``; returns its sequence."""
    from simulate.services.hosted_harness_conversation import _workspace_object_key

    digest = "sha256:" + hashlib.sha256(archive).hexdigest()
    storage.objects[_workspace_object_key(conversation, digest)] = archive
    for offset, kind, payload in (
        (0, "checkpoint_committed", {"digest": digest}),
        (1, "turn_completed", {"command_message_id": str(command.id)}),
    ):
        HostedHarnessConversationEvent.no_workspace_objects.create(
            event_id=f"{conversation.id}:{sequence + offset}",
            conversation=conversation,
            sequence=sequence + offset,
            kind=kind,
            payload=payload,
            digest=digest,
            emitted_at=timezone.now(),
        )
    return sequence + 1


def _paired(user, workspace, key, storage):
    """A backfilled environment whose first scenario's row was paired by its name.

    The row is keyed ``slot-1``; the suite's document keeps the scenario's own key.
    """
    definition = _definition(user, workspace)
    _agent_version(definition)
    environment = _legacy(user, workspace, key, storage, definition=definition)
    HostedHarnessScenario.no_workspace_objects.filter(
        job=environment, scenario_key=_key(NAMES[0])
    ).update(scenario_key="slot-1", name=NAMES[0], number=1)
    HostedHarnessScenario.no_workspace_objects.filter(
        job=environment, scenario_key=_key(NAMES[1])
    ).update(number=2)
    backfill_environment(environment.id)
    environment.refresh_from_db()
    assert HarnessSuiteMember.no_workspace_objects.filter(
        suite_version_id=environment.suite_version_id, scenario_key="slot-1"
    ).exists()
    return environment


def _identities(environment):
    return {
        row.id: (row.scenario_key, row.number, row.deleted)
        for row in HostedHarnessScenario.all_objects.filter(job=environment)
    }


def _calls(run):
    return list(
        CallExecution.no_workspace_objects.filter(
            test_execution_id=run.test_execution_id
        )
    )


class TestPins:
    def test_a_run_is_pinned_to_the_environments_defaults(
        self, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "runs-pins", storage)

        run = _run(environment)

        assert run.agent_version_id == environment.agent_version_id
        assert run.environment_version_id == environment.environment_version_id
        assert run.suite_version_id == environment.suite_version_id
        pins = run.payload["metadata"]["version_pins"]
        assert pins == {
            "agent_version_id": str(environment.agent_version_id),
            "environment_version_id": str(environment.environment_version_id),
            "suite_version_id": str(environment.suite_version_id),
            "agent_hash": HarnessAgentVersionRuntime.no_workspace_objects.get(
                agent_version_id=environment.agent_version_id
            ).hash,
            "environment_hash": environment.environment_version.hash,
            "suite_hash": environment.suite_version.hash,
            "assembly_key": run.assembled_bundle.assembly_key,
        }

    def test_the_sandbox_accepts_a_pinned_runs_payload(self, user, workspace, storage):
        environment = _versioned(user, workspace, "runs-pin-names", storage)

        payload = json.loads(json.dumps(_run(environment).payload))
        # The sandbox checks the job without the agent's secret references.
        (payload.get("agent") or {}).pop("secret_refs", None)

        assert "version_pins" in payload["metadata"]
        assert _secret_paths(payload) == []
        assert _secret_paths({"metadata": {"Api-Key": "x"}}) == ["metadata.Api-Key"]

    def test_a_run_leaves_the_builder_chat_state_behind(self, user, workspace, storage):
        environment = _versioned(user, workspace, "runs-chat-state", storage)
        payload = json.loads(json.dumps(environment.payload))
        payload["metadata"]["conversation_checkpoints"] = {
            "conv-1": {"draft_basis": {"token": "abc123"}}
        }
        payload["metadata"]["draft_saves"] = {"key-1": {"pieces": {}}}
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )
        environment.refresh_from_db()

        metadata = _run(environment).payload["metadata"]

        assert "conversation_checkpoints" not in metadata
        assert "draft_saves" not in metadata

    def test_the_test_execution_records_the_pinned_agent(
        self, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "runs-execution", storage)
        agent = environment.agent_version

        run = _run(environment)

        execution = Execution.objects.get(id=run.test_execution_id)
        assert execution.agent_version_id == agent.id
        assert execution.agent_definition_id == agent.agent_definition_id
        assert RunTest.objects.get(id=environment.run_test_id).agent_version_id is None

    def test_the_default_bundle_is_pinned_when_one_exists(
        self, user, workspace, storage
    ):
        digest = "sha256:" + "d" * 64
        body = _authoring(
            **{
                "environment-bundle/manifest.json": json.dumps(
                    {"files": [], "digest": digest}
                ),
                "runtime-validation.json": json.dumps(
                    {"compiler": {"bundle_digest": digest}}
                ),
            }
        )
        environment = _versioned(user, workspace, "runs-bundle", storage, body=body)
        assert environment.assembled_bundle_id is not None

        run = _run(environment)

        assert run.assembled_bundle_id == environment.assembled_bundle_id

    def test_the_run_still_sends_the_environments_agent(self, user, workspace, storage):
        # TODO: send the pinned runtime's agent_payload (versioning step 9).
        environment = _versioned(user, workspace, "runs-agent", storage)

        run = _run(environment)

        assert run.payload["agent"] == environment.payload["agent"]
        assert run.agent_version_id == environment.agent_version_id

    def test_the_environment_is_locked_before_anything_else(
        self, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "runs-lock", storage)

        with CaptureQueriesContext(connection) as queries:
            _run(environment)

        locks = [one["sql"] for one in queries if "FOR UPDATE" in one["sql"]]
        assert locks
        assert f'FROM "{HostedHarnessJob._meta.db_table}"' in locks[0]
        assert str(environment.id).replace("-", "") in locks[0].replace("-", "")

    def test_a_versioned_environment_runs_as_before_with_the_flag_off(
        self, user, workspace, storage, settings
    ):
        environment = _versioned(user, workspace, "runs-flag-off", storage)
        other = _agent_version(environment.run_test.agent_definition)
        RunTest.objects.filter(id=environment.run_test_id).update(agent_version=other)
        HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=environment.suite_version_id,
            scenario_key=_key(NAMES[0]),
        ).update(content={"name": NAMES[0], "max_turns": 9})
        settings.HARNESS_VERSIONING_ENABLED = False

        run = _run(environment)

        assert run.agent_version_id is None
        assert run.environment_version_id is None
        assert run.suite_version_id is None
        assert run.assembled_bundle_id is None
        assert "version_pins" not in run.payload["metadata"]
        # The edits come from the environment's own documents, as before versioning.
        edits = run.payload["metadata"]["scenario_edits"]
        rows = list(
            HostedHarnessScenario.no_workspace_objects.filter(
                job=environment, scenario_key=_key(NAMES[0])
            )
        )
        assert edits == _scenario_edits(environment, rows)
        assert edits.get(_key(NAMES[0]), {}).get("max_turns") != 9
        assert run.stage_outputs == environment.stage_outputs
        execution = Execution.objects.get(id=run.test_execution_id)
        assert execution.agent_version_id == other.id

    def test_a_scenario_outside_the_suite_runs_with_the_flag_off(
        self, user, workspace, storage, settings
    ):
        environment = _versioned(user, workspace, "runs-flag-off-stray", storage)
        HostedHarnessScenario.no_workspace_objects.filter(
            job=environment, scenario_key=_key(NAMES[1])
        ).update(scenario_key="not-in-the-suite")
        settings.HARNESS_VERSIONING_ENABLED = False

        run = _run(environment, ["not-in-the-suite"])

        assert run.payload["metadata"]["selected_scenario_keys"] == ["not-in-the-suite"]

    def test_an_environment_without_an_agent_pin_runs_the_run_tests_agent(
        self, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "runs-no-agent", storage)
        other = _agent_version(environment.run_test.agent_definition)
        RunTest.objects.filter(id=environment.run_test_id).update(agent_version=other)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=None
        )
        environment.refresh_from_db()

        run = _run(environment)

        assert run.agent_version_id is None
        assert run.assembled_bundle_id is None
        assert run.suite_version_id == environment.suite_version_id
        pins = run.payload["metadata"]["version_pins"]
        assert (pins["agent_version_id"], pins["agent_hash"]) == (None, None)
        execution = Execution.objects.get(id=run.test_execution_id)
        assert execution.agent_version_id == other.id
        assert execution.agent_definition_id == other.agent_definition_id

    def test_a_legacy_environment_runs_as_before(self, user, workspace, storage):
        environment = _legacy(user, workspace, "runs-legacy", storage)

        run = _run(environment)

        assert run.agent_version_id is None
        assert run.environment_version_id is None
        assert run.suite_version_id is None
        assert "version_pins" not in run.payload["metadata"]
        execution = Execution.objects.get(id=run.test_execution_id)
        # Recorded, so nothing later falls back to a newer version of the agent.
        assert execution.agent_version_id == (
            execution.agent_definition.latest_version.id
        )

    def test_a_legacy_runs_payload_is_the_environments_but_its_selection(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "runs-legacy-payload", storage)

        run = _run(environment)

        moved = {"scenario_count", "runtime", "metadata", "job_id", "run_id"}
        assert {k: v for k, v in run.payload.items() if k not in moved} == {
            k: v for k, v in environment.payload.items() if k not in moved
        }
        kept = {
            k: v
            for k, v in environment.payload["metadata"].items()
            if k
            not in {
                "scenario_extend",
                "usage_limit",
                "harness_spend",
                "authoring_usage_reports",
                "usage_reports",
                "sandbox_runtime",
            }
        }
        assert run.payload["metadata"].items() >= kept.items()

    def test_a_soft_deleted_agent_pin_is_logged_and_left_off_the_run(
        self, user, workspace, storage
    ):
        from structlog.testing import capture_logs

        from simulate.models import AgentVersion

        environment = _versioned(user, workspace, "runs-agent-deleted", storage)
        assert environment.agent_version_id is not None
        AgentVersion._base_manager.filter(id=environment.agent_version_id).update(
            deleted=True
        )

        with capture_logs() as logs:
            run = _run(environment)

        assert run.agent_version_id is None
        assert run.suite_version_id == environment.suite_version_id
        assert [
            (log["environment_id"], log["agent_version_id"])
            for log in logs
            if log["event"] == "harness_run_agent_pin_missing"
        ] == [(str(environment.id), str(environment.agent_version_id))]


class TestManifest:
    def test_the_manifest_and_count_follow_the_selection(
        self, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "runs-manifest", storage)
        keys = [_key(name) for name in NAMES[:2]]

        run = _run(environment, keys, trials=3)

        manifest = run.payload["metadata"]["execution_manifest"]
        assert run.payload["scenario_count"] == 6
        assert run.scenario_count == 6
        assert len(manifest) == 6
        assert sorted({entry["scenario_key"] for entry in manifest}) == sorted(keys)
        assert set(manifest[0]) >= {
            "execution_key",
            "scenario_key",
            "scenario_id",
            "dataset_row_id",
            "trial_index",
            "call_execution_id",
        }
        assert HostedHarnessExecution.no_workspace_objects.filter(job=run).count() == 6

    def test_two_of_ten_scenarios_three_times_is_six_calls(
        self, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "runs-two-of-ten", storage)
        names = [*NAMES[:2], *(f"extra_{index}" for index in range(8))]
        record_authoring(
            environment,
            _suite_archive([_scenario(name) for name in names]),
            origin="manual",
        )
        environment.refresh_from_db()
        assert (
            HarnessSuiteMember.no_workspace_objects.filter(
                suite_version_id=environment.suite_version_id
            ).count()
            == 10
        )
        keys = [_key("extra_3"), _key(NAMES[1])]

        run = _run(environment, keys, trials=3)

        assert (run.payload["scenario_count"], run.scenario_count) == (6, 6)
        manifest = run.payload["metadata"]["execution_manifest"]
        assert sorted(entry["scenario_key"] for entry in manifest) == sorted(keys * 3)
        assert sorted(entry["trial_index"] for entry in manifest) == [1, 1, 2, 2, 3, 3]
        assert HostedHarnessExecution.no_workspace_objects.filter(job=run).count() == 6
        assert len(_calls(run)) == 6

    def test_only_the_pinned_suites_scenarios_can_be_selected(
        self, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "runs-members", storage)
        stray = HostedHarnessScenario.no_workspace_objects.create(
            job=environment, scenario_key="not-in-the-suite", name="stray"
        )

        with pytest.raises(HostedHarnessError) as error:
            _run(environment, [stray.scenario_key])

        assert error.value.code == "scenario_selection_unknown"

    def test_scenario_edits_come_from_the_pinned_suite(self, user, workspace, storage):
        environment = _versioned(user, workspace, "runs-edits", storage)
        member = HarnessSuiteMember.no_workspace_objects.get(
            suite_version_id=environment.suite_version_id,
            scenario_key=_key(NAMES[0]),
        )
        member.content = {
            **member.content,
            "max_turns": 9,
            "persona": {"accent": "Scottish", "name": "Ada"},
        }
        member.save(update_fields=["content", "updated_at"])
        HostedHarnessScenario.no_workspace_objects.filter(
            job=environment, scenario_key=_key(NAMES[0])
        ).update(max_turns=3)

        edits = _run(environment).payload["metadata"]["scenario_edits"]

        assert edits[_key(NAMES[0])]["max_turns"] == 9
        assert edits[_key(NAMES[0])]["persona"] == {"accent": "Scottish"}

    def test_a_member_never_given_a_platform_scenario_is_bound_first(
        self, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "runs-bind", storage)
        dropped = _key(NAMES[1])
        HostedHarnessScenario.no_workspace_objects.filter(
            job=environment, scenario_key=dropped
        ).update(scenario=None, dataset_row=None, deleted=True)
        unrelated = HostedHarnessScenario.no_workspace_objects.create(
            job=environment, scenario_key="added-elsewhere", name="added elsewhere"
        )

        run = _run(environment, [dropped])

        row = HostedHarnessScenario.all_objects.get(
            job=environment, scenario_key=dropped
        )
        assert row.scenario_id is not None
        assert [
            entry["scenario_id"]
            for entry in run.payload["metadata"]["execution_manifest"]
        ] == [str(row.scenario_id)]
        unrelated.refresh_from_db()
        assert unrelated.scenario_id is None

    def test_a_member_that_cannot_be_bound_is_refused(
        self, user, workspace, storage, monkeypatch
    ):
        from simulate.services import hosted_harness_conversation

        environment = _versioned(user, workspace, "runs-unbound", storage)
        HostedHarnessScenario.no_workspace_objects.filter(
            job=environment, scenario_key=_key(NAMES[0])
        ).update(scenario=None, dataset_row=None)
        monkeypatch.setattr(
            hosted_harness_conversation,
            "_bind_added_scenarios",
            lambda environment, rows=None: None,
        )

        with pytest.raises(HostedHarnessError) as error:
            _run(environment)

        assert (error.value.code, error.value.status_code) == (
            "scenario_unavailable",
            409,
        )

    def test_a_bind_that_fails_is_a_409(self, user, workspace, storage, monkeypatch):
        from simulate.services import hosted_harness_conversation

        environment = _versioned(user, workspace, "runs-bind-fails", storage)
        HostedHarnessScenario.no_workspace_objects.filter(
            job=environment, scenario_key=_key(NAMES[0])
        ).update(scenario=None, dataset_row=None)

        def fail(environment, rows=None):
            raise HostedHarnessError(
                "scenario_provision_failed", "provisioning failed", status_code=422
            )

        monkeypatch.setattr(hosted_harness_conversation, "_bind_added_scenarios", fail)

        with pytest.raises(HostedHarnessError) as error:
            _run(environment)

        assert (error.value.code, error.value.status_code) == (
            "scenario_unavailable",
            409,
        )

    def test_a_rerun_selects_the_default_suites_scenarios_only(
        self, user, workspace, storage, settings
    ):
        from unittest.mock import patch

        from simulate.services.harness_provider import HostedHarnessProvider

        settings.HARNESS_PUBLIC_BASE_URL = "https://harness.example.test"
        environment = _versioned(user, workspace, "runs-rerun-orphan", storage)
        HostedHarnessScenario.no_workspace_objects.create(
            job=environment, scenario_key="orphan", name="orphan"
        )

        with patch("simulate.temporal.client.start_hosted_harness_gateway_workflow"):
            result = HostedHarnessProvider().rerun_saved(
                str(environment.id),
                organization=user.organization,
                workspace=workspace,
                environment_values={},
            )

        run = HostedHarnessJob.no_workspace_objects.get(id=result["job"]["job_id"])
        assert run.payload["metadata"]["selected_scenario_keys"] == [
            _key(name) for name in NAMES[:2]
        ]
        assert run.suite_version_id == environment.suite_version_id

    def test_a_rerun_with_the_flag_off_selects_as_before(
        self, user, workspace, storage, settings
    ):
        from unittest.mock import patch

        from simulate.services.harness_provider import HostedHarnessProvider

        settings.HARNESS_PUBLIC_BASE_URL = "https://harness.example.test"
        environment = _versioned(user, workspace, "runs-rerun-flag-off", storage)
        HostedHarnessScenario.no_workspace_objects.filter(
            job=environment, scenario_key=_key(NAMES[1])
        ).update(scenario_key="not-in-the-suite")
        settings.HARNESS_VERSIONING_ENABLED = False

        with patch("simulate.temporal.client.start_hosted_harness_gateway_workflow"):
            result = HostedHarnessProvider().rerun_saved(
                str(environment.id),
                organization=user.organization,
                workspace=workspace,
                environment_values={},
            )

        run = HostedHarnessJob.no_workspace_objects.get(id=result["job"]["job_id"])
        assert run.payload["metadata"]["selected_scenario_keys"] == [
            _key(NAMES[0]),
            "not-in-the-suite",
        ]
        assert run.suite_version_id is None
        assert "version_pins" not in run.payload["metadata"]

    def _rerun_dropped(self, user, workspace, storage, key):
        """A rerun of a run whose scenario the environment's new suite dropped."""
        from unittest.mock import patch

        from simulate.services.harness_provider import HostedHarnessProvider

        environment = _versioned(user, workspace, key, storage)
        source = _run(environment, [_key(NAMES[1])])
        HostedHarnessJob.no_workspace_objects.filter(id=source.id).update(
            state=HostedHarnessJob.State.COMPLETED
        )
        record_authoring(
            environment, _suite_archive([_scenario(NAMES[0])]), origin="manual"
        )
        with patch("simulate.temporal.client.start_hosted_harness_gateway_workflow"):
            result = HostedHarnessProvider().rerun_saved(
                str(source.id),
                organization=user.organization,
                workspace=workspace,
                environment_values={},
            )
        environment.refresh_from_db()
        return environment, source, result

    def test_a_rerun_of_a_pinned_run_keeps_its_suite(
        self, user, workspace, storage, settings
    ):
        settings.HARNESS_PUBLIC_BASE_URL = "https://harness.example.test"
        # The source's versions are not cached, so the rerun certifies them.
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True

        environment, source, result = self._rerun_dropped(
            user, workspace, storage, "runs-rerun-dropped"
        )

        run = HostedHarnessJob.no_workspace_objects.get(id=result["job"]["job_id"])
        assert run.suite_version_id == source.suite_version_id
        assert run.suite_version_id != environment.suite_version_id
        assert run.payload["metadata"]["selected_scenario_keys"] == [_key(NAMES[1])]
        assert run.payload["metadata"]["rerun_of"] == str(source.id)

    def test_a_rerun_that_cannot_run_its_sources_suite_selects_from_the_defaults(
        self, user, workspace, storage, settings
    ):
        settings.HARNESS_PUBLIC_BASE_URL = "https://harness.example.test"
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False

        with pytest.raises(HostedHarnessError) as error:
            self._rerun_dropped(user, workspace, storage, "runs-rerun-uncached")

        assert (error.value.code, error.value.status_code) == (
            "scenario_selection_unknown",
            400,
        )

    def test_the_environment_output_stays_the_sealed_bundles(
        self, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "runs-outputs-env", storage)
        sealed = {"kind": "environment", "data": {"manifest": "sealed"}}
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            stage_outputs=[sealed, {"kind": "scenarios", "data": ["stale"]}]
        )
        version = environment.environment_version
        version.documents = {
            **version.documents,
            "stage_outputs": [{"kind": "environment", "data": {"manifest": "older"}}],
        }
        version.save(update_fields=["documents"])
        environment.refresh_from_db()

        outputs = {
            one["kind"]: one for one in _run(environment).stage_outputs if "kind" in one
        }

        assert outputs["environment"] == sealed
        assert outputs["scenarios"]["data"] != ["stale"]

    def test_stage_outputs_come_from_the_pinned_versions(
        self, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "runs-outputs", storage)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            stage_outputs=[{"kind": "scenarios", "data": ["stale"]}]
        )
        environment.refresh_from_db()

        outputs = {
            one["kind"]: one for one in _run(environment).stage_outputs if "kind" in one
        }

        scenarios = outputs["scenarios"]["data"]
        assert scenarios != ["stale"]
        assert {one["name"] for one in scenarios} == set(NAMES[:2])


class TestHistory:
    def test_a_run_on_s1_keeps_s1_content_after_s2_is_default(
        self, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "runs-history", storage)
        run = _run(environment)
        record_authoring(
            environment,
            _suite_archive(
                [
                    _scenario(NAMES[0], instruction="call about a refund"),
                    _scenario(NAMES[1]),
                ]
            ),
            origin="chat",
        )
        environment.refresh_from_db()
        assert environment.suite_version.version_number == 2
        row = HostedHarnessScenario.no_workspace_objects.get(
            job=environment, scenario_key=_key(NAMES[0])
        )
        assert row.instruction == "call about a refund"

        shown = authored_scenarios_for_calls(
            environment.run_test_id,
            _calls(run),
            test_execution_id=run.test_execution_id,
        )

        assert {one.instruction for one in shown.values()} == {f"call about {NAMES[0]}"}
        row.refresh_from_db()
        assert row.instruction == "call about a refund"

    def test_a_scenario_dropped_by_s2_still_resolves_for_a_run_on_s1(
        self, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "runs-dropped", storage)
        dropped = _key(NAMES[1])
        run = _run(environment, [dropped])
        record_authoring(
            environment, _suite_archive([_scenario(NAMES[0])]), origin="manual"
        )
        assert HostedHarnessScenario.all_objects.get(
            job=environment, scenario_key=dropped
        ).deleted

        shown = authored_scenarios_for_calls(
            environment.run_test_id,
            _calls(run),
            test_execution_id=run.test_execution_id,
        )

        assert [one.scenario_key for one in shown.values()] == [dropped]
        assert all(
            one.instruction == f"call about {NAMES[1]}" for one in shown.values()
        )

    def test_a_legacy_run_keeps_the_identity_rows_content(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "runs-history-legacy", storage)
        run = _run(environment)
        HostedHarnessScenario.no_workspace_objects.filter(
            job=environment, scenario_key=_key(NAMES[0])
        ).update(instruction="edited in place")

        shown = authored_scenarios_for_calls(
            environment.run_test_id,
            _calls(run),
            test_execution_id=run.test_execution_id,
        )

        assert {one.instruction for one in shown.values()} == {"edited in place"}


class TestRetiredKeys:
    def _drop_second(self, environment):
        record_authoring(
            environment, _suite_archive([_scenario(NAMES[0])]), origin="manual"
        )

    def test_a_dropped_key_is_refused_when_a_person_adds_it_back(
        self, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "runs-retired", storage)
        self._drop_second(environment)
        environment.refresh_from_db()
        before = environment.suite_version_id

        with pytest.raises(HostedHarnessError) as error:
            record_authoring(
                environment,
                _suite_archive([_scenario(NAMES[0]), _scenario(NAMES[1])]),
                origin="chat",
            )

        assert (error.value.code, error.value.status_code) == (
            "scenario_key_retired",
            422,
        )
        assert _key(NAMES[1]) in error.value.message
        environment.refresh_from_db()
        assert environment.suite_version_id == before

    def test_with_the_flag_off_a_dropped_key_is_still_refused(
        self, user, workspace, storage, settings
    ):
        environment = _versioned(user, workspace, "runs-retired-flag-off", storage)
        self._drop_second(environment)
        settings.HARNESS_VERSIONING_ENABLED = False

        with pytest.raises(HostedHarnessError) as error:
            record_authoring(
                environment,
                _suite_archive([_scenario(NAMES[0]), _scenario(NAMES[1])]),
                origin="manual",
            )

        assert error.value.code == "scenario_key_retired"

    def test_the_suite_saves_once_the_scenario_is_renamed(
        self, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "runs-retired-renamed", storage)
        self._drop_second(environment)
        renamed = {**_scenario(NAMES[1]), "scenario_key": "a-fresh-key"}

        record_authoring(
            environment,
            _suite_archive([_scenario(NAMES[0]), renamed]),
            origin="chat",
        )

        environment.refresh_from_db()
        assert set(
            HarnessSuiteMember.no_workspace_objects.filter(
                suite_version_id=environment.suite_version_id
            ).values_list("scenario_key", flat=True)
        ) == {_key(NAMES[0]), "a-fresh-key"}

    def test_a_chat_save_that_is_refused_tells_the_person_why(
        self, user, workspace, storage
    ):
        from simulate.services import hosted_harness_conversation
        from simulate.tasks import hosted_harness_conversation as tasks

        promote = tasks.promote_hosted_harness_conversation_checkpoint._original_func
        environment = _versioned(user, workspace, "runs-retired-chat", storage)
        self._drop_second(environment)
        environment.refresh_from_db()
        before = environment.suite_version_id
        conversation, command, _ = hosted_harness_conversation.enqueue_message(
            environment, content="add it back", client_request_id="retired"
        )
        turn = _chat_turn(
            conversation,
            storage,
            _suite_archive([_scenario(NAMES[0]), _scenario(NAMES[1])]),
            1,
            command,
        )

        assert promote(str(conversation.id), turn) == ""

        message = HostedHarnessConversationMessage.no_workspace_objects.filter(
            conversation=conversation,
            role=HostedHarnessConversationMessage.Role.ASSISTANT,
        ).get()
        assert message.state == HostedHarnessConversationMessage.State.FAILED
        assert message.content.startswith("These changes were not saved: ")
        assert _key(NAMES[1]) in message.content
        environment.refresh_from_db()
        assert environment.suite_version_id == before

        renamed = {**_scenario(NAMES[1]), "scenario_key": "a-fresh-key"}
        turn = _chat_turn(
            conversation,
            storage,
            _suite_archive([_scenario(NAMES[0]), renamed]),
            turn + 1,
            command,
        )

        assert promote(str(conversation.id), turn) == str(environment.id)
        # Since drafts, the chat's save is made live by Save.
        _save_drafts(environment)
        assert environment.suite_version_id != before
        assert set(
            HarnessSuiteMember.no_workspace_objects.filter(
                suite_version_id=environment.suite_version_id
            ).values_list("scenario_key", flat=True)
        ) == {_key(NAMES[0]), "a-fresh-key"}

    def test_a_chat_save_taking_a_paired_rows_key_tells_the_person_why(
        self, user, workspace, storage
    ):
        from simulate.services import hosted_harness_conversation
        from simulate.tasks import hosted_harness_conversation as tasks

        promote = tasks.promote_hosted_harness_conversation_checkpoint._original_func
        environment = _paired(user, workspace, "runs-taken-chat", storage)
        before = _identities(environment)
        conversation, command, _ = hosted_harness_conversation.enqueue_message(
            environment, content="add one", client_request_id="taken"
        )
        added = {**_scenario("added"), "scenario_key": "slot-1"}
        turn = _chat_turn(
            conversation,
            storage,
            _suite_archive([_scenario(NAMES[0]), _scenario(NAMES[1]), added]),
            1,
            command,
        )

        assert promote(str(conversation.id), turn) == ""

        message = HostedHarnessConversationMessage.no_workspace_objects.filter(
            conversation=conversation,
            role=HostedHarnessConversationMessage.Role.ASSISTANT,
        ).get()
        assert message.state == HostedHarnessConversationMessage.State.FAILED
        assert "slot-1" in message.content
        assert _identities(environment) == before

    def test_a_key_the_default_suites_documents_hold_is_never_refused(
        self, user, workspace, storage
    ):
        environment = _paired(user, workspace, "runs-retired-paired", storage)
        twin = HostedHarnessScenario.all_objects.create(
            job=environment,
            scenario_key=_key(NAMES[0]),
            name="dropped long ago",
            deleted=True,
        )
        before = _identities(environment)

        record_authoring(
            environment,
            _suite_archive(
                [_scenario(NAMES[0], instruction="edited"), _scenario(NAMES[1])]
            ),
            origin="chat",
        )

        assert _identities(environment) == before
        twin.refresh_from_db()
        assert twin.deleted
        assert (
            HostedHarnessScenario.no_workspace_objects.get(
                job=environment, scenario_key="slot-1"
            ).instruction
            == "edited"
        )

    def test_a_build_may_bring_a_dropped_key_back(self, user, workspace, storage):
        environment = _versioned(user, workspace, "runs-retired-build", storage)
        self._drop_second(environment)

        record_authoring(
            environment,
            _suite_archive([_scenario(NAMES[0]), _scenario(NAMES[1])]),
            origin="extend",
        )

        assert not HostedHarnessScenario.all_objects.get(
            job=environment, scenario_key=_key(NAMES[1])
        ).deleted


class TestExactKeys:
    def test_a_new_key_never_revives_a_dropped_scenario_by_its_name(
        self, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "runs-exact", storage)
        record_authoring(
            environment, _suite_archive([_scenario(NAMES[0])]), origin="manual"
        )
        environment.refresh_from_db()
        renamed = {**_scenario(NAMES[1]), "scenario_key": "a-brand-new-key"}

        index_scenarios(environment, [_scenario(NAMES[0]), renamed], prune=True)

        live = set(
            HostedHarnessScenario.no_workspace_objects.filter(
                job=environment
            ).values_list("scenario_key", flat=True)
        )
        assert live == {_key(NAMES[0]), "a-brand-new-key"}
        assert HostedHarnessScenario.all_objects.get(
            job=environment, scenario_key=_key(NAMES[1])
        ).deleted

    def test_a_legacy_environment_matches_only_a_keyless_document_by_name(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "runs-exact-legacy", storage)
        rekeyed = {**_scenario(NAMES[0]), "scenario_key": "a-brand-new-key"}
        keyless = {"name": NAMES[1], "instruction": "no key here"}

        index_scenarios(environment, [rekeyed, keyless], prune=True)

        live = set(
            HostedHarnessScenario.no_workspace_objects.filter(
                job=environment
            ).values_list("scenario_key", flat=True)
        )
        assert live == {"a-brand-new-key", _key(NAMES[1])}

    def test_a_document_without_a_key_falls_back_to_its_name(
        self, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "runs-exact-keyless", storage)
        keyless = {"name": NAMES[0], "instruction": "no key here"}

        index_scenarios(environment, [keyless, _scenario(NAMES[1])], prune=True)

        row = HostedHarnessScenario.no_workspace_objects.get(
            job=environment, scenario_key=_key(NAMES[0])
        )
        assert row.instruction == "no key here"

    def test_a_row_a_backfill_paired_by_name_keeps_its_identity(
        self, user, workspace, storage
    ):
        environment = _paired(user, workspace, "runs-exact-paired", storage)
        before = _identities(environment)

        record_authoring(
            environment,
            _suite_archive([_scenario(NAMES[0]), _scenario(NAMES[1])]),
            origin="manual",
        )

        assert _identities(environment) == before

    def test_renaming_a_scenario_paired_by_name_keeps_its_row(
        self, user, workspace, storage
    ):
        environment = _paired(user, workspace, "runs-exact-paired-rename", storage)
        row = HostedHarnessScenario.no_workspace_objects.get(
            job=environment, scenario_key="slot-1"
        )

        record_authoring(
            environment,
            _suite_archive(
                [{**_scenario(NAMES[0]), "name": "renamed"}, _scenario(NAMES[1])]
            ),
            origin="manual",
        )

        row.refresh_from_db()
        assert (row.scenario_key, row.name, row.number, row.deleted) == (
            "slot-1",
            "renamed",
            1,
            False,
        )
        assert HostedHarnessScenario.all_objects.filter(job=environment).count() == 2

    def test_dropping_a_scenario_and_adding_one_by_its_name_makes_a_new_row(
        self, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "runs-exact-same-name", storage)
        old = HostedHarnessScenario.no_workspace_objects.get(
            job=environment, scenario_key=_key(NAMES[1])
        )
        added = {**_scenario(NAMES[1]), "scenario_key": "a-brand-new-key"}

        record_authoring(
            environment,
            _suite_archive([_scenario(NAMES[0]), added]),
            origin="manual",
        )

        old.refresh_from_db()
        assert old.deleted
        new = HostedHarnessScenario.no_workspace_objects.get(
            job=environment, scenario_key="a-brand-new-key"
        )
        assert new.id != old.id
        assert new.name == NAMES[1]
        assert new.number not in {None, old.number}

    def test_a_runs_own_rows_still_match_a_keyless_document_by_name(
        self, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "runs-exact-run", storage)
        run = _run(environment)
        assert run.environment_version_id is not None
        row = HostedHarnessScenario.no_workspace_objects.create(
            job=run, scenario_key=_key(NAMES[0]), name=NAMES[0]
        )

        index_scenarios(run, [{"name": NAMES[0], "instruction": "no key here"}])

        assert list(
            HostedHarnessScenario.all_objects.filter(job=run).values_list(
                "id", "scenario_key", "instruction"
            )
        ) == [(row.id, _key(NAMES[0]), "no key here")]

    def test_a_person_may_not_take_the_key_of_a_row_paired_by_name(
        self, user, workspace, storage
    ):
        environment = _paired(user, workspace, "runs-exact-taken", storage)
        before = _identities(environment)
        suite = environment.suite_version_id
        added = {**_scenario("added"), "scenario_key": "slot-1"}

        with pytest.raises(HostedHarnessError) as error:
            record_authoring(
                environment,
                _suite_archive([_scenario(NAMES[0]), _scenario(NAMES[1]), added]),
                origin="manual",
            )

        assert (error.value.code, error.value.status_code) == (
            "scenario_key_taken",
            422,
        )
        assert "slot-1" in error.value.message
        assert _identities(environment) == before
        environment.refresh_from_db()
        assert environment.suite_version_id == suite

    def test_a_build_keyed_like_a_row_paired_by_name_gets_a_row_of_its_own(
        self, user, workspace, storage
    ):
        environment = _paired(user, workspace, "runs-exact-taken-build", storage)
        paired = HostedHarnessScenario.no_workspace_objects.get(
            job=environment, scenario_key="slot-1"
        )
        added = {**_scenario("added"), "scenario_key": "slot-1"}

        # Listed first, so it reaches the paired row before that row's own document.
        record_authoring(
            environment,
            _suite_archive([added, _scenario(NAMES[0]), _scenario(NAMES[1])]),
            origin="extend",
        )
        environment.refresh_from_db()
        after = _identities(environment)
        # A later version re-pairs from these members and must keep every row.
        record_authoring(
            environment,
            _suite_archive(
                [
                    {**added, "instruction": "edited"},
                    _scenario(NAMES[0]),
                    _scenario(NAMES[1]),
                ]
            ),
            origin="extend",
        )
        environment.refresh_from_db()

        paired.refresh_from_db()
        assert (paired.scenario_key, paired.name, paired.deleted) == (
            "slot-1",
            NAMES[0],
            False,
        )
        own = HostedHarnessScenario.no_workspace_objects.get(
            job=environment, scenario_key="slot-1-2"
        )
        assert (own.name, own.instruction) == ("added", "edited")
        assert _identities(environment) == after
        assert {
            member.scenario_key: member.content["name"]
            for member in HarnessSuiteMember.no_workspace_objects.filter(
                suite_version_id=environment.suite_version_id
            )
        } == {"slot-1": NAMES[0], _key(NAMES[1]): NAMES[1], "slot-1-2": "added"}

    def test_renaming_a_paired_scenario_to_another_rows_key_loses_nothing(
        self, user, workspace, storage
    ):
        environment = _paired(user, workspace, "runs-exact-rename-clash", storage)
        before = _identities(environment)

        record_authoring(
            environment,
            _suite_archive(
                [{**_scenario(NAMES[0]), "name": _key(NAMES[1])}, _scenario(NAMES[1])]
            ),
            origin="manual",
        )

        environment.refresh_from_db()
        assert _identities(environment) == before
        assert {
            member.scenario_key: member.content["name"]
            for member in HarnessSuiteMember.no_workspace_objects.filter(
                suite_version_id=environment.suite_version_id
            )
        } == {"slot-1": _key(NAMES[1]), _key(NAMES[1]): NAMES[1]}

    def test_with_the_flag_off_a_paired_row_keeps_its_identity(
        self, user, workspace, storage, settings
    ):
        environment = _paired(user, workspace, "runs-exact-flag-off", storage)
        before = _identities(environment)
        settings.HARNESS_VERSIONING_ENABLED = False

        record_authoring(
            environment,
            _suite_archive(
                [{**_scenario(NAMES[0]), "name": "renamed"}, _scenario(NAMES[1])]
            ),
            origin="manual",
        )

        assert _identities(environment) == before
        assert (
            HostedHarnessScenario.no_workspace_objects.get(
                job=environment, scenario_key="slot-1"
            ).name
            == "renamed"
        )
