"""Typed decision contracts: no live provider calls or customer credentials."""

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.test import override_settings

from ee.decisions.client import DecisionClient
from ee.decisions.errors import (
    DecisionGatewayError,
    DecisionMappingError,
    DecisionResponseError,
)
from ee.decisions.evaluator import DecisionEvaluator
from ee.decisions.openai import normalize_response, request_body
from ee.evals.llm.jev_evaluator.evaluator import JevEvaluator
from evaluations.decision_models import (
    DECISION_MODELS,
    get_decision_model,
    model_groups,
)
from evaluations.decision_pricing import calculate_decision_cost
from evaluations.engine.instance import create_eval_instance
from model_hub.utils.jev_templates import validate_jev_template_save
from sdk.utils.helpers import _emit_jev_usage, _is_jev_evaluator
from tfc.ee_gates import describe_jev_error

pytestmark = pytest.mark.requires_ee
OR_MODEL = "decision/openrouter/typesafe/jev-1.14"
OPENAI = "decision/openai/gpt-6-luna"
PERPLEXITY = "decision/perplexity/pplx-decider-v1.2"
QUESTION = {
    "q1": {"type": "noul", "instructions": "Does the reply answer the question?"}
}


def reply(model="typesafe/jev-1.14-20261014", **extra):
    return {
        "model": model,
        "answers": {"q1": {"type": "noul", "noul": 0.8}},
        "usage": {"input_tokens": 100, "output_tokens": 0},
        **extra,
    }


def test_catalog_matches_observed_router_endpoint_snapshot():
    from pathlib import Path

    fixture = (
        Path(__file__).resolve().parents[4]
        / "api_contracts/evaluations/decision-models.json"
    )
    observed = json.loads(fixture.read_text())["canonical_models"]
    router = {
        spec.upstream_model: spec
        for spec in DECISION_MODELS.values()
        if spec.connection == "openrouter"
    }
    assert set(router) == set(observed)
    for model, canonical in observed.items():
        if not model.startswith("~"):
            assert canonical in router[model].accepted_models


def test_catalog_deduplicates_connections_and_excludes_extractions():
    groups = model_groups({"openrouter"})
    assert len(groups) == 35
    assert len(DECISION_MODELS) == 43
    jev = next(group for group in groups if group["model_name"] == "typesafe/jev-1.14")
    assert [(c["provider"], c["is_available"]) for c in jev["connections"]] == [
        ("typesafe", False),
        ("openrouter", True),
    ]
    assert all("clairic" not in group["model_name"] for group in groups)
    assert get_decision_model("gpt-6-luna") is None
    assert get_decision_model("openrouter/typesafe/jev-1.14") is None
    assert all(spec.accepted_models for spec in DECISION_MODELS.values())


@pytest.mark.parametrize("model", sorted(DECISION_MODELS))
def test_every_catalog_route_has_explicit_transport_and_decision_pricing(model):
    spec = DECISION_MODELS[model]
    assert calculate_decision_cost(model, {"input_tokens": 100})["total_cost"] >= 0
    if spec.connection == "typesafe":
        from tfc.ee_gates import is_jev_model

        assert is_jev_model(model)
        return
    if spec.unavailable_reason:
        with pytest.raises(DecisionMappingError, match="DECISION_MODEL_UNAVAILABLE"):
            DecisionClient(model=model, api_key="synthetic-route-key")
        return
    client = DecisionClient(model=model, api_key="synthetic-route-key")
    with patch.object(
        client, "_gateway_url", return_value="http://gateway/v1/systemone"
    ):
        url, body, gateway = client._transport(
            {"model": model, "state": "text", "questions": QUESTION}
        )
    assert gateway is True
    assert body["model"] == spec.gateway_model
    assert url.endswith(
        "/v1/decisions" if spec.connection == "openai" else "/v1/systemone"
    )
    assert model.startswith("decision/" + spec.connection + "/")


