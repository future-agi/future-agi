"""R4 noninterference and manual-model scope; synthetic values, mocked transport."""

import inspect
import json
import textwrap
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.services import provider_model_safety as safety
from agentcc.tests import test_provider_credential_api as api_fixtures
from agentcc.tests.provider_discovery_test_support import (
    discovery_enabled as discovery_enabled,
)
from agentcc.views import gateway
from agentcc.views import provider_credential as module
from integrations.services.credentials import CredentialManager
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

secondary_org_client = api_fixtures.secondary_org_client
secondary_org_context = api_fixtures.secondary_org_context

# Deliberately no shared suffixes: comparison with one key must not accidentally
# remove the other's candidates through an overlapping synthetic marker.
SAVED_A = "alphaunit-7319-cedar"
SAVED_B = "bravotest-4826-maple"
OWN = "callerunit-9537-willow"
MANUAL = [
    "ft:gpt-3.5-turbo-0125:personal::8sNGh8Xd",
    "togethercomputer/RedPajama-INCITE-7B-Chat",
    "NousResearch/Nous-Hermes-2-Mixtral-8x7B-DPO",
    "stabilityai/stable-diffusion-xl-base-1.0",
    "cognitivecomputations/dolphin-mixtral-8x22b",
    "sentence-transformers/all-MiniLM-L6-v2",
    "models/learnlm-1.5-pro-experimental",
    "risk-v1",
    "desk-assistant",
    "task-model-v2",
    "disk-sk-tool",
    "asia-model",
    "Basic-model-v1",
    "Bearer-free",
]


def candidates():
    raw = SAVED_A[:8]
    hex_window = raw.encode().hex()
    return [raw, raw[:4] + "/" + raw[4:], hex_window[:10] + "/" + hex_window[10:]]


def fake_row():
    return SimpleNamespace(
        encrypted_credentials=b"synthetic-cipher",
        base_url="",
        api_format="openai",
        extra_config={},
        models_list=MANUAL,
    )


def invoke_own_key(saved, *, saved_only=False):
    request = SimpleNamespace(
        organization=SimpleNamespace(id="synthetic-org"),
        data={
            "provider_name": "openai",
            "api_key": OWN,
            "base_url": "https://caller-controlled.invalid",
            "api_format": "openai",
            "api_path_prefix": "/custom",
            **({"saved_models_only": True} if saved_only else {}),
        },
    )
    with (
        patch.object(module, "AgentccProviderCredential") as model,
        patch.object(
            module.CredentialManager, "decrypt", return_value=saved
        ) as decrypt,
        patch.object(
            module.AgentccProviderCredentialViewSet,
            "_fetch_models_from_provider",
            return_value=candidates() + [OWN, "gpt-4o"],
        ) as upstream,
    ):
        model.no_workspace_objects.filter.return_value.first.return_value = fake_row()
        response = module.AgentccProviderCredentialViewSet().fetch_models(request)
    return response, model.no_workspace_objects.filter, decrypt, upstream


def response_bytes(response):
    # Direct-view microtests compare the serialized entire body. The integration
    # regression below compares actual DRF HTTP response.content bytes.
    return json.dumps(response.data, separators=(",", ":")).encode()


@pytest.mark.parametrize(
    "saved", [{"api_key": SAVED_A}, {"api_key": SAVED_B}, {"api_key": 12345}, None]
)
def test_own_key_never_loads_saved_context(saved):
    response, query, decrypt, upstream = invoke_own_key(saved)
    assert response.status_code == 200
    assert response.data["result"] == {
        "models": sorted(candidates() + ["gpt-4o"]),
        "dropped_model_count": 1,
    }
    query.assert_not_called()
    decrypt.assert_not_called()
    upstream.assert_called_once_with(
        "openai", "https://caller-controlled.invalid", OWN, "openai", "/custom"
    )


def test_own_key_cannot_request_saved_projection():
    response, query, decrypt, upstream = invoke_own_key(
        {"api_key": SAVED_A}, saved_only=True
    )
    assert response.status_code == 400
    query.assert_not_called()
    decrypt.assert_not_called()
    upstream.assert_not_called()


