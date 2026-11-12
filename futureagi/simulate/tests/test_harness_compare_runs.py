"""The N-run compare endpoint: contract, semantics, parity with the calls page."""

from __future__ import annotations

import gc
import json
import uuid
from collections import Counter
from collections.abc import Collection
from datetime import timedelta
from typing import Any

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.choices import DatasetSourceChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import (
    CallExecution,
    HostedHarnessExecution,
    HostedHarnessJob,
    HostedHarnessScenario,
    Scenarios,
    SimulateEvalConfig,
    TestExecution,
)
from simulate.models.run_test import RunTest

BASE = "/simulate/api/harness-environments"
CALLS = "/simulate/v3/test-executions/{}/calls/"
PARAM_SETS: list[dict[str, Any]] = [
    {},
    {"filters": {"status": ["failed"]}},
    {"filters": {"status_not": ["passed"]}},
    {"filters": {"goal": ["Refund a damaged order", "Cancel a subscription"]}},
    {"filters": {"goal_not": ["Refund a damaged order"]}},
    {"filters": {"sub_goal": ["Logged the case"]}},
    {"filters": {"sub_goal_not": ["Verified identity"]}},
    {"search": "refund"},
    {"search": "PIPELINE"},
    {"group_by": "goal"},
    {"group_by": "sub_goal"},
    {"group_by": "status"},
    {"group_by": "attack"},
    {"group_by": "task"},
    {"group_by": "accent"},
    {"group_by": "age"},
    {"group_by": "sub_goal", "filters": {"status": ["failed"]}, "search": "a"},
    # The Scenarios tab's filters.
    {"filters": {"scenario": ["Scenario 1"]}},
    {"filters": {"situation": ["case 2"]}},
    {"filters": {"persona": ["Elena Vargas"]}},
    {"filters": {"persona_not": ["Elena Vargas"]}},
    {"filters": {"age": ["36"]}},
    {"filters": {"attack": ["Prompt injection"]}},
    {"filters": {"attack_not": ["None"]}},
    {"filters": {"task": ["Lookup"]}},
    {"filters": {"task_not": ["Escalation"]}},
    {"group_by": "task", "filters": {"situation": ["CASE"], "age_not": ["29"]}},
]


USE_CASES = (
    "Refund a damaged order",
    "Change a delivery address",
    "Cancel a subscription",
    "Dispute a late fee",
)
SUB_GOALS = (
    "Verified identity",
    "Stated the policy",
    "Offered an alternative",
    "Confirmed next steps",
    "Kept the caller calm",
    "Logged the case",
)
PERSONAS = (
    ("Elena Vargas", "US", "female", "36"),
    ("Arjun Mehta", "Indian", "male", "29"),
    ("Kwame Mensah", "Ghanaian", "male", "52"),
)
ENDED = ("customer-ended-call", "assistant-ended-call", "silence-timed-out")
SCORES = {"passed": 0.9, "failed": 0.1, "inconclusive": 0.5, "error": 0.0}


def _outcome(number: int, run: int, trial: int) -> str:
    """A deterministic outcome that drifts from run to run."""
    roll = (number * 7 + run * 3 + trial) % 10
    if roll == 0:
        return "error"
    return "failed" if roll < 4 else "inconclusive" if roll == 4 else "passed"


def _job(workspace: Workspace, name: str, scenarios: int, **fields: Any):
    return HostedHarnessJob.no_workspace_objects.create(
        organization=workspace.organization,
        workspace=workspace,
        run_id=uuid.uuid4(),
        name=name,
        idempotency_key=name,
        request_digest="sha256:" + "0" * 64,
        schema_version="test",
        payload={"source": {"kind": "test"}, "metadata": {"name": name}},
        state=HostedHarnessJob.State.COMPLETED,
        current_stage="completed",
        seed=1,
        scenario_count=scenarios,
        artifact_level="none",
        max_artifact_bytes=0,
        deadline_at=timezone.now() + timedelta(days=1),
        **fields,
    )


def _call(
    execution: TestExecution,
    dataset_scenario: Scenarios,
    scenario: HostedHarnessScenario,
    config_id: str,
    number: int,
    run: int,
    trial: int,
    row_data: dict[str, Any],
    started: Any,
) -> CallExecution:
    outcome = _outcome(number, run, trial)
    errored = outcome == "error"
    held = [
        {"name": name, "held": outcome != "failed" if index == 0 else True}
        for index, name in enumerate(scenario.sub_goals)
    ]
    return CallExecution(
        test_execution=execution,
        scenario=dataset_scenario,
        status="failed" if errored else "completed",
        simulation_call_type="voice",
        started_at=started,
        completed_at=started + timedelta(seconds=60),
        duration_seconds=60,
        ended_reason="pipeline-error" if errored else ENDED[number % len(ENDED)],
        error_message="The agent did not answer." if errored else "",
        call_metadata={
            "harness_scenario_key": scenario.scenario_key,
            "harness_trial_index": trial,
            "harness_outcome_status": outcome,
            "row_data": row_data,
            "hosted_harness_receipt": {
                "scenario_key": scenario.scenario_key,
                "status": outcome,
                "sub_goals": held,
            },
        },
        eval_outputs={
            config_id: {
                "name": "Helpfulness",
                "status": "completed",
                "output": SCORES[outcome],
                "output_type": "Percentage",
            },
            "harness-policy": {
                "name": "Policy",
                "output": "Passed" if outcome == "passed" else "Failed",
                "source": "harness",
            },
        },
    )


def build_environment(
    workspace: Workspace,
    *,
    runs: int = 3,
    scenarios: int = 12,
    trials: int = 2,
    prefix: str = "t-cmp",
    edited: Collection[int] = (5,),
    skipped: Collection[tuple[int, int]] = (),
) -> HostedHarnessJob:
    """An environment with ``runs`` finished runs of ``scenarios`` x ``trials``.

    Scenarios in ``edited`` change their situation from the run halfway on;
    ``skipped`` holds the (run, scenario) pairs a run leaves out.
    """
    organization = workspace.organization
    run_test = RunTest.no_workspace_objects.create(
        name=f"{prefix} environment", organization=organization, workspace=workspace
    )
    environment = _job(workspace, f"{prefix} environment", scenarios, run_test=run_test)
    dataset_scenario = Scenarios.objects.create(
        name=f"{prefix} scenario",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.SCRIPT,
        organization=organization,
        workspace=workspace,
    )
    template = EvalTemplate.objects.create(
        name=f"{prefix} helpfulness",
        organization=organization,
        output_type_normalized="percentage",
        pass_threshold=0.5,
    )
    config = SimulateEvalConfig.objects.create(
        name="Helpfulness", run_test=run_test, eval_template=template
    )
    authored = HostedHarnessScenario.no_workspace_objects.bulk_create(
        HostedHarnessScenario(
            job=environment,
            scenario_key=f"{prefix}-{number:04d}",
            number=number + 1,
            name=f"Scenario {number + 1}",
            instruction=f"Call about case {number + 1}.",
            use_case=USE_CASES[number % len(USE_CASES)],
            persona={
                "name": PERSONAS[number % len(PERSONAS)][0],
                "accent": PERSONAS[number % len(PERSONAS)][1],
                "age_group": PERSONAS[number % len(PERSONAS)][3],
            },
            coverage={
                "overlay": ("None", "Prompt injection")[number % 2],
                "task": ("Lookup", "Change", "Escalation")[number % 3],
            },
            sub_goals=[SUB_GOALS[(number + step) % len(SUB_GOALS)] for step in (0, 1)]
            + ([SUB_GOALS[(number + 2) % len(SUB_GOALS)]] if number % 2 else []),
        )
        for number in range(scenarios)
    )
    now = timezone.now()
    for run in range(runs):
        created = now - timedelta(hours=runs - run)
        execution = TestExecution.no_workspace_objects.create(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.COMPLETED,
            started_at=created,
            completed_at=created + timedelta(minutes=30),
            trials=trials,
            total_scenarios=scenarios,
            total_calls=scenarios * trials,
        )
        TestExecution.no_workspace_objects.filter(id=execution.id).update(
            created_at=created
        )
        job = _job(
            workspace,
            f"{prefix} run {run + 1}",
            scenarios,
            environment=environment,
            run_test=run_test,
            test_execution=execution,
        )
        calls, allocations = [], []
        for number, scenario in enumerate(authored):
            if (run, number) in skipped:
                continue
            was_edited = number in edited and run >= runs // 2
            situation = f"The caller's case {number + 1} " + (
                "was escalated twice." if was_edited else "has been open a week."
            )
            name, accent, gender, age = PERSONAS[number % len(PERSONAS)]
            row_data = {
                "persona": {
                    "name": name,
                    "accent": accent,
                    "gender": gender,
                    "age": age,
                },
                "situation": situation,
                "outcome": f"The {scenario.use_case.lower()} is resolved.",
            }
            for trial in range(1, trials + 1):  # Hosted runs count trials from 1.
                calls.append(
                    _call(
                        execution,
                        dataset_scenario,
                        scenario,
                        str(config.id),
                        number,
                        run,
                        trial,
                        row_data,
                        created + timedelta(seconds=len(calls)),
                    )
                )
                allocations.append(
                    HostedHarnessExecution(
                        job=job,
                        source_scenario=scenario,
                        execution_key=f"{scenario.scenario_key}:{trial}",
                        trial_index=trial,
                        call_execution=calls[-1],
                    )
                )
        CallExecution.no_workspace_objects.bulk_create(calls, batch_size=1000)
        HostedHarnessExecution.no_workspace_objects.bulk_create(
            allocations, batch_size=1000
        )
    return environment


