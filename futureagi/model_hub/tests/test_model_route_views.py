"""Managed-aware model availability in the run-prompt model APIs (census
R3-11): GET /model-hub/api/models_list/ and
GET /model-hub/develops/retrieve_run_prompt_options/.

RED on base 651ab5bb: availability is stored-provider membership only
(model_hub/views/run_prompt.py:2358-2373, 2502-2516, 2598-2603), so an
organization with only a Future AGI key sees every model unavailable and no
item carries credential_mode/payer. RED on the prior OK-A bytes: the options
view labelled a provider with an empty ApiKey row "byok / own_provider_key"
while the runtime routes that model on Future AGI credits (OKA-C5).
``TestByokAvailabilityUnchanged`` pins today's BYOK is_available and passes
on base too.

Doubles: only the gateway port (MockGatewayManagedRouting). Provider
credentials are real ApiKey rows; the organization comes from the
authenticated request.
"""

import importlib

import pytest
from rest_framework import status

from accounts.models import Organization
from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS
from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from model_hub.models.api_key import ApiKey
from model_hub.tests.test_model_route_support import (
    ANTHROPIC_CHAT_MODEL,
    BYOK_FIXTURE_VALUE,
    OPENAI_CHAT_MODEL,
    TTS_MODEL,
    eligible_port,
)

pytestmark = pytest.mark.django_db

MODELS_LIST_URL = "/model-hub/api/models_list/"
OPTIONS_URL = "/model-hub/develops/retrieve_run_prompt_options/"

BYOK = {"is_available": True, "credential_mode": "byok", "payer": "own_provider_key"}
MANAGED = {
    "is_available": True,
    "credential_mode": "managed",
    "payer": "future_agi_credits",
}
UNAVAILABLE = {"is_available": False, "credential_mode": "unavailable", "payer": None}


@pytest.fixture
def mr():
    module = importlib.import_module("model_hub.services.model_route")
    from decimal import Decimal
    from model_hub.services import credit_ledger_port as ledger
    ledger.reset_meter_safety()
    with module.managed_routing_port(None), ledger.use_credit_ledger(
        ledger.MockCreditLedger(default_balance=Decimal("100"))
    ):
        yield module
    ledger.reset_meter_safety()


@pytest.fixture
def other_organization(db):
    return Organization.objects.create(name="One-key other organization")


def _store_byok(organization, workspace, user, provider, key=BYOK_FIXTURE_VALUE):
    return ApiKey.no_workspace_objects.create(
        provider=provider,
        organization=organization,
        workspace=workspace,
        key=key,
        user=user,
    )


def _listed(auth_client, name, **params):
    response = auth_client.get(MODELS_LIST_URL, {"name": name, **params})
    assert response.status_code == status.HTTP_200_OK
    rows = response.data["results"]
    assert len(rows) == 1
    return rows[0]


def _option(auth_client, name):
    response = auth_client.get(OPTIONS_URL)
    assert response.status_code == status.HTTP_200_OK
    rows = [m for m in response.data["result"]["models"] if m["model_name"] == name]
    assert len(rows) == 1
    return rows[0]


def _fields(row):
    return {key: row.get(key) for key in ("is_available", "credential_mode", "payer")}


class TestByokAvailabilityUnchanged:
    """Regression: BYOK is_available exactly as today (GREEN on base)."""

    def test_models_list(self, auth_client, organization, workspace, user):
        _store_byok(organization, workspace, user, "openai")
        assert _listed(auth_client, OPENAI_CHAT_MODEL)["is_available"] is True
        assert _listed(auth_client, ANTHROPIC_CHAT_MODEL)["is_available"] is False

    def test_run_prompt_options(self, auth_client, organization, workspace, user):
        _store_byok(organization, workspace, user, "openai")
        assert _option(auth_client, OPENAI_CHAT_MODEL)["is_available"] is True
        assert _option(auth_client, ANTHROPIC_CHAT_MODEL)["is_available"] is False


class TestModelsListManagedAware:
    def test_managed_model_runs_on_credits_without_a_provider_key(
        self, mr, auth_client, organization, workspace
    ):
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL})
        with mr.managed_routing_port(port):
            row = _listed(auth_client, ANTHROPIC_CHAT_MODEL)
        assert _fields(row) == MANAGED
        assert port.eligibility_calls[0]["organization_id"] == str(organization.id)
        assert port.eligibility_calls[0]["workspace_id"] == str(workspace.id)
        assert port.authorize_calls == []

    def test_stored_key_wins_over_managed(
        self, mr, auth_client, organization, workspace, user
    ):
        _store_byok(organization, workspace, user, "openai")
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL})
        with mr.managed_routing_port(port):
            row = _listed(auth_client, OPENAI_CHAT_MODEL)
        assert _fields(row) == BYOK

    def test_neither_key_nor_credits_is_unavailable(self, mr, auth_client):
        assert _fields(_listed(auth_client, ANTHROPIC_CHAT_MODEL)) == UNAVAILABLE

    def test_another_organizations_eligibility_is_not_shown(
        self, mr, auth_client, other_organization
    ):
        port = eligible_port(mr, other_organization, {ANTHROPIC_CHAT_MODEL})
        with mr.managed_routing_port(port):
            row = _listed(
                auth_client,
                ANTHROPIC_CHAT_MODEL,
                organization=str(other_organization.id),
                organization_id=str(other_organization.id),
            )
        assert _fields(row) == UNAVAILABLE

    def test_non_chat_model_is_never_managed(self, mr, auth_client, organization):
        port = eligible_port(mr, organization, {TTS_MODEL})
        with mr.managed_routing_port(port):
            row = _listed(auth_client, TTS_MODEL)
        assert _fields(row) == UNAVAILABLE

    def test_eligibility_failure_fails_safe(
        self, mr, auth_client, organization, workspace, user
    ):
        _store_byok(organization, workspace, user, "openai")
        port = eligible_port(
            mr,
            organization,
            {ANTHROPIC_CHAT_MODEL},
            eligibility_error=RuntimeError("gateway down"),
        )
        with mr.managed_routing_port(port):
            assert _fields(_listed(auth_client, OPENAI_CHAT_MODEL)) == BYOK
            assert _fields(_listed(auth_client, ANTHROPIC_CHAT_MODEL)) == UNAVAILABLE


