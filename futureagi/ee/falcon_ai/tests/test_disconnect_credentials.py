"""F5: synthetic credentials, write-alias assertions, transport-only doubles."""

import secrets
from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest
from django.db import router, transaction

from agentcc.services.credential_manager import encrypt_token
from ee.falcon_ai.models import MCPConnector
from integrations.models import IntegrationConnection, IntegrationPlatform
from integrations.services.credentials import CredentialManager

pytestmark = pytest.mark.django_db
MCP_FIELDS = (
    "auth_header_value",
    "oauth_client_secret",
    "oauth_access_token",
    "oauth_refresh_token",
    "oauth_code_verifier",
    "oauth_state",
)
PLATFORMS = ["mcp_" + auth for auth in ("none", "api_key", "bearer", "oauth")] + list(
    IntegrationPlatform.values
)
ENDPOINT = "https://mcp.example.invalid/revoke"


def check(condition, message):
    assert condition, message  # Never introspect a secret-bearing expression.


@pytest.fixture(params=PLATFORMS)
def connected(request, user, workspace, settings):
    settings.SLACK_API_URL = ""
    platform = request.param
    values = {field: secrets.token_urlsafe(32) for field in MCP_FIELDS}
    values["bot_token"] = "xoxb-" + secrets.token_urlsafe(32)
    if platform.startswith("mcp_"):
        obj = MCPConnector.no_workspace_objects.create(
            organization=user.organization,
            workspace=workspace,
            name="disconnect-" + secrets.token_hex(8),
            server_url="https://mcp.example.invalid/mcp",
            auth_type=platform.removeprefix("mcp_"),
            oauth_client_id="test-client",
            oauth_server_metadata={"revocation_endpoint": ENDPOINT},
            **{
                field: value if field in MCP_FIELDS[-2:] else encrypt_token(value)
                for field, value in values.items()
                if field in MCP_FIELDS
            },
        )
    else:
        obj = IntegrationConnection.no_workspace_objects.create(
            organization=user.organization,
            workspace=workspace,
            platform=platform,
            display_name="disconnect",
            host_url="https://provider.example.invalid",
            external_project_name="disconnect-" + secrets.token_hex(8),
            encrypted_credentials=CredentialManager.encrypt(
                {**values, "nested": {"private_key": secrets.token_urlsafe(32)}}
            ),
        )
    return obj, platform, values


def fresh(obj):
    alias = router.db_for_write(type(obj))
    return type(obj)._base_manager.using(alias).get(pk=obj.pk)


def cleared(obj):
    stored = fresh(obj)
    check(stored.deleted, "delete flag not persisted")
    check(stored.deleted_at is not None, "delete timestamp missing")
    if isinstance(stored, MCPConnector):
        check(all(not getattr(stored, f) for f in MCP_FIELDS), "credential retained")
    else:
        check(not stored.encrypted_credentials, "credential blob retained")


@contextmanager
def transport(failure=False):
    http = Mock()
    http.status_code = 200
    http.json.return_value = {"ok": True, "revoked": True}
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.post.return_value = http
    with (
        patch("tfc.utils.ssrf_guard.guarded_httpx_client", return_value=client),
        patch(
            "integrations.services.slack_service.requests.post", return_value=http
        ) as slack,
    ):
        if failure:
            # Deliberately toxic exception text must never reach a log/response.
            problem = RuntimeError(failure)
            client.post.side_effect = problem
            slack.side_effect = problem
        yield client.post, slack


def expected_call(platform, values, posts):
    mcp, slack = posts
    if platform == "mcp_oauth":
        check(mcp.call_count == 1 and not slack.called, "MCP revocation count")
        args, kwargs = mcp.call_args
        check(args == (ENDPOINT,), "wrong RFC 7009 endpoint")
        check(kwargs["data"]["token"] == values["oauth_refresh_token"], "wrong token")
        check(kwargs["data"]["token_type_hint"] == "refresh_token", "wrong hint")
        check(
            kwargs["data"]["client_secret"] == values["oauth_client_secret"],
            "wrong client",
        )
    elif platform == "slack":
        check(slack.call_count == 1 and not mcp.called, "Slack revocation count")
        args, kwargs = slack.call_args
        check(args == ("https://slack.com/api/auth.revoke",), "wrong Slack endpoint")
        check(
            kwargs["headers"]["Authorization"] == "Bearer " + values["bot_token"],
            "wrong bot",
        )
    else:
        check(not mcp.called and not slack.called, "unsupported provider contacted")


