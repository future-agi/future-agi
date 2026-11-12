"""Credential write contracts, with one real two-transaction row-lock regression.

Cipher and push are replaced; no provider is contacted.
"""

import json
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from accounts.models.organization import Organization
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.views import provider_credential as view_module
from agentcc.views.provider_credential import AgentccProviderCredentialViewSet
from django.db import connection, connections, transaction

AUXILIARY_FIELDS = [
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
    "service_account_json",
]


@pytest.fixture
def writes():
    instance = SimpleNamespace(
        provider_name="openai",
        organization=SimpleNamespace(id="org-1", pk="org-1"),
        base_url="",
        api_format="openai",
        display_name="Original",
        encrypted_credentials=b"old-ciphertext",
        models_list=[],
        extra_config={},
        save=MagicMock(),
    )
    with (
        patch.object(view_module, "AgentccProviderCredential") as model,
        patch.object(view_module, "AgentccProviderCredentialSerializer") as read,
        patch.object(view_module, "ensure_provider_base_url_allowed"),
        patch.object(view_module.transaction, "atomic"),
        patch.object(view_module, "lock_org"),
        patch.object(
            view_module.CredentialManager, "encrypt", return_value=b"new-ciphertext"
        ) as encrypt,
        patch.object(
            view_module.CredentialManager,
            "decrypt",
            return_value={
                "api_key": "stored-api",
                "aws_session_token": "stored-session",
            },
        ) as decrypt,
        patch.object(
            AgentccProviderCredentialViewSet,
            "_push_config_to_gateway",
            return_value=True,
        ) as push,
    ):
        model.no_workspace_objects.filter.return_value.first.return_value = None
        read.return_value.data = {}
        yield SimpleNamespace(
            instance=instance,
            model=model,
            encrypt=encrypt,
            decrypt=decrypt,
            push=push,
        )


def _call(writes, action, data):
    request = SimpleNamespace(data=data, organization=writes.instance.organization)
    view = AgentccProviderCredentialViewSet()
    view.request = request
    view.action = action
    view.format_kwarg = None
    view.kwargs = {}
    view.get_object = lambda: writes.instance
    return getattr(view, action)(request)


def _unchanged(writes):
    assert writes.instance.encrypted_credentials == b"old-ciphertext"
    assert writes.instance.display_name == "Original"
    writes.instance.save.assert_not_called()
    writes.encrypt.assert_not_called()
    writes.push.assert_not_called()


@pytest.mark.parametrize("action", ["update", "partial_update", "rotate"])
@pytest.mark.parametrize("blank", ["", "   ", "\t\r\n", " "])
def test_blank_api_key_is_refused_without_save_or_push(writes, action, blank):
    response = _call(
        writes,
        action,
        {
            "credentials": {
                "api_key": blank,
                "aws_session_token": "replacement-session",
            },
            "display_name": "Must not land",
        },
    )

    assert response.status_code == 400
    assert response.data["message"] == "credentials.api_key must not be blank"
    _unchanged(writes)


@pytest.mark.parametrize("field", ["api_key", *AUXILIARY_FIELDS])
@pytest.mark.parametrize("blank", ["", " \t\r\n", " "])
def test_create_with_only_blank_credentials_is_refused(writes, field, blank):
    response = _call(
        writes, "create", {"provider_name": "openai", "credentials": {field: blank}}
    )

    assert response.status_code == 400
    writes.model.assert_not_called()
    writes.encrypt.assert_not_called()
    writes.push.assert_not_called()


@pytest.mark.parametrize("field", AUXILIARY_FIELDS)
@pytest.mark.parametrize("blank", ["", " \t\r\n", " "])
def test_create_excludes_each_other_blank_secret(writes, field, blank):
    response = _call(
        writes,
        "create",
        {
            "provider_name": "openai",
            "credentials": {"api_key": "new-api", field: blank},
        },
    )

    assert response.status_code == 201
    writes.encrypt.assert_called_once_with({"api_key": "new-api"})
    writes.push.assert_called_once_with(writes.instance.organization)


@pytest.mark.parametrize("field", AUXILIARY_FIELDS)
@pytest.mark.parametrize("blank", ["", " \t\r\n", " "])
def test_rotate_treats_each_other_blank_secret_as_absent(writes, field, blank):
    writes.decrypt.return_value = {"api_key": "stored-api", field: "stored-other"}
    response = _call(
        writes, "rotate", {"credentials": {"api_key": "new-api", field: blank}}
    )

    assert response.status_code == 200
    writes.encrypt.assert_called_once_with(
        {"api_key": "new-api", field: "stored-other"}
    )
    writes.instance.save.assert_called_once()
    writes.push.assert_called_once_with(writes.instance.organization)


