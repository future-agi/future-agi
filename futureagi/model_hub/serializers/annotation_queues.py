import json
import uuid

from django.db import transaction
from django.db.models import Q
from rest_framework import serializers
from rest_framework.fields import empty

from accounts.models.user import User
from accounts.models.workspace import WorkspaceMembership
from model_hub.models.annotation_queues import (
    SOURCE_TYPE_FK_MAP,
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AnnotationQueueLabel,
    AutomationRule,
    ItemAnnotation,
    QueueItem,
    QueueItemReviewComment,
    QueueItemReviewThread,
    annotation_queue_effective_roles,
    normalize_annotator_roles,
    primary_annotator_role,
)
from model_hub.models.choices import AnnotationQueueStatusChoices, AnnotatorRole
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.serializers.scores import ScoreSerializer
from model_hub.utils.annotation_queue_helpers import (
    FIELD_MAPPING,
    CollectorSourceCache,
    get_fk_field_name,
    resolve_source_content,
    resolve_source_object,
    resolve_source_preview,
)
from tracer.serializers.filters import StrictInputSerializer, filter_list_field


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
        required=True,
        min_length=1,
        error_messages={
            "required": "At least one label is required.",
            "min_length": "At least one label is required.",
        },
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
    viewer_roles = serializers.SerializerMethodField()
    viewer_role = serializers.SerializerMethodField()
    deleted = serializers.BooleanField(read_only=True)

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
            "viewer_role",
            "viewer_roles",
            "deleted",
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

    def _request_org_workspace(self):
        request = self.context.get("request")
        if not request:
            return None, None
        organization = getattr(request, "organization", None) or getattr(
            getattr(request, "user", None),
            "organization",
            None,
        )
        return organization, getattr(request, "workspace", None)

    def _workspace_visibility_q(self, organization, workspace):
        if not workspace:
            return Q()
        if getattr(workspace, "is_default", False):
            return (
                Q(workspace=workspace)
                | Q(workspace__is_default=True, workspace__organization=organization)
                | Q(workspace__isnull=True, organization=organization)
            )
        return Q(workspace=workspace) | Q(
            workspace__isnull=True,
            organization=organization,
        )

    def _visible_label_queryset(self, label_ids):
        organization, workspace = self._request_org_workspace()
        queryset = AnnotationsLabels.all_objects.filter(
            id__in=label_ids,
            deleted=False,
        )
        if organization:
            queryset = queryset.filter(organization=organization)
        if workspace:
            queryset = queryset.filter(
                self._workspace_visibility_q(organization, workspace)
            )
        return queryset

    def validate(self, attrs):
        attrs = super().validate(attrs)
        organization, workspace = self._request_org_workspace()

        label_ids = attrs.get("label_ids") or []
        if label_ids:
            requested = {str(label_id) for label_id in label_ids}
            visible = {
                str(label_id)
                for label_id in self._visible_label_queryset(label_ids).values_list(
                    "id",
                    flat=True,
                )
            }
            if requested - visible:
                raise serializers.ValidationError(
                    {
                        "label_ids": "One or more labels are missing or not accessible from this workspace."
                    }
                )

        annotator_ids = attrs.get("annotator_ids") or []
        if annotator_ids:
            requested = {str(annotator_id) for annotator_id in annotator_ids}
            if workspace:
                visible = {
                    str(user_id)
                    for user_id in WorkspaceMembership.no_workspace_objects.filter(
                        workspace=workspace,
                        is_active=True,
                        user_id__in=annotator_ids,
                        user__is_active=True,
                    ).values_list("user_id", flat=True)
                }
            else:
                users = User.objects.filter(
                    id__in=annotator_ids,
                    is_active=True,
                )
                if organization:
                    users = users.filter(organization=organization)
                visible = {
                    str(user_id) for user_id in users.values_list("id", flat=True)
                }

            if requested - visible:
                raise serializers.ValidationError(
                    {
                        "annotator_ids": "One or more annotators are not active members of this workspace."
                    }
                )

        return attrs

    def _viewer_membership(self, obj, user):
        if not user:
            return None

        prefetched = getattr(obj, "_prefetched_objects_cache", {}).get(
            "queue_annotators"
        )
        if prefetched is not None:
            for member in prefetched:
                if str(member.user_id) == str(user.id) and not member.deleted:
                    return member
            return None

        return (
            obj.queue_annotators.filter(user=user, deleted=False)
            .order_by("-updated_at")
            .first()
        )

    def get_viewer_roles(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return []
        return annotation_queue_effective_roles(
            obj,
            user,
            membership=self._viewer_membership(obj, user),
        )

    def get_viewer_role(self, obj):
        roles = self.get_viewer_roles(obj)
        return primary_annotator_role(roles) if roles else None

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
            labels = self._visible_label_queryset(to_add)
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
                if (
                    annotator.role != primary_role
                    or annotator.normalized_roles != new_roles
                ):
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


class QueueItemListSerializer(serializers.ListSerializer):
    """Builds one :class:`CollectorSourceCache` for the page so ``source_preview``
    resolves collector spans/sessions from a single CH read instead of one point-read
    per item. Stashed in context for the child's ``get_source_preview`` to pick up."""

    def to_representation(self, data):
        items = list(data)
        self.context["ch_source_cache"] = CollectorSourceCache.for_items(items)
        return super().to_representation(items)


class QueueItemSerializer(serializers.ModelSerializer):
    source_id = serializers.CharField(write_only=True, required=False)
    source_preview = serializers.SerializerMethodField()
    workflow_status = serializers.SerializerMethodField()
    workflow_status_label = serializers.SerializerMethodField()
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
    comment_count = serializers.SerializerMethodField()
    open_feedback_count = serializers.SerializerMethodField()

    class Meta:
        model = QueueItem
        fields = [
            "id",
            "queue",
            "source_type",
            "source_id",
            "status",
            "workflow_status",
            "workflow_status_label",
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
            "comment_count",
            "open_feedback_count",
            "created_at",
        ]
        read_only_fields = ["queue"]
        list_serializer_class = QueueItemListSerializer

    def get_assigned_users(self, obj):
        assignments = getattr(obj, "active_assignments", None)
        if assignments is None:
            assignments = obj.assignments.filter(deleted=False).select_related("user")
        return [
            {
                "id": str(a.user_id),
                "name": a.user.name if a.user else None,
                "email": a.user.email if a.user else None,
            }
            for a in assignments
        ]

    def get_source_preview(self, obj):
        return resolve_source_preview(obj, ch_cache=self.context.get("ch_source_cache"))

    def get_workflow_status(self, obj):
        if (
            obj.review_status == "pending_review"
            and QueueItemReviewThread.objects.filter(
                queue_item=obj,
                blocking=True,
                status=QueueItemReviewThread.STATUS_ADDRESSED,
                deleted=False,
            ).exists()
        ):
            return "resubmitted"
        if obj.review_status == "pending_review":
            return "in_review"
        if obj.review_status == "rejected":
            return "needs_changes"
        return obj.status

    def get_workflow_status_label(self, obj):
        status = self.get_workflow_status(obj)
        return {
            "pending": "Pending Annotation",
            "in_progress": "In Progress",
            "in_review": "In Review",
            "needs_changes": "Needs Changes",
            "resubmitted": "Resubmitted",
            "completed": "Completed",
            "skipped": "Skipped",
        }.get(status, status)

    def get_comment_count(self, obj):
        return QueueItemReviewComment.objects.filter(
            queue_item=obj,
            action=QueueItemReviewComment.ACTION_COMMENT,
            deleted=False,
        ).count()

    def get_open_feedback_count(self, obj):
        return QueueItemReviewThread.objects.filter(
            queue_item=obj,
            blocking=True,
            status__in=[
                QueueItemReviewThread.STATUS_OPEN,
                QueueItemReviewThread.STATUS_REOPENED,
            ],
            deleted=False,
        ).count()

    def create(self, validated_data):
        source_id = validated_data.pop("source_id", None)
        source_type = validated_data.get("source_type")

        if source_id and source_type:
            fk_field = get_fk_field_name(source_type)
            if fk_field:
                request = self.context.get("request")
                organization = (
                    getattr(request, "organization", None) if request else None
                )
                workspace = getattr(request, "workspace", None) if request else None
                source_obj = resolve_source_object(
                    source_type,
                    source_id,
                    organization=organization,
                    workspace=workspace,
                    allow_ch_fallback=True,
                )
                if source_obj:
                    # Store the soft id, not the FK object: a CH-resolved source
                    # isn't a Django instance. QueueItem FKs are db_constraint=False
                    # so a bare ``_id`` persists (and Django FKs accept it too).
                    source_pk = getattr(source_obj, "pk", None) or getattr(
                        source_obj, "id", None
                    )
                    validated_data[f"{fk_field}_id"] = source_pk
                else:
                    raise serializers.ValidationError(
                        f"Source object not found: {source_type}={source_id}"
                    )

                queue = validated_data.get("queue")
                if (
                    queue
                    and QueueItem.objects.filter(
                        queue=queue,
                        deleted=False,
                        **{f"{fk_field}_id": source_pk},
                    ).exists()
                ):
                    raise serializers.ValidationError(
                        {
                            "source_id": (
                                "An active queue item already exists for this "
                                f"{source_type} source."
                            )
                        }
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


class SelectionSerializer(StrictInputSerializer):
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
    filter = filter_list_field(required=False, default=list)
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


class AddQueueItemSerializer(StrictInputSerializer):
    source_type = serializers.ChoiceField(choices=sorted(SOURCE_TYPE_FK_MAP))
    # Source ids are UUIDs for most source types, but observation spans use a
    # string primary key, so this stays a string and the resolver validates it.
    source_id = serializers.CharField(allow_blank=False)


class AddItemsSerializer(StrictInputSerializer):
    """Accepts either the enumerated ``items`` payload or a filter-mode
    ``selection`` payload. Exactly one of the two is required."""

    items = AddQueueItemSerializer(
        many=True,
        required=False,
    )
    selection = SelectionSerializer(required=False)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("items must not be empty.")
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


class BulkRemoveItemsSerializer(StrictInputSerializer):
    item_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
    )


class EmptyRequestSerializer(StrictInputSerializer):
    pass


class AnnotationQueueListQuerySerializer(StrictInputSerializer):
    status = serializers.CharField(required=False, allow_blank=True)
    search = serializers.CharField(required=False, allow_blank=True)
    include_counts = serializers.BooleanField(required=False)
    archived = serializers.BooleanField(required=False, default=False)
    page_size = serializers.IntegerField(required=False, min_value=1)


class QueueHardDeleteRequestSerializer(StrictInputSerializer):
    force = serializers.BooleanField()
    confirm_name = serializers.CharField()


class QueueStatusRequestSerializer(StrictInputSerializer):
    status = serializers.ChoiceField(
        choices=[choice.value for choice in AnnotationQueueStatusChoices]
    )


class QueueDefaultRequestSerializer(StrictInputSerializer):
    project_id = serializers.UUIDField(required=False)
    dataset_id = serializers.UUIDField(required=False)
    agent_definition_id = serializers.UUIDField(required=False)

    def validate(self, attrs):
        selected = [
            key
            for key in ("project_id", "dataset_id", "agent_definition_id")
            if attrs.get(key)
        ]
        if len(selected) != 1:
            raise serializers.ValidationError(
                "Provide exactly one of project_id, dataset_id, or agent_definition_id."
            )
        return attrs


class QueueLabelRequestSerializer(StrictInputSerializer):
    label_id = serializers.UUIDField()
    required = serializers.BooleanField(required=False, default=True)


class QueueExportColumnMappingSerializer(StrictInputSerializer):
    field = serializers.CharField(required=False, allow_blank=True)
    id = serializers.CharField(required=False, allow_blank=True)
    column = serializers.CharField(required=False, allow_blank=True)
    enabled = serializers.BooleanField(required=False, default=True)


class QueueExportToDatasetRequestSerializer(StrictInputSerializer):
    dataset_id = serializers.UUIDField(required=False)
    dataset_name = serializers.CharField(required=False, allow_blank=True)
    status_filter = serializers.CharField(
        required=False,
        allow_blank=True,
        default="completed",
    )
    column_mapping = QueueExportColumnMappingSerializer(
        many=True,
        required=False,
        default=list,
    )

    def validate(self, attrs):
        if (
            not attrs.get("dataset_id")
            and not str(attrs.get("dataset_name") or "").strip()
        ):
            raise serializers.ValidationError(
                "Either dataset_id or dataset_name is required."
            )
        return attrs


class QueueExportQuerySerializer(StrictInputSerializer):
    export_format = serializers.ChoiceField(
        choices=["json", "csv"],
        required=False,
    )
    status = serializers.CharField(required=False, allow_blank=True)


class QueueSourceLookupSerializer(StrictInputSerializer):
    source_type = serializers.ChoiceField(choices=sorted(SOURCE_TYPE_FK_MAP))
    source_id = serializers.CharField(allow_blank=False)
    span_notes_source_id = serializers.CharField(required=False, allow_blank=False)


class QueueSourceListQueryParamField(serializers.CharField):
    class Meta:
        swagger_schema_fields = {
            "type": "string",
            "description": (
                "JSON-encoded source lookup list. Each item must contain "
                "source_type and source_id, plus optional span_notes_source_id."
            ),
        }

    def to_internal_value(self, data):
        if data in (None, ""):
            return []
        try:
            sources = json.loads(data) if isinstance(data, str) else data
        except json.JSONDecodeError as exc:
            raise serializers.ValidationError("sources must be valid JSON.") from exc
        if not isinstance(sources, list) or not sources:
            raise serializers.ValidationError("sources must be a non-empty list.")

        serializer = QueueSourceLookupSerializer(data=sources, many=True)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data


class QueueForSourceQuerySerializer(StrictInputSerializer):
    source_type = serializers.ChoiceField(
        choices=sorted(SOURCE_TYPE_FK_MAP),
        required=False,
    )
    source_id = serializers.CharField(required=False, allow_blank=True)
    sources = QueueSourceListQueryParamField(required=False, allow_blank=True)

    def validate(self, attrs):
        sources = attrs.get("sources") or []
        has_single_source_type = bool(attrs.get("source_type"))
        has_single_source_id = bool(attrs.get("source_id"))

        if sources and (has_single_source_type or has_single_source_id):
            raise serializers.ValidationError(
                "Use either sources or source_type/source_id, not both."
            )
        if sources:
            return attrs
        if not has_single_source_type or not has_single_source_id:
            raise serializers.ValidationError(
                "source_type and source_id (or sources) are required."
            )
        attrs["sources"] = [
            {"source_type": attrs["source_type"], "source_id": attrs["source_id"]}
        ]
        return attrs


class RepeatedStringQueryParamField(serializers.ListField):
    child = serializers.CharField(allow_blank=False)

    class Meta:
        swagger_schema_fields = {
            "type": "array",
            "items": {"type": "string"},
        }

    def get_value(self, dictionary):
        if hasattr(dictionary, "getlist"):
            values = dictionary.getlist(self.field_name)
            return values if values else empty
        return dictionary.get(self.field_name, empty)

    def to_internal_value(self, data):
        if data in (None, ""):
            return []

        raw_values = data if isinstance(data, (list, tuple)) else [data]
        values = []
        for raw_value in raw_values:
            if raw_value is None:
                continue
            for part in str(raw_value).split(","):
                value = part.strip()
                if not value or value.lower() == "all":
                    continue
                if value not in values:
                    values.append(value)

        return super().to_internal_value(values)


class QueueItemListQuerySerializer(StrictInputSerializer):
    status = RepeatedStringQueryParamField(required=False)
    source_type = RepeatedStringQueryParamField(required=False)
    assigned_to = serializers.CharField(required=False, allow_blank=True)
    review_status = serializers.CharField(required=False, allow_blank=True)
    ordering = serializers.ChoiceField(
        choices=["created_at", "-created_at"],
        required=False,
    )


class QueueItemNextQuerySerializer(StrictInputSerializer):
    exclude = serializers.CharField(required=False, allow_blank=True)
    before = serializers.UUIDField(required=False)
    review_status = serializers.CharField(required=False, allow_blank=True)
    exclude_review_status = serializers.CharField(required=False, allow_blank=True)
    include_completed = serializers.BooleanField(required=False)
    view_mode = serializers.CharField(required=False, allow_blank=True)
    include_all_annotations = serializers.BooleanField(required=False)


class QueueItemAnnotateDetailQuerySerializer(StrictInputSerializer):
    annotator_id = serializers.UUIDField(required=False)
    include_completed = serializers.BooleanField(required=False)
    view_mode = serializers.CharField(required=False, allow_blank=True)
    review_status = serializers.CharField(required=False, allow_blank=True)
    exclude_review_status = serializers.CharField(required=False, allow_blank=True)
    include_all_annotations = serializers.BooleanField(required=False)
    reserve = serializers.BooleanField(required=False)


class QueueItemNavigationRequestSerializer(StrictInputSerializer):
    exclude = RepeatedStringQueryParamField(required=False, default=list)
    exclude_review_status = serializers.CharField(required=False, allow_blank=True)
    include_completed = serializers.BooleanField(required=False, default=False)


class QueueHardDeleteResultSerializer(serializers.Serializer):
    deleted = serializers.BooleanField()
    hard_deleted = serializers.BooleanField(required=False)
    archived = serializers.BooleanField(required=False)
    queue_id = serializers.UUIDField()


class QueueHardDeleteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueHardDeleteResultSerializer()


class QueueProgressUserProgressSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    completed = serializers.IntegerField()
    pending = serializers.IntegerField()
    in_progress = serializers.IntegerField()
    in_review = serializers.IntegerField()
    skipped = serializers.IntegerField()
    progress_pct = serializers.FloatField()


class QueueProgressAnnotatorStatSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    name = serializers.CharField(allow_null=True, required=False)
    completed = serializers.IntegerField()
    pending = serializers.IntegerField()
    in_progress = serializers.IntegerField()
    in_review = serializers.IntegerField()
    annotations_count = serializers.IntegerField()


class QueueProgressResultSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    pending = serializers.IntegerField()
    in_progress = serializers.IntegerField()
    in_review = serializers.IntegerField()
    completed = serializers.IntegerField()
    skipped = serializers.IntegerField()
    progress_pct = serializers.FloatField()
    annotator_stats = QueueProgressAnnotatorStatSerializer(many=True)
    user_progress = QueueProgressUserProgressSerializer()


class QueueProgressResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueProgressResultSerializer()


class QueueStatusResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = AnnotationQueueSerializer()


class QueueExportAnnotationsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = serializers.ListField(child=serializers.JSONField())


class QueueJsonResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = serializers.JSONField()


class QueueAnalyticsThroughputDailySerializer(serializers.Serializer):
    date = serializers.CharField()
    count = serializers.IntegerField()


class QueueAnalyticsThroughputSerializer(serializers.Serializer):
    daily = QueueAnalyticsThroughputDailySerializer(many=True)
    total_completed = serializers.IntegerField()
    avg_per_day = serializers.FloatField()


class QueueAnalyticsAnnotatorPerformanceSerializer(serializers.Serializer):
    user_id = serializers.CharField(allow_null=True, required=False)
    name = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    completed = serializers.IntegerField()
    last_active = serializers.DateTimeField(allow_null=True, required=False)


class QueueAnalyticsResultSerializer(serializers.Serializer):
    throughput = QueueAnalyticsThroughputSerializer()
    annotator_performance = QueueAnalyticsAnnotatorPerformanceSerializer(many=True)
    label_distribution = serializers.DictField(child=serializers.JSONField())
    status_breakdown = serializers.DictField(child=serializers.IntegerField())
    total = serializers.IntegerField()


class QueueAnalyticsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueAnalyticsResultSerializer()


class QueueExportFieldSerializer(serializers.Serializer):
    id = serializers.CharField()
    label = serializers.CharField()
    column = serializers.CharField()
    data_type = serializers.CharField()
    group = serializers.CharField()
    default = serializers.BooleanField()
    path = serializers.CharField(required=False, allow_blank=True)
    source_type = serializers.CharField(required=False, allow_blank=True)
    kind = serializers.CharField(required=False, allow_blank=True)
    label_id = serializers.UUIDField(required=False)
    slot = serializers.IntegerField(required=False)
    eval_key = serializers.CharField(required=False, allow_blank=True)
    expand_fields = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )


class QueueExportDefaultMappingSerializer(serializers.Serializer):
    field = serializers.CharField()
    column = serializers.CharField()
    enabled = serializers.BooleanField()


class QueueExportFieldsResultSerializer(serializers.Serializer):
    fields = QueueExportFieldSerializer(many=True)
    default_mapping = QueueExportDefaultMappingSerializer(many=True)


class QueueExportFieldsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueExportFieldsResultSerializer()


class QueueAgreementLabelSerializer(serializers.Serializer):
    label_name = serializers.CharField(allow_blank=True, allow_null=True)
    label_type = serializers.CharField(allow_blank=True, allow_null=True)
    agreement_pct = serializers.FloatField(allow_null=True)
    cohens_kappa = serializers.FloatField(allow_null=True)
    disagreement_count = serializers.IntegerField()
    disagreement_items = serializers.ListField(child=serializers.CharField())


class QueueAgreementAnnotatorPairSerializer(serializers.Serializer):
    annotator_1_id = serializers.CharField()
    annotator_2_id = serializers.CharField()
    agreement_pct = serializers.FloatField()
    total_comparisons = serializers.IntegerField()


class QueueAgreementResultSerializer(serializers.Serializer):
    overall_agreement = serializers.FloatField(allow_null=True)
    labels = serializers.DictField(child=QueueAgreementLabelSerializer())
    annotator_pairs = QueueAgreementAnnotatorPairSerializer(many=True)


class QueueAgreementResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueAgreementResultSerializer()


class QueueForSourceQueueSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    instructions = serializers.CharField(allow_blank=True)
    is_default = serializers.BooleanField()


class QueueForSourceItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.CharField()
    source_type = serializers.CharField()
    source_id = serializers.CharField(allow_null=True)


class QueueLabelResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    type = serializers.CharField()
    settings = serializers.JSONField()
    description = serializers.CharField(allow_blank=True, required=False)
    allow_notes = serializers.BooleanField()
    required = serializers.BooleanField()
    order = serializers.IntegerField()


class QueueForSourceEntrySerializer(serializers.Serializer):
    queue = QueueForSourceQueueSerializer()
    item = QueueForSourceItemSerializer(allow_null=True)
    labels = QueueLabelResultSerializer(many=True)
    existing_scores = serializers.DictField(child=serializers.JSONField())
    existing_notes = serializers.CharField(allow_blank=True)
    existing_label_notes = serializers.DictField(child=serializers.CharField())
    span_notes = serializers.ListField(child=serializers.JSONField())
    span_notes_source_id = serializers.CharField(allow_null=True, required=False)


class QueueForSourceResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueForSourceEntrySerializer(many=True)


class AutomationRuleEvaluateResultSerializer(serializers.Serializer):
    matched = serializers.IntegerField()
    added = serializers.IntegerField()
    duplicates = serializers.IntegerField()
    truncated = serializers.BooleanField(required=False)
    error = serializers.CharField(required=False, allow_blank=True)


class AutomationRuleEvaluateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = AutomationRuleEvaluateResultSerializer()


class QueueExportToDatasetResultSerializer(serializers.Serializer):
    dataset_id = serializers.UUIDField()
    dataset_name = serializers.CharField()
    rows_created = serializers.IntegerField()
    columns = serializers.ListField(child=serializers.CharField())


class QueueExportToDatasetResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueExportToDatasetResultSerializer()


class QueueDefaultQueueSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True, required=False)
    instructions = serializers.CharField(allow_blank=True, required=False)
    status = serializers.CharField()
    is_default = serializers.BooleanField()


class QueueDefaultResultSerializer(serializers.Serializer):
    queue = QueueDefaultQueueSerializer()
    labels = QueueLabelResultSerializer(many=True)
    created = serializers.BooleanField()
    action = serializers.ChoiceField(choices=["created", "restored", "fetched"])


class QueueDefaultResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueDefaultResultSerializer()


class QueueAddLabelResultSerializer(serializers.Serializer):
    label = QueueLabelResultSerializer()
    created = serializers.BooleanField()
    reopened_items = serializers.IntegerField()
    queue_status = serializers.CharField()


class QueueAddLabelResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueAddLabelResultSerializer()


class QueueRemoveLabelResultSerializer(serializers.Serializer):
    removed = serializers.BooleanField()


class QueueRemoveLabelResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueRemoveLabelResultSerializer()


class QueueAddItemsResultSerializer(serializers.Serializer):
    added = serializers.IntegerField()
    duplicates = serializers.IntegerField()
    errors = serializers.ListField(child=serializers.CharField())
    queue_status = serializers.CharField()
    total_matching = serializers.IntegerField(required=False)


class QueueAddItemsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueAddItemsResultSerializer()


class QueueBulkRemoveItemsResultSerializer(serializers.Serializer):
    removed = serializers.IntegerField()


class QueueBulkRemoveItemsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueBulkRemoveItemsResultSerializer()


class QueueBulkReviewItemsErrorSerializer(serializers.Serializer):
    item_id = serializers.CharField()
    error = serializers.CharField()


class QueueBulkReviewItemsResultSerializer(serializers.Serializer):
    reviewed = serializers.IntegerField()
    reviewed_item_ids = serializers.ListField(child=serializers.UUIDField())
    errors = QueueBulkReviewItemsErrorSerializer(many=True)
    action = serializers.ChoiceField(choices=["approve", "request_changes", "reject"])


class QueueBulkReviewItemsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueBulkReviewItemsResultSerializer()


class QueueSubmitAnnotationsResultSerializer(serializers.Serializer):
    submitted = serializers.IntegerField()


class QueueSubmitAnnotationsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueSubmitAnnotationsResultSerializer()


class QueueNavigationResultSerializer(serializers.Serializer):
    completed_item_id = serializers.UUIDField(required=False)
    skipped_item_id = serializers.UUIDField(required=False)
    next_item = serializers.JSONField(allow_null=True)


class QueueNavigationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueNavigationResultSerializer()


class QueueNextItemResultSerializer(serializers.Serializer):
    item = serializers.JSONField(allow_null=True)


class QueueNextItemResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueNextItemResultSerializer()


class QueueAnnotateDetailResultSerializer(serializers.Serializer):
    item = serializers.JSONField()
    queue = serializers.JSONField()
    labels = serializers.ListField(child=serializers.JSONField())
    annotations = serializers.ListField(child=serializers.JSONField())
    review_comments = serializers.ListField(child=serializers.JSONField())
    review_threads = serializers.ListField(child=serializers.JSONField())
    existing_notes = serializers.CharField(allow_blank=True)
    span_notes = serializers.ListField(child=serializers.JSONField())
    span_notes_source_id = serializers.CharField(allow_null=True, required=False)
    progress = serializers.JSONField()
    next_item_id = serializers.CharField(allow_null=True, required=False)
    prev_item_id = serializers.CharField(allow_null=True, required=False)


class QueueAnnotateDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueAnnotateDetailResultSerializer()


class QueueAssignItemsResultSerializer(serializers.Serializer):
    assigned = serializers.IntegerField()


class QueueAssignItemsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueAssignItemsResultSerializer()


class QueueReleaseReservationResultSerializer(serializers.Serializer):
    released = serializers.BooleanField()


class QueueReleaseReservationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueReleaseReservationResultSerializer()


class QueueItemAnnotationsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = ScoreSerializer(many=True)


class QueueDiscussionResultSerializer(serializers.Serializer):
    review_comments = serializers.ListField(child=serializers.JSONField())
    review_threads = serializers.ListField(child=serializers.JSONField())
    comment = serializers.JSONField(required=False)
    thread = serializers.JSONField(required=False)


class QueueDiscussionResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueDiscussionResultSerializer()


class QueueReviewItemResultSerializer(serializers.Serializer):
    reviewed_item_id = serializers.UUIDField()
    action = serializers.CharField()
    next_item = serializers.JSONField(allow_null=True)
    review_comments = serializers.ListField(child=serializers.JSONField())
    review_threads = serializers.ListField(child=serializers.JSONField())


class QueueReviewItemResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueReviewItemResultSerializer()


class QueueImportAnnotationsResultSerializer(serializers.Serializer):
    imported = serializers.IntegerField()


class QueueImportAnnotationsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = QueueImportAnnotationsResultSerializer()


class AutomationRuleEvaluateAcceptedResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    workflow_id = serializers.CharField()
    message = serializers.CharField()


AUTOMATION_RULE_VALUE_SCHEMA = {
    "description": (
        "Rule comparison value. Can be a scalar, list, object, boolean, "
        "or null depending on the operator."
    ),
}

AUTOMATION_RULE_CONDITION_RULE_SCHEMA = {
    "type": "object",
    "properties": {
        "field": {"type": "string", "minLength": 1},
        "op": {"type": "string", "default": "eq", "minLength": 1},
        "value": AUTOMATION_RULE_VALUE_SCHEMA,
    },
    "required": ["field"],
    "additionalProperties": False,
}

AUTOMATION_RULE_RULES_SCHEMA = {
    "type": "array",
    "items": AUTOMATION_RULE_CONDITION_RULE_SCHEMA,
}


class AutomationRuleRulesField(serializers.JSONField):
    class Meta:
        swagger_schema_fields = AUTOMATION_RULE_RULES_SCHEMA

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not isinstance(value, list):
            raise serializers.ValidationError("rules must be a list.")

        cleaned = []
        allowed_keys = {"field", "op", "value"}
        for index, rule in enumerate(value):
            if not isinstance(rule, dict):
                raise serializers.ValidationError(f"rules[{index}] must be an object.")
            unknown = sorted(set(rule) - allowed_keys)
            if unknown:
                raise serializers.ValidationError(
                    f"rules[{index}] has unknown field(s): {', '.join(unknown)}"
                )
            field = str(rule.get("field") or "").strip()
            if not field:
                raise serializers.ValidationError(f"rules[{index}].field is required.")
            op = str(rule.get("op") or "eq").strip()
            if not op:
                raise serializers.ValidationError(f"rules[{index}].op is required.")

            cleaned_rule = {"field": field, "op": op}
            if "value" in rule:
                cleaned_rule["value"] = rule["value"]
            cleaned.append(cleaned_rule)
        return cleaned


class AutomationRuleScopeSerializer(StrictInputSerializer):
    dataset_id = serializers.UUIDField(required=False)
    project_id = serializers.UUIDField(required=False)
    is_voice_call = serializers.BooleanField(required=False)
    remove_simulation_calls = serializers.BooleanField(required=False)


class AutomationRuleConditionsSerializer(StrictInputSerializer):
    operator = serializers.ChoiceField(choices=["and"], required=False, default="and")
    filter = filter_list_field(required=False)
    scope = AutomationRuleScopeSerializer(required=False)
    # `rules` is the deprecated ORM-field rule shape. Keep it typed for
    # existing saved rules while new UI/API writes use `filter`.
    rules = AutomationRuleRulesField(required=False)

    def validate(self, attrs):
        if attrs.get("filter") and attrs.get("rules"):
            raise serializers.ValidationError(
                "Use either filter or rules in automation rule conditions, not both."
            )
        return attrs