@pytest.mark.parametrize(
    "model",
    [
        "decision/openrouter/unknown",
        "decision/https://localhost/model",
        "decision/openai/../gpt-6-luna",
    ],
)
def test_unknown_route_refuses_before_transport(model):
    with pytest.raises(DecisionMappingError, match="DECISION_MODEL_UNKNOWN"):
        DecisionClient(model=model, api_key="synthetic")


@pytest.mark.parametrize(
    "model,actual,accepted",
    [
        (
            "decision/openrouter/~typesafe/jev-latest",
            "typesafe/jev-1.15-20261201",
            True,
        ),
        (
            "decision/openrouter/~perplexity/pplx-decider-latest",
            "perplexity/pplx-decider-v1.3",
            True,
        ),
        (
            "decision/openrouter/~perplexity/pplx-decider-latest",
            "perplexity/pplx-decider-flash-v1.3",
            False,
        ),
        ("decision/openrouter/~liquid/d1-latest", "liquid/d1.2-20261201", True),
        ("decision/openrouter/~liquid/d1-latest", "liquid/d1-ultra-20261201", False),
        (OR_MODEL, "typesafe/jev-1.15", False),
    ],
)
def test_latest_can_advance_only_within_explicit_model_family(model, actual, accepted):
    assert get_decision_model(model).accepts_model(actual) is accepted


@pytest.mark.parametrize(
    "upstream", ["pplx-decider-latest", "pplx-decider-flash-latest"]
)
def test_native_perplexity_may_report_the_exact_requested_alias(upstream):
    spec = get_decision_model(f"decision/perplexity/{upstream}")
    assert spec.accepts_model(upstream)
    other = (
        "pplx-decider-latest" if "flash" in upstream else "pplx-decider-flash-latest"
    )
    assert not spec.accepts_model(other)


def test_latest_families_do_not_accept_another_model():
    liquid = get_decision_model("decision/openrouter/~liquid/d1-latest")
    assert "liquid/d1.1-20261027" in liquid.accepted_models
    assert "liquid/d1-ultra-20261015" not in liquid.accepted_models
    decider = get_decision_model("decision/openrouter/~perplexity/pplx-decider-latest")
    assert not any("flash" in item for item in decider.accepted_models)


@pytest.mark.parametrize(
    "model,provider,endpoint,upstream",
    [
        (OR_MODEL, "openrouter", "/v1/systemone", "openrouter/typesafe/jev-1.14"),
        (OPENAI, "openai", "/v1/decisions", "openai/gpt-6-luna"),
        (PERPLEXITY, "perplexity", "/v1/systemone", "perplexity/pplx-decider-v1.2"),
    ],
)
@override_settings(
    JEV_TRANSPORT="direct", AGENTCC_INTERNAL_API_KEY="synthetic-internal"
)
def test_explicit_gateway_connection_ignores_jev_direct_setting(
    model, provider, endpoint, upstream
):
    client = DecisionClient(model=model, api_key="synthetic-org-key")
    with patch.object(
        client, "_gateway_url", return_value="http://gateway/v1/systemone"
    ):
        url, body, gateway = client._transport(
            {"model": model, "state": {"reply": "yes"}, "questions": QUESTION}
        )
    assert url == "http://gateway" + endpoint
    assert body["model"] == upstream and gateway is True
    assert client._headers(True)["x-agentcc-provider-key"] == "synthetic-org-key"
    if provider == "openai":
        assert body["questions"][0]["type"] == "predicate"
        assert json.loads(body["input"]) == {"reply": "yes"}
    else:
        assert body["questions"] == QUESTION


@pytest.mark.parametrize("actual", ["typesafe/jev-1.14", "typesafe/jev-1.14-20261014"])
def test_router_accepts_only_reviewed_model_identity(actual):
    client = DecisionClient(model=OR_MODEL, api_key="synthetic")
    with patch.object(client, "_request", return_value=reply(actual)) as request:
        assert client.evaluate(OR_MODEL, "text", QUESTION)["model"] == actual
    request.assert_called_once()


