"""Prompts > Run Prompt over HTTP: a model endpoint's own token count is shown only.

A model endpoint's own token count is shown only; it never feeds a charge. If
that recorded cost is ever billed, the platform's own count is used, decided
separately.

A custom model (Settings > AI Providers, provider "custom") reports its own
token count with each answer, and the custom model handler takes it when each
count is a whole number from 0 to 10,000,000. On the workbench's HTTP run
(``run_template`` with ``version`` and ``is_run: "prompt"``) that count is
shown with the answer and becomes the run's usage log row: ``input_token_count``
and ``cost`` (price per token x the count). Nothing is charged:
``deducted_cost`` is 0, the row's status is ``processing``, the wallet balance
and the managed credit entries are unchanged, and a usage event, where the run
sends one, carries no count. Each count is checked at 0 and at 10,000,000. The
wallet holds 1,000 times the largest cost, so a debit in any case with a
nonzero count would show; the both-zero case pins the shown count and a
zero-cost row.

The request goes through the API client and the URL. The model client
(``RunPrompt``), the handler factory, the custom model handler, the endpoint
call and its address checks, the billing writer and its tables are the real
ones. The stand-ins are the endpoint's DNS and transport (the guarded-post
fakes, so nothing leaves the process), the socket manager, the background
executor (run inline), the connection close (a no-op, so the run stays on the
test's transaction), the cache (local memory) and the usage emitter
(recorded, not sent).
"""

import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.test import override_settings

from model_hub.models.custom_models import CustomAIModel
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.tests.network_isolation import (
    no_external_lookups as no_external_lookups,
)
from model_hub.views import prompt_template as prompt_views
from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices
from tfc.utils.tests.guarded_post_fakes import (
    JSON_HEADERS,
    FakeResolver,
    FakeResponse,
    FakeTransport,
    UnguardedHttp,
)
from tfc.utils.tests.loopback_endpoints import clear_proxy_env

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.requires_ee,
    pytest.mark.usefixtures(
        "no_external_lookups", "urlconf_loaded_without_workspace_context"
    ),
]

