"""A release composition from its sealed releases: how it is made, edited, run and reported, and
how the adaptation that makes one is settled."""

import gzip
import io
import json
import tarfile
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest
from django.core.cache.backends.locmem import LocMemCache

from simulate.models import (
    AgentDefinition,
    AgentVersion,
    HarnessEnvironmentRelease,
    HarnessSuiteRelease,
    HostedHarnessAttempt,
    HostedHarnessJob,
    HostedHarnessScenario,
)
from simulate.models import (
    TestExecution as SimulationExecution,
)
from simulate.services import harness_fit, hosted_harness_gateway
from simulate.services import harness_releases as releases
from simulate.services.harness_capacity import SandboxCapacity
from simulate.services.harness_composition_edits import _applied
from simulate.services.harness_scenarios import index_scenarios
from simulate.services.hosted_harness import (
    HostedHarnessError,
    create_hosted_job,
    create_selected_harness_run,
    register_attempt,
    request_cancellation,
)
from simulate.services.hosted_harness_gateway import (
    HostedHarnessGateway,
    _extract_adaptation,
)
from simulate.services.hosted_sandbox import SandboxNotFoundError
from simulate.tests.test_hosted_harness_conversation import _payload
from simulate.tests.test_hosted_harness_gateway import _Daytona

JOBS = "/simulate/api/harness-jobs/"
ENVIRONMENTS = "/simulate/api/harness-environments/"
CONFIG = {
    "name": "Desk",
    "instructions": "Help customers.",
    "model": "vertex_ai/gemini-3.7-flash",
}


def _sha(letter):
    return "sha256:" + letter * 64


def _targz(files):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return gzip.compress(raw.getvalue(), mtime=0)


@pytest.fixture
def archives(monkeypatch):
    """The object store of release archives."""
    stored = {}
    monkeypatch.setattr(releases, "_download", stored.__getitem__)
    monkeypatch.setattr(releases, "_upload", stored.__setitem__)
    return stored


def _environment(organization, name="retail", letter="e", **fields):
    return HarnessEnvironmentRelease.no_workspace_objects.create(
        organization=organization,
        digest=_sha(letter),
        name=name,
        object_key=f"releases/{letter}",
        manifest={},
        tools=[{"name": "get_order", "description": "", "parameters": {}}],
        policy={},
        **fields,
    )


def _personas(keys):
    return [
        {
            "scenario_key": key,
            "scenario_name": f"Use case of {key}",
            "name": "Gareth",
            "situation": f"Wants {key}",
            "outcome": "Done",
        }
        for key in keys
    ]


def _entries(keys):
    return [{"key": key, "path": f"scenarios/{key}", "origin": "base"} for key in keys]


def _suite(archives, environment, keys, letter="a", **fields):
    archives[f"releases/suite-{letter}"] = _targz(
        {
            f"scenarios/{key}/scenario.json": json.dumps(
                {"name": key, "tests": f"tests {key}"}
            ).encode()
            for key in keys
        }
    )
    return HarnessSuiteRelease.no_workspace_objects.create(
        organization=environment.organization,
        digest=_sha(letter),
        name="retail_base",
        environment=environment,
        object_key=f"releases/suite-{letter}",
        manifest={"scenarios": _entries(keys)},
        personas=_personas(keys),
        **fields,
    )


def _version(organization, workspace=None, **definition):
    definition = AgentDefinition.no_workspace_objects.create(
        agent_name="ShopMate",
        agent_type="text",
        provider="retell",
        assistant_id="agent_shopmate",
        inbound=True,
        organization=organization,
        workspace=workspace,
        description="",
        **definition,
    )
    return AgentVersion.no_workspace_objects.create(
        agent_definition=definition,
        version_number=1,
        version_name="v1",
        organization=organization,
        description="",
        commit_message="",
    )


def _hosted(organization, workspace, suite, key="compose"):
    return releases.create_hosted_agent_composition(
        organization,
        workspace,
        suite=suite,
        idempotency_key=key,
        **CONFIG,
    )


def _connection(target="agent_shopmate", **config):
    return {
        "connector": "retell_chat",
        "mode": "provider_import",
        "config": {"agent_id": target, **config},
        "secret_refs": {
            "RETELL_API_KEY": {
                "manager": "platform-vault",
                "key": str(uuid.uuid4()),
                "purpose": "target_provider",
            }
        },
    }


