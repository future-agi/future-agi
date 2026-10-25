"""Model-hub provider keys are encrypted at rest.

These tests read the raw columns and require Fernet ciphertext under
``INTEGRATION_ENCRYPTION_KEY`` (``enc::`` plus a token, the shape the
org-config secrets and the gateway's credentials use) for ``ApiKey.key`` and
every string in ``ApiKey.config_json`` and ``CustomAIModel.key_config``,
every API response masked, and a key the deployment cannot decrypt failing
closed with a clear error instead of being sent as garbage or replaced by a
FutureAGI key.
"""

import base64
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from django.apps import apps as django_apps
from django.conf import settings
from django.db import connection
from rest_framework import status
from structlog.testing import capture_logs

from integrations.services.credentials import CredentialManager
from model_hub.models.api_key import ApiKey
from model_hub.models.custom_models import CustomAIModel

API_KEYS_URL = "/model-hub/api-keys/"
CUSTOM_MODELS_URL = "/model-hub/custom-models/"
CUSTOM_MODEL_CREATE_URL = "/model-hub/custom_models/create/"
PREFIX = "enc::"


# --- helpers -----------------------------------------------------------------


def _raw_text(model, pk, column):
    """The column exactly as the database holds it (jsonb as its text)."""
    table = connection.ops.quote_name(model._meta.db_table)
    col = connection.ops.quote_name(column)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {col}::text FROM {table} WHERE id = %s", [str(pk)])
        row = cursor.fetchone()
    return row[0] if row else None


def _raw_json(model, pk, column):
    text = _raw_text(model, pk, column)
    return None if text is None else json.loads(text)


def _legacy(clear):
    """The legacy stored form of ``clear``."""
    return base64.b64encode(settings.SECRET_KEY[:16].encode() + clear.encode()).decode()


def _foreign_ciphertext(clear="sk-test-other-deployment-not-real"):
    """A well-formed stored key that this deployment's key cannot decrypt."""
    token = Fernet(Fernet.generate_key()).encrypt(json.dumps({"v": clear}).encode())
    return PREFIX + token.decode()


def _assert_ciphertext_of(stored, clear):
    """``stored`` is this deployment's ciphertext of ``clear`` and nothing a
    reader of the table can turn back into ``clear``."""
    assert isinstance(stored, str), stored
    assert clear not in stored
    try:
        decoded = base64.b64decode(stored)  # as lenient as the old reader
    except Exception:
        decoded = b""
    assert clear.encode() not in decoded, "the stored key base64-decodes to the key"
    assert stored.startswith(PREFIX), stored
    assert CredentialManager.decrypt(stored[len(PREFIX) :].encode()) == {"v": clear}


def _string_leaves(value, path=()):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _string_leaves(v, path + (k,))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _string_leaves(v, path + (i,))
    elif isinstance(value, str):
        yield path, value


def _at(value, path):
    for step in path:
        value = value[step]
    return value


def _assert_never_carries(payload, *values):
    text = json.dumps(payload, default=str)
    for value in values:
        assert value not in text, f"a response carries {value[:12]}..."


def _row(model, pk):
    return model._base_manager.get(pk=pk)


# --- stored as ciphertext ----------------------------------------------------


