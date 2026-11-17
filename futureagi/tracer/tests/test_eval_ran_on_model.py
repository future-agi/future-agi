"""An agent evaluation's Observe row records the model its client ran on.

An agent's client can run on another model than the configured one: the
managed model in cloud, or a multimodal upgrade. The span, trace and session
writers store that model under ``ran_on_model`` in the row's output metadata,
whose readers pick named keys only. The response the row keeps and the session
eval log stay as they were. The task Usage view stays as it was while
EVAL_RAN_ON_LABEL_ENABLED is off; with it on, each run's detail gains
``ran_on_label``, the product name of the model the run ran on, and never the
stored id. The detail's model is then the one the agent evaluator was given, as
the run's metadata records it, else the eval's model, and the label compares
with it, so a later edit of the eval's model does not change what a run shows.
Only the engine and the cost log are replaced, as in the other
Observe eval tests; the session eval log reads the row from ClickHouse, seeded
as in test_session_eval_logs_endpoint.py.
"""

import json
from types import SimpleNamespace

import pytest

# Breaks the tracer.utils.eval <-> model_hub.tasks import cycle.
import model_hub.tasks  # noqa: F401
from evaluations.constants import (
    AGENT_EVALUATOR_TYPE_ID,
    CUSTOM_PROMPT_EVALUATOR_TYPE_ID,
)
from evaluations.engine.runner import EvalResult
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger
from tracer.tests._ch_seed import (
    seed_ch_eval_loggers_v2,
    truncate_ch_eval_logger_v2,
)

CONFIGURED_MODEL = "gpt-4o"
RAN_ON_MODEL = "falcon_ai"
MULTIMODAL_MODEL = "gemini-2.5-pro"
USAGE_DETAIL_KEYS = {
    "detail_complete",
    "omitted_fields",
    "eval_name",
    "model",
    "warnings",
    "output_type",
    "target_type",
    "span_name",
    "span_id",
    "trace_id",
    "session_id",
    "session_name",
    "output_bool",
    "output_float",
    "output_str",
    "results_explanation",
    "error_message",
    "input_variables",
}
RUN_PARAMS = {"input": "hello", "output": "world"}
AGENT_METADATA = json.dumps(
    {
        "usage": {"total_tokens": 150, "prompt_tokens": 120, "completion_tokens": 30},
        "cost": {"total_cost": 0.00105, "prompt_cost": 0.0006},
        "response_time": 5,
        "explanation": "The answer is grounded.",
        "agent_metadata": {
            "model_used": CONFIGURED_MODEL,
            "mode": "agent",
            "iterations": 1,
            "tool_calls_count": 0,
            "tools_used": [],
        },
    }
)
JUDGE_METADATA = json.dumps(
    {
        "usage": {"total_tokens": 90, "prompt_tokens": 80, "completion_tokens": 10},
        "cost": {"total_cost": 0.0004},
        "response_time": 3,
        "explanation": "The answer is grounded.",
    }
)


@pytest.fixture
def engine(monkeypatch):
    """The engine replaced by one run that names the configured model.

    The evaluator it hands back exposes the model its client ran on, as an
    agent evaluator does; a test may swap the evaluator and the metadata.
    """
    run = SimpleNamespace(
        evaluator=SimpleNamespace(ran_on_model=RAN_ON_MODEL),
        metadata=AGENT_METADATA,
    )

    def _run(_request):
        return EvalResult(
            value=True,
            data=dict(RUN_PARAMS),
            reason="The answer is grounded.",
            failure=False,
            runtime=5,
            model_used=CONFIGURED_MODEL,
            metrics=[{"id": "agent_eval_score", "value": "Pass"}],
            metadata=run.metadata,
            output_type="Pass/Fail",
            start_time=0.0,
            end_time=0.005,
            duration=0.005,
            cost={"total_cost": 0.00105},
            token_usage={"prompt_tokens": 120, "completion_tokens": 30},
            evaluator=run.evaluator,
        )

    monkeypatch.setattr("evaluations.engine.run_eval", _run, raising=False)
    monkeypatch.setattr("evaluations.engine.runner.run_eval", _run, raising=False)
    return run


def _configure(custom_eval_config, eval_type_id, model=CONFIGURED_MODEL):
    template = custom_eval_config.eval_template
    template.config["eval_type_id"] = eval_type_id
    template.save(update_fields=["config"])
    CustomEvalConfig.objects.filter(pk=custom_eval_config.pk).update(model=model)
    custom_eval_config.refresh_from_db()
    return custom_eval_config


@pytest.fixture
def agent_eval(custom_eval_config):
    return _configure(custom_eval_config, AGENT_EVALUATOR_TYPE_ID)


