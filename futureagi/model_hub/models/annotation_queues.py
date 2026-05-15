import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.choices import (
    AnnotationQueueStatusChoices,
    AnnotatorRole,
    AssignmentStrategy,
    AutomationRuleTriggerFrequency,
    QueueItemSourceType,
    QueueItemStatus,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from tfc.utils.base_model import BaseModel

VALID_STATUS_TRANSITIONS = {
    AnnotationQueueStatusChoices.DRAFT.value: {
        AnnotationQueueStatusChoices.ACTIVE.value,
    },
    AnnotationQueueStatusChoices.ACTIVE.value: {
        AnnotationQueueStatusChoices.PAUSED.value,
        AnnotationQueueStatusChoices.COMPLETED.value,
    },
    AnnotationQueueStatusChoices.PAUSED.value: {
        AnnotationQueueStatusChoices.ACTIVE.value,
        AnnotationQueueStatusChoices.COMPLETED.value,
    },
    AnnotationQueueStatusChoices.COMPLETED.value: {
        AnnotationQueueStatusChoices.ACTIVE.value,
        AnnotationQueueStatusChoices.PAUSED.value,
    },
}

ANNOTATOR_ROLE_PRIORITY = [
    AnnotatorRole.MANAGER.value,
    AnnotatorRole.REVIEWER.value,
    AnnotatorRole.ANNOTATOR.value,
]
FULL_ACCESS_QUEUE_ROLES = list(ANNOTATOR_ROLE_PRIORITY)


def normalize_annotator_roles(value, default=AnnotatorRole.ANNOTATOR.value):
    """Return a stable, valid role list from legacy strings or new arrays."""
    if value is None or value == "":
        raw_roles = []
    elif isinstance(value, str):
        raw_roles = [value]
    elif isinstance(value, (list, tuple, set)):
        raw_roles = list(value)
    else:
        raw_roles = []

    valid_roles = {role.value for role in AnnotatorRole}
    roles = []
    for role in raw_roles:
        if role in valid_roles and role not in roles:
            roles.append(role)

    if not roles and default:
        roles = [default]

    return [role for role in ANNOTATOR_ROLE_PRIORITY if role in roles] + [
        role for role in roles if role not in ANNOTATOR_ROLE_PRIORITY
    ]


def primary_annotator_role(roles):
    normalized = normalize_annotator_roles(roles)
    return normalized[0] if normalized else AnnotatorRole.ANNOTATOR.value


def annotation_queue_role_q(*roles):
    """Match memberships where a role is stored in legacy `role` or new `roles`."""
    normalized = normalize_annotator_roles(
        list(roles),
        default=None,
    )
    query = Q(role__in=normalized)
    for role in normalized:
        query |= Q(roles__contains=[role])
    return query


class AnnotationQueue(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    instructions = models.TextField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=AnnotationQueueStatusChoices.get_choices(),
        default=AnnotationQueueStatusChoices.DRAFT.value,
    )
    assignment_strategy = models.CharField(
        max_length=20,
        choices=AssignmentStrategy.get_choices(),
        default=AssignmentStrategy.MANUAL.value,
    )
    annotations_required = models.IntegerField(default=1)
    reservation_timeout_minutes = models.IntegerField(default=60)
    requires_review = models.BooleanField(default=False)
    auto_assign = models.BooleanField(
        default=False,
        help_text="When enabled, all queue members can annotate any item without explicit assignment.",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="annotation_queues",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="annotation_queues",
        null=True,
        blank=True,
    )
    project = models.ForeignKey(
        "tracer.Project",
        on_delete=models.CASCADE,
        related_name="annotation_queues",
        null=True,
        blank=True,
    )
    dataset = models.ForeignKey(
        "model_hub.Dataset",
        on_delete=models.CASCADE,
        related_name="annotation_queues",
        null=True,
        blank=True,
    )
    agent_definition = models.ForeignKey(
        "simulate.AgentDefinition",
        on_delete=models.CASCADE,
        related_name="annotation_queues",
        null=True,
        blank=True,
    )
    is_default = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_annotation_queues",
    )
    labels = models.ManyToManyField(
        AnnotationsLabels,
        through="AnnotationQueueLabel",
        related_name="queues",
        blank=True,
    )
    annotators = models.ManyToManyField(
        User,
        through="AnnotationQueueAnnotator",
        related_name="assigned_annotation_queues",
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                condition=Q(
                    deleted=False,
                    project__isnull=True,
                    dataset__isnull=True,
                    agent_definition__isnull=True,
                ),
                name="unique_active_queue_name_org",
            ),
            models.UniqueConstraint(
                fields=["organization", "name", "project"],
                condition=Q(deleted=False, project__isnull=False),
                name="unique_active_queue_name_org_project",
            ),
            models.UniqueConstraint(
                fields=["organization", "name", "dataset"],
                condition=Q(deleted=False, dataset__isnull=False),
                name="unique_active_queue_name_org_dataset",
            ),
            models.UniqueConstraint(
                fields=["organization", "name", "agent_definition"],
                condition=Q(deleted=False, agent_definition__isnull=False),
                name="unique_active_queue_name_org_agent",
            ),
            models.UniqueConstraint(
                fields=["project"],
                condition=Q(deleted=False, is_default=True, project__isnull=False),
                name="unique_default_queue_per_project",
            ),
            models.UniqueConstraint(
                fields=["dataset"],
                condition=Q(deleted=False, is_default=True, dataset__isnull=False),
                name="unique_default_queue_per_dataset",
            ),
            models.UniqueConstraint(
                fields=["agent_definition"],
                condition=Q(
                    deleted=False, is_default=True, agent_definition__isnull=False
                ),
                name="unique_default_queue_per_agent",
            ),
        ]

    def __str__(self):
        return f"AnnotationQueue: {self.name}"


