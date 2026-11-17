"""A template version keeps the run on the model and key it was set up
with.

The real engine builds and runs each evaluator (run_eval, create_eval_instance
and the registered evaluator class). Only the transports are replaced: the
gateway client, the model library, the managed route, the Turing
client and, for the agent evaluator, the managed agent stream. Each records
what it receives, so each test can check which transport was called and
with which model. Every key here is synthetic.
"""

import json
import uuid
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from evaluations.engine import instance
from evaluations.engine.params import prepare_run_params
from evaluations.engine.registry import get_eval_class
from evaluations.engine.runner import EvalRequest, run_eval
from model_hub.models.api_key import ApiKey
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
from model_hub.services import model_route

LIVE_RUBRIC = "Live rubric: is {{output}} polite?"
LIVE_SYSTEM = "Live system message"
VERSION_RUBRIC = "Version rubric: is {{output}} correct?"
VERSION_SYSTEM = "Version system message"
TEMPLATE_MODEL = "turing_small"
OTHER_TURING_MODEL = "turing_flash"
OTHER_MODEL = "gpt-4.1-mini"
AGENT_VERSION_MODEL = "gemini-2.5-pro"
MANAGED_AGENT_MODEL = "falcon_ai"
STORED_KEY = "sk-test-version-model-not-real-0001"
VERDICT = json.dumps({"result": "Pass", "explanation": "The output is correct."})
REFUSAL = (
    "This evaluation's saved version uses a different model than its key "
    "allows. Pick a key for that model or unpin the version."
)


def make_template(
    organization,
    workspace,
    *,
    model=TEMPLATE_MODEL,
    eval_type_id="CustomPromptEvaluator",
):
    return EvalTemplate.no_workspace_objects.create(
        name=f"version-model-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        owner="user",
        eval_type="llm",
        criteria=LIVE_RUBRIC,
        config={
            "eval_type_id": eval_type_id,
            "output": "Pass/Fail",
            "required_keys": ["output"],
            "rule_prompt": LIVE_RUBRIC,
            "system_prompt": LIVE_SYSTEM,
            "model": model,
        },
        model=model,
    )


def make_version(template, *, model, default=False):
    return EvalTemplateVersion.no_workspace_objects.create(
        eval_template=template,
        version_number=1,
        is_default=default,
        prompt_messages=[
            {"role": "system", "content": VERSION_SYSTEM},
            {"role": "user", "name": "eval_prompt", "content": VERSION_RUBRIC},
        ],
        config_snapshot=dict(template.config),
        criteria=VERSION_RUBRIC,
        model=model,
        organization=template.organization,
        workspace=template.workspace,
    )


def model_response(content=VERDICT):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content), finish_reason="stop"
            )
        ],
        usage=SimpleNamespace(total_tokens=3, prompt_tokens=2, completion_tokens=1),
        _hidden_params={},
    )


@contextmanager
def transports():
    """Every transport a judge can reach, replaced and recorded."""
    # Build the evaluator registry first, so only the run itself is observed.
    get_eval_class("CustomPromptEvaluator")
    gateway = MagicMock(name="platform gateway client")
    gateway.chat.completions.create.return_value = model_response()
    turing = MagicMock(name="Turing client")
    turing.return_value.chat_completion.return_value = VERDICT
    turing.return_value.token_usage = {
        "total_tokens": 3,
        "prompt_tokens": 2,
        "completion_tokens": 1,
    }
    turing.return_value.cost = {
        "total_cost": 0,
        "prompt_cost": 0,
        "completion_cost": 0,
    }
    with ExitStack() as stack:
        enter = stack.enter_context
        yield SimpleNamespace(
            gateway=gateway,
            get_gateway=enter(
                patch(
                    "ee.usage.services.gateway_llm_client.get_gateway_client",
                    return_value=gateway,
                )
            ),
            get_async_gateway=enter(
                patch(
                    "ee.usage.services.gateway_llm_client.get_async_gateway_client",
                    return_value=MagicMock(name="async platform gateway client"),
                )
            ),
            completion=enter(
                patch("litellm.completion", return_value=model_response())
            ),
            guarded=enter(
                patch(
                    "agentic_eval.core.llm.llm.guarded_completion",
                    return_value=model_response(),
                )
            ),
            managed=enter(
                patch("model_hub.services.model_route.managed_chat_completion")
            ),
            turing=enter(patch("ee.turing.client.TuringClient", turing)),
            key_lookup=enter(
                patch.object(instance, "_get_api_key", wraps=instance._get_api_key)
            ),
        )


