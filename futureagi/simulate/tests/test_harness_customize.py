"""Customize a ready environment: a private copy built from the ready world with a change."""

# The retail and shopmate fixtures are imported, so test arguments name them again.
# ruff: noqa: F811

import json
import uuid
from pathlib import Path

import pytest

from accounts.models.organization import Organization
from simulate.models import (
    HarnessEnvironmentRelease,
    HarnessSuiteRelease,
    HostedHarnessJob,
)
from simulate.services import harness_releases
from simulate.services.harness_releases import (
    complete_customization,
    customization_launch_for,
    design_name,
    list_releases,
)
from simulate.tests.test_harness_release_fit import retail, shopmate  # noqa: F401

CUSTOMIZE = "/simulate/api/harness-releases/customize/"
CHANGE = "Refunds over $500 need a supervisor"


@pytest.fixture
def submit(auth_client, retail, monkeypatch):
    from simulate.temporal import client

    started = []
    monkeypatch.setattr(
        client,
        "start_hosted_harness_gateway_workflow",
        lambda job_id, *args: started.append(job_id),
    )

    def send(**changed):
        return auth_client.post(
            CUSTOMIZE,
            {
                "suite_id": str(retail.id),
                "name": "Retail copy",
                "change": CHANGE,
                "idempotency_key": f"copy-{uuid.uuid4()}",
                **changed,
            },
            format="json",
        )

    send.started = started
    return send


@pytest.mark.django_db
class TestCustomize:
    def test_a_copy_is_a_job_that_starts_from_the_ready_world(self, submit, retail):
        response = submit()

        assert response.status_code == 202, response.content
        job = HostedHarnessJob.no_workspace_objects.get(id=submit.started[0])
        metadata = job.payload["metadata"]
        design = metadata["design"]
        assert design["requirements"] == CHANGE
        assert design["modality"] == "chat"
        assert design["name"].startswith("retail_copy_")
        assert design["template"]["environment"] == [
            {
                "digest": retail.environment.digest,
                "name": retail.environment.name,
                "object_key": retail.environment.object_key,
            }
        ]
        assert design["template"]["suite"]["digest"] == retail.digest
        source = metadata["customization"]["source"]
        assert source["suite_id"] == str(retail.id)
        assert source["environment_digest"] == retail.environment.digest
        assert metadata["customization"]["change"] == CHANGE

    def test_a_copy_needs_no_agent_and_stages_no_provider_credentials(
        self, submit, settings
    ):
        from simulate.models import AgentDefinition

        settings.HARNESS_VERSIONING_ENABLED = True
        agents = AgentDefinition.no_workspace_objects.count()

        response = submit()

        assert response.status_code == 202, response.content
        job = HostedHarnessJob.no_workspace_objects.get(id=submit.started[0])
        # No customer agent: none is pinned, read or made for the copy.
        assert job.agent_version_id is None
        assert AgentDefinition.no_workspace_objects.count() == agents
        agent = job.payload["agent"]
        assert agent["connector"] == "hosted_agent"
        assert not agent.get("secret_refs")
        assert "credential_values" not in job.payload
        assert "agent_version_id" not in job.payload["metadata"]["customization"]

    def test_an_agent_is_not_accepted(self, submit, shopmate):
        response = submit(agent_version_id=str(shopmate.id))

        assert response.status_code == 400, response.content
        assert not submit.started

    def test_a_voice_world_makes_a_voice_copy(self, submit, retail):
        environment = retail.environment
        environment.manifest = {
            **(environment.manifest or {}),
            "metadata": {"catalogue": {"channel": "voice"}},
        }
        environment.save(update_fields=["manifest"])

        submit()

        job = HostedHarnessJob.no_workspace_objects.get(id=submit.started[0])
        assert job.payload["metadata"]["design"]["modality"] == "voice"

    def test_a_copy_needs_a_change(self, submit):
        response = submit(change="   ")

        assert response.status_code == 400, response.content
        assert not submit.started

    def test_an_unknown_ready_environment_is_not_found(self, submit):
        response = submit(suite_id=str(uuid.uuid4()))

        assert response.status_code == 404, response.content

    def test_another_organizations_ready_environment_is_not_found(self, submit):
        other = Organization.objects.create(name="Elsewhere")
        environment = HarnessEnvironmentRelease.no_workspace_objects.create(
            organization=other,
            digest="sha256:" + "9" * 64,
            name="secret",
            object_key="releases/secret",
            manifest={},
            tools=[],
            policy={},
        )
        suite = HarnessSuiteRelease.no_workspace_objects.create(
            organization=other,
            digest="sha256:" + "8" * 64,
            name="secret_base",
            environment=environment,
            object_key="releases/secret_base",
            manifest={"scenarios": []},
            personas=[],
        )

        response = submit(suite_id=str(suite.id))

        assert response.status_code == 404, response.content
        assert not submit.started