@pytest.mark.parametrize(
    "body,code",
    [
        (reply("typesafe/jev-1.13"), "DECISION_MODEL_MISMATCH"),
        (reply(answers={"q1": {"type": "refusal"}}), "DECISION_REFUSED"),
    ],
)
def test_bad_success_is_final_not_retried(body, code):
    client = DecisionClient(model=OR_MODEL, api_key="synthetic")
    with patch.object(client, "_request", return_value=body) as request:
        with pytest.raises(DecisionResponseError, match=code):
            client.evaluate(OR_MODEL, "text", QUESTION)
    request.assert_called_once()


def test_utf8_limits_fail_before_provider_instead_of_truncating():
    model = "decision/openrouter/cloudflare/clef"
    client = DecisionClient(model=model, api_key="synthetic")
    with patch.object(client, "_request") as request:
        with pytest.raises(DecisionMappingError, match="DECISION_INPUT_TOO_LARGE"):
            client.evaluate(model, "界" * 1000, QUESTION)
    request.assert_not_called()


@pytest.mark.parametrize("key", [None, "", " ", {"api_key": "not-a-text-key"}])
def test_missing_or_wrong_credential_shape_is_refused(key):
    with pytest.raises(DecisionGatewayError, match="DECISION_KEY_MISSING"):
        DecisionClient(model=OR_MODEL, api_key=key)


def test_openai_choice_and_score_translation_preserves_authored_content():
    questions = {
        "q1": {
            "type": "choice",
            "instructions": ["Judge", "the state"],
            "criteria": {"Yes": "supported", "No": None},
        },
        "q2": {"type": "score", "instructions": "Grade", "criteria": ["bad", "good"]},
    }
    body = request_body({"state": "text", "questions": questions}, "openai/gpt-6-luna")
    assert body["questions"][0]["choices"] == [
        {"value": "Yes", "description": "supported"},
        {"value": "No"},
    ]
    assert body["questions"][1]["levels"] == [
        {"label": "0", "description": "bad"},
        {"label": "1", "description": "good"},
    ]
    result = normalize_response(
        {
            "model": "gpt-6-luna",
            "answers": [
                {
                    "name": "q2",
                    "type": "score",
                    "score": 0.8,
                    "confidence": 0.5,
                    "probabilities": [
                        {"value": 0, "label": "0", "probability": 0.2},
                        {"value": 1, "label": "1", "probability": 0.8},
                    ],
                },
                {
                    "name": "q1",
                    "type": "choice",
                    "choice": "Yes",
                    "confidence": 0.7,
                    "probabilities": [
                        {"value": "Yes", "probability": 0.8},
                        {"value": "No", "probability": 0.2},
                    ],
                },
            ],
        },
        questions,
    )
    assert result["answers"]["q1"]["probabilities"] == {"Yes": 0.8, "No": 0.2}
    assert result["answers"]["q2"]["score"] == 0.8
    assert result["answers"]["q2"]["legend"] == {"0": "bad", "1": "good"}


@pytest.mark.parametrize(
    "answer",
    [
        {"name": "wrong", "type": "predicate", "probability": 0.5},
        {"name": "q1", "type": "score", "score": 0.5},
        {"name": "q1", "type": "refusal"},
    ],
)
def test_openai_invalid_or_refused_answer_never_becomes_zero(answer):
    with pytest.raises(DecisionResponseError):
        normalize_response({"answers": [answer]}, QUESTION)


def test_non_jev_evaluator_has_general_billing_identity_and_typed_metadata():
    client = Mock(gateway_request_id="synthetic-request")
    non_jev = "decision/openrouter/morph/reflex"
    client.evaluate.return_value = reply(
        model="morph/reflex",
        usage={"input_tokens": 100, "output_tokens": 0, "cost": 0.0000042},
    )
    evaluator = DecisionEvaluator(
        rule_prompt="Judge {{input}}",
        model=non_jev,
        required_keys=["input"],
        client=client,
    )
    result = evaluator._evaluate(input="synthetic input")
    assert not isinstance(evaluator, JevEvaluator)
    assert not _is_jev_evaluator(evaluator)
    assert not _emit_jev_usage(evaluator, org_id="org", run_id="run")
    metadata = json.loads(result["metadata"])
    assert "jev" not in metadata
    assert metadata["decision"]["connection"] == "openrouter"
    assert metadata["decision"]["requested_model"] == non_jev
    assert evaluator.cost["total_cost"] == 0.0000042
    assert result["metrics"][0]["value"] == 0.8


