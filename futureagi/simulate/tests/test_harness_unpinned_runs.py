import pytest
from django.test import override_settings

from simulate.models import HarnessAgentVersionRuntime
from simulate.services import hosted_harness
from simulate.services.agent_setup import stored_agent
from simulate.services.harness_agent_versions import fill_agent_pin
from simulate.services.hosted_harness import (
    begin_scenarios,
    create_hosted_job,
    create_selected_harness_run,
    provision_scenarios,
    register_attempt,
)
from simulate.tests.test_harness_provider import _v1_payload
from simulate.tests.test_hosted_harness_authored_agent import _sent

pytestmark = pytest.mark.django_db


def _provisioned(user, workspace, key):
    """A provisioned build with no agent pin; returns its attempt."""
    environment, _ = create_hosted_job(
        user.organization,
        _v1_payload(scenario_count=1),
        idempotency_key=key,
        workspace=workspace,
    )
    attempt = register_attempt(
        environment.id, endpoint_base_url="https://harness.example.test"
    ).attempt
    provision_scenarios(
        attempt,
        {
            "operation": "provision",
            "name": "Unpinned suite",
            "modality": "text",
            "personas": [
                {
                    "scenario_key": "scenario-a",
                    "name": "A",
                    "role": "customer",
                    "situation": "First situation",
                    "outcome": "First outcome",
                    "persona": {"name": "A"},
                }
            ],
        },
    )
    return attempt


def _environment(user, workspace, key):
    """A built environment with no agent pin and no versions of its own."""
    environment = _provisioned(user, workspace, key).job
    environment.refresh_from_db()
    payload = dict(environment.payload)
    payload["metadata"] = {
        **(payload.get("metadata") or {}),
        "authoring_object_key": "harness/environments/authored.tar.gz",
    }
    environment.payload = payload
    environment.state = environment.State.COMPLETED
    environment.current_stage = "completed"
    environment.save(update_fields=["payload", "state", "current_stage", "updated_at"])
    return environment


def _run(environment, key):
    with override_settings(HARNESS_MAX_ARTIFACT_BYTES=10_737_418_240):
        run, _ = create_selected_harness_run(
            environment, scenario_keys=["scenario-a"], trials=1, idempotency_key=key
        )
    return run.test_execution


def _active_then_newer(agent, newer_status):
    agent.versions.update(status="archived")
    active = agent.create_version(description="p", commit_message="v1", status="active")
    agent.create_version(description="p2", commit_message="v2", status=newer_status)
    return active


@pytest.mark.parametrize("versioning", [True, False])
@pytest.mark.parametrize("newer_status", ["draft", "archived"])
def test_an_unpinned_run_is_filed_under_the_active_version(
    user, workspace, settings, versioning, newer_status
):
    settings.HARNESS_VERSIONING_ENABLED = versioning
    environment = _environment(user, workspace, f"unpinned-{versioning}-{newer_status}")
    agent = environment.run_test.agent_definition
    active = _active_then_newer(agent, newer_status)
    assert environment.agent_version_id is None

    assert _run(environment, "run").agent_version_id == active.id


def test_an_environment_owned_agent_runs_the_environments_default(
    user, workspace, settings
):
    settings.HARNESS_VERSIONING_ENABLED = True
    environment = _environment(user, workspace, "unpinned-owned")
    agent = environment.run_test.agent_definition
    default = agent.create_version(description="d", commit_message="d", status="draft")
    _held(default, agent_payload={"definition_owner": "environment"})
    _active_then_newer(agent, "draft")
    environment.agent_version = default
    environment.save(update_fields=["agent_version", "updated_at"])

    assert _run(environment, "run").agent_version_id == default.id


def _held(version, agent_payload=None):
    """Give a version a credential set of its own, as an environment's version has."""
    HarnessAgentVersionRuntime.no_workspace_objects.create(
        agent_version=version,
        agent_definition=version.agent_definition,
        organization=version.organization,
        workspace=version.workspace,
        hash="sha256:" + "0" * 64,
        source_kind="github",
        agent_payload=agent_payload or {},
    )


def _unheld_then_held_active(agent):
    """v1 free of any environment's credentials, v2 the active one but held."""
    agent.versions.update(status="archived")
    free = agent.create_version(description="p", commit_message="v1", status="draft")
    held = agent.create_version(description="p2", commit_message="v2", status="active")
    _held(held)
    return free


def test_a_shared_agents_build_is_filed_under_a_version_no_environment_holds(
    user, workspace, settings
):
    attempt = _provisioned(user, workspace, "unpinned-build")
    job = attempt.job
    job.refresh_from_db()
    assert job.agent_version_id is None
    free = _unheld_then_held_active(job.run_test.agent_definition)
    settings.HARNESS_VERSIONING_ENABLED = True

    begin_scenarios(
        attempt, {"run_test_id": str(job.run_test_id), "scenario_keys": ["scenario-a"]}
    )

    job.refresh_from_db()
    assert job.test_execution.agent_version_id == free.id


def test_an_unpinned_build_stores_its_agent_on_the_version_it_is_pinned_to(
    user, workspace, settings
):
    job = _provisioned(user, workspace, "unpinned-store").job
    job.refresh_from_db()
    assert job.agent_version_id is None
    agent = job.run_test.agent_definition
    _unheld_then_held_active(agent)
    settings.HARNESS_VERSIONING_ENABLED = True
    job.payload = {**job.payload, "source": {"kind": "github"}}

    hosted_harness._store_authored_agent(job, agent, _sent())
    assert fill_agent_pin(job)

    assert stored_agent(job.agent_version_id)["kind"] == "source"


def test_a_pinned_shared_agents_build_is_filed_under_its_pin(user, workspace, settings):
    attempt = _provisioned(user, workspace, "pinned-build")
    job = attempt.job
    job.refresh_from_db()
    agent = job.run_test.agent_definition
    pin = _unheld_then_held_active(agent)
    settings.HARNESS_VERSIONING_ENABLED = True
    assert fill_agent_pin(job)
    job.save(update_fields=["agent_version", "updated_at"])
    assert job.agent_version_id == pin.id
    # A newer active version appears before the build begins.
    agent.create_version(description="p3", commit_message="v3", status="active")

    begin_scenarios(
        attempt, {"run_test_id": str(job.run_test_id), "scenario_keys": ["scenario-a"]}
    )

    job.refresh_from_db()
    assert job.test_execution.agent_version_id == pin.id
