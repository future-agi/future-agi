"""Durable pre-call reservations and exact request-bound settlements."""

import json
import re
import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.db.models import Sum

from tracer.models.trace_grouping import (
    TraceGroupingAttempt,
    TraceGroupingCall,
    TraceGroupingScope,
)
from tracer.services.grouping.control import (
    GroupingConflict,
    GroupingControlError,
    GroupingNotFound,
    lock_attempt_scope,
)

MAX_RESULT_BYTES = 256 * 1024
REQUEST_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")


def _money(value: object) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise GroupingControlError("cost must be a decimal string") from exc
    if (
        not amount.is_finite()
        or amount < 0
        or amount >= Decimal("100000000000")
        or amount.as_tuple().exponent < -9
    ):
        raise GroupingControlError("cost must be a nonnegative 9-place decimal")
    return amount


def _receipt(call: TraceGroupingCall) -> dict:
    return {
        "receipt_id": str(call.id),
        "status": call.status,
        "request_digest": call.request_digest,
        "result": call.result,
        "cost_usd": str(call.cost_usd) if call.cost_usd is not None else None,
        "model_used": call.model_used or None,
        "input_tokens": call.input_tokens,
        "output_tokens": call.output_tokens,
        "failure_code": call.failure_code or None,
        "repair_intent": call.repair_intent,
    }


