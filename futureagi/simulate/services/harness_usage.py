"""Store ALK measurements and emit them on the existing billing rails."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from uuid import UUID, uuid5

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from simulate.models import HostedHarnessAttempt, HostedHarnessJob
from simulate.services.harness_environment import _VOICE_CONNECTORS
from simulate.services.hosted_harness import HostedHarnessError
from tfc.ee_gating import is_oss

_ACTIONS = frozenset(
    {"agent_fix", "harness_authoring", "sandbox_runtime", "text_call", "voice_call"}
)
_REPORT_KEY = "usage_reports"
_AUTHORING_REPORT_KEY = "authoring_usage_reports"
_NON_BILLABLE_FAILURE_DOMAINS = frozenset(
    {"infrastructure", "connectivity", "platform_sync"}
)
# The provider name a bring-your-own sandbox is recorded under; its time is the customer's.
BYO_SANDBOX_PROVIDER = "daytona-byo"
_MINUTE_PRECISION = Decimal("0.0001")
# A sandbox last seen alive this close to its lifetime limit, and found gone after it, reached
# that limit: it expired, and its time up to the limit is charged. Longer than every cadence at
# which the platform sees a live sandbox (the run poll, the chat heartbeat window).
SANDBOX_EXPIRY_TOLERANCE = timedelta(seconds=90)
# The run failure that means the provider no longer had the sandbox.
_RUN_SANDBOX_GONE = "sandbox_disappeared"
# Launch errors raised after the sandbox exists that are the platform's own doing, although the
# gateway records every launch error under "environment".
OUR_LAUNCH_ERRORS = frozenset(
    {"attempt_capability_activation_invalid", "conversation_idempotency_conflict"}
)
# A fix that ended on our side: its own sandbox and the check runs it cancelled are free.
OUR_FIX_FAILURES = frozenset(
    {
        "fix_dispatch_failed",
        "fix_engine_died",
        "fix_engine_failed",
        "fix_sandbox_failed",
        "fix_watch_failed",
        "fix_workflow_lost",
    }
)

logger = logging.getLogger(__name__)


def check_harness_usage(attempt: HostedHarnessAttempt, action: str) -> dict:
    decision = check_harness_action(str(attempt.job.organization_id), action)
    _save_usage_limit(attempt.job_id, decision)
    return decision


def _save_usage_limit(job_id, decision: dict) -> None:
    with transaction.atomic():
        job = HostedHarnessJob.no_workspace_objects.select_for_update().get(id=job_id)
        metadata = dict(job.payload.get("metadata") or {})
        if not decision["allowed"]:
            metadata["usage_limit"] = decision
        elif "usage_limit" in metadata:
            metadata.pop("usage_limit")
        else:
            return
        job.payload = {**job.payload, "metadata": metadata}
        job.save(update_fields=["payload", "updated_at"])


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


def usage_limit_error(exc: Exception) -> HostedHarnessError | None:
    """A usage refusal as a hosted harness error, for a caller that reads only that shape:
    the fix engine tells the organization's plan limit apart from the fix's own budget."""
    if is_oss():
        return None
    from ee.usage.exceptions import UsageLimitExceeded

    if not isinstance(exc, UsageLimitExceeded):
        return None
    return HostedHarnessError(
        "usage_limit",
        exc.check_result.reason or "the organization's usage limit is reached",
        status_code=402,
    )


def require_harness_authoring(organization_id: str) -> None:
    _require_harness_action(organization_id, "harness_authoring")


def require_sandbox_usage(organization_id: str) -> None:
    _require_harness_action(organization_id, "sandbox_runtime")


def require_sandbox_start(organization_id: str) -> None:
    """The sandbox check for a caller that answers only hosted harness errors (re-runs,
    resumes): a refusal keeps the fields the limit message and banner read."""
    try:
        require_sandbox_usage(organization_id)
    except Exception as exc:
        refusal = usage_limit_error(exc)
        if refusal is None:
            raise
        result = exc.check_result
        refusal.details = {
            "error_code": result.error_code,
            "dimension": result.dimension,
            "current_usage": result.current_usage,
            "limit": result.limit,
            "upgrade_cta": (
                result.upgrade_cta.model_dump() if result.upgrade_cta else None
            ),
        }
        raise refusal from exc


def require_sandbox_create(
    organization_id: str, *, attempt: HostedHarnessAttempt | None = None
) -> None:
    """Refuse a sandbox the organization's plan does not allow, just before it is created.
    Where the plan cannot be read (the sandbox runner has no billing config) the create goes
    ahead: the submission already checked where it could."""
    if is_oss():
        return
    try:
        decision = check_harness_action(organization_id, "sandbox_runtime")
    except Exception as exc:  # noqa: BLE001 - a check that cannot run never blocks
        # Expected on the sandbox runner, which has no billing config: not worth a warning
        # on every create there. Anything else is.
        expected = (
            isinstance(exc, ImportError) or type(exc).__name__ == "BillingConfigError"
        )
        (logger.info if expected else logger.warning)(
            "sandbox usage check unavailable, create allowed organization=%s error=%s",
            organization_id,
            type(exc).__name__,
        )
        return
    if decision["allowed"]:
        return
    if attempt is not None:
        try:
            _save_usage_limit(attempt.job_id, decision)
        except Exception:  # noqa: BLE001 - the refusal stands; only its banner is lost
            logger.exception("usage limit not saved on the run job=%s", attempt.job_id)
    raise HostedHarnessError(
        "usage_limit",
        decision.get("reason") or "the organization's usage limit is reached",
        status_code=402,
    )


def require_agent_fix_usage(organization_id: str) -> None:
    _require_harness_action(organization_id, "agent_fix")
    require_sandbox_usage(organization_id)


def require_harness_call_usage(job: HostedHarnessJob) -> None:
    from simulate.services.hosted_harness import _resolve_scenario_modality

    action = (
        "voice_call" if _resolve_scenario_modality(job, {}) == "voice" else "text_call"
    )
    _require_harness_action(str(job.organization_id), action)
    require_sandbox_usage(str(job.organization_id))


def require_harness_run_usage(organization_id: str, payload: dict) -> None:
    """Check authoring and any call rail that the submitted connector identifies."""

    require_harness_authoring(organization_id)
    require_sandbox_usage(organization_id)
    agent = payload.get("agent") or {}
    config = agent.get("config") or {}
    metadata = payload.get("metadata") or {}
    modality = str(metadata.get("modality") or config.get("modality") or "").lower()
    connector = str(agent.get("connector") or "").lower()
    if modality == "text" or connector == "retell_chat":
        actions = ("text_call",)
    elif modality == "voice" or connector in _VOICE_CONNECTORS:
        actions = ("voice_call",)
    else:
        # ``auto`` can resolve to either lane only after the authored contract is available.
        # The guest still performs the dimension-specific check immediately before dialing.
        actions = ()
    for action in actions:
        _require_harness_action(organization_id, action)


def require_harness_source_call_usage(
    organization_id: str, payload: dict, detected_connectors
) -> None:
    """Check the voice rail for an ``auto`` source whose scan found a voice provider."""

    agent = payload.get("agent") or {}
    config = agent.get("config") or {}
    metadata = payload.get("metadata") or {}
    modality = str(metadata.get("modality") or config.get("modality") or "").lower()
    connector = str(agent.get("connector") or "").lower()
    if connector != "auto" or modality in {"text", "voice"}:
        return
    if _VOICE_CONNECTORS & set(detected_connectors):
        _require_harness_action(organization_id, "voice_call")


def record_harness_usage(
    attempt: HostedHarnessAttempt, report: dict, *, emit: bool = True
) -> dict:
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
        if emit:
            transaction.on_commit(
                lambda: emit_harness_usage(
                    current,
                    normalized,
                    record_ids=new_record_ids,
                )
            )
    return {"accepted": True}


def is_billable_receipt(status: str, call: object, failure_domain: str | None) -> bool:
    """Whether a result receipt's call is charged: it was not skipped, it ran, and the
    platform's own infrastructure, connectivity or sync failure did not end it."""
    return (
        status != "skipped"
        and bool(call)
        and failure_domain not in _NON_BILLABLE_FAILURE_DOMAINS
    )


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
        if not is_billable_receipt(
            receipt_status,
            receipt_body.get("call"),
            (receipt_body.get("failure") or {}).get("domain"),
        ):
            continue
        if item.get("failure_domain") in _NON_BILLABLE_FAILURE_DOMAINS:
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
            legacy_job = HostedHarnessJob.all_objects.only("payload").get(
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


def replay_harness_usage(attempt: HostedHarnessAttempt, *, seal: bool = False) -> None:
    current = HostedHarnessAttempt.no_workspace_objects.get(id=attempt.id)
    job = HostedHarnessJob.all_objects.only("payload").get(id=current.job_id)
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
    if seal:
        # Only the seal sends the sandbox charge: it retries when the stream is down.
        emit_sandbox_runtime(current)
    price_fix_check_run(current.job_id)


def billed_ai_usd(usd: Decimal) -> Decimal:
    """What a model call bills the organization, in US dollars: its cost at the AI markup."""
    if is_oss():
        return usd
    from ee.usage.services.config import BillingConfig

    return usd * BillingConfig.get().get_ai_cost_markup()


def _paid_unit_usd(config, dimension: str) -> Decimal:
    """The first price a dimension charges past its free tiers."""
    return next(
        (
            tier.price_per_unit
            for tier in config.get_pricing_tiers(dimension)
            if tier.price_per_unit > 0
        ),
        Decimal(0),
    )


def price_fix_check_run(job_id) -> None:
    """Set what a fix's check run billed, once its usage is sealed: its conversations and
    calls at their price per unit, plus any AI credits. A fix holds a worst case for a check
    run until this replaces it."""
    if is_oss():
        return
    job = HostedHarnessJob.all_objects.filter(id=job_id, fix_run__isnull=False).first()
    if job is None:
        return
    from ee.usage.services.config import BillingConfig

    config = BillingConfig.get()
    used = harness_consumption(job) or {}
    usd = sum(
        (
            Decimal(str(used.get(dimension) or 0)) * _paid_unit_usd(config, dimension)
            for dimension in ("text_sim_tokens", "voice_sim_minutes", "sandbox_minutes")
        ),
        Decimal(str(used.get("ai_credits") or 0)) * Decimal("0.01"),
    )
    HostedHarnessJob.all_objects.filter(id=job.id).update(
        fix_cost_usd=usd.quantize(Decimal("0.0001"))
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
    # What was billed: the charge frozen once each attempt's end was final.
    consumption["sandbox_minutes"] = float(
        sum(
            (
                Decimal(attempt.sandbox_runtime["billed_minutes"])
                for attempt in attempts.values()
                if "billed_minutes" in (attempt.sandbox_runtime or {})
            ),
            Decimal(0),
        )
    )
    return consumption


def record_sandbox_runtime(
    attempt: HostedHarnessAttempt,
    *,
    started: bool = False,
    final: bool = False,
    provider: str | None = None,
    lifetime_seconds: int | None = None,
    armed_at: datetime | None = None,
    retried: bool = False,
) -> None:
    """Observe provision-to-confirmed-teardown duration; the seal bills it once it is final."""
    with transaction.atomic():
        current = HostedHarnessAttempt.no_workspace_objects.select_for_update().get(
            id=attempt.id
        )
        observation = dict(current.sandbox_runtime or {})
        if observation.get("ended_at") or (not observation and not started):
            return
        now = timezone.now()
        observation.setdefault("started_at", now.isoformat())
        if started and provider is not None and lifetime_seconds is not None:
            observation.setdefault("provider", provider)
            observation.setdefault("lifetime_seconds", int(lifetime_seconds))
            observation.setdefault(
                "armed_at",
                (
                    armed_at.isoformat()
                    if armed_at is not None
                    else observation["started_at"]
                ),
            )
        began = parse_datetime(observation["started_at"])
        observation["seconds"] = max(0.0, (now - began).total_seconds())
        observation["observed_at"] = now.isoformat()
        if final:
            observation["ended_at"] = now.isoformat()
            if retried:
                # The platform will run the job again: an expiry of this attempt is free.
                observation["retried"] = True
        current.sandbox_runtime = observation
        current.save(update_fields=["sandbox_runtime", "updated_at"])


def rearm_sandbox_runtime(
    attempt: HostedHarnessAttempt, *, provider, lifetime_seconds: int, renewed_at
) -> None:
    """Record a renewed lifetime limit, when the provider's renewal restarts it.
    ``renewed_at`` is taken just before the renewal call, so the recorded limit is never
    later than the provider's."""
    if not getattr(provider, "renews_ttl", False):
        return
    try:
        with transaction.atomic():
            current = HostedHarnessAttempt.no_workspace_objects.select_for_update().get(
                id=attempt.id
            )
            observation = dict(current.sandbox_runtime or {})
            if observation.get("ended_at") or "provider" not in observation:
                return
            observation["armed_at"] = renewed_at.isoformat()
            observation["lifetime_seconds"] = int(lifetime_seconds)
            current.sandbox_runtime = observation
            current.save(update_fields=["sandbox_runtime", "updated_at"])
    except Exception:  # noqa: BLE001 - metering never fails a launch
        logger.exception("sandbox lifetime renewal not recorded attempt=%s", attempt.id)


def sandbox_metered_end(ended_at, armed_at, lifetime_seconds: int):
    return min(ended_at, armed_at + timedelta(seconds=int(lifetime_seconds)))


def billable_sandbox_minutes(
    *,
    provider,
    failure_domains,
    started_at,
    ended_at,
    armed_at,
    lifetime_seconds,
    excluded=(),
) -> Decimal:
    """What a finished sandbox bills: nothing on a bring-your-own sandbox or one our own
    failure ended; otherwise start to confirmed end, never past its last lifetime limit,
    less the parts of ``excluded`` (start, end) spans inside that time, each moment once.
    """
    if provider == BYO_SANDBOX_PROVIDER:
        return Decimal(0)
    if any(domain in _NON_BILLABLE_FAILURE_DOMAINS for domain in failure_domains):
        return Decimal(0)
    end = sandbox_metered_end(ended_at, armed_at, lifetime_seconds)
    removed, reach = 0.0, started_at
    for low, high in sorted((max(a, started_at), min(b, end)) for a, b in excluded):
        low = max(low, reach)
        if high > low:
            removed += (high - low).total_seconds()
            reach = high
    seconds = max(0.0, (end - started_at).total_seconds() - removed)
    return (Decimal(str(seconds)) / Decimal(60)).quantize(
        _MINUTE_PRECISION, rounding=ROUND_DOWN
    )


def _aware(value: str):
    moment = parse_datetime(value)
    return timezone.make_aware(moment) if timezone.is_naive(moment) else moment


def reached_its_lifetime(*, cap, last_seen, found_at) -> bool:
    """Whether a sandbox found gone had reached its lifetime limit, which is an expiry
    charged up to the limit, rather than failed before it, which is free."""
    return (
        last_seen is not None
        and found_at >= cap
        and last_seen >= cap - SANDBOX_EXPIRY_TOLERANCE
    )


def _run_sandbox_domains(
    attempt: HostedHarnessAttempt, job: HostedHarnessJob, *, cap, ended_at
) -> list:
    """The failure domains that decide whether an attempt's sandbox is free."""
    failure = attempt.terminal_failure or {}
    if (
        failure.get("code") == _RUN_SANDBOX_GONE
        # A retried attempt is the platform's to pay for; only the run's last one is charged.
        and not (attempt.sandbox_runtime or {}).get("retried")
        and reached_its_lifetime(
            cap=cap, last_seen=attempt.heartbeat_at, found_at=ended_at
        )
    ):
        # It lived to its lifetime limit and expired: its time up to the limit is charged.
        return []
    domains = [failure.get("domain")]
    if (
        failure.get("domain") == "environment"
        and failure.get("code") in OUR_LAUNCH_ERRORS
    ):
        domains.append("infrastructure")
    if attempt.attempt_number == job.current_attempt_number:
        domains.append((job.failure or {}).get("domain"))
        # A check run cancelled because the fix it checks failed on our side.
        if job.cancel_reason == "fix_ended" and job.fix_run_id is not None:
            if (job.fix_run.failure or {}).get("code") in OUR_FIX_FAILURES:
                domains.append("infrastructure")
    return domains


def _run_sandbox_charge(attempt: HostedHarnessAttempt, job: HostedHarnessJob):
    """(minutes, metered end) an attempt's sandbox bills; (0, None) until cleanup confirmed
    it gone, and always for an attempt timed before providers and limits were recorded.
    """
    observation = attempt.sandbox_runtime or {}
    if attempt.cleanup_verified_at is None or not observation.get("ended_at"):
        return Decimal(0), None
    if (
        not {"started_at", "provider", "lifetime_seconds", "armed_at"}
        <= observation.keys()
    ):
        return Decimal(0), None
    started_at, ended_at, armed_at = (
        _aware(observation[key]) for key in ("started_at", "ended_at", "armed_at")
    )
    lifetime = int(observation["lifetime_seconds"])
    minutes = billable_sandbox_minutes(
        provider=observation["provider"],
        failure_domains=_run_sandbox_domains(
            attempt,
            job,
            cap=armed_at + timedelta(seconds=lifetime),
            ended_at=ended_at,
        ),
        started_at=started_at,
        ended_at=ended_at,
        armed_at=armed_at,
        lifetime_seconds=lifetime,
    )
    return minutes, sandbox_metered_end(ended_at, armed_at, lifetime)


def freeze_run_sandbox_charge(
    attempt: HostedHarnessAttempt, job: HostedHarnessJob
) -> None:
    """Store the attempt's sandbox charge once it is final, before anything can rewrite
    the job it is read from; every later send, and the run page, use the stored one."""
    if is_oss():
        # Nothing is billed, so the run page shows sandbox seconds alone, re-run or not.
        return
    with transaction.atomic():
        current = HostedHarnessAttempt.no_workspace_objects.select_for_update().get(
            id=attempt.id
        )
        observation = dict(current.sandbox_runtime or {})
        if "billed_minutes" in observation:
            return
        minutes, ended = _run_sandbox_charge(current, job)
        if ended is None:
            # Not final yet, or timed before this change: nothing to store.
            return
        observation["billed_minutes"] = str(minutes.quantize(_MINUTE_PRECISION))
        observation["metered_end"] = ended.isoformat()
        current.sandbox_runtime = observation
        current.save(update_fields=["sandbox_runtime", "updated_at"])


def emit_sandbox_runtime(attempt: HostedHarnessAttempt) -> None:
    if is_oss():
        return
    job = attempt.job
    freeze_run_sandbox_charge(attempt, job)
    observation = (
        HostedHarnessAttempt.no_workspace_objects.values_list(
            "sandbox_runtime", flat=True
        ).get(id=attempt.id)
        or {}
    )
    if "billed_minutes" not in observation:
        return
    minutes = Decimal(observation["billed_minutes"])
    if not minutes:
        return
    from ee.usage.schemas.event_types import BillingEventType
    from ee.usage.schemas.events import UsageEvent
    from ee.usage.services.emitter import emit

    # Raises when the event did not reach the stream, so the seal is retried, not lost.
    emit(
        UsageEvent(
            event_id=str(uuid5(UUID(str(attempt.id)), "sandbox_runtime")),
            org_id=str(job.organization_id),
            event_type=BillingEventType.SANDBOX_RUNTIME,
            amount=float(minutes),
            timestamp=_aware(observation["metered_end"]),
            properties={
                "source": "rl_environment",
                "source_id": str(job.id),
                "harness_job_id": str(job.id),
                "attempt_id": str(attempt.id),
                "workspace_id": str(job.workspace_id or ""),
                "test_execution_id": str(job.test_execution_id or ""),
                "sandbox_kind": "run",
                "provider": observation["provider"],
                "failure_domain": (attempt.terminal_failure or {}).get("domain"),
            },
        ),
        raise_on_failure=True,
    )
