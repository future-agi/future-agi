"""R3 counterexamples and exhaustive boundaries of the admission contract.

All keys are synthetic; every provider transport is replaced before invocation.
The HTTP/DB tests use the same additive body the real dialog hook sends.
"""

import base64
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.services import provider_model_safety as safety
from agentcc.tests import test_provider_credential_api as api_fixtures
from agentcc.tests.provider_discovery_test_support import (
    discovery_enabled as discovery_enabled,
)
from agentcc.views import provider_credential as module
from integrations.services.credentials import CredentialManager

secondary_org_client = api_fixtures.secondary_org_client
secondary_org_context = api_fixtures.secondary_org_context

SAVED = "original-fixture-only"
REPLACEMENT = "replacement-fixture-only"
AUX = "auxiliary-fixture-only"
EMBEDDED = "cataloguesk-test-not-a-real-provider-key"
SPLIT = "aB3dE6gH9jK2mN5p/Q8sT1vW4yZ7cF0iL"
NORMAL = [
    "gpt-4o",
    "claude-sonnet-4-5",
    "meta-llama/Llama-3.1-8B-Instruct",
    "models/gemini-2.5-pro",
    "org/model:tag",
]


def echoes():
    return [
        SAVED,
        SAVED[:11],
        base64.b64encode(SAVED.encode()).decode(),
        SAVED.encode().hex(),
        "original/fixture/only",
        AUX,
        REPLACEMENT,
        EMBEDDED,
        SPLIT,
    ]


@pytest.mark.parametrize("value", NORMAL)
def test_normal_ids_remain_discoverable(value):
    assert safety.filter_provider_model_ids([value], (SAVED, REPLACEMENT)) == {
        "models": [value],
        "dropped_model_count": 0,
    }


@pytest.mark.parametrize("value", echoes())
def test_counterexamples_are_omitted(value):
    assert safety.filter_provider_model_ids(
        [value, "gpt-4o"], (SAVED, REPLACEMENT, AUX)
    ) == {
        "models": ["gpt-4o"],
        "dropped_model_count": 1,
    }


@pytest.mark.parametrize(
    "bad", [2718281828459045, 1.5, True, None, b"fixture", b"\xff"]
)
def test_unsupported_secret_leaf_fails_closed_even_for_empty_list(bad):
    with pytest.raises(safety.InvalidProviderCredentialContext) as caught:
        safety.filter_provider_model_ids([], {"api_key": SAVED, "nested": [bad]})
    assert (
        str(caught.value)
        == "Provider credential context is invalid. Rotate the credential."
    )


@pytest.mark.parametrize(
    "bad",
    [
        2718281828459045,
        b"\xff",
        None,
        {"api_key": 2718281828459045},
        {"api_key": ["fixture"]},
        {"api_key": {"token": "fixture"}},
        {"api_key": None},
    ],
)
def test_numeric_or_undecodable_saved_context_never_reaches_transport(bad):
    row = SimpleNamespace(
        encrypted_credentials=b"synthetic-cipher",
        extra_config={},
        models_list=[],
        base_url="",
        api_format="openai",
        save=MagicMock(),
    )
    body = {"provider_name": "openai"}
    request = SimpleNamespace(
        data=body, organization=SimpleNamespace(id="synthetic-org")
    )
    logger = MagicMock()
    with (
        patch.object(module, "AgentccProviderCredential") as model,
        patch.object(module.CredentialManager, "decrypt", return_value=bad),
        patch.object(module.http_requests, "get") as transport,
        patch.object(module, "logger", logger),
    ):
        model.no_workspace_objects.filter.return_value.first.return_value = row
        transport.return_value.status_code = 200
        transport.return_value.json.return_value = {
            "data": [{"id": "2718281828459045"}, {"id": "gpt-4o"}]
        }
        response = module.AgentccProviderCredentialViewSet().fetch_models(request)
    assert response.status_code == 400
    assert response.data["code"] == "provider_credential_context_invalid"
    assert "2718281828459045" not in json.dumps(response.data) + repr(logger.mock_calls)
    transport.assert_not_called()
    assert not logger.mock_calls


