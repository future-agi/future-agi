"""J11-W2-003: a saved threshold delivers one email per committed crossing."""

from decimal import Decimal

import pytest
from django.db import transaction

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from agentcc.views.managed_credits import save_settings
from ee.usage.services import balance_cache
from ee.usage.services import managed_credits as mc
from ee.usage.tests.ledger_support import spend_record
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = [pytest.mark.integration, pytest.mark.django_db]
MICROS = 1_000_000


@pytest.fixture(autouse=True)
def isolated_delivery(monkeypatch, settings, mailoutbox):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.APP_BASE_URL = "https://billing.example.test"
    monkeypatch.setattr(mc, "request_push", lambda *args, **kwargs: None)
    monkeypatch.setattr(balance_cache, "write", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        balance_cache.balance_changed, "send_robust", lambda **kwargs: []
    )


def _fund_and_save(org, user, threshold=Decimal("5")):
    mc.grant(
        org, 10 * MICROS, idempotency_key="w2-alert-funding", actor=None, note="test"
    )
    save_settings(
        mc,
        org,
        {"low_balance_threshold_usd": threshold, "auto_reload": {"enabled": False}},
        Decimal("25"),
        True,
        actor=user,
    )


def _spend(org, request_id, amount, capture):
    with capture(execute=True):
        return mc.settle_spend(spend_record(org, request_id, billed=amount))


def _member(
    org,
    tag,
    *,
    level=None,
    role=OrganizationRoles.MEMBER,
    active=True,
    user_active=True,
):
    person = User.objects.create_user(
        email=f"{tag}@example.test",
        name=tag,
        organization=org,
        organization_role=role,
        is_active=user_active,
    )
    OrganizationMembership.no_workspace_objects.update_or_create(
        user=person,
        organization=org,
        defaults={"role": role, "level": level, "is_active": active},
    )
    return person


def test_j11_saved_threshold_emails_only_active_org_billing_admins(
    organization,
    user,
    mailoutbox,
    django_capture_on_commit_callbacks,
):
    admin = _member(organization, "w2-admin", level=Level.ADMIN)
    legacy = _member(organization, "w2-legacy-admin", role=OrganizationRoles.ADMIN)
    _member(organization, "w2-member", level=Level.MEMBER)
    _member(
        organization, "w2-demoted", level=Level.MEMBER, role=OrganizationRoles.ADMIN
    )
    _member(organization, "w2-inactive", level=Level.ADMIN, active=False)
    _member(organization, "w2-inactive-user", level=Level.ADMIN, user_active=False)
    other = Organization.objects.create(name="Other alert organization")
    _member(other, "w2-other-owner", level=Level.OWNER)
    _fund_and_save(organization, user)

    with django_capture_on_commit_callbacks(execute=True):
        mc.settle_spend(
            spend_record(organization, "w2-alert-crossing", billed=6 * MICROS)
        )
        assert mailoutbox == [], "no mail before transaction commit"

    recipients = {address for message in mailoutbox for address in message.to}
    assert recipients == {user.email, admin.email, legacy.email}
    for message in mailoutbox:
        assert "low" in message.subject.lower()
        assert organization.name in message.body
        assert "$4.00" in message.body and "$5.00" in message.body
        assert "https://billing.example.test/dashboard/settings/billing" in message.body


def test_j11_crossing_deduplicates_replays_and_rearms_after_refunding(
    organization,
    user,
    mailoutbox,
    django_capture_on_commit_callbacks,
):
    capture = django_capture_on_commit_callbacks
    _fund_and_save(organization, user)
    _spend(organization, "w2-at-threshold", 5 * MICROS, capture)
    assert mailoutbox == []  # "below", not "at"
    _, created = _spend(organization, "w2-below-threshold", MICROS, capture)
    assert created and len(mailoutbox) == 1
    _, created = _spend(organization, "w2-below-threshold", MICROS, capture)
    assert not created and len(mailoutbox) == 1
    _spend(organization, "w2-still-below", MICROS, capture)
    assert len(mailoutbox) == 1
    with capture(execute=True):
        mc.refund(organization, "w2-at-threshold", reason="test", actor=user)
    assert len(mailoutbox) == 1
    _spend(organization, "w2-second-crossing", 4 * MICROS, capture)
    assert len(mailoutbox) == 2


def test_j11_rolled_back_crossing_never_emails(
    organization,
    user,
    mailoutbox,
    django_capture_on_commit_callbacks,
):
    _fund_and_save(organization, user)
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError, match="rollback"):
            with transaction.atomic():
                mc.settle_spend(
                    spend_record(organization, "w2-rollback", billed=6 * MICROS)
                )
                raise RuntimeError("rollback")
    assert mailoutbox == []
    assert mc.ledger_balance(organization).balance_micros == 10 * MICROS


def test_j11_empty_threshold_sends_no_alert(
    organization,
    user,
    mailoutbox,
    django_capture_on_commit_callbacks,
):
    _fund_and_save(organization, user, threshold=None)
    _spend(organization, "w2-no-alert", 6 * MICROS, django_capture_on_commit_callbacks)
    assert mailoutbox == []