class TestRunPromptOptionsManagedAware:
    def test_items_carry_credential_mode_and_payer(
        self, mr, auth_client, organization, workspace, user
    ):
        _store_byok(organization, workspace, user, "openai")
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL})
        with mr.managed_routing_port(port):
            managed = _option(auth_client, ANTHROPIC_CHAT_MODEL)
            byok = _option(auth_client, OPENAI_CHAT_MODEL)
            tts = _option(auth_client, TTS_MODEL)
        assert _fields(managed) == MANAGED
        assert _fields(byok) == BYOK
        assert _fields(tts) == UNAVAILABLE

    def test_answers_are_not_written_into_the_shared_catalog(
        self, mr, auth_client, organization, workspace, user
    ):
        _store_byok(organization, workspace, user, "openai")
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL})
        with mr.managed_routing_port(port):
            _option(auth_client, ANTHROPIC_CHAT_MODEL)
        for entry in AVAILABLE_MODELS:
            # Base wrote is_available into these shared dicts (OKA-C10).
            assert "is_available" not in entry
            assert "credential_mode" not in entry
            assert "payer" not in entry


def _runtime_mode(mr, organization, workspace, model, provider):
    """How the runtime runs ``model`` for this organization: what
    get_api_key(allow_managed=True) returns, classified."""
    try:
        value = LiteLLMModelManager(model, organization_id=organization.id).get_api_key(
            organization_id=organization.id,
            workspace_id=workspace.id,
            provider=provider,
            allow_managed=True,
        )
    except mr.ModelRouteUnavailable:
        return "unavailable"
    return "managed" if mr.is_managed_route_binding(value) else "byok"


PAYER_OF_MODE = {
    "byok": "own_provider_key",
    "managed": "future_agi_credits",
    "unavailable": None,
}


class TestPickerMatchesRuntime:
    """OKA-C5: credential_mode/payer are what the runtime does (the same
    custom-model match, row choice and non-empty test as get_api_key);
    is_available keeps its existing meaning. An ApiKey row with an empty key
    is no stored credential at runtime (stored_credential_lookup), so the
    model runs on credits when the gateway admits it, and is refused
    otherwise; the picker must say so, not "byok"."""

    def test_empty_key_row_is_labelled_as_the_runtime_routes_it(
        self, mr, auth_client, organization, workspace, user
    ):
        _store_byok(organization, workspace, user, "anthropic", key="")
        port = eligible_port(mr, organization, {ANTHROPIC_CHAT_MODEL})
        with mr.managed_routing_port(port):
            option = _option(auth_client, ANTHROPIC_CHAT_MODEL)
            listed = _listed(auth_client, ANTHROPIC_CHAT_MODEL)
            runtime = _runtime_mode(
                mr, organization, workspace, ANTHROPIC_CHAT_MODEL, "anthropic"
            )
        assert runtime == "managed"
        for row in (option, listed):
            assert row["credential_mode"] == runtime
            assert row["payer"] == PAYER_OF_MODE[runtime]
            assert row["is_available"] is True
        assert port.authorize_calls == []

    def test_empty_key_row_without_credits_is_never_labelled_byok(
        self, mr, auth_client, organization, workspace, user
    ):
        _store_byok(organization, workspace, user, "anthropic", key="")
        option = _option(auth_client, ANTHROPIC_CHAT_MODEL)
        runtime = _runtime_mode(
            mr, organization, workspace, ANTHROPIC_CHAT_MODEL, "anthropic"
        )
        assert runtime == "unavailable"
        assert option["credential_mode"] == runtime
        assert option["payer"] is None
        # is_available is today's provider-row membership, unchanged.
        assert option["is_available"] is True

    def test_stored_key_label_matches_the_runtime(
        self, mr, auth_client, organization, workspace, user
    ):
        _store_byok(organization, workspace, user, "openai")
        port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL})
        with mr.managed_routing_port(port):
            option = _option(auth_client, OPENAI_CHAT_MODEL)
            listed = _listed(auth_client, OPENAI_CHAT_MODEL)
            runtime = _runtime_mode(
                mr, organization, workspace, OPENAI_CHAT_MODEL, "openai"
            )
        assert runtime == "byok"
        assert option["credential_mode"] == listed["credential_mode"] == runtime
