"""An environment-owned agent through provision, defaults and runs."""

import json
from unittest.mock import patch

import pytest

from accounts.models.workspace import Workspace
from simulate.models import (
    AgentDefinition,
    AgentVersion,
    HarnessAgentVersionRuntime,
    HostedHarnessJob,
)
from simulate.models.run_test import RunTest
from simulate.services import harness_agent_versions as service
from simulate.services import hosted_harness
from simulate.services.harness_agent_versions import (
    agent_version_secret_ref,
    create_agent_version,
    discard_agent_version,
    environment_credential_values,
    fill_agent_pin,
    mirror_default_status,
)
from simulate.services.harness_provider import HostedHarnessProvider
from simulate.services.harness_versioning.backfill import backfill_environment
from simulate.services.harness_versioning.versions import move_agent, record_authoring
from simulate.services.hosted_harness import (
    HostedHarnessError,
    agent_version_pickable,
    begin_scenarios,
    create_hosted_job,
    create_selected_harness_run,
    provision_scenarios,
    register_attempt,
)
from simulate.services.hosted_harness_gateway import PlatformSecretResolver
from simulate.tests.test_harness_agent_creation import (
    _body,
    _job,
    _phone,
    _post,
    _side,
    _vapi,
)
from simulate.tests.test_harness_agent_creation import (
    client as client,
)
from simulate.tests.test_harness_agent_creation import (
    gates as gates,
)
from simulate.tests.test_harness_amend_archive import NAMES, _key
from simulate.tests.test_harness_provider import _v1_payload
from simulate.tests.test_harness_versioning_backfill import (
    storage as storage,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_versioning_record import _authoring
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

pytestmark = pytest.mark.django_db

VALUES = {"VAPI_API_KEY": "val-vapi-1", "DB_URL": "val-db-1"}


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    settings.HARNESS_PROVIDER = "hosted"


def _provision(job, *, prompt="P1", modality="text", names=NAMES[:2], **extra):
    attempt = register_attempt(
        job.id, endpoint_base_url="https://harness.example.test"
    ).attempt
    body = {
        "operation": "provision",
        "name": "Suite",
        "agent_prompt": prompt,
        "personas": [
            {
                "scenario_key": _key(name),
                "name": name,
                "role": "customer",
                "situation": "s",
                "outcome": "o",
                "persona": {"name": name},
            }
            for name in names
        ],
    }
    if modality is not None:
        body["modality"] = modality
    result = provision_scenarios(attempt, {**body, **extra})
    job.refresh_from_db()
    return result


def _complete(job):
    payload = dict(job.payload)
    payload["metadata"] = {
        **payload["metadata"],
        "authoring_object_key": f"harness/environments/{job.id}.tar.gz",
    }
    HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
        payload=payload, state=HostedHarnessJob.State.COMPLETED
    )
    job.refresh_from_db()
    return job


def _run(environment, key="r"):
    run, _ = create_selected_harness_run(
        environment, scenario_keys=[_key(NAMES[0])], trials=1, idempotency_key=key
    )
    return run


def _agent_state(definition_id):
    definition = AgentDefinition.no_workspace_objects.get(id=definition_id)
    versions = AgentVersion._base_manager.filter(agent_definition_id=definition_id)
    return (
        {
            name: getattr(definition, name)
            for name in (
                "description",
                "agent_type",
                "contact_number",
                "inbound",
                "target_speaks_first",
                "assistant_id",
                "provider",
            )
        },
        sorted(
            (
                str(one.id),
                one.status,
                one.description,
                json.dumps(one.configuration_snapshot),
            )
            for one in versions
        ),
    )


