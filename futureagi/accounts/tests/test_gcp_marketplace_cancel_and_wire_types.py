"""Cancel-then-resubscribe keeps the surviving plan; usage wire types follow the metric.

Two production incidents behind these:

- A cancellation for an old entitlement dropped the organization to free even
  though a newer entitlement was live (Pub/Sub delivers out of order, and a
  customer who cancels one plan and buys another sees exactly this).
- Service Control rejected every payg report with "Inconsistent metric value
  type for metric name '.../gateway_request'. Expecting double, got int64":
  the service config types that one metric DOUBLE while scale's and
  enterprise's gateway metrics are INT64, so a per-dimension rule can never
  match all three plans.
"""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone

from accounts import gcp_marketplace_events as events
from accounts import gcp_marketplace_usage as usage
from accounts.models import Organization
from accounts.models.gcp_marketplace import (
    GCPMarketplaceEntitlement,
    GCPMarketplaceEntitlementState,
    GCPMarketplaceUsageCheckpoint,
)


def _entitlement(org, entitlement_id, status, plan_id="payg"):
    return GCPMarketplaceEntitlement.objects.create(
        entitlement_id=entitlement_id,
        organization=org,
        status=status,
        plan_id=plan_id,
        usage_reporting_id="project_number:1",
        google_update_time=timezone.now(),
    )


def _cancel_payload(entitlement_id):
    return {
        "eventId": f"CANCEL_ENTITLEMENT-{entitlement_id}",
        "eventType": "ENTITLEMENT_CANCELLED",
        "entitlement": {"id": entitlement_id},
    }


def _sync_to_cancelled(entitlement_id):
    """Stand in for sync_entitlement: Google now says CANCELLED."""
    row = GCPMarketplaceEntitlement.objects.get(entitlement_id=entitlement_id)
    row.status = GCPMarketplaceEntitlementState.CANCELLED
    row.google_update_time = timezone.now()
    row.save(update_fields=["status", "google_update_time", "updated_at"])
    return row


@pytest.mark.django_db
class TestCancelKeepsSurvivingEntitlement:
    def test_downgrades_when_nothing_else_is_live(self):
        org = Organization.objects.create(name="solo")
        _entitlement(org, "old", GCPMarketplaceEntitlementState.ACTIVE)

        with (
            patch.object(events, "sync_entitlement", side_effect=_sync_to_cancelled),
            patch.object(events, "_downgrade_to_free") as downgrade,
            patch.object(events, "_apply_plan") as apply_plan,
            patch.object(events, "_report_final_window_after_commit") as report,
        ):
            events.handle_entitlement_cancelled(_cancel_payload("old"))

        downgrade.assert_called_once()
        apply_plan.assert_not_called()
        report.assert_called_once()  # it was live, so its tail is billed

    def test_keeps_org_on_the_newer_live_entitlement(self):
        org = Organization.objects.create(name="resubscribed")
        _entitlement(org, "old", GCPMarketplaceEntitlementState.ACTIVE, plan_id="payg")
        newer = _entitlement(
            org, "new", GCPMarketplaceEntitlementState.ACTIVE, plan_id="scale"
        )

        with (
            patch.object(events, "sync_entitlement", side_effect=_sync_to_cancelled),
            patch.object(events, "_downgrade_to_free") as downgrade,
            patch.object(events, "_apply_plan") as apply_plan,
            patch.object(events, "_report_final_window_after_commit"),
        ):
            events.handle_entitlement_cancelled(_cancel_payload("old"))

        downgrade.assert_not_called()
        apply_plan.assert_called_once()
        assert apply_plan.call_args.args[0].pk == newer.pk

    def test_pending_new_entitlement_does_not_block_downgrade(self):
        # ACTIVATION_REQUESTED is not in service: the plan is applied when it
        # goes ACTIVE, by that handler. Cancelling the old one drops to free.
        org = Organization.objects.create(name="pending-new")
        _entitlement(org, "old", GCPMarketplaceEntitlementState.ACTIVE)
        _entitlement(org, "new", GCPMarketplaceEntitlementState.ACTIVATION_REQUESTED)

        with (
            patch.object(events, "sync_entitlement", side_effect=_sync_to_cancelled),
            patch.object(events, "_downgrade_to_free") as downgrade,
            patch.object(events, "_apply_plan") as apply_plan,
            patch.object(events, "_report_final_window_after_commit"),
        ):
            events.handle_entitlement_cancelled(_cancel_payload("old"))

        downgrade.assert_called_once()
        apply_plan.assert_not_called()


