"""An agent evaluation's SDK record names the model its client ran on.

An agent's client can run on another model than the configured one: the
managed model in cloud, or a multimodal upgrade. A standalone evaluation, sync
or async, stores that model under ``ran_on_model`` in the evaluation's stored
metadata, which no SDK response returns. Its model and every other metadata
key stay as the evaluator returned them. While EVAL_RAN_ON_LABEL_ENABLED is
on, the evaluation's result also carries ``ran_on_label``, the product name of
the model it ran on, and never the stored id. Its model is then the requested
model or, for an agent evaluation that recorded none, the model the evaluator
was given (the only one the sync path stores). The label compares with the
model the evaluator was given, which a run_config model in the request can make
differ from the requested one. Any other evaluation's model stays as it was.
Only the engine, the usage check, the cost log, ground truth and billing are
replaced, as in test_async_evaluation_organization.py.
"""

import json
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from evaluations import engine
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation, StatusChoices
from model_hub.services.ground_truth_service import GroundTruthService
from sdk.utils import evaluations as sdk_evaluations
from tfc.constants.api_calls import APICallStatusChoices
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.temporal.evaluations import activities

pytestmark = pytest.mark.django_db

CONFIGURED_MODEL = "gpt-4o"
RAN_ON_MODEL = "falcon_ai"
MULTIMODAL_MODEL = "gemini-2.5-pro"
RUN_CONFIG_MODEL = "turing_small"
RESULT_KEYS = {
    "eval_id",
    "name",
    "input_data",
    "reason",
    "runtime",
    "model",
    "output_type",
    "value",
    "error_localizer_enabled",
}
INPUTS = {"output": "Paris is the capital of France."}
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


def _template(organization, workspace, eval_type_id):
    return EvalTemplate.no_workspace_objects.create(
        name=f"ran-on-{eval_type_id.lower()}-{uuid.uuid4().hex}",
        organization=organization,
        workspace=workspace,
        owner="user",
        config={
            "eval_type_id": eval_type_id,
            "required_keys": ["output"],
            "output": "Pass/Fail",
        },
    )


@pytest.fixture
def engine_run(monkeypatch):
    """The engine replaced by one run that names the configured model.

    The evaluator it hands back exposes the model its client ran on, as an
    agent evaluator does; a test may swap the evaluator and the metadata.
    """
    # The root conftest supplies these same interfaces in OSS runs.
    from ee.usage.services import emitter, metering
    from ee.usage.services.config import BillingConfig

    state = SimpleNamespace(
        evaluator=SimpleNamespace(ran_on_model=RAN_ON_MODEL),
        metadata=AGENT_METADATA,
    )

    def _run(_request):
        return engine.EvalResult(
            value="Passed",
            data=dict(INPUTS),
            reason="The answer is grounded.",
            failure=False,
            runtime=5,
            model_used=CONFIGURED_MODEL,
            metrics=[{"id": "agent_eval_score", "value": "Pass"}],
            metadata=state.metadata,
            output_type="Pass/Fail",
            cost={"total_cost": 0.00105},
            token_usage={},
            evaluator=state.evaluator,
        )

    cost_log = SimpleNamespace(
        status=APICallStatusChoices.PROCESSING.value,
        config="{}",
        log_id=uuid.uuid4(),
        save=Mock(),
    )
    billing = SimpleNamespace(
        get_eval_per_run_fee=lambda: 0.01,
        calculate_ai_credits=lambda cost: cost * 100,
    )
    monkeypatch.setattr(
        metering, "check_usage", Mock(return_value=SimpleNamespace(allowed=True))
    )
    monkeypatch.setattr(
        sdk_evaluations,
        "log_and_deduct_cost_for_api_request",
        Mock(return_value=cost_log),
    )
    monkeypatch.setattr(GroundTruthService, "inject_context", Mock())
    monkeypatch.setattr(engine, "run_eval", _run)
    monkeypatch.setattr(BillingConfig, "get", Mock(return_value=billing))
    monkeypatch.setattr(emitter, "emit", Mock())
    # Connection cleanup is transport plumbing; keep pytest's DB transaction open.
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)
    return state


def _run_sync(template, user, workspace, organization):
    clear_workspace_context()
    response = sdk_evaluations._run_standalone_eval(
        template,
        dict(INPUTS),
        CONFIGURED_MODEL,
        user,
        workspace,
        {},
        error_localizer_enabled=False,
        trace_eval=False,
        custom_eval_name=None,
        span_id=None,
        organization=organization,
    )
    return response, Evaluation.no_workspace_objects.get(pk=response["eval_id"])


