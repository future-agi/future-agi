"""Service Control client for reporting Marketplace usage to Google.

We send quantities, never money. Google multiplies by the rate configured in
Producer Portal and bills the customer, so whatever is sent here becomes the
invoice with no review step in between.

Google documents no deduplication on operationId, and its own reference
implementation sends a random UUID. The checkpoint table, not this client, is
what prevents a window being reported twice.
"""

import json
import socket
import threading
from dataclasses import dataclass, field

import structlog
from django.conf import settings

logger = structlog.get_logger(__name__)

CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# Bounded so a hung socket cannot outlive the Temporal heartbeat that runs the
# caller. The drain heartbeats between messages, not during one, so an
# unbounded call here would fail the activity while the thread stays blocked.
REQUEST_TIMEOUT_SECONDS = 30

# Reads are retried on 5xx/429. report is not: a retry after a lost response
# is exactly the double-charge the checkpoint table exists to prevent.
READ_RETRIES = 3

# 4xx codes that do not prove the request was refused.
UNKNOWN_OUTCOME_STATUSES = frozenset({408, 429})


class GCPServiceControlNotConfigured(RuntimeError):
    """Raised when the marketplace service name is absent, as on OSS and EE."""


@dataclass
class ReportOutcome:
    """Ids Google named in reportErrors, and any error naming no operation."""

    rejected: set[str] = field(default_factory=set)
    unattributed: list[dict] = field(default_factory=list)

    @property
    def outcome_known(self) -> bool:
        return not self.unattributed


class GCPServiceControlService:
    def __init__(self, service_name: str | None = None):
        self._service_name = service_name or settings.GCP_MARKETPLACE_SERVICE_NAME
        self._client = None
        self._credentials_cache = None
        self._local = threading.local()

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
        if self._credentials_cache is not None:
            return self._credentials_cache

        from google.oauth2 import service_account

        sa_json = settings.GCP_MARKETPLACE_SA_JSON
        if sa_json:
            self._credentials_cache = (
                service_account.Credentials.from_service_account_info(
                    json.loads(sa_json), scopes=[CLOUD_SCOPE]
                )
            )
        else:
            import google.auth

            self._credentials_cache, _ = google.auth.default(scopes=[CLOUD_SCOPE])
        return self._credentials_cache

    def _authorized_http(self):
        import httplib2
        from google_auth_httplib2 import AuthorizedHttp

        return AuthorizedHttp(
            self._credentials(), http=httplib2.Http(timeout=REQUEST_TIMEOUT_SECONDS)
        )

    def _http(self):
        """One transport per thread.

        httplib2.Http is not thread-safe, and the hourly report and a
        cancellation's final report can run at the same time in one worker.
        """
        http = getattr(self._local, "http", None)
        if http is None:
            http = self._local.http = self._authorized_http()
        return http

    @property
    def client(self):
        """Built on first use so importing this module needs no credentials."""
        if self._client is None:
            from googleapiclient.discovery import build

            self._client = build(
                "servicecontrol",
                "v1",
                http=self._authorized_http(),
                cache_discovery=False,
            )
        return self._client

    @staticmethod
    def is_definitive_failure(exc: BaseException) -> bool:
        """Whether Google provably did not record the request.

        True for a 4xx (Google answered and refused) and for failures raised
        before a byte was sent (unknown host, connection refused). Everything
        else, timeouts and 5xx included, may have been processed: the caller
        must treat those as unknown, not failed.
        """
        from googleapiclient.errors import HttpError
        from httplib2 import ServerNotFoundError

        if isinstance(exc, HttpError):
            # 408 and 429 sit in the 4xx range but describe a server-side
            # condition, not a rejected request, so the operation may still
            # have been recorded. Unknown, like a 5xx.
            if exc.status_code in UNKNOWN_OUTCOME_STATUSES:
                return False
            return 400 <= exc.status_code < 500
        return isinstance(
            exc, (ServerNotFoundError, ConnectionRefusedError, socket.gaierror)
        )

    def build_operation(
        self,
        consumer_id: str,
        operation_id: str,
        start_time: str,
        end_time: str,
        metric_values: dict[str, tuple[float, bool]],
        operation_name: str = "usage_report",
    ) -> dict:
        """Build the operation sent to check and then to report.

        One operation for both calls, so what was checked is what gets billed.
        """
        # Only storage and voice simulation accept floating point. Sending a
        # double where Google expects an int64 is rejected per-operation.
        metric_value_sets = [
            {
                "metricName": self.metric_name(metric_id),
                "metricValues": [
                    (
                        {"doubleValue": float(value)}
                        if is_float
                        else {"int64Value": str(int(value))}
                    )
                ],
            }
            for metric_id, (value, is_float) in metric_values.items()
        ]

        return {
            "operationId": operation_id,
            "operationName": operation_name,
            "consumerId": consumer_id,
            "startTime": start_time,
            "endTime": end_time,
            "metricValueSets": metric_value_sets,
        }

    def check(self, operation: dict) -> list:
        """Whether Google still considers this consumer entitled.

        Returns any check errors. A non-empty list means the report must be
        skipped: it is Google's live answer, independent of whether our Pub/Sub
        consumer has kept the entitlement state current.
        """
        # check rejects userLabels. The caller passes the pre-report operation,
        # but strip defensively so a future caller cannot break the check call.
        body = {k: v for k, v in operation.items() if k != "userLabels"}

        response = (
            self.client.services()
            .check(serviceName=self.service_name, body={"operation": body})
            .execute(http=self._http(), num_retries=READ_RETRIES)
        )

        errors = response.get("checkErrors") or []
        if errors:
            logger.warning(
                "gcp_marketplace_check_errors",
                consumer_id=operation.get("consumerId"),
                operation_id=operation.get("operationId"),
                errors=errors,
            )
        return errors

    def report(
        self, operations: list[dict], user_labels: dict[str, str] | None = None
    ) -> ReportOutcome:
        """Report checked operations. Returns what Google rejected.

        An HTTP 200 does not mean the usage was accepted: per-operation failures
        come back in reportErrors. Treating a 200 as success would mark usage
        reported that Google rejected, and it would never be billed.

        Errors are per operation, so one bad metric fails only its own operation
        and the rest still bill. An error naming no operation leaves the ones it
        did not name neither accepted nor rejected, so they come back as unknown
        rather than rejected: a resend of one Google accepted bills it twice.
        """
        # Forwarded to the customer's Cloud Billing cost-management tools for
        # attribution, and accepted by report but not by check. Omitted entirely
        # when empty rather than sent as {}.
        if user_labels:
            operations = [{**op, "userLabels": user_labels} for op in operations]

        # Never retried, see READ_RETRIES.
        response = (
            self.client.services()
            .report(serviceName=self.service_name, body={"operations": operations})
            .execute(http=self._http())
        )

        errors = response.get("reportErrors") or []
        if not errors:
            return ReportOutcome()

        logger.error(
            "gcp_marketplace_report_errors",
            consumer_id=operations[0].get("consumerId") if operations else None,
            errors=errors,
        )

        outcome = ReportOutcome(
            rejected={e["operationId"] for e in errors if e.get("operationId")},
            unattributed=[e for e in errors if not e.get("operationId")],
        )
        if outcome.unattributed:
            logger.error(
                "gcp_marketplace_report_errors_unattributed",
                consumer_id=operations[0].get("consumerId") if operations else None,
                operations=len(operations),
                unattributed=len(outcome.unattributed),
            )
        return outcome


gcp_service_control = GCPServiceControlService()