class TestProvision:
    @pytest.mark.parametrize("context", ["none", "same", "other"])
    def test_the_first_provision_binds_the_environments_agent(
        self, client, user, workspace, gates, context
    ):
        job = _job(_post(client, workspace, _body(values={"DB_URL": "val-db-1"})))
        a1 = job.agent_version
        runtime = _side(a1.id)
        definitions = AgentDefinition.no_workspace_objects.count()
        versions = AgentVersion._base_manager.count()
        clear_workspace_context()
        if context != "none":
            # A request's workspace may differ from the job's: the bind holds anyway.
            current = workspace
            if context == "other":
                current = Workspace.objects.create(
                    name="Other", organization=user.organization, created_by=user
                )
            set_workspace_context(organization=user.organization, workspace=current)

        _provision(job, prompt="P1")

        assert AgentDefinition.no_workspace_objects.count() == definitions
        assert AgentVersion._base_manager.count() == versions
        run_test = RunTest.no_workspace_objects.get(id=job.run_test_id)
        assert run_test.agent_definition_id == a1.agent_definition_id
        definition = AgentDefinition.no_workspace_objects.get(id=a1.agent_definition_id)
        assert definition.description == "P1"
        a1.refresh_from_db()
        assert a1.configuration_snapshot["description"] == "P1"
        assert a1.description == ""
        after = _side(a1.id)
        assert (after.hash, after.prompt_hash) == (runtime.hash, runtime.prompt_hash)
        assert fill_agent_pin(job) is False
        assert job.agent_version_id == a1.id

    def test_an_auto_agent_takes_the_authored_modality(
        self, client, user, workspace, gates
    ):
        job = _job(_post(client, workspace, _body(values={"DB_URL": "val-db-1"})))

        _provision(job, modality="voice")

        definition = job.agent_version.agent_definition
        definition.refresh_from_db()
        assert definition.agent_type == "voice"

    def test_a_voice_connector_keeps_its_modality_when_none_is_said(
        self, client, user, workspace, gates
    ):
        job = _job(_post(client, workspace, _vapi()))
        selected = []

        def select(job, run_test, payload, modality):
            selected.append(modality)

        with (
            patch.object(hosted_harness, "_select_platform_evals", side_effect=select),
            patch.object(hosted_harness, "logger") as log,
        ):
            _provision(job, modality=None)

        definition = job.agent_version.agent_definition
        definition.refresh_from_db()
        assert definition.agent_type == "voice"
        assert selected == ["voice"]
        events = [call.args[0] for call in log.info.call_args_list]
        assert "harness_modality_overridden" in events

    def test_a_guest_named_agent_never_replaces_the_environments_own(
        self, client, user, workspace, gates
    ):
        own = _job(_post(client, workspace, _phone()))
        other = _job(_post(client, workspace, _phone(prompt="Other line.")))
        other_id = other.agent_version.agent_definition_id
        before = _agent_state(other_id)

        _provision(
            own, prompt="P1", modality="voice", agent_definition_id=str(other_id)
        )

        run_test = RunTest.no_workspace_objects.get(id=own.run_test_id)
        assert run_test.agent_definition_id == own.agent_version.agent_definition_id
        assert _agent_state(other_id) == before


class TestWriteOnce:
    def test_later_provisions_and_runs_write_nothing_to_the_agent(
        self, client, user, workspace, gates
    ):
        job = _job(_post(client, workspace, _vapi()))
        _provision(job)
        definition_id = job.agent_version.agent_definition_id
        before = _agent_state(definition_id)

        _provision(job, prompt="P-again")
        _provision(job, prompt="P-extend", names=NAMES[:3])
        _run(_complete(job))

        assert _agent_state(definition_id) == before

    def test_a_second_environment_fills_only_what_the_first_left_blank(
        self, client, user, workspace, gates
    ):
        first = _job(_post(client, workspace, _vapi(target_speaks_first=True)))
        _provision(first, prompt="P1")
        definition_id = first.agent_version.agent_definition_id
        before = _agent_state(definition_id)
        second_body = _vapi(key="val-vapi-2", target_speaks_first=False)
        second_body["agent"]["call_direction"] = "outbound"
        second = _job(_post(client, workspace, second_body))
        assert second.agent_version.agent_definition_id == definition_id
        versions = AgentVersion._base_manager.filter(
            agent_definition_id=definition_id
        ).count()

        _provision(second, prompt="P2")

        columns, _ = _agent_state(definition_id)
        assert columns == before[0]
        assert columns["description"] == "P1"
        assert (
            AgentVersion._base_manager.filter(agent_definition_id=definition_id).count()
            == versions
        )

    def test_a_legacy_job_still_gets_its_prompt_version(self, user, workspace):
        job, _ = create_hosted_job(
            user.organization,
            _v1_payload(scenario_count=2),
            idempotency_key="legacy",
            workspace=workspace,
        )

        _provision(job, prompt="P-legacy")

        run_test = RunTest.objects.get(id=job.run_test_id)
        definition = run_test.agent_definition
        assert definition.description == "P-legacy"
        assert definition.latest_version.description == "P-legacy"
        assert not service.environment_owned(definition.id)


