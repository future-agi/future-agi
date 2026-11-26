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
from simulate.models.agent_definition import ProviderCredentials
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
def submit(auth_client, retail, shopmate, monkeypatch):
    from simulate.services import harness_provider
    from simulate.temporal import client

    ProviderCredentials.objects.create(
        agent_version=shopmate, provider_type="retell", api_key="test-key"
    )
    started = []
    monkeypatch.setattr(
        client,
        "start_hosted_harness_gateway_workflow",
        lambda job_id, *args: started.append(job_id),
    )
    monkeypatch.setattr(
        harness_provider, "_validate_known_hosted_egress", lambda *args: None
    )

    def send(**changed):
        return auth_client.post(
            CUSTOMIZE,
            {
                "suite_id": str(retail.id),
                "agent_version_id": str(shopmate.id),
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
        assert "test-key" not in json.dumps(job.payload)

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
        output = _result(tmp_path, {"status": "completed"})

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

    def test_a_copy_that_names_another_source_is_refused(
        self,
        job,
        retail,
        tmp_path,
        monkeypatch,
    ):
        def register(environment_dir, suite_dir, *, organization):
            environment = HarnessEnvironmentRelease.no_workspace_objects.create(
                organization=organization,
                digest="sha256:" + "e" * 63 + "f",
                name="elsewhere",
                object_key="releases/elsewhere",
                manifest={"provenance": {"derived_from": "release:sha256:" + "0" * 64}},
                tools=[],
                policy={},
            )
            return environment, None

        monkeypatch.setattr(harness_releases, "register_release", register)

        settled = complete_customization(
            job, _result(tmp_path, {"status": "completed"})
        )

        assert settled.state == HostedHarnessJob.State.FAILED
        assert settled.failure["code"] == "customization_lineage_invalid"

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
