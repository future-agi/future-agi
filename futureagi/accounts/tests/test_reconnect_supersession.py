"""Reconnect supersession is durable provenance, not a missing journal."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import OrgApiKey, Workspace
from accounts.services.retirement_733 import run_retirement
from accounts.services.retirement_reconnect import unjournaled_connections
from accounts.services.retirement_rows import fingerprint, journal
from mcp_server.models import MCPConnection

pytestmark = pytest.mark.django_db


@pytest.fixture
def reconnect_rows(user):
    key = OrgApiKey._base_manager.create(
        organization=user.organization,
        type="mcp",
        enabled=False,
        retired_reason="733",
        prior_enabled=True,
    )

    def create():
        workspace = Workspace.objects.create(
            name=f"Reconnect test workspace {uuid4()}",
            organization=user.organization,
            created_by=user,
        )
        row = MCPConnection._base_manager.create(
            user=user,
            organization=user.organization,
            workspace=workspace,
            api_key=key,
            is_active=False,
            reconnect_required=True,
        )
        with transaction.atomic():
            manifest = journal(
                "mcp",
                row,
                "reconnect",
                {"is_active": True, "reconnect_required": False},
                fingerprint(row, ("is_active", "reconnect_required")),
                confirmed=True,
            )
        return row, manifest

    return create


def test_repeated_reverse_skips_superseded_and_restores_other_rows(reconnect_rows):
    changed, manifest = reconnect_rows()
    changed.save(update_fields=["updated_at"])
    with patch("accounts.services.retirement_733.gateway_retirement", MagicMock()):
        first = run_retirement(reverse=True)
        assert first["superseded"] == 1 and first["restored"] == 0
        manifest.refresh_from_db()
        assert manifest.superseded_by == "object_changed"
        untouched = fingerprint(changed, ("is_active", "reconnect_required"))
        assert unjournaled_connections() == 0
        other, other_manifest = reconnect_rows()
        preview = run_retirement(reverse=True, dry_run=True)
        assert preview["superseded"] == 1 and preview["restored"] == 1
        other.refresh_from_db()
        assert not other.is_active and other.reconnect_required
        second = run_retirement(reverse=True)
        assert second["superseded"] == 1 and second["restored"] == 1
        third = run_retirement(reverse=True)
        assert third["superseded"] == 1 and third["restored"] == 0
    changed.refresh_from_db()
    manifest.refresh_from_db()
    other.refresh_from_db()
    other_manifest.refresh_from_db()
    assert fingerprint(changed, ("is_active", "reconnect_required")) == untouched
    assert manifest.superseded_by == "object_changed" and manifest.reverted_at is None
    assert other.is_active and not other.reconnect_required
    assert other_manifest.reverted_at is not None and not other_manifest.superseded_by


@pytest.mark.parametrize("damage", ["missing", "incomplete", "unconfirmed", "reverted"])
def test_supersession_never_hides_missing_provenance(reconnect_rows, damage):
    changed, superseded = reconnect_rows()
    superseded.superseded_by = "object_changed"
    superseded.save(update_fields=["superseded_by"])
    other, invalid = reconnect_rows()
    if damage == "missing":
        invalid.delete()
    elif damage == "incomplete":
        invalid.applied_state = {}
        invalid.save(update_fields=["applied_state"])
    elif damage == "unconfirmed":
        invalid.confirmed = False
        invalid.save(update_fields=["confirmed"])
    else:
        invalid.reverted_at = timezone.now()
        invalid.save(update_fields=["reverted_at"])
    assert unjournaled_connections() == 1
    with patch("accounts.services.retirement_733.gateway_retirement", MagicMock()):
        for reverse in (False, True):
            with pytest.raises(CommandError, match="prior state is unavailable for 1"):
                run_retirement(reverse=reverse)
    for row in (changed, other):
        row.refresh_from_db()
        assert not row.is_active and row.reconnect_required
    superseded.refresh_from_db()
    assert superseded.superseded_by == "object_changed"
    assert superseded.reverted_at is None


def test_incomplete_superseded_journal_still_refuses(reconnect_rows):
    _, manifest = reconnect_rows()
    manifest.superseded_by = "object_changed"
    manifest.prior_state = {}
    manifest.save(update_fields=["superseded_by", "prior_state"])
    assert unjournaled_connections() == 1
    with patch("accounts.services.retirement_733.gateway_retirement", MagicMock()):
        with pytest.raises(CommandError, match="prior state is unavailable for 1"):
            run_retirement(reverse=True)