MODEL = "endpoint-count-custom"
ENDPOINT_HOST = "api.example.com"
ENDPOINT = f"https://{ENDPOINT_HOST}/v1/chat/completions"
PUBLIC_IP = "93.184.216.34"
ANSWER = "Green tea."
PROMPT = "Name one drink in two words."
# The most the custom model handler takes for each count the endpoint reports.
COUNT_CAP = 10_000_000
# Both counts at the cap (20,000,000 tokens) cost 100 at this price: the
# largest cost of any case.
PRICE_PER_TOKEN = Decimal("0.000005")
WALLET = Decimal("100000")
LOCAL_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@pytest.fixture
def metered_organization(organization):
    """An organization the real billing writer prices per token.

    PROMPT_BENCH is a billing call type: with a call type row, a subscription
    that is not rate limited and an organization price, a run that reaches the
    model writes one APICallLog row priced per token. The wallet holds
    1,000 times the largest cost of any case.
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
    tier, _ = SubscriptionTier.objects.get_or_create(
        name=SubscriptionTierChoices.CUSTOM.value,
        description="Workbench endpoint count test",
        defaults={"wallet_refill_amount": 0},
    )
    OrganizationSubscription.objects.update_or_create(
        organization=organization,
        defaults={
            "subscription_tier": tier,
            "plan": PlanChoices.ENTERPRISE.value,
            "wallet_balance": WALLET,
        },
    )
    Pricing.objects.create(
        api_call_type=call_type,
        organization=organization,
        price_per_call=PRICE_PER_TOKEN,
    )
    return organization


@pytest.fixture
def emitted(monkeypatch):
    """Usage events the run sends, recorded and not sent on."""
    from ee.usage.services import emitter

    events = []
    monkeypatch.setattr(emitter, "emit", events.append)
    return events


@pytest.fixture
def endpoint(monkeypatch, user, workspace):
    """The organization's custom model, and its endpoint answered in process.

    The endpoint call is the real one; its DNS and transport are the
    guarded-post fakes, so the host resolves to a public address and nothing
    leaves the process.
    """
    CustomAIModel.objects.create(
        user_model_id=MODEL,
        provider="custom",
        input_token_cost=0,
        output_token_cost=0,
        organization=user.organization,
        workspace=workspace,
        key_config={"api_base": ENDPOINT, "headers": {}, "custom_provider": True},
        user=user,
        deleted=False,
    )
    clear_proxy_env(monkeypatch)
    monkeypatch.delenv("CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS", raising=False)
    FakeResolver().install(monkeypatch).add(ENDPOINT_HOST, PUBLIC_IP)
    transport = FakeTransport().install(monkeypatch)
    unguarded = UnguardedHttp().install(monkeypatch)
    return SimpleNamespace(transport=transport, unguarded=unguarded)


def _completion_body(prompt_tokens, completion_tokens):
    """A chat-completions answer with the endpoint's own count."""
    return json.dumps(
        {
            "id": "endpoint-count",
            "choices": [{"message": {"role": "assistant", "content": ANSWER}}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
    ).encode()


def _wallet(organization):
    from ee.usage.models.usage import OrganizationSubscription

    return OrganizationSubscription._base_manager.get(
        organization=organization
    ).wallet_balance


def _managed_credit_entries():
    from ee.usage.models.managed_credits import ManagedCreditEntry

    return ManagedCreditEntry._base_manager.count()


@pytest.fixture
def http_run(monkeypatch, auth_client, metered_organization, workspace, user, endpoint):
    """POST run_template as the workbench's HTTP fallback does."""
    socket = Mock()
    monkeypatch.setattr(prompt_views, "get_websocket_manager", lambda _org: socket)
    monkeypatch.setattr(prompt_views, "close_old_connections", lambda: None)
    # The executor runs the submitted run in this thread, on this transaction.
    monkeypatch.setattr(
        prompt_views,
        "submit_with_retry",
        lambda _executor, func, *args, **kwargs: func(*args, **kwargs),
    )

    def run(prompt_tokens, completion_tokens):
        endpoint.transport.respond(
            FakeResponse(
                200, JSON_HEADERS, _completion_body(prompt_tokens, completion_tokens)
            )
        )
        config = {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": PROMPT}]}
            ],
            "configuration": {"model": MODEL},
        }
        template = PromptTemplate.no_workspace_objects.create(
            name="Endpoint count",
            organization=metered_organization,
            workspace=workspace,
            created_by=user,
        )
        version = PromptVersion.no_workspace_objects.create(
            original_template=template,
            template_version="v1",
            prompt_config_snapshot=config,
            variable_names={},
            is_draft=True,
        )
        # A hosted deployment: the endpoint's public address is allowed.
        with override_settings(CACHES=LOCAL_CACHE, CLOUD_DEPLOYMENT="US"):
            response = auth_client.post(
                f"/model-hub/prompt-templates/{template.id}/run_template/",
                {
                    "name": template.name,
                    "version": "v1",
                    "prompt_config": [config],
                    "variable_names": {},
                    "evaluation_configs": [],
                    "is_run": "prompt",
                },
                format="json",
            )
        assert response.status_code == 200, response.content
        version.refresh_from_db()
        return version

    return SimpleNamespace(run=run, socket=socket, endpoint=endpoint)


@pytest.mark.parametrize(
    ("prompt_tokens", "completion_tokens"),
    [
        (0, 0),
        (COUNT_CAP, COUNT_CAP),
        (0, COUNT_CAP),
        (COUNT_CAP, 0),
    ],
    ids=[
        "both-zero",
        "both-at-cap",
        "input-zero-output-at-cap",
        "input-at-cap-output-zero",
    ],
)
def test_a_model_endpoints_token_count_never_changes_what_is_charged(
    http_run, emitted, metered_organization, prompt_tokens, completion_tokens
):
    from ee.usage.models.usage import APICallLog

    reported = prompt_tokens + completion_tokens
    credit_entries_before = _managed_credit_entries()
    assert _wallet(metered_organization) == WALLET

    version = http_run.run(prompt_tokens, completion_tokens)

    # The run reached the endpoint once, through the one call, and answered.
    assert len(http_run.endpoint.transport.requests) == 1
    assert http_run.endpoint.unguarded.calls == []
    http_run.socket.send_error_message.assert_not_called()
    assert version.output == [ANSWER]
    # The endpoint's own count is the one shown with the answer.
    assert version.metadata[0]["usage"] == {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": reported,
    }

    rows = APICallLog._base_manager.filter(
        organization=metered_organization, source="run_prompt_gen"
    )
    assert rows.count() == 1
    row = rows.get()
    assert row.api_call_type.name == APICallTypeChoices.PROMPT_BENCH.value
    # The count reached the billing writer: the row records it and its price.
    assert row.input_token_count == reported
    assert row.cost == PRICE_PER_TOKEN * reported
    # Not charged: nothing deducted and the wallet unchanged. The status is
    # processing, not insufficient_credits, because the wallet covers every cost.
    assert row.deducted_cost == 0
    assert row.status == APICallStatusChoices.PROCESSING.value
    assert _wallet(metered_organization) == WALLET
    assert _managed_credit_entries() == credit_entries_before
    # A usage event, where the run sends one, is one call with no count in it.
    assert [(event.amount, sorted(event.properties)) for event in emitted] in (
        [],
        [(1, ["source", "source_id"])],
    )
