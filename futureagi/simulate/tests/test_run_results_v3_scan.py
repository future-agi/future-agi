"""The one-pass call scan returns exactly the rows the SQL read model derives."""

import itertools
import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from model_hub.models.choices import DatasetSourceChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import CallExecution, Scenarios, SimulateEvalConfig, TestExecution
from simulate.models.hosted_harness import HostedHarnessJob, HostedHarnessScenario
from simulate.models.run_test import RunTest
from simulate.services import run_results_v3_page as page_module
from simulate.services import run_results_v3_scan as scan_module
from simulate.services.run_results_v3_page import (
    CALL_VALUE_FIELDS,
    SQL_ORDERED_FIELDS,
    _call_values,
    _score_expressions,
    run_calls_page,
)
from simulate.services.run_results_v3_queries import (
    apply_run_call_query,
    run_call_facets,
    run_calls_queryset,
)
from simulate.services.run_results_v3_scan import scan_call_values
from simulate.views.run_results_v3 import RunCallsV3QuerySerializer


def _assert_identical(actual: Any, expected: Any, path: str = "") -> None:
    assert type(actual) is type(expected), (path, actual, expected)
    if isinstance(expected, dict):
        assert list(actual) == list(expected), path
        for key in expected:
            _assert_identical(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert len(actual) == len(expected), path
        for index, (one, other) in enumerate(zip(actual, expected, strict=True)):
            _assert_identical(one, other, f"{path}[{index}]")
    else:
        assert actual == expected, (path, actual, expected)


def _orm_rows(
    execution: TestExecution, columns: list[dict[str, str]], ordering: str | None
) -> list[dict[str, Any]]:
    queryset = run_calls_queryset(execution)
    if ordering is not None:
        queryset = apply_run_call_query(queryset, {"ordering": ordering})
    return _call_values(queryset, columns, _score_expressions(execution, columns))


def _assert_parity(execution: TestExecution, columns: list[dict[str, str]]) -> None:
    for ordering in ("-started_at", "started_at"):
        _assert_identical(
            scan_call_values(execution, columns, ordering),
            _orm_rows(execution, columns, ordering),
        )
    # The model's default order breaks no ties, so compare it by call.
    by_id = {row["id"]: row for row in _orm_rows(execution, columns, None)}
    scanned = scan_call_values(execution, columns, "-updated_at")
    assert len(scanned) == len(by_id)
    for row in scanned:
        _assert_identical(row, by_id[row["id"]])


@pytest.fixture
def run(organization, workspace):
    run_test = RunTest.objects.create(
        name="Scan run", organization=organization, workspace=workspace
    )
    execution = TestExecution.objects.create(
        run_test=run_test, status=TestExecution.ExecutionStatus.COMPLETED
    )
    scenario = Scenarios.objects.create(
        name="Scan scenario",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.SCRIPT,
        organization=organization,
        workspace=workspace,
    )
    return execution, scenario


def _config(execution: TestExecution, organization, name: str, **template) -> str:
    binding = template.pop("binding", {})
    eval_template = EvalTemplate.objects.create(
        name=name, organization=organization, **template
    )
    return str(
        SimulateEvalConfig.objects.create(
            name=name,
            run_test=execution.run_test,
            eval_template=eval_template,
            config=binding,
        ).id
    )


ENTRIES = [
    None,
    {"status": "completed", "output": True},
    {"status": "completed", "output": False},
    {"status": "completed", "output": "Passed"},
    {"status": "completed", "output": " FAILED ", "output_type": "Pass/Fail"},
    {"status": "completed", "output": "passed", "output_type": " pass_fail "},
    {"status": "completed", "output": {"failure": False}},
    {"status": "completed", "output": {"failure": True}, "output_type": "pass/fail"},
    {"status": "completed", "output": {"failure": 0}, "output_type": "pass/fail"},
    {"status": "completed", "output": 0.8},
    {"status": "completed", "output": 80},
    {"status": "completed", "output": "65"},
    {"status": "completed", "output": " 0.3"},
    {"status": "completed", "output": "+1e-1"},
    {"status": "completed", "output": -2},
    {"status": "completed", "output": "yes"},
    {"status": "completed", "output": "No"},
    {"status": "completed", "output": "success"},
    {"status": "completed", "output": "safe"},
    {"status": "completed", "output": " Unsafe "},
    {"status": "completed", "output": ["SAFE", "maybe", "safe"]},
    {"status": "completed", "output": [["safe"]]},
    {"status": "completed", "output": [[["maybe"]]]},
    {"status": "completed", "output": [[[["maybe"]]]]},
    {"status": "completed", "output": ["x", [["Unsafe"]], 3, {"a": "safe"}]},
    {"status": "completed", "output": {"choice": "safe", "choices": [["maybe"]]}},
    {"status": "Completed", "output": 0.5},
    {"status": "completed", "output": 0.7},
    {"status": "completed", "output": 70},
    {"status": " Failed", "output": "passed"},
    {"status": "completed", "output": {"score": "abc"}},
    {"status": "completed", "output": {"failure": "true"}, "output_type": "pass/fail"},
    {"status": "completed", "output": {"choices": ["safe ", "\tunsafe"]}},
    {"status": "completed", "output": {"choice": "maybe"}},
    {"status": "completed", "output": {"choice": "maybe", "choices": []}},
    {"status": "completed", "output": {"choice": "nothing", "choices": ["maybe"]}},
    {"status": "completed", "output": {"score": 0.9}},
    {"status": "completed", "output": {"score": "0.9"}},
    {"status": "completed", "output": {"result": "pass"}},
    {"status": "completed", "output": {"value": 3, "score": None}},
    {"status": "completed", "output": {"label": "x"}},
    {"status": "completed", "output": {}},
    {"status": "completed", "output": []},
    {"status": "completed", "output": None},
    {"status": "completed", "output": ""},
    {"status": "completed"},
    {"output": 0.75},
    {"status": "", "output": "true"},
    {"status": " ERROR ", "output": "passed"},
    {"status": "failed", "output": 1},
    {"status": "Pending", "output": 1},
    {"status": "skipped", "output": "passed"},
    {"status": None, "output": "failed"},
    "passed",
    5,
    ["passed"],
    True,
    None,
]
NATIVE = [
    None,
    {"source": "harness", "status": "completed", "output": "PASS "},
    {"source": "harness", "status": "completed", "output": False},
    {"source": "harness", "status": "error", "output": "pass"},
    {"source": "harness", "status": "completed", "output": 1},
    {"source": "harness", "output": "unsuccessful"},
    {"source": "other", "status": "completed", "output": "fail"},
    {"source": "harness", "status": "skipped", "output": "fail"},
    "harness",
]


@pytest.mark.django_db
@pytest.mark.parametrize("bounded", [False, True], ids=["default", "bounded"])
def test_scored_calls_match_the_sql_read_model(run, organization, monkeypatch, bounded):
    execution, scenario = run
    if bounded:
        # Small batches and memos that start over often must not change a row.
        monkeypatch.setattr(scan_module, "_SCAN_BATCH_SIZE", 3)
        monkeypatch.setattr(scan_module, "_MEMO_TEXT_LIMIT", 64)
    eval_ids = [
        _config(
            execution,
            organization,
            "pass fail",
            output_type_normalized="pass_fail",
            pass_threshold=0.5,
        ),
        _config(
            execution,
            organization,
            "reversed percentage",
            output_type_normalized="percentage",
            pass_threshold=0.7,
            binding={"reverse_output": True},
        ),
        _config(
            execution,
            organization,
            "choices",
            output_type_normalized="deterministic",
            pass_threshold=0.5,
            choice_scores={"safe": 1.0, "unsafe": 0.0, "maybe": 0.4},
        ),
        _config(
            execution,
            organization,
            "reversed choices",
            output_type_normalized="deterministic",
            pass_threshold=0.5,
            choice_scores={"safe": 2, "unsafe": -1},
            binding={"reverse_output": True},
        ),
        _config(
            execution,
            organization,
            "no threshold",
            output_type_normalized="percentage",
            binding={"pass_threshold": "bad"},
        ),
        _config(
            execution,
            organization,
            "no threshold pass fail",
            output_type_normalized="pass_fail",
            binding={"pass_threshold": True},
        ),
    ]
    columns = [{"id": eval_id} for eval_id in [*eval_ids, "native-check", "loose"]]
    statuses = ["completed"] * 5 + ["failed", "pending", "ongoing", "cancelled"]
    harness_statuses = [None, None, "failed", "Passed", "passed", "error", "unknown", 1]
    started = timezone.now()
    for index in range(len(ENTRIES)):
        for offset in range(2):
            outputs = {
                eval_id: ENTRIES[(index + offset * 7 + position * 5) % len(ENTRIES)]
                for position, eval_id in enumerate(eval_ids)
            }
            outputs["native-check"] = NATIVE[(index + offset) % len(NATIVE)]
            outputs["loose"] = ENTRIES[(index * 3 + offset) % len(ENTRIES)]
            outputs = {
                key: value
                for key, value in outputs.items()
                if value is not None or (index + offset) % 4 == 0
            }
            call_number = index * 2 + offset
            CallExecution.objects.create(
                test_execution=execution,
                scenario=scenario,
                status=statuses[call_number % len(statuses)],
                # Repeated and missing start times exercise the id tie-break.
                started_at=(
                    None
                    if call_number % 11 == 0
                    else started - timedelta(seconds=call_number // 3)
                ),
                call_metadata={
                    "harness_outcome_status": harness_statuses[
                        call_number % len(harness_statuses)
                    ]
                },
                eval_outputs=outputs,
            )
    _assert_parity(execution, columns)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "eval_outputs",
    [None, {}, [], "text", 3, ["{eval_id}"], ["other"], "{eval_id}"],
)
def test_eval_outputs_that_are_not_objects_match(run, organization, eval_outputs):
    execution, scenario = run
    eval_id = _config(
        execution,
        organization,
        "choices",
        output_type_normalized="deterministic",
        pass_threshold=0.5,
        choice_scores={"safe": 1.0},
    )
    if isinstance(eval_outputs, str):
        eval_outputs = eval_outputs.format(eval_id=eval_id)
    elif isinstance(eval_outputs, list):
        eval_outputs = [item.format(eval_id=eval_id) for item in eval_outputs]
    CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status="completed",
        eval_outputs=eval_outputs,
    )
    _assert_parity(execution, [{"id": eval_id}, {"id": "loose"}])


