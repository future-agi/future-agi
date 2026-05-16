import uuid

import structlog
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from model_hub.models.annotation_queues import QueueItem
from model_hub.models.choices import QueueItemStatus
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import SCORE_SOURCE_FK_MAP, Score
from model_hub.serializers.scores import (
    BulkCreateScoresResponseSerializer,
    BulkCreateScoresSerializer,
    CreateScoreSerializer,
    ScoreDeleteResponseSerializer,
    ScoreForSourceQuerySerializer,
    ScoreForSourceResponseSerializer,
    ScoreListQuerySerializer,
    ScoreResponseSerializer,
    ScoreSerializer,
)
from model_hub.utils.annotation_queue_helpers import (
    resolve_default_queue_item_for_source,
    resolve_source_object,
)
from tfc.constants.roles import OrganizationRoles
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination
from tracer.models.span_notes import SpanNotes

logger = structlog.get_logger(__name__)

ERROR_RESPONSES = {
    400: ApiErrorResponseSerializer,
    403: ApiErrorResponseSerializer,
    404: ApiErrorResponseSerializer,
    409: ApiErrorResponseSerializer,
    500: ApiErrorResponseSerializer,
}


def _resolve_queue_item(
    queue_item_id, source_type, source_obj, organization, user
):
    """Return a ``QueueItem`` to attribute a score to.

    If the request includes ``queue_item_id``, validate and return it.
    Otherwise fall back to the source's default queue, creating both the
    queue and the item if necessary.
    """
    if queue_item_id:
        try:
            queue_item = QueueItem.objects.get(
                pk=queue_item_id,
                organization=organization,
                deleted=False,
            )
        except QueueItem.DoesNotExist:
            return None
        fk_field = SCORE_SOURCE_FK_MAP.get(source_type)
        queue_item_source_id = (
            getattr(queue_item, f"{fk_field}_id", None) if fk_field else None
        )
        if (
            queue_item.source_type != source_type
            or str(queue_item_source_id) != str(source_obj.pk)
        ):
            logger.warning(
                "score_queue_item_source_mismatch",
                queue_item_id=str(queue_item_id),
                requested_source_type=source_type,
                requested_source_id=str(source_obj.pk),
                queue_item_source_type=queue_item.source_type,
                queue_item_source_id=str(queue_item_source_id),
            )
            return None
        return queue_item
    return resolve_default_queue_item_for_source(
        source_type, source_obj, organization, user
    )


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
    all required labels are now scored *in that queue's context*.

    Scores are now per-queue (Score.queue_item is non-null), so a score
    filled in Queue A no longer auto-completes Queue B's item even if
    both have the same required labels — each queue is its own review
    context. We fetch scored label IDs per queue_item rather than across
    the whole source.
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

    # Batch: collect scored label IDs per queue_item in one query
    queue_item_ids = [qi.id for qi in queue_items]
    scored_by_item = defaultdict(set)
    for label_id, queue_item_id in Score.objects.filter(
        **{fk_field: source_obj},
        annotator=annotator,
        queue_item_id__in=queue_item_ids,
        deleted=False,
    ).values_list("label_id", "queue_item_id"):
        scored_by_item[queue_item_id].add(label_id)

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

        # If all required labels are scored *for this queue_item*, mark it complete
        if required_label_ids <= scored_by_item.get(qi.id, set()):
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
        item, _ = QueueItem.objects.get_or_create(
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
        Score.no_workspace_objects.filter(
            source_type=source_type,
            **{f"{fk_field}_id": source_obj.pk},
            label_id__in=queue.queue_labels.filter(deleted=False).values_list(
                "label_id", flat=True
            ),
            organization=queue.organization,
            queue_item__isnull=True,
            deleted=False,
        ).update(queue_item=item)


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
        ).select_related("label", "annotator", "queue_item__queue")

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

    @swagger_auto_schema(query_serializer=ScoreListQuerySerializer)
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(
        request_body=CreateScoreSerializer,
        responses={200: ScoreResponseSerializer, **ERROR_RESPONSES},
    )
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

        # Resolve the queue context the score belongs to. Every Score must
        # have a non-null queue_item — caller may pass an explicit one
        # (queue-flow annotation), otherwise we attribute it to the source's
        # default queue (auto-created if missing). Per product decision:
        # there is no truly "inline" score; everything lives in a queue.
        queue_item = _resolve_queue_item(
            data.get("queue_item_id"),
            source_type,
            source_obj,
            request.organization,
            request.user,
        )
        if queue_item is None:
            return self._gm.bad_request(
                "Cannot resolve a default annotation queue for this source. "
                "Pass an explicit queue_item_id or score from a queue flow."
            )

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
                queue_item=queue_item,
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

    @swagger_auto_schema(
        request_body=BulkCreateScoresSerializer,
        responses={200: BulkCreateScoresResponseSerializer, **ERROR_RESPONSES},
    )
    @action(detail=False, methods=["post"], url_path="bulk")
    def bulk_create(self, request):
        """Create multiple scores on a single source (e.g. from inline annotator)."""
        serializer = BulkCreateScoresSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        source_type = data["source_type"]
        source_id = data["source_id"]
        span_notes = data.get("span_notes")  # None when field was not sent
        span_notes_source_id = data.get("span_notes_source_id")

        fk_field = SCORE_SOURCE_FK_MAP.get(source_type)
        if not fk_field:
            return self._gm.bad_request(f"Invalid source_type: {source_type}")

        source_obj = resolve_source_object(
            source_type, source_id, organization=request.organization
        )
        if not source_obj:
            return self._gm.not_found(f"Source not found: {source_type}={source_id}")

        span_notes_target = None
        if span_notes is not None:
            if source_type == "observation_span":
                span_notes_target = source_obj
            elif span_notes_source_id:
                span_notes_target = resolve_source_object(
                    "observation_span",
                    span_notes_source_id,
                    organization=request.organization,
                )
                if not span_notes_target:
                    return self._gm.not_found(
                        f"Span notes source not found: {span_notes_source_id}"
                    )

        # Resolve queue context once per request — every score in this bulk
        # call shares the same source, so they all land in the same
        # queue_item. See create() for the rationale on requiring queue_item.
        queue_item = _resolve_queue_item(
            data.get("queue_item_id"),
            source_type,
            source_obj,
            request.organization,
            request.user,
        )
        if queue_item is None:
            return self._gm.bad_request(
                "Cannot resolve a default annotation queue for this source. "
                "Pass an explicit queue_item_id or score from a queue flow."
            )

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
                    queue_item=queue_item,
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

            # span_notes is None when the field was omitted from the request.
            # For call annotations, labels save on the trace while item notes
            # still belong to the root observation span.
            #
            # We also mirror the note into ``QueueItemNote`` for this
            # queue_item. The drawer's editable ``existing_notes`` box is
            # strictly per-queue (reads QueueItemNote), so without this
            # mirror the voice/trace-call note wouldn't reload on reopen.
            # The SpanNote stays as legacy/cross-queue context.
            from model_hub.models.annotation_queues import QueueItemNote

            if span_notes_target is not None:
                if span_notes:
                    SpanNotes.objects.update_or_create(
                        span=span_notes_target,
                        created_by_user=request.user,
                        defaults={
                            "notes": span_notes,
                            "created_by_annotator": request.user.email,
                        },
                    )
                else:
                    # User explicitly cleared the notes field — delete the SpanNote
                    SpanNotes.objects.filter(
                        span=span_notes_target,
                        created_by_user=request.user,
                    ).delete()

            if span_notes is not None and queue_item is not None:
                if span_notes:
                    QueueItemNote.no_workspace_objects.update_or_create(
                        queue_item=queue_item,
                        annotator=request.user,
                        deleted=False,
                        defaults={
                            "notes": span_notes,
                            "organization": request.organization,
                            "workspace": getattr(request, "workspace", None)
                            or queue_item.workspace,
                        },
                    )
                else:
                    # User explicitly cleared — soft-delete the QueueItemNote.
                    QueueItemNote.no_workspace_objects.filter(
                        queue_item=queue_item,
                        annotator=request.user,
                        deleted=False,
                    ).update(deleted=True, deleted_at=timezone.now())

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

    @swagger_auto_schema(
        query_serializer=ScoreForSourceQuerySerializer,
        responses={200: ScoreForSourceResponseSerializer, **ERROR_RESPONSES},
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
            .select_related("label", "annotator", "queue_item__queue")
            .order_by("label__name", "-created_at")
        )

        response = self._gm.success_response(ScoreSerializer(scores, many=True).data)

        if source_type == "observation_span":
            # The trace-detail "Span Notes" panel should show every
            # whole-item note ever written on this span, broken out per
            # (queue, annotator). Pre-revamp this used to read raw
            # ``SpanNotes`` rows, but that table is keyed on
            # ``(span, user)`` — submitting from a second queue overwrites
            # the prior note. We now read ``QueueItemNote`` rows whose
            # queue item points at this span (one row per (queue, user))
            # AND any legacy ``SpanNote`` that has no QueueItemNote
            # counterpart from the same user.
            #
            # Notes on TRACE-level queue items are surfaced here too —
            # voice projects hold most call annotations at trace level
            # (one item per call) so a note written on the root span's
            # parent trace is semantically a "note on this span" for the
            # purposes of the trace-detail panel.
            from model_hub.models.annotation_queues import QueueItemNote
            from tracer.models.observation_span import ObservationSpan

            # Resolve the parent trace, scoped to the requester's organization
            # so a direct call with another org's span id can't surface this
            # org's queue notes via the trace-level filter branch. If the
            # span doesn't belong to this org, ``span_belongs_to_org`` stays
            # False and we skip note enrichment entirely.
            span_row = (
                ObservationSpan.objects.filter(
                    id=source_id,
                    project__organization=request.organization,
                )
                .values_list("trace_id", flat=True)
                .first()
            )
            span_belongs_to_org = span_row is not None
            trace_id = span_row

            queue_note_filter = Q(queue_item__observation_span_id=source_id)
            if trace_id:
                queue_note_filter |= Q(queue_item__trace_id=trace_id)

            queue_notes = (
                QueueItemNote.no_workspace_objects.filter(
                    queue_note_filter,
                    organization=request.organization,
                    queue_item__organization=request.organization,
                    queue_item__deleted=False,
                    deleted=False,
                )
                .select_related(
                    "annotator",
                    "queue_item",
                    "queue_item__queue",
                )
                .order_by("-updated_at", "-created_at")
            ) if span_belongs_to_org else []

            payloads = []
            seen_user_queue = set()
            users_with_queue_notes = set()
            for note in queue_notes:
                queue_name = (
                    note.queue_item.queue.name if note.queue_item and note.queue_item.queue else None
                )
                annotator_label = (
                    note.annotator.name or note.annotator.email
                    if note.annotator_id
                    else None
                )
                if note.annotator_id:
                    users_with_queue_notes.add(note.annotator_id)
                key = (note.annotator_id, note.queue_item_id)
                if key in seen_user_queue:
                    continue
                seen_user_queue.add(key)
                payloads.append(
                    {
                        "id": str(note.id),
                        "notes": note.notes,
                        "annotator": annotator_label,
                        "queue_name": queue_name,
                        "created_at": note.created_at.isoformat(),
                    }
                )

            # Legacy SpanNotes (annotator never wrote a queue-scoped note
            # for this span) — keep them in the list as backward-compat
            # context until the SpanNotes backfill runs. Gated on the
            # org-scoped span check above so cross-org callers can't read
            # this org's legacy notes.
            legacy_notes = (
                SpanNotes.objects.filter(span_id=source_id)
                .exclude(created_by_user_id__in=users_with_queue_notes)
                .select_related("created_by_user")
                .order_by("-created_at")
            ) if span_belongs_to_org else []
            for note in legacy_notes:
                payloads.append(
                    {
                        "id": str(note.id),
                        "notes": note.notes,
                        "annotator": note.created_by_annotator
                        or (
                            note.created_by_user.name
                            if note.created_by_user_id
                            else None
                        ),
                        "queue_name": None,
                        "created_at": note.created_at.isoformat(),
                    }
                )

            response.data["span_notes"] = payloads

        return response

    @swagger_auto_schema(
        responses={200: ScoreDeleteResponseSerializer, **ERROR_RESPONSES}
    )
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
