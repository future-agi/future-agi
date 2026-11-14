from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from simulate.models import CallExecution, TestExecution
from simulate.services.hosted_harness import create_hosted_job, register_attempt

from .test_hosted_harness_channels import _payload
from .test_hosted_harness_eval_selection import _provision

FULL_PROMPT = "Greeting: say hello.\n" + "Follow every booking rule exactly. " * 40
EXCERPT = "Greeting: say hello."


def _excerpt_era_harness_run(organization, workspace, *, key="backfill-key"):
    """A phone harness run whose agent, version and call stored only the excerpt."""
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key=key, workspace=workspace
    )
    job.payload = {
        **job.payload,
        "agent": {
            "connector": "phone",
            "config": {"target_system_prompt": FULL_PROMPT},
            "secret_refs": {},
        },
    }
    job.stage_outputs = [
        {"kind": "contract", "data": {"system_prompt_excerpt": EXCERPT}}
    ]
    job.save(update_fields=["payload", "stage_outputs"])
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    assert _provision(APIClient(), capability).status_code == 200
    job.refresh_from_db()

    agent = job.run_test.agent_definition
    agent.description = EXCERPT
    agent.save(update_fields=["description"])
    version = agent.latest_version
    version.description = EXCERPT
    version.configuration_snapshot = {
        **version.configuration_snapshot,
        "description": EXCERPT,
    }
    version.save(update_fields=["description", "configuration_snapshot"])
    execution = TestExecution.objects.create(
        run_test=job.run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        agent_definition=agent,
        agent_version=version,
    )
    call = CallExecution.objects.create(
        test_execution=execution,
        scenario=job.run_test.scenarios.first(),
        status=CallExecution.CallStatus.COMPLETED,
        agent_version=version,
        call_metadata={"agent_description": EXCERPT, "agent_prompt": EXCERPT},
    )
    return job, agent, version, call


def _run(*args):
    out = StringIO()
    call_command("backfill_harness_agent_prompts", *args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
def test_dry_run_reports_without_writing(organization, workspace):
    _, agent, version, call = _excerpt_era_harness_run(organization, workspace)

    output = _run("--dry-run")

    assert "DRY RUN" in output and "1 agents, 1 versions, 1 calls" in output
    agent.refresh_from_db()
    version.refresh_from_db()
    call.refresh_from_db()
    assert agent.description == EXCERPT
    assert version.configuration_snapshot["description"] == EXCERPT
    assert call.call_metadata["agent_prompt"] == EXCERPT


@pytest.mark.django_db
def test_backfill_restores_the_full_prompt_everywhere_and_is_idempotent(
    organization, workspace
):
    _, agent, version, call = _excerpt_era_harness_run(organization, workspace)

    _run()

    full = FULL_PROMPT.strip()
    agent.refresh_from_db()
    version.refresh_from_db()
    call.refresh_from_db()
    assert agent.description == full
    assert version.description == full
    assert version.configuration_snapshot["description"] == full
    assert call.call_metadata["agent_description"] == full
    assert call.call_metadata["agent_prompt"] == full
    assert "0 agents, 0 versions, 0 calls" in _run()


@pytest.mark.django_db
def test_a_description_that_is_not_part_of_the_prompt_is_left_alone(
    organization, workspace
):
    _, agent, version, _ = _excerpt_era_harness_run(organization, workspace)
    agent.description = "A hand-written description."
    agent.save(update_fields=["description"])

    _run()

    agent.refresh_from_db()
    version.refresh_from_db()
    assert agent.description == "A hand-written description."
    assert version.configuration_snapshot["description"] == FULL_PROMPT.strip()
