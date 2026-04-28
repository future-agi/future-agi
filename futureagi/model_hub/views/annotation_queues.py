import json
from datetime import timedelta

import structlog
from django.db.models import Count, Max, Prefetch, Q
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models.user import User
from model_hub.models.annotation_queues import (
    SOURCE_TYPE_FK_MAP,
    VALID_STATUS_TRANSITIONS,
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AnnotationQueueLabel,
    AutomationRule,
    QueueItem,
    QueueItemAssignment,
)
from model_hub.models.choices import (
    AnnotationQueueStatusChoices,
    AnnotatorRole,
    AssignmentStrategy,
    QueueItemStatus,
    ScoreSource,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import SCORE_SOURCE_FK_MAP, Score
from model_hub.serializers.annotation_queues import (
    AddItemsSerializer,
    AnnotateDetailSerializer,
    AnnotationQueueSerializer,
    AutomationRuleSerializer,
    QueueItemSerializer,
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
    resolve_source_preview,
)
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination
from tracer.models.project import Project

logger = structlog.get_logger(__name__)

# Shared cap for filter-mode bulk add. Phase 11 may introduce an async job
# path for selections exceeding this; until then, the endpoint errors with
# ``selection_too_large`` so the UI can prompt the user to narrow the filter.
MAX_SELECTION_CAP = 10_000

# Dispatch table for filter-mode resolvers. Later phases (6, 8) extend this
# with ``trace_session`` / ``call_execution`` entries alongside their own
# sibling resolver functions in ``model_hub.services.bulk_selection``.
FILTER_MODE_RESOLVERS = {
    "trace": resolve_filtered_trace_ids,
    "observation_span": resolve_filtered_span_ids,
    "trace_session": resolve_filtered_session_ids,
    "call_execution": resolve_filtered_call_execution_ids,
}


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
            AnnotationQueueAnnotator.objects.filter(
                queue=queue, deleted=False
            ).values_list("user_id", flat=True)
        )
        if member_ids:
            assignments = [
                QueueItemAssignment(queue_item=item, user_id=uid)
                for item in created
                for uid in member_ids
            ]
            QueueItemAssignment.objects.bulk_create(
                assignments, ignore_conflicts=True
            )

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

        status = self.request.query_params.get("status", None)
        search = self.request.query_params.get("search", None)
        include_counts = (
            self.request.query_params.get("include_counts", "").lower() == "true"
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
                check_ee_feature(
                    EEFeature.REVIEW_WORKFLOW, org_id=str(org.id)
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
        is_manager = AnnotationQueueAnnotator.objects.filter(
            queue=instance,
            user=request.user,
            role=AnnotatorRole.MANAGER.value,
            deleted=False,
        ).exists()
        if not is_manager:
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
        instance = self.get_object()
        self.perform_destroy(instance)
        return self._gm.success_response({"deleted": True})

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

        queue.deleted = False
        queue.deleted_at = None
        queue.save(update_fields=["deleted", "deleted_at", "updated_at"])
        serializer = self.get_serializer(queue)
        return self._gm.success_response(serializer.data)

    @action(detail=True, methods=["get"], url_path="progress")
    def progress(self, request, pk=None):
        queue = self.get_object()
        items_qs = QueueItem.objects.filter(queue=queue, deleted=False)
        total = items_qs.count()

        status_counts = {}
        for row in items_qs.values("status").annotate(cnt=Count("id")):
            status_counts[row["status"]] = row["cnt"]

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
                in_progress_cnt=Count("id", filter=Q(status="in_progress")),
            )
        ):
            uid = str(row["assigned_to"])
            assigned_stats[uid] = {
                "user_id": uid,
                "name": row["assigned_to__name"],
                "completed": row["completed_cnt"],
                "pending": row["pending_cnt"],
                "in_progress": row["in_progress_cnt"],
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
                    "annotations_count": row["annotations_count"],
                }

        # Per-user progress: items assigned to the user OR items with no
        # active assignments (available to everyone).
        has_active_assignment = Q(assignments__deleted=False)
        user_items = items_qs.filter(
            Q(assignments__user=request.user, assignments__deleted=False)
            | ~has_active_assignment
        ).distinct()
        user_total = user_items.count()
        user_status_counts = {}
        for row in user_items.values("status").annotate(cnt=Count("id", distinct=True)):
            user_status_counts[row["status"]] = row["cnt"]
        user_completed = user_status_counts.get(QueueItemStatus.COMPLETED.value, 0)
        user_progress_pct = (
            round((user_completed / user_total) * 100, 1) if user_total > 0 else 0
        )

        return self._gm.success_response(
            {
                "total": total,
                "pending": status_counts.get("pending", 0),
                "in_progress": status_counts.get("in_progress", 0),
                "completed": completed,
                "skipped": status_counts.get("skipped", 0),
                "progress_pct": progress_pct,
                "annotator_stats": list(assigned_stats.values()),
                "user_progress": {
                    "total": user_total,
                    "completed": user_completed,
                    "pending": user_status_counts.get(QueueItemStatus.PENDING.value, 0),
                    "in_progress": user_status_counts.get(
                        QueueItemStatus.IN_PROGRESS.value, 0
                    ),
                    "skipped": user_status_counts.get(QueueItemStatus.SKIPPED.value, 0),
                    "progress_pct": user_progress_pct,
                },
            }
        )

    @action(detail=True, methods=["post"], url_path="update-status")
    def update_status(self, request, pk=None):
        queue = self.get_object()
        new_status = request.data.get("status")

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

    @action(detail=True, methods=["get"], url_path="export")
    def export_annotations(self, request, pk=None):
        """Export all items with their annotations."""
        queue = self.get_object()
        items_qs = QueueItem.objects.filter(queue=queue, deleted=False)

        status_filter = request.query_params.get("status")
        if status_filter:
            items_qs = items_qs.filter(status=status_filter)

        items_qs = items_qs.order_by("order")

        # Batch-fetch all scores for this queue's items in a single query
        all_scores = (
            Score.objects.filter(
                queue_item__queue=queue, queue_item__deleted=False, deleted=False
            )
            .select_related("annotator", "label")
            .order_by("created_at")
        )
        if status_filter:
            all_scores = all_scores.filter(queue_item__status=status_filter)
        scores_by_item = {}
        for score in all_scores:
            scores_by_item.setdefault(score.queue_item_id, []).append(score)

        result = []
        for item in items_qs:
            annotations = scores_by_item.get(item.id, [])
            result.append(
                {
                    "item_id": str(item.id),
                    "source_type": item.source_type,
                    "status": item.status,
                    "order": item.order,
                    "annotations": [
                        {
                            "label_id": str(ann.label_id),
                            "label_name": ann.label.name if ann.label else None,
                            "value": ann.value,
                            "score_source": ann.score_source,
                            "annotator_name": (
                                ann.annotator.name if ann.annotator else None
                            ),
                            "created_at": (
                                ann.created_at.isoformat() if ann.created_at else None
                            ),
                        }
                        for ann in annotations
                    ],
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

    @action(detail=True, methods=["post"], url_path="export-to-dataset")
    def export_to_dataset(self, request, pk=None):
        """Export annotated items to a dataset with columns and cells."""
        from model_hub.models.choices import (
            AnnotationTypeChoices,
            DataTypeChoices,
            SourceChoices,
            StatusType,
        )
        from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

        LABEL_TYPE_TO_DATA_TYPE = {
            AnnotationTypeChoices.NUMERIC.value: DataTypeChoices.FLOAT.value,
            AnnotationTypeChoices.TEXT.value: DataTypeChoices.TEXT.value,
            AnnotationTypeChoices.CATEGORICAL.value: DataTypeChoices.ARRAY.value,
            AnnotationTypeChoices.STAR.value: DataTypeChoices.FLOAT.value,
            AnnotationTypeChoices.THUMBS_UP_DOWN.value: DataTypeChoices.TEXT.value,
        }

        queue = self.get_object()
        dataset_id = request.data.get("dataset_id")
        dataset_name = request.data.get("dataset_name")
        status_filter = request.data.get("status_filter", "completed")

        if not dataset_id and not dataset_name:
            return self._gm.bad_request(
                "Either dataset_id or dataset_name is required."
            )

        # Get or create dataset
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

        # Filter items with select_related to avoid N+1 on source FK lookups
        items_qs = QueueItem.objects.filter(queue=queue, deleted=False).select_related(
            "trace",
            "observation_span",
            "dataset_row",
            "prototype_run",
            "call_execution",
            "trace_session",
        )
        if status_filter:
            items_qs = items_qs.filter(status=status_filter)

        # Get max order in dataset
        max_order = (
            Row.objects.filter(dataset=dataset, deleted=False)
            .order_by("-order")
            .values_list("order", flat=True)
            .first()
        ) or 0

        # Batch-fetch all scores for this queue's items in a single query
        all_scores = Score.objects.filter(
            queue_item__queue=queue, queue_item__deleted=False, deleted=False
        ).select_related("label")
        if status_filter:
            all_scores = all_scores.filter(queue_item__status=status_filter)
        scores_by_item = {}
        for score in all_scores:
            scores_by_item.setdefault(score.queue_item_id, []).append(score)

        # --- Collect unique annotation labels ---
        label_map = {}  # label_name -> label_type
        for scores in scores_by_item.values():
            for score in scores:
                label_name = score.label.name if score.label else str(score.label_id)
                label_type = score.label.type if score.label else "text"
                label_map[label_name] = label_type

        # --- Define and reuse/create columns ---
        fixed_columns_def = [
            ("source_type", "text", SourceChoices.OTHERS.value),
            ("input", "text", SourceChoices.OTHERS.value),
            ("output", "text", SourceChoices.OTHERS.value),
        ]

        existing_columns = {
            col.name: col
            for col in Column.objects.filter(dataset=dataset, deleted=False)
        }

        columns = {}  # name -> Column instance
        new_columns = []

        for col_name, data_type, source in fixed_columns_def:
            if col_name in existing_columns:
                columns[col_name] = existing_columns[col_name]
            else:
                col = Column(
                    name=col_name,
                    data_type=data_type,
                    source=source,
                    dataset=dataset,
                    status=StatusType.COMPLETED.value,
                )
                new_columns.append(col)
                columns[col_name] = col

        for label_name, label_type in label_map.items():
            data_type = LABEL_TYPE_TO_DATA_TYPE.get(label_type, "text")
            if label_name in existing_columns:
                columns[label_name] = existing_columns[label_name]
            else:
                col = Column(
                    name=label_name,
                    data_type=data_type,
                    source=SourceChoices.ANNOTATION_LABEL.value,
                    dataset=dataset,
                    status=StatusType.COMPLETED.value,
                )
                new_columns.append(col)
                columns[label_name] = col

        if new_columns:
            Column.objects.bulk_create(new_columns)

        # Update column_order and column_config for new columns
        if new_columns:
            column_order = list(dataset.column_order or [])
            column_config = dict(dataset.column_config or {})
            for col in new_columns:
                col_id_str = str(col.id)
                column_order.append(col_id_str)
                column_config[col_id_str] = {"is_frozen": False, "is_visible": True}
            dataset.column_order = column_order
            dataset.column_config = column_config
            dataset.save(update_fields=["column_order", "column_config"])

        # --- Create Rows (metadata preserved for auditability) ---
        items_list = list(items_qs.order_by("order"))
        rows_to_create = []
        for i, item in enumerate(items_list):
            rows_to_create.append(
                Row(
                    dataset=dataset,
                    order=max_order + i + 1,
                    metadata={
                        "queue_id": str(queue.id),
                        "queue_item_id": str(item.id),
                    },
                )
            )

        if rows_to_create:
            Row.objects.bulk_create(rows_to_create, batch_size=500)

        # --- Create Cells ---
        def _to_cell_str(val):
            if val is None:
                return ""
            if isinstance(val, str):
                return val
            return json.dumps(val, default=str)

        def _extract_input_output(content):
            source_type = content.get("type", "")
            if source_type == "dataset_row":
                fields = content.get("fields", {})
                return _to_cell_str(fields.get("input", "")), _to_cell_str(
                    fields.get("output", "")
                )
            elif source_type == "prototype_run":
                return _to_cell_str(content.get("prompt")), _to_cell_str(
                    content.get("response")
                )
            else:
                return _to_cell_str(content.get("input")), _to_cell_str(
                    content.get("output")
                )

        cells_to_create = []
        for row, item in zip(rows_to_create, items_list):
            # source_type cell
            cells_to_create.append(
                Cell(
                    dataset=dataset,
                    row=row,
                    column=columns["source_type"],
                    value=item.source_type or "",
                )
            )

            # input/output cells
            content = resolve_source_content(item)
            input_val, output_val = _extract_input_output(content)
            cells_to_create.append(
                Cell(
                    dataset=dataset,
                    row=row,
                    column=columns["input"],
                    value=input_val,
                )
            )
            cells_to_create.append(
                Cell(
                    dataset=dataset,
                    row=row,
                    column=columns["output"],
                    value=output_val,
                )
            )

            # Label cells — use first annotator's value
            item_scores = scores_by_item.get(item.id, [])
            label_first_value = {}
            for score in item_scores:
                label_name = score.label.name if score.label else str(score.label_id)
                if label_name not in label_first_value:
                    label_first_value[label_name] = score.value

            for label_name in label_map:
                if label_name in columns:
                    value = label_first_value.get(label_name, "")
                    cells_to_create.append(
                        Cell(
                            dataset=dataset,
                            row=row,
                            column=columns[label_name],
                            value=_to_cell_str(value),
                        )
                    )

        if cells_to_create:
            Cell.objects.bulk_create(cells_to_create, batch_size=500)

        # --- Backfill empty cells for pre-existing rows ---
        if new_columns:
            pre_existing_rows = Row.objects.filter(
                dataset=dataset, deleted=False
            ).exclude(id__in=[r.id for r in rows_to_create])
            backfill_cells = []
            for row in pre_existing_rows:
                for col in new_columns:
                    backfill_cells.append(
                        Cell(dataset=dataset, row=row, column=col, value="")
                    )
            if backfill_cells:
                Cell.objects.bulk_create(backfill_cells, batch_size=500)

        return self._gm.success_response(
            {
                "dataset_id": str(dataset.id),
                "dataset_name": dataset.name,
                "rows_created": len(rows_to_create),
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

        project_id = request.data.get("project_id")
        dataset_id = request.data.get("dataset_id")
        agent_definition_id = request.data.get("agent_definition_id")

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

        queue, created = AnnotationQueue.objects.get_or_create(
            **lookup,
            is_default=True,
            deleted=False,
            defaults={
                "name": f"Default - {getattr(entity, 'name', None) or getattr(entity, 'agent_name', str(entity))}",
                "description": f"Default annotation queue for {getattr(entity, 'name', None) or getattr(entity, 'agent_name', str(entity))}",
                "status": AnnotationQueueStatusChoices.ACTIVE.value,
                "organization": org,
                "created_by": request.user,
                **defaults_extra,
            },
        )

        # Auto-add creator as manager so they can see Settings/Agreement tabs
        if created:
            AnnotationQueueAnnotator.objects.create(
                queue=queue,
                user=request.user,
                role=AnnotatorRole.MANAGER.value,
            )

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
                "created": created,
            }
        )

    @action(detail=True, methods=["post"], url_path="add-label")
    def add_label(self, request, pk=None):
        """
        Add a label to an annotation queue.
        Labels apply to all sources in the queue's project (for default queues).
        Queue items are created lazily when someone actually annotates.
        """
        queue = self.get_object()
        label_id = request.data.get("label_id")
        required = _is_truthy(request.data.get("required", True))

        if not label_id:
            return self._gm.bad_request("label_id is required.")

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
            }
        )

    @action(detail=True, methods=["post"], url_path="remove-label")
    def remove_label(self, request, pk=None):
        """Remove a label from an annotation queue."""
        queue = self.get_object()
        label_id = request.data.get("label_id")

        if not label_id:
            return self._gm.bad_request("label_id is required.")

        deleted_count = AnnotationQueueLabel.objects.filter(
            queue=queue, label_id=label_id, deleted=False
        ).update(deleted=True, deleted_at=timezone.now())

        if deleted_count == 0:
            return self._gm.not_found("Label not found in this queue.")

        return self._gm.success_response({"removed": True})

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
        for src in sources:
            st = src.get("source_type")
            if not st or not src.get("source_id"):
                return self._gm.bad_request(
                    "Each source must have source_type and source_id."
                )
            if st not in SOURCE_TYPE_FK_MAP:
                return self._gm.bad_request(f"Invalid source_type: {st}")

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
            queue, item, source_type_for_scores, source_id_for_scores
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

            # Fetch existing scores by this user for these labels on this source
            existing_scores = {}
            existing_notes = ""
            existing_label_notes = {}
            fk_field = SCORE_SOURCE_FK_MAP.get(source_type_for_scores)
            if fk_field and source_id_for_scores:
                label_ids = [ql.label_id for ql in queue_labels]
                user_scores = Score.objects.filter(
                    **{f"{fk_field}_id": source_id_for_scores},
                    source_type=source_type_for_scores,
                    label_id__in=label_ids,
                    annotator=request.user,
                    deleted=False,
                )
                for sc in user_scores:
                    existing_scores[str(sc.label_id)] = sc.value
                    if sc.notes:
                        existing_label_notes[str(sc.label_id)] = sc.notes

            # Include span_notes for observation_span sources
            span_notes = []
            if source_type_for_scores == "observation_span" and source_id_for_scores:
                from tracer.models.span_notes import SpanNotes

                db_notes = list(
                    SpanNotes.objects.filter(span_id=source_id_for_scores)
                    .select_related("created_by_user")
                    .order_by("-created_at")
                )
                span_notes = [
                    {
                        "id": str(note.id),
                        "notes": note.notes,
                        "annotator": note.created_by_annotator
                        or (
                            note.created_by_user.name
                            if note.created_by_user_id
                            else None
                        ),
                        "created_at": note.created_at.isoformat(),
                    }
                    for note in db_notes
                ]
                # Pre-populate existing_notes from the current user's own SpanNote
                user_span_note = next(
                    (n for n in db_notes if n.created_by_user_id == request.user.pk),
                    None,
                )
                if user_span_note:
                    existing_notes = user_span_note.notes

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
                    }
                    if item
                    else None
                ),
                "labels": labels,
                "existing_scores": existing_scores,
                "existing_notes": existing_notes,
                "existing_label_notes": existing_label_notes,
                "span_notes": span_notes,
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
                _build_queue_entry(queue, item, item.source_type, source_fk_id)
            )

        # For default queues that DON'T have queue items for these sources,
        # still return them so labels are available project-wide
        missing_default_ids = default_queue_ids - seen_queues
        if missing_default_ids:
            from tracer.models.project import Project

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
                        )
                    )

        return self._gm.success_response(results)


