"""The provider push assembly's warnings name a provider by a fixed label (its
name when FA knows the provider, else "custom") and the credential row id,
never a custom provider's own name: an org can name a provider anything. All
credential and database I/O is mocked; every value here is synthetic.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from structlog.testing import capture_logs

from agentcc.services import config_push

CUSTOM_NAME = "synthetic-custom-provider-name-canary"
ORG_ID = "00000000-0000-4000-8000-0000000000aa"
ROW_ID = uuid.UUID("00000000-0000-4000-8000-0000000000c1")


def credential(provider_name):
    return SimpleNamespace(
        id=ROW_ID,
        provider_name=provider_name,
        encrypted_credentials=b"synthetic",
        models_list=["m1"],
        extra_config={},
        base_url="",
        api_format="openai",
        default_timeout_seconds=None,
        max_concurrent=None,
        conn_pool_size=None,
    )


def assemble(rows, decrypt):
    model = MagicMock()
    model.no_workspace_objects.filter.return_value = rows
    with (
        patch("agentcc.models.provider_credential.AgentccProviderCredential", model),
        patch(
            "integrations.services.credentials.CredentialManager.decrypt",
            side_effect=decrypt,
        ),
        capture_logs() as records,
    ):
        providers = config_push._assemble_providers(ORG_ID)
    return providers, records


def event(records, name):
    matches = [record for record in records if record.get("event") == name]
    assert len(matches) == 1, records
    return matches[0]


def test_a_decrypt_failure_logs_the_label_and_row_id_not_the_custom_name():
    providers, records = assemble(
        [credential(CUSTOM_NAME)], decrypt=ValueError("synthetic")
    )
    assert providers == {}
    record = event(records, "provider_credential_decrypt_failed")
    assert record["provider"] == "custom"
    assert record["credential_id"] == str(ROW_ID)
    assert CUSTOM_NAME not in repr(records)


def test_a_known_provider_keeps_its_name_in_the_log():
    _, records = assemble([credential("openai")], decrypt=ValueError("synthetic"))
    record = event(records, "provider_credential_decrypt_failed")
    assert record["provider"] == "openai"
    assert record["credential_id"] == str(ROW_ID)


def test_a_refused_projection_logs_the_label_and_row_id():
    _, records = assemble([credential(CUSTOM_NAME)], decrypt=lambda _raw: "not-a-dict")
    record = event(records, "provider_model_projection_refused")
    assert record["provider"] == "custom"
    assert record["credential_id"] == str(ROW_ID)
    assert CUSTOM_NAME not in repr(records)


def test_unsupported_credential_fields_log_the_label_and_row_id():
    unsupported = config_push.UnsupportedProviderCredentialFields(
        CUSTOM_NAME, ["odd_field"]
    )
    with (
        patch(
            "agentcc.services.provider_model_safety.filter_provider_model_ids",
            return_value={"models": ["m1"]},
        ),
        patch.object(
            config_push, "_normalize_provider_credentials", side_effect=unsupported
        ),
    ):
        _, records = assemble(
            [credential(CUSTOM_NAME)],
            decrypt=lambda _raw: {"api_key": "synthetic-not-a-key"},
        )
    record = event(records, "provider_credential_unsupported_fields")
    assert record["provider"] == "custom"
    assert record["credential_id"] == str(ROW_ID)
    assert record["fields"] == ["odd_field"]
    assert CUSTOM_NAME not in repr(records)


def test_ignored_extra_config_logs_the_label():
    with capture_logs() as records:
        config_push._normalize_provider_extra_config(
            CUSTOM_NAME, {"odd_key": "synthetic"}
        )
    record = event(records, "provider_extra_config_ignored")
    assert record["provider"] == "custom"
    assert record["keys"] == ["odd_key"]
    assert CUSTOM_NAME not in repr(records)
