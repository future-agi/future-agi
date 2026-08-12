"""run-prompt error sanitization (issue #2081).

run-prompt failures must never return raw exception text (provider error
bodies, file paths, tracebacks) to API callers. These tests pin the
fail-closed contract of ``sanitize_run_prompt_error`` and its use in the
run-prompt execution paths (preview endpoint and run-for-rows endpoint).
"""

import json
import uuid
from unittest.mock import patch

import pytest
from rest_framework import status

from tfc.utils.error_codes import (
    GENERIC_RUN_PROMPT_ERROR,
    sanitize_run_prompt_error,
)


def _fake_exception(name, module, message):
    """Build an exception whose class looks like it was raised from `module`.

    Mirrors provider SDK exceptions (litellm/openai) without requiring the
    SDKs to be importable in this test.
    """
    error_type = type(name, (Exception,), {"__module__": module})
    return error_type(message)


def _litellm_error(name, message):
    return _fake_exception(name, "litellm.exceptions", message)


AUTH_RAW = (
    "OpenAIException - 401 Invalid API key: https://api.openai.com/v1 "
    "/etc/futureagi/secret.key"
)


class TestSanitizeRunPromptError:
    """Unit contract for the fail-closed sanitizer (no DB required)."""

    def test_provider_auth_error_is_mapped_to_clean_message(self):
        message = sanitize_run_prompt_error(
            _litellm_error("AuthenticationError", AUTH_RAW), is_llm_error=True
        )
        assert (
            message
            == "Provider authentication failed. Please check your API key and try again."
        )
        assert "api.openai.com" not in message
        assert "401" not in message
        assert "/etc/" not in message

    def test_provider_timeout_is_mapped_to_clean_message(self):
        message = sanitize_run_prompt_error(
            _litellm_error("Timeout", "APITimeoutError: timed out after 600.0s"),
            is_llm_error=True,
        )
        assert message == "The provider request timed out. Please try again."

    def test_builtin_timeout_is_mapped_to_clean_message(self):
        message = sanitize_run_prompt_error(
            TimeoutError("connect to 10.0.0.5:443 timed out"), is_llm_error=True
        )
        assert message == "The provider request timed out. Please try again."
        assert "10.0.0.5" not in message

    def test_provider_rate_limit_is_mapped_to_clean_message(self):
        message = sanitize_run_prompt_error(
            _litellm_error(
                "RateLimitError", "429 - rate_limited - too many requests"
            ),
            is_llm_error=True,
        )
        assert message == "Provider rate limit reached. Please wait and retry."

    def test_provider_insufficient_credits_is_mapped_to_clean_message(self):
        message = sanitize_run_prompt_error(
            _litellm_error(
                "InsufficientCreditsError", "billing.balance has 0.00 remaining"
            ),
            is_llm_error=True,
        )
        assert (
            message
            == "Insufficient credits to run the prompt. Please add credits and try again."
        )

    def test_unknown_exception_returns_generic_message(self):
        message = sanitize_run_prompt_error(
            RuntimeError(
                "psycopg.OperationalError: could not connect to "
                "/var/run/postgresql/.s.PGSQL.5432"
            ),
            is_llm_error=True,
        )
        assert message == GENERIC_RUN_PROMPT_ERROR
        assert "/var/run" not in message

    def test_llm_phase_value_error_is_redacted(self):
        # Provider detail arriving as a plain ValueError during the provider
        # call must not leak.
        message = sanitize_run_prompt_error(
            ValueError(f"provider body: {AUTH_RAW}"), is_llm_error=True
        )
        assert message == GENERIC_RUN_PROMPT_ERROR

    def test_local_validation_value_error_surfaced_verbatim(self):
        error = ValueError("Model gpt-4o does not support image input.")
        assert sanitize_run_prompt_error(error, is_llm_error=False) == str(error)

    def test_jinja_template_error_surfaced_verbatim(self):
        error = _fake_exception(
            "TemplateSyntaxError",
            "jinja2.exceptions",
            "expected token 'end of statement block', got '}'",
        )
        assert sanitize_run_prompt_error(error, is_llm_error=False) == str(error)

    def test_same_named_non_provider_exception_is_not_matched(self):
        # An app-level exception with a provider-like name must be redacted.
        error = _fake_exception(
            "AuthenticationError", "model_hub.services", f"api key file: {AUTH_RAW}"
        )
        message = sanitize_run_prompt_error(error, is_llm_error=True)
        assert message == GENERIC_RUN_PROMPT_ERROR
        assert "api.openai.com" not in message


# ── Fixtures (mirror model_hub/tests/test_run_prompt_api.py) ────────────────


