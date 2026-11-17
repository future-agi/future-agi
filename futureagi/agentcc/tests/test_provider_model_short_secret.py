"""A short saved credential must not erase saved model IDs.

Providers saved before model-ID filtering existed can hold a short
placeholder key ("x", "EMPTY", "none": the values local servers take). A
short value, or its short hex or base64 form, matched any model ID that
merely contained it, so such a provider lost saved models in the console
reads and in the config pushed to the gateway, and saved Auto routing
candidates among them stopped routing. A short value now removes only a
model ID equal to it. Long secrets keep their window rules, and every read
says how many saved IDs it left out.

Rows can also hold a null, boolean or number beside their key. Production
pushed those providers (the push skips null and empty values); the model-ID
projection refused them, so the push dropped the whole provider while the
providers read still called it pushed. On the read and push path a null leaf
is now absent and a boolean or number leaf is not a marker. An api_key that
is present and not text is still refused, and the reads say so: pushed
false, a plain reason, and a refused-provider count. Saves stay strict.
"""

import base64
from unittest.mock import MagicMock, patch

import pytest

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.services import config_push
from agentcc.services.provider_model_safety import filter_provider_model_ids
from agentcc.tests import test_provider_credential_api as api_fixtures
from integrations.services.credentials import CredentialManager

secondary_org_client = api_fixtures.secondary_org_client
secondary_org_context = api_fixtures.secondary_org_context

SHORT_KEYS = ["x", "EMPTY", "none"]
# Every saved ID here contains one of the short keys or one of their short
# encodings: "x" (raw), "78" (hex of "x"), "ea" (base64 "eA" of "x"),
# "empty" and "none" (raw, case-folded).
SAVED_MODELS = [
    "mixtral-8x7b",
    "o3-deep-research",
    "qwen2-78b-instruct",
    "llama3",
    "gpt-4o-mini",
    "empty-ctx-model",
    "nonesuch-7b",
]
CANDIDATES = ["mixtral-8x7b", "o3-deep-research", "qwen2-78b-instruct"]

# A long, visibly synthetic secret: it keeps the window rules.
LONG_SECRET = "synthetic-local-secret-0123456789"


def _provider(org, api_key, models):
    row = AgentccProviderCredential(
        organization=org,
        provider_name="openai",
        display_name="Local server",
        api_format="openai",
        base_url="https://llm.example.com",
        encrypted_credentials=CredentialManager.encrypt({"api_key": api_key}),
        models_list=list(models),
        extra_config={},
    )
    # Saved before workspaces were required: no workspace.
    row.disable_auto_workspace_assignment = True
    row.save(force_insert=True)
    return row


def _gateway_client():
    client = MagicMock()
    client.health_check.return_value = {"status": "ok"}
    client.get_config.return_value = {}
    client.provider_health.return_value = {"providers": {}}
    return client


# ---- the filter itself


@pytest.mark.parametrize("key", SHORT_KEYS)
def test_a_short_key_keeps_every_model_id_that_merely_contains_it(key):
    assert filter_provider_model_ids(
        SAVED_MODELS, ({"api_key": key},), enforce_discovery_grammar=False
    ) == {"models": SAVED_MODELS, "dropped_model_count": 0}


def test_a_short_key_keeps_ids_with_its_hex_and_base64_forms():
    # "x" is 78 in hex and eA in base64.
    assert filter_provider_model_ids(
        ["qwen2-78b-instruct", "o3-deep-research", "llama-78-ea"],
        ({"api_key": "x"},),
        enforce_discovery_grammar=False,
    ) == {
        "models": ["qwen2-78b-instruct", "o3-deep-research", "llama-78-ea"],
        "dropped_model_count": 0,
    }


@pytest.mark.parametrize(
    "key,saved", [("x", "X"), ("EMPTY", "empty"), ("none", "None")]
)
def test_a_short_key_still_removes_a_model_id_equal_to_it(key, saved):
    assert filter_provider_model_ids(
        [saved, "gpt-4o-mini"], ({"api_key": key},), enforce_discovery_grammar=False
    ) == {"models": ["gpt-4o-mini"], "dropped_model_count": 1}


def test_a_long_secret_inside_an_id_is_still_removed_raw_hex_and_base64():
    raw = "m-" + LONG_SECRET[3:11] + "-x"
    hexed = "m-" + LONG_SECRET.encode().hex()[4:20]
    b64 = "m-" + base64.b64encode(LONG_SECRET.encode()).decode()[2:14]
    whole = "model-" + LONG_SECRET
    result = filter_provider_model_ids(
        [raw, hexed, b64, whole, "gpt-4o-mini"],
        ({"api_key": LONG_SECRET},),
        enforce_discovery_grammar=False,
    )
    assert result == {"models": ["gpt-4o-mini"], "dropped_model_count": 4}


