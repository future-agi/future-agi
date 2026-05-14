import json
import uuid

import structlog
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from accounts.authentication import APIKeyAuthentication
from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from model_hub.views.scores import (
    _auto_complete_queue_items,
    _auto_create_queue_items_for_default_queues,
)
from tfc.utils.general_methods import GeneralMethods
from tracer.models.observation_span import ObservationSpan
from tracer.models.span_notes import SpanNotes
from tracer.serializers.annotation import (
    BulkAnnotationSerializer,
    GetTraceAnnotationSerializer,
)
from tracer.services.clickhouse.query_service import (
    AnalyticsQueryService,
    QueryType,
)

logger = structlog.get_logger(__name__)

User = get_user_model()


class TraceAnnotationView(ModelViewSet):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    serializer_class = GetTraceAnnotationSerializer

    # ------------------------------------------------------------------
    # ClickHouse helpers
    # ------------------------------------------------------------------

    def _get_annotations_from_clickhouse(
        self,
        observation_span_id,
        trace_id,
        annotators_list,
        exclude_annotators_list,
    ):
        """Fetch annotation rows from ClickHouse and format them to match
        the PG-based response.  Annotation label metadata and user emails
        are still resolved via PG because those tables are not replicated
        to ClickHouse.

        Returns a list of annotation dicts (same shape as PG path) or
        raises on failure so the caller can fall back.
        """
        analytics = AnalyticsQueryService()

        # -- Build the CH query ------------------------------------------------
        conditions = ["_peerdb_is_deleted = 0"]
        params: dict = {}

        if observation_span_id:
            # Replicate PG logic: annotations on the span OR trace-level
            # annotations (observation_span_id IS NULL) for the same trace.
            try:
                span_obj = ObservationSpan.objects.only("trace_id").get(
                    id=observation_span_id
                )
                conditions.append(
                    "(observation_span_id = %(span_id)s"
                    " OR (trace_id = %(trace_id)s AND observation_span_id IS NULL))"
                )
                params["span_id"] = str(observation_span_id)
                params["trace_id"] = str(span_obj.trace_id)
            except ObservationSpan.DoesNotExist:
                conditions.append("observation_span_id = %(span_id)s")
                params["span_id"] = str(observation_span_id)
        elif trace_id:
            # PG path filters via observation_span__trace_id + root span.
            # In CH we use trace_id directly (root-span constraint is
            # relaxed since the annotation table stores trace_id).
            conditions.append("trace_id = %(trace_id)s")
            params["trace_id"] = str(trace_id)
        else:
            return []

        if annotators_list:
            conditions.append("annotator_id IN %(annotator_ids)s")
            params["annotator_ids"] = tuple(annotators_list)

        if exclude_annotators_list:
            conditions.append("annotator_id NOT IN %(exclude_ids)s")
            params["exclude_ids"] = tuple(exclude_annotators_list)

        where = " AND ".join(conditions)
        query = f"""
            SELECT
                toString(id) AS id,
                toString(label_id) AS label_id,
                value,
                toString(annotator_id) AS annotator_id,
                score_source,
                created_at,
                updated_at
            FROM model_hub_score FINAL
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT 500
        """

        result = analytics.execute_ch_query(query, params, timeout_ms=5000)
        rows = result.data

        if not rows:
            return []

        # -- Resolve annotation labels and users from PG --------------------
        label_ids = {row["label_id"] for row in rows}
        labels_map = {
            str(lbl.id): lbl
            for lbl in AnnotationsLabels.objects.filter(id__in=label_ids)
        }

        user_ids = {
            row["annotator_id"]
            for row in rows
            if row.get("annotator_id")
            and row["annotator_id"] != "00000000-0000-0000-0000-000000000000"
        }
        users_map = {}
        if user_ids:
            users_map = {str(u.id): u for u in User.objects.filter(id__in=user_ids)}

        # -- Format rows ----------------------------------------------------
        annotations = []
        for row in rows:
            label = labels_map.get(row["label_id"])
            if not label:
                continue  # orphaned annotation, skip

            # Parse the value JSON from model_hub_score
            raw_val = row.get("value", "{}")
            if isinstance(raw_val, str):
                try:
                    val = json.loads(raw_val)
                except (json.JSONDecodeError, TypeError):
                    val = {}
            else:
                val = raw_val if isinstance(raw_val, dict) else {}

            # Determine the final annotation value based on label type
            final_annotation_value = None
            if label.type == AnnotationTypeChoices.CATEGORICAL.value:
                selected = val.get("selected", []) if isinstance(val, dict) else val
                final_annotation_value = selected if isinstance(selected, list) else []
            elif label.type == AnnotationTypeChoices.TEXT.value:
                final_annotation_value = (
                    val.get("text", val) if isinstance(val, dict) else val
                )
            elif label.type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
                thumb_val = val.get("value") if isinstance(val, dict) else val
                if thumb_val is not None:
                    final_annotation_value = (
                        "up" if thumb_val in (True, "up", 1, "true") else "down"
                    )
            elif label.type in [
                AnnotationTypeChoices.NUMERIC.value,
                AnnotationTypeChoices.STAR.value,
            ]:
                value_key = (
                    "value"
                    if label.type == AnnotationTypeChoices.NUMERIC.value
                    else "rating"
                )
                final_annotation_value = (
                    val.get(value_key) if isinstance(val, dict) else val
                )

            annotator_id = row.get("annotator_id")
            if annotator_id == "00000000-0000-0000-0000-000000000000":
                annotator_id = None
            user_obj = users_map.get(annotator_id) if annotator_id else None
            annotations.append(
                {
                    "id": row["id"],
                    "annotation_label_name": label.name,
                    "annotation_value": final_annotation_value,
                    "annotation_label_id": row["label_id"],
                    "annotator": user_obj.email if user_obj else None,
                    "annotator_id": str(user_obj.id) if user_obj else None,
                    "updated_by": row.get("score_source"),
                    "updated_at": row.get("updated_at"),
                    "annotation_type": label.type,
                    "settings": label.settings,
                }
            )

        return annotations

    # ------------------------------------------------------------------
    # Main endpoint
    # ------------------------------------------------------------------

    @action(detail=False, methods=["get"])
    def get_annotation_values(self, request, *args, **kwargs):
        try:
            observation_span_id = request.query_params.get(
                "observation_span_id"
            ) or request.query_params.get("observationSpanId")
            trace_id = request.query_params.get("trace_id") or request.query_params.get(
                "traceId"
            )
            annotators = request.query_params.get("annotators")
            exclude_annotators = request.query_params.get(
                "exclude_annotators"
            ) or request.query_params.get("excludeAnnotators")

            # Parse JSON lists for annotators and exclude_annotators
            annotators_list = None
            if annotators:
                try:
                    annotators_list = json.loads(annotators)
                    if not isinstance(annotators_list, list):
                        return self._gm.bad_request(
                            "Invalid annotators format. Expected JSON array."
                        )
                    # Validate UUID format for each item
                    for uuid_str in annotators_list:
                        uuid.UUID(uuid_str)
                except (json.JSONDecodeError, ValueError):
                    return self._gm.bad_request(
                        "Invalid annotators format. Expected JSON array of UUIDs."
                    )

            exclude_annotators_list = None
            if exclude_annotators:
                try:
                    exclude_annotators_list = json.loads(exclude_annotators)
                    if not isinstance(exclude_annotators_list, list):
                        return self._gm.bad_request(
                            "Invalid exclude_annotators format. Expected JSON array."
                        )
                    # Validate UUID format for each item
                    for uuid_str in exclude_annotators_list:
                        uuid.UUID(uuid_str)
                except (json.JSONDecodeError, ValueError):
                    return self._gm.bad_request(
                        "Invalid exclude_annotators format. Expected JSON array of UUIDs."
                    )

            if not observation_span_id and not trace_id:
                return self._gm.bad_request(
                    "At least one of observation_span_id or trace_id is required"
                )

            serializer = GetTraceAnnotationSerializer(
                data={
                    "observation_span_id": observation_span_id,
                    "trace_id": trace_id,
                    "annotators": annotators_list,
                    "exclude_annotators": exclude_annotators_list,
                }
            )
            serializer.is_valid(raise_exception=True)

            # ClickHouse dispatch for annotation data
            analytics = AnalyticsQueryService()
            if analytics.should_use_clickhouse(QueryType.ANNOTATION_DETAIL):
                try:
                    ch_annotations = self._get_annotations_from_clickhouse(
                        observation_span_id,
                        trace_id,
                        annotators_list,
                        exclude_annotators_list,
                    )
                    # Notes are always fetched from PG (not replicated to CH)
                    notes_details = self._get_notes_from_pg(observation_span_id)
                    return self._gm.success_response(
                        {"annotations": ch_annotations, "notes": notes_details}
                    )
                except Exception as e:
                    logger.warning(
                        "ClickHouse annotation-detail failed, falling back to PG",
                        error=str(e),
                    )
                    # Fall through to existing PG code

            query_params = {"deleted": False}
            if observation_span_id:
                query_params["observation_span_id"] = observation_span_id
            elif trace_id:
                query_params["observation_span__trace_id"] = trace_id
                query_params["observation_span__parent_span_id__isnull"] = True

            queryset = Score.objects.filter(**query_params)

            if annotators_list:  # Include only these annotators
                queryset = queryset.filter(annotator__in=annotators_list)

            if exclude_annotators_list:  # Exclude these annotators
                queryset = queryset.exclude(annotator__in=exclude_annotators_list)

            queryset = queryset.select_related("annotator", "label").order_by(
                "-created_at"
            )[:500]
            result = []

            for score in queryset:
                final_annotation_value = self._extract_display_value(score)

                result.append(
                    {
                        "id": str(score.id),
                        "annotation_label_name": score.label.name,
                        "annotation_value": final_annotation_value,
                        "annotation_label_id": str(score.label.id),
                        "annotator": score.annotator.email if score.annotator else None,
                        "annotator_id": (
                            str(score.annotator.id) if score.annotator else None
                        ),
                        "updated_by": (
                            str(score.annotator.id) if score.annotator else None
                        ),
                        "updated_at": score.updated_at,
                        "annotation_type": score.label.type,
                        "settings": score.label.settings,
                    }
                )

            notes_details = self._get_notes_from_pg(observation_span_id)
            return self._gm.success_response(
                {"annotations": result, "notes": notes_details}
            )
        except Exception as e:
            logger.exception(f"Error in getting annotation values: {str(e)}")
            return self._gm.bad_request(f"error getting the annotation values {str(e)}")

    @staticmethod
    def _get_notes_from_pg(observation_span_id):
        """Fetch span notes from PostgreSQL."""
        notes_details = []
        notes = SpanNotes.objects.filter(span_id=observation_span_id).select_related(
            "created_by_user"
        )
        for note in notes:
            notes_details.append(
                {
                    "id": str(note.id),
                    "notes": note.notes,
                    "created_by_annotator": note.created_by_annotator,
                    "created_by_user": (
                        note.created_by_user.name
                        if note.created_by_user.name
                        else note.created_by_user.email
                    ),
                    "created_by_user_id": str(note.created_by_user.id),
                    "updated_at": note.updated_at,
                }
            )
        return notes_details

    @staticmethod
    def _extract_display_value(score):
        """Extract a display-friendly value from Score's JSON value field."""
        val = score.value or {}
        label_type = score.label.type

        if label_type == AnnotationTypeChoices.CATEGORICAL.value:
            return val.get("selected", [])
        elif label_type == AnnotationTypeChoices.TEXT.value:
            return val.get("text")
        elif label_type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
            return val.get("value")  # "up" or "down"
        elif label_type in (
            AnnotationTypeChoices.NUMERIC.value,
            AnnotationTypeChoices.STAR.value,
        ):
            return (
                val.get("rating")
                if label_type == AnnotationTypeChoices.STAR.value
                else val.get("value")
            )
        return None


