"""Jev over the SDK: typed error codes, the Jev billing shape, and the
configure-time model gate."""

from unittest.mock import patch

import httpx
import pytest

pytestmark = pytest.mark.requires_ee

JEV_OK = {
    "model": "jev-1.13.0",
    "answers": {"q1": {"type": "noul", "noul": 0.91}},
    "usage": {"input_tokens": 320, "output_tokens": 20},
}


@pytest.fixture
def jev_gateway(monkeypatch):
    """A gateway address and internal key for the default Jev transport. Every
    test patches httpx.post, so neither is ever dialled."""
    for name in ("AGENTCC_INTERNAL_URL", "AGENTCC_GATEWAY_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(
        "AGENTCC_GATEWAY_INTERNAL_URL", "http://agentcc-gateway.test:8080"
    )
    monkeypatch.setenv("AGENTCC_INTERNAL_API_KEY", "internal-gateway-key")


@pytest.fixture
def jev_template(auth_client, user):
    """A saved Jev Pass/Fail template, and the org's TypeSafe key."""
    from model_hub.models.api_key import ApiKey
    from model_hub.models.evals_metric import EvalTemplate

    with patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False):
        created = auth_client.post(
            "/model-hub/eval-templates/create-v2/",
            {
                "name": "jev-sdk",
                "instructions": "Judge {{input}}",
                "model": "jev-latest",
                "output_type": "pass_fail",
                "jev_mapping": {},
            },
            format="json",
        )
    assert created.status_code == 200, created.data
    ApiKey.objects.create(
        provider="typesafe", organization=user.organization, key="org-typesafe-key"
    )
    return EvalTemplate.objects.get(id=created.data["result"]["id"])


def _run(auth_client, template, model, response):
    with (
        patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False),
        patch("tfc.capabilities.service.check_or_raise"),
        patch("httpx.post", return_value=response) as post,
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        result = auth_client.post(
            "/sdk/api/v1/new-eval/",
            {
                "eval_name": template.name,
                "inputs": {"input": "Agent: I issued a full refund."},
                **({"model": model} if model else {}),
            },
            format="json",
        )
    return result, post, emit


# The eval path closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
def test_unknown_jev_model_carries_its_typed_code(auth_client, jev_template):
    response, post, emit = _run(
        auth_client, jev_template, "jev-2.0.0", httpx.Response(200, json=JEV_OK)
    )
    assert response.status_code == 400, response.data
    assert response.data["code"] == "JEV_MODEL_UNKNOWN"
    assert response.data["attr"] == "model"
    assert response.data["details"] == {"model": ["JEV_MODEL_UNKNOWN"]}
    assert response.data["feature"] == "jev"
    assert "jev-2.0.0" in response.data["message"]
    post.assert_not_called()
    emit.assert_not_called()


# The eval path closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
def test_rejected_key_carries_its_typed_code_and_says_what_to_fix(
    auth_client, jev_template, jev_gateway
):
    rejected = httpx.Response(
        403,
        json={"error": {"code": "provider_key_rejected", "message": "upstream"}},
    )
    response, post, emit = _run(auth_client, jev_template, "jev-latest", rejected)
    assert response.status_code == 400, response.data
    assert response.data["code"] == "JEV_KEY_INVALID"
    assert response.data["message"] == (
        "TypeSafe rejected the API key saved under Settings → AI Providers. "
        "Update the key and try again."
    )
    assert post.call_count == 1
    for secret in ("org-typesafe-key", "internal-gateway-key", "upstream"):
        assert secret not in str(response.data)
    # A failed Jev run emits no billing event.
    emit.assert_not_called()


# The eval path closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
def test_no_model_run_bills_as_jev_whatever_model_typesafe_reports(
    auth_client, jev_template, jev_gateway
):
    """The template's jev-latest ran and TypeSafe reported a newer model: the
    run still bills as Jev, with the metering event keyed to its log."""
    from ee.jev.metering import usage_event_id
    from ee.usage.models.usage import APICallLog
    from ee.usage.services.config import BillingConfig

    # Saved from the detail page, as a template is before it runs without a
    # model: the save keeps the model in config, where the engine reads it.
    jev_template.config = {**jev_template.config, "model": "jev-latest"}
    jev_template.save(update_fields=["config"])
    newer = {**JEV_OK, "model": "jev-1.14.0"}
    response, _, emit = _run(
        auth_client, jev_template, None, httpx.Response(200, json=newer)
    )
    assert response.status_code == 200, response.data
    log = APICallLog.objects.get(source="standalone_v2", source_id=jev_template.id)
    [event] = [call.args[0] for call in emit.call_args_list]
    billing = BillingConfig.get()
    assert event.event_type == "jev_evaluator"
    assert event.amount == billing.calculate_ai_credits(billing.get_eval_per_run_fee())
    assert event.event_id == usage_event_id(str(log.log_id), 1)
    assert event.properties["requested_model"] == "jev-latest"
    assert event.properties["actual_model"] == "jev-1.14.0"
    [evaluation] = response.data["result"][0]["evaluations"]
    assert evaluation["reason"] == "Jev probability 0.91 vs threshold 0.50 → Pass"