# ---- the reads and the push, on rows saved before the filter existed


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("key", SHORT_KEYS)
@patch("agentcc.views.gateway.get_gateway_client")
def test_config_read_keeps_every_saved_model_and_counts_none_left_out(
    mock_get_client, key, secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    _provider(org, key, SAVED_MODELS)
    AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=1,
        is_active=True,
        routing={"auto": {"candidates": CANDIDATES}},
    )
    mock_get_client.return_value = _gateway_client()

    response = secondary_org_client.get("/agentcc/gateways/default/config/")

    assert response.status_code == 200, response.json()
    result = response.json()["result"]
    assert result["providers"]["openai"]["models"] == SAVED_MODELS
    assert result["providers"]["openai"]["dropped_model_count"] == 0
    assert result["routing"]["auto"]["candidates"] == CANDIDATES


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("key", SHORT_KEYS)
@patch("agentcc.views.gateway.get_gateway_client")
def test_providers_read_keeps_every_saved_model_and_counts_none_left_out(
    mock_get_client, key, secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    _provider(org, key, SAVED_MODELS)
    mock_get_client.return_value = _gateway_client()

    response = secondary_org_client.get("/agentcc/gateways/default/providers/")

    assert response.status_code == 200, response.json()
    (provider,) = response.json()["result"]["providers"]
    assert provider["models"] == SAVED_MODELS
    assert provider["dropped_model_count"] == 0


@pytest.mark.integration
@pytest.mark.parametrize("key", SHORT_KEYS)
def test_assemble_providers_keeps_every_saved_model(key, secondary_org_context):
    org, _ = secondary_org_context
    _provider(org, key, SAVED_MODELS)

    providers = config_push._assemble_providers(str(org.id))

    assert providers["openai"]["models"] == SAVED_MODELS


@pytest.mark.integration
@pytest.mark.parametrize("key", SHORT_KEYS)
def test_pushed_config_keeps_every_saved_model_and_the_auto_candidates(
    key, secondary_org_context
):
    org, _ = secondary_org_context
    _provider(org, key, SAVED_MODELS)
    config = AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=1,
        is_active=True,
        routing={"auto": {"preference": "cost", "candidates": CANDIDATES}},
    )
    client = MagicMock()
    with patch.object(config_push, "get_gateway_client", return_value=client):
        assert config_push.push_org_config(str(org.id), config) is True

    (_, payload), _ = client.set_org_config.call_args
    assert payload["providers"]["openai"]["models"] == SAVED_MODELS
    assert payload["routing"]["auto"]["candidates"] == CANDIDATES
    # Every saved candidate is still one of the provider's models.
    assert set(CANDIDATES) <= set(payload["providers"]["openai"]["models"])


@pytest.mark.integration
@pytest.mark.api
@patch("agentcc.views.gateway.get_gateway_client")
def test_reads_count_a_saved_id_left_out_for_a_long_secret_without_naming_it(
    mock_get_client, secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    leaked = "model-" + LONG_SECRET
    _provider(org, LONG_SECRET, ["gpt-4o-mini", leaked])
    mock_get_client.return_value = _gateway_client()

    providers_read = secondary_org_client.get("/agentcc/gateways/default/providers/")
    config_read = secondary_org_client.get("/agentcc/gateways/default/config/")

    assert providers_read.status_code == 200, providers_read.json()
    assert config_read.status_code == 200, config_read.json()
    (provider,) = providers_read.json()["result"]["providers"]
    assert provider["models"] == ["gpt-4o-mini"]
    assert provider["dropped_model_count"] == 1
    configured = config_read.json()["result"]["providers"]["openai"]
    assert configured["models"] == ["gpt-4o-mini"]
    assert configured["dropped_model_count"] == 1
    for body in (providers_read.content.decode(), config_read.content.decode()):
        assert LONG_SECRET not in body
        assert leaked not in body
    assert config_push._assemble_providers(str(org.id))["openai"]["models"] == [
        "gpt-4o-mini"
    ]


# ---- legacy leaf kinds beside the key: what the push and the reads keep

# A long, visibly synthetic key: no model ID below contains a window of it.
KEY = "sk-test-local-provider-0123456789abcdef"

# id, saved credentials, pushed by production 15b3e8a6 and by the fix,
# extra fields the pushed provider carries.
LEAF_KINDS = [
    ("missing", {"api_key": KEY}, True, {}),
    ("null", {"api_key": KEY, "api_version": None}, True, {}),
    ("empty", {"api_key": KEY, "aws_session_token": ""}, True, {}),
    ("bool", {"api_key": KEY, "zdr_attested": True}, True, {"zdr_attested": True}),
    ("int", {"api_key": KEY, "weight": 2}, True, {"weight": 2.0}),
    ("float", {"api_key": KEY, "weight": 0.5}, True, {"weight": 0.5}),
    ("null_api_key", {"api_key": None}, True, {}),
    # A key outside the provider contract: production left these out too.
    (
        "nested_map",
        {"api_key": KEY, "extra": {"flag": True, "n": 1, "z": None}},
        False,
        {},
    ),
    ("list", {"api_key": KEY, "scopes": ["fixture", 1]}, False, {}),
]


def _saved(org, credentials, models=("gpt-4o",), provider="openai"):
    row = AgentccProviderCredential(
        organization=org,
        provider_name=provider,
        display_name=provider,
        api_format="openai",
        base_url="https://llm.example.com",
        encrypted_credentials=CredentialManager.encrypt(credentials),
        models_list=list(models),
        extra_config={},
    )
    row.disable_auto_workspace_assignment = True
    row.save(force_insert=True)
    return row


def _push(org):
    config = AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=1, is_active=True
    )
    client = MagicMock()
    with patch.object(config_push, "get_gateway_client", return_value=client):
        assert config_push.push_org_config(str(org.id), config) is True
    (_, payload), _ = client.set_org_config.call_args
    return payload