def reserve_call(
    *,
    attempt_id: uuid.UUID,
    lease_token: str,
    request_key: str,
    request_digest: str,
    max_cost_usd: str,
    repair_intent: dict | None = None,
    severity_job_id: uuid.UUID | None = None,
) -> dict:
    if severity_job_id is not None:
        if repair_intent is not None or not request_key.startswith(
            f"severity:{severity_job_id}:"
        ):
            raise GroupingControlError("invalid severity request identity")
    if (
        not request_key
        or len(request_key) > 255
        or not REQUEST_DIGEST.fullmatch(request_digest)
    ):
        raise GroupingControlError("invalid grouping request identity")
    amount = _money(max_cost_usd)
    if amount <= 0:
        raise GroupingControlError("reservation must be positive")
    if repair_intent is not None:
        if (
            not isinstance(repair_intent, dict)
            or set(repair_intent)
            != {"primary_receipt_id", "group_index", "missing_own_report_ids"}
            or type(repair_intent["group_index"]) is not int
            or not 0 <= repair_intent["group_index"] < 100
            or not isinstance(repair_intent["missing_own_report_ids"], list)
            or not 1 <= len(repair_intent["missing_own_report_ids"]) <= 100
            or any(
                not isinstance(item, str)
                for item in repair_intent["missing_own_report_ids"]
            )
            or len(set(repair_intent["missing_own_report_ids"]))
            != len(repair_intent["missing_own_report_ids"])
        ):
            raise GroupingControlError("invalid repair intent")
        try:
            uuid.UUID(repair_intent["primary_receipt_id"])
            for item in repair_intent["missing_own_report_ids"]:
                uuid.UUID(item)
        except (ValueError, TypeError, AttributeError) as exc:
            raise GroupingControlError("repair intent has invalid identity") from exc
    with transaction.atomic():
        from accounts.models import Organization

        org_id = (
            TraceGroupingAttempt.no_workspace_objects.filter(pk=attempt_id)
            .values_list("work__scope__organization_id", flat=True)
            .first()
        )
        if org_id is None:
            raise GroupingNotFound("grouping attempt was not found")
        Organization.objects.select_for_update().get(pk=org_id)
        if severity_job_id is not None:
            from tracer.services.grouping.severity import severity_accounting_authority

            scope, attempt = severity_accounting_authority(
                job_id=severity_job_id, lease_token=lease_token
            )
            if attempt.id != attempt_id:
                raise GroupingConflict("severity accounting attempt mismatch")
        else:
            scope, attempt = lock_attempt_scope(
                attempt_id=attempt_id, lease_token=lease_token
            )
        if repair_intent is not None:
            primary = TraceGroupingCall.no_workspace_objects.filter(
                pk=uuid.UUID(repair_intent["primary_receipt_id"]),
                scope=scope,
                status__in=["settled", "unknown"],
            ).first()
            if (
                primary is None
                or primary.result is None
                or primary.attempt.snapshot_digest != attempt.snapshot_digest
                or primary.attempt.candidate_digest != attempt.candidate_digest
                or not isinstance(primary.result, dict)
                or not isinstance(primary.result.get("groups"), list)
                or repair_intent["group_index"] >= len(primary.result["groups"])
            ):
                raise GroupingConflict(
                    "repair target has no current settled primary proposal"
                )
        existing = TraceGroupingCall.no_workspace_objects.filter(
            scope=scope, request_key=request_key
        ).first()
        if existing:
            if (
                existing.request_digest != request_digest
                or existing.repair_intent != repair_intent
            ):
                raise GroupingConflict("request key was reused for a different call")
            # A changed estimate never rewrites an existing reservation.
            # A prior reservation could have been sent before a crash. It is
            # never permission to send a second paid call.
            return {**_receipt(existing), "created": False}
        budget = _money(
            getattr(settings, "ERROR_FEED_GROUPING_PROJECT_BUDGET_USD", "0")
        )
        work_budget = _money(
            getattr(settings, "ERROR_FEED_GROUPING_WORK_BUDGET_USD", "0")
        )
        tenant_budget = _money(
            getattr(settings, "ERROR_FEED_GROUPING_TENANT_BUDGET_USD", "0")
        )
        prior_work_calls = list(
            TraceGroupingCall.no_workspace_objects.filter(work=attempt.work)[:101]
        )
        if len(prior_work_calls) > 100:
            raise GroupingConflict("work call count exceeds bound")
        work_committed = sum(
            (item.cost_usd if item.cost_usd is not None else item.max_cost_usd)
            for item in prior_work_calls
        )
        tenant_totals = TraceGroupingScope.no_workspace_objects.filter(
            organization_id=scope.organization_id
        ).aggregate(spent=Sum("spent_usd"), reserved=Sum("reserved_usd"))
        tenant_committed = (tenant_totals["spent"] or Decimal(0)) + (
            tenant_totals["reserved"] or Decimal(0)
        )
        if getattr(settings, "ERROR_FEED_GROUPING_BUDGET_ENFORCED", True) and (
            budget <= 0
            or work_budget <= 0
            or tenant_budget <= 0
            or scope.spent_usd + scope.reserved_usd + amount > budget
            or work_committed + amount > work_budget
            or tenant_committed + amount > tenant_budget
        ):
            raise GroupingConflict("grouping project budget exhausted or disabled")
        call = TraceGroupingCall.no_workspace_objects.create(
            scope=scope,
            work=attempt.work,
            attempt=attempt,
            request_key=request_key,
            request_digest=request_digest,
            repair_intent=repair_intent,
            max_cost_usd=amount,
            status="reserved",
        )
        scope.reserved_usd += amount
        scope.save(update_fields=["reserved_usd", "updated_at"])
        return {**_receipt(call), "created": True}