class TestMirror:
    def test_the_default_shows_as_the_active_version(
        self, client, user, workspace, gates
    ):
        job = _job(_post(client, workspace, _vapi()))
        a1 = job.agent_version
        a2 = create_agent_version(
            a1.agent_definition,
            parent=a1,
            env=job,
            secret_values={"DB_URL": "val-db-1"},
            trigger="credentials",
            activate=False,
        )
        draft = create_agent_version(
            a1.agent_definition,
            parent=a1,
            env=job,
            secret_values={"DB_URL": "val-db-2"},
            trigger="credentials",
            activate=False,
        )

        def statuses():
            return {
                one.id: one.status
                for one in AgentVersion._base_manager.filter(
                    id__in=[a1.id, a2.id, draft.id]
                )
            }

        move_agent(job, a2)
        assert statuses() == {a1.id: "archived", a2.id: "active", draft.id: "draft"}
        move_agent(job, a1)
        assert statuses() == {a1.id: "active", a2.id: "archived", draft.id: "draft"}
        discard_agent_version(a2)
        assert AgentVersion._base_manager.get(id=a2.id).status == "deprecated"

    def test_mirrors_of_one_agent_take_turns(self, client, user, workspace, gates):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        version = _job(_post(client, workspace, _vapi())).agent_version

        with CaptureQueriesContext(connection) as queries:
            mirror_default_status(version)

        sql = [query["sql"] for query in queries.captured_queries]
        lock = next(i for i, one in enumerate(sql) if "pg_advisory_xact_lock" in one)
        assert lock < next(i for i, one in enumerate(sql) if one.startswith("UPDATE"))

    def test_a_version_with_its_own_settings_stays_environment_owned(
        self, client, user, workspace, gates
    ):
        job = _job(_post(client, workspace, _vapi()))
        a1 = job.agent_version

        a2 = create_agent_version(
            a1.agent_definition,
            parent=a1,
            env=job,
            agent_payload={"connector": "vapi", "config": {"assistant_id": "asst-1"}},
            trigger="agent_payload",
            activate=False,
        )

        payload = HarnessAgentVersionRuntime.no_workspace_objects.get(
            agent_version=a2
        ).agent_payload
        assert payload["definition_owner"] == "environment"

    def test_another_agents_statuses_are_left_alone(self, user, workspace):
        definition = AgentDefinition.no_workspace_objects.create(
            organization=user.organization,
            workspace=workspace,
            agent_name="Theirs",
            agent_type="voice",
            inbound=True,
            description="",
        )
        active = definition.create_version(
            description="a", commit_message="a", status="active"
        )
        other = definition.create_version(
            description="b", commit_message="b", status="draft"
        )

        mirror_default_status(other)

        assert AgentVersion._base_manager.get(id=active.id).status == "active"
        assert AgentVersion._base_manager.get(id=other.id).status == "draft"


class TestVersions:
    def test_the_first_versions_are_built_with_the_agents_first_version(
        self, client, user, workspace, gates, storage
    ):
        job = _job(_post(client, workspace, _vapi()))
        _provision(job)
        _complete(job)

        record_authoring(job, _authoring(), origin="authoring")

        job.refresh_from_db()
        assert job.environment_version.agent_version_id == job.agent_version_id
        response = client.get(
            f"/simulate/api/harness-environments/{job.id}/versions/",
            HTTP_X_WORKSPACE_ID=str(workspace.id),
        )
        assert response.status_code == 200, response.content
        agents = response.json()["agent"]
        assert [item["id"] for item in agents] == [str(job.agent_version_id)]
        assert agents[0]["is_default"] is True
        assert agents[0]["preflight_status"] == "passed"

    def test_the_backfill_leaves_the_first_version_alone(
        self, client, user, workspace, gates, storage
    ):
        job = _job(_post(client, workspace, _vapi()))
        _provision(job)
        _complete(job)
        storage.objects[job.payload["metadata"]["authoring_object_key"]] = _authoring()
        before = HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=job.agent_version_id
        ).values()[0]

        backfill_environment(job.id)

        job.refresh_from_db()
        assert job.agent_version_id == before["agent_version_id"]
        after = HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=job.agent_version_id
        ).values()[0]
        assert after == before


