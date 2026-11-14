"""External (Langfuse) evals bill a Jev run like every other path: one
ee.jev.metering event at the flat fee after the run succeeds, and nothing for
an unknown Jev model, which is rejected before anything is logged or billed."""

from unittest.mock import patch

import httpx
import pytest

pytestmark = pytest.mark.requires_ee

JEV_OK = {
    "model": "jev-1.13.0",
    "answers": {"q1": {"type": "noul", "noul": 0.91}},
    "usage": {"input_tokens": 320, "output_tokens": 20},
}

# Placeholder Langfuse settings: the config refuses to save without them, and
# every test patches the send, so they are never used.
LANGFUSE = {
    "langfuse_secret_key": "sk-lf-test",
    "langfuse_public_key": "pk-lf-test",
    "langfuse_host": "http://langfuse.test",
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


def _jev_template(auth_client, name="jev-external"):
    from model_hub.models.evals_metric import EvalTemplate

    with patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False):
        created = auth_client.post(
            "/model-hub/eval-templates/create-v2/",
            {
                "name": name,
                "instructions": "Judge {{input}}",
                "model": "jev-latest",
                "output_type": "pass_fail",
                "jev_mapping": {},
            },
            format="json",
        )
    assert created.status_code == 200, created.data
    template = EvalTemplate.objects.get(id=created.data["result"]["id"])
    # Saved from the detail page, as a template is before it runs without a
    # model: the save keeps the model in config, where the engine reads it.
    template.config = {**template.config, "model": "jev-latest"}
    template.save(update_fields=["config"])
    return template


def _config(auth_client, user, workspace, model, template=None):
    from model_hub.models.api_key import ApiKey
    from tracer.models.external_eval_config import ExternalEvalConfig, StatusChoices

    template = template or _jev_template(auth_client)
    ApiKey.objects.create(
        provider="typesafe", organization=user.organization, key="org-typesafe-key"
    )
    return ExternalEvalConfig.objects.create(
        organization=user.organization,
        workspace=workspace,
        eval_template=template,
        name="jev-external",
        config={},
        mapping={"input": "Agent: I issued a full refund."},
        model=model,
        platform="langfuse",
        credentials=dict(LANGFUSE),
        status=StatusChoices.PENDING,
    )


def _execute(config, typesafe=None):
    """Run the config once; returns the error it raised (or None), the usage
    events emitted, and the TypeSafe and Langfuse mocks. `typesafe` is what
    the gateway answers, a success by default."""
    from tracer.utils.external_eval import _execute_evaluation_on_external_platform

    error = None
    with (
        patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False),
        patch("tfc.capabilities.service.check_or_raise"),
        patch(
            "httpx.post", return_value=typesafe or httpx.Response(200, json=JEV_OK)
        ) as post,
        patch("ee.usage.services.emitter.emit") as emit,
        patch("tracer.utils.external_eval._send_eval_result") as send,
    ):
        try:
            _execute_evaluation_on_external_platform(config)
        except Exception as exc:
            error = exc
    return error, [call.args[0] for call in emit.call_args_list], post, send


# The eval path closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("model", ["jev-latest", None])
def test_a_jev_run_bills_once_with_the_metering_event(
    auth_client, user, workspace, jev_gateway, model
):
    from ee.jev.metering import usage_event_id
    from ee.usage.models.usage import APICallLog
    from ee.usage.services.config import BillingConfig

    config = _config(auth_client, user, workspace, model)
    error, events, post, send = _execute(config)
    assert error is None
    assert post.call_count == 1
    send.assert_called_once()
    log = APICallLog.objects.get(source="tracer", source_id=config.id)
    # Logged as a Jev run, not Turing Large, even when only the template names
    # the model, so the legacy per-call rate limit never applies.
    assert log.api_call_type.name == "jev_evaluator"
    billing = BillingConfig.get()
    # No up-front Turing Large charge: only the Jev event, after the run.
    [event] = events
    assert event.event_type == "jev_evaluator"
    assert event.amount == billing.calculate_ai_credits(billing.get_eval_per_run_fee())
    assert event.event_id == usage_event_id(str(log.log_id), 1)
    assert event.org_id == str(user.organization.id)
    assert event.properties["requested_model"] == "jev-latest"
    assert event.properties["mapping_revision"].startswith("jev-map-v1:")
    config.refresh_from_db()
    assert config.eval_results["value"] == "Passed"
    assert config.eval_results["reason"] == (
        "Jev probability 0.91 vs threshold 0.50 → Pass"
    )


# The eval path closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
def test_a_failed_jev_usage_emit_is_logged_and_the_run_still_lands(
    auth_client, user, workspace, jev_gateway
):
    from tracer.utils.external_eval import _execute_evaluation_on_external_platform

    config = _config(auth_client, user, workspace, "jev-latest")
    with (
        patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False),
        patch("tfc.capabilities.service.check_or_raise"),
        patch("httpx.post", return_value=httpx.Response(200, json=JEV_OK)),
        patch("ee.usage.services.emitter.emit", side_effect=RuntimeError("redis down")),
        patch("tracer.utils.external_eval._send_eval_result") as send,
        patch("tracer.utils.external_eval.logger") as logger,
    ):
        _execute_evaluation_on_external_platform(config)
    send.assert_called_once()
    warned = [c for c in logger.warning.call_args_list if c.args]
    assert [c.args[0] for c in warned] == ["jev_usage_emit_failed"]
    assert warned[0].kwargs["exc_info"] is True