@pytest.mark.parametrize("bad", [2718281828459045, None, False, ["fixture"]])
def test_raw_nonstring_key_is_not_coerced(bad):
    with patch.object(module.http_requests, "get") as transport:
        response = module.AgentccProviderCredentialViewSet().fetch_models(
            SimpleNamespace(data={"api_key": bad, "api_format": "openai"})
        )
    assert response.status_code == 400
    assert response.data["code"] == "provider_credential_context_invalid"
    transport.assert_not_called()


@pytest.mark.parametrize("length", range(1, 13))
def test_alphanumeric_atom_boundary(length):
    expected = length <= 10
    result = safety.filter_provider_model_ids(["a" * length], ())
    assert bool(result["models"]) is expected


@pytest.mark.parametrize("count", range(1, 8))
def test_route_segment_boundary(count):
    value = "/".join(["model"] * count)
    assert bool(safety.filter_provider_model_ids([value], ())["models"]) is (count <= 6)


@pytest.mark.parametrize("char", [chr(i) for i in range(128)])
def test_ascii_character_contract(char):
    value = "mod" + char + "el"
    allowed = char.isascii() and (char.isalnum() or char in ".:_@-/")
    assert bool(safety.filter_provider_model_ids([value], ())["models"]) is allowed


@pytest.mark.parametrize(
    "value", ["/model", "model/", "a//b", "a--b", "a..b", "m\n", "m ", "mé"]
)
def test_empty_atoms_controls_and_unicode_are_not_admitted(value):
    assert safety.filter_provider_model_ids([value], ()) == {
        "models": [],
        "dropped_model_count": 1,
    }


def test_secret_exclusion_has_no_public_catalog_bypass():
    for value in NORMAL + ["pplx-decider-v1.1-27b"]:
        assert safety.filter_provider_model_ids([value], (value,))["models"] == []


def test_guard_removal_controls_are_sensitized(monkeypatch):
    # This is an executable positive mutation control, not just a runlist claim.
    with monkeypatch.context() as mutant:
        mutant.setattr(safety, "_matches_grammar", lambda value: isinstance(value, str))
        assert safety.filter_provider_model_ids([EMBEDDED, SPLIT], (SAVED,))[
            "models"
        ] == [EMBEDDED, SPLIT]
    with monkeypatch.context() as mutant:
        mutant.setattr(safety, "_secret_windows", lambda values: (set(), set()))
        assert safety.filter_provider_model_ids([SAVED, SAVED[:11]], (SAVED,))[
            "models"
        ] == [SAVED, SAVED[:11]]
    with monkeypatch.context() as mutant:
        mutant.setattr(safety, "credential_context_strings", lambda values, **_: ())
        assert safety.filter_provider_model_ids(
            ["gpt-4o"], {"api_key": 2718281828459045}
        )["models"] == ["gpt-4o"]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("saved_only", [False, True])
def test_real_http_additive_saved_context_and_projection(
    saved_only, secondary_org_client, secondary_org_context
):
    org, _ = secondary_org_context
    row = AgentccProviderCredential(
        organization=org,
        provider_name="openai",
        base_url="",
        api_format="openai",
        encrypted_credentials=CredentialManager.encrypt(
            {"api_key": SAVED, "nested": {"token": AUX}}
        ),
        models_list=echoes() + NORMAL,
        extra_config={},
    )
    row.disable_auto_workspace_assignment = True
    row.save(force_insert=True)
    body = {"provider_name": "openai"}
    if saved_only:
        body["saved_models_only"] = True
    else:
        body.update(
            api_key=REPLACEMENT, base_url="", api_format="openai", api_path_prefix="/v1"
        )
    upstream = MagicMock(status_code=200)
    upstream.json.return_value = {
        "data": [{"id": value} for value in echoes() + NORMAL]
    }
    with patch.object(module.http_requests, "get", return_value=upstream) as transport:
        response = secondary_org_client.post(
            "/agentcc/provider-credentials/fetch_models/", body, format="json"
        )
    assert response.status_code == 200
    result = response.json()["result"]
    # Saved projection preserves unrelated grammar outliers; own-key listing
    # uses only REPLACEMENT (these legacy fixtures share a synthetic suffix).
    allowed = NORMAL + ([EMBEDDED, SPLIT] if saved_only else [SAVED[:11]])
    assert sorted(result["models"]) == sorted(allowed)
    assert result["dropped_model_count"] == len(echoes()) - (2 if saved_only else 1)
    for value in echoes():
        if value not in allowed:
            assert value not in response.content.decode()
    if saved_only:
        transport.assert_not_called()
    else:
        assert (
            transport.call_args.kwargs["headers"]["Authorization"]
            == "Bearer " + REPLACEMENT
        )
    row.refresh_from_db()
    assert row.models_list == echoes() + NORMAL
    assert (
        CredentialManager.decrypt(bytes(row.encrypted_credentials))["api_key"] == SAVED
    )