def _run_async(template, user, workspace, organization, eval_config=None):
    evaluation = Evaluation.no_workspace_objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        eval_template=template,
        model_name=CONFIGURED_MODEL,
        input_data=dict(INPUTS),
        eval_config=eval_config or {},
        status=StatusChoices.PENDING,
    )
    clear_workspace_context()
    outcome = activities._run_single_evaluation_sync(str(evaluation.pk))
    evaluation.refresh_from_db()
    return outcome, evaluation


def _get_result(client, evaluation):
    response = client.get("/sdk/api/v1/new-eval/", {"eval_id": str(evaluation.id)})
    assert response.status_code == 200, response.content
    return response


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_an_agent_evaluation_stores_the_model_its_client_ran_on(
    mode, engine_run, user, workspace, organization
):
    template = _template(organization, workspace, "AgentEvaluator")
    runner = _run_sync if mode == "sync" else _run_async

    _, evaluation = runner(template, user, workspace, organization)

    assert evaluation.status == StatusChoices.COMPLETED, evaluation.error_message
    assert json.loads(evaluation.metadata) == {
        **json.loads(AGENT_METADATA),
        "ran_on_model": RAN_ON_MODEL,
    }
    assert evaluation.model == CONFIGURED_MODEL


def test_the_sync_response_does_not_show_the_model_an_agent_ran_on(
    engine_run, user, workspace, organization
):
    template = _template(organization, workspace, "AgentEvaluator")

    response, _ = _run_sync(template, user, workspace, organization)

    body = json.dumps(response, default=str)
    assert "ran_on_model" not in body
    assert RAN_ON_MODEL not in body


def test_a_non_agent_evaluation_stores_the_evaluators_metadata_as_it_is(
    engine_run, user, workspace, organization
):
    template = _template(organization, workspace, "CustomPromptEvaluator")
    engine_run.evaluator = SimpleNamespace()
    engine_run.metadata = JUDGE_METADATA

    _, evaluation = _run_sync(template, user, workspace, organization)

    assert evaluation.status == StatusChoices.COMPLETED
    assert evaluation.metadata == JUDGE_METADATA


def test_with_the_ran_on_label_off_the_result_answers_as_before(
    engine_run, auth_client, user, workspace, organization, settings
):
    template = _template(organization, workspace, "AgentEvaluator")
    _, evaluation = _run_async(template, user, workspace, organization)
    settings.EVAL_RAN_ON_LABEL_ENABLED = False

    response = _get_result(auth_client, evaluation)

    body = response.content.decode()
    assert "ran_on" not in body
    assert RAN_ON_MODEL not in body
    payload = response.json()["result"]
    assert payload["eval_status"] == StatusChoices.COMPLETED
    assert set(payload["result"]) == RESULT_KEYS
    assert payload["result"]["model"] == CONFIGURED_MODEL


def test_the_ran_on_label_is_the_only_change_the_setting_makes_to_the_result(
    engine_run, auth_client, user, workspace, organization, settings
):
    template = _template(organization, workspace, "AgentEvaluator")
    _, evaluation = _run_async(template, user, workspace, organization)
    settings.EVAL_RAN_ON_LABEL_ENABLED = False
    off = _get_result(auth_client, evaluation).json()
    settings.EVAL_RAN_ON_LABEL_ENABLED = True
    on = _get_result(auth_client, evaluation).json()

    assert on["result"]["result"].pop("ran_on_label", None) == "Falcon AI"
    assert on == off


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
def test_with_the_ran_on_label_on_the_result_names_the_model_an_agent_ran_on(
    chosen,
    ran_on,
    label,
    engine_run,
    auth_client,
    user,
    workspace,
    organization,
    settings,
):
    template = _template(organization, workspace, "AgentEvaluator")
    engine_run.evaluator = SimpleNamespace(ran_on_model=ran_on)
    _, evaluation = _run_async(template, user, workspace, organization)
    assert json.loads(evaluation.metadata).get("ran_on_model") == ran_on
    # The model the request named. The run was given CONFIGURED_MODEL.
    Evaluation.no_workspace_objects.filter(pk=evaluation.pk).update(model_name=chosen)
    settings.EVAL_RAN_ON_LABEL_ENABLED = True

    response = _get_result(auth_client, evaluation)

    body = response.content.decode()
    assert "ran_on_model" not in body
    assert RAN_ON_MODEL not in body
    assert MULTIMODAL_MODEL not in body
    result = response.json()["result"]["result"]
    assert result["model"] == chosen
    assert "ran_on_label" in result
    assert result["ran_on_label"] == label


