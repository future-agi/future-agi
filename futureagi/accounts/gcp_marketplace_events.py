"""Handlers for Cloud Marketplace Pub/Sub lifecycle events.

Plain Django, no Temporal. The consumer activity is a thin caller.

Two rules hold everywhere:

1. Never trust the message body. It carries an event type and an id, nothing
   more, so every handler re-fetches current state from the Procurement API.
   A redelivered message from an hour ago then still produces today's answer.

2. Only terminal events change access. `*_REQUESTED` and `*_PENDING_*` are
   notifications: the customer has asked for something that has not happened
   yet. Acting on them revokes access people have paid for.
"""

import structlog
from django.db import transaction

from accounts.models.gcp_marketplace import (
    IN_SERVICE_STATES,
    GCPMarketplaceAccount,
    GCPMarketplaceEntitlement,
    GCPMarketplaceEntitlementState,
    GCPMarketplaceProcessedEvent,
    GCPMarketplaceUsageCheckpoint,
)
from accounts.services.gcp_procurement import gcp_procurement, resolve_plan

logger = structlog.get_logger(__name__)

try:
    from ee.usage.models.usage import (
        BillingMethodChoices,
        OrganizationSubscription,
        PlanChoices,
    )
except ImportError:
    BillingMethodChoices = None
    OrganizationSubscription = None
    PlanChoices = None


def _parse_time(value):
    if not value:
        return None
    from django.utils.dateparse import parse_datetime

    return parse_datetime(value)


def _is_not_found(exc: BaseException) -> bool:
    from googleapiclient.errors import HttpError

    return isinstance(exc, HttpError) and exc.status_code == 404


def _subject_id(payload: dict) -> str:
    for key in ("entitlement", "account"):
        subject = payload.get(key) or {}
        if subject.get("id"):
            return subject["id"]
    return ""


def _refresh_plan_caches(organization_id) -> None:
    """Every write to OrganizationSubscription.plan owes these two calls.

    Without them the resolver serves the previous plan for up to the cache TTL,
    so a customer who just paid keeps getting 402s until it expires.

    Deferred to commit because process_event runs handlers inside a transaction.
    publish_quotas_for_org reads the plan back and caches what it finds, so
    running it early would repopulate the cache from a write that may still roll
    back, and leave the stale value there for the rest of the TTL.
    """

    def _refresh():
        from ee.usage.services.entitlements import invalidate_plan_caches
        from ee.usage.services.metering import publish_quotas_for_org

        try:
            invalidate_plan_caches(str(organization_id))
            publish_quotas_for_org(str(organization_id))
        except Exception:
            # The plan is already committed. Raising here would fail an event
            # that succeeded and, outside a handler, abort a reconcile run for
            # every org after this one. The cache expires on its own.
            logger.exception(
                "gcp_marketplace_plan_cache_refresh_failed",
                organization_id=str(organization_id),
            )

    transaction.on_commit(_refresh)


def _apply_plan(entitlement_row: GCPMarketplaceEntitlement) -> None:
    """Set the organization's plan from the entitlement's marketplace plan."""
    if OrganizationSubscription is None or not entitlement_row.organization_id:
        return

    try:
        plan, interval = resolve_plan(entitlement_row.plan_id)
    except ValueError:
        # Raising leaves the message unacked and Google redelivers a plan we
        # still cannot map, for ever. The mapping needs a human.
        logger.error(
            "gcp_marketplace_unmapped_plan",
            entitlement_id=entitlement_row.entitlement_id,
            plan_id=entitlement_row.plan_id,
        )
        return

    OrganizationSubscription.objects.filter(
        organization_id=entitlement_row.organization_id
    ).update(
        plan=plan,
        billing_interval=interval,
        billing_method=BillingMethodChoices.GCP_MARKETPLACE,
    )
    _refresh_plan_caches(entitlement_row.organization_id)
    logger.info(
        "gcp_marketplace_plan_applied",
        entitlement_id=entitlement_row.entitlement_id,
        plan=plan,
        interval=interval,
    )


def _downgrade_org_to_free(organization_id) -> None:
    """Drop to free and hand billing back. Customer data is untouched."""
    if OrganizationSubscription is None or not organization_id:
        return

    OrganizationSubscription.objects.filter(organization_id=organization_id).update(
        plan=PlanChoices.FREE,
        billing_method=BillingMethodChoices.CARD,
    )
    _refresh_plan_caches(organization_id)


