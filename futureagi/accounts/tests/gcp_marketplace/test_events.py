"""Pub/Sub lifecycle handlers.

Every handler re-fetches Google and acts on the fetched state, so each test
sets what Google would answer and asserts what changed locally and which
Procurement writes went out.
"""

from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError

from accounts.gcp_marketplace_events import (
    SECOND_ENTITLEMENT_REASON,
    process_event,
    reconcile_entitlement_plans,
    sync_entitlement,
)
from accounts.models.gcp_marketplace import (
    GCPMarketplaceAccountState,
    GCPMarketplaceEntitlement,
    GCPMarketplaceEntitlementState,
    GCPMarketplaceProcessedEvent,
)
from accounts.tests.gcp_marketplace.support import (
    ACCOUNT_ID,
    ENTITLEMENT_ID,
    UPDATE_TIME,
    USAGE_REPORTING_ID,
    event,
    google_time,
    remote_account,
    remote_entitlement,
)
from ee.usage.models.usage import (
    BillingIntervalChoices,
    BillingMethodChoices,
    OrganizationSubscription,
    PlanChoices,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_ee, pytest.mark.django_db]

ACTIVE = GCPMarketplaceEntitlementState.ACTIVE
REQUESTED = GCPMarketplaceEntitlementState.ACTIVATION_REQUESTED
CANCELLED = GCPMarketplaceEntitlementState.CANCELLED
PENDING_CANCELLATION = GCPMarketplaceEntitlementState.PENDING_CANCELLATION
PENDING_APPROVAL = GCPMarketplaceEntitlementState.PENDING_PLAN_CHANGE_APPROVAL

FREE_ON_CARD = (
    PlanChoices.FREE,
    BillingIntervalChoices.MONTHLY,
    BillingMethodChoices.CARD,
)
PAYG_ON_MARKETPLACE = (
    PlanChoices.PAYG,
    BillingIntervalChoices.MONTHLY,
    BillingMethodChoices.GCP_MARKETPLACE,
)

SECOND_ID = "6450d107-8413-478d-be1b-142c5df5eb31"
LATER = "2026-09-09T18:15:06.594210Z"


def row(entitlement_id=ENTITLEMENT_ID):
    return GCPMarketplaceEntitlement.objects.get(entitlement_id=entitlement_id)


def plan_of(organization):
    sub = OrganizationSubscription.objects.get(organization=organization)
    return sub.plan, sub.billing_interval, sub.billing_method


def put_on_marketplace(subscription):
    subscription.plan = PlanChoices.PAYG
    subscription.billing_method = BillingMethodChoices.GCP_MARKETPLACE
    subscription.save()


def http_error(status: int) -> HttpError:
    return HttpError(resp=MagicMock(status=status, reason="x"), content=b"{}")


class TestProcessEvent:
    def test_handles_an_event_once_and_records_it(self, procurement, gcp_account):
        payload = event("ACCOUNT_ACTIVE", account_id=ACCOUNT_ID)

        assert process_event(payload) is True
        assert process_event(payload) is False

        assert procurement.get_account.call_count == 1
        ledger = GCPMarketplaceProcessedEvent.objects.get()
        assert ledger.event_type == "ACCOUNT_ACTIVE"
        assert ledger.subject_id == ACCOUNT_ID

    def test_unknown_type_is_acked_without_a_ledger_row(self, procurement):
        payload = event("ENTITLEMENT_SOMETHING_NEW", entitlement_id=ENTITLEMENT_ID)

        assert process_event(payload) is False
        assert not GCPMarketplaceProcessedEvent.objects.exists()

    def test_missing_event_id_is_acked(self, procurement):
        payload = event("ACCOUNT_ACTIVE", account_id=ACCOUNT_ID)
        del payload["eventId"]

        assert process_event(payload) is False
        procurement.get_account.assert_not_called()

    def test_handler_failure_rolls_the_ledger_row_back(self, procurement, gcp_account):
        procurement.get_account.side_effect = RuntimeError("procurement down")

        with pytest.raises(RuntimeError):
            process_event(event("ACCOUNT_ACTIVE", account_id=ACCOUNT_ID))

        assert not GCPMarketplaceProcessedEvent.objects.exists()

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Google reuses one eventId across the events of a purchase and the "
            "ledger keys on the id alone, so the second event is dropped as a "
            "duplicate. Fixed on fix/th-7731-marketplace-event-dedupe."
        ),
    )
    def test_events_of_one_purchase_share_an_event_id_and_are_all_handled(
        self, procurement, gcp_account
    ):
        shared = "CREATE_ENTITLEMENT-9b443172-4b7e-4559-8e04-3b15b4222f0e"

        first = process_event(
            event("ACCOUNT_ACTIVE", account_id=ACCOUNT_ID, event_id=shared)
        )
        second = process_event(
            event(
                "ENTITLEMENT_CREATION_REQUESTED",
                entitlement_id=ENTITLEMENT_ID,
                event_id=shared,
            )
        )

        assert (first, second) == (True, True)
        procurement.approve_entitlement.assert_called_once_with(ENTITLEMENT_ID)