class AssignItemsSerializer(StrictInputSerializer):
    item_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
    )
    user_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    action = serializers.ChoiceField(
        choices=["add", "set", "remove"],
        required=False,
        default="add",
    )


class DiscussionCommentRequestSerializer(StrictInputSerializer):
    comment = serializers.CharField(required=False, allow_blank=True)
    label_id = serializers.UUIDField(required=False)
    target_annotator_id = serializers.UUIDField(required=False)
    thread_id = serializers.UUIDField(required=False)
    mentioned_user_ids = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
    )

    def validate(self, attrs):
        if not str(attrs.get("comment") or "").strip():
            raise serializers.ValidationError("Comment text is required.")
        return attrs


class DiscussionThreadStatusRequestSerializer(StrictInputSerializer):
    comment = serializers.CharField(required=False, allow_blank=True)


class DiscussionReactionRequestSerializer(StrictInputSerializer):
    emoji = serializers.CharField(required=False, allow_blank=True, max_length=16)

    def validate(self, attrs):
        if not str(attrs.get("emoji") or "").strip():
            raise serializers.ValidationError("emoji is required.")
        return attrs


class ReviewLabelCommentRequestSerializer(StrictInputSerializer):
    label_id = serializers.UUIDField(required=False)
    target_annotator_id = serializers.UUIDField(required=False)
    comment = serializers.CharField(required=False, allow_blank=True)


class ReviewItemRequestSerializer(StrictInputSerializer):
    action = serializers.ChoiceField(
        choices=["approve", "request_changes", "reject", "comment"]
    )
    notes = serializers.CharField(required=False, allow_blank=True)
    label_comments = ReviewLabelCommentRequestSerializer(
        many=True,
        required=False,
        default=list,
    )


class BulkReviewItemsRequestSerializer(StrictInputSerializer):
    item_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
    )
    action = serializers.ChoiceField(choices=["approve", "request_changes", "reject"])
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        action = attrs.get("action")
        notes = str(attrs.get("notes") or "").strip()
        if action in {"request_changes", "reject"} and not notes:
            raise serializers.ValidationError(
                "Feedback is required when requesting changes."
            )
        return attrs


class ImportAnnotationEntrySerializer(StrictInputSerializer):
    label_id = serializers.UUIDField()
    value = serializers.JSONField()
    notes = serializers.CharField(required=False, allow_blank=True)
    score_source = serializers.CharField(required=False, allow_blank=True)


class ImportAnnotationsSerializer(StrictInputSerializer):
    annotations = ImportAnnotationEntrySerializer(many=True, allow_empty=False)
    annotator_id = serializers.UUIDField(required=False)


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