def _downgrade_to_free(entitlement_row: GCPMarketplaceEntitlement) -> None:
    _downgrade_org_to_free(entitlement_row.organization_id)
    logger.info(
        "gcp_marketplace_downgraded_to_free",
        entitlement_id=entitlement_row.entitlement_id,
    )


SECOND_ENTITLEMENT_REASON = (
    "This organization already has an active Future AGI subscription. "
    "Change plans from the existing subscription instead of buying a second one."
)


def other_in_service_entitlement(
    row: GCPMarketplaceEntitlement,
) -> GCPMarketplaceEntitlement | None:
    """Another live entitlement on the same organization, newest first.

    Usage is metered per organization, so a second entitlement cannot be
    billed separately. One subscription per organization; when there are
    more, the newest purchase is the one the customer is on (see
    handle_entitlement_active and billable_entitlements).
    """
    if not row.organization_id:
        return None
    return (
        GCPMarketplaceEntitlement.objects.filter(
            organization_id=row.organization_id, status__in=IN_SERVICE_STATES
        )
        .exclude(pk=row.pk)
        .order_by("-effective_at", "-created_at")
        .first()
    )


def reject_duplicate_entitlement(row: GCPMarketplaceEntitlement) -> bool:
    """Reject `row` if its organization already has a live entitlement."""
    existing = other_in_service_entitlement(row)
    if existing is None:
        return False
    logger.warning(
        "gcp_marketplace_duplicate_entitlement_rejected",
        entitlement_id=row.entitlement_id,
        existing_entitlement_id=existing.entitlement_id,
        organization_id=str(row.organization_id),
    )
    gcp_procurement.reject_entitlement(row.entitlement_id, SECOND_ENTITLEMENT_REASON)
    return True


def sync_entitlement(entitlement_id: str) -> GCPMarketplaceEntitlement | None:
    """Fetch an entitlement from Google and mirror it locally.

    Returns None when the incoming state is older than what we hold: Pub/Sub
    gives no ordering, so a delayed event can otherwise overwrite newer state.
    """
    remote = gcp_procurement.get_entitlement(entitlement_id)

    update_time = _parse_time(remote.get("updateTime"))
    account_id = gcp_procurement.bare_id(remote.get("account", ""))

    existing = GCPMarketplaceEntitlement.objects.filter(
        entitlement_id=entitlement_id
    ).first()
    if (
        existing
        and existing.google_update_time
        and update_time
        and existing.google_update_time > update_time
    ):
        logger.info(
            "gcp_marketplace_stale_event_ignored", entitlement_id=entitlement_id
        )
        return None

    account = GCPMarketplaceAccount.objects.filter(
        procurement_account_id=account_id
    ).first()

    defaults = {
        "account": account,
        "organization": account.organization if account else None,
        "plan_id": remote.get("plan", ""),
        "new_pending_plan": remote.get("newPendingPlan", "") or "",
        "status": remote.get("state", ""),
        "usage_reporting_id": remote.get("usageReportingId", "") or "",
        "effective_at": _parse_time(remote.get("createTime")),
        "expires_at": _parse_time(remote.get("subscriptionEndTime")),
        "offer": remote.get("offer", "") or "",
        "offer_end_time": _parse_time(remote.get("offerEndTime")),
        "cancellation_reason": remote.get("cancellationReason", "") or "",
        "google_update_time": update_time,
        "raw_payload": remote,
    }

    row, _ = GCPMarketplaceEntitlement.objects.update_or_create(
        entitlement_id=entitlement_id, defaults=defaults
    )
    return row


# ── handlers ──────────────────────────────────────────────────────────────


def handle_account_active(payload: dict) -> None:
    account_id = _subject_id(payload)
    remote = gcp_procurement.get_account(account_id)
    GCPMarketplaceAccount.objects.filter(procurement_account_id=account_id).update(
        state=remote.get("state", ""), raw_payload=remote
    )