def test_disconnect_clears_atomically_then_revokes(
    connected, django_capture_on_commit_callbacks, caplog
):
    obj, platform, values = connected
    alias = router.db_for_write(type(obj))
    with transport() as posts, caplog.at_level("INFO"):
        with django_capture_on_commit_callbacks(using=alias, execute=True) as callbacks:
            with transaction.atomic(using=alias):
                obj.delete()
                cleared(obj)
                check(not any(p.called for p in posts), "revocation before commit")
        check(len(callbacks) == 1, "missing on_commit callback")
        expected_call(platform, values, posts)
        cleared(obj)
    records = [
        r for r in caplog.records if r.name == "integrations.services.disconnect"
    ]
    check(len(records) == 1, "missing value-free outcome")
    supported = platform in ("mcp_oauth", "slack")
    check(
        records[0].status
        == ("REVOCATION_SUCCEEDED" if supported else "REVOCATION_UNSUPPORTED"),
        "wrong outcome",
    )
    check(
        records[0].provider == ("mcp" if platform.startswith("mcp_") else platform),
        "wrong provider id",
    )
    check(not any(v in caplog.text for v in values.values()), "credential in logs")


def test_disconnect_failure_never_restores_credentials(
    connected, django_capture_on_commit_callbacks, caplog
):
    obj, platform, values = connected
    with transport(values["oauth_access_token"]) as posts, caplog.at_level("INFO"):
        with django_capture_on_commit_callbacks(execute=True):
            obj.delete()
        cleared(obj)
        expected_call(platform, values, posts)
    check(not any(v in caplog.text for v in values.values()), "exception value leaked")
    records = [
        r for r in caplog.records if r.name == "integrations.services.disconnect"
    ]
    check(len(records) == 1, "missing outcome")
    check(
        records[0].status
        == (
            "REVOCATION_FAILED"
            if platform in ("mcp_oauth", "slack")
            else "REVOCATION_UNSUPPORTED"
        ),
        "wrong failure outcome",
    )


def test_disconnect_rollback_preserves_credentials(
    connected, django_capture_on_commit_callbacks
):
    obj, _, _ = connected
    original = fresh(obj)
    with transport() as posts:
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            with transaction.atomic():
                obj.delete()
                cleared(obj)
                transaction.set_rollback(True)
        stored = fresh(obj)
        check(not stored.deleted, "rollback persisted delete")
        fields = (
            MCP_FIELDS if isinstance(obj, MCPConnector) else ("encrypted_credentials",)
        )
        check(
            all(getattr(stored, f) == getattr(original, f) for f in fields),
            "rollback lost credential",
        )
        check(
            not callbacks and not any(p.called for p in posts), "rollback revoked token"
        )


def test_replica_object_uses_fresh_write_alias(
    connected, django_capture_on_commit_callbacks
):
    obj, platform, values = connected
    alias = router.db_for_write(type(obj))
    # A real persisted object with a replica provenance and stale credentials.
    # There is intentionally no replica connection: touching it fails the test.
    obj._state.db = "f5_forbidden_replica"
    if isinstance(obj, MCPConnector):
        obj.oauth_refresh_token = encrypt_token(secrets.token_urlsafe(32))
    else:
        obj.encrypted_credentials = CredentialManager.encrypt({})
    with (
        patch.object(router, "db_for_write", return_value=alias) as write_route,
        transport() as posts,
        django_capture_on_commit_callbacks(using=alias, execute=True),
    ):
        obj.delete()
        cleared(obj)
    check(write_route.called, "write router bypassed")
    expected_call(platform, values, posts)


def test_delete_api_clears_credentials(
    connected, user, django_capture_on_commit_callbacks
):
    from rest_framework.test import APIRequestFactory, force_authenticate

    from ee.falcon_ai.views_connectors import MCPConnectorDetailView
    from integrations.views.integration_connection import IntegrationConnectionViewSet

    obj, _, _ = connected
    req = APIRequestFactory().delete("/disconnect/")
    force_authenticate(req, user=user)
    req.organization, req.workspace = obj.organization, obj.workspace
    with transport(), django_capture_on_commit_callbacks(execute=True):
        if isinstance(obj, MCPConnector):
            response = MCPConnectorDetailView.as_view()(req, connector_id=obj.pk)
            check(response.status_code == 204, "MCP delete contract changed")
        else:
            # Permission contracts have their own existing tests; this calls the
            # real destroy body with an already-authorized row, not a DB double.
            view = IntegrationConnectionViewSet()
            with patch.object(view, "get_object", return_value=obj):
                response = view.destroy(req)
            check(response.status_code == 200, "integration delete contract changed")
        cleared(obj)


def test_repeat_delete_does_not_revoke_twice(
    connected, django_capture_on_commit_callbacks
):
    obj, platform, values = connected
    with transport() as posts:
        with django_capture_on_commit_callbacks(execute=True):
            obj.delete()
            obj.delete()
        expected_call(platform, values, posts)
        cleared(obj)
