"""A provider's saved model IDs survive a credential replacement and edits.

The reads and the push hold back a saved model ID they cannot offer: one that
spells the credential, or every ID of a row whose saved credential holds a
value that is not text (those providers read "not pushed: ... replace the
credential"). Replacing the credential and editing the provider must not
delete what is held back:

- Replacing a credential that decrypts but holds a non-text value keeps the
  saved IDs, screened against the new credential and the old credential's
  text values. Only a credential that cannot be decrypted at all is cleared.
- An edit that leaves the model list as it was (absent, or the list the
  console offered, in any order) keeps the held-back IDs stored.
- In the console, the edit dialog can load a non-text credential's saved
  models (the held-back ones counted, never named), and a complete new
  credential saved through update-provider replaces it and keeps the IDs.
- Auto routing candidates naming those models are offered again.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.services import config_push
from agentcc.tests import test_provider_credential_api as api_fixtures
from integrations.services.credentials import CredentialManager

secondary_org_client = api_fixtures.secondary_org_client
secondary_org_context = api_fixtures.secondary_org_context

KEY = "sk-test-local-provider-0123456789abcdef"
NEW_KEY = "sk-test-replacement-provider-fedcba9876543210"
OLD_SECRET = "synthetic-old-auxiliary-secret-0123456789"
MODELS = ["gpt-4o", "gpt-4o-mini"]
BASE_URL = "https://llm.example.com"
# Saved api_key values that are not text. Rotation refuses all four as a
# prior context; the reads hold back every ID of the first, third and fourth.
NON_TEXT_KEYS = [2718281828459045, None, True, ["fixture"]]
NON_TEXT_IDS = ["number", "null", "bool", "list"]


def _saved(org, credentials, models=MODELS):
    row = AgentccProviderCredential(
        organization=org,
        provider_name="openai",
        display_name="openai",
        api_format="openai",
        base_url=BASE_URL,
        encrypted_credentials=(
            credentials
            if isinstance(credentials, bytes)
            else CredentialManager.encrypt(credentials)
        ),
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


@contextmanager
def _no_push():
    from agentcc.views import gateway, provider_credential

    with (
        patch.object(
            provider_credential.AgentccProviderCredentialViewSet,
            "_push_config_to_gateway",
            return_value=True,
        ),
        patch.object(
            gateway.AgentccGatewayViewSet, "_push_current_config", return_value=True
        ),
    ):
        yield


def _rotate(client, row, credentials, replace=True):
    with _no_push():
        return client.post(
            f"/agentcc/provider-credentials/{row.pk}/rotate/",
            {"credentials": credentials, "replace_credentials": replace},
            format="json",
        )


def _patch(client, row, body):
    with _no_push():
        return client.patch(
            f"/agentcc/provider-credentials/{row.pk}/", body, format="json"
        )


def _console_save(client, config):
    with _no_push():
        return client.post(
            "/agentcc/gateways/default/update-provider/",
            {
                "name": "openai",
                "config": {"api_format": "openai", "base_url": BASE_URL, **config},
            },
            format="json",
        )


def _hydrate(client):
    # The edit dialog loads the saved models before it allows a save; a
    # failed load blocks the save.
    return client.post(
        "/agentcc/provider-credentials/fetch_models/",
        {"provider_name": "openai", "saved_models_only": True},
        format="json",
    )


def _console_replace(client, new_key):
    # What the edit dialog sends: the models it loaded (none when every saved
    # ID is held back) and the new key.
    hydration = _hydrate(client)
    assert hydration.status_code == 200, hydration.json()
    loaded = hydration.json()["result"]["models"]
    return _console_save(
        client, {"api_key": new_key, **({"models": loaded} if loaded else {})}
    )


def _config_read(client):
    with patch(
        "agentcc.views.gateway.get_gateway_client", return_value=_gateway_client()
    ):
        response = client.get("/agentcc/gateways/default/config/")
    assert response.status_code == 200, response.json()
    return response.json()["result"]


def _stored(row):
    row.refresh_from_db()
    return row.models_list, CredentialManager.decrypt(bytes(row.encrypted_credentials))


# ---- rotation


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("bad_key", NON_TEXT_KEYS, ids=NON_TEXT_IDS)
def test_replacing_a_non_text_credential_keeps_the_saved_models(
    bad_key, secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    row = _saved(org, {"api_key": bad_key})

    response = _rotate(secondary_org_client, row, {"api_key": NEW_KEY})

    assert response.status_code == 200, response.json()
    result = response.json()["result"]
    assert _stored(row) == (MODELS, {"api_key": NEW_KEY})
    # The answer lists the kept models and says nothing was cleared.
    assert result["models_list"] == MODELS
    assert "model_warning" not in result
    assert "code" not in result
    assert NEW_KEY not in response.content.decode()


@pytest.mark.integration
@pytest.mark.api
def test_a_replacement_drops_only_a_model_id_equal_to_the_new_key(
    secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    row = _saved(org, {"api_key": 2718281828459045}, models=[*MODELS, NEW_KEY])

    response = _rotate(secondary_org_client, row, {"api_key": NEW_KEY})

    assert response.status_code == 200, response.json()
    assert _stored(row)[0] == MODELS
    assert response.json()["result"]["dropped_model_count"] == 1
    assert NEW_KEY not in response.content.decode()


@pytest.mark.integration
@pytest.mark.api
def test_a_replacement_still_screens_the_old_credentials_text_values(
    secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    row = _saved(
        org,
        {"api_key": 2718281828459045, "api_secret": OLD_SECRET},
        models=[*MODELS, OLD_SECRET],
    )

    response = _rotate(secondary_org_client, row, {"api_key": NEW_KEY})

    assert response.status_code == 200, response.json()
    assert _stored(row)[0] == MODELS
    assert OLD_SECRET not in response.content.decode()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("path", ["rotate", "console"])
def test_a_replacement_screens_out_a_model_id_spelling_the_old_numeric_key(
    path, secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    row = _saved(
        org, {"api_key": 2718281828459045}, models=[*MODELS, "2718281828459045"]
    )

    if path == "rotate":
        response = _rotate(secondary_org_client, row, {"api_key": NEW_KEY})
    else:
        response = _console_replace(secondary_org_client, NEW_KEY)

    assert response.status_code == 200, response.json()
    assert _stored(row) == (MODELS, {"api_key": NEW_KEY})
    assert "2718281828459045" not in response.content.decode()


@pytest.mark.integration
@pytest.mark.api
def test_merging_onto_a_non_text_credential_is_still_refused(
    secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    row = _saved(org, {"api_key": 2718281828459045})
    original = bytes(row.encrypted_credentials)

    response = _rotate(secondary_org_client, row, {"api_key": NEW_KEY}, replace=False)

    assert response.status_code == 400
    row.refresh_from_db()
    assert row.models_list == MODELS
    assert bytes(row.encrypted_credentials) == original


@pytest.mark.integration
@pytest.mark.api
def test_a_credential_that_cannot_be_decrypted_is_still_cleared_on_replacement(
    secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    row = _saved(org, b"not-a-ciphertext")

    response = _rotate(secondary_org_client, row, {"api_key": NEW_KEY})

    assert response.status_code == 200, response.json()
    result = response.json()["result"]
    assert _stored(row) == ([], {"api_key": NEW_KEY})
    assert result["code"] == "provider_credential_context_invalid"
    assert "Re-enter" in result["model_warning"]


# ---- edits through the credential API


@pytest.mark.integration
@pytest.mark.api
def test_an_edit_without_models_keeps_held_back_ids_stored(
    secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    row = _saved(org, {"api_key": KEY}, models=[*MODELS, KEY])

    response = _patch(secondary_org_client, row, {"display_name": "Renamed"})

    assert response.status_code == 200, response.json()
    result = response.json()["result"]
    row.refresh_from_db()
    assert row.display_name == "Renamed"
    assert row.models_list == [*MODELS, KEY]
    assert result["models_list"] == MODELS
    assert result["dropped_model_count"] == 1
    assert KEY not in response.content.decode()


@pytest.mark.integration
@pytest.mark.api
def test_an_edit_resending_the_offered_list_keeps_held_back_ids_stored(
    secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    row = _saved(org, {"api_key": KEY}, models=[*MODELS, KEY])

    # The offered list, in another order.
    response = _patch(secondary_org_client, row, {"models_list": MODELS[::-1]})

    assert response.status_code == 200, response.json()
    row.refresh_from_db()
    assert row.models_list == [*MODELS, KEY]


@pytest.mark.integration
@pytest.mark.api
def test_an_edit_that_changes_the_list_saves_the_screened_list(
    secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    row = _saved(org, {"api_key": KEY})

    response = _patch(secondary_org_client, row, {"models_list": ["gpt-4o", KEY]})

    assert response.status_code == 200, response.json()
    row.refresh_from_db()
    assert row.models_list == ["gpt-4o"]
    assert response.json()["result"]["dropped_model_count"] == 1


# ---- edits in the console (update-provider)


@pytest.mark.integration
@pytest.mark.api
def test_a_console_edit_with_the_offered_list_keeps_held_back_ids_stored(
    secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    row = _saved(org, {"api_key": KEY}, models=[*MODELS, KEY])
    hydration = _hydrate(secondary_org_client)
    assert hydration.status_code == 200, hydration.json()
    loaded = hydration.json()["result"]["models"]
    assert sorted(loaded) == sorted(MODELS)

    # The list the dialog loaded, in another order.
    response = _console_save(secondary_org_client, {"models": loaded[::-1]})

    assert response.status_code == 200, response.json()
    assert _stored(row) == ([*MODELS, KEY], {"api_key": KEY})
    # Nothing was removed by this save, so it reports no omission.
    assert "dropped_model_count" not in response.json()["result"]


@pytest.mark.integration
@pytest.mark.api
def test_a_console_edit_with_nothing_offered_keeps_the_saved_ids(
    secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    # Every saved ID is held back, so the console shows none and sends none.
    row = _saved(org, {"api_key": KEY}, models=[KEY])

    response = _console_save(secondary_org_client, {"default_timeout": 30})

    assert response.status_code == 200, response.json()
    row.refresh_from_db()
    assert row.models_list == [KEY]


@pytest.mark.integration
@pytest.mark.api
def test_a_console_edit_that_clears_an_offered_list_still_clears_it(
    secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    row = _saved(org, {"api_key": KEY})

    response = _console_save(secondary_org_client, {"default_timeout": 30})

    assert response.status_code == 200, response.json()
    row.refresh_from_db()
    assert row.models_list == []


@pytest.mark.integration
@pytest.mark.api
def test_a_console_key_change_still_screens_out_an_id_spelling_the_old_key(
    secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    # Held back while KEY is the key; it would be offered once KEY is gone.
    row = _saved(org, {"api_key": KEY}, models=[*MODELS, KEY])

    response = _console_replace(secondary_org_client, NEW_KEY)

    assert response.status_code == 200, response.json()
    assert _stored(row) == (MODELS, {"api_key": NEW_KEY})
    read = _config_read(secondary_org_client)["providers"]["openai"]
    assert read["models"] == MODELS
    assert KEY not in response.content.decode()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("bad_key", NON_TEXT_KEYS, ids=NON_TEXT_IDS)
def test_the_edit_dialog_loads_a_non_text_credentials_saved_models(
    bad_key, secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    _saved(org, {"api_key": bad_key}, models=[*MODELS, "held-model-id"])
    # A null key is absent to the reads, so they offer every saved ID.
    held = bad_key is not None

    response = _hydrate(secondary_org_client)

    assert response.status_code == 200, response.json()
    result = response.json()["result"]
    expected = [] if held else [*MODELS, "held-model-id"]
    assert sorted(result["models"]) == sorted(expected)
    assert result["dropped_model_count"] == (3 if held else 0)
    assert ("code" in result) == held
    if held:
        assert "held-model-id" not in response.content.decode()
    # The same projection the console's config read shows.
    read = _config_read(secondary_org_client)["providers"]["openai"]
    assert sorted(read["models"]) == sorted(result["models"])
    assert read["dropped_model_count"] == result["dropped_model_count"]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize(
    "credentials, spelled",
    [
        ({"api_key": 2718281828459045}, ["2718281828459045"]),
        ({"api_key": ["fixture-old-key-0123456789"]}, ["fixture-old-key-0123456789"]),
        (
            {"api_key": 2718281828459045, "api_secret": OLD_SECRET},
            ["2718281828459045", OLD_SECRET],
        ),
    ],
    ids=["number", "list", "number-and-secret"],
)
def test_the_edit_dialogs_load_never_returns_the_old_credential_or_a_model_id(
    credentials, spelled, secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    # Saved IDs include ones that spell the old credential's values.
    saved = [*MODELS, *spelled]
    _saved(org, credentials, models=saved)

    response = _hydrate(secondary_org_client)

    assert response.status_code == 200, response.json()
    result = response.json()["result"]
    # The list stays empty and only the count says what is held back.
    assert result["models"] == []
    assert result["dropped_model_count"] == len(saved)
    assert set(result) <= {"models", "dropped_model_count", "code", "model_warning"}
    body = response.content.decode()
    for value in [*saved, *spelled]:
        assert value not in body


@pytest.mark.integration
@pytest.mark.api
def test_the_edit_dialog_still_cannot_load_a_credential_that_cannot_be_decrypted(
    secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    _saved(org, b"not-a-ciphertext")

    response = _hydrate(secondary_org_client)

    assert response.status_code == 400
    assert response.json()["code"] == "provider_credential_context_invalid"


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("bad_key", NON_TEXT_KEYS, ids=NON_TEXT_IDS)
def test_a_complete_new_key_in_the_console_replaces_a_non_text_credential(
    bad_key, secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    row = _saved(org, {"api_key": bad_key})

    response = _console_replace(secondary_org_client, NEW_KEY)

    assert response.status_code == 200, response.json()
    assert _stored(row) == (MODELS, {"api_key": NEW_KEY})
    configured = _config_read(secondary_org_client)
    assert configured["providers"]["openai"]["models"] == MODELS
    assert configured["refused_provider_count"] == 0
    assert NEW_KEY not in response.content.decode()


@pytest.mark.integration
@pytest.mark.api
def test_a_console_edit_without_a_new_key_still_refuses_a_non_text_credential(
    secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    row = _saved(org, {"api_key": 2718281828459045})
    original = bytes(row.encrypted_credentials)

    # A timeout edit with no new key; the dialog loaded no models to send.
    response = _console_save(secondary_org_client, {"default_timeout": 30})

    assert response.status_code == 400
    assert response.json()["code"] == "provider_credential_context_invalid"
    row.refresh_from_db()
    assert row.models_list == MODELS
    assert bytes(row.encrypted_credentials) == original


# ---- auto routing


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("path", ["rotate", "console"])
def test_auto_candidates_naming_held_back_models_are_offered_after_replacement(
    path, secondary_org_context, secondary_org_client
):
    org, _ = secondary_org_context
    row = _saved(org, {"api_key": 2718281828459045})
    config = AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=1,
        is_active=True,
        routing={"auto": {"preference": "cost", "candidates": ["gpt-4o"]}},
    )

    before = _config_read(secondary_org_client)
    assert before["providers"]["openai"]["models"] == []
    assert before["refused_provider_count"] == 1

    if path == "rotate":
        response = _rotate(secondary_org_client, row, {"api_key": NEW_KEY})
    else:
        response = _console_replace(secondary_org_client, NEW_KEY)
    assert response.status_code == 200, response.json()

    after = _config_read(secondary_org_client)
    assert after["providers"]["openai"]["models"] == MODELS
    assert after["refused_provider_count"] == 0
    client = MagicMock()
    with patch.object(config_push, "get_gateway_client", return_value=client):
        assert config_push.push_org_config(str(org.id), config) is True
    (_, payload), _ = client.set_org_config.call_args
    assert payload["routing"]["auto"]["candidates"] == ["gpt-4o"]
    assert set(payload["routing"]["auto"]["candidates"]) <= set(
        payload["providers"]["openai"]["models"]
    )
