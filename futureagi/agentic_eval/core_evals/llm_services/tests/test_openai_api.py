from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentic_eval.core_evals.llm_services.openai_api import OpenAiService, _first_choice


def test_first_choice_returns_provider_choice():
    choice = SimpleNamespace(message=SimpleNamespace(content="ok"))
    response = SimpleNamespace(choices=[choice])

    assert _first_choice(response, "chat_completion") is choice


def test_first_choice_rejects_empty_choices_with_context():
    response = SimpleNamespace(choices=[])

    with pytest.raises(ValueError, match="OpenAI log_probs returned no choices"):
        _first_choice(response, "log_probs")


def test_first_choice_rejects_missing_choices_with_context():
    response = SimpleNamespace()

    with pytest.raises(
        ValueError, match="OpenAI chat_completion_json returned no choices"
    ):
        _first_choice(response, "chat_completion_json")


def test_chat_completion_rejects_empty_choices_before_reading_usage():
    service = OpenAiService.__new__(OpenAiService)
    create = Mock(return_value=SimpleNamespace(choices=[]))
    service.openai = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    with pytest.raises(ValueError, match="OpenAI chat_completion returned no choices"):
        OpenAiService.chat_completion.__wrapped__(
            service,
            messages=[{"role": "user", "content": "score this"}],
            model="gpt-4o-mini",
        )

    create.assert_called_once()


def test_chat_completion_json_rejects_empty_choices_from_provider():
    service = OpenAiService.__new__(OpenAiService)
    create = Mock(return_value=SimpleNamespace(choices=[]))
    service.openai = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    with pytest.raises(
        ValueError, match="OpenAI chat_completion_json returned no choices"
    ):
        OpenAiService.chat_completion_json.__wrapped__(
            service,
            messages=[{"role": "user", "content": "return json"}],
            model="gpt-4o-mini",
        )

    create.assert_called_once()
