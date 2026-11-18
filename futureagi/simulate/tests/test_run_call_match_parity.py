"""The Python twin of the calls page's search and filters keeps the same calls."""

from __future__ import annotations

import json
import sys
import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from simulate.models import (
    CallExecution,
    HarnessSuiteMember,
    HarnessSuiteVersion,
    HostedHarnessExecution,
    HostedHarnessJob,
    HostedHarnessScenario,
    Scenarios,
    TestExecution,
)
from simulate.models.run_test import RunTest
from simulate.serializers.compare_runs import HarnessCompareRunsQuerySerializer
from simulate.services import run_call_match
from simulate.services.run_call_match import (
    _upper_char,
    filter_rows,
    pg_upper,
    read_call_facts,
    sub_goal_facet,
)
from simulate.services.run_results_v3_page import cached_execution_rows
from simulate.services.run_results_v3_queries import (
    _sub_goal_facet,
    apply_run_call_query,
    run_calls_queryset,
)

SUB_GOALS = ["a", "b", "c", "d", "e", "f", "g", "m", "5", "x"]
QUERIES: list[dict[str, Any]] = [
    {},
    {"search": "boom"},
    {"search": "BOOM"},
    {"search": "_"},
    {"search": "%"},
    {"search": "silence"},
    {"search": "goal a"},
    {"search": "scenario"},
    # Letters Python upper-cases to more than one letter, and Postgres does not.
    *({"search": term} for term in ("ss", "straße", "STRAẞE", "fi", "ﬁ", "prü", "ᾼ")),
    {"filters": {"goal": ["Goal A"]}},
    {"filters": {"goal_not": ["Goal A"]}},
    {"filters": {"status": ["failed"]}},
    {"filters": {"status": ["passed", "error"]}},
    {"filters": {"status_not": ["passed"]}},
    *({"filters": {"sub_goal": [value]}} for value in SUB_GOALS),
    *({"filters": {"sub_goal_not": [value]}} for value in SUB_GOALS),
    {"filters": {"sub_goal": ["a", "e"], "status_not": ["error"]}},
    {"search": "goal", "filters": {"goal": ["Goal B"], "sub_goal_not": ["a"]}},
    # The Scenarios-tab filters: the texts fold as ``icontains`` does.
    *(
        {"filters": {"scenario": [term]}}
        for term in ("straße", "STRAẞE", "ss", "ﬁ", "%", "_", "member", "registered")
    ),
    {"filters": {"scenario": ["Pinned", "Registered 4"]}},
    *(
        {"filters": {"situation": [term]}}
        for term in ("50%_off", "%", "_", "member only", "STRASSE", "straße", "case")
    ),
    *(
        {"filters": {f"{key}{suffix}": [value]}}
        for key, values in {
            "persona": ("Ana", "5", "Ungrouped"),
            "age": ("adult", "Ungrouped"),
            "attack": ("injection", "none", "Ungrouped"),
            "task": ("lookup", "Ungrouped"),
        }.items()
        for value in values
        for suffix in ("", "_not")
    ),
    {"filters": {"persona": ["Ana", "Bo"], "age_not": ["teen"]}},
    {
        "search": "goal",
        "filters": {
            "status_not": ["error"],
            "persona": ["Ana", "Bo"],
            "situation": ["case", "text"],
        },
    },
]
# The authored values each scenario key reads.
AUTHORED: dict[str, dict[str, Any]] = {
    "k-source": {
        "name": "Straße ﬁ refund",
        "instruction": "Call about 50%_off",
        "persona": {"name": "Ana", "age_group": "adult"},
        "coverage": {"overlay": "injection", "task": "lookup"},
    },
    "k-other": {
        "name": "Other",
        "instruction": "",
        "persona": {"name": 5, "age_group": "senior"},
        "coverage": {"overlay": "none"},
    },
    "k-pinned": {
        "name": "Pinned authored",
        "instruction": "authored text",
        "persona": {"name": "Ana"},
        "coverage": {"task": "escalation"},
    },
    "k-empty": {"name": "Empty authored", "persona": {"name": "Cy"}},
}


def _job(organization, workspace, **fields: Any) -> HostedHarnessJob:
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