def test_reading_the_ran_on_label_leaves_the_charge_rows_as_they_were(
    engine_run, auth_client, user, workspace, organization, settings
):
    usage = pytest.importorskip("ee.usage.models.usage")
    template = _template(organization, workspace, "AgentEvaluator")
    _, evaluation = _run_async(template, user, workspace, organization)
    # The evaluation's charge, as the cost log records a standalone run.
    usage.APICallLog.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        user=user,
        cost=Decimal("0.00105"),
        status=APICallStatusChoices.SUCCESS.value,
        source="standalone_v2",
        source_id=str(template.id),
        config={"model": CONFIGURED_MODEL},
    )

    def charge_rows():
        return (
            list(usage.APICallLog.all_objects.order_by("log_id").values()),
            list(usage.UsageEventLog.objects.order_by("id").values()),
        )

    charged = charge_rows()
    settings.EVAL_RAN_ON_LABEL_ENABLED = True

    response = _get_result(auth_client, evaluation)

    assert response.json()["result"]["result"].get("ran_on_label") == "Falcon AI"
    assert charge_rows() == charged
    assert str(template.id) in {row["source_id"] for row in charged[0]}


@pytest.mark.parametrize(
    "given, ran_on, label",
    [
        (CONFIGURED_MODEL, RAN_ON_MODEL, "Falcon AI"),
        ("turing_small", "turing_small", None),
    ],
    ids=["ran-on-another-model", "ran-on-the-given-model"],
)
def test_with_the_ran_on_label_on_a_sync_result_names_the_model_it_was_given(
    given,
    ran_on,
    label,
    engine_run,
    auth_client,
    user,
    workspace,
    organization,
    settings,
):
    template = _template(organization, workspace, "AgentEvaluator")
    engine_run.evaluator = SimpleNamespace(ran_on_model=ran_on)
    _, evaluation = _run_sync(template, user, workspace, organization)
    # The sync path stores the model it was given as model, never as model_name.
    assert evaluation.model_name is None
    Evaluation.no_workspace_objects.filter(pk=evaluation.pk).update(model=given)
    settings.EVAL_RAN_ON_LABEL_ENABLED = True

    response = _get_result(auth_client, evaluation)

    body = response.content.decode()
    assert "ran_on_model" not in body
    assert RAN_ON_MODEL not in body
    result = response.json()["result"]["result"]
    assert result["model"] == given
    assert "ran_on_label" in result
    assert result["ran_on_label"] == label


def test_with_the_ran_on_label_off_a_sync_result_answers_as_before(
    engine_run, auth_client, user, workspace, organization, settings
):
    template = _template(organization, workspace, "AgentEvaluator")
    _, evaluation = _run_sync(template, user, workspace, organization)
    settings.EVAL_RAN_ON_LABEL_ENABLED = False

    response = _get_result(auth_client, evaluation)

    body = response.content.decode()
    assert "ran_on" not in body
    assert RAN_ON_MODEL not in body
    result = response.json()["result"]["result"]
    assert set(result) == RESULT_KEYS
    # As before: the result's model is model_name, which the sync path leaves empty.
    assert result["model"] is None


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        json.loads(JUDGE_METADATA),
        "The answer is grounded.",
        json.dumps([RAN_ON_MODEL]),
    ],
    ids=["no-metadata", "dict-metadata", "non-json-text", "json-non-object"],
)
def test_with_the_ran_on_label_on_a_result_whose_metadata_names_no_model_has_no_label(
    metadata, engine_run, auth_client, user, workspace, organization, settings
):
    template = _template(organization, workspace, "AgentEvaluator")
    _, evaluation = _run_async(template, user, workspace, organization)
    Evaluation.no_workspace_objects.filter(pk=evaluation.pk).update(metadata=metadata)
    settings.EVAL_RAN_ON_LABEL_ENABLED = True

    response = _get_result(auth_client, evaluation)

    payload = response.json()["result"]
    assert payload["eval_status"] == StatusChoices.COMPLETED
    assert payload["result"]["model"] == CONFIGURED_MODEL
    assert "ran_on_label" in payload["result"]
    assert payload["result"]["ran_on_label"] is None


