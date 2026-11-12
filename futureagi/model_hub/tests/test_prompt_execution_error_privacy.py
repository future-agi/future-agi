"""Execution failures stay private on the socket, in storage and on later reads."""

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from django.db import OperationalError
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.models.choices import StatusType
from model_hub.tests.network_isolation import (
    no_external_lookups as no_external_lookups,
)
from model_hub.tests.test_prompt_handler_error_privacy import (
    SENTINEL,
)
from model_hub.tests.test_prompt_handler_error_privacy import (
    call_prompt_handler as _call_prompt_handler,
)
from model_hub.views import prompt_template as prompt_views
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_external_lookups")]
call_prompt_handler = _call_prompt_handler


def _assert_no_exception_text(value, error):
    # DRF response data can still contain UUIDs before renderer encoding.
    text = json.dumps(value, default=str)
    assert SENTINEL not in text
    assert str(error) not in text
    for fragment in str(error).split():
        assert fragment not in text


@pytest.fixture
def execution_runtime(prompt_version):
    # No provider, billing, socket or connection lifecycle work in these tests.
    with (
        patch.object(prompt_views, "close_old_connections"),
        patch.object(prompt_views.Organization.objects, "get", return_value=None),
        patch.object(prompt_views, "RunPrompt") as runner,
        patch.object(
            prompt_views,
            "replace_variables",
            return_value=prompt_version.prompt_config_snapshot["messages"],
        ),
    ):
        yield SimpleNamespace(
            view=prompt_views.PromptTemplateViewSet(),
            socket=Mock(),
            runner=runner,
        )


@pytest.fixture
def read_execution_details(user, organization, workspace):
    def read(execution):
        request = APIRequestFactory().get(
            "/model-hub/prompt-history/execution-details/"
        )
        request.organization = organization
        request.workspace = workspace
        force_authenticate(request, user=user)
        set_workspace_context(
            organization=organization,
            workspace=workspace,
            user=user,
            require_workspace=True,
        )
        try:
            return prompt_views.PromptHistoryExecutionViewSet.as_view(
                {"get": "get_execution_details"}
            )(request, execution_id=str(execution.id))
        finally:
            clear_workspace_context()

    return read


def _run(runtime, execution, *, run_index=None, is_sdk=False):
    return runtime.view.run(
        template=execution.original_template,
        execution=execution,
        organization_id=execution.original_template.organization_id,
        version_to_run=execution.template_version,
        is_run="prompt",
        is_sdk=is_sdk,
        run_index=run_index,
        ws_manager=runtime.socket,
    )


@pytest.mark.parametrize("run_index", [None, 1], ids=["append", "replace"])
@pytest.mark.parametrize("is_sdk", [False, True], ids=["websocket", "sdk"])
def test_run_iteration_error_is_private(
    execution_runtime,
    prompt_version,
    call_prompt_handler,
    read_execution_details,
    run_index,
    is_sdk,
):
    error = OperationalError(SENTINEL)
    runtime = execution_runtime
    if run_index is not None:
        prompt_version.variable_names = {"topic": ["first", "second"]}
        prompt_version.output = ["Keep this output", "Replace this output"]
        prompt_version.metadata = [{"token_count": 4}, {}]
        prompt_version.save(update_fields=["variable_names", "output", "metadata"])
    runtime.runner.return_value.litellm_response.side_effect = error
    with patch.object(prompt_views.logger, "exception") as log_exception:
        result = _run(runtime, prompt_version, run_index=run_index, is_sdk=is_sdk)

    runtime.runner.return_value.litellm_response.assert_called_once()
    index = run_index if run_index is not None else 0
    log_exception.assert_called_once_with(f"Error for index {index}: {error}")
    assert result == {"result": {}}
    if is_sdk:
        runtime.socket.send_error_message.assert_not_called()
    else:
        runtime.socket.send_error_message.assert_called_once_with(
            template_id=str(prompt_version.original_template_id),
            version=prompt_version.template_version,
            error="Failed to run prompt",
            result_index=index,
            num_results=2 if run_index is not None else 1,
        )
        _assert_no_exception_text(
            runtime.socket.send_error_message.call_args.kwargs, error
        )
    prompt_version.refresh_from_db()
    expected = (
        ["Keep this output", "Failed to run prompt"]
        if run_index is not None
        else ["Failed to run prompt"]
    )
    assert prompt_version.output == expected
    assert prompt_version.is_draft is False
    _assert_no_exception_text(prompt_version.output, error)
    by_name = call_prompt_handler(
        "get_template_by_name",
        {
            "name": prompt_version.original_template.name,
            "version": prompt_version.template_version,
        },
        method="get",
    )
    assert by_name.status_code == 200, by_name.data
    assert by_name.data["output"] == expected
    _assert_no_exception_text(by_name.data, error)
    history = read_execution_details(prompt_version)
    assert history.status_code == 200, history.data
    assert history.data["result"]["output"] == expected
    _assert_no_exception_text(history.data, error)