@pytest.fixture
def runs(organization, workspace) -> list[TestExecution]:
    """An unpinned run and a pinned one, with every sub-goal and search shape."""
    run_test = RunTest.objects.create(
        name="Match run", organization=organization, workspace=workspace
    )
    scenario = Scenarios.objects.create(
        name="Match scenario",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.SCRIPT,
        organization=organization,
        workspace=workspace,
    )
    environment = _job(organization, workspace, run_test=run_test)
    unpinned, pinned = (
        TestExecution.objects.create(
            run_test=run_test, status=TestExecution.ExecutionStatus.COMPLETED
        )
        for _ in range(2)
    )
    suite = HarnessSuiteVersion.no_workspace_objects.create(
        job=environment,
        organization_id=organization.id,
        workspace_id=workspace.id,
        version_number=1,
        status="active",
        origin="manual",
        hash="sha256:" + uuid.uuid4().hex * 2,
        archive_object_key="harness-suites/x.tar.gz",
        archive_sha256="",
        archive_size=0,
    )
    jobs = {
        unpinned.id: _job(
            organization,
            workspace,
            environment=environment,
            run_test=run_test,
            test_execution=unpinned,
        ),
        pinned.id: _job(
            organization,
            workspace,
            environment=environment,
            run_test=run_test,
            test_execution=pinned,
            suite_version=suite,
        ),
    }
    authored = {
        key: HostedHarnessScenario.no_workspace_objects.create(
            job=environment, scenario_key=key, sub_goals=sub_goals, **AUTHORED[key]
        )
        for key, sub_goals in {
            "k-source": ["a", {"name": "d"}],
            "k-other": ["f"],
            "k-pinned": ["a"],
            "k-empty": ["a"],
        }.items()
    }
    for key, content in {
        "k-pinned": {
            "sub_goals": ["m", {"name": "b"}],
            "name": "Member pinned",
            "instruction": "member only text",
            "persona": {"name": "Bo", "age_group": "teen"},
            "coverage": {"overlay": "jailbreak", "task": "lookup"},
        },
        "k-empty": {},
    }.items():
        HarnessSuiteMember.no_workspace_objects.create(
            suite_version=suite,
            scenario=authored[key],
            scenario_key=key,
            number=1,
            content=content,
        )
    receipt_goals = [{"name": "a", "held": True}, {"name": "b"}, {"held": False}]
    shapes: list[dict[str, Any]] = [
        {"metadata": {"hosted_harness_receipt": {"sub_goals": receipt_goals}}},
        {"metadata": {"hosted_harness_receipt": {"sub_goals": None}}},
        {
            "metadata": {
                "hosted_harness_receipt": {"sub_goals": []},
                "sub_goals": ["a", {"name": "c"}, 5, {"name": 5}],
            }
        },
        {"metadata": {}, "source": "k-source"},
        {"metadata": {"use_case": "Goal B"}, "registration": ["e", {"name": "x"}]},
        {"metadata": {}, "source": "k-other", "registration": ["g"]},
        {"metadata": {"sub_goals": None}, "source": "k-source"},
        {"metadata": {"hosted_harness_receipt": "sub_goals"}, "source": "k-source"},
        {"metadata": {"hosted_harness_receipt": {"sub_goals": {"name": "a"}}}},
        {"metadata": {"harness_scenario_key": "k-pinned"}, "source": "k-pinned"},
        {"metadata": {"harness_scenario_key": "k-empty"}, "source": "k-empty"},
        {"metadata": {"harness_scenario_key": ""}, "registration": ["e"]},
        {
            "metadata": {"harness_outcome_status": "failed"},
            "ended_reason": "Silence_Timed%Out",
        },
        {"metadata": {"harness_outcome_status": "error"}, "error_message": "Boom"},
        {"metadata": {"harness_outcome_status": "passed"}, "status": "pending"},
        {"metadata": ["sub_goals"]},
        {"metadata": {"use_case": "Prüfung ᾳ"}, "ended_reason": "Straße ﬁnal"},
        {"metadata": {"harness_scenario_key": "k-source"}},
        {"metadata": {"harness_scenario_key": "k-other"}},
    ]
    started = timezone.now()
    for run in (unpinned, pinned):
        for index, shape in enumerate(shapes):
            metadata = shape["metadata"]
            if isinstance(metadata, dict):
                metadata = {"use_case": "Goal A" if index % 2 else "Goal B", **metadata}
            call = CallExecution.objects.create(
                test_execution=run,
                scenario=scenario,
                status=shape.get("status", "completed"),
                started_at=started - timedelta(seconds=index),
                call_metadata=metadata,
                ended_reason=shape.get("ended_reason"),
                error_message=shape.get("error_message"),
            )
            if "source" in shape:
                HostedHarnessExecution.no_workspace_objects.create(
                    job=jobs[run.id],
                    source_scenario=authored[shape["source"]],
                    execution_key=f"{shape['source']}:{index}",
                    trial_index=index,
                    call_execution=call,
                )
            if "registration" in shape:
                HostedHarnessScenario.no_workspace_objects.create(
                    job=jobs[run.id],
                    scenario_key=f"reg-{index}",
                    call_execution=call,
                    sub_goals=shape["registration"],
                    name=f"Registered {index}",
                    instruction="STRASSE case" if index % 2 else "straße case",
                    # One persona that is not an object.
                    persona=(
                        "Ana" if index == 5 else {"name": "Dee", "age_group": "adult"}
                    ),
                    coverage={"overlay": "none", "task": "lookup"},
                )
    return [unpinned, pinned]


