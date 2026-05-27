"""Annotation queue digest emails — two cadences:

- **Realtime (every 15min cron, throttled to max 1/hour/user/track):**
  fires when new items have been assigned (annotator) or completed-pending-
  review (reviewer/manager) since the user's ``last_realtime_digest_at``.

- **Daily (hourly cron, fires per user-local 9am):** current-state snapshot
  of pending items grouped by queue, skipped when the user has zero pending.

Both tracks render through ``tfc.utils.email.email_helper`` using the
``annotation_realtime_digest.html`` / ``annotation_daily_digest.html``
templates. Users can disable both via ``AnnotationNotificationState.digest_enabled``
(unsubscribe link in the email footer) or pause realtime only via
``realtime_snoozed_until`` (snooze link).
"""

import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import structlog
from django.contrib.auth import get_user_model
from django.db.models import Count, Min, Q
from django.utils import timezone

from model_hub.models.annotation_queues import (
    AnnotationNotificationState,
    AnnotationQueue,
    AnnotationQueueAnnotator,
    QueueItem,
    annotation_queue_role_q,
)
from model_hub.models.choices import (
    AnnotatorRole,
    QueueItemStatus,
)
from tfc.utils.email import email_helper

logger = structlog.get_logger(__name__)

User = get_user_model()

# Throttle the realtime track to at most 1 email per hour per user. The
# scheduler fires every 15min so without this users would get up to 96
# emails/day — Linear and similar tools cap at 1/hour per track for
# exactly this deliverability reason.
REALTIME_THROTTLE = timedelta(hours=1)
# Daily digest minimum gap: 12 hours (so a user whose TZ shifts doesn't
# get two daily emails in the same calendar day).
DAILY_MIN_GAP = timedelta(hours=12)


# ---------------------------------------------------------------------------
# Pending-item resolution
# ---------------------------------------------------------------------------


def _annotator_pending_by_queue(
    user, since: Optional[datetime] = None
) -> Dict[str, Dict]:
    """Return ``{queue_id: {queue, count, oldest}}`` for items assigned to ``user``.

    Items are "pending" if status != COMPLETED. When ``since`` is given,
    only include items assigned *after* that timestamp (used for the
    realtime delta track). When ``since`` is None we return the current
    state (used for daily digest).
    """
    qs = QueueItem.objects.filter(
        deleted=False,
        queue__deleted=False,
    ).exclude(status=QueueItemStatus.COMPLETED.value)

    # Match either single-assignee FK or multi-assignee M2M.
    qs = qs.filter(Q(assigned_to=user) | Q(assigned_users=user)).distinct()

    if since is not None:
        # "New since last digest" — use created_at as a proxy for assignment
        # time; QueueItemAssignment lacks a populated assigned_at column on
        # legacy rows. For items added by automation rules ``created_at``
        # equals assignment time; for manually-assigned legacy items the
        # delta may include slightly older rows on first run.
        qs = qs.filter(created_at__gt=since)

    aggregated = (
        qs.values("queue_id", "queue__name")
        .annotate(count=Count("id"), oldest=Min("created_at"))
        .order_by("-count")
    )
    return {
        str(row["queue_id"]): {
            "queue_id": str(row["queue_id"]),
            "queue_name": row["queue__name"],
            "count": row["count"],
            "oldest_at": row["oldest"],
        }
        for row in aggregated
    }


def _reviewer_pending_by_queue(
    user, since: Optional[datetime] = None
) -> Dict[str, Dict]:
    """Return ``{queue_id: {queue, count, oldest}}`` for review work."""
    # Queues where the user can review: explicit reviewers and managers.
    review_queue_ids = list(
        AnnotationQueueAnnotator.objects.filter(
            user=user, deleted=False
        )
        .filter(
            annotation_queue_role_q(
                AnnotatorRole.REVIEWER.value,
                AnnotatorRole.MANAGER.value,
            )
        )
        .values_list("queue_id", flat=True)
        .distinct()
    )
    if not review_queue_ids:
        return {}

    # "Pending review" = items completed but not yet reviewed.
    qs = QueueItem.objects.filter(
        deleted=False,
        queue__deleted=False,
        queue_id__in=review_queue_ids,
        status=QueueItemStatus.COMPLETED.value,
        reviewed_at__isnull=True,
    )

    if since is not None:
        # Use updated_at as a proxy for "moved into completed state" — when
        # an annotator finishes an item the row updates. Slightly imprecise
        # but avoids a join to the activity log.
        qs = qs.filter(updated_at__gt=since)

    aggregated = (
        qs.values("queue_id", "queue__name")
        .annotate(count=Count("id"), oldest=Min("updated_at"))
        .order_by("-count")
    )
    return {
        str(row["queue_id"]): {
            "queue_id": str(row["queue_id"]),
            "queue_name": row["queue__name"],
            "count": row["count"],
            "oldest_at": row["oldest"],
        }
        for row in aggregated
    }


