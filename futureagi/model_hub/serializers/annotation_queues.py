from django.db import transaction
from rest_framework import serializers

from accounts.models.user import User
from model_hub.models.annotation_queues import (
    SOURCE_TYPE_FK_MAP,
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AnnotationQueueLabel,
    AutomationRule,
    ItemAnnotation,
    QueueItem,
    normalize_annotator_roles,
    primary_annotator_role,
)
from model_hub.models.choices import AnnotatorRole
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.serializers.scores import ScoreSerializer
from model_hub.utils.annotation_queue_helpers import (
    get_fk_field_name,
    resolve_source_content,
    resolve_source_object,
    resolve_source_preview,
)


class QueueLabelNestedSerializer(serializers.ModelSerializer):
    label_id = serializers.UUIDField(source="label.id")
    name = serializers.CharField(source="label.name", read_only=True)
    type = serializers.CharField(source="label.type", read_only=True)

    class Meta:
        model = AnnotationQueueLabel
        fields = ["id", "label_id", "name", "type", "required", "order"]
        read_only_fields = ["id"]


class QueueAnnotatorNestedSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source="user.id")
    name = serializers.CharField(source="user.name", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    roles = serializers.SerializerMethodField()

    role = serializers.CharField(default="annotator")

    class Meta:
        model = AnnotationQueueAnnotator
        fields = ["id", "user_id", "name", "email", "role", "roles"]
        read_only_fields = ["id"]

    def get_roles(self, obj):
        return obj.normalized_roles


class AnnotationQueueSerializer(serializers.ModelSerializer):
    label_ids = serializers.ListField(
        child=serializers.UUIDField(),
        write_only=True,
        required=False,
        default=list,
    )
    annotator_ids = serializers.ListField(
        child=serializers.UUIDField(),
        write_only=True,
        required=False,
        default=list,
    )
    annotator_roles = serializers.DictField(
        child=serializers.JSONField(),
        write_only=True,
        required=False,
        default=dict,
    )
    labels = QueueLabelNestedSerializer(
        source="queue_labels", many=True, read_only=True
    )
    annotators = QueueAnnotatorNestedSerializer(
        source="queue_annotators", many=True, read_only=True
    )
    label_count = serializers.IntegerField(read_only=True, required=False)
    annotator_count = serializers.IntegerField(read_only=True, required=False)
    item_count = serializers.IntegerField(read_only=True, required=False)
    completed_count = serializers.IntegerField(read_only=True, required=False)
    created_by_name = serializers.CharField(
        source="created_by.name", read_only=True, default=None
    )

    class Meta:
        model = AnnotationQueue
        fields = [
            "id",
            "name",
            "description",
            "instructions",
            "status",
            "assignment_strategy",
            "annotations_required",
            "reservation_timeout_minutes",
            "requires_review",
            "auto_assign",
            "organization",
            "project",
            "dataset",
            "agent_definition",
            "is_default",
            "labels",
            "annotators",
            "label_ids",
            "annotator_ids",
            "annotator_roles",
            "label_count",
            "annotator_count",
            "item_count",
            "completed_count",
            "created_by",
            "created_by_name",
            "created_at",
        ]
        read_only_fields = [
            "organization",
            "created_by",
            "status",
            "project",
            "dataset",
            "agent_definition",
            "is_default",
        ]

    def validate_name(self, value):
        organization = None
        if "request" in self.context:
            organization = getattr(self.context["request"].user, "organization", None)

        if organization:
            # Scope uniqueness check to the project/dataset/agent_definition (if present)
            scope_kwargs = {}
            if self.instance:
                scope_kwargs["project"] = getattr(self.instance, "project", None)
                scope_kwargs["dataset"] = getattr(self.instance, "dataset", None)
                scope_kwargs["agent_definition"] = getattr(
                    self.instance, "agent_definition", None
                )
            else:
                # For new queues, use initial_data from request context
                # (project/dataset/agent_definition are set in perform_create)
                request = self.context.get("request")
                initial = request.data if request else {}
                scope_kwargs["project_id"] = initial.get("project_id")
                scope_kwargs["dataset_id"] = initial.get("dataset_id")
                scope_kwargs["agent_definition_id"] = initial.get("agent_definition_id")
            qs = AnnotationQueue.objects.filter(
                name__iexact=value,
                organization=organization,
                deleted=False,
                **scope_kwargs,
            )
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    "A queue with this name already exists."
                )
        return value

    def validate_annotator_roles(self, value):
        normalized = {}
        for user_id, roles in (value or {}).items():
            role_list = normalize_annotator_roles(roles, default=None)
            if not role_list:
                raise serializers.ValidationError(
                    f"Invalid roles for annotator {user_id}."
                )
            normalized[str(user_id)] = role_list
        return normalized

    def _sync_labels(self, queue, label_ids):
        existing = set(
            queue.queue_labels.filter(deleted=False).values_list("label_id", flat=True)
        )
        incoming = set(label_ids)

        to_remove = existing - incoming
        if to_remove:
            queue.queue_labels.filter(label_id__in=to_remove).update(deleted=True)

        to_add = incoming - existing
        if to_add:
            labels = AnnotationsLabels.objects.filter(id__in=to_add, deleted=False)
            # Count remaining (non-removed) labels for correct ordering
            remaining_count = queue.queue_labels.filter(deleted=False).count()
            AnnotationQueueLabel.objects.bulk_create(
                [
                    AnnotationQueueLabel(queue=queue, label=label, order=idx)
                    for idx, label in enumerate(labels, start=remaining_count)
                ]
            )

    def _sync_annotators(self, queue, annotator_ids, annotator_roles=None):
        roles = annotator_roles or {}
        existing_qs = queue.queue_annotators.filter(deleted=False)
        existing = set(existing_qs.values_list("user_id", flat=True))
        incoming = set(annotator_ids)

        # 1. Soft-delete removed annotators
        to_remove = existing - incoming
        if to_remove:
            existing_qs.filter(user_id__in=to_remove).update(deleted=True)

        # 2. Update role for existing annotators if role changed
        to_keep = existing & incoming
        for annotator in existing_qs.filter(user_id__in=to_keep):
            new_roles = roles.get(str(annotator.user_id))
            if new_roles:
                primary_role = primary_annotator_role(new_roles)
                if annotator.role != primary_role or annotator.normalized_roles != new_roles:
                    annotator.role = primary_role
                    annotator.roles = new_roles
                    annotator.save(update_fields=["role", "roles", "updated_at"])

        # 3. Create new annotators with role from dict, defaulting to "annotator"
        to_add = incoming - existing
        if to_add:
            users = User.objects.filter(id__in=to_add)
            AnnotationQueueAnnotator.objects.bulk_create(
                [
                    AnnotationQueueAnnotator(
                        queue=queue,
                        user=user,
                        role=primary_annotator_role(
                            roles.get(str(user.id), [AnnotatorRole.ANNOTATOR.value])
                        ),
                        roles=normalize_annotator_roles(
                            roles.get(str(user.id), [AnnotatorRole.ANNOTATOR.value])
                        ),
                    )
                    for user in users
                ]
            )

    @transaction.atomic
    def create(self, validated_data):
        label_ids = validated_data.pop("label_ids", [])
        annotator_ids = validated_data.pop("annotator_ids", [])
        annotator_roles = validated_data.pop("annotator_roles", {})
        queue = AnnotationQueue(**validated_data)
        queue.save()

        if label_ids:
            self._sync_labels(queue, label_ids)

        # Auto-add creator as manager (override role if already in annotator_ids)
        creator = queue.created_by
        if creator:
            creator_id = str(creator.pk)
            creator_roles = [
                AnnotatorRole.MANAGER.value,
                AnnotatorRole.REVIEWER.value,
                AnnotatorRole.ANNOTATOR.value,
            ]
            annotator_ids_str = [str(aid) for aid in annotator_ids]
            if creator_id not in annotator_ids_str:
                # Creator not explicitly listed — add them with full access.
                annotator_roles[creator_id] = creator_roles
                annotator_ids = list(annotator_ids) + [creator.pk]
            else:
                # Creator was listed — ensure they keep full access.
                annotator_roles[creator_id] = creator_roles

        if annotator_ids:
            self._sync_annotators(queue, annotator_ids, annotator_roles)

        return queue

    @transaction.atomic
    def update(self, instance, validated_data):
        label_ids = validated_data.pop("label_ids", None)
        annotator_ids = validated_data.pop("annotator_ids", None)
        annotator_roles = validated_data.pop("annotator_roles", {})

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if label_ids is not None:
            self._sync_labels(instance, label_ids)
        if annotator_ids is not None:
            self._sync_annotators(instance, annotator_ids, annotator_roles)

        return instance