class TestUnpinnedRuns:
    def _environment(self, client, workspace):
        job = _job(_post(client, workspace, _body(values=VALUES)))
        _provision(job)
        return _complete(job)

    def test_a_run_with_versioning_off_reads_the_environments_version(
        self, client, user, workspace, gates, settings
    ):
        environment = self._environment(client, workspace)
        settings.HARNESS_VERSIONING_ENABLED = False

        run = _run(environment)

        assert run.agent_version_id is None
        assert PlatformSecretResolver().resolve(run) == VALUES
        assert PlatformSecretResolver().resolve(environment) == VALUES

    def test_a_rerun_with_versioning_off_takes_its_new_value(
        self, client, user, workspace, gates, settings
    ):
        environment = self._environment(client, workspace)
        settings.HARNESS_VERSIONING_ENABLED = False
        settings.HARNESS_PUBLIC_BASE_URL = "https://harness.example.test"

        with patch("simulate.temporal.client.start_hosted_harness_gateway_workflow"):
            result = HostedHarnessProvider().rerun_saved(
                str(environment.id),
                organization=user.organization,
                workspace=workspace,
                environment_values={"DB_URL": "val-db-new"},
            )

        child = HostedHarnessJob.no_workspace_objects.get(id=result["job"]["job_id"])
        assert child.payload["agent"]["secret_refs"]["DB_URL"]["key"].startswith(
            "harness-db_url-"
        )
        assert PlatformSecretResolver().resolve(child) == {
            **VALUES,
            "DB_URL": "val-db-new",
        }

    def test_a_shared_agents_run_records_its_own_environments_version(
        self, client, user, workspace, gates, settings
    ):
        from simulate.models import CallExecution, TestExecution

        first = _job(_post(client, workspace, _vapi()))
        second = _job(_post(client, workspace, _vapi(key="val-vapi-2")))
        definition = first.agent_version.agent_definition
        assert second.agent_version.agent_definition_id == definition.id
        assert definition.latest_version.id == second.agent_version_id
        _provision(first, modality="voice")
        environment = _complete(first)
        settings.HARNESS_VERSIONING_ENABLED = False

        run = _run(environment)

        execution = TestExecution.no_workspace_objects.get(
            execution_metadata__harness_job_id=str(run.id)
        )
        assert execution.agent_version_id == first.agent_version_id
        calls = CallExecution.no_workspace_objects.filter(test_execution=execution)
        assert calls.exists()
        assert set(calls.values_list("agent_version_id", flat=True)) == {
            first.agent_version_id
        }

    def test_a_shared_agents_build_records_its_own_environments_version(
        self, client, user, workspace, gates
    ):
        from simulate.models import CallExecution, HostedHarnessAttempt

        first = _job(_post(client, workspace, _vapi()))
        second = _job(_post(client, workspace, _vapi(key="val-vapi-2")))
        definition = first.agent_version.agent_definition
        assert definition.latest_version.id == second.agent_version_id
        _provision(first, modality="voice")
        attempt = HostedHarnessAttempt.no_workspace_objects.filter(job=first).latest(
            "created_at"
        )

        begin_scenarios(
            attempt,
            {
                "run_test_id": str(first.run_test_id),
                "scenario_keys": [_key(name) for name in NAMES[:2]],
            },
        )

        first.refresh_from_db()
        assert first.test_execution.agent_version_id == first.agent_version_id
        calls = CallExecution.no_workspace_objects.filter(
            test_execution=first.test_execution
        )
        assert calls.exists()
        assert set(calls.values_list("agent_version_id", flat=True)) == {
            first.agent_version_id
        }

    def test_a_run_without_versions_reads_the_environments_version(
        self, client, user, workspace, gates
    ):
        environment = self._environment(client, workspace)
        assert environment.environment_version_id is None

        run = _run(environment)

        assert run.agent_version_id is None
        assert PlatformSecretResolver().resolve(run) == VALUES

    def test_a_name_the_moved_default_lacks_is_unavailable(
        self, client, user, workspace, gates
    ):
        environment = self._environment(client, workspace)
        run = _run(environment)
        smaller = create_agent_version(
            environment.agent_version.agent_definition,
            parent=None,
            env=environment,
            secret_values={"DB_URL": "val-db-1"},
            trigger="credentials",
            activate=False,
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=smaller
        )
        run.payload["agent"]["secret_refs"] = {
            "VAPI_API_KEY": agent_version_secret_ref("VAPI_API_KEY")
        }

        with pytest.raises(HostedHarnessError) as refused:
            PlatformSecretResolver().resolve(run)

        assert refused.value.code == "credential_unavailable"
        assert "VAPI_API_KEY" in refused.value.message
        assert "val-" not in refused.value.message

    def test_a_legacy_run_reads_its_stored_secrets_as_before(
        self, user, workspace, gates
    ):
        from simulate.tests.test_harness_agent_creation import _stored_secret

        ref = _stored_secret(user.organization, "harness-db-legacy", "val-db-1")
        payload = _v1_payload(scenario_count=2)
        payload["agent"]["secret_refs"] = {"DB_URL": ref}
        environment, _ = create_hosted_job(
            user.organization, payload, idempotency_key="legacy", workspace=workspace
        )
        _provision(environment)
        environment = _author(_complete(environment))
        run = _run(environment)

        assert environment_credential_values(run) is None
        assert PlatformSecretResolver().resolve(run) == {"DB_URL": "val-db-1"}
        assert run.payload["source"]["commit_sha"] == BUILT


