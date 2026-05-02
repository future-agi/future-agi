import uuid

import structlog
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from model_hub.models.annotation_queues import QueueItem
from model_hub.models.choices import QueueItemStatus
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import SCORE_SOURCE_FK_MAP, Score
from tracer.models.span_notes import SpanNotes
from model_hub.serializers.scores import (
    BulkCreateScoresSerializer,
    CreateScoreSerializer,
    ScoreSerializer,
)
from model_hub.utils.annotation_queue_helpers import resolve_source_object
from tfc.constants.roles import OrganizationRoles
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination

logger = structlog.get_logger(__name__)


def _safe_auto_create_queue_items_for_default_queues(*args, **kwargs):
    """Wrap ``_auto_create_queue_items_for_default_queues`` so a failure
    inside an ``on_commit`` hook can't bubble (hooks have no error path)."""
    try:
        _auto_create_queue_items_for_default_queues(*args, **kwargs)
    except Exception:
        logger.exception("auto_create_queue_items_failed", args=str(args))


def _safe_auto_complete_queue_items(*args, **kwargs):
    """Wrap ``_auto_complete_queue_items`` for use inside ``on_commit`` hooks."""
    try:
        _auto_complete_queue_items(*args, **kwargs)
    except Exception:
        logger.exception("auto_complete_queue_items_failed", args=str(args))


def _auto_complete_queue_items(source_type, source_obj, annotator):
    """
    Check if any QueueItem references this source and auto-complete if
    all required labels are now scored.
    """
    from collections import defaultdict

    from model_hub.models.annotation_queues import AnnotationQueueLabel

    fk_field = SCORE_SOURCE_FK_MAP.get(source_type)
    if not fk_field:
        return

    # Find queue items pointing to this source that are not yet completed
    queue_items = QueueItem.objects.filter(
        **{fk_field: source_obj},
        deleted=False,
        status__in=[QueueItemStatus.PENDING.value, QueueItemStatus.IN_PROGRESS.value],
    ).select_related("queue")

    queue_items = list(queue_items)
    if not queue_items:
        return

    # Batch: collect all scored label IDs for this source+annotator in one query
    scored_label_ids = set(
        Score.objects.filter(
            **{fk_field: source_obj},
            annotator=annotator,
            deleted=False,
        ).values_list("label_id", flat=True)
    )

    # Batch-fetch required labels for all relevant queues upfront (avoids N+1)
    queue_ids = {qi.queue_id for qi in queue_items}
    required_by_queue = defaultdict(set)
    for ql in AnnotationQueueLabel.objects.filter(
        queue_id__in=queue_ids, deleted=False, required=True
    ):
        required_by_queue[ql.queue_id].add(ql.label_id)

    for qi in queue_items:
        required_label_ids = required_by_queue.get(qi.queue_id, set())
        if not required_label_ids:
            continue

        # If all required labels are scored, mark the queue item complete
        if required_label_ids <= scored_label_ids:
            qi.status = QueueItemStatus.COMPLETED.value
            qi.save(update_fields=["status", "updated_at"])
            logger.info(
                "queue_item_auto_completed",
                queue_item_id=str(qi.id),
                source_type=source_type,
                annotator_id=str(annotator.id) if annotator else None,
            )


