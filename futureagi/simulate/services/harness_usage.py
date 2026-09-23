"""Store ALK measurements and emit them on the existing billing rails."""

from __future__ import annotations

import json
import logging
from uuid import UUID, uuid5

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from simulate.models import HostedHarnessAttempt, HostedHarnessJob
from simulate.services.hosted_harness import HostedHarnessError
from tfc.ee_gating import is_oss

_ACTIONS = frozenset({"harness_authoring", "text_call", "voice_call"})
_REPORT_KEY = "usage_reports"
_AUTHORING_REPORT_KEY = "authoring_usage_reports"
_NON_BILLABLE_FAILURE_DOMAINS = frozenset(
    {"infrastructure", "connectivity", "platform_sync"}
)

logger = logging.getLogger(__name__)


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
    if action not in _ACTIONS:
        raise HostedHarnessError(
            "usage_action_invalid", "Unsupported hosted harness usage action."
        )
    if is_oss():
        return {"allowed": True}
    from ee.usage.services.metering import check_usage

    return check_usage(organization_id, action).model_dump(mode="json")


def _require_harness_action(organization_id: str, action: str) -> None:
    decision = check_harness_action(organization_id, action)
    if decision["allowed"]:
        return
    from ee.usage.exceptions import UsageLimitExceeded
    from ee.usage.schemas.events import CheckResult

    raise UsageLimitExceeded(CheckResult.model_validate(decision))


def require_harness_authoring(organization_id: str) -> None:
    _require_harness_action(organization_id, "harness_authoring")


def require_harness_run_usage(organization_id: str, payload: dict) -> None:
    """Check authoring and any call rail that the submitted connector identifies."""

    require_harness_authoring(organization_id)
    agent = payload.get("agent") or {}
    config = agent.get("config") or {}
    metadata = payload.get("metadata") or {}
    modality = str(metadata.get("modality") or config.get("modality") or "").lower()
    connector = str(agent.get("connector") or "").lower()
    if modality == "text" or connector == "retell_chat":
        actions = ("text_call",)
    elif modality == "voice" or connector in {"livekit", "vapi", "retell"}:
        actions = ("voice_call",)
    else:
        # ``auto`` can resolve to either lane only after the authored contract is available.
        # The guest still performs the dimension-specific check immediately before dialing.
        actions = ()
    for action in actions:
        _require_harness_action(organization_id, action)


def record_harness_usage(attempt: HostedHarnessAttempt, report: dict) -> dict:
    normalized = json.loads(json.dumps(report, cls=DjangoJSONEncoder))
    records = normalized["records"]
    by_id = {item["id"]: item for item in records}
    if len(by_id) != len(records):
        raise HostedHarnessError(
            "usage_duplicate_id", "A report must name each action only once."
        )

    with transaction.atomic():
        current = HostedHarnessAttempt.no_workspace_objects.select_for_update().get(
            id=attempt.id
        )
        old = current.usage_report
        if old is None:
            legacy_job = HostedHarnessJob.no_workspace_objects.only("payload").get(
                id=current.job_id
            )
            old = (
                (legacy_job.payload.get("metadata") or {}).get(_REPORT_KEY) or {}
            ).get(str(current.id))
        previous = {item["id"]: item for item in (old or {}).get("records", [])}
        if old is not None:
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
        new_record_ids = frozenset(by_id) - frozenset(previous)
        current.usage_report = normalized
        current.save(update_fields=["usage_report", "updated_at"])
        transaction.on_commit(
            lambda: emit_harness_usage(
                current,
                normalized,
                record_ids=new_record_ids,
            )
        )
    return {"accepted": True}


def _billable_records(
    attempt: HostedHarnessAttempt,
    report: dict,
    *,
    record_ids: frozenset[str] | None = None,
):
    cached = getattr(attempt, "_prefetched_objects_cache", {}).get("result_receipts")
    receipts = {
        receipt.execution.execution_key
        if receipt.execution_id
        else receipt.scenario.scenario_key: receipt
        for receipt in (
            cached
            if cached is not None
            else attempt.result_receipts.select_related("scenario", "execution").all()
        )
    }
    history = attempt.receipt_history or {}
    for item in report["records"]:
        if record_ids is not None and item["id"] not in record_ids:
            continue
        if not item["amount"]:
            continue
        receipt = receipts.get(item["scenario_key"])
        if receipt is not None:
            receipt_status = receipt.status
            receipt_body = receipt.body
        else:
            historical = history.get(item["scenario_key"])
            if historical is None:
                continue
            receipt_status = historical["status"]
            receipt_body = historical["body"]
        if receipt_status == "skipped" or not receipt_body.get("call"):
            continue
        record_failure_domain = item.get("failure_domain")
        receipt_failure_domain = (receipt_body.get("failure") or {}).get("domain")
        if (
            record_failure_domain in _NON_BILLABLE_FAILURE_DOMAINS
            or receipt_failure_domain in _NON_BILLABLE_FAILURE_DOMAINS
        ):
            continue
        # Hosted simulator usage is always platform-funded.  Keep the guest field in properties
        # for audit, but never let an untrusted value suppress a billable simulator call.
        yield item