@pytest.mark.parametrize("action", ["update", "partial_update"])
@pytest.mark.parametrize("field", AUXILIARY_FIELDS)
def test_metadata_update_does_not_write_blank_secret_fields(writes, action, field):
    response = _call(
        writes, action, {"credentials": {field: " \t"}, "display_name": "Renamed"}
    )

    assert response.status_code == 200
    assert writes.instance.encrypted_credentials == b"old-ciphertext"
    assert writes.instance.display_name == "Renamed"
    writes.encrypt.assert_not_called()
    assert writes.instance.save.call_args.kwargs == {
        "update_fields": ["updated_at", "display_name", "models_list"]
    }
    writes.push.assert_called_once_with(writes.instance.organization)


@pytest.mark.parametrize("action", ["update", "partial_update"])
def test_omitted_credentials_keep_stored_bytes_on_metadata_update(writes, action):
    response = _call(writes, action, {"display_name": "Renamed"})

    assert response.status_code == 200
    assert writes.instance.encrypted_credentials == b"old-ciphertext"
    writes.encrypt.assert_not_called()
    writes.push.assert_called_once_with(writes.instance.organization)


def test_nonblank_rotate_keeps_omitted_keys_and_trims_as_before(writes):
    response = _call(writes, "rotate", {"credentials": {"api_key": "  rotated-api\n"}})

    assert response.status_code == 200
    writes.encrypt.assert_called_once_with(
        {"api_key": "rotated-api", "aws_session_token": "stored-session"}
    )
    assert writes.instance.encrypted_credentials == b"new-ciphertext"
    assert writes.instance.last_rotated_at is not None
    writes.instance.save.assert_called_once_with(
        update_fields=[
            "encrypted_credentials",
            "models_list",
            "last_rotated_at",
            "updated_at",
        ]
    )
    writes.push.assert_called_once_with(writes.instance.organization)


def test_nonblank_other_secret_rotates_while_omitted_api_key_is_required(writes):
    response = _call(
        writes, "rotate", {"credentials": {"aws_session_token": "new-session"}}
    )
    assert response.status_code == 400
    assert response.data["message"] == "credentials must contain 'api_key'"
    _unchanged(writes)

    response = _call(
        writes,
        "rotate",
        {"credentials": {"api_key": "new-api", "aws_session_token": "new-session"}},
    )
    assert response.status_code == 200
    writes.encrypt.assert_called_once_with(
        {"api_key": "new-api", "aws_session_token": "new-session"}
    )


def test_create_keeps_explicit_workspace_assignment_opt_out(writes):
    response = _call(
        writes,
        "create",
        {"provider_name": "openai", "credentials": {"api_key": "new-api"}},
    )

    assert response.status_code == 201
    assert writes.model.return_value.disable_auto_workspace_assignment is True
    writes.model.return_value.save.assert_called_once_with(force_insert=True)
    assert writes.model.call_args.kwargs["encrypted_credentials"] == b"new-ciphertext"
    writes.push.assert_called_once_with(writes.instance.organization)


@pytest.mark.parametrize("action", ["create", "update", "partial_update", "rotate"])
@pytest.mark.parametrize("field", ["api_key", *AUXILIARY_FIELDS])
@pytest.mark.parametrize("invalid", [None, 17, 1.5, True, [], {"fixture": "value"}])
def test_nonstring_values_are_refused_before_decryption(writes, action, field, invalid):
    data = {"provider_name": "openai", "credentials": {"api_key": "new-api"}}
    data["credentials"][field] = invalid
    response = _call(writes, action, data)
    assert response.status_code == 400
    assert response.data["message"] == "Credential values must be strings."
    _unchanged(writes)
    writes.decrypt.assert_not_called()
    writes.model.assert_not_called()


@pytest.mark.parametrize(
    "credentials",
    [
        {"api_key": "new-api"},
        {"api_key": "new-api", "aws_session_token": "new-session"},
    ],
)
def test_explicit_complete_replacement_clears_models_when_old_decryption_fails(
    writes, credentials
):
    writes.decrypt.side_effect = ValueError("unreadable fixture")
    response = _call(
        writes, "rotate", {"credentials": credentials, "replace_credentials": True}
    )
    assert response.status_code == 200
    writes.decrypt.assert_called_once_with(b"old-ciphertext")
    assert writes.instance.models_list == []
    assert response.data["result"]["code"] == "provider_credential_context_invalid"
    assert "Re-enter" in response.data["result"]["model_warning"]
    writes.encrypt.assert_called_once_with(credentials)
    writes.instance.save.assert_called_once()
    writes.push.assert_called_once_with(writes.instance.organization)