@pytest.mark.django_db
class TestFinalWindowOnlyForLiveEntitlements:
    def test_never_live_entitlement_is_not_reported(self):
        # Cancelled straight from ACTIVATION_REQUESTED: no usage, and the
        # consumer project never had the service enabled (SERVICE_DISABLED).
        org = Organization.objects.create(name="never-live")
        _entitlement(org, "abandoned", GCPMarketplaceEntitlementState.ACTIVATION_REQUESTED)

        with (
            patch.object(events, "sync_entitlement", side_effect=_sync_to_cancelled),
            patch.object(events, "_downgrade_to_free"),
            patch.object(events, "_report_final_window_after_commit") as report,
        ):
            events.handle_entitlement_cancelled(_cancel_payload("abandoned"))

        report.assert_not_called()

    def test_recorded_window_is_still_reported_even_if_status_was_stale(self):
        # Local status never caught up to ACTIVE, but a window was recorded
        # against it: that usage is real and must be billed.
        org = Organization.objects.create(name="stale-status")
        row = _entitlement(
            org, "stale", GCPMarketplaceEntitlementState.ACTIVATION_REQUESTED
        )
        now = timezone.now()
        GCPMarketplaceUsageCheckpoint.objects.create(
            entitlement=row,
            organization=org,
            metric="gateway_requests",
            window_start=now - timedelta(hours=1),
            window_end=now,
            quantity_reported=Decimal("3"),
            operation_id="op-1",
        )

        with (
            patch.object(events, "sync_entitlement", side_effect=_sync_to_cancelled),
            patch.object(events, "_downgrade_to_free"),
            patch.object(events, "_report_final_window_after_commit") as report,
        ):
            events.handle_entitlement_cancelled(_cancel_payload("stale"))

        report.assert_called_once()


@pytest.mark.django_db
class TestNewestEntitlementWins:
    """A second purchase activates under automatic approval; it is the one the customer is on."""

    @staticmethod
    def _dated(row, days_ago):
        row.effective_at = timezone.now() - timedelta(days=days_ago)
        row.save(update_fields=["effective_at"])
        return row

    def test_active_applies_newest_and_flags_the_overlap(self):
        org = Organization.objects.create(name="overlap")
        self._dated(_entitlement(org, "older", GCPMarketplaceEntitlementState.ACTIVE, plan_id="payg"), 1)
        newer = self._dated(_entitlement(org, "newer", GCPMarketplaceEntitlementState.ACTIVE, plan_id="scale"), 0)

        with (
            patch.object(events, "sync_entitlement", return_value=newer),
            patch.object(events, "_apply_plan") as apply_plan,
            patch.object(events.logger, "error") as log_error,
        ):
            events.handle_entitlement_active({"entitlement": {"id": "newer"}})

        assert apply_plan.call_args.args[0].pk == newer.pk
        assert log_error.call_args.args[0] == "gcp_marketplace_multiple_in_service_entitlements"
        assert log_error.call_args.kwargs["superseded_entitlement_id"] == "older"

    def test_billing_follows_the_newest_entitlement(self):
        org = Organization.objects.create(name="billing-newest")
        self._dated(_entitlement(org, "older", GCPMarketplaceEntitlementState.ACTIVE), 1)
        newer = self._dated(_entitlement(org, "newer", GCPMarketplaceEntitlementState.ACTIVE), 0)

        assert [e.pk for e in usage.billable_entitlements()] == [newer.pk]

    def test_cancel_survivor_is_the_newest(self):
        org = Organization.objects.create(name="survivor-newest")
        _entitlement(org, "old", GCPMarketplaceEntitlementState.ACTIVE)
        self._dated(_entitlement(org, "a", GCPMarketplaceEntitlementState.ACTIVE), 1)
        b = self._dated(_entitlement(org, "b", GCPMarketplaceEntitlementState.ACTIVE), 0)

        with (
            patch.object(events, "sync_entitlement", side_effect=_sync_to_cancelled),
            patch.object(events, "_downgrade_to_free") as downgrade,
            patch.object(events, "_apply_plan") as apply_plan,
            patch.object(events, "_report_final_window_after_commit"),
        ):
            events.handle_entitlement_cancelled(_cancel_payload("old"))

        downgrade.assert_not_called()
        assert apply_plan.call_args.args[0].pk == b.pk