def handle_account_deleted(payload: dict) -> None:
    """Unlink, never delete. This ends a billing relationship, not a customer."""
    account_id = _subject_id(payload)
    account = GCPMarketplaceAccount.objects.filter(
        procurement_account_id=account_id
    ).first()
    if account is None:
        return

    # Handed back before the link goes, or the org keeps a Marketplace billing
    # method with no Marketplace behind it and nothing bills it at all.
    _downgrade_org_to_free(account.organization_id)

    account.organization = None
    account.save(update_fields=["organization", "updated_at"])
    logger.info("gcp_marketplace_account_deleted", account_id=account_id)


def handle_entitlement_creation_requested(payload: dict) -> None:
    """Approve, or hold it if sign-up has not completed yet.

    An entitlement cannot be approved before its account is approved, and the
    account is only approved once the customer has a User with us. Anything that
    arrives first is stored and approve_pending_entitlements picks it up.
    """
    row = sync_entitlement(_subject_id(payload))
    if row is None:
        return

    if row.status != GCPMarketplaceEntitlementState.ACTIVATION_REQUESTED:
        # Already approved. approve runs inside the handler's transaction, so a
        # lost response rolls back and the message redelivers; the state just
        # fetched from Google, not the message, decides whether to call again.
        logger.info(
            "gcp_marketplace_entitlement_approval_not_pending",
            entitlement_id=row.entitlement_id,
            status=row.status,
        )
        return

    if reject_duplicate_entitlement(row):
        return

    if not row.account_id or not row.account.approved_at:
        logger.info(
            "gcp_marketplace_entitlement_held_for_signup",
            entitlement_id=row.entitlement_id,
        )
        return

    gcp_procurement.approve_entitlement(row.entitlement_id)


def handle_entitlement_active(payload: dict) -> None:
    """Billing starts here. usage_reporting_id is captured by sync_entitlement."""
    row = sync_entitlement(_subject_id(payload))
    if row is None:
        return
    _activate(row)


def _activate(row: GCPMarketplaceEntitlement) -> None:
    """Put the organization on the entitlement's plan. Google's state decides.

    Shared by the ENTITLEMENT_ACTIVE event and the hourly pending pass, which
    catches an activation whose event never arrived. The plan follows the
    state just fetched from Google, never the message: a row Google still
    shows awaiting activation gets nothing, and the pending pass revisits it.
    """
    if row.status not in IN_SERVICE_STATES:
        logger.warning(
            "gcp_marketplace_activation_not_in_service_at_google",
            entitlement_id=row.entitlement_id,
            status=row.status,
        )
        return
    _apply_plan(row)

    # Under automatic offer approval nothing rejects a second purchase before
    # it activates. The customer bought this one, so it wins: the plan above
    # and billable_entitlements both follow it. The older one keeps costing
    # the customer at Google until it is cancelled, which needs a human.
    overlap = other_in_service_entitlement(row)
    if overlap is not None:
        logger.error(
            "gcp_marketplace_multiple_in_service_entitlements",
            entitlement_id=row.entitlement_id,
            superseded_entitlement_id=overlap.entitlement_id,
            organization_id=str(row.organization_id),
        )

    if not row.usage_reporting_id:
        # The plan is applied and the customer is consuming, but nothing can
        # be billed without a consumer id and no further event is owed to us.
        # reconcile_entitlement_plans re-fetches this hourly until it appears.
        logger.error(
            "gcp_marketplace_missing_usage_reporting_id",
            entitlement_id=row.entitlement_id,
        )


def handle_plan_change_requested(payload: dict) -> None:
    """Approve the pending plan. Access does not change until PLAN_CHANGED."""
    row = sync_entitlement(_subject_id(payload))
    if row is None:
        return
    if row.status != GCPMarketplaceEntitlementState.PENDING_PLAN_CHANGE_APPROVAL:
        # Same guard as handle_entitlement_creation_requested: a redelivery
        # after a lost approve response must not approve twice.
        logger.info(
            "gcp_marketplace_plan_change_approval_not_pending",
            entitlement_id=row.entitlement_id,
            status=row.status,
        )
        return
    if not row.new_pending_plan:
        logger.warning(
            "gcp_marketplace_plan_change_without_pending_plan",
            entitlement_id=row.entitlement_id,
        )
        return
    gcp_procurement.approve_plan_change(row.entitlement_id, row.new_pending_plan)


def handle_plan_changed(payload: dict) -> None:
    """The change has taken effect. Only now does the plan move."""
    row = sync_entitlement(_subject_id(payload))
    if row is None:
        return
    _apply_plan(row)


