"""Worker boundary tests use no provider, ClickHouse or production database."""

import uuid
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory

from tracer.services.grouping.control import GroupingConflict
from tracer.views.trace_grouping import (
    ClaimGroupingView,
    ReserveGroupingCallView,
    SettleGroupingCallView,
)
from tracer.views.trace_severity import ReserveSeverityView, SettleSeverityView


@override_settings(INTERNAL_API_SECRET="test-grouping-secret")
class GroupingControlApiTests(SimpleTestCase):
    def test_severity_accounting_dispatch_does_not_shadow_response_operation(self):
        job_id = uuid.uuid4()
        common = {
            "lease_token": "lease",
            "request_key": f"severity:{job_id}:fixture",
            "request_digest": "sha256:" + "a" * 64,
        }
        for view, extra in (
            (ReserveSeverityView, {"max_cost_usd": "0.100000000"}),
            (SettleSeverityView, {"status": "unknown", "result": None}),
        ):
            with patch(
                "tracer.views.trace_severity.severity.account_call",
                return_value={"status": "unknown"},
            ) as service:
                response = view.as_view()(
                    self.request({**common, **extra}), job_id=job_id
                )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, {"status": "unknown"})
            self.assertEqual(service.call_args.kwargs["job_id"], job_id)
            self.assertTrue(callable(service.call_args.kwargs["accounting_operation"]))

    def request(self, data, authenticated=True):
        return APIRequestFactory().post(
            "/grouping/claims/",
            data,
            format="json",
            **(
                {"HTTP_AUTHORIZATION": "Bearer test-grouping-secret"}
                if authenticated
                else {}
            ),
        )

    def test_claim_requires_service_authentication(self):
        with patch(
            "tracer.views.trace_grouping.control.claim_grouping_work"
        ) as service:
            response = ClaimGroupingView.as_view()(
                self.request({"worker_id": "worker", "limit": 1}, False)
            )
        self.assertEqual(response.status_code, 403)
        service.assert_not_called()

    def test_claim_validates_unknown_fields_and_bounds_before_service(self):
        for body in (
            {"worker_id": "worker", "limit": 100},
            {"worker_id": "worker", "limit": 1, "project_id": "override"},
        ):
            with patch(
                "tracer.views.trace_grouping.control.claim_grouping_work"
            ) as service:
                response = ClaimGroupingView.as_view()(self.request(body))
            self.assertEqual(response.status_code, 400)
            service.assert_not_called()

    def test_claim_returns_service_owned_scoped_snapshot(self):
        with patch(
            "tracer.views.trace_grouping.control.claim_grouping_work",
            return_value={"claims": []},
        ) as service:
            response = ClaimGroupingView.as_view()(
                self.request({"worker_id": "worker", "limit": 1})
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"claims": []})
        service.assert_called_once_with(worker_id="worker", limit=1)

    def test_cost_is_decimal_and_conflict_is_not_success(self):
        digest = "sha256:" + "a" * 64
        body = {
            "lease_token": "lease",
            "request_key": digest,
            "request_digest": digest,
            "max_cost_usd": "0.100000001",
        }
        with patch(
            "tracer.views.trace_grouping.accounting.reserve_call",
            side_effect=GroupingConflict("budget exhausted"),
        ) as service:
            response = ReserveGroupingCallView.as_view()(
                self.request(body), attempt_id=uuid.uuid4()
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            service.call_args.kwargs["max_cost_usd"], Decimal("0.100000001")
        )

    def test_unknown_usage_and_result_are_preserved(self):
        digest = "sha256:" + "a" * 64
        body = {
            "lease_token": "lease",
            "request_key": digest,
            "request_digest": digest,
            "status": "unknown",
            "result": {"decision": "defer"},
            "model_used": None,
            "cost_usd": None,
            "input_tokens": None,
            "output_tokens": None,
        }
        with patch(
            "tracer.views.trace_grouping.accounting.settle_call",
            return_value={"status": "unknown"},
        ) as service:
            response = SettleGroupingCallView.as_view()(
                self.request(body), attempt_id=uuid.uuid4()
            )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(service.call_args.kwargs["model_used"])
        self.assertIsNone(service.call_args.kwargs["cost_usd"])
        self.assertEqual(service.call_args.kwargs["result"], {"decision": "defer"})