# ---------------------------------------------------------------------------
# State + token helpers
# ---------------------------------------------------------------------------


def _get_state(user) -> AnnotationNotificationState:
    state, _ = AnnotationNotificationState.objects.get_or_create(user=user)
    return state


def _unsubscribe_token(user_id) -> str:
    """HMAC-signed token so unsubscribe links can't be guessed."""
    from django.core.signing import TimestampSigner

    return TimestampSigner(salt="annotation-digest-unsub").sign(str(user_id))


def _verify_unsubscribe_token(token: str) -> Optional[str]:
    """Returns user_id if valid (and not older than 30 days), else None."""
    from django.core.signing import BadSignature, TimestampSigner

    try:
        return TimestampSigner(salt="annotation-digest-unsub").unsign(
            token, max_age=timedelta(days=30)
        )
    except BadSignature:
        return None


def _frontend_url() -> str:
    return os.environ.get("FRONTEND_URL", "https://app.futureagi.com").rstrip("/")


def _backend_url() -> str:
    return os.environ.get("BACKEND_URL", "https://api.futureagi.com").rstrip("/")


# ---------------------------------------------------------------------------
# Realtime digest (every 15min cron, throttled to 1/hour/user)
# ---------------------------------------------------------------------------


def _send_realtime_for_user(user, now: datetime) -> bool:
    """Send a realtime digest email if the user has new items + throttle allows.

    Returns True iff an email was sent.
    """
    state = _get_state(user)

    if not state.digest_enabled:
        return False

    if state.realtime_snoozed_until and state.realtime_snoozed_until > now:
        return False

    # Throttle: at most 1 realtime email per hour per user.
    if state.last_realtime_digest_at and (
        now - state.last_realtime_digest_at
    ) < REALTIME_THROTTLE:
        return False

    since = state.last_realtime_digest_at
    annotator_buckets = _annotator_pending_by_queue(user, since=since)
    reviewer_buckets = _reviewer_pending_by_queue(user, since=since)

    annotator_total = sum(b["count"] for b in annotator_buckets.values())
    reviewer_total = sum(b["count"] for b in reviewer_buckets.values())
    if annotator_total == 0 and reviewer_total == 0:
        # Skip-if-empty — don't send a "nothing new" email. We don't bump
        # last_realtime_digest_at here either, so the next tick sees the
        # same delta window.
        return False

    subject_parts = []
    if annotator_total:
        subject_parts.append(
            f"{annotator_total} new item{'s' if annotator_total != 1 else ''} assigned"
        )
    if reviewer_total:
        subject_parts.append(
            f"{reviewer_total} new pending review{'s' if reviewer_total != 1 else ''}"
        )
    subject = " · ".join(subject_parts)

    frontend_url = _frontend_url()
    backend_url = _backend_url()
    unsub_token = _unsubscribe_token(user.id)
    template_data = {
        "user_name": (getattr(user, "name", None) or user.email),
        "annotator_total": annotator_total,
        "reviewer_total": reviewer_total,
        "annotator_buckets": list(annotator_buckets.values()),
        "reviewer_buckets": list(reviewer_buckets.values()),
        "queues_url": f"{frontend_url}/dashboard/annotations/queues",
        "unsubscribe_url": (
            f"{backend_url}/accounts/notifications/unsubscribe/?token={unsub_token}"
        ),
        "snooze_url": (
            f"{backend_url}/accounts/notifications/snooze/?token={unsub_token}&days=7"
        ),
    }

    try:
        email_helper(
            mail_subject=subject,
            template_name="annotation_realtime_digest.html",
            template_data=template_data,
            to_email_list=[user.email],
        )
    except Exception as exc:
        logger.warning(
            "annotation_realtime_digest_send_failed",
            user_id=str(user.id),
            error=str(exc),
        )
        return False

    state.last_realtime_digest_at = now
    state.save(update_fields=["last_realtime_digest_at", "updated_at"])
    return True


def send_annotation_realtime_digest_tick() -> Dict[str, int]:
    """One run of the 15-min realtime digest cron. Returns summary counters."""
    now = timezone.now()
    sent = 0
    skipped = 0
    errors = 0
    # Candidates: users who are annotators OR managers on any non-deleted queue.
    candidate_ids = set(
        AnnotationQueueAnnotator.objects.filter(
            deleted=False, queue__deleted=False
        ).values_list("user_id", flat=True)
    )
    candidate_ids |= set(
        QueueItem.objects.filter(
            deleted=False, queue__deleted=False, assigned_to__isnull=False
        ).values_list("assigned_to_id", flat=True)
    )

    for user in User.objects.filter(id__in=candidate_ids, is_active=True):
        try:
            if _send_realtime_for_user(user, now):
                sent += 1
            else:
                skipped += 1
        except Exception as exc:
            errors += 1
            logger.exception(
                "annotation_realtime_digest_user_failed",
                user_id=str(user.id),
                error=str(exc),
            )

    summary = {"sent": sent, "skipped": skipped, "errors": errors}
    logger.info("annotation_realtime_digest_tick", **summary)
    return summary


