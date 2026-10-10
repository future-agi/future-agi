"""A page's slim call load renders the same rows as loading whole calls."""

import json
import uuid
from collections import defaultdict
from datetime import timedelta
from typing import Any

import pytest
from django.db import connection
from django.db.models import Case, IntegerField, Q, Value, When
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from model_hub.models.choices import DatasetSourceChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import (
    AgentDefinition,
    CallExecution,
    RunTest,
    Scenarios,
    SimulateEvalConfig,
    TestExecution,
)
from simulate.models.hosted_harness import (
    HostedHarnessExecution,
    HostedHarnessJob,
    HostedHarnessScenario,
)
from simulate.services.harness_scenarios import authored_scenarios_for_calls
from simulate.services.run_results_v3 import (
    CALL_ROW_METADATA_KEYS,
    _row_dimensions,
    build_call_rows,
    build_evaluation_catalog,
)
from simulate.services.run_results_v3_page import page_calls

DIMENSION_COLUMNS = (
    "persona",
    "use_case",
    "goal",
    "outcome",
    "situation",
    "branch",
    "conversation_branch",
)


def _whole_calls(page: dict[str, Any]) -> list[CallExecution]:
    """The page load before slimming: whole calls with their run joined per row."""
    by_id = {
        str(call.id): call
        for call in CallExecution.objects.filter(
            id__in=page["page_ids"]
        ).select_related("scenario", "test_execution__agent_definition")
    }
    goals = {row["id"]: row["result_goal"] for row in page["rows"]}
    calls = []
    for call_id in page["page_ids"]:
        if (call := by_id.get(call_id)) is None:
            continue
        call.result_goal = goals[call_id]
        calls.append(call)
    return calls


def _reference_row_dimensions(
    calls: list[CallExecution], extra_row_ids: set[str] | None = None
) -> dict[str, dict[str, Any]]:
    row_ids = {
        str(call.row_id or (call.call_metadata or {}).get("row_id"))
        for call in calls
        if call.row_id
        or (isinstance(call.call_metadata, dict) and call.call_metadata.get("row_id"))
    }
    row_ids |= extra_row_ids or set()
    if not row_ids:
        return {}
    dimensions: dict[str, dict[str, Any]] = defaultdict(dict)
    cells = Cell.all_objects.filter(
        row_id__in=row_ids, column__name__in=list(DIMENSION_COLUMNS)
    ).select_related("column")
    for cell in cells:
        dimensions[str(cell.row_id)][cell.column.name] = cell.value
    return dimensions


