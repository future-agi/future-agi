import ast
import json
import traceback
import uuid

import numpy as np
import pandas as pd
import structlog
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Case, CharField, Q, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.models import User
from agentic_eval.core.embeddings.embedding_manager import (
    EmbeddingManager,
)
from tfc.constants.levels import Level

logger = structlog.get_logger(__name__)
from model_hub.models.choices import (
    AnnotationTypeChoices,
    CellStatus,
    DataTypeChoices,
    SourceChoices,
)

# from ee.agenthub.feedback_agent_updated.utils import RAG
from model_hub.models.develop_annotations import Annotations, AnnotationsLabels
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.serializers.develop_annotations import (
    AnnotationsLabelsSerializer,
    AnnotationsSerializer,
    UserSerializer,
)
from model_hub.utils.auto_annotate import generate_annotations_task
from model_hub.utils.SQL_queries import SQLQueryHandler
from model_hub.utils.utils import corpus_builder
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.ee_gating import FeatureUnavailable
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination


class AnnotationsLabelsViewSet(BaseModelViewSetMixinWithUserOrg, viewsets.ModelViewSet):
    serializer_class = AnnotationsLabelsSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = ExtendedPageNumberPagination
    _gm = GeneralMethods()

    def get_queryset(self):
        dataset_id = self.request.query_params.get("dataset", None)
        project_id = self.request.query_params.get("project_id", None)
        label_type = self.request.query_params.get("type", None)
        search = self.request.query_params.get("search", None)
        include_usage_count = (
            self.request.query_params.get("include_usage_count", "").lower() == "true"
        )
        # ``include_archived=true`` returns soft-deleted labels alongside live
        # ones so the frontend can offer a "show archived" toggle. The mixin's
        # default queryset filters ``deleted=False``; using
        # ``AnnotationsLabels.all_objects`` bypasses that.
        include_archived = (
            self.request.query_params.get("include_archived", "").lower() == "true"
        )

        if include_archived:
            queryset = AnnotationsLabels.all_objects.select_related("project")
            # Re-apply the mixin's organization filter manually since we
            # bypassed its get_queryset.
            org = getattr(self.request, "organization", None)
            if org is not None:
                queryset = queryset.filter(organization=org)
        else:
            # Get base queryset with automatic filtering from mixin
            queryset = super().get_queryset().select_related("project")

        if project_id:
            queryset = queryset.filter(project_id=project_id)
            # Add output_type based on annotation type
            queryset = queryset.annotate(
                output_type=Case(
                    When(
                        type=AnnotationTypeChoices.CATEGORICAL.value,
                        then=Value("choices"),
                    ),
                    When(type=AnnotationTypeChoices.TEXT.value, then=Value("text")),
                    When(
                        type=AnnotationTypeChoices.THUMBS_UP_DOWN.value,
                        then=Value("Pass/Fail"),
                    ),
                    default=Value("score"),
                    output_field=CharField(),
                )
            )

        if label_type:
            queryset = queryset.filter(type=label_type)

        if search:
            queryset = queryset.filter(name__icontains=search)

        if include_usage_count:
            from django.db.models import Count, Q
            from django.db.models.functions import Coalesce

            queryset = queryset.annotate(
                trace_annotations_count=Coalesce(
                    Count(
                        "annotation_label",
                        filter=Q(annotation_label__deleted=False),
                        distinct=True,
                    ),
                    Value(0),
                ),
                annotation_count=Coalesce(
                    Count(
                        "scores",
                        filter=Q(scores__deleted=False),
                        distinct=True,
                    ),
                    Value(0),
                ),
            )

        if dataset_id:
            valid_label_ids = []
            col_ids = Column.objects.filter(
                dataset_id=dataset_id, deleted=False
            ).values_list("id", flat=True)
            valid_column_ids = set(map(str, col_ids))
            for label in list(queryset):
                is_valid = True
                if label.type == AnnotationTypeChoices.CATEGORICAL.value:
                    input_columns = label.settings.get("inputs", [])
                    if input_columns:
                        for col_id in input_columns:
                            try:
                                if col_id not in valid_column_ids:
                                    is_valid = False
                                    break

                            except Exception:
                                is_valid = False
                                break

                if is_valid:
                    valid_label_ids.append(label.id)

            queryset = queryset.filter(id__in=valid_label_ids)

        return queryset.order_by("-created_at")

    def perform_create(self, serializer):
        try:
            serializer.save(
                organization=getattr(self.request, "organization", None)
                or self.request.user.organization
            )
            return self._gm.success_response("Annotation label created successfully")
        except ValidationError as e:
            raise serializers.ValidationError(e)  # noqa: B904
        except Exception as e:
            logger.exception(f"Error in annotation label creation: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("ANNOTATION_LABEL_CREATION_FAILED")
            )

    def perform_update(self, serializer):
        try:
            serializer.save()
            return self._gm.success_response("Annotation label updated successfully")
        except ValidationError as e:
            raise serializers.ValidationError(e)  # noqa: B904
        except Exception as e:
            logger.exception(f"Error in annotation label updation: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("ANNOTATION_LABEL_UPDATION_FAILED")
            )

    def perform_destroy(self, instance):
        try:
            instance.delete()
            return self._gm.success_response("Annotation label deleted successfully")
        except Exception as e:
            logger.exception(f"Error in annotation label deletion: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("ANNOTATION_LABEL_DELETION_FAILED")
            )

    def add_output_type(self, data):
        for item in data:
            label_type = item.get("type")
            if label_type == AnnotationTypeChoices.CATEGORICAL.value:
                item["output_type"] = "choices"
            elif label_type == AnnotationTypeChoices.TEXT.value:
                item["output_type"] = "text"
            elif label_type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
                item["output_type"] = "Pass/Fail"
            else:
                item["output_type"] = "score"

        return data

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()

            # Apply pagination
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                data = serializer.data

                # Add output_type to each item
                data = self.add_output_type(data)

                return self.get_paginated_response(data)

            # If no pagination is applied, return all data
            serializer = self.get_serializer(queryset, many=True)
            data = serializer.data

            data = self.add_output_type(data)

            return self._gm.success_response(data)
        except Exception as e:
            logger.exception(f"Error in listing annotation labels: {str(e)}")
            return self._gm.internal_server_error_response(
                "Failed to list annotation labels"
            )

    def create(self, request, *args, **kwargs):
        """Custom create to provide clearer error responses in GM format."""
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            # Return validation errors in the same structure frontend expects.
            logger.warning(f"Annotation label validation failed: {serializer.errors}")
            return self._gm.bad_request(
                f"Annotation label creation failed: {serializer.errors}"
            )

        try:
            serializer.save(
                organization=getattr(request, "organization", None)
                or request.user.organization
            )
        except serializers.ValidationError as exc:
            # Serializer or model raised duplicate-label validation.
            detail = exc.detail
            logger.warning(f"Annotation label save failed: {detail}")
            return self._gm.bad_request(f"Annotation label creation failed: {detail}")
        except ValidationError as exc:
            # AnnotationsLabels.save() calls full_clean() which raises Django's
            # ValidationError on bad settings (e.g. numeric min >= max). Catch
            # it here so the API returns 400 instead of bubbling up to a 500.
            detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            logger.warning(f"Annotation label save failed (model validation): {detail}")
            return self._gm.bad_request(f"Annotation label creation failed: {detail}")

        return self._gm.success_response("Annotation label created successfully")

    @action(detail=True, methods=["post"], url_path="restore")
    def restore(self, request, pk=None):
        """Restore a soft-deleted (archived) annotation label."""
        try:
            label = AnnotationsLabels.all_objects.get(
                pk=pk,
                deleted=True,
                organization=request.user.organization,
            )
        except AnnotationsLabels.DoesNotExist:
            return self._gm.not_found("Label not found or not archived.")

        label.deleted = False
        label.deleted_at = None
        label.save(update_fields=["deleted", "deleted_at", "updated_at"])

        serializer = self.get_serializer(label)
        return self._gm.success_response(serializer.data)