class QueueItemReviewCommentSerializer(serializers.ModelSerializer):
    thread_id = serializers.SerializerMethodField()
    thread_status = serializers.SerializerMethodField()
    thread_scope = serializers.SerializerMethodField()
    blocking = serializers.SerializerMethodField()
    reviewer_id = serializers.SerializerMethodField()
    reviewer_name = serializers.SerializerMethodField()
    reviewer_email = serializers.SerializerMethodField()
    label_id = serializers.SerializerMethodField()
    label_name = serializers.SerializerMethodField()
    target_annotator_id = serializers.SerializerMethodField()
    target_annotator_name = serializers.SerializerMethodField()
    target_annotator_email = serializers.SerializerMethodField()
    mentioned_users = serializers.SerializerMethodField()
    reactions = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()

    class Meta:
        model = QueueItemReviewComment
        fields = [
            "id",
            "thread_id",
            "thread_status",
            "thread_scope",
            "blocking",
            "action",
            "comment",
            "label_id",
            "label_name",
            "target_annotator_id",
            "target_annotator_name",
            "target_annotator_email",
            "mentioned_users",
            "reactions",
            "reviewer_id",
            "reviewer_name",
            "reviewer_email",
            "can_edit",
            "can_delete",
            "created_at",
            "updated_at",
        ]

    def get_thread_id(self, obj):
        return str(obj.thread_id) if obj.thread_id else None

    def get_thread_status(self, obj):
        return obj.thread.status if obj.thread_id and obj.thread else None

    def get_thread_scope(self, obj):
        return obj.thread.scope if obj.thread_id and obj.thread else None

    def get_blocking(self, obj):
        return bool(obj.thread.blocking) if obj.thread_id and obj.thread else False

    def get_reviewer_id(self, obj):
        return str(obj.reviewer_id) if obj.reviewer_id else None

    def get_reviewer_name(self, obj):
        return obj.reviewer.name if obj.reviewer else None

    def get_reviewer_email(self, obj):
        return obj.reviewer.email if obj.reviewer else None

    def get_label_id(self, obj):
        return str(obj.label_id) if obj.label_id else None

    def get_label_name(self, obj):
        return obj.label.name if obj.label else None

    def get_target_annotator_id(self, obj):
        return str(obj.target_annotator_id) if obj.target_annotator_id else None

    def get_target_annotator_name(self, obj):
        return obj.target_annotator.name if obj.target_annotator else None

    def get_target_annotator_email(self, obj):
        return obj.target_annotator.email if obj.target_annotator else None

    def get_mentioned_users(self, obj):
        return [
            {
                "id": str(user.id),
                "name": user.name,
                "email": user.email,
            }
            for user in obj.mentioned_users.all()
        ]

    def get_reactions(self, obj):
        request = self.context.get("request")
        current_user_id = (
            str(request.user.id)
            if request
            and getattr(request, "user", None)
            and request.user.is_authenticated
            else None
        )
        reactions = obj.reactions or {}
        return [
            {
                "emoji": emoji,
                "count": len(user_ids),
                "user_ids": [str(user_id) for user_id in user_ids],
                "reacted_by_current_user": bool(
                    current_user_id
                    and current_user_id in {str(uid) for uid in user_ids}
                ),
            }
            for emoji, user_ids in reactions.items()
            if isinstance(user_ids, list)
        ]

    def get_can_edit(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        return bool(
            user
            and getattr(user, "is_authenticated", False)
            and obj.action == QueueItemReviewComment.ACTION_COMMENT
            and str(obj.reviewer_id or "") == str(user.id)
        )

    def get_can_delete(self, obj):
        return self.get_can_edit(obj)


class QueueItemReviewThreadSerializer(serializers.ModelSerializer):
    created_by_id = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    created_by_email = serializers.SerializerMethodField()
    label_id = serializers.SerializerMethodField()
    label_name = serializers.SerializerMethodField()
    target_annotator_id = serializers.SerializerMethodField()
    target_annotator_name = serializers.SerializerMethodField()
    target_annotator_email = serializers.SerializerMethodField()
    comments = QueueItemReviewCommentSerializer(many=True, read_only=True)

    class Meta:
        model = QueueItemReviewThread
        fields = [
            "id",
            "action",
            "scope",
            "status",
            "blocking",
            "label_id",
            "label_name",
            "target_annotator_id",
            "target_annotator_name",
            "target_annotator_email",
            "created_by_id",
            "created_by_name",
            "created_by_email",
            "addressed_at",
            "resolved_at",
            "reopened_at",
            "created_at",
            "comments",
        ]

    def get_created_by_id(self, obj):
        return str(obj.created_by_id) if obj.created_by_id else None

    def get_created_by_name(self, obj):
        return obj.created_by.name if obj.created_by else None

    def get_created_by_email(self, obj):
        return obj.created_by.email if obj.created_by else None

    def get_label_id(self, obj):
        return str(obj.label_id) if obj.label_id else None

    def get_label_name(self, obj):
        return obj.label.name if obj.label else None

    def get_target_annotator_id(self, obj):
        return str(obj.target_annotator_id) if obj.target_annotator_id else None

    def get_target_annotator_name(self, obj):
        return obj.target_annotator.name if obj.target_annotator else None

    def get_target_annotator_email(self, obj):
        return obj.target_annotator.email if obj.target_annotator else None


class SubmitAnnotationEntrySerializer(StrictInputSerializer):
    label_id = serializers.UUIDField()
    value = serializers.JSONField()
    notes = serializers.CharField(required=False, allow_blank=True)


class SubmitAnnotationsSerializer(StrictInputSerializer):
    annotations = SubmitAnnotationEntrySerializer(many=True, allow_empty=False)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    item_notes = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default=None
    )


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
                "workflow_status": (
                    "resubmitted"
                    if item.review_status == "pending_review"
                    and QueueItemReviewThread.objects.filter(
                        queue_item=item,
                        blocking=True,
                        status=QueueItemReviewThread.STATUS_ADDRESSED,
                        deleted=False,
                    ).exists()
                    else "in_review"
                    if item.review_status == "pending_review"
                    else "needs_changes"
                    if item.review_status == "rejected"
                    else item.status
                ),
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
            "review_comments": QueueItemReviewCommentSerializer(
                instance.get("review_comments", []),
                many=True,
                context=self.context,
            ).data,
            "review_threads": QueueItemReviewThreadSerializer(
                instance.get("review_threads", []),
                many=True,
                context=self.context,
            ).data,
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
    conditions = AutomationRuleConditionsSerializer(required=False)

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

    def validate_conditions(self, value):
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("conditions must be an object.")
        if "filters" in value:
            raise serializers.ValidationError(
                "Use conditions.filter with the canonical filter list."
            )

        conditions = dict(value)
        scope = conditions.get("scope")
        if scope is not None:
            conditions["scope"] = {
                key: str(item) if isinstance(item, uuid.UUID) else item
                for key, item in dict(scope).items()
            }
        return conditions

    def validate(self, attrs):
        attrs = super().validate(attrs)
        source_type = attrs.get(
            "source_type", getattr(self.instance, "source_type", None)
        )
        conditions = attrs.get("conditions")
        if not conditions and self.instance is not None:
            conditions = self.instance.conditions or {}

        rules = (conditions or {}).get("rules") or []
        if rules and source_type in FIELD_MAPPING:
            valid_fields = set(FIELD_MAPPING[source_type])
            invalid_fields = sorted(
                {
                    rule.get("field")
                    for rule in rules
                    if rule.get("field") not in valid_fields
                }
            )
            if invalid_fields:
                raise serializers.ValidationError(
                    {
                        "conditions": (
                            "Unknown rule field(s): "
                            f"{', '.join(str(field) for field in invalid_fields)}"
                        )
                    }
                )
        return attrs