class QueueItemSerializer(serializers.ModelSerializer):
    source_id = serializers.CharField(write_only=True, required=False)
    source_preview = serializers.SerializerMethodField()
    assigned_to_name = serializers.CharField(
        source="assigned_to.name", read_only=True, default=None
    )
    assigned_users = serializers.SerializerMethodField()
    reserved_by_name = serializers.CharField(
        source="reserved_by.name", read_only=True, default=None
    )
    reviewed_by_name = serializers.CharField(
        source="reviewed_by.name", read_only=True, default=None
    )

    class Meta:
        model = QueueItem
        fields = [
            "id",
            "queue",
            "source_type",
            "source_id",
            "status",
            "priority",
            "order",
            "metadata",
            "assigned_to",
            "assigned_to_name",
            "assigned_users",
            "reserved_by",
            "reserved_by_name",
            "reservation_expires_at",
            "review_status",
            "reviewed_by",
            "reviewed_by_name",
            "reviewed_at",
            "review_notes",
            "source_preview",
            "created_at",
        ]
        read_only_fields = ["queue"]

    def get_assigned_users(self, obj):
        assignments = obj.assignments.filter(deleted=False).select_related("user")
        return [
            {"id": str(a.user_id), "name": a.user.name if a.user else None}
            for a in assignments
        ]

    def get_source_preview(self, obj):
        return resolve_source_preview(obj)

    def create(self, validated_data):
        source_id = validated_data.pop("source_id", None)
        source_type = validated_data.get("source_type")

        if source_id and source_type:
            fk_field = get_fk_field_name(source_type)
            if fk_field:
                request = self.context.get("request")
                workspace = getattr(request, "workspace", None) if request else None
                source_obj = resolve_source_object(
                    source_type, source_id, workspace=workspace
                )
                if source_obj:
                    validated_data[fk_field] = source_obj
                else:
                    raise serializers.ValidationError(
                        f"Source object not found: {source_type}={source_id}"
                    )

        return super().create(validated_data)