def run(template, organization, workspace, *, version_number=None):
    """A run whose binding names the template's Turing model."""
    return run_eval(
        EvalRequest(
            eval_template=template,
            inputs={"output": "The answer is 4."},
            model=TEMPLATE_MODEL,
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
            version_number=version_number,
            feedback_workspace_id=None,
        )
    )


def asked_models(seams):
    return [call.args[0] for call in seams.key_lookup.call_args_list]


def assert_no_gateway_or_model_library_call(seams):
    assert seams.gateway.chat.completions.create.call_count == 0
    assert seams.completion.call_count == 0
    assert seams.guarded.call_count == 0
    assert seams.managed.call_count == 0


@pytest.mark.django_db
def test_a_version_on_a_model_with_no_key_is_refused_before_any_model_call(
    organization, workspace
):
    """A Turing template whose pinned version names another provider's model,
    and the organization has no key for it: the run is refused before an
    evaluator exists, and no model transport is called at all: not the model
    library, not the Turing client, not any other."""
    template = make_template(organization, workspace)
    make_version(template, model=OTHER_MODEL)

    with model_route.managed_routing_port(None), transports() as seams:
        with pytest.raises(ValueError) as refused:
            run(template, organization, workspace, version_number=1)

    assert str(refused.value) == REFUSAL
    assert asked_models(seams) == [OTHER_MODEL]
    # Building an evaluator fetches the gateway client; none was built.
    assert seams.get_gateway.call_count == 0
    assert seams.turing.call_count == 0
    assert_no_gateway_or_model_library_call(seams)


@pytest.mark.django_db
def test_an_unpinned_run_keeps_the_template_model_when_the_default_version_names_another(
    organization, workspace
):
    """The template's default version names another provider's model, and the
    run pins no version: the run keeps the live template, so its Turing model
    runs on the Turing client. The default version's model is never looked
    up and reaches neither the model library nor any other model transport."""
    template = make_template(organization, workspace)
    make_version(template, model=OTHER_MODEL, default=True)

    with model_route.managed_routing_port(None), transports() as seams:
        result = run(template, organization, workspace)

    assert result.failure is False
    assert result.model_used == TEMPLATE_MODEL
    seams.turing.return_value.chat_completion.assert_called_once()
    sent = seams.turing.return_value.chat_completion.call_args.kwargs
    assert sent["model"] == TEMPLATE_MODEL
    prompt = json.dumps(sent["messages"])
    assert "Live rubric" in prompt
    assert "Version rubric" not in prompt
    assert asked_models(seams) == []
    assert_no_gateway_or_model_library_call(seams)


@pytest.mark.django_db
def test_a_version_on_another_turing_model_runs_on_the_turing_client(
    organization, workspace
):
    """A Turing template whose pinned version names another Turing model runs
    that model on the Turing client, with no key lookup and no call to the
    model library or any other model transport."""
    template = make_template(organization, workspace)
    make_version(template, model=OTHER_TURING_MODEL)

    with transports() as seams:
        result = run(template, organization, workspace, version_number=1)

    assert result.failure is False
    assert result.model_used == OTHER_TURING_MODEL
    assert result.evaluator.provider == "turing"
    seams.turing.return_value.chat_completion.assert_called_once()
    sent = seams.turing.return_value.chat_completion.call_args.kwargs
    assert sent["model"] == OTHER_TURING_MODEL
    assert "Version rubric" in json.dumps(sent["messages"])
    assert asked_models(seams) == []
    assert_no_gateway_or_model_library_call(seams)


@pytest.mark.django_db
def test_a_version_on_a_model_with_a_stored_key_calls_that_model_with_that_key(
    organization, workspace
):
    """A Turing template whose pinned version names another provider's model,
    and the organization has a key for it: the model library is called with
    that model and that key, and no other model transport is called."""
    template = make_template(organization, workspace)
    make_version(template, model=OTHER_MODEL)
    ApiKey.objects.create(
        organization=organization,
        workspace=workspace,
        provider="openai",
        key=STORED_KEY,
    )

    with transports() as seams:
        result = run(template, organization, workspace, version_number=1)

    assert result.failure is False
    assert result.model_used == OTHER_MODEL
    asked = asked_models(seams)
    assert asked
    assert set(asked) == {OTHER_MODEL}
    seams.completion.assert_called_once()
    sent = seams.completion.call_args.kwargs
    assert sent["model"] == OTHER_MODEL
    assert sent["api_key"] == STORED_KEY
    assert seams.gateway.chat.completions.create.call_count == 0
    assert seams.managed.call_count == 0
    assert seams.turing.call_count == 0