def _canonical(version, connection, environment, **changed):
    return releases._with_digest(
        {
            "schema_version": releases.AGENT_BINDING_SCHEMA,
            "agent": {
                **releases.agent_reference(
                    version, connection["connector"], connection["config"]
                ),
                "provider_target_id": connection["config"]["agent_id"],
                "provider_digest": _sha("9"),
            },
            "environment": {"digest": environment.digest},
            "mode": "canonical",
            "agent_tools": [],
            "tools": [],
            "fixtures": {},
            "aliases": {},
            "rendering": {},
            "prompt_changes": [],
            **changed,
        }
    )


def _compose(organization, workspace, suite, version, connection, binding):
    return releases.create_composition(
        organization,
        workspace,
        suite=suite,
        agent=connection,
        agent_version=version,
        name="Retail",
        idempotency_key=f"compose-{uuid.uuid4()}",
        binding=binding,
    )


# --- compositions -----------------------------------------------------------------------------


@pytest.mark.django_db
class TestCompose:
    def test_an_agent_that_names_no_workspace_is_composed_where_it_may_be_fitted(
        self, organization, workspace, archives
    ):
        version = _version(organization)
        suite = _suite(archives, _environment(organization), ["a"])
        connection = _connection()

        assert (
            releases.organization_agent_version(organization, workspace, version.id)
            == version
        )
        composition = _compose(
            organization,
            workspace,
            suite,
            version,
            connection,
            _canonical(version, connection, suite.environment),
        )

        assert composition.state == HostedHarnessJob.State.COMPLETED
        assert composition.run_test.agent_definition_id == version.agent_definition_id

    def test_a_deleted_agent_is_not_composed(self, organization, workspace, archives):
        version = _version(organization, workspace)
        suite = _suite(archives, _environment(organization), ["a"])
        connection = _connection()
        binding = _canonical(version, connection, suite.environment)
        version.agent_definition.deleted = True
        version.agent_definition.save(update_fields=["deleted"])

        with pytest.raises(HostedHarnessError) as refused:
            _compose(organization, workspace, suite, version, connection, binding)

        assert refused.value.code == "agent_version_not_found"

    @pytest.mark.parametrize(
        "connection",
        [_connection("agent_other"), _connection(dynamic_variables={"a": "1"})],
        ids=["another-target", "another-config"],
    )
    def test_a_binding_of_another_connection_is_refused(
        self, organization, workspace, archives, connection
    ):
        version = _version(organization, workspace)
        suite = _suite(archives, _environment(organization), ["a"])
        binding = _canonical(version, _connection(), suite.environment)

        with pytest.raises(HostedHarnessError) as refused:
            _compose(organization, workspace, suite, version, connection, binding)

        assert refused.value.code == "binding_invalid"

    def test_a_binding_of_another_provider_target_is_refused(
        self, organization, workspace, archives
    ):
        version = _version(organization, workspace)
        suite = _suite(archives, _environment(organization), ["a"])
        connection = _connection()
        sealed = _canonical(version, connection, suite.environment)
        agent = {**sealed["agent"], "provider_target_id": "agent_other"}
        binding = _canonical(version, connection, suite.environment, agent=agent)

        with pytest.raises(HostedHarnessError) as refused:
            _compose(organization, workspace, suite, version, connection, binding)

        assert refused.value.code == "binding_invalid"
        assert "another provider agent" in str(refused.value)

    def test_keys_that_differ_only_in_spelling_are_two_scenarios(
        self, organization, workspace, archives
    ):
        keys = ["update-insurance", "update_insurance", "Update-Insurance"]
        suite = _suite(archives, _environment(organization), keys)

        composition = _hosted(organization, workspace, suite)

        rows = HostedHarnessScenario.no_workspace_objects.filter(job=composition)
        assert {row.scenario_key: row.name for row in rows} == {k: k for k in keys}


@pytest.mark.django_db
class TestStrictKeys:
    def _index(self, job, *docs):
        index_scenarios(
            job,
            [{"scenario_key": key, "name": name} for key, name in docs],
            prune=True,
            strict_keys=True,
        )
        return {
            row.scenario_key: (row.id, row.name)
            for row in HostedHarnessScenario.no_workspace_objects.filter(
                job=job, deleted=False
            )
        }

    @pytest.fixture
    def job(self, organization):
        return create_hosted_job(organization, _payload(), idempotency_key="strict")[0]

    def test_the_same_name_under_a_new_key_is_a_new_scenario(self, job):
        before = self._index(job, ("old-key", "Cancel"))
        after = self._index(job, ("new-key", "Cancel"))

        assert set(after) == {"new-key"}
        assert after["new-key"][0] != before["old-key"][0]

    def test_a_dash_and_an_underscore_are_two_scenarios(self, job):
        before = self._index(job, ("update-insurance", "A"))
        after = self._index(job, ("update-insurance", "A"), ("update_insurance", "B"))

        assert after["update-insurance"] == before["update-insurance"]
        assert after["update_insurance"][1] == "B"

    def test_a_dropped_scenario_comes_back_only_under_its_exact_key(self, job):
        dropped = self._index(job, ("Update-Insurance", "A"))["Update-Insurance"][0]
        other = self._index(job, ("update-insurance", "B"))
        back = self._index(job, ("update-insurance", "B"), ("Update-Insurance", "A"))

        assert other["update-insurance"][0] != dropped
        assert back["Update-Insurance"][0] == dropped