def _rows(execution: TestExecution) -> list[dict[str, Any]]:
    """The run's cached unfiltered rows, as a compare page reads them."""
    return cached_execution_rows([execution], {execution.id: []})[execution.id]


def _sql_ids(execution: TestExecution, query: dict[str, Any]) -> list[str]:
    return [
        str(call_id)
        for call_id in apply_run_call_query(
            run_calls_queryset(execution), query
        ).values_list("id", flat=True)
    ]


@pytest.mark.parametrize("query", QUERIES, ids=[str(query) for query in QUERIES])
def test_each_query_is_one_the_compare_page_accepts(query):
    serializer = HarnessCompareRunsQuerySerializer(
        data={
            "runs": f"{uuid.uuid4()},{uuid.uuid4()}",
            **query,
            "filters": json.dumps(query.get("filters") or {}),
        }
    )
    assert serializer.is_valid(), serializer.errors


@pytest.mark.integration
@pytest.mark.django_db
@pytest.mark.parametrize("query", QUERIES, ids=[str(query) for query in QUERIES])
def test_filters_keep_the_calls_the_sql_query_keeps(runs, query):
    facts = read_call_facts([run.id for run in runs])
    for run in runs:
        rows = _rows(run)
        kept = [row["id"] for row in filter_rows(rows, facts[run.id], query)]
        assert kept == _sql_ids(run, query), run


@pytest.mark.parametrize("key", ["scenario_not", "situation_not", "name"])
def test_text_filters_have_no_not_variant(key):
    serializer = HarnessCompareRunsQuerySerializer(
        data={
            "runs": f"{uuid.uuid4()},{uuid.uuid4()}",
            "filters": json.dumps({key: ["x"]}),
        }
    )
    assert not serializer.is_valid()
    assert "filters" in serializer.errors


@pytest.mark.integration
@pytest.mark.django_db
def test_status_chips_drop_only_the_status_filters(runs):
    query = {"filters": {"status": ["failed"], "sub_goal_not": ["a"]}}
    facts = read_call_facts([runs[0].id])
    rows = _rows(runs[0])

    kept = filter_rows(rows, facts[runs[0].id], query, drop_status=True)

    assert [row["id"] for row in kept] == _sql_ids(
        runs[0], {"filters": {"sub_goal_not": ["a"]}}
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_sub_goal_facet_counts_like_the_sql_facet(runs):
    facts = read_call_facts([run.id for run in runs])
    for run in runs:
        assert sub_goal_facet(facts[run.id].values()) == _sub_goal_facet(
            CallExecution.objects.filter(test_execution=run)
        )


@pytest.mark.integration
@pytest.mark.django_db
def test_scenario_key_is_the_sql_scenario_key(runs):
    facts = read_call_facts([run.id for run in runs])
    for run in runs:
        expected = dict(
            run_calls_queryset(run).values_list("id", "result_scenario_key")
        )
        assert {
            call_id: one["scenario_key"] for call_id, one in facts[run.id].items()
        } == {str(call_id): key for call_id, key in expected.items()}


def test_pg_upper_folds_each_letter_as_postgres_does():
    for code in range(sys.maxunicode + 1):
        if not 0xD800 <= code <= 0xDFFF:
            char = chr(code)
            assert pg_upper(char) == _upper_char(char), hex(code)
    assert pg_upper("Straße ﬁ") == "STRAẞE ﬁ"


def test_pg_upper_skips_the_letter_loop_when_no_letter_expands(monkeypatch):
    def letter_by_letter(char: str) -> str:
        raise AssertionError("folded letter by letter")

    monkeypatch.setattr(run_call_match, "_upper_char", letter_by_letter)

    assert pg_upper("It’s a refund – 返金") == "IT’S A REFUND – 返金"
