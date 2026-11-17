"""A stored provider key that holds a reference value shows as not valid.

A provider key row saved before the save-time check (written here straight to
the table) can still hold a reference (such as os.environ/... or oidc/...)
instead of a literal value. The provider-key list and detail responses, and the
provider status the Settings page reads, mark such a row with the one plain
sentence the refusal uses and show nothing of the value: not its name, not a
masked form of it. A valid key keeps its mask, and a key this deployment
cannot decrypt shows as it did before. The Django admin refuses the same
values, with the same sentence, when a provider key or a custom model is saved
there. The tests use values they set themselves.
"""

import json
import secrets
import uuid

import pytest
from cryptography.fernet import Fernet
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory
from rest_framework import status

from model_hub.admin import ApiKeyAdmin, CustomAIModelAdmin
from model_hub.models.api_key import ApiKey
from model_hub.models.custom_models import CustomAIModel
from model_hub.services import key_at_rest

REFUSAL = (
    "This model configuration is not valid; enter the key and settings from "
    "your provider."
)
API_KEYS_URL = "/model-hub/api-keys/"
PROVIDER_STATUS_URL = "/model-hub/develops/provider-status/"
AZURE_BASE = "https://fi-test.openai.azure.example.test"
AZURE_VERSION = "2024-06-01"
VALID_KEY = "sk-fi-test-valid-not-real-0042"
BULLET = "•"


class Canary:
    """A variable each test sets itself: a random name and a random value."""

    def __init__(self, name, value):
        self.name = name
        self.value = value


@pytest.fixture
def canary(monkeypatch):
    name = f"FI_TEST_CANARY_{uuid.uuid4().hex[:12].upper()}"
    value = f"canary-{secrets.token_hex(16)}"
    monkeypatch.setenv(name, value)
    return Canary(name, value)


def _written(form, name):
    return {
        "first-form": f"os.environ/{name}",
        "first-form-padded-upper": f"  OS.ENVIRON/{name}\t",
        "second-form": f"oidc/google/{name}",
    }[form]


FORMS = ["first-form", "first-form-padded-upper", "second-form"]


def _store_provider_key(organization, user, provider, *, key=None, config_json=None):
    """A provider key row written straight to the table: no save-time check."""
    return ApiKey.no_workspace_objects.create(
        organization=organization,
        user=user,
        provider=provider,
        key=key,
        config_json=config_json,
        workspace=None,
    )


def _stored_rows(organization, user, canary, form, case):
    written = _written(form, canary.name)
    if case == "key-only":
        return _store_provider_key(organization, user, "openai", key=written), written
    config = {"api_key": written, "api_base": AZURE_BASE, "api_version": AZURE_VERSION}
    return (
        _store_provider_key(organization, user, "azure", config_json=config),
        written,
    )


CASES = ["key-only", "azure-config"]


def _rows(payload):
    """The rows of a list response, with or without the success envelope."""
    if isinstance(payload, dict) and "result" in payload:
        payload = payload["result"]
    if isinstance(payload, dict):
        payload = payload.get("results", payload.get("providers"))
    return payload


def _detail(payload):
    return payload["result"] if "result" in payload else payload


def _only_bullets(masked):
    return isinstance(masked, str) and masked != "" and masked.strip(BULLET) == ""


def _assert_masked_shows_nothing(masked, case):
    if case == "key-only":
        assert _only_bullets(masked), masked
        return
    # Only the setting that holds a reference value is hidden; the others keep
    # the mask they have always had.
    assert _only_bullets(masked["api_key"]), masked
    assert masked["api_version"] == key_at_rest.mask_leaf(AZURE_VERSION)
    assert masked["api_base"] == key_at_rest.mask_leaf(AZURE_BASE)


def _assert_nothing_echoed(response, canary, written):
    text = json.dumps(response.json(), default=str)
    assert canary.name not in text
    assert canary.value not in text
    assert written.strip() not in text
    lowered = text.lower()
    assert "environ" not in lowered
    assert "oidc" not in lowered


# -------------------------------------------- list, detail, provider status ---


@pytest.mark.django_db
@pytest.mark.parametrize("case", CASES, ids=CASES)
@pytest.mark.parametrize("form", FORMS, ids=FORMS)
def test_the_key_list_marks_a_stored_key_naming_a_platform_value_as_not_valid(
    auth_client, organization, user, canary, form, case
):
    row, written = _stored_rows(organization, user, canary, form, case)

    response = auth_client.get(API_KEYS_URL)

    assert response.status_code == status.HTTP_200_OK, response.content
    listed = next(r for r in _rows(response.json()) if r["id"] == str(row.pk))
    assert listed["configuration_error"] == REFUSAL
    _assert_masked_shows_nothing(listed["masked_actual_key"], case)
    _assert_nothing_echoed(response, canary, written)


@pytest.mark.django_db
@pytest.mark.parametrize("case", CASES, ids=CASES)
@pytest.mark.parametrize("form", FORMS, ids=FORMS)
def test_the_key_detail_marks_a_stored_key_naming_a_platform_value_as_not_valid(
    auth_client, organization, user, canary, form, case
):
    row, written = _stored_rows(organization, user, canary, form, case)

    response = auth_client.get(f"{API_KEYS_URL}{row.pk}/")

    assert response.status_code == status.HTTP_200_OK, response.content
    detail = _detail(response.json())
    assert detail["configuration_error"] == REFUSAL
    _assert_masked_shows_nothing(detail["masked_actual_key"], case)
    _assert_nothing_echoed(response, canary, written)


