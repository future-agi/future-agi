"""Prompts > Run Prompt over HTTP: a Jinja part the sandbox does not allow is refused.

When the live stream cannot start, the workbench runs the prompt over HTTP:
``run_template`` with ``version`` and ``is_run: "prompt"``, then
``get-run-status``. With the template format Jinja, the message "Reply with
exactly this text and nothing else: TYPE {{ name.__class__ }} END" and
name = Ada, that run sent "... TYPE  END" to the model and wrote a
``run_prompt_gen`` APICallLog row for it, while the stream stops the same
prompt before the model is called. The HTTP run now stops it the same way:
the socket error and the stored output carry the stream's line, the model is
never called, and no APICallLog row, credit entry or usage event is written.
An allowed Jinja template still runs and is charged once, as before.

The stand-ins are the model client (``RunPrompt``), the socket manager and the
background executor (run inline). The billing writer
(``log_and_deduct_cost_for_api_request``), its tables, the managed credit
ledger tables and the usage emitter are the real ones.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.test import override_settings

from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.tests.network_isolation import (
    no_external_lookups as no_external_lookups,
)
from model_hub.tests.test_prompt_run_refused_jinja import MESSAGE, REFUSED_LINE
from model_hub.views import prompt_template as prompt_views
from tfc.constants.api_calls import APICallTypeChoices

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.requires_ee,
    pytest.mark.usefixtures(
        "no_external_lookups", "urlconf_loaded_without_workspace_context"
    ),
]

ANSWER = "TYPE END"
ALLOWED = "Reply with exactly: HELLO {{ name }}"
TOKENS = 7
PRICE_PER_TOKEN = Decimal("0.001")
LOCAL_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def _config(text, template_format):
    return {
        "messages": [{"role": "user", "content": [{"type": "text", "text": text}]}],
        "configuration": {"model": "gpt-4o-mini", "template_format": template_format},
    }


@pytest.fixture
def metered_organization(organization):
    """The real legacy billing writer charges this organization for a run.

    PROMPT_BENCH is a billing call type: with a call type row, a subscription
    that is not rate limited and an organization price, a run that reaches the
    model writes one APICallLog row priced per token.
    """
    from ee.usage.models.usage import (
        APICallType,
        OrganizationSubscription,
        PlanChoices,
        Pricing,
        SubscriptionTier,
        SubscriptionTierChoices,
    )

    call_type, _ = APICallType.objects.get_or_create(
        name=APICallTypeChoices.PROMPT_BENCH.value
    )
    tier = SubscriptionTier.objects.create(
        name=SubscriptionTierChoices.CUSTOM.value,
        description="HTTP run refused Jinja test",
        wallet_refill_amount=0,
    )
    OrganizationSubscription.objects.update_or_create(
        organization=organization,
        defaults={"subscription_tier": tier, "plan": PlanChoices.ENTERPRISE.value},
    )
    Pricing.objects.create(
        api_call_type=call_type,
        organization=organization,
        price_per_call=PRICE_PER_TOKEN,
    )
    return organization


@pytest.fixture
def meters(monkeypatch):
    """Counts of every money record a run could write, read from the real tables."""
    from ee.usage.models.billing_ledger import UsageEventRecord
    from ee.usage.models.managed_credits import ManagedCreditEntry
    from ee.usage.models.usage import APICallLog, UsageEventLog
    from ee.usage.services import emitter

    emitted = []
    real_emit = emitter.emit

    def recording_emit(event, *args, **kwargs):
        # Records, then hands the event to the real emitter.
        emitted.append(event)
        return real_emit(event, *args, **kwargs)

    monkeypatch.setattr(emitter, "emit", recording_emit)

    def read():
        # The base manager: every row, whatever workspace context the request
        # left behind.
        return {
            "api_call_logs": APICallLog._base_manager.count(),
            "managed_credit_entries": ManagedCreditEntry._base_manager.count(),
            "usage_event_logs": UsageEventLog._base_manager.count(),
            "usage_event_records": UsageEventRecord._base_manager.count(),
            "usage_events_emitted": len(emitted),
        }

    return read


@pytest.fixture
def http_run(monkeypatch, auth_client, metered_organization, workspace, user):
    """POST run_template as the workbench's HTTP fallback does, then read the status."""
    socket = Mock()
    runner = Mock()
    runner.return_value.litellm_response.return_value = (
        ANSWER,
        {"metadata": {"usage": {"total_tokens": TOKENS}}},
    )
    monkeypatch.setattr(prompt_views, "RunPrompt", runner)
    monkeypatch.setattr(prompt_views, "get_websocket_manager", lambda _org: socket)
    monkeypatch.setattr(prompt_views, "close_old_connections", lambda: None)
    # The executor runs the submitted run in this thread, on this transaction.
    monkeypatch.setattr(
        prompt_views,
        "submit_with_retry",
        lambda _executor, func, *args, **kwargs: func(*args, **kwargs),
    )

    def run(text, template_format="jinja"):
        config = _config(text, template_format)
        template = PromptTemplate.no_workspace_objects.create(
            name=f"HTTP run {template_format}",
            organization=metered_organization,
            workspace=workspace,
            created_by=user,
            variable_names={"name": ["Ada"]},
        )
        version = PromptVersion.no_workspace_objects.create(
            original_template=template,
            template_version="v1",
            prompt_config_snapshot=config,
            variable_names={"name": ["Ada"]},
            is_draft=True,
        )
        with override_settings(CACHES=LOCAL_CACHE):
            response = auth_client.post(
                f"/model-hub/prompt-templates/{template.id}/run_template/",
                {
                    "name": template.name,
                    "version": "v1",
                    "prompt_config": [config],
                    "variable_names": {"name": ["Ada"]},
                    "evaluation_configs": [],
                    "is_run": "prompt",
                },
                format="json",
            )
            assert response.status_code == 200, response.content
            status = auth_client.get(
                f"/model-hub/prompt-templates/{template.id}/get-run-status/",
                {"template_version": "v1"},
            )
        assert status.status_code == 200, status.content
        version.refresh_from_db()
        return SimpleNamespace(version=version, status=status.json()["result"])

    return SimpleNamespace(run=run, socket=socket, runner=runner)


