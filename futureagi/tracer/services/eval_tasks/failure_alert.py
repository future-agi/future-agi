"""Internal Slack alert when a continuous eval task fails.

Fired from ``set_eval_task_status_activity`` when it writes ``failed`` — the
only live writer of that status — so it behaves like a DB trigger without a
schema change. The failure reason is not stored anywhere in the DB; it is read
from the failing run's Temporal history, which still exists because the
workflow is inside its ``except`` when it marks the task failed.

Posts to ``ERROR_LOGS_WEBHOOK`` (the internal #critical channel). Best-effort:
nothing here may fail the status write.
"""

import asyncio
import hashlib
from datetime import timedelta
from typing import NamedTuple

import structlog
from django.conf import settings
from django.core.cache import cache
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

from tfc.telemetry import otel_sync_to_async
from tfc.utils.slack import send_critical_slack_notification

logger = structlog.get_logger(__name__)

_REASON_LIMIT = 1500
_EVAL_NAME_LIMIT = 5
# The history read gets its own budget so a slow Temporal still lets the alert
# go out (with the workflow id to look the reason up by). The Slack post is
# bounded by the webhook timeout instead: the post runs in a worker thread,
# which asyncio cancellation cannot interrupt.
_REASON_TIMEOUT_SECONDS = 8
_SLACK_TIMEOUT_SECONDS = 10
# One alert per (task, failing activity, error type) in this window. Covers a
# retried status activity and the sweeper's opt-in auto-recovery
# (EVAL_TASK_SWEEP_RECOVER_FAILED) failing the same way again every tick, and
# re-alerts after the window while the task keeps failing.
_DEDUPE_TTL_SECONDS = 6 * 3600

# Failures the workflow catches and survives: an entry's eval failing (caught
# and recorded on the entry) and the follow-up that marks the entry errored
# (gathered with return_exceptions). Never the reason a task failed.
_HANDLED_ACTIVITIES = frozenset({"run_eval_entry_activity", "fail_eval_entry_activity"})


class FailureReason(NamedTuple):
    activity: str
    error_type: str
    message: str

    @property
    def text(self) -> str:
        parts = [self.activity, self.error_type, self.message]
        return " · ".join(p for p in parts if p)[:_REASON_LIMIT]


async def notify_eval_task_failed(task_id: str, workflow_id: str, run_id: str) -> None:
    if not str(getattr(settings, "ERROR_LOGS_WEBHOOK", "") or "").strip():
        return
    try:
        try:
            reason = await asyncio.wait_for(
                fetch_failure_reason(workflow_id, run_id),
                timeout=_REASON_TIMEOUT_SECONDS,
            )
        except Exception as e:
            logger.warning(
                "eval_task_failure_reason_unavailable",
                task_id=task_id,
                error_type=type(e).__name__,
            )
            reason = None
        await otel_sync_to_async(_post_failure_alert, thread_sensitive=False)(
            task_id, workflow_id, reason
        )
    except Exception:
        logger.exception("eval_task_failure_alert_failed", task_id=task_id)


async def fetch_failure_reason(workflow_id: str, run_id: str) -> FailureReason | None:
    """The control activity whose failure ended this run. None when the run
    failed in workflow code rather than in an activity."""
    from tfc.temporal.common.client import get_client

    client = await get_client()
    history = await client.get_workflow_handle(
        workflow_id, run_id=run_id
    ).fetch_history()

    activity_names: dict[int, str] = {}
    reason = None
    for event in history.events:
        if event.HasField("activity_task_scheduled_event_attributes"):
            activity_names[event.event_id] = (
                event.activity_task_scheduled_event_attributes.activity_type.name
            )
            continue
        if event.HasField("activity_task_completed_event_attributes"):
            # A later success of the same activity means its earlier failure
            # was handled (e.g. a deferred reconcile that went through next poll).
            name = activity_names.get(
                event.activity_task_completed_event_attributes.scheduled_event_id
            )
            if reason and name == reason.activity:
                reason = None
            continue
        if event.HasField("activity_task_failed_event_attributes"):
            attrs = event.activity_task_failed_event_attributes
        elif event.HasField("activity_task_timed_out_event_attributes"):
            attrs = event.activity_task_timed_out_event_attributes
        else:
            continue
        name = activity_names.get(attrs.scheduled_event_id, "unknown activity")
        if name not in _HANDLED_ACTIVITIES:
            reason = describe_failure(name, attrs.failure)
    return reason


def describe_failure(activity_name: str, failure) -> FailureReason:
    # The outermost application failure is the one the activity raised with
    # its own type (Temporal adds wrappers above it and the chained causes
    # below it); without one, fall back to the innermost cause.
    node = failure
    while not node.HasField("application_failure_info") and node.HasField("cause"):
        node = node.cause
    error_type = (
        node.application_failure_info.type
        if node.HasField("application_failure_info")
        else ""
    )
    return FailureReason(activity_name, error_type, node.message)


def _post_failure_alert(
    task_id: str, workflow_id: str, reason: FailureReason | None
) -> None:
    close_old_connections()
    try:
        from tracer.models.eval_task import EvalTask

        key = _dedupe_key(task_id, reason)
        if _cache_get(key):
            logger.info("eval_task_failure_alert_deduped", task_id=task_id)
            return
        task = EvalTask.objects.select_related(
            "project__organization", "project__workspace", "project__user"
        ).get(id=task_id)
        text, blocks = build_failure_alert(task, workflow_id, reason)
        # Marked only after a successful post, so an attempt that dies before
        # posting leaves the retry free to alert.
        if send_critical_slack_notification(
            text, blocks=blocks, timeout=_SLACK_TIMEOUT_SECONDS
        ):
            _cache_set(key)
    finally:
        close_old_connections()