def test_with_the_ran_on_label_on_a_failed_result_has_no_label(
    auth_client, user, workspace, organization, settings
):
    template = _template(organization, workspace, "AgentEvaluator")
    evaluation = Evaluation.no_workspace_objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        eval_template=template,
        model_name=CONFIGURED_MODEL,
        input_data=dict(INPUTS),
        eval_config={},
        status=StatusChoices.FAILED,
        error_message="The evaluation could not run.",
    )
    settings.EVAL_RAN_ON_LABEL_ENABLED = True

    response = _get_result(auth_client, evaluation)

    payload = response.json()["result"]
    assert payload["eval_status"] == StatusChoices.FAILED
    assert payload["result"]["error_message"] == "The evaluation could not run."
    assert "ran_on_label" in payload["result"]
    assert payload["result"]["ran_on_label"] is None


@pytest.mark.parametrize(
    "metadata, ran_on",
    [
        (json.dumps({"ran_on_model": RAN_ON_MODEL}), RAN_ON_MODEL),
        ({"ran_on_model": RAN_ON_MODEL}, RAN_ON_MODEL),
        ({"ran_on_model": [RAN_ON_MODEL]}, None),
    ],
    ids=["json-object", "dict", "not-a-name"],
)
def test_the_stored_ran_on_model_reads_as_a_name_or_none(metadata, ran_on):
    assert sdk_evaluations.stored_ran_on_model(metadata) == ran_on


@pytest.mark.parametrize(
    "eval_type_id, mode",
    [
        ("CustomPromptEvaluator", "sync"),
        ("DeterministicEvaluator", "sync"),
        ("CustomPromptEvaluator", "async"),
    ],
    ids=["llm-judge-sync", "deterministic-sync", "llm-judge-async"],
)
def test_with_the_ran_on_label_on_a_non_agent_result_naming_no_model_is_as_before(
    eval_type_id,
    mode,
    engine_run,
    auth_client,
    user,
    workspace,
    organization,
    settings,
):
    template = _template(organization, workspace, eval_type_id)
    engine_run.evaluator = SimpleNamespace()
    engine_run.metadata = JUDGE_METADATA
    runner = _run_sync if mode == "sync" else _run_async
    _, evaluation = runner(template, user, workspace, organization)
    # A request that named no model; the evaluator still reported one.
    Evaluation.no_workspace_objects.filter(pk=evaluation.pk).update(model_name=None)
    evaluation.refresh_from_db()
    assert evaluation.status == StatusChoices.COMPLETED, evaluation.error_message
    assert evaluation.model_name is None
    assert evaluation.model == CONFIGURED_MODEL
    settings.EVAL_RAN_ON_LABEL_ENABLED = False
    off = _get_result(auth_client, evaluation).json()
    settings.EVAL_RAN_ON_LABEL_ENABLED = True
    on = _get_result(auth_client, evaluation).json()

    assert on["result"]["result"].pop("ran_on_label", "absent") is None
    assert on == off
    assert off["result"]["result"]["model"] is None


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_with_the_ran_on_label_on_an_agent_result_naming_no_model_shows_the_given_one(
    mode, engine_run, auth_client, user, workspace, organization, settings
):
    template = _template(organization, workspace, "AgentEvaluator")
    runner = _run_sync if mode == "sync" else _run_async
    _, evaluation = runner(template, user, workspace, organization)
    # A request that named no model; the agent evaluator records the one it
    # was given as model.
    Evaluation.no_workspace_objects.filter(pk=evaluation.pk).update(model_name=None)
    evaluation.refresh_from_db()
    assert evaluation.status == StatusChoices.COMPLETED, evaluation.error_message
    assert evaluation.model_name is None
    assert evaluation.model == CONFIGURED_MODEL
    settings.EVAL_RAN_ON_LABEL_ENABLED = True

    response = _get_result(auth_client, evaluation)

    result = response.json()["result"]["result"]
    assert result["model"] == CONFIGURED_MODEL
    assert result["ran_on_label"] == "Falcon AI"


