import uuid

import pytest
from rest_framework.test import APIClient

from simulate.models import HostedHarnessScenario
from simulate.services.harness_scenarios import index_scenarios
from simulate.services.hosted_harness import create_selected_harness_run
from simulate.tests.test_harness_amend_archive import NAMES, _key, _run_environment

BASE = "/simulate/api/harness-environments"
LANGUAGES = ["English", "Spanish", "Hindi"]


@pytest.fixture
def environment(user, workspace):
    environment = _run_environment(user, workspace, "environment-scenarios")
    index_scenarios(
        environment,
        [
            {
                "name": name,
                "scenario_key": _key(name),
                "use_case": "Bookings",
                "branch": f"branch of {name}",
                "persona": {"name": name, "languages": LANGUAGES, "multilingual": True},
            }
            for name in NAMES[:2]
        ],
    )
    return environment


@pytest.fixture
def client(user, workspace):
    client = APIClient()
    client.force_authenticate(user=user)
    client.defaults["HTTP_X_WORKSPACE_ID"] = str(workspace.id)
    return client


@pytest.mark.django_db
def test_scenarios_are_listed_by_row_id_with_every_language(client, environment):
    response = client.get(f"{BASE}/{environment.id}/scenarios/", {"group_by": ""})

    assert response.status_code == 200, response.content
    body = response.json()
    rows = HostedHarnessScenario.no_workspace_objects.filter(job=environment)
    assert {one["id"] for one in body["results"]} == {str(row.id) for row in rows}
    assert {one["scenario_key"] for one in body["results"]} == {
        _key(name) for name in NAMES[:2]
    }
    for one in body["results"]:
        assert one["persona"]["languages"] == LANGUAGES
        assert one["persona"]["multilingual"] is True
    assert body["count"] == 2
    assert {"groups", "fields", "scenario_editing", "groupings", "level_labels"} <= set(
        body
    )


@pytest.mark.django_db
def test_the_environment_list_matches_the_older_route(client, environment):
    params = {"group_by": "goal", "ordering": "-number"}
    new = client.get(f"{BASE}/{environment.id}/scenarios/", params).json()
    old = client.get(
        f"/simulate/api/harness-jobs/{environment.id}/scenarios/", params
    ).json()

    assert new == old


@pytest.mark.django_db
def test_coverage_is_served_for_the_environment(client, environment):
    response = client.get(f"{BASE}/{environment.id}/scenarios/coverage/")

    assert response.status_code == 200, response.content
    assert {"per_axis", "rows", "columns", "cells", "axes"} <= set(response.json())


@pytest.mark.django_db
def test_one_scenario_is_read_by_its_row_id(client, environment):
    row = HostedHarnessScenario.no_workspace_objects.filter(job=environment).first()

    response = client.get(f"{BASE}/{environment.id}/scenarios/{row.id}/")

    assert response.status_code == 200, response.content
    assert response.json()["id"] == str(row.id)
    assert response.json()["persona"]["languages"] == LANGUAGES


@pytest.mark.django_db
def test_unknown_scenarios_and_run_ids_are_not_found(client, environment):
    row = HostedHarnessScenario.no_workspace_objects.filter(job=environment).first()
    run, _ = create_selected_harness_run(
        environment,
        scenario_keys=[row.scenario_key],
        trials=1,
        idempotency_key="scenario-detail-run",
    )

    assert (
        client.get(f"{BASE}/{environment.id}/scenarios/{uuid.uuid4()}/").status_code
        == 404
    )
    assert client.get(f"{BASE}/{run.id}/scenarios/").status_code == 404
    assert client.get(f"{BASE}/{run.id}/scenarios/{row.id}/").status_code == 404
