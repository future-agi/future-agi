"""An LLM judge on a Settings › AI Providers custom model calls that model.

Walk row 7 (11-11): the judge list offers the workspace's custom model
(provider "custom"), yet Test Evaluation answered "Evaluation could not be
completed. Please retry." and no request reached the model. The judge's
``LLM.call_llm`` handed litellm a dict credential with
``custom_llm_provider="custom"`` and neither the model's URL nor its headers.
The judge now POSTs the OpenAI-compatible chat body to the stored
chat-completions URL through ``guarded_post``, as Run Prompt's custom model
handler does, and a judge call that fails says which model, why and where to
fix it, in the Test result and in the cell's stored reason.

No network: the endpoint's host name resolves to a fixed public address and
``FakeTransport`` stands in for the connection ``guarded_post`` makes.
"""

import json
from types import SimpleNamespace
from unittest import mock

import pytest
from rest_framework import status

from agentic_eval.core.llm.llm import LLM
from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator import (
    CustomPromptEvaluator,
)
from evaluations.engine.instance import create_eval_instance
from model_hub.models.choices import OwnerChoices
from model_hub.models.custom_models import CustomAIModel
from model_hub.models.evals_metric import EvalTemplate
from model_hub.views.eval_runner import EvaluationRunner
from tfc.utils.error_codes import get_specific_error_message
from tfc.utils.tests.guarded_post_fakes import (
    JSON_HEADERS,
    FakeResolver,
    FakeResponse,
    FakeTransport,
)
from tfc.utils.tests.loopback_endpoints import clear_proxy_env

PUBLIC_IP = "93.184.216.34"
ENDPOINT = "https://api.example.com/v1/chat/completions"
MODEL = "liquid/lfm-3-1.7b"
TENANT_AUTH = "Bearer tenant-key"
VERDICT = {"result": "Pass", "explanation": "A plain, respectful answer."}
PRIVATE_TEXT = "upstream-private-error-text"
UNAUTHORIZED = (
    f"The judge model {MODEL} could not be reached: The endpoint answered "
    "HTTP 401 Unauthorized. Check Settings › AI Providers."
)
TIMED_OUT = (
    f"The judge model {MODEL} could not be reached: The endpoint did not "
    "answer in time. Check Settings › AI Providers."
)


def _completion(content, usage=None):
    body = {
        "id": "gen-1",
        "object": "chat.completion",
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }
    if usage is not None:
        body["usage"] = usage
    return FakeResponse(200, JSON_HEADERS, json.dumps(body).encode())


class _EndpointResolver(FakeResolver):
    """The endpoint's host resolves to PUBLIC_IP; every other name (the test
    database, the cache) resolves as usual."""

    def __call__(self, host, port=None, *args, **kwargs):
        if str(host).lower().rstrip(".") in self.table:
            return super().__call__(host, port, *args, **kwargs)
        return self._real_getaddrinfo(host, port, *args, **kwargs)


@pytest.fixture(autouse=True)
def _no_env_proxy(monkeypatch):
    # guarded_post honours HTTP(S)_PROXY; these tests use the fake transport.
    clear_proxy_env(monkeypatch)


@pytest.fixture
def transport(monkeypatch):
    resolver = _EndpointResolver().install(monkeypatch)
    resolver.add("api.example.com", PUBLIC_IP)
    return FakeTransport().install(monkeypatch)