class BulkAnnotationView(APIView):
    _gm = GeneralMethods()
    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            # Initial validation
            validated_data = self._validate_request_data(request)
            if isinstance(validated_data, Response):  # Error response
                return validated_data

            records = validated_data["records"]

            # Pre-fetch all required data
            prefetch_data = self._prefetch_data(request, records)

            # Process all records
            results = self._process_records(request, records, prefetch_data)

            # Save all data in bulk
            save_results = self._save_data(
                results["annotations_to_create"],
                results["annotations_to_update"],
                results["notes_to_create"],
                request.user,
            )

            # Format and return response
            return self._format_response(
                annotations_created=save_results["annotations_created"],
                annotations_updated=save_results["annotations_updated"],
                notes_created=save_results["notes_created"],
                errors=results["errors"],
                warnings=results["warnings"],
            )

        except Exception as e:
            logger.exception(f"Error in bulk annotation processing: {str(e)}")
            return self._gm.bad_request(f"Error processing bulk annotations: {str(e)}")

    def _validate_request_data(self, request):
        """Validate the incoming request data and check global limits."""
        MAX_RECORDS = 1000

        serializer = BulkAnnotationSerializer(data=request.data)
        if not serializer.is_valid():
            return self._gm.bad_request(f"Validation error: {serializer.errors}")

        validated_data = serializer.validated_data
        records = validated_data["records"]

        # Global validation: Reject if the entire payload is too large
        if len(records) > MAX_RECORDS:
            return self._gm.bad_request(
                f"Too many records. Maximum allowed: {MAX_RECORDS}"
            )

        return validated_data

    def _prefetch_data(self, request, records):
        """Pre-fetch all spans, annotation labels, and existing duplicates to avoid repeated DB hits."""
        # Pre-fetch all spans
        span_ids = [
            record.get("observation_span_id")
            for record in records
            if record.get("observation_span_id")
        ]
        span_map = {
            str(span.id): span
            for span in ObservationSpan.objects.select_related("project").filter(
                id__in=span_ids
            )
        }

        # Prefetch all annotation labels used in the request
        label_ids = {
            str(ann["annotation_label_id"])
            for rec in records
            for ann in rec.get("annotations", [])
        }
        annotation_label_map = {
            str(lbl.id): lbl
            for lbl in AnnotationsLabels.objects.select_related("project").filter(
                id__in=label_ids
            )
        }

        # Collect (span, label, current_user) keys appearing in request for duplicate detection
        current_user_id = str(request.user.id)
        request_keys = {
            (
                str(rec["observation_span_id"]),
                str(ann["annotation_label_id"]),
                current_user_id,
            )
            for rec in records
            for ann in rec.get("annotations", [])
        }

        # Tuple-precise duplicate prefetch. Using ``span_id__in × label_id__in``
        # would over-fetch the cartesian product — N spans × M labels rows
        # per request, even when the user only submitted N (span, label) pairs.
        # We OR the exact (span, label) tuples instead so the query returns
        # at most ``len(request_keys)`` rows.
        if request_keys:
            from django.db.models import Q

            tuple_filter = Q()
            for span_id, label_id, _ in request_keys:
                tuple_filter |= Q(
                    observation_span_id=span_id,
                    label_id=label_id,
                )
            existing_duplicates = Score.objects.filter(
                tuple_filter,
                annotator_id=current_user_id,
                deleted=False,
            )
        else:
            existing_duplicates = Score.objects.none()

        existing_key_map = {
            (str(s.observation_span_id), str(s.label_id), str(s.annotator_id)): s
            for s in existing_duplicates
        }

        # Collect keys for note duplicate detection. We now rely exclusively on the currently
        # authenticated user's id instead of any `annotator_id` field in the payload.
        note_keys = {
            (str(rec["observation_span_id"]), current_user_id, note["text"])
            for rec in records
            for note in rec.get("notes", [])
        }

        existing_notes_set = set()
        if note_keys:
            existing_notes = SpanNotes.objects.filter(
                span_id__in=[k[0] for k in note_keys],
                created_by_annotator=current_user_id,
                notes__in=[k[2] for k in note_keys],
            )
            existing_notes_set = {
                (n.span_id, n.created_by_annotator, n.notes) for n in existing_notes
            }

        return {
            "span_map": span_map,
            "annotation_label_map": annotation_label_map,
            "existing_key_map": existing_key_map,
            "existing_key_set": set(existing_key_map.keys()),
            "existing_notes_set": existing_notes_set,
        }

    def _process_records(self, request, records, prefetch_data):
        """Process all records and collect annotations/notes to create and any errors."""
        annotations_to_create = []
        notes_to_create = []
        annotations_to_update = []
        errors = []
        warnings = []
        seen_keys = set()
        note_seen = set()

        caller_org = getattr(request.user, "organization", None)

        with transaction.atomic():
            for idx, record in enumerate(records, start=1):
                # Validate individual record
                record_validation = self._validate_record(
                    record, prefetch_data["span_map"], caller_org, idx
                )

                if record_validation["errors"]:
                    errors.extend(record_validation["errors"])
                    continue

                span = record_validation["span"]

                # Process annotations for this record
                annotation_results = self._process_annotations(
                    request, record, span, prefetch_data, caller_org, seen_keys, idx
                )

                annotations_to_create.extend(annotation_results["to_create"])
                annotations_to_update.extend(annotation_results["to_update"])
                errors.extend(annotation_results["errors"])
                warnings.extend(annotation_results["warnings"])

                # Process notes for this record
                note_results = self._process_notes(
                    request,
                    record,
                    span,
                    note_seen,
                    prefetch_data["existing_notes_set"],
                    idx,
                )

                notes_to_create.extend(note_results["to_create"])
                errors.extend(note_results["errors"])

        return {
            "annotations_to_create": annotations_to_create,
            "annotations_to_update": annotations_to_update,
            "notes_to_create": notes_to_create,
            "errors": errors,
            "warnings": warnings,
        }

    def _validate_record(self, record, span_map, caller_org, idx):
        """Validate a single record and return the span if valid."""
        PER_RECORD_LIMIT = 20
        MAX_NOTE_LENGTH = 5000
        errors = []

        annotations_list = record.get("annotations", []) or []
        notes_list = record.get("notes", []) or []

        if not annotations_list and not notes_list:
            errors.append(
                {
                    "record_index": idx,
                    "error": "Record must include either annotations or notes",
                }
            )
            return {"errors": errors, "span": None}

        if len(annotations_list) > PER_RECORD_LIMIT:
            errors.append(
                {
                    "record_index": idx,
                    "error": f"Exceeded annotation limit of {PER_RECORD_LIMIT}",
                }
            )
            return {"errors": errors, "span": None}

        if len(notes_list) > PER_RECORD_LIMIT:
            errors.append(
                {
                    "record_index": idx,
                    "error": f"Exceeded note limit of {PER_RECORD_LIMIT}",
                }
            )
            return {"errors": errors, "span": None}

        # Check note lengths
        for note_idx, note_obj in enumerate(notes_list, start=1):
            note_text = note_obj.get("text", "") or ""
            if len(note_text) > MAX_NOTE_LENGTH:
                errors.append(
                    {
                        "record_index": idx,
                        "note_index": note_idx,
                        "error": f"Note exceeds max length of {MAX_NOTE_LENGTH}",
                    }
                )
                return {"errors": errors, "span": None}

        span_id = record.get("observation_span_id")
        if not span_id:
            errors.append(
                {"record_index": idx, "error": "observation_span_id is required"}
            )
            return {"errors": errors, "span": None}

        span = span_map.get(str(span_id))
        if not span:
            errors.append(
                {"record_index": idx, "span_id": span_id, "error": "Span not found"}
            )
            return {"errors": errors, "span": None}

        # Organization access guard
        if caller_org and span.project.organization_id != caller_org.id:
            errors.append(
                {
                    "record_index": idx,
                    "span_id": span_id,
                    "annotation_error": "Access denied: span ID is Invalid",
                }
            )
            return {"errors": errors, "span": None}

        return {"errors": [], "span": span}

    def _process_annotations(
        self, request, record, span, prefetch_data, caller_org, seen_keys, idx
    ):
        """Process all annotations for a single record."""
        annotations_to_create = []
        annotations_to_update = []
        errors = []
        warnings = []

        annotation_label_map = prefetch_data["annotation_label_map"]
        existing_key_set = prefetch_data["existing_key_set"]
        existing_key_map = prefetch_data["existing_key_map"]

        for ann_data in record.get("annotations", []):
            try:
                annotation_result = self._process_single_annotation(
                    request,
                    ann_data,
                    span,
                    annotation_label_map,
                    caller_org,
                    existing_key_set,
                    existing_key_map,
                    seen_keys,
                    idx,
                )

                if annotation_result.get("error"):
                    errors.append(annotation_result["error"])
                if annotation_result.get("warning"):
                    warnings.append(annotation_result["warning"])
                if annotation_result.get("to_create"):
                    annotations_to_create.append(annotation_result["to_create"])
                if annotation_result.get("to_update"):
                    annotations_to_update.append(annotation_result["to_update"])

            except Exception as e:
                errors.append(
                    {
                        "record_index": idx,
                        "span_id": span.id,
                        "annotation_error": str(e),
                    }
                )

        return {
            "to_create": annotations_to_create,
            "to_update": annotations_to_update,
            "errors": errors,
            "warnings": warnings,
        }

    def _process_single_annotation(
        self,
        request,
        ann_data,
        span,
        annotation_label_map,
        caller_org,
        existing_key_set,
        existing_key_map,
        seen_keys,
        idx,
    ):
        """Process a single annotation and return the result."""
        annotation_label = annotation_label_map.get(
            str(ann_data["annotation_label_id"])
        )
        if not annotation_label:
            return {
                "error": {
                    "record_index": idx,
                    "span_id": span.id,
                    "annotation_error": "Annotation label not found",
                },
                "to_create": None,
                "to_update": None,
            }

        # Check if annotation label belongs to the same project as the span
        if annotation_label.project and annotation_label.project != span.project:
            return {
                "error": {
                    "record_index": idx,
                    "span_id": span.id,
                    "annotation_error": f'Annotation label "{annotation_label.name}" does not belong to span\'s project',
                },
                "to_create": None,
                "to_update": None,
            }

        # Org check for annotation label
        if caller_org and annotation_label.organization_id != caller_org.id:
            return {
                "error": {
                    "record_index": idx,
                    "span_id": span.id,
                    "annotation_error": "Access denied: annotation label ID is Invalid",
                },
                "to_create": None,
                "to_update": None,
            }

        # Validate annotation type and value fields
        validation_result = self._validate_annotation_value(
            ann_data, annotation_label, idx, span.id
        )
        if validation_result["error"]:
            return {
                "error": validation_result["error"],
                "to_create": None,
                "to_update": None,
            }

        value_fields = validation_result["value_fields"]

        # Duplicate detection (authenticated user only)
        duplicate_key = (str(span.id), str(annotation_label.id), str(request.user.id))
        if duplicate_key in seen_keys:
            return {
                "error": {
                    "record_index": idx,
                    "span_id": span.id,
                    "annotation_error": "Duplicate annotation in same request for this annotator",
                },
                "to_create": None,
                "to_update": None,
            }
        seen_keys.add(duplicate_key)

        # Check duplicates already in DB
        if duplicate_key in existing_key_set:
            # Update existing Score
            existing_obj = existing_key_map[duplicate_key]
            existing_obj.value = value_fields
            warning = {
                "record_index": idx,
                "span_id": str(span.id),
                "label_id": str(annotation_label.id),
                "warning": "Existing annotation updated instead of created",
            }
            return {
                "error": None,
                "to_create": None,
                "to_update": existing_obj,
                "warning": warning,
            }

        # Create new Score.
        # NOTE: ``Score.project`` is a FK to ``model_hub.DevelopAI``, NOT
        # to ``tracer.Project``. Earlier code set ``project=span.project``
        # which raised ``ValueError`` at .save() time and silently dropped
        # every record into the response's ``errors`` list. The Score model
        # already filters by ``organization`` for tenancy, and the
        # ``project`` FK is only used for DevelopAI scoping (which doesn't
        # apply to tracer-side annotations), so it's safe to leave NULL.
        score = Score(
            observation_span=span,
            label=annotation_label,
            annotator=request.user,
            source_type="observation_span",
            value=value_fields,
            score_source="human",
            organization=span.project.organization,
        )
        return {
            "error": None,
            "to_create": score,
            "to_update": None,
            "warning": None,
        }

    def _validate_annotation_value(self, ann_data, annotation_label, idx, span_id):
        """Validate annotation value based on its type.

        Returns {"error": ..., "value_fields": dict} where value_fields is a
        Score-compatible JSON dict (e.g. {"value": 5}, {"rating": 3},
        {"text": "..."}, {"selected": [...]}, {"value": "up"/"down"}).
        """
        annotation_type = annotation_label.type

        if annotation_type == AnnotationTypeChoices.TEXT.value:
            if "value" not in ann_data:
                return {
                    "error": {
                        "record_index": idx,
                        "span_id": span_id,
                        "annotation_error": 'TEXT annotation requires "value" field',
                    },
                    "value_fields": None,
                }

        elif annotation_type in [
            AnnotationTypeChoices.NUMERIC.value,
            AnnotationTypeChoices.STAR.value,
        ]:
            if "value_float" not in ann_data:
                return {
                    "error": {
                        "record_index": idx,
                        "span_id": span_id,
                        "annotation_error": f'{annotation_type.upper()} annotation requires "value_float" field',
                    },
                    "value_fields": None,
                }

            # Additional validation against label settings (min/max for numeric, star count, etc.)
            if annotation_label.settings:
                try:
                    numeric_value = float(ann_data["value_float"])
                except (TypeError, ValueError):
                    return {
                        "error": {
                            "record_index": idx,
                            "span_id": span_id,
                            "annotation_error": "value_float must be a number",
                        },
                        "value_fields": None,
                    }

                if annotation_type == AnnotationTypeChoices.NUMERIC.value:
                    min_val = annotation_label.settings.get("min")
                    max_val = annotation_label.settings.get("max")
                    if min_val is not None and numeric_value < min_val:
                        return {
                            "error": {
                                "record_index": idx,
                                "span_id": span_id,
                                "annotation_error": f"value_float {numeric_value} is below minimum {min_val}",
                            },
                            "value_fields": None,
                        }
                    if max_val is not None and numeric_value > max_val:
                        return {
                            "error": {
                                "record_index": idx,
                                "span_id": span_id,
                                "annotation_error": f"value_float {numeric_value} exceeds maximum {max_val}",
                            },
                            "value_fields": None,
                        }

                    # Optional step_size validation
                    step_size = annotation_label.settings.get("step_size")
                    if step_size:
                        remainder = (numeric_value - (min_val or 0)) % step_size
                        # Allow some floating point tolerance
                        if (
                            remainder not in (0, step_size)
                            and remainder > 1e-6
                            and (step_size - remainder) > 1e-6
                        ):
                            return {
                                "error": {
                                    "record_index": idx,
                                    "span_id": span_id,
                                    "annotation_error": f"value_float must align with step_size {step_size}",
                                },
                                "value_fields": None,
                            }

                elif annotation_type == AnnotationTypeChoices.STAR.value:
                    max_stars = annotation_label.settings.get("no_of_stars")
                    if max_stars is not None and (
                        numeric_value < 1 or numeric_value > max_stars
                    ):
                        return {
                            "error": {
                                "record_index": idx,
                                "span_id": span_id,
                                "annotation_error": f"value_float must be between 1 and {max_stars}",
                            },
                            "value_fields": None,
                        }

        elif annotation_type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
            if "value_bool" not in ann_data:
                return {
                    "error": {
                        "record_index": idx,
                        "span_id": span_id,
                        "annotation_error": 'THUMBS_UP_DOWN annotation requires "value_bool" field',
                    },
                    "value_fields": None,
                }

        elif annotation_type == AnnotationTypeChoices.CATEGORICAL.value:
            if "value_str_list" not in ann_data:
                return {
                    "error": {
                        "record_index": idx,
                        "span_id": span_id,
                        "annotation_error": 'CATEGORICAL annotation requires "value_str_list" field',
                    },
                    "value_fields": None,
                }

            # Validate categorical value against allowed options
            if annotation_label.settings and "options" in annotation_label.settings:
                allowed_options = [
                    opt["label"] for opt in annotation_label.settings["options"]
                ]
                invalid_values = [
                    val
                    for val in ann_data["value_str_list"]
                    if val not in allowed_options
                ]
                if invalid_values:
                    return {
                        "error": {
                            "record_index": idx,
                            "span_id": span_id,
                            "annotation_error": f"Invalid categorical values: {', '.join(invalid_values)}. Allowed options: {', '.join(allowed_options)}",
                        },
                        "value_fields": None,
                    }

            # If multi_choice is False, ensure only one value provided
            if annotation_label.settings and not annotation_label.settings.get(
                "multi_choice", True
            ):
                if len(ann_data["value_str_list"]) != 1:
                    return {
                        "error": {
                            "record_index": idx,
                            "span_id": span_id,
                            "annotation_error": "Multiple values provided but this label does not allow multi_selection",
                        },
                        "value_fields": None,
                    }

        else:
            return {
                "error": {
                    "record_index": idx,
                    "span_id": span_id,
                    "annotation_error": f"Unsupported annotation type: {annotation_type}",
                },
                "value_fields": None,
            }

        # Ensure exactly one value field is provided in the payload
        value_keys_provided = [
            k
            for k in ["value", "value_float", "value_bool", "value_str_list"]
            if ann_data.get(k) is not None
        ]
        if len(value_keys_provided) != 1:
            return {
                "error": {
                    "record_index": idx,
                    "span_id": span_id,
                    "annotation_error": "Provide exactly one value field per annotation. Found: "
                    + ", ".join(value_keys_provided),
                },
                "value_fields": None,
            }

        # Extra check: categorical value must be a list
        if (
            annotation_type == AnnotationTypeChoices.CATEGORICAL.value
            and not isinstance(ann_data["value_str_list"], list)
        ):
            return {
                "error": {
                    "record_index": idx,
                    "span_id": span_id,
                    "annotation_error": 'For CATEGORICAL annotations "value_str_list" must be an array.',
                },
                "value_fields": None,
            }

        # TEXT length validations
        if (
            annotation_type == AnnotationTypeChoices.TEXT.value
            and annotation_label.settings
        ):
            min_len = annotation_label.settings.get("min_length")
            max_len = annotation_label.settings.get("max_length")
            value_length = len(ann_data["value"])
            if min_len is not None and value_length < min_len:
                return {
                    "error": {
                        "record_index": idx,
                        "span_id": span_id,
                        "annotation_error": f"Text too short. Minimum length is {min_len} characters.",
                    },
                    "value_fields": None,
                }
            if max_len is not None and value_length > max_len:
                return {
                    "error": {
                        "record_index": idx,
                        "span_id": span_id,
                        "annotation_error": f"Text too long. Maximum length is {max_len} characters.",
                    },
                    "value_fields": None,
                }

        # ── Convert validated input to Score JSON value format ──
        value_fields = self._build_score_value(ann_data, annotation_type)

        return {"error": None, "value_fields": value_fields}

    @staticmethod
    def _build_score_value(ann_data, annotation_type):
        """Convert validated annotation input fields to Score JSON value dict."""
        if annotation_type == AnnotationTypeChoices.TEXT.value:
            return {"text": ann_data["value"]}

        if annotation_type == AnnotationTypeChoices.NUMERIC.value:
            return {"value": float(ann_data["value_float"])}

        if annotation_type == AnnotationTypeChoices.STAR.value:
            return {"rating": float(ann_data["value_float"])}

        if annotation_type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
            return {"value": "up" if ann_data["value_bool"] else "down"}

        if annotation_type == AnnotationTypeChoices.CATEGORICAL.value:
            return {"selected": ann_data["value_str_list"]}

        return {}

    def _process_notes(self, request, record, span, note_seen, existing_notes_set, idx):
        """Process all notes for a single record."""
        notes_to_create = []
        errors = []

        for note_data in record.get("notes", []):
            try:
                note_key = (span.id, request.user.id, note_data["text"])
                if note_key in note_seen:
                    continue  # skip duplicate in same payload
                note_seen.add(note_key)

                # skip if identical note already exists in DB (using prefetched data)
                if note_key in existing_notes_set:
                    continue

                note = SpanNotes(
                    span=span,
                    notes=note_data["text"],
                    created_by_user=request.user,
                    created_by_annotator=request.user.id,
                )
                notes_to_create.append(note)
            except Exception as e:
                errors.append(
                    {"record_index": idx, "span_id": span.id, "note_error": str(e)}
                )

        return {"to_create": notes_to_create, "errors": errors}

    def _save_data(
        self, annotations_to_create, annotations_to_update, notes_to_create, user
    ):
        """Perform bulk operations to save Score objects and notes."""
        annotations_created = 0
        annotations_updated = 0
        notes_created = 0

        # Bulk create Score objects
        if annotations_to_create:
            Score.objects.bulk_create(annotations_to_create)
            annotations_created = len(annotations_to_create)

        # Bulk update Score objects
        if annotations_to_update:
            for obj in annotations_to_update:
                obj.updated_at = timezone.now()
            Score.objects.bulk_update(
                annotations_to_update,
                ["value", "updated_at"],
            )
            annotations_updated = len(annotations_to_update)

        # Bulk create notes
        if notes_to_create:
            SpanNotes.objects.bulk_create(notes_to_create)
            notes_created = len(notes_to_create)

        # Auto-create queue items for default queues and auto-complete
        all_scores = list(annotations_to_create) + list(annotations_to_update)
        if all_scores:
            # Group scores by (source_type, source_obj) for batched queue operations
            source_groups = {}
            for score in all_scores:
                source_type = score.source_type
                source_obj = (
                    score.observation_span
                )  # BulkAnnotationView only handles observation_span
                key = (source_type, source_obj.pk if source_obj else None)
                if key not in source_groups:
                    source_groups[key] = {"source_obj": source_obj, "label_ids": []}
                source_groups[key]["label_ids"].append(score.label_id)

            for (source_type, _), group in source_groups.items():
                try:
                    _auto_create_queue_items_for_default_queues(
                        source_type, group["source_obj"], group["label_ids"]
                    )
                    _auto_complete_queue_items(source_type, group["source_obj"], user)
                except Exception:
                    logger.exception(
                        "Error in queue operations during bulk annotation save"
                    )

        return {
            "annotations_created": annotations_created,
            "annotations_updated": annotations_updated,
            "notes_created": notes_created,
        }

    def _format_response(
        self, annotations_created, annotations_updated, notes_created, errors, warnings
    ):
        """Format the final response with results and errors."""
        response_data = {
            "message": "Bulk annotation completed",
            "annotations_created": annotations_created,
            "annotations_updated": annotations_updated,
            "notes_created": notes_created,
            "succeeded_count": annotations_created
            + annotations_updated
            + notes_created,
            "errors_count": len(errors),
            "warnings_count": len(warnings),
            "warnings": warnings if warnings else None,
            "errors": errors if errors else None,
        }

        return self._gm.success_response(response_data)


class GetAnnotationLabelsView(APIView):
    _gm = GeneralMethods()
    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            queryset = AnnotationsLabels.objects.filter(
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )

            project_id = request.query_params.get("project_id")
            if project_id:
                queryset = queryset.filter(project_id=project_id)

            labels_list = list(
                queryset.values("id", "name", "type", "description", "settings")
            )

            return self._gm.success_response(labels_list)
        except Exception as e:
            logger.exception(f"Error in getting annotation labels: {str(e)}")
            return self._gm.bad_request(f"Error getting annotation labels: {str(e)}")