@pytest.mark.django_db
def test_run_without_configs_or_calls_matches(run):
    execution, scenario = run
    assert scan_call_values(execution, []) == []
    CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status="completed",
        eval_outputs={"native": {"source": "harness", "output": "pass"}},
    )
    _assert_parity(execution, [])
    _assert_parity(execution, [{"id": "native"}])


@pytest.mark.django_db
def test_metrics_and_goals_match(run, organization, workspace, user):
    execution, scenario = run
    dataset = Dataset.no_workspace_objects.create(
        name="Scan dataset",
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
        for name in ("use_case", "goal", "other")
    }

    def dataset_row(*cells):
        row = Row.objects.create(dataset=dataset, order=0)
        for name, value in cells:
            Cell.objects.create(
                dataset=dataset, column=columns[name], row=row, value=value
            )
        return row.id

    described = Scenarios.objects.create(
        name="Described",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.SCRIPT,
        organization=organization,
        workspace=workspace,
        metadata={"use_case": "", "goal": "Scenario goal"},
    )
    unnamed = Scenarios.objects.create(
        name="",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.SCRIPT,
        organization=organization,
        workspace=workspace,
    )
    calls = [
        ({"use_case": "Metadata use case", "goal": "Metadata goal"}, None, scenario),
        ({"use_case": "", "goal": "Metadata goal"}, None, scenario),
        ({"row_data": {"use_case": "", "goal": "Row goal"}}, None, scenario),
        (
            {},
            dataset_row(("goal", "Cell goal"), ("use_case", "Cell use case")),
            scenario,
        ),
        ({}, dataset_row(("goal", "Cell goal"), ("other", "Other")), scenario),
        ({}, dataset_row(("use_case", "")), described),
        ({}, dataset_row(("use_case", None), ("goal", "Later")), described),
        ({}, dataset_row(), described),
        ({}, uuid.uuid4(), unnamed),
        ({}, None, unnamed),
    ]
    metrics = [
        ({"avg_latency_ms": "12.5", "turn_count": 4, "csat_score": 7.5}, None, None),
        ({"avg_latency_ms": "abc", "bot_message_count": 3, "csat_score": 11}, 250, 13),
        ({"turn_count": "", "bot_message_count": "2", "total_tokens": 1234}, None, 7),
        ({"total_tokens": "1e3", "csat_score": "-1"}, 9, None),
        ({"total_tokens": " 12", "csat_score": "10"}, None, None),
        (None, None, None),
    ]
    for index, (metadata, row_id, call_scenario) in enumerate(calls):
        conversation, latency, cost = metrics[index % len(metrics)]
        CallExecution.objects.create(
            test_execution=execution,
            scenario=call_scenario,
            status="completed",
            call_metadata=metadata,
            row_id=row_id,
            duration_seconds=index * 10 or None,
            avg_agent_latency_ms=latency,
            customer_cost_cents=cost,
            avg_stop_time_after_interruption_ms=index or None,
            ai_interruption_count=index % 3 or None,
            conversation_metrics_data=conversation,
        )
    _assert_parity(execution, [])