class TestSyncEntitlement:
    def test_mirrors_google_and_links_the_account(self, procurement, gcp_account):
        synced = sync_entitlement(ENTITLEMENT_ID)

        assert synced.account == gcp_account
        assert synced.organization == gcp_account.organization
        assert synced.plan_id == "payg"
        assert synced.status == REQUESTED
        assert synced.usage_reporting_id == USAGE_REPORTING_ID
        assert synced.google_update_time == google_time(UPDATE_TIME)
        assert synced.raw_payload["name"].endswith(ENTITLEMENT_ID)

    def test_unknown_account_leaves_the_row_orphaned(self, procurement):
        synced = sync_entitlement(ENTITLEMENT_ID)

        assert synced.account is None
        assert synced.organization is None

    def test_state_older_than_stored_is_ignored(self, procurement, entitlement):
        procurement.get_entitlement.return_value = remote_entitlement(
            state=CANCELLED, update_time="2026-09-09T10:00:00Z"
        )

        assert sync_entitlement(ENTITLEMENT_ID) is None
        assert row().status == ACTIVE


class TestCreationRequested:
    @staticmethod
    def payload(entitlement_id=ENTITLEMENT_ID):
        return event("ENTITLEMENT_CREATION_REQUESTED", entitlement_id=entitlement_id)

    def test_approves_when_sign_up_already_approved_the_account(
        self, procurement, gcp_account
    ):
        assert process_event(self.payload()) is True

        procurement.approve_entitlement.assert_called_once_with(ENTITLEMENT_ID)
        assert row().account == gcp_account

    def test_holds_until_sign_up_approves_the_account(self, procurement, gcp_account):
        gcp_account.approved_at = None
        gcp_account.save(update_fields=["approved_at"])

        process_event(self.payload())

        procurement.approve_entitlement.assert_not_called()
        assert row().status == REQUESTED

    def test_holds_when_the_account_row_does_not_exist_yet(self, procurement):
        process_event(self.payload())

        procurement.approve_entitlement.assert_not_called()
        assert row().account is None

    def test_does_not_approve_again_after_a_lost_response(
        self, procurement, gcp_account
    ):
        procurement.get_entitlement.return_value = remote_entitlement(state=ACTIVE)

        process_event(self.payload())

        procurement.approve_entitlement.assert_not_called()

    def test_rejects_a_second_purchase_while_one_is_in_service(
        self, procurement, entitlement
    ):
        procurement.get_entitlement.return_value = remote_entitlement(
            SECOND_ID, state=REQUESTED, plan="scale-P1Y"
        )

        process_event(self.payload(SECOND_ID))

        procurement.reject_entitlement.assert_called_once_with(
            SECOND_ID, SECOND_ENTITLEMENT_REASON
        )
        procurement.approve_entitlement.assert_not_called()


