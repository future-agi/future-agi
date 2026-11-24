"""Free-text tags on an environment's scenarios: kept apart from the versioned suite."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from simulate.models import HostedHarnessJob, HostedHarnessScenario
from simulate.models.harness_scenario_tags import HarnessScenarioTags
from simulate.services import harness_scenario_tags as tags_service
from simulate.services.harness_scenarios import index_scenarios
from simulate.services.hosted_harness import HostedHarnessError
from simulate.tests.test_harness_scenario_amend import SUITE, _job

pytestmark = pytest.mark.django_db


@pytest.fixture
def environment(user, workspace):
    job = _job(user, workspace, SUITE)
    index_scenarios(job, SUITE)
    return job


def _keys(environment):
    return list(
        HostedHarnessScenario.no_workspace_objects.filter(job=environment)
        .order_by("number")
        .values_list("scenario_key", flat=True)
    )


def _client(user, environment):
    client = APIClient()
    client.force_authenticate(user=user)
    client.credentials(HTTP_X_WORKSPACE_ID=str(environment.workspace_id))
    return client


def _base(environment):
    return f"/simulate/api/harness-environments/{environment.id}/scenario-tags/"


class TestCleaning:
    def test_tags_are_trimmed_and_kept_once_in_the_order_given(self):
        assert tags_service.clean_tags([" billing ", "", "regression", "billing"]) == [
            "billing",
            "regression",
        ]

    def test_a_too_long_tag_or_too_many_tags_are_refused(self):
        with pytest.raises(HostedHarnessError):
            tags_service.clean_tags(["x" * (tags_service.MAX_TAG_LENGTH + 1)])
        with pytest.raises(HostedHarnessError):
            tags_service.clean_tags(
                [f"t{n}" for n in range(tags_service.MAX_TAGS_PER_SCENARIO + 1)]
            )


class TestService:
    def test_setting_tags_replaces_them_and_an_empty_list_clears_the_row(
        self, environment
    ):
        key = _keys(environment)[0]

        assert tags_service.set_tags(environment, key, ["billing"]) == ["billing"]
        assert tags_service.set_tags(environment, key, ["refunds"]) == ["refunds"]
        assert tags_service.tags_by_key(environment, [key]) == {key: ["refunds"]}

        tags_service.set_tags(environment, key, [])
        assert not HarnessScenarioTags.all_objects.filter(
            environment=environment, scenario_key=key
        ).exists()

    def test_a_scenario_the_environment_does_not_have_is_refused(self, environment):
        with pytest.raises(HostedHarnessError) as refused:
            tags_service.set_tags(environment, "no-such-scenario", ["billing"])

        assert refused.value.code == "scenario_not_found"

    def test_a_bulk_change_adds_and_removes_without_touching_other_tags(
        self, environment
    ):
        first, second = _keys(environment)
        tags_service.set_tags(environment, first, ["billing", "old"])

        changed = tags_service.change_tags(
            environment, [first, second], add=["regression"], remove=["old"]
        )

        assert changed == {first: ["billing", "regression"], second: ["regression"]}
        assert tags_service.environment_tags(environment) == ["billing", "regression"]

    @pytest.mark.parametrize("change", ["set", "change"])
    def test_a_tag_change_holds_the_environment_before_it_reads_the_tags(
        self, environment, change
    ):
        key = _keys(environment)[0]
        job_table = HostedHarnessJob._meta.db_table
        tags_table = HarnessScenarioTags._meta.db_table

        with CaptureQueriesContext(connection) as queries:
            if change == "set":
                tags_service.set_tags(environment, key, ["billing"])
            else:
                tags_service.change_tags(environment, [key], add=["billing"])

        statements = [query["sql"] for query in queries.captured_queries]
        held = next(
            index
            for index, sql in enumerate(statements)
            if f'FROM "{job_table}"' in sql and "FOR UPDATE" in sql
        )
        first_tags_read = next(
            index
            for index, sql in enumerate(statements)
            if f'FROM "{tags_table}"' in sql
        )
        assert held < first_tags_read
        assert tags_service.tags_by_key(environment, [key]) == {key: ["billing"]}

    def test_tagging_never_changes_the_versioned_suite(self, environment):
        before = list(
            HostedHarnessScenario.no_workspace_objects.filter(
                job=environment
            ).values_list("id", "scenario_key", "number")
        )

        tags_service.change_tags(environment, _keys(environment), add=["billing"])

        after = list(
            HostedHarnessScenario.no_workspace_objects.filter(
                job=environment
            ).values_list("id", "scenario_key", "number")
        )
        assert after == before


class TestApi:
    def test_tags_are_listed_set_and_changed_in_bulk(self, user, environment):
        client = _client(user, environment)
        first, second = _keys(environment)

        set_one = client.put(
            f"{_base(environment)}scenarios/{first}/",
            {"tags": ["billing"]},
            format="json",
        )
        assert set_one.status_code == 200, set_one.content
        assert set_one.json() == {"scenario_key": first, "tags": ["billing"]}

        changed = client.post(
            f"{_base(environment)}change/",
            {"scenario_keys": [first, second], "add": ["regression"]},
            format="json",
        )
        assert changed.status_code == 200, changed.content
        assert changed.json()["tags"] == ["billing", "regression"]

        listed = client.get(_base(environment))
        assert listed.json() == {"tags": ["billing", "regression"]}

    def test_the_scenario_list_shows_each_rows_tags_and_filters_by_them(
        self, user, environment
    ):
        client = _client(user, environment)
        first, second = _keys(environment)
        tags_service.set_tags(environment, first, ["billing"])
        tags_service.set_tags(environment, second, ["refunds"])
        scenarios = f"/simulate/api/harness-jobs/{environment.id}/scenarios/"

        page = client.get(scenarios, {"group_by": ""}).json()
        rows = {row["scenario_key"]: row["tags"] for row in page["results"]}
        assert rows == {first: ["billing"], second: ["refunds"]}
        field = next(one for one in page["fields"] if one["value"] == "tags")
        assert field["counts"] == {"billing": 1, "refunds": 1}

        only = client.get(scenarios, {"group_by": "", "tags": "billing"}).json()
        assert [row["scenario_key"] for row in only["results"]] == [first]

        without = client.get(scenarios, {"group_by": "", "tags_not": "billing"}).json()
        assert [row["scenario_key"] for row in without["results"]] == [second]

    def test_the_coverage_grid_counts_only_the_scenarios_with_the_picked_tags(
        self, user, environment
    ):
        client = _client(user, environment)
        first, second = _keys(environment)
        for key, task in ((first, "refund"), (second, "booking")):
            HostedHarnessScenario.no_workspace_objects.filter(
                job=environment, scenario_key=key
            ).update(coverage={"task": task, "overlay": "none"})
        tags_service.set_tags(environment, first, ["billing"])
        coverage = f"/simulate/api/harness-jobs/{environment.id}/scenarios/coverage/"
        axes = {"row_axis": "task", "col_axis": "overlay"}

        everything = client.get(coverage, axes)
        only = client.get(coverage, {**axes, "tags": "billing"})
        without = client.get(coverage, {**axes, "tags_not": "billing"})

        assert everything.status_code == 200, everything.content
        assert everything.json()["rows"] == ["booking", "refund"]
        assert only.json()["rows"] == ["refund"]
        assert only.json()["cells"] == [{"row": "refund", "column": "none", "count": 1}]
        assert without.json()["rows"] == ["booking"]

    def test_an_empty_change_is_refused(self, user, environment):
        response = _client(user, environment).post(
            f"{_base(environment)}change/",
            {"scenario_keys": _keys(environment)},
            format="json",
        )

        assert response.status_code == 400
        assert response.json()["error"] == "scenario_tags_invalid"

    def test_an_unknown_environment_is_not_found(self, user, environment):
        response = _client(user, environment).get(
            "/simulate/api/harness-environments/"
            "00000000-0000-0000-0000-000000000000/scenario-tags/"
        )

        assert response.status_code == 404