# ---------------------------------------------------------------------------
# Daily digest (hourly cron, fires per user-local hour)
# ---------------------------------------------------------------------------


def _user_local_hour(tz_name: str, now_utc: datetime) -> Optional[int]:
    """Return the user's local hour (0-23) given a tz name, or None on bad tz."""
    try:
        import zoneinfo
    except ImportError:  # pragma: no cover — Python < 3.9 not supported
        return None
    try:
        tz = zoneinfo.ZoneInfo(tz_name or "UTC")
    except Exception:
        tz = zoneinfo.ZoneInfo("UTC")
    return now_utc.astimezone(tz).hour


def _send_daily_for_user(user, now: datetime) -> bool:
    """Send a daily digest if user-TZ-local hour matches + > DAILY_MIN_GAP."""
    state = _get_state(user)

    if not state.digest_enabled:
        return False

    local_hour = _user_local_hour(getattr(user, "last_timezone", "UTC"), now)
    if local_hour != state.daily_digest_hour_local:
        return False

    if state.last_daily_digest_at and (
        now - state.last_daily_digest_at
    ) < DAILY_MIN_GAP:
        return False

    # Current-state snapshot — pass since=None so we count everything pending,
    # not just the delta. Daily is a "morning reminder" not a "what changed".
    annotator_buckets = _annotator_pending_by_queue(user, since=None)
    reviewer_buckets = _reviewer_pending_by_queue(user, since=None)

    annotator_total = sum(b["count"] for b in annotator_buckets.values())
    reviewer_total = sum(b["count"] for b in reviewer_buckets.values())
    if annotator_total == 0 and reviewer_total == 0:
        # Skip-if-empty as the user explicitly requested.
        return False

    subject_parts = []
    if annotator_total:
        subject_parts.append(
            f"{annotator_total} item{'s' if annotator_total != 1 else ''} pending"
        )
    if reviewer_total:
        subject_parts.append(
            f"{reviewer_total} review{'s' if reviewer_total != 1 else ''} pending"
        )
    subject = "Your annotation queue — " + " · ".join(subject_parts)

    frontend_url = _frontend_url()
    backend_url = _backend_url()
    unsub_token = _unsubscribe_token(user.id)
    template_data = {
        "user_name": (getattr(user, "name", None) or user.email),
        "annotator_total": annotator_total,
        "reviewer_total": reviewer_total,
        "annotator_buckets": list(annotator_buckets.values()),
        "reviewer_buckets": list(reviewer_buckets.values()),
        "queues_url": f"{frontend_url}/dashboard/annotations/queues",
        "unsubscribe_url": (
            f"{backend_url}/accounts/notifications/unsubscribe/?token={unsub_token}"
        ),
    }

    try:
        email_helper(
            mail_subject=subject,
            template_name="annotation_daily_digest.html",
            template_data=template_data,
            to_email_list=[user.email],
        )
    except Exception as exc:
        logger.warning(
            "annotation_daily_digest_send_failed",
            user_id=str(user.id),
            error=str(exc),
        )
        return False

    state.last_daily_digest_at = now
    state.save(update_fields=["last_daily_digest_at", "updated_at"])
    return True


def send_annotation_daily_digest_tick() -> Dict[str, int]:
    """Hourly cron that fires for users whose local hour matches their digest hour."""
    now = timezone.now()
    sent = 0
    skipped = 0
    errors = 0
    candidate_ids = set(
        AnnotationQueueAnnotator.objects.filter(
            deleted=False, queue__deleted=False
        ).values_list("user_id", flat=True)
    )
    candidate_ids |= set(
        QueueItem.objects.filter(
            deleted=False, queue__deleted=False, assigned_to__isnull=False
        ).values_list("assigned_to_id", flat=True)
    )

    for user in User.objects.filter(id__in=candidate_ids, is_active=True):
        try:
            if _send_daily_for_user(user, now):
                sent += 1
            else:
                skipped += 1
        except Exception as exc:
            errors += 1
            logger.exception(
                "annotation_daily_digest_user_failed",
                user_id=str(user.id),
                error=str(exc),
            )

    summary = {"sent": sent, "skipped": skipped, "errors": errors}
    logger.info("annotation_daily_digest_tick", **summary)
    return summary