def _harness_job(organization, workspace, **fields) -> HostedHarnessJob:
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


@pytest.mark.django_db
def test_authored_scenarios_match(run, organization, workspace):
    execution, scenario = run
    sibling = TestExecution.objects.create(run_test=execution.run_test)
    environment = _harness_job(organization, workspace)
    own = _harness_job(
        organization,
        workspace,
        environment=environment,
        run_test=execution.run_test,
        test_execution=execution,
    )
    other = _harness_job(
        organization, workspace, run_test=execution.run_test, test_execution=sibling
    )

    def call(key: str | None) -> CallExecution:
        return CallExecution.objects.create(
            test_execution=execution,
            scenario=scenario,
            status="completed",
            call_metadata={"harness_scenario_key": key} if key is not None else {},
        )

    def authored(job, key, **fields) -> HostedHarnessScenario:
        return HostedHarnessScenario.no_workspace_objects.create(
            job=job, scenario_key=key, **fields
        )

    authored(
        environment,
        "shared",
        use_case="Environment",
        persona={"accent": "Scottish"},
        sub_goals=["older"],
    )
    authored(
        own,
        "shared",
        use_case="Own",
        persona={"accent": "", "age_group": "adult"},
        coverage={"overlay": "jailbreak", "task": "refund"},
        sub_goals=["book", {"name": "pay"}],
    )
    authored(environment, "environment-only", use_case="", sub_goals=None)
    authored(other, "sibling-only", use_case="Sibling")
    authored(environment, "newest", use_case="Environment newest")
    authored(own, "newest", use_case="Own", persona=["not", "object"])
    linked_call = call("shared")
    authored(
        other,
        "linked",
        call_execution=linked_call,
        use_case="Linked",
        coverage={"task": "linked task"},
        sub_goals=[],
    )
    for key in ("shared", "environment-only", "sibling-only", "newest", "", None):
        call(key)
    _assert_parity(execution, [])