@pytest.fixture(autouse=True)
def _empty_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def environment(workspace):
    return build_environment(workspace)


def _runs(environment) -> list[TestExecution]:
    return list(
        TestExecution.objects.filter(run_test_id=environment.run_test_id).order_by(
            "created_at"
        )
    )


def _encoded(params: dict[str, Any]) -> dict[str, Any]:
    return {
        key: json.dumps(value) if key == "filters" else value
        for key, value in params.items()
    }


def _compare(client, environment, runs, **params: Any):
    return client.get(
        f"{BASE}/{environment.id}/compare-runs/",
        {"runs": ",".join(str(run.id) for run in runs), **_encoded(params)},
    )


def _calls(client, run, **params: Any) -> dict[str, Any]:
    response = client.get(CALLS.format(run.id), {"page_size": 500, **_encoded(params)})
    assert response.status_code == 200
    return response.json()


def _ok(response) -> dict[str, Any]:
    assert response.status_code == 200, response.content[:1000]
    return response.json()


def _assert_declared(body: dict[str, Any]) -> None:
    """The body is what the endpoint's serializer, and so its swagger, declares."""
    from simulate.serializers.compare_runs import HarnessCompareRunsSerializer

    serializer = HarnessCompareRunsSerializer(data=body)
    assert serializer.is_valid(), serializer.errors


def _key_calls(run: TestExecution, key: str) -> list[CallExecution]:
    return list(
        CallExecution.objects.filter(
            test_execution=run, call_metadata__harness_scenario_key=key
        ).order_by("call_metadata__harness_trial_index")
    )


def _grade(calls: list[CallExecution], outcome: str) -> None:
    """Grade ``calls`` ``outcome``, with every eval agreeing."""
    for call in calls:
        call.status = "completed"
        call.call_metadata = {
            **call.call_metadata,
            "harness_outcome_status": outcome,
            "hosted_harness_receipt": {
                **call.call_metadata["hosted_harness_receipt"],
                "status": outcome,
            },
        }
        call.eval_outputs = {
            eval_id: {
                **output,
                "output": (
                    ("Passed" if outcome == "passed" else "Failed")
                    if output.get("source") == "harness"
                    else (0.9 if outcome == "passed" else 0.1)
                ),
            }
            for eval_id, output in call.eval_outputs.items()
        }
        call.save(update_fields=["status", "call_metadata", "eval_outputs"])
    cache.clear()


def _shared_keys(runs: list[TestExecution], trials: int = 2) -> list[str]:
    """The scenarios every run played, unedited."""
    keys = []
    for number in range(12):
        key = f"t-cmp-{number:04d}"
        calls = [_key_calls(run, key) for run in runs]
        if all(len(one) == trials for one in calls) and (
            len(
                {c.call_metadata["row_data"]["situation"] for one in calls for c in one}
            )
            == 1
        ):
            keys.append(key)
    return keys


def _shared_key(runs: list[TestExecution], trials: int = 2) -> str:
    """A scenario every run played, unedited."""
    keys = _shared_keys(runs, trials)
    assert keys, "No scenario every run played."
    return keys[0]


def _other_workspace(workspace: Workspace, user: User) -> Workspace:
    return Workspace.objects.create(
        name="Other",
        organization=workspace.organization,
        is_active=True,
        created_by=user,
    )


def _row(body: dict[str, Any], key: str) -> dict[str, Any]:
    [row] = [row for row in body["rows"] if row["scenario_key"] == key]
    return row