@pytest.fixture
def run_config_engine_run(engine_run, monkeypatch):
    """engine_run, giving the agent the model the request's run_config names
    instead of the requested one, as the engine does
    (evaluations/engine/instance.py)."""
    run = engine.run_eval

    def _run(request):
        result = run(request)
        result.model_used = request.runtime_config["run_config"]["model"]
        return result

    monkeypatch.setattr(engine, "run_eval", _run)
    return engine_run


@pytest.mark.parametrize(
    "ran_on, label",
    [(RUN_CONFIG_MODEL, None), (RAN_ON_MODEL, "Falcon AI")],
    ids=["ran-on-the-given-model", "ran-on-another-model"],
)
def test_with_the_ran_on_label_on_a_result_given_a_run_config_model_compares_with_it(
    ran_on,
    label,
    run_config_engine_run,
    auth_client,
    user,
    workspace,
    organization,
    settings,
):
    template = _template(organization, workspace, "AgentEvaluator")
    run_config_engine_run.evaluator = SimpleNamespace(ran_on_model=ran_on)
    # The request names CONFIGURED_MODEL, and its config's run_config names the
    # model the agent is given.
    _, evaluation = _run_async(
        template,
        user,
        workspace,
        organization,
        eval_config={"run_config": {"model": RUN_CONFIG_MODEL}},
    )
    assert evaluation.status == StatusChoices.COMPLETED, evaluation.error_message
    assert evaluation.model_name == CONFIGURED_MODEL
    assert evaluation.model == RUN_CONFIG_MODEL
    assert json.loads(evaluation.metadata)["ran_on_model"] == ran_on
    settings.EVAL_RAN_ON_LABEL_ENABLED = True

    response = _get_result(auth_client, evaluation)

    body = response.content.decode()
    assert "ran_on_model" not in body
    assert RAN_ON_MODEL not in body
    result = response.json()["result"]["result"]
    assert result["model"] == CONFIGURED_MODEL
    assert "ran_on_label" in result
    assert result["ran_on_label"] == label


def test_with_the_ran_on_label_off_a_result_given_a_run_config_model_answers_as_before(
    run_config_engine_run, auth_client, user, workspace, organization, settings
):
    template = _template(organization, workspace, "AgentEvaluator")
    run_config_engine_run.evaluator = SimpleNamespace(ran_on_model=RUN_CONFIG_MODEL)
    _, evaluation = _run_async(
        template,
        user,
        workspace,
        organization,
        eval_config={"run_config": {"model": RUN_CONFIG_MODEL}},
    )
    assert evaluation.status == StatusChoices.COMPLETED, evaluation.error_message
    assert evaluation.model_name == CONFIGURED_MODEL
    assert evaluation.model == RUN_CONFIG_MODEL
    settings.EVAL_RAN_ON_LABEL_ENABLED = False

    response = _get_result(auth_client, evaluation)

    body = response.content.decode()
    assert "ran_on" not in body
    result = response.json()["result"]["result"]
    assert set(result) == RESULT_KEYS
    # As before: the result's model is the requested one.
    assert result["model"] == CONFIGURED_MODEL


def test_with_the_ran_on_label_setting_absent_the_result_names_the_model_an_agent_ran_on(
    engine_run, auth_client, user, workspace, organization, settings
):
    # The label is on by default; only an explicit False turns it off.
    template = _template(organization, workspace, "AgentEvaluator")
    _, evaluation = _run_async(template, user, workspace, organization)
    if hasattr(settings, "EVAL_RAN_ON_LABEL_ENABLED"):
        del settings.EVAL_RAN_ON_LABEL_ENABLED

    response = _get_result(auth_client, evaluation)

    body = response.content.decode()
    assert "ran_on_model" not in body
    assert RAN_ON_MODEL not in body
    result = response.json()["result"]["result"]
    assert result["model"] == CONFIGURED_MODEL
    assert result["ran_on_label"] == "Falcon AI"


def test_with_the_ran_on_label_setting_absent_a_sync_result_names_the_model_it_ran_on(
    engine_run, auth_client, user, workspace, organization, settings
):
    template = _template(organization, workspace, "AgentEvaluator")
    _, evaluation = _run_sync(template, user, workspace, organization)
    if hasattr(settings, "EVAL_RAN_ON_LABEL_ENABLED"):
        del settings.EVAL_RAN_ON_LABEL_ENABLED

    response = _get_result(auth_client, evaluation)

    result = response.json()["result"]["result"]
    assert result["model"] == CONFIGURED_MODEL
    assert result["ran_on_label"] == "Falcon AI"
