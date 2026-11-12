"""Queue through the unchanged, frozen six-keyword checkout boundary.

The account lock, current consent validation, intent reservation and outbox write
share a default-DB transaction. An active intent takes precedence over mutable
settings and current balance. The checkout boundary still validates current
receipt inputs even on duplicates and must not change the original queued
payload. Only the trusted
terminal/ledger-credit hooks advance arming; HTTP observations cannot do so.
The cloud tree owns the real adapter, the protected outcome/charge/credit writer
and the automatic caller/append-only balance writer. No charge or credit occurs
here.
"""

from django.db import transaction
from django.utils.decorators import method_decorator
from rest_framework import serializers, status

from accounts.models.audit_log import AuditLog, AuditScope
from agentcc.serializers.managed_credits import ManagedCreditErrorResponseSerializer
from agentcc.services.auto_reload_arming import (
    get_locked_auto_reload_inflight,
    get_locked_auto_reload_intent,
    observe_locked_auto_reload_balance,
)
from agentcc.services.auto_reload_consent import (
    get_current_auto_reload_consent,
    has_current_auto_reload_consent,
)
from agentcc.views import managed_credits as wallet
from tfc.utils.api_contracts import validated_request

_CONSENT_REVISIONS = {"auto-reload-v1": 1}
_REFUSAL_MESSAGES = {
    "consent_missing": "Auto-reload consent is required.",
    "consent_stale": "Auto-reload consent must be renewed.",
    "payment_method_mismatch": "The saved payment method is not authorized for auto-reload.",
    "amount_above_consent": "The reload amount exceeds the accepted consent.",
    "disabled": "Auto-reload is disabled.",
}


class AutoReloadTriggerRequestSerializer(serializers.Serializer):
    """Retry hint retained for clients; never the server reload-event identity."""

    intent_id = serializers.UUIDField()


class AutoReloadTriggerResultSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=("queued", "duplicate"))


class AutoReloadTriggerResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AutoReloadTriggerResultSerializer()


def _consent_required():
    return wallet.ManagedCreditsApiError(
        status.HTTP_409_CONFLICT,
        "auto_reload_consent_required",
        "Review and save auto-reload consent for the current payment method "
        "and settings before requesting an auto-reload.",
    )


def _checked_trigger_status(result):
    """Never serialize arbitrary dependency values, messages or payment data."""
    result_status = getattr(result, "status", None)
    reason = getattr(result, "reason", "")
    if (
        result_status == "refused"
        and isinstance(reason, str)
        and reason in _REFUSAL_MESSAGES
    ):
        raise wallet.ManagedCreditsApiError(
            status.HTTP_409_CONFLICT,
            "auto_reload_" + reason,
            _REFUSAL_MESSAGES[reason],
        )
    if result_status not in ("queued", "duplicate") or reason not in ("", None):
        raise wallet.BillingUnavailable("The auto-reload checkout adapter")
    return result_status


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class ManagedCreditAutoReloadTriggerView(wallet._ManagedCreditsView):
    @validated_request(
        request_serializer=AutoReloadTriggerRequestSerializer,
        reject_unknown_fields=True,
        responses={
            200: AutoReloadTriggerResponseSerializer,
            409: ManagedCreditErrorResponseSerializer,
            **wallet.ERRORS,
        },
    )
    @wallet._dependency_errors
    def post(self, request):
        """Queue one server-derived reload event; Owner or Admin, POST only."""
        org = wallet._request_org(request)
        if transaction.get_connection().in_atomic_block:
            raise wallet.BillingUnavailable(
                "Auto-reload",
                "Auto-reload must be requested after settings have committed.",
            )
        ledger = wallet._ledger()
        result_status = None
        with transaction.atomic(using="default"):
            account = (
                ledger.ManagedCreditAccount.objects.using("default")
                .select_for_update()
                .filter(organization=org)
                .first()
            )
            if not has_current_auto_reload_consent(account):
                raise _consent_required()
            receipt = get_current_auto_reload_consent(account)
            revision = (
                _CONSENT_REVISIONS.get(receipt.consent_version) if receipt else None
            )
            if revision is None:
                raise _consent_required()
            arming = observe_locked_auto_reload_balance(
                account, balance_micros=ledger.ledger_balance(org).balance_micros
            )
            active = get_locked_auto_reload_inflight(account)
            if active is not None or arming.below_threshold:
                trigger = getattr(
                    wallet._checkout(), "request_auto_reload_checkout", None
                )
                if not callable(trigger):
                    raise wallet.BillingUnavailable("The auto-reload checkout adapter")
                intent_id = get_locked_auto_reload_intent(account)
                result = trigger(
                    org_id=org.pk,
                    consent_receipt_id=receipt.pk,
                    intent_id=intent_id,
                    amount_micros=receipt.amount_micros,
                    payment_method_id=receipt.payment_method_id,
                    consent_revision=revision,
                )
                result_status = _checked_trigger_status(result)
                if active is not None and result_status != "duplicate":
                    # Existing exclusion metadata must correspond to an existing
                    # checkout-trigger row. An unexpected insert rolls back,
                    # never succeeds.
                    raise wallet.BillingUnavailable("The auto-reload checkout adapter")
                if result_status == "queued":
                    # Required manual-only audit, not a best-effort/on-commit log.
                    # Share intent/outbox rollback and the account lock; retries
                    # return duplicate above and never add or rewrite an event.
                    AuditLog.no_workspace_objects.using("default").create(
                        organization_id=org.pk,
                        actor_id=request.user.pk,
                        action="auto_reload.manual_trigger",
                        scope=AuditScope.ORGANIZATION,
                        target_id=org.pk,
                        metadata={"intent_id": intent_id},
                    )
        if result_status is None:
            raise wallet.ManagedCreditsApiError(
                status.HTTP_409_CONFLICT,
                "auto_reload_threshold_not_reached",
                "The balance is above the auto-reload threshold.",
            )
        return self._gm.success_response(
            AutoReloadTriggerResultSerializer({"status": result_status}).data
        )