# --- what a job request may carry ---------------------------------------------------------------


@pytest.mark.django_db
class TestJobRequests:
    @pytest.fixture(params=["", "preflight/"])
    def send(self, request, auth_client, organization, monkeypatch):
        from simulate.services import harness_provider
        from simulate.temporal import client

        monkeypatch.setattr(
            client, "start_hosted_harness_gateway_workflow", lambda *args: None
        )
        monkeypatch.setattr(
            harness_provider, "_validate_known_hosted_egress", lambda *args: None
        )

        def post(body):
            response = auth_client.post(
                f"{JOBS}{request.param}",
                {"artifacts": {"level": "full"}, **body},
                format="json",
                HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
            )
            assert not HostedHarnessJob.no_workspace_objects.filter(
                organization=organization
            ).exists()
            return response

        return post

    @pytest.mark.parametrize("versioning", [False, True])
    def test_our_hosted_agent_is_never_a_job_of_its_own(
        self, send, settings, versioning
    ):
        settings.HARNESS_VERSIONING_ENABLED = versioning

        response = send({"agent": {"connector": "hosted_agent", "config": CONFIG}})

        assert response.status_code == 400, response.content
        assert b"only on an environment it is composed with" in response.content

    @pytest.mark.parametrize(
        "key",
        ["adaptation", "adaptation_result", "release_composition", "run_manifest"],
    )
    def test_the_release_services_own_metadata_is_refused(self, send, key):
        response = send({"agent": _connection(), "metadata": {key: {"suite": {}}}})

        assert response.status_code == 400, response.content
        assert b"metadata contains reserved" in response.content


# --- runs of a composition ----------------------------------------------------------------------


@pytest.fixture
def composition(organization, workspace, archives):
    suite = _suite(archives, _environment(organization), ["gone", "kept"])
    return _hosted(organization, workspace, suite)


def _run(composition, *keys):
    run, _created = create_selected_harness_run(
        composition,
        scenario_keys=list(keys),
        trials=1,
        idempotency_key=f"run-{uuid.uuid4()}",
    )
    return run