@pytest.fixture
def view_path(monkeypatch):
    """What the Test Evaluation view runs around the judge, made test-safe.

    - Stored-feedback retrieval: EmbeddingManager.close() closes every Django
      connection, which breaks the test's enclosing transaction (the usage
      checks then fail with "the connection is closed"). A stand-in with no
      stored feedback, as in test_custom_prompt_feedback.
    - Usage pre-check and API call log, stubbed as in
      test_eval_playground_response_shape and test_feedback_rag_consumer_scope.
    - Ground truth: none is configured; short-circuited as there.
    """
    manager = mock.Mock()
    manager.retrieve_avg_rag_based_examples.return_value = []
    manager.process_examples.return_value = []
    monkeypatch.setattr("model_hub.views.eval_runner.EmbeddingManager", lambda: manager)
    monkeypatch.setattr(
        "model_hub.views.utils.evals.log_and_deduct_cost_for_api_request", None
    )
    monkeypatch.setattr(
        "ee.usage.services.metering.check_usage",
        lambda *_args, **_kwargs: SimpleNamespace(allowed=True),
    )
    monkeypatch.setattr(
        "model_hub.views.utils.evals.GroundTruthService.inject_context",
        lambda params, *_args, **_kwargs: params,
    )
    monkeypatch.setattr(
        "model_hub.views.utils.evals.GroundTruthService.resolve_preview_examples",
        lambda **_kwargs: None,
    )
    return manager


def _custom_model(user, workspace):
    """The walk's model: Settings › AI Providers, provider "custom"."""
    return CustomAIModel.objects.create(
        user_model_id=MODEL,
        provider="custom",
        input_token_cost=0,
        output_token_cost=0,
        organization=user.organization,
        workspace=workspace,
        key_config={"api_base": ENDPOINT, "headers": {"Authorization": TENANT_AUTH}},
        user=user,
        deleted=False,
    )


def _judge_template(user, workspace):
    """A Pass/Fail LLM-as-a-judge evaluation."""
    return EvalTemplate.no_workspace_objects.create(
        name="custom-model-judge",
        organization=user.organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="llm",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "output": "Pass/Fail",
            "required_keys": ["output"],
            "rule_prompt": "Is this answer free of toxic language? {{output}}",
        },
        criteria="Is this answer free of toxic language? {{output}}",
        visible_ui=True,
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
    )


def _judge(user, workspace):
    _custom_model(user, workspace)
    instance, _criteria = create_eval_instance(
        eval_class=CustomPromptEvaluator,
        eval_template=_judge_template(user, workspace),
        config={},
        model=MODEL,
        organization_id=str(user.organization.id),
        workspace_id=str(workspace.id),
    )
    return instance


def _test_evaluation(auth_client, template):
    """What the dataset's Test Evaluation button sends (DatasetTestMode)."""
    return auth_client.post(
        "/model-hub/eval-playground/",
        {
            "template_id": str(template.id),
            "model": MODEL,
            "error_localizer": False,
            "config": {"mapping": {"output": "Paris"}},
        },
        format="json",
    )


@pytest.mark.django_db
def test_judge_on_a_custom_model_calls_its_endpoint_and_scores(
    user, workspace, transport
):
    transport.respond(
        _completion(
            json.dumps(VERDICT),
            usage={"prompt_tokens": 21, "completion_tokens": 9, "total_tokens": 30},
        )
    )
    judge = _judge(user, workspace)

    result = judge.run(output="Paris", required_keys=["output"])

    scored = result.eval_results[0]
    assert scored["data"] == {"result": "Pass"}
    assert scored["failure"] is False
    assert scored["reason"] == VERDICT["explanation"]
    # One request, to the stored chat-completions URL, with the stored headers.
    assert [pool["host"] for pool in transport.pools] == [PUBLIC_IP]
    assert transport.pools[0]["server_hostname"] == "api.example.com"
    assert len(transport.requests) == 1
    assert transport.requests[0]["url"] == "/v1/chat/completions"
    assert transport.sent_headers["authorization"] == TENANT_AUTH
    sent = json.loads(transport.requests[0]["body"])
    assert sent["model"] == MODEL
    assert sent["stream"] is False
    assert "max_tokens" not in sent
    assert sent["messages"][-1]["role"] == "user"
    assert "Paris" in json.dumps(sent["messages"])
    assert judge.token_usage["prompt_tokens"] == 21
    assert judge.token_usage["completion_tokens"] == 9


@pytest.mark.django_db
def test_test_evaluation_with_a_custom_judge_model_scores(
    auth_client, user, workspace, transport, view_path
):
    transport.respond(_completion(json.dumps(VERDICT)))
    _custom_model(user, workspace)

    response = _test_evaluation(auth_client, _judge_template(user, workspace))

    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data["result"]["output"] == "Passed"
    assert response.data["result"]["reason"] == VERDICT["explanation"]
    assert len(transport.requests) == 1
    assert transport.requests[0]["url"] == "/v1/chat/completions"