class AnnotationQueueLabel(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    queue = models.ForeignKey(
        AnnotationQueue,
        on_delete=models.CASCADE,
        related_name="queue_labels",
    )
    label = models.ForeignKey(
        AnnotationsLabels,
        on_delete=models.CASCADE,
        related_name="queue_memberships",
    )
    required = models.BooleanField(default=True)
    order = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["queue", "label"],
                condition=Q(deleted=False),
                name="unique_active_queue_label",
            )
        ]

    def __str__(self):
        return f"QueueLabel: {self.queue_id} - {self.label_id}"


class AnnotationQueueAnnotator(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    queue = models.ForeignKey(
        AnnotationQueue,
        on_delete=models.CASCADE,
        related_name="queue_annotators",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="annotation_queue_assignments",
    )
    role = models.CharField(
        max_length=20,
        choices=AnnotatorRole.get_choices(),
        default=AnnotatorRole.ANNOTATOR.value,
    )
    roles = models.JSONField(default=list, blank=True)

    @property
    def normalized_roles(self):
        return normalize_annotator_roles(self.roles or self.role)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["queue", "user"],
                condition=Q(deleted=False),
                name="unique_active_queue_annotator",
            )
        ]

    def __str__(self):
        return f"QueueAnnotator: {self.queue_id} - {self.user_id} ({self.role})"


def user_has_annotation_queue_admin_access(queue, user):
    """Org admins/owners and workspace admins manage queues in their scope."""
    if not queue or not user or not getattr(user, "is_active", False):
        return False

    organization = getattr(queue, "organization", None)
    if organization and user.has_global_workspace_access(organization):
        return True

    workspace = getattr(queue, "workspace", None)
    if not workspace:
        return False

    from accounts.models.workspace import WorkspaceMembership
    from tfc.constants.levels import Level

    membership = (
        WorkspaceMembership.no_workspace_objects.filter(
            workspace=workspace,
            user=user,
            is_active=True,
        )
        .order_by("-updated_at")
        .first()
    )
    return bool(
        membership and membership.level_or_legacy >= Level.WORKSPACE_ADMIN
    )


def annotation_queue_effective_roles(queue, user, membership=None):
    """Return explicit queue roles, or manager-equivalent admin roles."""
    if not queue or not user or not getattr(user, "is_active", False):
        return []

    if user_has_annotation_queue_admin_access(queue, user):
        return list(FULL_ACCESS_QUEUE_ROLES)

    if membership is None:
        membership = (
            AnnotationQueueAnnotator.objects.filter(
                queue=queue,
                user=user,
                deleted=False,
            )
            .order_by("-updated_at")
            .first()
        )
    if membership:
        return membership.normalized_roles

    return []


