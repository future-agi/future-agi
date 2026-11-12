"""The BL-LEDGER-2A integration items tfc owns (the interface's "Integration
items" section): the 60 s sweep of unacknowledged granted pushes and the R4
gateway reconciler beside the settlement drain in
``tfc/temporal/schedules/agentcc_managed.py``, and the unconfirmed-catalog
flag check at boot.

The ledger's own functions are patched here: their behaviour is BL-LEDGER's
and is tested in ``ee/usage/tests``. These tests pin only that the schedules
call them, and that a build without the ledger skips loudly.
"""

import sys
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.utils import timezone
from structlog.testing import capture_logs

from accounts.models.organization import Organization

reconcile = pytest.importorskip("ee.usage.services.reconcile")
managed_credits = pytest.importorskip("ee.usage.services.managed_credits")

from ee.usage.models.managed_credits import ManagedCreditAccount  # noqa: E402
from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY  # noqa: E402
from tfc.temporal.schedules import ALL_SCHEDULES, agentcc_managed  # noqa: E402

SCHEDULES = {s.schedule_id: s for s in agentcc_managed.AGENTCC_MANAGED_SCHEDULES}


# --- the schedules -------------------------------------------------------------


def test_the_drain_sweep_and_reconciler_are_scheduled_side_by_side():
    expected = {
        "agentcc-managed-settlement-drain": (
            "drain_managed_settlements_activity",
            30,
        ),
        "agentcc-managed-push-sweep": ("sweep_unacked_pushes_activity", 60),
        "agentcc-managed-gateway-reconcile": (
            "reconcile_managed_gateway_activity",
            3600,
        ),
    }
    assert {
        sid: (s.activity_name, s.interval_seconds) for sid, s in SCHEDULES.items()
    } == expected
    for schedule in SCHEDULES.values():
        assert schedule in ALL_SCHEDULES
        assert schedule.activity_name in _ACTIVITY_REGISTRY
        assert schedule.queue == "default"
    # Hourly: a missed run is caught up when the worker returns.
    assert SCHEDULES["agentcc-managed-gateway-reconcile"].catchup_window_seconds > 0


def test_the_sweep_runs_sweep_unacked_pushes():
    with patch.object(managed_credits, "sweep_unacked_pushes", return_value=4) as sweep:
        result = agentcc_managed.sweep_unacked_pushes_activity._original_func()

    sweep.assert_called_once_with()
    assert result == {"swept": 4}


@pytest.mark.django_db
def test_the_reconciler_checks_every_account_and_stamps_it(organization):
    other = Organization.objects.create(name="R4 other org")
    never = Organization.objects.create(name="R4 org with no account")
    stale = timezone.now() - timedelta(days=1)
    ManagedCreditAccount.objects.create(organization=organization)
    ManagedCreditAccount.objects.create(organization=other, last_reconciled_at=stale)
    ok = reconcile.InvariantResult("I5", True, 1)
    bad = reconcile.InvariantResult("I6", False, 3, ("gateway granted 1 != 0",))

    def check(org):
        return (ok, bad) if org.pk == other.pk else (ok,)

    with patch.object(reconcile, "check_gateway", side_effect=check) as checked:
        result = agentcc_managed.reconcile_managed_gateway_activity._original_func()

    assert sorted(str(c.args[0].pk) for c in checked.call_args_list) == sorted(
        [str(organization.pk), str(other.pk)]
    )
    assert str(never.pk) not in {str(c.args[0].pk) for c in checked.call_args_list}
    assert result == {"orgs": 2, "failed": 1, "errors": 0}
    for org in (organization, other):
        account = ManagedCreditAccount.objects.get(organization=org)
        assert account.last_reconciled_at > stale


@pytest.mark.django_db
def test_one_orgs_reconcile_error_never_stops_the_others(organization):
    other = Organization.objects.create(name="R4 healthy org")
    ManagedCreditAccount.objects.create(organization=organization)
    ManagedCreditAccount.objects.create(organization=other)
    ok = reconcile.InvariantResult("I5", True, 1)

    def check(org):
        if org.pk == organization.pk:
            raise RuntimeError("boom")
        return (ok,)

    with (
        patch.object(reconcile, "check_gateway", side_effect=check),
        capture_logs() as logs,
    ):
        result = agentcc_managed.reconcile_managed_gateway_activity._original_func()

    assert result == {"orgs": 1, "failed": 0, "errors": 1}
    assert ManagedCreditAccount.objects.get(organization=other).last_reconciled_at
    assert not ManagedCreditAccount.objects.get(
        organization=organization
    ).last_reconciled_at
    failed = [e for e in logs if e["event"] == "managed_gateway_reconcile_failed"]
    assert [e["org_id"] for e in failed] == [str(organization.pk)]


@pytest.mark.parametrize(
    ("activity", "module"),
    [
        ("sweep_unacked_pushes_activity", "ee.usage.services.managed_credits"),
        ("reconcile_managed_gateway_activity", "ee.usage.services.reconcile"),
    ],
)
def test_the_ledger_schedules_skip_loudly_without_ee(activity, module):
    with patch.dict(sys.modules, {module: None}), capture_logs() as logs:
        result = getattr(agentcc_managed, activity)._original_func()

    assert result == {}
    assert [(e["event"], e["log_level"]) for e in logs] == [
        ("managed_ledger_schedule_skipped", "warning")
    ]


# --- boot -------------------------------------------------------------------------


def test_boot_runs_the_unconfirmed_catalog_flag_check():
    from ee.usage.services import rating

    with patch.object(rating, "check_unconfirmed_flag") as check:
        apps.get_app_config("tfc").ready()

    check.assert_called_once_with()


@override_settings(BILLING_CATALOG_ALLOW_UNCONFIRMED=True, ENV_TYPE="production")
def test_boot_refuses_the_unconfirmed_catalog_flag_in_production():
    with pytest.raises(ImproperlyConfigured, match="BILLING_CATALOG_ALLOW_UNCONFIRMED"):
        apps.get_app_config("tfc").ready()


@override_settings(BILLING_CATALOG_ALLOW_UNCONFIRMED=False, ENV_TYPE="production")
def test_boot_is_quiet_with_the_flag_off():
    apps.get_app_config("tfc").ready()
