"""Editing and reading a finished run's authored suite."""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from simulate.models import HostedHarnessJob, HostedHarnessStageOutput

pytestmark = pytest.mark.django_db


def _job(user, workspace, scenarios):
    job = HostedHarnessJob.no_workspace_objects.create(
        organization=user.organization,
        workspace=workspace,
        run_id=uuid.uuid4(),
        idempotency_key=uuid.uuid4().hex,
        request_digest=uuid.uuid4().hex,
        schema_version="1.6",
        seed=1,
        artifact_level="standard",
        max_artifact_bytes=1024,
        deadline_at=timezone.now() + timedelta(hours=1),
        scenario_count=len(scenarios),
        payload={"metadata": {}, "runtime": {"max_duration_seconds": 600}},
    )
    HostedHarnessStageOutput.no_workspace_objects.create(
        job=job,
        title="Scenarios",
        summary=f"{len(scenarios)} pre-authored scenarios",
        kind="scenarios",
        data=scenarios,
    )
    return job


SUITE = [
    {"name": "one", "tests": "the agent holds the line", "max_turns": 10},
    {"name": "two", "tests": "the agent asks", "persona": {"accent": "American"}},
]


def _client(user, job):
    """A caller scoped to the job's workspace, as the UI is."""
    client = APIClient()
    client.force_authenticate(user=user)
    client.credentials(HTTP_X_WORKSPACE_ID=str(job.workspace_id))
    return client


def _post(user, job, changes, rework=True):
    return _client(user, job).post(
        f"/simulate/api/harness-jobs/{job.id}/scenarios/amend/",
        {"changes": changes, "rework": rework},
        format="json",
    )


def test_a_descriptive_edit_lands_on_the_suite(user, workspace):
    job = _job(user, workspace, SUITE)
    with patch(
        "simulate.services.hosted_harness_gateway.push_scenarios_into_live_sandbox",
        return_value=False,
    ), patch(
        "simulate.services.hosted_harness_gateway.rewrite_authoring_scenarios",
        return_value=None,
    ):
        response = _post(user, job, [
            {"op": "set_field", "scenario": "one", "field": "tests", "value": "reworded"}
        ])
    assert response.status_code == 200, response.content
    assert response.json()["receipts"][0]["outcome"] == "applied"
    output = HostedHarnessStageOutput.no_workspace_objects.get(job=job, kind="scenarios")
    assert output.data[0]["tests"] == "reworded"


def test_a_field_that_is_proved_rather_than_described_is_refused(user, workspace):
    job = _job(user, workspace, SUITE)
    with patch(
        "simulate.services.hosted_harness_gateway.push_scenarios_into_live_sandbox",
        return_value=False,
    ), patch(
        "simulate.services.hosted_harness_gateway.rewrite_authoring_scenarios",
        return_value=None,
    ):
        response = _post(user, job, [
            {"op": "set_field", "scenario": "one", "field": "instruction", "value": "x"}
        ])
    receipt = response.json()["receipts"][0]
    assert receipt["outcome"] == "refused"
    assert "not editable" in receipt["why"]


def test_declining_a_reproof_keeps_the_run_unchanged(user, workspace):
    job = _job(user, workspace, SUITE)
    with patch(
        "simulate.services.hosted_harness_gateway.push_scenarios_into_live_sandbox",
        return_value=False,
    ), patch(
        "simulate.services.hosted_harness_gateway.rewrite_authoring_scenarios",
        return_value=None,
    ):
        response = _post(
            user, job, [{"op": "drop", "scenario": "two"}], rework=False
        )
    assert response.json()["receipts"][0]["outcome"] == "refused"
    output = HostedHarnessStageOutput.no_workspace_objects.get(job=job, kind="scenarios")
    assert len(output.data) == 2


def test_an_edit_reaching_a_live_guest_is_queued_not_applied(user, workspace):
    job = _job(user, workspace, SUITE)
    with patch(
        "simulate.services.hosted_harness_gateway.push_scenarios_into_live_sandbox",
        return_value=True,
    ) as pushed, patch(
        "simulate.services.hosted_harness_gateway.rewrite_authoring_scenarios",
        return_value=None,
    ) as sealed:
        response = _post(user, job, [
            {"op": "set_field", "scenario": "one", "field": "tests", "value": "reworded"}
        ])
    assert response.json()["receipts"][0]["outcome"] == "queued"
    pushed.assert_called_once()
    sealed.assert_called_once()


