import structlog

from model_hub.models.annotation_queues import AutomationRule
from model_hub.utils.annotation_queue_helpers import (
    evaluate_rule,
    is_automation_rule_due,
)
from tfc.temporal import temporal_activity

logger = structlog.get_logger(__name__)


# Filter fields that are scoped to the requesting user. Recurring rules
# could supply rule.created_by (we do, below) but if that user is later
# deleted or removed from the workspace, the filter would fail. We skip
# them on the scheduled path so the issue is loud, not silent. Source of
# truth is bulk_selection._USER_SCOPED_COLUMN_IDS — keep these in sync.
USER_SCOPED_FIELDS = frozenset({"my_annotations", "annotator"})


def _has_user_scoped_filter(rule):
    conditions = rule.conditions or {}
    rules = conditions.get("rules") or []
    for cond in rules:
        if cond.get("field") in USER_SCOPED_FIELDS:
            return True
    filters = conditions.get("filter") or conditions.get("filters") or []
    for entry in filters:
        col = entry.get("column_id") or entry.get("columnId")
        if col in USER_SCOPED_FIELDS:
            return True
    return False


def run_due_automation_rules():
    """Evaluate enabled annotation automation rules whose cadence is due."""
    rules = (
        AutomationRule.objects.select_related(
            "queue",
            "queue__workspace",
            "organization",
            "created_by",
        )
        .filter(deleted=False, enabled=True, queue__deleted=False)
        .exclude(trigger_frequency="manual")
        .order_by("last_triggered_at", "created_at")
    )

    checked = 0
    evaluated = 0
    errors = 0
    added = 0
    duplicates = 0

    for rule in rules.iterator(chunk_size=100):
        checked += 1
        if not is_automation_rule_due(rule):
            continue
        if _has_user_scoped_filter(rule):
            errors += 1
            logger.warning(
                "automation_rule_scheduled_skipped_user_scoped_filter",
                rule_id=str(rule.pk),
            )
            continue
        try:
            # Run as the rule's creator so any non-user-scoped filter that
            # still uses request-time context (workspace fallback, etc.)
            # has a sensible identity to fall back on.
            result = evaluate_rule(rule, user=rule.created_by)
        except Exception as exc:
            errors += 1
            logger.exception(
                "automation_rule_scheduled_evaluation_exception",
                rule_id=str(rule.pk),
                error=str(exc),
            )
            continue
        if result.get("error"):
            errors += 1
            logger.warning(
                "automation_rule_scheduled_evaluation_error",
                rule_id=str(rule.pk),
                error=result.get("error"),
            )
            continue
        evaluated += 1
        added += result.get("added", 0)
        duplicates += result.get("duplicates", 0)

    summary = {
        "checked": checked,
        "evaluated": evaluated,
        "errors": errors,
        "added": added,
        "duplicates": duplicates,
    }
    logger.info("automation_rules_due_evaluation_complete", **summary)
    return summary


@temporal_activity(time_limit=1800, queue="default")
def evaluate_due_automation_rules():
    return run_due_automation_rules()


@temporal_activity(time_limit=900, queue="default")
def send_annotation_realtime_digest_task():
    """Every-15-min cron — fan out realtime digest emails to eligible users."""
    from model_hub.utils.annotation_digest import (
        send_annotation_realtime_digest_tick,
    )

    return send_annotation_realtime_digest_tick()


@temporal_activity(time_limit=900, queue="default")
def send_annotation_daily_digest_task():
    """Hourly cron — fires daily digest emails to users whose local hour matches."""
    from model_hub.utils.annotation_digest import (
        send_annotation_daily_digest_tick,
    )

    return send_annotation_daily_digest_tick()


@temporal_activity(time_limit=3600, queue="tasks_l")
def evaluate_rule_manual_async(rule_id, triggered_by_user_id=None):
    """Async heavy-lifting path for a manual rule run.

    Triggered from ``AutomationRuleViewSet.evaluate`` so the HTTP request can
    return 202 immediately while the (potentially long) filter resolution +
    bulk_create + auto-assign run on a worker.

    Always sends a completion email to creator + queue managers, regardless of
    run size, so users have a consistent "your run is ready" signal.
    """
    from django.contrib.auth import get_user_model

    rule = AutomationRule.objects.select_related(
        "queue", "queue__workspace", "organization", "created_by"
    ).get(pk=rule_id)

    triggered_by = None
    if triggered_by_user_id:
        try:
            triggered_by = get_user_model().objects.get(pk=triggered_by_user_id)
        except Exception:
            logger.warning(
                "automation_rule_manual_async_user_missing",
                rule_id=str(rule_id),
                user_id=str(triggered_by_user_id),
            )

    result = None
    error_message = None
    try:
        # cap=None → evaluate_rule fetches everything matching the filter.
        # _evaluate_rule_inner pages internally via cap+1 sentinel; the
        # watermark filter (last_triggered_at) bounds subsequent runs to
        # the delta, so even unbounded runs are bounded in practice on
        # rules that have run before.
        result = evaluate_rule(rule, user=triggered_by, cap=10_000_000)
    except Exception as exc:
        logger.exception(
            "automation_rule_manual_async_failed",
            rule_id=str(rule_id),
            error=str(exc),
        )
        error_message = str(exc) or exc.__class__.__name__
        result = {"matched": 0, "added": 0, "duplicates": 0}

    try:
        from model_hub.utils.annotation_queue_helpers import (
            send_rule_completion_email,
        )

        send_rule_completion_email(
            rule,
            result,
            triggered_by_user_id=triggered_by_user_id,
            error_message=error_message,
        )
    except Exception as exc:
        # Email failure must not poison the activity result; downstream
        # workflow status still reflects the actual evaluation outcome.
        logger.warning(
            "automation_rule_manual_async_email_failed",
            rule_id=str(rule_id),
            error=str(exc),
        )

    return result
