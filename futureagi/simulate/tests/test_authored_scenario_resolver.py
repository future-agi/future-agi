"""Scenario resolution preserves execution ownership and legacy run-test scope."""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from simulate.models import CallExecution, RunTest, Scenarios
from simulate.models.hosted_harness import HostedHarnessJob, HostedHarnessScenario
from simulate.models.test_execution import TestExecution as Execution
from simulate.services.harness_scenarios import authored_scenarios_for_calls
from simulate.services.run_results_v3 import _authored_branches


@pytest.fixture
def authored_suite(db, organization, workspace):
    run_test = RunTest.objects.create(
        name="Resolver run", organization=organization, workspace=workspace
    )
    execution = Execution.objects.create(run_test=run_test)
    sibling = Execution.objects.create(run_test=run_test)
    scenario = Scenarios.objects.create(
        name="Resolver scenario", organization=organization, workspace=workspace
    )

    def job(**fields):
        return HostedHarnessJob.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            run_id=uuid.uuid4(),
            idempotency_key=uuid.uuid4().hex,
            request_digest="digest",
            schema_version="1.6",
            seed=1,
            artifact_level="standard",
            max_artifact_bytes=1024,
            deadline_at=timezone.now() + timedelta(hours=1),
            scenario_count=1,
            payload={},
            **fields,
        )

    environment = job()
    own_job = job(environment=environment, run_test=run_test, test_execution=execution)
    sibling_job = job(
        environment=environment, run_test=run_test, test_execution=sibling
    )

    def call(key):
        return CallExecution.objects.create(
            test_execution=execution,
            scenario=scenario,
            call_metadata={"harness_scenario_key": key},
        )

    return execution, environment, own_job, sibling_job, call


def test_execution_scope_prefers_own_run_and_excludes_siblings(
    authored_suite, django_assert_num_queries
):
    execution, environment, own_job, sibling_job, call = authored_suite
    own = HostedHarnessScenario.no_workspace_objects.create(
        job=own_job, scenario_key="shared", branch="Own run"
    )
    fallback = HostedHarnessScenario.no_workspace_objects.create(
        job=environment, scenario_key="environment-only", branch="Environment"
    )
    HostedHarnessScenario.no_workspace_objects.create(
        job=environment, scenario_key="shared", branch="Newer environment"
    )
    for key in ("shared", "environment-only", "sibling-only"):
        HostedHarnessScenario.no_workspace_objects.create(
            job=sibling_job, scenario_key=key, branch="Newer sibling"
        )
    calls = [call(key) for key in ("shared", "environment-only", "sibling-only")]

    with django_assert_num_queries(1):
        resolved = authored_scenarios_for_calls(
            execution.run_test_id, calls, test_execution_id=execution.id
        )
    assert [resolved[item.id] for item in calls] == [own, fallback, None]
    with django_assert_num_queries(1):
        assert _authored_branches(execution, calls) == {
            str(calls[0].id): "Own run",
            str(calls[1].id): "Environment",
            str(calls[2].id): "",
        }


@pytest.mark.parametrize("scoped", [False, True])
def test_direct_link_beats_a_newer_key_match(authored_suite, scoped):
    execution, environment, own_job, _, call = authored_suite
    item = call("shared")
    linked = HostedHarnessScenario.no_workspace_objects.create(
        job=environment,
        scenario_key="linked",
        call_execution=item,
        branch="Linked scenario",
    )
    HostedHarnessScenario.no_workspace_objects.create(
        job=own_job, scenario_key="shared", branch="Newer key match"
    )
    options = {"test_execution_id": execution.id} if scoped else {}
    assert authored_scenarios_for_calls(execution.run_test_id, [item], **options) == {
        item.id: linked
    }


def test_legacy_run_test_scope_retains_newest_sibling_match(authored_suite):
    execution, environment, own_job, sibling_job, call = authored_suite
    item = call("shared")
    for owner in (own_job, environment):
        HostedHarnessScenario.no_workspace_objects.create(
            job=owner, scenario_key="shared"
        )
    newest = HostedHarnessScenario.no_workspace_objects.create(
        job=sibling_job, scenario_key="shared"
    )
    assert authored_scenarios_for_calls(execution.run_test_id, [item]) == {
        item.id: newest
    }


@pytest.mark.parametrize("key", [[], {}, 42, True, None, "", " "])
@pytest.mark.parametrize("scoped", [False, True])
def test_invalid_keys_are_safe_in_both_resolution_modes(authored_suite, key, scoped):
    execution, environment, _, _, call = authored_suite
    unlinked = call(key)
    linked = call(key)
    authored = HostedHarnessScenario.no_workspace_objects.create(
        job=environment, scenario_key="linked", call_execution=linked
    )
    options = {"test_execution_id": execution.id} if scoped else {}
    assert authored_scenarios_for_calls(
        execution.run_test_id, [unlinked, linked], **options
    ) == {unlinked.id: None, linked.id: authored}
