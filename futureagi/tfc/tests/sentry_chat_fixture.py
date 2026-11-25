"""One claimed prompt chat, with in-memory substitutes for its external I/O."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import redis
import tiktoken

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.api_key import ApiKey
from model_hub.models.custom_models import CustomAIModel
from model_hub.models.run_prompt import PromptVersion
from simulate.models import CallExecution, ChatMessageModel, RunTest, Scenarios
from simulate.models.test_execution import TestExecution


def claimed_prompt_chat(monkeypatch):
    organization = Organization(name="Fixture org")
    run = RunTest(
        source_type="prompt",
        organization=organization,
        workspace=Workspace(organization=organization),
        prompt_version=PromptVersion(
            prompt_config_snapshot={
                "configuration": {"model": "gpt-4o-mini"},
                "messages": [{"role": "system", "content": "Answer briefly."}],
            },
        ),
    )
    call = CallExecution(
        status=CallExecution.CallStatus.ONGOING,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        scenario=Scenarios(name="Fixture scenario"),
        test_execution=TestExecution(
            run_test=run, status=TestExecution.ExecutionStatus.RUNNING
        ),
        call_metadata={"system_prompt": "Ask one question.", "initial_message": "Hi!"},
    )

    by_id = {str(call.id): call}
    query = SimpleNamespace(get=lambda *, id: by_id[id])
    monkeypatch.setattr(CallExecution.objects, "select_related", lambda *args: query)

    # Keep the stored row separate from the live call, including its mutable metadata.
    stored = {
        field: deepcopy(getattr(call, field))
        for field in (
            "assistant_id",
            "call_metadata",
            "simulation_call_type",
            "started_at",
            "status",
            "ended_reason",
            "ended_at",
        )
    }

    def save(*, update_fields=None):
        fields = stored.keys() if update_fields is None else update_fields
        stored.update({field: deepcopy(getattr(call, field)) for field in fields})

    def refresh_from_db():
        for field, value in stored.items():
            setattr(call, field, deepcopy(value))

    monkeypatch.setattr(call, "save", Mock(wraps=save))
    monkeypatch.setattr(call, "refresh_from_db", refresh_from_db)
    messages = []

    def create_message(**fields):
        row = ChatMessageModel(**fields)
        messages.append(row)
        return row

    def filter_messages(*, call_execution):
        rows = [m for m in messages if m.call_execution_id == call_execution.id]
        return SimpleNamespace(
            order_by=lambda key: sorted(rows, key=lambda row: getattr(row, key)),
            aggregate=lambda **kwargs: {"total": sum(row.tokens for row in rows)},
        )

    monkeypatch.setattr(ChatMessageModel.objects, "create", create_message)
    monkeypatch.setattr(ChatMessageModel.objects, "filter", filter_messages)

    # The task imports lock/state helpers that PING at import time. Stub the
    # Redis command transport *before* loading the task, not its usage checks.
    cache = {
        f"plan:{organization.id}": "payg",
        f"billing_status:{organization.id}": "active",
    }

    def command(client, verb, *args, **kwargs):
        if verb == "PING":
            return True
        if verb == "GET":
            return cache.get(args[0])
        if verb == "PUBLISH":
            return 0
        pytest.fail(f"Unexpected Redis command: {verb}")

    monkeypatch.setattr(redis.Redis, "execute_command", command)
    # The token estimator otherwise downloads a vocabulary on a cold machine.
    encoding = SimpleNamespace(encode=str.split)
    monkeypatch.setattr(tiktoken, "get_encoding", lambda name: encoding)

    # Keep prompt assembly, model selection and response handling real. The
    # catalog sees no custom rows and one synthetic workspace credential.
    no_custom_models = CustomAIModel.objects.none()
    monkeypatch.setattr(CustomAIModel.objects, "get_queryset", lambda: no_custom_models)
    key = SimpleNamespace(key="fixture", actual_key="fixture")

    def workspace_key(*, organization_id, workspace_id, provider):
        assert organization_id == str(organization.id)
        assert workspace_id == str(run.workspace_id) and provider == "openai"
        return SimpleNamespace(first=lambda: key)

    monkeypatch.setattr(ApiKey.objects, "filter", workspace_key)

    import litellm

    def model_reply(*, messages, model, **kwargs):
        assert messages[-1] == {"role": "user", "content": "Hi!"}
        return litellm.ModelResponse(
            model=model,
            choices=[{"message": {"role": "assistant", "content": "How can I help?"}}],
            usage={"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
        )

    monkeypatch.setattr(litellm, "completion", model_reply)
    return call
