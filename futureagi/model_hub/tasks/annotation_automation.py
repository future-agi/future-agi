import structlog

from model_hub.models.annotation_queues import AutomationRule
from model_hub.utils.annotation_queue_helpers import (
    AUTOMATION_RULE_ACTOR_ERROR,
    AUTOMATION_RULE_ENTITLEMENT_CODE,
    AUTOMATION_RULE_MATCH_LIMIT,
    _automation_actor_refusal,
    _automation_rule_actor,
    automation_rule_entitlement_check,
    evaluate_rule,
    is_automation_rule_due,
)
from tfc.temporal import temporal_activity

logger = structlog.get_logger(__name__)


def run_due_automation_rules():
    """Evaluate enabled annotation automation rules whose cadence is due."""
    rules = (
        AutomationRule.no_workspace_objects.select_related(
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
    entitlement_cache = {}

    for rule in rules.iterator(chunk_size=100):
        checked += 1
        if not is_automation_rule_due(rule):
            continue
        try:
            actor = _automation_rule_actor(rule.queue, rule.created_by)
            if actor is None:
                errors += 1
                _automation_actor_refusal(rule)
                continue
            check = automation_rule_entitlement_check(rule, cache=entitlement_cache)
            if check is not None and not check.allowed:
                errors += 1
                logger.warning(
                    "automation_rule_scheduled_skipped_entitlement",
                    rule_id=str(rule.pk),
                    reason=AUTOMATION_RULE_ENTITLEMENT_CODE,
                )
                continue
            # evaluate_rule requires a live, active creator with current workspace
            # access and queue-manager rights, independently of source ownership.
            # A deleted or revoked creator fails closed, never running actor-free.
            # User-scoped filters use this revalidated creator, just as manual
            # runs use their revalidated triggering actor.
            result = evaluate_rule(
                rule,
                user=actor,
                cap=AUTOMATION_RULE_MATCH_LIMIT,
                entitlement_cache=entitlement_cache,
            )
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

    Authorized attempts send a completion email to creator + queue managers,
    regardless of run size. An unavailable actor gets only the fixed refusal,
    never queue metadata through a completion notification.
    """
    from django.contrib.auth import get_user_model
    from django.core.exceptions import ValidationError

    rule = AutomationRule.no_workspace_objects.select_related(
        "queue", "queue__workspace", "organization", "created_by"
    ).get(pk=rule_id)

    triggered_by = None
    if triggered_by_user_id:
        try:
            triggered_by = (
                get_user_model()
                .objects.filter(pk=triggered_by_user_id, is_active=True)
                .first()
            )
        except (ValidationError, ValueError, TypeError):
            pass
    # Refuse before entitlement/scope errors or completion notification could
    # mask the missing run authority. Never substitute creator/request context.
    # Database failures propagate rather than masquerading as missing accounts.
    triggered_by = _automation_rule_actor(rule.queue, triggered_by)
    if triggered_by is None:
        return _automation_actor_refusal(rule)

    result = None
    error_message = None
    try:
        # Prove the full selection fits the supported ceiling before any queue
        # write.  A cap+1 sentinel returns a specific error and zero additions
        # when the rule matches more than 10,000 items.
        result = evaluate_rule(
            rule,
            user=triggered_by,
            cap=AUTOMATION_RULE_MATCH_LIMIT,
        )
        error_message = result.get("error") or None
    except Exception as exc:
        logger.exception(
            "automation_rule_manual_async_failed",
            rule_id=str(rule_id),
            error=str(exc),
        )
        error_message = str(exc) or exc.__class__.__name__
        result = {"matched": 0, "added": 0, "duplicates": 0}

    if result.get("error") == AUTOMATION_RULE_ACTOR_ERROR:
        # The notification helper resolves recipients and renders queue metadata.
        # An unauthorized/missing actor must not reach that secondary read path.
        return result

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