@pytest.mark.parametrize("is_sdk", [False, True], ids=["websocket", "sdk"])
def test_run_outer_error_is_private(execution_runtime, prompt_version, is_sdk):
    error = OperationalError(SENTINEL)
    prompt_version.placeholders = {"history": [{"role": "user", "content": "Hello"}]}
    with (
        patch.object(
            prompt_views, "validate_and_parse_placeholder", side_effect=error
        ) as parse,
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        result = _run(execution_runtime, prompt_version, is_sdk=is_sdk)

    parse.assert_called_once()
    execution_runtime.runner.assert_not_called()
    log_exception.assert_called_once_with(f"Error in run method: {error}")
    _assert_no_exception_text(result, error)
    assert result == {"responses": ["Failed to run prompt"], "evaluations": [None]}
    if is_sdk:
        execution_runtime.socket.send_error_message.assert_not_called()
    else:
        execution_runtime.socket.send_error_message.assert_called_once_with(
            template_id=str(prompt_version.original_template_id),
            version=prompt_version.template_version,
            error="Failed to run prompt",
            result_index=0,
            num_results=1,
        )
        _assert_no_exception_text(
            execution_runtime.socket.send_error_message.call_args.kwargs, error
        )


@pytest.mark.parametrize("run_index", [None, 1], ids=["iteration", "selected"])
def test_run_eval_id_error_is_private(
    prompt_version, read_execution_details, run_index
):
    error = OperationalError(SENTINEL)
    view = prompt_views.PromptTemplateViewSet()
    config_id = str(uuid4())
    with (
        patch.object(view, "run_evaluation", side_effect=error) as evaluate,
        patch.object(prompt_views, "track_running_eval_count") as track,
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        view.run_eval_id(
            config_id,
            "Response",
            [],
            {},
            prompt_version.original_template.organization_id,
            prompt_version.original_template,
            None,
            None,
            prompt_version,
            run_index,
            0,
        )

    evaluate.assert_called_once()
    log_exception.assert_called_once_with(f"Error in evaluation: {error}")
    track.assert_called_once_with(
        start=False, prompt_config_eval_id=config_id, operation="set"
    )
    prompt_version.refresh_from_db()
    index = run_index if run_index is not None else 0
    result = prompt_version.evaluation_results[config_id]["results"][index]
    _assert_no_exception_text(prompt_version.evaluation_results, error)
    assert result == {
        "output": "Failed to run evaluation",
        "value": None,
        "status": StatusType.ERROR.value,
        "meta": {
            "response_time_ms": 0,
            "reason": "Failed to run evaluation",
            "failure": True,
            "token_count": 0,
        },
    }
    history = read_execution_details(prompt_version)
    assert history.status_code == 200, history.data
    assert (
        history.data["result"]["evaluation_results"]
        == prompt_version.evaluation_results
    )
    _assert_no_exception_text(history.data, error)


def test_run_success_is_unchanged(execution_runtime, prompt_version):
    execution_runtime.runner.return_value.litellm_response.return_value = (
        "Public model output",
        {"metadata": {"token_count": 3}},
    )
    with patch.object(prompt_views.logger, "exception") as log_exception:
        result = _run(execution_runtime, prompt_version)

    assert result == {"result": {}}
    prompt_version.refresh_from_db()
    assert prompt_version.output == ["Public model output"]
    assert prompt_version.metadata == [{"token_count": 3}]
    assert prompt_version.is_draft is False
    execution_runtime.runner.return_value.litellm_response.assert_called_once()
    execution_runtime.socket.send_error_message.assert_not_called()
    execution_runtime.socket.send_all_completed_message.assert_called_once_with(
        template_id=str(prompt_version.original_template_id),
        version=prompt_version.template_version,
    )
    log_exception.assert_not_called()


def test_run_eval_id_success_is_unchanged(prompt_version):
    view = prompt_views.PromptTemplateViewSet()
    config_id = str(uuid4())
    with (
        patch.object(
            view,
            "run_evaluation",
            return_value=(
                0.75,
                {
                    "output": "Public evaluation output",
                    "runtime": 12,
                    "reason": "Public explanation",
                    "metadata": {"token_count": 3},
                },
            ),
        ) as evaluate,
        patch.object(prompt_views, "track_running_eval_count"),
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        view.run_eval_id(
            config_id,
            "Response",
            [],
            {},
            prompt_version.original_template.organization_id,
            prompt_version.original_template,
            None,
            None,
            prompt_version,
            None,
            0,
        )

    evaluate.assert_called_once()
    prompt_version.refresh_from_db()
    assert prompt_version.evaluation_results[config_id]["results"][0] == {
        "output": "Public evaluation output",
        "value": 0.75,
        "status": StatusType.COMPLETED.value,
        "meta": {
            "response_time_ms": 12,
            "reason": "Public explanation",
            "failure": False,
            "token_count": 3,
        },
    }
    log_exception.assert_not_called()


# A saved version can carry null for messages, configuration, tools,
# tool_choice or response_format (SDK and API writes store what they are
# sent). Run treats each stored null like an absent value.

HELLO = [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}]
STORED_NULLS = {
    "tools-null": {
        "messages": HELLO,
        "configuration": {"model": "gpt-4o-mini", "tools": None},
    },
    "tool_choice-null": {
        "messages": HELLO,
        "configuration": {"model": "gpt-4o-mini", "tool_choice": None},
    },
    "response_format-null": {
        "messages": HELLO,
        "configuration": {"model": "gpt-4o-mini", "response_format": None},
    },
    "tools-tool_choice-response_format-null": {
        "messages": HELLO,
        "configuration": {
            "model": "gpt-4o-mini",
            "tools": None,
            "tool_choice": None,
            "response_format": None,
        },
    },
    "configuration-null": {"messages": HELLO, "configuration": None},
    "messages-null": {"messages": None, "configuration": {"model": "gpt-4o-mini"}},
}


@pytest.fixture
def stored_snapshot_runtime():
    # Real variable replacement; no provider, billing or connection work.
    with (
        patch.object(prompt_views, "close_old_connections"),
        patch.object(prompt_views.Organization.objects, "get", return_value=None),
        patch.object(prompt_views, "RunPrompt") as runner,
    ):
        runner.return_value.litellm_response.return_value = (
            "Stored version output",
            {"metadata": {"token_count": 2}},
        )
        yield SimpleNamespace(
            view=prompt_views.PromptTemplateViewSet(),
            socket=Mock(),
            runner=runner,
        )


def _store_snapshot(prompt_version, snapshot):
    prompt_version.prompt_config_snapshot = snapshot
    prompt_version.save(update_fields=["prompt_config_snapshot"])
    prompt_version.refresh_from_db()
    assert prompt_version.prompt_config_snapshot == snapshot


def _run_stored(runtime, prompt_version):
    with patch.object(prompt_views.logger, "exception") as log_exception:
        result = _run(runtime, prompt_version)
    log_exception.assert_not_called()
    assert result == {"result": {}}
    runtime.runner.assert_called_once()
    runtime.runner.return_value.litellm_response.assert_called_once()
    runtime.socket.send_error_message.assert_not_called()
    prompt_version.refresh_from_db()
    assert prompt_version.output == ["Stored version output"]
    assert prompt_version.metadata == [{"token_count": 2}]
    assert prompt_version.is_draft is False
    return runtime.runner.call_args.kwargs


@pytest.mark.parametrize(
    "snapshot", list(STORED_NULLS.values()), ids=list(STORED_NULLS)
)
def test_run_treats_stored_null_as_absent(
    stored_snapshot_runtime, prompt_version, snapshot
):
    _store_snapshot(prompt_version, snapshot)

    sent = _run_stored(stored_snapshot_runtime, prompt_version)

    assert sent["tools"] == []
    assert sent["tool_choice"] is None
    assert sent["response_format"] is None
    assert sent["messages"] == ([] if snapshot["messages"] is None else HELLO)
    configuration = snapshot["configuration"] or {}
    assert sent["model"] == configuration.get("model")


def test_run_stored_tools_and_formats_are_unchanged(
    stored_snapshot_runtime, prompt_version
):
    parameters = {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    }
    _store_snapshot(
        prompt_version,
        {
            "messages": HELLO,
            "configuration": {
                "model": "gpt-4o-mini",
                "temperature": 0.2,
                "tools": [
                    {
                        "name": "lookup_city",
                        "description": "Look up a city",
                        "config": {"parameters": parameters},
                    },
                    {"name": "no_schema", "description": "Skipped", "config": None},
                ],
                "tool_choice": "auto",
                "response_format": {"type": "json_object"},
            },
        },
    )

    sent = _run_stored(stored_snapshot_runtime, prompt_version)

    assert sent["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "lookup_city",
                "description": "Look up a city",
                "parameters": parameters,
            },
        }
    ]
    assert sent["tool_choice"] == "auto"
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["model"] == "gpt-4o-mini"
    assert sent["temperature"] == 0.2
    assert sent["messages"] == HELLO
