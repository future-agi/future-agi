"""Store ALK measurements and emit them on the existing billing rails."""

from __future__ import annotations

import json
from uuid import UUID, uuid5

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils.dateparse import parse_datetime

from simulate.models import HostedHarnessAttempt, HostedHarnessJob
from simulate.services.hosted_harness import HostedHarnessError
from tfc.ee_gating import is_oss

_EVENT_TYPES = {
    "harness_authoring": "harness_authoring",
    "text_call": "text_call",
    "voice_call": "voice_call",
}
_REPORT_KEY = "usage_reports"
_AUTHORING_REPORT_KEY = "authoring_usage_reports"
_MAX_AUTHORING_STAGE_TOKENS = 1_000_000_000
_NON_BILLABLE_FAILURE_DOMAINS = frozenset(
    {"infrastructure", "connectivity", "platform_sync"}
)


def check_harness_usage(attempt: HostedHarnessAttempt, action: str) -> dict:
    decision = check_harness_action(str(attempt.job.organization_id), action)
    with transaction.atomic():
        job = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=attempt.job_id
        )
        metadata = dict(job.payload.get("metadata") or {})
        if not decision["allowed"]:
            metadata["usage_limit"] = decision
        elif "usage_limit" in metadata:
            metadata.pop("usage_limit")
        else:
            return decision
        job.payload = {**job.payload, "metadata": metadata}
        job.save(update_fields=["payload", "updated_at"])
    return decision


def check_harness_action(organization_id: str, action: str) -> dict:
    try:
        event_type = _EVENT_TYPES[action]
    except KeyError as exc:
        raise HostedHarnessError(
            "usage_action_invalid", "Unsupported hosted harness usage action."
        ) from exc
    if is_oss():
        return {"allowed": True}
    from ee.usage.services.metering import check_usage

    return check_usage(organization_id, event_type).model_dump(mode="json")


def require_harness_authoring(organization_id: str) -> None:
    decision = check_harness_action(organization_id, "harness_authoring")
    if not decision["allowed"]:
        from ee.usage.exceptions import UsageLimitExceeded
        from ee.usage.schemas.events import CheckResult

        raise UsageLimitExceeded(CheckResult.model_validate(decision))


def record_harness_usage(attempt: HostedHarnessAttempt, report: dict) -> dict:
    normalized = json.loads(json.dumps(report, cls=DjangoJSONEncoder))
    records = normalized["records"]
    by_id = {item["id"]: item for item in records}
    if len(by_id) != len(records):
        raise HostedHarnessError(
            "usage_duplicate_id", "A report must name each action only once."
        )

    with transaction.atomic():
        job = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=attempt.job_id
        )
        metadata = dict(job.payload.get("metadata") or {})
        reports = dict(metadata.get(_REPORT_KEY) or {})
        old = reports.get(str(attempt.id))
        if old:
            previous = {item["id"]: item for item in old["records"]}
            if any(
                key in by_id and by_id[key] != item for key, item in previous.items()
            ):
                raise HostedHarnessError(
                    "usage_record_conflict",
                    "A finalized usage record cannot change.",
                    status_code=409,
                )
            if not previous.keys() <= by_id.keys():
                if not by_id.keys() <= previous.keys():
                    raise HostedHarnessError(
                        "usage_report_conflict",
                        "A report must extend the previous ALK snapshot.",
                        status_code=409,
                    )
                normalized = old
        reports[str(attempt.id)] = normalized
        metadata[_REPORT_KEY] = reports
        job.payload = {**job.payload, "metadata": metadata}
        job.save(update_fields=["payload", "updated_at"])
        transaction.on_commit(lambda: emit_harness_usage(attempt, normalized))
    return {"accepted": True}


