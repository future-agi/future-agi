"""The server-side judge reads its provider key through the ApiKey model's accessor, never by decoding the stored column.

The integrated provider-key store uses RF19's lazy accessors. Code that reads ``ApiKey.actual_key``
keeps working across storage changes; code that decodes ``ApiKey.key`` itself would break or send a wrong key.
These tests give the stored column a value that only the model can read (a stand-in for the new format) and make the
model's decryption return the key: the judge's key lookup must return exactly that key. A reader that decoded the
column directly would get an error or a different value, and the tests would fail.

``LiteLLMModelManager.get_api_key`` has three sources, each covered: an ``ApiKey`` row's key (``actual_key``), an
``ApiKey`` row's JSON credentials for providers that use them (``actual_json``, e.g. Vertex AI service accounts), and
an organization's custom model's key config (``CustomAIModel.actual_json``).
"""

from __future__ import annotations

import base64

import pytest

from model_hub.models.api_key import ApiKey
from model_hub.models.custom_models import CustomAIModel

ACCESSOR_VALUE = "sk-dsx-from-the-model-accessor"
OPAQUE_COLUMN = "v2:opaque-ciphertext-only-the-model-can-read"
ACCESSOR_JSON = {
    "type": "service_account",
    "project_id": "dsx-accessor-project",
    "private_key": "dsx-private-key-from-the-model-accessor",
}
OPAQUE_JSON = {
    "type": "v2:opaque-type",
    "project_id": "v2:opaque-project",
    "private_key": "v2:opaque-private-key",
}
CUSTOM_MODEL = "dsx-custom-judge"
CUSTOM_ACCESSOR_JSON = {
    "key": "sk-dsx-custom-from-the-model-accessor",
    "api_base": "http://127.0.0.1:9/v1",
}
CUSTOM_OPAQUE_JSON = {"key": "v2:opaque-custom-key", "api_base": "v2:opaque-base"}


def _direct_decode(column: str) -> str | None:
    """What a reader that bypasses the model would do with today's format: base64, drop the 16-byte salt."""
    try:
        return base64.b64decode(column, validate=True)[16:].decode()
    except Exception:
        return None


def _direct_decode_json(column: dict) -> dict:
    """The same bypass applied to each value of a JSON credential column."""
    return {
        k: _direct_decode(v) if isinstance(v, str) else v for k, v in column.items()
    }


def _json_accessor(self, json_key=None):
    """The model's JSON decryption after the format change: the opaque columns decrypt to the accessor values."""
    column = json_key if json_key is not None else self.config_json
    if column == OPAQUE_JSON:
        return dict(ACCESSOR_JSON)
    if column == CUSTOM_OPAQUE_JSON:
        return dict(CUSTOM_ACCESSOR_JSON)
    return {}


@pytest.fixture
def opaque_json_store(organization, workspace, monkeypatch):
    """A provider whose credentials are JSON (Vertex AI): the key column is empty, config_json holds them."""
    ApiKey.objects.create(
        organization=organization,
        workspace=workspace,
        provider="vertex_ai",
        config_json={
            "type": "service_account",
            "project_id": "before-the-format-change",
            "private_key": "dsx-private-key-before-the-format-change",
        },
    )
    ApiKey.objects.filter(organization=organization, provider="vertex_ai").update(
        config_json=OPAQUE_JSON
    )
    # RF19 exposes a lazy property rather than the earlier decrypt_json method.
    monkeypatch.setattr(ApiKey, "actual_json", property(_json_accessor))
    return organization, workspace


@pytest.fixture
def opaque_custom_model(organization, workspace, user, monkeypatch):
    """An organization's custom model: its key config is read through CustomAIModel.actual_json."""
    CustomAIModel.objects.create(
        user_model_id=CUSTOM_MODEL,
        provider="openai",
        input_token_cost=0.01,
        output_token_cost=0.02,
        organization=organization,
        workspace=workspace,
        user=user,
        key_config={
            "key": "sk-dsx-custom-before-the-format-change",
            "api_base": "http://127.0.0.1:9/v1",
        },
    )
    CustomAIModel.objects.filter(
        organization=organization, user_model_id=CUSTOM_MODEL
    ).update(key_config=CUSTOM_OPAQUE_JSON)
    # Keep the test at the public accessor seam, independent of storage format.
    monkeypatch.setattr(
        CustomAIModel,
        "actual_json",
        property(lambda self: _json_accessor(self, self.key_config)),
    )
    return organization, workspace