@pytest.mark.django_db
class TestSignupAppliesPlanOfOrphanEntitlement:
    """Google activated before sign-up finished; linking the row must apply its plan."""

    def test_linked_active_orphan_gets_its_plan_applied(self):
        from accounts import gcp_marketplace_utils as utils
        from accounts.models.gcp_marketplace import GCPMarketplaceAccount

        org = Organization.objects.create(name="late-signup")
        account = GCPMarketplaceAccount.objects.create(
            procurement_account_id="acct-1", organization=org
        )
        orphan = GCPMarketplaceEntitlement.objects.create(
            entitlement_id="paid-before-signup",
            status=GCPMarketplaceEntitlementState.ACTIVE,
            plan_id="payg",
            usage_reporting_id="project_number:1",
            raw_payload={"account": "providers/p/accounts/acct-1"},
        )
        GCPMarketplaceEntitlement.objects.create(
            entitlement_id="someone-elses",
            status=GCPMarketplaceEntitlementState.ACTIVE,
            plan_id="payg",
            raw_payload={"account": "providers/p/accounts/acct-2"},
        )

        with patch.object(events, "_apply_plan") as apply_plan:
            linked = utils._link_orphan_entitlements(account)

        orphan.refresh_from_db()
        assert linked == 1
        assert orphan.organization_id == org.id and orphan.account_id == account.id
        apply_plan.assert_called_once()
        assert apply_plan.call_args.args[0].pk == orphan.pk

    def test_pending_orphan_is_linked_but_not_applied(self):
        from accounts import gcp_marketplace_utils as utils
        from accounts.models.gcp_marketplace import GCPMarketplaceAccount

        org = Organization.objects.create(name="pending-signup")
        account = GCPMarketplaceAccount.objects.create(
            procurement_account_id="acct-3", organization=org
        )
        GCPMarketplaceEntitlement.objects.create(
            entitlement_id="still-pending",
            status=GCPMarketplaceEntitlementState.ACTIVATION_REQUESTED,
            plan_id="payg",
            raw_payload={"account": "providers/p/accounts/acct-3"},
        )

        with patch.object(events, "_apply_plan") as apply_plan:
            assert utils._link_orphan_entitlements(account) == 1

        apply_plan.assert_not_called()  # ENTITLEMENT_ACTIVE will do it