@pytest.mark.parametrize("rank", ["linked", "own_job", "environment"])
def test_newest_authored_scenario_wins_within_a_rank(rank):
    # The schema allows one row per rank for a call, so the rows are built here;
    # the SQL read model orders them by ``match_rank, -created_at``.
    call_id, own_job, environment = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    job = environment if rank == "environment" else own_job
    linked = call_id if rank == "linked" else None
    now = timezone.now()
    # (id, job, key, call, created_at, use_case, sub_goals, accent, age, attack, task)
    older = (uuid.uuid4(), job, "repeated", linked, now - timedelta(hours=1))
    older += ("Older", '["older"]', "Welsh", "adult", "prompt_injection", "booking")
    newer = (uuid.uuid4(), job, "repeated", linked, now)
    newer += ("Newer", '["newer"]', "Irish", "senior", "jailbreak", "refund")

    # The older row comes first, so row order alone cannot pick the newer one.
    authored = scan_module._AuthoredScenarios(
        [older, newer], {own_job}, {own_job, environment}
    )

    assert authored.pick(call_id, "repeated") == newer


@pytest.mark.django_db
def test_rows_keep_the_call_value_shape(run, organization):
    execution, scenario = run
    eval_id = _config(
        execution, organization, "shape", output_type_normalized="percentage"
    )
    CallExecution.objects.create(
        test_execution=execution, scenario=scenario, status="completed"
    )
    (row,) = scan_call_values(execution, [{"id": eval_id}])
    assert list(row) == [*dict.fromkeys(CALL_VALUE_FIELDS), "scores"]
    assert list(row["scores"]) == [eval_id]