class TestLineage:
    def test_a_shared_agent_lists_only_the_environments_own_versions(
        self, client, user, workspace, gates
    ):
        first = _job(_post(client, workspace, _vapi()))
        second = _job(_post(client, workspace, _vapi()))
        assert first.agent_version_id == second.agent_version_id
        theirs = create_agent_version(
            first.agent_version.agent_definition,
            parent=first.agent_version,
            env=first,
            secret_values={"VAPI_API_KEY": "val-vapi-2"},
            trigger="credentials",
            activate=False,
        )
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version=theirs
        ).update(preflight_status="passed")

        def listed(job):
            response = client.get(
                f"/simulate/api/harness-environments/{job.id}/versions/",
                HTTP_X_WORKSPACE_ID=str(workspace.id),
            )
            assert response.status_code == 200, response.content
            return {item["id"] for item in response.json()["agent"]}

        assert str(theirs.id) in listed(first)
        assert listed(second) == {str(second.agent_version_id)}
        assert agent_version_pickable(first, theirs.id)
        assert not agent_version_pickable(second, theirs.id)

    def test_a_creation_onto_anothers_version_takes_none_of_its_ancestors(
        self, client, user, workspace, gates
    ):
        first = _job(_post(client, workspace, _vapi()))
        ancestor = first.agent_version
        rotated = create_agent_version(
            ancestor.agent_definition,
            parent=ancestor,
            env=first,
            secret_values={"VAPI_API_KEY": "val-vapi-2"},
            trigger="credentials",
            activate=False,
        )
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version=rotated
        ).update(preflight_status="passed")

        second = _job(_post(client, workspace, _vapi(key="val-vapi-2")))

        assert second.agent_version_id == rotated.id
        assert _side(rotated.id).parent_id == _side(ancestor.id).id
        assert str(ancestor.id) not in service.lineage_version_ids(
            second, ancestor.agent_definition_id
        )
        assert not agent_version_pickable(second, ancestor.id)
        response = client.get(
            f"/simulate/api/harness-environments/{second.id}/versions/",
            HTTP_X_WORKSPACE_ID=str(workspace.id),
        )
        assert response.status_code == 200, response.content
        assert str(ancestor.id) not in {item["id"] for item in response.json()["agent"]}


BUILT = "a" * 40


def _author(job, commit_sha=BUILT):
    """The build's authoring, up to the sandbox: it resolves the branch to a commit."""
    from types import SimpleNamespace

    from simulate.services.hosted_harness_gateway import HostedHarnessGateway

    gateway = object.__new__(HostedHarnessGateway)

    def stop(*_args, **_kwargs):
        raise RuntimeError("stop at the sandbox")

    gateway.client = SimpleNamespace(create=stop)
    module = "simulate.services.hosted_harness_gateway"
    with (
        patch(f"{module}._mark_stage"),
        patch(f"{module}.HostedSourceAcquirer.acquire", return_value=(b"", commit_sha)),
        patch(f"{module}._provider_import_authoring_material", side_effect=stop),
    ):
        with pytest.raises(RuntimeError, match="stop at the sandbox"):
            gateway.author(HostedHarnessJob.no_workspace_objects.get(id=job.id))
    job.refresh_from_db()
    return job


