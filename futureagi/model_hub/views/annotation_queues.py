import json
import re
import threading
import unicodedata
import uuid
from datetime import date, datetime, timedelta

import structlog
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Exists, Max, OuterRef, Prefetch, Q
from django.db.models.functions import Coalesce, Lower, TruncDate
from django.utils import timezone
from drf_yasg.utils import no_body, swagger_auto_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models.user import User
from model_hub.models.annotation_queues import (
    FULL_ACCESS_QUEUE_ROLES,
    SOURCE_TYPE_FK_MAP,
    VALID_STATUS_TRANSITIONS,
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AnnotationQueueLabel,
    AutomationRule,
    QueueItem,
    QueueItemAssignment,
    QueueItemNote,
    QueueItemReviewComment,
    QueueItemReviewThread,
    annotation_queue_effective_roles,
    annotation_queue_role_q,
    user_has_annotation_queue_admin_access,
)
from model_hub.models.choices import (
    AnnotationQueueStatusChoices,
    AnnotationTypeChoices,
    AnnotatorRole,
    AssignmentStrategy,
    DataTypeChoices,
    QueueItemSourceType,
    QueueItemStatus,
    ScoreSource,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import SCORE_SOURCE_FK_MAP, Score
from model_hub.serializers.annotation_queues import (
    AddItemsSerializer,
    AnnotationQueueListQuerySerializer,
    AssignItemsSerializer,
    AnnotateDetailSerializer,
    AnnotationQueueSerializer,
    AutomationRuleSerializer,
    BulkRemoveItemsSerializer,
    DiscussionCommentRequestSerializer,
    DiscussionReactionRequestSerializer,
    DiscussionThreadStatusRequestSerializer,
    ImportAnnotationsSerializer,
    QueueDefaultRequestSerializer,
    QueueExportQuerySerializer,
    QueueExportToDatasetRequestSerializer,
    QueueForSourceQuerySerializer,
    QueueHardDeleteRequestSerializer,
    QueueItemAnnotateDetailQuerySerializer,
    QueueItemListQuerySerializer,
    QueueItemNavigationRequestSerializer,
    QueueItemNextQuerySerializer,
    QueueLabelRequestSerializer,
    QueueItemReviewCommentSerializer,
    QueueItemReviewThreadSerializer,
    QueueItemSerializer,
    QueueStatusRequestSerializer,
    ReviewItemRequestSerializer,
    SubmitAnnotationsSerializer,
)
from model_hub.serializers.scores import ScoreSerializer
from model_hub.services.bulk_selection import (
    resolve_filtered_call_execution_ids,
    resolve_filtered_session_ids,
    resolve_filtered_span_ids,
    resolve_filtered_trace_ids,
)
from model_hub.utils.annotation_queue_helpers import (
    auto_assign_items,
    calculate_agreement,
    evaluate_rule,
    get_fk_field_name,
    resolve_source_content,
    resolve_source_object,
)
from model_hub.utils.utils import send_message_to_channel
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.email import email_helper
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.span_notes import SpanNotes

logger = structlog.get_logger(__name__)

# Shared cap for filter-mode bulk add. Phase 11 may introduce an async job
# path for selections exceeding this; until then, the endpoint errors with
# ``selection_too_large`` so the UI can prompt the user to narrow the filter.
MAX_SELECTION_CAP = 10_000

SOURCE_TYPE_EXPORT_LABELS = {
    QueueItemSourceType.DATASET_ROW.value: "dataset row",
    QueueItemSourceType.TRACE.value: "trace",
    QueueItemSourceType.OBSERVATION_SPAN.value: "span",
    QueueItemSourceType.PROTOTYPE_RUN.value: "prototype run",
    QueueItemSourceType.CALL_EXECUTION.value: "voice call",
    QueueItemSourceType.TRACE_SESSION.value: "session",
}

ANNOTATION_SLOT_FIELDS = (
    ("value", "score", None),
    ("annotator_name", "annotator name", DataTypeChoices.TEXT.value),
    ("annotator_email", "annotator email", DataTypeChoices.TEXT.value),
    ("annotator_id", "annotator ID", DataTypeChoices.TEXT.value),
    ("notes", "notes", DataTypeChoices.TEXT.value),
    ("score_source", "score source", DataTypeChoices.TEXT.value),
    ("created_at", "annotated at", DataTypeChoices.DATETIME.value),
    ("updated_at", "updated at", DataTypeChoices.DATETIME.value),
    ("annotation", "annotation record", DataTypeChoices.JSON.value),
)

OPEN_REVIEW_THREAD_STATUSES = (
    QueueItemReviewThread.STATUS_OPEN,
    QueueItemReviewThread.STATUS_REOPENED,
)
VISIBLE_REVIEW_COMMENT_ACTIONS = (
    QueueItemReviewComment.ACTION_COMMENT,
    QueueItemReviewComment.ACTION_APPROVE,
    QueueItemReviewComment.ACTION_REQUEST_CHANGES,
)
MAX_MENTIONED_USERS_PER_COMMENT = 50
USER_MENTION_RE = re.compile(
    r"user:([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)
EMAIL_ADDRESS_RE = re.compile(
    r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$",
    re.IGNORECASE,
)
EMAIL_MENTION_RE = re.compile(
    r"(?<![\w.+-])@"
    r"([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})"
    r"(?=$|[\s,;:!?\)\]}]|\.(?=$|\s))",
    re.IGNORECASE,
)
MAX_DISCUSSION_REACTION_LENGTH = 16
DEFAULT_QUEUE_FULL_ACCESS_ROLES = list(FULL_ACCESS_QUEUE_ROLES)


def _is_supported_discussion_reaction(value):
    emoji = str(value or "").strip()
    if not emoji or len(emoji) > MAX_DISCUSSION_REACTION_LENGTH:
        return False

    has_symbol = False
    for char in emoji:
        category = unicodedata.category(char)
        if char.isspace() or char.isalpha() or char.isdigit():
            return False
        if char.isascii() and char not in ("\u200d", "\ufe0f", "\u20e3"):
            return False
        if category[0] in ("S", "M") or char in ("\u200d", "\ufe0f", "\u20e3"):
            has_symbol = True
            continue
        return False
    return has_symbol


def _queue_item_user_scope(queryset, user, *, include_unassigned):
    """Apply legacy and multi-assignment ownership rules for queue items."""
    user_id = getattr(user, "id", user)
    assigned_to_user = Q(assigned_to_id=user_id) | Q(
        assignments__user_id=user_id,
        assignments__deleted=False,
    )
    if include_unassigned:
        assigned_to_user |= Q(assigned_to__isnull=True) & ~Q(assignments__deleted=False)
    return queryset.filter(assigned_to_user).distinct()


def _scope_targeted_rework_items(queryset, user):
    """Only route targeted review rework back to the targeted annotator."""
    if not user:
        return queryset

    global_rework_threads = QueueItemReviewThread.objects.filter(
        queue_item_id=OuterRef("pk"),
        action=QueueItemReviewThread.ACTION_REQUEST_CHANGES,
        blocking=True,
        status__in=OPEN_REVIEW_THREAD_STATUSES,
        target_annotator__isnull=True,
        deleted=False,
    )
    targeted_rework_threads = QueueItemReviewThread.objects.filter(
        queue_item_id=OuterRef("pk"),
        action=QueueItemReviewThread.ACTION_REQUEST_CHANGES,
        blocking=True,
        status__in=OPEN_REVIEW_THREAD_STATUSES,
        target_annotator__isnull=False,
        deleted=False,
    )
    targeted_threads_to_user = targeted_rework_threads.filter(
        target_annotator_id=user.id
    )
    targeted_rework = QueueItemReviewComment.objects.filter(
        queue_item_id=OuterRef("pk"),
        thread__isnull=True,
        action=QueueItemReviewComment.ACTION_REQUEST_CHANGES,
        target_annotator__isnull=False,
        deleted=False,
    )
    global_rework = QueueItemReviewComment.objects.filter(
        queue_item_id=OuterRef("pk"),
        thread__isnull=True,
        action=QueueItemReviewComment.ACTION_REQUEST_CHANGES,
        target_annotator__isnull=True,
        deleted=False,
    )
    targeted_to_user = targeted_rework.filter(target_annotator_id=user.id)
    return queryset.annotate(
        _has_global_rework_thread=Exists(global_rework_threads),
        _has_targeted_rework_thread=Exists(targeted_rework_threads),
        _targeted_thread_to_requester=Exists(targeted_threads_to_user),
        _has_global_rework=Exists(global_rework),
        _has_targeted_rework=Exists(targeted_rework),
        _targeted_to_requester=Exists(targeted_to_user),
    ).filter(
        (Q(_has_targeted_rework_thread=False) & Q(_has_targeted_rework=False))
        | Q(_has_global_rework_thread=True)
        | Q(_has_global_rework=True)
        | Q(_targeted_thread_to_requester=True)
        | Q(_targeted_to_requester=True)
    )


def _open_blocking_rework_threads(item):
    return QueueItemReviewThread.objects.filter(
        queue_item=item,
        action=QueueItemReviewThread.ACTION_REQUEST_CHANGES,
        blocking=True,
        status__in=OPEN_REVIEW_THREAD_STATUSES,
        deleted=False,
    )


def _has_open_rework_for_user(item, user):
    """Whether this user can edit an item that is otherwise pending review."""
    if not user:
        return False
    threads = _open_blocking_rework_threads(item)
    if threads.filter(target_annotator__isnull=True).exists():
        return True
    if threads.filter(target_annotator=user).exists():
        return True

    # Legacy safety for pre-thread request-change comments.
    return QueueItemReviewComment.objects.filter(
        queue_item=item,
        thread__isnull=True,
        action=QueueItemReviewComment.ACTION_REQUEST_CHANGES,
        deleted=False,
    ).filter(Q(target_annotator__isnull=True) | Q(target_annotator=user)).exists()


def _apply_review_status_filters_for_user(
    queryset,
    *,
    review_status=None,
    exclude_review_status=None,
    user=None,
    is_reviewer=False,
):
    """Apply review filters without hiding targeted rework from its annotator."""
    if review_status:
        return queryset.filter(review_status=review_status)

    if not exclude_review_status:
        return queryset

    if exclude_review_status != "pending_review" or not user or is_reviewer:
        return queryset.exclude(review_status=exclude_review_status)

    targeted_threads_to_user = QueueItemReviewThread.objects.filter(
        queue_item_id=OuterRef("pk"),
        action=QueueItemReviewThread.ACTION_REQUEST_CHANGES,
        blocking=True,
        status__in=OPEN_REVIEW_THREAD_STATUSES,
        target_annotator_id=user.id,
        deleted=False,
    )
    global_rework_threads = QueueItemReviewThread.objects.filter(
        queue_item_id=OuterRef("pk"),
        action=QueueItemReviewThread.ACTION_REQUEST_CHANGES,
        blocking=True,
        status__in=OPEN_REVIEW_THREAD_STATUSES,
        target_annotator__isnull=True,
        deleted=False,
    )
    targeted_legacy_to_user = QueueItemReviewComment.objects.filter(
        queue_item_id=OuterRef("pk"),
        thread__isnull=True,
        action=QueueItemReviewComment.ACTION_REQUEST_CHANGES,
        target_annotator_id=user.id,
        deleted=False,
    )
    global_legacy_rework = QueueItemReviewComment.objects.filter(
        queue_item_id=OuterRef("pk"),
        thread__isnull=True,
        action=QueueItemReviewComment.ACTION_REQUEST_CHANGES,
        target_annotator__isnull=True,
        deleted=False,
    )
    requester_scores = Score.objects.filter(
        queue_item_id=OuterRef("pk"),
        annotator_id=user.id,
        deleted=False,
    )
    item_scores = Score.objects.filter(
        queue_item_id=OuterRef("pk"),
        deleted=False,
    )
    return (
        queryset.annotate(
            _pending_rework_thread_to_requester=Exists(targeted_threads_to_user),
            _pending_global_rework_thread=Exists(global_rework_threads),
            _pending_legacy_rework_to_requester=Exists(targeted_legacy_to_user),
            _pending_global_legacy_rework=Exists(global_legacy_rework),
            _requester_has_queue_score=Exists(requester_scores),
            _item_has_queue_score=Exists(item_scores),
        )
        .exclude(
            Q(review_status=exclude_review_status)
            & (Q(_requester_has_queue_score=True) | Q(_item_has_queue_score=False))
            & Q(_pending_rework_thread_to_requester=False)
            & Q(_pending_global_rework_thread=False)
            & Q(_pending_legacy_rework_to_requester=False)
            & Q(_pending_global_legacy_rework=False)
        )
    )


def _targeted_rework_denies_user(item, user, *, allow_reviewer_override=False):
    if not user:
        return False
    if allow_reviewer_override and _has_queue_role(
        item.queue_id,
        user,
        AnnotatorRole.REVIEWER.value,
        AnnotatorRole.MANAGER.value,
    ):
        return False

    global_threads = _open_blocking_rework_threads(item).filter(
        target_annotator__isnull=True
    )
    if global_threads.exists():
        return False

    targeted_threads = _open_blocking_rework_threads(item).filter(
        target_annotator__isnull=False
    )
    if targeted_threads.exists():
        return not targeted_threads.filter(target_annotator=user).exists()

    global_rework = QueueItemReviewComment.objects.filter(
        queue_item=item,
        thread__isnull=True,
        action=QueueItemReviewComment.ACTION_REQUEST_CHANGES,
        target_annotator__isnull=True,
        deleted=False,
    )
    if global_rework.exists():
        return False

    targeted_rework = QueueItemReviewComment.objects.filter(
        queue_item=item,
        thread__isnull=True,
        action=QueueItemReviewComment.ACTION_REQUEST_CHANGES,
        target_annotator__isnull=False,
        deleted=False,
    )
    return (
        targeted_rework.exists()
        and not targeted_rework.filter(target_annotator=user).exists()
    )


def _review_thread_scope(label=None, target_annotator=None):
    if label and target_annotator:
        return QueueItemReviewThread.SCOPE_SCORE
    if label:
        return QueueItemReviewThread.SCOPE_LABEL
    return QueueItemReviewThread.SCOPE_ITEM


def _create_review_thread_comment(
    *,
    item,
    reviewer,
    action,
    comment,
    organization,
    workspace,
    label=None,
    target_annotator=None,
    mentioned_users=None,
    blocking=False,
    status=None,
):
    thread = QueueItemReviewThread.objects.create(
        queue_item=item,
        created_by=reviewer,
        label=label,
        target_annotator=target_annotator,
        action=action,
        scope=_review_thread_scope(label, target_annotator),
        blocking=blocking,
        status=status or QueueItemReviewThread.STATUS_OPEN,
        organization=organization,
        workspace=workspace,
    )
    review_comment = QueueItemReviewComment.objects.create(
        thread=thread,
        queue_item=item,
        reviewer=reviewer,
        label=label,
        target_annotator=target_annotator,
        action=action,
        comment=comment,
        organization=organization,
        workspace=workspace,
    )
    if mentioned_users:
        review_comment.mentioned_users.set(mentioned_users)
    return review_comment


def _visible_review_threads(item, user, *, is_reviewer):
    threads = (
        item.review_threads.filter(deleted=False)
        .select_related("created_by", "label", "target_annotator")
        .prefetch_related(
            Prefetch(
                "comments",
                queryset=QueueItemReviewComment.objects.filter(deleted=False)
                .select_related("reviewer", "label", "target_annotator", "thread")
                .prefetch_related("mentioned_users")
                .order_by("created_at"),
            )
        )
        .order_by("created_at")
    )
    if not is_reviewer:
        threads = threads.filter(
            Q(target_annotator__isnull=True) | Q(target_annotator=user)
        )
    return threads


def _visible_review_comments(item, user, *, is_reviewer):
    comments = (
        item.review_comments.filter(
            deleted=False,
            action__in=VISIBLE_REVIEW_COMMENT_ACTIONS,
        )
        .select_related("thread", "reviewer", "label", "target_annotator")
        .prefetch_related("mentioned_users")
        .order_by("created_at")
    )
    if not is_reviewer:
        comments = comments.filter(
            Q(target_annotator__isnull=True) | Q(target_annotator=user)
        )
    return comments


def _can_manage_discussion_thread(thread, user, *, is_reviewer):
    return is_reviewer or str(thread.created_by_id or "") == str(user.id)


def _open_blocking_review_threads(item):
    return QueueItemReviewThread.objects.filter(
        queue_item=item,
        blocking=True,
        status__in=OPEN_REVIEW_THREAD_STATUSES,
        deleted=False,
    )


def _discussion_payload(item, request, *, is_reviewer, comment=None, thread=None):
    comments = _visible_review_comments(
        item,
        request.user,
        is_reviewer=is_reviewer,
    ).filter(action=QueueItemReviewComment.ACTION_COMMENT)
    threads = _visible_review_threads(item, request.user, is_reviewer=is_reviewer)
    context = {"request": request}
    payload = {
        "review_comments": QueueItemReviewCommentSerializer(
            comments,
            many=True,
            context=context,
        ).data,
        "review_threads": QueueItemReviewThreadSerializer(
            threads,
            many=True,
            context=context,
        ).data,
    }
    if comment is not None:
        payload["comment"] = QueueItemReviewCommentSerializer(
            comment,
            context=context,
        ).data
    if thread is not None:
        payload["thread"] = QueueItemReviewThreadSerializer(
            thread,
            context=context,
        ).data
    return payload


def _queue_member_user_ids(item):
    return set(
        AnnotationQueueAnnotator.objects.filter(
            queue=item.queue,
            deleted=False,
            user__is_active=True,
        ).values_list("user_id", flat=True)
    )


def _queue_member_user_ids_for_roles(item, *roles):
    return set(
        AnnotationQueueAnnotator.objects.filter(
            annotation_queue_role_q(*roles),
            queue=item.queue,
            deleted=False,
            user__is_active=True,
        ).values_list("user_id", flat=True)
    )


def _queue_reviewer_manager_user_ids(item):
    return _queue_member_user_ids_for_roles(
        item,
        AnnotatorRole.REVIEWER.value,
        AnnotatorRole.MANAGER.value,
    )


def _queue_item_annotation_owner_user_ids(item):
    """Users whose work this item-level review/comment is about."""
    recipient_ids = set(
        _scores_for_queue_item(item)
        .filter(annotator__is_active=True)
        .values_list("annotator_id", flat=True)
        .distinct()
    )
    recipient_ids.update(
        item.assigned_users.filter(is_active=True).values_list("id", flat=True)
    )
    if item.assigned_to_id and User.objects.filter(
        id=item.assigned_to_id,
        is_active=True,
    ).exists():
        recipient_ids.add(item.assigned_to_id)
    return recipient_ids


def _discussion_thread_participant_user_ids(thread, *, exclude_comment_id=None):
    """Users already participating in a discussion thread."""
    if thread is None:
        return set()

    participant_ids = set()
    if thread.created_by_id:
        participant_ids.add(thread.created_by_id)
    if thread.target_annotator_id:
        participant_ids.add(thread.target_annotator_id)

    comments = QueueItemReviewComment.objects.filter(
        thread=thread,
        deleted=False,
    ).prefetch_related("mentioned_users")
    if exclude_comment_id:
        comments = comments.exclude(id=exclude_comment_id)

    for thread_comment in comments:
        if thread_comment.reviewer_id:
            participant_ids.add(thread_comment.reviewer_id)
        if thread_comment.target_annotator_id:
            participant_ids.add(thread_comment.target_annotator_id)
        participant_ids.update(
            thread_comment.mentioned_users.values_list("id", flat=True)
        )
    return participant_ids


def _annotation_discussion_url(item):
    app_url = (getattr(settings, "APP_URL", "") or "").rstrip("/")
    path = f"/dashboard/annotations/queues/{item.queue_id}/annotate?itemId={item.id}"
    if app_url and not app_url.startswith(("http://", "https://")):
        app_url = f"{getattr(settings, 'ssl', 'https://')}{app_url}"
    return f"{app_url}{path}" if app_url else path


def _source_label(item):
    source_type = (item.source_type or "item").replace("_", " ")
    return f"{source_type} {str(item.id)[:8]}"


def _notification_event_label(comment):
    if comment.action == QueueItemReviewComment.ACTION_RESOLVE:
        return "resolved a discussion thread"
    if comment.action == QueueItemReviewComment.ACTION_REOPEN:
        return "reopened a discussion thread"
    return "added a comment"


def _discussion_notification_recipient_ids(item, comment, thread):
    """Return users who should receive email for a discussion update.

    Email is intentionally narrower than realtime/in-app updates: notify
    explicit mentions, targeted annotators, and people already participating in
    the thread. For brand-new unscoped item comments, notify reviewers/managers
    rather than every queue member.
    """
    mentioned_user_ids = set(comment.mentioned_users.values_list("id", flat=True))
    recipient_ids = set(mentioned_user_ids)
    actor_id = comment.reviewer_id
    if comment.target_annotator_id:
        recipient_ids.add(comment.target_annotator_id)

    if comment.action == QueueItemReviewComment.ACTION_RESOLVE:
        if actor_id:
            recipient_ids.discard(actor_id)
        return recipient_ids

    is_root_comment = (
        thread
        and thread.action == QueueItemReviewThread.ACTION_COMMENT
        and thread.comments.filter(deleted=False).count() <= 1
    )

    if thread and not is_root_comment:
        recipient_ids.update(
            _discussion_thread_participant_user_ids(
                thread,
                exclude_comment_id=comment.id,
            )
        )
    elif comment.action in (
        QueueItemReviewComment.ACTION_APPROVE,
        QueueItemReviewComment.ACTION_REQUEST_CHANGES,
    ):
        if not comment.target_annotator_id:
            recipient_ids.update(_queue_item_annotation_owner_user_ids(item))
    else:
        has_explicit_other_recipient = mentioned_user_ids - {actor_id}
        if comment.target_annotator_id and comment.target_annotator_id != actor_id:
            has_explicit_other_recipient.add(comment.target_annotator_id)
        if not has_explicit_other_recipient:
            recipient_ids.update(_queue_reviewer_manager_user_ids(item))

    if actor_id:
        recipient_ids.discard(actor_id)
    return recipient_ids


def _send_annotation_discussion_email(*, item, comment, thread, recipient_emails):
    if not recipient_emails:
        return
    actor_name = comment.reviewer.name if comment.reviewer else "Someone"
    queue_name = item.queue.name if item.queue_id else "an annotation queue"
    subject = f"{actor_name} {_notification_event_label(comment)} in {queue_name}"
    try:
        email_helper(
            subject,
            "annotation_discussion_notification.html",
            {
                "actor_name": actor_name,
                "event_label": _notification_event_label(comment),
                "queue_name": queue_name,
                "item_label": _source_label(item),
                "label_name": comment.label.name if comment.label_id else None,
                "target_annotator_name": (
                    comment.target_annotator.name
                    if comment.target_annotator_id
                    else None
                ),
                "comment": comment.comment,
                "thread_status": thread.status if thread else None,
                "item_url": _annotation_discussion_url(item),
            },
            recipient_emails,
        )
        logger.info(
            "annotation_discussion_email_sent",
            queue_id=str(item.queue_id),
            item_id=str(item.id),
            comment_id=str(comment.id),
            recipient_count=len(recipient_emails),
        )
    except Exception:
        logger.exception(
            "annotation_discussion_email_failed",
            queue_id=str(item.queue_id),
            item_id=str(item.id),
            comment_id=str(comment.id),
        )


def _discussion_notifications_are_async():
    return getattr(
        settings,
        "ANNOTATION_DISCUSSION_NOTIFICATIONS_ASYNC",
        settings.EMAIL_BACKEND != "django.core.mail.backends.locmem.EmailBackend",
    )


def _run_annotation_discussion_notification_after_commit(
    callback, *, name, force_async=False
):
    def _send():
        should_run_async = _discussion_notifications_are_async()
        if force_async and not should_run_async:
            if getattr(settings, "TESTING", False):
                return
            should_run_async = True

        if not should_run_async:
            callback()
            return

        threading.Thread(
            target=callback,
            name=name,
            daemon=True,
        ).start()

    transaction.on_commit(_send)


def _broadcast_annotation_discussion_update(item, comment, thread=None):
    if not item.organization_id:
        return

    payload = {
        "type": "annotation_discussion_updated",
        "data": {
            "queue_id": str(item.queue_id),
            "item_id": str(item.id),
            "comment_id": str(comment.id),
            "thread_id": str(thread.id) if thread else None,
            "action": comment.action,
            "thread_status": thread.status if thread else None,
            "created_at": comment.created_at.isoformat()
            if comment.created_at
            else None,
        },
    }

    def _send():
        try:
            send_message_to_channel(item.organization_id, payload)
        except Exception:
            logger.exception(
                "annotation_discussion_websocket_broadcast_failed",
                queue_id=str(item.queue_id),
                item_id=str(item.id),
                comment_id=str(comment.id),
            )

    _run_annotation_discussion_notification_after_commit(
        _send,
        name=f"annotation-discussion-broadcast-{comment.id}",
        force_async=True,
    )


def _notify_annotation_discussion(item, comment, thread=None):
    thread = thread or comment.thread
    _broadcast_annotation_discussion_update(item, comment, thread)
    recipient_ids = _discussion_notification_recipient_ids(item, comment, thread)
    if not recipient_ids:
        return
    recipient_emails = list(
        User.objects.filter(id__in=recipient_ids, is_active=True)
        .exclude(email__isnull=True)
        .exclude(email="")
        .values_list("email", flat=True)
        .distinct()
    )
    if not recipient_emails:
        return

    logger.info(
        "annotation_discussion_email_queued",
        queue_id=str(item.queue_id),
        item_id=str(item.id),
        comment_id=str(comment.id),
        recipient_count=len(recipient_emails),
        async_delivery=_discussion_notifications_are_async(),
    )
    _run_annotation_discussion_notification_after_commit(
        lambda: _send_annotation_discussion_email(
            item=item,
            comment=comment,
            thread=thread,
            recipient_emails=recipient_emails,
        ),
        name=f"annotation-discussion-email-{comment.id}",
    )


def _mark_review_threads_addressed(item, user, organization, workspace):
    """Mark visible blocking feedback as addressed when the annotator resubmits."""
    threads = list(
        _open_blocking_review_threads(item)
        .filter(Q(target_annotator__isnull=True) | Q(target_annotator=user))
        .select_related("label", "target_annotator")
    )
    now = timezone.now()
    for thread in threads:
        thread.status = QueueItemReviewThread.STATUS_ADDRESSED
        thread.addressed_by = user
        thread.addressed_at = now
        thread.save(
            update_fields=[
                "status",
                "addressed_by",
                "addressed_at",
                "updated_at",
            ]
        )
        QueueItemReviewComment.objects.create(
            thread=thread,
            queue_item=item,
            reviewer=user,
            label=thread.label,
            target_annotator=thread.target_annotator,
            action=QueueItemReviewComment.ACTION_ADDRESSED,
            comment="Resubmitted for review.",
            organization=organization,
            workspace=workspace,
        )
    return len(threads)


def _scores_for_queue_item(item):
    """Return the scores attributed to this queue item.

    Strictly per-queue: with the new ``(source, label, annotator, queue_item)``
    uniqueness, every Score belongs to exactly one queue's review
    context. Cross-queue scores must never leak into this queue's history
    or completion gating.

    Orphan scores (``queue_item IS NULL``) are not surfaced here — they
    are handled by the on_commit auto-attach hook in
    ``model_hub.views.scores._auto_create_queue_items_for_default_queues``,
    which migrates orphans onto a default-queue item asynchronously. If
    an orphan score actually belongs to *this* queue's item, the hook
    will eventually attach it and a refetch will pick it up.
    """
    label_ids = list(
        item.queue.queue_labels.filter(deleted=False).values_list("label_id", flat=True)
    )
    if not label_ids:
        return Score.objects.none()

    return Score.objects.filter(
        queue_item=item, label_id__in=label_ids, deleted=False
    )


def _required_label_ids_for_queue(queue):
    return list(
        queue.queue_labels.filter(deleted=False, required=True).values_list(
            "label_id", flat=True
        )
    )


def _item_has_required_label_coverage(item, required_label_ids=None):
    """Return whether every required queue label has enough annotator scores."""
    if required_label_ids is None:
        required_label_ids = _required_label_ids_for_queue(item.queue)
    if not required_label_ids:
        return True

    required_count = max(int(item.queue.annotations_required or 1), 1)
    counts = {
        row["label_id"]: row["annotator_count"]
        for row in (
            _scores_for_queue_item(item)
            .filter(label_id__in=required_label_ids)
            .values("label_id")
            .annotate(annotator_count=Count("annotator", distinct=True))
        )
    }
    return all(
        counts.get(label_id, 0) >= required_count for label_id in required_label_ids
    )


QUEUE_ITEM_WORK_ORDERING = ("-created_at", "-id")
QUEUE_ITEM_REVERSE_WORK_ORDERING = ("created_at", "id")


def _queue_items_after_work_cursor(queryset, current_item):
    """Items after the current one in the default newest-first work order."""
    return (
        queryset.filter(
            Q(created_at__lt=current_item.created_at)
            | Q(created_at=current_item.created_at, id__lt=current_item.id)
        )
        .exclude(pk=current_item.pk)
        .order_by(*QUEUE_ITEM_WORK_ORDERING)
    )


def _queue_items_before_work_cursor(queryset, current_item):
    """Items before the current one in the default newest-first work order."""
    return (
        queryset.filter(
            Q(created_at__gt=current_item.created_at)
            | Q(created_at=current_item.created_at, id__gt=current_item.id)
        )
        .exclude(pk=current_item.pk)
        .order_by(*QUEUE_ITEM_REVERSE_WORK_ORDERING)
    )


def _reopen_items_missing_required_labels(queue):
    """Move completed items back to work when queue labels become incomplete."""
    required_label_ids = _required_label_ids_for_queue(queue)
    if not required_label_ids:
        return 0
    required_label_set = set(required_label_ids)
    required_count = max(int(queue.annotations_required or 1), 1)

    completed_items = list(
        QueueItem.objects.filter(
            queue=queue,
            status=QueueItemStatus.COMPLETED.value,
            deleted=False,
        ).select_related("queue")
    )
    scores_by_item = _scores_for_queue_items(completed_items, required_label_ids)
    reopen_ids = []
    for item in completed_items:
        annotators_by_label = {label_id: set() for label_id in required_label_ids}
        for score in scores_by_item.get(item.id, []):
            if score.label_id in required_label_set and score.annotator_id:
                annotators_by_label.setdefault(score.label_id, set()).add(
                    score.annotator_id
                )
        if any(
            len(annotator_ids) < required_count
            for annotator_ids in annotators_by_label.values()
        ):
            reopen_ids.append(item.id)
    if not reopen_ids:
        return 0

    return QueueItem.objects.filter(id__in=reopen_ids).update(
        status=QueueItemStatus.IN_PROGRESS.value,
        review_status=None,
        reviewed_by=None,
        reviewed_at=None,
        updated_at=timezone.now(),
    )


def _span_notes_target_for_queue_item(item):
    """Return the span that stores whole-item notes for queue annotation."""
    if item.source_type == "observation_span" and item.observation_span_id:
        return item.observation_span
    if item.source_type != "trace" or not item.trace_id:
        return None

    root_spans = ObservationSpan.objects.filter(
        trace_id=item.trace_id,
        deleted=False,
    ).filter(Q(parent_span_id__isnull=True) | Q(parent_span_id=""))
    return (
        root_spans.filter(observation_type="conversation")
        .order_by("start_time", "created_at")
        .first()
        or root_spans.order_by("start_time", "created_at").first()
    )


def _serialize_queue_item_note(note):
    return {
        "id": str(note.id),
        "notes": note.notes,
        "annotator": note.annotator.email
        if note.annotator_id and note.annotator
        else None,
        "annotator_id": str(note.annotator_id) if note.annotator_id else None,
        "created_at": note.created_at.isoformat(),
    }


def _serialize_span_note(note):
    return {
        "id": str(note.id),
        "notes": note.notes,
        "annotator": note.created_by_annotator
        or (note.created_by_user.name if note.created_by_user_id else None),
        "annotator_id": str(note.created_by_user_id)
        if note.created_by_user_id
        else None,
        "created_at": note.created_at.isoformat(),
    }


def _item_note_rows(item):
    return list(
        QueueItemNote.no_workspace_objects.filter(queue_item=item, deleted=False)
        .select_related("annotator")
        .order_by("-updated_at", "-created_at")
    )


def _span_note_rows(span):
    if span is None:
        return []
    return list(
        SpanNotes.objects.filter(span=span)
        .select_related("created_by_user")
        .order_by("-created_at")
    )


def _item_note_payloads(item, span_notes_target=None, viewer_user_id=None):
    """Return whole-item notes from queue storage plus legacy span notes.

    When ``viewer_user_id`` is provided, the requester's own SpanNote is
    filtered out — they shouldn't see "their" note in a read-only list
    when the editable field above it is per-queue and empty (the
    SpanNote was written via another queue or the legacy inline endpoint
    and is not associated with this queue). Other annotators' SpanNotes
    remain visible as cross-queue historical context.
    """
    queue_notes = _item_note_rows(item)
    seen_user_ids = {note.annotator_id for note in queue_notes if note.annotator_id}
    payloads = [_serialize_queue_item_note(note) for note in queue_notes]
    for span_note in _span_note_rows(span_notes_target):
        if span_note.created_by_user_id in seen_user_ids:
            continue
        if viewer_user_id and span_note.created_by_user_id == viewer_user_id:
            continue
        payloads.append(_serialize_span_note(span_note))
    return payloads


def _existing_item_note_for_user(item, user_id):
    """Return this user's whole-item note for this queue's item.

    Strictly queue-scoped via ``QueueItemNote``. Pre-revamp this used to
    fall back to a span-level ``SpanNote``, which leaked notes written
    via Queue A (or the legacy inline endpoint) into every other queue
    containing the same span. SpanNotes now surface only as read-only
    context via ``_item_note_payloads``.
    """
    if not user_id:
        return ""
    queue_note = (
        QueueItemNote.no_workspace_objects.filter(
            queue_item=item,
            annotator_id=user_id,
            deleted=False,
        )
        .order_by("-updated_at", "-created_at")
        .first()
    )
    return queue_note.notes if queue_note else ""


def _source_id_for_queue_item(item):
    fk_field = SOURCE_TYPE_FK_MAP.get(item.source_type)
    return str(getattr(item, f"{fk_field}_id", "") or "") if fk_field else ""


def _to_cell_str(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return json.dumps(value, default=str)


def _extract_input_output(content):
    source_type = content.get("type", "")
    if source_type == "dataset_row":
        fields = content.get("fields", {})
        return _to_cell_str(fields.get("input", "")), _to_cell_str(
            fields.get("output", "")
        )
    if source_type == "prototype_run":
        return _to_cell_str(content.get("prompt")), _to_cell_str(
            content.get("response")
        )
    return _to_cell_str(content.get("input")), _to_cell_str(content.get("output"))


def _first_existing(content, *keys):
    for key in keys:
        if content.get(key) is not None:
            return content.get(key)
    return None


def _latest_item_note(item):
    """Latest whole-item note for this queue's item — queue-scoped only.

    Pre-revamp this fell back to the most recent ``SpanNote`` on the
    item's source span, leaking notes from other queues. Now strictly
    reads ``QueueItemNote`` attached to this ``queue_item``.
    """
    queue_note = (
        QueueItemNote.no_workspace_objects.filter(queue_item=item, deleted=False)
        .order_by("-updated_at", "-created_at")
        .first()
    )
    return queue_note.notes if queue_note else ""


def _span_note_targets_for_queue_items(items):
    """Resolve whole-item note spans in bulk for export."""
    targets = {}
    trace_ids = []
    for item in items:
        if item.source_type == "observation_span" and item.observation_span_id:
            targets[item.id] = item.observation_span
        elif item.source_type == "trace" and item.trace_id:
            trace_ids.append(item.trace_id)

    if trace_ids:
        spans = (
            ObservationSpan.objects.filter(
                trace_id__in=trace_ids,
                deleted=False,
            )
            .filter(Q(parent_span_id__isnull=True) | Q(parent_span_id=""))
            .order_by("trace_id", "start_time", "created_at")
        )
        first_root_by_trace = {}
        first_conversation_by_trace = {}
        for span in spans:
            first_root_by_trace.setdefault(span.trace_id, span)
            if span.observation_type == "conversation":
                first_conversation_by_trace.setdefault(span.trace_id, span)
        for item in items:
            if item.source_type == "trace" and item.trace_id:
                target = first_conversation_by_trace.get(
                    item.trace_id
                ) or first_root_by_trace.get(item.trace_id)
                if target is not None:
                    targets[item.id] = target

    return targets


def _latest_item_notes_for_queue_items(items):
    """Bulk version of ``_latest_item_note`` — queue-scoped only.

    Pre-revamp this filled missing entries from span-level ``SpanNotes``
    on the item's root span, which leaked one queue's note into every
    other queue containing the same source on export. Now strictly
    reads ``QueueItemNote`` attached to each ``queue_item``.
    """
    item_ids = [item.id for item in items]
    notes_by_item = {}
    for note in (
        QueueItemNote.no_workspace_objects.filter(
            queue_item_id__in=item_ids,
            deleted=False,
        )
        .order_by("queue_item_id", "-updated_at", "-created_at")
        .values("queue_item_id", "notes")
    ):
        notes_by_item.setdefault(note["queue_item_id"], note["notes"])
    return notes_by_item


def _normalize_export_status_filter(status_filter):
    if status_filter is None:
        return None
    normalized = str(status_filter).strip()
    if not normalized or normalized.lower() == "all":
        return None
    return normalized


def _scores_for_queue_items(items, queue_label_ids):
    """Bulk variant of ``_scores_for_queue_item`` — same per-queue scoping.

    Strictly returns scores attributed to one of *items* via ``queue_item``.
    No cross-queue, no orphan, no queue-member fallback — those would leak
    a sibling queue's value into this queue's export/dataset payload, which
    contradicts the per-queue Score uniqueness contract.
    """
    if not items or not queue_label_ids:
        return {}

    item_ids = [item.id for item in items]
    scores_by_item = {item_id: [] for item_id in item_ids}
    scores = (
        Score.objects.filter(
            queue_item_id__in=item_ids,
            label_id__in=queue_label_ids,
            deleted=False,
        )
        .select_related("label", "annotator")
        .order_by("created_at")
    )
    for score in scores:
        if score.queue_item_id in scores_by_item:
            scores_by_item[score.queue_item_id].append(score)

    for item_scores in scores_by_item.values():
        item_scores.sort(
            key=lambda score: (
                score.created_at.isoformat() if score.created_at else "",
                str(score.id),
            )
        )

    return scores_by_item


def _infer_dataset_type(value):
    if isinstance(value, bool):
        return DataTypeChoices.BOOLEAN.value
    if isinstance(value, int) and not isinstance(value, bool):
        return DataTypeChoices.INTEGER.value
    if isinstance(value, float):
        return DataTypeChoices.FLOAT.value
    if isinstance(value, (date, datetime)):
        return DataTypeChoices.DATETIME.value
    if isinstance(value, list):
        return DataTypeChoices.ARRAY.value
    if isinstance(value, dict):
        return DataTypeChoices.JSON.value
    return DataTypeChoices.TEXT.value


def _source_export_label(source_type):
    return SOURCE_TYPE_EXPORT_LABELS.get(
        source_type, str(source_type).replace("_", " ")
    )


def _parse_attribute_field_id(field_id):
    if not field_id.startswith("attr:"):
        return None, ""

    path = field_id.removeprefix("attr:").strip()
    known_source_types = {choice.value for choice in QueueItemSourceType}
    source_type, separator, scoped_path = path.partition(":")
    if separator and source_type in known_source_types:
        return source_type, scoped_path.strip()
    return None, path


def _flatten_export_attributes(value, prefix="", depth=0):
    if value is None or depth > 3:
        return []
    if not isinstance(value, dict):
        return [(prefix, value)] if prefix else []

    flattened = []
    for key, child in value.items():
        key_str = str(key)
        path = f"{prefix}.{key_str}" if prefix else key_str
        if isinstance(child, dict) and depth < 3:
            flattened.extend(_flatten_export_attributes(child, path, depth + 1))
        else:
            flattened.append((path, child))
    return flattened


def _nested_get(value, dotted_path):
    current = value
    for part in dotted_path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
    return current


def _single_or_list(values):
    present = [value for value in values if value is not None and value != ""]
    if not present:
        return ""
    if len(present) == 1:
        return present[0]
    return present


def _score_created_at(score):
    created_at = getattr(score, "created_at", None)
    return created_at.isoformat() if created_at else None


def _score_updated_at(score):
    updated_at = getattr(score, "updated_at", None)
    return updated_at.isoformat() if updated_at else None


def _score_annotator(score):
    return getattr(score, "annotator", None) if score.annotator_id else None


def _score_annotator_name(score):
    annotator = _score_annotator(score)
    return annotator.name if annotator else None


def _score_annotator_email(score):
    annotator = _score_annotator(score)
    return annotator.email if annotator else None


def _serialize_score_for_export(score):
    annotator = _score_annotator(score)
    return {
        "label_id": str(score.label_id),
        "label_name": score.label.name if score.label else None,
        "value": score.value,
        "notes": score.notes,
        "annotator_id": str(score.annotator_id) if score.annotator_id else None,
        "annotator_name": annotator.name if annotator else None,
        "annotator_email": annotator.email if annotator else None,
        "score_source": score.score_source,
        "created_at": _score_created_at(score),
        "updated_at": _score_updated_at(score),
    }


def _serialize_item_review(item):
    reviewer = getattr(item, "reviewed_by", None)
    return {
        "requires_review": bool(getattr(item.queue, "requires_review", False)),
        "status": item.review_status,
        "notes": item.review_notes,
        "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
        "reviewed_by_id": str(item.reviewed_by_id) if item.reviewed_by_id else None,
        "reviewed_by_name": reviewer.name if reviewer else None,
        "reviewed_by_email": reviewer.email if reviewer else None,
    }


def _label_scores(scores, label_id):
    return [score for score in scores if str(score.label_id) == str(label_id)]


def _label_export_value(scores, label_id, kind):
    label_scores = _label_scores(scores, label_id)
    if kind == "annotation":
        return [_serialize_score_for_export(score) for score in label_scores]

    value_getters = {
        "value": lambda score: score.value,
        "notes": lambda score: score.notes,
        "annotator_id": lambda score: (
            str(score.annotator_id) if score.annotator_id else None
        ),
        "annotator_name": _score_annotator_name,
        "annotator_email": _score_annotator_email,
        "score_source": lambda score: score.score_source,
        "created_at": _score_created_at,
        "updated_at": _score_updated_at,
    }
    getter = value_getters.get(kind)
    if getter is None:
        return ""
    return _single_or_list([getter(score) for score in label_scores])


def _label_slot_export_value(scores, label_id, slot, kind):
    label_scores = _label_scores(scores, label_id)
    index = max(int(slot or 1) - 1, 0)
    if index >= len(label_scores):
        return ""

    score = label_scores[index]
    if kind == "annotation":
        return _serialize_score_for_export(score)
    return _label_export_value([score], label_id, kind)


def _annotation_metrics_for_scores(scores):
    metrics = {}
    for score in scores:
        if not score.label:
            continue
        metrics.setdefault(score.label.name, []).append(
            _serialize_score_for_export(score)
        )
    return {
        label_name: entries[0] if len(entries) == 1 else entries
        for label_name, entries in metrics.items()
    }


def _eval_output_value(log):
    if log.output_float is not None:
        return log.output_float
    if log.output_bool is not None:
        return log.output_bool
    if log.output_str not in (None, ""):
        return log.output_str
    return log.output_str_list


def _eval_metric_key(log):
    if log.custom_eval_config_id and getattr(log.custom_eval_config, "name", None):
        return log.custom_eval_config.name
    return log.eval_type_id or log.eval_id or str(log.id)


def _serialize_eval_log(log):
    return {
        "score": _eval_output_value(log),
        "explanation": log.results_explanation or log.eval_explanation,
        "tags": log.results_tags or log.eval_tags,
        "error": log.error,
        "error_message": log.error_message if log.error else None,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


def _eval_metrics_for_queue_items(items):
    from collections import defaultdict

    if not items:
        return {}

    span_item_ids = defaultdict(list)
    trace_item_ids = defaultdict(list)
    for item in items:
        if item.source_type == "observation_span" and item.observation_span_id:
            span_item_ids[str(item.observation_span_id)].append(item.id)
        elif item.source_type == "trace" and item.trace_id:
            trace_item_ids[str(item.trace_id)].append(item.id)

    eval_filter = Q()
    if span_item_ids:
        eval_filter |= Q(observation_span_id__in=list(span_item_ids))
    if trace_item_ids:
        eval_filter |= Q(trace_id__in=list(trace_item_ids))
    if not eval_filter:
        return {}

    metrics_by_item = {item.id: {} for item in items}
    seen_by_item = {item.id: set() for item in items}
    eval_logs = (
        EvalLogger.objects.filter(eval_filter, deleted=False)
        .select_related("custom_eval_config")
        .order_by("created_at")
    )
    for log in eval_logs:
        matched_item_ids = []
        if log.observation_span_id:
            matched_item_ids.extend(span_item_ids.get(str(log.observation_span_id), []))
        if log.trace_id:
            matched_item_ids.extend(trace_item_ids.get(str(log.trace_id), []))

        for item_id in matched_item_ids:
            if log.id in seen_by_item[item_id]:
                continue
            seen_by_item[item_id].add(log.id)
            key = _eval_metric_key(log)
            metrics_by_item[item_id].setdefault(key, []).append(
                _serialize_eval_log(log)
            )

    return metrics_by_item


def _eval_metrics_export_value(metrics_by_key):
    return {
        key: entries[0] if len(entries) == 1 else entries
        for key, entries in metrics_by_key.items()
    }


def _eval_export_value(metrics_by_key, eval_key, kind):
    entries = metrics_by_key.get(eval_key, [])
    return _single_or_list([entry.get(kind) for entry in entries])


LABEL_TYPE_TO_DATA_TYPE = {
    AnnotationTypeChoices.NUMERIC.value: DataTypeChoices.FLOAT.value,
    AnnotationTypeChoices.TEXT.value: DataTypeChoices.TEXT.value,
    AnnotationTypeChoices.CATEGORICAL.value: DataTypeChoices.ARRAY.value,
    AnnotationTypeChoices.STAR.value: DataTypeChoices.FLOAT.value,
    AnnotationTypeChoices.THUMBS_UP_DOWN.value: DataTypeChoices.TEXT.value,
}


def _unique_export_column_name(name, used):
    base = (name or "column").strip() or "column"
    candidate = base
    suffix = 2
    while candidate.lower() in used:
        candidate = f"{base} {suffix}"
        suffix += 1
    used.add(candidate.lower())
    return candidate


def _export_field(
    field_id,
    label,
    column,
    data_type=DataTypeChoices.TEXT.value,
    group="Source",
    default=False,
    *,
    path=None,
    kind=None,
    label_id=None,
    eval_key=None,
    slot=None,
    source_type=None,
    expand_fields=None,
):
    field = {
        "id": field_id,
        "label": label,
        "column": column,
        "data_type": data_type,
        "group": group,
        "default": default,
    }
    if path:
        field["path"] = path
    if kind:
        field["kind"] = kind
    if label_id:
        field["label_id"] = str(label_id)
    if eval_key:
        field["eval_key"] = eval_key
    if slot:
        field["slot"] = int(slot)
    if source_type:
        field["source_type"] = source_type
    if expand_fields:
        field["expand_fields"] = expand_fields
    return field


def _base_export_field_defs():
    return [
        _export_field("source_type", "Source type", "source_type", default=True),
        _export_field("source_id", "Source ID", "source_id", default=True),
        _export_field(
            "source_name", "Source name", "source_name", default=True, path="name"
        ),
        _export_field(
            "source_status",
            "Source status",
            "source_status",
            default=True,
            path="status",
        ),
        _export_field(
            "project_id", "Project ID", "project_id", default=True, path="project_id"
        ),
        _export_field(
            "item_status",
            "Queue item status",
            "item_status",
            group="Queue item",
            default=True,
        ),
        _export_field(
            "item_order",
            "Queue item order",
            "item_order",
            DataTypeChoices.INTEGER.value,
            "Queue item",
            True,
        ),
        _export_field(
            "queue_requires_review",
            "Requires review",
            "requires_review",
            DataTypeChoices.BOOLEAN.value,
            "Review",
            True,
        ),
        _export_field(
            "review_status",
            "Review status",
            "review_status",
            group="Review",
            default=True,
        ),
        _export_field(
            "reviewed_by_name",
            "Reviewer name",
            "reviewer_name",
            group="Review",
            default=True,
        ),
        _export_field(
            "reviewed_by_email",
            "Reviewer email",
            "reviewer_email",
            group="Review",
            default=True,
        ),
        _export_field(
            "reviewed_by_id",
            "Reviewer ID",
            "reviewer_id",
            group="Review",
            default=True,
        ),
        _export_field(
            "reviewed_at",
            "Reviewed at",
            "reviewed_at",
            DataTypeChoices.DATETIME.value,
            "Review",
            True,
        ),
        _export_field(
            "review_notes",
            "Review notes",
            "review_notes",
            group="Review",
            default=True,
        ),
        _export_field("input", "Input", "input", default=True),
        _export_field("output", "Output", "output", default=True),
        _export_field(
            "latency_ms",
            "Latency (ms)",
            "latency_ms",
            DataTypeChoices.FLOAT.value,
            "Metrics",
            True,
        ),
        _export_field(
            "response_time_ms",
            "Response time (ms)",
            "response_time_ms",
            DataTypeChoices.FLOAT.value,
            "Metrics",
            True,
        ),
        _export_field(
            "duration_seconds",
            "Duration (seconds)",
            "duration_seconds",
            DataTypeChoices.FLOAT.value,
            "Metrics",
            True,
        ),
        _export_field(
            "eval_metrics",
            "Eval metrics",
            "eval_metrics",
            DataTypeChoices.JSON.value,
            "Evals",
            True,
            kind="eval_metrics",
        ),
        _export_field(
            "annotation_metrics",
            "Annotation metrics",
            "annotation_metrics",
            DataTypeChoices.JSON.value,
            "Annotations",
            True,
            kind="annotation_metrics",
        ),
        _export_field(
            "item_notes",
            "Item notes",
            "item_notes",
            group="Annotations",
            default=True,
        ),
        _export_field("trace_id", "Trace ID", "trace_id", path="trace_id"),
        _export_field("span_id", "Span ID", "span_id", path="span_id"),
        _export_field("call_id", "Call ID", "call_id", path="call_id"),
        _export_field("session_id", "Session ID", "session_id", path="session_id"),
        _export_field(
            "project_source", "Project source", "project_source", path="project_source"
        ),
        _export_field(
            "observation_type", "Span type", "observation_type", path="observation_type"
        ),
        _export_field("model", "Model", "model", path="model"),
        _export_field("provider", "Provider", "provider", path="provider"),
        _export_field(
            "cost", "Cost", "cost", DataTypeChoices.FLOAT.value, "Metrics", path="cost"
        ),
        _export_field(
            "prompt_tokens",
            "Prompt tokens",
            "prompt_tokens",
            DataTypeChoices.INTEGER.value,
            "Metrics",
            path="prompt_tokens",
        ),
        _export_field(
            "completion_tokens",
            "Completion tokens",
            "completion_tokens",
            DataTypeChoices.INTEGER.value,
            "Metrics",
            path="completion_tokens",
        ),
        _export_field(
            "total_tokens",
            "Total tokens",
            "total_tokens",
            DataTypeChoices.INTEGER.value,
            "Metrics",
            path="total_tokens",
        ),
        _export_field(
            "start_time",
            "Start time",
            "start_time",
            DataTypeChoices.DATETIME.value,
            "Source",
            path="start_time",
        ),
        _export_field(
            "end_time",
            "End time",
            "end_time",
            DataTypeChoices.DATETIME.value,
            "Source",
            path="end_time",
        ),
        _export_field(
            "created_at",
            "Source created at",
            "source_created_at",
            DataTypeChoices.DATETIME.value,
            "Source",
            path="created_at",
        ),
        _export_field(
            "call_type", "Call type", "call_type", group="Voice", path="call_type"
        ),
        _export_field(
            "phone_number",
            "Phone number",
            "phone_number",
            group="Voice",
            path="phone_number",
        ),
        _export_field(
            "customer_number",
            "Customer number",
            "customer_number",
            group="Voice",
            path="customer_number",
        ),
        _export_field(
            "ended_reason",
            "Ended reason",
            "ended_reason",
            group="Voice",
            path="ended_reason",
        ),
        _export_field(
            "message_count",
            "Message count",
            "message_count",
            DataTypeChoices.INTEGER.value,
            "Voice",
            path="message_count",
        ),
        _export_field(
            "user_wpm",
            "User WPM",
            "user_wpm",
            DataTypeChoices.FLOAT.value,
            "Voice",
            path="user_wpm",
        ),
        _export_field(
            "agent_wpm",
            "Agent WPM",
            "agent_wpm",
            DataTypeChoices.FLOAT.value,
            "Voice",
            path="agent_wpm",
        ),
        _export_field(
            "talk_ratio",
            "Talk ratio",
            "talk_ratio",
            DataTypeChoices.FLOAT.value,
            "Voice",
            path="talk_ratio",
        ),
        _export_field(
            "call_summary",
            "Call summary",
            "call_summary",
            group="Voice",
            path="call_summary",
        ),
    ]


def _build_annotation_queue_export_fields(queue, sample_items=None):
    fields = []
    used_columns = set()

    for field in _base_export_field_defs():
        field = dict(field)
        field["column"] = _unique_export_column_name(field["column"], used_columns)
        fields.append(field)

    if sample_items is None:
        sample_items = (
            QueueItem.objects.filter(queue=queue, deleted=False)
            .select_related(
                "trace",
                "observation_span",
                "dataset_row",
                "prototype_run",
                "call_execution",
                "trace_session",
            )
            .prefetch_related(
                Prefetch(
                    "trace__observation_spans",
                    queryset=ObservationSpan.objects.filter(deleted=False).order_by(
                        "start_time", "created_at"
                    ),
                    to_attr="_queue_export_spans",
                )
            )
            .order_by("order", "created_at")[:100]
        )
    sample_items = list(sample_items)

    labels = list(
        queue.queue_labels.filter(deleted=False)
        .select_related("label")
        .order_by("order", "created_at")
    )
    label_ids = [queue_label.label_id for queue_label in labels if queue_label.label_id]
    slot_counts_by_label = {
        str(label_id): max(int(queue.annotations_required or 1), 1)
        for label_id in label_ids
    }
    scores_by_item = _scores_for_queue_items(sample_items, label_ids)
    for item_scores in scores_by_item.values():
        for label_id in label_ids:
            label_key = str(label_id)
            slot_counts_by_label[label_key] = max(
                slot_counts_by_label.get(label_key, 1),
                len(_label_scores(item_scores, label_id)),
            )

    for queue_label in labels:
        label = queue_label.label
        if not label:
            continue
        value_column = _unique_export_column_name(f"{label.name} values", used_columns)
        fields.append(
            _export_field(
                f"label:{label.id}:value",
                f"{label.name} all scores",
                value_column,
                LABEL_TYPE_TO_DATA_TYPE.get(label.type, DataTypeChoices.TEXT.value),
                "Annotations",
                False,
                kind="value",
                label_id=label.id,
            )
        )
        label_fanout_fields = [
            ("notes", f"{label.name} notes", DataTypeChoices.TEXT.value),
            ("annotator_id", f"{label.name} annotator ID", DataTypeChoices.TEXT.value),
            ("annotator_name", f"{label.name} annotator", DataTypeChoices.TEXT.value),
            (
                "annotator_email",
                f"{label.name} annotator email",
                DataTypeChoices.TEXT.value,
            ),
            ("score_source", f"{label.name} score source", DataTypeChoices.TEXT.value),
            (
                "created_at",
                f"{label.name} annotated at",
                DataTypeChoices.DATETIME.value,
            ),
            (
                "annotation",
                f"{label.name} annotation record",
                DataTypeChoices.JSON.value,
            ),
        ]
        for kind, label_text, data_type in label_fanout_fields:
            fields.append(
                _export_field(
                    f"label:{label.id}:{kind}",
                    label_text,
                    _unique_export_column_name(label_text, used_columns),
                    data_type,
                    "Annotations",
                    False,
                    kind=kind,
                    label_id=label.id,
                )
            )
        slot_field_ids = []
        for slot in range(1, slot_counts_by_label.get(str(label.id), 1) + 1):
            for kind, slot_label, explicit_data_type in ANNOTATION_SLOT_FIELDS:
                data_type = explicit_data_type or LABEL_TYPE_TO_DATA_TYPE.get(
                    label.type, DataTypeChoices.TEXT.value
                )
                label_text = f"{label.name} annotation {slot} {slot_label}"
                field_id = f"label:{label.id}:slot:{slot}:{kind}"
                slot_field_ids.append(field_id)
                fields.append(
                    _export_field(
                        field_id,
                        label_text,
                        _unique_export_column_name(label_text, used_columns),
                        data_type,
                        "Annotations",
                        True,
                        kind=kind,
                        label_id=label.id,
                        slot=slot,
                    )
                )
        if slot_field_ids:
            fields.append(
                _export_field(
                    f"label:{label.id}:annotation_columns",
                    f"{label.name} annotation columns",
                    f"{label.name} annotation columns",
                    DataTypeChoices.JSON.value,
                    "Annotations",
                    False,
                    kind="annotation_bundle",
                    label_id=label.id,
                    expand_fields=slot_field_ids,
                )
            )

    attribute_roots = (
        "fields",
        "metadata",
        "span_attributes",
        "resource_attributes",
        "eval_attributes",
        "call_metadata",
        "provider_call_data",
        "monitor_call_data",
        "analysis_data",
        "evaluation_data",
        "customer_latency_metrics",
    )
    sample_contents = [(item, resolve_source_content(item)) for item in sample_items]
    source_types = {
        content.get("type") or item.source_type
        for item, content in sample_contents
        if content.get("type") or item.source_type
    }
    has_multiple_source_types = len(source_types) > 1
    seen_attr_ids = set()
    for item, content in sample_contents:
        source_type = content.get("type") or item.source_type
        for root in attribute_roots:
            root_value = content.get(root)
            for path, value in _flatten_export_attributes(root_value, root):
                field_id = (
                    f"attr:{source_type}:{path}"
                    if has_multiple_source_types
                    else f"attr:{path}"
                )
                if field_id in seen_attr_ids:
                    continue
                seen_attr_ids.add(field_id)
                fields.append(
                    {
                        "id": field_id,
                        "label": path.replace("_", " "),
                        "column": _unique_export_column_name(path, used_columns),
                        "data_type": _infer_dataset_type(value),
                        "group": (
                            f"From {_source_export_label(source_type)}"
                            if has_multiple_source_types
                            else "Attributes"
                        ),
                        "default": False,
                        "path": path,
                        **(
                            {"source_type": source_type}
                            if has_multiple_source_types
                            else {}
                        ),
                    }
                )

    eval_metrics_by_item = _eval_metrics_for_queue_items(sample_items)
    seen_eval_ids = set()
    for metrics_by_key in eval_metrics_by_item.values():
        for eval_key, entries in metrics_by_key.items():
            if not entries:
                continue
            first_score = next(
                (
                    entry.get("score")
                    for entry in entries
                    if entry.get("score") is not None
                ),
                None,
            )
            eval_field_defs = [
                ("score", f"{eval_key} eval score", _infer_dataset_type(first_score)),
                (
                    "explanation",
                    f"{eval_key} eval explanation",
                    DataTypeChoices.JSON.value,
                ),
                ("error", f"{eval_key} eval error", DataTypeChoices.BOOLEAN.value),
            ]
            for kind, label_text, data_type in eval_field_defs:
                field_id = f"eval:{eval_key}:{kind}"
                if field_id in seen_eval_ids:
                    continue
                seen_eval_ids.add(field_id)
                fields.append(
                    _export_field(
                        field_id,
                        label_text,
                        _unique_export_column_name(label_text, used_columns),
                        data_type,
                        "Evals",
                        False,
                        kind=kind,
                        eval_key=eval_key,
                    )
                )

    return {
        "fields": fields,
        "default_mapping": [
            {
                "field": field["id"],
                "column": field["column"],
                "enabled": True,
            }
            for field in fields
            if field.get("default")
        ],
    }


def _infer_attribute_field_data_type(field_id, sample_items):
    source_type, path = _parse_attribute_field_id(field_id)
    if not path:
        return DataTypeChoices.TEXT.value

    for item in sample_items or []:
        if source_type and item.source_type != source_type:
            continue
        value = _nested_get(resolve_source_content(item), path)
        if value not in (None, ""):
            return _infer_dataset_type(value)
    return DataTypeChoices.TEXT.value


def _custom_attribute_export_field(field_id, sample_items=None):
    if not field_id.startswith("attr:"):
        return None
    source_type, path = _parse_attribute_field_id(field_id)
    if not path:
        return None
    return _export_field(
        field_id,
        path.replace("_", " "),
        path,
        _infer_attribute_field_data_type(field_id, sample_items),
        f"From {_source_export_label(source_type)}" if source_type else "Attributes",
        False,
        path=path,
        source_type=source_type,
    )


def _export_column_source(field_def):
    if field_def["id"].startswith("label:"):
        return SourceChoices.ANNOTATION_LABEL.value
    if field_def["id"].startswith("eval:") or field_def["id"] == "eval_metrics":
        return SourceChoices.EVALUATION.value
    return SourceChoices.OTHERS.value


def _resolve_export_field_value(item, content, runtime, mapping):
    field_id = mapping["field"]
    field_def = mapping.get("field_def") or {}
    if field_id == "source_type":
        return item.source_type or ""
    if field_id == "source_id":
        return _source_id_for_queue_item(item)
    if field_id == "item_status":
        return item.status
    if field_id == "item_order":
        return item.order
    if field_id == "input":
        return runtime["input_value"]
    if field_id == "output":
        return runtime["output_value"]
    if field_id == "latency_ms":
        return _first_existing(content, "latency_ms", "latency")
    if field_id == "response_time_ms":
        return content.get("response_time_ms")
    if field_id == "duration_seconds":
        return content.get("duration_seconds")
    if field_id == "item_notes":
        return runtime["item_notes"]
    if field_id == "queue_requires_review":
        return bool(getattr(item.queue, "requires_review", False))
    if field_id == "review_status":
        return item.review_status or ""
    if field_id == "reviewed_by_name":
        reviewer = getattr(item, "reviewed_by", None)
        return reviewer.name if reviewer else ""
    if field_id == "reviewed_by_email":
        reviewer = getattr(item, "reviewed_by", None)
        return reviewer.email if reviewer else ""
    if field_id == "reviewed_by_id":
        return str(item.reviewed_by_id) if item.reviewed_by_id else ""
    if field_id == "reviewed_at":
        return item.reviewed_at
    if field_id == "review_notes":
        return item.review_notes or ""
    if field_id == "annotation_metrics":
        return _annotation_metrics_for_scores(runtime["scores"])
    if field_id == "eval_metrics":
        return _eval_metrics_export_value(runtime["eval_metrics"])
    if field_id.startswith("label:"):
        if field_def.get("slot"):
            return _label_slot_export_value(
                runtime["scores"],
                field_def.get("label_id"),
                field_def.get("slot"),
                field_def.get("kind"),
            )
        return _label_export_value(
            runtime["scores"], field_def.get("label_id"), field_def.get("kind")
        )
    if field_id.startswith("eval:"):
        return _eval_export_value(
            runtime["eval_metrics"], field_def.get("eval_key"), field_def.get("kind")
        )
    if field_id.startswith("attr:"):
        source_type = field_def.get("source_type")
        if source_type and item.source_type != source_type:
            return ""
        path = field_def.get("path") or _parse_attribute_field_id(field_id)[1]
        return _nested_get(content, path)
    if field_def.get("path"):
        return _nested_get(content, field_def["path"])
    return ""


# Dispatch table for filter-mode resolvers. Later phases (6, 8) extend this
# with ``trace_session`` / ``call_execution`` entries alongside their own
# sibling resolver functions in ``model_hub.services.bulk_selection``.
FILTER_MODE_RESOLVERS = {
    "trace": resolve_filtered_trace_ids,
    "observation_span": resolve_filtered_span_ids,
    "trace_session": resolve_filtered_session_ids,
    "call_execution": resolve_filtered_call_execution_ids,
}


def _is_queue_manager(queue, user):
    return AnnotatorRole.MANAGER.value in annotation_queue_effective_roles(queue, user)


def _has_queue_role(queue_id, user, *roles):
    normalized_roles = set(roles)
    if not normalized_roles:
        return False

    if AnnotationQueueAnnotator.objects.filter(
        annotation_queue_role_q(*roles),
        queue_id=queue_id,
        user=user,
        deleted=False,
    ).exists():
        return True

    queue = (
        AnnotationQueue.objects.select_related("organization", "workspace")
        .filter(pk=queue_id, deleted=False)
        .first()
    )
    return bool(
        normalized_roles.intersection(FULL_ACCESS_QUEUE_ROLES)
        and user_has_annotation_queue_admin_access(queue, user)
    )


def _queue_member_ids(queue_id):
    return {
        str(user_id)
        for user_id in AnnotationQueueAnnotator.objects.filter(
            queue_id=queue_id,
            deleted=False,
        ).values_list("user_id", flat=True)
    }


def _extract_mentioned_user_ids(comment):
    """Support rich-text mention payloads that embed ids as user:<uuid>."""
    return {match.group(1) for match in USER_MENTION_RE.finditer(comment or "")}


def _extract_mentioned_emails(comment):
    """Support typed @email mentions when the frontend did not send user ids."""
    return {
        match.group(1).strip().lower()
        for match in EMAIL_MENTION_RE.finditer(comment or "")
    }


def _split_mention_references(raw_mentions):
    mention_ids = set()
    mention_emails = set()

    for mention in raw_mentions:
        normalized = str(mention).strip()
        if not normalized:
            continue

        rich_match = USER_MENTION_RE.fullmatch(normalized)
        if rich_match:
            mention_ids.add(rich_match.group(1))
            continue

        try:
            mention_ids.add(str(uuid.UUID(normalized)))
            continue
        except (TypeError, ValueError):
            pass

        email = normalized[1:] if normalized.startswith("@") else normalized
        if EMAIL_ADDRESS_RE.fullmatch(email):
            mention_emails.add(email.lower())
            continue

        mention_ids.add(normalized)

    return mention_ids, mention_emails


def _queue_member_ids_from_emails(queue_id, emails):
    normalized_emails = {
        str(email).strip().lower()
        for email in emails
        if str(email).strip()
    }
    if not normalized_emails:
        return set()

    return {
        str(user_id)
        for user_id in AnnotationQueueAnnotator.objects.filter(
            queue_id=queue_id,
            deleted=False,
        )
        .annotate(user_email_lower=Lower("user__email"))
        .filter(user_email_lower__in=normalized_emails)
        .values_list("user_id", flat=True)
    }


def _queue_members_from_ids(queue_id, user_ids):
    if not user_ids:
        return []

    member_ids = _queue_member_ids(queue_id)
    normalized_ids = []
    seen = set()
    for user_id in user_ids:
        try:
            normalized = str(uuid.UUID(str(user_id)))
        except (TypeError, ValueError):
            raise ValueError("Invalid mentioned user.") from None
        if normalized not in member_ids:
            raise ValueError("Mentioned users must be members of this queue.")
        if normalized not in seen:
            normalized_ids.append(normalized)
            seen.add(normalized)
        if len(normalized_ids) > MAX_MENTIONED_USERS_PER_COMMENT:
            raise ValueError(
                f"At most {MAX_MENTIONED_USERS_PER_COMMENT} users can be mentioned."
            )

    users_by_id = {
        str(user.id): user for user in User.objects.filter(id__in=normalized_ids)
    }
    return [
        users_by_id[user_id] for user_id in normalized_ids if user_id in users_by_id
    ]


def _restore_archived_default_queue(queue):
    """Un-soft-delete a previously archived default queue and reset its
    automation rules so they don't all fire at once on first scheduler tick.

    A queue can be archived (soft-deleted) by a user clicking "Delete" in
    the UI. Default queue identity is per-scope, so when the user lands
    back on the project/dataset/agent's annotation page we restore the
    archived queue rather than create a new sibling. Rules + items + label
    bindings come back too — that's the whole point.

    Edge case the cadence-reset addresses: if the queue was archived for
    a long time, every attached rule's ``last_triggered_at`` is now stale
    enough that the scheduler thinks they're all immediately due — leading
    to a flood of evaluations on the first tick after restore. Bumping
    ``last_triggered_at`` to "now" defers them by their normal cadence
    (hourly/daily/etc) so the user sees a smooth ramp-back-up.
    """
    from django.utils import timezone as tz

    from model_hub.models.annotation_queues import AutomationRule

    queue.deleted = False
    queue.deleted_at = None
    queue.save(update_fields=["deleted", "deleted_at", "updated_at"])

    AutomationRule.objects.filter(queue=queue, deleted=False).update(
        last_triggered_at=tz.now()
    )


def _ensure_default_queue_member_can_manage(queue, user):
    """Default queues are project entrypoints; make active users full members.

    Custom queues keep explicit membership semantics. For default queues,
    the Observe/inline annotation path can fetch an existing queue created by
    someone else or restored from archive. Without a membership row the user
    can annotate via the default-queue open access path, but the queue detail
    page hides management surfaces such as Rules and Settings. Upserting the
    active requester keeps the role-gated API/UI behavior consistent.
    """
    if not queue or not user or not getattr(queue, "is_default", False):
        return None

    active = AnnotationQueueAnnotator.objects.filter(
        queue=queue,
        user=user,
        deleted=False,
    ).first()
    if active:
        if (
            active.role == AnnotatorRole.MANAGER.value
            and active.normalized_roles == DEFAULT_QUEUE_FULL_ACCESS_ROLES
        ):
            return active
        active.role = AnnotatorRole.MANAGER.value
        active.roles = DEFAULT_QUEUE_FULL_ACCESS_ROLES
        active.save(update_fields=["role", "roles", "updated_at"])
        return active

    soft_deleted = (
        AnnotationQueueAnnotator.all_objects.filter(queue=queue, user=user)
        .order_by("-updated_at")
        .first()
    )
    if soft_deleted:
        soft_deleted.deleted = False
        soft_deleted.deleted_at = None
        soft_deleted.role = AnnotatorRole.MANAGER.value
        soft_deleted.roles = DEFAULT_QUEUE_FULL_ACCESS_ROLES
        soft_deleted.save(
            update_fields=["deleted", "deleted_at", "role", "roles", "updated_at"]
        )
        return soft_deleted

    return AnnotationQueueAnnotator.objects.create(
        queue=queue,
        user=user,
        role=AnnotatorRole.MANAGER.value,
        roles=DEFAULT_QUEUE_FULL_ACCESS_ROLES,
    )


def _finalize_bulk_add(queue, items_to_create):
    """Bulk-create QueueItems, run auto-assign, flip queue status if needed.

    Shared by both the enumerated ``items`` branch and the filter-mode
    ``selection`` branch of the ``add-items`` action. Keeping the
    post-create logic in one place prevents auto-assign semantics from
    drifting between the two paths.

    Returns (added_count, new_queue_status).
    """
    from django.db import transaction

    created = []
    if items_to_create:
        with transaction.atomic():
            created = QueueItem.objects.bulk_create(items_to_create)

    # Auto-assign: when auto_assign is True, assign all items to all annotators
    # (each item gets no specific assigned_to — all members can work on any
    # item). When using round-robin/load-balanced strategy, distribute items.
    if created and queue.assignment_strategy != "manual":
        auto_assign_items(queue, created)
        QueueItem.objects.bulk_update(created, ["assigned_to"])
    elif created and queue.auto_assign:
        member_ids = list(
            AnnotationQueueAnnotator.objects.filter(queue=queue, deleted=False)
            .filter(annotation_queue_role_q(AnnotatorRole.ANNOTATOR.value))
            .values_list("user_id", flat=True)
            .distinct()
        )
        if member_ids:
            assignments = [
                QueueItemAssignment(queue_item=item, user_id=uid)
                for item in created
                for uid in member_ids
            ]
            QueueItemAssignment.objects.bulk_create(assignments, ignore_conflicts=True)

    # Re-activate the queue if it was completed and new items were added
    new_status = queue.status
    if (
        len(created) > 0
        and queue.status == AnnotationQueueStatusChoices.COMPLETED.value
    ):
        queue.status = AnnotationQueueStatusChoices.ACTIVE.value
        queue.save()
        new_status = queue.status

    return len(created), new_status


def _flatten_validation_errors(detail) -> str:
    """Extract the first human-readable message from DRF ValidationError detail."""
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        return str(detail[0]) if detail else "Validation error."
    if isinstance(detail, dict):
        for value in detail.values():
            if isinstance(value, list) and value:
                return str(value[0])
            if isinstance(value, str):
                return value
    return "Validation error."


def _is_truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _is_review_workspace_request(request, *, is_reviewer, review_status=None) -> bool:
    """Whether annotate/next-item should use manager/reviewer comparison scope."""
    if not is_reviewer:
        return False
    view_mode = (
        request.query_params.get("view_mode")
        or request.query_params.get("mode")
        or ""
    )
    return (
        review_status == "pending_review"
        or str(view_mode).strip().lower() in {"review", "comparison", "submissions"}
        or _is_truthy(request.query_params.get("include_all_annotations"))
    )


def _workspace_visible_queue_count(org, workspace):
    queryset = AnnotationQueue.no_workspace_objects.filter(organization=org)
    if not workspace:
        return queryset.count()
    if getattr(workspace, "is_default", False):
        return queryset.filter(
            Q(workspace=workspace)
            | Q(workspace__is_default=True, workspace__organization=org)
            | Q(workspace__isnull=True)
        ).count()
    return queryset.filter(workspace=workspace).count()


def _check_annotation_queue_create_limit(org, workspace=None):
    """Enforce Cloud plan limits before activating a new annotation queue."""
    from tfc.ee_gating import (
        EEResource,
        FeatureUnavailable,
        check_ee_can_create,
        is_oss,
    )

    # Annotation queues are a core self-hosted/local feature. Pricing limits
    # only apply when the EE/Cloud entitlement service is available.
    if is_oss():
        return

    # Queue pricing limits are org-level entitlements. Do not use the default
    # manager here because it applies the current workspace context and would
    # under-count queues that exist in other workspaces in the same org.
    current_count = AnnotationQueue.no_workspace_objects.filter(
        organization=org,
    ).count()
    try:
        check_ee_can_create(
            EEResource.ANNOTATION_QUEUES,
            org_id=str(org.id),
            current_count=current_count,
        )
    except FeatureUnavailable as exc:
        workspace_count = _workspace_visible_queue_count(org, workspace)
        if workspace and workspace_count != current_count:
            metadata = dict(getattr(exc, "metadata", {}) or {})
            metadata["workspace_usage"] = workspace_count
            metadata["other_workspace_usage"] = max(current_count - workspace_count, 0)
            limit = metadata.get("limit")
            limit_text = f"{limit} " if limit is not None else ""
            detail = (
                f"You've reached the {limit_text}annotation queues limit across "
                f"this organization ({current_count} existing queues; "
                f"{workspace_count} in the current workspace and "
                f"{metadata['other_workspace_usage']} in other workspaces). "
                "Archive unused queues in another workspace or upgrade your plan."
            )
            raise FeatureUnavailable(
                EEResource.ANNOTATION_QUEUES,
                detail=detail,
                code=getattr(exc, "error_code", None),
                upgrade_cta=getattr(exc, "upgrade_cta", None),
                metadata=metadata,
            )
        raise


class AnnotationQueueViewSet(BaseModelViewSetMixinWithUserOrg, viewsets.ModelViewSet):
    serializer_class = AnnotationQueueSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = ExtendedPageNumberPagination
    queryset = AnnotationQueue.objects.all()
    _gm = GeneralMethods()

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related("created_by")
            .prefetch_related(
                Prefetch(
                    "queue_labels",
                    queryset=AnnotationQueueLabel.objects.filter(
                        deleted=False
                    ).select_related("label"),
                ),
                Prefetch(
                    "queue_annotators",
                    queryset=AnnotationQueueAnnotator.objects.filter(
                        deleted=False
                    ).select_related("user"),
                ),
            )
        )

        is_list_action = getattr(self, "action", None) == "list"
        status = (
            self.request.query_params.get("status", None) if is_list_action else None
        )
        search = (
            self.request.query_params.get("search", None) if is_list_action else None
        )
        include_counts = (
            is_list_action
            and self.request.query_params.get("include_counts", "").lower() == "true"
        )

        if status:
            queryset = queryset.filter(status=status)
        if search:
            queryset = queryset.filter(name__icontains=search)

        if include_counts:
            queryset = queryset.annotate(
                label_count=Coalesce(
                    Count(
                        "queue_labels",
                        filter=Q(queue_labels__deleted=False),
                        distinct=True,
                    ),
                    0,
                ),
                annotator_count=Coalesce(
                    Count(
                        "queue_annotators",
                        filter=Q(queue_annotators__deleted=False),
                        distinct=True,
                    ),
                    0,
                ),
                item_count=Coalesce(
                    Count(
                        "items",
                        filter=Q(items__deleted=False),
                        distinct=True,
                    ),
                    0,
                ),
                completed_count=Coalesce(
                    Count(
                        "items",
                        filter=Q(
                            items__deleted=False,
                            items__status="completed",
                        ),
                        distinct=True,
                    ),
                    0,
                ),
            )

        return queryset.order_by("-created_at")

    @swagger_auto_schema(query_serializer=AnnotationQueueListQuerySerializer)
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        org = getattr(request, "organization", None) or request.user.organization
        try:
            serializer.is_valid(raise_exception=True)

            from tfc.ee_gating import (
                EEFeature,
                check_ee_feature,
            )

            requires_review = _is_truthy(
                serializer.validated_data.get("requires_review", False)
            )
            if requires_review:
                check_ee_feature(EEFeature.REVIEW_WORKFLOW, org_id=str(org.id))
            _check_annotation_queue_create_limit(
                org, getattr(request, "workspace", None)
            )
        except serializers.ValidationError as exc:
            msg = _flatten_validation_errors(exc.detail)
            return self._gm.custom_error_response(status_code=400, result=msg)

        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)

        return Response(
            serializer.data, status=status.HTTP_201_CREATED, headers=headers
        )

    def perform_create(self, serializer):
        serializer.save(
            organization=self.request.organization,
            created_by=self.request.user,
        )

    def update(self, request, *args, **kwargs):
        """Only managers of the queue may update queue settings."""
        instance = self.get_object()
        if not _is_queue_manager(instance, request.user):
            return self._gm.forbidden_response(
                "Only queue managers can update queue settings."
            )

        requires_review_requested = request.data.get("requires_review")
        if requires_review_requested is not None and _is_truthy(
            requires_review_requested
        ):
            from tfc.ee_gating import EEFeature, check_ee_feature

            org = getattr(request, "organization", None) or request.user.organization
            check_ee_feature(EEFeature.REVIEW_WORKFLOW, org_id=str(org.id))

        try:
            return super().update(request, *args, **kwargs)
        except serializers.ValidationError as exc:
            msg = _flatten_validation_errors(exc.detail)
            return self._gm.custom_error_response(status_code=400, result=msg)

    def perform_update(self, serializer):
        old_strategy = serializer.instance.assignment_strategy
        instance = serializer.save()

        # Auto-assign existing unassigned items when switching to an auto strategy
        new_strategy = instance.assignment_strategy
        if old_strategy == "manual" and new_strategy in (
            "round_robin",
            "load_balanced",
        ):
            unassigned = list(
                QueueItem.objects.filter(
                    queue=instance,
                    deleted=False,
                    assigned_to__isnull=True,
                    status="pending",
                )
            )
            if unassigned:
                auto_assign_items(instance, unassigned)
                QueueItem.objects.bulk_update(unassigned, ["assigned_to"])

    def destroy(self, request, *args, **kwargs):
        """Archive a queue (soft delete).

        ``BaseModel.delete()`` flips ``deleted=True`` instead of removing
        the row. Attached automation rules go dormant (the scheduler
        filters ``queue__deleted=False``), items stay invisible but
        recoverable, label bindings preserved.

        For truly destructive removal, use the ``hard-delete`` action
        below.
        """
        instance = self.get_object()
        self.perform_destroy(instance)
        return self._gm.success_response(
            {"deleted": True, "archived": True, "queue_id": str(instance.pk)}
        )

    @swagger_auto_schema(request_body=no_body)
    @action(detail=True, methods=["post"], url_path="restore")
    def restore(self, request, pk=None):
        try:
            queue = AnnotationQueue.all_objects.get(
                pk=pk,
                deleted=True,
                organization=request.organization,
            )
        except AnnotationQueue.DoesNotExist:
            return self._gm.not_found("Queue not found or not archived.")

        # Resets rule cadence so users don't get a flood of fires on the
        # first scheduler tick after restoring a long-archived queue.
        _restore_archived_default_queue(queue)
        serializer = self.get_serializer(queue)
        return self._gm.success_response(serializer.data)

    @swagger_auto_schema(request_body=QueueHardDeleteRequestSerializer)
    @action(detail=True, methods=["post"], url_path="hard-delete")
    def hard_delete(self, request, pk=None):
        """Permanently remove a queue + everything attached.

        Hard delete cascades through the FK graph (rules, items,
        assignments, scores) via ``on_delete=CASCADE``. There is no
        recovery — callers must pass ``force=true`` AND the queue's
        exact name as ``confirm_name`` so the action can't fire from
        a typo'd request.
        """
        queue = self.get_object()
        serializer = QueueHardDeleteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data.get("force") is not True:
            return self._gm.bad_request(
                "Hard delete requires force=true. Use the archive endpoint "
                "(DELETE /annotation-queues/<id>/) for soft-delete with "
                "restore."
            )
        confirm_name = data.get("confirm_name") or ""
        if confirm_name != queue.name:
            return self._gm.bad_request(
                "confirm_name must match the queue's exact name to hard-delete it."
            )
        queue_id = str(queue.pk)
        # Real DB delete — not BaseModel.delete() which only flips deleted=True.
        # The Manager delete() uses queryset semantics, which Django routes to
        # SQL DELETE rather than per-row .delete() — that's what we want here.
        AnnotationQueue.all_objects.filter(pk=queue.pk).delete()
        return self._gm.success_response(
            {"deleted": True, "hard_deleted": True, "queue_id": queue_id}
        )

    @action(detail=True, methods=["get"], url_path="progress")
    def progress(self, request, pk=None):
        queue = self.get_object()
        items_qs = QueueItem.objects.filter(queue=queue, deleted=False)
        total = items_qs.count()

        status_counts = {}
        for row in items_qs.values("status").annotate(cnt=Count("id")):
            status_counts[row["status"]] = row["cnt"]

        in_review = items_qs.filter(review_status="pending_review").count()
        in_progress = max(
            status_counts.get(QueueItemStatus.IN_PROGRESS.value, 0) - in_review,
            0,
        )
        completed = status_counts.get("completed", 0)
        progress_pct = round((completed / total) * 100, 1) if total > 0 else 0

        # Per-annotator stats: combine assignment-based + actual annotation work
        assigned_stats = {}
        for row in (
            items_qs.exclude(assigned_to__isnull=True)
            .values("assigned_to", "assigned_to__name")
            .annotate(
                completed_cnt=Count("id", filter=Q(status="completed")),
                pending_cnt=Count("id", filter=Q(status="pending")),
                in_progress_cnt=Count(
                    "id",
                    filter=Q(status="in_progress") & ~Q(review_status="pending_review"),
                ),
                in_review_cnt=Count("id", filter=Q(review_status="pending_review")),
            )
        ):
            uid = str(row["assigned_to"])
            assigned_stats[uid] = {
                "user_id": uid,
                "name": row["assigned_to__name"],
                "completed": row["completed_cnt"],
                "pending": row["pending_cnt"],
                "in_progress": row["in_progress_cnt"],
                "in_review": row["in_review_cnt"],
                "annotations_count": 0,
            }

        # Supplement with actual annotation counts from Score
        actual_work = (
            Score.objects.filter(queue_item__queue=queue, deleted=False)
            .values("annotator", "annotator__name")
            .annotate(annotations_count=Count("id"))
        )
        for row in actual_work:
            uid = str(row["annotator"])
            if uid in assigned_stats:
                assigned_stats[uid]["annotations_count"] = row["annotations_count"]
            else:
                assigned_stats[uid] = {
                    "user_id": uid,
                    "name": row["annotator__name"],
                    "completed": 0,
                    "pending": 0,
                    "in_progress": 0,
                    "in_review": 0,
                    "annotations_count": row["annotations_count"],
                }

        user_items = _queue_item_user_scope(
            items_qs,
            request.user,
            include_unassigned=(
                queue.assignment_strategy == AssignmentStrategy.MANUAL.value
            ),
        )
        user_total = user_items.count()
        user_status_counts = {}
        for row in user_items.values("status").annotate(cnt=Count("id", distinct=True)):
            user_status_counts[row["status"]] = row["cnt"]
        user_in_review = user_items.filter(review_status="pending_review").count()
        user_in_progress = max(
            user_status_counts.get(QueueItemStatus.IN_PROGRESS.value, 0)
            - user_in_review,
            0,
        )
        user_completed = user_status_counts.get(QueueItemStatus.COMPLETED.value, 0)
        user_progress_pct = (
            round((user_completed / user_total) * 100, 1) if user_total > 0 else 0
        )

        return self._gm.success_response(
            {
                "total": total,
                "pending": status_counts.get("pending", 0),
                "in_progress": in_progress,
                "in_review": in_review,
                "completed": completed,
                "skipped": status_counts.get("skipped", 0),
                "progress_pct": progress_pct,
                "annotator_stats": list(assigned_stats.values()),
                "user_progress": {
                    "total": user_total,
                    "completed": user_completed,
                    "pending": user_status_counts.get(QueueItemStatus.PENDING.value, 0),
                    "in_progress": user_in_progress,
                    "in_review": user_in_review,
                    "skipped": user_status_counts.get(QueueItemStatus.SKIPPED.value, 0),
                    "progress_pct": user_progress_pct,
                },
            }
        )

    @swagger_auto_schema(request_body=QueueStatusRequestSerializer)
    @action(detail=True, methods=["post"], url_path="update-status")
    def update_status(self, request, pk=None):
        queue = self.get_object()
        serializer = QueueStatusRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data.get("status")

        if not new_status:
            return self._gm.bad_request("Status is required.")

        valid_values = [c.value for c in AnnotationQueueStatusChoices]
        if new_status not in valid_values:
            return self._gm.bad_request(
                f"Invalid status. Must be one of: {', '.join(valid_values)}"
            )

        allowed = VALID_STATUS_TRANSITIONS.get(queue.status, set())
        if new_status not in allowed:
            return self._gm.bad_request(
                f"Cannot transition from '{queue.status}' to '{new_status}'."
            )

        queue.status = new_status
        queue.save(update_fields=["status", "updated_at"])
        serializer = self.get_serializer(queue)
        return self._gm.success_response(serializer.data)

    @swagger_auto_schema(query_serializer=QueueExportQuerySerializer)
    @action(detail=True, methods=["get"], url_path="export")
    def export_annotations(self, request, pk=None):
        """Export all items with their annotations."""
        queue = self.get_object()
        items_qs = (
            QueueItem.objects.filter(queue=queue, deleted=False)
            .select_related(
                "queue",
                "reviewed_by",
                "trace",
                "observation_span",
                "dataset_row",
                "prototype_run",
                "call_execution",
                "trace_session",
            )
            .prefetch_related(
                Prefetch(
                    "trace__observation_spans",
                    queryset=ObservationSpan.objects.filter(deleted=False).order_by(
                        "start_time", "created_at"
                    ),
                    to_attr="_queue_export_spans",
                )
            )
        )

        status_filter = _normalize_export_status_filter(
            request.query_params.get("status")
        )
        if status_filter:
            items_qs = items_qs.filter(status=status_filter)

        items_list = list(items_qs.order_by("order", "created_at"))
        queue_label_ids = list(
            queue.queue_labels.filter(deleted=False).values_list("label_id", flat=True)
        )
        scores_by_item = _scores_for_queue_items(items_list, queue_label_ids)
        item_notes_by_id = _latest_item_notes_for_queue_items(items_list)
        eval_metrics_by_item = _eval_metrics_for_queue_items(items_list)

        result = []
        for item in items_list:
            content = resolve_source_content(item)
            annotations = [
                _serialize_score_for_export(score)
                for score in scores_by_item.get(item.id, [])
            ]
            evals = _eval_metrics_export_value(eval_metrics_by_item.get(item.id, {}))
            result.append(
                {
                    "item_id": str(item.id),
                    "source_type": item.source_type,
                    "source_id": _source_id_for_queue_item(item),
                    "status": item.status,
                    "order": item.order,
                    "review": _serialize_item_review(item),
                    "item_notes": item_notes_by_id.get(item.id, ""),
                    "annotations": annotations,
                    "annotation_metrics": _annotation_metrics_for_scores(
                        scores_by_item.get(item.id, [])
                    ),
                    "evals": evals,
                    "source": content,
                }
            )

        fmt = request.query_params.get(
            "export_format", request.query_params.get("format", "json")
        )
        if fmt == "csv":
            import csv
            import io

            from django.http import HttpResponse

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(
                [
                    "item_id",
                    "source_type",
                    "status",
                    "order",
                    "label_id",
                    "label_name",
                    "value",
                    "score_source",
                    "notes",
                    "annotator_name",
                    "created_at",
                ]
            )
            for item_data in result:
                if not item_data["annotations"]:
                    writer.writerow(
                        [
                            item_data["item_id"],
                            item_data["source_type"],
                            item_data["status"],
                            item_data["order"],
                            "",
                            "",
                            "",
                            "",
                            "",
                            "",
                            "",
                        ]
                    )
                for ann in item_data["annotations"]:
                    writer.writerow(
                        [
                            item_data["item_id"],
                            item_data["source_type"],
                            item_data["status"],
                            item_data["order"],
                            ann["label_id"],
                            ann["label_name"],
                            (
                                ann["value"]
                                if not isinstance(ann["value"], dict)
                                else str(ann["value"])
                            ),
                            ann["score_source"],
                            ann["notes"] or "",
                            ann["annotator_name"],
                            ann["created_at"],
                        ]
                    )
            from urllib.parse import quote

            response = HttpResponse(output.getvalue(), content_type="text/csv")
            safe_pk = quote(str(pk), safe="")
            response["Content-Disposition"] = (
                f'attachment; filename="queue_{safe_pk}_annotations.csv"'
            )
            return response

        return self._gm.success_response(result)

    @action(detail=True, methods=["get"], url_path="analytics")
    def analytics(self, request, pk=None):
        """Queue analytics: throughput, annotator performance, label distribution."""
        queue = self.get_object()
        items_qs = QueueItem.objects.filter(queue=queue, deleted=False)

        # Status breakdown
        status_counts = {}
        for row in items_qs.values("status").annotate(cnt=Count("id")):
            status_counts[row["status"]] = row["cnt"]

        total = sum(status_counts.values())
        completed = status_counts.get("completed", 0)

        # Throughput: completed items by date (last 30 days)
        thirty_days_ago = timezone.now() - timedelta(days=30)
        daily_throughput = list(
            items_qs.filter(
                status="completed",
                updated_at__gte=thirty_days_ago,
            )
            .annotate(date=TruncDate("updated_at"))
            .values("date")
            .annotate(count=Count("id"))
            .order_by("date")
        )

        completed_in_window = items_qs.filter(
            status="completed", updated_at__gte=thirty_days_ago
        ).count()
        avg_per_day = round(completed_in_window / 30, 1)

        # Annotator performance
        annotator_perf = list(
            Score.objects.filter(queue_item__queue=queue, deleted=False)
            .values("annotator", "annotator__name")
            .annotate(
                completed=Count("id"),
                last_active=Max("created_at"),
            )
            .order_by("-completed")
        )

        # Label distribution
        label_dist_raw = (
            Score.objects.filter(queue_item__queue=queue, deleted=False)
            .values("label__id", "label__name", "label__type", "value")
            .annotate(count=Count("id"))
        )
        label_dist = {}
        for row in label_dist_raw:
            lid = str(row["label__id"])
            if lid not in label_dist:
                label_dist[lid] = {
                    "name": row["label__name"],
                    "type": row["label__type"],
                    "values": {},
                }
            val_key = (
                str(row["value"]) if not isinstance(row["value"], str) else row["value"]
            )
            label_dist[lid]["values"][val_key] = row["count"]

        return self._gm.success_response(
            {
                "throughput": {
                    "daily": [
                        {"date": str(d["date"]), "count": d["count"]}
                        for d in daily_throughput
                    ],
                    "total_completed": completed,
                    "avg_per_day": avg_per_day,
                },
                "annotator_performance": [
                    {
                        "user_id": str(a["annotator"]),
                        "name": a["annotator__name"],
                        "completed": a["completed"],
                        "last_active": (
                            a["last_active"].isoformat() if a["last_active"] else None
                        ),
                    }
                    for a in annotator_perf
                ],
                "label_distribution": label_dist,
                "status_breakdown": status_counts,
                "total": total,
            }
        )

    @action(detail=True, methods=["get"], url_path="export-fields")
    def export_fields(self, request, pk=None):
        """Return source/label/attribute fields available for dataset export."""
        queue = self.get_object()
        return self._gm.success_response(_build_annotation_queue_export_fields(queue))

    @swagger_auto_schema(request_body=QueueExportToDatasetRequestSerializer)
    @action(detail=True, methods=["post"], url_path="export-to-dataset")
    def export_to_dataset(self, request, pk=None):
        """Export queue items to a dataset using a user-editable column mapping."""
        from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

        queue = self.get_object()
        serializer = QueueExportToDatasetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        dataset_id = data.get("dataset_id")
        dataset_name = data.get("dataset_name")
        status_filter = _normalize_export_status_filter(
            data.get("status_filter", "completed")
        )

        if not dataset_id and not dataset_name:
            return self._gm.bad_request(
                "Either dataset_id or dataset_name is required."
            )

        if dataset_id:
            try:
                dataset = Dataset.objects.get(
                    pk=dataset_id,
                    organization=request.organization,
                    deleted=False,
                )
            except Dataset.DoesNotExist:
                return self._gm.not_found("Dataset not found.")
        else:
            dataset = Dataset.objects.create(
                name=dataset_name,
                organization=request.organization,
                workspace=queue.workspace,
                user=request.user,
            )

        items_qs = (
            QueueItem.objects.filter(queue=queue, deleted=False)
            .select_related(
                "queue",
                "reviewed_by",
                "trace",
                "observation_span",
                "dataset_row",
                "prototype_run",
                "call_execution",
                "trace_session",
            )
            .prefetch_related(
                Prefetch(
                    "trace__observation_spans",
                    queryset=ObservationSpan.objects.filter(deleted=False).order_by(
                        "start_time", "created_at"
                    ),
                    to_attr="_queue_export_spans",
                )
            )
        )
        if status_filter:
            items_qs = items_qs.filter(status=status_filter)
        items_list = list(items_qs.order_by("order", "created_at"))

        export_field_defs = _build_annotation_queue_export_fields(
            queue, sample_items=items_list[:100]
        )
        fields_by_id = {field["id"]: field for field in export_field_defs["fields"]}
        requested_mapping = data.get("column_mapping") or []
        if not requested_mapping:
            requested_mapping = export_field_defs["default_mapping"]

        column_mapping = []
        used_columns = set()
        for entry in requested_mapping:
            if entry.get("enabled") is False:
                continue
            field_id = entry.get("field") or entry.get("id")
            field_def = fields_by_id.get(field_id) or _custom_attribute_export_field(
                field_id or "", items_list[:100]
            )
            if not field_def:
                continue
            if field_def.get("expand_fields"):
                continue
            column_name = (entry.get("column") or field_def["column"] or "").strip()
            if not column_name:
                continue
            if column_name.lower() in used_columns:
                return self._gm.bad_request(
                    f"Duplicate export column name: {column_name}"
                )
            used_columns.add(column_name.lower())
            column_mapping.append(
                {
                    "field": field_id,
                    "field_def": field_def,
                    "column": column_name,
                    "data_type": field_def["data_type"],
                    "source": _export_column_source(field_def),
                }
            )

        if not column_mapping:
            return self._gm.bad_request("Select at least one export column.")

        existing_columns = {
            col.name.lower(): col
            for col in Column.objects.filter(dataset=dataset, deleted=False)
        }
        columns = {}
        new_columns = []
        for mapping in column_mapping:
            column_name = mapping["column"]
            existing_column = existing_columns.get(column_name.lower())
            if existing_column:
                columns[column_name] = existing_column
                continue
            column = Column(
                name=column_name,
                data_type=mapping["data_type"],
                source=mapping["source"],
                dataset=dataset,
                status=StatusType.COMPLETED.value,
            )
            new_columns.append(column)
            columns[column_name] = column

        if new_columns:
            Column.objects.bulk_create(new_columns)
            column_order = list(dataset.column_order or [])
            column_config = dict(dataset.column_config or {})
            for column in new_columns:
                column_id = str(column.id)
                if column_id not in column_order:
                    column_order.append(column_id)
                column_config[column_id] = {
                    "is_frozen": False,
                    "is_visible": True,
                }
            dataset.column_order = column_order
            dataset.column_config = column_config
            dataset.save(update_fields=["column_order", "column_config"])

        max_order = (
            Row.objects.filter(dataset=dataset, deleted=False)
            .order_by("-order")
            .values_list("order", flat=True)
            .first()
        ) or 0

        rows_to_create = []
        item_runtime = {}
        queue_label_ids = list(
            queue.queue_labels.filter(deleted=False).values_list("label_id", flat=True)
        )
        scores_by_item = _scores_for_queue_items(items_list, queue_label_ids)
        item_notes_by_id = _latest_item_notes_for_queue_items(items_list)
        eval_metrics_by_item = _eval_metrics_for_queue_items(items_list)
        for i, item in enumerate(items_list):
            content = resolve_source_content(item)
            scores = scores_by_item.get(item.id, [])
            annotations_metadata = {}
            for score in scores:
                label_id = str(score.label_id)
                annotations_metadata.setdefault(label_id, []).append(
                    _serialize_score_for_export(score)
                )

            rows_to_create.append(
                Row(
                    dataset=dataset,
                    order=max_order + i + 1,
                    metadata={
                        "queue_id": str(queue.id),
                        "queue_item_id": str(item.id),
                        "source_type": item.source_type,
                        "source_id": _source_id_for_queue_item(item),
                        "annotations": annotations_metadata,
                        "review": _serialize_item_review(item),
                    },
                )
            )
            item_runtime[item.id] = {
                "content": content,
                "scores": scores,
                "item_notes": item_notes_by_id.get(item.id, ""),
                "eval_metrics": eval_metrics_by_item.get(item.id, {}),
            }

        if rows_to_create:
            Row.objects.bulk_create(rows_to_create, batch_size=500)

        cells_to_create = []
        mapped_column_ids = {columns[mapping["column"]].id for mapping in column_mapping}
        unmapped_existing_columns = [
            column
            for column in existing_columns.values()
            if column.id not in mapped_column_ids
        ]
        for row, item in zip(rows_to_create, items_list, strict=False):
            runtime = item_runtime[item.id]
            content = runtime["content"]
            input_value, output_value = _extract_input_output(content)
            runtime["input_value"] = input_value
            runtime["output_value"] = output_value

            for mapping in column_mapping:
                value = _resolve_export_field_value(item, content, runtime, mapping)

                cells_to_create.append(
                    Cell(
                        dataset=dataset,
                        row=row,
                        column=columns[mapping["column"]],
                        value=_to_cell_str(value),
                    )
                )
            cells_to_create.extend(
                Cell(dataset=dataset, row=row, column=column, value="")
                for column in unmapped_existing_columns
            )

        if cells_to_create:
            Cell.objects.bulk_create(cells_to_create, batch_size=500)

        if new_columns:
            pre_existing_rows = Row.objects.filter(
                dataset=dataset, deleted=False
            ).exclude(id__in=[row.id for row in rows_to_create])
            backfill_cells = [
                Cell(dataset=dataset, row=row, column=column, value="")
                for row in pre_existing_rows
                for column in new_columns
            ]
            if backfill_cells:
                Cell.objects.bulk_create(backfill_cells, batch_size=500)

        return self._gm.success_response(
            {
                "dataset_id": str(dataset.id),
                "dataset_name": dataset.name,
                "rows_created": len(rows_to_create),
                "columns": [mapping["column"] for mapping in column_mapping],
            }
        )

    @action(detail=True, methods=["get"], url_path="agreement")
    def agreement(self, request, pk=None):
        """Calculate inter-annotator agreement metrics."""
        try:
            try:
                from ee.usage.services.entitlements import Entitlements
            except ImportError:
                Entitlements = None

            org = getattr(request, "organization", None) or request.user.organization
            if Entitlements is not None:
                feat_check = Entitlements.check_feature(
                    str(org.id), "has_agreement_metrics"
                )
                if not feat_check.allowed:
                    return self._gm.forbidden_response(feat_check.reason)
        except ImportError:
            pass

        queue = self.get_object()
        result = calculate_agreement(queue)
        return self._gm.success_response(result)

    @swagger_auto_schema(request_body=QueueDefaultRequestSerializer)
    @action(detail=False, methods=["post"], url_path="get-or-create-default")
    def get_or_create_default(self, request):
        """
        Get or create the default annotation queue for a project, dataset, or agent definition.
        Default queues are open to all org members (no annotator restriction).

        Body params (one of):
          - project_id
          - dataset_id
          - agent_definition_id
        """
        from model_hub.models.develop_dataset import Dataset
        from simulate.models.agent_definition import AgentDefinition
        from tracer.models.project import Project

        serializer = QueueDefaultRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        project_id = data.get("project_id")
        dataset_id = data.get("dataset_id")
        agent_definition_id = data.get("agent_definition_id")

        org = request.organization

        if project_id:
            try:
                entity = Project.objects.get(
                    id=project_id, organization=org, deleted=False
                )
            except Project.DoesNotExist:
                return self._gm.not_found("Project not found.")
            lookup = {"project": entity}
            defaults_extra = {"workspace": entity.workspace}
        elif dataset_id:
            try:
                entity = Dataset.objects.get(
                    id=dataset_id, organization=org, deleted=False
                )
            except Dataset.DoesNotExist:
                return self._gm.not_found("Dataset not found.")
            lookup = {"dataset": entity}
            defaults_extra = {"workspace": entity.workspace}
        elif agent_definition_id:
            try:
                entity = AgentDefinition.objects.get(
                    id=agent_definition_id, organization=org, deleted=False
                )
            except AgentDefinition.DoesNotExist:
                return self._gm.not_found("Agent definition not found.")
            lookup = {"agent_definition": entity}
            defaults_extra = {"workspace": getattr(entity, "workspace", None)}
        else:
            return self._gm.bad_request(
                "project_id, dataset_id, or agent_definition_id is required."
            )

        # Default-queue identity is per-scope (project/dataset/agent), not
        # per-row. The flow is:
        #   1. If an active default already exists, return it.
        #   2. Else if an archived default exists for this scope, restore
        #      it (preserves its rules + items + history) — surfacing
        #      restore=True in the response so the UI can tell the user.
        #   3. Else create a new one.
        # The user-facing "Delete" button only archives, never hard-deletes,
        # so this path is the natural recovery for an accidental archive.
        queue = AnnotationQueue.objects.filter(
            **lookup, is_default=True, deleted=False, organization=org
        ).first()
        action = "fetched"
        if not queue:
            archived = (
                AnnotationQueue.all_objects.filter(
                    **lookup, is_default=True, deleted=True, organization=org
                )
                .order_by("-deleted_at")
                .first()
            )
            if archived:
                _check_annotation_queue_create_limit(
                    org, getattr(request, "workspace", None)
                )
                _restore_archived_default_queue(archived)
                queue = archived
                action = "restored"
            else:
                _check_annotation_queue_create_limit(
                    org, getattr(request, "workspace", None)
                )
                queue = AnnotationQueue.objects.create(
                    is_default=True,
                    name=f"Default - {getattr(entity, 'name', None) or getattr(entity, 'agent_name', str(entity))}",
                    description=f"Default annotation queue for {getattr(entity, 'name', None) or getattr(entity, 'agent_name', str(entity))}",
                    status=AnnotationQueueStatusChoices.ACTIVE.value,
                    organization=org,
                    created_by=request.user,
                    **lookup,
                    **defaults_extra,
                )
                action = "created"

        _ensure_default_queue_member_can_manage(queue, request.user)

        queue_labels = (
            queue.queue_labels.filter(deleted=False)
            .select_related("label")
            .order_by("order")
        )
        labels = [
            {
                "id": str(ql.label.id),
                "name": ql.label.name,
                "type": ql.label.type,
                "settings": ql.label.settings or {},
                "description": ql.label.description or "",
                "allow_notes": ql.label.allow_notes,
                "required": ql.required,
                "order": ql.order,
            }
            for ql in queue_labels
        ]

        return self._gm.success_response(
            {
                "queue": {
                    "id": str(queue.id),
                    "name": queue.name,
                    "description": queue.description or "",
                    "instructions": queue.instructions or "",
                    "status": queue.status,
                    "is_default": queue.is_default,
                },
                "labels": labels,
                # `created` retained for backwards compat; new clients should
                # check `action` (one of "created" | "restored" | "fetched")
                # so they can surface restore-from-archive in the UI.
                "created": action == "created",
                "action": action,
            }
        )

    @swagger_auto_schema(request_body=QueueLabelRequestSerializer)
    @action(detail=True, methods=["post"], url_path="add-label")
    def add_label(self, request, pk=None):
        """
        Add a label to an annotation queue.
        Labels apply to all sources in the queue's project (for default queues).
        Queue items are created lazily when someone actually annotates.
        """
        queue = self.get_object()
        serializer = QueueLabelRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        label_id = data.get("label_id")
        required = _is_truthy(data.get("required", True))

        if not label_id:
            return self._gm.bad_request("label_id is required.")

        if required:
            from tfc.ee_gating import EEFeature, check_ee_feature

            org = getattr(request, "organization", None) or request.user.organization
            check_ee_feature(EEFeature.REQUIRED_LABELS, org_id=str(org.id))

        try:
            label = AnnotationsLabels.objects.get(id=label_id, deleted=False)
        except AnnotationsLabels.DoesNotExist:
            return self._gm.not_found("Label not found.")

        # Add label to queue if not already there
        max_order = (
            queue.queue_labels.filter(deleted=False)
            .aggregate(max_order=Max("order"))
            .get("max_order")
            or 0
        )
        ql, label_created = AnnotationQueueLabel.objects.get_or_create(
            queue=queue,
            label=label,
            deleted=False,
            defaults={"order": max_order + 1, "required": required},
        )
        if not label_created and ql.required != required:
            ql.required = required
            ql.save(update_fields=["required", "updated_at"])

        reopened_items = 0
        if required:
            reopened_items = _reopen_items_missing_required_labels(queue)
            if (
                reopened_items
                and queue.status == AnnotationQueueStatusChoices.COMPLETED.value
            ):
                queue.status = AnnotationQueueStatusChoices.ACTIVE.value
                queue.save(update_fields=["status", "updated_at"])

        return self._gm.success_response(
            {
                "label": {
                    "id": str(label.id),
                    "name": label.name,
                    "type": label.type,
                    "settings": label.settings or {},
                    "description": label.description or "",
                    "allow_notes": label.allow_notes,
                    "required": ql.required,
                    "order": ql.order,
                },
                "created": label_created,
                "reopened_items": reopened_items,
                "queue_status": queue.status,
            }
        )

    @swagger_auto_schema(request_body=QueueLabelRequestSerializer)
    @action(detail=True, methods=["post"], url_path="remove-label")
    def remove_label(self, request, pk=None):
        """Remove a label from an annotation queue."""
        queue = self.get_object()
        serializer = QueueLabelRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        label_id = serializer.validated_data.get("label_id")

        if not label_id:
            return self._gm.bad_request("label_id is required.")

        deleted_count = AnnotationQueueLabel.objects.filter(
            queue=queue, label_id=label_id, deleted=False
        ).update(deleted=True, deleted_at=timezone.now())

        if deleted_count == 0:
            return self._gm.not_found("Label not found in this queue.")

        return self._gm.success_response({"removed": True})

    @swagger_auto_schema(query_serializer=QueueForSourceQuerySerializer)
    @action(detail=False, methods=["get"], url_path="for-source")
    def for_source(self, request):
        """
        Find annotation queues for a given source that the current user can annotate.
        Includes queues where:
        - The source is a queue item AND the user is an annotator in that queue
          (regardless of whether the item is explicitly assigned to them)

        Query params:
          - source_type, source_id  (single source)
          - OR sources (JSON array of {source_type, source_id} objects for multi-source lookup)
        """
        import json

        # Parse sources – either single or multi
        sources_param = request.query_params.get("sources")
        if sources_param:
            try:
                sources = json.loads(sources_param)
            except (json.JSONDecodeError, TypeError):
                return self._gm.bad_request("Invalid sources JSON.")
        else:
            source_type = request.query_params.get("source_type")
            source_id = request.query_params.get("source_id")
            if not source_type or not source_id:
                return self._gm.bad_request(
                    "source_type and source_id (or sources) are required."
                )
            sources = [{"source_type": source_type, "source_id": source_id}]

        # Validate all sources
        span_notes_source_ids = {}
        for src in sources:
            st = src.get("source_type")
            sid = src.get("source_id")
            if not st or not sid:
                return self._gm.bad_request(
                    "Each source must have source_type and source_id."
                )
            if st not in SOURCE_TYPE_FK_MAP:
                return self._gm.bad_request(f"Invalid source_type: {st}")
            span_notes_source_id = src.get("span_notes_source_id")
            if span_notes_source_id:
                span_notes_source = resolve_source_object(
                    "observation_span",
                    span_notes_source_id,
                    organization=request.organization,
                )
                if not span_notes_source:
                    return self._gm.not_found(
                        f"Span notes source not found: {span_notes_source_id}"
                    )
                span_notes_source_ids[(st, str(sid))] = span_notes_source_id

        # Get all queue IDs where the current user is an annotator
        user_queue_ids = set(
            AnnotationQueueAnnotator.objects.filter(
                user=request.user,
                deleted=False,
                queue__deleted=False,
                queue__organization=request.organization,
            ).values_list("queue_id", flat=True)
        )

        # Also include default queues (open to all org members)
        default_queue_ids = set(
            AnnotationQueue.objects.filter(
                is_default=True,
                deleted=False,
                organization=request.organization,
                status=AnnotationQueueStatusChoices.ACTIVE.value,
            ).values_list("id", flat=True)
        )

        # Also include queues created by the current user
        created_queue_ids = set(
            AnnotationQueue.objects.filter(
                created_by=request.user,
                deleted=False,
                organization=request.organization,
                status=AnnotationQueueStatusChoices.ACTIVE.value,
            ).values_list("id", flat=True)
        )

        accessible_queue_ids = user_queue_ids | default_queue_ids | created_queue_ids

        # Find queue items across all sources
        item_q = Q()
        for src in sources:
            fk_field = SOURCE_TYPE_FK_MAP[src["source_type"]]
            item_q |= Q(
                **{f"{fk_field}_id": src["source_id"]},
                source_type=src["source_type"],
            )

        items = (
            QueueItem.objects.filter(item_q)
            .filter(
                queue_id__in=accessible_queue_ids,
                status__in=[
                    QueueItemStatus.PENDING.value,
                    QueueItemStatus.IN_PROGRESS.value,
                    QueueItemStatus.COMPLETED.value,
                ],
                deleted=False,
            )
            .select_related("queue", "assigned_to")
            .order_by("queue__name", "order")
        )

        # Helper to build labels list and existing scores for a queue
        def _build_queue_entry(
            queue,
            item,
            source_type_for_scores,
            source_id_for_scores,
            span_notes_source_id=None,
        ):
            queue_labels = (
                queue.queue_labels.filter(deleted=False)
                .select_related("label")
                .order_by("order")
            )
            labels = [
                {
                    "id": str(ql.label.id),
                    "name": ql.label.name,
                    "type": ql.label.type,
                    "settings": ql.label.settings or {},
                    "description": ql.label.description or "",
                    "allow_notes": ql.label.allow_notes,
                    "required": ql.required,
                    "order": ql.order,
                }
                for ql in queue_labels
            ]

            # Fetch existing scores by this user for these labels on this
            # source, scoped strictly to *this* queue's review context.
            # Score uniqueness now includes queue_item, so each queue keeps
            # its own value for the same (source, label, annotator); a
            # value filled in queue A must not surface as a prefill in
            # queue B. Queues without their own score yet show empty.
            existing_scores = {}
            existing_notes = ""
            existing_label_notes = {}
            fk_field = SCORE_SOURCE_FK_MAP.get(source_type_for_scores)
            if fk_field and source_id_for_scores and item is not None:
                label_ids = [ql.label_id for ql in queue_labels]
                user_scores = Score.objects.filter(
                    **{f"{fk_field}_id": source_id_for_scores},
                    source_type=source_type_for_scores,
                    label_id__in=label_ids,
                    annotator=request.user,
                    queue_item=item,
                    deleted=False,
                )
                for sc in user_scores:
                    existing_scores[str(sc.label_id)] = sc.value
                    if sc.notes:
                        existing_label_notes[str(sc.label_id)] = sc.notes

            # Include whole-item notes saved through queue annotation. Trace/span
            # items may also have legacy SpanNotes, so keep those as fallback.
            span_notes = []
            queue_note_rows = []
            if item is not None:
                queue_note_rows = _item_note_rows(item)
                span_notes = [
                    _serialize_queue_item_note(note) for note in queue_note_rows
                ]
                user_queue_note = next(
                    (
                        note
                        for note in queue_note_rows
                        if note.annotator_id == request.user.pk
                    ),
                    None,
                )
                if user_queue_note:
                    existing_notes = user_queue_note.notes

            span_notes_lookup_id = (
                source_id_for_scores
                if source_type_for_scores == "observation_span"
                else span_notes_source_id
            )
            if span_notes_lookup_id:
                db_notes = list(
                    SpanNotes.objects.filter(span_id=span_notes_lookup_id)
                    .select_related("created_by_user")
                    .order_by("-created_at")
                )
                seen_user_ids = {
                    note.annotator_id for note in queue_note_rows if note.annotator_id
                }
                # SpanNotes is span-level, independent of any queue. We
                # surface them as read-only context but:
                #   1. Don't prefill the editable existing_notes box —
                #      that previously leaked a note written via Queue A
                #      into every other queue containing the same span.
                #   2. Filter out the **requesting user's own** SpanNote.
                #      Showing it here would mean "you see your own note
                #      from another queue but can't edit it from this
                #      queue" — visually confusing. After the notes
                #      backfill runs, the user's legacy SpanNote will
                #      appear in the default queue's editable field.
                span_notes.extend(
                    _serialize_span_note(note)
                    for note in db_notes
                    if note.created_by_user_id not in seen_user_ids
                    and note.created_by_user_id != request.user.pk
                )

            return {
                "queue": {
                    "id": str(queue.id),
                    "name": queue.name,
                    "instructions": queue.instructions or "",
                    "is_default": queue.is_default,
                },
                "item": (
                    {
                        "id": str(item.id),
                        "status": item.status,
                        "source_type": item.source_type,
                        "source_id": str(source_id_for_scores)
                        if source_id_for_scores
                        else None,
                    }
                    if item
                    else None
                ),
                "labels": labels,
                "existing_scores": existing_scores,
                "existing_notes": existing_notes,
                "existing_label_notes": existing_label_notes,
                "span_notes": span_notes,
                "span_notes_source_id": span_notes_lookup_id,
            }

        # Group by queue and include labels + source info
        results = []
        seen_queues = set()
        for item in items:
            queue = item.queue
            if queue.id in seen_queues:
                continue
            seen_queues.add(queue.id)

            source_fk_id = getattr(
                item, f"{SOURCE_TYPE_FK_MAP[item.source_type]}_id", None
            )
            results.append(
                _build_queue_entry(
                    queue,
                    item,
                    item.source_type,
                    source_fk_id,
                    span_notes_source_id=span_notes_source_ids.get(
                        (item.source_type, str(source_fk_id))
                    ),
                )
            )

        # For default queues that DON'T have queue items for these sources,
        # still return them so labels are available project-wide
        missing_default_ids = default_queue_ids - seen_queues
        if missing_default_ids:
            missing_defaults = AnnotationQueue.objects.filter(
                id__in=missing_default_ids,
            ).select_related("project", "dataset", "agent_definition")

            for dq in missing_defaults:
                if dq.id in seen_queues:
                    continue

                # Check if any source belongs to this default queue's scope
                matched_source = None
                for src in sources:
                    st = src["source_type"]
                    sid = src["source_id"]

                    # Project-scoped default queues
                    if dq.project_id and st in (
                        "trace",
                        "observation_span",
                        "trace_session",
                    ):
                        from tracer.models.observation_span import ObservationSpan
                        from tracer.models.trace import Trace

                        if st == "trace":
                            exists = Trace.objects.filter(
                                id=sid, project_id=dq.project_id, deleted=False
                            ).exists()
                        elif st == "observation_span":
                            exists = ObservationSpan.objects.filter(
                                id=sid, project_id=dq.project_id, deleted=False
                            ).exists()
                        elif st == "trace_session":
                            from tracer.models.trace_session import TraceSession

                            exists = TraceSession.objects.filter(
                                id=sid, project_id=dq.project_id, deleted=False
                            ).exists()
                        else:
                            exists = False

                        if exists:
                            matched_source = src
                            break

                    # Dataset-scoped default queues
                    if dq.dataset_id and st == "dataset_row":
                        from model_hub.models.develop_dataset import Row

                        exists = Row.objects.filter(
                            id=sid, dataset_id=dq.dataset_id, deleted=False
                        ).exists()
                        if exists:
                            matched_source = src
                            break

                    # Agent-definition-scoped default queues
                    if dq.agent_definition_id and st == "call_execution":
                        from simulate.models import CallExecution

                        exists = CallExecution.objects.filter(
                            id=sid,
                            test_execution__agent_definition_id=dq.agent_definition_id,
                            deleted=False,
                        ).exists()
                        if exists:
                            matched_source = src
                            break

                    # Agent-definition-scoped default queues for traces/spans
                    # (voice observability: Trace → Project → ObservabilityProvider → AgentDefinition)
                    if dq.agent_definition_id and st in (
                        "trace",
                        "observation_span",
                        "trace_session",
                    ):
                        from tracer.models.observation_span import ObservationSpan
                        from tracer.models.trace import Trace

                        if st == "trace":
                            exists = Trace.objects.filter(
                                id=sid,
                                project__observability_providers__agent_definition=dq.agent_definition_id,
                                deleted=False,
                            ).exists()
                        elif st == "observation_span":
                            exists = ObservationSpan.objects.filter(
                                id=sid,
                                project__observability_providers__agent_definition=dq.agent_definition_id,
                                deleted=False,
                            ).exists()
                        elif st == "trace_session":
                            from tracer.models.trace_session import TraceSession

                            exists = TraceSession.objects.filter(
                                id=sid,
                                project__observability_providers__agent_definition=dq.agent_definition_id,
                                deleted=False,
                            ).exists()
                        else:
                            exists = False

                        if exists:
                            matched_source = src
                            break

                if matched_source:
                    seen_queues.add(dq.id)
                    results.append(
                        _build_queue_entry(
                            dq,
                            None,
                            matched_source["source_type"],
                            matched_source["source_id"],
                            span_notes_source_id=matched_source.get(
                                "span_notes_source_id"
                            ),
                        )
                    )

        return self._gm.success_response(results)


class QueueItemViewSet(BaseModelViewSetMixinWithUserOrg, viewsets.ModelViewSet):
    serializer_class = QueueItemSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = ExtendedPageNumberPagination
    queryset = QueueItem.objects.all()
    _gm = GeneralMethods()

    def _get_queue_for_management(self, queue_id, request):
        try:
            queue = AnnotationQueue.objects.get(
                pk=queue_id,
                organization=request.organization,
                deleted=False,
            )
        except AnnotationQueue.DoesNotExist:
            return None
        return queue

    def _require_queue_manager(self, queue, request):
        if not _is_queue_manager(queue, request.user):
            return self._gm.forbidden_response(
                "Only queue managers can manage queue items."
            )
        return None

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related(
                "assigned_to",
                "reserved_by",
                "reviewed_by",
            )
            .prefetch_related(
                "dataset_row",
                "trace",
                "observation_span",
                "prototype_run",
                "call_execution",
                "trace_session",
            )
        )
        queue_id = self.kwargs.get("queue_id")
        if queue_id:
            queryset = queryset.filter(queue_id=queue_id)
            if not _has_queue_role(
                queue_id,
                self.request.user,
                AnnotatorRole.REVIEWER.value,
                AnnotatorRole.MANAGER.value,
            ):
                queryset = _scope_targeted_rework_items(queryset, self.request.user)

        status = _normalize_export_status_filter(
            self.request.query_params.get("status")
        )
        source_type = self.request.query_params.get("source_type")
        assigned_to = self.request.query_params.get("assigned_to")

        if status == "in_review":
            addressed_threads = QueueItemReviewThread.objects.filter(
                queue_item_id=OuterRef("pk"),
                blocking=True,
                status=QueueItemReviewThread.STATUS_ADDRESSED,
                deleted=False,
            )
            queryset = (
                queryset.filter(review_status="pending_review")
                .annotate(_has_addressed_review=Exists(addressed_threads))
                .filter(_has_addressed_review=False)
            )
        elif status == "resubmitted":
            addressed_threads = QueueItemReviewThread.objects.filter(
                queue_item_id=OuterRef("pk"),
                blocking=True,
                status=QueueItemReviewThread.STATUS_ADDRESSED,
                deleted=False,
            )
            queryset = (
                queryset.filter(review_status="pending_review")
                .annotate(_has_addressed_review=Exists(addressed_threads))
                .filter(_has_addressed_review=True)
            )
        elif status == "needs_changes":
            queryset = queryset.filter(review_status="rejected")
        elif status:
            queryset = queryset.filter(status=status)
        if source_type:
            queryset = queryset.filter(source_type=source_type)
        if assigned_to == "me":
            queryset = _queue_item_user_scope(
                queryset,
                self.request.user,
                include_unassigned=False,
            )

        review_status = _normalize_export_status_filter(
            self.request.query_params.get("review_status")
        )
        if review_status:
            queryset = queryset.filter(review_status=review_status)

        ordering = self.request.query_params.get("ordering") or "-created_at"
        queue_item_ordering = {
            "created_at": ("created_at", "id"),
            "-created_at": ("-created_at", "-id"),
        }
        return queryset.order_by(
            *queue_item_ordering.get(ordering, queue_item_ordering["-created_at"])
        )

    @swagger_auto_schema(query_serializer=QueueItemListQuerySerializer)
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        queue_id = self.kwargs.get("queue_id")
        queue = AnnotationQueue.objects.get(
            pk=queue_id,
            organization=self.request.organization,
            deleted=False,
        )
        serializer.save(
            queue=queue,
            organization=self.request.organization,
        )

    def create(self, request, *args, **kwargs):
        queue_id = kwargs.get("queue_id") or self.kwargs.get("queue_id")
        queue = self._get_queue_for_management(queue_id, request)
        if queue is None:
            return self._gm.not_found("Queue not found.")
        denied = self._require_queue_manager(queue, request)
        if denied is not None:
            return denied
        return super().create(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        item = self.get_object()
        denied = self._require_queue_manager(item.queue, request)
        if denied is not None:
            return denied
        return super().destroy(request, *args, **kwargs)

    @swagger_auto_schema(request_body=AddItemsSerializer)
    @action(detail=False, methods=["post"], url_path="add-items")
    def add_items(self, request, queue_id=None):
        serializer = AddItemsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            queue = AnnotationQueue.objects.get(
                pk=queue_id,
                organization=request.organization,
                deleted=False,
            )
        except AnnotationQueue.DoesNotExist:
            return self._gm.not_found("Queue not found.")

        denied = self._require_queue_manager(queue, request)
        if denied is not None:
            return denied

        if serializer.validated_data.get("selection"):
            return self._add_items_filter_mode(
                request, queue, serializer.validated_data["selection"]
            )

        return self._add_items_enumerated(
            request, queue, serializer.validated_data["items"]
        )

    def _add_items_enumerated(self, request, queue, items_data):
        """Add QueueItems from an explicit list of (source_type, source_id) dicts."""
        duplicates = 0
        errors = []
        items_to_create = []

        max_order = (
            QueueItem.objects.filter(queue=queue, deleted=False)
            .order_by("-order")
            .values_list("order", flat=True)
            .first()
            or 0
        )

        for item_data in items_data:
            source_type = item_data["source_type"]
            source_id = item_data["source_id"]
            fk_field = get_fk_field_name(source_type)

            if not fk_field:
                errors.append(f"Invalid source_type: {source_type}")
                continue

            source_obj = resolve_source_object(
                source_type,
                source_id,
                organization=request.organization,
                workspace=getattr(request, "workspace", None),
            )
            if not source_obj:
                errors.append(f"Not found: {source_type}={source_id}")
                continue

            dup_filter = {
                "queue": queue,
                fk_field: source_obj,
                "deleted": False,
            }
            if QueueItem.objects.filter(**dup_filter).exists():
                duplicates += 1
                continue

            max_order += 1
            items_to_create.append(
                QueueItem(
                    queue=queue,
                    source_type=source_type,
                    organization=request.organization,
                    order=max_order,
                    **{fk_field: source_obj},
                )
            )

        added, new_status = _finalize_bulk_add(queue, items_to_create)

        return self._gm.success_response(
            {
                "added": added,
                "duplicates": duplicates,
                "errors": errors,
                "queue_status": new_status,
            }
        )

    def _add_items_filter_mode(self, request, queue, selection):
        """Add QueueItems for every source row matching ``selection.filter``
        in ``selection.project_id``, minus ``selection.exclude_ids``.
        """
        source_type = selection["source_type"]
        project_id = selection["project_id"]
        filter_payload = selection.get("filter", [])
        exclude_ids = set(selection.get("exclude_ids", []))

        resolver = FILTER_MODE_RESOLVERS.get(source_type)
        if resolver is None:
            # Serializer already restricts source_type to the supported set,
            # but defense-in-depth if the constant is widened elsewhere.
            return self._gm.bad_request(
                f"selection.source_type={source_type!r} is not supported yet."
            )

        try:
            resolver_kwargs = {
                "project_id": project_id,
                "filters": filter_payload,
                "exclude_ids": exclude_ids,
                "organization": request.organization,
                "workspace": getattr(request, "workspace", None),
                "cap": MAX_SELECTION_CAP,
                "user": request.user,
            }
            # Voice-call flags are only honored by the trace resolver.
            # Other resolvers don't accept these kwargs, so gate on
            # source_type to avoid TypeError.
            if source_type == "trace":
                resolver_kwargs["is_voice_call"] = bool(
                    selection.get("is_voice_call", False)
                )
                resolver_kwargs["remove_simulation_calls"] = bool(
                    selection.get("remove_simulation_calls", False)
                )
            result = resolver(**resolver_kwargs)
        except Project.DoesNotExist:
            return self._gm.not_found("Project not found in organization.")
        except ValueError as e:
            return self._gm.bad_request(str(e))

        if result.truncated:
            return Response(
                {
                    "result": None,
                    "code": 400,
                    "error": {
                        "type": "selection_too_large",
                        "message": (
                            f"Selection matches {result.total_matching} items, "
                            f"which exceeds the {MAX_SELECTION_CAP}-item cap. "
                            "Narrow the filter and retry."
                        ),
                        "total_matching": result.total_matching,
                        "cap": MAX_SELECTION_CAP,
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        resolved_ids = result.ids
        fk_field = get_fk_field_name(source_type)

        # Duplicate detection in a single IN query — cheaper than per-row
        # .exists() checks for large resolved sets.
        existing_ids = set(
            QueueItem.objects.filter(
                queue=queue,
                deleted=False,
                **{f"{fk_field}_id__in": resolved_ids},
            ).values_list(f"{fk_field}_id", flat=True)
        )
        fresh_ids = [tid for tid in resolved_ids if tid not in existing_ids]
        duplicates = len(existing_ids)

        max_order = (
            QueueItem.objects.filter(queue=queue, deleted=False)
            .order_by("-order")
            .values_list("order", flat=True)
            .first()
            or 0
        )
        items_to_create = [
            QueueItem(
                queue=queue,
                source_type=source_type,
                organization=request.organization,
                order=max_order + i,
                **{f"{fk_field}_id": tid},
            )
            for i, tid in enumerate(fresh_ids, start=1)
        ]

        added, new_status = _finalize_bulk_add(queue, items_to_create)

        logger.info(
            "queue_add_items_filter_mode",
            queue_id=str(queue.id),
            project_id=str(project_id),
            source_type=source_type,
            total_matching=result.total_matching,
            exclude_count=len(exclude_ids),
            added=added,
            duplicates=duplicates,
        )

        return self._gm.success_response(
            {
                "added": added,
                "duplicates": duplicates,
                "errors": [],
                "queue_status": new_status,
                "total_matching": result.total_matching,
            }
        )

    @swagger_auto_schema(request_body=BulkRemoveItemsSerializer)
    @action(detail=False, methods=["post"], url_path="bulk-remove")
    def bulk_remove(self, request, queue_id=None):
        queue = self._get_queue_for_management(queue_id, request)
        if queue is None:
            return self._gm.not_found("Queue not found.")
        denied = self._require_queue_manager(queue, request)
        if denied is not None:
            return denied

        serializer = BulkRemoveItemsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item_ids = serializer.validated_data["item_ids"]
        if not item_ids:
            return self._gm.bad_request("item_ids is required.")

        removed = QueueItem.objects.filter(
            id__in=item_ids,
            queue_id=queue_id,
            organization=request.organization,
            deleted=False,
        ).update(deleted=True, deleted_at=timezone.now())

        return self._gm.success_response({"removed": removed})

    # ------------------------------------------------------------------
    # Phase 3A: Annotation actions
    # ------------------------------------------------------------------

    def _get_next_pending_item(
        self,
        queue_id,
        exclude_id=None,
        exclude_ids=None,
        user=None,
        review_status=None,
        exclude_review_status=None,
        include_completed=False,
        review_view=False,
    ):
        """Return the next work item, keeping queue order stable.

        Older clients send the entire local history as ``exclude``. Treat the
        last ID in that list as the current cursor instead of removing every
        visited item; otherwise Submit/Next can jump over skipped or pending
        rows until the page is refreshed.
        """
        now = timezone.now()
        current_item = None
        cursor_id = exclude_id
        if exclude_ids:
            cursor_id = exclude_ids[-1]
        if cursor_id:
            current_item = (
                QueueItem.objects.filter(
                    queue_id=queue_id,
                    pk=cursor_id,
                    deleted=False,
                )
                .only("id", "created_at")
                .first()
            )

        work_statuses = [
            QueueItemStatus.SKIPPED.value,
            QueueItemStatus.PENDING.value,
            QueueItemStatus.IN_PROGRESS.value,
        ]
        if include_completed:
            work_statuses.append(QueueItemStatus.COMPLETED.value)

        base_qs = QueueItem.objects.filter(
            queue_id=queue_id,
            status__in=work_statuses,
            deleted=False,
        )
        queue = AnnotationQueue.objects.filter(pk=queue_id).first()
        is_reviewer = user and _has_queue_role(
            queue_id,
            user,
            AnnotatorRole.REVIEWER.value,
            AnnotatorRole.MANAGER.value,
        )
        is_review_mode = is_reviewer and (
            review_status == "pending_review" or review_view
        )
        base_qs = _apply_review_status_filters_for_user(
            base_qs,
            review_status=review_status,
            exclude_review_status=exclude_review_status,
            user=user,
            is_reviewer=is_review_mode,
        )
        if user and queue and not queue.auto_assign and not is_review_mode:
            base_qs = _queue_item_user_scope(
                base_qs,
                user,
                include_unassigned=True,
            )
        if user and not is_reviewer and review_status != "pending_review":
            base_qs = _scope_targeted_rework_items(base_qs, user)

        # Exclude items reserved by others (unless expired)
        available_qs = base_qs.filter(
            Q(reserved_by__isnull=True)
            | Q(reserved_by=user)
            | Q(reservation_expires_at__lt=now)
        )

        if current_item:
            return _queue_items_after_work_cursor(available_qs, current_item).first()

        rework_item = (
            available_qs.filter(review_status="rejected")
            .order_by(*QUEUE_ITEM_WORK_ORDERING)
            .first()
        )
        if rework_item:
            return rework_item

        return available_qs.order_by(*QUEUE_ITEM_WORK_ORDERING).first()

    @swagger_auto_schema(request_body=SubmitAnnotationsSerializer)
    @action(detail=True, methods=["post"], url_path="annotations/submit")
    def submit_annotations(self, request, queue_id=None, pk=None):
        """Submit or update annotations for a queue item."""
        # Only allow annotation when queue is active
        try:
            queue = AnnotationQueue.objects.get(
                pk=queue_id,
                organization=request.organization,
                deleted=False,
            )
        except AnnotationQueue.DoesNotExist:
            return self._gm.not_found("Queue not found.")

        try:
            item = QueueItem.objects.get(
                pk=pk,
                queue_id=queue_id,
                organization=request.organization,
                deleted=False,
            )
        except QueueItem.DoesNotExist:
            return self._gm.not_found("Queue item not found.")

        is_completed_skipped_retry = (
            queue.status == AnnotationQueueStatusChoices.COMPLETED.value
            and item.status == QueueItemStatus.SKIPPED.value
        )
        is_completed_item_edit = (
            queue.status == AnnotationQueueStatusChoices.COMPLETED.value
            and item.status == QueueItemStatus.COMPLETED.value
        )
        if (
            queue.status != AnnotationQueueStatusChoices.ACTIVE.value
            and not is_completed_skipped_retry
            and not is_completed_item_edit
        ):
            return self._gm.bad_request(
                "Annotations can only be submitted when the queue is active."
            )

        if _targeted_rework_denies_user(
            item,
            request.user,
            allow_reviewer_override=True,
        ):
            return self._gm.forbidden_response(
                "This item was sent back to a different annotator."
            )

        item_scores = _scores_for_queue_item(item)
        item_has_submitted_scores = item_scores.exists()
        user_has_submitted_score = item_scores.filter(annotator=request.user).exists()
        user_is_assigned_to_item = (
            item.assigned_to_id == request.user.id
            or QueueItemAssignment.objects.filter(
                queue_item=item,
                user=request.user,
                deleted=False,
            ).exists()
        )
        user_can_start_missing_review_annotation = (
            item_has_submitted_scores
            and not user_has_submitted_score
            and (
                user_is_assigned_to_item
                or _has_queue_role(
                    queue_id,
                    request.user,
                    AnnotatorRole.ANNOTATOR.value,
                    AnnotatorRole.MANAGER.value,
                )
            )
        )
        if (
            queue.requires_review
            and item.review_status == "pending_review"
            and not _has_open_rework_for_user(item, request.user)
            and not user_can_start_missing_review_annotation
        ):
            return self._gm.bad_request(
                "This item is waiting for review. It can be edited after a "
                "reviewer requests changes."
            )

        if is_completed_skipped_retry:
            queue.status = AnnotationQueueStatusChoices.ACTIVE.value
            queue.save(update_fields=["status", "updated_at"])

        # Enforce assignment ownership: when auto_assign is False (manual mode),
        # only assigned annotators (or managers/reviewers) may submit.
        # When auto_assign is True, anyone can annotate any item.
        if not item.queue.auto_assign:
            has_assignments = QueueItemAssignment.objects.filter(
                queue_item=item, deleted=False
            ).exists() or bool(item.assigned_to_id)
            if has_assignments:
                is_assigned = (
                    QueueItemAssignment.objects.filter(
                        queue_item=item, user=request.user, deleted=False
                    ).exists()
                    or item.assigned_to_id == request.user.id
                )
                if not is_assigned:
                    is_manager = _has_queue_role(
                        queue_id,
                        request.user,
                        AnnotatorRole.MANAGER.value,
                        AnnotatorRole.REVIEWER.value,
                    )
                    if not is_manager:
                        return self._gm.forbidden_response(
                            "This item is assigned to another annotator."
                        )

        serializer = SubmitAnnotationsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        annotations_data = serializer.validated_data["annotations"]
        legacy_notes = serializer.validated_data.get("notes", "")
        item_notes = serializer.validated_data.get("item_notes")
        submitted = 0

        # Resolve source FK for Score creation
        source_fk_field = SCORE_SOURCE_FK_MAP.get(item.source_type)
        source_obj = getattr(item, source_fk_field) if source_fk_field else None
        span_notes_target = _span_notes_target_for_queue_item(item)
        # Older clients sent one top-level `notes` field. For trace/span items that
        # can store whole-item notes, treat that legacy field as the item note so it
        # never gets copied into every label note by accident.
        if item_notes is None and legacy_notes and span_notes_target is not None:
            item_notes = legacy_notes
            label_notes_fallback = ""
        else:
            label_notes_fallback = legacy_notes

        # Pre-fetch valid label IDs for this queue
        queue_label_ids = set(
            item.queue.queue_labels.filter(deleted=False).values_list(
                "label_id", flat=True
            )
        )

        for ann_data in annotations_data:
            label_id = ann_data["label_id"]
            value = ann_data["value"]

            try:
                label = AnnotationsLabels.objects.get(pk=label_id, deleted=False)
            except AnnotationsLabels.DoesNotExist:
                continue

            # Validate label belongs to this queue
            if label.pk not in queue_label_ids:
                continue

            per_label_notes = (
                ann_data.get("notes", label_notes_fallback) if label.allow_notes else ""
            )

            # Upsert Score (unified annotation primitive)
            # Use no_workspace_objects + _id fields to avoid the LEFT JOIN
            # on nullable workspace FK that triggers PostgreSQL's "FOR UPDATE
            # cannot be applied to the nullable side of an outer join".
            if source_obj and source_fk_field:
                # Scope the upsert by queue_item so each queue review context
                # owns its own Score row even when the same annotator scores
                # the same label across multiple queues.
                score, _ = Score.no_workspace_objects.update_or_create(
                    **{f"{source_fk_field}_id": source_obj.pk},
                    label_id=label.pk,
                    annotator_id=request.user.pk,
                    queue_item=item,
                    deleted=False,
                    defaults={
                        "source_type": item.source_type,
                        "value": value,
                        "score_source": "human",
                        "notes": per_label_notes,
                        "organization": request.organization,
                    },
                )
                submitted += 1

        if item_notes is not None:
            if item_notes:
                QueueItemNote.no_workspace_objects.update_or_create(
                    queue_item=item,
                    annotator=request.user,
                    deleted=False,
                    defaults={
                        "notes": item_notes,
                        "organization": request.organization,
                        "workspace": getattr(request, "workspace", None)
                        or item.workspace,
                    },
                )
            else:
                now = timezone.now()
                QueueItemNote.no_workspace_objects.filter(
                    queue_item=item,
                    annotator=request.user,
                    deleted=False,
                ).update(deleted=True, deleted_at=now, updated_at=now)

        if span_notes_target is not None and item_notes is not None:
            if item_notes:
                SpanNotes.objects.update_or_create(
                    span=span_notes_target,
                    created_by_user=request.user,
                    defaults={
                        "notes": item_notes,
                        "created_by_annotator": request.user.email,
                    },
                )
            else:
                SpanNotes.objects.filter(
                    span=span_notes_target,
                    created_by_user=request.user,
                ).delete()

        # Update item status to in_progress if pending
        if item.status == QueueItemStatus.PENDING.value:
            item.status = QueueItemStatus.IN_PROGRESS.value
            item.save(update_fields=["status", "updated_at"])

        return self._gm.success_response({"submitted": submitted})

    def _maybe_auto_complete_queue(self, queue_id):
        """Auto-complete queue if all items are done (not for default queues)."""
        remaining_count = (
            QueueItem.objects.filter(queue_id=queue_id, deleted=False)
            .exclude(status=QueueItemStatus.COMPLETED.value)
            .count()
        )
        if remaining_count == 0:
            AnnotationQueue.objects.filter(
                pk=queue_id,
                status=AnnotationQueueStatusChoices.ACTIVE.value,
                is_default=False,
            ).update(status=AnnotationQueueStatusChoices.COMPLETED.value)

    @staticmethod
    def _parse_exclude_ids(raw, current_pk=None):
        """Parse exclude IDs from request data (list or comma-separated string)."""
        if isinstance(raw, list):
            exclude_ids = [str(eid).strip() for eid in raw if str(eid).strip()]
        elif isinstance(raw, str) and raw:
            exclude_ids = [eid.strip() for eid in raw.split(",") if eid.strip()]
        else:
            exclude_ids = []
        if current_pk:
            current_id = str(current_pk)
            # Complete/skip uses the last parsed ID as the cursor. Browser
            # history can contain items visited after the current item when a
            # user navigates back, so force the submitted item to be last.
            exclude_ids = [eid for eid in exclude_ids if eid != current_id]
            exclude_ids.append(current_id)
        return exclude_ids

    def _clear_reservation(self, item):
        """Clear reservation fields on an item."""
        item.reserved_by = None
        item.reserved_at = None
        item.reservation_expires_at = None

    @swagger_auto_schema(request_body=QueueItemNavigationRequestSerializer)
    @action(detail=True, methods=["post"], url_path="complete")
    def complete_item(self, request, queue_id=None, pk=None):
        """Mark item as completed and return next pending item."""
        serializer = QueueItemNavigationRequestSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            item = QueueItem.objects.select_related("queue").get(
                pk=pk,
                queue_id=queue_id,
                organization=request.organization,
                deleted=False,
            )
        except QueueItem.DoesNotExist:
            return self._gm.not_found("Queue item not found.")

        queue = item.queue

        if _targeted_rework_denies_user(
            item,
            request.user,
            allow_reviewer_override=True,
        ):
            return self._gm.forbidden_response(
                "This item was sent back to a different annotator."
            )

        # Verify the requesting user has actually annotated this item.
        item_scores = _scores_for_queue_item(item)
        user_has_annotated = item_scores.filter(annotator=request.user).exists()
        if not user_has_annotated:
            return self._gm.bad_request(
                "You must submit annotations before completing."
            )

        # Multi-annotator: every required label must have enough annotator
        # submissions. This keeps reopened items incomplete when a manager adds
        # a new required label after earlier work was already marked complete.
        annotation_count = item_scores.values("annotator").distinct().count()
        has_required_label_coverage = _item_has_required_label_coverage(item)
        if (
            annotation_count >= queue.annotations_required
            and has_required_label_coverage
        ):
            if queue.requires_review:
                if _has_open_rework_for_user(item, request.user):
                    _mark_review_threads_addressed(
                        item,
                        request.user,
                        request.organization,
                        getattr(request, "workspace", None),
                    )
                item.status = QueueItemStatus.IN_PROGRESS.value
                item.review_status = "pending_review"
            else:
                item.status = QueueItemStatus.COMPLETED.value
        else:
            item.status = QueueItemStatus.IN_PROGRESS.value

        # Clear reservation
        self._clear_reservation(item)
        item.save(
            update_fields=[
                "status",
                "review_status",
                "reserved_by",
                "reserved_at",
                "reservation_expires_at",
                "updated_at",
            ]
        )

        self._maybe_auto_complete_queue(queue_id)

        exclude_ids = self._parse_exclude_ids(
            data.get("exclude", []), current_pk=pk
        )

        next_item = self._get_next_pending_item(
            queue_id,
            exclude_ids=exclude_ids or None,
            user=request.user,
            exclude_review_status=data.get("exclude_review_status"),
            include_completed=_is_truthy(data.get("include_completed")),
        )
        next_item_data = QueueItemSerializer(next_item).data if next_item else None

        return self._gm.success_response(
            {
                "completed_item_id": str(pk),
                "next_item": next_item_data,
            }
        )

    @swagger_auto_schema(request_body=QueueItemNavigationRequestSerializer)
    @action(detail=True, methods=["post"], url_path="skip")
    def skip_item(self, request, queue_id=None, pk=None):
        """Mark item as skipped and return next pending item."""
        serializer = QueueItemNavigationRequestSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            item = QueueItem.objects.get(
                pk=pk,
                queue_id=queue_id,
                organization=request.organization,
                deleted=False,
            )
        except QueueItem.DoesNotExist:
            return self._gm.not_found("Queue item not found.")

        if _targeted_rework_denies_user(
            item,
            request.user,
            allow_reviewer_override=True,
        ):
            return self._gm.forbidden_response(
                "This item was sent back to a different annotator."
            )

        if item.queue.requires_review and item.review_status == "pending_review":
            return self._gm.bad_request(
                "This item is waiting for review and cannot be skipped."
            )

        item.status = QueueItemStatus.SKIPPED.value
        self._clear_reservation(item)
        item.save(
            update_fields=[
                "status",
                "reserved_by",
                "reserved_at",
                "reservation_expires_at",
                "updated_at",
            ]
        )

        exclude_ids = self._parse_exclude_ids(
            data.get("exclude", []), current_pk=pk
        )

        next_item = self._get_next_pending_item(
            queue_id,
            exclude_ids=exclude_ids or None,
            user=request.user,
            exclude_review_status=data.get("exclude_review_status"),
            include_completed=_is_truthy(data.get("include_completed")),
        )
        next_item_data = QueueItemSerializer(next_item).data if next_item else None

        return self._gm.success_response(
            {
                "skipped_item_id": str(pk),
                "next_item": next_item_data,
            }
        )

    @swagger_auto_schema(query_serializer=QueueItemNextQuerySerializer)
    @action(detail=False, methods=["get"], url_path="next-item")
    def next_item(self, request, queue_id=None):
        """Get the next or previous item in the queue.

        Query params:
          exclude: comma-separated item IDs to skip
          before:  item ID — returns the item immediately before this one in order
          review_status: optional review status filter (for reviewer queues)
          exclude_review_status: optional review status to omit (for annotator queues)
          include_completed: when true, navigation can visit completed items too
        """
        review_status = request.query_params.get("review_status")
        exclude_review_status = request.query_params.get("exclude_review_status")
        include_completed = _is_truthy(request.query_params.get("include_completed"))
        is_reviewer = _has_queue_role(
            queue_id,
            request.user,
            AnnotatorRole.REVIEWER.value,
            AnnotatorRole.MANAGER.value,
        )
        is_review_navigation = _is_review_workspace_request(
            request,
            is_reviewer=is_reviewer,
            review_status=review_status,
        )
        before_id = request.query_params.get("before")
        if before_id:
            try:
                current = QueueItem.objects.get(
                    pk=before_id, queue_id=queue_id, deleted=False
                )
            except QueueItem.DoesNotExist:
                return self._gm.success_response({"item": None})
            prev_qs = QueueItem.objects.filter(queue_id=queue_id, deleted=False)
            queue = AnnotationQueue.objects.filter(pk=queue_id).first()
            if not include_completed:
                prev_qs = prev_qs.filter(
                    status__in=[
                        QueueItemStatus.SKIPPED.value,
                        QueueItemStatus.PENDING.value,
                        QueueItemStatus.IN_PROGRESS.value,
                    ]
                )
            prev_qs = _apply_review_status_filters_for_user(
                prev_qs,
                review_status=review_status,
                exclude_review_status=exclude_review_status,
                user=request.user,
                is_reviewer=is_review_navigation,
            )
            if (
                queue
                and not queue.auto_assign
                and not is_review_navigation
            ):
                prev_qs = _queue_item_user_scope(
                    prev_qs,
                    request.user,
                    include_unassigned=True,
                )
            if not is_reviewer and review_status != "pending_review":
                prev_qs = _scope_targeted_rework_items(prev_qs, request.user)
            prev_qs = prev_qs.filter(
                Q(reserved_by__isnull=True)
                | Q(reserved_by=request.user)
                | Q(reservation_expires_at__lt=timezone.now())
            )
            prev_item = _queue_items_before_work_cursor(prev_qs, current).first()
            item_data = QueueItemSerializer(prev_item).data if prev_item else None
            return self._gm.success_response({"item": item_data})

        exclude_ids = self._parse_exclude_ids(request.query_params.get("exclude", ""))
        item = self._get_next_pending_item(
            queue_id,
            exclude_ids=exclude_ids or None,
            user=request.user,
            review_status=review_status,
            exclude_review_status=exclude_review_status,
            include_completed=include_completed,
            review_view=is_review_navigation,
        )
        if not item:
            return self._gm.success_response({"item": None})

        item_data = QueueItemSerializer(item).data
        return self._gm.success_response({"item": item_data})

    @swagger_auto_schema(query_serializer=QueueItemAnnotateDetailQuerySerializer)
    @action(detail=True, methods=["get"], url_path="annotate-detail")
    def annotate_detail(self, request, queue_id=None, pk=None):
        """Get full annotation workspace data for an item."""
        try:
            item = QueueItem.objects.select_related(
                "dataset_row",
                "trace",
                "trace__project",
                "observation_span",
                "prototype_run",
                "call_execution",
                "assigned_to",
            ).get(
                pk=pk,
                queue_id=queue_id,
                organization=request.organization,
                deleted=False,
            )
        except QueueItem.DoesNotExist:
            return self._gm.not_found("Queue item not found.")

        queue = item.queue
        now = timezone.now()
        include_completed = _is_truthy(request.query_params.get("include_completed"))
        review_status = request.query_params.get("review_status")
        exclude_review_status = request.query_params.get("exclude_review_status")
        is_reviewer = _has_queue_role(
            queue_id,
            request.user,
            AnnotatorRole.REVIEWER.value,
            AnnotatorRole.MANAGER.value,
        )
        is_review_detail = _is_review_workspace_request(
            request,
            is_reviewer=is_reviewer,
            review_status=review_status,
        )
        if not is_reviewer and _targeted_rework_denies_user(item, request.user):
            return self._gm.forbidden_response(
                "This item was sent back to a different annotator."
            )

        # Reservation logic: opt-in via ?reserve=true query param
        reserve = request.query_params.get("reserve", "").lower() == "true"
        if reserve:
            # Atomic reservation to prevent race condition
            updated = (
                QueueItem.objects.filter(
                    pk=pk,
                    queue_id=queue_id,
                    deleted=False,
                )
                .filter(
                    Q(reserved_by__isnull=True)
                    | Q(reserved_by=request.user)
                    | Q(reservation_expires_at__lt=now)
                )
                .update(
                    reserved_by=request.user,
                    reserved_at=now,
                    reservation_expires_at=now
                    + timedelta(minutes=queue.reservation_timeout_minutes or 60),
                    updated_at=now,
                )
            )
            if not updated:
                return self._gm.bad_request("Item is reserved by another annotator.")
            item.refresh_from_db()

        labels = (
            queue.queue_labels.filter(deleted=False)
            .select_related("label")
            .order_by("order")
        )
        # Review mode compares every annotator's scores. Outside review mode,
        # even reviewer/manager users get their own annotation draft so a
        # multi-role user can still annotate an assigned item.
        annotations_qs = _scores_for_queue_item(item).select_related("label")
        raw_annotator_id = request.query_params.get("annotator_id") or None
        viewing_annotator_id = None
        if raw_annotator_id and is_reviewer:
            try:
                viewing_annotator_id = uuid.UUID(raw_annotator_id)
            except (ValueError, TypeError):
                return self._gm.bad_request("Invalid annotator selection.")

        if viewing_annotator_id:
            annotations_qs = annotations_qs.filter(annotator_id=viewing_annotator_id)
        elif not is_review_detail:
            annotations_qs = annotations_qs.filter(annotator=request.user)
        annotations = annotations_qs
        review_comments = _visible_review_comments(
            item,
            request.user,
            is_reviewer=is_reviewer,
        )
        review_threads = _visible_review_threads(
            item,
            request.user,
            is_reviewer=is_reviewer,
        )

        existing_notes = ""
        span_notes = []
        span_notes_source_id = None
        span_notes_target = _span_notes_target_for_queue_item(item)
        if span_notes_target is not None:
            span_notes_source_id = span_notes_target.id
        span_notes = _item_note_payloads(
            item, span_notes_target, viewer_user_id=request.user.pk
        )
        notes_user_id = viewing_annotator_id or request.user.pk
        existing_notes = _existing_item_note_for_user(item, notes_user_id)

        # Manual queues show overall progress. Distributed queues show the
        # current annotator's slice, so "2/4" means their assigned workload,
        # not the whole team's queue.
        progress_qs = QueueItem.objects.filter(queue_id=queue_id, deleted=False)
        if queue.assignment_strategy != AssignmentStrategy.MANUAL.value:
            scoped_progress_qs = _queue_item_user_scope(
                progress_qs,
                viewing_annotator_id or request.user,
                include_unassigned=False,
            )
            if viewing_annotator_id or scoped_progress_qs.exists():
                progress_qs = scoped_progress_qs
        agg = progress_qs.aggregate(
            total=Count("id"),
            completed=Count("id", filter=Q(status=QueueItemStatus.COMPLETED.value)),
            before_current=Count(
                "id",
                filter=Q(created_at__gt=item.created_at)
                | Q(created_at=item.created_at, id__gt=item.id),
            ),
        )
        total = agg["total"]
        completed = agg["completed"]
        current_position = (agg["before_current"] or 0) + 1

        user_items = _queue_item_user_scope(
            QueueItem.objects.filter(queue_id=queue_id, deleted=False),
            viewing_annotator_id or request.user,
            include_unassigned=queue.assignment_strategy
            == AssignmentStrategy.MANUAL.value,
        )
        user_agg = user_items.aggregate(
            user_total=Count("id", distinct=True),
            user_completed=Count(
                "id",
                distinct=True,
                filter=Q(status=QueueItemStatus.COMPLETED.value),
            ),
        )

        # Adjacent items for prefetching — items the user can annotate
        # (assigned to them, or unassigned). Reviewers/managers also get
        # items assigned to others, so they can navigate the full queue
        # in view-only mode.
        if is_review_detail:
            annotatable_qs = QueueItem.objects.filter(queue_id=queue_id, deleted=False)
        else:
            annotatable_qs = _queue_item_user_scope(
                QueueItem.objects.filter(queue_id=queue_id, deleted=False),
                request.user,
                include_unassigned=True,
            )
        if not include_completed:
            annotatable_qs = annotatable_qs.filter(
                status__in=[
                    QueueItemStatus.SKIPPED.value,
                    QueueItemStatus.PENDING.value,
                    QueueItemStatus.IN_PROGRESS.value,
                ]
            )
        annotatable_qs = _apply_review_status_filters_for_user(
            annotatable_qs,
            review_status=review_status,
            exclude_review_status=exclude_review_status,
            user=request.user,
            is_reviewer=is_review_detail,
        )
        if not is_reviewer and review_status != "pending_review":
            annotatable_qs = _scope_targeted_rework_items(
                annotatable_qs,
                request.user,
            )
        next_item = (
            _queue_items_after_work_cursor(annotatable_qs, item)
            .values_list("id", flat=True)
            .first()
        )
        prev_item = (
            _queue_items_before_work_cursor(annotatable_qs, item)
            .values_list("id", flat=True)
            .first()
        )

        data = {
            "item": item,
            "queue": queue,
            "labels": labels,
            "annotations": annotations,
            "review_comments": review_comments,
            "review_threads": review_threads,
            "existing_notes": existing_notes,
            "span_notes": span_notes,
            "span_notes_source_id": span_notes_source_id,
            "progress": {
                "total": total,
                "completed": completed,
                "current_position": current_position,
                "user_progress": {
                    "total": user_agg["user_total"],
                    "completed": user_agg["user_completed"],
                },
            },
            "next_item_id": str(next_item) if next_item else None,
            "prev_item_id": str(prev_item) if prev_item else None,
        }

        serializer = AnnotateDetailSerializer(data, context={"request": request})
        return self._gm.success_response(serializer.data)

    @swagger_auto_schema(request_body=AssignItemsSerializer)
    @action(detail=False, methods=["post"], url_path="assign")
    def assign_items(self, request, queue_id=None):
        """Assign items to one or more annotators.

        Accepts:
          item_ids: list of item UUIDs (required)
          user_ids: list of user UUIDs to assign (use this for multi-assign)
          user_id:  single user UUID (legacy compat, treated as user_ids=[user_id])
          action:   "add" (default) | "set" | "remove"
                    add    — add users to existing assignments
                    set    — replace all assignments with the given users
                    remove — remove given users from assignments
                    If user_ids is empty with action="set", clears all assignments.
        """
        queue = self._get_queue_for_management(queue_id, request)
        if queue is None:
            return self._gm.not_found("Queue not found.")

        serializer = AssignItemsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item_ids = serializer.validated_data["item_ids"]
        user_ids = serializer.validated_data.get("user_ids", [])
        user_id = serializer.validated_data.get("user_id")
        action = serializer.validated_data.get("action", "add")

        # Legacy compat: single user_id
        if user_id is not None and not user_ids:
            user_ids = [user_id]
            if action == "add":
                action = "set"  # legacy single-assign was a full replace

        if not item_ids:
            return self._gm.bad_request("item_ids is required.")

        is_manager = _is_queue_manager(queue, request.user)
        if not is_manager:
            user_ids_as_strings = {str(uid) for uid in user_ids}
            is_self_assignment = (
                action in {"add", "set"}
                and user_ids_as_strings == {str(request.user.id)}
                and _has_queue_role(
                    queue_id,
                    request.user,
                    AnnotatorRole.ANNOTATOR.value,
                    AnnotatorRole.MANAGER.value,
                )
            )
            if not is_self_assignment:
                denied = self._require_queue_manager(queue, request)
                if denied is not None:
                    return denied
            elif queue.auto_assign:
                return self._gm.bad_request(
                    "Auto-assign queues do not need manual item assignment."
                )
            else:
                requested_items = QueueItem.objects.filter(
                    id__in=item_ids,
                    queue_id=queue_id,
                    organization=request.organization,
                    deleted=False,
                )
                assigned_to_other = requested_items.exclude(
                    Q(assigned_to__isnull=True) | Q(assigned_to=request.user)
                ).exists()
                has_other_assignment = QueueItemAssignment.objects.filter(
                    queue_item__in=requested_items,
                    deleted=False,
                ).exclude(user=request.user).exists()
                if assigned_to_other or has_other_assignment:
                    return self._gm.forbidden_response(
                        "Only queue managers can change items assigned to another annotator."
                    )

        # Handle unassign (user_id=null with no user_ids)
        if user_id is None and not user_ids and action == "set":
            # Clear all assignments for these items
            items = QueueItem.objects.filter(
                id__in=item_ids,
                queue_id=queue_id,
                organization=request.organization,
                deleted=False,
            )
            QueueItemAssignment.objects.filter(
                queue_item__in=items, deleted=False
            ).update(deleted=True)
            # Also clear legacy FK
            items.update(assigned_to_id=None)
            return self._gm.success_response({"assigned": 0})

        # Validate all user_ids
        queue_member_ids = set(
            AnnotationQueueAnnotator.objects.filter(
                queue_id=queue_id, deleted=False
            ).values_list("user_id", flat=True)
        )
        for uid in user_ids:
            if uid not in queue_member_ids and str(uid) not in {
                str(mid) for mid in queue_member_ids
            }:
                return self._gm.bad_request(
                    f"User {uid} is not an annotator in this queue."
                )

        items = QueueItem.objects.filter(
            id__in=item_ids,
            queue_id=queue_id,
            organization=request.organization,
            deleted=False,
        )
        item_pks = list(items.values_list("pk", flat=True))

        if action == "set":
            # Soft-delete existing assignments not in new set
            QueueItemAssignment.objects.filter(
                queue_item_id__in=item_pks, deleted=False
            ).exclude(user_id__in=user_ids).update(deleted=True)

        if action == "remove":
            QueueItemAssignment.objects.filter(
                queue_item_id__in=item_pks,
                user_id__in=user_ids,
                deleted=False,
            ).update(deleted=True)
        else:
            # add or set — create assignments
            existing = set(
                QueueItemAssignment.objects.filter(
                    queue_item_id__in=item_pks,
                    user_id__in=user_ids,
                    deleted=False,
                ).values_list("queue_item_id", "user_id")
            )
            to_create = []
            for item_pk in item_pks:
                for uid in user_ids:
                    if (item_pk, uid) not in existing:
                        to_create.append(
                            QueueItemAssignment(queue_item_id=item_pk, user_id=uid)
                        )
            if to_create:
                QueueItemAssignment.objects.bulk_create(
                    to_create, ignore_conflicts=True
                )
            # Also restore any soft-deleted assignments
            QueueItemAssignment.objects.filter(
                queue_item_id__in=item_pks,
                user_id__in=user_ids,
                deleted=True,
            ).update(deleted=False, deleted_at=None)

        # Update legacy FK to first assigned user (backward compat)
        for item_pk in item_pks:
            first_assignment = (
                QueueItemAssignment.objects.filter(queue_item_id=item_pk, deleted=False)
                .values_list("user_id", flat=True)
                .first()
            )
            QueueItem.objects.filter(pk=item_pk).update(assigned_to_id=first_assignment)

        return self._gm.success_response({"assigned": len(item_pks) * len(user_ids)})

    @swagger_auto_schema(request_body=no_body)
    @action(detail=True, methods=["post"], url_path="release")
    def release_reservation(self, request, queue_id=None, pk=None):
        """Release reservation on an item."""
        try:
            item = QueueItem.objects.get(
                pk=pk,
                queue_id=queue_id,
                organization=request.organization,
                deleted=False,
            )
        except QueueItem.DoesNotExist:
            return self._gm.not_found("Queue item not found.")

        if item.reserved_by and item.reserved_by != request.user:
            return self._gm.bad_request("You can only release your own reservation.")

        self._clear_reservation(item)
        item.save(
            update_fields=[
                "reserved_by",
                "reserved_at",
                "reservation_expires_at",
                "updated_at",
            ]
        )
        return self._gm.success_response({"released": True})

    @action(detail=True, methods=["get"], url_path="annotations")
    def annotations_list(self, request, queue_id=None, pk=None):
        """List all annotations for a queue item (across all annotators)."""
        try:
            item = QueueItem.objects.get(
                pk=pk,
                queue_id=queue_id,
                organization=request.organization,
                deleted=False,
            )
        except QueueItem.DoesNotExist:
            return self._gm.not_found("Queue item not found.")

        annotations = (
            _scores_for_queue_item(item)
            .select_related("annotator", "label")
            .order_by("-created_at")
        )
        serializer = ScoreSerializer(annotations, many=True)
        return self._gm.success_response(serializer.data)

    def _discussion_item_and_role(self, request, queue_id, pk):
        try:
            item = QueueItem.objects.select_related("queue").get(
                pk=pk,
                queue_id=queue_id,
                organization=request.organization,
                deleted=False,
            )
        except QueueItem.DoesNotExist:
            return None, False, self._gm.not_found("Queue item not found.")

        is_queue_member = _has_queue_role(
            queue_id,
            request.user,
            AnnotatorRole.ANNOTATOR.value,
            AnnotatorRole.REVIEWER.value,
            AnnotatorRole.MANAGER.value,
        )
        if not is_queue_member:
            return (
                None,
                False,
                self._gm.forbidden_response(
                    "Only queue members can discuss this item."
                ),
            )

        is_reviewer = _has_queue_role(
            queue_id,
            request.user,
            AnnotatorRole.REVIEWER.value,
            AnnotatorRole.MANAGER.value,
        )
        return item, is_reviewer, None

    def _set_discussion_thread_status(
        self,
        request,
        *,
        queue_id,
        pk,
        thread_id,
        next_status,
    ):
        item, is_reviewer, error = self._discussion_item_and_role(
            request,
            queue_id,
            pk,
        )
        if error:
            return error

        try:
            thread_uuid = uuid.UUID(str(thread_id))
        except (TypeError, ValueError):
            return self._gm.bad_request("Invalid discussion thread.")

        thread = (
            _visible_review_threads(item, request.user, is_reviewer=is_reviewer)
            .filter(id=thread_uuid)
            .first()
        )
        if thread is None:
            return self._gm.not_found("Discussion thread not found.")
        if thread.blocking and not is_reviewer:
            return self._gm.forbidden_response(
                "Only reviewers or managers can resolve blocking review threads."
            )
        if not thread.blocking and not _can_manage_discussion_thread(
            thread,
            request.user,
            is_reviewer=is_reviewer,
        ):
            return self._gm.forbidden_response(
                "Only reviewers, managers, or the thread creator can resolve or reopen this thread."
            )

        now = timezone.now()
        update_fields = ["status", "updated_at"]
        if next_status == QueueItemReviewThread.STATUS_RESOLVED:
            thread.status = QueueItemReviewThread.STATUS_RESOLVED
            thread.resolved_by = request.user
            thread.resolved_at = now
            action = QueueItemReviewComment.ACTION_RESOLVE
            default_comment = "Resolved."
            update_fields.extend(["resolved_by", "resolved_at"])
        else:
            thread.status = QueueItemReviewThread.STATUS_REOPENED
            thread.reopened_by = request.user
            thread.reopened_at = now
            action = QueueItemReviewComment.ACTION_REOPEN
            default_comment = "Reopened."
            update_fields.extend(["reopened_by", "reopened_at"])
        thread.save(update_fields=update_fields)

        status_comment = QueueItemReviewComment.objects.create(
            thread=thread,
            queue_item=item,
            reviewer=request.user,
            label=thread.label,
            target_annotator=thread.target_annotator,
            action=action,
            comment=str(request.data.get("comment") or default_comment).strip()
            or default_comment,
            organization=request.organization,
            workspace=getattr(request, "workspace", None),
        )
        _notify_annotation_discussion(item, status_comment, thread)

        return self._gm.success_response(
            _discussion_payload(
                item,
                request,
                is_reviewer=is_reviewer,
                comment=status_comment,
                thread=thread,
            )
        )

    @swagger_auto_schema(method="post", request_body=DiscussionCommentRequestSerializer)
    @action(detail=True, methods=["get", "post"], url_path="discussion")
    def discussion(self, request, queue_id=None, pk=None):
        """List or create non-blocking discussion comments for a queue item."""
        item, is_reviewer, error = self._discussion_item_and_role(
            request,
            queue_id,
            pk,
        )
        if error:
            return error

        if request.method == "GET":
            payload = _discussion_payload(item, request, is_reviewer=is_reviewer)
            search = (request.query_params.get("search") or "").strip()
            if search:
                comments = _visible_review_comments(
                    item,
                    request.user,
                    is_reviewer=is_reviewer,
                ).filter(action=QueueItemReviewComment.ACTION_COMMENT)
                comments = comments.filter(
                    Q(comment__icontains=search)
                    | Q(reviewer__name__icontains=search)
                    | Q(reviewer__email__icontains=search)
                    | Q(target_annotator__name__icontains=search)
                    | Q(target_annotator__email__icontains=search)
                    | Q(label__name__icontains=search)
                )
                payload["review_comments"] = QueueItemReviewCommentSerializer(
                    comments,
                    many=True,
                    context={"request": request},
                ).data
            return self._gm.success_response(payload)

        serializer = DiscussionCommentRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        comment = str(data.get("comment") or data.get("content") or "").strip()
        if not comment:
            return self._gm.bad_request("Comment text is required.")

        queue_labels = {
            str(queue_label.label_id): queue_label.label
            for queue_label in item.queue.queue_labels.filter(
                deleted=False
            ).select_related("label")
        }
        label = None
        label_id = data.get("label_id") or data.get("label")
        if label_id:
            label = queue_labels.get(str(label_id))
            if not label:
                return self._gm.bad_request(
                    f"Label {label_id} is not part of this queue."
                )

        member_ids = _queue_member_ids(queue_id)
        target_annotator = None
        target_annotator_id = data.get("target_annotator_id")
        if target_annotator_id:
            try:
                target_uuid = uuid.UUID(str(target_annotator_id))
            except (TypeError, ValueError):
                return self._gm.bad_request("Invalid target annotator.")
            if str(target_uuid) not in member_ids:
                return self._gm.bad_request(
                    "Target annotator must be a member of this queue."
                )
            if not is_reviewer and str(target_uuid) != str(request.user.id):
                return self._gm.forbidden_response(
                    "Only reviewers or managers can target comments to another annotator."
                )
            target_annotator = User.objects.filter(pk=target_uuid).first()
            if target_annotator is None:
                return self._gm.bad_request("Target annotator not found.")

        thread = None
        thread_id = data.get("thread_id") or data.get("thread")
        if thread_id:
            try:
                thread_uuid = uuid.UUID(str(thread_id))
            except (TypeError, ValueError):
                return self._gm.bad_request("Invalid discussion thread.")
            thread = (
                _visible_review_threads(item, request.user, is_reviewer=is_reviewer)
                .filter(id=thread_uuid)
                .first()
            )
            if thread is None:
                return self._gm.not_found("Discussion thread not found.")
            if thread.blocking and not is_reviewer:
                return self._gm.forbidden_response(
                    "Only reviewers or managers can reply to blocking review threads."
                )
            label = thread.label
            target_annotator = thread.target_annotator

        raw_mentions = data.get("mentioned_user_ids")
        if raw_mentions is None:
            raw_mentions = data.get("mentions", [])
        if isinstance(raw_mentions, str):
            raw_mentions = [raw_mentions]
        if not isinstance(raw_mentions, list):
            return self._gm.bad_request("mentioned_user_ids must be a list.")

        mention_ids, mention_emails = _split_mention_references(raw_mentions)
        mention_ids.update(_extract_mentioned_user_ids(comment))
        mention_emails.update(_extract_mentioned_emails(comment))
        mention_ids.update(_queue_member_ids_from_emails(queue_id, mention_emails))
        if target_annotator is not None:
            mention_ids.add(str(target_annotator.id))
        try:
            mentioned_users = _queue_members_from_ids(queue_id, mention_ids)
        except ValueError as exc:
            return self._gm.bad_request(str(exc))

        if thread is not None:
            if thread.status == QueueItemReviewThread.STATUS_RESOLVED:
                if not _can_manage_discussion_thread(
                    thread,
                    request.user,
                    is_reviewer=is_reviewer,
                ):
                    return self._gm.forbidden_response(
                        "Only reviewers, managers, or the thread creator can reopen this thread."
                    )
                reopened_at = timezone.now()
                thread.status = QueueItemReviewThread.STATUS_REOPENED
                thread.reopened_by = request.user
                thread.reopened_at = reopened_at
                thread.save(
                    update_fields=[
                        "status",
                        "reopened_by",
                        "reopened_at",
                        "updated_at",
                    ]
                )
                QueueItemReviewComment.objects.create(
                    thread=thread,
                    queue_item=item,
                    reviewer=request.user,
                    label=label,
                    target_annotator=target_annotator,
                    action=QueueItemReviewComment.ACTION_REOPEN,
                    comment="Reopened by reply.",
                    organization=request.organization,
                    workspace=getattr(request, "workspace", None),
                )
            created_comment = QueueItemReviewComment.objects.create(
                thread=thread,
                queue_item=item,
                reviewer=request.user,
                label=label,
                target_annotator=target_annotator,
                action=QueueItemReviewComment.ACTION_COMMENT,
                comment=comment,
                organization=request.organization,
                workspace=getattr(request, "workspace", None),
            )
            if mentioned_users:
                created_comment.mentioned_users.set(mentioned_users)
        else:
            created_comment = _create_review_thread_comment(
                item=item,
                reviewer=request.user,
                label=label,
                target_annotator=target_annotator,
                action=QueueItemReviewComment.ACTION_COMMENT,
                comment=comment,
                organization=request.organization,
                workspace=getattr(request, "workspace", None),
                mentioned_users=mentioned_users,
                blocking=False,
                status=QueueItemReviewThread.STATUS_OPEN,
            )
            thread = created_comment.thread

        _notify_annotation_discussion(item, created_comment, thread)

        return self._gm.success_response(
            _discussion_payload(
                item,
                request,
                is_reviewer=is_reviewer,
                comment=created_comment,
                thread=thread,
            )
        )

    @swagger_auto_schema(request_body=DiscussionThreadStatusRequestSerializer)
    @action(
        detail=True,
        methods=["post"],
        url_path=r"discussion/(?P<thread_id>[^/.]+)/resolve",
    )
    def resolve_discussion_thread(self, request, queue_id=None, pk=None, thread_id=None):
        return self._set_discussion_thread_status(
            request,
            queue_id=queue_id,
            pk=pk,
            thread_id=thread_id,
            next_status=QueueItemReviewThread.STATUS_RESOLVED,
        )

    @swagger_auto_schema(request_body=DiscussionThreadStatusRequestSerializer)
    @action(
        detail=True,
        methods=["post"],
        url_path=r"discussion/(?P<thread_id>[^/.]+)/reopen",
    )
    def reopen_discussion_thread(self, request, queue_id=None, pk=None, thread_id=None):
        return self._set_discussion_thread_status(
            request,
            queue_id=queue_id,
            pk=pk,
            thread_id=thread_id,
            next_status=QueueItemReviewThread.STATUS_REOPENED,
        )

    @swagger_auto_schema(request_body=DiscussionReactionRequestSerializer)
    @action(
        detail=True,
        methods=["post"],
        url_path=r"discussion/comments/(?P<comment_id>[^/.]+)/reaction",
    )
    def discussion_comment_reaction(
        self,
        request,
        queue_id=None,
        pk=None,
        comment_id=None,
    ):
        """Toggle the current user's reaction on a discussion comment."""
        item, is_reviewer, error = self._discussion_item_and_role(
            request,
            queue_id,
            pk,
        )
        if error:
            return error

        try:
            comment_uuid = uuid.UUID(str(comment_id))
        except (TypeError, ValueError):
            return self._gm.bad_request("Invalid discussion comment.")

        comment = (
            _visible_review_comments(item, request.user, is_reviewer=is_reviewer)
            .filter(id=comment_uuid, action=QueueItemReviewComment.ACTION_COMMENT)
            .first()
        )
        if comment is None:
            return self._gm.not_found("Discussion comment not found.")

        serializer = DiscussionReactionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        emoji = str(
            serializer.validated_data.get("emoji")
            or serializer.validated_data.get("reaction")
            or ""
        )
        if not _is_supported_discussion_reaction(emoji):
            return self._gm.bad_request("Unsupported reaction emoji.")

        user_id = str(request.user.id)
        reactions = dict(comment.reactions or {})
        user_ids = {str(value) for value in reactions.get(emoji, [])}
        if user_id in user_ids:
            user_ids.remove(user_id)
        else:
            user_ids.add(user_id)
        if user_ids:
            reactions[emoji] = sorted(user_ids)
        else:
            reactions.pop(emoji, None)
        comment.reactions = reactions
        comment.save(update_fields=["reactions", "updated_at"])

        return self._gm.success_response(
            _discussion_payload(
                item,
                request,
                is_reviewer=is_reviewer,
                comment=comment,
                thread=comment.thread,
            )
        )

    @swagger_auto_schema(request_body=ReviewItemRequestSerializer)
    @action(detail=True, methods=["post"], url_path="review")
    def review_item(self, request, queue_id=None, pk=None):
        """Approve, request changes, or leave reviewer feedback on an item."""
        try:
            from ee.usage.services.entitlements import Entitlements
        except ImportError:
            Entitlements = None

        if Entitlements is not None:
            org = getattr(request, "organization", None) or request.user.organization
            feat_check = Entitlements.check_feature(
                str(org.id),
                "has_review_workflow",
            )
            if not feat_check.allowed:
                return self._gm.forbidden_response(feat_check.reason)

        # Verify requesting user has reviewer or manager role
        if not _has_queue_role(
            queue_id,
            request.user,
            AnnotatorRole.REVIEWER.value,
            AnnotatorRole.MANAGER.value,
        ):
            return self._gm.forbidden_response(
                "Only reviewers or managers can review items."
            )

        try:
            item = QueueItem.objects.select_related("queue").get(
                pk=pk,
                queue_id=queue_id,
                organization=request.organization,
                deleted=False,
            )
        except QueueItem.DoesNotExist:
            return self._gm.not_found("Queue item not found.")

        serializer = ReviewItemRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        requested_action = data.get("action")
        action_aliases = {
            "approve": QueueItemReviewComment.ACTION_APPROVE,
            "reject": QueueItemReviewComment.ACTION_REQUEST_CHANGES,
            "request_changes": QueueItemReviewComment.ACTION_REQUEST_CHANGES,
            "comment": QueueItemReviewComment.ACTION_COMMENT,
        }
        review_action = action_aliases.get(requested_action)
        if review_action is None:
            return self._gm.bad_request(
                "action must be 'approve', 'request_changes', 'reject', or 'comment'."
            )
        if (
            review_action
            in (
                QueueItemReviewComment.ACTION_APPROVE,
                QueueItemReviewComment.ACTION_REQUEST_CHANGES,
            )
            and item.review_status != "pending_review"
        ):
            return self._gm.bad_request(
                "Only items pending review can be approved or sent back."
            )

        notes = str(data.get("notes") or "").strip()
        raw_label_comments = data.get("label_comments") or []
        if not isinstance(raw_label_comments, list):
            return self._gm.bad_request("label_comments must be a list.")

        queue_labels = {
            str(queue_label.label_id): queue_label.label
            for queue_label in item.queue.queue_labels.filter(
                deleted=False
            ).select_related("label")
        }
        queue_member_ids = {
            str(user_id)
            for user_id in AnnotationQueueAnnotator.objects.filter(
                queue_id=queue_id,
                deleted=False,
            ).values_list("user_id", flat=True)
        }
        item_scores = _scores_for_queue_item(item)

        label_comments = []
        for raw_comment in raw_label_comments:
            if not isinstance(raw_comment, dict):
                return self._gm.bad_request("Each label comment must be an object.")

            comment = str(
                raw_comment.get("comment") or raw_comment.get("notes") or ""
            ).strip()
            if not comment:
                continue

            label_id = raw_comment.get("label_id") or raw_comment.get("label")
            if not label_id:
                return self._gm.bad_request("label_id is required for label comments.")
            label = queue_labels.get(str(label_id))
            if not label:
                return self._gm.bad_request(
                    f"Label {label_id} is not part of this queue."
                )

            target_annotator = None
            target_annotator_id = raw_comment.get(
                "target_annotator_id"
            ) or raw_comment.get("annotator_id")
            if not target_annotator_id:
                return self._gm.bad_request(
                    "target_annotator_id is required for label feedback."
                )
            try:
                target_uuid = uuid.UUID(str(target_annotator_id))
            except (TypeError, ValueError):
                return self._gm.bad_request("Invalid target annotator.")
            if str(target_uuid) not in queue_member_ids:
                return self._gm.bad_request(
                    "Target annotator must be a member of this queue."
                )
            target_annotator = User.objects.filter(pk=target_uuid).first()
            if target_annotator is None:
                return self._gm.bad_request("Target annotator not found.")
            if not item_scores.filter(
                label=label,
                annotator=target_annotator,
            ).exists():
                return self._gm.bad_request(
                    "Target annotator has not submitted this label."
                )

            label_comments.append(
                {
                    "label": label,
                    "target_annotator": target_annotator,
                    "comment": comment,
                }
            )

        has_feedback = bool(notes or label_comments)
        if (
            review_action == QueueItemReviewComment.ACTION_REQUEST_CHANGES
            and not has_feedback
        ):
            return self._gm.bad_request("Feedback is required when requesting changes.")
        if review_action == QueueItemReviewComment.ACTION_COMMENT and not has_feedback:
            return self._gm.bad_request("Comment text is required.")
        if not item_scores.exists():
            return self._gm.bad_request(
                "Review requires at least one submitted annotation."
            )

        now = timezone.now()
        workspace = getattr(request, "workspace", None)
        comments_to_notify = []

        if review_action == QueueItemReviewComment.ACTION_APPROVE:
            open_blocking_threads = _open_blocking_review_threads(item)
            if open_blocking_threads.exists():
                return self._gm.bad_request(
                    "All requested changes must be addressed before approval."
                )
            item.status = QueueItemStatus.COMPLETED.value
            item.review_status = "approved"
            QueueItemReviewThread.objects.filter(
                queue_item=item,
                status__in=[
                    QueueItemReviewThread.STATUS_OPEN,
                    QueueItemReviewThread.STATUS_REOPENED,
                    QueueItemReviewThread.STATUS_ADDRESSED,
                ],
                deleted=False,
            ).update(
                status=QueueItemReviewThread.STATUS_RESOLVED,
                resolved_by=request.user,
                resolved_at=now,
                updated_at=now,
            )
        elif review_action == QueueItemReviewComment.ACTION_REQUEST_CHANGES:
            # Score-specific feedback should only route work back to the target
            # annotator. Whole-item feedback keeps the legacy rejected status.
            item.status = QueueItemStatus.IN_PROGRESS.value
            item.review_status = "pending_review" if label_comments else "rejected"

        if notes:
            overall_comment = _create_review_thread_comment(
                item=item,
                reviewer=request.user,
                action=review_action,
                comment=notes,
                organization=request.organization,
                workspace=workspace,
                # If score-specific feedback exists, the overall note is context.
                # Otherwise it is the blocking review issue for the whole item.
                blocking=(
                    review_action == QueueItemReviewComment.ACTION_REQUEST_CHANGES
                    and not label_comments
                ),
                status=(
                    QueueItemReviewThread.STATUS_RESOLVED
                    if review_action == QueueItemReviewComment.ACTION_APPROVE
                    else QueueItemReviewThread.STATUS_OPEN
                ),
            )
            if not label_comments:
                comments_to_notify.append(overall_comment)
        elif review_action == QueueItemReviewComment.ACTION_APPROVE:
            approve_comment = _create_review_thread_comment(
                item=item,
                reviewer=request.user,
                action=review_action,
                comment="Approved.",
                organization=request.organization,
                workspace=workspace,
                blocking=False,
                status=QueueItemReviewThread.STATUS_RESOLVED,
            )
            comments_to_notify.append(approve_comment)

        for label_comment in label_comments:
            created_label_comment = _create_review_thread_comment(
                item=item,
                reviewer=request.user,
                label=label_comment["label"],
                target_annotator=label_comment["target_annotator"],
                action=review_action,
                comment=label_comment["comment"],
                organization=request.organization,
                workspace=workspace,
                blocking=(
                    review_action == QueueItemReviewComment.ACTION_REQUEST_CHANGES
                ),
                status=(
                    QueueItemReviewThread.STATUS_OPEN
                    if review_action == QueueItemReviewComment.ACTION_REQUEST_CHANGES
                    else QueueItemReviewThread.STATUS_RESOLVED
                ),
            )
            comments_to_notify.append(created_label_comment)

        if review_action != QueueItemReviewComment.ACTION_COMMENT:
            item.reviewed_by = request.user
            item.reviewed_at = now
            if notes:
                item.review_notes = notes
            elif label_comments:
                count = len(label_comments)
                suffix = "score" if count == 1 else "scores"
                item.review_notes = (
                    f"Reviewer requested changes on {count} annotation {suffix}."
                )
            else:
                item.review_notes = ""
            self._clear_reservation(item)
            item.save(
                update_fields=[
                    "status",
                    "review_status",
                    "reviewed_by",
                    "reviewed_at",
                    "review_notes",
                    "reserved_by",
                    "reserved_at",
                    "reservation_expires_at",
                    "updated_at",
                ]
            )

        for created_comment in comments_to_notify:
            _notify_annotation_discussion(
                item,
                created_comment,
                created_comment.thread,
            )

        # Auto-complete queue check
        if review_action == QueueItemReviewComment.ACTION_APPROVE:
            self._maybe_auto_complete_queue(queue_id)

        next_review_status = "pending_review" if item.queue.requires_review else None
        next_item = self._get_next_pending_item(
            queue_id,
            exclude_id=pk,
            user=request.user,
            review_status=next_review_status,
        )
        next_item_data = QueueItemSerializer(next_item).data if next_item else None
        review_comments = _visible_review_comments(
            item,
            request.user,
            is_reviewer=True,
        )
        review_threads = _visible_review_threads(
            item,
            request.user,
            is_reviewer=True,
        )

        return self._gm.success_response(
            {
                "reviewed_item_id": str(pk),
                "action": review_action,
                "next_item": next_item_data,
                "review_comments": QueueItemReviewCommentSerializer(
                    review_comments,
                    many=True,
                    context={"request": request},
                ).data,
                "review_threads": QueueItemReviewThreadSerializer(
                    review_threads,
                    many=True,
                    context={"request": request},
                ).data,
            }
        )

    @swagger_auto_schema(request_body=ImportAnnotationsSerializer)
    @action(detail=True, methods=["post"], url_path="annotations/import")
    def import_annotations(self, request, queue_id=None, pk=None):
        """Import annotations from external sources."""
        try:
            item = QueueItem.objects.get(
                pk=pk,
                queue_id=queue_id,
                organization=request.organization,
                deleted=False,
            )
        except QueueItem.DoesNotExist:
            return self._gm.not_found("Queue item not found.")

        annotations_data = request.data.get("annotations", [])
        annotator_id = request.data.get("annotator_id")

        if not annotations_data:
            return self._gm.bad_request("annotations list is required.")

        annotator = request.user
        if annotator_id:
            try:
                workspace = getattr(request, "workspace", None)
                user_qs = User.objects.filter(pk=annotator_id)
                if workspace:
                    user_qs = user_qs.filter(
                        workspace_memberships__workspace=workspace,
                        workspace_memberships__is_active=True,
                    )
                else:
                    user_qs = user_qs.filter(
                        Q(organization=request.organization)
                        | Q(
                            organization_memberships__organization=request.organization,
                            organization_memberships__is_active=True,
                        )
                    )
                annotator = user_qs.distinct().get()
            except User.DoesNotExist:
                return self._gm.bad_request("Annotator not found in this workspace.")

        # Resolve source FK for Score creation
        source_fk_field = SCORE_SOURCE_FK_MAP.get(item.source_type)
        source_obj = getattr(item, source_fk_field) if source_fk_field else None

        imported = 0
        valid_sources = {c.value for c in ScoreSource}
        for ann_data in annotations_data:
            label_id = ann_data.get("label_id")
            value = ann_data.get("value")
            score_source = ann_data.get("score_source", "imported")

            if not label_id or value is None:
                continue

            # Validate score_source against allowed choices
            if score_source not in valid_sources:
                continue

            try:
                label = AnnotationsLabels.objects.get(pk=label_id, deleted=False)
            except AnnotationsLabels.DoesNotExist:
                continue

            if source_obj and source_fk_field:
                # Scope the upsert by queue_item so an import lands in this
                # queue's review context. Without queue_item in the lookup
                # we'd hit the per-queue uniqueness constraint as soon as
                # the same (source, label, annotator) is imported into a
                # second queue.
                score, _ = Score.no_workspace_objects.update_or_create(
                    **{f"{source_fk_field}_id": source_obj.pk},
                    label_id=label.pk,
                    annotator_id=annotator.pk,
                    queue_item=item,
                    deleted=False,
                    defaults={
                        "source_type": item.source_type,
                        "value": value,
                        "score_source": score_source or "human",
                        "notes": "",
                        "organization": request.organization,
                    },
                )
                imported += 1

        return self._gm.success_response({"imported": imported})


class AutomationRuleViewSet(BaseModelViewSetMixinWithUserOrg, viewsets.ModelViewSet):
    serializer_class = AutomationRuleSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = ExtendedPageNumberPagination
    queryset = AutomationRule.objects.all()
    _gm = GeneralMethods()

    def _queue_manager_error(self, request, queue_id=None):
        org = getattr(request, "organization", None) or request.user.organization
        try:
            queue = AnnotationQueue.objects.get(
                pk=queue_id or self.kwargs.get("queue_id"),
                organization=org,
                deleted=False,
            )
        except AnnotationQueue.DoesNotExist:
            return self._gm.not_found("Queue not found.")

        if not _is_queue_manager(queue, request.user):
            return self._gm.forbidden_response(
                "Only queue managers can manage automation rules."
            )

        return None

    def get_queryset(self):
        queryset = super().get_queryset().select_related("created_by")
        queue_id = self.kwargs.get("queue_id")
        if queue_id:
            queryset = queryset.filter(queue_id=queue_id)
        return queryset.order_by("-created_at")

    def create(self, request, *args, **kwargs):
        manager_error = self._queue_manager_error(request)
        if manager_error:
            return manager_error

        # Entitlement check: can this org create more automation rules?
        try:
            try:
                from ee.usage.services.entitlements import Entitlements
            except ImportError:
                Entitlements = None

            org = getattr(request, "organization", None) or request.user.organization
            current_count = AutomationRule.objects.filter(
                organization=org, deleted=False
            ).count()
            check = Entitlements.can_create(
                str(org.id), "automation_rules", current_count
            )
            if not check.allowed:
                return self._gm.forbidden_response(check.reason)
        except ImportError:
            pass

        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        manager_error = self._queue_manager_error(request)
        if manager_error:
            return manager_error
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        manager_error = self._queue_manager_error(request)
        if manager_error:
            return manager_error
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        manager_error = self._queue_manager_error(request)
        if manager_error:
            return manager_error
        return super().destroy(request, *args, **kwargs)

    def perform_create(self, serializer):
        queue_id = self.kwargs.get("queue_id")
        org = (
            getattr(self.request, "organization", None)
            or self.request.user.organization
        )
        queue = AnnotationQueue.objects.get(
            pk=queue_id,
            organization=org,
            deleted=False,
        )
        serializer.save(
            queue=queue,
            organization=org,
            created_by=self.request.user,
        )

    @swagger_auto_schema(request_body=no_body)
    @action(detail=True, methods=["post"], url_path="evaluate")
    def evaluate(self, request, queue_id=None, pk=None):
        """Trigger a manual rule run with a sync-or-async branch.

        Small runs (filter resolves to ≤ ``RULE_RUN_SYNC_THRESHOLD``) finish
        in the HTTP request and return 200 with the result — fast feedback
        for the common case. Large runs (mostly first-ever runs on backlogs
        or rules with wide filters) hand the work to a Temporal activity and
        return 202 immediately. The activity emails creator + queue managers
        on completion.

        The peek is a cheap dry-run (``[:cap+1]`` LIMIT, no COUNT(*)) — sub-
        100ms even on 10M+ row trace tables — so this branch costs little
        even when it ends up taking the sync path.
        """
        manager_error = self._queue_manager_error(request, queue_id=queue_id)
        if manager_error:
            return manager_error

        try:
            rule = AutomationRule.objects.get(
                pk=pk,
                queue_id=queue_id,
                organization=request.organization,
                deleted=False,
            )
        except AutomationRule.DoesNotExist:
            return self._gm.not_found("Rule not found.")

        # The scheduled evaluator already filters out archived queues,
        # but the manual endpoint hadn't — would silently add orphan
        # items to a queue marked deleted. Block here with a 409 so
        # the user sees a clear error and either restores the queue
        # or moves the rule.
        if getattr(rule.queue, "deleted", False):
            return self._gm.custom_error_response(
                status_code=status.HTTP_409_CONFLICT,
                result=(
                    "Queue is archived. Restore the queue (POST "
                    "/annotation-queues/<id>/restore/) before evaluating "
                    "rules attached to it."
                ),
            )

        # Multi-click guard. If the rule fired in the last 30s, refuse with
        # 409 so a second click while the first run is still in flight
        # doesn't blow up to two workflows + two emails + two PG row-lock
        # waits. The lower-level QueueItem unique constraint already prevents
        # data corruption, but it's still wasteful work and confusing UX.
        # 30s is small enough that legitimate re-runs aren't impacted —
        # users can wait, and the FE keeps the Run button disabled for the
        # same window.
        if rule.last_triggered_at and (
            timezone.now() - rule.last_triggered_at
        ) < timedelta(seconds=30):
            return self._gm.custom_error_response(
                status_code=status.HTTP_409_CONFLICT,
                result=(
                    "A run is already in progress for this rule. "
                    "Please wait a few seconds before trying again."
                ),
            )

        from model_hub.utils.annotation_queue_helpers import (
            RULE_RUN_SYNC_THRESHOLD,
        )

        # Cheap peek: dry_run with cap=SYNC_THRESHOLD. Returns ≤ cap+1 ids
        # without doing the bulk_create/finalize work, then we branch.
        # If the peek itself errors out, fall back to the async path so
        # the user gets a clean response rather than a 500.
        try:
            peek = evaluate_rule(
                rule,
                dry_run=True,
                user=request.user,
                cap=RULE_RUN_SYNC_THRESHOLD,
            )
        except Exception as exc:
            logger.warning(
                "automation_rule_manual_peek_failed",
                rule_id=str(rule.pk),
                error=str(exc),
            )
            peek = {"truncated": True}

        if not peek.get("truncated"):
            # Small enough to handle inline — user sees the result
            # immediately, no email overhead.
            result = evaluate_rule(
                rule, user=request.user, cap=RULE_RUN_SYNC_THRESHOLD
            )
            return self._gm.success_response(result)

        # Large run — hand off to Temporal. task_id is suffixed with a
        # millisecond timestamp so two clicks of "Run" don't collide on the
        # same workflow_id. Activity-level serialisation already handles
        # concurrency: ``evaluate_rule`` holds a ``select_for_update`` on
        # the rule row inside the activity, and ``QueueItem`` unique
        # constraints make the bulk_create idempotent.
        from tfc.temporal.drop_in.runner import start_activity_sync

        # Reserve the rule *before* scheduling — the 30s multi-click guard
        # above only catches in-flight runs once the worker has bumped
        # ``last_triggered_at`` via ``_update_rule_stats``. For async runs
        # that bump happens minutes later (inside the Temporal activity),
        # leaving a window where two clicks both pass the guard and schedule
        # duplicate workflows + duplicate completion emails. Bump now from
        # the view so the second click sees a fresh timestamp.
        previous_last_triggered_at = rule.last_triggered_at
        scheduled_at = timezone.now()
        AutomationRule.objects.filter(pk=rule.pk).update(
            last_triggered_at=scheduled_at
        )
        rule.last_triggered_at = scheduled_at

        task_id = (
            f"automation-rule-eval-{rule.pk}-"
            f"{int(scheduled_at.timestamp() * 1000)}"
        )

        try:
            workflow_id = start_activity_sync(
                activity_name="evaluate_rule_manual_async",
                kwargs={
                    "rule_id": str(rule.pk),
                    "triggered_by_user_id": (
                        str(request.user.id) if request.user else None
                    ),
                },
                queue="tasks_l",
                task_id=task_id,
            )
        except Exception as exc:
            # Schedule failed — release the reservation so the user can
            # retry immediately without waiting out the 30s lockout.
            AutomationRule.objects.filter(pk=rule.pk).update(
                last_triggered_at=previous_last_triggered_at
            )
            rule.last_triggered_at = previous_last_triggered_at
            logger.exception(
                "automation_rule_manual_run_schedule_failed",
                rule_id=str(rule.pk),
                error=str(exc),
            )
            return self._gm.internal_server_error_response(
                f"Failed to schedule rule run: {exc}"
            )

        return Response(
            {
                "status": "scheduled",
                "workflow_id": workflow_id,
                "message": (
                    "We're preparing your data. You'll get an email when "
                    "it's ready."
                ),
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["get"], url_path="preview")
    def preview(self, request, queue_id=None, pk=None):
        """Preview how many items match a rule (dry run)."""
        manager_error = self._queue_manager_error(request, queue_id=queue_id)
        if manager_error:
            return manager_error

        try:
            rule = AutomationRule.objects.get(
                pk=pk,
                queue_id=queue_id,
                organization=request.organization,
                deleted=False,
            )
        except AutomationRule.DoesNotExist:
            return self._gm.not_found("Rule not found.")

        result = evaluate_rule(rule, dry_run=True, user=request.user)
        return self._gm.success_response(result)