@pytest.mark.parametrize(
    "model,output,choices",
    [
        ("decision/openrouter/respan/span-01", "choices", {"a": 0, "b": 1}),
        (
            "decision/openrouter/togethercomputer/tev1-4b-experimental",
            "choices",
            {"a": 1},
        ),
    ],
)
def test_unsupported_question_shapes_rejected_on_save(model, output, choices):
    config = {"model": model, "output": output, "eval_type_id": "CustomPromptEvaluator"}
    response = validate_jev_template_save(
        config, model=model, previous_model=model, choice_scores=choices
    )
    assert response.status_code == 400
    assert response.data["code"] == "DECISION_MAPPING_INVALID"


def test_dispatch_reads_selected_provider_not_typesafe_or_chat():
    template = SimpleNamespace(
        config={
            "model": OPENAI,
            "eval_type_id": "CustomPromptEvaluator",
            "rule_prompt": "Judge {{input}}",
            "required_keys": ["input"],
            "output": "Pass/Fail",
        },
        organization=SimpleNamespace(id="trusted-org"),
        workspace=SimpleNamespace(id="trusted-workspace"),
        choice_scores=None,
        choices=[],
        pass_threshold=0.5,
        multi_choice=False,
    )
    with (
        patch("evaluations.engine.instance.resolve_version", return_value=None),
        patch(
            "evaluations.engine.instance._get_decision_api_key",
            return_value="synthetic",
        ) as key,
        patch("evaluations.engine.instance._get_typesafe_api_key") as typesafe,
        patch("evaluations.engine.instance._get_api_key") as chat,
        patch("tfc.capabilities.service.check_or_raise") as capability,
    ):
        evaluator, _ = create_eval_instance(Mock(), template, model=None)
    assert isinstance(evaluator, DecisionEvaluator)
    key.assert_called_once_with(OPENAI, "trusted-org", "trusted-workspace")
    typesafe.assert_not_called()
    chat.assert_not_called()
    capability.assert_not_called()
    assert evaluator.api_key is None and "api_key" not in evaluator._jev_config


def test_router_jev_keeps_flat_fee_but_records_correct_connection():
    from ee.jev.metering import build_usage_events

    client = Mock(gateway_request_id="synthetic-request")
    client.evaluate.return_value = reply(
        usage={"input_tokens": 100, "output_tokens": 0, "cost": 0.0000042}
    )
    client.attempts = [
        {
            "attempt": 1,
            "outcome": "success",
            "usage": {"input_tokens": 100, "output_tokens": 0},
        }
    ]
    evaluator = DecisionEvaluator(
        rule_prompt="Judge {{input}}",
        model=OR_MODEL,
        required_keys=["input"],
        client=client,
    )
    evaluator._evaluate(input="synthetic input")
    assert _is_jev_evaluator(evaluator)
    assert evaluator.cost["total_cost"] == 0
    event = build_usage_events(
        evaluator, org_id="org", cell_or_run_id="run", amount=0.06
    )[0]
    assert event.properties["provider"] == "openrouter"
    assert float(event.amount) == 0.06


@pytest.mark.parametrize(
    "model,cost", [(OPENAI, 0.1), (PERPLEXITY, 0.02), ("jev-latest", 0), (OR_MODEL, 0)]
)
def test_decision_pricing_never_uses_chat_prices(model, cost):
    assert (
        calculate_decision_cost(model, {"input_tokens": 1000000})["total_cost"] == cost
    )


def test_key_error_names_connection_without_provider_body():
    from ee.jev.client import JevGatewayError

    error = DecisionGatewayError("openrouter", JevGatewayError("JEV_KEY_INVALID"))
    described = describe_jev_error(error)
    assert described["code"] == "DECISION_KEY_INVALID"
    assert "OpenRouter" in described["message"]
    assert "TypeSafe" not in described["message"]