def _page(execution, query, monkeypatch=None, scan=True):
    if not scan:
        monkeypatch.setattr(page_module, "_scan_ordering", lambda query: None)
        monkeypatch.setattr(
            page_module,
            "scan_call_values",
            lambda execution, columns, ordering: _orm_rows(execution, columns, None),
        )
    return run_calls_page(
        execution,
        {"page": 1, "page_size": 3, **query},
        [],
        lambda: run_calls_queryset(execution),
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "query",
    [
        {},
        {"ordering": "started_at", "search": "  ", "filters": {"goal": []}},
        {"group_by": "goal"},
        {"search": "Scan"},
        {"filters": {"status": ["passed"]}},
        {"group_by": "status", "group_key": "inconclusive"},
        {"ordering": "-duration_seconds"},
    ],
)
def test_page_matches_the_sql_read_model(run, monkeypatch, query):
    execution, scenario = run
    execution.status = TestExecution.ExecutionStatus.RUNNING
    execution.save(update_fields=["status"])
    started = timezone.now()
    for index, status in zip(range(7), itertools.cycle(["completed", "failed"])):
        CallExecution.objects.create(
            test_execution=execution,
            scenario=scenario,
            status=status,
            started_at=started - timedelta(seconds=index // 2),
            duration_seconds=index,
        )
    scanned = _page(execution, query)
    with monkeypatch.context() as patched:
        expected = _page(execution, query, patched, scan=False)
    for key in ("rows", "page_ids", "summary", "execution_summary", "count"):
        _assert_identical(scanned[key], expected[key], key)


@pytest.mark.parametrize(
    "query,ordering",
    [
        ({}, "-started_at"),
        ({"ordering": "started_at", "filters": {"goal": []}}, "started_at"),
        ({"group_by": "goal", "group_key": None}, "-started_at"),
        ({"search": " x "}, None),
        ({"filters": {"call_execution_id": ["x"]}}, None),
        ({"group_by": "goal", "group_key": ""}, None),
        ({"ordering": "goal"}, None),
    ],
)
def test_scan_serves_only_unfiltered_start_time_orderings(query, ordering):
    assert page_module._scan_ordering(query) == ordering


BOUNDARY_CONFIGS = [
    ("deterministic", 0.15, False, {"a": 0.1, "b": 0.2}, [["a", "b"]]),
    ("deterministic", 0.45, False, {"a": 0.7, "b": 0.2}, [["a", "b"], ["b"]]),
    ("deterministic", 0.45, True, {"a": 0.7, "b": 0.2}, [["a", "b"]]),
    (
        "deterministic",
        0.3666666666666667,
        False,
        {"a": 0.3, "b": 0.7, "c": 0.1},
        [["a", "b", "c"], {"choices": ["c", "a"]}],
    ),
    ("deterministic", 0.4, False, {"a": 0.4}, ["a", {"choice": "a"}]),
    ("percentage", 0.7, False, {}, [0.7, 70, "0.7", 0.69]),
    ("percentage", 0.3, True, {}, [0.7, 0.3, 30]),
    ("pass_fail", 0.5, False, {}, [0, 1, "pass"]),
    ("pass_fail", 0.5, True, {}, [0, 1]),
]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "output_type,threshold,reverse,choices,outputs", BOUNDARY_CONFIGS
)
def test_scores_on_the_threshold_match(
    run, organization, output_type, threshold, reverse, choices, outputs
):
    execution, scenario = run
    eval_id = _config(
        execution,
        organization,
        "boundary",
        output_type_normalized=output_type,
        pass_threshold=threshold,
        choice_scores=choices,
        binding={"reverse_output": reverse},
    )
    for output in outputs:
        CallExecution.objects.create(
            test_execution=execution,
            scenario=scenario,
            status="completed",
            eval_outputs={eval_id: {"status": "completed", "output": output}},
        )
    _assert_parity(execution, [{"id": eval_id}])