def _bounded_usage_timestamp(attempt: HostedHarnessAttempt, occurred_at: str):
    timestamp = parse_datetime(occurred_at)
    if timestamp is None:
        timestamp = attempt.created_at
    if timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp)
    lower = attempt.created_at
    upper = attempt.cleanup_verified_at or attempt.expires_at or timezone.now()
    if timezone.is_naive(lower):
        lower = timezone.make_aware(lower)
    if timezone.is_naive(upper):
        upper = timezone.make_aware(upper)
    return min(max(timestamp, lower), max(lower, upper))


def emit_harness_usage(
    attempt: HostedHarnessAttempt,
    report: dict,
    *,
    record_ids: frozenset[str] | None = None,
) -> None:
    if is_oss():
        return
    from ee.usage.schemas.events import UsageEvent
    from ee.usage.services.emitter import emit

    job = attempt.job
    for item in _billable_records(attempt, report, record_ids=record_ids):
        event_id = uuid5(UUID(str(attempt.id)), str(item["id"]))
        emit(
            UsageEvent(
                event_id=str(event_id),
                org_id=str(job.organization_id),
                event_type=item["action"],
                amount=item["amount"],
                timestamp=_bounded_usage_timestamp(attempt, item["occurred_at"]),
                properties={
                    "source": "rl_environment",
                    "source_id": str(job.id),
                    "harness_job_id": str(job.id),
                    "attempt_id": str(attempt.id),
                    "scenario_key": item["scenario_key"],
                    "workspace_id": str(job.workspace_id or ""),
                    "test_execution_id": str(job.test_execution_id or ""),
                    "funding": item["funding"],
                    "outcome": item.get("outcome", "completed"),
                    "failure_domain": item.get("failure_domain"),
                },
            )
        )


