"""
PromptTemplateViewSet.run: a row whose model call fails is a handled result.

The failure is stored as that row's output and sent to the UI, so the view
must not log it again as an error with a traceback under its own logger.
"""

from unittest.mock import MagicMock, patch

import litellm
import pytest
from structlog.testing import capture_logs

from model_hub.models.api_key import ApiKey
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.views.prompt_template import PromptTemplateViewSet

MISSING_KEY_MESSAGE = (
    "API key not configured for openai. Please add your API key in settings."
)


def _make_version(organization, workspace, user, customers):
    template = PromptTemplate.objects.create(
        name="Row failure template",
        organization=organization,
        workspace=workspace,
        created_by=user,
        variable_names=["customer"],
    )
    version = PromptVersion.objects.create(
        original_template=template,
        template_version="v1",
        prompt_config_snapshot={
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "Say hi to {{customer}}"}],
                }
            ],
            "configuration": {
                "model": "gpt-4o",
                "temperature": 0.2,
                "max_tokens": 50,
                "response_format": {"type": "text"},
            },
        },
        variable_names={"customer": customers},
        output=[],
        is_default=True,
    )
    return template, version


@pytest.fixture(autouse=True)
def _keep_test_connection():
    # run() starts with close_old_connections(), which would drop the test
    # transaction's connection.
    with patch("model_hub.views.prompt_template.close_old_connections"):
        yield


def _row_errors(logs):
    return [
        entry
        for entry in logs
        if entry.get("log_level") in ("error", "critical", "exception")
        and str(entry.get("event", "")).startswith("Error for index")
    ]


@pytest.mark.django_db
def test_missing_provider_key_rows_are_not_logged_as_errors(
    organization, workspace, user
):
    template, version = _make_version(organization, workspace, user, ["Ada", "Grace"])
    ws_manager = MagicMock()

    with capture_logs() as logs:
        PromptTemplateViewSet().run(
            template,
            version,
            organization.id,
            None,
            is_run="prompt",
            is_sdk=False,
            workspace=workspace,
            ws_manager=ws_manager,
        )

    version.refresh_from_db()
    # The caller still gets the specific failure for each row.
    assert version.output == [MISSING_KEY_MESSAGE, MISSING_KEY_MESSAGE]
    sent_errors = [
        c.kwargs.get("error") for c in ws_manager.send_error_message.call_args_list
    ]
    assert sent_errors == [MISSING_KEY_MESSAGE, MISSING_KEY_MESSAGE]
    # ...but the handled row failure is not logged as an unhandled error.
    assert _row_errors(logs) == []


@pytest.mark.django_db
def test_provider_rate_limit_row_is_not_logged_as_error(
    organization, workspace, user
):
    ApiKey.objects.create(provider="openai", organization=organization, key="sk-test")
    template, version = _make_version(organization, workspace, user, ["Ada"])

    rate_limited = litellm.RateLimitError(
        message="Rate limit reached for gpt-4o", llm_provider="openai", model="gpt-4o"
    )
    with (
        patch(
            "agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers."
            "llm_handler.litellm.completion",
            side_effect=rate_limited,
        ),
        capture_logs() as logs,
    ):
        PromptTemplateViewSet().run(
            template,
            version,
            organization.id,
            None,
            is_run="prompt",
            is_sdk=True,
            workspace=workspace,
            ws_manager=MagicMock(),
        )

    version.refresh_from_db()
    assert len(version.output) == 1
    assert "Rate limit reached for gpt-4o" in version.output[0]
    assert _row_errors(logs) == []
