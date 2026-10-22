"""The Environment read shows the saved observability refusal only while the agent's switch is off."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from simulate.models import HostedHarnessJob
from simulate.models.agent_setup import AgentVersionSetup
from simulate.services.harness_provider import serialize_job
from simulate.tests.test_agent_setup_service import source_agent
from simulate.tests.test_hosted_harness_connected_agent import (
    _definition,
    _job,
    _provision,
    _stored_refusal,
)
from tracer.models.observability_provider import ObservabilityProvider
from tracer.services import observability_switch

PROVIDER_TABLE = "tracer_observability_provider"


def _refused_environment(organization, workspace, key):
    """A real provision that asked for observability on code with no instrumenter."""
    job, capability = _job(
        organization, workspace, key, metadata={"observability_enabled": True}
    )
    response = _provision(capability, agent=source_agent(instrumentation="none"))
    assert response.status_code == 200, response.content
    job.refresh_from_db()
    saved = _stored_refusal(job)
    assert saved is not None
    return job, saved


def _read_metadata(job):
    return serialize_job(HostedHarnessJob.objects.get(id=job.id))["job"]["metadata"]


def _switch(job, *, enabled):
    agent = _definition(job)
    return observability_switch.set_agent_observability(
        agent_definition=agent,
        enabled=enabled,
        user_id=None,
        organization=job.organization,
        workspace=job.workspace,
    )


def _add_instrumenter(job):
    # The owner fixes the code and authors again; only the stored word matters to the switch.
    updated = AgentVersionSetup.all_objects.filter(
        agent_version__agent_definition=_definition(job)
    ).update(instrumentation="traceai")
    assert updated == 1


@pytest.mark.django_db
def test_off_the_saved_refusal_is_read_as_saved(organization, workspace):
    job, saved = _refused_environment(organization, workspace, "read-off")

    assert _definition(job).observability_provider_id is None
    assert _read_metadata(job)["observability_refusal"] == saved


@pytest.mark.django_db
def test_on_the_refusal_is_hidden_from_the_read_but_kept_in_storage(
    organization, workspace
):
    # Written with the shape the provision saves, since the agent is turned on from the start.
    job, capability = _job(
        organization, workspace, "read-on", metadata={"observability_enabled": True}
    )
    response = _provision(capability, agent=source_agent(instrumentation="traceai"))
    assert response.status_code == 200, response.content
    job.refresh_from_db()
    assert _definition(job).observability_provider.enabled is True
    refusal = {
        "code": "instrumentation_missing",
        "details": {"instrumentation": ["saved earlier"]},
    }
    payload = dict(job.payload)
    payload["metadata"] = {**payload["metadata"], "observability_refusal": refusal}
    HostedHarnessJob.objects.filter(id=job.id).update(payload=payload)

    metadata = _read_metadata(job)

    assert "observability_refusal" not in metadata
    assert metadata["observability_enabled"] is True
    assert (
        HostedHarnessJob.objects.get(id=job.id).payload["metadata"][
            "observability_refusal"
        ]
        == refusal
    )


@pytest.mark.django_db
def test_turned_on_later_from_the_agent_page_the_refusal_is_no_longer_read(
    organization, workspace
):
    job, saved = _refused_environment(organization, workspace, "read-later")
    assert _read_metadata(job)["observability_refusal"] == saved

    _add_instrumenter(job)
    row = _switch(job, enabled=True)

    assert row is not None and row.enabled is True
    assert "observability_refusal" not in _read_metadata(job)
    assert _stored_refusal(job) == saved


@pytest.mark.django_db
def test_turned_off_again_the_refusal_is_read_again(organization, workspace):
    job, saved = _refused_environment(organization, workspace, "read-again")
    _add_instrumenter(job)
    _switch(job, enabled=True)
    assert "observability_refusal" not in _read_metadata(job)

    _switch(job, enabled=False)

    assert ObservabilityProvider.no_workspace_objects.count() == 1
    assert _definition(job).observability_provider.enabled is False
    assert _read_metadata(job)["observability_refusal"] == saved


@pytest.mark.django_db
def test_a_job_with_no_refusal_reads_its_metadata_without_the_provider_lookup(
    organization, workspace
):
    job, capability = _job(
        organization, workspace, "read-none", metadata={"observability_enabled": True}
    )
    response = _provision(capability, agent=source_agent(instrumentation="traceai"))
    assert response.status_code == 200, response.content
    stored = HostedHarnessJob.objects.get(id=job.id)
    assert stored.run_test_id is not None
    assert "observability_refusal" not in stored.payload["metadata"]

    with CaptureQueriesContext(connection) as queries:
        result = serialize_job(stored)

    assert result["job"]["metadata"] == stored.payload["metadata"]
    assert [q for q in queries.captured_queries if PROVIDER_TABLE in q["sql"]] == []


@pytest.mark.django_db
def test_a_saved_refusal_costs_one_provider_lookup(organization, workspace):
    job, _ = _refused_environment(organization, workspace, "read-one-query")
    stored = HostedHarnessJob.objects.get(id=job.id)

    with CaptureQueriesContext(connection) as queries:
        serialize_job(stored)

    lookups = [q for q in queries.captured_queries if PROVIDER_TABLE in q["sql"]]
    assert len(lookups) == 1


@pytest.mark.django_db
def test_a_refusal_on_a_job_with_no_run_test_is_read_as_saved(organization, workspace):
    job, _ = _job(organization, workspace, "read-no-run-test")
    refusal = {"code": "bound", "details": {}}
    payload = dict(job.payload)
    payload["metadata"] = {**payload["metadata"], "observability_refusal": refusal}
    HostedHarnessJob.objects.filter(id=job.id).update(payload=payload)
    stored = HostedHarnessJob.objects.get(id=job.id)
    assert stored.run_test_id is None

    with CaptureQueriesContext(connection) as queries:
        metadata = serialize_job(stored)["job"]["metadata"]

    assert metadata["observability_refusal"] == refusal
    assert [q for q in queries.captured_queries if PROVIDER_TABLE in q["sql"]] == []
