"""Workspace-key isolation and raw protocol, with LiteLLM transport fixtures."""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from model_hub.models.api_key import ApiKey
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.model_calls import (
    _normal_message,
    call_allowance,
    complete_skill_turn,
    list_test_models,
    resolve_test_connection,
)
from model_hub.services.skills_hub.v11.types import SkillScope

pytestmark = pytest.mark.django_db


@pytest.fixture
def configured(settings, organization, workspace):
    settings.SKILLS_VERIFIED_MODELS = ["gpt-4o-mini"]
    key = ApiKey.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        provider="openai",
        key="synthetic-test-value",
    )
    return SkillScope(organization.id, workspace.id), key


def test_picker_and_resolver_use_same_workspace(
    configured, hub_other_workspace, organization
):
    scope, key = configured
    connection = resolve_test_connection(scope=scope, model_id="gpt-4o-mini")
    assert connection.connection_id == str(key.id)
    assert connection.api_key == "synthetic-test-value"
    assert connection.api_key not in repr(connection)
    assert connection.api_key not in str(connection.identity())
    assert list_test_models(scope=scope)[0]["can_run"]
    other = SkillScope(organization.id, hub_other_workspace.id)
    assert not list_test_models(scope=other)[0]["can_run"]
    with pytest.raises(SkillsHubError) as caught:
        resolve_test_connection(scope=other, model_id="gpt-4o-mini")
    assert caught.value.code == "model_not_configured"


def test_no_global_or_environment_key_fallback(
    settings, monkeypatch, organization, workspace
):
    settings.SKILLS_VERIFIED_MODELS = ["gpt-4o-mini"]
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-env-value")
    global_key = ApiKey.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        provider="openai",
        key="synthetic-global-value",
    )
    # Existing post-save auto-assignment would otherwise turn this into a workspace key.
    ApiKey.no_workspace_objects.filter(pk=global_key.pk).update(workspace=None)
    with pytest.raises(SkillsHubError) as caught:
        resolve_test_connection(
            scope=SkillScope(organization.id, workspace.id), model_id="gpt-4o-mini"
        )
    assert caught.value.code == "model_not_configured"


def test_foreign_scope_and_deleted_key_denied(configured, hub_other_organization):
    scope, key = configured
    with pytest.raises(SkillsHubError):
        resolve_test_connection(
            scope=SkillScope(hub_other_organization.id, scope.workspace_id),
            model_id="gpt-4o-mini",
        )
    ApiKey.no_workspace_objects.filter(id=key.id).update(deleted=True)
    with pytest.raises(SkillsHubError) as caught:
        resolve_test_connection(scope=scope, model_id="gpt-4o-mini")
    assert caught.value.code == "model_not_configured"


def test_unverified_models_fail_closed(configured, settings):
    scope, _ = configured
    settings.SKILLS_VERIFIED_MODELS = []
    assert list_test_models(scope=scope) == []
    with pytest.raises(SkillsHubError) as caught:
        resolve_test_connection(scope=scope, model_id="gpt-4o-mini")
    assert caught.value.code == "model_tools_unsupported"


def test_normalizer_preserves_empty_tool_messages_and_strips_reasoning():
    raw = {
        "content": None,
        "reasoning_content": "private reasoning",
        "tool_calls": [
            {
                "id": "parallel-1",
                "type": "function",
                "function": {"name": "load_skill", "arguments": '{"skill_id":"x"}'},
            },
            {
                "id": "parallel-2",
                "type": "function",
                "function": {"name": "load_skill", "arguments": '{"skill_id":"y"}'},
            },
        ],
    }
    normal = _normal_message(raw)
    assert normal["content"] is None
    assert [call["id"] for call in normal["tool_calls"]] == ["parallel-1", "parallel-2"]
    assert "reasoning_content" not in normal
    assert _normal_message({"content": '[{"function":"not-a-tool"}]'}) == {
        "role": "assistant",
        "content": '[{"function":"not-a-tool"}]',
    }
    raw["tool_calls"][1]["id"] = "parallel-1"
    with pytest.raises(SkillsHubError):
        _normal_message(raw)


def test_raw_completion_explicit_key_no_retries(configured, monkeypatch):
    import litellm

    scope, key = configured
    connection = resolve_test_connection(scope=scope, model_id="gpt-4o-mini")
    response = litellm.ModelResponse(
        id="recorded-call",
        model="gpt-4o-mini-2024-07-18",
        choices=[
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "tool-1",
                            "type": "function",
                            "function": {
                                "name": "load_skill",
                                "arguments": '{"skill_id":"x"}',
                            },
                        }
                    ],
                },
            }
        ],
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )
    transport = AsyncMock(return_value=response)
    monkeypatch.setattr(litellm, "acompletion", transport)
    monkeypatch.setenv("OPENAI_API_BASE", "https://unapproved.example.invalid/v1")
    messages = [{"role": "user", "content": "synthetic request"}]
    result = asyncio.run(
        complete_skill_turn(
            connection=connection,
            messages=messages,
            tools=[],
            parameters={"temperature": 0, "max_output_tokens": 100},
            timeout_ms=5000,
        )
    )
    sent = transport.call_args.kwargs
    assert (
        sent["api_key"] == "synthetic-test-value"
        and sent["num_retries"] == 0
        and sent["drop_params"] is False
        and sent["api_base"] == "https://api.openai.com/v1"
        and sent["custom_llm_provider"] == "openai"
    )
    assert (
        result.assistant_message["content"] is None
        and result.assistant_message["tool_calls"][0]["id"] == "tool-1"
    )
    assert (
        result.returned_model == "gpt-4o-mini-2024-07-18"
        and result.usage["total_tokens"] == 15
    )
    assert result.estimated_cost_usd > 0


def test_provider_timeout_sanitized(configured, monkeypatch):
    import litellm

    scope, _ = configured
    connection = resolve_test_connection(scope=scope, model_id="gpt-4o-mini")
    monkeypatch.setattr(
        litellm,
        "acompletion",
        AsyncMock(side_effect=TimeoutError("sensitive raw transport")),
    )
    with pytest.raises(SkillsHubError) as caught:
        asyncio.run(
            complete_skill_turn(
                connection=connection,
                messages=[{"role": "user", "content": "Synthetic request"}],
                tools=[],
                parameters={"temperature": 0, "max_output_tokens": 100},
                timeout_ms=10,
            )
        )
    assert (
        caught.value.code == "provider_timeout"
        and "sensitive" not in caught.value.message
    )


def test_missing_usage_is_not_reported_as_free(configured, monkeypatch):
    import litellm

    scope, _ = configured
    connection = resolve_test_connection(scope=scope, model_id="gpt-4o-mini")
    response = litellm.ModelResponse(
        model="gpt-4o-mini",
        choices=[
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "Answer"},
            }
        ],
    )
    response.usage = None
    monkeypatch.setattr(litellm, "acompletion", AsyncMock(return_value=response))
    result = asyncio.run(
        complete_skill_turn(
            connection=connection,
            messages=[{"role": "user", "content": "Synthetic request"}],
            tools=[],
            parameters={"temperature": 0, "max_output_tokens": 100},
            timeout_ms=1000,
        )
    )
    assert result.usage is None and result.estimated_cost_usd is None


def test_conservative_allowance_counts_utf8_bytes(configured):
    scope, _ = configured
    connection = resolve_test_connection(scope=scope, model_id="gpt-4o-mini")
    tokens, estimate = call_allowance(
        connection=connection,
        messages=[{"role": "user", "content": "界" * 10}],
        tools=[],
        max_output_tokens=100,
    )
    assert tokens > 1130 and estimate > Decimal(0)
