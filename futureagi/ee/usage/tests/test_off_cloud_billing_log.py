"""Self-hosted usage logging must not depend on a Cloud subscription row (TH-8084 R3).

The off-cloud cap checks return before ``create_organization_subscription_if_not_exists``
(that is pinned in ``test_off_cloud_caps.py``), so organizations created through
``/accounts/organizations/create/`` or ``/accounts/organizations/new/`` have no
``OrganizationSubscription`` row. ``log_and_deduct_cost_for_api_request`` still has to
write its measurement row for included actions such as prompt generation; it must
not return ``None`` and make the view report insufficient credits.

The provider transport is a deterministic in-process double (``_DeterministicProviderLLM``),
token counting is an offline word count (tiktoken would download its BPE file) and the
thread-pool submit runs inline, so no model provider or asset host is ever contacted.
Cloud keeps its lazy subscription bootstrap and its existing failure mode.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from ee.usage.models.usage import APICallLog, APICallType, OrganizationSubscription
from ee.usage.utils.usage_entries import (
    create_organization_subscription_if_not_exists,
    deduct_cost_for_request,
    log_and_deduct_cost_for_api_request,
)
from tfc.capabilities import edition
from tfc.capabilities.tests.edition_factories import make_user
from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices

pytestmark = [pytest.mark.django_db, pytest.mark.requires_ee]

ORG_CREATE_URL = "/accounts/organizations/create/"
ORG_NEW_URL = "/accounts/organizations/new/"
GENERATE_URL = "/model-hub/prompt-templates/generate-prompt/"
PROMPT_BENCH = APICallTypeChoices.PROMPT_BENCH.value


class _DeterministicProviderLLM:
    """Test double for the provider transport used by ``PromptGenerator``.

    It replaces ``agentic_eval.core.llm.llm.LLM`` inside the generator module only:
    no client is built and no network request is made.
    """

    calls: list[dict] = []

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs

    def _get_completion_content(self, messages, **kwargs):
        _DeterministicProviderLLM.calls.append({"messages": messages, **kwargs})
        return "You are a concise bug triage assistant for {{api_name}}."


@pytest.fixture
def provider_double(monkeypatch):
    from ee.agenthub.prompt_generate_agent import prompt_generate
    from model_hub.views import prompt_template

    _DeterministicProviderLLM.calls = []
    monkeypatch.setattr(prompt_generate, "LLM", _DeterministicProviderLLM)
    monkeypatch.setattr(prompt_generate, "call_websocket", lambda *a, **k: None)
    # tiktoken downloads its BPE file on first use; count words offline instead.
    for module in (prompt_generate, prompt_template):
        monkeypatch.setattr(module, "count_text_tokens", lambda text: len(text.split()))
    # Inline instead of the shared ThreadPoolExecutor: same function, same args.
    monkeypatch.setattr(
        prompt_template,
        "submit_with_retry",
        lambda _executor, func, *args, **kwargs: func(*args, **kwargs),
    )
    emitted = []
    monkeypatch.setattr(
        "ee.usage.services.emitter.emit", lambda event: emitted.append(event)
    )
    return emitted


@pytest.fixture
def community(monkeypatch):
    monkeypatch.setattr(edition, "is_cloud", lambda: False)
    monkeypatch.setattr(edition, "enterprise_license_usable", lambda: False)


@pytest.fixture
def licensed_self_host(monkeypatch):
    monkeypatch.setattr(edition, "is_cloud", lambda: False)
    monkeypatch.setattr(edition, "enterprise_license_usable", lambda: True)


def _jwt_client(email, password="testpassword123"):
    client = APIClient()
    login = client.post(
        "/accounts/token/", {"email": email, "password": password}, format="json"
    )
    assert login.status_code == 200, login.content
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['access']}")
    return client


def _generate(client, **headers):
    return client.post(
        GENERATE_URL,
        {"statement": "Write a concise bug triage prompt for a JSON API."},
        format="json",
        **headers,
    )


def _assert_prompt_generated(response, organization, emitted):
    assert response.status_code == 200, response.content
    assert response.json()["result"]["generation_id"].startswith("generate_")
    row = APICallLog.objects.get(
        organization=organization, api_call_type__name=PROMPT_BENCH
    )
    assert row.source == "run_prompt_gen"
    assert row.status == APICallStatusChoices.SUCCESS.value
    assert float(row.deducted_cost) == 0
    assert len(_DeterministicProviderLLM.calls) == 2
    assert [e.event_type for e in emitted] == ["ai_prompt_creation"]
    assert emitted[0].properties["source_id"] == str(row.log_id)
    return row


def test_first_community_org_from_org_create_can_generate_prompts(
    community, provider_double
):
    """AC-03 / AC-05 / AC-18: the first Community org from the org-less creation
    endpoint has no subscription row and still runs included prompt generation."""
    founder = make_user()
    client = _jwt_client(founder.email)
    created = client.post(ORG_CREATE_URL, {"organization_name": "Solo"}, format="json")
    assert created.status_code in (200, 201), created.content
    organization = Organization.objects.get()
    assert not OrganizationSubscription.objects.filter(
        organization=organization
    ).exists()

    response = _generate(_jwt_client(founder.email))

    _assert_prompt_generated(response, organization, provider_double)
    # Self-hosted logging never bootstraps Cloud billing state.
    assert not OrganizationSubscription.objects.filter(
        organization=organization
    ).exists()


def test_additional_licensed_org_can_generate_prompts(
    licensed_self_host, user, provider_double
):
    """AC-03 / AC-10: an Enterprise self-host's additional organization (created
    through the mounted endpoint the licence unlocks) runs prompt generation."""
    client = _jwt_client(user.email)
    created = client.post(ORG_NEW_URL, {"name": "Second Org"}, format="json")
    assert created.status_code == 201, created.content
    result = created.json()["result"]
    organization = Organization.objects.get(id=result["organization"]["id"])
    assert Workspace.no_workspace_objects.filter(organization=organization).exists()
    assert not OrganizationSubscription.objects.filter(
        organization=organization
    ).exists()

    response = _generate(
        client,
        HTTP_X_ORGANIZATION_ID=str(organization.id),
        HTTP_X_WORKSPACE_ID=result["workspace"]["id"],
    )

    _assert_prompt_generated(response, organization, provider_double)


def test_cloud_keeps_the_lazy_subscription_bootstrap(
    edition_cloud, user, provider_double
):
    """AC-13: on Cloud the rate check still creates the Free subscription before
    the billing log, exactly as before."""
    organization = user.organization
    assert not OrganizationSubscription.objects.filter(
        organization=organization
    ).exists()

    response = _generate(_jwt_client(user.email))

    _assert_prompt_generated(response, organization, provider_double)
    assert OrganizationSubscription.objects.filter(organization=organization).exists()


def test_cloud_missing_subscription_still_fails_closed(edition_cloud, organization):
    """AC-13: Cloud's deduction helper keeps its existing behaviour when the
    subscription row is missing (returns None, no log row)."""
    api_call_type = APICallType.objects.get(name=PROMPT_BENCH)
    assert (
        deduct_cost_for_request(organization, PROMPT_BENCH, api_call_type, 10, {})
        is None
    )
    assert not APICallLog.objects.filter(organization=organization).exists()


BILLING_TYPES = [
    APICallTypeChoices.PROMPT_BENCH.value,
    APICallTypeChoices.SYNTHETIC_DATA_GENERATION.value,
    APICallTypeChoices.AUTO_ANNOTATION.value,
    APICallTypeChoices.DATASET_OPTIMIZATION.value,
]


@pytest.mark.parametrize("api_call_type", BILLING_TYPES)
def test_self_hosted_measurement_matches_a_subscribed_org(community, api_call_type):
    """AC-03 / AC-09: off-cloud the measurement row for an org without a
    subscription matches one for an org with the Free subscription; the edition
    rule and the log path never need the row."""
    bare = Organization.objects.create(name="No subscription")
    subscribed = Organization.objects.create(name="Free subscription")
    create_organization_subscription_if_not_exists(subscribed)

    rows = [
        log_and_deduct_cost_for_api_request(
            org, api_call_type, config={"input_tokens": 120, "reference_id": "r3"}
        )
        for org in (bare, subscribed)
    ]

    assert None not in rows, rows
    assert {row.status for row in rows} == {APICallStatusChoices.PROCESSING.value}
    assert rows[0].cost == rows[1].cost
    assert float(rows[0].deducted_cost) == 0
    assert rows[0].input_token_count == 120
    assert not OrganizationSubscription.objects.filter(organization=bare).exists()
