from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from openai.types import CompletionUsage

from agentic_eval.core_evals.llm_services.openai_api import OpenAiService

MESSAGES = [{"role": "user", "content": "score this"}]
MODEL = "gpt-4o-mini"


@pytest.fixture
def service():
    service = OpenAiService.__new__(OpenAiService)
    service.openai = Mock()
    return service


@pytest.mark.parametrize(
    ("method", "operation"),
    [
        (OpenAiService.chat_completion.__wrapped__, "chat_completion"),
        (OpenAiService.chat_completion_json.__wrapped__, "chat_completion_json"),
        (OpenAiService.log_probs, "log_probs"),
    ],
)
@pytest.mark.parametrize(
    "fields", [{}, {"choices": None}, {"choices": []}], ids=["omitted", "null", "empty"]
)
def test_completion_rejects_missing_choices(service, method, operation, fields):
    service.openai.chat.completions.create.return_value = SimpleNamespace(**fields)

    with pytest.raises(ValueError, match=f"OpenAI {operation} returned no choices"):
        method(service, messages=MESSAGES, model=MODEL)


@pytest.mark.parametrize("fields", [{}, {"usage": None}], ids=["omitted", "null"])
def test_chat_completion_rejects_missing_usage(service, fields):
    choice = SimpleNamespace(message=SimpleNamespace(content="ok"))
    service.openai.chat.completions.create.return_value = SimpleNamespace(
        choices=[choice], **fields
    )

    with pytest.raises(ValueError, match="OpenAI chat_completion returned no usage"):
        OpenAiService.chat_completion.__wrapped__(service, MESSAGES, MODEL)


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"logprobs": None},
        {"logprobs": SimpleNamespace()},
        {"logprobs": SimpleNamespace(content=None)},
    ],
    ids=["omitted", "null", "content-omitted", "content-null"],
)
def test_log_probs_rejects_missing_content(service, fields):
    service.openai.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(**fields)]
    )

    with pytest.raises(
        ValueError, match="OpenAI log_probs returned no logprobs content"
    ):
        service.log_probs(MESSAGES, MODEL)


def test_chat_completion_preserves_content_and_usage(service):
    choice = SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content="ok")
    )
    usage = CompletionUsage(completion_tokens=2, prompt_tokens=8, total_tokens=10)
    service.openai.chat.completions.create.return_value = SimpleNamespace(
        choices=[choice], usage=usage
    )

    result = OpenAiService.chat_completion.__wrapped__(service, MESSAGES, MODEL)

    assert result["value"] == "ok"
    assert result["metadata"]["usage"] == {
        "completion_tokens": 2,
        "prompt_tokens": 8,
        "total_tokens": 10,
    }


@pytest.mark.parametrize("values", [[], [-0.2, -0.9]], ids=["empty", "tokens"])
def test_log_probs_returns_token_scores(service, values):
    content = [SimpleNamespace(logprob=value) for value in values]
    choice = SimpleNamespace(logprobs=SimpleNamespace(content=content))
    service.openai.chat.completions.create.return_value = SimpleNamespace(
        choices=[choice]
    )

    assert service.log_probs(MESSAGES, MODEL) == values