@pytest.mark.integration
@pytest.mark.api
def test_saved_projection_lookup_is_organization_scoped(
    secondary_org_client, secondary_org_context
):
    org, _ = secondary_org_context
    with patch.object(
        module.AgentccProviderCredential.no_workspace_objects, "filter"
    ) as query:
        query.return_value.first.return_value = None
        response = secondary_org_client.post(
            "/agentcc/provider-credentials/fetch_models/",
            {"provider_name": "openai", "saved_models_only": True},
            format="json",
        )
    assert query.call_args.kwargs["organization"].pk == org.pk
    assert response.status_code == 200
    assert response.json()["result"]["models"] == []


def _numeric_key_row(org):
    row = AgentccProviderCredential(
        organization=org,
        provider_name="openai",
        api_format="openai",
        encrypted_credentials=CredentialManager.encrypt({"api_key": 2718281828459045}),
        models_list=["gpt-4o"],
        extra_config={},
    )
    row.disable_auto_workspace_assignment = True
    row.save(force_insert=True)
    return row


@pytest.mark.integration
@pytest.mark.api
def test_numeric_saved_key_refuses_model_writes_without_push(
    secondary_org_client, secondary_org_context
):
    from agentcc.views import gateway

    org, _ = secondary_org_context
    row = _numeric_key_row(org)
    original = bytes(row.encrypted_credentials)
    with (
        patch.object(
            module.AgentccProviderCredentialViewSet, "_push_config_to_gateway"
        ) as push,
        patch.object(
            gateway.AgentccGatewayViewSet, "_push_current_config"
        ) as gateway_push,
    ):
        response = secondary_org_client.patch(
            f"/agentcc/provider-credentials/{row.pk}/",
            {"models_list": ["2718281828459045", "gpt-4o"]},
            format="json",
        )
    assert response.status_code == 400
    assert response.json()["code"] == "provider_credential_context_invalid"
    assert "2718281828459045" not in response.content.decode()
    push.assert_not_called()
    gateway_push.assert_not_called()
    row.refresh_from_db()
    assert row.models_list == ["gpt-4o"]
    assert bytes(row.encrypted_credentials) == original


# The console's provider dialog replaces a numeric saved key with a complete
# new key ("not pushed: ... replace the credential"). The old key never comes
# back as a model ID: its decimal form is screened out of the saved list.
@pytest.mark.integration
@pytest.mark.api
def test_dialog_replaces_a_numeric_saved_key_and_screens_it_from_models(
    secondary_org_client, secondary_org_context
):
    from agentcc.views import gateway

    org, _ = secondary_org_context
    row = _numeric_key_row(org)
    with patch.object(
        gateway.AgentccGatewayViewSet, "_push_current_config", return_value=True
    ) as gateway_push:
        response = secondary_org_client.post(
            "/agentcc/gateways/default/update-provider/",
            {
                "name": "openai",
                "config": {
                    "api_key": REPLACEMENT,
                    "models": ["2718281828459045", "gpt-4o"],
                },
            },
            format="json",
        )
    assert response.status_code == 200, response.json()
    assert "2718281828459045" not in response.content.decode()
    assert REPLACEMENT not in response.content.decode()
    gateway_push.assert_called_once()
    row.refresh_from_db()
    assert row.models_list == ["gpt-4o"]
    assert CredentialManager.decrypt(bytes(row.encrypted_credentials)) == {
        "api_key": REPLACEMENT
    }