def _auto_create_queue_items_for_default_queues(source_type, source_obj, label_ids):
    """
    For default queues: auto-create a QueueItem when someone annotates a source
    that belongs to the queue's scope (project, dataset, or agent_definition).
    This enables lazy queue item creation — labels show up for all sources in
    the scope, but queue items are only created when someone actually annotates.
    """
    from model_hub.models.annotation_queues import (
        SOURCE_TYPE_FK_MAP,
        AnnotationQueue,
        AnnotationQueueLabel,
    )
    from model_hub.models.choices import AnnotationQueueStatusChoices

    fk_field = SOURCE_TYPE_FK_MAP.get(source_type)
    if not fk_field:
        return

    # Build scope filters for default queues this source belongs to
    scope_q = Q()

    # Project-scoped: trace, observation_span, trace_session have project FK
    project = getattr(source_obj, "project", None)
    if project:
        scope_q |= Q(project=project)

    # Dataset-scoped: dataset_row has dataset FK
    dataset = getattr(source_obj, "dataset", None)
    if dataset:
        scope_q |= Q(dataset=dataset)

    # Agent-definition-scoped: call_execution → test_execution → agent_definition
    if source_type == "call_execution":
        test_execution = getattr(source_obj, "test_execution", None)
        if test_execution:
            agent_definition = getattr(test_execution, "agent_definition", None)
            if agent_definition:
                scope_q |= Q(agent_definition=agent_definition)

    # Agent-definition-scoped via voice observability:
    # trace/span → project → observability_provider → agent_definition
    if source_type in ("trace", "observation_span", "trace_session") and project:
        try:
            from simulate.models.agent_definition import AgentDefinition

            agent_def_ids = list(
                AgentDefinition.objects.filter(
                    observability_provider__project=project,
                    deleted=False,
                ).values_list("id", flat=True)
            )
            if agent_def_ids:
                scope_q |= Q(agent_definition_id__in=agent_def_ids)
        except Exception:
            logger.exception(
                "auto_create_agent_def_lookup_failed",
                source_type=source_type,
            )

    if not scope_q:
        return

    # Find default queues for this scope that include any of these labels
    default_queues = AnnotationQueue.objects.filter(
        scope_q,
        is_default=True,
        deleted=False,
        status=AnnotationQueueStatusChoices.ACTIVE.value,
        queue_labels__label_id__in=label_ids,
        queue_labels__deleted=False,
    ).distinct()

    for queue in default_queues:
        QueueItem.objects.get_or_create(
            queue=queue,
            source_type=source_type,
            **{f"{fk_field}_id": source_obj.pk},
            deleted=False,
            defaults={
                "organization": queue.organization,
                "workspace": queue.workspace,
                "status": QueueItemStatus.PENDING.value,
            },
        )