class AnnotationsViewSet(BaseModelViewSetMixinWithUserOrg, viewsets.ModelViewSet):
    serializer_class = AnnotationsSerializer
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def get_queryset(self):
        dataset_id = self.request.query_params.get("dataset", None)

        # Get base queryset with automatic filtering from mixin
        queryset = (
            super()
            .get_queryset()
            .select_related("dataset")
            .prefetch_related("assigned_users", "labels", "columns")
        )

        if dataset_id:
            try:
                dataset = get_object_or_404(Dataset, id=dataset_id)
                queryset = queryset.filter(dataset=dataset)
            except Exception as e:
                logger.exception(f"Error in loading annotation: {str(e)}")
                # Note: get_queryset cannot return Response, so we return empty queryset
                return Annotations.objects.none()

        return queryset

    def create(self, request, *args, **kwargs):
        try:
            # Extract label requirements before modifying request data
            labels_data = request.data.get("labels", [])
            label_requirements = {}
            has_required_labels = False

            # Create a new request.data with only label IDs
            modified_data = request.data.copy()
            modified_data["labels"] = []

            for label_item in labels_data:
                if isinstance(label_item, dict):
                    label_id = label_item["id"]
                    required = label_item.get("required", True)
                    modified_data["labels"].append(label_id)
                    label_requirements[str(label_id)] = required
                    has_required_labels = has_required_labels or bool(required)
                else:
                    modified_data["labels"].append(label_item)
                    label_requirements[str(label_item)] = True
                    has_required_labels = True

            if has_required_labels:
                from tfc.ee_gating import EEFeature, check_ee_feature

                org = (
                    getattr(request, "organization", None)
                    or request.user.organization
                )
                check_ee_feature(EEFeature.REQUIRED_LABELS, org_id=str(org.id))

            serializer = self.get_serializer(data=modified_data)
            serializer.is_valid(raise_exception=True)
            annotation = serializer.save(
                organization=getattr(request, "organization", None)
                or request.user.organization
            )

            # Then set M2M fields and validate them
            assigned_users = request.data.get("assigned_users", [])
            if assigned_users:
                annotation.assigned_users.set(assigned_users)
                # Explicitly run M2M validations
                annotation.validate_assigned_users()

            # Set and validate labels
            if modified_data["labels"]:
                annotation.labels.set(modified_data["labels"])
                annotation.validate_labels()

            self.process_new_annotaion(
                annotation, annotation.labels.all(), label_requirements
            )

            # Emit storage usage event for annotation creation
            try:
                import json as _json

                try:
                    from ee.usage.schemas.events import UsageEvent
                except ImportError:
                    UsageEvent = None
                try:
                    from ee.usage.services.emitter import emit
                except ImportError:
                    emit = None

                org = (
                    getattr(request, "organization", None) or request.user.organization
                )
                annotation_size = len(
                    _json.dumps(serializer.validated_data, default=str).encode()
                )
                # Non-chargeable tracking event — annotation creation is free.
                # "annotation_creation" is intentionally not in billing.yaml.
                emit(
                    UsageEvent(
                        org_id=str(org.id),
                        event_type="annotation_creation",
                        amount=annotation_size,
                        properties={
                            "source": "annotation",
                            "source_id": str(annotation.id),
                        },
                    )
                )
            except Exception:
                logger.debug("emit_annotation_event_failed")

            return self._gm.success_response("Annotation created successfully")
        except ValidationError:
            return self._gm.bad_request(get_error_message("ANNOTATION_CREATION_FAILED"))
        except FeatureUnavailable:
            raise
        except Exception as e:
            logger.exception(f"Error in creating annotation: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("ANNOTATION_CREATION_FAILED")
            )

    def update(self, request, *args, **kwargs):
        try:
            instance = self.get_object()

            # Process labels and their requirements
            labels_data = request.data.get("labels", [])
            label_requirements = {}
            modified_data = request.data.copy()
            modified_data["labels"] = []
            has_required_labels = False

            for label_item in labels_data:
                if isinstance(label_item, dict):
                    label_id = label_item["id"]
                    required = label_item.get("required", True)
                    modified_data["labels"].append(label_id)
                    label_requirements[str(label_id)] = required
                    has_required_labels = has_required_labels or bool(required)
                else:
                    modified_data["labels"].append(label_item)
                    label_requirements[str(label_item)] = True
                    has_required_labels = True

            if has_required_labels:
                from tfc.ee_gating import EEFeature, check_ee_feature

                org = (
                    getattr(request, "organization", None)
                    or request.user.organization
                )
                check_ee_feature(EEFeature.REQUIRED_LABELS, org_id=str(org.id))

            serializer = self.get_serializer(instance, data=modified_data)
            serializer.is_valid(raise_exception=True)

            # Get the old labels before saving
            old_labels = set(instance.labels.all())
            old_users = set(instance.assigned_users.all())
            old_responses = instance.responses

            # Save the updated annotation
            annotation = serializer.save()
            new_labels = set(annotation.labels.all())
            new_responses = annotation.responses
            new_users = set(annotation.assigned_users.all())

            # Then set M2M fields and validate them
            annotation.assigned_users.clear()
            assigned_users = request.data.get("assigned_users", [])
            removed_user = old_users - new_users

            cells = Cell.objects.filter(
                dataset=annotation.dataset,
                column__in=annotation.columns.all(),
                deleted=False,
                feedback_info__annotation__user_id__in=removed_user,
            )

            for cell in cells:
                feedback_info = cell.feedback_info or {}
                if "annotation" in feedback_info:
                    feedback_info["annotation"]["user_id"] = None
                    cell.value = None
                    cell.feedback_info = feedback_info

            if cells:
                Cell.objects.bulk_update(cells, ["value", "feedback_info"])

            if assigned_users:
                for user in assigned_users:
                    annotation.assigned_users.add(user)
                # Explicitly run M2M validations
                annotation.validate_assigned_users()

            # Set and validate labels'
            annotation.labels.clear()
            if modified_data["labels"]:
                for label in modified_data["labels"]:
                    annotation.labels.add(label)
                annotation.validate_labels()

            # Find removed and added labels
            removed_labels = old_labels - new_labels
            added_labels = new_labels - old_labels

            rows = Row.objects.filter(dataset=annotation.dataset, deleted=False)

            if removed_labels:
                for label in removed_labels:
                    source_id = f"{annotation.id}-sourceid-{label.id}"
                    columns_to_delete = Column.objects.filter(
                        source_id=source_id, dataset=annotation.dataset, deleted=False
                    )

                    # Delete cells for these columns
                    Cell.objects.filter(
                        dataset=annotation.dataset,
                        row__in=rows,
                        column__in=columns_to_delete,
                        deleted=False,
                    ).update(deleted=True, deleted_at=timezone.now())

                    # Bulk update columns
                    columns_list = list(columns_to_delete)
                    column_ids = [str(column.id) for column in columns_list]

                    # Update dataset configuration
                    annotation.dataset.column_order = [
                        col
                        for col in annotation.dataset.column_order
                        if col not in column_ids
                    ]
                    annotation.dataset.column_config = {
                        k: v
                        for k, v in annotation.dataset.column_config.items()
                        if k not in column_ids
                    }

                    # Bulk update columns
                    now = timezone.now()
                    for column in columns_list:
                        column.deleted = True
                        column.deleted_at = now

                    Column.objects.bulk_update(columns_list, ["deleted", "deleted_at"])

                    # Remove columns from annotation's columns in bulk
                    annotation.columns.remove(*columns_list)

                # Save dataset and annotation changes
                annotation.dataset.save()
                annotation.save()

            # Handle response count changes
            if old_responses != new_responses:
                existing_labels = old_labels & new_labels

                if new_responses < old_responses:
                    # Get columns to delete for reduced responses
                    columns_to_delete = []

                    for label in existing_labels:
                        source_id = f"{annotation.id}-sourceid-{label.id}"
                        columns = Column.objects.filter(
                            source_id__startswith=source_id,
                            dataset=annotation.dataset,
                            deleted=False,
                        ).order_by("created_at")[: old_responses - new_responses]
                        columns_to_delete.extend(columns)

                    # Delete associated cells
                    Cell.objects.filter(
                        dataset=annotation.dataset,
                        row__in=rows,
                        column__in=columns_to_delete,
                        deleted=False,
                    ).update(deleted=True, deleted_at=timezone.now())

                    # Update dataset configuration
                    for column in columns_to_delete:
                        if str(column.id) in annotation.dataset.column_order:
                            annotation.dataset.column_order.remove(str(column.id))
                        if str(column.id) in annotation.dataset.column_config:
                            del annotation.dataset.column_config[str(column.id)]

                        # Remove column from annotation's columns
                        annotation.columns.remove(column)

                        # Mark column as deleted
                        column.deleted = True
                        column.deleted_at = timezone.now()
                        column.save()

                    # Save dataset changes
                    annotation.dataset.save()
                    annotation.save()

                if new_responses > old_responses:
                    # Create new columns for additional responses
                    for label in existing_labels:
                        for response in range(old_responses, new_responses):
                            column_name = f"{annotation.name}:{label.name}:{response}"
                            column = Column.objects.create(
                                id=uuid.uuid4(),
                                name=column_name,
                                data_type=self.get_data_type_for_label(label.type),
                                source=SourceChoices.ANNOTATION_LABEL.value,
                                dataset=annotation.dataset,
                                source_id=f"{annotation.id}-sourceid-{label.id}",
                            )

                            # Add column to annotation and update dataset config
                            annotation.columns.add(column)
                            annotation.dataset.column_order.append(str(column.id))
                            annotation.dataset.column_config[str(column.id)] = {
                                "is_frozen": False,
                                "is_visible": True,
                            }

                    annotation.dataset.save()
                    annotation.save()

            if label_requirements:
                annotation.summary["label_requirements"] = label_requirements
                annotation.save()

            if added_labels:
                self.process_new_annotaion(annotation, added_labels, label_requirements)

            return self._gm.success_response("Annotation updated successfully")
        except ValidationError:
            return self._gm.bad_request(
                get_error_message("FAILED_TO_UPDATE_ANNOTATION")
            )
        except Exception as e:
            logger.exception(f"Error in updating annotation: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_UPDATE_ANNOTATION")
            )

    def destroy(self, request, *args, **kwargs):
        try:
            annotation = self.get_object()

            rows = Row.objects.filter(dataset=annotation.dataset)
            columns = Column.objects.filter(
                source_id__startswith=f"{annotation.id}",
                dataset=annotation.dataset,
                deleted=False,
            ).values_list("id", flat=True)
            cells = Cell.objects.filter(
                dataset=annotation.dataset, row__in=rows, column__in=columns
            )

            dataset = annotation.dataset
            # Remove column from column_order and column_config
            column_str_ids = [str(col_id) for col_id in columns]
            dataset.column_order = [
                col_id
                for col_id in dataset.column_order
                if col_id not in column_str_ids
            ]

            # Remove from column_config if it exists
            for col_id in column_str_ids:
                dataset.column_config.pop(col_id, None)

            columns.update(deleted=True, deleted_at=timezone.now())
            cells.update(deleted=True, deleted_at=timezone.now())
            dataset.save()

            annotation.delete()

            return self._gm.success_response("Annotation deleted successfully")
        except ValidationError:
            return self._gm.bad_request(
                get_error_message("FAILED_TO_DELETE_ANNOTATION")
            )
        except Exception as e:
            logger.exception(f"Error in deleting annotation: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_DELETE_ANNOTATION")
            )

    @action(detail=False, methods=["post"])
    @transaction.atomic
    def bulk_destroy(self, request):
        """
        Bulk delete annotations and their associated data
        Expected input: {"annotation_ids": ["uuid1", "uuid2", ...]}
        """
        try:
            annotation_ids = request.data.get("annotation_ids", [])
            if not annotation_ids:
                return self._gm.bad_request(get_error_message("ANNOTATION_ID_REQUIRED"))

            annotations = Annotations.objects.filter(
                id__in=annotation_ids,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )

            if not annotations.exists():
                return self._gm.not_found(get_error_message("ANNOTATION_NOT_FOUND"))

            deleted_count = 0
            errors = []

            for annotation in annotations:
                try:
                    self.kwargs["pk"] = annotation.id
                    self.destroy(request)
                    deleted_count += 1
                except Exception as e:
                    errors.append(
                        f"Failed to delete annotation {annotation.id}: {str(e)}"
                    )

            response_data = {
                "message": f"Successfully deleted {deleted_count} annotations",
                "deleted_count": deleted_count,
            }

            if errors:
                response_data["errors"] = errors

            return self._gm.success_response(response_data)

        except Exception as e:
            logger.exception(f"Error in deleting annotation: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_DELETE_ANNOTATION")
            )

    def get_data_type_for_label(self, label_type):
        # Map annotation types to column data types
        type_mapping = {
            AnnotationTypeChoices.NUMERIC.value: DataTypeChoices.FLOAT.value,
            AnnotationTypeChoices.TEXT.value: DataTypeChoices.TEXT.value,
            AnnotationTypeChoices.CATEGORICAL.value: DataTypeChoices.ARRAY.value,
            # Add more mappings as needed
        }
        return type_mapping.get(
            label_type, "text"
        )  # Default to string if type not found

    @action(detail=True, methods=["post"])
    def update_cells(self, request, pk=None):
        try:
            annotation = self.get_object()
            label_updates = request.data.get("label_values", [])
            response_fields_updates = request.data.get("response_field_values", [])

            if not label_updates and not response_fields_updates:
                return self._gm.bad_request(
                    get_error_message("LABLE_VALUES_OR_RESPONSE_FIELD_VALUES_MISSING")
                )

            if not annotation.assigned_users.filter(id=request.user.id).exists():
                return self._gm.forbidden_response(
                    get_error_message("NOT_AUTHORIZED_TO_UPDATE")
                )

            # Handle label updates
            for update in label_updates:
                row_id = update.get("row_id")
                label_id = update.get("label_id")
                value = update.get("value")
                description = update.get("description", "")
                column_id = update.get("column_id")
                time_taken = update.get("time_taken")
                if not all([row_id, label_id, value]):
                    return self._gm.bad_request(
                        get_error_message(
                            "ROW_ID_LABEL_ID_AND_VALUE_REQUIRED_IN_LABLE_UPDATE"
                        )
                    )

                label = AnnotationsLabels.objects.get(id=label_id)
                auto_annotate = label.settings.get("auto_annotate", False)

                if label.type == AnnotationTypeChoices.CATEGORICAL.value:
                    if isinstance(value, str):
                        try:
                            value = json.loads(value.replace("'", '"'))
                        except json.JSONDecodeError:
                            value = [value]

                self.validate_label_value(label, value)

                if Cell.objects.filter(
                    row_id=row_id,
                    column_id=column_id,
                    deleted=False,
                    value__regex=r"^(?!\s*$).+",
                    feedback_info__annotation__has_key="user_id",
                ).exists():
                    # if someother user has updated the value at same time then we need to check if we can annotate the value then we will give another column id if available
                    can_annotate, column_id = self.can_user_annotate(
                        annotation.id, row_id, label.id, request.user.id
                    )
                    if not can_annotate:
                        return self._gm.forbidden_response(
                            f"{get_error_message('CANNOT_ANNOTATE_LABEL')} {row_id} "
                        )

                try:
                    column = Column.objects.get(id=column_id)
                except Column.DoesNotExist:
                    return self._gm.not_found("Column not found")

                # Update cell value
                cell, created = Cell.objects.get_or_create(
                    dataset=annotation.dataset, row_id=row_id, column=column
                )

                # Initialize cell_value for comparison (used by auto_annotate for categorical)
                cell_value = []
                if label.type == AnnotationTypeChoices.CATEGORICAL.value:
                    try:
                        cell_value = cell.value if cell.value else []
                        if isinstance(cell_value, str):
                            if cell_value.startswith("[") and cell_value.endswith("]"):
                                cell_value = json.loads(cell_value.replace("'", '"'))
                            else:
                                cell_value = [cell_value]
                    except json.JSONDecodeError:
                        cell_value = [cell_value]

                if label.type == AnnotationTypeChoices.TEXT.value:
                    existing_metadata = label.metadata.get(str(annotation.dataset_id))
                    if existing_metadata:
                        sentences = [
                            (
                                " ".join(existing_metadata.get("vocab"))
                                if existing_metadata.get("vocab")
                                else ""
                            ),
                            value,
                        ]
                        min_len = existing_metadata.get("min_len")
                        max_len = existing_metadata.get("max_len")
                        avg_len = existing_metadata.get("avg_len")

                        vocab, top_20, min_len, max_len, avg_len = (
                            corpus_builder.build_annotation_corpus(sentences)
                        )

                        label.metadata[str(annotation.dataset_id)] = {
                            "vocab": vocab,
                            "top_20": top_20,
                            "min_len": min_len,
                            "max_len": max_len,
                            "avg_len": avg_len,
                        }
                        label.save()

                if (
                    auto_annotate
                    and set(cell_value) == set(value)
                    and not cell.feedback_info.get("annotation", {}).get("user_id")
                ):
                    cell.status = CellStatus.PASS.value
                    cell.feedback_info = {
                        "description": description if description else "",
                        "annotation": {
                            "user_id": str(request.user.id),
                            "auto_annotate": auto_annotate,
                            "verified": True,
                            "label_id": str(label.id),
                            "annotation_id": str(annotation.id),
                            "time_taken": time_taken,
                        },
                    }
                    cell.save()
                else:
                    if (
                        label.type == AnnotationTypeChoices.CATEGORICAL.value
                        and isinstance(value, str)
                    ):
                        value = [value]

                    cell.value = value
                    cell.status = CellStatus.PASS.value
                    cell.feedback_info = {
                        "description": description if description else "",
                        "annotation": {
                            "user_id": str(request.user.id),
                            "auto_annotate": auto_annotate,
                            "verified": False,
                            "label_id": str(label.id),
                            "annotation_id": str(annotation.id),
                            "time_taken": time_taken,
                        },
                    }
                    cell.save()
                    if auto_annotate:
                        # Initialize row data for RAG
                        row_dict = {}
                        inputs = []
                        row_dict["feedback_comment"] = description
                        row_dict["feedback_value"] = value

                        # Get all cells for the current row
                        if label_updates:
                            row_id = label_updates[0].get("row_id")
                            row_cells = Cell.objects.filter(
                                dataset=annotation.dataset, row_id=row_id, deleted=False
                            ).select_related("column")

                            # Add cell values to row_dict
                            for cell in row_cells:
                                column_id = str(cell.column.id)
                                row_dict[column_id] = cell.value

                            # Get input fields from label settings
                            for label in annotation.labels.all():
                                if (
                                    label.type
                                    == AnnotationTypeChoices.CATEGORICAL.value
                                ):
                                    input_columns = label.settings.get("inputs", [])
                                    inputs.extend(input_columns)

                        # Call RAG data formatter with the collected data
                        embedding_manager = EmbeddingManager()
                        # get_fewshots = RAG()
                        embedding_manager.data_formatter(
                            eval_id=annotation.id,
                            row_dict=row_dict,
                            inputs_formater=inputs,
                            organization_id=annotation.dataset.organization.id,
                            workspace_id=(
                                annotation.dataset.workspace.id
                                if annotation.dataset.workspace
                                else None
                            ),
                        )
                        embedding_manager.close()
                        generate_annotations_task.apply_async(
                            args=(label.id, annotation.id, row_id)
                        )

            # Handle response fields updates
            for update in response_fields_updates:
                row_id = str(update.get("row_id"))
                column_id = str(update.get("column_id"))
                value = update.get("value")

                if not all([row_id, column_id, value]):
                    return self._gm.bad_request(
                        get_error_message("ROW_ID_COLUMN_ID_AND_VALUE_REQUIRED")
                    )

                try:
                    column = Column.objects.get(id=column_id)
                except Column.DoesNotExist:
                    return self._gm.not_found(
                        f"{get_error_message('COLUMN_NOT_FOUND')} {column_id}"
                    )

                # Update cell value for response fields
                cell, created = Cell.objects.get_or_create(
                    dataset=annotation.dataset, row_id=row_id, column_id=column_id
                )
                cell.value = value
                cell.save()

            return self._gm.success_response({"message": "Cells updated successfully"})

        except Exception as e:
            logger.exception(f"Error in cell updation: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("CELLS_UPDATION_FAILED")
            )

    def validate_label_value(self, label, value):
        """
        Validates if the provided value matches the label type constraints.
        """
        try:
            if label.type == AnnotationTypeChoices.TEXT.value and not isinstance(
                value, str
            ):
                raise ValidationError(
                    f"Value for text annotation must be a string, got {type(value).__name__}"
                )

            if label.type == AnnotationTypeChoices.NUMERIC.value and not isinstance(
                value, int | float
            ):
                raise ValidationError(
                    f"Value for numeric annotation must be a int/float, got {type(value).__name__}"
                )

            if label.type == AnnotationTypeChoices.CATEGORICAL.value:
                if isinstance(value, str):
                    try:
                        value = json.loads(value.replace("'", '"'))
                    except json.JSONDecodeError:
                        value = [value]

                values_to_check = value if isinstance(value, list) else [value]
                options = label.settings.get("options", [])
                valid_labels = [
                    opt["label"]
                    for opt in options
                    if isinstance(opt, dict) and "label" in opt
                ]

                invalid_values = [
                    val for val in values_to_check if val not in valid_labels
                ]
                if invalid_values:
                    raise ValidationError(
                        f"Values {invalid_values} are not in allowed options for {label.name}: {valid_labels}"
                    )

        except ValidationError as e:
            raise e
        except Exception as e:
            raise ValidationError(f"Validation error: {str(e)}")  # noqa: B904

    @action(detail=True, methods=["post"])
    def reset_annotations(self, request, pk=None):
        try:
            annotation = Annotations.objects.get(id=pk)
            row_id = str(request.data.get("row_id"))
            user_id = str(request.user.id)

            if request.user not in annotation.assigned_users.all():
                return self._gm.forbidden_response(
                    get_error_message("ONLY_OWNER_CAN_VIEW_TEAMS")
                )
            if not row_id:
                return self._gm.bad_request(get_error_message("ROW_ID_MISSING"))

            try:
                row = Row.objects.get(id=row_id)
            except Row.DoesNotExist:
                return self._gm.bad_request(get_error_message("ROW_NOT_EXIST"))

            cells = list(
                Cell.objects.filter(
                    dataset=annotation.dataset,
                    row_id=row_id,
                    column__in=annotation.columns.all(),
                    deleted=False,
                    feedback_info__annotation__user_id=user_id,
                )
            )

            cells_to_update = []
            for cell in cells:
                feedback_info = cell.feedback_info or {}
                if "annotation" in feedback_info:
                    feedback_info["annotation"]["user_id"] = None
                    cell.value = None
                    cell.feedback_info = feedback_info
                    cells_to_update.append(cell)

            if cells_to_update:
                Cell.objects.bulk_update(cells_to_update, ["value", "feedback_info"])

            if row.order < annotation.lowest_unfinished_row:
                annotation.lowest_unfinished_row = row.order
                annotation.save()

            return self._gm.success_response(
                {"message": "Annotations reset successfully"}
            )

        except Annotations.DoesNotExist:
            return self._gm.not_found(get_error_message("ANNOTATION_ID_NOT_EXIST"))
        except Exception as e:
            logger.exception(f"Error in reseting the annotation: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_RESET_ANNOTATION")
            )

    @action(detail=True, methods=["get"])
    def annotate_row(self, request, pk=None):
        """
        Annotate a specific row with the provided values.
        """
        try:
            # Retrieve the annotation object
            annotation = Annotations.objects.get(id=pk, deleted=False)
            Dataset.objects.get(id=annotation.dataset.id, deleted=False)

            # Get the row order from the request data
            row_order = request.query_params.get(
                "row_order"
            ) or request.query_params.get("rowOrder")
            if row_order is None:
                return self._gm.bad_request(get_error_message("ROW_ORDER_MISSING"))

            row = Row.objects.get(dataset=annotation.dataset, order=int(row_order))

            try:
                next_row = (
                    Row.objects.filter(
                        dataset=annotation.dataset,
                        order__gt=int(row_order),
                        deleted=False,
                    )
                    .order_by("order")
                    .first()
                )
            except Row.DoesNotExist:
                next_row = None

            total_rows = Row.objects.filter(
                dataset=annotation.dataset, deleted=False
            ).order_by("order")
            row_id_to_number = {
                str(r.id): index + 1 for index, r in enumerate(list(total_rows))
            }

            try:
                previous_row = (
                    Row.objects.filter(
                        dataset=annotation.dataset,
                        order__lt=int(row_order),
                        deleted=False,
                    )
                    .order_by("-order")
                    .first()
                )
            except Row.DoesNotExist:
                previous_row = None

            first_row = total_rows.first()
            last_row = total_rows.last()

            # Prepare the data to return
            result = {
                "label": [],
                "static_fields": [],
                "response_fields": [],
                "next_row_order": next_row.order if next_row else None,
                "next_row_number": (
                    row_id_to_number.get(str(next_row.id)) if next_row else None
                ),
                "previous_row_order": previous_row.order if previous_row else None,
                "previous_row_number": (
                    row_id_to_number.get(str(previous_row.id)) if previous_row else None
                ),
                "first_row_order": first_row.order if first_row else None,
                "last_row_order": last_row.order if last_row else None,
                "current_row_number": row_id_to_number.get(str(row.id)),
                "total_rows": total_rows.count(),
                "label_requirements": annotation.summary.get("label_requirements", {}),
            }

            # Process labels and corresponding cells
            for label in annotation.labels.all():
                can_annotate, column_id = self.can_user_annotate(
                    annotation.id, row.id, label.id, request.user.id
                )
                cell_value = None
                cell = None
                if can_annotate:
                    column = Column.objects.get(id=column_id)
                    cell, created = Cell.objects.get_or_create(
                        dataset=annotation.dataset, column=column, row=row
                    )
                    cell_value = cell.value
                    if cell_value:
                        if label.type == AnnotationTypeChoices.CATEGORICAL.value:
                            try:
                                if isinstance(cell_value, str):
                                    if cell_value.startswith(
                                        "["
                                    ) and cell_value.endswith("]"):
                                        cell_value = json.loads(
                                            cell_value.replace("'", '"')
                                        )
                                    else:
                                        cell_value = [cell_value]
                            except json.JSONDecodeError:
                                cell_value = [cell_value]
                        elif label.type == AnnotationTypeChoices.NUMERIC.value:
                            try:
                                cell_value = float(cell_value)
                            except (ValueError, TypeError):
                                cell_value = None

                result["label"].append(
                    {
                        "label_id": str(label.id),  # Convert UUID to string
                        "label_name": label.name,
                        "label_type": label.type,
                        "label_settings": label.settings,
                        "can_annotate": can_annotate,
                        "row_id": str(row.id) if can_annotate else None,
                        "column_id": (
                            str(column_id) if column_id else None
                        ),  # Convert UUID to string
                        "cell_value": cell_value,
                        "cell_description": (
                            cell.feedback_info.get("description", "")
                            if cell and isinstance(cell.feedback_info, dict)
                            else ""
                        ),
                    }
                )

            # Process static and response fields
            for field_type in ["static_fields", "response_fields"]:
                fields = getattr(annotation, field_type, []) or []
                for field in fields:
                    try:
                        column = Column.objects.get(id=field["column_id"])
                        cell = Cell.objects.get(
                            dataset=annotation.dataset,
                            column=column,
                            row=row,
                            deleted=False,
                        )
                        value = cell.value
                        if value and column.data_type == DataTypeChoices.ARRAY.value:
                            try:
                                if isinstance(value, str):
                                    if value.startswith("[") and value.endswith("]"):
                                        value = json.loads(value.replace("'", '"'))
                                    else:
                                        value = [value]
                            except json.JSONDecodeError:
                                value = [value]
                        field_data = {
                            "field_type": field_type,
                            "column_name": column.name,
                            "column_id": str(column.id),  # Convert UUID to string
                            "value": cell.value,
                            "row_id": str(row.id),  # Convert UUID to string
                        }
                        field_data.update(field)
                        result[field_type].append(field_data)
                    except (Column.DoesNotExist, Cell.DoesNotExist):
                        continue  # Skip if column or cell not found

            serializer = AnnotationsSerializer(annotation, context={"request": request})
            result["summary"] = serializer.data.get("summary", {})

            return self._gm.success_response(
                {"message": "Row annotated successfully.", "data": result}
            )

        except Dataset.DoesNotExist:
            return self._gm.not_found(get_error_message("DATASET_NOT_FOUND"))
        except Row.DoesNotExist:
            return self._gm.not_found(get_error_message("ROW_NOT_FOUND"))
        except Annotations.DoesNotExist:
            return self._gm.not_found(get_error_message("ANNOTATION_NOT_FOUND"))
        except Exception as e:
            logger.exception(f"Error in annotation of row: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_ANNOTATION_ROW")
            )

    def can_user_annotate(self, annotation_id, row_id, label_id, user_id):
        """
        Check if a user can annotate a specific label in a row and return appropriate column_id

        Returns:
            tuple: (can_annotate: bool, column_id: str | None)
            - If user has already annotated: (True, existing_column_id)
            - If empty column available: (True, available_column_id)
            - If no columns available: (False, None)
        """

        annotation = Annotations.objects.get(id=annotation_id, deleted=False)
        row = Row.objects.get(id=row_id)
        label = AnnotationsLabels.objects.get(id=label_id)

        if not annotation.assigned_users.filter(id=user_id).exists():
            return False, None

        # Get all cells for this annotation-label
        source_id = f"{annotation.id}-sourceid-{label.id}"
        cells = Cell.objects.filter(
            dataset=annotation.dataset,
            row=row,
            column__source_id=source_id,
            deleted=False,
        ).select_related("column")

        available_column_id = None
        responses = annotation.responses
        if not cells:
            col = (
                Column.objects.filter(source_id=source_id)
                .order_by("created_at")
                .first()
            )
            if col is None:
                return False, None
            return True, col.id
        if len(cells) != responses:
            cols = list(
                Column.objects.filter(source_id=source_id).order_by("created_at")
            )
            if len(cells) < len(cols):
                col = cols[len(cells)]
                return True, col.id
            return False, None

        for cell in cells:
            feedback_info = cell.feedback_info or {}
            annotation_info = feedback_info.get("annotation", {})

            # Check if this user has already annotated
            if annotation_info.get("user_id") == str(user_id):
                return True, str(cell.column_id)

            # Keep track of first available column
            if not annotation_info.get("user_id") and available_column_id is None:
                available_column_id = str(cell.column_id)

        # Return available column if found
        if available_column_id:
            return True, available_column_id

        return False, None

    @action(detail=False, methods=["post"])
    def preview_annotations(self, request):
        """
        Preview the first row of data for specified columns in a dataset.
        """
        dataset_id = request.data.get("dataset_id")
        static_columns = request.data.get("static_column", [])
        response_columns = request.data.get("response_column", [])

        if not dataset_id:
            return self._gm.bad_request(get_error_message("DATASET_ID_MISSING"))

        try:
            Dataset.objects.get(
                id=dataset_id,
                deleted=False,
                organization=getattr(request, "organization", None)
                or request.user.organization,
            )
        except Dataset.DoesNotExist:
            return self._gm.not_found(get_error_message("DATASET_NOT_FOUND"))

        if not static_columns and not response_columns:
            return self._gm.bad_request(
                get_error_message("STATIC_OR_RESPONSE_COLUMN_MISSING")
            )

        first_row = Row.objects.filter(dataset_id=dataset_id, deleted=False).first()

        if not first_row:
            return self._gm.not_found(get_error_message("EMPTY_DATASET"))

        preview_data = {"static_fields": [], "response_fields": []}

        for column_id in static_columns:
            try:
                column = Column.objects.get(id=column_id, deleted=False)
                cell = Cell.objects.get(
                    dataset_id=dataset_id, row=first_row, column=column, deleted=False
                )

                preview_data["static_fields"].append(
                    {
                        "column_id": str(column.id),
                        "column_name": column.name,
                        "data_type": column.data_type,
                        "value": cell.value,
                    }
                )
            except (Column.DoesNotExist, Cell.DoesNotExist):
                return self._gm.not_found(get_error_message("COLUMN_OR_CELL_NOT_FOUND"))
            except ValueError as e:
                return self._gm.bad_request(
                    {"error": f"Something went wrong: {str(e)}"}
                )

        for column_id in response_columns:
            try:
                column = Column.objects.get(id=column_id, deleted=False)
                cell = Cell.objects.get(
                    dataset_id=dataset_id, row=first_row, column=column, deleted=False
                )

                preview_data["response_fields"].append(
                    {
                        "column_id": str(column.id),
                        "column_name": column.name,
                        "data_type": column.data_type,
                        "value": cell.value,
                    }
                )
            except (Column.DoesNotExist, Cell.DoesNotExist):
                return self._gm.not_found(get_error_message("COLUMN_OR_CELL_NOT_FOUND"))
            except ValueError as e:
                return self._gm.bad_request(
                    {"error": f"Something went wrong: {str(e)}"}
                )

        return self._gm.success_response(
            {
                "row_id": str(first_row.id),
                "row_number": first_row.order,
                "preview_data": preview_data,
            }
        )

    def process_new_annotaion(self, annotation, labels, label_requirements):
        for label in labels:
            if label.type == AnnotationTypeChoices.CATEGORICAL.value:
                input_columns = label.settings.get("inputs", [])
                if input_columns:
                    for col_id in input_columns:
                        column = Column.objects.get(id=col_id, deleted=False)
                        if column.dataset != annotation.dataset:
                            raise ValidationError(
                                f"Input column '{column.name}' does not belong to the same dataset as the annotation"
                            )

            auto_annotate = label.settings.get("auto_annotate", False)
            responses = annotation.responses
            for response in range(responses):
                column_name = f"{annotation.name}:{label.name}:{response}"
                column = Column.objects.create(
                    id=uuid.uuid4(),
                    name=column_name,
                    data_type=self.get_data_type_for_label(label.type),
                    source=SourceChoices.ANNOTATION_LABEL.value,
                    dataset=annotation.dataset,
                    source_id=f"{annotation.id}-sourceid-{label.id}",
                )
                annotation.columns.add(column)
                annotation.dataset.column_order.append(str(column.id))
                annotation.dataset.column_config[str(column.id)] = {
                    "is_frozen": False,
                    "is_visible": True,
                }
                annotation.dataset.save()

            if auto_annotate:
                # --write code here--
                org = (
                    getattr(self.request, "organization", None)
                    or self.request.user.organization.id
                )
                generate_annotations_task.apply_async(
                    args=(label.id, annotation.id, org)
                )

        if not isinstance(annotation.summary, dict):
            annotation.summary = {}
        annotation.summary["label_requirements"] = label_requirements
        annotation.lowest_unfinished_row = 0  # when a new label added, we will always have an un-finished in the first row
        annotation.save()