@pytest.mark.django_db
def test_an_unreachable_custom_judge_says_why_in_the_test_result(
    auth_client, user, workspace, transport, view_path
):
    transport.respond(FakeResponse(401, JSON_HEADERS, PRIVATE_TEXT.encode()))
    _custom_model(user, workspace)

    response = _test_evaluation(auth_client, _judge_template(user, workspace))

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    shown = str(response.data)
    assert UNAUTHORIZED in shown
    assert "Please retry" not in shown
    assert PRIVATE_TEXT not in shown
    assert "api.example.com" not in shown
    assert "litellm" not in shown.lower()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "answer, message",
    [
        (FakeResponse(401, JSON_HEADERS, PRIVATE_TEXT.encode()), UNAUTHORIZED),
        (TimeoutError("read timed out"), TIMED_OUT),
    ],
    ids=["refused", "timeout"],
)
def test_the_cell_reason_says_which_judge_model_failed_and_why(
    user, workspace, transport, answer, message
):
    if isinstance(answer, BaseException):
        transport.fail_with(answer)
    else:
        transport.respond(answer)
    judge = _judge(user, workspace)

    with pytest.raises(ValueError) as raised:
        judge.run(output="Paris", required_keys=["output"])

    # The dataset runner stores get_specific_error_message(error) as the
    # cell's reason (EvaluationRunner._handle_error).
    stored = get_specific_error_message(raised.value)
    assert stored == message
    cell_payload, _status, _value = EvaluationRunner.__new__(
        EvaluationRunner
    )._handle_error(stored)
    assert cell_payload == {"reason": message}
    assert PRIVATE_TEXT not in stored


@pytest.mark.django_db
def test_a_refused_response_format_is_asked_again_without_it(
    user, workspace, transport
):
    transport.respond(
        FakeResponse(400, JSON_HEADERS, b""), _completion(json.dumps(VERDICT))
    )
    judge = _judge(user, workspace)

    result = judge.run(output="Paris", required_keys=["output"])

    assert result.eval_results[0]["data"] == {"result": "Pass"}
    first, second = (json.loads(sent["body"]) for sent in transport.requests)
    assert first["response_format"]["type"] == "json_schema"
    assert "response_format" not in second
    assert second["messages"] == first["messages"]


def _litellm_answer(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


@pytest.mark.django_db
def test_a_provider_key_judge_still_goes_through_litellm(monkeypatch, transport):
    # LLM() builds an OpenAI client for provider "openai".
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    llm = LLM(provider="openai", model_name="acme-judge-deployment", api_key="sk-t")

    with mock.patch(
        "agentic_eval.core.llm.llm.litellm.completion",
        return_value=_litellm_answer("ok"),
    ) as completion:
        assert llm.call_llm([{"role": "user", "content": "hi"}], "openai") == "ok"

    completion.assert_called_once()
    assert completion.call_args.kwargs["api_key"] == "sk-t"
    assert transport.requests == []


@pytest.mark.django_db
def test_an_openai_custom_row_still_goes_through_guarded_litellm(
    monkeypatch, transport
):
    # LLM() builds an OpenAI client for provider "openai"; a dict key is not
    # given to it, so it needs one from the environment.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    config = {"key": "sk-row", "api_base": "https://api.example.com/v1"}
    llm = LLM(provider="openai", model_name="acme-openai-row", api_key=config)

    with mock.patch(
        "agentic_eval.core.llm.llm.guarded_completion",
        return_value=_litellm_answer("ok"),
    ) as completion:
        assert llm.call_llm([{"role": "user", "content": "hi"}], "openai") == "ok"

    completion.assert_called_once()
    assert completion.call_args.kwargs["api_base"] == "https://api.example.com/v1"
    assert completion.call_args.kwargs["api_key"] == "sk-row"
    assert transport.requests == []