def test_a_copys_name_is_a_release_slug():
    assert design_name("Banking · copy", "banking", "k1").startswith("banking_copy_")
    assert design_name("  ", "banking", "k1").startswith("banking_copy_")
    assert design_name("9 lives", "banking", "k1").startswith("banking_copy_")
    assert design_name("Banking copy", "banking", "k1") != design_name(
        "Banking copy", "banking", "k2"
    )


@pytest.mark.django_db
def test_the_copy_is_designed_from_the_staged_ready_world(
    submit,
    retail,
    monkeypatch,
):
    monkeypatch.setattr(harness_releases, "_download", lambda key: b"archive")
    submit()
    job = HostedHarnessJob.no_workspace_objects.get(id=submit.started[0])

    launch = customization_launch_for(job)

    assert launch.command == (
        "python -m fi.alk.harness.design hosted --job /work/job.json "
        "--releases /work/releases --workspace /work/authoring"
    )
    assert [path for path, _ in launch.uploads] == [
        f"/work/release-{retail.environment.digest.removeprefix('sha256:')}.tar.gz",
        "/work/release-suite.tar.gz",
    ]
    assert "/work/releases/suite" in launch.prepare_command


def _result(directory: Path, document: dict) -> Path:
    (directory / "result.json").write_text(json.dumps(document))
    return directory


def _sealed(
    output: Path, *, derived_from: str | None, template_suite: str | None
) -> Path:
    """A completed design's output: the copy's environment and suite manifests, each sealed
    with its digest, naming the source they were made from."""
    from simulate.services.harness_releases import _release_digest

    for name, manifest in (
        (
            "environment/release.json",
            {"name": "copy", "provenance": {"derived_from": derived_from}},
        ),
        (
            "suite/suite.json",
            {"name": "copy_base", "metadata": {"template_suite": template_suite}},
        ),
    ):
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        manifest = {**manifest, "files": []}
        path.write_text(json.dumps({**manifest, "digest": _release_digest(manifest)}))
    return _result(output, {"status": "completed"})