class UserPagination(ExtendedPageNumberPagination):
    page_size = 30


class UserViewSet(viewsets.ModelViewSet):
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = UserPagination
    _gm = GeneralMethods()

    def get_queryset(self):
        try:
            from accounts.models.organization_membership import OrganizationMembership
            from accounts.models.workspace import WorkspaceMembership

            organization_id = self.kwargs["organization_id"]
            is_active_param = self.request.query_params.get("is_active")

            # Prefer workspace-level filtering when workspace context is available
            workspace = getattr(self.request, "workspace", None)
            if workspace:
                explicit_workspace_user_ids = WorkspaceMembership.objects.filter(
                    workspace=workspace, is_active=True
                ).values_list("user_id", flat=True)
                auto_access_user_ids = OrganizationMembership.no_workspace_objects.filter(
                    organization_id=organization_id,
                    is_active=True,
                ).filter(
                    Q(level__gte=Level.ADMIN)
                    | Q(level__isnull=True, role__in=["Admin", "Owner"])
                ).values_list("user_id", flat=True)
                user_ids = list(explicit_workspace_user_ids) + list(
                    auto_access_user_ids
                )
            else:
                # Fallback to org membership
                user_ids = OrganizationMembership.no_workspace_objects.filter(
                    organization_id=organization_id, is_active=True
                ).values_list("user_id", flat=True)

            if is_active_param is not None:
                is_active = str(is_active_param).lower() in ["true", "1", "t", "yes"]
                queryset = User.objects.filter(id__in=user_ids, is_active=is_active)
            else:
                queryset = User.objects.filter(id__in=user_ids)

            if not queryset.exists():
                self._gm.not_found(get_error_message("USER_NOT_FOUND_IN_ORG"))
                raise NotFound(detail="No users found for the specified organization.")

            # Search by name or email (applied after existence check so empty
            # search results don't trigger 404 for the entire org)
            search = self.request.query_params.get("search", "").strip()
            if search:
                queryset = queryset.filter(
                    Q(name__icontains=search) | Q(email__icontains=search)
                )

            return queryset
        except KeyError:
            self._gm.bad_request(get_error_message("ORGANIZATION_ID_MISSING"))
            raise NotFound(detail="Organization ID is required.")  # noqa: B904
        except Exception as e:
            logger.exception(f"Error in fetching the user of an organization: {str(e)}")
            self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_LOAD_USER_OF_ORG")
            )
            raise Exception(  # noqa: B904
                f"An error occurred while processing your request: {str(e)}"
            )

    def list(self, request, *args, **kwargs):
        try:
            return super().list(request, *args, **kwargs)
        except Exception as e:
            logger.exception(f"Error in fetching the user of an organization: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_LOAD_USER_OF_ORG")
            )