@pytest.mark.django_db
def test_a_text_key_is_stored_as_ciphertext_not_as_base64(auth_client):
    clear = "sk-test-kar-text-key-not-real-0001"
    response = auth_client.post(
        API_KEYS_URL, {"provider": "openai", "key": clear}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    pk = response.data["result"]["id"]

    _assert_ciphertext_of(_raw_text(ApiKey, pk, "key"), clear)
    assert _row(ApiKey, pk).actual_key == clear


@pytest.mark.django_db
def test_every_string_of_a_json_key_is_stored_as_ciphertext(auth_client):
    clear = {
        "api_key": "kar-azure-key-not-real-0002",
        "api_base": "https://kar.openai.azure.example.test",
        "api_version": "2024-06-01",
    }
    response = auth_client.post(
        API_KEYS_URL, {"provider": "azure", "config_json": clear}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    pk = response.data["result"]["id"]

    stored = _raw_json(ApiKey, pk, "config_json")
    assert sorted(stored) == sorted(clear)
    for path, value in _string_leaves(clear):
        _assert_ciphertext_of(_at(stored, path), value)
    assert _row(ApiKey, pk).actual_json == clear


@pytest.mark.django_db
@patch("model_hub.views.custom_model.validate_model_working", return_value=True)
@patch("model_hub.views.custom_model.get_model_volume", return_value=(0, 0))
def test_a_custom_model_key_config_is_stored_as_ciphertext(_vol, _val, auth_client):
    clear = {"key": "sk-test-kar-custom-not-real-0003"}
    response = auth_client.post(
        CUSTOM_MODEL_CREATE_URL,
        {
            "model_provider": "openai",
            "model_name": "kar-custom-model",
            "input_token_cost": 0.01,
            "output_token_cost": 0.03,
            "config_json": clear,
        },
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    pk = response.data["result"]["data"]["id"]

    stored = _raw_json(CustomAIModel, pk, "key_config")
    _assert_ciphertext_of(stored["key"], clear["key"])
    assert _row(CustomAIModel, pk).actual_json == clear

    listed = auth_client.get(CUSTOM_MODELS_URL)
    assert listed.status_code == status.HTTP_200_OK
    _assert_never_carries(listed.data, clear["key"], stored["key"])


# --- masked reads ------------------------------------------------------------


@pytest.mark.django_db
def test_no_response_carries_the_key_its_ciphertext_or_its_old_encoding(auth_client):
    clear = "sk-test-kar-masked-not-real-0004"
    created = auth_client.post(
        API_KEYS_URL, {"provider": "anthropic", "key": clear}, format="json"
    )
    assert created.status_code == status.HTTP_200_OK
    pk = created.data["result"]["id"]
    stored = _raw_text(ApiKey, pk, "key")
    never = (clear, stored, stored[len(PREFIX) :], _legacy(clear))

    detail = auth_client.get(f"{API_KEYS_URL}{pk}/")
    listed = auth_client.get(API_KEYS_URL)
    assert detail.status_code == listed.status_code == status.HTTP_200_OK
    for payload in (created.data, detail.data, listed.data):
        _assert_never_carries(payload, *never)
    masked = created.data["result"]["masked_actual_key"]
    assert masked.startswith(clear[:4]) and masked.endswith(clear[-4:])
    assert "*" in masked


# --- a value a caller sends is always a new key -------------------------------


@pytest.mark.django_db
def test_a_ciphertext_typed_as_a_key_is_never_decrypted_and_fails_closed(
    auth_client, organization, user
):
    # A stored value submitted as a key is stored encrypted as typed, is never
    # decrypted to another row's key nor sent anywhere as a key, reads as a key
    # that cannot be used, and is listed masked.
    victim = ApiKey.no_workspace_objects.create(
        organization=organization,
        user=user,
        provider="openai",
        key="sk-test-kar-victim-not-real-0005",
    )
    victim_stored = _raw_text(ApiKey, victim.pk, "key")

    response = auth_client.post(
        API_KEYS_URL, {"provider": "groq", "key": victim_stored}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK
    _assert_never_carries(response.data, victim_stored, victim_stored[5:])
    assert "cannot be decrypted" in response.data["result"]["masked_actual_key"]
    row = _row(ApiKey, response.data["result"]["id"])
    _assert_ciphertext_of(_raw_text(ApiKey, row.pk, "key"), victim_stored)
    with pytest.raises(ValueError, match="cannot be decrypted"):
        _ = row.actual_key


@pytest.mark.django_db
def test_a_saved_row_keeps_its_ciphertext_and_a_changed_leaf_is_encrypted(
    organization, user
):
    clear = {
        "api_key": "kar-azure-key-not-real-0006",
        "api_base": "https://kar-a.openai.azure.example.test",
        "api_version": "2024-06-01",
    }
    row = ApiKey.no_workspace_objects.create(
        organization=organization, user=user, provider="azure", config_json=clear
    )
    first = _raw_json(ApiKey, row.pk, "config_json")

    row.save()  # the same instance again
    assert _raw_json(ApiKey, row.pk, "config_json") == first

    loaded = _row(ApiKey, row.pk)
    loaded.config_json["api_base"] = "https://kar-b.openai.azure.example.test"
    loaded.save()
    after = _raw_json(ApiKey, row.pk, "config_json")
    assert after["api_key"] == first["api_key"]
    assert after["api_version"] == first["api_version"]
    _assert_ciphertext_of(after["api_base"], "https://kar-b.openai.azure.example.test")
    assert _row(ApiKey, row.pk).actual_json == {
        **clear,
        "api_base": "https://kar-b.openai.azure.example.test",
    }


# --- rows in the old encoding ------------------------------------------------


@pytest.mark.django_db
def test_a_row_in_the_old_encoding_reads_back_and_is_encrypted_on_its_next_save(
    organization, user
):
    # An old pod can still write the old encoding during a rolling deploy.
    clear = "sk-test-kar-legacy-not-real-0007"
    row = ApiKey.no_workspace_objects.create(
        organization=organization, user=user, provider="openai", key="placeholder"
    )
    ApiKey._base_manager.filter(pk=row.pk).update(key=_legacy(clear))

    loaded = _row(ApiKey, row.pk)
    assert loaded.actual_key == clear
    loaded.save()
    _assert_ciphertext_of(_raw_text(ApiKey, row.pk, "key"), clear)
    assert _row(ApiKey, row.pk).actual_key == clear


@pytest.mark.django_db
def test_ciphertext_inside_the_old_encoding_is_never_decrypted_and_fails_closed(
    organization, user
):
    # A token stored where a key belongs (typed as a key, or wrapped by an
    # earlier release's pod during a rolling deploy) is never decrypted to the
    # key of the row it came from, and a call that needs it fails closed instead
    # of sending it.
    victim = ApiKey.no_workspace_objects.create(
        organization=organization,
        user=user,
        provider="openai",
        key="sk-test-kar-victim-not-real-0008",
    )
    victim_stored = _raw_text(ApiKey, victim.pk, "key")
    row = ApiKey.no_workspace_objects.create(
        organization=organization, user=user, provider="groq", key="placeholder"
    )
    ApiKey._base_manager.filter(pk=row.pk).update(key=_legacy(victim_stored))
    loaded = _row(ApiKey, row.pk)
    with pytest.raises(ValueError, match="cannot be decrypted"):
        _ = loaded.actual_key
    assert "cannot be decrypted" in loaded.masked_actual_key


# --- without a stable encryption key, saving and using a key keeps working ---


@pytest.mark.django_db
@pytest.mark.parametrize("no_stable_key", ["unset", "made_up_per_process"])
def test_without_a_stable_key_a_key_is_saved_and_read_in_the_legacy_encoding(
    auth_client, settings, no_stable_key
):
    # Without INTEGRATION_ENCRYPTION_KEY, or with a key made up per process that
    # another process cannot read, saving and using a model-hub key keeps working
    # in the legacy encoding.
    if no_stable_key == "unset":
        settings.INTEGRATION_ENCRYPTION_KEY = ""
    else:
        settings.INTEGRATION_ENCRYPTION_KEY_EPHEMERAL = True
    clear = "sk-test-kar-no-stable-key-not-real-0011"
    response = auth_client.post(
        API_KEYS_URL, {"provider": "openai", "key": clear}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    pk = response.data["result"]["id"]
    stored = _raw_text(ApiKey, pk, "key")
    assert not stored.startswith(PREFIX)
    assert base64.b64decode(stored)[16:].decode() == clear
    assert _row(ApiKey, pk).actual_key == clear
    _assert_never_carries(response.data, clear, stored)


# --- a malformed INTEGRATION_ENCRYPTION_KEY never breaks saving a key ---

# What an operator can leave in INTEGRATION_ENCRYPTION_KEY that Fernet refuses:
# a placeholder such as your-fernet-key, and valid base64 of the wrong length.
# Never a real key.
MALFORMED_KEYS = (
    "your-fernet-key",
    base64.urlsafe_b64encode(b"sixteen-byte-key").decode(),
)
MALFORMED_IDS = ("placeholder", "wrong_length")
NOT_ENCRYPTED = "model_hub_keys_not_encrypted"
KEY_INVALID_AT_STARTUP = "model_hub_encryption_key_invalid"
FIX = "model_hub_keys_at_rest --encrypt"


def _events(records, event):
    return [r for r in records if r.get("event") == event]


def _assert_names_the_fix(record, malformed, *secrets):
    """A warning names the variable and the fix, never the key's value (nor
    any model-hub key)."""
    assert record.get("log_level") == "warning", record
    text = json.dumps(record, default=str)
    assert "INTEGRATION_ENCRYPTION_KEY" in text
    assert "valid Fernet key" in text
    assert FIX in text
    for value in (malformed, *secrets):
        assert value not in text


@pytest.mark.django_db
@pytest.mark.parametrize("malformed", MALFORMED_KEYS, ids=MALFORMED_IDS)
def test_a_malformed_encryption_key_never_breaks_saving_a_key(
    auth_client, settings, malformed
):
    # Whatever INTEGRATION_ENCRYPTION_KEY holds, a model-hub key saves and works;
    # under a malformed key it is kept in the legacy encoding and the save says
    # how to fix the key.
    settings.INTEGRATION_ENCRYPTION_KEY = malformed
    clear = "sk-test-karb-malformed-key-not-real-0012"
    with capture_logs() as records:
        response = auth_client.post(
            API_KEYS_URL, {"provider": "openai", "key": clear}, format="json"
        )
    assert response.status_code == status.HTTP_200_OK, response.data
    pk = response.data["result"]["id"]
    stored = _raw_text(ApiKey, pk, "key")
    assert not stored.startswith(PREFIX)
    assert base64.b64decode(stored)[16:].decode() == clear
    assert _row(ApiKey, pk).actual_key == clear
    _assert_never_carries(response.data, clear, stored, malformed)
    warnings = _events(records, NOT_ENCRYPTED)
    assert len(warnings) == 1, records
    _assert_names_the_fix(warnings[0], malformed, clear, stored)


@pytest.mark.django_db
def test_a_malformed_encryption_key_is_named_on_every_save(
    organization, user, settings
):
    # Once per save of a row holding a key (not once per process, not once per
    # string of a JSON key), so the operator sees it wherever keys are saved.
    malformed = MALFORMED_KEYS[0]
    settings.INTEGRATION_ENCRYPTION_KEY = malformed
    clear = {
        "api_key": "karb-azure-malformed-not-real",
        "api_base": "https://karb.openai.azure.example.test",
        "api_version": "2024-06-01",
    }
    rotated = {**clear, "api_key": "karb-azure-rotated-not-real"}
    with capture_logs() as records:
        row = ApiKey.no_workspace_objects.create(
            organization=organization, user=user, provider="azure", config_json=clear
        )
        loaded = _row(ApiKey, row.pk)
        loaded.config_json = rotated
        loaded.save()
    warnings = _events(records, NOT_ENCRYPTED)
    assert len(warnings) == 2, records
    for warning in warnings:
        _assert_names_the_fix(warning, malformed, *clear.values(), *rotated.values())
    stored = _raw_json(ApiKey, row.pk, "config_json")
    assert not any(v.startswith(PREFIX) for _, v in _string_leaves(stored))
    assert _row(ApiKey, row.pk).actual_json == rotated


@pytest.mark.parametrize("malformed", MALFORMED_KEYS, ids=MALFORMED_IDS)
def test_a_malformed_encryption_key_is_named_at_startup(settings, malformed):
    # Every Django process (backend, Celery and Temporal workers, management
    # commands) runs ModelHubConfig.ready() once at startup.
    settings.INTEGRATION_ENCRYPTION_KEY = malformed
    with capture_logs() as records:
        django_apps.get_app_config("model_hub").ready()
    warnings = _events(records, KEY_INVALID_AT_STARTUP)
    assert len(warnings) == 1, records
    _assert_names_the_fix(warnings[0], malformed)

    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
    with capture_logs() as records:
        django_apps.get_app_config("model_hub").ready()
    assert not _events(records, KEY_INVALID_AT_STARTUP), records


REPO = Path(__file__).resolve().parents[3]
ENV_EXAMPLES = (REPO / ".env.example", REPO / "futureagi" / ".env.example")


@pytest.mark.parametrize(
    "env_example", ENV_EXAMPLES, ids=lambda p: str(p.relative_to(REPO))
)
def test_env_examples_ship_an_empty_encryption_key_and_say_how_to_make_one(
    env_example,
):
    # The manual backend setup copies futureagi/.env.example. An empty
    # INTEGRATION_ENCRYPTION_KEY (as both examples ship it) lets a local install
    # make one up per process; the comment above it says how to generate a real one.
    lines = env_example.read_text(encoding="utf-8").splitlines()
    assigned = [
        i
        for i, line in enumerate(lines)
        if line.startswith("INTEGRATION_ENCRYPTION_KEY=")
    ]
    assert len(assigned) == 1, assigned
    line = lines[assigned[0]]
    assert line == "INTEGRATION_ENCRYPTION_KEY=", line
    comment = []
    for above in reversed(lines[: assigned[0]]):
        if not above.startswith("#"):
            break
        comment.insert(0, above)
    assert any("Fernet" in c for c in comment), comment
    if env_example.parent.name == "futureagi":
        assert any("Fernet.generate_key()" in c for c in comment), comment


# --- a key this deployment cannot decrypt fails closed ------------------------


def _unreadable_key(organization, user, provider="openai", workspace=None):
    row = ApiKey.no_workspace_objects.create(
        organization=organization,
        user=user,
        provider=provider,
        key="placeholder",
        workspace=workspace,
    )
    ApiKey._base_manager.filter(pk=row.pk).update(key=_foreign_ciphertext())
    return row


@pytest.mark.django_db
def test_an_undecryptable_key_fails_closed_on_the_provider_path(organization, user):
    from agentic_eval.core_evals.run_prompt.litellm_models import (
        LiteLLMModelManager,
    )

    _unreadable_key(organization, user, provider="openai")
    manager = LiteLLMModelManager(model_name="", organization_id=organization.id)
    with pytest.raises(ValueError) as caught:
        manager.get_api_key(organization_id=organization.id, provider="openai")
    message = str(caught.value)
    assert "openai" in message
    assert "cannot be decrypted" in message
    assert "INTEGRATION_ENCRYPTION_KEY" in message


@pytest.mark.django_db
def test_an_undecryptable_hugging_face_key_never_falls_back_to_the_platform_token(
    organization, user
):
    from model_hub.utils import utils as hub_utils

    _unreadable_key(organization, user, provider="huggingface")
    with (
        patch.object(hub_utils.requests, "get") as http_get,
        pytest.raises(ValueError, match="cannot be decrypted"),
    ):
        hub_utils.load_hf_dataset_with_retries(
            "kar/none", "default", "train", organization.id, streaming=False
        )
    http_get.assert_not_called()


@pytest.mark.django_db
def test_an_undecryptable_key_is_listed_masked_so_it_can_be_replaced(
    auth_client, organization, user, workspace
):
    row = _unreadable_key(organization, user, provider="openai", workspace=workspace)
    stored = _raw_text(ApiKey, row.pk, "key")

    listed = auth_client.get(API_KEYS_URL)
    detail = auth_client.get(f"{API_KEYS_URL}{row.pk}/")
    assert listed.status_code == detail.status_code == status.HTTP_200_OK
    for payload in (listed.data, detail.data):
        _assert_never_carries(payload, stored, stored[len(PREFIX) :])
    masked = detail.data["result"]["masked_actual_key"]
    assert isinstance(masked, str) and "cannot be decrypted" in masked

    replaced = auth_client.post(
        API_KEYS_URL,
        {"provider": "openai", "key": "sk-test-kar-replacement-not-real-0009"},
        format="json",
    )
    assert replaced.status_code == status.HTTP_200_OK
    assert _row(ApiKey, row.pk).actual_key == "sk-test-kar-replacement-not-real-0009"


@pytest.mark.django_db
@patch("model_hub.views.custom_model.get_model_volume", return_value=(0, 0))
def test_an_undecryptable_custom_model_key_fails_closed_and_lists_masked(
    _vol, auth_client, organization, user, workspace
):
    from agentic_eval.core_evals.run_prompt.litellm_models import (
        LiteLLMModelManager,
    )

    model = CustomAIModel.objects.create(
        user_model_id="kar-unreadable-custom",
        provider="openai",
        input_token_cost=0.0,
        output_token_cost=0.0,
        organization=organization,
        workspace=workspace,
        user=user,
        key_config={"key": "placeholder"},
    )
    foreign = _foreign_ciphertext()
    CustomAIModel._base_manager.filter(pk=model.pk).update(key_config={"key": foreign})

    listed = auth_client.get(CUSTOM_MODELS_URL)
    assert listed.status_code == status.HTTP_200_OK
    _assert_never_carries(listed.data, foreign, foreign[len(PREFIX) :])

    manager = LiteLLMModelManager(
        model_name="kar-unreadable-custom", organization_id=organization.id
    )
    with pytest.raises(ValueError, match="cannot be decrypted"):
        manager.get_api_key(organization_id=organization.id)