@pytest.mark.django_db
class TestSettleCustomization:
    @pytest.fixture
    def job(self, submit):
        submit()
        return HostedHarnessJob.no_workspace_objects.get(id=submit.started[0])

    def test_a_sealed_copy_is_registered_as_the_organizations_own(
        self,
        job,
        retail,
        tmp_path,
        monkeypatch,
        organization,
    ):
        registered = {}

        def register(environment_dir, suite_dir, *, organization):
            environment = HarnessEnvironmentRelease.no_workspace_objects.create(
                organization=organization,
                digest="sha256:" + "c" * 64,
                name=job.payload["metadata"]["design"]["name"],
                object_key="releases/copy",
                manifest={
                    "provenance": {
                        "derived_from": f"release:{retail.environment.digest}"
                    }
                },
                tools=retail.environment.tools,
                policy={},
            )
            suite = HarnessSuiteRelease.no_workspace_objects.create(
                organization=organization,
                digest="sha256:" + "d" * 64,
                name=f"{environment.name}_base",
                environment=environment,
                object_key="releases/copy_base",
                manifest={"scenarios": [{"key": "a", "origin": "base"}]},
                personas=[],
            )
            registered.update(environment=environment, suite=suite)
            return environment, suite

        monkeypatch.setattr(harness_releases, "register_release", register)
        output = _sealed(
            tmp_path,
            derived_from=f"release:{retail.environment.digest}",
            template_suite=retail.digest,
        )

        settled = complete_customization(job, output)

        assert settled.state == HostedHarnessJob.State.COMPLETED
        result = settled.payload["metadata"]["customization_result"]
        assert result["environment_release_id"] == str(registered["environment"].id)
        assert result["suite_release_id"] == str(registered["suite"].id)
        assert result["scenario_count"] == 1
        copy = next(
            entry
            for entry in list_releases(organization)
            if entry["id"] == str(registered["environment"].id)
        )
        assert copy["system"] is False
        assert copy["derived_from"]["digest"] == retail.environment.digest
        original = next(
            entry
            for entry in list_releases(organization)
            if entry["id"] == str(retail.environment.id)
        )
        assert original["derived_from"] is None

    @pytest.mark.parametrize(
        "lineage",
        [
            {"derived_from": "release:sha256:" + "0" * 64},
            {"derived_from": None},
            {"template_suite": "sha256:" + "0" * 64},
            {"template_suite": None},
        ],
        ids=[
            "another-environment",
            "no-environment-source",
            "another-suite",
            "no-suite-source",
        ],
    )
    def test_a_copy_that_names_another_source_is_refused_before_it_is_stored(
        self, job, retail, tmp_path, monkeypatch, lineage
    ):
        stored = []
        monkeypatch.setattr(
            harness_releases, "_upload", lambda *args: stored.append(args)
        )
        output = _sealed(
            tmp_path,
            **{
                "derived_from": f"release:{retail.environment.digest}",
                "template_suite": retail.digest,
                **lineage,
            },
        )
        releases = HarnessEnvironmentRelease.no_workspace_objects.count()

        settled = complete_customization(job, output)

        assert settled.state == HostedHarnessJob.State.FAILED
        assert settled.failure["code"] == "customization_lineage_invalid"
        # Refused before register_release: nothing uploaded, no release rows.
        assert stored == []
        assert HarnessEnvironmentRelease.no_workspace_objects.count() == releases

    def test_a_design_that_stopped_fails_with_its_reason(self, job, tmp_path):
        output = _result(
            tmp_path,
            {
                "status": "failed",
                "failure": {"code": "design_stopped", "message": "no such records"},
            },
        )

        settled = complete_customization(job, output)

        assert settled.state == HostedHarnessJob.State.FAILED
        assert settled.failure["code"] == "design_stopped"
        assert settled.failure["message"] == "no such records"

    def test_no_output_fails_and_a_settled_copy_keeps_its_outcome(self, job):
        settled = complete_customization(job, None)

        assert settled.state == HostedHarnessJob.State.FAILED
        assert settled.failure["code"] == "design_result_missing"
        again = complete_customization(settled, None)
        assert again.terminal_at == settled.terminal_at

    def test_the_sandbox_ending_keeps_a_settled_copys_outcome(self, job, settings):
        from simulate.services.hosted_harness import record_cleanup, register_attempt

        settings.HARNESS_PUBLIC_BASE_URL = "https://platform.example"
        attempt = register_attempt(
            job.id, endpoint_base_url="https://platform.example"
        ).attempt
        settled = complete_customization(job, None)
        attempt.provider_ref = "sbx-copy"
        attempt.terminal_stage = "completed"
        attempt.terminal_event_received = True
        attempt.save(
            update_fields=[
                "provider_ref",
                "terminal_stage",
                "terminal_event_received",
                "updated_at",
            ]
        )

        ended = record_cleanup(
            attempt.id, provider_ref="sbx-copy", verified_absent=True
        )

        # Settled from its design output, the copy is not judged as a build's snapshot.
        assert ended.state == settled.state
        assert ended.failure["code"] == "design_result_missing"


# --- the gateway: asking the builder, and settling from the sandbox ------------------------------


@pytest.mark.django_db
def test_the_builder_is_asked_for_the_change(submit, monkeypatch):
    from simulate.services import hosted_harness_gateway
    from simulate.tasks import hosted_harness_conversation as runtime

    monkeypatch.setattr(runtime, "schedule_conversation_runtime", lambda *a: None)
    submit()
    job = HostedHarnessJob.no_workspace_objects.get(id=submit.started[0])

    hosted_harness_gateway._ask_harness_to_customize(
        job, attempt=type("Attempt", (), {"id": "a1"})(), endpoint_base_url="https://x"
    )

    message = job.conversation.messages.get()
    assert message.content.startswith(
        "Make a private copy of the ready environment retail, with this change: "
        + CHANGE
    )