@pytest.mark.integration
@pytest.mark.django_db
class TestContract:
    def test_shape(self, auth_client, environment):
        runs = _runs(environment)

        body = _ok(_compare(auth_client, environment, runs))

        assert set(body) == {
            "baseline_execution_id",
            "runs",
            "evaluation_columns",
            "facets",
            "level_labels",
            "changed_count",
            "count",
            "page",
            "page_size",
            "total_pages",
            "groups",
            "rows",
        }
        assert body["baseline_execution_id"] == str(runs[0].id)
        assert [run["execution"]["id"] for run in body["runs"]] == [
            str(run.id) for run in runs
        ]
        assert [run["is_baseline"] for run in body["runs"]] == [True, False, False]
        assert body["page_size"] == 25
        row = body["rows"][0]
        assert set(row) == {
            "row_id",
            "scenario_key",
            "scenario",
            "use_case",
            "persona",
            "persona_details",
            "situation",
            "ideal_outcome",
            "calls",
            "movement",
        }
        assert set(row["calls"]) == {str(run.id) for run in runs}
        for histograms in (run["histograms"] for run in body["runs"]):
            assert histograms and all(len(bands) == 10 for bands in histograms.values())
        _assert_declared(body)
        # The check bites: a value the serializer does not declare fails it.
        from simulate.serializers.compare_runs import HarnessCompareRunsSerializer

        row["movement"] = "unchanged"
        assert not HarnessCompareRunsSerializer(data=body).is_valid()

    @pytest.mark.parametrize(
        "params,field",
        [
            ({"page_size": 0}, "page_size"),
            ({"page_size": 101}, "page_size"),
            ({"page": 0}, "page"),
            ({"ordering": "-started_at"}, "ordering"),
            ({"group_by": "persona"}, "group_by"),
            ({"filters": {"goal_outcome": ["passed"]}}, "filters"),
            ({"filters": {"call_execution_id": [str(uuid.uuid4())]}}, "filters"),
            ({"filters": {"status": ["green"]}}, "filters"),
            ({"unknown": "x"}, "unknown"),
        ],
    )
    def test_invalid_query_is_rejected(self, auth_client, environment, params, field):
        response = _compare(auth_client, environment, _runs(environment), **params)

        assert response.status_code == 400
        assert field in json.dumps(response.json())

    @pytest.mark.parametrize("count", [1, 9])
    def test_two_to_eight_runs(self, auth_client, environment, count):
        runs = (_runs(environment) * 3)[:count]
        response = auth_client.get(
            f"{BASE}/{environment.id}/compare-runs/",
            {"runs": ",".join(str(uuid.uuid4()) for _ in runs)},
        )

        assert response.status_code == 400
        assert "runs" in json.dumps(response.json())

    def test_a_run_is_picked_once(self, auth_client, environment):
        first = _runs(environment)[0]

        response = _compare(auth_client, environment, [first, first])

        assert response.status_code == 400

    def test_an_unknown_run_is_not_found(self, auth_client, environment):
        runs = _runs(environment)
        response = auth_client.get(
            f"{BASE}/{environment.id}/compare-runs/",
            {"runs": f"{runs[0].id},{uuid.uuid4()}"},
        )

        assert response.status_code == 404

    def test_a_run_of_another_environment_is_not_found(
        self, auth_client, environment, workspace
    ):
        other = build_environment(
            workspace, runs=2, scenarios=1, trials=1, prefix="t-other", edited=()
        )

        response = _compare(
            auth_client, environment, [_runs(environment)[0], _runs(other)[0]]
        )

        assert response.status_code == 404

    def test_an_unknown_environment_is_not_found(self, auth_client, environment):
        response = auth_client.get(
            f"{BASE}/{uuid.uuid4()}/compare-runs/",
            {"runs": ",".join(str(run.id) for run in _runs(environment)[:2])},
        )

        assert response.status_code == 404

    def test_another_workspaces_environment_is_not_found(
        self, auth_client, environment, workspace, user
    ):
        other = _other_workspace(workspace, user)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            workspace=other
        )

        response = _compare(auth_client, environment, _runs(environment)[:2])

        assert response.status_code == 404

    def test_a_run_of_another_workspace_is_not_found(
        self, auth_client, environment, workspace, user
    ):
        foreign = build_environment(
            _other_workspace(workspace, user),
            runs=2,
            scenarios=1,
            trials=1,
            prefix="t-foreign",
            edited=(),
        )

        response = _compare(
            auth_client, environment, [_runs(environment)[0], _runs(foreign)[0]]
        )

        assert response.status_code == 404

    @pytest.mark.parametrize(
        "status,code",
        [
            (TestExecution.ExecutionStatus.RUNNING, "comparison_run_unfinished"),
            (TestExecution.ExecutionStatus.FAILED, "comparison_run_failed"),
            (TestExecution.ExecutionStatus.CANCELLED, "comparison_run_cancelled"),
        ],
    )
    def test_only_completed_runs_compare(self, auth_client, environment, status, code):
        runs = _runs(environment)
        TestExecution.objects.filter(id=runs[1].id).update(status=status)

        response = _compare(auth_client, environment, runs)

        assert response.status_code == 400
        assert response.json()["error"] == code
        assert response.json()["execution_id"] == str(runs[1].id)