@pytest.mark.django_db
def test_configure_evaluations_rejects_an_unknown_jev_model(auth_client, jev_template):
    from tracer.models.external_eval_config import ExternalEvalConfig

    with patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False):
        response = auth_client.post(
            "/sdk/api/v1/configure-evaluations/",
            {
                "eval_config": {
                    "eval_templates": jev_template.name,
                    "inputs": {"input": "Agent: I issued a full refund."},
                    "model_name": "jev-2.0.0",
                },
                "platform": "langfuse",
                "custom_eval_name": "jev-external",
            },
            format="json",
        )
    assert response.status_code == 400, response.data
    assert response.data["code"] == "JEV_MODEL_UNKNOWN"
    assert not ExternalEvalConfig.objects.filter(eval_template=jev_template).exists()


# The eval path closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
def test_a_run_time_jev_error_keeps_its_own_code(
    auth_client, jev_template, jev_gateway
):
    """A media link as an input fails with its own code and the details
    naming the rule, and nothing is sent to TypeSafe or billed."""
    with (
        patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False),
        patch("tfc.capabilities.service.check_or_raise"),
        patch("httpx.post") as post,
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        response = auth_client.post(
            "/sdk/api/v1/new-eval/",
            {
                "eval_name": jev_template.name,
                "inputs": {"input": "https://example.invalid/files/invoice.pdf"},
                "model": "jev-latest",
            },
            format="json",
        )
    assert response.status_code == 400, response.data
    assert response.data["code"] == "JEV_INPUT_UNSUPPORTED"
    assert response.data["details"] == {"state": ["JEV_INPUT_UNSUPPORTED"]}
    assert response.data["attr"] == "state"
    post.assert_not_called()
    emit.assert_not_called()


# The eval path closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
def test_an_async_run_records_the_jev_message(user, workspace, jev_template):
    """An async SDK run stores the same fixed text the sync response shows."""
    from ee.jev.client import JevGatewayError
    from model_hub.models.evaluation import Evaluation, StatusChoices
    from tfc.temporal.evaluations.activities import _run_single_evaluation_sync

    evaluation = Evaluation.objects.create(
        user=user,
        organization=user.organization,
        workspace=workspace,
        eval_template=jev_template,
        model_name="jev-latest",
        input_data={"input": "Agent: I issued a full refund."},
        eval_config={},
    )
    with (
        patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False),
        patch("tfc.capabilities.service.check_or_raise"),
        patch(
            "ee.jev.client.JevSystemOneClient.evaluate",
            side_effect=JevGatewayError("JEV_RATE_LIMITED", True, "3"),
        ),
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        _run_single_evaluation_sync(str(evaluation.id))
    evaluation.refresh_from_db()
    assert evaluation.status == StatusChoices.FAILED
    assert evaluation.error_message == (
        "TypeSafe's rate limit was reached. Try again shortly."
    )
    emit.assert_not_called()


@pytest.mark.parametrize(
    "model,expected,warns",
    [
        ("jev-latest", "jev_evaluator", False),
        ("jev-1.13.0", "jev_evaluator", False),
        ("gpt-5", "turing_large_evaluator", True),
    ],
)
def test_jev_has_its_own_legacy_call_type(model, expected, warns):
    from sdk.utils import helpers

    # Patch this module's logger rather than reconfiguring structlog, which is
    # global and would leak into whatever test runs next.
    with patch.object(helpers, "logger") as logger:
        api_call_type = helpers._get_api_call_type(model)
    assert api_call_type == expected
    warned = any(
        call.args[:1] == ("unknown_model_for_api_call_type",)
        for call in logger.warning.call_args_list
    )
    assert warned is warns
