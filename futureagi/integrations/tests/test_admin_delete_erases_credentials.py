"""Deleting connections from the admin erases their stored credentials.

The admin's "delete selected" action disconnects each selected connection the
way the API does: the stored credential is erased with the delete, the row is
kept as deleted, and a Slack authorization is revoked once the delete commits.
"""

import uuid
from unittest.mock import patch

import pytest
from django.contrib import admin
from django.db import router
from django.test import TestCase

from integrations.admin import IntegrationConnectionAdmin
from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
)
from integrations.services.credentials import CredentialManager

pytestmark = pytest.mark.django_db


def _made_up():
    return f"test-only-{uuid.uuid4().hex}"


def _connection(organization, workspace, user, platform, name, credentials):
    return IntegrationConnection.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user,
        platform=platform,
        display_name=name,
        host_url="https://provider.example.invalid",
        encrypted_credentials=CredentialManager.encrypt(credentials),
        external_project_name=name,
        status=ConnectionStatus.ACTIVE,
    )


def _stored(connection):
    alias = router.db_for_write(IntegrationConnection)
    return IntegrationConnection.all_objects.using(alias).get(pk=connection.pk)


def _connection_admin():
    return IntegrationConnectionAdmin(IntegrationConnection, admin.site)


def _delete_selected(connections):
    selected = IntegrationConnection.no_workspace_objects.filter(
        pk__in=[connection.pk for connection in connections]
    )
    _connection_admin().delete_queryset(None, selected)


def _is_disconnected(connection):
    stored = _stored(connection)
    return stored.deleted is True and bytes(stored.encrypted_credentials) == b""


def test_delete_selected_erases_the_credential_of_every_selected_connection(
    organization, workspace, user
):
    first = _connection(
        organization,
        workspace,
        user,
        IntegrationPlatform.LANGFUSE,
        "first",
        {"api_key": _made_up()},
    )
    second = _connection(
        organization,
        workspace,
        user,
        IntegrationPlatform.DATADOG,
        "second",
        {"api_key": _made_up()},
    )
    kept = _connection(
        organization,
        workspace,
        user,
        IntegrationPlatform.LANGFUSE,
        "kept",
        {"api_key": _made_up()},
    )

    _delete_selected([first, second])

    assert _is_disconnected(first)
    assert _is_disconnected(second)
    untouched = _stored(kept)
    assert untouched.deleted is False
    assert bytes(untouched.encrypted_credentials) != b""


def test_delete_selected_revokes_a_slack_authorization_after_the_delete_commits(
    organization, workspace, user
):
    bot_token = _made_up()
    slack = _connection(
        organization,
        workspace,
        user,
        IntegrationPlatform.SLACK,
        "team",
        {"bot_token": bot_token},
    )
    alias = router.db_for_write(IntegrationConnection)

    with patch(
        "integrations.services.slack_service.slack_api",
        return_value={"ok": True, "revoked": True},
    ) as slack_api:
        with TestCase.captureOnCommitCallbacks(using=alias, execute=True) as callbacks:
            _delete_selected([slack])
            slack_api.assert_not_called()
        assert len(callbacks) == 1
        slack_api.assert_called_once_with("auth.revoke", bot_token, payload={})

    assert _is_disconnected(slack)


def test_deleting_one_connection_in_the_admin_erases_its_credential(
    organization, workspace, user
):
    connection = _connection(
        organization,
        workspace,
        user,
        IntegrationPlatform.LANGFUSE,
        "single",
        {"api_key": _made_up()},
    )

    _connection_admin().delete_model(None, connection)

    assert _is_disconnected(connection)
