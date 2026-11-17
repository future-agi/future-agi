"""A scenario's text fields filter by Contains, Starts with and Does not contain."""

import pytest
from django.http import QueryDict

from simulate.models import HostedHarnessScenario
from simulate.services import harness_scenarios
from simulate.tests import test_harness_versioning_record as record_tests
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = pytest.mark.django_db

DOCUMENTS = [
    {
        "name": "refund_late_order",
        "scenario_key": "refund-late-order",
        "instruction": "Customer asks for a refund on a late order",
    },
    {
        "name": "late_refund_policy",
        "scenario_key": "late-refund-policy",
        "instruction": "Customer asks about the refund policy",
    },
    {
        "name": "Refund_Upper",
        "scenario_key": "refund-upper",
        "instruction": "REFUND in capitals",
    },
    {
        # No situation written: the stored text is empty.
        "name": "cancel_ride",
        "scenario_key": "cancel-ride",
    },
]
ALL = {
    "refund-late-order",
    "late-refund-policy",
    "refund-upper",
    "cancel-ride",
}


@pytest.fixture(autouse=True)
def versioning_off(settings):
    settings.HARNESS_VERSIONING_ENABLED = False


def _suite(user, workspace, key, documents=DOCUMENTS):
    environment = record_tests._environment(user, workspace, key)
    harness_scenarios.index_scenarios(environment, documents, prune=True)
    return environment


def _both(environment):
    """The suite as the stored query and as the unsaved rows, which must agree."""
    queryset = HostedHarnessScenario.no_workspace_objects.filter(job=environment)
    return [queryset, harness_scenarios.ScenarioRows(queryset)]


def _found(suite, query):
    found = harness_scenarios.apply_filters(suite, QueryDict(query))
    return {one.scenario_key for one in found}


@pytest.fixture
def suites(user, workspace, storage):  # noqa: F811 - fixture
    return _both(_suite(user, workspace, "tm-suites"))


class TestTextMatchers:
    def test_contains_returns_the_rows_whose_name_has_the_text(self, suites):
        for suite in suites:
            assert _found(suite, "name_contains=refund") == {
                "refund-late-order",
                "late-refund-policy",
            }

    def test_contains_reads_the_situation(self, suites):
        for suite in suites:
            assert _found(suite, "instruction_contains=refund") == {
                "refund-late-order",
                "late-refund-policy",
            }

    def test_starts_with_returns_the_rows_whose_name_begins_with_the_text(self, suites):
        for suite in suites:
            assert _found(suite, "name_starts_with=refund") == {"refund-late-order"}
            assert _found(suite, "instruction_starts_with=Customer") == {
                "refund-late-order",
                "late-refund-policy",
            }

    def test_not_contains_returns_every_row_without_the_text(self, suites):
        for suite in suites:
            assert _found(suite, "name_not_contains=refund") == {
                "refund-upper",
                "cancel-ride",
            }

    def test_matching_is_as_case_sensitive_as_equals(self, suites):
        # Equals is an exact match today, so a matcher does not fold case either.
        for suite in suites:
            assert _found(suite, "name=refund_late_order") == {"refund-late-order"}
            assert _found(suite, "name=Refund_late_order") == set()
            assert _found(suite, "name_contains=Refund") == {"refund-upper"}
            assert _found(suite, "name_starts_with=REFUND") == set()
            assert _found(suite, "instruction_contains=REFUND") == {"refund-upper"}

    def test_a_row_with_no_text_is_left_out_by_contains_and_starts_with(self, suites):
        for suite in suites:
            assert "cancel-ride" not in _found(suite, "instruction_contains=a")
            assert "cancel-ride" not in _found(suite, "instruction_starts_with=C")

    def test_a_row_with_no_text_is_kept_by_not_contains(self, suites):
        for suite in suites:
            assert _found(suite, "instruction_not_contains=refund") == {
                "refund-upper",
                "cancel-ride",
            }

    def test_repeated_keys_are_alternatives_and_not_contains_drops_every_one(
        self, suites
    ):
        for suite in suites:
            assert _found(suite, "name_contains=ride&name_contains=policy") == {
                "cancel-ride",
                "late-refund-policy",
            }
            assert _found(suite, "name_not_contains=ride&name_not_contains=policy") == {
                "refund-late-order",
                "refund-upper",
            }

    def test_matchers_and_other_filters_narrow_together(self, suites):
        for suite in suites:
            assert _found(
                suite, "name_contains=refund&instruction_not_contains=policy"
            ) == {"refund-late-order"}

    def test_the_stored_percent_and_underscore_are_matched_as_typed(
        self,
        user,
        workspace,
        storage,  # noqa: F811 - fixture
    ):
        documents = [
            {"name": "a_b", "scenario_key": "k1", "instruction": "100% sure"},
            {"name": "axb", "scenario_key": "k2", "instruction": "100 sure"},
        ]
        for suite in _both(_suite(user, workspace, "tm-wild", documents)):
            assert _found(suite, "name_contains=a_b") == {"k1"}
            assert _found(suite, "instruction_contains=100%25") == {"k1"}
            assert _found(suite, "name_starts_with=a_") == {"k1"}

    def test_an_unknown_operator_narrows_nothing(self, suites):
        for suite in suites:
            assert _found(suite, "name_sounds_like=refund") == ALL
            assert _found(suite, "name_ends_with=order") == ALL

    def test_a_matcher_on_a_field_that_is_not_text_narrows_nothing(self, suites):
        for suite in suites:
            assert _found(suite, "use_case_contains=refunds") == ALL
            assert _found(suite, "persona.gender_starts_with=f") == ALL
            assert _found(suite, "use_case_not_contains=refunds") == ALL

    def test_equals_and_not_equals_match_the_whole_text_as_before(self, suites):
        for suite in suites:
            assert _found(suite, "name=refund_late_order&name=cancel_ride") == {
                "refund-late-order",
                "cancel-ride",
            }
            assert _found(suite, "name=refund") == set()
            assert _found(suite, "name_not=refund_late_order") == ALL - {
                "refund-late-order"
            }
            assert _found(suite, "name_not=refund") == ALL


class TestScenariosEndpoint:
    def _keys(self, auth_client, environment, query):
        response = auth_client.get(
            f"/simulate/api/harness-jobs/{environment.id}/scenarios/?{query}"
        )
        assert response.status_code == 200, response.content
        return {one["scenario_key"] for one in response.json()["results"]}

    def test_the_list_answers_with_the_rows_that_match(
        self,
        user,
        workspace,
        storage,
        auth_client,  # noqa: F811 - fixture
    ):
        environment = _suite(user, workspace, "tm-list")

        assert self._keys(auth_client, environment, "name_contains=refund") == {
            "refund-late-order",
            "late-refund-policy",
        }
        assert self._keys(auth_client, environment, "name_starts_with=refund") == {
            "refund-late-order"
        }
        assert self._keys(auth_client, environment, "name_not_contains=refund") == {
            "refund-upper",
            "cancel-ride",
        }

    def test_a_match_stays_inside_the_scenarios_environment(
        self,
        user,
        workspace,
        storage,
        auth_client,  # noqa: F811 - fixture
    ):
        mine = _suite(user, workspace, "tm-mine")
        _suite(
            user,
            workspace,
            "tm-other",
            [{"name": "refund_elsewhere", "scenario_key": "elsewhere"}],
        )

        assert self._keys(auth_client, mine, "name_contains=refund") == {
            "refund-late-order",
            "late-refund-policy",
        }
