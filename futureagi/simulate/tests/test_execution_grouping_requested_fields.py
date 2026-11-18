import json
from datetime import timedelta

import pytest

from simulate.models import CallExecution, RunTest, Scenarios
from simulate.models.test_execution import TestExecution as SimulationTestExecution
from simulate.utils.test_execution import DEFAULT_CHAT_SIM_COL


@pytest.fixture
def chat_run(db, organization, workspace):
    run_test = RunTest.objects.create(
        name="Grouped chat", organization=organization, workspace=workspace
    )
    execution = SimulationTestExecution.objects.create(
        run_test=run_test, status=SimulationTestExecution.ExecutionStatus.COMPLETED
    )

    def add_call(name="Checkout", *, status="completed", score=4):
        scenario, _ = Scenarios.objects.get_or_create(
            organization=organization,
            workspace=workspace,
            name=name,
            defaults={"source": "prompt", "scenario_type": "script"},
        )
        return CallExecution.objects.create(
            test_execution=execution,
            scenario=scenario,
            status=status,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
            call_type="chat",
            overall_score=score,
            response_time_ms=200,
            conversation_metrics_data={"avg_latency_ms": 100, "csat_score": score},
        )

    return execution, add_call


def _detail(client, execution, **params):
    for key in ("row_groups", "group_keys"):
        if key in params:
            params[key] = json.dumps(params[key])
    response = client.get(f"/simulate/test-executions/{execution.id}/", params)
    assert response.status_code == 200, response.data
    return response.data


def test_status_group_ignores_displayed_score(auth_client, chat_run):
    execution, add = chat_run
    first, second = add(score=2), add(score=4)
    # Isolate display selection: ordering by creation time cannot split these rows.
    CallExecution.objects.filter(pk=second.pk).update(created_at=first.created_at)

    data = _detail(auth_client, execution, row_groups=["status"])
    assert data["column_order"] == DEFAULT_CHAT_SIM_COL
    assert data["count"] == 1
    (row,) = data["results"]
    assert (row["status"], row["count"], row["avg_overall_score"]) == (
        "completed",
        2,
        3,
    )
    assert "overall_score" not in row


def test_status_pages_ignore_creation_time(auth_client, chat_run):
    execution, add = chat_run
    # Isolate ordering: the displayed column is already the requested field.
    columns = [{"id": "status", "column_name": "Status"}]
    execution.execution_metadata = {"Provider": True, "column_order": columns}
    execution.save(update_fields=["execution_metadata"])
    add(status="failed")
    first, second = add(), add()
    CallExecution.objects.filter(pk=second.pk).update(
        created_at=first.created_at + timedelta(minutes=1)
    )

    for page, status, count in [(1, "completed", 2), (2, "failed", 1)]:
        data = _detail(
            auth_client, execution, row_groups=["status"], page=page, limit=1
        )
        assert data["count"] == data["total_pages"] == 2
        assert data["current_page"] == page
        assert data["next"] == ("?page=2" if page == 1 else None)
        assert data["previous"] == (None if page == 1 else "?page=1")
        assert data["column_order"] == columns
        (row,) = data["results"]
        assert (row["status"], row["count"]) == (status, count)


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        (
            [],
            [
                ("completed", "Checkout", 2),
                ("completed", "Returns", 1),
                ("failed", "Checkout", 1),
            ],
        ),
        (["completed"], [("completed", "Checkout", 2), ("completed", "Returns", 1)]),
        (["completed", "Checkout"], [("completed", "Checkout", 2)]),
    ],
)
def test_flat_groups_with_positional_keys(auth_client, chat_run, keys, expected):
    execution, add = chat_run
    add("Returns")
    add("Checkout", status="failed")
    add("Checkout")
    add("Checkout")

    data = _detail(
        auth_client, execution, row_groups=["status", "scenario"], group_keys=keys
    )
    assert [
        (r.get("status"), r.get("scenario__name"), r["count"]) for r in data["results"]
    ] == expected
    assert all("id" not in row for row in data["results"])


@pytest.mark.parametrize(
    ("alias", "key", "field", "value"),
    [
        ("callType", "CHAT", "call_type", "chat"),
        ("overallScore", "4", "overall_score", 4),
        ("responseTime", "200", "response_time_ms", 200),
    ],
)
def test_legacy_group_ids_keep_field_names_and_filters(
    auth_client, chat_run, alias, key, field, value
):
    execution, add = chat_run
    add()
    data = _detail(auth_client, execution, row_groups=[alias], group_keys=[key])
    (row,) = data["results"]
    assert (row.get(field), row["count"]) == (value, 1)
    assert "overall_score" not in row or field == "overall_score"


@pytest.mark.parametrize("params", [{}, {"row_groups": []}], ids=["omitted", "empty"])
def test_ungrouped_calls_keep_newest_updated_order(auth_client, chat_run, params):
    execution, add = chat_run
    first, second = add(), add()
    # The first-created call was updated most recently.
    CallExecution.objects.filter(pk=first.pk).update(
        updated_at=second.updated_at + timedelta(minutes=1)
    )
    data = _detail(auth_client, execution, **params)
    assert data["column_order"] == DEFAULT_CHAT_SIM_COL
    assert data["count"] == 2
    assert [row["id"] for row in data["results"]] == [str(first.id), str(second.id)]
