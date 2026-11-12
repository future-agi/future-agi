"""Consumer contract for the reconnect migration's non-secret journal."""

from unittest.mock import patch

import pytest
from django.core.management.base import CommandError
from django.db import transaction

from accounts.models import OrgApiKey
from accounts.models.retirement_manifest import RetirementManifest
from accounts.services.retirement_733 import run_retirement, unjournaled_connections
from accounts.services.retirement_rows import fingerprint, journal, reverse_rows
from mcp_server.models import MCPConnection

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("prior_active", [True, False])
def test_migration_journal_is_consumed_without_guessing_prior_state(
    user, workspace, prior_active
):
    key = OrgApiKey._base_manager.create(
        organization=user.organization,
        type="mcp",
        enabled=False,
        retired_reason="733",
        prior_enabled=True,
    )
    connection = MCPConnection._base_manager.create(
        user=user,
        organization=user.organization,
        workspace=workspace,
        api_key=key,
        is_active=prior_active,
        reconnect_required=False,
    )
    fields = ("is_active", "reconnect_required")
    with transaction.atomic():
        connection.is_active, connection.reconnect_required = False, True
        connection.save(update_fields=[*fields, "updated_at"])
        journal(
            "mcp",
            connection,
            "reconnect",
            {"is_active": prior_active, "reconnect_required": False},
            fingerprint(connection, fields),
            confirmed=True,
        )
    assert unjournaled_connections() == 0
    assert reverse_rows(dry_run=True)["restored"] == 1
    connection.refresh_from_db()
    assert connection.reconnect_required and not connection.is_active
    assert reverse_rows()["restored"] == 1
    connection.refresh_from_db()
    assert connection.is_active is prior_active
    assert connection.reconnect_required is False


def test_missing_migration_journal_refuses_both_directions(user, workspace):
    key = OrgApiKey._base_manager.create(
        organization=user.organization,
        type="mcp",
        enabled=False,
        retired_reason="733",
        prior_enabled=True,
    )
    MCPConnection._base_manager.create(
        user=user,
        organization=user.organization,
        workspace=workspace,
        api_key=key,
        is_active=False,
        reconnect_required=True,
    )
    with patch(
        "accounts.services.retirement_733.gateway_retirement",
        return_value=lambda *a, **k: {},
    ):
        for reverse in (True, False):
            with pytest.raises(
                CommandError, match="Reconnect prior state is unavailable for 1"
            ):
                run_retirement(reverse=reverse)
    assert RetirementManifest.objects.count() == 0