@pytest.mark.django_db
class TestRuns:
    def test_the_manifest_freezes_the_simulator_and_its_fallbacks(
        self, composition, monkeypatch
    ):
        monkeypatch.setenv("SIMULATOR_LLM_FALLBACK_MODELS", "m1, m2")
        monkeypatch.delenv("SIMULATOR_LLM_THINKING", raising=False)
        run = _run(composition, "kept")
        frozen = run.payload["metadata"]["run_manifest"]["simulator"]
        assert frozen["llm"]["fallback_models"] == ["m1", "m2"]

        monkeypatch.setenv("SIMULATOR_LLM_FALLBACK_MODELS", "later")
        monkeypatch.setenv("SIMULATOR_LLM_THINKING", "high")
        monkeypatch.setenv("SIMULATOR_LLM_MODEL", "another-model")
        values = hosted_harness_gateway.simulator_settings()
        releases.apply_frozen_simulator(values, frozen)

        assert values["SIMULATOR_LLM_FALLBACK_MODELS"] == "m1,m2"
        assert values["SIMULATOR_LLM_MODEL"] == frozen["llm"]["model"]
        # Frozen as unset: a later setting does not leak into the run.
        assert "SIMULATOR_LLM_THINKING" not in values

    def test_a_run_made_before_a_suite_edit_reports_what_it_ran(
        self, auth_client, organization, archives, composition
    ):
        run = _run(composition, "gone", "kept")
        parent = HarnessSuiteRelease.no_workspace_objects.get(digest=_sha("a"))
        edited = _suite(
            archives, parent.environment, ["kept", "new"], letter="c", parent=parent
        )
        held = composition.payload["metadata"]["release_composition"]
        held["suite"] = {
            "digest": edited.digest,
            "name": edited.name,
            "object_key": edited.object_key,
        }
        held["alignment"] = {"unscorable": [{"scenario_key": "kept", "reason": "x"}]}
        composition.save(update_fields=["payload"])

        response = auth_client.get(
            f"{ENVIRONMENTS}{composition.id}/runs/{run.test_execution_id}/report/"
        )

        assert response.status_code == 200, response.content
        report = response.json()
        assert [one["scenario_key"] for one in report["scenarios"]] == ["gone", "kept"]
        assert report["manifest"]["suite"]["digest"] == parent.digest
        assert "binding_proof" not in report["manifest"]
        assert report["coverage"]["unscorable"] == []
        assert report["coverage"]["suite_scenarios"] == 2

    def test_its_scenarios_are_edited_in_its_chat(self, auth_client, composition):
        response = auth_client.post(
            f"{JOBS}{composition.id}/scenarios/amend/",
            {"changes": [{"op": "drop", "scenario": "gone"}]},
            format="json",
        )

        assert response.status_code == 409, response.content
        assert response.json()["code"] == "composition_edit_in_chat"

    def test_a_rerun_of_part_of_it_keeps_the_runtime_the_whole_run_froze(
        self, organization, monkeypatch, settings
    ):
        settings.ALK_HOSTED_EGRESS_UNRESTRICTED = True
        payload = _payload()
        payload["scenario_count"] = 2
        payload["metadata"] = {
            "execution_manifest": [{"execution_key": "one"}, {"execution_key": "two"}],
            "rerun_execution_keys": ["two"],
        }
        job, _ = create_hosted_job(
            organization, payload, idempotency_key="release-rerun"
        )

        def capacity(payload):
            width = payload["scenario_count"]
            return SandboxCapacity(
                f"wide-{width}",
                6,
                12_288,
                20,
                width,
                f"image-{width}",
                _sha(str(width)),
            )

        monkeypatch.setattr(
            "simulate.services.harness_capacity.configured_capacity", capacity
        )
        monkeypatch.setattr(
            releases,
            "release_launch_for",
            lambda job: releases.ReleaseLaunch(
                uploads=(),
                prepare_command="true",
                bundle_command="true",
                simulator={"llm": {"provider": "vertex", "model": "m"}},
                runtime={"image_name": "image-2", "image_digest": _sha("2")},
            ),
        )
        monkeypatch.setattr(
            hosted_harness_gateway, "_platform_simulator_material", dict
        )
        gateway = object.__new__(HostedHarnessGateway)
        gateway.client = _Daytona()
        gateway.snapshot = "alk-hosted-v1"
        gateway.snapshot_digest = _sha("b")

        attempt = gateway.launch(job, endpoint_base_url="https://platform.example.com")

        assert attempt.state == HostedHarnessAttempt.State.RUNNING
        dispatched = json.loads(gateway.client.sandbox.fs.uploads["/work/job.json"])
        assert dispatched["scenario_count"] == 1


# --- the catalogue ------------------------------------------------------------------------------


@pytest.mark.django_db
class TestReleases:
    def test_a_release_sealed_again_replaces_the_earlier_one_in_the_list(
        self, organization, workspace, archives
    ):
        earlier = _environment(organization, letter="1")
        suite = _suite(archives, earlier, ["a"])
        composition = _hosted(organization, workspace, suite)
        later = _environment(organization, letter="2")
        with releases._owned_by(None):
            shared = _environment(None, letter="3")

        listed = {one["digest"] for one in releases.list_releases(organization)}

        # A system release of the same name is another release.
        assert listed == {later.digest, shared.digest}
        # What was made from the earlier one still finds it.
        assert (
            releases._visible(HarnessEnvironmentRelease, organization, earlier.digest)
            == earlier
        )
        assert releases.visible_suite(organization, suite.id) == suite
        assert _run(composition, "a").payload["metadata"]["run_manifest"]