@pytest.mark.django_db
def test_the_gateway_settles_a_copy_from_its_design_output(submit):
    from types import SimpleNamespace

    from simulate.tests.test_harness_compositions import (
        _attempted,
        _gateway,
        _Gone,
        _targz,
    )

    submit()
    job = HostedHarnessJob.no_workspace_objects.get(id=submit.started[0])
    attempt = _attempted(job)
    packed = []
    stopped = json.dumps(
        {"status": "failed", "failure": {"code": "design_stopped", "message": "no"}}
    ).encode()

    class Files:
        def download_file(self, path, timeout):
            packed.append(path)
            return _targz({"result.json": stopped})

    sandbox = SimpleNamespace(
        fs=Files(),
        process=SimpleNamespace(
            exec=lambda command, **kwargs: SimpleNamespace(exit_code=0, result=command)
        ),
    )
    # The sandbox is read once; it is gone by the time cleanup checks for it.
    found = iter([sandbox])
    client = _Gone()
    client.get = lambda *args, **kwargs: next(found, None) or _Gone().get()

    settled = _gateway(client)._finish_customization(attempt)

    assert packed == ["/tmp/design.tar.gz"]
    assert settled.state == HostedHarnessJob.State.FAILED
    assert settled.failure["code"] == "design_stopped"


# --- a design that paused on a question: parked, then continued ------------------------------------

WAITING = json.dumps(
    {
        "status": "failed",
        "failure": {
            "code": "design_waiting",
            "message": "the design is waiting for an answer; continue it to resume",
        },
    }
).encode()


def _sandbox(files_by_path):
    """A sandbox whose packed archives are `files_by_path`, read once, then gone."""
    from types import SimpleNamespace

    from simulate.tests.test_harness_compositions import _Gone

    class Files:
        def download_file(self, path, timeout):
            return files_by_path[path]

    sandbox = SimpleNamespace(
        fs=Files(),
        process=SimpleNamespace(
            exec=lambda command, **kwargs: SimpleNamespace(exit_code=0, result="")
        ),
    )
    client = _Gone()
    found = iter([sandbox])
    client.get = lambda *args, **kwargs: next(found, None) or _Gone().get()
    return client


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture
def store(monkeypatch):
    """The object store: release archives and stored checkpoints."""
    stored: dict[str, bytes] = {}
    monkeypatch.setattr(harness_releases, "_upload", stored.__setitem__)
    monkeypatch.setattr(
        harness_releases, "_download", lambda key: stored.get(key, b"archive")
    )
    return stored


@pytest.mark.django_db
def test_a_parked_copy_continues_and_seals(
    submit, retail, store, auth_client, monkeypatch, tmp_path, organization
):
    from simulate.services import hosted_harness_gateway
    from simulate.services.harness_releases import DESIGN_CHECKPOINT_PATH
    from simulate.tasks import hosted_harness_conversation as runtime
    from simulate.tests.test_harness_compositions import _attempted, _gateway, _targz

    scheduled = []
    monkeypatch.setattr(
        runtime, "schedule_conversation_runtime", lambda *a: scheduled.append(a)
    )
    submit()
    job = HostedHarnessJob.no_workspace_objects.get(id=submit.started[0])
    hosted_harness_gateway._ask_harness_to_customize(
        job, attempt=type("Attempt", (), {"id": "a1"})(), endpoint_base_url="https://x"
    )
    asked = job.conversation.messages.count()

    # 1. Nobody answered the builder's question: the design parks with its workspace.
    checkpoint = _targz(
        {
            "design.json": b'{"name": "retail_copy"}',
            ".futureagi-pending-question.json": b'{"question": "Who approves?"}',
        }
    )
    parked = _gateway(
        _sandbox(
            {
                "/tmp/design.tar.gz": _targz({"result.json": WAITING}),
                "/tmp/design-checkpoint.tar.gz": checkpoint,
            }
        )
    )._finish_customization(_attempted(job))

    assert parked.state == HostedHarnessJob.State.PARKED
    assert parked.failure is None and parked.terminal_at is None
    kept = parked.payload["metadata"]["customization_parked"]
    assert store[kept["object_key"]] == checkpoint
    # The sandbox's end was recorded and left the copy parked.
    parked.refresh_from_db()
    assert parked.state == HostedHarnessJob.State.PARKED

    # 2. Continue queues it again, to resume from that workspace.
    response = auth_client.post(f"{CUSTOMIZE}{job.id}/continue/", {}, format="json")

    assert response.status_code == 202, response.content
    assert submit.started == [job.id, job.id] or submit.started == [
        str(job.id),
        str(job.id),
    ]
    job.refresh_from_db()
    assert job.state == HostedHarnessJob.State.QUEUED
    assert job.payload["metadata"]["design"]["resume_from"] == str(job.id)

    # 3. The next sandbox gets the workspace back before the design opens.
    launch = customization_launch_for(job)
    assert launch.uploads[0] == (DESIGN_CHECKPOINT_PATH, checkpoint)
    assert launch.prepare_command.startswith(
        f"mkdir -p /work/authoring && tar -xzf {DESIGN_CHECKPOINT_PATH} -C /work/authoring"
    )

    # 4. The builder isn't sent the change again: it asks its open question again itself.
    hosted_harness_gateway._ask_harness_to_customize(
        job, attempt=type("Attempt", (), {"id": "a2"})(), endpoint_base_url="https://x"
    )
    assert job.conversation.messages.count() == asked
    assert len(scheduled) == 2

    # 5. Answered and approved in the chat, the design seals the copy.
    def register(environment_dir, suite_dir, *, organization):
        environment = HarnessEnvironmentRelease.no_workspace_objects.create(
            organization=organization,
            digest="sha256:" + "c" * 64,
            name="retail_copy",
            object_key="releases/copy",
            manifest={
                "provenance": {"derived_from": f"release:{retail.environment.digest}"}
            },
            tools=[],
            policy={},
        )
        suite = HarnessSuiteRelease.no_workspace_objects.create(
            organization=organization,
            digest="sha256:" + "d" * 64,
            name="retail_copy_base",
            environment=environment,
            object_key="releases/copy_base",
            manifest={"scenarios": [{"key": "a"}, {"key": "b"}]},
            personas=[],
        )
        return environment, suite

    monkeypatch.setattr(harness_releases, "register_release", register)
    sealed = _tree(
        _sealed(
            tmp_path,
            derived_from=f"release:{retail.environment.digest}",
            template_suite=retail.digest,
        )
    )
    settled = _gateway(
        _sandbox({"/tmp/design.tar.gz": _targz(sealed)})
    )._finish_customization(_attempted(job))

    assert settled.state == HostedHarnessJob.State.COMPLETED
    assert settled.payload["metadata"]["customization_result"]["scenario_count"] == 2
    assert settled.current_attempt_number == 2


