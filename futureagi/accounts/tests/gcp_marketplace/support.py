"""Fakes and payload builders shared by the GCP Marketplace suite.

Google is never called. The Procurement and Service Control singletons are
replaced by fakes that keep the real resource-name helpers and record every
write, so tests assert on what would have been sent.

Payload shapes follow what the production subscription actually delivered:
the events of one purchase share an eventId, cancellations carry their own,
and a Procurement GET returns the full resource, not the event body.
"""

from unittest.mock import MagicMock

from django.utils.dateparse import parse_datetime

from accounts.services.gcp_procurement import SIGNUP_APPROVAL
from accounts.services.gcp_service_control import ReportOutcome

PROVIDER_ID = "futureagiprimary"
PRODUCT = "futureagi.endpoints.futureagiprimary.cloud.goog"
ACCOUNT_ID = "1557400d-2c70-4abb-adf4-e5111a6a9393"
ENTITLEMENT_ID = "a8ad2d7f-4475-4bc2-886b-15bdd3ddb102"
USAGE_REPORTING_ID = "project_number:69718560399"
CREATE_TIME = "2026-09-09T15:40:00.568941Z"
UPDATE_TIME = "2026-09-09T15:40:03.439363Z"
PURCHASE_EVENT_ID = "CREATE_ENTITLEMENT-344268f5-c72e-4570-91a1-e1045965cc1d"

REQUESTED = "ENTITLEMENT_ACTIVATION_REQUESTED"


def google_time(value: str):
    return parse_datetime(value)


def remote_entitlement(
    entitlement_id: str = ENTITLEMENT_ID,
    *,
    state: str = REQUESTED,
    plan: str = "payg",
    account_id: str = ACCOUNT_ID,
    update_time: str = UPDATE_TIME,
    usage_reporting_id: str = USAGE_REPORTING_ID,
    **extra,
) -> dict:
    """A Procurement `entitlements.get` response."""
    payload = {
        "name": f"providers/{PROVIDER_ID}/entitlements/{entitlement_id}",
        "account": f"providers/{PROVIDER_ID}/accounts/{account_id}",
        "provider": PROVIDER_ID,
        "product": PRODUCT,
        "productExternalName": PRODUCT,
        "plan": plan,
        "state": state,
        "createTime": CREATE_TIME,
        "updateTime": update_time,
        "usageReportingId": usage_reporting_id,
        "orderId": entitlement_id,
        "offer": (
            f"projects/641933367818/services/{PRODUCT}/standardOffers/"
            "84d66619-5945-4905-a63e-1dd495185dbb"
        ),
    }
    payload.update(extra)
    return payload


def remote_account(
    account_id: str = ACCOUNT_ID,
    *,
    state: str = "ACCOUNT_ACTIVE",
    signup_pending: bool = True,
) -> dict:
    """A Procurement `accounts.get` response."""
    return {
        "name": f"providers/{PROVIDER_ID}/accounts/{account_id}",
        "provider": PROVIDER_ID,
        "state": state,
        "approvals": [
            {
                "name": SIGNUP_APPROVAL,
                "state": "PENDING" if signup_pending else "APPROVED",
                "updateTime": CREATE_TIME,
            }
        ],
        "createTime": CREATE_TIME,
        "updateTime": CREATE_TIME,
    }


def event(
    event_type: str,
    *,
    entitlement_id: str | None = None,
    account_id: str | None = None,
    event_id: str = PURCHASE_EVENT_ID,
) -> dict:
    """A Pub/Sub message body as Google publishes it."""
    payload = {"eventId": event_id, "eventType": event_type, "providerId": PROVIDER_ID}
    if entitlement_id:
        payload["entitlement"] = {"id": entitlement_id, "updateTime": UPDATE_TIME}
    if account_id:
        payload["account"] = {"id": account_id, "updateTime": CREATE_TIME}
    return payload


class FakeProcurement:
    """Stands in for the `gcp_procurement` singleton.

    Reads return whatever the test assigns; writes are MagicMocks so tests
    assert on the exact call. The name helpers are real: handlers depend on
    `bare_id` to link accounts and the fake must agree with production.
    """

    provider_id = PROVIDER_ID

    def __init__(self):
        self.get_entitlement = MagicMock(return_value=remote_entitlement())
        self.get_account = MagicMock(return_value=remote_account())
        self.approve_account = MagicMock(return_value={})
        self.approve_entitlement = MagicMock(return_value={})
        self.approve_plan_change = MagicMock(return_value={})
        self.reject_entitlement = MagicMock(return_value={})
        self.list_entitlements = MagicMock(return_value={"entitlements": []})
        self.iter_entitlements = MagicMock(side_effect=lambda **kwargs: iter(()))

    @staticmethod
    def bare_id(resource_name: str) -> str:
        return (resource_name or "").rsplit("/", 1)[-1]

    def account_name(self, account_id: str) -> str:
        return f"providers/{PROVIDER_ID}/accounts/{account_id}"

    def entitlement_name(self, entitlement_id: str) -> str:
        return f"providers/{PROVIDER_ID}/entitlements/{entitlement_id}"

    @staticmethod
    def pending_approval(account: dict, approval_name: str = SIGNUP_APPROVAL) -> bool:
        for approval in account.get("approvals") or []:
            if approval.get("name") == approval_name:
                return approval.get("state") == "PENDING"
        return False


class FakeServiceControl:
    """Stands in for the `gcp_service_control` singleton.

    `build_operation` mirrors the production shape so tests can read the
    consumer id, metric names and values off what `report` received.
    """

    def __init__(self):
        self.check = MagicMock(return_value=[])
        self.report = MagicMock(return_value=ReportOutcome())
        self.is_definitive_failure = MagicMock(return_value=False)

    def metric_name(self, metric_id: str) -> str:
        return f"{PRODUCT}/{metric_id}"

    def build_operation(
        self,
        consumer_id: str,
        operation_id: str,
        start_time: str,
        end_time: str,
        metric_values: dict[str, tuple[float, bool]],
        operation_name: str = "usage_report",
    ) -> dict:
        return {
            "operationId": operation_id,
            "operationName": operation_name,
            "consumerId": consumer_id,
            "startTime": start_time,
            "endTime": end_time,
            "metricValueSets": [
                {
                    "metricName": self.metric_name(metric_id),
                    "metricValues": [
                        {"doubleValue": float(value)}
                        if is_float
                        else {"int64Value": str(int(value))}
                    ],
                }
                for metric_id, (value, is_float) in metric_values.items()
            ],
        }