def _billable_records(attempt: HostedHarnessAttempt, report: dict):
    receipts = {
        receipt.scenario.scenario_key: receipt
        for receipt in attempt.result_receipts.select_related("scenario").all()
    }
    for item in report["records"]:
        if not item["amount"]:
            continue
        if item["action"] == "text_call" and item["funding"] == "customer":
            continue
        receipt = receipts.get(item["scenario_key"])
        if (
            receipt is None
            or receipt.status == "skipped"
            or not receipt.body.get("call")
        ):
            continue
        failure_domain = (receipt.body.get("failure") or {}).get("domain")
        if failure_domain in _NON_BILLABLE_FAILURE_DOMAINS:
            continue
        yield item


def emit_harness_usage(attempt: HostedHarnessAttempt, report: dict) -> None:
    if is_oss():
        return
    from ee.usage.schemas.events import UsageEvent
    from ee.usage.services.emitter import emit

    job = attempt.job
    for item in _billable_records(attempt, report):
        event_id = uuid5(UUID(str(attempt.id)), str(item["id"]))
        emit(
            UsageEvent(
                event_id=str(event_id),
                org_id=str(job.organization_id),
                event_type=_EVENT_TYPES[item["action"]],
                amount=item["amount"],
                timestamp=parse_datetime(item["occurred_at"]),
                properties={
                    "source": "rl_environment",
                    "source_id": str(job.id),
                    "harness_job_id": str(job.id),
                    "attempt_id": str(attempt.id),
                    "scenario_key": item["scenario_key"],
                    "workspace_id": str(job.workspace_id or ""),
                    "test_execution_id": str(job.test_execution_id or ""),
                    "funding": item["funding"],
                },
            )
        )


def _authoring_stage_record(item: dict) -> dict | None:
    from agentic_eval.core_evals.fi_utils.token_count_helper import (
        calculate_total_cost,
    )
    from ee.usage.services.config import BillingConfig

    stage = str(item.get("stage") or "").strip()
    models = sorted(
        {
            str(model).strip()
            for model in (item.get("models") or [])
            if str(model).strip()
        }
    )
    if not stage or len(stage) > 128 or len(models) != 1:
        raise HostedHarnessError(
            "authoring_usage_invalid",
            "Each authoring stage must name exactly one priced model.",
        )
    counts = {}
    for field in ("tokens_in", "tokens_out", "tokens_cached"):
        value = item.get(field, 0)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            or value > _MAX_AUTHORING_STAGE_TOKENS
        ):
            raise HostedHarnessError(
                "authoring_usage_invalid",
                f"Authoring {field} must be a non-negative integer.",
            )
        counts[field] = value
    if counts["tokens_cached"] > counts["tokens_in"]:
        raise HostedHarnessError(
            "authoring_usage_invalid",
            "Cached authoring tokens cannot exceed input tokens.",
        )
    if not counts["tokens_in"] and not counts["tokens_out"]:
        return None

    model = models[0]
    cost = calculate_total_cost(
        model,
        {
            "prompt_tokens": counts["tokens_in"],
            "completion_tokens": counts["tokens_out"],
            "cached_input_tokens": counts["tokens_cached"],
        },
    )
    if cost["pricing_source"] != "available_models":
        raise HostedHarnessError(
            "authoring_model_unpriced",
            f"Authoring model {model!r} is absent from the platform pricing catalog.",
        )
    return {
        "stage": stage,
        "model": model,
        **counts,
        "cost_usd": cost["total_cost"],
        "credits": BillingConfig.get().calculate_ai_credits(cost["total_cost"]),
        "pricing_source": cost["pricing_source"],
    }


