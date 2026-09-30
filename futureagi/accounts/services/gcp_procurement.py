"""Client for the Cloud Commerce Partner Procurement API.

Discovery-based: there is no dedicated client library for this API.

All methods take and return bare ids. Full resource names
(``providers/{provider}/entitlements/{id}``) are built internally so callers
never handle them.

Failures raise. A Pub/Sub handler that swallows an error would ack the message
and lose the event, so nothing here returns None on failure.
"""

import json
import threading

import structlog
from django.conf import settings

from accounts.services.gcp_service_control import READ_RETRIES, REQUEST_TIMEOUT_SECONDS

logger = structlog.get_logger(__name__)

CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

SIGNUP_APPROVAL = "signup"


class GCPProcurementNotConfigured(RuntimeError):
    """Raised when the marketplace settings are absent, as on OSS and EE."""


class GCPProcurementService:
    """Thin wrapper over providers.accounts and providers.entitlements."""

    def __init__(self, provider_id: str | None = None):
        self._provider_id = provider_id or settings.GCP_MARKETPLACE_PROVIDER_ID
        self._client = None
        self._credentials_cache = None
        self._local = threading.local()

    @property
    def provider_id(self) -> str:
        if not self._provider_id:
            raise GCPProcurementNotConfigured("GCP_MARKETPLACE_PROVIDER_ID is not set")
        return self._provider_id

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
        """One transport per thread: httplib2.Http is not thread-safe."""
        http = getattr(self._local, "http", None)
        if http is None:
            http = self._local.http = self._authorized_http()
        return http

    def _read(self, request):
        return request.execute(http=self._http(), num_retries=READ_RETRIES)

    def _write(self, request):
        """Approvals are not retried by the transport.

        Google does not document approve as idempotent. A lost response leaves
        the handler's transaction unacked, so Pub/Sub redelivers and the handler
        re-checks the entitlement state before calling again.
        """
        return request.execute(http=self._http())

    @property
    def client(self):
        """Built on first use. Importing this module must not need credentials."""
        if self._client is None:
            from googleapiclient.discovery import build

            self._client = build(
                "cloudcommerceprocurement",
                "v1",
                http=self._authorized_http(),
                cache_discovery=False,
            )
        return self._client

    # ── resource names ────────────────────────────────────────────────────

    def account_name(self, account_id: str) -> str:
        return f"providers/{self.provider_id}/accounts/{account_id}"

    def entitlement_name(self, entitlement_id: str) -> str:
        return f"providers/{self.provider_id}/entitlements/{entitlement_id}"

    @staticmethod
    def bare_id(resource_name: str) -> str:
        """Last segment of a resource name, or the value unchanged if bare."""
        return (resource_name or "").rsplit("/", 1)[-1]

    # ── accounts ──────────────────────────────────────────────────────────

    def get_account(self, account_id: str) -> dict:
        return self._read(
            self.client.providers().accounts().get(name=self.account_name(account_id))
        )

    def approve_account(
        self, account_id: str, approval_name: str = SIGNUP_APPROVAL
    ) -> dict:
        logger.info("gcp_marketplace_account_approve", account_id=account_id)
        return self._write(
            self.client.providers()
            .accounts()
            .approve(
                name=self.account_name(account_id),
                body={"approvalName": approval_name},
            )
        )

    # def reject_account(
    #     self, account_id: str, reason: str, approval_name: str = SIGNUP_APPROVAL
    # ) -> dict:
    #     logger.warning(
    #         "gcp_marketplace_account_reject", account_id=account_id, reason=reason
    #     )
    #     return (
    #         self.client.providers()
    #         .accounts()
    #         .reject(
    #             name=self.account_name(account_id),
    #             body={"approvalName": approval_name, "reason": reason},
    #         )
    #         .execute()
    #     )

    def list_accounts(self, page_token: str | None = None) -> dict:
        return self._read(
            self.client.providers()
            .accounts()
            .list(
                parent=f"providers/{self.provider_id}",
                pageToken=page_token,
            )
        )

    @staticmethod
    def pending_approval(account: dict, approval_name: str = SIGNUP_APPROVAL) -> bool:
        """Whether an approval is still awaiting us.

        Branch on this rather than on account state: ACCOUNT_ACTIVATION_REQUESTED
        is deprecated and accounts now go straight to ACCOUNT_ACTIVE, so a state
        check would never fire and every approval would be skipped.
        """
        for approval in account.get("approvals") or []:
            if approval.get("name") == approval_name:
                return approval.get("state") == "PENDING"
        return False

    # ── entitlements ──────────────────────────────────────────────────────

    def get_entitlement(self, entitlement_id: str) -> dict:
        return self._read(
            self.client.providers()
            .entitlements()
            .get(name=self.entitlement_name(entitlement_id))
        )

    def approve_entitlement(
        self, entitlement_id: str, migrated_from_entitlement_id: str | None = None
    ) -> dict:
        logger.info(
            "gcp_marketplace_entitlement_approve", entitlement_id=entitlement_id
        )
        body: dict = {}
        if migrated_from_entitlement_id:
            body["entitlementMigrated"] = self.entitlement_name(
                migrated_from_entitlement_id
            )
        return self._write(
            self.client.providers()
            .entitlements()
            .approve(name=self.entitlement_name(entitlement_id), body=body)
        )

    def approve_plan_change(self, entitlement_id: str, pending_plan: str) -> dict:
        """`pending_plan` is the entitlement's newPendingPlan, not its current plan."""
        logger.info(
            "gcp_marketplace_plan_change_approve",
            entitlement_id=entitlement_id,
            pending_plan=pending_plan,
        )
        return self._write(
            self.client.providers()
            .entitlements()
            .approvePlanChange(
                name=self.entitlement_name(entitlement_id),
                body={"pendingPlanName": pending_plan},
            )
        )

    def reject_entitlement(self, entitlement_id: str, reason: str) -> dict:
        """Refuse a purchase. The customer is not charged for a rejected one."""
        logger.warning(
            "gcp_marketplace_entitlement_reject",
            entitlement_id=entitlement_id,
            reason=reason,
        )
        return self._write(
            self.client.providers()
            .entitlements()
            .reject(
                name=self.entitlement_name(entitlement_id),
                body={"reason": reason},
            )
        )

    # def reject_plan_change(
    #     self, entitlement_id: str, pending_plan: str, reason: str
    # ) -> dict:
    #     logger.warning(
    #         "gcp_marketplace_plan_change_reject",
    #         entitlement_id=entitlement_id,
    #         reason=reason,
    #     )
    #     return (
    #         self.client.providers()
    #         .entitlements()
    #         .rejectPlanChange(
    #             name=self.entitlement_name(entitlement_id),
    #             body={"pendingPlanName": pending_plan, "reason": reason},
    #         )
    #         .execute()
    #     )

    def suspend_entitlement(self, entitlement_id: str, reason: str) -> dict:
        """Revokes access without cancelling. Destructive: never call automatically."""
        logger.warning(
            "gcp_marketplace_entitlement_suspend",
            entitlement_id=entitlement_id,
            reason=reason,
        )
        return self._write(
            self.client.providers()
            .entitlements()
            .suspend(
                name=self.entitlement_name(entitlement_id),
                body={"reason": reason},
            )
        )

    def list_entitlements(
        self, account_id: str | None = None, page_token: str | None = None
    ) -> dict:
        """Used by reconciliation. `account_id` filters to one customer."""
        kwargs: dict = {
            "parent": f"providers/{self.provider_id}",
            "pageToken": page_token,
        }
        if account_id:
            kwargs["filter"] = f"account={account_id}"
        return self._read(self.client.providers().entitlements().list(**kwargs))

    def iter_entitlements(self, account_id: str | None = None):
        """Yields every entitlement, following pagination."""
        page_token = None
        while True:
            page = self.list_entitlements(account_id=account_id, page_token=page_token)
            yield from page.get("entitlements") or []
            page_token = page.get("nextPageToken")
            if not page_token:
                return


def base_plan_id(plan_id: str) -> str:
    """Strip the annual suffix. Metric ids are shared by a plan's two variants."""
    return (plan_id or "").removesuffix("-P1Y")


def resolve_plan(plan_id: str) -> tuple[str, str]:
    """Marketplace plan id -> (internal plan, billing interval).

    Maps to PlanChoices, not the legacy SubscriptionTierChoices the AWS
    integration uses. Raises on an unmapped id rather than defaulting: a plan
    renamed in the portal should fail loudly, not quietly put someone on the
    wrong tier. Private offers negotiate price on the offer, not the plan, so
    they still arrive against one of these ids.
    """
    mapping = settings.GCP_MARKETPLACE_PLAN_MAP
    if plan_id not in mapping:
        raise ValueError(f"Unmapped GCP Marketplace plan {plan_id!r}")
    return mapping[plan_id]


def metric_id_for(plan_id: str, dimension: str) -> str | None:
    """Metric id for a dimension on a given plan, or None if not billed."""
    plan_metrics = settings.GCP_MARKETPLACE_METRIC_MAP.get(base_plan_id(plan_id))
    if not plan_metrics:
        return None
    return plan_metrics.get(dimension)


gcp_procurement = GCPProcurementService()