@pytest.mark.django_db
class TestParkedCopy:
    @pytest.fixture
    def parked(self, submit, store):
        submit()
        job = HostedHarnessJob.no_workspace_objects.get(id=submit.started[0])
        return harness_releases.park_customization(job, _checkpoint())

    def test_only_a_parked_copy_continues(self, parked, auth_client, submit):
        first = auth_client.post(f"{CUSTOMIZE}{parked.id}/continue/", {}, format="json")
        again = auth_client.post(f"{CUSTOMIZE}{parked.id}/continue/", {}, format="json")

        assert first.status_code == 202, first.content
        # Already continuing: answered as it is, and not started twice.
        assert again.status_code == 202, again.content
        assert len(submit.started) == 2

    def test_an_ended_copy_cant_continue(self, parked, auth_client):
        parked.state = HostedHarnessJob.State.FAILED
        parked.save(update_fields=["state"])

        response = auth_client.post(
            f"{CUSTOMIZE}{parked.id}/continue/", {}, format="json"
        )

        assert response.status_code == 409, response.content

    def test_an_unknown_copy_is_not_found(self, auth_client):
        response = auth_client.post(
            f"{CUSTOMIZE}{uuid.uuid4()}/continue/", {}, format="json"
        )

        assert response.status_code == 404, response.content

    def test_cancelling_a_parked_copy_ends_it(self, parked, monkeypatch):
        from simulate.services.hosted_harness import request_cancellation

        canceled = request_cancellation(parked, "user_canceled")

        assert canceled.state == HostedHarnessJob.State.CANCELED
        assert canceled.terminal_at is not None

    def test_a_parked_copy_keeps_waiting(self, parked):
        from simulate.services.harness_environment import (
            STATUS_AWAITING_APPROVAL,
            status_for,
        )
        from simulate.services.hosted_harness_conversation import (
            _TERMINAL_JOB_STATES,
        )

        # Its sandbox ending no longer changes it, and its chat waits for Continue.
        assert complete_customization(parked, None).state == (
            HostedHarnessJob.State.PARKED
        )
        assert status_for(parked) == STATUS_AWAITING_APPROVAL
        assert parked.state in _TERMINAL_JOB_STATES


def _checkpoint() -> bytes:
    from simulate.tests.test_harness_compositions import _targz

    return _targz({"design.json": b"{}"})