@pytest.mark.django_db
class TestSecondPurchaseRejectedAtOfferAccepted:
    """Automatic approval sends no creation request; OFFER_ACCEPTED is where a duplicate is still rejectable."""

    @staticmethod
    def _offer_accepted(entitlement_id):
        return {
            "eventId": f"CREATE_ENTITLEMENT-{entitlement_id}",
            "eventType": "ENTITLEMENT_OFFER_ACCEPTED",
            "entitlement": {"id": entitlement_id, "newOfferStartTime": "2026-09-11T07:00:00Z"},
        }

    def test_second_purchase_while_one_is_live_is_rejected(self):
        org = Organization.objects.create(name="already-subscribed")
        _entitlement(org, "live", GCPMarketplaceEntitlementState.ACTIVE)
        second = _entitlement(org, "second", GCPMarketplaceEntitlementState.ACTIVATION_REQUESTED)

        with (
            patch.object(events, "sync_entitlement", return_value=second),
            patch.object(events.gcp_procurement, "reject_entitlement") as reject,
            patch.object(events, "_apply_plan") as apply_plan,
        ):
            events.handle_offer_accepted(self._offer_accepted("second"))

        reject.assert_called_once()
        assert reject.call_args.args[0] == "second"
        apply_plan.assert_not_called()

    def test_first_purchase_is_left_to_activate(self):
        org = Organization.objects.create(name="first-purchase")
        first = _entitlement(org, "first", GCPMarketplaceEntitlementState.ACTIVATION_REQUESTED)

        with (
            patch.object(events, "sync_entitlement", return_value=first),
            patch.object(events.gcp_procurement, "reject_entitlement") as reject,
            patch.object(events, "_apply_plan") as apply_plan,
        ):
            events.handle_offer_accepted(self._offer_accepted("first"))

        reject.assert_not_called()
        apply_plan.assert_not_called()  # ENTITLEMENT_ACTIVE applies it

    def test_repurchase_after_cancel_is_not_a_duplicate(self):
        org = Organization.objects.create(name="resubscribe")
        _entitlement(org, "old", GCPMarketplaceEntitlementState.CANCELLED)
        new = _entitlement(org, "new", GCPMarketplaceEntitlementState.ACTIVATION_REQUESTED)

        with (
            patch.object(events, "sync_entitlement", return_value=new),
            patch.object(events.gcp_procurement, "reject_entitlement") as reject,
        ):
            events.handle_offer_accepted(self._offer_accepted("new"))

        reject.assert_not_called()


@pytest.mark.django_db
class TestReconcileCatchesWhatEventsMissed:
    """A lost CANCELLED, or a suspension (no event), must not leave an org paid for ever."""

    def test_row_that_left_service_at_google_is_settled(self):
        org = Organization.objects.create(name="lost-cancel")
        row = _entitlement(org, "gone", GCPMarketplaceEntitlementState.ACTIVE)

        def google_says_cancelled(entitlement_id):
            row.status = GCPMarketplaceEntitlementState.CANCELLED
            row.save(update_fields=["status", "updated_at"])
            return row

        counts = {"left_service": 0, "consumer_id_recovered": 0, "consumer_id_missing": 0,
                  "unmapped": 0, "repaired": 0}
        with (
            patch.object(events, "sync_entitlement", side_effect=google_says_cancelled),
            patch.object(events, "_downgrade_to_free") as downgrade,
            patch.object(events, "_report_final_window_after_commit") as report,
        ):
            events._reconcile_entitlement_plan(row, counts)

        assert counts["left_service"] == 1
        downgrade.assert_called_once()
        report.assert_called_once()

    def test_locally_forced_active_row_is_downgraded_without_a_tail(self):
        # Status written by hand while Google still says awaiting activation:
        # access is handed back, but there is no live consumer to bill.
        org = Organization.objects.create(name="forced-active")
        row = _entitlement(org, "forced", GCPMarketplaceEntitlementState.ACTIVE)

        def google_says_pending(entitlement_id):
            row.status = GCPMarketplaceEntitlementState.ACTIVATION_REQUESTED
            row.save(update_fields=["status", "updated_at"])
            return row

        counts = {"left_service": 0, "consumer_id_recovered": 0, "consumer_id_missing": 0,
                  "unmapped": 0, "repaired": 0}
        with (
            patch.object(events, "sync_entitlement", side_effect=google_says_pending),
            patch.object(events, "_downgrade_to_free") as downgrade,
            patch.object(events, "_report_final_window_after_commit") as report,
        ):
            events._reconcile_entitlement_plan(row, counts)

        downgrade.assert_called_once()
        report.assert_not_called()

    def test_paid_marketplace_org_with_no_entitlement_is_downgraded(self):
        from ee.usage.models.usage import (
            BillingMethodChoices,
            OrganizationSubscription,
            SubscriptionTier,
            SubscriptionTierChoices,
        )

        org = Organization.objects.create(name="stranded")
        tier = SubscriptionTier.objects.create(name=SubscriptionTierChoices.FREE)
        OrganizationSubscription.objects.create(
            organization=org,
            subscription_tier=tier,
            plan="scale",
            billing_method=BillingMethodChoices.GCP_MARKETPLACE,
        )
        _entitlement(org, "dead", GCPMarketplaceEntitlementState.CANCELLED)
        counts = {"paid_without_entitlement": 0}

        with patch.object(events, "_downgrade_org_to_free") as downgrade:
            events._reconcile_paid_orgs_without_entitlement(counts)

        assert counts["paid_without_entitlement"] == 1
        downgrade.assert_called_once_with(org.id)

    def test_org_awaiting_activation_is_left_alone(self):
        from ee.usage.models.usage import (
            BillingMethodChoices,
            OrganizationSubscription,
            SubscriptionTier,
            SubscriptionTierChoices,
        )

        org = Organization.objects.create(name="awaiting")
        tier = SubscriptionTier.objects.create(name=SubscriptionTierChoices.FREE)
        OrganizationSubscription.objects.create(
            organization=org,
            subscription_tier=tier,
            plan="free",
            billing_method=BillingMethodChoices.GCP_MARKETPLACE,
        )
        _entitlement(org, "pending", GCPMarketplaceEntitlementState.ACTIVATION_REQUESTED)
        counts = {"paid_without_entitlement": 0}

        with patch.object(events, "_downgrade_org_to_free") as downgrade:
            events._reconcile_paid_orgs_without_entitlement(counts)

        assert counts["paid_without_entitlement"] == 0
        downgrade.assert_not_called()