def _report_final_window_after_commit(row: GCPMarketplaceEntitlement) -> None:
    """Bill the tail after the handler commits, never inside it.

    A report is a charge and cannot be rolled back, so a commit failure after
    the call would discard the checkpoints and the dedupe row while the charge
    stood, and the redelivered event would bill the same window again.
    """

    def _report():
        from accounts.gcp_marketplace_usage import report_final_window

        try:
            report_final_window(row)
        except Exception:
            # Raising after commit acks nothing and cannot undo the downgrade.
            # Not lost: the checkpoints hold the snapshot, and the hourly sweep
            # resends any left FAILED. An unknown outcome stays PENDING for
            # reconcile_usage, as everywhere else.
            logger.exception(
                "gcp_marketplace_final_usage_report_failed",
                entitlement_id=row.entitlement_id,
            )

    transaction.on_commit(_report)


def handle_entitlement_cancelled(payload: dict) -> None:
    entitlement_id = _subject_id(payload)
    # What this row was before Google's cancellation overwrote it. Whether the
    # tail is billed depends on it, and sync_entitlement discards it.
    was_live = (
        GCPMarketplaceEntitlement.objects.filter(
            entitlement_id=entitlement_id, status__in=IN_SERVICE_STATES
        ).exists()
    )
    row = sync_entitlement(entitlement_id)
    if row is None:
        return
    _settle_left_service(row, was_live=was_live)


def _settle_left_service(row: GCPMarketplaceEntitlement, *, was_live: bool) -> None:
    """The entitlement is no longer in service: hand access back, bill the tail.

    Shared by the cancellation event and the hourly reconcile, which catches
    the same transition when the event never arrived (lost past retention or
    dead-lettered) or when there is no event at all, as with a suspension.
    """
    # Access first: a billing failure must never leave a cancelled customer on
    # a paid plan. But the organization may already be on another entitlement:
    # a customer who cancels one plan and buys another, or the two events
    # arriving in the wrong order since Pub/Sub does not order them. Dropping
    # to free then would take away a subscription that is live and paid for.
    survivor = other_in_service_entitlement(row)
    if survivor is None:
        _downgrade_to_free(row)
    else:
        _apply_plan(survivor)
        logger.info(
            "gcp_marketplace_cancelled_org_stays_on_other_entitlement",
            entitlement_id=row.entitlement_id,
            surviving_entitlement_id=survivor.entitlement_id,
        )

    if was_live or GCPMarketplaceUsageCheckpoint.objects.filter(entitlement=row).exists():
        _report_final_window_after_commit(row)
    else:
        logger.info(
            "gcp_marketplace_final_usage_skipped_never_live",
            entitlement_id=row.entitlement_id,
        )


def handle_entitlement_renewed(payload: dict) -> None:
    """Moves the billing period boundary. Nothing else reports that it moved."""
    row = sync_entitlement(_subject_id(payload))
    if row is None:
        return
    _apply_plan(row)


def handle_offer_accepted(payload: dict) -> None:
    """Every purchase goes through an offer, standard or private.

    For a standard offer this arrives with the creation request, while the
    entitlement is still awaiting approval, so the plan is applied only once
    Google reports it active. ENTITLEMENT_ACTIVE covers the usual case; this
    covers a private offer accepted on an entitlement that is already live.
    Negotiated economics stay on the Marketplace offer.
    """
    row = sync_entitlement(_subject_id(payload))
    if row is None:
        return
    if row.status == GCPMarketplaceEntitlementState.ACTIVATION_REQUESTED:
        # One subscription per organization. With automatic offer approval
        # there is no creation request to reject, so this is the only moment
        # a second purchase is still rejectable: it stays awaiting activation
        # until the offer's start time, and reject is valid in that state.
        if reject_duplicate_entitlement(row):
            return
    if row.status != GCPMarketplaceEntitlementState.ACTIVE:
        # With automatic offer approval this is the only message that carries
        # the offer's start time: Google activates at that moment, not on any
        # approve call, and the entitlement resource never shows it.
        offer = payload.get("entitlement") or {}
        logger.info(
            "gcp_marketplace_offer_accepted_before_active",
            entitlement_id=row.entitlement_id,
            status=row.status,
            new_offer_start_time=offer.get("newOfferStartTime"),
            new_offer_end_time=offer.get("newOfferEndTime"),
        )
        return
    _apply_plan(row)


