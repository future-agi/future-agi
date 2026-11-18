from uuid import UUID

import pytest

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import AgentDefinition, CallExecution, RunTest, Scenarios
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.test_execution import TestExecution as SimulationExecution
from simulate.views.run_test import _flat_kpi_evals

pytestmark = [pytest.mark.integration, pytest.mark.api]


@pytest.fixture
def call(db, organization, workspace):
    agent = AgentDefinition.objects.create(
        organization=organization,
        workspace=workspace,
        agent_name="KPI test agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+12025550100",
        inbound=True,
        languages=["en"],
    )
    run = RunTest.objects.create(
        organization=organization,
        workspace=workspace,
        name="KPI run",
        agent_definition=agent,
    )
    execution = SimulationExecution.objects.create(
        run_test=run, status=SimulationExecution.ExecutionStatus.COMPLETED
    )
    scenario = Scenarios.objects.create(
        organization=organization,
        workspace=workspace,
        name="Greeting",
        source="Greet the caller",
        scenario_type=Scenarios.ScenarioTypes.SCRIPT,
    )
    return CallExecution.objects.create(
        scenario=scenario,
        test_execution=execution,
        status=CallExecution.CallStatus.COMPLETED,
        duration_seconds=1,
        overall_score=85,
        response_time_ms=420,
        avg_agent_latency_ms=310,
    )


@pytest.fixture
def bind(call, organization):
    template = EvalTemplate.objects.create(
        name="KPI template", organization=organization, config={}
    )

    def create(number, name, output_type, output, *, choices=()):
        binding = SimulateEvalConfig.objects.create(
            id=UUID(int=number),
            name=name,
            eval_template=template,
            run_test=call.test_execution.run_test,
            config={"run_config": {"choices": list(choices)}},
        )
        return str(binding.pk), {
            "name": name,
            "output_type": output_type,
            "output": output,
        }

    return create


@pytest.fixture
def kpis(call, auth_client):
    def get(*entries):
        call.eval_outputs = dict(entries)
        call.save(update_fields=["eval_outputs"])
        response = auth_client.get(
            f"/simulate/test-executions/{call.test_execution_id}/kpis/"
        )
        assert response.status_code == 200
        return response.json()

    return get


@pytest.mark.parametrize("output,count", [("warm", 1), (None, 0)])
def test_distribution_does_not_replace_score(bind, kpis, output, count):
    score = bind(2, "Tone", "score", 0.8)
    choice = bind(1, "Avg Tone", "choices", output, choices=("Warm", "Cold"))
    data = kpis(choice, score)
    assert data["avg_tone"] == 80
    assert data[f"avg_tone__{choice[0]}"] == {
        "Warm": count,
        "Cold": 0,
        "choices": ["Warm", "Cold"],
    }


def test_measured_kpis_are_not_replaced_by_evaluator_percentages(bind, kpis):
    system = kpis()
    assert (
        system["avg_score"],
        system["avg_response"],
        system["avg_agent_latency"],
    ) == (85, 420, 310)
    score = bind(1, "Score", "score", 0.2)
    response = bind(2, "Response", "score", 0.3)
    latency = bind(3, "Agent Latency", "score", 0.4)
    assert kpis(score, response, latency) == {
        **system,
        f"avg_score__{score[0]}": 20,
        f"avg_response__{response[0]}": 30,
        f"avg_agent_latency__{latency[0]}": 40,
    }


def test_colliding_distributions_keep_counts_labels_and_occupied_alias(bind, kpis):
    system = kpis()
    first = bind(
        1, "Call Mood", "choices", ["shared", "warm"], choices=("Shared", "Warm")
    )
    second = bind(
        2, "call_mood", "choices", ["shared", "shared"], choices=("Shared", "Cool")
    )
    alias = f"call_mood__{second[0]}"
    natural = bind(3, alias, "choices", "blue", choices=("Blue",))
    assert kpis(first, second, natural) == {
        **system,
        "call_mood": {"Shared": 1, "Warm": 1, "choices": ["Shared", "Warm"]},
        alias: {"Blue": 1, "choices": ["Blue"]},
        alias + "_": {"Shared": 2, "Cool": 0, "choices": ["Shared", "Cool"]},
    }


def test_flat_keys_are_independent_of_row_order():
    entries = [
        (("avg_tone", "c", "score"), 80),
        (("avg_tone", "b", "score"), 90),
        (("avg_tone", "a", "numeric"), 3),
        (("avg_tone__c", "d", "distribution"), {}),
    ]
    expected = {
        "avg_tone": 90,
        "avg_tone__c__": 80,
        "avg_tone__a": 3,
        "avg_tone__c": {},
    }
    for rows in (entries, reversed(entries)):
        assert _flat_kpi_evals(dict(rows), {"avg_tone__c_"}) == expected


def test_scalar_precedes_numeric_choice_then_distribution(bind, kpis):
    score = bind(3, "Rating", "score", 0.8)
    numeric = bind(2, "rating", "choices", 4)
    distribution = bind(1, "Avg Rating", "choices", "good")
    data = kpis(distribution, numeric, score)
    assert data["avg_rating"] == 80
    assert data[f"avg_rating__{numeric[0]}"] == 4
    assert data[f"avg_rating__{distribution[0]}"] == {"good": 1, "choices": ["good"]}


def test_noncolliding_keys_values_and_null_behavior(bind, kpis):
    system = kpis()
    assert kpis(
        bind(1, "Accuracy", "score", 0.8),
        bind(2, "Rank", "choices", 3),
        bind(3, "Mood", "choices", "warm", choices=("Warm", "Cold")),
        bind(4, "Unscored", "score", None),
        bind(5, "Empty", "choices", None, choices=("Yes", "No")),
    ) == {
        **system,
        "avg_accuracy": 80,
        "avg_rank": 3,
        "mood": {"Warm": 1, "Cold": 0, "choices": ["Warm", "Cold"]},
        "avg_unscored": 0,
        "empty": {"Yes": 0, "No": 0, "choices": ["Yes", "No"]},
    }