# ---------------------------------------------------------------------------
# Bulk selection (filter-mode) — Phase 2 of annotation-queue-bulk-select.
# Modes and source_types are module-level sets so later phases extend the
# set, not the surrounding validator logic.
# ---------------------------------------------------------------------------
SUPPORTED_SELECTION_MODES = {"filter"}
SUPPORTED_SELECTION_SOURCE_TYPES = {
    "trace",
    "observation_span",
    "trace_session",
    "call_execution",
}  # Phases 2 + 4 + 6 + 8


class SelectionSerializer(serializers.Serializer):
    """Filter-mode bulk-add payload.

    When present on an ``add-items`` request, the view runs the server-side
    resolver against ``filter`` within ``project_id`` and bulk-creates
    QueueItems for the matching source rows minus ``exclude_ids``.
    """

    mode = serializers.ChoiceField(choices=sorted(SUPPORTED_SELECTION_MODES))
    source_type = serializers.ChoiceField(
        choices=sorted(SUPPORTED_SELECTION_SOURCE_TYPES)
    )
    project_id = serializers.UUIDField()
    filter = serializers.ListField(
        child=serializers.DictField(), required=False, default=list
    )
    # exclude_ids are compared against the resolver's string-cast IDs, so
    # accept any string (UUIDs for trace/session/call_execution, hex for
    # observation_span).
    exclude_ids = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    # Voice/simulator projects only. Mirrors the grid toolbar's
    # ``remove_simulation_calls`` toggle so the backend resolver hides
    # VAPI simulator calls when the user has that toggle on. Ignored by
    # non-trace source types and by non-simulator projects.
    remove_simulation_calls = serializers.BooleanField(required=False, default=False)
    # Explicit signal that the selection came from the voice grid (which
    # uses ``list_voice_calls`` → traces with a conversation root). When
    # true the trace resolver applies the ``has_conversation_root`` and
    # voice-system-metrics constraints so its result set matches the grid.
    # More reliable than gating on ``project.source`` which is
    # inconsistent across historical simulator projects.
    is_voice_call = serializers.BooleanField(required=False, default=False)


class AddItemsSerializer(serializers.Serializer):
    """Accepts either the enumerated ``items`` payload or a filter-mode
    ``selection`` payload. Exactly one of the two is required."""

    items = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        allow_empty=False,
    )
    selection = SelectionSerializer(required=False)

    def validate_items(self, value):
        valid_types = {st for st in SOURCE_TYPE_FK_MAP}
        for item in value:
            if "source_type" not in item or "source_id" not in item:
                raise serializers.ValidationError(
                    "Each item must have 'source_type' and 'source_id'."
                )
            if item["source_type"] not in valid_types:
                raise serializers.ValidationError(
                    f"Invalid source_type: {item['source_type']}"
                )
        return value

    def validate(self, attrs):
        has_items = bool(attrs.get("items"))
        has_selection = bool(attrs.get("selection"))
        if has_items and has_selection:
            raise serializers.ValidationError(
                "Provide exactly one of 'items' or 'selection', not both."
            )
        if not has_items and not has_selection:
            raise serializers.ValidationError(
                "Provide exactly one of 'items' or 'selection'."
            )
        return attrs