def handle_entitlement_deleted(payload: dict) -> None:
    """Google purged its record, about 60 days after cancellation.

    Nothing is deleted here. Access was handed back to card billing at
    cancellation, so the organization may since have become a direct
    customer, and deleting it would destroy a paying account. Whether an
    abandoned organization should be removed is a support decision, so the
    two cases are told apart in the log and left there.
    """
    entitlement_id = _subject_id(payload)
    try:
        row = sync_entitlement(entitlement_id)
    except Exception as exc:
        # Google has purged the record, so the re-fetch can answer 404. That
        # is the event's meaning, not a failure: fall back to the local row
        # rather than redeliver until the poison guard drops it.
        if not _is_not_found(exc):
            raise
        row = GCPMarketplaceEntitlement.objects.filter(
            entitlement_id=entitlement_id
        ).first()
        logger.info(
            "gcp_marketplace_entitlement_gone_on_google", entitlement_id=entitlement_id
        )
    if row is None or not row.organization_id or OrganizationSubscription is None:
        return

    subscription = OrganizationSubscription.objects.filter(
        organization_id=row.organization_id
    ).first()
    continues = subscription is not None and (
        subscription.is_marketplace_billed
        or subscription.plan != PlanChoices.FREE
        or bool(subscription.stripe_subscription_id)
    )

    if continues:
        logger.info(
            "gcp_marketplace_entitlement_deleted_org_continues",
            entitlement_id=row.entitlement_id,
            organization_id=str(row.organization_id),
            plan=subscription.plan,
            billing_method=subscription.billing_method,
        )
        return

    logger.error(
        "gcp_marketplace_entitlement_deleted_org_abandoned",
        entitlement_id=row.entitlement_id,
        organization_id=str(row.organization_id),
        note="Google expects customer data removed; review and delete by hand",
    )


def handle_sync_only(payload: dict) -> None:
    """Mirror state without touching access.

    Covers the pending and reverted events. The customer has asked for
    something, or un-asked; either way nothing has taken effect yet.
    """
    sync_entitlement(_subject_id(payload))


HANDLERS = {
    "ACCOUNT_ACTIVE": handle_account_active,
    "ACCOUNT_DELETED": handle_account_deleted,
    "ENTITLEMENT_CREATION_REQUESTED": handle_entitlement_creation_requested,
    "ENTITLEMENT_ACTIVE": handle_entitlement_active,
    "ENTITLEMENT_OFFER_ACCEPTED": handle_offer_accepted,
    "ENTITLEMENT_PLAN_CHANGE_REQUESTED": handle_plan_change_requested,
    "ENTITLEMENT_PLAN_CHANGED": handle_plan_changed,
    "ENTITLEMENT_PLAN_CHANGE_CANCELLED": handle_sync_only,
    "ENTITLEMENT_PENDING_CANCELLATION": handle_sync_only,
    "ENTITLEMENT_CANCELLATION_REVERTED": handle_sync_only,
    "ENTITLEMENT_CANCELLING": handle_sync_only,
    "ENTITLEMENT_CANCELLED": handle_entitlement_cancelled,
    "ENTITLEMENT_RENEWED": handle_entitlement_renewed,
    "ENTITLEMENT_OFFER_ENDED": handle_sync_only,
    "ENTITLEMENT_DELETED": handle_entitlement_deleted,
}