class AnnotationSummaryView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def fleiss_kappa(self, table, method="fleiss"):
        """Fleiss' and Randolph's kappa multi-rater agreement measure

        Parameters
        ----------
        table : array_like, 2-D
            assumes subjects in rows, and categories in columns. Convert raw data
            into this format by using
            :func:`statsmodels.stats.inter_rater.aggregate_raters`
        method : str
            Method 'fleiss' returns Fleiss' kappa which uses the sample margin
            to define the chance outcome.
            Method 'randolph' or 'uniform' (only first 4 letters are needed)
            returns Randolph's (2005) multirater kappa which assumes a uniform
            distribution of the categories to define the chance outcome.

        Returns
        -------
        kappa : float
            Fleiss's or Randolph's kappa statistic for inter rater agreement

        Notes
        -----
        no variance or hypothesis tests yet

        Interrater agreement measures like Fleiss's kappa measure agreement relative
        to chance agreement. Different authors have proposed ways of defining
        these chance agreements. Fleiss' is based on the marginal sample distribution
        of categories, while Randolph uses a uniform distribution of categories as
        benchmark. Warrens (2010) showed that Randolph's kappa is always larger or
        equal to Fleiss' kappa. Under some commonly observed condition, Fleiss' and
        Randolph's kappa provide lower and upper bounds for two similar kappa_like
        measures by Light (1971) and Hubert (1977).

        References
        ----------
        Wikipedia https://en.wikipedia.org/wiki/Fleiss%27_kappa

        Fleiss, Joseph L. 1971. "Measuring Nominal Scale Agreement among Many
        Raters." Psychological Bulletin 76 (5): 378-82.
        https://doi.org/10.1037/h0031619.

        Randolph, Justus J. 2005 "Free-Marginal Multirater Kappa (multirater
        K [free]): An Alternative to Fleiss' Fixed-Marginal Multirater Kappa."
        Presented at the Joensuu Learning and Instruction Symposium, vol. 2005
        https://eric.ed.gov/?id=ED490661

        Warrens, Matthijs J. 2010. "Inequalities between Multi-Rater Kappas."
        Advances in Data Analysis and Classification 4 (4): 271-86.
        https://doi.org/10.1007/s11634-010-0073-4.
        """

        table = 1.0 * np.asarray(table)  # avoid integer division
        n_sub, n_cat = table.shape
        n_total = table.sum()
        n_rater = table.sum(1)
        n_rat = n_rater.max()
        # assume fully ranked
        assert n_total == n_sub * n_rat

        # marginal frequency  of categories
        p_cat = table.sum(0) / n_total

        table2 = table * table
        p_rat = (table2.sum(1) - n_rat) / (n_rat * (n_rat - 1.0))
        p_mean = p_rat.mean()

        p_mean_exp = (p_cat * p_cat).sum()

        kappa = (p_mean - p_mean_exp) / (1 - p_mean_exp)
        return kappa

    def calculate_similarity(self, set1, set2):
        if not hasattr(self, "_str2emb"):
            self._str2emb = {}

        def _embed(texts):
            embedding_manager = EmbeddingManager()
            model = embedding_manager.get_syn_embedding()
            new_txts = [t for t in texts if t not in self._str2emb]
            if new_txts:  # batch-encode only the unknown ones
                try:
                    batch_size = 10
                    for i in range(0, len(new_txts), batch_size):
                        batch = new_txts[i : i + batch_size]
                        vecs = model(batch)  # encode this batch
                        for t, v in zip(batch, vecs, strict=False):
                            self._str2emb[t] = np.array(v)
                except Exception as e:
                    traceback.print_exc()
                    raise e

            # Always return the embeddings for all texts in order
            return np.vstack([self._str2emb[t] for t in texts])

        E1 = _embed(set1)
        E2 = _embed(set2)

        def _avg_upper(sim_mat: np.ndarray) -> float:
            n = sim_mat.shape[0]
            if n < 2:
                return 0.0
            # take upper triangle without diag
            return sim_mat[np.triu_indices(n, k=1)].mean()

        # --- 3. Fast inter-set average similarity -----------------------
        inter_set_avg_similarity = (E1 @ E2.T).mean()  # single matrix multiply

        # --- 4. Return exactly the same schema you used before ----------
        return inter_set_avg_similarity

    def avg_semantic_similarity(self, df):
        """
        Computes the average pairwise semantic similarity for each row_id group in df,
        then averages across groups.
        """
        row_sims = []

        for _row_id, group in df.groupby("row_id"):
            texts = group["value"].tolist()
            if len(texts) < 2:
                continue  # need at least 2 texts for similarity

            # ✅ Use SimilarityCalculator
            sim_result = self.calculate_similarity(texts, texts)

            row_sims.append(sim_result)

        return round(np.mean(row_sims), 2) if row_sims else None

    def build_counts_matrix(self, df, categories):
        """
        Convert annotations into counts matrix for Fleiss' Kappa.

        df has columns: row_id, user_id, value (list of labels)
        categories is list of all possible categories
        """
        cat_index = {c: i for i, c in enumerate(categories)}

        # keep only rows where at least 2 annotators contributed
        row_counts = df.groupby("row_id")["user_id"].nunique()
        valid_rows = row_counts[row_counts >= 2].index
        df_filtered = df[df["row_id"].isin(valid_rows)]
        if df_filtered.empty:
            return None

        n_items = len(valid_rows)
        k = len(categories)
        counts = np.zeros((n_items, k), dtype=int)

        for row_idx, (_row_id, group) in enumerate(df_filtered.groupby("row_id")):
            for labels in group["value"]:
                for label in labels:
                    if label in cat_index:
                        counts[row_idx, cat_index[label]] += 1
        return counts

    def build_corpus(self, dataset_id, label, texts):
        sentences = texts["value"].tolist()
        vocab, top_20, min_len, max_len, avg_len = (
            corpus_builder.build_annotation_corpus(sentences)
        )
        existing_metadata = label.metadata
        existing_metadata = label.metadata or {}
        existing_metadata[str(dataset_id)] = {
            "vocab": vocab,
            "top_20": top_20,
            "min_len": min_len,
            "max_len": max_len,
            "avg_len": avg_len,
        }

        label.metadata = existing_metadata
        label.save(update_fields=["metadata"])

        return vocab, top_20, min_len, max_len, avg_len

    def nan_to_none(self, obj):
        if isinstance(obj, float) and np.isnan(obj):
            return None
        if isinstance(obj, dict):
            return {k: self.nan_to_none(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.nan_to_none(x) for x in obj]
        return obj

    def get(self, request, dataset_id):
        try:
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            try:
                try:
                    from ee.usage.services.entitlements import Entitlements
                except ImportError:
                    Entitlements = None

                feat_check = Entitlements.check_feature(
                    str(organization.id), "has_agreement_metrics"
                )
                if not feat_check.allowed:
                    return self._gm.forbidden_response(feat_check.reason)
            except ImportError:
                pass

            dataset = get_object_or_404(
                Dataset, id=dataset_id, organization=organization
            )

            # Score-only data path. Reads the unified Score model
            # (`source_type='dataset_row'`) instead of the legacy
            # ``model_hub_annotations`` + ``Cell.feedback_info['annotation']``
            # CTEs. Returns DataFrames in the same shape so the pandas
            # aggregation logic below is unchanged.
            from model_hub.services.annotation_summary_service import (
                get_annotation_summary_data,
            )

            summary_data = get_annotation_summary_data(
                dataset_id, organization_id=organization.id
            )
            header_df = summary_data["header_data"]
            metric_df = summary_data["metric_calc"]
            graph_df = summary_data["graph"]
            heatmap_df = summary_data["heatmap"]
            annotator_performance_df = summary_data["annotator_performance"]
            dataset_coverage_df = summary_data["dataset_annot_summary"]
            dataset_coverage = round(
                (
                    dataset_coverage_df["fully_annotated_rows"].iloc[0]
                    / dataset_coverage_df["not_deleted_rows"].iloc[0]
                )
                * 100,
                2,
            )

            total_rows = int(dataset_coverage_df["not_deleted_rows"].iloc[0])
            fully_annotated_rows = int(
                dataset_coverage_df["fully_annotated_rows"].iloc[0]
            )

            dataset_coverage_df = dataset_coverage_df.map(
                lambda x: None if pd.isna(x) else x
            )
            remaining_rows = total_rows - fully_annotated_rows
            valid_times = (
                annotator_performance_df["avg_time"]
                .replace(0, np.nan)
                .dropna()
                .astype(float)
            )
            if valid_times.empty:
                completion_eta_seconds = None
            else:
                throughput_per_annotator = 1 / valid_times
                total_throughput = throughput_per_annotator.sum()
                completion_eta_seconds = remaining_rows / total_throughput

            records = header_df.to_dict(orient="records")
            result = {"labels": []}

            agreements = []
            for r in records:
                # Parse nested JSON in catLabelCounts if it’s a string
                try:
                    if r["type"] == "numeric":
                        metrics = metric_df[metric_df["label_id"] == r["label_id"]]
                        # pivot to row_id × user_id
                        wide = metrics.pivot(
                            index="row_id", columns="user_id", values="value"
                        )
                        # ensure numeric
                        wide = wide.apply(pd.to_numeric, errors="coerce")
                        # compute user-user correlations (pairwise, ignoring NaN)
                        user_corr = wide.corr(method="pearson")
                        mask = np.triu(np.ones_like(user_corr), k=1).astype(bool)
                        correlations = user_corr.where(mask).stack().dropna()
                        average_correlation = correlations.mean()
                        if pd.notna(average_correlation):
                            agreements.append(average_correlation)
                        r["correlation"] = (
                            round(average_correlation, 2)
                            if pd.notna(average_correlation)
                            else None
                        )
                        r["range"] = f"{r['min_value']}-{r['max_value']}"
                        r.pop("min_value")
                        r.pop("max_value")
                        graph_data = graph_df[graph_df["label_id"] == r["label_id"]]
                        heatmap_data = heatmap_df[
                            heatmap_df["label_id"] == r["label_id"]
                        ]

                        r["graph_data"] = {
                            f"{row.bucket_min}-{row.bucket_max}": row.count
                            for row in graph_data.itertuples(index=False)
                        }

                        r["heatmap_data"] = {
                            user_id: {
                                f"{row.bucket_min}-{row.bucket_max}": row.count
                                for row in user_data.itertuples(index=False)
                            }
                            for user_id, user_data in heatmap_data.groupby("user_id")
                        }

                    elif r["type"] == "categorical":
                        # self.calculate_multilabel_agreement(categorical_metrics, )

                        counts = json.loads(r["cat_label_counts"])

                        categorical_metrics = metric_df[
                            metric_df["label_id"] == r["label_id"]
                        ]
                        categorical_metrics = categorical_metrics[
                            ["row_id", "user_id", "value"]
                        ]
                        categorical_metrics["value"] = categorical_metrics[
                            "value"
                        ].apply(ast.literal_eval)

                        counts_matrix = self.build_counts_matrix(
                            categorical_metrics, counts
                        )
                        if counts_matrix is None:
                            kappa = None
                        else:
                            kappa = round(self.fleiss_kappa(counts_matrix), 2)
                            if pd.notna(kappa):
                                agreements.append(kappa)
                        r["mode_value"] = counts = {
                            k: v for k, v in counts.items() if k is not None
                        }
                        r["mode_value"] = max(counts, key=counts.get)
                        total = sum(counts.values())
                        r["kappa"] = kappa
                        if total > 0:
                            r["graph_data"] = {k: v / total for k, v in counts.items()}
                            r["num_unique"] = len(counts)
                        else:
                            r["graph_data"] = {}

                        r.pop("cat_label_counts")

                    elif r["type"] == "text":
                        try:
                            label = AnnotationsLabels.objects.get(id=r["label_id"])
                        except AnnotationsLabels.DoesNotExist:
                            logger.warning("label_not_found", label_id=r["label_id"])
                            continue

                        text_metrics = metric_df[metric_df["label_id"] == r["label_id"]]
                        corpus = label.metadata.get(str(dataset_id))
                        if not corpus:
                            vocab, top_20, min_len, max_len, avg_len = (
                                self.build_corpus(dataset_id, label, text_metrics)
                            )
                        else:
                            vocab = corpus.get("vocab")
                            top_20 = corpus.get("top_20")
                            min_len = corpus.get("min_len")
                            max_len = corpus.get("max_len")
                            avg_len = corpus.get("avg_len")

                        cosine_similarity = self.avg_semantic_similarity(text_metrics)
                        if cosine_similarity is not None and pd.notna(
                            cosine_similarity
                        ):
                            agreements.append(cosine_similarity)
                        r["cosine_similarity"] = cosine_similarity
                        r["len_range"] = f"{min_len}-{max_len}"
                        r["avg_len"] = avg_len
                        r["vocab_size"] = len(vocab)
                        r["key_terms"] = top_20

                    # label_id = r.pop("label_id")
                    result["labels"].append(r)

                except json.JSONDecodeError:
                    pass
            annotator_performance_df["avg_time"] = (
                (
                    pd.to_numeric(annotator_performance_df["avg_time"], errors="coerce")
                    / 60
                )
                .round(2)
                .astype(float)
            )
            result["annotators"] = annotator_performance_df.to_dict(orient="records")
            result["header"] = {
                "dataset_coverage": (
                    dataset_coverage.item() if dataset_coverage is not None else None
                ),
                "completion_eta": (
                    round(completion_eta_seconds.item(), 2)
                    if completion_eta_seconds is not None
                    else None
                ),
                "overall_agreement": (
                    round(sum(agreements) / len(agreements), 2) if agreements else None
                ),
            }
            return self._gm.success_response(self.nan_to_none(result))
        except Exception as e:
            logger.exception(f"ERROR IN ANNOTATION SUMMARY: {e}")
            return self._gm.bad_request(
                get_error_message("UNABLE_TO_FETCH_ANNOTATION_SUMMARY")
            )