@pytest.mark.django_db
class TestEnvironmentList:
    def _ids(self, auth_client):
        response = auth_client.get(ENVIRONMENTS)
        assert response.status_code == 200, response.content
        return {row["id"] for row in response.json()["results"]}

    def _job(self, organization, workspace, state="queued", **metadata):
        payload = _payload()
        payload["metadata"] = metadata
        job, _ = create_hosted_job(
            organization,
            payload,
            idempotency_key=str(uuid.uuid4()),
            workspace=workspace,
        )
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(state=state)
        return str(job.id)

    def test_a_job_without_metadata_is_listed(
        self, auth_client, organization, workspace
    ):
        identifier = self._job(organization, workspace)
        payload = _payload()
        del payload["metadata"]
        HostedHarnessJob.no_workspace_objects.filter(id=identifier).update(
            payload=payload
        )

        assert identifier in self._ids(auth_client)

    def test_only_an_adaptation_in_review_is_listed(
        self, auth_client, organization, workspace
    ):
        review = self._job(organization, workspace, adaptation={"mode": "review"})
        for hidden in ({"mode": "run"}, {}):
            self._job(organization, workspace, adaptation=hidden)
        self._job(organization, workspace, "completed", adaptation={"mode": "review"})

        assert self._ids(auth_client) == {review}


# --- settling an adaptation ---------------------------------------------------------------------


def _adaptation(organization, **adaptation):
    payload = _payload()
    payload["metadata"] = {"adaptation": adaptation}
    job, _ = create_hosted_job(
        organization, payload, idempotency_key=f"adapt-{uuid.uuid4()}"
    )
    return job


def _attempted(job):
    register_attempt(
        job.id, endpoint_base_url="https://x", provider_ref=f"sandbox-{job.id}"
    )
    job.refresh_from_db()
    return HostedHarnessAttempt.no_workspace_objects.get(
        job=job, attempt_number=job.current_attempt_number
    )


class _Gone:
    """A provider whose sandbox was already deleted."""

    name = "daytona"
    runtime_name = runtime_digest = None

    def get(self, *args, **kwargs):
        raise SandboxNotFoundError("gone")


def _gateway(client=None):
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = client or _Gone()
    gateway._provider_error = None
    return gateway


@pytest.fixture
def composed(organization, workspace, archives, composition, monkeypatch):
    """An output whose adaptation completes with `composition`; what each call was given."""
    calls = []

    def adapt(job, output, verified=None):
        calls.append(job.id)
        return {"composition_id": str(composition.id)}, None

    monkeypatch.setattr(releases, "_adapt", adapt)
    return calls


@pytest.fixture
def output(tmp_path):
    (tmp_path / "result.json").write_text("{}")
    return tmp_path


@pytest.fixture
def dispatched(monkeypatch):
    from simulate.services import harness_usage
    from simulate.temporal import client

    started = []
    monkeypatch.setattr(
        client,
        "start_hosted_harness_gateway_workflow",
        lambda job_id, *args: started.append(job_id),
    )
    monkeypatch.setattr(harness_usage, "require_harness_call_usage", lambda job: None)
    return started


@pytest.fixture
def runs(composition):
    return HostedHarnessJob.no_workspace_objects.filter(environment=composition)


@pytest.mark.django_db
class TestSettled:
    @pytest.mark.parametrize("mode", ["review", "run"])
    def test_a_worker_lost_after_the_sandbox_is_deleted_changes_nothing(
        self, organization, composed, output, dispatched, runs, mode
    ):
        job = _adaptation(organization, mode=mode)
        attempt = _attempted(job)
        releases.complete_adaptation(job, output)

        settled = _gateway().reconcile_completed(attempt)

        assert settled.state == HostedHarnessJob.State.COMPLETED
        assert settled.failure is None
        attempt.refresh_from_db()
        assert attempt.terminal_stage == "completed"
        assert attempt.cleanup_verified_at is not None
        assert len(composed) == 1
        assert runs.count() == len(dispatched) == (1 if mode == "run" else 0)

    def test_a_failed_adaptation_is_not_retried_either(
        self, organization, composed, dispatched
    ):
        job = _adaptation(organization, mode="review")
        attempt = _attempted(job)
        failed = releases.complete_adaptation(job, None)

        settled = _gateway().reconcile_completed(attempt)

        assert settled.state == HostedHarnessJob.State.FAILED
        assert settled.failure == failed.failure
        assert settled.failure["code"] == "adaptation_result_missing"

    def test_a_cancel_after_it_completed_keeps_its_outcome(
        self, organization, composed, output, dispatched, runs
    ):
        job = _adaptation(organization, mode="review")
        _attempted(job)
        releases.complete_adaptation(job, output)
        job.refresh_from_db()

        ended = _gateway().cancel(job, reason="user_canceled")

        assert ended.state == HostedHarnessJob.State.COMPLETED

    @pytest.mark.parametrize("ended", ["canceled", "deleted"])
    def test_a_canceled_or_deleted_one_composes_and_runs_nothing(
        self, organization, composed, output, dispatched, runs, ended
    ):
        job = _adaptation(organization, mode="run")
        attempt = _attempted(job)
        if ended == "canceled":
            request_cancellation(job, "user_canceled")
        else:
            HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(deleted=True)

        settled = releases.complete_adaptation(job, output)

        assert settled.state == HostedHarnessJob.State.CANCELED
        assert composed == [] and dispatched == []
        assert not runs.exists()
        if ended == "canceled":
            settled = _gateway().reconcile_completed(attempt)
            assert settled.state == HostedHarnessJob.State.CANCELED

    def test_one_already_settled_is_not_adapted_again(
        self, organization, composed, output, dispatched, runs
    ):
        job = _adaptation(organization, mode="run")
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
            state=HostedHarnessJob.State.FAILED, failure={"code": "sandbox_disappeared"}
        )

        settled = releases.complete_adaptation(job, output)

        assert settled.failure == {"code": "sandbox_disappeared"}
        assert composed == [] and dispatched == []


