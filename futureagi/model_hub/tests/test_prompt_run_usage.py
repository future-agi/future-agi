"""Prompt runner usage emission must not be skipped or break prompt results."""

from unittest.mock import Mock

import pytest

from model_hub.views import prompt_template as prompt_views

pytestmark = [pytest.mark.django_db, pytest.mark.requires_ee]


@pytest.fixture
def run_prompt_with_usage(monkeypatch, organization, workspace, prompt_version):
    from ee.usage.services import emitter

    # The background runner must not close pytest-django's transaction connection.
    monkeypatch.setattr(prompt_views, "close_old_connections", Mock())
    metadata = {"usage": {"total_tokens": 12}}
    provider_response = Mock(return_value=("Hello back", {"metadata": metadata}))
    monkeypatch.setattr(prompt_views.RunPrompt, "litellm_response", provider_response)
    legacy_charge = Mock()
    monkeypatch.setattr(
        prompt_views, "log_and_deduct_cost_for_api_request", legacy_charge
    )
    emit = Mock()
    monkeypatch.setattr(emitter, "emit", emit)
    ws_manager = Mock()

    def run():
        result = prompt_views.PromptTemplateViewSet().run(
            prompt_version.original_template,
            prompt_version,
            organization.id,
            "v1",
            is_run="prompt",
            workspace=workspace,
            ws_manager=ws_manager,
        )
        assert result == {"result": {}}
        provider_response.assert_called_once()
        prompt_version.refresh_from_db()
        assert prompt_version.output == ["Hello back"]
        assert prompt_version.metadata == [metadata]
        assert prompt_version.is_draft is False
        legacy_charge.assert_called_once_with(
            organization,
            "prompt_bench",
            config={"input_tokens": 12},
            source="run_prompt_gen",
            workspace=workspace,
        )
        ws_manager.send_error_message.assert_not_called()
        ws_manager.send_all_completed_message.assert_called_once_with(
            template_id=str(prompt_version.original_template_id), version="v1"
        )

    return run, emit


def test_prompt_run_emits_usage(run_prompt_with_usage, organization, prompt_version):
    from ee.usage.schemas.events import UsageEvent

    run, emit = run_prompt_with_usage
    run()

    emit.assert_called_once()
    event = emit.call_args.args[0]
    assert isinstance(event, UsageEvent)
    assert event.org_id == str(organization.id)
    assert event.event_type == "prompt_bench"
    assert event.amount == 1
    assert event.properties == {
        "source": "run_prompt_gen",
        "source_id": str(prompt_version.original_template_id),
    }


def test_prompt_run_keeps_result_when_usage_emit_fails(run_prompt_with_usage):
    run, emit = run_prompt_with_usage
    emit.side_effect = RuntimeError("metering unavailable")

    run()

    emit.assert_called_once()


def test_prompt_run_keeps_result_without_usage_emitter(
    run_prompt_with_usage, monkeypatch
):
    from ee.usage.services import emitter

    run, emit = run_prompt_with_usage
    monkeypatch.setattr(emitter, "emit", None)

    run()

    emit.assert_not_called()