@pytest.fixture
def ch_eval_logger(settings):
    """Seed only the direct-write table the session eval log reads."""
    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
    truncate_ch_eval_logger_v2()
    yield
    truncate_ch_eval_logger_v2()


def _evaluate(target, custom_eval_config, *, eval_task_id=None, **targets):
    """Run one Observe evaluation on ``target`` and return the row it wrote."""
    from tracer.utils.eval import (
        OBSERVE,
        _execute_evaluation,
        _execute_evaluation_for_session,
        _execute_evaluation_for_trace,
    )

    if target == "span":
        _execute_evaluation(
            observation_span_id=targets["observation_span"].id,
            custom_eval_config_id=custom_eval_config.id,
            eval_task_id=eval_task_id,
            type=OBSERVE,
            run_params=dict(RUN_PARAMS),
        )
    elif target == "trace":
        _execute_evaluation_for_trace(
            trace=targets["trace"],
            anchor_span=targets["observation_span"],
            custom_eval_config=custom_eval_config,
            eval_task_id=eval_task_id,
            run_params=dict(RUN_PARAMS),
        )
    else:
        _execute_evaluation_for_session(
            trace_session=targets["trace_session"],
            custom_eval_config=custom_eval_config,
            eval_task_id=eval_task_id,
            run_params=dict(RUN_PARAMS),
        )
    return (
        EvalLogger.objects.filter(custom_eval_config=custom_eval_config)
        .order_by("-created_at")
        .first()
    )


def _assert_the_ran_on_model_is_not_shown(response):
    assert response.status_code == 200, response.content
    body = response.content.decode()
    assert "ran_on_model" not in body
    assert RAN_ON_MODEL not in body
    assert CONFIGURED_MODEL in body


def _get_usage(auth_client, eval_task):
    return auth_client.get(
        "/tracer/eval-task/get_usage/",
        {"eval_task_id": str(eval_task.id), "period": "30d"},
    )


@pytest.mark.django_db
@pytest.mark.parametrize("target", ["span", "trace", "session"])
def test_an_agent_rows_output_metadata_records_the_model_its_client_ran_on(
    target,
    observation_span,
    trace,
    trace_session,
    agent_eval,
    engine,
    stub_cost_log,
):
    eval_log = _evaluate(
        target,
        agent_eval,
        observation_span=observation_span,
        trace=trace,
        trace_session=trace_session,
    )

    assert eval_log is not None
    assert eval_log.error is False
    assert eval_log.output_metadata == {
        **json.loads(AGENT_METADATA),
        "ran_on_model": RAN_ON_MODEL,
    }
    # The response the row keeps is the evaluator's, unchanged.
    assert eval_log.results_explanation["model"] == CONFIGURED_MODEL
    assert eval_log.results_explanation["metadata"] == AGENT_METADATA