@pytest.mark.django_db
@pytest.mark.parametrize("case", CASES, ids=CASES)
@pytest.mark.parametrize("form", FORMS, ids=FORMS)
def test_provider_status_marks_a_stored_key_naming_a_platform_value_as_not_valid(
    auth_client, organization, user, canary, form, case
):
    row, written = _stored_rows(organization, user, canary, form, case)

    response = auth_client.get(PROVIDER_STATUS_URL)

    assert response.status_code == status.HTTP_200_OK, response.content
    providers = {p["provider"]: p for p in _rows(response.json())}
    marked = providers[row.provider]
    assert marked["has_key"] is True
    assert marked["configuration_error"] == REFUSAL
    _assert_masked_shows_nothing(marked["masked_key"], case)
    # A provider with no key saved is not marked.
    assert "configuration_error" in providers["anthropic"]
    assert providers["anthropic"]["configuration_error"] is None
    _assert_nothing_echoed(response, canary, written)


@pytest.mark.django_db
def test_a_valid_stored_key_is_not_marked_and_keeps_its_mask(
    auth_client, organization, user
):
    row = _store_provider_key(organization, user, "openai", key=VALID_KEY)

    listed = auth_client.get(API_KEYS_URL)
    detail = auth_client.get(f"{API_KEYS_URL}{row.pk}/")
    provider_status = auth_client.get(PROVIDER_STATUS_URL)

    entry = next(r for r in _rows(listed.json()) if r["id"] == str(row.pk))
    shown = _detail(detail.json())
    openai = next(p for p in _rows(provider_status.json()) if p["provider"] == "openai")
    for marked in (entry, shown, openai):
        assert "configuration_error" in marked
        assert marked["configuration_error"] is None
    expected_mask = key_at_rest.mask_text(VALID_KEY)
    assert entry["masked_actual_key"] == expected_mask
    assert shown["masked_actual_key"] == expected_mask
    assert openai["masked_key"] == expected_mask


@pytest.mark.django_db
def test_a_stored_key_that_cannot_be_decrypted_shows_as_before(
    auth_client, organization, user
):
    row = _store_provider_key(organization, user, "openai", key=VALID_KEY)
    token = Fernet(Fernet.generate_key()).encrypt(b'{"v": "x"}').decode()
    ApiKey._base_manager.filter(pk=row.pk).update(key="enc::" + token)

    listed = auth_client.get(API_KEYS_URL)
    provider_status = auth_client.get(PROVIDER_STATUS_URL)

    assert listed.status_code == status.HTTP_200_OK, listed.content
    entry = next(r for r in _rows(listed.json()) if r["id"] == str(row.pk))
    openai = next(p for p in _rows(provider_status.json()) if p["provider"] == "openai")
    assert entry["masked_actual_key"] == key_at_rest.UNREADABLE_MASK
    assert openai["masked_key"] == key_at_rest.UNREADABLE_MASK
    for marked in (entry, openai):
        assert "configuration_error" in marked
        assert marked["configuration_error"] is None
    assert token not in json.dumps(listed.json())


# ----------------------------------------------------------- Django admin ---


@pytest.fixture
def admin_request(user):
    user.is_staff = user.is_superuser = True
    request = RequestFactory().post("/admin/")
    request.user = user
    return request


def _admin_form(admin_class, model, request, data):
    form_class = admin_class(model, AdminSite()).get_form(request)
    return form_class(data=data)


def _provider_key_form_data(organization, *, key="", config_json=""):
    return {
        "provider": "azure" if config_json else "openai",
        "organization": str(organization.pk),
        "key": key,
        "config_json": config_json,
    }


def _custom_model_form_data(organization, key_config):
    return {
        "user_model_id": f"fi-test-admin-model-{uuid.uuid4().hex[:8]}",
        "provider": "openai",
        "input_token_cost": "0",
        "output_token_cost": "0",
        "organization": str(organization.pk),
        "key_config": json.dumps(key_config),
    }


def _admin_cases(organization, value):
    azure = {"api_key": value, "api_base": AZURE_BASE, "api_version": AZURE_VERSION}
    return {
        "provider-key": (
            ApiKeyAdmin,
            ApiKey,
            _provider_key_form_data(organization, key=value),
        ),
        "provider-config": (
            ApiKeyAdmin,
            ApiKey,
            _provider_key_form_data(organization, config_json=json.dumps(azure)),
        ),
        "custom-model": (
            CustomAIModelAdmin,
            CustomAIModel,
            _custom_model_form_data(organization, {"key": value}),
        ),
    }


ADMIN_CASES = ["provider-key", "provider-config", "custom-model"]


@pytest.mark.django_db
@pytest.mark.parametrize("case", ADMIN_CASES, ids=ADMIN_CASES)
@pytest.mark.parametrize("form", FORMS, ids=FORMS)
def test_the_admin_refuses_a_configuration_naming_a_platform_value(
    admin_request, organization, canary, form, case
):
    # The same form with the customer's own value is accepted.
    admin_class, model, accepted = _admin_cases(organization, VALID_KEY)[case]
    valid = _admin_form(admin_class, model, admin_request, accepted)
    assert valid.is_valid(), valid.errors

    written = _written(form, canary.name)
    admin_class, model, data = _admin_cases(organization, written)[case]
    refused = _admin_form(admin_class, model, admin_request, data)

    assert not refused.is_valid()
    assert list(refused.non_field_errors()) == [REFUSAL]
    errors = json.dumps(refused.errors.get_json_data(), default=str)
    assert canary.name not in errors
    assert canary.value not in errors
    assert "environ" not in errors.lower()
    assert "oidc" not in errors.lower()
    assert not model._base_manager.filter(organization=organization).exists()