@pytest.mark.integration
@pytest.mark.parametrize(
    "credentials,pushed,fields",
    [case[1:] for case in LEAF_KINDS],
    ids=[case[0] for case in LEAF_KINDS],
)
def test_push_keeps_a_legacy_leaf_provider_as_production_did(
    credentials, pushed, fields, secondary_org_context
):
    org, _ = secondary_org_context
    _saved(org, credentials)

    payload = _push(org)
    assembled = config_push._assemble_providers(str(org.id))

    assert ("openai" in payload["providers"]) is pushed
    assert ("openai" in assembled) is pushed
    if pushed:
        assert payload["providers"]["openai"]["models"] == ["gpt-4o"]
        for name, value in fields.items():
            assert payload["providers"]["openai"][name] == value


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize(
    "credentials,pushed",
    [case[1:3] for case in LEAF_KINDS],
    ids=[case[0] for case in LEAF_KINDS],
)
@patch("agentcc.views.gateway.get_gateway_client")
def test_reads_list_a_legacy_leaf_provider_and_say_whether_it_routes(
    mock_get_client, credentials, pushed, secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    _saved(org, credentials)
    mock_get_client.return_value = _gateway_client()

    providers_read = secondary_org_client.get("/agentcc/gateways/default/providers/")
    config_read = secondary_org_client.get("/agentcc/gateways/default/config/")

    assert providers_read.status_code == 200, providers_read.json()
    assert config_read.status_code == 200, config_read.json()
    listed = providers_read.json()["result"]
    (provider,) = listed["providers"]
    assert provider["models"] == ["gpt-4o"]
    assert provider["pushed"] is pushed
    assert listed["refused_provider_count"] == (0 if pushed else 1)
    configured = config_read.json()["result"]
    assert configured["providers"]["openai"]["models"] == ["gpt-4o"]
    assert configured["refused_provider_count"] == (0 if pushed else 1)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("bad_key", [2718281828459045, True, ["fixture"]])
@patch("agentcc.views.gateway.get_gateway_client")
def test_a_refused_provider_is_shown_as_not_pushed_and_others_still_route(
    mock_get_client, bad_key, secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    _saved(org, {"api_key": bad_key}, provider="openai")
    _saved(org, {"api_key": KEY}, models=("claude-fixture",), provider="anthropic")
    mock_get_client.return_value = _gateway_client()

    payload = _push(org)
    providers_read = secondary_org_client.get("/agentcc/gateways/default/providers/")
    config_read = secondary_org_client.get("/agentcc/gateways/default/config/")

    # The fixed provider routes; the refused one is left out of the push.
    assert set(payload["providers"]) == {"anthropic"}
    assert payload["providers"]["anthropic"]["models"] == ["claude-fixture"]
    listed = providers_read.json()["result"]
    by_id = {item["id"]: item for item in listed["providers"]}
    assert by_id["anthropic"]["pushed"] is True
    refused = by_id["openai"]
    assert refused["pushed"] is False
    assert refused["status"] == "unhealthy"
    assert refused["status_reason"] == (
        "not pushed: the saved credential holds a value that is not text; "
        "replace the credential"
    )
    assert refused["models"] == []
    assert listed["refused_provider_count"] == 1
    assert config_read.json()["result"]["refused_provider_count"] == 1
    for body in (providers_read.content.decode(), config_read.content.decode()):
        assert "2718281828459045" not in body
        assert KEY not in body


@pytest.mark.integration
@pytest.mark.api
def test_a_save_with_a_number_beside_the_key_is_still_refused(
    secondary_org_context, secondary_org_client
):
    from agentcc.views import gateway, provider_credential

    org, _ = secondary_org_context
    row = _saved(org, {"api_key": KEY, "weight": 2})
    original = bytes(row.encrypted_credentials)
    with (
        patch.object(
            provider_credential.AgentccProviderCredentialViewSet,
            "_push_config_to_gateway",
        ) as push,
        patch.object(gateway.AgentccGatewayViewSet, "_push_current_config") as again,
    ):
        response = secondary_org_client.patch(
            f"/agentcc/provider-credentials/{row.pk}/",
            {"models_list": ["gpt-4o", "gpt-4o-mini"]},
            format="json",
        )
    assert response.status_code == 400
    assert response.json()["code"] == "provider_credential_context_invalid"
    push.assert_not_called()
    again.assert_not_called()
    row.refresh_from_db()
    assert row.models_list == ["gpt-4o"]
    assert bytes(row.encrypted_credentials) == original