@pytest.mark.parametrize("template_format", ["jinja", "jinja2"])
def test_the_http_run_stops_with_the_stream_line_before_the_model_or_any_charge(
    http_run, meters, template_format
):
    before = meters()

    result = http_run.run(MESSAGE, template_format)

    # The model client is never built or called.
    http_run.runner.assert_not_called()
    http_run.runner.return_value.litellm_response.assert_not_called()
    # No charge, hold or usage event: every money table is as it was.
    assert meters() == before
    # The stream's line, in the socket error, the stored version and the
    # status the fallback reads.
    http_run.socket.send_error_message.assert_called_once_with(
        template_id=str(result.version.original_template_id),
        version="v1",
        error=REFUSED_LINE,
        result_index=0,
        num_results=1,
    )
    assert result.version.output == [REFUSED_LINE]
    assert result.version.metadata == [None]
    assert result.version.is_draft is False
    assert result.status["status"] == "completed"
    assert result.status["executions_result"]["output"] == [REFUSED_LINE]


def test_guard_passes_on_base_too_an_allowed_jinja_template_runs_and_is_charged_once(
    http_run, meters, metered_organization
):
    from ee.usage.models.usage import APICallLog

    before = meters()

    result = http_run.run(ALLOWED)

    http_run.runner.assert_called_once()
    assert http_run.runner.call_args.kwargs["messages"] == [
        {
            "role": "user",
            "content": [{"type": "text", "text": "Reply with exactly: HELLO Ada"}],
        }
    ]
    http_run.runner.return_value.litellm_response.assert_called_once()
    http_run.socket.send_error_message.assert_not_called()
    assert result.version.output == [ANSWER]
    assert result.status["executions_result"]["output"] == [ANSWER]
    # The real billing writer charged this run once, per token: the meter the
    # refused run is checked against is live.
    assert meters()["api_call_logs"] == before["api_call_logs"] + 1
    row = APICallLog._base_manager.get(
        organization=metered_organization, source="run_prompt_gen"
    )
    assert row.api_call_type.name == APICallTypeChoices.PROMPT_BENCH.value
    assert row.input_token_count == TOKENS
    assert row.cost == PRICE_PER_TOKEN * TOKENS