# The eval path closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
def test_an_unknown_jev_model_is_rejected_before_anything_is_billed(
    auth_client, user, workspace, jev_gateway
):
    from ee.jev.mapping import JevMappingError
    from ee.usage.models.usage import APICallLog

    config = _config(auth_client, user, workspace, "jev-2.0.0")
    error, events, post, send = _execute(config)
    assert isinstance(error, JevMappingError)
    assert error.code == "JEV_MODEL_UNKNOWN"
    assert events == []
    post.assert_not_called()
    send.assert_not_called()
    assert not APICallLog.objects.filter(source_id=config.id).exists()


def _gateway_error(status, code):
    """A gateway error envelope. Retry-After 0 keeps a retried run instant."""
    return httpx.Response(
        status,
        json={"error": {"type": "upstream_error", "code": code, "message": "x"}},
        headers={"Retry-After": "0"},
    )


# The eval path closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "answer,calls,message",
    [
        (
            _gateway_error(403, "provider_key_rejected"),
            1,
            "TypeSafe rejected the API key saved under Settings → AI Providers. "
            "Update the key and try again.",
        ),
        (
            _gateway_error(503, "provider_overloaded"),
            3,
            "TypeSafe couldn't complete this evaluation. Try again.",
        ),
    ],
)
def test_a_failed_jev_run_bills_nothing_and_records_its_message(
    auth_client, user, workspace, jev_gateway, answer, calls, message
):
    """The failure goes through the real client, so its attempts are recorded
    and billing them by mistake would emit events."""
    from tracer.models.external_eval_config import StatusChoices
    from tracer.utils.external_eval import run_external_eval_config

    config = _config(auth_client, user, workspace, "jev-latest")
    with (
        patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False),
        patch("tfc.capabilities.service.check_or_raise"),
        patch("httpx.post", return_value=answer) as post,
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        run_external_eval_config(str(config.id))
    assert post.call_count == calls
    config.refresh_from_db()
    assert config.status == StatusChoices.FAILED
    assert config.error_message == message
    emit.assert_not_called()


def _version_resolves(monkeypatch):
    """Let the engine read a template's default version, as it does once the
    version's use is counted without error. resolve_version still returns
    None today, because EvalTemplateVersion has no usage_count to update."""
    from model_hub.models.evals_metric import EvalTemplateVersion

    monkeypatch.setattr(
        "evaluations.engine.instance.resolve_version",
        lambda template, *args, **kwargs: EvalTemplateVersion.objects.get_default(
            template
        ),
    )


# The eval path closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
def test_a_version_that_runs_turing_is_billed_up_front(
    auth_client, user, workspace, jev_gateway, monkeypatch
):
    """The template's config names Jev but its default version names Turing
    Large, which the engine runs: billing follows the evaluator, not the
    template config."""
    from model_hub.models.evals_metric import EvalTemplateVersion

    config = _config(auth_client, user, workspace, None)
    version = EvalTemplateVersion.objects.get_default(config.eval_template)
    version.model = "turing_large"
    version.save(update_fields=["model"])
    _version_resolves(monkeypatch)
    with patch(
        "tracer.utils.external_eval._run_external_platform_evaluation",
        side_effect=RuntimeError("stop"),
    ):
        error, events, post, _ = _execute(config)
    assert str(error) == "stop"
    post.assert_not_called()
    [event] = events
    assert event.event_type == "turing_large_evaluator"


# The eval path closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
def test_a_version_that_runs_jev_is_billed_as_jev(
    auth_client, user, workspace, jev_gateway, monkeypatch
):
    """The template's config names Turing Large but its default version names
    Jev, which the engine runs: one Jev event, and no up-front Turing one."""
    from model_hub.models.evals_metric import EvalTemplateVersion

    config = _config(auth_client, user, workspace, None)
    template = config.eval_template
    assert EvalTemplateVersion.objects.get_default(template).model == "jev-latest"
    template.config = {**template.config, "model": "turing_large"}
    template.save(update_fields=["config"])
    _version_resolves(monkeypatch)
    error, events, post, _ = _execute(config)
    assert error is None
    assert post.call_count == 1
    [event] = events
    assert event.event_type == "jev_evaluator"


# The eval path closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
def test_a_non_jev_external_eval_still_bills_up_front(
    auth_client, user, workspace, jev_gateway
):
    """Only Jev runs move to after-run billing; other models keep the
    existing up-front event."""
    config = _config(auth_client, user, workspace, "jev-latest")
    config.model = "turing_large"
    config.save()
    with patch("tracer.utils.external_eval.EvaluationRunner") as runner:
        runner.return_value._create_eval_instance.side_effect = RuntimeError("stop")
        error, events, _, _ = _execute(config)
    assert str(error) == "stop"
    [event] = events
    assert event.event_type == "turing_large_evaluator"
    assert event.properties["source"] == "tracer"
    assert event.properties["source_id"] == str(config.id)