# Source FK field name mapping for QueueItem
SOURCE_TYPE_FK_MAP = {
    QueueItemSourceType.DATASET_ROW.value: "dataset_row",
    QueueItemSourceType.TRACE.value: "trace",
    QueueItemSourceType.OBSERVATION_SPAN.value: "observation_span",
    QueueItemSourceType.PROTOTYPE_RUN.value: "prototype_run",
    QueueItemSourceType.CALL_EXECUTION.value: "call_execution",
    QueueItemSourceType.TRACE_SESSION.value: "trace_session",
}


class QueueItem(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    queue = models.ForeignKey(
        AnnotationQueue,
        on_delete=models.CASCADE,
        related_name="items",
    )
    source_type = models.CharField(
        max_length=50,
        choices=QueueItemSourceType.get_choices(),
    )
    status = models.CharField(
        max_length=20,
        choices=QueueItemStatus.get_choices(),
        default=QueueItemStatus.PENDING.value,
    )
    priority = models.IntegerField(default=0)
    order = models.IntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    # Deprecated: single-assignee FK. Use assigned_users M2M instead.
    assigned_to = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_queue_items",
    )
    assigned_users = models.ManyToManyField(
        User,
        through="QueueItemAssignment",
        related_name="multi_assigned_queue_items",
        blank=True,
    )

    # Reservation fields
    reserved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reserved_queue_items",
    )
    reserved_at = models.DateTimeField(null=True, blank=True)
    reservation_expires_at = models.DateTimeField(null=True, blank=True)

    # Review fields
    review_status = models.CharField(max_length=20, null=True, blank=True)
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_queue_items",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(null=True, blank=True)

    # Source references — only one populated per item
    dataset_row = models.ForeignKey(
        "model_hub.Row",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="queue_items",
    )
    trace = models.ForeignKey(
        "tracer.Trace",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="queue_items",
    )
    observation_span = models.ForeignKey(
        "tracer.ObservationSpan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="queue_items",
    )
    prototype_run = models.ForeignKey(
        "model_hub.RunPrompter",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="queue_items",
    )
    call_execution = models.ForeignKey(
        "simulate.CallExecution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="queue_items",
    )
    trace_session = models.ForeignKey(
        "tracer.TraceSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="queue_items",
    )

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="queue_items",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="queue_items",
        null=True,
        blank=True,
    )

    class Meta:
        indexes = [
            models.Index(fields=["queue", "status"]),
            models.Index(fields=["queue", "source_type"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["queue", "dataset_row"],
                condition=Q(deleted=False, dataset_row__isnull=False),
                name="unique_queue_dataset_row",
            ),
            models.UniqueConstraint(
                fields=["queue", "trace"],
                condition=Q(deleted=False, trace__isnull=False),
                name="unique_queue_trace",
            ),
            models.UniqueConstraint(
                fields=["queue", "observation_span"],
                condition=Q(deleted=False, observation_span__isnull=False),
                name="unique_queue_observation_span",
            ),
            models.UniqueConstraint(
                fields=["queue", "prototype_run"],
                condition=Q(deleted=False, prototype_run__isnull=False),
                name="unique_queue_prototype_run",
            ),
            models.UniqueConstraint(
                fields=["queue", "call_execution"],
                condition=Q(deleted=False, call_execution__isnull=False),
                name="unique_queue_call_execution",
            ),
            models.UniqueConstraint(
                fields=["queue", "trace_session"],
                condition=Q(deleted=False, trace_session__isnull=False),
                name="unique_queue_trace_session",
            ),
        ]

    def clean(self):
        super().clean()
        fk_field = SOURCE_TYPE_FK_MAP.get(self.source_type)
        if not fk_field:
            raise ValidationError(f"Invalid source_type: {self.source_type}")
        if getattr(self, f"{fk_field}_id") is None:
            raise ValidationError(
                f"source_type '{self.source_type}' requires '{fk_field}' to be set."
            )
        # Ensure no other source FK is set
        for _st, field in SOURCE_TYPE_FK_MAP.items():
            if field != fk_field and getattr(self, f"{field}_id") is not None:
                raise ValidationError(
                    f"Only '{fk_field}' should be set for source_type '{self.source_type}', "
                    f"but '{field}' is also set."
                )

    def __str__(self):
        return f"QueueItem: {self.id} ({self.source_type})"


class QueueItemAssignment(BaseModel):
    """Through model for multi-annotator assignment on queue items."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    queue_item = models.ForeignKey(
        QueueItem,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="queue_item_assignments",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["queue_item", "user"],
                condition=Q(deleted=False),
                name="unique_active_queue_item_assignment",
            )
        ]

    def __str__(self):
        return f"QueueItemAssignment: {self.queue_item_id} - {self.user_id}"


class ItemAnnotation(BaseModel):
    """Stores one annotation value per label per item per annotator."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    queue_item = models.ForeignKey(
        QueueItem,
        on_delete=models.CASCADE,
        related_name="annotations",
    )
    annotator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="item_annotations",
    )
    label = models.ForeignKey(
        AnnotationsLabels,
        on_delete=models.CASCADE,
        related_name="item_annotations",
    )
    value = models.JSONField(default=dict)
    score_source = models.CharField(max_length=20, default="human")
    notes = models.TextField(null=True, blank=True)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="item_annotations",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="item_annotations",
        null=True,
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["queue_item", "annotator", "label"],
                condition=Q(deleted=False),
                name="unique_item_annotation_per_label_per_annotator",
            )
        ]
        indexes = [
            models.Index(fields=["queue_item", "annotator"]),
        ]

    def __str__(self):
        return f"ItemAnnotation: {self.id} (item={self.queue_item_id}, label={self.label_id})"


