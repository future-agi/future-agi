"""Credential regressions: generated needles, boolean-only failure messages."""

import base64
import importlib
import importlib.util
import secrets
from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import RequestFactory
from django.utils import timezone

from agentcc.services.credential_manager import decrypt_token, encrypt_token
from ee.falcon_ai import oauth_client
from ee.falcon_ai.admin import MCPConnectorAdmin
from ee.falcon_ai.mcp_proxy import MCPConnectorProxy
from ee.falcon_ai.models import MCPConnector
from ee.falcon_ai.serializers_connectors import (
    MCPConnectorCreateSerializer,
    MCPConnectorDetailSerializer,
    MCPConnectorListSerializer,
)
from ee.falcon_ai.serializers_contracts import MCPConnectorUpdateRequestSerializer

OAUTH_FIELDS = ("oauth_client_secret", "oauth_access_token", "oauth_refresh_token")
FIELDS = (*OAUTH_FIELDS, "auth_header_value")
MIGRATION = "ee.falcon_ai.migrations.0010_encrypt_connector_credentials"
pytestmark = pytest.mark.django_db


def check(condition, message):
    # Do not let pytest introspection disclose generated credentials on failure.
    assert condition, message


def needle():
    return "credential-" + secrets.token_urlsafe(32)


def assert_encrypted(stored, logical):
    check(stored.startswith("enc::"), "credential is not encrypted")
    check(stored != logical, "plaintext persisted")
    encoded = base64.b64encode(logical.encode()).decode()
    check(stored != encoded, "base64 persisted")
    check(decrypt_token(stored) == logical, "logical credential changed")


@pytest.fixture
def connector(user, workspace):
    return MCPConnector.no_workspace_objects.create(
        organization=user.organization,
        workspace=workspace,
        name="at-rest-" + secrets.token_hex(8),
        server_url="https://mcp.example.invalid/mcp",
        auth_type="oauth",
        oauth_server_metadata={
            "authorization_endpoint": "https://mcp.example.invalid/authorize",
            "token_endpoint": "https://mcp.example.invalid/token",
            "registration_endpoint": "https://mcp.example.invalid/register",
        },
    )


def row(connector):
    return MCPConnector._base_manager.get(pk=connector.pk)


def test_registration_encrypts_stored_secret(connector):
    value = needle()
    with patch.object(
        oauth_client,
        "register_client",
        return_value={
            "client_id": "test-client",
            "client_secret": value,
        },
    ):
        oauth_client.build_authorization_url(
            connector, "https://app.example.invalid/cb"
        )
    assert_encrypted(row(connector).oauth_client_secret, value)


@pytest.mark.parametrize("prefix", ["", "enc::"])
def test_exchange_encrypts_stored_tokens(connector, prefix):
    access, refresh = prefix + needle(), needle()
    connector.oauth_client_secret = encrypt_token(needle())
    # A callback snapshot represents an already-persisted registration.
    connector.save()
    response = Mock()
    response.json.return_value = {"access_token": access, "refresh_token": refresh}
    with patch.object(oauth_client.httpx, "post", return_value=response):
        oauth_client.exchange_code_for_tokens(
            connector, needle(), "https://app.example.invalid/cb"
        )
    stored = row(connector)
    assert_encrypted(stored.oauth_access_token, access)
    assert_encrypted(stored.oauth_refresh_token, refresh)


@pytest.mark.parametrize("rotates", [False, True])
def test_refresh_encrypts_and_preserves_logical_tokens(connector, rotates):
    old_refresh, access, new_refresh, client_secret = (needle() for _ in range(4))
    connector.oauth_refresh_token = encrypt_token(old_refresh)
    connector.oauth_client_secret = encrypt_token(client_secret)
    connector.save()  # Refresh validates the persisted WRITE registration.
    response = Mock()
    response.json.return_value = {"access_token": access}
    if rotates:
        response.json.return_value["refresh_token"] = new_refresh
    with patch.object(oauth_client.httpx, "post", return_value=response) as post:
        oauth_client.refresh_access_token(connector)
    sent = post.call_args.kwargs["data"]
    check(sent["refresh_token"] == old_refresh, "refresh payload changed")
    check(sent["client_secret"] == client_secret, "client payload changed")
    stored = row(connector)
    assert_encrypted(stored.oauth_access_token, access)
    assert_encrypted(
        stored.oauth_refresh_token, new_refresh if rotates else old_refresh
    )