class TestEntitlementActive:
    @staticmethod
    def activate(procurement, **remote):
        procurement.get_entitlement.return_value = remote_entitlement(
            state=ACTIVE, **remote
        )
        return process_event(event("ENTITLEMENT_ACTIVE", entitlement_id=ENTITLEMENT_ID))

    @pytest.mark.parametrize(
        "plan_id, plan, interval",
        [
            ("payg", PlanChoices.PAYG, BillingIntervalChoices.MONTHLY),
            ("scale", PlanChoices.SCALE, BillingIntervalChoices.MONTHLY),
            ("scale-P1Y", PlanChoices.SCALE, BillingIntervalChoices.ANNUAL),
            ("enterprise-P1Y", PlanChoices.ENTERPRISE, BillingIntervalChoices.ANNUAL),
        ],
    )
    def test_applies_the_plan_and_moves_billing_to_the_marketplace(
        self, procurement, gcp_account, subscription, plan_id, plan, interval
    ):
        self.activate(procurement, plan=plan_id)

        assert plan_of(gcp_account.organization) == (
            plan,
            interval,
            BillingMethodChoices.GCP_MARKETPLACE,
        )
        assert row().usage_reporting_id == USAGE_REPORTING_ID

    def test_unmapped_plan_is_recorded_but_not_applied(
        self, procurement, gcp_account, subscription
    ):
        assert self.activate(procurement, plan="legacy-gold") is True

        assert plan_of(gcp_account.organization) == FREE_ON_CARD
        assert row().plan_id == "legacy-gold"

    def test_missing_usage_reporting_id_still_applies_the_plan(
        self, procurement, gcp_account, subscription
    ):
        self.activate(procurement, usage_reporting_id="")

        assert plan_of(gcp_account.organization) == PAYG_ON_MARKETPLACE
        assert row().usage_reporting_id == ""


class TestOfferAccepted:
    @staticmethod
    def accept(procurement, **remote):
        procurement.get_entitlement.return_value = remote_entitlement(**remote)
        return process_event(
            event("ENTITLEMENT_OFFER_ACCEPTED", entitlement_id=ENTITLEMENT_ID)
        )

    def test_private_offer_on_a_live_entitlement_applies_its_plan(
        self, procurement, gcp_account, subscription
    ):
        self.accept(procurement, state=ACTIVE, plan="enterprise-P1Y")

        assert plan_of(gcp_account.organization) == (
            PlanChoices.ENTERPRISE,
            BillingIntervalChoices.ANNUAL,
            BillingMethodChoices.GCP_MARKETPLACE,
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Standard offers publish ENTITLEMENT_OFFER_ACCEPTED at purchase, while "
            "the entitlement still awaits approval, and the handler applies the "
            "plan regardless of state. Fixed on fix/th-7731-marketplace-event-dedupe."
        ),
    )
    def test_standard_offer_before_activation_does_not_apply_the_plan(
        self, procurement, gcp_account, subscription
    ):
        self.accept(procurement, state=REQUESTED, plan="payg")

        assert plan_of(gcp_account.organization) == FREE_ON_CARD
        assert row().status == REQUESTED


class TestPlanChange:
    @staticmethod
    def requested(procurement, **remote):
        procurement.get_entitlement.return_value = remote_entitlement(**remote)
        return process_event(
            event("ENTITLEMENT_PLAN_CHANGE_REQUESTED", entitlement_id=ENTITLEMENT_ID)
        )

    def test_requested_change_is_approved_with_the_pending_plan(
        self, procurement, entitlement
    ):
        self.requested(
            procurement,
            state=PENDING_APPROVAL,
            newPendingPlan="scale-P1Y",
            update_time=LATER,
        )

        procurement.approve_plan_change.assert_called_once_with(
            ENTITLEMENT_ID, "scale-P1Y"
        )
        assert row().new_pending_plan == "scale-P1Y"

    def test_request_without_a_pending_plan_is_acked_and_skipped(
        self, procurement, entitlement
    ):
        assert (
            self.requested(procurement, state=PENDING_APPROVAL, update_time=LATER)
            is True
        )

        procurement.approve_plan_change.assert_not_called()

    def test_request_no_longer_pending_is_not_approved_twice(
        self, procurement, entitlement
    ):
        self.requested(
            procurement,
            state=ACTIVE,
            newPendingPlan="scale-P1Y",
            update_time=LATER,
        )

        procurement.approve_plan_change.assert_not_called()

    def test_changed_plan_takes_effect_only_when_google_says_so(
        self, procurement, entitlement, subscription
    ):
        put_on_marketplace(subscription)
        procurement.get_entitlement.return_value = remote_entitlement(
            state=ACTIVE, plan="scale-P1Y", update_time=LATER
        )

        process_event(event("ENTITLEMENT_PLAN_CHANGED", entitlement_id=ENTITLEMENT_ID))

        assert plan_of(entitlement.organization) == (
            PlanChoices.SCALE,
            BillingIntervalChoices.ANNUAL,
            BillingMethodChoices.GCP_MARKETPLACE,
        )
        assert row().plan_id == "scale-P1Y"