class TestDrainActivityHeartbeatsFromPullThread:
    """The pull runs on a worker thread; heartbeat must be delivered on the activity's loop.

    Production failure: RuntimeError("no running event loop") from
    activity.heartbeat() inside _drain_sync, on the first message of every
    batch, so nothing was ever processed or acked.
    """

    @staticmethod
    def _run(fn, messages=5):
        import asyncio

        from temporalio.testing import ActivityEnvironment

        import tfc.temporal.marketplace.activities as act

        def fake_drain(heartbeat):
            for _ in range(messages):
                heartbeat()
            return {"events_processed": messages, "had_events": True}

        beats = []

        def worker_like_heartbeat(*details):
            # temporalio's worker schedules a task on the loop; from any other
            # thread this is the exact error seen in production.
            asyncio.get_running_loop()
            beats.append(details)

        async def go():
            env = ActivityEnvironment()
            env.on_heartbeat = worker_like_heartbeat
            with (
                patch.object(act, "_is_configured", return_value=True),
                patch.object(act, "_drain_sync", fake_drain),
            ):
                return await env.run(fn)

        return asyncio.run(go()), len(beats)

    def test_fixed_activity_heartbeats_once_per_message(self):
        import tfc.temporal.marketplace.activities as act

        result, beats = self._run(act.drain_gcp_marketplace_events_activity)
        assert result.events_processed == 5 and result.had_events is True
        assert beats == 5

    def test_passing_heartbeat_straight_into_the_thread_reproduces_the_outage(self):
        import asyncio

        from temporalio import activity

        import tfc.temporal.marketplace.activities as act

        @activity.defn(name="drain_as_shipped_in_v1_37_1")
        async def old(input=None):
            return await asyncio.to_thread(act._drain_sync, activity.heartbeat)

        with pytest.raises(RuntimeError, match="no running event loop"):
            self._run(old)