def settle_call(
    *,
    attempt_id: uuid.UUID,
    lease_token: str,
    request_key: str,
    request_digest: str,
    status: str,
    result: object = None,
    model_used: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: str | None = None,
    failure_code: str = "",
    severity_job_id: uuid.UUID | None = None,
) -> dict:
    if severity_job_id is not None and not request_key.startswith(
        f"severity:{severity_job_id}:"
    ):
        raise GroupingControlError("invalid severity request identity")
    if status not in {"settled", "unknown"}:
        raise GroupingControlError("invalid call settlement status")
    if model_used is not None and (
        not isinstance(model_used, str) or len(model_used) > 255
    ):
        raise GroupingControlError("invalid model identity")
    if not isinstance(failure_code, str) or len(failure_code) > 100:
        raise GroupingControlError("invalid failure code")
    if any(
        value is not None and (type(value) is not int or value < 0)
        for value in (input_tokens, output_tokens)
    ):
        raise GroupingControlError("token counts must be nonnegative")
    if status == "settled" and cost_usd is None:
        raise GroupingControlError("settled call requires known cost")
    if status == "unknown" and cost_usd is not None:
        raise GroupingControlError("unknown call must retain its cost reservation")
    if result is not None:
        try:
            encoded = json.dumps(result, allow_nan=False, ensure_ascii=False).encode(
                "utf-8"
            )
        except (TypeError, ValueError) as exc:
            raise GroupingControlError("call result is not JSON") from exc
        if len(encoded) > MAX_RESULT_BYTES:
            raise GroupingControlError("call result exceeds bound")
    amount = _money(cost_usd) if cost_usd is not None else None
    with transaction.atomic():
        # Settlement is restricted to the original attempt/token, but a late
        # receipt may settle after lease expiry. No algorithm mutation occurs.
        if severity_job_id is not None:
            from tracer.services.grouping.severity import severity_accounting_authority

            scope, attempt = severity_accounting_authority(
                job_id=severity_job_id, lease_token=lease_token, settlement=True
            )
            if attempt.id != attempt_id:
                raise GroupingConflict("severity accounting attempt mismatch")
        else:
            scope, attempt = lock_attempt_scope(
                attempt_id=attempt_id,
                lease_token=lease_token,
                allow_expired_for_settlement=True,
            )
        call = (
            TraceGroupingCall.no_workspace_objects.select_for_update()
            .filter(scope=attempt.work.scope, request_key=request_key)
            .first()
        )
        if call is None:
            raise GroupingNotFound("reserved call was not found")
        if call.request_digest != request_digest:
            raise GroupingConflict("settlement request digest changed")
        if call.attempt_id != attempt.id:
            raise GroupingConflict("only original reservation attempt may settle")
        if call.status not in {"reserved", "unknown"}:
            if (
                call.status != status
                or call.result != result
                or call.cost_usd != amount
                or call.failure_code != failure_code
            ):
                raise GroupingConflict("call was already settled differently")
            return _receipt(call)
        if call.status == "unknown":
            if (
                (call.result is not None and call.result != result)
                or (
                    call.model_used
                    and model_used is not None
                    and call.model_used != model_used
                )
                or (
                    call.input_tokens is not None
                    and input_tokens is not None
                    and call.input_tokens != input_tokens
                )
                or (
                    call.output_tokens is not None
                    and output_tokens is not None
                    and call.output_tokens != output_tokens
                )
            ):
                raise GroupingConflict("unknown call receipt cannot be overwritten")
            if status == "unknown" and result is None and call.result is not None:
                raise GroupingConflict("unknown call result cannot be discarded")
            if (
                status == "unknown"
                and result == call.result
                and model_used in {None, call.model_used}
            ):
                return _receipt(call)
        # Provider usage is authoritative even if it exceeds the pre-call
        # estimate. Record the overage; subsequent reserves fail the budget.
        call.status = status
        call.result = result if result is not None else call.result
        call.model_used = model_used or call.model_used
        call.input_tokens = (
            input_tokens if input_tokens is not None else call.input_tokens
        )
        call.output_tokens = (
            output_tokens if output_tokens is not None else call.output_tokens
        )
        call.cost_usd = amount
        call.failure_code = failure_code
        call.save(
            update_fields=[
                "status",
                "result",
                "model_used",
                "input_tokens",
                "output_tokens",
                "cost_usd",
                "failure_code",
                "updated_at",
            ]
        )
        if amount is not None:
            scope.reserved_usd -= call.max_cost_usd
            scope.spent_usd += amount
            scope.save(update_fields=["reserved_usd", "spent_usd", "updated_at"])
        # Unknown outcomes retain their reservation until a matching final
        # receipt or audited manual reconciliation; never silently refund.
        return _receipt(call)