@pytest.mark.django_db
@pytest.mark.parametrize(
    "choices,threshold,score,outcome",
    [
        ({"a": 0.1, "b": 0.2}, 0.15, 0.15, "passed"),
        ({"a": 0.7, "b": 0.2}, 0.45, 0.45, "passed"),
        # Rounded half away from zero, not truncated.
        ({"a": 1e-20, "b": 1e-20, "c": 3e-20}, 0.5, 1.6666666666666668e-20, "failed"),
        # Divided at numeric's 16 significant digits.
        ({"a": 0.0097, "b": 0.0035, "c": 0.2603}, 0.5, 0.09116666666666666, "failed"),
    ],
)
def test_choice_means_use_numeric_arithmetic(
    run, organization, choices, threshold, score, outcome
):
    execution, scenario = run
    eval_id = _config(
        execution,
        organization,
        "numeric mean",
        output_type_normalized="deterministic",
        pass_threshold=threshold,
        choice_scores=choices,
    )
    CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status="completed",
        eval_outputs={eval_id: {"status": "completed", "output": list(choices)}},
    )
    (row,) = scan_call_values(execution, [{"id": eval_id}])
    assert row["scores"][eval_id] == score
    assert row["result_outcome"] == outcome
    _assert_parity(execution, [{"id": eval_id}])


# The ORM path scores in SQL and the scan ports those rules to Python. This
# corpus is the guard that keeps the two in sync: it crosses every stored entry
# shape with every scoring spec, so a rule changed on one side only fails here.
_ABSENT: Any = object()
CORPUS_STATUSES = [
    _ABSENT,
    None,
    "",
    "completed",
    "Completed",
    " pending ",
    "\tpending",
    "skipped",
    "error",
    "\terror",
    "Failed",
    "failed",
]
CORPUS_OUTPUTS = [
    _ABSENT,
    None,
    *("pass", " PASS", "Passed", "fail", "FAILED ", "failed", "true", "False"),
    *("yes", "No", "success", "unsuccessful", "safe", " Unsafe ", "maybe", ""),
    *("SAFE\u3000", "\u2028unsafe\u2000", "safe\u00a0"),
    "abc",
    True,
    False,
    *(0, 1, 0.5, 0.3, 0.69, 80, 50, -2),
    *("0.5", " 65", "+1e-1", "-3"),
    {"score": 0.9},
    {"score": "0.3"},
    {"result": "pass"},
    {"output": "fail"},
    {"choice": "safe"},
    {"value": 70},
    {"score": None, "value": 3},
    {"label": "x"},
    ["safe", "maybe"],
    [["unsafe"]],
    [[[["maybe"]]]],
    ["PASS"],
    [],
    {"choices": ["safe ", "\tunsafe"]},
    {"choices": [["maybe"], "unsafe"]},
    {"choice": "maybe", "choices": []},
    {"failure": True},
    {"failure": False},
]
CORPUS_OUTPUT_TYPES = [
    _ABSENT,
    "pass/fail",
    "pass_fail",
    "Pass_Fail",
    "percentage",
    "x",
]
CORPUS_SOURCES = [_ABSENT, "harness", "other"]
# (name, template fields) per scoring spec; thresholds sit on corpus outputs.
CORPUS_SPECS = [
    ("pass fail", {"output_type_normalized": "pass_fail", "pass_threshold": 0.5}),
    (
        "pass fail without threshold",
        {"output_type_normalized": "pass_fail", "binding": {"pass_threshold": True}},
    ),
    ("percentage", {"output_type_normalized": "percentage", "pass_threshold": 0.5}),
    (
        "percentage without threshold",
        {"output_type_normalized": "percentage", "binding": {"pass_threshold": "x"}},
    ),
    (
        "reversed percentage",
        {
            "output_type_normalized": "percentage",
            "pass_threshold": 0.3,
            "binding": {"reverse_output": True},
        },
    ),
    (
        "choices",
        {
            "output_type_normalized": "deterministic",
            "pass_threshold": 0.5,
            "choice_scores": {"safe": 1.0, "unsafe": 0.0, "maybe": 0.5},
        },
    ),
    (
        "reversed choices",
        {
            "output_type_normalized": "deterministic",
            "pass_threshold": 0.5,
            "choice_scores": {"safe": 2, "unsafe": -1},
            "binding": {"reverse_output": True},
        },
    ),
]


def _corpus_entries(output_type: Any) -> list[dict[str, Any]]:
    entries = []
    for status, output, source in itertools.product(
        CORPUS_STATUSES, CORPUS_OUTPUTS, CORPUS_SOURCES
    ):
        fields = {
            "status": status,
            "output": output,
            "output_type": output_type,
            "source": source,
        }
        entries.append({k: v for k, v in fields.items() if v is not _ABSENT})
    return entries


