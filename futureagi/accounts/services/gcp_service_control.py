"""Service Control client for reporting Marketplace usage to Google.

We send quantities, never money. Google multiplies by the rate configured in
Producer Portal and bills the customer, so whatever is sent here becomes the
invoice with no review step in between.

Google documents no deduplication on operationId, and its own reference
implementation sends a random UUID. The checkpoint table, not this client, is
what prevents a window being reported twice.
"""

import json

import structlog
from django.conf import settings

logger = structlog.get_logger(__name__)

CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


class GCPServiceControlNotConfigured(RuntimeError):
    """Raised when the marketplace service name is absent, as on OSS and EE."""


class GCPServiceControlService:
    def __init__(self, service_name: str | None = None):
        self._service_name = service_name or settings.GCP_MARKETPLACE_SERVICE_NAME
        self._client = None

    @property
    def service_name(self) -> str:
        if not self._service_name:
            raise GCPServiceControlNotConfigured(
                "GCP_MARKETPLACE_SERVICE_NAME is not set"
            )
        return self._service_name

    def metric_name(self, metric_id: str) -> str:
        return f"{self.service_name}/{metric_id}"

    def _credentials(self):
        from google.oauth2 import service_account

        sa_json = settings.GCP_MARKETPLACE_SA_JSON
        if sa_json:
            return service_account.Credentials.from_service_account_info(
                json.loads(sa_json), scopes=[CLOUD_SCOPE]
            )

        import google.auth

        credentials, _ = google.auth.default(scopes=[CLOUD_SCOPE])
        return credentials

    @property
    def client(self):
        """Built on first use so importing this module needs no credentials."""
        if self._client is None:
            from googleapiclient.discovery import build

            self._client = build(
                "servicecontrol",
                "v1",
                credentials=self._credentials(),
                cache_discovery=False,
            )
        return self._client

    # def check(self, consumer_id: str, operation_id: str) -> list:
    #     """Whether a consumer is still entitled. Returns any check errors.

    #     The codelab calls this before reporting and skips the report when it
    #     returns errors, which is a second signal alongside Pub/Sub that a
    #     customer should lose access.
    #     """
    #     response = (
    #         self.client.services()
    #         .check(
    #             serviceName=self.service_name,
    #             body={
    #                 "operation": {
    #                     "operationId": operation_id,
    #                     "operationName": "check",
    #                     "consumerId": consumer_id,
    #                 }
    #             },
    #         )
    #         .execute()
    #     )
    #     return response.get("checkErrors") or []

    def report(
        self,
        consumer_id: str,
        operation_id: str,
        start_time: str,
        end_time: str,
        metric_values: dict[str, tuple[float, bool]],
        operation_name: str = "usage_report",
        user_labels: dict[str, str] | None = None,
    ) -> list:
        """Report usage for one consumer and window. Returns any report errors.

        An HTTP 200 does not mean the usage was accepted: per-operation failures
        come back in reportErrors. Treating a 200 as success would mark usage
        reported that Google rejected, and it would never be billed.
        """
        # Only storage and voice simulation accept floating point. Sending a
        # double where Google expects an int64 is rejected per-operation.
        metric_value_sets = [
            {
                "metricName": self.metric_name(metric_id),
                "metricValues": [
                    {"doubleValue": float(value)}
                    if is_float
                    else {"int64Value": str(int(value))}
                ],
            }
            for metric_id, (value, is_float) in metric_values.items()
        ]

        operation = {
            "operationId": operation_id,
            "operationName": operation_name,
            "consumerId": consumer_id,
            "startTime": start_time,
            "endTime": end_time,
            "metricValueSets": metric_value_sets,
        }
        # Forwarded to the customer's Cloud Billing cost-management tools for
        # attribution. Omitted entirely when empty rather than sent as {}.
        if user_labels:
            operation["userLabels"] = user_labels

        body = {"operations": [operation]}

        response = (
            self.client.services()
            .report(serviceName=self.service_name, body=body)
            .execute()
        )

        errors = response.get("reportErrors") or []
        if errors:
            logger.error(
                "gcp_marketplace_report_errors",
                consumer_id=consumer_id,
                operation_id=operation_id,
                errors=errors,
            )
        return errors


gcp_service_control = GCPServiceControlService()
