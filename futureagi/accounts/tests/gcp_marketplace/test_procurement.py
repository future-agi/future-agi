"""Procurement API client: resource names, request bodies, plan resolution.

No Google calls. The discovery client is replaced with a mock so the exact
request each method builds can be asserted.
"""

from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from django.test import override_settings

from accounts.services.gcp_procurement import (
    SIGNUP_APPROVAL,
    GCPProcurementService,
    base_plan_id,
    metric_id_for,
    resolve_plan,
)

pytestmark = [pytest.mark.unit]

PROVIDER = "futureagiprimary"


@pytest.fixture
def service():
    return GCPProcurementService(provider_id=PROVIDER)


@pytest.fixture
def client(service):
    """The discovery client, with reads and writes returning canned dicts."""
    fake = MagicMock()
    with (
        patch.object(
            GCPProcurementService,
            "client",
            new_callable=PropertyMock,
            return_value=fake,
        ),
        patch.object(
            service, "_read", side_effect=lambda request: {"request": request}
        ),
        patch.object(
            service, "_write", side_effect=lambda request: {"request": request}
        ),
    ):
        yield fake


def entitlements(client):
    return client.providers.return_value.entitlements.return_value


def accounts(client):
    return client.providers.return_value.accounts.return_value


class TestResourceNames:
    @pytest.mark.parametrize(
        "value, expected",
        [
            (f"providers/{PROVIDER}/accounts/E-1234", "E-1234"),
            (f"providers/{PROVIDER}/entitlements/abc", "abc"),
            ("bare-id", "bare-id"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_bare_id_takes_the_last_segment(self, value, expected):
        assert GCPProcurementService.bare_id(value) == expected

    def test_names_are_built_from_the_provider(self, service):
        assert service.account_name("E-1") == f"providers/{PROVIDER}/accounts/E-1"
        assert service.entitlement_name("x") == f"providers/{PROVIDER}/entitlements/x"


class TestPendingApproval:
    def test_pending_signup_approval(self):
        account = {"approvals": [{"name": SIGNUP_APPROVAL, "state": "PENDING"}]}
        assert GCPProcurementService.pending_approval(account) is True

    def test_granted_signup_approval(self):
        account = {"approvals": [{"name": SIGNUP_APPROVAL, "state": "APPROVED"}]}
        assert GCPProcurementService.pending_approval(account) is False

    def test_no_approvals_means_nothing_to_grant(self):
        assert GCPProcurementService.pending_approval({}) is False


class TestRequests:
    def test_account_approval_names_the_signup_approval(self, service, client):
        service.approve_account("E-1")

        accounts(client).approve.assert_called_once_with(
            name=f"providers/{PROVIDER}/accounts/E-1",
            body={"approvalName": SIGNUP_APPROVAL},
        )

    def test_entitlement_approval_sends_an_empty_body_by_default(self, service, client):
        service.approve_entitlement("new")

        entitlements(client).approve.assert_called_once_with(
            name=f"providers/{PROVIDER}/entitlements/new", body={}
        )

    def test_migrated_entitlement_is_sent_as_a_resource_name(self, service, client):
        service.approve_entitlement("new", migrated_from_entitlement_id="old")

        entitlements(client).approve.assert_called_once_with(
            name=f"providers/{PROVIDER}/entitlements/new",
            body={"entitlementMigrated": f"providers/{PROVIDER}/entitlements/old"},
        )

    def test_plan_change_approval_names_the_pending_plan(self, service, client):
        service.approve_plan_change("e-1", "scale-P1Y")

        entitlements(client).approvePlanChange.assert_called_once_with(
            name=f"providers/{PROVIDER}/entitlements/e-1",
            body={"pendingPlanName": "scale-P1Y"},
        )

    def test_rejection_carries_the_reason(self, service, client):
        service.reject_entitlement("e-1", "duplicate")

        entitlements(client).reject.assert_called_once_with(
            name=f"providers/{PROVIDER}/entitlements/e-1", body={"reason": "duplicate"}
        )

    def test_listing_filters_by_the_bare_account_id(self, service, client):
        service.list_entitlements(account_id="E-1")

        entitlements(client).list.assert_called_once_with(
            parent=f"providers/{PROVIDER}", pageToken=None, filter="account=E-1"
        )

    def test_listing_without_an_account_has_no_filter(self, service, client):
        service.list_entitlements()

        entitlements(client).list.assert_called_once_with(
            parent=f"providers/{PROVIDER}", pageToken=None
        )

    def test_iteration_follows_pagination(self, service):
        pages = [
            {"entitlements": [{"name": "a"}, {"name": "b"}], "nextPageToken": "p2"},
            {"entitlements": [{"name": "c"}]},
        ]
        with patch.object(service, "list_entitlements", side_effect=pages) as listing:
            names = [e["name"] for e in service.iter_entitlements(account_id="E-1")]

        assert names == ["a", "b", "c"]
        assert [call.kwargs["page_token"] for call in listing.call_args_list] == [
            None,
            "p2",
        ]


class TestPlanResolution:
    @pytest.mark.parametrize(
        "plan_id, expected",
        [
            ("payg", ("payg", "monthly")),
            ("scale", ("scale", "monthly")),
            ("scale-P1Y", ("scale", "annual")),
            ("enterprise-P1Y", ("enterprise", "annual")),
        ],
    )
    def test_portal_plans_map_to_internal_plan_and_interval(self, plan_id, expected):
        assert resolve_plan(plan_id) == expected

    @pytest.mark.parametrize("plan_id", ["enterprise", "legacy-gold", "", None])
    def test_unmapped_plan_raises_rather_than_defaulting(self, plan_id):
        with pytest.raises(ValueError, match="Unmapped"):
            resolve_plan(plan_id)

    def test_annual_variants_share_the_base_plan_metrics(self):
        assert base_plan_id("scale-P1Y") == "scale"
        assert base_plan_id("payg") == "payg"
        assert metric_id_for("scale-P1Y", "storage") == metric_id_for(
            "scale", "storage"
        )

    def test_metric_ids_are_per_plan(self):
        assert metric_id_for("payg", "ai_credits") == "payg_credits"
        assert metric_id_for("payg", "gateway_requests") == "gateway_request"
        assert metric_id_for("scale", "gateway_requests") == "scale_gateway_request"

    def test_unknown_plan_or_dimension_is_not_billed(self):
        assert metric_id_for("legacy-gold", "ai_credits") is None
        assert metric_id_for("payg", "seats") is None

    @override_settings(GCP_MARKETPLACE_METRIC_MAP={"payg": {"ai_credits": "credits"}})
    def test_dimension_left_out_of_the_map_is_skipped(self):
        assert metric_id_for("payg", "ai_credits") == "credits"
        assert metric_id_for("payg", "storage") is None