def test_own_key_guard_removal_mutation_is_detected(monkeypatch):
    before = [
        response_bytes(invoke_own_key({"api_key": key})[0])
        for key in (SAVED_A, SAVED_B)
    ]
    assert before[0] == before[1]
    source = textwrap.dedent(
        inspect.getsource(module.AgentccProviderCredentialViewSet.fetch_models)
    )
    changes = [
        ("if provider_name and not request_key:", "if provider_name:"),
        (
            "(api_key, saved_secrets) if uses_saved_key else (api_key,)",
            "(api_key, saved_secrets)",
        ),
    ]
    for old, new in changes:
        assert source.count(old) == 1
        source = source.replace(old, new)
    namespace = dict(vars(module))
    exec(compile(source, "<r4-saved-context-mutant>", "exec"), namespace)
    monkeypatch.setattr(
        module.AgentccProviderCredentialViewSet,
        "fetch_models",
        namespace["fetch_models"],
    )
    after = [
        response_bytes(invoke_own_key({"api_key": key})[0])
        for key in (SAVED_A, SAVED_B)
    ]
    assert after[0] != after[1], (
        "Removing both isolation guards must break the differential"
    )


@pytest.mark.integration
@pytest.mark.api
def test_member_own_key_http_bytes_are_independent_of_saved_credential(
    user, secondary_org_client, secondary_org_context
):
    org, workspace = secondary_org_context
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=org
    ).update(role=OrganizationRoles.MEMBER, level=Level.MEMBER)
    WorkspaceMembership.objects.filter(user=user, workspace=workspace).update(
        role=OrganizationRoles.WORKSPACE_MEMBER, level=Level.WORKSPACE_MEMBER
    )
    row = AgentccProviderCredential(
        organization=org,
        provider_name="openai",
        api_format="openai",
        encrypted_credentials=CredentialManager.encrypt({"api_key": SAVED_A}),
        models_list=MANUAL,
        extra_config={},
    )
    row.disable_auto_workspace_assignment = True
    row.save(force_insert=True)
    # Exercise real RBAC; a member must be denied an admin mutation.
    denied = secondary_org_client.patch(
        f"/agentcc/provider-credentials/{row.pk}/",
        {"display_name": "not allowed"},
        format="json",
    )
    assert denied.status_code == 403
    bodies = []
    for key in (SAVED_A, SAVED_B):
        row.encrypted_credentials = CredentialManager.encrypt({"api_key": key})
        row.save(update_fields=["encrypted_credentials"])
        with (
            patch.object(
                module.CredentialManager, "decrypt", wraps=CredentialManager.decrypt
            ) as decrypt,
            patch.object(
                module.AgentccProviderCredentialViewSet,
                "_fetch_models_from_provider",
                return_value=candidates() + [OWN, "gpt-4o"],
            ) as upstream,
        ):
            response = secondary_org_client.post(
                "/agentcc/provider-credentials/fetch_models/",
                {
                    "provider_name": "openai",
                    "api_key": OWN,
                    "base_url": "https://caller-controlled.invalid",
                    "api_format": "openai",
                },
                format="json",
            )
        assert response.status_code == 200
        assert response.json()["result"] == {
            "models": sorted(candidates() + ["gpt-4o"]),
            "dropped_model_count": 1,
        }
        decrypt.assert_not_called()
        upstream.assert_called_once_with(
            "openai", "https://caller-controlled.invalid", OWN, "openai", None
        )
        bodies.append(response.content)
    assert bodies[0] == bodies[1]


@pytest.mark.parametrize("value", MANUAL)
def test_manual_ids_use_credential_exclusion_not_discovery_grammar(value):
    assert safety.filter_provider_model_ids(
        [value], (OWN,), enforce_discovery_grammar=False
    ) == {
        "models": [value],
        "dropped_model_count": 0,
    }
    assert safety.filter_provider_model_ids(
        [value], (value,), enforce_discovery_grammar=False
    ) == {
        "models": [],
        "dropped_model_count": 1,
    }