@pytest.mark.integration
@pytest.mark.django_db
class TestSemantics:
    def test_an_edited_scenario_is_two_rows(self, auth_client, environment):
        runs = _runs(environment)

        body = _ok(_compare(auth_client, environment, runs, page_size=100))

        keys = Counter(row["scenario_key"] for row in body["rows"])
        [(edited, versions)] = [(key, n) for key, n in keys.items() if n > 1]
        assert versions == 2
        first, second = (row for row in body["rows"] if row["scenario_key"] == edited)
        assert first["situation"] != second["situation"]
        assert first["row_id"] != second["row_id"]
        for row in (first, second):
            ran = [run for run, calls in row["calls"].items() if calls]
            assert 0 < len(ran) < len(runs)
            for calls in row["calls"].values():
                assert {call["scenario_details"] for call in calls} <= {
                    row["situation"]
                }

    def test_a_scenario_left_out_of_a_run_is_not_run(self, auth_client, environment):
        runs = _runs(environment)
        key = "t-cmp-0003"
        CallExecution.objects.filter(
            test_execution=runs[2], call_metadata__harness_scenario_key=key
        ).delete()

        body = _ok(_compare(auth_client, environment, runs, page_size=100))

        [row] = [row for row in body["rows"] if row["scenario_key"] == key]
        assert row["calls"][str(runs[2].id)] == []
        assert len(row["calls"][str(runs[0].id)]) == 2

    def test_trials_are_lines_in_trial_order(self, auth_client, environment):
        body = _ok(_compare(auth_client, environment, _runs(environment)))

        for row in body["rows"]:
            for calls in row["calls"].values():
                assert [call["trial_index"] for call in calls] in ([], [1, 2])

    def test_a_row_is_kept_when_any_call_matches_and_shows_every_call(
        self, auth_client, environment
    ):
        runs = _runs(environment)
        everything = _ok(_compare(auth_client, environment, runs, page_size=100))
        shared = next(row for row in everything["rows"] if all(row["calls"].values()))
        needle = shared["calls"][str(runs[1].id)][0]["id"]
        CallExecution.objects.filter(id=needle).update(ended_reason="needle-hangup")
        cache.clear()

        body = _ok(_compare(auth_client, environment, runs, search="NEEDLE"))

        [row] = body["rows"]
        assert body["count"] == 1 < everything["count"]
        [before] = [one for one in everything["rows"] if one["row_id"] == row["row_id"]]
        assert {
            run: [c["id"] for c in calls] for run, calls in row["calls"].items()
        } == {run: [c["id"] for c in calls] for run, calls in before["calls"].items()}
        assert all(row["calls"].values())
        matched = [run["summary"]["total"] for run in body["runs"]]
        assert matched == [0, 1, 0]

    def test_a_row_joins_only_the_groups_of_its_matching_calls(
        self, auth_client, environment
    ):
        runs = _runs(environment)
        key = _shared_key(runs)
        for run in runs:
            _grade(_key_calls(run, key), "passed")
        needle = _key_calls(runs[1], key)[0]
        _grade([needle], "failed")
        CallExecution.objects.filter(id=needle.id).update(ended_reason="needle-hangup")
        cache.clear()

        body = _ok(
            _compare(auth_client, environment, runs, search="NEEDLE", group_by="status")
        )

        [row] = body["rows"]
        assert any(len(calls) > 1 for calls in row["calls"].values())
        assert [(g["key"], g["row_ids"], g["row_count"]) for g in body["groups"]] == [
            ("failed", [row["row_id"]], 1)
        ]

    def test_a_row_joins_every_group_of_its_calls(self, auth_client, environment):
        body = _ok(
            _compare(
                auth_client,
                environment,
                _runs(environment),
                page_size=100,
                group_by="sub_goal",
            )
        )

        joined = Counter(
            row_id for group in body["groups"] for row_id in group["row_ids"]
        )
        assert max(joined.values()) > 1
        assert set(joined) == {row["row_id"] for row in body["rows"]}

    def test_movement_orders_the_rows(self, auth_client, environment):
        order = {"regressed": 0, "mixed": 1, "improved": 2, "same": 3}

        body = _ok(
            _compare(auth_client, environment, _runs(environment), page_size=100)
        )

        ranks = [order[row["movement"]] for row in body["rows"]]
        assert ranks == sorted(ranks)
        assert len(set(ranks)) > 1
        assert 0 < body["changed_count"] <= body["count"]

    def test_scenario_ordering(self, auth_client, environment):
        body = _ok(
            _compare(
                auth_client,
                environment,
                _runs(environment),
                page_size=100,
                ordering="scenario",
            )
        )

        keys = [(row["scenario"].lower(), row["scenario_key"]) for row in body["rows"]]
        assert keys == sorted(keys)

    def test_pages_count_scenario_rows(self, auth_client, environment):
        runs = _runs(environment)
        whole = _ok(_compare(auth_client, environment, runs, page_size=100))

        first = _ok(_compare(auth_client, environment, runs, page_size=5))
        third = _ok(_compare(auth_client, environment, runs, page_size=5, page=3))

        assert first["count"] == whole["count"]
        assert first["total_pages"] == -(-whole["count"] // 5)
        assert [row["row_id"] for row in first["rows"]] == [
            row["row_id"] for row in whole["rows"][:5]
        ]
        assert [row["row_id"] for row in third["rows"]] == [
            row["row_id"] for row in whole["rows"][10:15]
        ]

    def test_the_detail_page_and_compare_share_one_cache_entry(
        self, auth_client, environment
    ):
        runs = _runs(environment)
        for run in runs:
            _calls(auth_client, run)

        with CaptureQueriesContext(connection) as queries:
            _ok(_compare(auth_client, environment, runs))

        assert not any(
            "jsonb_path_query_first(eval_outputs" in query["sql"]
            for query in queries.captured_queries
        )

    def test_a_missing_trial_is_not_a_changed_outcome(self, auth_client, environment):
        runs = _runs(environment)
        key = _shared_key(runs)
        for run in runs:
            _grade(_key_calls(run, key), "passed")
        before = _ok(_compare(auth_client, environment, runs, page_size=100))
        _key_calls(runs[1], key)[1].delete()
        cache.clear()

        after = _ok(_compare(auth_client, environment, runs, page_size=100))

        assert _row(after, key)["movement"] == "same"
        assert len(_row(after, key)["calls"][str(runs[1].id)]) == 1
        assert after["changed_count"] == before["changed_count"]

    def test_a_flipped_outcome_is_a_regression(self, auth_client, environment):
        runs = _runs(environment)
        key = _shared_key(runs)
        for run in runs:
            _grade(_key_calls(run, key), "passed")
        before = _ok(_compare(auth_client, environment, runs, page_size=100))
        _grade(_key_calls(runs[1], key), "failed")

        after = _ok(_compare(auth_client, environment, runs, page_size=100))

        assert _row(before, key)["movement"] == "same"
        assert _row(after, key)["movement"] == "regressed"
        assert after["changed_count"] == before["changed_count"] + 1
        # Regressions lead the default order.
        assert after["rows"][0]["movement"] == "regressed"

    def test_a_trial_flipped_to_an_outcome_already_in_the_row_is_changed(
        self, auth_client, environment
    ):
        runs = _runs(environment)
        key = _shared_key(runs)
        for run in runs:
            first, second = _key_calls(run, key)
            _grade([first], "passed")
            _grade([second], "inconclusive")
        before = _ok(_compare(auth_client, environment, runs, page_size=100))
        first, second = _key_calls(runs[1], key)
        _grade([first], "inconclusive")
        _grade([second], "passed")

        after = _ok(_compare(auth_client, environment, runs, page_size=100))

        # The same rate and the same set of outcomes, but each trial flipped.
        assert _row(after, key)["movement"] == "same"
        assert after["changed_count"] == before["changed_count"] + 1

    def test_a_flipped_row_leads_the_unchanged_rows_of_its_movement(
        self, auth_client, environment
    ):
        runs = _runs(environment)
        keys = _shared_keys(runs)
        for key in keys:
            for run in runs:
                first, second = _key_calls(run, key)
                _grade([first], "passed")
                _grade([second], "inconclusive")
        before = _ok(_compare(auth_client, environment, runs, page_size=100))
        # The one scenario order alone would put last among them.
        flipped = max(
            keys,
            key=lambda key: (_row(before, key)["scenario"].lower(), key),
        )
        first, second = _key_calls(runs[1], flipped)
        _grade([first], "inconclusive")
        _grade([second], "passed")

        body = _ok(_compare(auth_client, environment, runs, page_size=100))

        order = [row["scenario_key"] for row in body["rows"]]
        assert len(keys) > 1
        assert _row(body, flipped)["movement"] == "same"
        assert all(
            order.index(flipped) < order.index(key) for key in set(keys) - {flipped}
        )

    def test_runs_moving_both_ways_are_mixed_between_regressions_and_improvements(
        self, auth_client, environment
    ):
        runs = _runs(environment)
        mixed, regressed, improved = _shared_keys(runs)[:3]
        for key, grades in (
            (mixed, (["passed", "failed"], ["failed", "failed"], ["passed"] * 2)),
            (regressed, (["passed"] * 2, ["failed"] * 2, ["passed"] * 2)),
            (improved, (["failed"] * 2, ["passed"] * 2, ["passed"] * 2)),
        ):
            for run, outcomes in zip(runs, grades, strict=True):
                for call, outcome in zip(_key_calls(run, key), outcomes, strict=True):
                    _grade([call], outcome)

        body = _ok(_compare(auth_client, environment, runs, page_size=100))

        order = [row["scenario_key"] for row in body["rows"]]
        assert _row(body, mixed)["movement"] == "mixed"
        assert order.index(regressed) < order.index(mixed) < order.index(improved)

    def test_a_page_holds_fewer_rows_when_they_hold_many_trials(
        self, auth_client, workspace
    ):
        environment = build_environment(
            workspace, runs=2, scenarios=20, trials=20, prefix="t-trials", edited=()
        )
        runs = _runs(environment)

        body = _ok(_compare(auth_client, environment, runs))

        # 2 runs x 20 trials a row: 15 rows make the 600-call page.
        assert body["page_size"] == 25
        assert len(body["rows"]) == 15
        assert body["total_pages"] == -(-body["count"] // 15)
        second = _ok(_compare(auth_client, environment, runs, page=2))
        assert {row["row_id"] for row in second["rows"]}.isdisjoint(
            row["row_id"] for row in body["rows"]
        )

    def test_only_the_rows_with_many_calls_shrink_their_page(
        self, auth_client, environment, monkeypatch
    ):
        runs = _runs(environment)
        whole = _ok(_compare(auth_client, environment, runs, page_size=100))
        from simulate.services import run_compare_page

        sizes = [sum(map(len, row["calls"].values())) for row in whole["rows"]]
        assert max(sizes) == 6 > min(sizes)
        monkeypatch.setattr(run_compare_page, "MAX_PAGE_CALLS", 10)

        first = _ok(_compare(auth_client, environment, runs, page_size=100))
        pages = [first] + [
            _ok(_compare(auth_client, environment, runs, page_size=100, page=page))
            for page in range(2, first["total_pages"] + 1)
        ]

        # Rows of fewer calls share a page rather than every page sized by the largest.
        assert first["page_size"] == 100
        # Sized by the largest row, a 6-call row, every page would hold one row.
        assert first["total_pages"] < whole["count"]
        for page in pages:
            calls = [c for row in page["rows"] for c in row["calls"].values()]
            assert sum(map(len, calls)) <= 10
        assert [row["row_id"] for page in pages for row in page["rows"]] == [
            row["row_id"] for row in whole["rows"]
        ]

    def test_a_better_run_is_an_improvement(self, auth_client, environment):
        runs = _runs(environment)
        key = _shared_key(runs)
        _grade(_key_calls(runs[0], key), "failed")
        for run in runs[1:]:
            _grade(_key_calls(run, key), "passed")

        body = _ok(_compare(auth_client, environment, runs, page_size=100))

        assert _row(body, key)["movement"] == "improved"

    def test_histograms_count_each_runs_scored_matching_calls(
        self, auth_client, environment
    ):
        runs = _runs(environment)
        failed = {"filters": {"status": ["failed"]}}
        whole = _ok(_compare(auth_client, environment, runs))
        body = _ok(_compare(auth_client, environment, runs, **failed))

        for run, line, unfiltered in zip(
            runs, body["runs"], whole["runs"], strict=True
        ):
            detail = _calls(auth_client, run, group_by="status", **failed)
            for eval_id, bands in line["histograms"].items():
                scored = sum(
                    group["aggregates"]["evaluations"][eval_id]["scored"]
                    for group in detail["groups"]
                )
                assert sum(bands) == scored > 0
                assert sum(unfiltered["histograms"][eval_id]) > scored

    def test_a_histogram_puts_each_score_in_its_band(self, auth_client, environment):
        runs = _runs(environment)
        scores = [0.05, 0.55, 0.95, 1.0]
        calls = list(CallExecution.objects.filter(test_execution=runs[0]))
        for index, call in enumerate(calls):
            for eval_id, output in call.eval_outputs.items():
                if eval_id != "harness-policy":
                    output["output"] = scores[index % len(scores)]
            call.save(update_fields=["eval_outputs"])
        cache.clear()

        body = _ok(_compare(auth_client, environment, runs))

        [bands] = [
            bands
            for eval_id, bands in body["runs"][0]["histograms"].items()
            if eval_id != "harness-policy"
        ]
        counts = Counter(scores[index % len(scores)] for index in range(len(calls)))
        assert bands == [
            counts[0.05],
            0,
            0,
            0,
            0,
            counts[0.55],
            0,
            0,
            0,
            counts[0.95] + counts[1.0],
        ]

    def test_a_group_counts_its_rows_on_every_page(self, auth_client, environment):
        runs = _runs(environment)
        whole = _ok(
            _compare(auth_client, environment, runs, page_size=100, group_by="sub_goal")
        )

        first = _ok(
            _compare(auth_client, environment, runs, page_size=3, group_by="sub_goal")
        )

        counts = {group["key"]: len(group["row_ids"]) for group in whole["groups"]}
        assert {group["key"]: group["row_count"] for group in whole["groups"]} == (
            counts
        )
        for group in first["groups"]:
            assert group["row_count"] == counts[group["key"]]
        assert any(
            group["row_count"] > len(group["row_ids"]) for group in first["groups"]
        )

    def test_groups_and_charts_score_a_harness_column_the_catalog_predates(
        self, auth_client, environment
    ):
        runs = _runs(environment)
        params = {"group_by": "status", "page_size": 100}
        _ok(_compare(auth_client, environment, runs, **params))
        # A late harness judgement, after the column catalog and rows were cached.
        for call in CallExecution.objects.filter(test_execution=runs[0]):
            call.eval_outputs["harness-late"] = {
                "name": "Late",
                "output": "Passed",
                "source": "harness",
            }
            call.save(update_fields=["eval_outputs"])

        body = _ok(_compare(auth_client, environment, runs, **params))
        detail = _calls(auth_client, runs[0], group_by="status")

        assert "harness-late" in {c["id"] for c in body["evaluation_columns"]}
        assert sum(body["runs"][0]["histograms"]["harness-late"]) > 0
        for group in detail["groups"]:
            [ours] = [g for g in body["groups"] if g["key"] == group["key"]]
            late = ours["runs"][str(runs[0].id)]["aggregates"]["evaluations"]
            assert (
                late["harness-late"]
                == group["aggregates"]["evaluations"]["harness-late"]
            )
            assert late["harness-late"]["scored"] > 0

    def test_an_eval_graded_as_a_sub_goal_in_one_run_reads_as_an_evaluation(
        self, auth_client, environment
    ):
        runs = _runs(environment)
        for call in CallExecution.objects.filter(test_execution=runs[0]):
            receipt = call.call_metadata["hosted_harness_receipt"]
            receipt["sub_goals"] = [*receipt["sub_goals"], {"name": "Policy"}]
            call.save(update_fields=["call_metadata"])
        cache.clear()

        body = _ok(_compare(auth_client, environment, runs))

        kinds = {
            column["id"]: column["kind"]
            for column in _calls(auth_client, runs[0])["evaluation_columns"]
        }
        [policy] = [
            c for c in body["evaluation_columns"] if c["id"] == "harness-policy"
        ]
        assert kinds["harness-policy"] == "sub_goal"
        assert policy == {
            "id": "harness-policy",
            "name": "Policy",
            "kind": "evaluation",
        }

    def test_the_build_leaves_the_collector_on(
        self, auth_client, environment, monkeypatch
    ):
        from simulate.services import run_compare_page

        seen = []
        filter_rows = run_compare_page.filter_rows

        def watched(*args: Any, **kwargs: Any) -> Any:
            seen.append(gc.isenabled())
            return filter_rows(*args, **kwargs)

        monkeypatch.setattr(run_compare_page, "filter_rows", watched)

        _ok(_compare(auth_client, environment, _runs(environment)))

        assert seen and all(seen)


@pytest.mark.parametrize(
    "columns,same",
    [
        ({"goal": "Cancel a subscription"}, False),
        ({"goal": "Refund a damaged order"}, True),
    ],
)
def test_a_use_case_alone_tells_scenario_versions_apart(columns, same):
    from simulate.services.run_compare_page import _row_id

    shown = {
        "scenario": "Sample scenario",
        "goal": "Refund a damaged order",
        "persona": None,
        "persona_details": None,
        "scenario_details": None,
        "ideal_outcome": None,
    }

    one = _row_id("k", shown)
    other = _row_id("k", {**shown, **columns})

    assert (one == other) is same
    assert _row_id("k", {**shown, "goal": "Sample scenario"}) == "k"


@pytest.mark.integration
@pytest.mark.django_db
class TestDatasetRowScenarios:
    """Row identity read from the authored scenario's dataset row, as hosted runs do."""

    @pytest.fixture
    def versions(self, environment, organization, user, workspace):
        runs = _runs(environment)
        key = _shared_key(runs)
        dataset = Dataset.no_workspace_objects.create(
            name="Compare scenarios",
            organization=organization,
            workspace=workspace,
            user=user,
            source=DatasetSourceChoices.SCENARIO.value,
        )
        column = Column.objects.create(
            dataset=dataset,
            name="situation",
            data_type="text",
            source=SourceChoices.OTHERS.value,
        )
        cells = []
        for order, text in enumerate(("Situation one", "Situation two")):
            row = Row.objects.create(dataset=dataset, order=order)
            cells.append(
                Cell.objects.create(dataset=dataset, column=column, row=row, value=text)
            )
        authored = HostedHarnessScenario.no_workspace_objects.get(
            job=environment, scenario_key=key
        )
        authored.dataset_row = cells[0].row
        authored.save(update_fields=["dataset_row"])
        # The last run played an edited copy, registered on its own job.
        own_job = HostedHarnessJob.no_workspace_objects.get(test_execution=runs[-1])
        edited = HostedHarnessScenario.no_workspace_objects.create(
            job=own_job,
            scenario_key=key,
            use_case=authored.use_case,
            dataset_row=cells[1].row,
        )
        for run in runs:
            calls = _key_calls(run, key)
            for call in calls:
                metadata = dict(call.call_metadata)
                metadata.pop("row_data")
                call.call_metadata = metadata
                call.save(update_fields=["call_metadata"])
            if run == runs[-1]:
                HostedHarnessExecution.no_workspace_objects.filter(
                    call_execution__in=calls
                ).update(source_scenario=edited)
        cache.clear()
        return runs, key, cells

    def test_an_edited_dataset_row_is_two_rows(
        self, auth_client, environment, versions
    ):
        runs, key, _ = versions

        body = _ok(_compare(auth_client, environment, runs, page_size=100))

        rows = [row for row in body["rows"] if row["scenario_key"] == key]
        assert sorted(row["situation"] for row in rows) == [
            "Situation one",
            "Situation two",
        ]
        for row in rows:
            ran = {run for run, calls in row["calls"].items() if calls}
            expected = (
                {str(runs[-1].id)}
                if row["situation"] == "Situation two"
                else {str(run.id) for run in runs[:-1]}
            )
            assert ran == expected
            for calls in row["calls"].values():
                assert {call["scenario_details"] for call in calls} <= {
                    row["situation"]
                }

    def test_dataset_rows_read_like_the_scenario_sources(
        self, auth_client, environment, versions
    ):
        """Compare's narrow dataset-row read gives the row columns the full read does."""
        runs, key, cells = versions
        _ok(_compare(auth_client, environment, runs))
        from simulate.models.harness_versioning import (
            HarnessSuiteMember,
            HarnessSuiteVersion,
        )
        from simulate.services.run_compare_page import (
            call_dataset_rows,
            call_scenario_sources,
            row_level_columns,
        )

        authored = HostedHarnessScenario.no_workspace_objects.get(
            job=environment, scenario_key=key
        )
        # The first run is pinned to a suite whose member rewrites the scenario.
        suite = HarnessSuiteVersion.no_workspace_objects.create(
            job=environment,
            organization_id=environment.organization_id,
            workspace_id=environment.workspace_id,
            version_number=1,
            status="active",
            origin="manual",
            hash="sha256:" + uuid.uuid4().hex * 2,
            archive_object_key="harness-suites/x.tar.gz",
            archive_sha256="",
            archive_size=0,
        )
        HarnessSuiteMember.no_workspace_objects.create(
            suite_version=suite,
            scenario=authored,
            scenario_key=key,
            number=1,
            content={"name": "Pinned", "use_case": "A pinned use case"},
        )
        HostedHarnessJob.no_workspace_objects.filter(test_execution=runs[0]).update(
            suite_version=suite
        )
        # An allocation whose scenario has no dataset row wins over the call's own
        # registration, which has one.
        orphan = _key_calls(runs[1], key)[0]
        HostedHarnessExecution.no_workspace_objects.filter(
            call_execution=orphan
        ).update(
            source_scenario=HostedHarnessScenario.no_workspace_objects.create(
                job=environment, scenario_key=f"{key}-plain"
            )
        )
        HostedHarnessScenario.no_workspace_objects.create(
            job=HostedHarnessJob.no_workspace_objects.get(test_execution=runs[1]),
            scenario_key=f"{key}-registered",
            call_execution=orphan,
            dataset_row=cells[1].row,
        )
        # A call with no allocation reads its registration.
        registered = _key_calls(runs[1], key)[1]
        HostedHarnessExecution.no_workspace_objects.filter(
            call_execution=registered
        ).delete()
        HostedHarnessScenario.no_workspace_objects.create(
            job=HostedHarnessJob.no_workspace_objects.get(test_execution=runs[1]),
            scenario_key=f"{key}-own",
            call_execution=registered,
            dataset_row=cells[1].row,
        )
        calls = list(
            CallExecution.objects.filter(test_execution__in=runs).select_related(
                "scenario"
            )
        )

        narrow = call_dataset_rows(calls)
        full = call_scenario_sources(calls)

        assert narrow[1] == full[1]
        assert str(orphan.id) not in narrow[0]
        assert narrow[0][str(registered.id)].dataset_row_id == cells[1].row_id
        for call in calls:
            assert row_level_columns(call, *narrow) == row_level_columns(call, *full)

    def test_the_same_content_is_one_row(self, auth_client, environment, versions):
        runs, key, cells = versions
        Cell.objects.filter(id=cells[1].id).update(value="Situation one")
        cache.clear()

        body = _ok(_compare(auth_client, environment, runs, page_size=100))

        [row] = [row for row in body["rows"] if row["scenario_key"] == key]
        assert row["situation"] == "Situation one"
        assert all(row["calls"].values())


@pytest.mark.integration
@pytest.mark.django_db
@pytest.mark.parametrize("params", PARAM_SETS, ids=[str(one) for one in PARAM_SETS])
def test_each_run_equals_its_calls_page(auth_client, environment, params):
    runs = _runs(environment)
    body = _ok(_compare(auth_client, environment, runs, page_size=100, **params))
    _assert_declared(body)
    shown = {
        call["id"]: call
        for row in body["rows"]
        for calls in row["calls"].values()
        for call in calls
    }
    columns = {column["id"]: column for column in body["evaluation_columns"]}
    kept_ids: set[str] = set()
    for run, line in zip(runs, body["runs"], strict=True):
        detail = _calls(auth_client, run, **params)
        kept_ids |= {call["id"] for call in detail["results"]}
        unfiltered = {call["id"]: call for call in _calls(auth_client, run)["results"]}
        assert line["execution"] == detail["execution"]
        assert line["summary"] == detail["summary"]
        assert line["status_counts"] == detail["status_counts"]
        # Every call this run shows is the detail page's row for it, and every
        # call the detail page keeps is on a kept row.
        mine = {
            call["id"] for row in body["rows"] for call in row["calls"][str(run.id)]
        }
        assert {call["id"] for call in detail["results"]} <= mine
        for call_id in mine:
            assert shown[call_id] == unfiltered[call_id]
        for column in detail["evaluation_columns"]:
            assert columns[column["id"]] == column
        groups = {
            group["key"]: group["runs"][str(run.id)]
            for group in body["groups"]
            if str(run.id) in group["runs"]
        }
        assert set(groups) == {group["key"] for group in detail["groups"]}
        for group in detail["groups"]:
            ours = {k: v for k, v in groups[group["key"]].items() if k != "result_ids"}
            assert ours == {k: v for k, v in group.items() if k != "result_ids"}
    # Every row holds a call some run's detail page keeps, and all fit one page.
    for row in body["rows"]:
        assert any(
            call["id"] in kept_ids for calls in row["calls"].values() for call in calls
        )
    assert body["count"] == len(body["rows"])


@pytest.mark.integration
@pytest.mark.django_db
def test_facets_sum_every_runs_facets(auth_client, environment):
    runs = _runs(environment)

    body = _ok(_compare(auth_client, environment, runs))

    for name in ("goal", "status", "sub_goal", "persona", "age", "attack", "task"):
        expected: Counter[str] = Counter()
        for run in runs:
            expected.update(
                {
                    one["value"]: one["count"]
                    for one in _calls(auth_client, run)["facets"][name]
                }
            )
        assert {one["value"]: one["count"] for one in body["facets"][name]} == expected


@pytest.mark.integration
@pytest.mark.django_db
def test_level_labels_match_every_runs_calls(auth_client, environment):
    runs = _runs(environment)

    body = _ok(_compare(auth_client, environment, runs))

    expected: dict[str, str] = {}
    for run in runs:
        expected.update(_calls(auth_client, run)["level_labels"])
    assert body["level_labels"] == expected


@pytest.mark.integration
@pytest.mark.django_db
def test_queries_do_not_grow_with_the_calls(auth_client, workspace):
    cold_counts, warm_counts = [], []
    for scenarios, prefix in ((3, "t-small"), (30, "t-large")):
        environment = build_environment(
            workspace, scenarios=scenarios, prefix=prefix, edited=()
        )
        runs = _runs(environment)
        params = {"group_by": "sub_goal", "filters": {"status_not": ["error"]}}
        # A full default page too, so a per-call read in the page build shows.
        for page_size in (3, 25):
            cache.clear()
            with CaptureQueriesContext(connection) as cold:
                _ok(
                    _compare(
                        auth_client, environment, runs, page_size=page_size, **params
                    )
                )
            with CaptureQueriesContext(connection) as warm:
                _ok(
                    _compare(
                        auth_client, environment, runs, page_size=page_size, **params
                    )
                )
            cold_counts.append(len(cold))
            warm_counts.append(len(warm))
    assert len(set(cold_counts)) == 1
    assert len(set(warm_counts)) == 1
    assert warm_counts[-1] <= 18 + 3


@pytest.mark.integration
@pytest.mark.django_db
def test_warm_queries_add_at_most_one_per_run(auth_client, workspace):
    environment = build_environment(
        workspace, runs=5, scenarios=3, prefix="t-warm", edited=()
    )
    runs = _runs(environment)
    counts = {}
    for count in (2, 5):
        _ok(_compare(auth_client, environment, runs[:count], page_size=3))
        with CaptureQueriesContext(connection) as warm:
            _ok(_compare(auth_client, environment, runs[:count], page_size=3))
        counts[count] = len(warm)
        # The runs' ordinals are counted in SQL, not read run by run.
        assert any(
            'COUNT("simulate_test_execution"."id") FILTER' in query["sql"]
            for query in warm.captured_queries
        )
    assert counts[5] - counts[2] <= 3


@pytest.mark.integration
@pytest.mark.django_db
def test_cold_queries_grow_only_with_the_runs(auth_client, workspace):
    environment = build_environment(
        workspace, runs=5, scenarios=3, prefix="t-runs", edited=()
    )
    runs = _runs(environment)
    counts = {}
    for count in (2, 3, 5):
        cache.clear()
        with CaptureQueriesContext(connection) as cold:
            _ok(_compare(auth_client, environment, runs[:count], page_size=3))
        counts[count] = len(cold)
    per_run = counts[3] - counts[2]
    assert counts[5] - counts[3] == 2 * per_run


def _off_page_call(body: dict[str, Any], run: TestExecution) -> CallExecution:
    """A call of ``run`` the page does not show, so only the counts reflect it."""
    shown = {
        call["id"]
        for row in body["rows"]
        for calls in row["calls"].values()
        for call in calls
    }
    return (
        CallExecution.objects.filter(test_execution=run)
        .exclude(id__in=shown)
        .order_by("id")
        .first()
    )


def _scoring(body: dict[str, Any]) -> list[dict[str, int]]:
    return [run["summary"]["scoring"] for run in body["runs"]]


@pytest.mark.integration
@pytest.mark.django_db
def test_a_pending_call_counts_as_due_once_its_clock_passes(auth_client, workspace):
    from unittest.mock import patch

    from simulate.services.harness_run_evals import EVAL_QUEUED_KEY

    environment = build_environment(
        workspace, runs=2, scenarios=6, trials=1, prefix="t-due", edited=()
    )
    runs = _runs(environment)
    config_id = str(
        SimulateEvalConfig.objects.get(run_test_id=environment.run_test_id).id
    )
    first = _ok(_compare(auth_client, environment, runs, page_size=1))
    waiting = _off_page_call(first, runs[0])
    now = timezone.now()
    waiting.eval_outputs = {**waiting.eval_outputs, config_id: {"status": "pending"}}
    waiting.call_metadata = {
        **waiting.call_metadata,
        EVAL_QUEUED_KEY: {config_id: now.isoformat()},
    }
    waiting.save(update_fields=["eval_outputs", "call_metadata"])
    cache.clear()

    cold = _ok(_compare(auth_client, environment, runs, page_size=1))
    with patch("django.utils.timezone.now", return_value=now + timedelta(minutes=31)):
        warm = _ok(_compare(auth_client, environment, runs, page_size=1))
        calls_page = _calls(auth_client, runs[0])
        cache.clear()
        fresh = _ok(_compare(auth_client, environment, runs, page_size=1))

    assert _scoring(cold)[0]["pending"] == 1
    assert _scoring(warm)[0]["pending"] == 0
    assert _scoring(warm) == _scoring(fresh)
    assert _scoring(warm)[0] == calls_page["summary"]["scoring"]


@pytest.mark.integration
@pytest.mark.django_db
def test_settled_statuses_are_dropped_when_the_runnable_evals_change(
    auth_client, workspace
):
    environment = build_environment(
        workspace, runs=2, scenarios=6, trials=1, prefix="t-runnable", edited=()
    )
    runs = _runs(environment)
    config = SimulateEvalConfig.objects.get(run_test_id=environment.run_test_id)
    first = _ok(_compare(auth_client, environment, runs, page_size=1))
    # Without its own entry this call is settled while the eval runs on no receipt,
    # and expected (then long overdue) once the eval has a mapping.
    unscored = _off_page_call(first, runs[0])
    unscored.eval_outputs = {
        key: value
        for key, value in unscored.eval_outputs.items()
        if key != str(config.id)
    }
    unscored.call_metadata = {**unscored.call_metadata, "eval_started": True}
    unscored.save(update_fields=["eval_outputs", "call_metadata"])
    cache.clear()
    stale = _ok(_compare(auth_client, environment, runs, page_size=1))

    # ``update`` moves no cache key input, so the cached facts stay.
    SimulateEvalConfig.objects.filter(id=config.id).update(
        mapping={"input": "transcript"}
    )
    warm = _ok(_compare(auth_client, environment, runs, page_size=1))
    calls_page = _calls(auth_client, runs[0])
    cache.clear()
    fresh = _ok(_compare(auth_client, environment, runs, page_size=1))

    assert _scoring(fresh) != _scoring(stale)
    assert _scoring(warm) == _scoring(fresh)
    assert _scoring(warm)[0] == calls_page["summary"]["scoring"]


def _expire_rows(shape: str = "calls:v4") -> dict[str, tuple[Any, Any]]:
    """Drop only the cached rows, as when they expire before the compare facts.

    Returns the dropped entries, stored as they were, so a test can put them back.
    """
    locmem: Any = cache  # The test settings' LocMemCache.
    return {
        key: (locmem._cache.pop(key), locmem._expire_info.pop(key, None))
        for key in [key for key in locmem._cache if f":simulate:v3:{shape}" in key]
    }


def _regrade_off_page_call(body: dict[str, Any], run: TestExecution) -> None:
    """Fail one off-page call's eval, a change no cache key input follows."""
    config_id = str(SimulateEvalConfig.objects.get(run_test_id=run.run_test_id).id)
    call = _off_page_call(body, run)
    graded = call.eval_outputs
    CallExecution.objects.filter(id=call.id).update(
        eval_outputs={**graded, config_id: {**graded[config_id], "status": "failed"}}
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_facts_written_late_from_older_rows_do_not_settle_the_newer_rows(
    auth_client, workspace
):
    """A compare that read the old rows writes its facts after another rescanned."""
    environment = build_environment(
        workspace, runs=2, scenarios=6, trials=1, prefix="t-late", edited=()
    )
    runs = _runs(environment)
    first = _ok(_compare(auth_client, environment, runs, page_size=1))
    late_facts = _expire_rows("compare-facts:")
    _regrade_off_page_call(first, runs[0])
    _expire_rows()
    # This compare rescans the rows and writes facts from them...
    _ok(_compare(auth_client, environment, runs, page_size=1))
    # ...then the late write of a compare that settled from the old rows lands.
    locmem: Any = cache
    for key, (value, expiry) in late_facts.items():
        locmem._cache[key] = value
        locmem._expire_info[key] = expiry

    warm = _ok(_compare(auth_client, environment, runs, page_size=1))
    calls_page = _calls(auth_client, runs[0])

    assert calls_page["summary"]["scoring"]["failed"] == 1
    assert _scoring(warm)[0] == calls_page["summary"]["scoring"]


@pytest.mark.integration
@pytest.mark.django_db
def test_facts_read_while_the_calls_page_rescans_do_not_settle_the_newer_rows(
    auth_client, workspace, monkeypatch
):
    """The calls page rescans the rows while a compare reads facts from the old ones."""
    import simulate.views.harness_environment  # noqa: F401
    from simulate.services import run_compare_page
    from simulate.services.run_results_v3 import build_evaluation_catalog
    from simulate.services.run_results_v3_page import (
        _cached_call_values,
        scan_call_values,
    )
    from simulate.services.run_results_v3_scoring import live_eval_specs

    environment = build_environment(
        workspace, runs=2, scenarios=6, trials=1, prefix="t-midway", edited=()
    )
    runs = _runs(environment)
    first = _ok(_compare(auth_client, environment, runs, page_size=1))
    _regrade_off_page_call(first, runs[0])
    # The old rows stay cached; only the facts are gone.
    _expire_rows("compare-facts:")
    read_call_facts = run_compare_page.read_call_facts
    configs = list(
        SimulateEvalConfig.objects.filter(
            run_test_id=environment.run_test_id, deleted=False
        ).values("id", "name")
    )
    columns = build_evaluation_catalog(runs[0], configs=configs)[0]

    def rescanned_meanwhile(*args: Any, **kwargs: Any) -> Any:
        monkeypatch.setattr(run_compare_page, "read_call_facts", read_call_facts)
        _expire_rows()
        _cached_call_values(
            runs[0],
            {},
            columns,
            lambda: scan_call_values(
                runs[0],
                columns,
                "-started_at",
                live_evals=live_eval_specs(runs[0].run_test_id),
            ),
        )
        return read_call_facts(*args, **kwargs)

    monkeypatch.setattr(run_compare_page, "read_call_facts", rescanned_meanwhile)
    _ok(_compare(auth_client, environment, runs, page_size=1))

    warm = _ok(_compare(auth_client, environment, runs, page_size=1))
    calls_page = _calls(auth_client, runs[0])

    assert calls_page["summary"]["scoring"]["failed"] == 1
    assert _scoring(warm)[0] == calls_page["summary"]["scoring"]


@pytest.mark.integration
@pytest.mark.django_db
def test_settled_statuses_follow_the_rows_the_calls_page_reads_again(
    auth_client, workspace
):
    environment = build_environment(
        workspace, runs=2, scenarios=6, trials=1, prefix="t-reread", edited=()
    )
    runs = _runs(environment)
    config_id = str(
        SimulateEvalConfig.objects.get(run_test_id=environment.run_test_id).id
    )
    first = _ok(_compare(auth_client, environment, runs, page_size=1))
    regraded = _off_page_call(first, runs[0])
    graded = regraded.eval_outputs
    regraded.eval_outputs = {
        **graded,
        config_id: {**graded[config_id], "status": "failed"},
    }
    regraded.save(update_fields=["eval_outputs"])
    cache.clear()
    # The calls page writes the rows, then compare settles the call as failed.
    _calls(auth_client, runs[0])
    stale = _ok(_compare(auth_client, environment, runs, page_size=1))

    # A late re-grade moves no key input; the rows expire and the calls page
    # reads them again.
    CallExecution.objects.filter(id=regraded.id).update(eval_outputs=graded)
    _expire_rows()
    calls_page = _calls(auth_client, runs[0])
    warm = _ok(_compare(auth_client, environment, runs, page_size=1))

    assert _scoring(stale)[0]["failed"] == 1
    assert _scoring(warm)[0] == calls_page["summary"]["scoring"]


@pytest.mark.integration
@pytest.mark.django_db
def test_a_row_reads_its_scenario_as_its_calls_do_after_an_edit(auth_client, workspace):
    environment = build_environment(
        workspace, runs=2, scenarios=3, trials=1, prefix="t-renamed", edited=()
    )
    runs = _runs(environment)
    first = _ok(_compare(auth_client, environment, runs))["rows"][0]
    call_id = first["calls"][str(runs[0].id)][0]["id"]
    # ``update`` moves no cache key input, so the cached facts stay.
    Scenarios.objects.filter(call_executions__id=call_id).update(name="Renamed")

    row = next(
        row
        for row in _ok(_compare(auth_client, environment, runs))["rows"]
        if row["row_id"] == first["row_id"]
    )

    assert row["scenario"] == row["calls"][str(runs[0].id)][0]["scenario"]
    assert row["scenario"] == "Renamed"


@pytest.mark.integration
@pytest.mark.django_db
def test_list_groups_hold_the_calls_the_calls_page_groups(auth_client, workspace):
    """A sub-goal stored with spaces, or not in a list, groups as on the calls page."""
    environment = build_environment(
        workspace, runs=2, scenarios=6, trials=1, prefix="t-loose", edited=()
    )
    scenarios = HostedHarnessScenario.objects.filter(job=environment).order_by("number")
    scenarios.filter(id=scenarios[0].id).update(sub_goals=["Logged the case "])
    scenarios.filter(id=scenarios[1].id).update(sub_goals="Logged the case")
    runs = _runs(environment)
    cache.clear()
    body = _ok(
        _compare(auth_client, environment, runs, page_size=100, group_by="sub_goal")
    )

    for run in runs:
        detail = _calls(auth_client, run, group_by="sub_goal")
        groups = {
            group["key"]: group["runs"][str(run.id)]
            for group in body["groups"]
            if str(run.id) in group["runs"]
        }
        for group in detail["groups"]:
            ours = {k: v for k, v in groups[group["key"]].items() if k != "result_ids"}
            assert ours == {k: v for k, v in group.items() if k != "result_ids"}


# Queries a compare makes: a constant plus one per run warm, five per run cold.
COLD_QUERIES = (23, 5)
WARM_QUERIES = (16, 1)


@pytest.mark.integration
@pytest.mark.django_db
def test_query_counts_follow_the_runs_not_the_calls(auth_client, workspace):
    counts = {}
    for runs, scenarios in ((5, 30), (8, 30), (5, 500)):
        environment = build_environment(
            workspace,
            runs=runs,
            scenarios=scenarios,
            trials=1,
            prefix=f"t-q{runs}-{scenarios}",
            edited=(),
        )
        params = {"group_by": "sub_goal", "filters": {"status_not": ["error"]}}
        played = _runs(environment)
        cache.clear()
        with CaptureQueriesContext(connection) as cold:
            _ok(_compare(auth_client, environment, played, **params))
        with CaptureQueriesContext(connection) as warm:
            _ok(_compare(auth_client, environment, played, **params))
        counts[runs, scenarios] = (len(cold), len(warm))
    for (runs, _), (cold, warm) in counts.items():
        assert cold == COLD_QUERIES[0] + COLD_QUERIES[1] * runs
        assert warm == WARM_QUERIES[0] + WARM_QUERIES[1] * runs