@pytest.mark.django_db
class TestRunMode:
    def test_a_run_created_but_not_started_is_started(
        self, organization, composed, output, dispatched, runs, monkeypatch
    ):
        from simulate.temporal import client

        job = _adaptation(organization, mode="run")

        def died(*args):
            raise SystemExit

        with monkeypatch.context() as worker:
            worker.setattr(client, "start_hosted_harness_gateway_workflow", died)
            with pytest.raises(SystemExit):
                releases.complete_adaptation(job, output)
        assert runs.get().state == HostedHarnessJob.State.QUEUED

        settled = releases.complete_adaptation(job, output)

        assert dispatched == [str(runs.get().id)]
        assert (
            settled.payload["metadata"]["adaptation_result"]["run"]["job_id"]
            == dispatched[0]
        )
        # Recorded: asked again, nothing starts twice.
        releases.complete_adaptation(job, output)
        assert len(dispatched) == 1

    def test_a_run_that_cannot_be_scheduled_is_failed(
        self, organization, composed, output, dispatched, runs, monkeypatch
    ):
        from simulate.temporal import client

        def down(*args):
            raise RuntimeError("temporal is down")

        monkeypatch.setattr(client, "start_hosted_harness_gateway_workflow", down)
        job = _adaptation(organization, mode="run")

        settled = releases.complete_adaptation(job, output)

        assert (
            settled.payload["metadata"]["adaptation_result"]["run_failure"]["code"]
            == "run_not_started"
        )
        run = runs.get()
        assert run.state == HostedHarnessJob.State.FAILED
        assert run.failure["code"] == "scheduler_unavailable"
        assert run.test_execution.status == SimulationExecution.ExecutionStatus.FAILED


# --- what the sandbox left ----------------------------------------------------------------------

RESULT = {
    "schema_version": releases.ADAPTATION_RESULT_SCHEMA,
    "status": "completed",
    "suite": {"digest": _sha("a"), "derived": False},
}


@pytest.mark.django_db
class TestMalformedOutput:
    @pytest.mark.parametrize(
        "result, binding",
        [
            ("[]", "{}"),
            ('"done"', "{}"),
            ("not json", "{}"),
            ("[" * 100_000, "{}"),
            ('{"a": "\\u0000"}', "{}"),
            ({**RESULT, "status": "failed", "failure": "x"}, "{}"),
            ({**RESULT, "suite": []}, "{}"),
            ({**RESULT, "suite": "x"}, "{}"),
            (RESULT, "[]"),
            ({**RESULT, "alignment": ["x"]}, {"mode": "canonical"}),
            ({**RESULT, "alignment": {"unscorable": ["x"]}}, {"mode": "canonical"}),
        ],
        ids=[
            "a-list",
            "a-string",
            "not-json",
            "too-deep",
            "nul",
            "failure-text",
            "suite-list",
            "suite-text",
            "binding-list",
            "alignment-list",
            "unscorable-text",
        ],
    )
    def test_it_fails_the_adaptation(
        self, organization, workspace, archives, tmp_path, result, binding
    ):
        suite = _suite(archives, _environment(organization), ["a"])
        job = _adaptation(
            organization,
            mode="run",
            suite={"digest": suite.digest},
            agent_version_id=str(_version(organization).id),
        )
        for name, body in (("result.json", result), ("binding.json", binding)):
            (tmp_path / name).write_text(
                body if isinstance(body, str) else json.dumps(body)
            )
        verified = {
            "schema_version": releases.ADAPTATION_VERIFIED_SCHEMA,
            "binding": {"digest": None, "mode": "canonical"},
        }
        if isinstance(binding, dict):
            (tmp_path / "binding.json").write_text(
                json.dumps({**binding, "digest": "d"})
            )
            verified["binding"]["digest"] = "d"

        settled = releases.complete_adaptation(job, tmp_path, verified)

        assert settled.state == HostedHarnessJob.State.FAILED
        assert settled.failure["code"] == "adaptation_result_invalid"

    @pytest.mark.parametrize(
        "proof",
        [
            {"binding": [], "suite": "x", "environment": None},
            {"scenarios": {"a": {"status": []}}},
            {"scenarios": ["a"]},
        ],
        ids=["references", "a-status", "scenarios"],
    )
    def test_a_malformed_proof_is_unproved(self, proof):
        from simulate.tests.test_harness_release_fit import (
            _accepted,
            _proof,
            _routed_binding,
        )

        binding = _routed_binding()

        with pytest.raises(HostedHarnessError) as refused:
            _accepted(binding, {**_proof(binding, a="proved"), **proof}, keys=["a"])

        assert refused.value.code == "binding_unproved"