class ScoreViewSet(viewsets.ModelViewSet):
    """
    Universal Score CRUD.

    GET    /model-hub/scores/?source_type=trace&source_id=<uuid>
    POST   /model-hub/scores/                 (single score)
    POST   /model-hub/scores/bulk/            (multiple scores on one source)
    DELETE /model-hub/scores/<id>/
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ScoreSerializer
    pagination_class = ExtendedPageNumberPagination
    _gm = GeneralMethods()

    def get_queryset(self):
        qs = Score.objects.filter(
            organization=self.request.organization,
            deleted=False,
        ).select_related("label", "annotator")

        # Filter by source
        source_type = self.request.query_params.get("source_type")
        source_id = self.request.query_params.get("source_id")
        if source_type and source_id:
            fk_field = SCORE_SOURCE_FK_MAP.get(source_type)
            if fk_field:
                qs = qs.filter(**{f"{fk_field}_id": source_id})

        # Filter by label
        label_id = self.request.query_params.get("label_id")
        if label_id:
            qs = qs.filter(label_id=label_id)

        # Filter by annotator
        annotator_id = self.request.query_params.get("annotator_id")
        if annotator_id:
            qs = qs.filter(annotator_id=annotator_id)

        return qs.order_by("-created_at")

    def create(self, request, *args, **kwargs):
        """Create a single score."""
        serializer = CreateScoreSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        source_type = data["source_type"]
        source_id = data["source_id"]
        label_id = data["label_id"]

        fk_field = SCORE_SOURCE_FK_MAP.get(source_type)
        if not fk_field:
            return self._gm.bad_request(f"Invalid source_type: {source_type}")

        source_obj = resolve_source_object(
            source_type, source_id, organization=request.organization
        )
        if not source_obj:
            return self._gm.not_found(f"Source not found: {source_type}={source_id}")

        try:
            label = AnnotationsLabels.objects.get(pk=label_id, deleted=False)
        except AnnotationsLabels.DoesNotExist:
            return self._gm.not_found(f"Label not found: {label_id}")

        # Upsert: update if exists, create if not.
        #
        # WHY no_workspace_objects is used here:
        # The default manager adds a LEFT JOIN on the nullable workspace FK
        # for workspace-scoped filtering.  PostgreSQL's SELECT … FOR UPDATE
        # (used internally by update_or_create) cannot be applied to the
        # nullable side of an outer join, causing
        # "FOR UPDATE cannot be applied to the nullable side of an outer join".
        # Using no_workspace_objects bypasses that LEFT JOIN.  The workspace
        # field is still populated automatically via the post-save signal
        # (set_workspace_from_organization), so workspace-scoped reads
        # continue to work correctly.
        with transaction.atomic():
            score, created = Score.no_workspace_objects.update_or_create(
                **{f"{fk_field}_id": source_obj.pk},
                label_id=label.pk,
                annotator_id=request.user.pk,
                deleted=False,
                defaults={
                    "source_type": source_type,
                    "value": data["value"],
                    "score_source": data.get("score_source", "human"),
                    "notes": data.get("notes", ""),
                    "organization": request.organization,
                },
            )

            # Run queue side-effects AFTER the transaction commits — see
            # https://docs.djangoproject.com/en/5.1/topics/db/transactions/#django.db.transaction.on_commit
            # Bare ``except Exception`` inside ``atomic()`` would catch the
            # error but leave the transaction in a "needs rollback" state;
            # the Score would commit, the response would say success, but
            # subsequent ORM calls in the same request would raise
            # ``TransactionManagementError``. ``on_commit`` runs the work
            # outside the transaction, so a failure there can't poison the
            # write that already happened.
            transaction.on_commit(
                lambda: _safe_auto_create_queue_items_for_default_queues(
                    source_type, source_obj, [label_id]
                )
            )
            transaction.on_commit(
                lambda: _safe_auto_complete_queue_items(
                    source_type, source_obj, request.user
                )
            )

        result = ScoreSerializer(score).data
        return self._gm.success_response(result)

    @action(detail=False, methods=["post"], url_path="bulk")
    def bulk_create(self, request):
        """Create multiple scores on a single source (e.g. from inline annotator)."""
        serializer = BulkCreateScoresSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        source_type = data["source_type"]
        source_id = data["source_id"]
        span_notes = data.get("span_notes")  # None when field was not sent

        fk_field = SCORE_SOURCE_FK_MAP.get(source_type)
        if not fk_field:
            return self._gm.bad_request(f"Invalid source_type: {source_type}")

        source_obj = resolve_source_object(
            source_type, source_id, organization=request.organization
        )
        if not source_obj:
            return self._gm.not_found(f"Source not found: {source_type}={source_id}")

        created_scores = []
        errors = []

        with transaction.atomic():
            for score_data in data["scores"]:
                label_id = score_data["label_id"]
                value = score_data["value"]

                try:
                    label = AnnotationsLabels.objects.get(pk=label_id, deleted=False)
                except AnnotationsLabels.DoesNotExist:
                    errors.append(f"Label not found: {label_id}")
                    continue

                # Per-score notes: only saved if the label has allow_notes=True.
                per_score_notes = score_data.get("notes", "") if label.allow_notes else ""

                # See comment in create() for why no_workspace_objects is used:
                # avoids FOR UPDATE + nullable LEFT JOIN issue; workspace is
                # assigned via post-save signal.
                score, _ = Score.no_workspace_objects.update_or_create(
                    **{f"{fk_field}_id": source_obj.pk},
                    label_id=label.pk,
                    annotator_id=request.user.pk,
                    deleted=False,
                    defaults={
                        "source_type": source_type,
                        "value": value,
                        "score_source": score_data.get("score_source", "human"),
                        "notes": per_score_notes,
                        "organization": request.organization,
                    },
                )
                created_scores.append(score)

            # span_notes is None when the field was omitted from the request
            # (user did not interact with the notes field at all).
            # Only touch SpanNotes when the field was explicitly sent.
            if source_type == "observation_span" and span_notes is not None:
                if span_notes:
                    SpanNotes.objects.update_or_create(
                        span=source_obj,
                        created_by_user=request.user,
                        defaults={
                            "notes": span_notes,
                            "created_by_annotator": request.user.email,
                        },
                    )
                else:
                    # User explicitly cleared the notes field — delete the SpanNote
                    SpanNotes.objects.filter(
                        span=source_obj,
                        created_by_user=request.user,
                    ).delete()

            # Same rationale as in ``create()``: run side-effects after commit
            # so a failure can't poison the transaction that just wrote the
            # Score rows. Single hooks per side-effect (not N) since both
            # operate on the source object, not per-score.
            scored_label_ids = [s["label_id"] for s in data["scores"]]
            transaction.on_commit(
                lambda: _safe_auto_create_queue_items_for_default_queues(
                    source_type, source_obj, scored_label_ids
                )
            )
            transaction.on_commit(
                lambda: _safe_auto_complete_queue_items(
                    source_type, source_obj, request.user
                )
            )

        return self._gm.success_response(
            {
                "scores": ScoreSerializer(created_scores, many=True).data,
                "errors": errors,
            }
        )

    @action(detail=False, methods=["get"], url_path="for-source")
    def for_source(self, request):
        """
        Get all scores for a specific source.
        GET /model-hub/scores/for-source/?source_type=trace&source_id=<uuid>
        """
        source_type = request.query_params.get("source_type")
        source_id = request.query_params.get("source_id")

        if not source_type or not source_id:
            return self._gm.bad_request("source_type and source_id are required.")

        # observation_span uses CharField PK (not UUID) — skip UUID validation for it
        if source_type != "observation_span":
            try:
                uuid.UUID(source_id)
            except (ValueError, AttributeError):
                return self._gm.bad_request("source_id must be a valid UUID.")

        fk_field = SCORE_SOURCE_FK_MAP.get(source_type)
        if not fk_field:
            return self._gm.bad_request(f"Invalid source_type: {source_type}")

        scores = (
            Score.objects.filter(
                **{f"{fk_field}_id": source_id},
                organization=request.organization,
                deleted=False,
            )
            .select_related("label", "annotator")
            .order_by("label__name", "-created_at")
        )

        response = self._gm.success_response(ScoreSerializer(scores, many=True).data)

        if source_type == "observation_span":
            span_notes = (
                SpanNotes.objects.filter(span_id=source_id)
                .select_related("created_by_user")
                .order_by("-created_at")
            )
            response.data["span_notes"] = [
                {
                    "id": str(note.id),
                    "notes": note.notes,
                    "annotator": note.created_by_annotator
                    or (note.created_by_user.name if note.created_by_user_id else None),
                    "created_at": note.created_at.isoformat(),
                }
                for note in span_notes
            ]

        return response

    def destroy(self, request, *args, **kwargs):
        """Soft-delete a score.

        Only the annotator who created the score or an org Owner/Admin may
        delete it.
        """
        try:
            score = Score.objects.get(
                pk=kwargs["pk"],
                organization=request.organization,
                deleted=False,
            )
        except Score.DoesNotExist:
            return self._gm.not_found("Score not found.")

        # Ownership check: annotator themselves or org admin/owner
        is_owner_or_admin = request.user.get_organization_role(
            request.organization
        ) in (OrganizationRoles.OWNER, OrganizationRoles.ADMIN)
        if score.annotator_id != request.user.pk and not is_owner_or_admin:
            return self._gm.bad_request(
                "You do not have permission to delete this score."
            )

        score.deleted = True
        score.deleted_at = timezone.now()
        score.save(update_fields=["deleted", "deleted_at", "updated_at"])
        return self._gm.success_response({"deleted": True})