class QueueItemNote(BaseModel):
    """Stores one whole-item annotation note per item per annotator."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    queue_item = models.ForeignKey(
        QueueItem,
        on_delete=models.CASCADE,
        related_name="item_notes",
    )
    annotator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="annotation_queue_item_notes",
    )
    notes = models.TextField(blank=True)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="annotation_queue_item_notes",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="annotation_queue_item_notes",
        null=True,
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["queue_item", "annotator"],
                condition=Q(deleted=False),
                name="unique_active_queue_item_note_per_annotator",
            )
        ]
        indexes = [
            models.Index(fields=["queue_item", "annotator"]),
            models.Index(fields=["queue_item", "updated_at"]),
        ]

    def __str__(self):
        return f"QueueItemNote: {self.id} (item={self.queue_item_id})"


class QueueItemReviewThread(BaseModel):
    """A review issue scoped to an item, label, or one annotator's score."""

    ACTION_COMMENT = "comment"
    ACTION_APPROVE = "approve"
    ACTION_REQUEST_CHANGES = "request_changes"
    ACTION_CHOICES = [
        (ACTION_COMMENT, "Comment"),
        (ACTION_APPROVE, "Approve"),
        (ACTION_REQUEST_CHANGES, "Request Changes"),
    ]

    SCOPE_ITEM = "item"
    SCOPE_LABEL = "label"
    SCOPE_SCORE = "score"
    SCOPE_CHOICES = [
        (SCOPE_ITEM, "Item"),
        (SCOPE_LABEL, "Label"),
        (SCOPE_SCORE, "Score"),
    ]

    STATUS_OPEN = "open"
    STATUS_ADDRESSED = "addressed"
    STATUS_RESOLVED = "resolved"
    STATUS_REOPENED = "reopened"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_ADDRESSED, "Addressed"),
        (STATUS_RESOLVED, "Resolved"),
        (STATUS_REOPENED, "Reopened"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    queue_item = models.ForeignKey(
        QueueItem,
        on_delete=models.CASCADE,
        related_name="review_threads",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_annotation_review_threads",
    )
    label = models.ForeignKey(
        AnnotationsLabels,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="review_threads",
    )
    target_annotator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="targeted_annotation_review_threads",
    )
    action = models.CharField(
        max_length=30,
        choices=ACTION_CHOICES,
        default=ACTION_COMMENT,
    )
    scope = models.CharField(
        max_length=20,
        choices=SCOPE_CHOICES,
        default=SCOPE_ITEM,
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_OPEN,
    )
    blocking = models.BooleanField(default=False)
    addressed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="addressed_annotation_review_threads",
    )
    addressed_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_annotation_review_threads",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    reopened_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reopened_annotation_review_threads",
    )
    reopened_at = models.DateTimeField(null=True, blank=True)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="annotation_review_threads",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="annotation_review_threads",
        null=True,
        blank=True,
    )

    class Meta:
        indexes = [
            models.Index(fields=["queue_item", "status"]),
            models.Index(fields=["queue_item", "blocking", "status"]),
            models.Index(fields=["queue_item", "target_annotator"]),
            models.Index(fields=["queue_item", "label"]),
        ]

    def __str__(self):
        return f"QueueItemReviewThread: {self.id} (item={self.queue_item_id})"


