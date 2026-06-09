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