@pytest.mark.django_db
@pytest.mark.parametrize(
    "output_type",
    CORPUS_OUTPUT_TYPES,
    ids=lambda value: "absent" if value is _ABSENT else repr(value),
)
def test_every_stored_entry_shape_scores_like_the_sql_read_model(
    run, organization, output_type
):
    execution, scenario = run
    eval_ids = [
        _config(execution, organization, name, **dict(template))
        for name, template in CORPUS_SPECS
    ]
    # Unconfigured keys, scored natively and judged as harness checks.
    columns = [{"id": eval_id} for eval_id in [*eval_ids, "native", "loose"]]
    entries = _corpus_entries(output_type)
    calls: list[tuple[str, Any, Any]] = [
        ("completed", None, outputs)
        for outputs in (None, {}, [], "text", [eval_ids[0]], eval_ids[0], 3)
    ]
    for index, entry in enumerate(entries):
        outputs = dict.fromkeys(eval_ids, entry)
        # Shifted entries mix verdicts across keys within a call.
        outputs["native"] = entries[(index * 7 + 3) % len(entries)]
        outputs["loose"] = entries[(index * 11 + 5) % len(entries)]
        calls.append(("completed", None, outputs))
    # The lifecycle and harness outcome around a sample of the entries.
    lifecycle = itertools.product(
        ["completed", "failed", "pending", "queued", "ongoing", "analyzing", "x"],
        [None, "failed", " Passed", "passed", "error", "unknown", 1],
    )
    for index, (status, harness_status) in enumerate(lifecycle):
        outputs = {key: entries[index * 13 % len(entries)] for key in eval_ids[:3]}
        outputs["native"] = entries[index * 17 % len(entries)]
        calls.append((status, harness_status, outputs))
    started = timezone.now()
    CallExecution.objects.bulk_create(
        CallExecution(
            test_execution=execution,
            scenario=scenario,
            status=status,
            started_at=started - timedelta(seconds=index // 2),
            call_metadata={"harness_outcome_status": harness_status},
            eval_outputs=outputs,
        )
        for index, (status, harness_status, outputs) in enumerate(calls)
    )
    _assert_identical(
        scan_call_values(execution, columns, "-started_at"),
        _orm_rows(execution, columns, "-started_at"),
    )


ORDERING_CHOICES = list(RunCallsV3QuerySerializer().fields["ordering"].choices)


@pytest.mark.django_db
def test_scan_rejects_an_unknown_ordering(run):
    execution, _ = run
    with pytest.raises(ValueError, match="-started_at"):
        scan_call_values(execution, [], "name")


def test_every_accepted_ordering_is_scanned_or_left_to_sql():
    assert {choice.lstrip("-") for choice in ORDERING_CHOICES} == (
        SQL_ORDERED_FIELDS | {"started_at"}
    )


@pytest.mark.django_db
def test_orderings_the_sql_path_sorts_by_are_never_scanned(run):
    execution, _ = run
    queryset = run_calls_queryset(execution)
    probes = {
        *ORDERING_CHOICES,
        *(field.name for field in CallExecution._meta.get_fields()),
        *queryset.query.annotations,
    }
    for ordering in sorted(probes):
        for signed in (ordering, f"-{ordering}"):
            first = apply_run_call_query(queryset, {"ordering": signed}).query.order_by
            sorted_by = first[0].lstrip("-")
            scanned = page_module._scan_ordering({"ordering": signed})
            if sorted_by == "started_at":
                expected = "-started_at" if signed.startswith("-") else "started_at"
                assert scanned == expected, signed
            else:
                assert scanned is None, signed
                assert signed.lstrip("-") in SQL_ORDERED_FIELDS, signed


@pytest.mark.django_db
def test_every_accepted_ordering_pages_like_the_sql_read_model(
    run, organization, workspace, monkeypatch
):
    execution, scenario = run
    # A running run is never cached, so both paths read their own rows.
    execution.status = TestExecution.ExecutionStatus.RUNNING
    execution.save(update_fields=["status"])
    other = Scenarios.objects.create(
        name="Another scenario",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.SCRIPT,
        organization=organization,
        workspace=workspace,
    )
    started = timezone.now()
    statuses = ["completed", "failed", "pending", "ongoing"]
    for index in range(9):
        CallExecution.objects.create(
            test_execution=execution,
            scenario=other if index % 2 else scenario,
            status=statuses[index % len(statuses)],
            # Repeated and missing start times exercise the tie-break.
            started_at=started - timedelta(seconds=index // 3) if index % 4 else None,
            duration_seconds=None if index % 3 == 0 else index % 4,
            customer_cost_cents=index % 2,
            avg_agent_latency_ms=index % 5 or None,
            conversation_metrics_data={
                "turn_count": index % 3,
                "total_tokens": index * 7 % 4,
                "csat_score": index % 4 * 2,
            },
            call_metadata={"goal": f"Goal {index % 3}"},
        )
    for ordering in ORDERING_CHOICES:
        for page in (1, 3):
            query = {"ordering": ordering, "page": page}
            scanned = _page(execution, query)
            with monkeypatch.context() as patched:
                expected = _page(execution, query, patched, scan=False)
            for key in ("rows", "page_ids", "count"):
                _assert_identical(scanned[key], expected[key], f"{ordering}.{key}")


def _facets(rows: list[dict[str, Any]], execution: TestExecution) -> dict[str, Any]:
    return run_call_facets(
        lambda: rows, lambda: CallExecution.objects.filter(test_execution=execution)
    )


@pytest.mark.django_db
def test_soft_deleted_calls_are_left_out_like_the_sql_read_model(
    run, organization, workspace, monkeypatch
):
    execution, scenario = run
    execution.status = TestExecution.ExecutionStatus.RUNNING
    execution.save(update_fields=["status"])
    eval_id = _config(
        execution,
        organization,
        "deleted",
        output_type_normalized="percentage",
        pass_threshold=0.5,
    )
    job = _harness_job(
        organization,
        workspace,
        run_test=execution.run_test,
        test_execution=execution,
    )
    started = timezone.now()
    live, deleted = [], []
    for index in range(6):
        removed = index % 2 == 1
        call = CallExecution.objects.create(
            test_execution=execution,
            scenario=scenario,
            status="completed" if index % 3 else "failed",
            started_at=started - timedelta(seconds=index),
            duration_seconds=100 if removed else index,
            call_metadata={
                "harness_scenario_key": "shared",
                "goal": "Deleted goal" if removed else "Live goal",
            },
            eval_outputs={
                eval_id: {"status": "completed", "output": 0.0 if removed else 0.9}
            },
        )
        (deleted if removed else live).append(call)
    HostedHarnessScenario.no_workspace_objects.create(
        job=job,
        scenario_key="shared",
        use_case="Authored for a deleted call",
        call_execution=deleted[0],
    )
    CallExecution.all_objects.filter(id__in=[call.id for call in deleted]).update(
        deleted=True
    )
    columns = [{"id": eval_id}]
    _assert_parity(execution, columns)
    live_ids = {str(call.id) for call in live}
    assert {row["id"] for row in scan_call_values(execution, columns)} == live_ids

    # Whole-run rows are scanned for both a default page and a filtered one.
    for query in ({}, {"filters": {"goal": ["Live goal"]}}):
        scanned = run_calls_page(
            execution,
            {"page": 1, "page_size": 3, **query},
            columns,
            lambda: run_calls_queryset(execution),
        )
        with monkeypatch.context() as patched:
            patched.setattr(page_module, "_scan_ordering", lambda query: None)
            patched.setattr(
                page_module,
                "scan_call_values",
                lambda execution, columns, ordering: _orm_rows(
                    execution, columns, None
                ),
            )
            expected = run_calls_page(
                execution,
                {"page": 1, "page_size": 3, **query},
                columns,
                lambda: run_calls_queryset(execution),
            )
        for key in ("rows", "page_ids", "summary", "execution_summary", "count"):
            _assert_identical(scanned[key], expected[key], key)
        by_id = {row["id"]: row for row in expected["execution_rows"]}
        assert len(scanned["execution_rows"]) == len(by_id) == len(live_ids)
        for row in scanned["execution_rows"]:
            _assert_identical(row, by_id[row["id"]])
        assert _facets(scanned["execution_rows"], execution) == _facets(
            expected["execution_rows"], execution
        )
        status_facet = _facets(scanned["execution_rows"], execution)["status"]
        assert sum(facet["count"] for facet in status_facet) == len(live_ids)