class TestOutputArchive:
    def test_an_output_is_unpacked(self, tmp_path):
        assert _extract_adaptation(_targz({"result.json": b"{}"}), str(tmp_path))
        assert (tmp_path / "result.json").read_bytes() == b"{}"

    @pytest.mark.parametrize(
        "body",
        [
            b"",
            b"not a gzip",
            _targz({"result.json": b"x" * 4096})[:60],
            _targz({"result.json": b"{}"})[:12] + b"\xff" * 60,
        ],
        ids=["empty", "not-gzip", "truncated", "corrupt"],
    )
    def test_a_broken_one_is_not(self, tmp_path, body):
        assert not _extract_adaptation(body, str(tmp_path))

    def test_one_larger_than_the_limit_is_not(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hosted_harness_gateway, "_ADAPTATION_MAX_BYTES", 1024)

        assert not _extract_adaptation(
            _targz({"result.json": b"{}", "bomb": b"\0" * 4096}), str(tmp_path)
        )
        assert not list(tmp_path.iterdir())

    @pytest.mark.django_db
    def test_one_larger_than_the_limit_is_never_downloaded(self, organization):
        job = _adaptation(organization, mode="review")
        attempt = _attempted(job)
        too_large = hosted_harness_gateway._ADAPTATION_TOO_LARGE_EXIT

        class Files:
            def download_file(self, path, timeout):
                assert "adaptation.tar.gz" not in path
                raise SandboxNotFoundError(path)

        sandbox = SimpleNamespace(
            fs=Files(),
            process=SimpleNamespace(
                exec=lambda *args, **kwargs: SimpleNamespace(
                    exit_code=too_large, result=""
                )
            ),
        )
        found = iter([sandbox])
        client = _Gone()
        client.get = lambda *args, **kwargs: next(found, None) or _Gone().get()

        settled = _gateway(client)._finish_adaptation(attempt)

        assert settled.state == HostedHarnessJob.State.FAILED
        assert settled.failure["code"] == "adaptation_result_missing"


# --- a suite edited in the chat -----------------------------------------------------------------


