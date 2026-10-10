"""The v3 calls endpoint's request contract: validation, scoping and caching."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import CallExecution, Scenarios, SimulateEvalConfig, TestExecution
from simulate.models.hosted_harness import HostedHarnessJob, HostedHarnessScenario
from simulate.models.run_test import RunTest

URL = "/simulate/v3/test-executions/{}/calls/"


def _run(organization, workspace, **fields: Any) -> TestExecution:
    run_test = RunTest.no_workspace_objects.create(
        name="Calls view run", organization=organization, workspace=workspace
    )
    return TestExecution.no_workspace_objects.create(
        run_test=run_test,
        status=fields.pop("status", TestExecution.ExecutionStatus.COMPLETED),
        **fields,
    )


@pytest.fixture
def execution(organization, workspace) -> TestExecution:
    return _run(organization, workspace)


@pytest.fixture
def calls(execution, organization, workspace) -> list[CallExecution]:
    scenario = Scenarios.objects.create(
        name="Calls view scenario",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.SCRIPT,
        organization=organization,
        workspace=workspace,
    )
    template = EvalTemplate.objects.create(
        name="Calls view eval",
        organization=organization,
        output_type_normalized="percentage",
        pass_threshold=0.5,
    )
    eval_id = str(
        SimulateEvalConfig.objects.create(
            name="Calls view eval", run_test=execution.run_test, eval_template=template
        ).id
    )
    started = timezone.now()
    return [
        CallExecution.objects.create(
            test_execution=execution,
            scenario=scenario,
            status=call_status,
            started_at=started - timedelta(seconds=index),
            duration_seconds=index * 10,
            call_metadata={"goal": f"Goal {index % 2}"},
            eval_outputs={eval_id: {"status": "completed", "output": score}},
        )
        for index, (call_status, score) in enumerate(
            [
                ("completed", 0.9),
                ("completed", 0.2),
                ("failed", None),
                ("completed", 0.6),
                ("pending", None),
            ]
        )
    ]


def _get(client, execution: TestExecution, **params: Any):
    return client.get(URL.format(execution.id), params)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestRunCallsV3Contract:
    @pytest.mark.parametrize(
        "params,field",
        [
            ({"filters": "{not json"}, "filters"),
            ({"filters": "[]"}, "filters"),
            ({"filters": json.dumps({"persona": ["x"]})}, "filters"),
            ({"filters": json.dumps({"status": ["green"]})}, "filters"),
            ({"group_by": "persona"}, "group_by"),
            ({"ordering": "csat"}, "ordering"),
            ({"ordering": "--started_at"}, "ordering"),
            ({"page": 0}, "page"),
            ({"page": "two"}, "page"),
            ({"page_size": 0}, "page_size"),
            ({"page_size": 501}, "page_size"),
        ],
    )
    def test_invalid_query_is_rejected(self, auth_client, execution, params, field):
        response = _get(auth_client, execution, **params)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in json.dumps(response.json())

    def test_validation_runs_before_the_run_lookup(self, auth_client):
        response = auth_client.get(URL.format(uuid.uuid4()), {"page_size": 0})

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_largest_page_size_is_accepted(self, auth_client, execution, calls):
        body = _get(auth_client, execution, page_size=500).json()

        assert (body["count"], len(body["results"]), body["total_pages"]) == (5, 5, 1)

    def test_page_beyond_the_last_is_empty(self, auth_client, execution, calls):
        response = _get(auth_client, execution, page=4, page_size=2)

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["results"] == []
        assert (body["count"], body["page"], body["total_pages"]) == (5, 4, 3)
        assert body["summary"]["total"] == 5

    def test_hand_off_of_another_runs_calls_is_empty(
        self, auth_client, execution, calls, organization, workspace
    ):
        sibling = TestExecution.objects.create(
            run_test=execution.run_test,
            status=TestExecution.ExecutionStatus.COMPLETED,
        )
        foreign = CallExecution.objects.create(
            test_execution=sibling, scenario=calls[0].scenario, status="completed"
        )

        response = _get(
            auth_client,
            execution,
            filters=json.dumps({"call_execution_id": [str(foreign.id)]}),
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert (body["count"], body["results"], body["groups"]) == (0, [], [])
        assert body["summary"]["total"] == 0
        assert body["execution"]["summary"]["total"] == 5
        assert body["facets"]["status"] == []
        assert body["facets"]["goal"] == []

    def test_unknown_run_is_not_found(self, auth_client):
        response = auth_client.get(URL.format(uuid.uuid4()))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_run_of_another_organization_is_not_found(self, auth_client, user):
        other = Organization.objects.create(name="Other organization")
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other workspace",
            organization=other,
            is_default=True,
            is_active=True,
            created_by=user,
        )
        foreign = _run(other, other_workspace)

        response = auth_client.get(URL.format(foreign.id))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("same_organization", [True, False])
    def test_workspace_header_without_the_run_is_not_found(
        self, auth_client, execution, calls, organization, user, same_organization
    ):
        workspace = Workspace.no_workspace_objects.create(
            name="Elsewhere",
            organization=(
                organization
                if same_organization
                else Organization.objects.create(name="Elsewhere org")
            ),
            is_default=False,
            is_active=True,
            created_by=user,
        )

        assert _get(auth_client, execution).status_code == status.HTTP_200_OK
        auth_client.set_workspace(workspace)

        response = _get(auth_client, execution)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize(
        "run_status",
        [
            TestExecution.ExecutionStatus.COMPLETED,
            TestExecution.ExecutionStatus.RUNNING,
        ],
    )
    def test_warm_requests_return_the_cold_bodies(
        self, auth_client, execution, calls, run_status
    ):
        execution.status = run_status
        execution.save(update_fields=["status"])
        requests = {
            "default": {},
            "filtered": {"filters": json.dumps({"status": ["passed"]})},
            "grouped": {"group_by": "status", "group_key": "failed"},
            "hand_off": {
                "filters": json.dumps(
                    {"call_execution_id": [str(calls[0].id), str(calls[2].id)]}
                )
            },
        }
        cold = {}
        for name, params in requests.items():
            cache.clear()
            response = _get(auth_client, execution, **params)
            assert response.status_code == status.HTTP_200_OK
            cold[name] = response.json()
        assert cold["filtered"]["count"] == 2
        assert cold["hand_off"]["count"] == 2

        cache.clear()
        sequence = ["default", "default", "filtered", "default", "hand_off"]
        sequence += ["grouped", "filtered", "hand_off", "default"]
        for name in sequence:
            assert _get(auth_client, execution, **requests[name]).json() == cold[name]

    def test_hand_off_scopes_the_sub_goal_facet(
        self, auth_client, execution, calls, organization, workspace
    ):
        job = HostedHarnessJob.no_workspace_objects.create(
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
            run_test=execution.run_test,
            test_execution=execution,
        )

        def receipt(call: CallExecution, *names: str) -> None:
            call.call_metadata = {
                **call.call_metadata,
                "hosted_harness_receipt": {
                    "sub_goals": [{"name": name, "held": True} for name in names]
                },
            }
            call.save(update_fields=["call_metadata"])

        def authored(call: CallExecution, *names: str) -> None:
            HostedHarnessScenario.no_workspace_objects.create(
                job=job,
                scenario_key=f"key-{call.id}",
                call_execution=call,
                sub_goals=list(names),
            )

        receipt(calls[0], "Receipt goal", "Shared goal")
        authored(calls[2], "Authored goal", "Shared goal")
        receipt(calls[1], "Left out goal", "Shared goal")
        authored(calls[3], "Left out authored goal")
        hand_off = [str(calls[0].id), str(calls[2].id)]

        for _ in range(2):
            body = _get(
                auth_client,
                execution,
                filters=json.dumps({"call_execution_id": hand_off}),
            ).json()
            assert body["facets"]["sub_goal"] == [
                {"value": "Shared goal", "count": 2},
                {"value": "Authored goal", "count": 1},
                {"value": "Receipt goal", "count": 1},
            ]
            assert body["facets"]["goal"] == [{"value": "Goal 0", "count": 2}]
            assert sorted(item["value"] for item in body["facets"]["status"]) == [
                "error",
                "passed",
            ]

        whole = _get(auth_client, execution).json()["facets"]["sub_goal"]
        assert {"value": "Shared goal", "count": 3} in whole
        assert {"value": "Left out authored goal", "count": 1} in whole


# Each ordering's ascending call order; descending is its reverse, so a missing
# value sorts last ascending and first descending.
EXPECTED_ORDER = {
    "started_at": [3, 2, 1, 0],
    "duration_seconds": [2, 3, 0, 1],
    "latency_ms": [2, 3, 1, 0],
    "turn_count": [1, 3, 0, 2],
    "tokens": [0, 2, 1, 3],
    "cost_cents": [1, 0, 2, 3],
    "scenario": [1, 3, 2, 0],
    "goal": [2, 0, 3, 1],
    "outcome": [0, 3, 1, 2],
}


# Call 0..3; a None leaves that call's value missing.
SORTABLE_CALLS = [
    {
        "scenario": "delta",
        "goal": "kilo",
        "status": "failed",
        "duration_seconds": 20,
        "avg_agent_latency_ms": None,
        "conversation_metrics_data": {"turn_count": 3, "total_tokens": 10},
        "customer_cost_cents": 5,
    },
    {
        "scenario": "alpha",
        "goal": "mike",
        "status": "completed",
        "harness_outcome_status": "passed",
        "duration_seconds": None,
        "avg_agent_latency_ms": 300,
        "conversation_metrics_data": {"turn_count": 1, "total_tokens": 40},
        "customer_cost_cents": 2,
    },
    {
        "scenario": "charlie",
        "goal": "juliet",
        "status": "pending",
        "duration_seconds": 5,
        "avg_agent_latency_ms": 100,
        "conversation_metrics_data": {"total_tokens": 30},
        "customer_cost_cents": 9,
    },
    {
        "scenario": "bravo",
        "goal": "lima",
        "status": "completed",
        "harness_outcome_status": "failed",
        "duration_seconds": 10,
        "avg_agent_latency_ms": 200,
        "conversation_metrics_data": {"turn_count": 2},
        "customer_cost_cents": None,
    },
]


@pytest.fixture
def sortable_calls(execution, organization, workspace) -> list[CallExecution]:
    started = timezone.now()
    calls = []
    for index, spec in enumerate(SORTABLE_CALLS):
        fields = dict(spec)
        metadata = {"goal": fields.pop("goal")}
        if outcome := fields.pop("harness_outcome_status", None):
            metadata["harness_outcome_status"] = outcome
        scenario = Scenarios.objects.create(
            name=fields.pop("scenario"),
            source="test",
            scenario_type=Scenarios.ScenarioTypes.SCRIPT,
            organization=organization,
            workspace=workspace,
        )
        calls.append(
            CallExecution.objects.create(
                test_execution=execution,
                scenario=scenario,
                started_at=started - timedelta(seconds=index),
                call_metadata=metadata,
                **fields,
            )
        )
    return calls


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
@pytest.mark.parametrize("field", list(EXPECTED_ORDER))
@pytest.mark.parametrize("descending", [False, True], ids=["asc", "desc"])
def test_each_ordering_sorts_by_its_own_field(
    auth_client, execution, sortable_calls, field, descending
):
    expected = EXPECTED_ORDER[field][::-1] if descending else EXPECTED_ORDER[field]

    body = _get(
        auth_client,
        execution,
        ordering=f"-{field}" if descending else field,
        page_size=500,
    ).json()

    assert [row["id"] for row in body["results"]] == [
        str(sortable_calls[index].id) for index in expected
    ]