def test_the_suite_is_read_a_page_at_a_time(user, workspace):
    job = _job(user, workspace, SUITE)
    response = _client(user, job).get(
        f"/simulate/api/harness-jobs/{job.id}/scenarios/?limit=1&page=1"
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert set(body) >= {
        "count",
        "next",
        "previous",
        "results",
        "total_pages",
        "current_page",
    }
    assert body["current_page"] == 1


class TestScenarioNumbersSurviveADeletion:
    """A scenario keeps its number across deletions."""

    def _numbers(self, job):
        from simulate.models import HostedHarnessScenario

        return {
            row.scenario_key: row.number
            for row in HostedHarnessScenario.no_workspace_objects.filter(job=job)
        }

    def test_survivors_keep_their_number(self, user, workspace):
        from simulate.services.harness_scenarios import index_scenarios

        suite = [{"name": name, "tests": "t"} for name in ("one", "two", "three", "four")]
        job = _job(user, workspace, suite)
        index_scenarios(job, suite)
        assert self._numbers(job) == {"one": 1, "two": 2, "three": 3, "four": 4}

        index_scenarios(job, [one for one in suite if one["name"] != "two"], prune=True)
        assert self._numbers(job) == {"one": 1, "three": 3, "four": 4}

    def test_a_dropped_scenario_leaves_the_table(self, user, workspace):
        from simulate.services.harness_scenarios import index_scenarios

        suite = [{"name": name, "tests": "t"} for name in ("one", "two")]
        job = _job(user, workspace, suite)
        index_scenarios(job, suite)
        index_scenarios(job, [suite[0]], prune=True)
        assert set(self._numbers(job)) == {"one"}

    def test_a_new_scenario_takes_the_next_free_number(self, user, workspace):
        from simulate.services.harness_scenarios import index_scenarios

        suite = [{"name": name, "tests": "t"} for name in ("one", "two", "three")]
        job = _job(user, workspace, suite)
        index_scenarios(job, suite)
        index_scenarios(job, [suite[0], suite[2], {"name": "four", "tests": "t"}], prune=True)
        assert self._numbers(job) == {"one": 1, "three": 3, "four": 4}

    def test_a_new_scenario_under_a_renamed_ones_old_name_gets_its_own_row(
        self, user, workspace
    ):
        from simulate.models import HostedHarnessScenario
        from simulate.services.harness_scenarios import index_scenarios

        suite = [
            {"name": name, "scenario_key": name, "tests": "t"}
            for name in ("one", "two")
        ]
        job = _job(user, workspace, suite)
        index_scenarios(job, suite)
        renamed = {"name": "second", "scenario_key": "two", "tests": "t"}
        reused = {"name": "two", "scenario_key": "two-2", "tests": "t"}
        index_scenarios(job, [suite[0], renamed, reused], prune=True)

        rows = {
            row.scenario_key: (row.number, row.name)
            for row in HostedHarnessScenario.no_workspace_objects.filter(job=job)
        }
        assert rows == {"one": (1, "one"), "two": (2, "second"), "two-2": (3, "two")}

        index_scenarios(job, [suite[0], renamed, reused], prune=True)
        again = {
            row.scenario_key: (row.number, row.name)
            for row in HostedHarnessScenario.no_workspace_objects.filter(job=job)
        }
        assert again == rows

    def test_an_older_suite_that_repeats_a_name_still_saves_onto_its_row(
        self, user, workspace
    ):
        from simulate.services.harness_scenarios import index_scenarios

        suite = [{"name": name, "tests": "t"} for name in ("one", "two")]
        job = _job(user, workspace, suite)
        index_scenarios(job, suite)
        index_scenarios(job, [suite[0], {"name": "one", "tests": "u"}], prune=True)
        assert self._numbers(job) == {"one": 1}

    def test_two_scenarios_with_one_key_each_keep_a_row(self, user, workspace):
        from simulate.models import HostedHarnessScenario
        from simulate.services.harness_scenarios import index_scenarios

        suite = [
            {"name": "ödeme sorusu", "scenario_key": "k", "tests": "t"},
            {"name": "ödeme iadesi", "scenario_key": "k", "tests": "t"},
        ]
        job = _job(user, workspace, suite)
        assert index_scenarios(job, suite) == 2

        def rows():
            return {
                row.scenario_key: (row.number, row.name)
                for row in HostedHarnessScenario.no_workspace_objects.filter(job=job)
            }

        assert rows() == {"k": (1, "ödeme sorusu"), "k-2": (2, "ödeme iadesi")}
        index_scenarios(job, suite, prune=True)
        assert rows() == {"k": (1, "ödeme sorusu"), "k-2": (2, "ödeme iadesi")}

    def test_a_repeated_key_never_takes_the_row_of_the_scenario_holding_it(
        self, user, workspace
    ):
        from simulate.models import HostedHarnessScenario
        from simulate.services.harness_scenarios import index_scenarios

        suite = [{"name": "one", "scenario_key": "one", "tests": "t"}]
        job = _job(user, workspace, suite)
        index_scenarios(job, suite)
        repeated = {"name": "other", "scenario_key": "one", "tests": "t"}
        index_scenarios(job, [suite[0], repeated], prune=True)

        rows = {
            row.scenario_key: (row.number, row.name)
            for row in HostedHarnessScenario.no_workspace_objects.filter(job=job)
        }
        assert rows == {"one": (1, "one"), "one-2": (2, "other")}

    def test_a_scenario_listed_twice_writes_one_row_every_time(self, user, workspace):
        from simulate.services.harness_scenarios import index_scenarios

        doc = {"name": "one", "scenario_key": "k", "tests": "t"}
        job = _job(user, workspace, [doc, dict(doc)])
        for _ in range(2):
            index_scenarios(job, [doc, dict(doc)], prune=True)
        assert self._numbers(job) == {"k": 1}

    def test_a_new_suite_that_repeats_a_name_without_keys_saves_one_row(
        self, user, workspace
    ):
        from simulate.services.harness_scenarios import index_scenarios

        suite = [{"name": "one", "tests": "t"}, {"name": "one", "tests": "u"}]
        job = _job(user, workspace, suite)
        index_scenarios(job, suite)
        assert self._numbers(job) == {"one": 1}

    def test_adding_a_dropped_scenarios_key_again_brings_its_row_back(
        self, user, workspace
    ):
        from simulate.models import HostedHarnessScenario
        from simulate.services.harness_scenarios import index_scenarios

        suite = [
            {"name": name, "scenario_key": name, "tests": "t"}
            for name in ("one", "two")
        ]
        job = _job(user, workspace, suite)
        index_scenarios(job, suite)
        index_scenarios(job, [suite[0]], prune=True)
        index_scenarios(
            job,
            [suite[0], {"name": "two-new", "scenario_key": "two", "tests": "t"}],
            prune=True,
        )

        row = HostedHarnessScenario.all_objects.get(job=job, scenario_key="two")
        assert (row.number, row.name, row.deleted) == (2, "two-new", False)

    def test_a_new_scenario_named_like_a_dropped_one_gets_its_own_row(
        self, user, workspace
    ):
        from simulate.models import HostedHarnessScenario
        from simulate.services.harness_scenarios import index_scenarios

        suite = [
            {"name": name, "scenario_key": name, "tests": "t"}
            for name in ("one", "two")
        ]
        job = _job(user, workspace, suite)
        index_scenarios(job, suite)
        index_scenarios(job, [suite[0]], prune=True)
        # The harness never gives a used key to a new scenario, so it arrives with its own.
        index_scenarios(
            job,
            [suite[0], {"name": "two", "scenario_key": "two-2", "tests": "other"}],
            prune=True,
        )
        rows = {
            row.scenario_key: (row.number, row.name, row.deleted)
            for row in HostedHarnessScenario.all_objects.filter(job=job)
        }
        assert rows == {
            "one": (1, "one", False),
            "two": (2, "two", True),
            "two-2": (3, "two", False),
        }

    def test_keys_that_differ_only_in_case_or_separator_keep_their_rows(
        self, user, workspace
    ):
        from simulate.services.harness_scenarios import index_scenarios

        suite = [
            {"name": "A", "scenario_key": "a_b", "tests": "t"},
            {"name": "B", "scenario_key": "a-b", "tests": "t"},
            {"name": "C", "scenario_key": "Key", "tests": "t"},
            {"name": "D", "scenario_key": "key", "tests": "t"},
        ]
        job = _job(user, workspace, suite)
        index_scenarios(job, suite)
        first = self._rows(job)
        for _ in range(2):
            index_scenarios(job, suite, prune=True)
        assert self._rows(job) == first
        assert first == {
            "a_b": (1, "A"),
            "a-b": (2, "B"),
            "Key": (3, "C"),
            "key": (4, "D"),
        }

    def test_repeated_keys_keep_each_scenario_on_its_row_in_any_order(
        self, user, workspace
    ):
        from simulate.services.harness_scenarios import index_scenarios

        suite = [
            {"name": "A", "scenario_key": "k", "tests": "t"},
            {"name": "B", "scenario_key": "k", "tests": "t"},
            {"name": "C", "scenario_key": "k-2", "tests": "t"},
        ]
        job = _job(user, workspace, suite)
        index_scenarios(job, suite)
        rows = self._rows(job)
        assert rows == {"k": (1, "A"), "k-3": (2, "B"), "k-2": (3, "C")}
        index_scenarios(job, list(reversed(suite)), prune=True)
        assert self._rows(job) == rows
        # The second holder renamed under its old key keeps its row.
        index_scenarios(
            job, [suite[0], {**suite[1], "name": "B2"}, suite[2]], prune=True
        )
        assert self._rows(job) == {"k": (1, "A"), "k-3": (2, "B2"), "k-2": (3, "C")}

    def test_a_scenario_without_a_key_never_writes_over_a_keyed_one(
        self, user, workspace
    ):
        from simulate.services.harness_scenarios import index_scenarios

        suite = [{"name": "first", "scenario_key": "k", "tests": "t"}]
        job = _job(user, workspace, suite)
        index_scenarios(job, suite)
        index_scenarios(job, [suite[0], {"name": "k", "tests": "t"}], prune=True)
        assert self._rows(job) == {"k": (1, "first"), "k-2": (2, "k")}

    @staticmethod
    def _rows(job):
        from simulate.models import HostedHarnessScenario

        return {
            row.scenario_key: (row.number, row.name)
            for row in HostedHarnessScenario.no_workspace_objects.filter(job=job)
        }

    def test_a_number_means_the_row_the_table_shows(self, user, workspace):
        from simulate.services.harness_provider import scenarios_meant
        from simulate.services.harness_scenarios import index_scenarios

        suite = [{"name": name, "tests": "t"} for name in ("one", "two", "three", "four")]
        job = _job(user, workspace, suite)
        index_scenarios(job, suite)
        left = [one for one in suite if one["name"] != "two"]
        index_scenarios(job, left, prune=True)

        numbering = self._numbers(job)
        by_number = {number: key for key, number in numbering.items()}
        assert scenarios_meant("4", left, by_number) == ["four"]
        assert scenarios_meant("3-4", left, by_number) == ["three", "four"]


def test_a_row_key_finds_an_older_suite_that_has_only_names():
    from simulate.services.harness_provider import scenarios_meant

    suite = [{"name": "rachel_surge_comfort_booking"}, {"name": "dana_book_cab"}]
    assert scenarios_meant("rachel-surge-comfort-booking", suite) == [
        "rachel_surge_comfort_booking"
    ]
    assert scenarios_meant(
        ["dana-book-cab", "Rachel_Surge_Comfort_Booking"], suite
    ) == ["dana_book_cab", "rachel_surge_comfort_booking"]
    assert scenarios_meant("unknown-scenario", suite) == []


def test_a_short_suite_on_a_poll_never_deletes_a_row(user, workspace):
    from simulate.models import HostedHarnessScenario
    from simulate.services.harness_scenarios import index_scenarios

    suite = [{"name": name, "tests": "t"} for name in ("one", "two", "three")]
    job = _job(user, workspace, suite)
    index_scenarios(job, suite)
    index_scenarios(job, suite[:1])
    assert HostedHarnessScenario.no_workspace_objects.filter(job=job).count() == 3


def test_a_scenario_with_no_attack_reads_as_no_attack_on_every_attack_axis():
    from simulate.services.harness_scenarios import level_label

    assert level_label("none") == "No attack"
    assert level_label("absent") == "No attack"
    assert level_label("prompt_injection") == "Injected instruction"


def _gate_refusing(name):
    """The harness gates, failing `name` as a scenario written under older rules would."""
    import importlib.util
    import sys
    import types

    gate = types.ModuleType("fi.alk.harness.scenario")
    gate.Scenario = types.SimpleNamespace(model_validate=lambda one: one)
    gate.scenario_edit_problems = lambda one: (
        ["breaks a newer rule"] if one.get("name") == name else []
    )
    modules = {"fi.alk.harness.scenario": gate}
    for parent in ("fi", "fi.alk", "fi.alk.harness"):
        try:
            present = (
                parent in sys.modules or importlib.util.find_spec(parent) is not None
            )
        except ModuleNotFoundError:
            present = False
        if not present:
            modules[parent] = types.ModuleType(parent)
    return patch.dict(sys.modules, modules)


def _amend_with_gate(user, job, refusing, changes):
    with _gate_refusing(refusing), patch(
        "simulate.services.hosted_harness_gateway.push_scenarios_into_live_sandbox",
        return_value=False,
    ), patch(
        "simulate.services.hosted_harness_gateway.rewrite_authoring_scenarios",
        return_value=None,
    ):
        return _post(user, job, changes)


@pytest.mark.parametrize(
    "change",
    [
        {"op": "drop", "scenario": "one"},
        {"op": "set_field", "scenario": "one", "field": "tests", "value": "reworded"},
    ],
)
def test_an_older_neighbour_does_not_block_a_drop_or_an_edit(user, workspace, change):
    job = _job(user, workspace, SUITE)
    response = _amend_with_gate(user, job, "two", [change])
    assert response.status_code == 200, response.content
    assert [one["outcome"] for one in response.json()["receipts"]] == ["applied"]


def test_an_edit_that_breaks_the_gates_is_still_refused(user, workspace):
    job = _job(user, workspace, SUITE)
    response = _amend_with_gate(
        user,
        job,
        "one",
        [{"op": "set_field", "scenario": "one", "field": "tests", "value": "reworded"}],
    )
    assert response.json()["receipts"][0]["outcome"] == "refused"
    output = HostedHarnessStageOutput.no_workspace_objects.get(job=job, kind="scenarios")
    assert output.data[0]["tests"] == "the agent holds the line"


def test_an_archive_that_cannot_take_the_edit_leaves_everything_unchanged(
    user, workspace
):
    from simulate.services.hosted_harness_gateway import AuthoringArchiveKept

    job = _job(user, workspace, SUITE)
    with patch(
        "simulate.services.hosted_harness_gateway.push_scenarios_into_live_sandbox",
        return_value=False,
    ) as pushed, patch(
        "simulate.services.hosted_harness_gateway.rewrite_authoring_scenarios",
        side_effect=AuthoringArchiveKept("the change would lose files a run needs"),
    ):
        response = _post(user, job, [{"op": "drop", "scenario": "two"}])
    receipt = response.json()["receipts"][0]
    assert receipt["outcome"] == "refused"
    assert "nothing changed" in receipt["why"]
    output = HostedHarnessStageOutput.no_workspace_objects.get(job=job, kind="scenarios")
    assert [one["name"] for one in output.data] == ["one", "two"]
    pushed.assert_not_called()


def test_the_harness_is_sent_every_key_the_environment_has_used(user, workspace):
    from simulate.services.harness_scenarios import index_scenarios
    from simulate.services.hosted_harness_gateway import _with_used_scenario_keys

    suite = [
        {"name": name, "scenario_key": name, "tests": "t"} for name in ("one", "two")
    ]
    job = _job(user, workspace, suite)
    index_scenarios(job, suite)
    index_scenarios(job, [suite[0]], prune=True)
    document = _with_used_scenario_keys({"metadata": {"name": "x"}}, job)
    assert document["metadata"] == {"name": "x", "used_scenario_keys": ["one", "two"]}