def record_harness_authoring_usage(attempt: HostedHarnessAttempt, spend: dict) -> None:
    """Price sealed authoring stages on the platform and persist replay evidence."""
    if is_oss():
        return
    records = [
        record
        for item in spend.get("stages") or []
        if (record := _authoring_stage_record(item)) is not None
    ]
    if not records:
        return
    stages = [item["stage"] for item in records]
    if len(stages) != len(set(stages)):
        raise HostedHarnessError(
            "authoring_usage_invalid",
            "Authoring usage stages must have unique names.",
        )
    report = {
        "schema_version": "futureagi.harness-authoring-usage.v1",
        "records": records,
    }
    with transaction.atomic():
        job = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=attempt.job_id
        )
        metadata = dict(job.payload.get("metadata") or {})
        reports = dict(metadata.get(_AUTHORING_REPORT_KEY) or {})
        key = str(attempt.id)
        old = reports.get(key)
        if old is not None and old != report:
            raise HostedHarnessError(
                "authoring_usage_conflict",
                "Finalized authoring usage cannot change.",
                status_code=409,
            )
        reports[key] = report
        metadata[_AUTHORING_REPORT_KEY] = reports
        job.payload = {**job.payload, "metadata": metadata}
        job.save(update_fields=["payload", "updated_at"])
        transaction.on_commit(lambda: emit_harness_authoring_usage(attempt, report))


def emit_harness_authoring_usage(attempt: HostedHarnessAttempt, report: dict) -> None:
    if is_oss():
        return
    from ee.usage.schemas.event_types import BillingEventType
    from ee.usage.schemas.events import UsageEvent
    from ee.usage.services.emitter import emit

    job = attempt.job
    for item in report["records"]:
        if not item["credits"]:
            continue
        event_id = uuid5(UUID(str(attempt.id)), f"authoring:{item['stage']}")
        emit(
            UsageEvent(
                event_id=str(event_id),
                org_id=str(job.organization_id),
                event_type=BillingEventType.HARNESS_AUTHORING,
                amount=item["credits"],
                timestamp=attempt.created_at,
                properties={
                    "source": "rl_environment",
                    "source_id": str(job.id),
                    "harness_job_id": str(job.id),
                    "attempt_id": str(attempt.id),
                    "workspace_id": str(job.workspace_id or ""),
                    "test_execution_id": str(job.test_execution_id or ""),
                    "phase": "authoring",
                    "authoring_stage": item["stage"],
                    "model": item["model"],
                    "input_tokens": item["tokens_in"],
                    "output_tokens": item["tokens_out"],
                    "cached_input_tokens": item["tokens_cached"],
                    "raw_cost_usd": item["cost_usd"],
                    "pricing_source": item["pricing_source"],
                },
            )
        )


def replay_harness_usage(attempt: HostedHarnessAttempt) -> None:
    job = HostedHarnessJob.no_workspace_objects.only("payload").get(id=attempt.job_id)
    metadata = job.payload.get("metadata") or {}
    report = (metadata.get(_REPORT_KEY) or {}).get(str(attempt.id))
    if report is not None:
        emit_harness_usage(attempt, report)
    authoring = (metadata.get(_AUTHORING_REPORT_KEY) or {}).get(str(attempt.id))
    if authoring is not None:
        emit_harness_authoring_usage(attempt, authoring)


def harness_consumption(job: HostedHarnessJob) -> dict | None:
    metadata = job.payload.get("metadata") or {}
    reports = metadata.get(_REPORT_KEY) or {}
    authoring_reports = metadata.get(_AUTHORING_REPORT_KEY) or {}
    if not reports and not authoring_reports:
        return None

    consumption = {"text_sim_tokens": 0, "voice_sim_minutes": 0, "ai_credits": 0}
    attempts = {
        str(attempt.id): attempt
        for attempt in job.attempts.prefetch_related("result_receipts__scenario")
    }
    for attempt_id, report in reports.items():
        attempt = attempts.get(attempt_id)
        if attempt is None:
            continue
        for item in _billable_records(attempt, report):
            dimension = (
                "text_sim_tokens"
                if item["action"] == "text_call"
                else "voice_sim_minutes"
            )
            consumption[dimension] += item["amount"]
    consumption["ai_credits"] = sum(
        record["credits"]
        for report in authoring_reports.values()
        for record in report["records"]
    )
    return consumption