class TestBuiltCommit:
    def _built(self, client, workspace, storage):
        job = _job(_post(client, workspace, _body(values=VALUES)))
        child = create_agent_version(
            job.agent_version.agent_definition,
            parent=job.agent_version,
            env=job,
            secret_values={"DB_URL": "val-db-2"},
            trigger="credentials",
            activate=False,
        )
        _author(job)
        assert job.payload["source"]["commit_sha"] == BUILT
        _provision(job)
        _complete(job)
        record_authoring(job, _authoring(), origin="authoring")
        job.refresh_from_db()
        return job, child

    def test_the_first_versions_runs_fetch_the_commit_it_was_built_on(
        self, client, user, workspace, gates, storage
    ):
        environment, child = self._built(client, workspace, storage)

        run = _run(environment)

        assert run.agent_version_id == environment.agent_version_id
        assert run.payload["source"]["commit_sha"] == BUILT
        assert _side(child.id).source["commit_sha"] == BUILT

    @pytest.mark.parametrize("made", ["before", "after"])
    def test_a_credential_child_runs_the_built_commit(
        self, client, user, workspace, gates, storage, made
    ):
        environment, child = self._built(client, workspace, storage)
        if made == "after":
            child = create_agent_version(
                environment.agent_version.agent_definition,
                parent=environment.agent_version,
                env=environment,
                secret_values={"DB_URL": "val-db-3"},
                trigger="credentials",
                activate=False,
            )
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version=child
        ).update(preflight_status="passed")
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=child
        )
        environment.refresh_from_db()

        run = _run(environment)

        assert run.agent_version_id == child.id
        assert run.payload["source"]["commit_sha"] == BUILT

    def test_the_built_commit_is_part_of_the_first_versions_identity(
        self, client, user, workspace, gates, storage
    ):
        environment, _ = self._built(client, workspace, storage)
        a1 = environment.agent_version

        again = create_agent_version(
            a1.agent_definition,
            parent=a1,
            env=environment,
            source={
                "kind": "github",
                "repository": "acme/agent",
                "ref": "main",
                "commit_sha": BUILT,
                "visibility": "public",
            },
            trigger="credentials",
            activate=False,
        )

        assert again.id == a1.id

    def test_a_later_authoring_leaves_the_stamped_commit(
        self, client, user, workspace, gates, storage
    ):
        environment, _ = self._built(client, workspace, storage)
        before = _side(environment.agent_version_id)

        _author(environment, commit_sha="b" * 40)

        after = _side(environment.agent_version_id)
        assert (after.source, after.hash) == (before.source, before.hash)

    @pytest.mark.parametrize("first_builds", [True, False])
    def test_a_shared_version_keeps_the_branch_for_each_environment(
        self, client, user, workspace, gates, storage, caplog, first_builds
    ):
        body = _body(
            "vapi",
            mode="connect_only",
            config={"assistant_id": "asst-1"},
            values={"VAPI_API_KEY": "val-vapi-1"},
        )
        first = _job(_post(client, workspace, body))
        second = _job(_post(client, workspace, body))
        assert first.agent_version_id == second.agent_version_id
        builds = [(first, BUILT), (second, "b" * 40)]
        for environment, commit_sha in builds if first_builds else builds[::-1]:
            with caplog.at_level("WARNING", logger=service.logger.name):
                _author(environment, commit_sha=commit_sha)
            assert (
                f"harness_agent_commit_not_stamped job={environment.id}"
                f" version={environment.agent_version_id} reason=shared"
            ) in caplog.text
            _provision(environment)
            _complete(environment)
            record_authoring(environment, _authoring(), origin="authoring")
            environment.refresh_from_db()

        assert "commit_sha" not in _side(first.agent_version_id).source
        for environment, key in ((first, "r1"), (second, "r2")):
            run = _run(environment, key=key)
            assert run.agent_version_id == first.agent_version_id
            assert "commit_sha" not in run.payload["source"]

    def test_with_versioning_off_nothing_is_stamped(
        self, client, user, workspace, gates, settings, django_assert_num_queries
    ):
        environment = _job(_post(client, workspace, _body(values=VALUES)))
        before = _side(environment.agent_version_id)
        settings.HARNESS_VERSIONING_ENABLED = False

        with django_assert_num_queries(0):
            service.stamp_built_commit(environment, BUILT)
        _author(environment)

        after = _side(environment.agent_version_id)
        assert (after.source, after.hash) == (before.source, before.hash)