@pytest.fixture
def opaque_store(organization, workspace, monkeypatch):
    ApiKey.objects.create(
        organization=organization,
        workspace=workspace,
        provider="openai",
        key="sk-dsx-value-before-the-format-change",
    )
    # The stored column holds a format only the model accessor can read.
    # Patch the lazy property, not the removed pre-RF19 decrypt_key method.
    ApiKey.objects.filter(organization=organization, provider="openai").update(
        key=OPAQUE_COLUMN
    )
    monkeypatch.setattr(
        ApiKey,
        "actual_key",
        property(lambda self: ACCESSOR_VALUE if self.key == OPAQUE_COLUMN else None),
    )
    return organization, workspace


@pytest.mark.django_db
def test_the_eval_engine_judge_gets_the_key_the_accessor_returns(opaque_store):
    from evaluations.engine.instance import _get_api_key

    organization, workspace = opaque_store
    assert _get_api_key("gpt-4.1-mini", organization.id, workspace.id) == ACCESSOR_VALUE


@pytest.mark.django_db
def test_the_model_manager_key_lookup_reads_the_accessor(opaque_store):
    from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager

    organization, workspace = opaque_store
    manager = LiteLLMModelManager("gpt-4.1-mini", exclude_providers="custom")
    assert (
        manager.get_api_key(organization_id=organization.id, workspace_id=workspace.id)
        == ACCESSOR_VALUE
    )
    # and that value is the model's own accessor on the stored row
    stored = ApiKey.objects.get(organization=organization, provider="openai")
    assert stored.actual_key == ACCESSOR_VALUE


@pytest.mark.django_db
def test_the_fixture_discriminates_a_direct_decoder(opaque_store):
    """Negative control: decoding the stored column directly cannot produce the key the judge must use."""
    organization, _ = opaque_store
    column = (
        ApiKey.objects.filter(organization=organization, provider="openai")
        .values_list("key", flat=True)
        .get()
    )
    assert column == OPAQUE_COLUMN
    assert _direct_decode(column) != ACCESSOR_VALUE
    # Positive control for the historical encoding the direct reader understands.
    legacy_column = base64.b64encode(b"0" * 16 + b"sk-dsx-today").decode()
    assert _direct_decode(legacy_column) == "sk-dsx-today"


@pytest.mark.django_db
def test_the_model_manager_reads_json_credentials_through_the_accessor(
    opaque_json_store,
):
    """The ApiKey.actual_json branch: a provider with JSON credentials and no key."""
    from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager

    organization, workspace = opaque_json_store
    manager = LiteLLMModelManager("gemini-2.5-flash", exclude_providers="custom")
    got = manager.get_api_key(
        organization_id=organization.id, workspace_id=workspace.id, provider="vertex_ai"
    )
    assert got == ACCESSOR_JSON
    stored = ApiKey.objects.get(organization=organization, provider="vertex_ai")
    assert stored.key in (None, "")
    assert stored.config_json == OPAQUE_JSON
    assert stored.actual_json == ACCESSOR_JSON
    # a reader that decoded each stored value itself would not get the credentials
    assert _direct_decode_json(stored.config_json) != ACCESSOR_JSON


@pytest.mark.django_db
def test_the_model_manager_reads_a_custom_models_key_config_through_the_accessor(
    opaque_custom_model,
):
    """The CustomAIModel.actual_json branch: an organization's custom model wins over the provider keys."""
    from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager

    organization, workspace = opaque_custom_model
    manager = LiteLLMModelManager(CUSTOM_MODEL, organization_id=organization.id)
    got = manager.get_api_key(
        organization_id=organization.id, workspace_id=workspace.id
    )
    assert got == CUSTOM_ACCESSOR_JSON
    stored = CustomAIModel.objects.get(
        organization=organization, user_model_id=CUSTOM_MODEL
    )
    assert stored.key_config == CUSTOM_OPAQUE_JSON
    assert stored.actual_json == CUSTOM_ACCESSOR_JSON
    assert _direct_decode_json(stored.key_config) != CUSTOM_ACCESSOR_JSON