def _reference_authored(run_test_id, calls, *, test_execution_id=None):
    source_keys = {}
    for call in calls:
        metadata = call.call_metadata if isinstance(call.call_metadata, dict) else {}
        key = metadata.get("harness_scenario_key")
        source_keys[call.id] = key if isinstance(key, str) and key.strip() else None
    call_ids = [call.id for call in calls]
    keys = set(source_keys.values()) - {None}
    if test_execution_id is not None:
        own_run = Q(job__test_execution_id=test_execution_id)
        scope = own_run | Q(job__simulation_runs__test_execution_id=test_execution_id)
    else:
        run_jobs = HostedHarnessJob.no_workspace_objects.filter(run_test_id=run_test_id)
        scope = Q(job_id__in=run_jobs.values("id")) | Q(
            job_id__in=run_jobs.values("environment_id")
        )
    rows = HostedHarnessScenario.all_objects.filter(
        Q(call_execution_id__in=call_ids) | (scope & Q(scenario_key__in=keys))
    )
    if test_execution_id is not None:
        rows = rows.annotate(
            match_rank=Case(
                When(own_run, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        ).order_by("match_rank", "-created_at")
    else:
        rows = rows.order_by("-created_at")
    linked, by_key = {}, {}
    for row in rows:
        if row.call_execution_id:
            linked.setdefault(row.call_execution_id, row)
        by_key.setdefault(row.scenario_key, row)
    return {
        call.id: linked.get(call.id) or by_key.get(source_keys[call.id])
        for call in calls
    }


def _ids(resolved: dict) -> list[tuple[str, str | None]]:
    return [(str(k), str(v.id) if v else None) for k, v in resolved.items()]


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str)


@pytest.fixture
def run(db, organization, workspace, user):
    agent = AgentDefinition.objects.create(
        agent_name="Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        description="Agent",
        provider="vapi",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    run_test = RunTest.objects.create(
        name="Page run", organization=organization, workspace=workspace
    )
    execution = TestExecution.objects.create(
        run_test=run_test,
        agent_definition=agent,
        status=TestExecution.ExecutionStatus.COMPLETED,
        execution_metadata={"selected_scenario_keys": ["k"], "big": "x" * 5000},
    )
    plain = Scenarios.objects.create(
        name="Plain scenario", organization=organization, workspace=workspace
    )
    rich = Scenarios.objects.create(
        name="Rich scenario",
        organization=organization,
        workspace=workspace,
        metadata={
            "persona": {"name": "Scenario persona", "accent": "US"},
            "use_case": "Scenario use case",
            "conversation_branch": "Scenario branch",
        },
    )
    template = EvalTemplate.objects.create(
        name="Gate",
        config={"output": "Pass/Fail"},
        organization=organization,
        output_type_normalized="pass_fail",
    )
    score_template = EvalTemplate.objects.create(
        name="Score",
        config={"output": "score"},
        organization=organization,
        output_type_normalized="percentage",
        pass_threshold=0.5,
    )
    gate = SimulateEvalConfig.objects.create(
        name="Gate", eval_template=template, run_test=run_test
    )
    score = SimulateEvalConfig.objects.create(
        name="Score", eval_template=score_template, run_test=run_test
    )

    dataset = Dataset.no_workspace_objects.create(
        name="Rows",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    columns = {
        name: Column.objects.create(
            dataset=dataset,
            name=name,
            data_type="text",
            source=SourceChoices.OTHERS.value,
        )
        for name in DIMENSION_COLUMNS
    }
    row = Row.objects.create(dataset=dataset, order=0)
    authored_row = Row.objects.create(dataset=dataset, order=1)
    for name, value in (
        ("persona", '{"name": "Row persona", "age_group": "30s"}'),
        ("use_case", "Row use case"),
        ("outcome", "Row outcome"),
        ("situation", "Row situation"),
        ("branch", "Row branch"),
    ):
        Cell.objects.create(dataset=dataset, column=columns[name], row=row, value=value)
    # Two cells for one column: which one shows depends on the read order.
    older = Cell.objects.create(
        dataset=dataset, column=columns["goal"], row=row, value="Older goal"
    )
    Cell.objects.filter(id=older.id).update(
        created_at=timezone.now() - timedelta(days=1)
    )
    Cell.objects.create(
        dataset=dataset, column=columns["goal"], row=row, value="Newer goal"
    )
    Cell.objects.create(
        dataset=dataset,
        column=columns["situation"],
        row=authored_row,
        value="Authored situation",
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
    HostedHarnessScenario.no_workspace_objects.create(
        job=environment,
        scenario_key="env-key",
        branch="Environment branch",
        sub_goals=["Authored goal A", "Authored goal B"],
        dataset_row=authored_row,
    )
    HostedHarnessScenario.no_workspace_objects.create(
        job=own_job, scenario_key="own-key", branch="Own branch"
    )
    HostedHarnessScenario.no_workspace_objects.create(
        job=environment, scenario_key="own-key", branch="Shadowed branch"
    )

    now = timezone.now()
    gate_id, score_id = str(gate.id), str(score.id)
    prompt_noise = {"system_prompt": "p" * 4000, "voice_settings": {"a": 1}}
    specs = [
        {
            "scenario": rich,
            "row_id": row.id,
            "simulation_call_type": "voice",
            "status": CallExecution.CallStatus.COMPLETED,
            "call_metadata": {
                **prompt_noise,
                "persona": "Metadata persona",
                "use_case": "Metadata use case",
                "harness_scenario_key": "own-key",
                "harness_outcome_status": "passed",
                "harness_trial_index": 2,
                "hosted_harness_receipt": {
                    "scenario_key": "own-key",
                    "sub_goals": [
                        {"name": "Greets", "held": True},
                        {"name": "Verifies", "held": "yes"},
                        {"name": ""},
                        "Bare goal",
                    ],
                },
                "conversation_branch": "Metadata branch",
            },
            "provider_call_data": {
                "retell": {},
                "vapi": {"id": "1", "tool_calls": [{"n": 1}, 2]},
            },
            "eval_outputs": {
                gate_id: {"status": "completed", "output": "Passed", "name": "Gate"},
                score_id: {"status": "completed", "output": 0.8, "reason": "ok"},
                "harness-1": {"source": "harness", "name": "Greets", "output": True},
                "stale": {"status": "completed", "output": True},
            },
            "conversation_metrics_data": {
                "total_tokens": 120,
                "turn_count": 6,
                "csat_score": "10",
            },
            "avg_agent_latency_ms": 420,
            "customer_cost_cents": 12,
            "stt_cost_cents": 1,
            "llm_cost_cents": 2,
            "tts_cost_cents": 3,
            "storage_cost_cents": 0.5,
            "duration_seconds": 33,
            "started_at": now - timedelta(minutes=3),
            "completed_at": now - timedelta(minutes=2),
            "ended_reason": "customer-ended-call",
        },
        {
            "scenario": plain,
            "simulation_call_type": "text",
            "status": CallExecution.CallStatus.COMPLETED,
            "call_metadata": {
                **prompt_noise,
                "row_id": str(row.id),
                "row_data": {
                    "persona": {"persona": "Row-data persona", "traits": "calm, terse"},
                    "goal": "Row-data goal",
                    "situation": "Row-data situation",
                    "branch": "Row-data branch",
                },
                "harness_scenario_key": "env-key",
                "sub_goals": ["Metadata goal"],
            },
            "eval_outputs": {
                gate_id: {"status": "failed", "output": None},
                score_id: {"status": "completed", "output": {"score": 40}},
            },
            "conversation_metrics_data": {"bot_message_count": 3, "avg_latency_ms": 99},
            "started_at": now - timedelta(minutes=2),
        },
        {
            "scenario": plain,
            "simulation_call_type": "voice",
            "status": CallExecution.CallStatus.FAILED,
            "call_metadata": "not an object",
            "eval_outputs": None,
            "error_message": "boom",
            "started_at": now - timedelta(minutes=1),
        },
        {
            "scenario": rich,
            "simulation_call_type": "voice",
            "status": CallExecution.CallStatus.ONGOING,
            "call_metadata": ["not", "an", "object"],
            "started_at": now,
        },
        {
            "scenario": plain,
            "simulation_call_type": "voice",
            "status": CallExecution.CallStatus.COMPLETED,
            "call_metadata": {
                "goal": "Metadata goal",
                "harness_outcome_status": "error",
            },
            "provider_call_data": {"vapi": {}, "retell": {"call": 1}},
            "eval_outputs": {gate_id: {"status": "completed", "output": "Failed"}},
            "started_at": now - timedelta(minutes=4),
        },
        {
            "scenario": plain,
            "simulation_call_type": "voice",
            "status": CallExecution.CallStatus.COMPLETED,
            "call_metadata": {"persona": '{"name": "Json persona", "voice": "warm"}'},
            "started_at": now - timedelta(minutes=5),
        },
        {
            "scenario": rich,
            "row_id": row.id,
            "simulation_call_type": "text",
            "status": CallExecution.CallStatus.COMPLETED,
            "call_metadata": {},
            "eval_outputs": {},
            "started_at": now - timedelta(minutes=6),
        },
    ]
    calls = [
        CallExecution.objects.create(test_execution=execution, **spec) for spec in specs
    ]
    linked = HostedHarnessScenario.no_workspace_objects.create(
        job=own_job,
        scenario_key="linked-key",
        branch="Linked branch",
        call_execution=calls[5],
        sub_goals=["Linked goal"],
    )
    HostedHarnessExecution.all_objects.create(
        job=own_job,
        source_scenario=linked,
        execution_key="x",
        trial_index=0,
        call_execution=calls[4],
    )
    execution = TestExecution.objects.select_related(
        "run_test", "agent_definition", "agent_version", "run_test__agent_version"
    ).get(id=execution.id)
    return execution, calls, environment, own_job


def _page(calls: list[CallExecution], goals: dict[int, str] | None = None) -> dict:
    goals = goals or {}
    rows = [
        {"id": str(call.id), "result_goal": goals.get(index)}
        for index, call in enumerate(calls)
    ]
    return {"rows": rows, "page_ids": [row["id"] for row in rows]}


@pytest.mark.django_db
@pytest.mark.parametrize("goals", [{}, {1: "Coalesced goal"}])
def test_slim_page_renders_the_same_rows_and_columns(run, goals):
    execution, calls, _, _ = run
    page = _page(calls, goals)
    columns, live_eval_ids = build_evaluation_catalog(execution)

    whole = _whole_calls(page)
    slim = page_calls(page, execution)
    with CaptureQueriesContext(connection) as old_queries:
        old_rows, old_columns = build_call_rows(
            execution, whole, columns, live_eval_ids
        )
    with CaptureQueriesContext(connection) as new_queries:
        new_rows, new_columns = build_call_rows(execution, slim, columns, live_eval_ids)

    assert _dumps(new_rows) == _dumps(old_rows)
    assert _dumps(new_columns) == _dumps(old_columns)
    # No deferred field is fetched per call while building rows.
    assert len(new_queries) == len(old_queries)
    assert len(old_rows) == len(calls)
    by_id = {row["id"]: row for row in new_rows}
    assert by_id[str(calls[0].id)]["provider"] == "vapi"
    assert by_id[str(calls[4].id)]["provider"] == "retell"
    assert by_id[str(calls[2].id)]["provider"] == "vapi"
    assert by_id[str(calls[1].id)]["goal"] == goals.get(1, "Row-data goal")
    # Dataset cells fill what call metadata leaves out.
    assert by_id[str(calls[6].id)]["goal"] == "Row use case"
    assert by_id[str(calls[6].id)]["persona"] == "Row persona"
    assert by_id[str(calls[6].id)]["scenario_details"] == "Row situation"
    assert by_id[str(calls[6].id)]["conversation_branch"] == "Row branch"
    assert by_id[str(calls[0].id)]["sub_goal_results"][1] == {
        "name": "Verifies",
        "passed": None,
    }
    assert by_id[str(calls[0].id)]["conversation_branch"] == "Own branch"
    assert by_id[str(calls[1].id)]["conversation_branch"] == "Environment branch"


@pytest.mark.django_db
def test_slim_page_loads_one_query_and_only_read_metadata_keys(run):
    execution, calls, _, _ = run
    page = _page(calls)
    with CaptureQueriesContext(connection) as queries:
        slim = page_calls(page, execution)
    assert len(queries) == 1
    assert "execution_metadata" not in queries[0]["sql"]

    by_id = {call.id: call for call in slim}
    # The subset sits beside call_metadata, which stays unloaded for a save.
    assert all("call_metadata" in call.get_deferred_fields() for call in slim)
    assert set(by_id[calls[0].id].row_metadata) <= set(CALL_ROW_METADATA_KEYS)
    assert "system_prompt" not in by_id[calls[0].id].row_metadata
    assert by_id[calls[0].id].row_metadata["hosted_harness_receipt"] == (
        calls[0].call_metadata["hosted_harness_receipt"]
    )
    assert by_id[calls[2].id].row_metadata == "not an object"
    assert by_id[calls[3].id].row_metadata == ["not", "an", "object"]
    assert by_id[calls[5].id].row_metadata == calls[5].call_metadata
    assert all(call.test_execution is execution for call in slim)
    assert [str(call.id) for call in slim] == page["page_ids"]


@pytest.mark.django_db
def test_slim_page_skips_missing_ids_and_keeps_page_order(run):
    execution, calls, _, _ = run
    page = _page(list(reversed(calls)))
    page["page_ids"].insert(1, str(uuid.uuid4()))
    page["rows"].insert(1, {"id": page["page_ids"][1], "result_goal": None})
    assert [call.id for call in page_calls(page, execution)] == [
        call.id for call in reversed(calls)
    ]


@pytest.mark.django_db
def test_slim_page_leaves_another_runs_call_to_its_own_run(
    run, organization, workspace
):
    execution, calls, _, _ = run
    other = TestExecution.objects.create(run_test=execution.run_test)
    foreign = CallExecution.objects.create(
        test_execution=other, scenario=calls[0].scenario, call_metadata={}
    )
    slim = page_calls(_page([foreign]), execution)
    assert slim[0].test_execution_id == other.id
    assert slim[0].test_execution.agent_definition is None


@pytest.mark.django_db
def test_row_dimensions_match_the_whole_cell_read(run):
    _, calls, _, _ = run
    extra = {str(row_id) for row_id in Row.objects.values_list("id", flat=True)}
    expected = _reference_row_dimensions(calls, extra)
    assert _dumps(_row_dimensions(calls, extra)) == _dumps(expected)
    assert _row_dimensions(calls, extra)[str(calls[0].row_id)]["goal"] == "Older goal"
    assert _row_dimensions([calls[2]]) == {}


@pytest.mark.django_db
@pytest.mark.parametrize("scoped", [True, False])
def test_authored_scenarios_match_the_join_query(run, scoped):
    execution, calls, _, _ = run
    options = {"test_execution_id": execution.id} if scoped else {}
    for subset in (calls, calls[:1], calls[1:2], calls[2:4], []):
        assert _ids(
            authored_scenarios_for_calls(execution.run_test_id, subset, **options)
        ) == _ids(_reference_authored(execution.run_test_id, subset, **options))


@pytest.mark.django_db
def test_authored_scenarios_without_a_job_or_keys(run):
    execution, calls, _, _ = run
    sibling = TestExecution.objects.create(run_test=execution.run_test)
    for test_execution_id in (sibling.id, uuid.uuid4()):
        for subset in (calls, calls[2:4]):
            resolved = authored_scenarios_for_calls(
                execution.run_test_id, subset, test_execution_id=test_execution_id
            )
            assert _ids(resolved) == _ids(
                _reference_authored(
                    execution.run_test_id, subset, test_execution_id=test_execution_id
                )
            )
    # Only the call-linked scenario survives without a job in scope.
    resolved = authored_scenarios_for_calls(
        execution.run_test_id, calls, test_execution_id=sibling.id
    )
    assert [scenario is not None for scenario in resolved.values()] == [
        False,
        False,
        False,
        False,
        False,
        True,
        False,
    ]


@pytest.mark.django_db
def test_authored_scenarios_keep_soft_deleted_jobs_in_scope(run):
    execution, calls, environment, own_job = run
    HostedHarnessJob.all_objects.filter(id__in=[environment.id, own_job.id]).update(
        deleted=True
    )
    options = {"test_execution_id": execution.id}
    resolved = authored_scenarios_for_calls(execution.run_test_id, calls, **options)
    assert _ids(resolved) == _ids(
        _reference_authored(execution.run_test_id, calls, **options)
    )
    assert resolved[calls[1].id].branch == "Environment branch"