def reconcile_entitlement_plans() -> dict:
    """Hourly repair for entitlements the events could not finish.

    Two gaps, both terminal events Google never redelivers:

    - _apply_plan skips an unmapped plan id so the message acks, leaving the
      customer on free until the mapping is added. Re-applied here.
    - ENTITLEMENT_ACTIVE can arrive without usageReportingId, leaving a paid
      customer nothing can bill. Re-fetched here until Google supplies it.

    Each row is isolated: one org's bad row or Redis blip must not strand
    every org after it.
    """
    counts = {
        "checked": 0,
        "repaired": 0,
        "unmapped": 0,
        "consumer_id_recovered": 0,
        "consumer_id_missing": 0,
        "pending_checked": 0,
        "pending_resolved": 0,
        "pending_approved": 0,
        "left_service": 0,
        "paid_without_entitlement": 0,
        "failed": 0,
    }
    if OrganizationSubscription is None:
        return counts

    entitlements = (
        GCPMarketplaceEntitlement.objects.filter(
            status__in=IN_SERVICE_STATES, organization__isnull=False
        )
        .order_by("organization_id", "-effective_at", "-created_at")
        .iterator(chunk_size=200)
    )

    # Newest per organization, matching handle_entitlement_active and
    # billable_entitlements; re-applying every row would let an older
    # entitlement overwrite the plan the customer actually bought.
    seen_orgs: set = set()
    for row in entitlements:
        if row.organization_id in seen_orgs:
            continue
        seen_orgs.add(row.organization_id)
        counts["checked"] += 1
        try:
            _reconcile_entitlement_plan(row, counts)
        except Exception:
            counts["failed"] += 1
            logger.exception(
                "gcp_marketplace_plan_reconcile_failed",
                entitlement_id=row.entitlement_id,
                organization_id=str(row.organization_id),
            )

    _reconcile_pending_entitlements(counts)
    _reconcile_paid_orgs_without_entitlement(counts)
    if counts["failed"]:
        logger.error(
            "gcp_marketplace_plan_reconcile_incomplete", failed=counts["failed"]
        )
    return counts


def _reconcile_paid_orgs_without_entitlement(counts: dict) -> None:
    """A Marketplace-billed organization on a paid plan must hold an entitlement.

    Everything above works from entitlement rows. An organization whose rows
    are all terminal, or which has none (a deleted account, a row removed by
    hand), is invisible to those passes and would stay paid with nothing
    behind it. Pending activation is left alone: that is the normal state
    between purchase and Google's start time, and the plan is still free.
    """
    backed = GCPMarketplaceEntitlement.objects.filter(
        status__in=(
            *IN_SERVICE_STATES,
            GCPMarketplaceEntitlementState.ACTIVATION_REQUESTED,
        ),
        organization__isnull=False,
    ).values("organization_id")
    stranded = (
        OrganizationSubscription.objects.filter(
            billing_method=BillingMethodChoices.GCP_MARKETPLACE
        )
        .exclude(plan=PlanChoices.FREE)
        .exclude(organization_id__in=backed)
    )
    for subscription in stranded.iterator(chunk_size=200):
        counts["paid_without_entitlement"] += 1
        logger.error(
            "gcp_marketplace_paid_plan_without_entitlement_downgraded",
            organization_id=str(subscription.organization_id),
            plan=subscription.plan,
        )
        _downgrade_org_to_free(subscription.organization_id)


def _reconcile_pending_entitlements(counts: dict) -> None:
    """Re-fetch rows still awaiting approval and finish what sign-up could not.

    A pending row is not in service, so the plan loop above never revisits it.
    Two things go wrong without this: Google cancels or rejects the order and
    the row keeps saying awaiting approval, and an approval that the sign-up
    and the creation event both missed is never attempted again.
    """
    from accounts.gcp_marketplace_utils import approve_pending_entitlements

    pending = GCPMarketplaceEntitlement.objects.filter(
        status=GCPMarketplaceEntitlementState.ACTIVATION_REQUESTED
    ).select_related("account")
    accounts_done: set = set()
    for row in pending.iterator(chunk_size=200):
        counts["pending_checked"] += 1
        try:
            refreshed = sync_entitlement(row.entitlement_id)
            if refreshed is None:
                continue
            if refreshed.status != GCPMarketplaceEntitlementState.ACTIVATION_REQUESTED:
                counts["pending_resolved"] += 1
                if refreshed.status in IN_SERVICE_STATES:
                    # Google activated it and ENTITLEMENT_ACTIVE never reached
                    # us. The customer has paid; give them the plan now rather
                    # than never.
                    logger.warning(
                        "gcp_marketplace_activated_without_event",
                        entitlement_id=refreshed.entitlement_id,
                    )
                    _activate(refreshed)
                continue
            account = refreshed.account
            if account is None or not account.approved_at:
                continue
            if account.pk in accounts_done:
                continue
            accounts_done.add(account.pk)
            counts["pending_approved"] += approve_pending_entitlements(account)
        except Exception:
            counts["failed"] += 1
            logger.exception(
                "gcp_marketplace_pending_reconcile_failed",
                entitlement_id=row.entitlement_id,
            )


