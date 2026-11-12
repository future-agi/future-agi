"""Deliver committed wallet threshold crossings through the existing mail path."""

from decimal import Decimal

import structlog

from accounts.models.organization_membership import OrganizationMembership
from tfc.constants.levels import Level
from tfc.utils.email import email_delivery_configured, email_helper

logger = structlog.get_logger(__name__)


def send_low_balance_alert(org, balance_micros: int, threshold_micros: int) -> None:
    """Only active organization billing admins, never workspace-only admins.

    The locked ledger write decides the crossing and registers this on commit
    only for a newly inserted entry. Replays, further below-threshold spends
    and rolled-back writes therefore do not send another alert. Funding back
    above the threshold naturally arms the next downward crossing.
    """
    if not email_delivery_configured():
        logger.warning(
            "managed_credit_alert_unavailable",
            org_id=str(org.pk),
            reason="email_unconfigured",
        )
        return
    memberships = OrganizationMembership.no_workspace_objects.select_related(
        "user"
    ).filter(
        organization=org,
        is_active=True,
        user__is_active=True,
    )
    recipients = sorted(
        {
            membership.user.email
            for membership in memberships
            if membership.level_or_legacy >= Level.ADMIN and membership.user.email
        }
    )
    if not recipients:
        logger.warning(
            "managed_credit_alert_unavailable",
            org_id=str(org.pk),
            reason="no_billing_admin",
        )
        return
    email_helper(
        "AI Gateway credit balance is low",
        "usage/email/low_balance.html",
        {
            "organization_name": org.name,
            "balance_usd": f"{Decimal(balance_micros) / 1_000_000:.2f}",
            "threshold_usd": f"{Decimal(threshold_micros) / 1_000_000:.2f}",
        },
        recipients,
    )