class TestCancellation:
    def test_pending_cancellation_only_syncs(
        self, procurement, entitlement, subscription
    ):
        put_on_marketplace(subscription)
        procurement.get_entitlement.return_value = remote_entitlement(
            state=PENDING_CANCELLATION, update_time=LATER
        )

        process_event(
            event("ENTITLEMENT_PENDING_CANCELLATION", entitlement_id=ENTITLEMENT_ID)
        )

        assert row().status == PENDING_CANCELLATION
        assert plan_of(entitlement.organization) == PAYG_ON_MARKETPLACE

    def test_cancelled_downgrades_first_and_reports_the_tail_after_commit(
        self, procurement, entitlement, subscription, django_capture_on_commit_callbacks
    ):
        put_on_marketplace(subscription)
        procurement.get_entitlement.return_value = remote_entitlement(
            state=CANCELLED, update_time=LATER
        )

        with (
            patch("accounts.gcp_marketplace_usage.report_final_window") as final,
            django_capture_on_commit_callbacks(execute=True),
        ):
            process_event(event("ENTITLEMENT_CANCELLED", entitlement_id=ENTITLEMENT_ID))

        assert row().status == CANCELLED
        assert row().google_update_time == google_time(LATER)
        assert plan_of(entitlement.organization) == FREE_ON_CARD
        final.assert_called_once()
        assert final.call_args.args[0].entitlement_id == ENTITLEMENT_ID

    def test_a_failed_tail_report_does_not_undo_the_downgrade(
        self, procurement, entitlement, subscription, django_capture_on_commit_callbacks
    ):
        put_on_marketplace(subscription)
        procurement.get_entitlement.return_value = remote_entitlement(
            state=CANCELLED, update_time=LATER
        )

        with (
            patch(
                "accounts.gcp_marketplace_usage.report_final_window",
                side_effect=RuntimeError("service control down"),
            ),
            django_capture_on_commit_callbacks(execute=True),
        ):
            process_event(event("ENTITLEMENT_CANCELLED", entitlement_id=ENTITLEMENT_ID))

        assert plan_of(entitlement.organization) == FREE_ON_CARD


class TestEntitlementDeleted:
    @staticmethod
    def delete(procurement):
        return process_event(
            event("ENTITLEMENT_DELETED", entitlement_id=ENTITLEMENT_ID)
        )

    def test_marketplace_billed_organization_is_left_alone(
        self, procurement, entitlement, subscription
    ):
        put_on_marketplace(subscription)
        procurement.get_entitlement.return_value = remote_entitlement(
            state=CANCELLED, update_time=LATER
        )

        assert self.delete(procurement) is True
        assert plan_of(entitlement.organization) == PAYG_ON_MARKETPLACE

    def test_abandoned_organization_is_reported_not_deleted(
        self, procurement, entitlement, subscription
    ):
        procurement.get_entitlement.return_value = remote_entitlement(
            state=CANCELLED, update_time=LATER
        )

        assert self.delete(procurement) is True
        assert OrganizationSubscription.objects.filter(
            organization=entitlement.organization
        ).exists()

    def test_entitlement_already_purged_on_google_uses_the_local_row(
        self, procurement, entitlement, subscription
    ):
        procurement.get_entitlement.side_effect = http_error(404)

        assert self.delete(procurement) is True
        assert row().status == ACTIVE

    def test_other_procurement_errors_propagate(self, procurement, entitlement):
        procurement.get_entitlement.side_effect = http_error(500)

        with pytest.raises(HttpError):
            self.delete(procurement)