@pytest.mark.parametrize("bad", [12345, None, b"synthetic", {"api_key": ["synthetic"]}])
def test_manual_scope_still_refuses_invalid_context(bad):
    with pytest.raises(safety.InvalidProviderCredentialContext):
        safety.filter_provider_model_ids(MANUAL, bad, enforce_discovery_grammar=False)


@pytest.mark.parametrize("saved_only", [False, True])
def test_saved_key_listing_and_hydration_preserve_manual_ids(saved_only):
    row = fake_row()
    row.models_list = MANUAL + [SAVED_A]
    with (
        patch.object(module, "AgentccProviderCredential") as model,
        patch.object(
            module.CredentialManager, "decrypt", return_value={"api_key": SAVED_A}
        ),
        patch.object(
            module.AgentccProviderCredentialViewSet,
            "_fetch_models_from_provider",
            return_value=MANUAL + [SAVED_A],
        ) as upstream,
    ):
        model.no_workspace_objects.filter.return_value.first.return_value = row
        response = module.AgentccProviderCredentialViewSet().fetch_models(
            SimpleNamespace(
                organization=SimpleNamespace(id="synthetic-org"),
                data={"provider_name": "openai", "saved_models_only": saved_only},
            )
        )
    assert response.status_code == 200
    assert sorted(response.data["result"]["models"]) == sorted(MANUAL)
    assert response.data["result"]["dropped_model_count"] == 1
    if saved_only:
        upstream.assert_not_called()
    else:
        upstream.assert_called_once_with("openai", "", SAVED_A, "openai", None)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize(
    "route", ["create", "patch", "put", "gateway-create", "gateway-edit"]
)
def test_manual_and_saved_ids_survive_write_read_and_push(
    route, secondary_org_client, secondary_org_context
):
    org, _ = secondary_org_context
    row = None
    if route not in ("create", "gateway-create"):
        row = AgentccProviderCredential(
            organization=org,
            provider_name="openai",
            api_format="openai",
            encrypted_credentials=CredentialManager.encrypt({"api_key": OWN}),
            models_list=MANUAL,
            extra_config={},
        )
        row.disable_auto_workspace_assignment = True
        row.save(force_insert=True)
    observed = []

    def push(_org):
        observed.append(
            AgentccProviderCredential.no_workspace_objects.get(
                organization=org, provider_name="openai", deleted=False
            ).models_list
        )
        return True

    with (
        patch.object(
            module.AgentccProviderCredentialViewSet,
            "_push_config_to_gateway",
            side_effect=push,
        ),
        patch.object(
            gateway.AgentccGatewayViewSet, "_push_current_config", side_effect=push
        ),
    ):
        if route.startswith("gateway"):
            response = secondary_org_client.post(
                "/agentcc/gateways/default/update-provider/",
                {
                    "name": "openai",
                    "config": {"api_key": OWN, "models": MANUAL + [OWN]},
                },
                format="json",
            )
        elif route == "create":
            response = secondary_org_client.post(
                "/agentcc/provider-credentials/",
                {
                    "provider_name": "openai",
                    "credentials": {"api_key": OWN},
                    "models_list": MANUAL + [OWN],
                },
                format="json",
            )
        else:
            response = getattr(secondary_org_client, route)(
                f"/agentcc/provider-credentials/{row.pk}/",
                {"models_list": MANUAL + [OWN]},
                format="json",
            )
    assert response.status_code == (201 if route == "create" else 200)
    assert response.json()["result"]["dropped_model_count"] == 1
    row = AgentccProviderCredential.no_workspace_objects.get(
        organization=org, provider_name="openai", deleted=False
    )
    assert row.models_list == MANUAL
    assert observed == [MANUAL]
    for url in (
        "/agentcc/provider-credentials/",
        f"/agentcc/provider-credentials/{row.pk}/",
    ):
        read = secondary_org_client.get(url)
        assert read.status_code == 200
        result = read.json()["result"]
        assert (result[0] if isinstance(result, list) else result)[
            "models_list"
        ] == MANUAL
    hydrated = secondary_org_client.post(
        "/agentcc/provider-credentials/fetch_models/",
        {"provider_name": "openai", "saved_models_only": True},
        format="json",
    )
    assert hydrated.status_code == 200
    assert hydrated.json()["result"]["models"] == MANUAL