@pytest.mark.parametrize("auth_type", ["oauth", "bearer", "api_key"])
@pytest.mark.parametrize("representation", ["raw", "base64", "malformed"])
def test_header_builder_refuses_legacy_or_corrupt_values(
    connector, auth_type, representation
):
    value = needle()
    if representation == "base64":
        value = base64.b64encode(value.encode()).decode()
    elif representation == "malformed":
        value = "enc::" + value
    connector.auth_type = auth_type
    connector.oauth_access_token = value
    connector.auth_header_value = value
    caught = None
    try:
        MCPConnectorProxy()._build_headers(connector)
    except Exception as error:
        caught = error
    check(caught is not None, "unprotected credential was accepted")
    check(type(caught).__name__ == "FalconCredentialError", "typed refusal required")
    check(value not in str(caught), "credential leaked in error")


@pytest.mark.parametrize(
    "field,operation",
    [
        ("oauth_client_secret", "exchange_code_for_tokens"),
        ("oauth_client_secret", "refresh_access_token"),
        ("oauth_refresh_token", "refresh_access_token"),
    ],
)
def test_token_endpoint_refuses_legacy_before_io(connector, field, operation):
    connector.oauth_refresh_token = encrypt_token(needle())
    connector.oauth_client_secret = encrypt_token(needle())
    setattr(connector, field, needle())
    caught = None
    with patch.object(oauth_client.httpx, "post") as post:
        try:
            if operation == "exchange_code_for_tokens":
                oauth_client.exchange_code_for_tokens(
                    connector, needle(), "https://app.example.invalid/cb"
                )
            else:
                oauth_client.refresh_access_token(connector)
        except Exception as error:
            caught = error
    check(caught is not None, "legacy credential was accepted")
    check(type(caught).__name__ == "FalconCredentialError", "typed refusal required")
    check(post.call_count == 0, "request sent before refusal")


@pytest.mark.parametrize("field", ["oauth_refresh_token", "oauth_client_secret"])
@pytest.mark.parametrize("representation", ["raw", "base64", "malformed"])
def test_refresh_refuses_persisted_unreadable_credential_before_io(
    connector, field, representation
):
    from django.db import router

    from ee.falcon_ai.oauth_registration import _identity

    alias = router.db_for_write(type(connector))
    connector.oauth_client_id = "test-client"
    connector.oauth_refresh_token = encrypt_token(needle())
    connector.oauth_client_secret = encrypt_token(needle())
    stored = needle()
    if representation == "base64":
        stored = base64.b64encode(stored.encode()).decode()
    elif representation == "malformed":
        stored = "enc::" + stored
    setattr(connector, field, stored)
    connector.save(using=alias)
    connector.refresh_from_db(using=alias)
    current = MCPConnector._base_manager.using(alias).get(pk=connector.pk)
    check(getattr(current, field) == stored, "fixture credential was changed")
    check(
        _identity(connector) == _identity(current),
        "snapshot differs from WRITE row",
    )
    caught = None
    with (
        patch.object(oauth_client.httpx, "post") as post,
        patch.object(
            oauth_client,
            "_refresh_access_token",
            wraps=oauth_client._refresh_access_token,
        ) as refresh,
    ):
        try:
            oauth_client.refresh_access_token(connector)
        except Exception as error:
            caught = error
    check(refresh.call_count == 1, "matching snapshot refused before refresh")
    check(type(caught).__name__ == "FalconCredentialError", "typed refusal required")
    check(post.call_count == 0, "request sent before refusal")


@pytest.mark.parametrize("representation", ["raw", "base64", "malformed"])
def test_exchange_refuses_persisted_unreadable_client_secret_before_io(
    connector, representation
):
    from django.db import router

    from ee.falcon_ai.oauth_registration import _identity

    alias = router.db_for_write(type(connector))
    connector.oauth_client_id = "test-client"
    connector.oauth_refresh_token = encrypt_token(needle())
    stored = needle()
    if representation == "base64":
        stored = base64.b64encode(stored.encode()).decode()
    elif representation == "malformed":
        stored = "enc::" + stored
    connector.oauth_client_secret = stored
    connector.save(using=alias)
    connector.refresh_from_db(using=alias)
    current = MCPConnector._base_manager.using(alias).get(pk=connector.pk)
    check(current.oauth_client_secret == stored, "fixture credential was changed")
    check(
        _identity(connector) == _identity(current),
        "snapshot differs from WRITE row",
    )
    caught = None
    with (
        patch.object(oauth_client.httpx, "post") as post,
        patch.object(
            oauth_client,
            "_exchange_code_for_tokens",
            wraps=oauth_client._exchange_code_for_tokens,
        ) as exchange,
    ):
        try:
            oauth_client.exchange_code_for_tokens(
                connector, needle(), "https://app.example.invalid/cb"
            )
        except Exception as error:
            caught = error
    check(exchange.call_count == 1, "matching snapshot refused before exchange")
    check(type(caught).__name__ == "FalconCredentialError", "typed refusal required")
    check(post.call_count == 0, "request sent before refusal")