class TestAccountEvents:
    def test_account_active_refreshes_state_from_google(self, procurement, gcp_account):
        gcp_account.state = GCPMarketplaceAccountState.ACTIVATION_REQUESTED
        gcp_account.save(update_fields=["state"])
        procurement.get_account.return_value = remote_account(state="ACCOUNT_ACTIVE")

        process_event(event("ACCOUNT_ACTIVE", account_id=ACCOUNT_ID))

        gcp_account.refresh_from_db()
        assert gcp_account.state == GCPMarketplaceAccountState.ACTIVE
        assert gcp_account.raw_payload["state"] == "ACCOUNT_ACTIVE"

    def test_account_deleted_unlinks_and_hands_billing_back(
        self, procurement, gcp_account, subscription
    ):
        put_on_marketplace(subscription)
        organization = gcp_account.organization

        process_event(event("ACCOUNT_DELETED", account_id=ACCOUNT_ID))

        gcp_account.refresh_from_db()
        assert gcp_account.organization is None
        assert plan_of(organization) == FREE_ON_CARD

    def test_account_deleted_for_an_unknown_account_is_acked(self, procurement):
        assert process_event(event("ACCOUNT_DELETED", account_id=ACCOUNT_ID)) is True


class TestReconcilePlans:
    def test_repairs_plan_drift(self, procurement, entitlement, subscription):
        counts = reconcile_entitlement_plans()

        assert counts["checked"] == 1
        assert counts["repaired"] == 1
        assert plan_of(entitlement.organization) == PAYG_ON_MARKETPLACE

    def test_leaves_a_matching_plan_alone(self, procurement, entitlement, subscription):
        put_on_marketplace(subscription)

        counts = reconcile_entitlement_plans()

        assert counts["repaired"] == 0
        procurement.get_entitlement.assert_not_called()

    def test_recovers_a_missing_usage_reporting_id(
        self, procurement, entitlement, subscription
    ):
        entitlement.usage_reporting_id = ""
        entitlement.save(update_fields=["usage_reporting_id"])
        procurement.get_entitlement.return_value = remote_entitlement(state=ACTIVE)

        counts = reconcile_entitlement_plans()

        assert counts["consumer_id_recovered"] == 1
        assert row().usage_reporting_id == USAGE_REPORTING_ID

    def test_counts_an_id_google_still_has_not_supplied(
        self, procurement, entitlement, subscription
    ):
        entitlement.usage_reporting_id = ""
        entitlement.save(update_fields=["usage_reporting_id"])
        procurement.get_entitlement.return_value = remote_entitlement(
            state=ACTIVE, usage_reporting_id=""
        )

        counts = reconcile_entitlement_plans()

        assert counts["consumer_id_missing"] == 1

    def test_does_not_reapply_a_plan_the_refetch_shows_leaving_service(
        self, procurement, entitlement, subscription
    ):
        entitlement.usage_reporting_id = ""
        entitlement.save(update_fields=["usage_reporting_id"])
        procurement.get_entitlement.return_value = remote_entitlement(
            state=CANCELLED, update_time=LATER
        )

        counts = reconcile_entitlement_plans()

        assert counts["repaired"] == 0
        assert plan_of(entitlement.organization) == FREE_ON_CARD

    def test_counts_unmapped_plans(self, procurement, entitlement, subscription):
        entitlement.plan_id = "legacy-gold"
        entitlement.save(update_fields=["plan_id"])

        counts = reconcile_entitlement_plans()

        assert counts["unmapped"] == 1
        assert plan_of(entitlement.organization) == FREE_ON_CARD

    def test_ignores_rows_out_of_service(self, procurement, entitlement, subscription):
        entitlement.status = CANCELLED
        entitlement.save(update_fields=["status"])

        counts = reconcile_entitlement_plans()

        assert counts["checked"] == 0