@pytest.mark.parametrize(
    "credentials",
    [
        {"aws_session_token": "new-session"},
        {"api_key": " \t"},
        {"api_key": "new-api", "aws_session_token": " \t"},
    ],
)
def test_partial_full_replacement_is_refused(writes, credentials):
    writes.decrypt.side_effect = ValueError("unreadable fixture")
    response = _call(
        writes, "rotate", {"credentials": credentials, "replace_credentials": True}
    )
    assert response.status_code == 400
    _unchanged(writes)
    writes.decrypt.assert_not_called()


def test_unreadable_merge_never_silently_discards_unknown_auxiliary_secrets(writes):
    writes.decrypt.side_effect = ValueError("unreadable fixture")
    response = _call(writes, "rotate", {"credentials": {"api_key": "new-api"}})
    assert response.status_code == 400
    assert response.data["message"] == (
        "Saved provider credential could not be decrypted or validated. "
        "Repair the encryption configuration or submit a complete "
        "credential set with replace_credentials=true to replace it."
    )
    _unchanged(writes)


@pytest.mark.parametrize("invalid", [None, "true", 1, {}])
def test_full_replacement_requires_a_boolean_opt_in(writes, invalid):
    data = {"credentials": {"api_key": "new-api"}, "replace_credentials": invalid}
    response = _call(writes, "rotate", data)
    assert response.status_code == 400
    _unchanged(writes)
    writes.decrypt.assert_not_called()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("auxiliary", [{}, {"aws_session_token": " \t"}])
def test_locked_rotation_preserves_a_concurrent_auxiliary_replacement(auxiliary):
    if not connection.features.has_select_for_update:
        pytest.skip("Requires real row locks")
    org = Organization.objects.create(name="Credential rotation lock regression")
    credential = AgentccProviderCredential(
        organization=org,
        provider_name="openai",
        encrypted_credentials=json.dumps(
            {"api_key": "old-api", "aws_session_token": "old-session"}
        ).encode(),
    )
    credential.disable_auto_workspace_assignment = True
    credential.save(force_insert=True)
    attempted, committed = Event(), Event()
    results = []

    def observe_read(execute, sql, params, many, context):
        # The old unlocked SELECT must finish before A commits; the fixed
        # org/credential locking SELECT must wait for A's real transaction.
        if "FOR NO KEY UPDATE" in sql or "FOR UPDATE" in sql:
            attempted.set()
        elif sql.lstrip().startswith("SELECT") and "agentcc_provider_credential" in sql:
            result = execute(sql, params, many, context)
            attempted.set()
            assert committed.wait(10)
            return result
        return execute(sql, params, many, context)

    def rotate():
        try:
            request = SimpleNamespace(
                organization=org,
                query_params={},
                data={"credentials": {"api_key": "rotated-api", **auxiliary}},
            )
            view = AgentccProviderCredentialViewSet()
            view.request = request
            view.action = "rotate"
            view.kwargs = {"pk": credential.pk}
            view.permission_classes = []
            with connection.execute_wrapper(observe_read):
                results.append(view.rotate(request, pk=credential.pk).status_code)
        except Exception as error:
            results.append(type(error).__name__)
        finally:
            connections.close_all()

    worker = Thread(target=rotate, daemon=True)
    with (
        patch.object(
            view_module.CredentialManager,
            "decrypt",
            side_effect=lambda b: json.loads(bytes(b)),
        ),
        patch.object(
            view_module.CredentialManager,
            "encrypt",
            side_effect=lambda d: json.dumps(d).encode(),
        ),
        patch.object(view_module, "AgentccProviderCredentialSerializer") as read,
        patch.object(
            AgentccProviderCredentialViewSet,
            "_push_config_to_gateway",
            return_value=True,
        ),
    ):
        read.return_value.data = {}
        try:
            with transaction.atomic():
                view_module.lock_org(org.pk)
                current = AgentccProviderCredential.no_workspace_objects.select_for_update().get(
                    pk=credential.pk
                )
                worker.start()
                assert attempted.wait(10)
                current.encrypted_credentials = json.dumps(
                    {"api_key": "replacement-api", "aws_session_token": "new-session"}
                ).encode()
                current.save(update_fields=["encrypted_credentials"])
        finally:
            committed.set()
            if worker.ident is not None:
                worker.join(10)
        assert not worker.is_alive()
    assert results == [200]
    credential.refresh_from_db()
    assert json.loads(bytes(credential.encrypted_credentials)) == {
        "api_key": "rotated-api",
        "aws_session_token": "new-session",
    }