class QueueItemViewSet(BaseModelViewSetMixinWithUserOrg, viewsets.ModelViewSet):
    serializer_class = QueueItemSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = ExtendedPageNumberPagination
    queryset = QueueItem.objects.all()
    _gm = GeneralMethods()

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

        status = self.request.query_params.get("status")
        source_type = self.request.query_params.get("source_type")
        assigned_to = self.request.query_params.get("assigned_to")

        if status:
            queryset = queryset.filter(status=status)
        if source_type:
            queryset = queryset.filter(source_type=source_type)
        if assigned_to == "me":
            queryset = queryset.filter(
                assignments__user=self.request.user,
                assignments__deleted=False,
            ).distinct()

        review_status = self.request.query_params.get("review_status")
        if review_status:
            queryset = queryset.filter(review_status=review_status)

        return queryset.order_by("order", "-created_at")

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
            resolver_kwargs = dict(
                project_id=project_id,
                filters=filter_payload,
                exclude_ids=exclude_ids,
                organization=request.organization,
                workspace=getattr(request, "workspace", None),
                cap=MAX_SELECTION_CAP,
                user=request.user,
            )
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

    @action(detail=False, methods=["post"], url_path="bulk-remove")
    def bulk_remove(self, request, queue_id=None):
        item_ids = request.data.get("item_ids", [])
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
        self, queue_id, exclude_id=None, exclude_ids=None, user=None
    ):
        """Get the next pending item in the queue. Prefers items assigned to user. Skips reserved items."""
        now = timezone.now()
        base_qs = QueueItem.objects.filter(
            queue_id=queue_id,
            status__in=[
                QueueItemStatus.PENDING.value,
                QueueItemStatus.IN_PROGRESS.value,
            ],
            deleted=False,
        ).order_by("order", "created_at")
        if exclude_ids:
            base_qs = base_qs.exclude(id__in=exclude_ids)
        elif exclude_id:
            base_qs = base_qs.exclude(id=exclude_id)

        # Exclude items reserved by others (unless expired)
        available_qs = base_qs.filter(
            Q(reserved_by__isnull=True)
            | Q(reserved_by=user)
            | Q(reservation_expires_at__lt=now)
        )

        return available_qs.first()

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

        if queue.status != AnnotationQueueStatusChoices.ACTIVE.value:
            return self._gm.bad_request(
                "Annotations can only be submitted when the queue is active."
            )

        try:
            item = QueueItem.objects.get(
                pk=pk,
                queue_id=queue_id,
                organization=request.organization,
                deleted=False,
            )
        except QueueItem.DoesNotExist:
            return self._gm.not_found("Queue item not found.")

        # Enforce assignment ownership: when auto_assign is False (manual mode),
        # only assigned annotators (or managers/reviewers) may submit.
        # When auto_assign is True, anyone can annotate any item.
        if not item.queue.auto_assign:
            has_assignments = QueueItemAssignment.objects.filter(
                queue_item=item, deleted=False
            ).exists()
            if has_assignments:
                is_assigned = QueueItemAssignment.objects.filter(
                    queue_item=item, user=request.user, deleted=False
                ).exists()
                if not is_assigned:
                    is_manager = AnnotationQueueAnnotator.objects.filter(
                        queue_id=queue_id,
                        user=request.user,
                        role__in=[
                            AnnotatorRole.MANAGER.value,
                            AnnotatorRole.REVIEWER.value,
                        ],
                        deleted=False,
                    ).exists()
                    if not is_manager:
                        return self._gm.forbidden_response(
                            "This item is assigned to another annotator."
                        )

        serializer = SubmitAnnotationsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        annotations_data = serializer.validated_data["annotations"]
        notes = serializer.validated_data.get("notes", "")
        submitted = 0

        # Resolve source FK for Score creation
        source_fk_field = SCORE_SOURCE_FK_MAP.get(item.source_type)
        source_obj = getattr(item, source_fk_field) if source_fk_field else None

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

            # Upsert Score (unified annotation primitive)
            # Use no_workspace_objects + _id fields to avoid the LEFT JOIN
            # on nullable workspace FK that triggers PostgreSQL's "FOR UPDATE
            # cannot be applied to the nullable side of an outer join".
            if source_obj and source_fk_field:
                Score.no_workspace_objects.update_or_create(
                    **{f"{source_fk_field}_id": source_obj.pk},
                    label_id=label.pk,
                    annotator_id=request.user.pk,
                    deleted=False,
                    defaults={
                        "source_type": item.source_type,
                        "value": value,
                        "score_source": "human",
                        "notes": notes,
                        "queue_item": item,
                        "organization": request.organization,
                    },
                )
                submitted += 1

        # Update item status to in_progress if pending
        if item.status == QueueItemStatus.PENDING.value:
            item.status = QueueItemStatus.IN_PROGRESS.value
            item.save(update_fields=["status", "updated_at"])

        return self._gm.success_response({"submitted": submitted})

    def _maybe_auto_complete_queue(self, queue_id):
        """Auto-complete queue if all items are done (not for default queues)."""
        pending_count = QueueItem.objects.filter(
            queue_id=queue_id,
            deleted=False,
            status__in=[
                QueueItemStatus.PENDING.value,
                QueueItemStatus.IN_PROGRESS.value,
            ],
        ).count()
        if pending_count == 0:
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
        if current_pk and str(current_pk) not in exclude_ids:
            exclude_ids.append(str(current_pk))
        return exclude_ids

    def _clear_reservation(self, item):
        """Clear reservation fields on an item."""
        item.reserved_by = None
        item.reserved_at = None
        item.reservation_expires_at = None

    @action(detail=True, methods=["post"], url_path="complete")
    def complete_item(self, request, queue_id=None, pk=None):
        """Mark item as completed and return next pending item."""
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

        # Verify the requesting user has actually annotated this item
        user_has_annotated = Score.objects.filter(
            queue_item=item, annotator=request.user, deleted=False
        ).exists()
        if not user_has_annotated:
            return self._gm.bad_request(
                "You must submit annotations before completing."
            )

        # Multi-annotator: check if enough annotators have submitted
        annotation_count = (
            Score.objects.filter(queue_item=item, deleted=False)
            .values("annotator")
            .distinct()
            .count()
        )
        if annotation_count >= queue.annotations_required:
            if queue.requires_review:
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
            request.data.get("exclude", []), current_pk=pk
        )

        next_item = self._get_next_pending_item(
            queue_id,
            exclude_ids=exclude_ids or None,
            user=request.user,
        )
        next_item_data = QueueItemSerializer(next_item).data if next_item else None

        return self._gm.success_response(
            {
                "completed_item_id": str(pk),
                "next_item": next_item_data,
            }
        )

    @action(detail=True, methods=["post"], url_path="skip")
    def skip_item(self, request, queue_id=None, pk=None):
        """Mark item as skipped and return next pending item."""
        try:
            item = QueueItem.objects.get(
                pk=pk,
                queue_id=queue_id,
                organization=request.organization,
                deleted=False,
            )
        except QueueItem.DoesNotExist:
            return self._gm.not_found("Queue item not found.")

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
            request.data.get("exclude", []), current_pk=pk
        )

        next_item = self._get_next_pending_item(
            queue_id,
            exclude_ids=exclude_ids or None,
            user=request.user,
        )
        next_item_data = QueueItemSerializer(next_item).data if next_item else None

        return self._gm.success_response(
            {
                "skipped_item_id": str(pk),
                "next_item": next_item_data,
            }
        )

    @action(detail=False, methods=["get"], url_path="next-item")
    def next_item(self, request, queue_id=None):
        """Get the next or previous item in the queue.

        Query params:
          exclude: comma-separated item IDs to skip
          before:  item ID — returns the item immediately before this one in order
        """
        before_id = request.query_params.get("before")
        if before_id:
            try:
                current = QueueItem.objects.get(
                    pk=before_id, queue_id=queue_id, deleted=False
                )
            except QueueItem.DoesNotExist:
                return self._gm.success_response({"item": None})
            prev_item = (
                QueueItem.objects.filter(
                    queue_id=queue_id,
                    deleted=False,
                    order__lt=current.order,
                )
                .order_by("-order", "-created_at")
                .first()
            )
            item_data = QueueItemSerializer(prev_item).data if prev_item else None
            return self._gm.success_response({"item": item_data})

        exclude_ids = self._parse_exclude_ids(request.query_params.get("exclude", ""))
        item = self._get_next_pending_item(
            queue_id,
            exclude_ids=exclude_ids or None,
            user=request.user,
        )
        if not item:
            return self._gm.success_response({"item": None})

        item_data = QueueItemSerializer(item).data
        return self._gm.success_response({"item": item_data})

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
        # For items pending review, return all annotations so reviewers
        # can see the annotator's submitted values. Only expose to users
        # with reviewer or manager role to prevent information leaks.
        is_reviewer = AnnotationQueueAnnotator.objects.filter(
            queue_id=queue_id,
            user=request.user,
            role__in=[AnnotatorRole.REVIEWER.value, AnnotatorRole.MANAGER.value],
            deleted=False,
        ).exists()
        review_status_pending = "pending_review"
        if item.review_status == review_status_pending and is_reviewer:
            annotations = Score.objects.filter(
                queue_item=item,
                deleted=False,
            ).select_related("label")
        else:
            annotations = Score.objects.filter(
                queue_item=item,
                annotator=request.user,
                deleted=False,
            ).select_related("label")

        # Compute overall progress (all items in queue, unfiltered).
        progress_qs = QueueItem.objects.filter(queue_id=queue_id, deleted=False)
        agg = progress_qs.aggregate(
            total=Count("id"),
            completed=Count("id", filter=Q(status=QueueItemStatus.COMPLETED.value)),
            before_current=Count("id", filter=Q(order__lt=item.order)),
        )
        total = agg["total"]
        completed = agg["completed"]
        current_position = (agg["before_current"] or 0) + 1

        # Per-user progress: items assigned to the user or unassigned
        has_active_assignment = Q(assignments__deleted=False)
        user_items = (
            QueueItem.objects.filter(queue_id=queue_id, deleted=False)
            .filter(
                Q(assignments__user=request.user, assignments__deleted=False)
                | ~has_active_assignment
            )
            .distinct()
        )
        user_agg = user_items.aggregate(
            user_total=Count("id", distinct=True),
            user_completed=Count(
                "id",
                distinct=True,
                filter=Q(status=QueueItemStatus.COMPLETED.value),
            ),
        )

        # Adjacent items for prefetching — only items the user can annotate
        # (assigned to them, or unassigned)
        annotatable_qs = QueueItem.objects.filter(
            queue_id=queue_id, deleted=False
        ).filter(
            Q(assignments__user=request.user, assignments__deleted=False)
            | ~Q(assignments__deleted=False)
        )
        next_item = (
            annotatable_qs.filter(order__gt=item.order)
            .order_by("order", "created_at")
            .values_list("id", flat=True)
            .first()
        )
        prev_item = (
            annotatable_qs.filter(order__lt=item.order)
            .order_by("-order", "-created_at")
            .values_list("id", flat=True)
            .first()
        )

        data = {
            "item": item,
            "queue": queue,
            "labels": labels,
            "annotations": annotations,
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

        serializer = AnnotateDetailSerializer(data)
        return self._gm.success_response(serializer.data)

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
        item_ids = request.data.get("item_ids", [])
        user_ids = request.data.get("user_ids", [])
        user_id = request.data.get("user_id")
        action = request.data.get("action", "add")

        # Legacy compat: single user_id
        if user_id is not None and not user_ids:
            user_ids = [user_id]
            if action == "add":
                action = "set"  # legacy single-assign was a full replace

        if not item_ids:
            return self._gm.bad_request("item_ids is required.")

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
        org = request.organization
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
            Score.objects.filter(queue_item=item, deleted=False)
            .select_related("annotator", "label")
            .order_by("-created_at")
        )
        serializer = ScoreSerializer(annotations, many=True)
        return self._gm.success_response(serializer.data)

    @action(detail=True, methods=["post"], url_path="review")
    def review_item(self, request, queue_id=None, pk=None):
        """Approve or reject an item as a reviewer."""
        try:
            try:
                from ee.usage.services.entitlements import Entitlements
            except ImportError:
                Entitlements = None

            org = getattr(request, "organization", None) or request.user.organization
            feat_check = Entitlements.check_feature(str(org.id), "has_review_workflow")
            if not feat_check.allowed:
                return self._gm.forbidden_response(feat_check.reason)
        except ImportError:
            pass

        # Verify requesting user has reviewer or manager role
        if not AnnotationQueueAnnotator.objects.filter(
            queue_id=queue_id,
            user=request.user,
            role__in=[AnnotatorRole.REVIEWER.value, AnnotatorRole.MANAGER.value],
            deleted=False,
        ).exists():
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

        review_action = request.data.get("action")
        notes = request.data.get("notes", "")

        if review_action not in ("approve", "reject"):
            return self._gm.bad_request("action must be 'approve' or 'reject'.")

        now = timezone.now()

        if review_action == "approve":
            item.status = QueueItemStatus.COMPLETED.value
            item.review_status = "approved"
        else:
            # Set to PENDING so the item re-enters the annotation queue
            item.status = QueueItemStatus.PENDING.value
            item.review_status = "rejected"

        item.reviewed_by = request.user
        item.reviewed_at = now
        item.review_notes = notes
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

        # Auto-complete queue check
        if review_action == "approve":
            self._maybe_auto_complete_queue(queue_id)

        next_item = self._get_next_pending_item(
            queue_id, exclude_id=pk, user=request.user
        )
        next_item_data = QueueItemSerializer(next_item).data if next_item else None

        return self._gm.success_response(
            {
                "reviewed_item_id": str(pk),
                "action": review_action,
                "next_item": next_item_data,
            }
        )

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
                Score.no_workspace_objects.update_or_create(
                    **{f"{source_fk_field}_id": source_obj.pk},
                    label_id=label.pk,
                    annotator_id=annotator.pk,
                    deleted=False,
                    defaults={
                        "source_type": item.source_type,
                        "value": value,
                        "score_source": score_source or "human",
                        "notes": "",
                        "organization": request.organization,
                        "queue_item": item,
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

    def get_queryset(self):
        queryset = super().get_queryset().select_related("created_by")
        queue_id = self.kwargs.get("queue_id")
        if queue_id:
            queryset = queryset.filter(queue_id=queue_id)
        return queryset.order_by("-created_at")

    def create(self, request, *args, **kwargs):
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

    @action(detail=True, methods=["post"], url_path="evaluate")
    def evaluate(self, request, queue_id=None, pk=None):
        """Manually trigger rule evaluation."""
        try:
            rule = AutomationRule.objects.get(
                pk=pk,
                queue_id=queue_id,
                organization=request.organization,
                deleted=False,
            )
        except AutomationRule.DoesNotExist:
            return self._gm.not_found("Rule not found.")

        result = evaluate_rule(rule)
        return self._gm.success_response(result)

    @action(detail=True, methods=["get"], url_path="preview")
    def preview(self, request, queue_id=None, pk=None):
        """Preview how many items match a rule (dry run)."""
        try:
            rule = AutomationRule.objects.get(
                pk=pk,
                queue_id=queue_id,
                organization=request.organization,
                deleted=False,
            )
        except AutomationRule.DoesNotExist:
            return self._gm.not_found("Rule not found.")

        result = evaluate_rule(rule, dry_run=True)
        return self._gm.success_response(result)