def _dedupe_key(task_id: str, reason: FailureReason | None) -> str:
    signature = f"{reason.activity}:{reason.error_type}" if reason else ""
    digest = hashlib.sha1(signature.encode()).hexdigest()[:16]
    return f"eval-task-failed-alert:{task_id}:{digest}"


def _cache_get(key: str) -> bool:
    # Fail open: a cache outage may double-post, never drop the alert.
    try:
        return bool(cache.get(key))
    except Exception:
        logger.warning("eval_task_failure_alert_cache_unavailable", op="get")
        return False


def _cache_set(key: str) -> None:
    try:
        cache.set(key, 1, _DEDUPE_TTL_SECONDS)
    except Exception:
        logger.warning("eval_task_failure_alert_cache_unavailable", op="set")


def _esc(value) -> str:
    """Slack mrkdwn escaping: customer-controlled names must not render as
    mentions (``<!channel>``) or break links."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_failure_alert(task, workflow_id: str, reason: FailureReason | None):
    """``(fallback_text, blocks)`` for one failed task."""
    from tracer.models.observation_span import EvalEntryStatus, EvalLogger

    project = task.project
    org = project.organization
    org_name = org.name or "(no name)"
    entries = EvalLogger.objects.filter(eval_task_id=str(task.id))
    stranded = entries.filter(
        status__in=[EvalEntryStatus.PENDING, EvalEntryStatus.RUNNING]
    ).count()
    last_24h = entries.filter(
        created_at__gte=timezone.now() - timedelta(hours=24)
    ).count()
    eval_names = [_esc(e.name or e.id) for e in task.evals.all()[:_EVAL_NAME_LIMIT]]
    tier = _lookup("tier", _tier, org) or "unknown"
    owner = _lookup("owner", _owner_email, org, project) or "no owner found"

    app_base = getattr(settings, "APP_BASE_URL", "").rstrip("/")
    task_label = f"*{_esc(task.name)}*"
    if app_base:
        # ``|`` ends the URL part of a link; the label after it is escaped.
        task_label = f"<{app_base}/dashboard/tasks/{task.id}|{_esc(task.name)}>"
    down_since = (
        f"<!date^{int(task.end_time.timestamp())}^{{date_short_pretty}} at {{time}}|"
        f"{task.end_time:%Y-%m-%d %H:%M} UTC>"
        if task.end_time
        else "unknown"
    )

    who = "\n".join(
        [
            f"*Customer:* {_esc(org_name)} · tier *{_esc(tier)}* · {_esc(owner)}",
            f"*Org:* `{org.id}`",
            f"*Workspace:* {_esc(project.workspace.name) if project.workspace else '—'}",
            f"*Project:* {_esc(project.name)} (`{project.id}`)",
            f"*Task:* {task_label} (`{task.id}`)",
        ]
    )
    if reason:
        # A backtick fence inside the message would close the code block.
        why = "```" + _esc(reason.text).replace("```", "'''") + "```"
    else:
        why = (
            "_No failed activity in the run's history — the workflow itself "
            "raised. Check Temporal._"
        )
    impact = "\n".join(
        [
            f"*Down since:* {down_since} — evals stopped for this task until "
            "someone resumes it",
            f"*Evals:* {', '.join(eval_names) or '—'}",
            f"*Entries stranded pending/running:* {stranded}",
            f"*Entries created in last 24h:* {last_24h} (≈ evals/day now missed)",
        ]
    )
    trace = (
        f"*Temporal workflow:* `{workflow_id}`\n"
        f"*Sentry:* search `temporal.workflow_id:{workflow_id}` for the stack trace"
    )

    text = f"Continuous eval task FAILED — {_esc(org_name)}: {_esc(task.name)}"
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f":rotating_light: Continuous eval task FAILED — {org_name}"[
                    :150
                ],
                "emoji": True,
            },
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": who[:3000]}},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Why*\n{why}"[:3000]},
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": impact[:3000]}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": trace}]},
    ]
    return text, blocks


def _lookup(field: str, fn, *args):
    # One failed enrichment must not cost the whole alert.
    try:
        return fn(*args)
    except Exception as e:
        logger.warning(
            "eval_task_failure_alert_lookup_failed",
            field=field,
            error_type=type(e).__name__,
        )
        return None


def _tier(org) -> str | None:
    try:
        from ee.usage.models.usage import OrganizationSubscription
    except ImportError:
        return None
    sub = (
        OrganizationSubscription.objects.filter(organization=org)
        .select_related("subscription_tier")
        .first()
    )
    return sub.subscription_tier.name if sub else None


def _owner_email(org, project) -> str | None:
    from accounts.models.organization_membership import OrganizationMembership
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    owner = (
        OrganizationMembership.objects.filter(organization=org, is_active=True)
        .filter(
            Q(level=Level.OWNER) | Q(level__isnull=True, role=OrganizationRoles.OWNER)
        )
        .select_related("user")
        .order_by("joined_at")
        .first()
    )
    if owner:
        return owner.user.email
    return project.user.email if project.user else None