@pytest.mark.django_db
def test_a_non_agent_rows_output_metadata_is_the_evaluators_metadata_only(
    observation_span,
    custom_eval_config,
    engine,
    stub_cost_log,
):
    judge = _configure(custom_eval_config, CUSTOM_PROMPT_EVALUATOR_TYPE_ID)
    engine.evaluator = SimpleNamespace()
    engine.metadata = JUDGE_METADATA

    eval_log = _evaluate("span", judge, observation_span=observation_span)

    assert eval_log.error is False
    assert eval_log.output_metadata == json.loads(JUDGE_METADATA)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_the_task_usage_view_does_not_show_the_model_an_agent_ran_on(
    auth_client,
    observation_span,
    agent_eval,
    eval_task,
    engine,
    stub_cost_log,
):
    eval_log = _evaluate(
        "span",
        agent_eval,
        eval_task_id=eval_task.id,
        observation_span=observation_span,
    )

    response = auth_client.get(
        "/tracer/eval-task/get_usage/",
        {"eval_task_id": str(eval_task.id), "period": "30d"},
    )

    _assert_the_ran_on_model_is_not_shown(response)
    rows = response.json()["result"]["logs"]["results"]
    assert [row["id"] for row in rows] == [str(eval_log.id)]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_the_session_eval_log_does_not_show_the_model_an_agent_ran_on(
    auth_client,
    trace_session,
    agent_eval,
    engine,
    stub_cost_log,
    ch_eval_logger,
):
    eval_log = _evaluate("session", agent_eval, trace_session=trace_session)
    seed_ch_eval_loggers_v2([eval_log])

    response = auth_client.get(f"/tracer/trace-session/{trace_session.id}/eval_logs/")

    _assert_the_ran_on_model_is_not_shown(response)
    payload = response.json()
    items = payload.get("result", payload)["items"]
    assert [item["id"] for item in items] == [str(eval_log.id)]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_with_the_ran_on_label_off_the_task_usage_view_answers_as_before(
    auth_client,
    observation_span,
    agent_eval,
    eval_task,
    engine,
    stub_cost_log,
    settings,
):
    _evaluate(
        "span",
        agent_eval,
        eval_task_id=eval_task.id,
        observation_span=observation_span,
    )
    settings.EVAL_RAN_ON_LABEL_ENABLED = False

    response = _get_usage(auth_client, eval_task)

    _assert_the_ran_on_model_is_not_shown(response)
    assert "ran_on_label" not in response.content.decode()
    [row] = response.json()["result"]["logs"]["results"]
    assert set(row["detail"]) == USAGE_DETAIL_KEYS


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_the_ran_on_label_is_the_only_change_the_setting_makes_to_the_task_usage_view(
    auth_client,
    observation_span,
    agent_eval,
    eval_task,
    engine,
    stub_cost_log,
    settings,
):
    _evaluate(
        "span",
        agent_eval,
        eval_task_id=eval_task.id,
        observation_span=observation_span,
    )
    settings.EVAL_RAN_ON_LABEL_ENABLED = False
    off = _get_usage(auth_client, eval_task).json()["result"]["logs"]
    settings.EVAL_RAN_ON_LABEL_ENABLED = True
    on = _get_usage(auth_client, eval_task).json()["result"]["logs"]

    assert on["results"][0]["detail"].pop("ran_on_label", None) == "Falcon AI"
    assert on == off


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
@pytest.mark.parametrize(
    "chosen, ran_on, label",
    [
        (CONFIGURED_MODEL, RAN_ON_MODEL, "Falcon AI"),
        (CONFIGURED_MODEL, "turing_small", "Turing Small"),
        (CONFIGURED_MODEL, MULTIMODAL_MODEL, None),
        ("turing_small", "turing_small", "Turing Small"),
        (CONFIGURED_MODEL, None, None),
    ],
    ids=[
        "falcon-ai",
        "turing-small",
        "multimodal-model",
        "chosen-model",
        "nothing-recorded",
    ],
)
def test_with_the_ran_on_label_on_the_task_usage_view_names_the_model_a_run_ran_on(
    chosen,
    ran_on,
    label,
    auth_client,
    observation_span,
    agent_eval,
    eval_task,
    engine,
    stub_cost_log,
    settings,
):
    engine.evaluator = SimpleNamespace(ran_on_model=ran_on)
    eval_log = _evaluate(
        "span",
        agent_eval,
        eval_task_id=eval_task.id,
        observation_span=observation_span,
    )
    assert eval_log.output_metadata.get("ran_on_model") == ran_on
    # The eval's model when the view reads the run.
    _configure(agent_eval, AGENT_EVALUATOR_TYPE_ID, model=chosen)
    settings.EVAL_RAN_ON_LABEL_ENABLED = True

    response = _get_usage(auth_client, eval_task)

    assert response.status_code == 200, response.content
    body = response.content.decode()
    assert "ran_on_model" not in body
    assert RAN_ON_MODEL not in body
    assert MULTIMODAL_MODEL not in body
    [row] = response.json()["result"]["logs"]["results"]
    assert row["id"] == str(eval_log.id)
    # The model the run was given, whatever the eval names now.
    assert row["detail"]["model"] == CONFIGURED_MODEL
    assert "ran_on_label" in row["detail"]
    assert row["detail"]["ran_on_label"] == label


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
@pytest.mark.parametrize(
    "given, ran_on, label",
    [
        (CONFIGURED_MODEL, RAN_ON_MODEL, "Falcon AI"),
        ("turing_small", "turing_small", None),
    ],
    ids=["ran-on-another-model", "ran-on-the-given-model"],
)
def test_with_the_ran_on_label_on_a_run_whose_eval_names_no_model_shows_the_given_one(
    given,
    ran_on,
    label,
    auth_client,
    observation_span,
    agent_eval,
    eval_task,
    engine,
    stub_cost_log,
    settings,
):
    metadata = json.loads(AGENT_METADATA)
    metadata["agent_metadata"]["model_used"] = given
    engine.metadata = json.dumps(metadata)
    engine.evaluator = SimpleNamespace(ran_on_model=ran_on)
    eval_log = _evaluate(
        "span",
        agent_eval,
        eval_task_id=eval_task.id,
        observation_span=observation_span,
    )
    # The eval names no model, so the evaluator ran with the one it was given.
    _configure(agent_eval, AGENT_EVALUATOR_TYPE_ID, model=None)
    settings.EVAL_RAN_ON_LABEL_ENABLED = True

    response = _get_usage(auth_client, eval_task)

    assert response.status_code == 200, response.content
    body = response.content.decode()
    assert "ran_on_model" not in body
    assert RAN_ON_MODEL not in body
    [row] = response.json()["result"]["logs"]["results"]
    assert row["id"] == str(eval_log.id)
    assert row["detail"]["model"] == given
    assert "ran_on_label" in row["detail"]
    assert row["detail"]["ran_on_label"] == label


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
@pytest.mark.parametrize(
    "output_metadata",
    [None, [RAN_ON_MODEL]],
    ids=["no-metadata", "json-non-object"],
)
def test_with_the_ran_on_label_on_a_run_whose_metadata_names_no_model_has_no_label(
    output_metadata,
    auth_client,
    observation_span,
    agent_eval,
    eval_task,
    engine,
    stub_cost_log,
    settings,
):
    eval_log = _evaluate(
        "span",
        agent_eval,
        eval_task_id=eval_task.id,
        observation_span=observation_span,
    )
    EvalLogger.objects.filter(pk=eval_log.pk).update(output_metadata=output_metadata)
    settings.EVAL_RAN_ON_LABEL_ENABLED = True

    response = _get_usage(auth_client, eval_task)

    assert response.status_code == 200, response.content
    [row] = response.json()["result"]["logs"]["results"]
    assert row["id"] == str(eval_log.id)
    assert row["detail"]["model"] == CONFIGURED_MODEL
    assert "ran_on_label" in row["detail"]
    assert row["detail"]["ran_on_label"] is None


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_with_the_ran_on_label_on_a_run_keeps_its_given_model_after_the_eval_changes(
    auth_client,
    observation_span,
    agent_eval,
    eval_task,
    engine,
    stub_cost_log,
    settings,
):
    # The run is given turing_small and runs on it.
    _configure(agent_eval, AGENT_EVALUATOR_TYPE_ID, model="turing_small")
    metadata = json.loads(AGENT_METADATA)
    metadata["agent_metadata"]["model_used"] = "turing_small"
    engine.metadata = json.dumps(metadata)
    engine.evaluator = SimpleNamespace(ran_on_model="turing_small")
    eval_log = _evaluate(
        "span",
        agent_eval,
        eval_task_id=eval_task.id,
        observation_span=observation_span,
    )
    # The eval's model is edited after the run.
    _configure(agent_eval, AGENT_EVALUATOR_TYPE_ID, model=CONFIGURED_MODEL)
    settings.EVAL_RAN_ON_LABEL_ENABLED = True

    response = _get_usage(auth_client, eval_task)

    assert response.status_code == 200, response.content
    assert "Turing Small" not in response.content.decode()
    [row] = response.json()["result"]["logs"]["results"]
    assert row["id"] == str(eval_log.id)
    assert row["detail"]["model"] == "turing_small"
    assert "ran_on_label" in row["detail"]
    assert row["detail"]["ran_on_label"] is None


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_with_the_ran_on_label_on_a_run_recording_no_given_model_shows_the_evals_model(
    auth_client,
    observation_span,
    agent_eval,
    eval_task,
    engine,
    stub_cost_log,
    settings,
):
    # A run whose metadata records no model it was given.
    metadata = json.loads(AGENT_METADATA)
    del metadata["agent_metadata"]["model_used"]
    engine.metadata = json.dumps(metadata)
    engine.evaluator = SimpleNamespace(ran_on_model="turing_small")
    eval_log = _evaluate(
        "span",
        agent_eval,
        eval_task_id=eval_task.id,
        observation_span=observation_span,
    )
    settings.EVAL_RAN_ON_LABEL_ENABLED = True

    response = _get_usage(auth_client, eval_task)

    assert response.status_code == 200, response.content
    [row] = response.json()["result"]["logs"]["results"]
    assert row["id"] == str(eval_log.id)
    assert row["detail"]["model"] == CONFIGURED_MODEL
    assert row["detail"]["ran_on_label"] == "Turing Small"


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_with_the_ran_on_label_setting_absent_the_task_usage_view_names_the_model_a_run_ran_on(
    auth_client,
    observation_span,
    agent_eval,
    eval_task,
    engine,
    stub_cost_log,
    settings,
):
    # The label is on by default; only an explicit False turns it off.
    eval_log = _evaluate(
        "span",
        agent_eval,
        eval_task_id=eval_task.id,
        observation_span=observation_span,
    )
    if hasattr(settings, "EVAL_RAN_ON_LABEL_ENABLED"):
        del settings.EVAL_RAN_ON_LABEL_ENABLED

    response = _get_usage(auth_client, eval_task)

    assert response.status_code == 200, response.content
    body = response.content.decode()
    assert "ran_on_model" not in body
    assert RAN_ON_MODEL not in body
    [row] = response.json()["result"]["logs"]["results"]
    assert row["id"] == str(eval_log.id)
    assert row["detail"]["model"] == CONFIGURED_MODEL
    assert row["detail"]["ran_on_label"] == "Falcon AI"