@pytest.mark.parametrize(
    "operation", ["refresh_access_token", "exchange_code_for_tokens"]
)
def test_token_endpoint_refuses_stale_snapshot_before_io(connector, operation):
    from django.db import router

    from ee.falcon_ai.oauth_registration import _identity

    alias = router.db_for_write(type(connector))
    connector.oauth_client_id = "test-client"
    connector.oauth_refresh_token = encrypt_token(needle())
    connector.oauth_client_secret = encrypt_token(needle())
    connector.save(using=alias)
    connector.refresh_from_db(using=alias)
    current = MCPConnector._base_manager.using(alias).get(pk=connector.pk)
    connector.oauth_client_secret = encrypt_token(needle())
    check(
        _identity(connector) != _identity(current),
        "stale control unexpectedly matches WRITE row",
    )
    caught = None
    with (
        patch.object(oauth_client.httpx, "post") as post,
        patch.object(oauth_client, "_" + operation) as token_operation,
    ):
        try:
            if operation == "exchange_code_for_tokens":
                oauth_client.exchange_code_for_tokens(
                    connector, needle(), "https://app.example.invalid/cb"
                )
            else:
                oauth_client.refresh_access_token(connector)
        except Exception as error:
            caught = error
    check(type(caught).__name__ == "FalconCredentialError", "typed refusal required")
    check(token_operation.call_count == 0, "stale snapshot reached token operation")
    check(post.call_count == 0, "request sent before refusal")


def test_expired_access_preserves_typed_refusal(connector):
    connector.oauth_access_token = encrypt_token(needle())
    connector.oauth_refresh_token = needle()
    connector.oauth_token_expires_at = timezone.now() - timedelta(seconds=1)
    caught = None
    try:
        MCPConnectorProxy()._build_headers(connector)
    except Exception as error:
        caught = error
    check(
        type(caught).__name__ == "FalconCredentialError",
        "refresh refusal was swallowed",
    )


@pytest.mark.parametrize("auth_type", ["oauth", "bearer", "api_key"])
def test_encrypted_header_positive_control(connector, auth_type):
    value = needle()
    connector.auth_type = auth_type
    connector.oauth_access_token = encrypt_token(value)
    connector.auth_header_value = encrypt_token(value)
    headers = MCPConnectorProxy()._build_headers(connector)
    expected = value if auth_type == "api_key" else "Bearer " + value
    check(headers["Authorization"] == expected, "header credential changed")


def test_admin_and_serializers_do_not_expose_credentials(connector, user):
    request = RequestFactory().get("/")
    request.user = user
    model_admin = MCPConnectorAdmin(MCPConnector, AdminSite())
    form = model_admin.get_form(request, obj=connector)
    hidden = (*FIELDS, "oauth_code_verifier", "oauth_state")
    check(not set(hidden).intersection(form.base_fields), "admin exposes credentials")
    for serializer in (MCPConnectorListSerializer, MCPConnectorDetailSerializer):
        check(
            not set(hidden).intersection(serializer(connector).data),
            "serializer exposes credentials",
        )


@pytest.mark.parametrize(
    "serializer_class",
    [MCPConnectorCreateSerializer, MCPConnectorUpdateRequestSerializer],
)
def test_header_input_is_write_only(serializer_class):
    check(
        serializer_class().fields["auth_header_value"].write_only,
        "header input is readable",
    )


def test_shared_helper_retains_legacy_behavior():
    value = needle()
    check(decrypt_token(value) == value, "shared helper compatibility changed")


def migration_module():
    check(
        importlib.util.find_spec(MIGRATION) is not None,
        "credential data migration missing",
    )
    return importlib.import_module(MIGRATION)