class QueueItemReviewComment(BaseModel):
    """Auditable reviewer feedback for a queue item.

    ``QueueItem.review_notes`` is kept as the latest summary for list/export
    compatibility. This table stores the full review trail, including
    optional label-level feedback.
    """

    ACTION_COMMENT = "comment"
    ACTION_APPROVE = "approve"
    ACTION_REQUEST_CHANGES = "request_changes"
    ACTION_ADDRESSED = "addressed"
    ACTION_RESOLVE = "resolve"
    ACTION_REOPEN = "reopen"
    ACTION_CHOICES = [
        (ACTION_COMMENT, "Comment"),
        (ACTION_APPROVE, "Approve"),
        (ACTION_REQUEST_CHANGES, "Request Changes"),
        (ACTION_ADDRESSED, "Addressed"),
        (ACTION_RESOLVE, "Resolve"),
        (ACTION_REOPEN, "Reopen"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thread = models.ForeignKey(
        QueueItemReviewThread,
        on_delete=models.CASCADE,
        related_name="comments",
        null=True,
        blank=True,
    )
    queue_item = models.ForeignKey(
        QueueItem,
        on_delete=models.CASCADE,
        related_name="review_comments",
    )
    reviewer = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="annotation_review_comments",
    )
    label = models.ForeignKey(
        AnnotationsLabels,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="review_comments",
    )
    target_annotator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="targeted_annotation_review_comments",
    )
    mentioned_users = models.ManyToManyField(
        User,
        blank=True,
        related_name="mentioned_annotation_review_comments",
    )
    action = models.CharField(
        max_length=30,
        choices=ACTION_CHOICES,
        default=ACTION_COMMENT,
    )
    comment = models.TextField()
    reactions = models.JSONField(default=dict, blank=True)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="annotation_review_comments",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="annotation_review_comments",
        null=True,
        blank=True,
    )

    class Meta:
        indexes = [
            models.Index(fields=["queue_item", "created_at"]),
            models.Index(fields=["queue_item", "label"]),
            models.Index(fields=["queue_item", "target_annotator"]),
            models.Index(fields=["thread", "created_at"]),
        ]

    def __str__(self):
        return f"QueueItemReviewComment: {self.id} (item={self.queue_item_id})"


class AutomationRule(BaseModel):
    """Rule-based auto-routing of items to annotation queues."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    queue = models.ForeignKey(
        AnnotationQueue,
        on_delete=models.CASCADE,
        related_name="automation_rules",
    )
    source_type = models.CharField(
        max_length=50,
        choices=QueueItemSourceType.get_choices(),
    )
    conditions = models.JSONField(default=dict)
    enabled = models.BooleanField(default=True)
    trigger_frequency = models.CharField(
        max_length=20,
        choices=AutomationRuleTriggerFrequency.get_choices(),
        default=AutomationRuleTriggerFrequency.MANUAL.value,
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="automation_rules",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_automation_rules",
    )
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    trigger_count = models.IntegerField(default=0)

    def __str__(self):
        return f"AutomationRule: {self.name} (queue={self.queue_id})"


class AnnotationNotificationState(BaseModel):
    """Per-user state for the annotation digest emails.

    Tracks two cadences:
    - **Realtime (every 15min cron):** when ``last_realtime_digest_at`` is
      older than the 60-minute throttle, eligible to fire if there are new
      pending items since that timestamp.
    - **Daily (hourly cron):** when local-time-now matches
      ``daily_digest_hour_local`` and ``last_daily_digest_at`` is older
      than ~12h, eligible to fire with a current-state snapshot.

    ``digest_enabled = False`` opts the user out of both tracks
    (unsubscribe link in the email footer flips this). ``realtime_snoozed_until``
    pauses just the realtime track for N days when the user clicks Snooze.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="annotation_notification_state",
    )
    last_realtime_digest_at = models.DateTimeField(null=True, blank=True)
    last_daily_digest_at = models.DateTimeField(null=True, blank=True)
    digest_enabled = models.BooleanField(default=True)
    realtime_snoozed_until = models.DateTimeField(null=True, blank=True)
    daily_digest_hour_local = models.IntegerField(
        default=9,
        help_text="Hour of day (0-23) to send the daily digest in the user's local TZ.",
    )

    def __str__(self):
        return f"AnnotationNotificationState({self.user_id})"