@pytest.fixture
def dataset(db, organization, workspace):
    from model_hub.models.choices import DatasetSourceChoices
    from model_hub.models.develop_dataset import Dataset

    ds = Dataset.objects.create(
        name="Sanitization Dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    ds.column_order = []
    ds.save()
    return ds


@pytest.fixture
def input_column(db, dataset):
    from model_hub.models.choices import DataTypeChoices, SourceChoices
    from model_hub.models.develop_dataset import Column

    col = Column.objects.create(
        name="Input Column",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order.append(str(col.id))
    dataset.save()
    return col


@pytest.fixture
def row(db, dataset):
    from model_hub.models.develop_dataset import Row

    return Row.objects.create(dataset=dataset, order=0)


@pytest.fixture
def cell(db, dataset, input_column, row):
    from model_hub.models.develop_dataset import Cell

    return Cell.objects.create(
        dataset=dataset,
        column=input_column,
        row=row,
        value="Acme Corp",
    )


@pytest.fixture
def run_prompter(db, dataset, organization, workspace):
    from model_hub.models.choices import StatusType
    from model_hub.models.run_prompt import RunPrompter

    return RunPrompter.objects.create(
        name="Sanitization Run Prompter",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        status=StatusType.NOT_STARTED.value,
        model="gpt-4",
        messages=[{"role": "user", "content": "Test prompt"}],
        run_prompt_config={},
    )


def _preview_config():
    return {
        "model": "gpt-4",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is {{Input Column}}?"}
                ],
            }
        ],
        "temperature": 0.7,
        "max_tokens": 100,
        "output_format": "string",
    }


def _preview_payload(dataset_id):
    return {
        "dataset_id": str(dataset_id),
        "name": "sanitized-preview",
        "config": _preview_config(),
        "first_n_rows": 1,
    }


class TestPreviewRunPromptColumnSanitizesErrors:
    """Preview responses must never carry raw exception text."""

    @pytest.mark.django_db
    def test_preview_success_passthrough_unchanged(
        self, auth_client, dataset, input_column, row, cell
    ):
        with patch(
            "model_hub.views.run_prompt.RunPrompt.litellm_response",
            return_value=(
                "Hello there!",
                {
                    "data": {"response": "Hello there!"},
                    "metadata": {"usage": {}, "cost": {}},
                },
            ),
        ):
            response = auth_client.post(
                "/model-hub/develops/preview_run_prompt_column/",
                _preview_payload(dataset.id),
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["responses"] == ["Hello there!"]

    @pytest.mark.django_db
    def test_preview_provider_error_returns_sanitized_message(
        self, auth_client, dataset, input_column, row, cell
    ):
        with patch(
            "model_hub.views.run_prompt.RunPrompt.litellm_response",
            side_effect=_litellm_error("AuthenticationError", AUTH_RAW),
        ):
            response = auth_client.post(
                "/model-hub/develops/preview_run_prompt_column/",
                _preview_payload(dataset.id),
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        body = json.dumps(response.json())
        assert response.json()["result"]["responses"] == [
            "Provider authentication failed. Please check your API key and try again."
        ]
        assert "api.openai.com" not in body
        assert "401" not in body
        assert "/etc/" not in body

    @pytest.mark.django_db
    def test_preview_unknown_exception_returns_generic_message(
        self, auth_client, dataset, input_column, row, cell
    ):
        raw_traceback = (
            "Traceback (most recent call last):\n  File "
            "'/srv/futureagi/model_hub/views/run_prompt.py', line 1953\n"
            f"RuntimeError: {AUTH_RAW}"
        )
        with patch(
            "model_hub.views.run_prompt.RunPrompt.litellm_response",
            side_effect=RuntimeError(raw_traceback),
        ):
            response = auth_client.post(
                "/model-hub/develops/preview_run_prompt_column/",
                _preview_payload(dataset.id),
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        body = json.dumps(response.json())
        assert response.json()["result"]["responses"] == [GENERIC_RUN_PROMPT_ERROR]
        assert "Traceback" not in body
        assert "/srv/futureagi" not in body
        assert "api.openai.com" not in body


class TestRunPromptForRowsSanitizesErrors:
    """Queueing endpoint errors must be sanitized before reaching callers."""

    @pytest.mark.django_db
    def test_unknown_exception_returns_generic_message(
        self, auth_client, dataset, run_prompter, row
    ):
        raw_error = RuntimeError(
            f"celery connection refused at /var/run/redis.sock: {AUTH_RAW}"
        )
        with patch(
            "model_hub.views.run_prompt.run_all_prompts_task.apply_async",
            side_effect=raw_error,
        ):
            response = auth_client.post(
                "/model-hub/run-prompt-for-rows/",
                {
                    "run_prompt_ids": [str(run_prompter.id)],
                    "row_ids": [str(row.id)],
                },
                format="json",
            )

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        body = json.dumps(response.json())
        assert response.json()["message"] == GENERIC_RUN_PROMPT_ERROR
        assert "redis.sock" not in body
        assert "api.openai.com" not in body
