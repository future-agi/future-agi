"""Retirement journals retain non-secret prior state and respect later edits."""

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from accounts.models import OrgApiKey

pytestmark = pytest.mark.django_db


def legacy_key(user, **kwargs):
    return OrgApiKey._base_manager.create(
        organization=user.organization,
        type="system",
        name="test-only-retired",
        **kwargs,
    )


def test_fa_forward_reverse_repeat_is_exact_and_dry_run_is_readonly(user):
    from accounts.models.retirement_manifest import RetirementManifest
    from accounts.services.retirement_rows import retire_fa_rows, reverse_rows

    row = legacy_key(user, enabled=True)
    disabled = legacy_key(user, enabled=False, deleted=True)
    before = row.updated_at
    assert retire_fa_rows(dry_run=True) == 1
    row.refresh_from_db()
    assert row.enabled and row.updated_at == before
    assert RetirementManifest.objects.count() == 0
    with patch("accounts.services.retirement_rows.publish_key_revocation"):
        retire_fa_rows()
        retire_fa_rows()
    row.refresh_from_db()
    assert not row.enabled
    manifest = RetirementManifest.objects.get(service="fa", object_id=str(row.pk))
    assert manifest.prior_state["enabled"] is True
    assert set(manifest.prior_state) == {
        "enabled",
        "retired_at",
        "retired_reason",
        "prior_enabled",
    }
    assert RetirementManifest.objects.count() == 1
    assert reverse_rows(dry_run=True)["restored"] == 1
    row.refresh_from_db()
    assert row.enabled is False
    assert reverse_rows()["restored"] == 1
    row.refresh_from_db()
    disabled.refresh_from_db()
    assert row.enabled is True and row.retired_reason is None
    assert disabled.enabled is False and disabled.deleted is True
    assert reverse_rows()["restored"] == 0
    with patch("accounts.services.retirement_rows.publish_key_revocation"):
        retire_fa_rows()
    assert reverse_rows()["restored"] == 1


def test_customer_change_is_never_reversed(user):
    from accounts.models.retirement_manifest import RetirementManifest
    from accounts.services.retirement_rows import retire_fa_rows, reverse_rows

    row = legacy_key(user, enabled=True)
    with patch("accounts.services.retirement_rows.publish_key_revocation"):
        retire_fa_rows()
    OrgApiKey._base_manager.filter(pk=row.pk).update(
        deleted=True, updated_at=timezone.now()
    )
    result = reverse_rows()
    assert result["superseded"] == 1 and result["restored"] == 0
    row.refresh_from_db()
    assert row.deleted is True and row.enabled is False
    assert RetirementManifest.objects.get(object_id=str(row.pk)).superseded_by


def test_gateway_adapter_preserves_first_prior_and_commits_with_the_key(user):
    from accounts.models.retirement_manifest import RetirementManifest
    from accounts.services.retirement_733 import gateway_adapter
    from agentcc.models import AgentccAPIKey

    row = AgentccAPIKey._base_manager.create(
        organization=user.organization,
        user=user,
        name="console-batch-test",
        gateway_key_id="test-only-gateway",
        status="active",
    )
    prior = {"status": "active", "expires_at": None}
    with pytest.raises(RuntimeError), transaction.atomic():
        row.status = "revoked"
        row.save(update_fields=["status", "updated_at"])
        gateway_adapter().record(key=row, prior_state=prior, confirmed=False)
        raise RuntimeError("rollback")
    row.refresh_from_db()
    assert row.status == "active" and not RetirementManifest.objects.exists()
    with transaction.atomic():
        row.status = "revoked"
        row.save(update_fields=["status", "updated_at"])
        gateway_adapter().record(key=row, prior_state=prior, confirmed=False)
        gateway_adapter().record(
            key=row,
            prior_state={"status": "revoked", "expires_at": None},
            confirmed=True,
        )
    manifest = RetirementManifest.objects.get(service="gateway")
    assert manifest.prior_state == prior and manifest.confirmed


def test_command_is_registered_and_l4_absence_fails_before_mutation(user):
    from django.core.management.base import CommandError

    row = legacy_key(user, enabled=True)
    with patch(
        "accounts.services.retirement_733.gateway_retirement",
        side_effect=CommandError("Gateway retirement is not installed"),
    ):
        with pytest.raises(CommandError, match="not installed"):
            call_command("retire_org_keys_733", stdout=StringIO())
    row.refresh_from_db()
    assert row.enabled is True