@pytest.mark.parametrize(
    "representation", ["base64", "raw", "permissive_base64", "prefixed_logical"]
)
def test_migration_preserves_old_decoder_and_encrypts_all_rows(
    connector, representation
):
    migration = migration_module()
    logical = ("enc::" if representation == "prefixed_logical" else "") + needle()
    if representation == "raw":
        logical = "!" + logical + "!"
        value = logical
        # Use the exact old decoder semantics, not strict base64 validation.
        try:
            logical = base64.b64decode(value.encode("utf-8")).decode("utf-8")
        except Exception:
            pass
    else:
        value = base64.b64encode(logical.encode()).decode()
        if representation == "permissive_base64":
            value = "!" + value + "!"
    header = needle()
    changes = dict.fromkeys(OAUTH_FIELDS, value)
    changes["auth_header_value"] = header
    changes["deleted"] = True
    MCPConnector._base_manager.filter(pk=connector.pk).update(**changes)
    historical = (
        MigrationLoader(connection)
        .project_state(
            [
                ("falcon_ai", "0009_resync_builtin_skills"),
            ]
        )
        .apps
    )
    migration.encrypt_connector_credentials(historical, connection.schema_editor())
    stored = row(connector)
    for field in OAUTH_FIELDS:
        assert_encrypted(getattr(stored, field), logical)
    assert_encrypted(stored.auth_header_value, header)
    check(
        not migration.Migration.operations[0].reversible,
        "migration can restore plaintext",
    )


def test_migration_keeps_encrypted_header_and_empty_oauth(connector):
    migration = migration_module()
    header = encrypt_token(needle())
    MCPConnector._base_manager.filter(pk=connector.pk).update(auth_header_value=header)
    historical = (
        MigrationLoader(connection)
        .project_state(
            [
                ("falcon_ai", "0009_resync_builtin_skills"),
            ]
        )
        .apps
    )
    migration.encrypt_connector_credentials(historical, connection.schema_editor())
    stored = row(connector)
    check(stored.auth_header_value == header, "encrypted header changed")
    check(
        all(getattr(stored, field) == "" for field in OAUTH_FIELDS),
        "empty values changed",
    )


def test_refresh_exception_does_not_leak(connector):
    value = needle()
    connector.oauth_access_token = encrypt_token(needle())
    connector.oauth_token_expires_at = timezone.now() - timedelta(seconds=1)
    caught = None
    with (
        patch.object(
            oauth_client, "refresh_access_token", side_effect=RuntimeError(value)
        ),
        patch.object(oauth_client, "logger") as logger,
    ):
        try:
            oauth_client.get_valid_access_token(connector)
        except Exception as error:
            caught = error
    check(caught is not None, "refresh failure was ignored")
    check(value not in str(caught), "credential leaked in exception")
    check(value not in str(logger.mock_calls), "credential leaked in log")


def test_proxy_error_does_not_persist_or_return_exception_value(connector):
    value = needle()
    connector.oauth_access_token = encrypt_token(needle())
    proxy = MCPConnectorProxy()
    with (
        patch.object(proxy, "_post_with_fallback", side_effect=RuntimeError(value)),
        patch("ee.falcon_ai.mcp_proxy.logger") as logger,
    ):
        result = proxy.discover_tools_sync(connector)
    check(not result["success"], "upstream failure was ignored")
    check(value not in str(result), "credential leaked in result")
    check(value not in row(connector).last_error, "credential leaked at rest in error")
    check(value not in str(logger.mock_calls), "credential leaked in log")


@pytest.mark.parametrize(
    "field", ["credential_generation", "deleted", "organization_id"]
)
def test_writer_rejects_boundary_owned_columns(connector, field):
    from ee.falcon_ai.credentials import FalconCredentialError
    from ee.falcon_ai.oauth_registration import save_connector

    before = tuple(getattr(row(connector), f.attname) for f in connector._meta.fields)
    with pytest.raises(FalconCredentialError):
        save_connector(connector, {field: 0})
    after = tuple(getattr(row(connector), f.attname) for f in connector._meta.fields)
    check(after == before, "rejected column write changed row")


def test_writer_refuses_snapshot_write_route_change(connector):
    from django.db import router

    from ee.falcon_ai.credentials import FalconCredentialError
    from ee.falcon_ai.oauth_registration import registration_operation, save_connector

    before = row(connector).credential_generation
    with registration_operation(connector) as current:
        with patch.object(router, "db_for_write", return_value="forbidden_write_route"):
            with pytest.raises(FalconCredentialError):
                save_connector(current, {"last_error": "static test value"})
    check(row(connector).credential_generation == before, "route change wrote a row")