def _reconcile_entitlement_plan(row: GCPMarketplaceEntitlement, counts: dict) -> None:
    had_consumer_id = bool(row.usage_reporting_id)
    # Always re-fetch. Terminal events are delivered at most once and never
    # replayed after retention or the dead-letter queue, and a suspension has
    # no event at all; a row that is in service here and not at Google would
    # otherwise keep its paid plan, and report usage to a consumer Google has
    # closed, for ever.
    refreshed = sync_entitlement(row.entitlement_id)
    if refreshed is not None:
        row = refreshed
    if row.status not in IN_SERVICE_STATES:
        counts["left_service"] += 1
        logger.warning(
            "gcp_marketplace_left_service_without_event",
            entitlement_id=row.entitlement_id,
            status=row.status,
            organization_id=str(row.organization_id),
        )
        # A row back at awaiting activation was never live at Google, so
        # there is no consumer to bill a tail to; anything else was.
        _settle_left_service(
            row,
            was_live=row.status != GCPMarketplaceEntitlementState.ACTIVATION_REQUESTED,
        )
        return
    if not had_consumer_id:
        if row.usage_reporting_id:
            counts["consumer_id_recovered"] += 1
            logger.info(
                "gcp_marketplace_usage_reporting_id_recovered",
                entitlement_id=row.entitlement_id,
            )
        else:
            counts["consumer_id_missing"] += 1
            logger.error(
                "gcp_marketplace_missing_usage_reporting_id",
                entitlement_id=row.entitlement_id,
                organization_id=str(row.organization_id),
            )

    try:
        plan, interval = resolve_plan(row.plan_id)
    except ValueError:
        counts["unmapped"] += 1
        logger.error(
            "gcp_marketplace_unmapped_plan_unresolved",
            entitlement_id=row.entitlement_id,
            plan_id=row.plan_id,
            organization_id=str(row.organization_id),
        )
        return

    matches = OrganizationSubscription.objects.filter(
        organization_id=row.organization_id,
        plan=plan,
        billing_interval=interval,
        billing_method=BillingMethodChoices.GCP_MARKETPLACE,
    ).exists()
    if matches:
        return

    counts["repaired"] += 1
    logger.warning(
        "gcp_marketplace_plan_drift_repaired",
        entitlement_id=row.entitlement_id,
        organization_id=str(row.organization_id),
        plan=plan,
    )
    _apply_plan(row)


def ledger_key(event_type: str, event_id: str) -> str:
    """Google reuses one eventId across every event of a single operation.

    A purchase publishes ACCOUNT_ACTIVE, ENTITLEMENT_CREATION_REQUESTED and
    ENTITLEMENT_OFFER_ACCEPTED under the same id, so the id alone would let
    the first one processed swallow the other two.
    """
    return f"{event_type}:{event_id}"


def process_event(payload: dict) -> bool:
    """Handle one message exactly once. Returns False if already seen.

    The unique constraint on event_id is what makes this concurrency-safe. The
    ack deadline can expire while a handler is still running, so two workers can
    hold the same message at once; checking in Python first would lose the race.
    """
    event_id = payload.get("eventId")
    event_type = payload.get("eventType", "")

    if not event_id:
        logger.warning("gcp_marketplace_event_without_id", event_type=event_type)
        return False

    handler = HANDLERS.get(event_type)
    if handler is None:
        # Ack rather than raise. An unrecognised type from Google would
        # otherwise redeliver forever and fill the dead-letter queue.
        logger.warning(
            "gcp_marketplace_unknown_event_type",
            event_type=event_type,
            event_id=event_id,
        )
        return False

    with transaction.atomic():
        _, created = GCPMarketplaceProcessedEvent.objects.get_or_create(
            event_id=ledger_key(event_type, event_id),
            defaults={
                "event_type": event_type,
                "subject_id": _subject_id(payload),
            },
        )
        if not created:
            logger.info("gcp_marketplace_duplicate_event", event_id=event_id)
            return False

        handler(payload)

    logger.info(
        "gcp_marketplace_event_processed",
        event_type=event_type,
        event_id=event_id,
        subject_id=_subject_id(payload),
    )
    return True