# ---------------------------------------------------------------------------
# Phase 3A: Annotation serializers
# ---------------------------------------------------------------------------


class ItemAnnotationSerializer(serializers.ModelSerializer):
    label_id = serializers.UUIDField(source="label.id", read_only=True)
    label_name = serializers.CharField(source="label.name", read_only=True)
    label_type = serializers.CharField(source="label.type", read_only=True)
    annotator_name = serializers.CharField(
        source="annotator.name", read_only=True, default=None
    )

    class Meta:
        model = ItemAnnotation
        fields = [
            "id",
            "label_id",
            "label_name",
            "label_type",
            "value",
            "score_source",
            "notes",
            "annotator",
            "annotator_name",
            "created_at",
        ]
        read_only_fields = ["annotator"]


class SubmitAnnotationsSerializer(serializers.Serializer):
    annotations = serializers.ListField(
        child=serializers.DictField(),
        min_length=1,
    )
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    item_notes = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default=None
    )

    def validate_annotations(self, value):
        for ann in value:
            if "label_id" not in ann or "value" not in ann:
                raise serializers.ValidationError(
                    "Each annotation must have 'label_id' and 'value'."
                )
        return value


class QueueLabelDetailSerializer(serializers.ModelSerializer):
    """Extended label serializer for annotation interface — includes settings."""

    label_id = serializers.UUIDField(source="label.id")
    name = serializers.CharField(source="label.name", read_only=True)
    type = serializers.CharField(source="label.type", read_only=True)
    settings = serializers.JSONField(source="label.settings", read_only=True)
    allow_notes = serializers.BooleanField(source="label.allow_notes", read_only=True)
    description = serializers.CharField(
        source="label.description", read_only=True, default=None
    )

    class Meta:
        model = AnnotationQueueLabel
        fields = [
            "id",
            "label_id",
            "name",
            "type",
            "settings",
            "description",
            "allow_notes",
            "required",
            "order",
        ]


class AnnotateDetailSerializer(serializers.Serializer):
    """Composite serializer for the annotation workspace detail endpoint."""

    def to_representation(self, instance):
        item = instance["item"]
        queue = instance["queue"]
        labels = instance["labels"]
        annotations = instance["annotations"]
        progress = instance["progress"]

        return {
            "item": {
                "id": str(item.id),
                "source_type": item.source_type,
                "status": item.status,
                "review_status": item.review_status,
                "order": item.order,
                "assigned_to_id": (
                    str(item.assigned_to_id) if item.assigned_to_id else None
                ),
                "assigned_to_name": (
                    item.assigned_to.name
                    if item.assigned_to_id and item.assigned_to
                    else None
                ),
                "assigned_users": [
                    {"id": str(a.user_id), "name": a.user.name if a.user else None}
                    for a in item.assignments.filter(deleted=False).select_related(
                        "user"
                    )
                ],
                "source_content": resolve_source_content(item),
                "source_preview": resolve_source_preview(item),
                "review_notes": item.review_notes,
                "reviewed_by_name": (
                    item.reviewed_by.name if item.reviewed_by else None
                ),
                "reviewed_at": item.reviewed_at,
            },
            "queue": {
                "id": str(queue.id),
                "name": queue.name,
                "status": queue.status,
                "instructions": queue.instructions,
            },
            "labels": QueueLabelDetailSerializer(labels, many=True).data,
            "annotations": ScoreSerializer(annotations, many=True).data,
            "existing_notes": instance.get("existing_notes", ""),
            "span_notes": instance.get("span_notes", []),
            "span_notes_source_id": instance.get("span_notes_source_id"),
            "progress": progress,
            "next_item_id": instance.get("next_item_id"),
            "prev_item_id": instance.get("prev_item_id"),
        }


class AutomationRuleSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(
        source="created_by.name", read_only=True, default=None
    )

    class Meta:
        model = AutomationRule
        fields = [
            "id",
            "name",
            "queue",
            "source_type",
            "conditions",
            "enabled",
            "trigger_frequency",
            "organization",
            "created_by",
            "created_by_name",
            "last_triggered_at",
            "trigger_count",
            "created_at",
        ]
        read_only_fields = [
            "organization",
            "created_by",
            "queue",
            "trigger_count",
            "last_triggered_at",
        ]