def _seal(directory, name, document):
    """Write `document` as a sealed manifest of what `directory` holds."""
    import hashlib

    manifest = {
        **document,
        "files": [
            {
                "path": path.relative_to(directory).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
            for path in sorted(directory.rglob("*"))
            if path.is_file()
        ],
    }
    manifest["digest"] = releases._release_digest(manifest)
    (directory / name).write_text(json.dumps(manifest))
    return manifest


@pytest.mark.django_db
class TestSuiteEdits:
    @pytest.fixture
    def edit(self, organization, archives, composition, tmp_path):
        from simulate.tests.test_harness_release_fit import _proof, _routed_binding

        held = composition.payload["metadata"]["release_composition"]
        held["binding"] = _routed_binding()
        # The routed binding of the fit tests targets the same environment digest.
        assert held["binding"]["environment"]["digest"] == _sha("e")

        def apply(path="scenarios/kept", **summary):
            change = tmp_path / f"change-{uuid.uuid4()}"
            suite_dir = change / "suite"
            (suite_dir / "scenarios/kept").mkdir(parents=True)
            (suite_dir / "scenarios/kept/scenario.json").write_text('{"name": "kept"}')
            (suite_dir / "proofs.json").write_text(
                json.dumps(
                    {
                        "runtime": {
                            "engine": "postgres",
                            "scenarios": {"kept": {"holds": True}},
                        }
                    }
                )
            )
            (suite_dir / "provision.json").write_text(
                json.dumps({"personas": _personas(["kept"])})
            )
            suite = _seal(
                suite_dir,
                "suite.json",
                {
                    "schema_version": releases.SUITE_RELEASE_SCHEMA,
                    "name": "retail_base",
                    "environment": {"digest": _sha("e")},
                    "parent": {"digest": _sha("a")},
                    "scenarios": [{"key": "kept", "path": path, "origin": "base"}],
                },
            )
            proof = _proof(held["binding"], kept="proved")
            proof["suite"] = {"digest": suite["digest"]}
            (change / "binding-proof.json").write_text(json.dumps(proof))
            (change / "trajectories.json").write_text("{}")
            (change / "change.json").write_text(
                json.dumps(
                    {
                        "schema_version": "futureagi.composition-change.v1",
                        "parent": _sha("a"),
                        "suite": suite["digest"],
                        "binding_proof": {"digest": releases.proof_digest(proof)},
                        **summary,
                    }
                )
            )
            return _applied(composition, held, change), proof

        return apply

    def test_a_sealed_change_is_applied(self, edit):
        (updated, suite, personas), proof = edit()

        assert updated["suite"]["digest"] == suite["digest"]
        assert updated["binding_proof"] == proof
        assert [one["scenario_key"] for one in personas] == ["kept"]

    @pytest.mark.parametrize(
        "sealed",
        [None, {}, "x", {"digest": _sha("0")}],
        ids=["missing", "empty", "not-a-reference", "another-proof"],
    )
    def test_a_routed_change_names_the_proof_it_sealed(self, edit, sealed):
        with pytest.raises(HostedHarnessError) as refused:
            edit(binding_proof=sealed)

        assert refused.value.code == "composition_change_invalid"

    @pytest.mark.parametrize(
        "path",
        [
            "../../../etc",
            "/etc",
            "scenarios/../scenarios/kept",
            "./scenarios/kept",
            "scenarios//kept",
            "scenarios/kept/",
            "scenarios/unlisted",
            "",
            None,
            ["scenarios/kept"],
        ],
    )
    def test_a_scenario_is_read_only_from_the_sealed_suite(self, edit, path):
        with pytest.raises(HostedHarnessError) as refused:
            edit(path)

        assert refused.value.code == "release_invalid"


# --- the fit's model call -----------------------------------------------------------------------


class TestModelCall:
    @pytest.fixture(autouse=True)
    def cache(self, monkeypatch):
        cache = LocMemCache(str(uuid.uuid4()), {})
        monkeypatch.setattr(harness_fit, "cache", cache)

    def test_one_request_at_a_time_asks_the_model(self, monkeypatch):
        asked = []

        def ask(task, material):
            asked.append(task)
            with pytest.raises(harness_fit._Busy):
                harness_fit._one_at_a_time(task, task, material)
            return {"pairs": []}

        monkeypatch.setattr(harness_fit, "_ask_model", ask)

        assert harness_fit._one_at_a_time("fit", "fit", {}) == {"pairs": []}
        # Another question is not held up, and the next request may ask again.
        assert harness_fit._one_at_a_time("other", "other", {}) == {"pairs": []}
        assert harness_fit._one_at_a_time("fit", "fit", {}) == {"pairs": []}
        assert len(asked) == 3

    def test_a_request_that_fails_lets_the_next_one_ask(self, monkeypatch):
        replies = iter([httpx.ReadTimeout("slow"), None])

        def ask(task, material):
            if error := next(replies):
                raise error
            return {"pairs": []}

        monkeypatch.setattr(harness_fit, "_ask_model", ask)

        with pytest.raises(httpx.ReadTimeout):
            harness_fit._one_at_a_time("fit", "task", {})
        assert harness_fit._one_at_a_time("fit", "task", {}) == {"pairs": []}

    def test_the_budget_is_of_the_whole_call_not_of_each_phase(self, monkeypatch):
        def slow(url, **options):
            time.sleep(1.5)

        monkeypatch.setattr(harness_fit, "_MODEL_BUDGET_SECONDS", 1.1)
        monkeypatch.setattr(
            harness_fit, "platform_model_gateway", lambda: ("https://gw", "k")
        )
        monkeypatch.setattr(harness_fit.httpx, "post", slow)
        began = time.monotonic()

        with pytest.raises(httpx.TimeoutException):
            harness_fit._ask_model("task", {})

        assert time.monotonic() - began < 1.4
