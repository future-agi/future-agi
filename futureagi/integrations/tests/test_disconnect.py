"""R3-H6-004: the DELETE route must erase locally and revoke after commit."""

import logging
import uuid
from unittest.mock import patch

import pytest
from django.db import router, transaction
from django.test import TestCase
from rest_framework import status

from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
)
from integrations.services.credentials import CredentialManager


@pytest.fixture
def slack_bot_token():
    return f"test-only-slack-{uuid.uuid4().hex}"


@pytest.fixture
def slack_connection(organization, workspace, user, slack_bot_token):
    return IntegrationConnection.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user,
        platform=IntegrationPlatform.SLACK,
        display_name="Slack disconnect fixture",
        host_url="https://slack.com",
        encrypted_credentials=CredentialManager.encrypt({"bot_token": slack_bot_token}),
        external_project_name="disconnect-test-team",
        status=ConnectionStatus.ACTIVE,
        status_message="Previous integration diagnostic",
        backfill_completed=True,
    )


def _assert_disconnected(connection):
    stored = IntegrationConnection.all_objects.using(
        router.db_for_write(IntegrationConnection)
    ).get(pk=connection.pk)
    assert stored.deleted is True
    assert stored.deleted_at is not None
    assert bytes(stored.encrypted_credentials) == b""
    assert stored.status_message is None
    assert not IntegrationConnection.no_workspace_objects.filter(
        pk=connection.pk
    ).exists()


@pytest.mark.api
@pytest.mark.django_db
class TestIntegrationDisconnectAPI:
    def test_slack_delete_revokes_captured_token_only_after_commit(
        self, auth_client, slack_connection, slack_bot_token
    ):
        alias = router.db_for_write(IntegrationConnection)
        with patch(
            "integrations.services.slack_service.slack_api",
            return_value={"ok": True, "revoked": True},
        ) as slack_api:
            with TestCase.captureOnCommitCallbacks(using=alias, execute=True) as callbacks:
                response = auth_client.delete(
                    f"/integrations/connections/{slack_connection.pk}/"
                )
                assert response.status_code == status.HTTP_200_OK
                assert response.json() == {"status": True, "result": {"deleted": True}}
                slack_api.assert_not_called()
                _assert_disconnected(slack_connection)

            assert len(callbacks) == 1
            slack_api.assert_called_once_with("auth.revoke", slack_bot_token, payload={})
            _assert_disconnected(slack_connection)

    @pytest.mark.parametrize("failure", ["exception", "not_revoked"])
    def test_slack_revocation_failure_does_not_fail_disconnect_or_expose_token(
        self, auth_client, slack_connection, slack_bot_token, caplog, failure
    ):
        alias = router.db_for_write(IntegrationConnection)
        with (
            caplog.at_level(logging.INFO, logger="integrations.services.disconnect"),
            patch(
                "integrations.services.slack_service.slack_api",
                return_value={"ok": True, "revoked": False},
            ) as slack_api,
        ):
            if failure == "exception":
                slack_api.side_effect = RuntimeError(f"Transport failed: {slack_bot_token}")
            with TestCase.captureOnCommitCallbacks(using=alias, execute=True) as callbacks:
                response = auth_client.delete(
                    f"/integrations/connections/{slack_connection.pk}/"
                )
                assert response.status_code == status.HTTP_200_OK
                assert response.json() == {"status": True, "result": {"deleted": True}}
                slack_api.assert_not_called()
                _assert_disconnected(slack_connection)

            assert len(callbacks) == 1
            slack_api.assert_called_once_with("auth.revoke", slack_bot_token, payload={})
            _assert_disconnected(slack_connection)

        revocation_records = [
            record
            for record in caplog.records
            if record.name == "integrations.services.disconnect"
        ]
        assert len(revocation_records) == 1
        assert revocation_records[0].provider == IntegrationPlatform.SLACK
        assert revocation_records[0].status == "REVOCATION_FAILED"
        assert slack_bot_token not in repr([record.__dict__ for record in caplog.records])
        assert slack_bot_token not in response.content.decode()

    def test_rolled_back_slack_delete_keeps_credentials_and_never_revokes(
        self, auth_client, slack_connection, slack_bot_token
    ):
        alias = router.db_for_write(IntegrationConnection)
        original_credentials = bytes(slack_connection.encrypted_credentials)
        with patch("integrations.services.slack_service.slack_api") as slack_api:
            with TestCase.captureOnCommitCallbacks(using=alias, execute=True) as callbacks:
                with transaction.atomic(using=alias):
                    response = auth_client.delete(
                        f"/integrations/connections/{slack_connection.pk}/"
                    )
                    assert response.status_code == status.HTTP_200_OK
                    _assert_disconnected(slack_connection)
                    slack_api.assert_not_called()
                    transaction.set_rollback(True, using=alias)

            assert callbacks == []
            slack_api.assert_not_called()

        stored = IntegrationConnection.all_objects.using(alias).get(pk=slack_connection.pk)
        assert stored.deleted is False
        assert stored.deleted_at is None
        assert bytes(stored.encrypted_credentials) == original_credentials
        assert CredentialManager.decrypt(bytes(stored.encrypted_credentials)) == {
            "bot_token": slack_bot_token
        }
        assert stored.status_message == "Previous integration diagnostic"

    @pytest.mark.parametrize(
        "platform",
        [platform for platform in IntegrationPlatform.values if platform != "slack"],
    )
    def test_non_slack_delete_remains_local_and_soft_deletes(
        self, auth_client, integration_connection, platform
    ):
        integration_connection.platform = platform
        integration_connection.save(update_fields=["platform"])
        alias = router.db_for_write(IntegrationConnection)
        with patch("integrations.services.slack_service.slack_api") as slack_api:
            with TestCase.captureOnCommitCallbacks(using=alias, execute=True):
                response = auth_client.delete(
                    f"/integrations/connections/{integration_connection.pk}/"
                )
                assert response.status_code == status.HTTP_200_OK
                assert response.json() == {"status": True, "result": {"deleted": True}}
                _assert_disconnected(integration_connection)
            slack_api.assert_not_called()