# The eval path closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
def test_a_version_on_an_unknown_jev_model_bills_nothing(
    auth_client, user, workspace, jev_gateway, monkeypatch
):
    """Only the default version names the unknown Jev model, so the engine
    rejects it as it builds the evaluator: nothing is billed up front."""
    from ee.jev.mapping import JevMappingError
    from model_hub.models.evals_metric import EvalTemplateVersion

    config = _config(auth_client, user, workspace, None)
    template = config.eval_template
    template.config = {**template.config, "model": "turing_large"}
    template.save(update_fields=["config"])
    version = EvalTemplateVersion.objects.get_default(template)
    version.model = "jev-2.0.0"
    version.save(update_fields=["model"])
    _version_resolves(monkeypatch)
    error, events, post, send = _execute(config)
    assert isinstance(error, JevMappingError)
    assert error.code == "JEV_MODEL_UNKNOWN"
    assert events == []
    post.assert_not_called()
    send.assert_not_called()


def _composite_config(auth_client, user, workspace, link_configs):
    """An external config on a composite whose children are Jev templates,
    one per entry in `link_configs` (each child link's own config)."""
    from model_hub.models.evals_metric import CompositeEvalChild, EvalTemplate

    parent = EvalTemplate.no_workspace_objects.create(
        name="jev-composite",
        organization=user.organization,
        workspace=workspace,
        owner="user",
        template_type="composite",
        config={},
    )
    for order, link_config in enumerate(link_configs):
        CompositeEvalChild.objects.create(
            parent=parent,
            child=_jev_template(auth_client, name=f"jev-child-{order}"),
            order=order,
            weight=1.0,
            config=link_config,
        )
    return _config(auth_client, user, workspace, None, template=parent)


# The eval path closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
def test_a_jev_composite_bills_only_its_children_jev_events(
    auth_client, user, workspace, jev_gateway
):
    from ee.jev.metering import usage_event_id

    config = _composite_config(auth_client, user, workspace, [{}, {}])
    error, events, post, send = _execute(config)
    assert error is None
    assert post.call_count == 2
    send.assert_called_once()
    # No up-front Turing Large charge for the composite: each child's Jev
    # event, keyed to that child's own run.
    assert [event.event_type for event in events] == ["jev_evaluator"] * 2
    config.refresh_from_db()
    children = config.eval_results["children"]
    assert [child["status"] for child in children] == ["completed"] * 2
    assert sorted(event.event_id for event in events) == sorted(
        usage_event_id(child["log_id"], 1) for child in children
    )


# The eval path closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
def test_a_jev_composite_whose_children_fail_bills_nothing(
    auth_client, user, workspace, jev_gateway
):
    config = _composite_config(auth_client, user, workspace, [{}, {}])
    error, events, post, _ = _execute(
        config, typesafe=_gateway_error(403, "provider_key_rejected")
    )
    assert error is None
    assert post.call_count == 2
    assert events == []
    config.refresh_from_db()
    assert [child["status"] for child in config.eval_results["children"]] == [
        "failed"
    ] * 2


# The eval path closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
def test_a_composite_child_on_an_unknown_jev_model_stops_the_run_up_front(
    auth_client, user, workspace, jev_gateway
):
    from ee.jev.mapping import JevMappingError
    from ee.usage.models.usage import APICallLog

    config = _composite_config(
        auth_client, user, workspace, [{}, {"run_config": {"model": "jev-2.0.0"}}]
    )
    error, events, post, send = _execute(config)
    assert isinstance(error, JevMappingError)
    assert error.code == "JEV_MODEL_UNKNOWN"
    assert events == []
    post.assert_not_called()
    send.assert_not_called()
    assert not APICallLog.objects.filter(source_id=config.id).exists()


# The eval path closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
def test_a_non_jev_composite_adds_no_event_of_its_own(
    auth_client, user, workspace, jev_gateway
):
    from types import SimpleNamespace

    from model_hub.models.evals_metric import CompositeEvalChild, EvalTemplate

    config = _composite_config(auth_client, user, workspace, [])
    child = EvalTemplate.no_workspace_objects.create(
        name="llm-child",
        organization=user.organization,
        workspace=workspace,
        owner="user",
        config={"model": "gpt-4o"},
    )
    CompositeEvalChild.objects.create(
        parent=config.eval_template, child=child, order=0, weight=1.0, config={}
    )
    outcome = SimpleNamespace(
        aggregate_score=1.0, summary="ok", aggregate_pass=True, child_results=[]
    )
    # The children bill themselves inside the composite helper, which is
    # stubbed here: whatever the parent emits is its own up-front charge.
    with patch(
        "model_hub.utils.composite_execution.execute_composite_children_sync",
        return_value=outcome,
    ):
        error, events, post, send = _execute(config)
    assert error is None
    assert events == []
    post.assert_not_called()
    send.assert_called_once()