@pytest.mark.django_db
class TestPlanFollowsGoogleState:
    """The plan is applied from Google's state, never from the message alone."""

    def test_active_event_but_google_still_pending_applies_nothing(self):
        org = Organization.objects.create(name="early-active-event")
        row = _entitlement(org, "early", GCPMarketplaceEntitlementState.ACTIVATION_REQUESTED)

        with (
            patch.object(events, "sync_entitlement", return_value=row),
            patch.object(events, "_apply_plan") as apply_plan,
        ):
            events.handle_entitlement_active({"entitlement": {"id": "early"}})

        apply_plan.assert_not_called()

    def test_pending_pass_activates_when_google_did_without_an_event(self):
        org = Organization.objects.create(name="lost-active-event")
        row = _entitlement(org, "silent", GCPMarketplaceEntitlementState.ACTIVATION_REQUESTED)

        def google_says_active(entitlement_id):
            row.status = GCPMarketplaceEntitlementState.ACTIVE
            row.save(update_fields=["status", "updated_at"])
            return row

        counts = {"pending_checked": 0, "pending_resolved": 0, "pending_approved": 0, "failed": 0}
        with (
            patch.object(events, "sync_entitlement", side_effect=google_says_active),
            patch.object(events, "_apply_plan") as apply_plan,
        ):
            events._reconcile_pending_entitlements(counts)

        assert counts["pending_resolved"] == 1
        apply_plan.assert_called_once()
        assert apply_plan.call_args.args[0].pk == row.pk

    def test_pending_pass_does_not_touch_a_cancelled_one(self):
        org = Organization.objects.create(name="cancelled-before-active")
        row = _entitlement(org, "dead", GCPMarketplaceEntitlementState.ACTIVATION_REQUESTED)

        def google_says_cancelled(entitlement_id):
            row.status = GCPMarketplaceEntitlementState.CANCELLED
            row.save(update_fields=["status", "updated_at"])
            return row

        counts = {"pending_checked": 0, "pending_resolved": 0, "pending_approved": 0, "failed": 0}
        with (
            patch.object(events, "sync_entitlement", side_effect=google_says_cancelled),
            patch.object(events, "_apply_plan") as apply_plan,
        ):
            events._reconcile_pending_entitlements(counts)

        assert counts["pending_resolved"] == 1
        apply_plan.assert_not_called()


class TestWireTypeFollowsMetric:
    """A count is int64 on scale_gateway_request and double on payg's gateway_request."""

    @pytest.fixture(autouse=True)
    def _configured_service_control(self):
        # build_operation qualifies metric ids with the service name. The
        # module singleton read the (unset) test setting at import time, so
        # hand the usage module a configured instance instead.
        from accounts.services.gcp_service_control import GCPServiceControlService

        configured = GCPServiceControlService(
            service_name="futureagi.endpoints.test.cloud.goog"
        )
        with patch.object(usage, "gcp_service_control", configured):
            yield

    @staticmethod
    def _operation(metric_id, quantity):
        now = timezone.now()
        entitlement = SimpleNamespace(usage_reporting_id="project_number:1")
        checkpoint = SimpleNamespace(
            operation_id="op",
            window_start=now - timedelta(hours=1),
            window_end=now,
            quantity_reported=Decimal(quantity),
            metric="gateway_requests",
        )
        with patch.object(usage, "_org_label", return_value="org"):
            return usage._operation_for(entitlement, checkpoint, metric_id)

    @pytest.mark.parametrize(
        "metric_id, expected_key",
        [
            ("gateway_request", "doubleValue"),  # payg: DOUBLE in the service config
            ("scale_gateway_request", "int64Value"),
            ("enterprise_gateway_request", "int64Value"),
            ("payg_storage", "doubleValue"),
            ("scale_storage", "doubleValue"),
            ("payg_cache_hits", "int64Value"),
            ("scale_credits", "int64Value"),
        ],
    )
    def test_value_type_matches_service_config(self, metric_id, expected_key):
        op = self._operation(metric_id, "7")
        (value,) = op["metricValueSets"][0]["metricValues"]
        assert list(value) == [expected_key]

    def test_double_metric_carries_a_float(self):
        op = self._operation("gateway_request", "7")
        assert op["metricValueSets"][0]["metricValues"][0] == {"doubleValue": 7.0}

    def test_int_metric_carries_a_string_int64(self):
        op = self._operation("scale_gateway_request", "7")
        assert op["metricValueSets"][0]["metricValues"][0] == {"int64Value": "7"}
