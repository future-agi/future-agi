"""Usage reporting to Service Control and the daily reconcile.

Windows are pinned with `_window_end` so every test is independent of the
clock. Quantities come from UsageSummary rows written directly.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.conf import settings
from django.utils import timezone

from accounts.gcp_marketplace_usage import (
    _reconcile_entitlement,
    billable_entitlements,
    report_all_usage,
    report_entitlement_usage,
    report_final_window,
)
from accounts.models.gcp_marketplace import (
    GCPMarketplaceEntitlement,
    GCPMarketplaceEntitlementState,
    GCPMarketplaceUsageCheckpoint,
    GCPUsageReportStatus,
)
from accounts.services.gcp_service_control import ReportOutcome
from accounts.tests.gcp_marketplace.support import (
    ENTITLEMENT_ID,
    USAGE_REPORTING_ID,
    FakeServiceControl,
)
from ee.usage.models.usage import UsageSummary

pytestmark = [pytest.mark.integration, pytest.mark.requires_ee, pytest.mark.django_db]

REPORTED = GCPUsageReportStatus.REPORTED
PENDING = GCPUsageReportStatus.PENDING
FAILED = GCPUsageReportStatus.FAILED

T0 = datetime(2026, 9, 9, 18, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)
PERIOD = "2026-09"
PERIOD_START = datetime(2026, 9, 1, tzinfo=UTC)
ALLOWANCE = {"ai_credits": Decimal("100")}
CONTAINER_LABEL = "cloudmarketplace.googleapis.com/container_name"


@pytest.fixture
def service_control():
    fake = FakeServiceControl()
    with patch("accounts.gcp_marketplace_usage.gcp_service_control", fake):
        yield fake


@pytest.fixture
def alerts():
    with patch("accounts.gcp_marketplace_usage.capture_message") as mocked:
        yield mocked


@pytest.fixture(autouse=True)
def allowance():
    with patch(
        "accounts.gcp_marketplace_usage._free_allowance",
        side_effect=lambda dimension, plan: ALLOWANCE.get(dimension, Decimal(0)),
    ):
        yield


def usage(organization, **totals):
    for dimension, total in totals.items():
        UsageSummary.objects.update_or_create(
            organization=organization,
            dimension=dimension,
            period=PERIOD,
            defaults={
                "total_usage": Decimal(str(total)),
                "last_flushed_at": timezone.now(),
            },
        )


def checkpoints(entitlement) -> dict[str, GCPMarketplaceUsageCheckpoint]:
    return {
        c.metric: c
        for c in GCPMarketplaceUsageCheckpoint.objects.filter(entitlement=entitlement)
    }


def checkpoint(entitlement, metric, status, quantity="0", window_start=PERIOD_START):
    return GCPMarketplaceUsageCheckpoint.objects.create(
        organization=entitlement.organization,
        entitlement=entitlement,
        metric=metric,
        window_start=window_start,
        window_end=T0,
        quantity_reported=Decimal(quantity),
        operation_id=f"op-{metric}-{status}",
        report_status=status,
        error_detail="",
    )


def metric_of(operation) -> str:
    return operation["metricValueSets"][0]["metricName"].rsplit("/", 1)[-1]


def values_of(operation) -> list:
    return operation["metricValueSets"][0]["metricValues"]


class TestReportEntitlementUsage:
    def test_reports_every_metric_above_its_allowance(
        self, entitlement, service_control, alerts
    ):
        usage(
            entitlement.organization,
            ai_credits=150,
            storage="1.5",
            text_sim_tokens="10.7",
        )

        assert report_entitlement_usage(entitlement, _window_end=T0) == 6

        rows = checkpoints(entitlement)
        assert set(rows) == set(settings.GCP_MARKETPLACE_DIMENSIONS)
        assert rows["ai_credits"].quantity_reported == Decimal("50")
        assert rows["storage"].quantity_reported == Decimal("1.5")
        assert rows["text_sim_tokens"].quantity_reported == Decimal("10")
        assert rows["gateway_requests"].quantity_reported == Decimal("0")
        assert all(
            row.report_status == REPORTED
            and row.window_start == PERIOD_START
            and row.window_end == T0
            and row.reported_at is not None
            for row in rows.values()
        )

        service_control.check.assert_called_once()
        (operations,), kwargs = service_control.report.call_args
        by_metric = {metric_of(op): op for op in operations}
        assert set(by_metric) == set(
            settings.GCP_MARKETPLACE_METRIC_MAP["payg"].values()
        )
        assert {op["consumerId"] for op in operations} == {USAGE_REPORTING_ID}
        assert values_of(by_metric["payg_credits"]) == [{"int64Value": "50"}]
        assert values_of(by_metric["payg_storage"]) == [{"doubleValue": 1.5}]
        assert kwargs["user_labels"] == {CONTAINER_LABEL: "test-organization"}
        alerts.assert_not_called()

    def test_the_same_window_is_never_sent_twice(self, entitlement, service_control):
        usage(entitlement.organization, ai_credits=150)

        assert report_entitlement_usage(entitlement, _window_end=T0) == 6
        assert report_entitlement_usage(entitlement, _window_end=T0) == 0

        assert GCPMarketplaceUsageCheckpoint.objects.count() == 6
        assert service_control.report.call_count == 1

    def test_the_next_window_reports_only_the_increase(
        self, entitlement, service_control
    ):
        usage(entitlement.organization, ai_credits=150)
        report_entitlement_usage(entitlement, _window_end=T0)
        usage(entitlement.organization, ai_credits=175)

        assert report_entitlement_usage(entitlement, _window_end=T1) == 6

        second = GCPMarketplaceUsageCheckpoint.objects.get(
            entitlement=entitlement, metric="ai_credits", window_start=T0
        )
        assert second.quantity_reported == Decimal("25")
        assert second.window_end == T1

    def test_usage_inside_the_allowance_reports_zero(
        self, entitlement, service_control
    ):
        usage(entitlement.organization, ai_credits=80)

        report_entitlement_usage(entitlement, _window_end=T0)

        assert checkpoints(entitlement)["ai_credits"].quantity_reported == Decimal("0")

    def test_check_failure_marks_every_window_failed_and_sends_nothing(
        self, entitlement, service_control
    ):
        usage(entitlement.organization, ai_credits=150)
        service_control.check.return_value = [{"code": "CONSUMER_INVALID"}]

        assert report_entitlement_usage(entitlement, _window_end=T0) == 0

        rows = checkpoints(entitlement)
        assert {row.report_status for row in rows.values()} == {FAILED}
        assert all("CONSUMER_INVALID" in row.error_detail for row in rows.values())
        service_control.report.assert_not_called()

    def test_a_rejected_operation_fails_only_its_own_metric(
        self, entitlement, service_control
    ):
        usage(entitlement.organization, ai_credits=150)
        service_control.report.side_effect = lambda operations, user_labels=None: (
            ReportOutcome(
                rejected={
                    op["operationId"]
                    for op in operations
                    if metric_of(op) == "payg_credits"
                }
            )
        )

        assert report_entitlement_usage(entitlement, _window_end=T0) == 5

        rows = checkpoints(entitlement)
        assert rows["ai_credits"].report_status == FAILED
        assert rows["ai_credits"].error_detail == "rejected by Service Control"
        assert {
            row.report_status for metric, row in rows.items() if metric != "ai_credits"
        } == {REPORTED}

    def test_an_error_naming_no_operation_leaves_windows_unresolved_and_pages(
        self, entitlement, service_control, alerts
    ):
        usage(entitlement.organization, ai_credits=150)
        service_control.report.return_value = ReportOutcome(
            unattributed=[{"description": "quota exceeded"}]
        )

        assert report_entitlement_usage(entitlement, _window_end=T0) == 0

        rows = checkpoints(entitlement)
        assert {row.report_status for row in rows.values()} == {PENDING}
        alerts.assert_called_once()
        assert (
            alerts.call_args.kwargs["tags"]["alarm"]
            == "gcp_marketplace_usage_unresolved"
        )

    def test_transport_failure_with_unknown_outcome_stays_pending(
        self, entitlement, service_control, alerts
    ):
        usage(entitlement.organization, ai_credits=150)
        service_control.report.side_effect = ConnectionError("connection reset")

        with pytest.raises(ConnectionError):
            report_entitlement_usage(entitlement, _window_end=T0)

        assert {row.report_status for row in checkpoints(entitlement).values()} == {
            PENDING
        }
        alerts.assert_called_once()

    def test_definitive_failure_is_marked_failed(
        self, entitlement, service_control, alerts
    ):
        usage(entitlement.organization, ai_credits=150)
        service_control.report.side_effect = RuntimeError("400 invalid metric")
        service_control.is_definitive_failure.return_value = True

        with pytest.raises(RuntimeError):
            report_entitlement_usage(entitlement, _window_end=T0)

        assert {row.report_status for row in checkpoints(entitlement).values()} == {
            FAILED
        }
        alerts.assert_not_called()

    def test_an_unresolved_window_is_never_resent(self, entitlement, service_control):
        stuck = checkpoint(entitlement, "ai_credits", PENDING, quantity="9")
        usage(entitlement.organization, ai_credits=150)

        assert report_entitlement_usage(entitlement, _window_end=T0) == 5

        stuck.refresh_from_db()
        assert stuck.report_status == PENDING
        assert stuck.operation_id == f"op-ai_credits-{PENDING}"

    def test_without_a_consumer_id_nothing_is_reported(
        self, entitlement, service_control
    ):
        entitlement.usage_reporting_id = ""
        entitlement.save(update_fields=["usage_reporting_id"])

        assert report_entitlement_usage(entitlement, _window_end=T0) == 0

        assert not GCPMarketplaceUsageCheckpoint.objects.exists()
        service_control.check.assert_not_called()

    def test_unmapped_plan_reports_nothing(self, entitlement, service_control):
        entitlement.plan_id = "legacy-gold"
        entitlement.save(update_fields=["plan_id"])

        assert report_entitlement_usage(entitlement, _window_end=T0) == 0
        assert not GCPMarketplaceUsageCheckpoint.objects.exists()


class TestFinalWindow:
    def test_ends_a_second_before_the_cancellation_and_skips_check(
        self, entitlement, service_control
    ):
        cancelled_at = datetime(2026, 9, 9, 18, 15, 6, tzinfo=UTC)
        entitlement.status = GCPMarketplaceEntitlementState.CANCELLED
        entitlement.google_update_time = cancelled_at
        entitlement.save(update_fields=["status", "google_update_time"])
        usage(entitlement.organization, ai_credits=150)

        assert report_final_window(entitlement) == 6

        service_control.check.assert_not_called()
        rows = checkpoints(entitlement)
        assert {row.window_end for row in rows.values()} == {
            cancelled_at - timedelta(seconds=1)
        }
        assert rows["ai_credits"].quantity_reported == Decimal("50")


class TestBillableEntitlements:
    def test_one_entitlement_per_organization_oldest_first(self, entitlement):
        GCPMarketplaceEntitlement.objects.create(
            entitlement_id="second-purchase",
            account=entitlement.account,
            organization=entitlement.organization,
            plan_id="scale",
            status=GCPMarketplaceEntitlementState.ACTIVE,
            usage_reporting_id="project_number:2",
            effective_at=entitlement.effective_at + timedelta(hours=1),
        )

        assert [e.entitlement_id for e in billable_entitlements()] == [ENTITLEMENT_ID]

    def test_requires_a_consumer_id_unless_told_otherwise(self, entitlement):
        entitlement.usage_reporting_id = ""
        entitlement.save(update_fields=["usage_reporting_id"])

        assert billable_entitlements() == []
        assert billable_entitlements(require_consumer_id=False) == [entitlement]

    def test_ignores_rows_out_of_service(self, entitlement):
        entitlement.status = GCPMarketplaceEntitlementState.CANCELLED
        entitlement.save(update_fields=["status"])

        assert billable_entitlements() == []


class TestReportAllUsage:
    def test_reports_each_billable_entitlement_and_resends_failed_tails(
        self, entitlement, service_control, alerts
    ):
        cancelled = GCPMarketplaceEntitlement.objects.create(
            entitlement_id="cancelled-1",
            account=entitlement.account,
            organization=entitlement.organization,
            plan_id="payg",
            status=GCPMarketplaceEntitlementState.CANCELLED,
            usage_reporting_id="project_number:3",
            effective_at=entitlement.effective_at,
            google_update_time=entitlement.google_update_time,
        )
        tail = checkpoint(cancelled, "ai_credits", FAILED, quantity="7")

        result = report_all_usage()

        assert result == {
            "entitlements": 1,
            "metrics": 6,
            "failures": 0,
            "final_windows_resent": 1,
            "final_window_failures": 0,
        }
        tail.refresh_from_db()
        assert tail.report_status == REPORTED
        assert tail.operation_id != f"op-ai_credits-{FAILED}"
        assert tail.quantity_reported == Decimal("7")

    def test_one_failing_entitlement_does_not_stop_the_sweep(
        self, entitlement, service_control, alerts
    ):
        service_control.report.side_effect = RuntimeError("boom")
        service_control.is_definitive_failure.return_value = True

        result = report_all_usage()

        assert result["failures"] == 1
        assert result["entitlements"] == 0


class TestReconcile:
    def test_fractional_float_usage_reported_exactly_is_not_a_discrepancy(
        self, entitlement
    ):
        usage(entitlement.organization, storage="1.5")
        checkpoint(entitlement, "storage", REPORTED, quantity="1.5")

        assert _reconcile_entitlement(entitlement, PERIOD) == []

    def test_integer_usage_is_compared_after_allowance_and_floor(self, entitlement):
        usage(entitlement.organization, ai_credits="150.9")
        checkpoint(entitlement, "ai_credits", REPORTED, quantity="40")

        (discrepancy,) = _reconcile_entitlement(entitlement, PERIOD)

        assert discrepancy["metric"] == "ai_credits"
        assert Decimal(discrepancy["ledger"]) == 50
        assert Decimal(discrepancy["reported"]) == 40
        assert Decimal(discrepancy["difference"]) == 10

    def test_only_reported_windows_count_as_reported(self, entitlement):
        usage(entitlement.organization, ai_credits=150)
        checkpoint(entitlement, "ai_credits", PENDING, quantity="50")

        (discrepancy,) = _reconcile_entitlement(entitlement, PERIOD)

        assert Decimal(discrepancy["reported"]) == 0

    def test_unmapped_plan_is_skipped(self, entitlement):
        entitlement.plan_id = "legacy-gold"
        entitlement.save(update_fields=["plan_id"])

        assert _reconcile_entitlement(entitlement, PERIOD) == []