@pytest.mark.django_db
def test_a_caller_that_keeps_its_model_runs_the_version_prompt_on_that_model_without_a_key(
    organization, workspace
):
    """A caller that keeps its own model (a composite child or jury seat whose
    link chose it) runs a Turing template whose pinned version names another
    provider's model, and the organization has no key for it: the version's
    prompt runs on the caller's Turing model on the Turing client. The
    version's model is never looked up and reaches neither the platform
    gateway nor the model library."""
    template = make_template(organization, workspace)
    make_version(template, model=OTHER_MODEL)

    with model_route.managed_routing_port(None), transports() as seams:
        evaluator, criteria = instance.create_eval_instance(
            eval_class=get_eval_class("CustomPromptEvaluator"),
            eval_template=template,
            config={},
            model=TEMPLATE_MODEL,
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
            version_number=1,
            keep_model=True,
        )
        # The evaluator is built on the caller's model, not the version's.
        assert evaluator._model == TEMPLATE_MODEL
        assert evaluator.provider == "turing"
        assert evaluator._is_turing is True
        run_params = prepare_run_params(
            inputs={"output": "The answer is 4."},
            eval_template=template,
            is_futureagi=False,
            criteria=criteria,
            organization_id=str(organization.id),
            workspace_id=None,
        )
        outcome = evaluator.run(**run_params)

    assert outcome.eval_results[0].get("failure") is False
    seams.turing.return_value.chat_completion.assert_called_once()
    sent = seams.turing.return_value.chat_completion.call_args.kwargs
    assert sent["model"] == TEMPLATE_MODEL
    assert "Version rubric" in json.dumps(sent["messages"])
    assert asked_models(seams) == []
    assert_no_gateway_or_model_library_call(seams)


@pytest.mark.django_db
def test_a_cloud_agent_evaluator_sends_the_managed_agent_model_not_the_version_model(
    organization, workspace
):
    """An agent evaluator on cloud (managed gateway available) whose pinned
    version names a customer model: every request on the managed agent
    stream names the managed agent model, never the template's or the
    version's model."""
    from ee.evals.llm.agent_evaluator.evaluator import AgentEvaluator
    from ee.falcon_ai.agent import AgentLoop
    from tfc.licensing.types import DeploymentLocation

    template = make_template(
        organization, workspace, model=OTHER_MODEL, eval_type_id="AgentEvaluator"
    )
    make_version(template, model=AGENT_VERSION_MODEL)
    evaluator, _criteria = instance.create_eval_instance(
        eval_class=AgentEvaluator,
        eval_template=template,
        config={},
        model=OTHER_MODEL,
        organization_id=str(organization.id),
        workspace_id=str(workspace.id),
        version_number=1,
    )
    # The evaluator is built with the version's model, and that model is not
    # a Turing model, so the evaluator keeps the agent loop's default client.
    assert evaluator._model == AGENT_VERSION_MODEL
    assert evaluator._is_turing is False
    evaluator._current_eval_id = str(uuid.uuid4())

    payloads = []

    async def managed_stream(payload):
        payloads.append(payload)
        yield {"choices": [{"delta": {"content": VERDICT}, "finish_reason": "stop"}]}

    class OneCallLoop(AgentLoop):
        """The real loop's client set-up; one request instead of the loop."""

        async def run(self, *, user_message, system_prompt_override=None, **kwargs):
            messages = [
                {"role": "system", "content": system_prompt_override or ""},
                {"role": "user", "content": user_message},
            ]
            async for _chunk in self.llm_client.stream_completion(messages):
                pass
            return {"content": VERDICT, "tool_calls": []}

    with (
        patch(
            "tfc.capabilities.service.get_deployment_location",
            return_value=DeploymentLocation.CLOUD,
        ),
        patch("ee.falcon_ai.agent.AgentLoop", OneCallLoop),
        patch("ee.licensing.managed_ai.stream_chat_completion", managed_stream),
        patch("ai_tools.registry.registry"),
        patch.object(evaluator, "_build_tool_context", return_value=MagicMock()),
    ):
        result = evaluator._run_agent("Judge whether the output is correct.")

    assert result["content"] == VERDICT
    assert payloads, "nothing was sent on the managed agent stream"
    sent_models = [payload.get("model") for payload in payloads]
    assert set(sent_models) == {MANAGED_AGENT_MODEL}
    assert AGENT_VERSION_MODEL not in sent_models
    assert OTHER_MODEL not in sent_models