def _authoring_stage_record(item: dict) -> dict | None:
    from agentic_eval.core_evals.fi_utils.token_count_helper import (
        calculate_total_cost,
    )
    from agentic_eval.core_evals.run_prompt.model_pricing import get_model_pricing
    from ee.usage.services.config import BillingConfig

    counts = {}
    for field in ("tokens_in", "tokens_out", "tokens_cached"):
        value = item.get(field, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
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

    model = models[0]
    pricing = get_model_pricing(model)
    if (
        not isinstance(pricing, dict)
        or not {
            "input_per_1M_tokens",
            "output_per_1M_tokens",
        }
        <= pricing.keys()
    ):
        raise HostedHarnessError(
            "authoring_model_unpriced",
            f"Authoring model {model!r} has no token pricing in the platform catalog.",
        )
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
        current = HostedHarnessAttempt.no_workspace_objects.select_for_update().get(
            id=attempt.id
        )
        old = current.authoring_usage_report
        if old is None:
            legacy_job = HostedHarnessJob.no_workspace_objects.only("payload").get(
                id=current.job_id
            )
            old = (
                (legacy_job.payload.get("metadata") or {}).get(_AUTHORING_REPORT_KEY)
                or {}
            ).get(str(current.id))
        if old is not None and old != report:
            raise HostedHarnessError(
                "authoring_usage_conflict",
                "Finalized authoring usage cannot change.",
                status_code=409,
            )
        current.authoring_usage_report = report
        current.save(update_fields=["authoring_usage_report", "updated_at"])
        transaction.on_commit(lambda: emit_harness_authoring_usage(current, report))


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
    current = HostedHarnessAttempt.no_workspace_objects.get(id=attempt.id)
    job = HostedHarnessJob.no_workspace_objects.only("payload").get(id=current.job_id)
    metadata = job.payload.get("metadata") or {}
    report = current.usage_report
    if report is None:
        report = (metadata.get(_REPORT_KEY) or {}).get(str(current.id))
    if report is not None:
        emit_harness_usage(current, report)
    if not metadata.get("simulation_only"):
        authoring = current.authoring_usage_report
        if authoring is None:
            authoring = (metadata.get(_AUTHORING_REPORT_KEY) or {}).get(str(current.id))
        if authoring is not None:
            emit_harness_authoring_usage(current, authoring)
        elif current.cleanup_verified_at is not None:
            spend = ((metadata.get("harness_spend") or {}).get("attempts") or {}).get(
                str(current.attempt_number)
            )
            if spend is not None:
                try:
                    record_harness_authoring_usage(current, spend)
                except HostedHarnessError:
                    logger.exception(
                        "Authoring usage could not be finalized for attempt %s",
                        current.id,
                    )


def harness_consumption(job: HostedHarnessJob) -> dict | None:
    metadata = job.payload.get("metadata") or {}
    legacy_reports = metadata.get(_REPORT_KEY) or {}
    legacy_authoring_reports = metadata.get(_AUTHORING_REPORT_KEY) or {}
    legacy_runtimes = metadata.get("sandbox_runtime") or {}
    authoring_spend = (
        {}
        if metadata.get("simulation_only")
        else (metadata.get("harness_spend") or {}).get("attempts") or {}
    )
    attempts = {
        str(attempt.id): attempt
        for attempt in job.attempts.prefetch_related("result_receipts__scenario")
    }
    reports = {
        attempt_id: (
            attempt.usage_report
            if attempt.usage_report is not None
            else legacy_reports.get(attempt_id)
        )
        for attempt_id, attempt in attempts.items()
        if attempt.usage_report is not None or attempt_id in legacy_reports
    }
    authoring_reports = {
        attempt_id: (
            attempt.authoring_usage_report
            if attempt.authoring_usage_report is not None
            else legacy_authoring_reports.get(attempt_id)
        )
        for attempt_id, attempt in attempts.items()
        if not metadata.get("simulation_only")
        and (
            attempt.authoring_usage_report is not None
            or attempt_id in legacy_authoring_reports
        )
    }
    runtimes = {
        attempt_id: (
            attempt.sandbox_runtime
            if attempt.sandbox_runtime
            else legacy_runtimes.get(attempt_id)
        )
        for attempt_id, attempt in attempts.items()
        if attempt.sandbox_runtime or attempt_id in legacy_runtimes
    }
    if not reports and not authoring_reports and not runtimes and not authoring_spend:
        return None

    consumption = {"text_sim_tokens": 0, "voice_sim_minutes": 0, "ai_credits": 0}
    for attempt_id, report in reports.items():
        attempt = attempts[attempt_id]
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
    authoring_estimate_unavailable = False
    if authoring_spend and not is_oss():
        try:
            for attempt_id, attempt in attempts.items():
                if attempt_id in authoring_reports:
                    continue
                spend = authoring_spend.get(str(attempt.attempt_number)) or {}
                for stage in spend.get("stages") or []:
                    record = _authoring_stage_record(stage)
                    if record is not None:
                        consumption["ai_credits"] += record["credits"]
        except HostedHarnessError:
            logger.warning(
                "Authoring credit estimate unavailable for job %s",
                job.id,
                exc_info=True,
            )
            authoring_estimate_unavailable = True
    if authoring_estimate_unavailable:
        consumption["ai_credits"] = None
    consumption["sandbox_seconds"] = sum(
        observation["seconds"] for observation in runtimes.values()
    )
    return consumption


def record_sandbox_runtime(
    attempt: HostedHarnessAttempt, *, started: bool = False, final: bool = False
) -> None:
    """Observe provision-to-confirmed-teardown duration without billing it."""
    with transaction.atomic():
        current = HostedHarnessAttempt.no_workspace_objects.select_for_update().get(
            id=attempt.id
        )
        observation = dict(current.sandbox_runtime or {})
        if observation.get("ended_at") or (not observation and not started):
            return
        now = timezone.now()
        observation.setdefault("started_at", now.isoformat())
        began = parse_datetime(observation["started_at"])
        observation["seconds"] = max(0.0, (now - began).total_seconds())
        observation["observed_at"] = now.isoformat()
        if final:
            observation["ended_at"] = now.isoformat()
        current.sandbox_runtime = observation
        current.save(update_fields=["sandbox_runtime", "updated_at"])
