# views.py
import hashlib
import io
import json
import traceback
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import structlog
from django.db import close_old_connections, connection
from django.db.models import Count, Max, Prefetch, Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django_filters import rest_framework as django_filters
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import generics
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView

from agentic_eval.core.utils.functions import normalize_val

logger = structlog.get_logger(__name__)
from model_hub.models.choices import (
    CellStatus,
    ModelChoices,
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import (
    ExperimentAgentConfig,
    ExperimentComparison,
    ExperimentDatasetTable,
    ExperimentPromptConfig,
    ExperimentsTable,
)
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.serializers.experiments import (
    ExperimentCreateV2Serializer,
    ExperimentDetailV2Serializer,
    ExperimentIdListSerializer,
    ExperimentListSerializer,
    ExperimentListV2Serializer,
    ExperimentRerunCellsSerializer,
    ExperimentsTableGetSerializer,
    ExperimentsTableSerializer,
    ExperimentUpdateV2Serializer,
)
from model_hub.services.dataset_snapshot import create_dataset_snapshot
from model_hub.tasks.experiment_runner import process_experiments
from model_hub.utils.eval_result_columns import infer_eval_result_column_data_type
from model_hub.utils.function_eval_params import (
    has_function_params_schema,
    normalize_eval_runtime_config,
)
from model_hub.utils.SQL_queries import SQLQueryHandler
from model_hub.utils.utils import get_diff
from model_hub.views.develop_dataset import UserEvalSerializer
from model_hub.views.experiment_runner import ExperimentRunner
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.error_codes import get_error_message
from tfc.utils.functions import calculate_column_average
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination
from tfc.utils.parse_errors import parse_serialized_errors


def get_rank_suffix(rank):
    """Return the appropriate suffix for the ranking number (1st, 2nd, 3rd, etc.)"""
    if 10 <= rank % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(rank % 10, "th")


def normalize_score(value, min_val, max_val):
    """Normalize value to a 0-10 scale."""
    if min_val == max_val:
        return 5.0
    return normalize_val((min_val, max_val), (0, 10), value)


# Keys the credit-limit helper (`_mark_cells_usage_limit_error`) writes into
# `Cell.value_infos` that the frontend needs to render the upgrade CTA. The
# experiment serializer otherwise strips value_infos down to just `reason`,
# which loses the actionable metadata.
_USAGE_LIMIT_PASSTHROUGH_KEYS = (
    "error_code",
    "upgrade_cta",
    "dimension",
    "current_usage",
    "limit",
)


def _passthrough_usage_limit_fields(cell_data, value_infos):
    """Copy credit-limit fields from raw `value_infos` into the serialized cell.

    The experiment endpoint deliberately strips `value_infos` to keep payloads
    small, but credit-limit cells need a few extra fields so the frontend can
    render "Upgrade plan" CTAs. Mutates `cell_data["value_infos"]` in place.
    """
    if not isinstance(value_infos, dict):
        return
    target = cell_data.setdefault("value_infos", {})
    for key in _USAGE_LIMIT_PASSTHROUGH_KEYS:
        if value_infos.get(key) is not None and key not in target:
            target[key] = value_infos[key]


def extract_cell_usage(cell):
    """Extract usage metrics from a cell's value_infos.

    Returns a dict with prompt_tokens, completion_tokens, total_tokens,
    response_time or None if parsing fails.
    """
    try:
        value_infos = json.loads(cell.value_infos) if cell.value_infos else {}
        metadata = (
            value_infos.get("metadata", {})
            if isinstance(value_infos.get("metadata"), dict)
            else json.loads(value_infos.get("metadata", "{}"))
        )
        usage = metadata.get("usage", {})
        if not usage:
            return None
        return {
            "prompt_tokens": usage.get("prompt_tokens") or 0,
            "completion_tokens": usage.get("completion_tokens") or 0,
            "total_tokens": usage.get("total_tokens") or 0,
            "response_time": metadata.get("response_time"),
        }
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None


def parse_model_spec(model_spec):
    """Parse a model spec (string or dict) into (name, display_name, config)."""
    if isinstance(model_spec, dict):
        return (
            model_spec.get("name", ""),
            model_spec.get("display_name"),
            model_spec.get("config") or {},
        )
    return str(model_spec), None, {}


def rank_and_persist_comparisons(experiment_id, dataset_metrics, weights):
    """Normalize scores, persist comparisons, sort and assign ranks.

    Mutates dataset_metrics in place (adds normalized_scores, overall_rating,
    rank, rank_suffix, total_datasets). Persists to ExperimentComparison.
    """
    # Calculate metrics range
    metrics_range = {
        "completion_tokens": {
            "min": min((d["avg_completion_tokens"] or 0) for d in dataset_metrics),
            "max": max((d["avg_completion_tokens"] or 0) for d in dataset_metrics),
        },
        "total_tokens": {
            "min": min((d["avg_total_tokens"] or 0) for d in dataset_metrics),
            "max": max((d["avg_total_tokens"] or 0) for d in dataset_metrics),
        },
        "response_time": {
            "min": min((d["avg_response_time"] or 0) for d in dataset_metrics),
            "max": max((d["avg_response_time"] or 0) for d in dataset_metrics),
        },
        "score": {
            "min": min(
                (d["avg_score"] for d in dataset_metrics if d["avg_score"] is not None),
                default=0,
            ),
            "max": max(
                (d["avg_score"] for d in dataset_metrics if d["avg_score"] is not None),
                default=10,
            ),
        },
    }

    for metrics in dataset_metrics:
        ct = metrics["avg_completion_tokens"] or 0
        tt = metrics["avg_total_tokens"] or 0
        rt = metrics["avg_response_time"] or 0

        normalized_scores = {
            "completion_tokens": normalize_score(
                ct * weights["completion_tokens"],
                metrics_range["completion_tokens"]["min"]
                * weights["completion_tokens"],
                metrics_range["completion_tokens"]["max"]
                * weights["completion_tokens"],
            ),
            "total_tokens": normalize_score(
                tt * weights["total_tokens"],
                metrics_range["total_tokens"]["min"] * weights["total_tokens"],
                metrics_range["total_tokens"]["max"] * weights["total_tokens"],
            ),
            "response_time": normalize_score(
                rt * weights["response_time"],
                metrics_range["response_time"]["min"] * weights["response_time"],
                metrics_range["response_time"]["max"] * weights["response_time"],
            ),
        }

        if metrics["avg_score"] is not None:
            normalized_scores["score"] = normalize_score(
                metrics["avg_score"],
                metrics_range["score"]["min"],
                metrics_range["score"]["max"],
            )

        metrics["normalized_scores"] = normalized_scores
        metrics["overall_rating"] = sum(normalized_scores.values()) / len(
            normalized_scores
        )

        update_id = (
            metrics["dataset_id"]
            if ExperimentDatasetTable.objects.filter(id=metrics["dataset_id"]).exists()
            else None
        )
        ExperimentComparison.objects.update_or_create(
            experiment_id=experiment_id,
            experiment_dataset_id=update_id,
            response_time_weight=weights["response_time"],
            scores_weight=dict(weights.items()),
            total_tokens_weight=weights["total_tokens"],
            completion_tokens_weight=weights["completion_tokens"],
            defaults={
                "avg_completion_tokens": metrics["avg_completion_tokens"],
                "avg_total_tokens": metrics["avg_total_tokens"],
                "avg_response_time": metrics["avg_response_time"],
                "avg_score": metrics["avg_score"],
                "normalized_completion_tokens": metrics["normalized_scores"][
                    "completion_tokens"
                ],
                "normalized_total_tokens": metrics["normalized_scores"]["total_tokens"],
                "normalized_response_time": metrics["normalized_scores"][
                    "response_time"
                ],
                "normalized_score": metrics["normalized_scores"].get("score"),
                "overall_rating": metrics["overall_rating"],
            },
        )

    # Sort and rank
    dataset_metrics.sort(key=lambda x: x["overall_rating"], reverse=True)
    for rank, metrics in enumerate(dataset_metrics, 1):
        metrics["rank"] = rank
        metrics["rank_suffix"] = get_rank_suffix(rank)
        metrics["total_datasets"] = len(dataset_metrics)

        update_id = (
            metrics["dataset_id"]
            if ExperimentDatasetTable.objects.filter(id=metrics["dataset_id"]).exists()
            else None
        )
        ExperimentComparison.objects.filter(
            experiment_id=experiment_id,
            experiment_dataset_id=update_id,
            deleted=False,
        ).update(rank=rank)


class DateTimeFilter(django_filters.Filter):
    def filter(self, qs, value):
        if not value:
            return qs

        try:
            params = self.parent.request.query_params
            filter_op = params.get("filter_op", "equals")

            if filter_op == "between" or filter_op == "not_between":
                # Expect value to be a comma-separated string of two dates
                date_strings = value.split(",")
                if len(date_strings) != 2:
                    raise ValueError("Between operation requires two dates")
                dates = [
                    datetime.strptime(ds.strip(), "%Y-%m-%d %H:%M:%S")
                    for ds in date_strings
                ]

                if filter_op == "between":
                    return qs.filter(created_at__range=dates)
                else:  # not_between
                    return qs.exclude(created_at__range=dates)

            # Convert single date string to datetime
            filter_value = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")

            filter_map = {
                "equals": "created_at",
                "not_equals": "created_at",
                "greater_than": "created_at__gt",
                "less_than": "created_at__lt",
                "greater_than_or_equal": "created_at__gte",
                "less_than_or_equal": "created_at__lte",
            }

            if filter_op not in filter_map:
                raise ValueError(f"Invalid filter operation: {filter_op}")

            filter_lookup = filter_map[filter_op]

            if filter_op == "not_equals":
                return qs.exclude(created_at=filter_value)

            return qs.filter(**{filter_lookup: filter_value})

        except ValueError as e:
            raise ValueError(  # noqa: B904
                f"Invalid datetime format. Must be in the format: YYYY-MM-DD HH:MM:SS. Error: {str(e)}"
            )


class ExperimentFilter(django_filters.FilterSet):
    created_at = DateTimeFilter()
    status = django_filters.ChoiceFilter(choices=StatusType.get_choices())
    dataset_id = django_filters.UUIDFilter(field_name="dataset_id")

    class Meta:
        model = ExperimentsTable
        fields = ["created_at", "status", "dataset_id"]


class ExperimentsTableView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request):
        experiment_id = request.query_params.get("experiment_id")
        organization = (
            getattr(request, "organization", None) or request.user.organization
        )
        try:
            experiment = ExperimentsTable.objects.select_related("dataset").get(
                id=experiment_id
            )
            # Enforce organization isolation
            if experiment.dataset.organization_id != organization.id:
                return self._gm.not_found("Experiment not found")
            serializer = ExperimentsTableSerializer(experiment).data
            return self._gm.success_response(serializer)
        except ExperimentsTable.DoesNotExist:
            return self._gm.not_found("Experiment not found")
        except Exception:
            return self._gm.bad_request("Invalid experiment ID")

    def post(self, request):
        try:
            serializer = ExperimentsTableSerializer(data=request.data)
            if serializer.is_valid():
                validated_data = serializer.validated_data

                if experiment_name_exists(
                    validated_data["name"], validated_data["dataset"]
                ):
                    return self._gm.bad_request(
                        get_error_message("EXPERIMENT_NAME_EXISTS")
                    )

                experiment = ExperimentsTable.objects.create(
                    name=validated_data["name"],
                    dataset=validated_data["dataset"],
                    column=validated_data["column"],
                    prompt_config=validated_data["prompt_config"],
                    user=request.user,
                )
                if "user_eval_template_ids" in validated_data:
                    experiment.user_eval_template_ids.set(
                        validated_data["user_eval_template_ids"]
                    )

                # Start Temporal workflow immediately (don't wait for periodic task)
                try:
                    from tfc.temporal.experiments import start_experiment_workflow

                    workflow_id = start_experiment_workflow(
                        experiment_id=str(experiment.id),
                        max_concurrent_rows=10,
                    )
                    logger.info(
                        f"Started Temporal workflow {workflow_id} for new experiment {experiment.id}"
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to start Temporal workflow for experiment {experiment.id}: {e}. "
                        "Will be picked up by periodic task."
                    )

                return self._gm.success_response("Experiment created successfully.")
            return self._gm.bad_request(parse_serialized_errors(serializer))
        except Exception as e:
            logger.exception(f"Error in creating experiment: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_CREATE_EXP"))

    def put(self, request):
        try:
            data = request.data
            pk = data.get("experiment_id")
            re_run = data.get("re_run")
            experiment = get_object_or_404(ExperimentsTable, pk=pk)
            serializer = ExperimentsTableSerializer(
                experiment, data=request.data, partial=True
            )
            if serializer.is_valid():
                validated_data = serializer.validated_data
                if experiment_name_exists(
                    validated_data["name"], validated_data["dataset"], exclude_id=pk
                ):
                    return self._gm.bad_request(
                        get_error_message("EXPERIMENT_NAME_EXISTS")
                    )

                ExperimentsTable.objects.filter(id=pk).update(
                    name=validated_data.get("name", experiment.name),
                    dataset=validated_data.get("dataset", experiment.dataset),
                    column=validated_data.get("column", experiment.column),
                    prompt_config=validated_data.get(
                        "prompt_config", experiment.prompt_config
                    ),
                )
                if "user_eval_template_ids" in validated_data:
                    experiment.user_eval_template_ids.set(
                        validated_data["user_eval_template_ids"]
                    )

                if re_run:
                    try:
                        from tfc.temporal.experiments import start_experiment_workflow

                        experiment_dataset_table = experiment.experiments_datasets

                        if experiment_dataset_table.exists():
                            experiment_dataset_table.update(deleted=True)

                        workflow_id = start_experiment_workflow(
                            experiment_id=str(experiment.id),
                            max_concurrent_rows=10,
                        )
                        logger.info(
                            f"Started Temporal workflow {workflow_id} for new experiment {experiment.id}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to start Temporal workflow for experiment {experiment.id}: {e}. "
                            "Will be picked up by periodic task."
                        )

                return self._gm.success_response("Experiment updated successfully.")
            return self._gm.bad_request(parse_serialized_errors(serializer))
        except Exception as e:
            logger.exception(f"Error in updating experiment: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_UPDATE_EXP"))


class ExperimentsTableListView(generics.ListAPIView):
    queryset = ExperimentsTable.objects.filter(deleted=False).all()
    serializer_class = ExperimentsTableGetSerializer
    pagination_class = ExtendedPageNumberPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["status"]
    search_fields = ["name", "dataset__name"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return self._gm.success_response(serializer.data)


class ExperimentsTableDetailView(BaseModelViewSetMixin, generics.ListAPIView):
    queryset = ExperimentsTable.objects.all()
    serializer_class = ExperimentsTableGetSerializer
    filter_backends = [SearchFilter]
    search_fields = ["name"]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        dataset_id = self.request.query_params.get("dataset_id")

        # Get base queryset with automatic filtering from mixin
        experiments = super().get_queryset()

        # Apply dataset organization filtering
        experiments = experiments.filter(
            dataset__organization=getattr(self.request, "organization", None)
            or self.request.user.organization
        )

        if dataset_id:
            experiments = experiments.filter(dataset_id=dataset_id)
        return experiments


class DatasetExperimentsView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def _get_base_cell_data(
        self, cell, base_column, group_id, experiment, prefetched_base_cells=None
    ):
        """Extract base cell data for comparison if needed."""
        if not experiment.column:
            return None, {}
        # Skip diff for the base column itself
        if str(cell.column_id) == str(experiment.column_id):
            return None, {}
        # Skip diff for evaluation columns
        if cell.column.source in (
            SourceChoices.EXPERIMENT_EVALUATION.value,
            SourceChoices.EVALUATION.value,
            SourceChoices.EVALUATION_REASON.value,
            SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
        ):
            return None, {}

        if prefetched_base_cells:
            base_cell = prefetched_base_cells.get(cell.row_id)
        else:
            base_cell = Cell.objects.filter(
                row=cell.row,
                column=base_column,
                deleted=False,
                status=CellStatus.PASS.value,
            ).first()

        base_value_infos = (
            self._parse_value_infos(base_cell.value_infos)
            if base_cell
            and base_cell.value_infos
            and base_cell.status == CellStatus.PASS.value
            else {}
        )
        base_value = (
            base_cell.value
            if base_cell and base_cell.status == CellStatus.PASS.value
            else None
        )
        return base_value, base_value_infos

    def _extract_metadata(self, value_infos):
        """Extract and normalize metadata from value_infos."""
        metadata = {}
        if isinstance(value_infos.get("metadata", "{}"), str):
            metadata = json.loads(value_infos.get("metadata", "{}"))
        elif isinstance(value_infos.get("metadata", {}), dict):
            metadata = value_infos.get("metadata", {})
        return metadata

    def _process_exp_dataset_cell(
        self,
        cell,
        column,
        group_id,
        diff,
        experiment,
        base_column,
        row_id,
        index,
        prefetched_base_cells=None,
    ):
        try:
            # Extract and normalize metadata
            value_infos = self._parse_value_infos(cell.value_infos)

            metadata = self._extract_metadata(value_infos)
            reason_value = value_infos.get("reason")
            if reason_value is None and isinstance(value_infos.get("data"), dict):
                reason_value = value_infos.get("data", {}).get("response")
            if reason_value is not None and "reason" not in value_infos:
                value_infos["reason"] = reason_value

            # Get base cell data if needed for comparison
            base_value = None
            base_value_infos = {}
            if diff or row_id:
                base_value, base_value_infos = self._get_base_cell_data(
                    cell, base_column, group_id, experiment, prefetched_base_cells
                )
            base_reason_value = base_value_infos.get("reason")
            if base_reason_value is None and isinstance(
                base_value_infos.get("data"), dict
            ):
                base_reason_value = base_value_infos.get("data", {}).get("response")
            if base_reason_value is not None and "reason" not in base_value_infos:
                base_value_infos["reason"] = base_reason_value

            # Prepare cell data
            cell_data = {
                "cell_value": (
                    get_diff(base_value, cell.value)
                    if diff and base_value
                    else cell.value or ""
                ),
                "status": (
                    cell.status.lower()
                    if hasattr(cell.status, "lower")
                    else cell.status
                ),
                "metadata": {
                    "response_time_ms": metadata.get("response_time", 0),
                    "token_count": metadata.get("usage", {}).get("total_tokens", 0),
                    "cost": metadata.get("cost", {}),
                    "cell_metadata": metadata,
                    "reason": reason_value,
                },
            }

            # Add diff value if row_id is specified
            if row_id and not diff and index != 0:
                logger.info("Diffing cell value")
                cell_data["cell_diff_value"] = get_diff(base_value, cell.value)

            # Add valueInfos if there's a reason
            if (
                diff
                and base_reason_value
                and (value_infos.get("reason") or reason_value)
            ):
                cell_data["value_infos"] = {
                    "reason": get_diff(
                        base_reason_value,
                        value_infos.get("reason") or reason_value,
                    )
                }
            elif value_infos.get("reason") or reason_value:
                cell_data["value_infos"] = {
                    "reason": value_infos.get("reason") or reason_value
                }

            # Preserve credit-limit / upgrade metadata so the frontend can
            # render the upgrade CTA instead of a bare error message.
            _passthrough_usage_limit_fields(cell_data, value_infos)

            return cell.row_id, str(column.id), cell_data

        except Exception as e:
            logger.error(e)
            return None, None, None

    def _process_dataset_cell(self, cell):
        try:
            value_infos = self._parse_value_infos(cell.value_infos)
            metadata = self._extract_metadata(value_infos)
            reason_value = value_infos.get("reason")
            if reason_value is None and isinstance(value_infos.get("data"), dict):
                reason_value = value_infos.get("data", {}).get("response")
            if reason_value is not None and "reason" not in value_infos:
                value_infos["reason"] = reason_value

            cell_data = {
                "cell_value": cell.value or "",
                "status": (
                    cell.status.lower()
                    if hasattr(cell.status, "lower")
                    else cell.status
                ),
                "metadata": {
                    "response_time_ms": metadata.get("response_time", 0),
                    "token_count": metadata.get("usage", {}).get("total_tokens", 0),
                    "cost": metadata.get("cost", {}),
                    "cell_metadata": metadata,
                    "reason": reason_value,
                },
                "value_infos": {"reason": value_infos.get("reason") or reason_value},
            }

            _passthrough_usage_limit_fields(cell_data, value_infos)

            return cell.row_id, cell_data
        except Exception as e:
            logger.error(e)
            return None, None

    def _parse_bool(self, raw, default=False) -> bool:
        """Parse a query param value as boolean."""
        if raw is None:
            return default
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"true", "1", "yes"}

    def _parse_value_infos(self, raw):
        """Normalize value_infos from Cell (handles dict from JSONField or JSON string)."""
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    def get(self, request, experiment_id, row_id=None, *args, **kwargs):
        try:
            # Get pagination parameters
            page_size = request.GET.get("page_size", 10)
            current_page = request.GET.get("current_page_index", 0)
            column_config_only = self._parse_bool(
                request.GET.get("column_config_only"), default=False
            )
            diff = self._parse_bool(request.GET.get("get_diff"), default=False)
            search_key = request.GET.get("search", "").strip()

            try:
                page_size = int(page_size)
                current_page = int(current_page)
            except ValueError:
                return self._gm.bad_request(get_error_message("INVALID_PAGINATION"))

            start = current_page * page_size
            end = start + page_size

            # Get specific experiment with all related data
            # NOTE: removed cell_set prefetch — we bulk-fetch cells below
            experiment = ExperimentsTable.objects.prefetch_related(
                "experiments_datasets",
                "experiments_datasets__columns",
                "experiments_datasets__columns__cell_set",
                "experiments_datasets__prompt_config",
                "experiment_datasets",
                "experiment_datasets__columns",
                "experiment_datasets__columns__cell_set",
                "experiment_datasets__prompt_config",
                "experiment_datasets__prompt_config__prompt_template",
                "experiment_datasets__prompt_config__prompt_version",
                "experiment_datasets__agent_config",
                "user_eval_template_ids",
            ).get(
                id=experiment_id,
                dataset__organization=getattr(request, "organization", None)
                or request.user.organization,
                dataset__deleted=False,
                deleted=False,
            )
            # V2 experiments link EDTs via FK (experiment_datasets),
            # V1 uses the M2M (experiments_datasets).
            is_v2 = bool(experiment.snapshot_dataset_id)

            # Get IDs of deleted eval templates to exclude their columns
            deleted_eval_metric_ids = list(
                experiment.user_eval_template_ids.filter(
                    template__deleted=True
                ).values_list("id", flat=True)
            )

            # ── Determine dataset context ─────────────────────────────
            # V2 experiments use a snapshot dataset (rows/columns copied
            # with new IDs). All cells are stored against snapshot rows.
            if experiment.snapshot_dataset_id:
                query_dataset = experiment.snapshot_dataset
                target_column = experiment.column
            else:
                query_dataset = experiment.dataset
                target_column = experiment.column

            # ── Row-first pagination ──────────────────────────────────
            rows_qs = Row.objects.filter(dataset=query_dataset, deleted=False).order_by(
                "order"
            )

            # ── Apply search filter (list view only) ─────────────────
            search_results = {}
            if search_key and not row_id and not column_config_only:
                sql_results = SQLQueryHandler.search_cells_by_text(
                    search_key.lower(), query_dataset.id
                )
                matched_cell_ids = set()
                for cell_id, key_exists, indices in sql_results:
                    matched_cell_ids.add(cell_id)
                    search_results[str(cell_id)] = {
                        "key_exists": key_exists,
                        "indices": indices,
                    }
                if matched_cell_ids:
                    matched_row_ids = set(
                        Cell.objects.filter(
                            id__in=matched_cell_ids, deleted=False
                        ).values_list("row_id", flat=True)
                    )
                    rows_qs = rows_qs.filter(id__in=matched_row_ids)
                else:
                    rows_qs = rows_qs.none()

            next_row_ids = []
            if row_id:
                paginated_rows = rows_qs.filter(id=row_id)
                total_rows = 1
                total_pages = 1
                # Compute next 50 rows for single-row view
                target_row = paginated_rows.first()
                if target_row:
                    next_row_ids = list(
                        rows_qs.filter(order__gt=target_row.order)
                        .order_by("order")[:50]
                        .values_list("id", flat=True)
                    )
            else:
                total_rows = rows_qs.count()
                total_pages = (total_rows + page_size - 1) // page_size
                paginated_rows = rows_qs[start:end]

            row_ids_list = list(paginated_rows.values_list("id", flat=True))
            cells_by_row: dict[Any, Any] = {
                rid: {"row_id": str(rid)} for rid in row_ids_list
            }

            # ── Prepare column sets ───────────────────────────────────
            # Allowed column source types to display from the dataset.
            # Add more SourceChoices values here to surface additional column types.
            ALLOWED_DATASET_COLUMN_SOURCES = [
                SourceChoices.OTHERS.value,
                SourceChoices.RUN_PROMPT.value,
            ]
            user_eval_metric = list(experiment.user_eval_template_ids.all())
            all_columns = Column.objects.filter(
                source_id__in=[str(metric.id) for metric in user_eval_metric],
                deleted=False,
            ).exclude(source_id__in=deleted_eval_metric_ids)
            q_filter = Q(source__in=ALLOWED_DATASET_COLUMN_SOURCES)
            if target_column:
                q_filter |= Q(id=target_column.id)
            q_filter |= Q(source_id__in=[str(metric.id) for metric in user_eval_metric])
            q_filter |= Q(
                source_id__in=[
                    f"{str(column.id)}-sourceid-{str(metric.id)}"
                    for column in all_columns
                    for metric in user_eval_metric
                ]
            )
            dataset_other_columns = (
                Column.objects.filter(
                    q_filter,
                    deleted=False,
                    dataset=query_dataset,
                )
                .exclude(source_id__in=deleted_eval_metric_ids)
                .order_by("created_at")
            )

            def get_column_averages(column):
                avg_score = calculate_column_average(column)
                avg_score = avg_score.get("average", None)
                return {str(column.id): avg_score}

            futures = []
            col_avgs = {}
            exp_cols_by_exp_dataset: dict[Any, Any] = {}
            all_cols = []
            edt_qs = (
                experiment.experiment_datasets.filter(deleted=False).order_by(
                    "prompt_config__order"
                )
                if is_v2
                else experiment.experiments_datasets.filter(deleted=False)
            )
            for _i, exp_dataset in enumerate(edt_qs.all()):
                exp_cols = exp_dataset.columns.filter(
                    deleted=False, dataset=query_dataset
                ).all()
                exp_cols_by_exp_dataset[str(exp_dataset.id)] = exp_cols
                all_cols.extend(exp_cols)

            if column_config_only:
                with ThreadPoolExecutor(max_workers=10) as executor:
                    for column in list(dataset_other_columns) + all_cols:
                        futures.append(executor.submit(get_column_averages, column))
                for future in as_completed(futures):
                    col_avgs.update(future.result())

            # ── Bulk-load UserEvalMetric map (eliminates N+1) ─────────
            eval_id_set = set()
            for col in list(dataset_other_columns) + list(all_cols):
                if col.source in [
                    SourceChoices.EXPERIMENT_EVALUATION.value,
                    SourceChoices.EVALUATION.value,
                    SourceChoices.EVALUATION_REASON.value,
                    SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
                ]:
                    eid = (
                        col.source_id.split("-sourceid-")[1]
                        if col.source_id and "-sourceid-" in col.source_id
                        else col.source_id
                    )
                    if eid:
                        eval_id_set.add(eid)

            eval_metrics_qs = UserEvalMetric.objects.select_related("template").filter(
                id__in=list(eval_id_set)
            )
            eval_metric_map: dict[str, UserEvalMetric] = {
                str(m.id): m for m in eval_metrics_qs
            }

            # ── Build columnConfig for dataset_other_columns ──────────
            column_config = []
            for column in dataset_other_columns:
                # Skip columns associated with deleted/hidden evaluations
                if column.source in [
                    SourceChoices.EXPERIMENT_EVALUATION.value,
                    SourceChoices.EVALUATION.value,
                    SourceChoices.EVALUATION_REASON.value,
                ]:
                    eval_id_check = (
                        column.source_id.split("-sourceid-")[1]
                        if column.source_id and "-sourceid-" in column.source_id
                        else column.source_id
                    )
                    if eval_id_check and eval_id_check in [
                        str(id) for id in deleted_eval_metric_ids
                    ]:
                        continue

                col_status = column.status
                choices_map = {}
                eval_output_type = None
                eval_template_id = None
                if column.source in [
                    SourceChoices.EXPERIMENT_EVALUATION.value,
                    SourceChoices.EVALUATION.value,
                    SourceChoices.EVALUATION_REASON.value,
                ]:
                    eval_id = (
                        column.source_id.split("-sourceid-")[1]
                        if column.source_id and "-sourceid-" in column.source_id
                        else column.source_id
                    )
                    if eval_id:
                        em = eval_metric_map.get(eval_id)
                        if em and not em.deleted:
                            col_status = em.status
                            choices_map = em.template.config.get("choices_map", {})
                            eval_output_type = em.template.config.get("output", "score")
                            eval_template_id = str(em.template.id)

                column_config.append(
                    {
                        "id": str(column.id),
                        "name": column.name,
                        "origin_type": (
                            "evaluation"
                            if column.source
                            in [
                                SourceChoices.EXPERIMENT_EVALUATION.value,
                                SourceChoices.EVALUATION.value,
                                SourceChoices.EVALUATION_REASON.value,
                            ]
                            else column.source
                        ),
                        "data_type": (
                            column.data_type
                            if "-reason" not in column.name
                            else column.source
                        ),
                        "status": col_status,
                        "group": {
                            "id": (
                                str(column.source_id.split("-sourceid-")[1])
                                if "-reason" in column.name
                                and column.source_id
                                and "-sourceid-" in column.source_id
                                else (
                                    str(column.source_id)
                                    if column.source_id
                                    and target_column
                                    and str(column.id) != str(target_column.id)
                                    else str(column.id)
                                )
                            ),
                            "name": (
                                column.name
                                if "-reason" not in column.name
                                else column.name.split("-reason")[0]
                            ),
                            "data_type": (
                                column.data_type
                                if "-reason" not in column.name
                                else "text"
                            ),
                            "origin": (
                                "Evaluation"
                                if column.source
                                in [
                                    SourceChoices.EXPERIMENT_EVALUATION.value,
                                    SourceChoices.EVALUATION.value,
                                    SourceChoices.EVALUATION_REASON.value,
                                ]
                                else "Dataset"
                            ),
                        },
                        "average_score": col_avgs.get(str(column.id)),
                        "dataset_id": str(query_dataset.id),
                        "choices_map": choices_map,
                        "is_base_column": (
                            target_column is not None
                            and str(column.id) == str(target_column.id)
                        ),
                        "output_type": eval_output_type,
                        "eval_template_id": eval_template_id,
                    }
                )

            # ── Bulk-fetch dataset cells for the page ─────────────────
            if not column_config_only and row_ids_list:
                dataset_col_ids = list(
                    dataset_other_columns.values_list("id", flat=True)
                )
                dataset_cells = Cell.objects.filter(
                    row_id__in=row_ids_list,
                    column_id__in=dataset_col_ids,
                    deleted=False,
                    row__deleted=False,
                    column__deleted=False,
                )
                for cell in dataset_cells:
                    cell_row_id, cell_data = self._process_dataset_cell(cell)
                    if cell_row_id is not None and cell_row_id in cells_by_row:
                        cells_by_row[cell_row_id][str(cell.column_id)] = cell_data
                        if search_results and str(cell.id) in search_results:
                            cell_data["key_exists"] = search_results[str(cell.id)][
                                "key_exists"
                            ]
                            cell_data["indices"] = search_results[str(cell.id)][
                                "indices"
                            ]

            # ── Build columnConfig + cells for experiment datasets ─────
            # Build experiment-dataset → (group_id, group_name) map
            exp_dataset_group_map = {}
            is_llm = getattr(experiment, "experiment_type", "llm") == "llm"
            inline_counter = 0
            seen_inline_groups = {}  # grp_id -> grp_name
            for _exp_ds in edt_qs.all():
                try:
                    epc = _exp_ds.prompt_config
                    if is_llm and epc.prompt_version_id:
                        grp_id = str(epc.prompt_version_id)
                        pt_name = (
                            epc.prompt_template.name
                            if epc.prompt_template
                            else "prompt"
                        )
                        pv_name = (
                            epc.prompt_version.template_version
                            if epc.prompt_version
                            else ""
                        )
                        grp_name = f"{pt_name}_{pv_name}" if pv_name else pt_name
                    else:
                        messages = epc.get_messages() or []
                        msg_str = json.dumps(messages, sort_keys=True, default=str)
                        grp_id = hashlib.md5(msg_str.encode()).hexdigest()
                        if grp_id in seen_inline_groups:
                            grp_name = seen_inline_groups[grp_id]
                        else:
                            inline_counter += 1
                            grp_name = f"p{inline_counter}"
                            seen_inline_groups[grp_id] = grp_name
                    exp_dataset_group_map[str(_exp_ds.id)] = (grp_id, grp_name)
                except Exception:
                    exp_dataset_group_map[str(_exp_ds.id)] = (
                        str(_exp_ds.id),
                        _exp_ds.name,
                    )

            # ── Pre-compute agent topology for ordering + metadata ────
            agent_edt_ids = set()
            agent_node_order = {}  # edt_id -> {node_id_str: position}
            agent_end_nodes = {}  # edt_id -> set of end node_id_str
            for _exp_ds in edt_qs.all():
                try:
                    eac = _exp_ds.agent_config
                    edt_id_str = str(_exp_ds.id)
                    agent_edt_ids.add(edt_id_str)
                    from agent_playground.services.engine.analyzer import GraphAnalyzer

                    topology = GraphAnalyzer.analyze(eac.graph_version_id)
                    agent_node_order[edt_id_str] = {
                        str(nid): pos
                        for pos, nid in enumerate(topology.topological_order)
                    }
                    agent_end_nodes[edt_id_str] = {
                        str(nid) for nid in topology.end_node_ids
                    }
                except Exception:
                    pass  # Not an agent EDT or analysis failed

            # Use experiment.column as the base for diff comparisons
            base_column = experiment.column
            prefetched_base_cells = {}
            if (diff or row_id) and base_column and row_ids_list:
                base_cells_qs = Cell.objects.filter(
                    row_id__in=row_ids_list,
                    column=base_column,
                    deleted=False,
                    status=CellStatus.PASS.value,
                )
                for bc in base_cells_qs:
                    prefetched_base_cells[bc.row_id] = bc

            for i, exp_dataset in enumerate(edt_qs.all()):
                for column in exp_cols_by_exp_dataset[str(exp_dataset.id)]:
                    # Skip columns associated with deleted/hidden evaluations
                    if column.source in [
                        SourceChoices.EXPERIMENT_EVALUATION.value,
                        SourceChoices.EVALUATION_REASON.value,
                        SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
                    ]:
                        eval_id_from_source = None
                        if column.source_id and "-sourceid-" in column.source_id:
                            eval_id_from_source = column.source_id.split("-sourceid-")[
                                1
                            ]
                        if eval_id_from_source and eval_id_from_source in [
                            str(id) for id in deleted_eval_metric_ids
                        ]:
                            continue

                    col_status = column.status
                    choices_map = {}
                    # Determine origin type and group info
                    if column.source in [
                        SourceChoices.EXPERIMENT_EVALUATION.value,
                        SourceChoices.EVALUATION_REASON.value,
                    ]:
                        origin_type = "evaluation"
                        try:
                            eval_id = (
                                column.source_id.split("-sourceid-")[1]
                                if column.source_id
                                else None
                            )
                            em = eval_metric_map.get(eval_id) if eval_id else None
                            if em:
                                col_status = em.status
                                data_type = infer_eval_result_column_data_type(
                                    em.template
                                )
                                choices_map = em.template.config.get("choices_map", {})
                                group = {
                                    "id": str(eval_id),
                                    "name": em.name,
                                    "data_type": data_type,
                                    "origin": "Evaluation",
                                }
                            else:
                                group = {}
                        except (IndexError, AttributeError):
                            group = {}
                    elif (
                        column.source == SourceChoices.EXPERIMENT_EVALUATION_TAGS.value
                    ):
                        origin_type = "evaluation_tags"
                        try:
                            eval_id = (
                                column.source_id.split("-sourceid-")[1]
                                if column.source_id
                                else None
                            )
                            em = eval_metric_map.get(eval_id) if eval_id else None
                            if em:
                                col_status = em.status
                                group = {
                                    "id": str(eval_id),
                                    "name": em.name + "-tags",
                                    "data_type": "array",
                                    "origin": origin_type,
                                }
                            else:
                                group = {}
                        except (IndexError, AttributeError):
                            group = {}
                    else:
                        origin_type = column.source
                        if target_column and str(column.id) == str(target_column.id):
                            grp_id = str(column.id)
                            grp_name = column.name
                        else:
                            grp_id, grp_name = exp_dataset_group_map.get(
                                str(exp_dataset.id),
                                (str(column.id), column.name),
                            )
                        group = {
                            "id": grp_id,
                            "name": grp_name,
                            "data_type": (
                                column.data_type
                                if "-reason" not in column.name
                                else "text"
                            ),
                            "origin": "Experiment",
                        }
                    # Add column config
                    col_config_entry = {
                        "id": str(column.id),
                        "name": column.name,
                        "origin_type": origin_type,
                        "data_type": (
                            column.data_type if "-reason" not in column.name else "text"
                        ),
                        "status": col_status,
                        "group": group,
                        "average_score": (
                            col_avgs.get(str(column.id)) if column_config_only else None
                        ),
                        "dataset_id": str(exp_dataset.id),
                        "choices_map": choices_map,
                    }
                    if origin_type in ("evaluation", "evaluation_tags"):
                        col_config_entry["source_id"] = str(exp_dataset.id)
                    elif column.source_id:
                        col_config_entry["source_id"] = str(column.source_id)

                    # Add agent metadata (only for prompt/experiment columns, not evaluations)
                    edt_id_str = str(exp_dataset.id)
                    if edt_id_str in agent_edt_ids and origin_type not in (
                        "evaluation",
                        "evaluation_tags",
                    ):
                        node_id = (column.metadata or {}).get("node_id")
                        col_config_entry["is_agent"] = True
                        col_config_entry["is_final"] = (
                            node_id in agent_end_nodes.get(edt_id_str, set())
                            if node_id
                            else False
                        )

                    column_config.append(col_config_entry)

                if column_config_only:
                    continue

                # ── Bulk-fetch experiment cells for this exp_dataset ───
                exp_col_ids = list(
                    exp_cols_by_exp_dataset[str(exp_dataset.id)].values_list(
                        "id", flat=True
                    )
                )
                if row_ids_list and exp_col_ids:
                    exp_cells = Cell.objects.filter(
                        row_id__in=row_ids_list,
                        column_id__in=exp_col_ids,
                        deleted=False,
                        row__deleted=False,
                        column__deleted=False,
                    ).select_related("row", "column")

                    # Build column lookup for group_id
                    col_group_map = {}
                    for config in column_config:
                        col_group_map[config.get("id")] = config.get("group", {}).get(
                            "id"
                        )

                    for cell in exp_cells:
                        group_id = col_group_map.get(str(cell.column_id))
                        cell_row_id, column_id, cell_data = (
                            self._process_exp_dataset_cell(
                                cell,
                                cell.column,
                                group_id,
                                diff,
                                experiment,
                                base_column,
                                row_id,
                                i,
                                prefetched_base_cells,
                            )
                        )
                        if (
                            cell_row_id is not None
                            and column_id is not None
                            and cell_row_id in cells_by_row
                        ):
                            cells_by_row[cell_row_id][column_id] = cell_data
                            if search_results and str(cell.id) in search_results:
                                cell_data["key_exists"] = search_results[str(cell.id)][
                                    "key_exists"
                                ]
                                cell_data["indices"] = search_results[str(cell.id)][
                                    "indices"
                                ]

            # ── Order columns by canonical column_order ────────────
            if is_v2 and query_dataset.column_order:
                _order_map = {
                    uid: idx for idx, uid in enumerate(query_dataset.column_order)
                }
                column_config.sort(
                    key=lambda c: _order_map.get(
                        c["id"], len(query_dataset.column_order)
                    )
                )

            if column_config_only:
                # Determine experiment-level output format (uniform across all prompt configs)
                try:
                    experiment_output_format = (experiment.prompt_config or [{}])[
                        0
                    ].get("output_format", "text")
                except Exception:
                    experiment_output_format = "text"

                return self._gm.success_response(
                    {
                        "column_config": column_config,
                        "output_format": experiment_output_format,
                        "status": experiment.status,
                    }
                )

            # Build table_data in stable row order
            table_data = [cells_by_row[rid] for rid in row_ids_list]

            # Determine experiment-level output format (uniform across all prompt configs)
            try:
                experiment_output_format = (experiment.prompt_config or [{}])[0].get(
                    "output_format", "text"
                )
            except Exception:
                experiment_output_format = "text"

            response_data = {
                "column_config": column_config,
                "table": table_data,
                "metadata": {
                    "total_rows": total_rows,
                    "dataset": str(query_dataset.id),
                    "dataset_name": query_dataset.name,
                    "column": str(target_column.id) if target_column else None,
                    "total_pages": total_pages,
                    "description": {
                        str(eval.id): eval.template.description
                        for eval in experiment.user_eval_template_ids.filter(
                            deleted=False, template__deleted=False
                        ).all()
                    },
                },
                "output_format": experiment_output_format,
            }
            if row_id:
                response_data["next_row_ids"] = next_row_ids

            response_data["status"] = experiment.status
            return self._gm.success_response(response_data)

        except ExperimentsTable.DoesNotExist:
            return self._gm.not_found(get_error_message("EXPERIMENT_NOT_FOUND"))
        except Exception as e:
            logger.exception(f"Error in fetching experiment: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_GET_EXP"))


class GetRowDiffView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            experiment_id = request.data.get("experiment_id")
            all_column_ids = request.data.get("column_ids", [])
            row_ids = request.data.get("row_ids", [])
            compare_column_ids = request.data.get("compare_column_ids", [])

            experiment = get_object_or_404(
                ExperimentsTable, id=experiment_id, deleted=False
            )
            base_column = (
                experiment.experiments_datasets.filter(deleted=False)
                .first()
                .columns.filter(id__in=compare_column_ids, deleted=False)
                .first()
            )
            if not base_column:
                return self._gm.bad_request(get_error_message("COLUMN_NOT_FOUND"))

            other_column_ids = [
                col_id for col_id in compare_column_ids if col_id != str(base_column.id)
            ]
            cells = list(
                Cell.objects.filter(
                    row_id__in=row_ids,
                    column_id__in=all_column_ids,
                    deleted=False,
                    row__deleted=False,
                    column__deleted=False,
                ).select_related("row", "column")
            )

            cell_map = {}
            for cell in cells:
                cell_map[str(cell.column_id), str(cell.row_id)] = cell
            diff_data = {}
            for row_id in row_ids:
                diff_data[row_id] = {}
                for column_id in all_column_ids:
                    current_cell = cell_map.get((column_id, row_id))
                    if not current_cell:
                        continue
                    base_cell = cell_map.get((str(base_column.id), str(row_id)))
                    diff_data[row_id][column_id] = {
                        "cell_value": current_cell.value,
                        "cell_diff_value": (
                            get_diff(base_cell.value, current_cell.value)
                            if column_id in other_column_ids
                            and (
                                base_cell.status == CellStatus.PASS.value
                                and current_cell.status == CellStatus.PASS.value
                            )
                            else None
                        ),
                        "status": current_cell.status,
                        "value_infos": (
                            json.loads(current_cell.value_infos)
                            if current_cell.value_infos
                            else None
                        ),
                    }

            return self._gm.success_response(diff_data)
        except Exception as e:
            logger.exception(f"ERROR IN GET ROW DIFF {e}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_GET_ROW_DIFF")
            )


class GetRowDiffV2View(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            experiment_id = request.data.get("experiment_id")
            all_column_ids = request.data.get("column_ids", [])
            row_ids = request.data.get("row_ids", [])
            compare_column_ids = request.data.get("compare_column_ids", [])

            experiment = get_object_or_404(
                ExperimentsTable, id=experiment_id, deleted=False
            )

            organization = getattr(request, "organization", None) or request.user.organization
            if experiment.dataset.organization_id != organization.id:
                return self._gm.bad_request("Experiment not found.")

            if not experiment.snapshot_dataset_id:
                return self._gm.bad_request(
                    "This endpoint is for V2 experiments only. "
                    "Use the v1 row-diff endpoint instead."
                )

            base_column = experiment.column
            compare_column_id_set = set(compare_column_ids)
            cells = list(
                Cell.objects.filter(
                    row_id__in=row_ids,
                    column_id__in=all_column_ids,
                    deleted=False,
                    row__deleted=False,
                    column__deleted=False,
                ).select_related("row", "column")
            )

            cell_map = {}
            for cell in cells:
                cell_map[str(cell.column_id), str(cell.row_id)] = cell
            diff_data = {}
            for row_id in row_ids:
                diff_data[row_id] = {}
                for column_id in all_column_ids:
                    current_cell = cell_map.get((column_id, row_id))
                    if not current_cell:
                        continue
                    base_cell = (
                        cell_map.get((str(base_column.id), str(row_id)))
                        if base_column
                        else None
                    )
                    diff_data[row_id][column_id] = {
                        "cell_value": current_cell.value,
                        "cell_diff_value": (
                            get_diff(base_cell.value, current_cell.value)
                            if column_id in compare_column_id_set
                            and base_cell
                            and base_cell.status == CellStatus.PASS.value
                            and current_cell.status == CellStatus.PASS.value
                            else None
                        ),
                        "status": current_cell.status,
                        "value_infos": (
                            json.loads(current_cell.value_infos)
                            if isinstance(current_cell.value_infos, str)
                            and current_cell.value_infos
                            else current_cell.value_infos
                        ),
                    }

            return self._gm.success_response(diff_data)
        except Exception as e:
            logger.exception(f"ERROR IN GET ROW DIFF V2 {e}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_GET_ROW_DIFF")
            )


class ExperimentListAPIView(BaseModelViewSetMixin, generics.ListAPIView):
    _gm = GeneralMethods()
    serializer_class = ExperimentListSerializer
    queryset = ExperimentsTable.objects.all()
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter, SearchFilter]
    filterset_class = ExperimentFilter
    ordering_fields = ["created_at"]
    pagination_class = ExtendedPageNumberPagination
    search_fields = ["name", "status"]

    def get_queryset(self):
        # Get base queryset with automatic filtering from mixin
        queryset = super().get_queryset()
        dataset_id = self.request.query_params.get("dataset_id")
        if dataset_id:
            queryset = queryset.filter(dataset_id=dataset_id)
        return queryset

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.filter_queryset(self.get_queryset())
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)
            serializer = self.get_serializer(queryset, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception(f"Error in fetching experiment: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_GET_EXPs"))


class ExperimentStatsView(APIView):
    _gm = GeneralMethods()
    renderer_classes = (JSONRenderer,)
    permission_classes = [IsAuthenticated]

    def get(self, request, experiment_id):
        organization = (
            getattr(request, "organization", None) or request.user.organization
        )
        try:
            experiment = ExperimentsTable.objects.prefetch_related(
                "experiments_datasets",
                "experiments_datasets__columns",
                "user_eval_template_ids",
                "dataset",
            ).get(id=experiment_id)

            # Enforce organization isolation
            if experiment.dataset.organization_id != organization.id:
                return self._gm.not_found("Experiment not found")

            # Initialize response structure
            column_config = []
            table_data = []

            # Get IDs of deleted eval templates to exclude their columns
            # Only hide evaluations when template is deleted (delete_column=True was used)
            deleted_eval_metric_ids = list(
                experiment.user_eval_template_ids.filter(
                    template__deleted=True
                ).values_list("id", flat=True)
            )

            columns = Column.objects.filter(dataset=experiment.dataset, deleted=False)
            base_column = (
                columns.filter(id=experiment.column.id).first()
                if experiment.column
                else None
            )
            eval_metric_ids = (
                experiment.user_eval_template_ids.filter(template__deleted=False)
                .select_related("template")
                .all()
            )
            eval_metric_map_stats = {str(m.id): m for m in eval_metric_ids}

            if base_column:
                row_data = {
                    "dataset_id": str(experiment.dataset.id),
                    "experiment_dataset_name": base_column.name,
                    "average_response_time": 0,
                    "prompt_token": 0,
                    "completion_token": 0,
                    "total_token": 0,
                }
                # Process columns and collect metrics
                total_response_time = 0
                valid_cells_count = 0

                cells = Cell.objects.filter(
                    column_id=str(base_column.id),
                    dataset_id=str(experiment.dataset.id),
                    deleted=False,
                    row__deleted=False,
                    column__deleted=False,
                )

                for cell in cells:
                    try:
                        value_infos = (
                            json.loads(cell.value_infos) if cell.value_infos else {}
                        )
                        metadata = (
                            value_infos.get("metadata", {})
                            if isinstance(value_infos.get("metadata"), dict)
                            else json.loads(value_infos.get("metadata", "{}"))
                        )
                        usage = metadata.get("usage", {})

                        if usage:
                            # Safely add token counts with default 0 for None values
                            row_data["prompt_token"] += usage.get("prompt_tokens") or 0
                            row_data["completion_token"] += (
                                usage.get("completion_tokens") or 0
                            )
                            row_data["total_token"] += usage.get("total_tokens") or 0

                            # Safely add response time
                            response_time = metadata.get("response_time")
                            if response_time is not None:
                                total_response_time += response_time
                                valid_cells_count += 1
                    except (json.JSONDecodeError, AttributeError, TypeError):
                        continue

                if valid_cells_count > 0:
                    row_data["average_response_time"] = round(
                        total_response_time / valid_cells_count, 2
                    )

                # Process run prompt columns for token usage
                dataset_cols = columns.filter(
                    source__in=[
                        SourceChoices.EVALUATION.value,
                        SourceChoices.EVALUATION_TAGS.value,
                    ],
                    source_id__in=[str(uuid.id) for uuid in eval_metric_ids],
                ).exclude(source_id__in=[str(id) for id in deleted_eval_metric_ids])
                for column in dataset_cols:
                    # Add to column config if not already added
                    last_cell = column.cell_set.filter(
                        deleted=False, row__deleted=False, column__deleted=False
                    ).last()
                    status = last_cell.status if last_cell else "NotStarted"
                    name = column.name
                    name_present = any(col["name"] == name for col in column_config)

                    if not name_present:
                        col_entry = {
                            "status": (
                                status if isinstance(status, str) else str(status)
                            ),
                            "name": name,
                        }
                        em = eval_metric_map_stats.get(str(column.source_id))
                        if em:
                            col_entry["output_type"] = em.template.config.get(
                                "output", "score"
                            )
                            col_entry["eval_template_id"] = str(em.template.id)
                        column_config.append(col_entry)

                    # Add evaluation score to row data
                    try:
                        avg_score = calculate_column_average(column.id)
                        if isinstance(avg_score, dict):
                            row_data[name] = avg_score.get("average", 0)
                        else:
                            row_data[name] = (
                                float(avg_score) if avg_score is not None else 0
                            )
                    except (TypeError, ValueError):
                        row_data[name] = 0

                table_data.append(row_data)

            # Process each experiment dataset
            for exp_dataset in experiment.experiments_datasets.all():
                columns = exp_dataset.columns.filter(deleted=False)

                # Initialize row data with default values
                row_data = {
                    "dataset_id": str(exp_dataset.id),
                    "experiment_dataset_name": exp_dataset.name,
                    "average_response_time": 0,
                    "prompt_token": 0,
                    "completion_token": 0,
                    "total_token": 0,
                }

                # Process columns and collect metrics
                total_response_time = 0
                valid_cells_count = 0

                # Process run prompt columns for token usage
                run_exp_columns = columns.filter(
                    source_id=str(exp_dataset.id), source=SourceChoices.EXPERIMENT.value
                )
                for column in run_exp_columns:
                    cells = column.cell_set.filter(
                        deleted=False, row__deleted=False, column__deleted=False
                    )

                    for cell in cells:
                        try:
                            value_infos = (
                                json.loads(cell.value_infos) if cell.value_infos else {}
                            )
                            metadata = (
                                value_infos.get("metadata", {})
                                if isinstance(value_infos.get("metadata"), dict)
                                else json.loads(value_infos.get("metadata", "{}"))
                            )
                            usage = metadata.get("usage", {})

                            if usage:
                                # Safely add token counts with default 0 for None values
                                row_data["prompt_token"] += (
                                    usage.get("prompt_tokens") or 0
                                )
                                row_data["completion_token"] += (
                                    usage.get("completion_tokens") or 0
                                )
                                row_data["total_token"] += (
                                    usage.get("total_tokens") or 0
                                )

                                # Safely add response time
                                response_time = metadata.get("response_time")
                                if response_time is not None:
                                    total_response_time += response_time
                                    valid_cells_count += 1
                        except (json.JSONDecodeError, AttributeError, TypeError):
                            continue

                # Calculate average response time
                if valid_cells_count > 0:
                    row_data["average_response_time"] = round(
                        total_response_time / valid_cells_count, 2
                    )

                # Process evaluation columns
                eval_columns = columns.filter(
                    source__in=[
                        SourceChoices.EXPERIMENT_EVALUATION.value,
                        SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
                    ],
                    source_id__startswith=str(exp_dataset.id),
                )
                for column in eval_columns:
                    # Skip columns associated with deleted/hidden evaluations
                    if column.source in [
                        SourceChoices.EXPERIMENT_EVALUATION.value,
                        SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
                    ]:
                        eval_id_from_source = None
                        if column.source_id and "-sourceid-" in column.source_id:
                            eval_id_from_source = column.source_id.split("-sourceid-")[
                                1
                            ]

                        # Skip if this column belongs to a deleted/hidden evaluation
                        if eval_id_from_source and eval_id_from_source in [
                            str(id) for id in deleted_eval_metric_ids
                        ]:
                            continue

                    # Add to column config if not already added
                    last_cell = column.cell_set.filter(
                        deleted=False, row__deleted=False, column__deleted=False
                    ).last()
                    status = last_cell.status if last_cell else "NotStarted"
                    name = column.name.split(f"-{exp_dataset.name}")[0]
                    name_present = any(
                        col["name"].split(f"-{exp_dataset.name}")[0] == name
                        for col in column_config
                    )

                    if not name_present:
                        reverse = False
                        eval_output_type = None
                        eval_tmpl_id = None
                        if column.source in [
                            SourceChoices.EXPERIMENT_EVALUATION.value,
                            SourceChoices.EVALUATION.value,
                        ]:
                            user_eval_id = (
                                column.source_id.split("-sourceid-")[1]
                                if column.source
                                == SourceChoices.EXPERIMENT_EVALUATION.value
                                else column.source_id
                            )
                            em = eval_metric_map_stats.get(str(user_eval_id))
                            if em:
                                reverse = em.template.config.get(
                                    "reverse_output", False
                                )
                                eval_output_type = em.template.config.get(
                                    "output", "score"
                                )
                                eval_tmpl_id = str(em.template.id)
                            else:
                                try:
                                    found_em = UserEvalMetric.objects.select_related(
                                        "template"
                                    ).get(id=user_eval_id)
                                    reverse = found_em.template.config.get(
                                        "reverse_output", False
                                    )
                                    eval_output_type = found_em.template.config.get(
                                        "output", "score"
                                    )
                                    eval_tmpl_id = str(found_em.template.id)
                                except UserEvalMetric.DoesNotExist:
                                    reverse = False
                        column_config.append(
                            {
                                "status": (
                                    status if isinstance(status, str) else str(status)
                                ),
                                "name": column.name.split(f"-{exp_dataset.name}")[0],
                                "reverse_output": reverse,
                                "output_type": eval_output_type,
                                "eval_template_id": eval_tmpl_id,
                            }
                        )

                    # Add evaluation score to row data
                    try:
                        avg_score = calculate_column_average(column.id)
                        if isinstance(avg_score, dict):
                            row_data[name] = avg_score.get("average", 0)
                        else:
                            row_data[name] = (
                                float(avg_score) if avg_score is not None else 0
                            )
                    except (TypeError, ValueError):
                        row_data[name] = 0

                    cells = column.cell_set.filter(
                        deleted=False, row__deleted=False, column__deleted=False
                    )

                    for cell in cells:
                        try:
                            value_infos = (
                                json.loads(cell.value_infos) if cell.value_infos else {}
                            )
                            metadata = (
                                value_infos.get("metadata", {})
                                if isinstance(value_infos.get("metadata"), dict)
                                else json.loads(value_infos.get("metadata", "{}"))
                            )
                            usage = metadata.get("usage", {})

                            if usage:
                                # Safely add token counts with default 0 for None values
                                row_data["prompt_token"] += (
                                    usage.get("prompt_tokens") or 0
                                )
                                row_data["completion_token"] += (
                                    usage.get("completion_tokens") or 0
                                )
                                row_data["total_token"] += (
                                    usage.get("total_tokens") or 0
                                )

                                # Safely add response time
                                response_time = metadata.get("response_time")
                                if response_time is not None:
                                    total_response_time += response_time
                                    valid_cells_count += 1
                        except (json.JSONDecodeError, AttributeError, TypeError):
                            continue

                    # Calculate average response time
                    if valid_cells_count > 0:
                        row_data["average_response_time"] = round(
                            total_response_time / valid_cells_count, 2
                        )

                table_data.append(row_data)

            # Sort table data by average scores and add ranks
            isWinnerChosen = False
            if table_data:
                # Calculate overall score for each row (average of all evaluation scores)
                for row in table_data:
                    rank_id = (
                        row["dataset_id"]
                        if ExperimentComparison.objects.filter(
                            experiment_id=experiment_id,
                            experiment_dataset_id=row["dataset_id"],
                        ).exists()
                        else None
                    )
                    comparison = ExperimentComparison.objects.filter(
                        experiment_id=experiment_id,
                        experiment_dataset_id=rank_id,
                        deleted=False,
                    ).first()
                    row["rank"] = comparison.rank if comparison else None
                    if not isWinnerChosen:
                        isWinnerChosen = True if row["rank"] else False

            return self._gm.success_response(
                {
                    "column_config": column_config,
                    "table_data": table_data,
                    "metadata": {"is_winner_chosen": isWinnerChosen},
                }
            )

        except ExperimentsTable.DoesNotExist:
            return self._gm.bad_request(get_error_message("EXPERIMENT_NOT_FOUND"))
        except Exception as e:
            logger.exception(f"Error in fetching experiment's details: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_GET_EXP_DATA"))


class ExperimentStatsV2View(APIView):
    """Stats view for V2 experiments that read from snapshot_dataset."""

    _gm = GeneralMethods()
    renderer_classes = (JSONRenderer,)
    permission_classes = [IsAuthenticated]

    def get(self, request, experiment_id):
        organization = (
            getattr(request, "organization", None) or request.user.organization
        )
        try:
            experiment = ExperimentsTable.objects.prefetch_related(
                "experiment_datasets",
                "experiment_datasets__columns",
                "user_eval_template_ids",
                "dataset",
                "snapshot_dataset",
            ).get(id=experiment_id)

            # Enforce organization isolation
            if experiment.dataset.organization_id != organization.id:
                return self._gm.not_found("Experiment not found")

            if not experiment.snapshot_dataset_id:
                return self._gm.bad_request(
                    "This experiment has no snapshot dataset. "
                    "Use the v1 stats endpoint."
                )

            snapshot_dataset = experiment.snapshot_dataset

            # Initialize response structure
            column_config = []
            table_data = []

            # Get IDs of deleted eval templates to exclude their columns
            deleted_eval_metric_ids = list(
                experiment.user_eval_template_ids.filter(
                    template__deleted=True
                ).values_list("id", flat=True)
            )

            # Resolve base column inside the snapshot dataset.
            # experiment.column points to the original dataset column;
            # its snapshot copy has source_id == str(original_col.id).
            snapshot_columns = Column.objects.filter(
                dataset=snapshot_dataset, deleted=False
            )
            base_column = None
            if experiment.column:
                base_column = snapshot_columns.filter(
                    source_id=str(experiment.column.id),
                ).first()

            eval_metric_ids = (
                experiment.user_eval_template_ids.filter(template__deleted=False)
                .select_related("template")
                .all()
            )
            eval_metric_map_stats = {str(m.id): m for m in eval_metric_ids}

            # ── Base column stats (if present) ───────────────────────
            if base_column:
                row_data = {
                    "dataset_id": str(snapshot_dataset.id),
                    "experiment_dataset_name": base_column.name,
                    "average_response_time": 0,
                    "prompt_token": 0,
                    "completion_token": 0,
                    "total_token": 0,
                }
                total_response_time = 0
                valid_cells_count = 0

                cells = Cell.objects.filter(
                    column_id=str(base_column.id),
                    dataset_id=str(snapshot_dataset.id),
                    deleted=False,
                    row__deleted=False,
                    column__deleted=False,
                )

                for cell in cells:
                    try:
                        value_infos = (
                            json.loads(cell.value_infos) if cell.value_infos else {}
                        )
                        metadata = (
                            value_infos.get("metadata", {})
                            if isinstance(value_infos.get("metadata"), dict)
                            else json.loads(value_infos.get("metadata", "{}"))
                        )
                        usage = metadata.get("usage", {})

                        if usage:
                            row_data["prompt_token"] += usage.get("prompt_tokens") or 0
                            row_data["completion_token"] += (
                                usage.get("completion_tokens") or 0
                            )
                            row_data["total_token"] += usage.get("total_tokens") or 0

                            response_time = metadata.get("response_time")
                            if response_time is not None:
                                total_response_time += response_time
                                valid_cells_count += 1
                    except (json.JSONDecodeError, AttributeError, TypeError):
                        continue

                if valid_cells_count > 0:
                    row_data["average_response_time"] = round(
                        total_response_time / valid_cells_count, 2
                    )

                # Eval columns on the snapshot dataset (base-level evals)
                dataset_cols = snapshot_columns.filter(
                    source__in=[
                        SourceChoices.EVALUATION.value,
                        SourceChoices.EVALUATION_TAGS.value,
                    ],
                    source_id__in=[str(uuid.id) for uuid in eval_metric_ids],
                ).exclude(source_id__in=[str(id) for id in deleted_eval_metric_ids])
                for column in dataset_cols:
                    last_cell = column.cell_set.filter(
                        deleted=False, row__deleted=False, column__deleted=False
                    ).last()
                    status = last_cell.status if last_cell else "NotStarted"
                    name = column.name
                    name_present = any(col["name"] == name for col in column_config)

                    if not name_present:
                        col_entry = {
                            "status": (
                                status if isinstance(status, str) else str(status)
                            ),
                            "name": name,
                        }
                        em = eval_metric_map_stats.get(str(column.source_id))
                        if em:
                            col_entry["output_type"] = em.template.config.get(
                                "output", "score"
                            )
                            col_entry["eval_template_id"] = str(em.template.id)
                        column_config.append(col_entry)

                    try:
                        avg_score = calculate_column_average(column.id)
                        if isinstance(avg_score, dict):
                            row_data[name] = avg_score.get("average", 0)
                        else:
                            row_data[name] = (
                                float(avg_score) if avg_score is not None else 0
                            )
                    except (TypeError, ValueError):
                        row_data[name] = 0

                table_data.append(row_data)

            # ── Process each experiment dataset (FK relation) ────────
            for exp_dataset in experiment.experiment_datasets.filter(
                deleted=False
            ).all():
                columns = exp_dataset.columns.filter(deleted=False)

                row_data = {
                    "dataset_id": str(exp_dataset.id),
                    "experiment_dataset_name": exp_dataset.name,
                    "average_response_time": 0,
                    "prompt_token": 0,
                    "completion_token": 0,
                    "total_token": 0,
                }

                total_response_time = 0
                valid_cells_count = 0

                # Run prompt columns for token usage
                run_exp_columns = columns.filter(
                    source_id=str(exp_dataset.id), source=SourceChoices.EXPERIMENT.value
                )
                for column in run_exp_columns:
                    cells = column.cell_set.filter(
                        deleted=False, row__deleted=False, column__deleted=False
                    )

                    for cell in cells:
                        try:
                            value_infos = (
                                json.loads(cell.value_infos) if cell.value_infos else {}
                            )
                            metadata = (
                                value_infos.get("metadata", {})
                                if isinstance(value_infos.get("metadata"), dict)
                                else json.loads(value_infos.get("metadata", "{}"))
                            )
                            usage = metadata.get("usage", {})

                            if usage:
                                row_data["prompt_token"] += (
                                    usage.get("prompt_tokens") or 0
                                )
                                row_data["completion_token"] += (
                                    usage.get("completion_tokens") or 0
                                )
                                row_data["total_token"] += (
                                    usage.get("total_tokens") or 0
                                )

                                response_time = metadata.get("response_time")
                                if response_time is not None:
                                    total_response_time += response_time
                                    valid_cells_count += 1
                        except (json.JSONDecodeError, AttributeError, TypeError):
                            continue

                if valid_cells_count > 0:
                    row_data["average_response_time"] = round(
                        total_response_time / valid_cells_count, 2
                    )

                # Evaluation columns (exclude reason columns)
                eval_columns = columns.filter(
                    source__in=[
                        SourceChoices.EXPERIMENT_EVALUATION.value,
                        SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
                    ],
                    source_id__startswith=str(exp_dataset.id),
                ).exclude(name__endswith="-reason")
                for column in eval_columns:
                    # Skip deleted/hidden evaluations
                    if column.source in [
                        SourceChoices.EXPERIMENT_EVALUATION.value,
                        SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
                    ]:
                        eval_id_from_source = None
                        if column.source_id and "-sourceid-" in column.source_id:
                            eval_id_from_source = column.source_id.split("-sourceid-")[
                                1
                            ]

                        if eval_id_from_source and eval_id_from_source in [
                            str(id) for id in deleted_eval_metric_ids
                        ]:
                            continue

                    last_cell = column.cell_set.filter(
                        deleted=False, row__deleted=False, column__deleted=False
                    ).last()
                    status = last_cell.status if last_cell else "NotStarted"
                    name = column.name.split(f"-{exp_dataset.name}")[0]
                    name_present = any(
                        col["name"].split(f"-{exp_dataset.name}")[0] == name
                        for col in column_config
                    )

                    if not name_present:
                        reverse = False
                        eval_output_type = None
                        eval_tmpl_id = None
                        if column.source in [
                            SourceChoices.EXPERIMENT_EVALUATION.value,
                            SourceChoices.EVALUATION.value,
                        ]:
                            user_eval_id = (
                                column.source_id.split("-sourceid-")[1]
                                if column.source
                                == SourceChoices.EXPERIMENT_EVALUATION.value
                                else column.source_id
                            )
                            em = eval_metric_map_stats.get(str(user_eval_id))
                            if em:
                                reverse = em.template.config.get(
                                    "reverse_output", False
                                )
                                eval_output_type = em.template.config.get(
                                    "output", "score"
                                )
                                eval_tmpl_id = str(em.template.id)
                            else:
                                try:
                                    found_em = UserEvalMetric.objects.select_related(
                                        "template"
                                    ).get(id=user_eval_id)
                                    reverse = found_em.template.config.get(
                                        "reverse_output", False
                                    )
                                    eval_output_type = found_em.template.config.get(
                                        "output", "score"
                                    )
                                    eval_tmpl_id = str(found_em.template.id)
                                except UserEvalMetric.DoesNotExist:
                                    reverse = False
                        column_config.append(
                            {
                                "status": (
                                    status if isinstance(status, str) else str(status)
                                ),
                                "name": column.name.split(f"-{exp_dataset.name}")[0],
                                "reverse_output": reverse,
                                "output_type": eval_output_type,
                                "eval_template_id": eval_tmpl_id,
                            }
                        )

                    try:
                        avg_score = calculate_column_average(column.id)
                        if isinstance(avg_score, dict):
                            row_data[name] = avg_score.get("average", 0)
                        else:
                            row_data[name] = (
                                float(avg_score) if avg_score is not None else 0
                            )
                    except (TypeError, ValueError):
                        row_data[name] = 0

                table_data.append(row_data)

            # Ranks
            isWinnerChosen = False
            if table_data:
                for row in table_data:
                    rank_id = (
                        row["dataset_id"]
                        if ExperimentComparison.objects.filter(
                            experiment_id=experiment_id,
                            experiment_dataset_id=row["dataset_id"],
                        ).exists()
                        else None
                    )
                    comparison = ExperimentComparison.objects.filter(
                        experiment_id=experiment_id,
                        experiment_dataset_id=rank_id,
                        deleted=False,
                    ).first()
                    row["rank"] = comparison.rank if comparison else None
                    if not isWinnerChosen:
                        isWinnerChosen = True if row["rank"] else False

            return self._gm.success_response(
                {
                    "column_config": column_config,
                    "table_data": table_data,
                    "metadata": {"is_winner_chosen": isWinnerChosen},
                }
            )

        except ExperimentsTable.DoesNotExist:
            return self._gm.bad_request(get_error_message("EXPERIMENT_NOT_FOUND"))
        except Exception as e:
            logger.exception(f"Error in fetching v2 experiment stats: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_GET_EXP_DATA"))


class ExperimentEvaluationStatsView(APIView):
    _gm = GeneralMethods()

    def get(self, request, experiment_id, evaluation_id):
        try:
            # Get the experiment and its associated datasets
            experiment = get_object_or_404(ExperimentsTable, id=experiment_id)

            evaluation_model = get_object_or_404(UserEvalMetric, id=evaluation_id)

            # Get all experiment datasets and their columns
            exp_datasets = experiment.experiments_datasets.prefetch_related(
                "columns"
            ).all()
            if not exp_datasets:
                return self._gm.bad_request(
                    get_error_message("EXPERIMENT_DATASET_NOT_FOUND")
                )

            response_data = {
                "experiment_id": str(experiment_id),
                "experiment_name": experiment.name,
                "evaluation_id": str(evaluation_id),
                "evaluation_name": str(evaluation_model.name),
                "evaluation_template_id": str(evaluation_model.template.id),
                "dataset_id": str(experiment.dataset.id),
                "dataset_name": experiment.dataset.name,
                "evaluation_columns": [],
            }

            # Process evaluation columns from all experiment datasets
            eval_columns = []
            for exp_dataset in exp_datasets:
                dataset_eval_columns = exp_dataset.columns.filter(
                    source__in=[
                        SourceChoices.EXPERIMENT_EVALUATION.value,
                        SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
                    ]
                )
                eval_columns.extend(dataset_eval_columns)

            for column in eval_columns:
                cells = column.cell_set.filter(
                    deleted=False, row__deleted=False, column__deleted=False
                )

                # Calculate token statistics
                total_completion_tokens = 0
                total_prompt_tokens = 0
                total_tokens = 0
                total_response_time = 0
                valid_cells = 0

                for cell in cells:
                    try:
                        value_infos = (
                            json.loads(cell.value_infos) if cell.value_infos else {}
                        )
                        metadata = value_infos.get("metadata", {})
                        usage = metadata.get("usage", {})

                        if usage:
                            total_completion_tokens += usage.get("completion_tokens", 0)
                            total_prompt_tokens += usage.get("prompt_tokens", 0)
                            total_tokens += usage.get("total_tokens", 0)
                            total_response_time += metadata.get("response_time", 0)
                            valid_cells += 1
                    except (json.JSONDecodeError, AttributeError, TypeError):
                        continue

                eval_stats = {
                    "column_name": column.name,
                    "column_id": str(column.id),
                    "total_rows": cells.count(),
                    "success_rate": (
                        (
                            cells.exclude(status=CellStatus.ERROR.value).count()
                            / cells.count()
                            * 100
                        )
                        if cells.count() > 0
                        else 0
                    ),
                    "avg_response_time": (
                        round(total_response_time / valid_cells, 2)
                        if valid_cells > 0
                        else 0
                    ),
                    "token_usage": {
                        "avg_completion_tokens": (
                            round(total_completion_tokens / valid_cells, 2)
                            if valid_cells > 0
                            else 0
                        ),
                        "avg_prompt_tokens": (
                            round(total_prompt_tokens / valid_cells, 2)
                            if valid_cells > 0
                            else 0
                        ),
                        "avg_total_tokens": (
                            round(total_tokens / valid_cells, 2)
                            if valid_cells > 0
                            else 0
                        ),
                        "total_tokens": total_tokens,
                    },
                }

                # Add score statistics if available
                try:
                    eval_stats["avg_score"] = calculate_column_average(column.id)
                except Exception:
                    eval_stats["avg_score"] = None

                response_data["evaluation_columns"].append(eval_stats)

            return self._gm.success_response(response_data)

        except Exception as e:
            logger.exception(f"Error in fetching experiment's details: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_GET_EXP_DATA"))


class ExperimentDatasetComparisonView(APIView):
    _gm = GeneralMethods()

    def _calculate_column_metrics(self, column, exp_dataset):
        """Calculate metrics for a single column"""
        cells = column.cell_set.filter(
            deleted=False, row__deleted=False, column__deleted=False
        )
        total_completion_tokens, total_tokens, total_response_time, valid_cells = (
            0,
            0,
            0,
            0,
        )

        for cell in cells:
            try:
                value_infos = json.loads(cell.value_infos) if cell.value_infos else {}
                metadata = (
                    value_infos.get("metadata", {})
                    if isinstance(value_infos.get("metadata"), dict)
                    else json.loads(value_infos.get("metadata", "{}"))
                )
                usage = metadata.get("usage", {})
                if usage:
                    total_completion_tokens += usage.get("completion_tokens", 0)
                    total_tokens += usage.get("total_tokens", 0)
                    total_response_time += metadata.get("response_time", 0)
                    valid_cells += 1
            except (json.JSONDecodeError, AttributeError, TypeError):
                continue

        avg_score = None
        try:
            avg_score = calculate_column_average(column.id)
        except Exception:
            pass

        if valid_cells == 0:
            return {
                "column_id": str(column.id),
                "column_name": column.name,
                "avg_completion_tokens": 0,
                "avg_total_tokens": 0,
                "avg_response_time": 0,
                "avg_score": avg_score,
            }

        return {
            "column_id": str(column.id),
            "column_name": column.name,
            "avg_completion_tokens": round(total_completion_tokens / valid_cells, 2),
            "avg_total_tokens": round(total_tokens / valid_cells, 2),
            "avg_response_time": round(total_response_time / valid_cells, 2),
            "avg_score": avg_score,
        }

    def _calculate_dataset_metrics(self, exp_dataset):
        """Calculate metrics for a dataset"""

        columns = exp_dataset.columns.all()
        column_metrics = [
            metrics
            for column in columns
            if (metrics := self._calculate_column_metrics(column, exp_dataset))
        ]

        valid_scores = [
            m["avg_score"].get("average")
            * self.weights.get(m["column_name"].split(f"-{exp_dataset.name}")[0], 0)
            for m in column_metrics
            if m["avg_score"].get("average") is not None
        ]
        try:
            return {
                "dataset_id": str(exp_dataset.id),
                "avg_completion_tokens": sum(
                    m["avg_completion_tokens"] for m in column_metrics
                )
                / len(column_metrics),
                "avg_total_tokens": sum(m["avg_total_tokens"] for m in column_metrics)
                / len(column_metrics),
                "avg_response_time": sum(m["avg_response_time"] for m in column_metrics)
                / len(column_metrics),
                "avg_score": (
                    sum(valid_scores) / len(valid_scores) if valid_scores else None
                ),
                "columns": column_metrics,
            }
        except Exception:
            return {
                "dataset_id": str(exp_dataset.id),
                "avg_completion_tokens": 0,
                "avg_total_tokens": 0,
                "avg_response_time": 0,
                "avg_score": (
                    sum(valid_scores) / len(valid_scores) if valid_scores else None
                ),
                "columns": column_metrics,
            }

    def _calculate_for_base_column(self, base_dataset, base_column, eval_metric_ids):
        column_metrics = self._calculate_column_metrics(base_column, base_dataset)
        dataset_cols = Column.objects.filter(
            dataset=base_dataset,
            deleted=False,
            source__in=[
                SourceChoices.EVALUATION.value,
                SourceChoices.EVALUATION_TAGS.value,
            ],
            source_id__in=[str(uuid.id) for uuid in eval_metric_ids],
        )

        eval_column_metrics = [
            metrics
            for column in dataset_cols
            if (metrics := self._calculate_column_metrics(column, base_dataset))
        ]
        valid_scores = [
            m["avg_score"].get("average")
            * self.weights.get(m["column_name"].split(f"-{base_dataset.name}")[0], 0)
            for m in eval_column_metrics
            if m["avg_score"].get("average") is not None
        ]

        cells = Cell.objects.filter(
            column_id=str(base_column.id),
            dataset_id=str(base_dataset.id),
            deleted=False,
            row__deleted=False,
            column__deleted=False,
        ).all()
        return {
            "dataset_id": str(base_dataset.id),
            "avg_completion_tokens": (
                column_metrics["avg_completion_tokens"] / len(cells) if cells else None
            ),
            "avg_total_tokens": (
                column_metrics["avg_total_tokens"] / len(cells) if cells else None
            ),
            "avg_response_time": (
                column_metrics["avg_response_time"] / len(cells) if cells else None
            ),
            "avg_score": (
                sum(valid_scores) / len(valid_scores) if valid_scores else None
            ),
            "columns": eval_column_metrics,
        }

    def post(self, request, experiment_id):
        try:
            # Get evaluation-specific weights
            self.weights = {
                key: value
                for key, value in request.data.items()
                if key != "eval_template_ids"
            }

            experiment = ExperimentsTable.objects.prefetch_related(
                "dataset",
                "experiments_datasets",
                "experiments_datasets__columns",
                "user_eval_template_ids",
            ).get(id=experiment_id)

            dataset_metrics = [
                metrics
                for exp_dataset in experiment.experiments_datasets.all()
                if (metrics := self._calculate_dataset_metrics(exp_dataset))
            ]

            if experiment.column:
                base_result = self._calculate_for_base_column(
                    experiment.dataset,
                    experiment.column,
                    experiment.user_eval_template_ids.all(),
                )
                if base_result:
                    dataset_metrics.append(base_result)

            if not dataset_metrics:
                return self._gm.success_response(
                    {
                        "experiment_id": str(experiment_id),
                        "experiment_name": experiment.name,
                        "total_datasets": 0,
                        "dataset_comparisons": [],
                    }
                )

            rank_and_persist_comparisons(experiment_id, dataset_metrics, self.weights)

            return self._gm.success_response(
                {
                    "experiment_id": str(experiment_id),
                    "experiment_name": experiment.name,
                    "total_datasets": len(dataset_metrics),
                    "weights_applied": self.weights,  # Include weights in response
                    "dataset_comparisons": dataset_metrics,
                }
            )

        except ExperimentsTable.DoesNotExist:
            return self._gm.bad_request(get_error_message("EXPERIMENT_NOT_FOUND"))
        except Exception as e:
            logger.exception(f"Error in comparing experiment: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_COMPARE_EXPS"))


class ExperimentDatasetComparisonV2View(APIView):
    """V2 compare view: reads from experiment_datasets FK + snapshot_dataset."""

    _gm = GeneralMethods()

    def _calculate_column_metrics(self, column):
        cells = column.cell_set.filter(
            deleted=False, row__deleted=False, column__deleted=False
        )
        total_completion_tokens, total_tokens, total_response_time, valid_cells = (
            0,
            0,
            0,
            0,
        )

        for cell in cells:
            try:
                value_infos = json.loads(cell.value_infos) if cell.value_infos else {}
                metadata = (
                    value_infos.get("metadata", {})
                    if isinstance(value_infos.get("metadata"), dict)
                    else json.loads(value_infos.get("metadata", "{}"))
                )
                usage = metadata.get("usage", {})
                if usage:
                    total_completion_tokens += usage.get("completion_tokens", 0)
                    total_tokens += usage.get("total_tokens", 0)
                    total_response_time += metadata.get("response_time", 0)
                    valid_cells += 1
            except (json.JSONDecodeError, AttributeError, TypeError):
                continue

        avg_score = None
        try:
            avg_score = calculate_column_average(column.id)
        except Exception:
            pass

        return {
            "column_id": str(column.id),
            "column_name": column.name,
            "avg_completion_tokens": (
                round(total_completion_tokens / valid_cells, 2) if valid_cells else 0
            ),
            "avg_total_tokens": (
                round(total_tokens / valid_cells, 2) if valid_cells else 0
            ),
            "avg_response_time": (
                round(total_response_time / valid_cells, 2) if valid_cells else 0
            ),
            "avg_score": avg_score,
        }

    def _calculate_dataset_metrics(
        self, exp_dataset, eval_metric_ids, deleted_eval_metric_ids
    ):
        columns = exp_dataset.columns.filter(deleted=False)

        # Run-prompt / agent output columns for token usage
        run_exp_columns = columns.filter(
            source_id=str(exp_dataset.id),
            source=SourceChoices.EXPERIMENT.value,
        )
        total_completion_tokens, total_tokens, total_response_time, valid_cells = (
            0,
            0,
            0,
            0,
        )
        for column in run_exp_columns:
            metrics = self._calculate_column_metrics(column)
            total_completion_tokens += metrics["avg_completion_tokens"]
            total_tokens += metrics["avg_total_tokens"]
            total_response_time += metrics["avg_response_time"]
            if metrics["avg_completion_tokens"] or metrics["avg_response_time"]:
                valid_cells += 1

        # Eval columns (exclude reason columns)
        eval_columns = (
            columns.filter(
                source__in=[
                    SourceChoices.EXPERIMENT_EVALUATION.value,
                    SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
                ],
                source_id__startswith=str(exp_dataset.id),
            )
            .exclude(name__endswith="-reason")
            .exclude(source_id__in=[str(eid) for eid in deleted_eval_metric_ids])
        )

        eval_column_metrics = [
            self._calculate_column_metrics(col) for col in eval_columns
        ]

        valid_scores = []
        for m in eval_column_metrics:
            if m["avg_score"] and m["avg_score"].get("average") is not None:
                col_key = m["column_name"].split(f"-{exp_dataset.name}")[0]
                weight = self.weights.get(col_key, 0)
                valid_scores.append(m["avg_score"]["average"] * weight)

        avg_completion = total_completion_tokens / valid_cells if valid_cells else 0
        avg_tokens = total_tokens / valid_cells if valid_cells else 0
        avg_response = total_response_time / valid_cells if valid_cells else 0

        return {
            "dataset_id": str(exp_dataset.id),
            "avg_completion_tokens": round(avg_completion, 2),
            "avg_total_tokens": round(avg_tokens, 2),
            "avg_response_time": round(avg_response, 2),
            "avg_score": (
                sum(valid_scores) / len(valid_scores) if valid_scores else None
            ),
            "columns": eval_column_metrics,
        }

    def _calculate_base_metrics(
        self, snapshot_dataset, base_column, eval_metric_ids, deleted_eval_metric_ids
    ):
        column_metrics = self._calculate_column_metrics(base_column)

        dataset_cols = (
            Column.objects.filter(
                dataset=snapshot_dataset,
                deleted=False,
                source__in=[
                    SourceChoices.EVALUATION.value,
                    SourceChoices.EVALUATION_TAGS.value,
                ],
                source_id__in=[str(m.id) for m in eval_metric_ids],
            )
            .exclude(source_id__in=[str(eid) for eid in deleted_eval_metric_ids])
            .exclude(name__endswith="-reason")
        )

        eval_column_metrics = [
            self._calculate_column_metrics(col) for col in dataset_cols
        ]

        valid_scores = []
        for m in eval_column_metrics:
            if m["avg_score"] and m["avg_score"].get("average") is not None:
                col_key = m["column_name"].split(f"-{snapshot_dataset.name}")[0]
                weight = self.weights.get(col_key, 0)
                valid_scores.append(m["avg_score"]["average"] * weight)

        return {
            "dataset_id": str(snapshot_dataset.id),
            "avg_completion_tokens": column_metrics["avg_completion_tokens"],
            "avg_total_tokens": column_metrics["avg_total_tokens"],
            "avg_response_time": column_metrics["avg_response_time"],
            "avg_score": (
                sum(valid_scores) / len(valid_scores) if valid_scores else None
            ),
            "columns": eval_column_metrics,
        }

    def post(self, request, experiment_id):
        try:
            self.weights = {
                key: value
                for key, value in request.data.items()
                if key != "eval_template_ids"
            }

            experiment = ExperimentsTable.objects.prefetch_related(
                "experiment_datasets",
                "experiment_datasets__columns",
                "user_eval_template_ids",
                "snapshot_dataset",
            ).get(id=experiment_id)

            if not experiment.snapshot_dataset_id:
                return self._gm.bad_request(
                    "This experiment has no snapshot dataset. "
                    "Use the v1 compare endpoint."
                )

            snapshot_dataset = experiment.snapshot_dataset
            eval_metric_ids = (
                experiment.user_eval_template_ids.filter(template__deleted=False)
                .select_related("template")
                .all()
            )
            deleted_eval_metric_ids = list(
                experiment.user_eval_template_ids.filter(
                    template__deleted=True
                ).values_list("id", flat=True)
            )

            dataset_metrics = []

            # Process each EDT (prompt config / agent config)
            for exp_dataset in experiment.experiment_datasets.filter(deleted=False):
                metrics = self._calculate_dataset_metrics(
                    exp_dataset, eval_metric_ids, deleted_eval_metric_ids
                )
                if metrics:
                    dataset_metrics.append(metrics)

            # Process base column (if present)
            if experiment.column:
                snapshot_col = Column.objects.filter(
                    dataset=snapshot_dataset,
                    source_id=str(experiment.column.id),
                    deleted=False,
                ).first()
                if snapshot_col:
                    base_result = self._calculate_base_metrics(
                        snapshot_dataset,
                        snapshot_col,
                        eval_metric_ids,
                        deleted_eval_metric_ids,
                    )
                    if base_result:
                        dataset_metrics.append(base_result)

            if not dataset_metrics:
                return self._gm.success_response(
                    {
                        "experiment_id": str(experiment_id),
                        "experiment_name": experiment.name,
                        "total_datasets": 0,
                        "dataset_comparisons": [],
                    }
                )

            rank_and_persist_comparisons(experiment_id, dataset_metrics, self.weights)

            return self._gm.success_response(
                {
                    "experiment_id": str(experiment_id),
                    "experiment_name": experiment.name,
                    "total_datasets": len(dataset_metrics),
                    "weights_applied": self.weights,
                    "dataset_comparisons": dataset_metrics,
                }
            )

        except ExperimentsTable.DoesNotExist:
            return self._gm.bad_request(get_error_message("EXPERIMENT_NOT_FOUND"))
        except Exception as e:
            logger.exception(f"Error in comparing v2 experiment: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_COMPARE_EXPS"))


class RunAdditionalEvaluationsView(APIView):
    _gm = GeneralMethods()

    def post(self, request, experiment_id):
        """
        Request body format:
        {
            "eval_template_ids": ["uuid1", "uuid2", ...]
        }
        """
        try:
            experiment = get_object_or_404(
                ExperimentsTable.objects.select_related("dataset").prefetch_related(
                    "experiments_datasets", "user_eval_template_ids"
                ),
                id=experiment_id,
                deleted=False,
            )
            eval_template_ids = request.data.get("eval_template_ids", [])
            UserEvalMetric.objects.filter(id__in=eval_template_ids).update(
                source_id=experiment.id
            )
            experiment_runner = ExperimentRunner(experiment_id=experiment.id)
            experiment_runner.load_experiment()
            logger.info("SENDING FOR EMPTY")
            experiment_runner.empty_or_create_evals_column(
                eval_template_ids=eval_template_ids
            )
            logger.info("EMPTIED")
            experiment.user_eval_template_ids.all().filter(
                id__in=eval_template_ids
            ).update(status=StatusType.EXPERIMENT_EVALUATION.value)

            # Start V2 Temporal workflow to actually execute the evals
            try:
                from tfc.temporal.experiments import start_experiment_v2_workflow

                start_experiment_v2_workflow(
                    experiment_id=str(experiment.id),
                    rerun_eval_template_ids=eval_template_ids,
                )
            except Exception as wf_err:
                logger.warning(
                    "Failed to start eval workflow for run-evaluations",
                    experiment_id=str(experiment.id),
                    error=str(wf_err),
                )

            return self._gm.success_response(
                {"message": "Additional evaluations added successfully"}
            )

        except ValueError:
            return self._gm.bad_request(
                get_error_message("FAILED_TO_ADD_ADDITIONAL_EVAL")
            )
        except Exception as e:
            logger.exception(f"Error in adding additional eval: {str(e)}")
            return self._gm.bad_request(
                get_error_message("FAILED_TO_ADD_ADDITIONAL_EVAL")
            )


class AddExperimentEvalView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, experiment_id, *args, **kwargs):
        try:
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            serializer = UserEvalSerializer(data=request.data)
            save_as_template = request.data.get("save_as_template", False)
            run = request.data.get("run", False)

            if serializer.is_valid():
                validated_data = serializer.validated_data
                experiment = get_object_or_404(ExperimentsTable, id=experiment_id)

                template_id = validated_data.get("template_id")
                # Save as template if requested
                if UserEvalMetric.objects.filter(
                    name=validated_data.get("name"),
                    organization=organization,
                    dataset_id=experiment.dataset.id,
                    deleted=False,
                ).exists():
                    return self._gm.bad_request(get_error_message("EVAL_NAME_EXISTS"))

                if save_as_template:
                    template = EvalTemplate.no_workspace_objects.get(
                        id=validated_data.get("template_id")
                    )
                    if (
                        EvalTemplate.objects.filter(
                            name=validated_data.get("name"),
                            organization=organization,
                            deleted=False,
                        ).exists()
                        or EvalTemplate.no_workspace_objects.filter(
                            name=validated_data.get("name"),
                            owner=OwnerChoices.SYSTEM.value,
                            deleted=False,
                        ).exists()
                    ):
                        return self._gm.bad_request(
                            get_error_message("EVAL_NAME_EXISTS")
                        )

                    new_template = EvalTemplate(
                        name=validated_data.get("name"),
                        description=template.description,
                        config=template.config,
                        eval_tags=template.eval_tags,
                        criteria=template.criteria,
                        choices=template.choices,
                        multi_choice=template.multi_choice,
                        organization=organization,
                        owner=OwnerChoices.USER.value,
                    )
                    new_config = template.config
                    runtime_config = normalize_eval_runtime_config(
                        template.config, validated_data.get("config", {})
                    )
                    input_config = runtime_config.get("config", {})
                    input_params = runtime_config.get("params", {})
                    for key in input_config:
                        if key in new_config.get("config", {}):
                            new_config["config"][key]["default"] = input_config[key]
                    if has_function_params_schema(new_config):
                        for key, value in input_params.items():
                            if key in new_config.get("function_params_schema", {}):
                                new_config["function_params_schema"][key][
                                    "default"
                                ] = value
                    new_template.config = new_config
                    new_template.save()
                    template_id = new_template.id

                logger.info(f"CONFIG: {validated_data.get('config')}")
                selected_template = EvalTemplate.no_workspace_objects.get(
                    id=template_id
                )
                normalized_config = normalize_eval_runtime_config(
                    selected_template.config, validated_data.get("config", {})
                )
                # Create UserEvalMetric
                # V2 experiments use snapshot_dataset for eval execution
                eval_dataset = experiment.snapshot_dataset or experiment.dataset
                user_eval_metric = UserEvalMetric.objects.create(
                    name=validated_data.get("name"),
                    organization=organization,
                    dataset_id=eval_dataset.id,
                    template_id=template_id,
                    config=normalized_config,
                    status=StatusType.EXPERIMENT_EVALUATION.value,
                    source_id=experiment.id,
                    user=request.user,
                    model=validated_data.get("model", ModelChoices.TURING_LARGE.value),
                    composite_weight_overrides=validated_data.get(
                        "composite_weight_overrides"
                    ),
                )

                experiment.user_eval_template_ids.add(user_eval_metric)

                if run:
                    experiment.status = StatusType.RUNNING.value
                    experiment.save(update_fields=["status"])
                    experiment_runner = ExperimentRunner(experiment_id=experiment.id)
                    experiment_runner.load_experiment()
                    experiment_runner.empty_or_create_evals_column(
                        eval_template_ids=[str(user_eval_metric.id)]
                    )
                    experiment.user_eval_template_ids.all().filter(
                        id__in=[str(user_eval_metric.id)]
                    ).update(status=StatusType.EXPERIMENT_EVALUATION.value)

                    # Mirror dataset parity: make the newly-created per-EDT
                    # eval (+ reason) columns visible in the grid immediately
                    # instead of waiting for a rerun to rebuild column_order.
                    if experiment.snapshot_dataset_id:
                        _build_and_save_v2_column_order(
                            experiment, experiment.snapshot_dataset
                        )

                    # Start V2 Temporal workflow to actually execute the eval
                    try:
                        from tfc.temporal.experiments import (
                            start_experiment_v2_workflow,
                        )

                        start_experiment_v2_workflow(
                            experiment_id=str(experiment.id),
                            rerun_eval_template_ids=[str(user_eval_metric.id)],
                        )
                    except Exception as wf_err:
                        logger.warning(
                            "Failed to start eval workflow for add-eval",
                            experiment_id=str(experiment.id),
                            error=str(wf_err),
                        )

                return self._gm.success_response(
                    {
                        "message": "Evaluation added successfully",
                        "eval_id": str(user_eval_metric.id),
                    }
                )

            return self._gm.bad_request(parse_serialized_errors(serializer))

        except Exception as e:
            logger.exception(f"Error in adding additional eval: {str(e)}")
            return self._gm.bad_request(
                get_error_message("FAILED_TO_ADD_EVALUATION_IN_EXP")
            )


class ExperimentComparisonDetailsView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request, experiment_id):
        try:
            # Get the latest comparison per dataset for this experiment
            latest_ids = (
                ExperimentComparison.objects.filter(
                    experiment_id=experiment_id, deleted=False
                )
                .values("experiment_dataset_id")
                .annotate(latest_id=Max("updated_at"))
                .values_list("latest_id", flat=True)
            )
            comparisons = ExperimentComparison.objects.filter(
                experiment_id=experiment_id,
                deleted=False,
                updated_at__in=latest_ids,
            ).order_by("rank")

            # Format the response data
            comparison_data = []
            for comparison in comparisons:
                data = {
                    "scores_weight": comparison.scores_weight,
                    "experiment_dataset_id": str(comparison.experiment_dataset_id),
                    "rank": comparison.rank,
                    "rank_suffix": get_rank_suffix(comparison.rank),
                    "metrics": {
                        "raw": {
                            "avg_completion_tokens": comparison.avg_completion_tokens,
                            "avg_total_tokens": comparison.avg_total_tokens,
                            "avg_response_time": comparison.avg_response_time,
                            "avg_score": comparison.avg_score,
                        },
                        "normalized": {
                            "completion_tokens": comparison.normalized_completion_tokens,
                            "total_tokens": comparison.normalized_total_tokens,
                            "response_time": comparison.normalized_response_time,
                            "score": comparison.normalized_score,
                        },
                    },
                    "weights": {
                        "response_time": comparison.response_time_weight,
                        "scores": comparison.scores_weight,
                        "total_tokens": comparison.total_tokens_weight,
                        "completion_tokens": comparison.completion_tokens_weight,
                    },
                    "overall_rating": comparison.overall_rating,
                }
                comparison_data.append(data)

            response_data = {
                "experiment_id": str(experiment_id),
                "total_comparisons": len(comparison_data),
                "comparisons": comparison_data,
            }

            return self._gm.success_response(response_data)

        except Exception as e:
            logger.exception(
                f"Error in fetching experiment's comparision data: {str(e)}"
            )
            return self._gm.bad_request(
                get_error_message("FAILED_TO_GET_EXP_COMPARE_DATA")
            )


class ExperimentDeleteView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def delete(self, request):
        try:
            # Get list of experiment IDs from request data
            experiment_ids = request.data.get("experiment_ids", [])

            if not experiment_ids:
                return self._gm.bad_request(get_error_message("MISSING_EXP_IDS"))

            # Bulk update experiments to mark them as deleted
            updated_count = ExperimentsTable.objects.filter(
                id__in=experiment_ids
            ).update(deleted=True)

            return self._gm.success_response(
                {
                    "message": f"{updated_count} experiments deleted successfully",
                    "deleted_count": updated_count,
                }
            )

        except Exception as e:
            logger.exception(f"Error in deleting experiment: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_DELETE_EXP"))


class ExperimentRerunView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            serializer = ExperimentIdListSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(parse_serialized_errors(serializer))

            experiment_ids = serializer.validated_data["experiment_ids"]
            use_temporal = request.data.get("use_temporal", True)  # Default to Temporal
            max_concurrent_rows = request.data.get("max_concurrent_rows", 10)

            if use_temporal:
                # Use Temporal workflows
                from tfc.temporal.experiments import start_experiment_workflow

                # Set status to QUEUED immediately so UI reflects the change
                ExperimentsTable.objects.filter(id__in=experiment_ids).update(
                    status=StatusType.QUEUED.value
                )

                workflow_ids = []
                for experiment_id in experiment_ids:
                    try:
                        workflow_id = start_experiment_workflow(
                            experiment_id=str(experiment_id),
                            max_concurrent_rows=max_concurrent_rows,
                        )
                        workflow_ids.append(workflow_id)
                        logger.info(
                            f"Started Temporal workflow {workflow_id} for experiment {experiment_id}"
                        )
                    except Exception as e:
                        logger.exception(
                            f"Failed to start Temporal workflow for experiment {experiment_id}: {e}"
                        )
                        # Fall back to Celery for this experiment
                        ExperimentsTable.objects.filter(id=experiment_id).update(
                            status=StatusType.RUNNING.value
                        )
                        process_experiments.apply_async(args=([str(experiment_id)],))

                return self._gm.success_response(
                    "Re-Run has started for the experiments."
                )
            else:
                # Use Celery (legacy)
                ExperimentsTable.objects.filter(id__in=experiment_ids).update(
                    status=StatusType.RUNNING.value
                )
                process_experiments.apply_async(args=(experiment_ids,))
                return self._gm.success_response(
                    "Re-Run has started for the experiments."
                )

        except Exception as e:
            logger.exception(f"Error in re run the experiment: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_RE_RUN_EXP")
            )


class DownloadExperimentsView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    # parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get(self, request, experiment_id, *args, **kwargs):
        try:
            # Get dataset and verify it exists
            experiment = ExperimentsTable.objects.prefetch_related(
                "experiments_datasets",
                "experiments_datasets__columns",
                "experiments_datasets__columns__cell_set",
                "user_eval_template_ids",
            ).get(id=experiment_id)

            # Get all columns in the correct order
            # Allowed column source types to display from the dataset.
            # Add more SourceChoices values here to surface additional column types.
            ALLOWED_DATASET_COLUMN_SOURCES = [
                SourceChoices.OTHERS.value,
                SourceChoices.RUN_PROMPT.value,
            ]
            user_eval_metric = list(
                experiment.user_eval_template_ids.filter(template__deleted=False).all()
            )
            all_columns = Column.objects.filter(
                source_id__in=[str(metric.id) for metric in user_eval_metric],
                deleted=False,
            )
            q_filter = Q(source__in=ALLOWED_DATASET_COLUMN_SOURCES)
            if experiment.column:
                q_filter |= Q(id=experiment.column.id)
            q_filter |= Q(source_id__in=[str(metric.id) for metric in user_eval_metric])
            q_filter |= Q(
                source_id__in=[
                    f"{str(column.id)}-sourceid-{str(metric.id)}"
                    for column in all_columns
                    for metric in user_eval_metric
                ]
            )
            dataset_other_columns = Column.objects.filter(
                q_filter,
                deleted=False,
                dataset=experiment.dataset,
            ).order_by("created_at")

            cells_by_row: dict[Any, Any] = {}
            data = defaultdict(list)
            num_rows = 0
            remaining_cols = []

            for column in dataset_other_columns:
                for cell in column.cell_set.filter(
                    deleted=False, row__deleted=False, column__deleted=False
                ):
                    if cell.row.deleted is False and cell.column.deleted is False:
                        if cell.row_id not in cells_by_row:
                            cells_by_row[cell.row_id] = "added"
                            num_rows += 1

                data.update({column.name: ["" for _ in range(num_rows)]})

            for exp_dataset in experiment.experiments_datasets.all():
                for column in exp_dataset.columns.filter(deleted=False):
                    remaining_cols.append(column)
                    data.update({column.name: ["" for _ in range(num_rows)]})

            # columns = Column.objects.filter(id__in=list(dataset_other_columns.values_list("id", flat=True))+remaining_cols)
            columns = Column.objects.filter(
                id__in=list(dataset_other_columns.values_list("id", flat=True))
                + [col.id for col in remaining_cols],
                deleted=False,
            )
            logger.info(f"CELLS BY ROW: {cells_by_row.keys()}")
            rows = Row.objects.filter(id__in=cells_by_row.keys(), deleted=False)
            # Fetch all cells in bulk to improve performance
            cells = Cell.objects.filter(
                row__in=rows, column__in=columns, deleted=False
            ).select_related("row", "column")

            # Create a mapping of (row_id, column_id) to cell value
            cell_mapping = {
                (str(cell.row_id), str(cell.column_id)): cell.value for cell in cells
            }

            # Fill the data dictionary
            for idx, row in enumerate(rows):
                for col in columns:
                    value = cell_mapping.get((str(row.id), str(col.id)), "")
                    logger.info(f"IDX: {idx}")
                    logger.info(f"COL NAME: {value}")
                    data[col.name][idx] = value if value is not None else ""

            df = pd.DataFrame(data)

            # Convert to CSV buffer
            buffer = io.BytesIO()
            df.to_csv(buffer, index=False, encoding="utf-8")
            buffer.seek(0)

            # Create the response with the file
            filename = f"{experiment.name or 'experiment'}.csv"
            response = FileResponse(
                buffer, as_attachment=True, filename=filename, content_type="text/csv"
            )

            return response

        except Exception as e:
            logger.exception(f"Error in downloading the dataset: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_DOWNLOAD_DATASET")
            )


class ExperimentsTableV2View(APIView):
    """
    V2 experiment API.
    POST /experiments/v2/ — Create experiment with structured configs.
    GET  /experiments/v2/<experiment_id>/ — Retrieve experiment detail.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request, experiment_id):
        organization = getattr(request, "organization", None) or request.user.organization

        try:
            experiment = (
                ExperimentsTable.objects.select_related("dataset")
                .prefetch_related(
                    Prefetch(
                        "experiment_datasets",
                        queryset=ExperimentDatasetTable.objects.filter(
                            deleted=False
                        ).select_related(
                            "prompt_config__prompt_template",
                            "prompt_config__prompt_version",
                            "agent_config__graph",
                            "agent_config__graph_version",
                        ),
                    ),
                    "user_eval_template_ids",
                )
                .get(
                    id=experiment_id,
                    dataset__organization=organization,
                    deleted=False,
                )
            )
        except ExperimentsTable.DoesNotExist:
            return self._gm.not_found("Experiment not found")

        serializer = ExperimentDetailV2Serializer(
            experiment,
            context={"organization_id": str(organization.id)},
        )
        return self._gm.success_response(serializer.data)

    def post(self, request):
        serializer = ExperimentCreateV2Serializer(data=request.data)
        if not serializer.is_valid():
            return self._gm.bad_request(parse_serialized_errors(serializer))

        data = serializer.validated_data
        organization = getattr(request, "organization", None) or request.user.organization

        try:
            # Validate dataset belongs to org
            try:
                dataset = Dataset.objects.get(
                    id=data["dataset_id"],
                    organization=organization,
                    deleted=False,
                )
            except Dataset.DoesNotExist:
                return self._gm.not_found("Dataset not found")

            # Validate column belongs to dataset (optional)
            column = None
            if data.get("column_id"):
                try:
                    column = Column.objects.get(
                        id=data["column_id"],
                        dataset=dataset,
                        deleted=False,
                    )
                except Column.DoesNotExist:
                    return self._gm.not_found("Column not found in dataset")

            # Check duplicate name
            if experiment_name_exists(data["name"], dataset):
                return self._gm.bad_request(get_error_message("EXPERIMENT_NAME_EXISTS"))

            # Validate UI-selected prompt/agent versions BEFORE creating
            # experiment/snapshot to fail fast and avoid wasted compute.
            experiment_type = data.get("experiment_type", "llm")
            validated_prompt_versions = {}
            validated_agent_versions = {}
            if experiment_type == "llm":
                from agent_playground.models.graph_version import GraphVersion as GV

                for entry in data["prompt_config"]:
                    is_agent = bool(entry.get("agent_id")) and bool(
                        entry.get("agent_version")
                    )

                    if is_agent:
                        try:
                            gv = GV.no_workspace_objects.select_related("graph").get(
                                id=entry["agent_version"]
                            )
                        except GV.DoesNotExist:
                            return self._gm.bad_request(
                                f"Agent version {entry['agent_version']} not found."
                            )

                        if str(gv.graph_id) != str(entry["agent_id"]):
                            return self._gm.bad_request(
                                "Selected agent version does not belong to the selected agent."
                            )
                        if gv.status == "draft":
                            return self._gm.bad_request(
                                f"Agent '{gv.graph.name} v{gv.version_number}' is in draft state and cannot be used in experiments."
                            )

                        validated_agent_versions[entry["agent_version"]] = gv
                    else:
                        try:
                            prompt_version = PromptVersion.objects.select_related(
                                "original_template"
                            ).get(id=entry["prompt_version"])
                        except PromptVersion.DoesNotExist:
                            return self._gm.bad_request(
                                f"PromptVersion {entry['prompt_version']} not found."
                            )

                        if str(prompt_version.original_template_id) != str(
                            entry["prompt_id"]
                        ):
                            return self._gm.bad_request(
                                "Selected prompt version does not belong to the selected prompt."
                            )
                        if prompt_version.is_draft:
                            return self._gm.bad_request(
                                f"Prompt '{prompt_version.original_template.name} {prompt_version.template_version}' is in draft state and cannot be used in experiments."
                            )

                        validated_prompt_versions[entry["prompt_version"]] = (
                            prompt_version
                        )

            # Create experiment
            experiment = ExperimentsTable.objects.create(
                name=data["name"],
                dataset=dataset,
                column=column,
                experiment_type=experiment_type,
                prompt_config=[],  # Deprecated — configs stored in EPC/EAC
                user=request.user,
            )

            # Create dataset snapshot
            snapshot_dataset, column_mapping, row_mapping = create_dataset_snapshot(
                dataset, experiment
            )

            # Point experiment.column to the snapshot copy of the target column
            if column and column.id in column_mapping:
                experiment.column = column_mapping[column.id]
                experiment.save(update_fields=["column"])

            # Process prompt_config entries → create EDT + EPC/EAC
            order = 0

            for entry in data["prompt_config"]:
                is_agent = bool(entry.get("agent_id")) and bool(
                    entry.get("agent_version")
                )

                if is_agent:
                    # Agent entry → one EDT + one EAC
                    from agent_playground.models.graph_version import GraphVersion as GV

                    gv = validated_agent_versions.get(entry["agent_version"])
                    if not gv:
                        gv = GV.no_workspace_objects.select_related("graph").get(
                            id=entry["agent_version"]
                        )

                    agent_name = (
                        entry.get("name") or f"{gv.graph.name} v{gv.version_number}"
                    )

                    edt = ExperimentDatasetTable.objects.create(
                        name=agent_name,
                        experiment=experiment,
                        status=StatusType.NOT_STARTED.value,
                    )

                    eac = ExperimentAgentConfig.objects.create(
                        experiment_dataset=edt,
                        graph_id=entry["agent_id"],
                        graph_version_id=entry["agent_version"],
                        name=agent_name,
                        order=order,
                    )

                    # Pre-create agent output columns (LLM + subgraph nodes)
                    from model_hub.views.experiment_runner import ExperimentRunner

                    ExperimentRunner.create_agent_output_columns(
                        eac, str(snapshot_dataset.id)
                    )

                    order += 1

                else:
                    # Prompt entry → one EDT + one EPC (1:1 mapping)
                    model_spec = entry.get("model")
                    model_params = entry.get("model_params", {})
                    configuration = entry.get("configuration", {})
                    output_format = entry.get("output_format", "string")
                    messages = entry.get("messages")
                    # Remap source column UUIDs → snapshot column UUIDs in messages
                    # so that populate_placeholders() can find them in the snapshot dataset
                    if messages and column_mapping:
                        from model_hub.views.utils.utils import update_column_id

                        col_id_str_mapping = {
                            str(old_id): str(new_col.id)
                            for old_id, new_col in column_mapping.items()
                        }
                        messages = [
                            update_column_id(dict(msg), col_id_str_mapping)
                            for msg in messages
                        ]
                    voice_input_column_id = entry.get("voice_input_column_id")
                    prompt_entry_name = entry.get("name", "")

                    # Validate prompt refs for LLM experiments
                    prompt_template = None
                    prompt_version = None
                    if experiment_type == "llm":
                        prompt_version = validated_prompt_versions.get(
                            entry["prompt_version"]
                        )
                        if not prompt_version:
                            prompt_version = PromptVersion.objects.select_related(
                                "original_template"
                            ).get(id=entry["prompt_version"])
                        prompt_template = prompt_version.original_template

                    # Resolve voice_input_column for STT
                    voice_input_column = None
                    if voice_input_column_id:
                        # Map original column to snapshot column
                        voice_input_column = column_mapping.get(voice_input_column_id)
                        if not voice_input_column:
                            # Try direct lookup in snapshot
                            voice_input_column = Column.objects.filter(
                                id=voice_input_column_id,
                                dataset=snapshot_dataset,
                                deleted=False,
                            ).first()

                    # Parse model spec — can be string or dict (ModelSpec)
                    model_name, model_display_name, model_config = parse_model_spec(
                        model_spec
                    )

                    # Build EDT name
                    if prompt_entry_name:
                        edt_name = f"{prompt_entry_name}-{model_name}"
                    else:
                        edt_name = model_name

                    edt = ExperimentDatasetTable.objects.create(
                        name=edt_name,
                        experiment=experiment,
                        status=StatusType.NOT_STARTED.value,
                    )

                    ExperimentPromptConfig.objects.create(
                        experiment_dataset=edt,
                        prompt_template=prompt_template,
                        prompt_version=prompt_version,
                        name=edt_name,
                        model=model_name,
                        model_display_name=model_display_name,
                        model_config=model_config,
                        model_params=model_params,
                        configuration=configuration,
                        output_format=output_format,
                        order=order,
                        messages=messages,
                        voice_input_column=voice_input_column,
                    )

                    # Pre-create prompt output column
                    from model_hub.services.column_service import (
                        create_experiment_column,
                    )

                    col, _ = create_experiment_column(
                        dataset=snapshot_dataset,
                        source_id=edt.id,
                        name=edt_name,
                        output_format=output_format,
                        response_format=(
                            model_config.get("response_format")
                            if model_config
                            else None
                        ),
                        status=StatusType.NOT_STARTED.value,
                    )
                    edt.columns.add(col)

                    order += 1

            # Create eval metrics inline (matching AddExperimentEvalView pattern)
            eval_metrics_data = data.get("user_eval_metrics", [])
            if eval_metrics_data:
                created_metrics = _create_eval_metrics_inline(
                    eval_entries=eval_metrics_data,
                    experiment=experiment,
                    snapshot_dataset=snapshot_dataset,
                    organization=organization,
                    user=request.user,
                    column_mapping=column_mapping,
                    workspace=getattr(request, "workspace", None),
                )
                experiment.user_eval_template_ids.set(created_metrics)

                # Pre-create eval columns (requires EDT output columns to exist)
                from model_hub.views.experiment_runner import ExperimentRunner

                exp_runner = ExperimentRunner(experiment.id)
                exp_runner.load_experiment()
                exp_runner.empty_or_create_evals_column()

            # Build canonical column ordering for the snapshot
            _build_and_save_v2_column_order(experiment, snapshot_dataset)

            # Start V2 Temporal workflow
            try:
                from tfc.temporal.experiments import start_experiment_v2_workflow

                workflow_id = start_experiment_v2_workflow(
                    experiment_id=str(experiment.id),
                    max_concurrent_rows=10,
                )
                logger.info(
                    "Started V2 Temporal workflow for experiment",
                    workflow_id=workflow_id,
                    experiment_id=str(experiment.id),
                )
            except Exception as e:
                logger.warning(
                    "Failed to start V2 Temporal workflow for experiment",
                    experiment_id=str(experiment.id),
                    error=str(e),
                )

            return self._gm.success_response("Experiment created successfully.")

        except Exception as e:
            logger.exception(f"Error creating V2 experiment: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_CREATE_EXP"))

    def put(self, request, experiment_id):
        """Update a V2 experiment with diff-based selective re-run.

        Editable fields: column_id, prompt_config, user_eval_metrics.
        Re-run triggers (determined by fingerprint diffs, not field presence):
        - prompt_config has new/modified entries → re-run those configs + ALL dependent evals
        - user_eval_metrics has new/modified entries → re-run only those evals
        - column_id changed → delete old base eval columns, re-run base evals
        - If FE sends unchanged data, diffs return empty → no re-run
        """
        serializer = ExperimentUpdateV2Serializer(data=request.data)
        if not serializer.is_valid():
            return self._gm.bad_request(parse_serialized_errors(serializer))

        data = serializer.validated_data
        organization = getattr(request, "organization", None) or request.user.organization

        try:
            experiment = ExperimentsTable.objects.get(
                id=experiment_id,
                dataset__organization=organization,
                deleted=False,
            )
        except ExperimentsTable.DoesNotExist:
            return self._gm.not_found("Experiment not found")

        try:
            # --- Update column_id with change detection ---
            column_changed = False
            if "column_id" in data:
                new_column_id = data["column_id"]
                old_column_id = experiment.column_id

                if new_column_id != old_column_id:
                    # Validate new column against snapshot dataset
                    if new_column_id is not None:
                        try:
                            column = Column.objects.get(
                                id=new_column_id,
                                dataset=experiment.snapshot_dataset,
                                deleted=False,
                            )
                        except Column.DoesNotExist:
                            return self._gm.not_found("Column not found in dataset")
                    else:
                        column = None

                    # Delete old base eval columns if old column existed
                    if old_column_id:
                        _delete_base_eval_columns(experiment)

                    experiment.column = column
                    experiment.save(update_fields=["column"])

                    # Trigger base eval re-run only if new column is set
                    if new_column_id is not None:
                        column_changed = True

            rerun_prompt_ids = []
            rerun_agent_ids = []
            rerun_eval_ids = []

            # --- Diff prompt_config: creates new EDTs, soft-deletes removed ones ---
            if "prompt_config" in data:
                rerun_prompt_ids, rerun_agent_ids = _diff_and_update_configs(
                    experiment,
                    data["prompt_config"],
                )

                # Pre-create eval columns for NEW EDTs so the FE can
                # render them immediately on the first poll (before the
                # workflow runs).  Only targets new configs — changed
                # configs already have eval columns from the initial run.
                if rerun_prompt_ids or rerun_agent_ids:
                    _precreate_eval_columns_for_configs(
                        experiment, rerun_prompt_ids, rerun_agent_ids
                    )

            # --- Diff eval metrics: creates new UserEvalMetrics, updates M2M ---
            if "user_eval_metrics" in data:
                rerun_eval_ids = _diff_and_update_evals(
                    experiment,
                    data["user_eval_metrics"],
                    organization,
                    request.user,
                    workspace=getattr(request, "workspace", None),
                )

            # --- Trigger selective re-run only if diffs found actual changes ---
            needs_rerun = bool(
                rerun_prompt_ids or rerun_agent_ids or rerun_eval_ids or column_changed
            )
            logger.info(
                "NEED RUN",
                needs_rerun=needs_rerun,
                rerun_prompt_ids=rerun_prompt_ids,
                rerun_agent_ids=rerun_agent_ids,
                rerun_eval_ids=rerun_eval_ids,
                column_changed=column_changed,
            )

            # Rebuild column ordering after mutations
            if "prompt_config" in data or "user_eval_metrics" in data or column_changed:
                _build_and_save_v2_column_order(experiment, experiment.snapshot_dataset)

            if needs_rerun:
                from tfc.temporal.experiments import start_experiment_v2_workflow

                workflow_id = start_experiment_v2_workflow(
                    experiment_id=str(experiment.id),
                    rerun_prompt_config_ids=rerun_prompt_ids,
                    rerun_agent_config_ids=rerun_agent_ids,
                    rerun_eval_template_ids=rerun_eval_ids,
                    column_changed=column_changed,
                )
                logger.info(
                    "Started selective V2 workflow for experiment update",
                    workflow_id=workflow_id,
                    experiment_id=str(experiment.id),
                    rerun_prompt_ids=rerun_prompt_ids,
                    rerun_agent_ids=rerun_agent_ids,
                    rerun_eval_ids=rerun_eval_ids,
                    column_changed=column_changed,
                )

            exp_serializer = ExperimentDetailV2Serializer(
                experiment,
                context={"organization_id": str(organization.id)},
            )
            return self._gm.success_response(exp_serializer.data)

        except ValueError as e:
            return self._gm.bad_request(str(e))
        except Exception as e:
            logger.exception(f"Error updating V2 experiment: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_CREATE_EXP"))


# =============================================================================
# V2 Helpers
# =============================================================================


def _build_column_mapping_from_snapshot(snapshot_dataset):
    """Build {original_col_UUID: snapshot_Column} mapping from snapshot columns.

    Snapshot columns store the original column ID in their source_id field.
    This reconstructs the column_mapping that was used during snapshot creation,
    so eval mappings can be translated from original to snapshot column UUIDs.
    """
    if not snapshot_dataset:
        return {}

    import uuid as _uuid_mod

    column_mapping = {}
    for col in Column.objects.filter(dataset=snapshot_dataset, deleted=False):
        if col.source_id:
            try:
                original_id = _uuid_mod.UUID(str(col.source_id))
                column_mapping[original_id] = col
            except (ValueError, AttributeError):
                pass
    return column_mapping


def _build_and_save_v2_column_order(experiment, snapshot_dataset):
    """Build canonical column ordering and save to snapshot_dataset.column_order.

    Order: base_column → EDT output cols (prompt_config order) → eval cols → other dataset cols.
    """
    from django.db.models import F, Value
    from django.db.models.functions import Coalesce

    ordered = []
    seen = set()

    def _add(col_id_str):
        if col_id_str not in seen:
            seen.add(col_id_str)
            ordered.append(col_id_str)

    # 1. Base column first
    if experiment.column_id:
        _add(str(experiment.column_id))

    # 2. EDT output columns in prompt_config order
    edt_qs = (
        experiment.experiment_datasets.filter(deleted=False)
        .annotate(
            config_order=Coalesce(
                F("prompt_config__order"), F("agent_config__order"), Value(999)
            )
        )
        .order_by("config_order")
    )
    for edt in edt_qs:
        output_cols = list(
            edt.columns.filter(deleted=False, source=SourceChoices.EXPERIMENT.value)
        )
        # For agent EDTs, sort by topological order
        try:
            eac = edt.agent_config
            from agent_playground.services.engine.analyzer import GraphAnalyzer

            topology = GraphAnalyzer.analyze(eac.graph_version_id)
            node_order = {
                str(nid): pos for pos, nid in enumerate(topology.topological_order)
            }
            output_cols.sort(
                key=lambda c: node_order.get((c.metadata or {}).get("node_id", ""), 999)
            )
        except Exception:
            pass  # Not an agent EDT or analysis failed — keep creation order
        for col in output_cols:
            _add(str(col.id))

    # 3. Eval columns (all types, by created_at)
    eval_sources = [
        SourceChoices.EVALUATION.value,
        SourceChoices.EVALUATION_REASON.value,
        SourceChoices.EXPERIMENT_EVALUATION.value,
        SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
    ]
    eval_cols = Column.objects.filter(
        dataset=snapshot_dataset, deleted=False, source__in=eval_sources
    ).order_by("created_at")
    for col in eval_cols:
        _add(str(col.id))

    # 4. Remaining dataset columns (preserve original order from snapshot)
    existing_order = snapshot_dataset.column_order or []
    for uid in existing_order:
        _add(uid)
    # Catch any dataset columns not in existing_order
    other_cols = Column.objects.filter(
        dataset=snapshot_dataset,
        deleted=False,
        source__in=[SourceChoices.OTHERS.value, SourceChoices.RUN_PROMPT.value],
    ).order_by("created_at")
    for col in other_cols:
        _add(str(col.id))

    snapshot_dataset.column_order = ordered
    snapshot_dataset.save(update_fields=["column_order"])


def _translate_eval_mapping(mapping, column_mapping):
    """Translate original column UUIDs in eval mapping to snapshot column UUIDs.

    Skips special values ("output", "prompt_chain") and non-UUID values.
    Handles UUID.json.path format (translates just the UUID prefix).
    """
    from model_hub.views.eval_runner import (
        _extract_column_id_and_path,
        _is_special_mapping_value,
    )

    if not mapping or not column_mapping:
        return mapping

    translated = {}
    for key, value in mapping.items():
        if isinstance(value, list):
            translated[key] = [
                _translate_single_mapping_value(v, column_mapping) for v in value
            ]
        else:
            translated[key] = _translate_single_mapping_value(value, column_mapping)
    return translated


def _translate_single_mapping_value(value, column_mapping):
    """Translate a single mapping value if it's a column UUID."""
    from model_hub.views.eval_runner import (
        _extract_column_id_and_path,
        _is_special_mapping_value,
    )

    if not value or not isinstance(value, str):
        return value
    if _is_special_mapping_value(value):
        return value

    from uuid import UUID as _UUID

    base_id, json_path = _extract_column_id_and_path(value)
    try:
        original_uuid = _UUID(base_id)
    except (ValueError, AttributeError):
        return value  # Not a UUID — keep as-is

    snapshot_col = column_mapping.get(original_uuid)
    if snapshot_col:
        new_id = str(snapshot_col.id)
        return f"{new_id}.{json_path}" if json_path else new_id
    return value  # No mapping found — keep original


def _create_eval_metrics_inline(
    eval_entries,
    experiment,
    snapshot_dataset,
    organization,
    user,
    column_mapping=None,
    workspace=None,
):
    """Create UserEvalMetric records inline during experiment creation/update.

    Matches AddExperimentEvalView pattern (views/experiments.py:2024-2034):
    - Sets status=EXPERIMENT_EVALUATION
    - Sets source_id=experiment.id
    - Uses EvalTemplate.no_workspace_objects for template lookup

    Args:
        eval_entries: List of validated eval config dicts (from EvalMetricEntrySerializer).
        experiment: The ExperimentsTable instance.
        snapshot_dataset: The snapshot Dataset to bind evals to.
        organization: The organization.
        user: The user creating the eval.
        column_mapping: Optional dict {original_col_id: snapshot_column} for translating
            mapping UUIDs from original dataset to snapshot dataset.
        workspace: The workspace from the request header.

    Returns:
        List of created UserEvalMetric instances.
    """
    created = []
    for entry in eval_entries:
        template = EvalTemplate.no_workspace_objects.get(id=entry["template_id"])

        config = entry["config"]
        # Translate original column UUIDs in mapping to snapshot column UUIDs
        if column_mapping and config.get("mapping"):
            config = {
                **config,
                "mapping": _translate_eval_mapping(config["mapping"], column_mapping),
            }

        metric = UserEvalMetric.objects.create(
            name=entry["name"],
            organization=organization,
            workspace=workspace,
            dataset=snapshot_dataset,
            template=template,
            config=config,
            status=StatusType.EXPERIMENT_EVALUATION.value,
            source_id=str(experiment.id),
            user=user,
            model=entry.get("model", ""),
            error_localizer=entry.get("error_localizer", False),
            kb_id=entry.get("kb_id"),
            # Per-binding weight overrides for composite evals. Ignored
            # for single-template metrics. See Phase 7 wiring plan.
            composite_weight_overrides=entry.get("composite_weight_overrides"),
        )
        created.append(metric)
    return created


def _delete_base_eval_columns(experiment):
    """Soft-delete base eval columns (and their reason columns) on the snapshot dataset.

    Base eval columns have source_id = str(user_eval_metric.id) and live on the
    snapshot dataset.  Reason columns follow the pattern
    "{eval_col_id}-sourceid-{user_eval_metric_id}".
    """
    from django.utils import timezone

    from model_hub.models.choices import SourceChoices
    from model_hub.models.develop_dataset import Column

    snapshot_ds = experiment.snapshot_dataset
    if not snapshot_ds:
        return

    eval_metric_ids = list(
        experiment.user_eval_template_ids.values_list("id", flat=True)
    )
    str_metric_ids = [str(mid) for mid in eval_metric_ids]

    # Find base eval columns
    base_eval_cols = Column.objects.filter(
        dataset=snapshot_ds,
        source=SourceChoices.EVALUATION.value,
        source_id__in=str_metric_ids,
        deleted=False,
    )

    base_col_ids = list(base_eval_cols.values_list("id", flat=True))

    # Find associated reason columns
    reason_source_ids = []
    for col_id in base_col_ids:
        for metric_id in str_metric_ids:
            reason_source_ids.append(f"{col_id}-sourceid-{metric_id}")

    now = timezone.now()
    base_eval_cols.update(deleted=True, deleted_at=now)

    if reason_source_ids:
        Column.objects.filter(
            dataset=snapshot_ds,
            source=SourceChoices.EVALUATION_REASON.value,
            source_id__in=reason_source_ids,
            deleted=False,
        ).update(deleted=True, deleted_at=now)


def _soft_delete_edt_and_columns(edt):
    """Soft-delete an ExperimentDatasetTable and its associated experiment columns.

    Deletes both the M2M-linked columns (prompt output) AND per-EDT eval
    columns whose source_id embeds the EDT id (format:
    ``{edt.id}-{col.id}-sourceid-{metric_id}``).
    """
    from django.utils import timezone

    now = timezone.now()
    snapshot_dataset = edt.experiment.snapshot_dataset

    # 1. Soft-delete M2M-linked columns (prompt output column)
    edt.columns.filter(deleted=False).update(deleted=True, deleted_at=now)

    # 2. Soft-delete per-EDT eval columns + their reason columns
    edt_id_prefix = str(edt.id)
    eval_cols = Column.objects.filter(
        dataset=snapshot_dataset,
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id__startswith=edt_id_prefix,
        deleted=False,
    )
    # Collect source_ids to find matching reason columns
    eval_source_ids = list(eval_cols.values_list("source_id", flat=True))
    eval_cols.update(deleted=True, deleted_at=now)

    if eval_source_ids:
        reason_source_ids = [f"{sid}-reason" for sid in eval_source_ids]
        Column.objects.filter(
            dataset=snapshot_dataset,
            source=SourceChoices.EVALUATION_REASON.value,
            source_id__in=reason_source_ids,
            deleted=False,
        ).update(deleted=True, deleted_at=now)

    edt.deleted = True
    edt.save(update_fields=["deleted"])


def _precreate_eval_columns_for_configs(
    experiment, prompt_config_ids, agent_config_ids
):
    """Pre-create per-EDT eval columns for newly added prompt/agent configs.

    Delegates to ExperimentRunner.precreate_eval_columns_for_edts which
    creates eval columns only for the specified EDTs without resetting
    any existing cells.
    """
    from model_hub.views.experiment_runner import ExperimentRunner

    new_edt_ids = set()

    if prompt_config_ids:
        edt_ids = ExperimentPromptConfig.objects.filter(
            id__in=prompt_config_ids,
            experiment_dataset__deleted=False,
        ).values_list("experiment_dataset_id", flat=True)
        new_edt_ids.update(edt_ids)

    if agent_config_ids:
        edt_ids = ExperimentAgentConfig.objects.filter(
            id__in=agent_config_ids,
            experiment_dataset__deleted=False,
        ).values_list("experiment_dataset_id", flat=True)
        new_edt_ids.update(edt_ids)

    if not new_edt_ids:
        return

    exp_runner = ExperimentRunner(experiment.id)
    exp_runner.load_experiment()
    exp_runner.precreate_eval_columns_for_edts(list(new_edt_ids))


def _has_prompt_config_changed(
    epc,
    entry,
    model_name,
    model_config,
    per_model_params,
    configuration,
    output_format,
    messages,
    voice_input_column,
):
    """Check if any output-affecting field on a prompt config has changed."""
    if str(epc.prompt_template_id or "") != str(entry.get("prompt_id") or ""):
        return True
    if str(epc.prompt_version_id or "") != str(entry.get("prompt_version") or ""):
        return True
    if epc.model != model_name:
        return True
    if json.dumps(epc.model_config or {}, sort_keys=True) != json.dumps(
        model_config or {}, sort_keys=True
    ):
        return True
    if json.dumps(epc.model_params or {}, sort_keys=True) != json.dumps(
        per_model_params or {}, sort_keys=True
    ):
        return True
    if json.dumps(epc.configuration or {}, sort_keys=True) != json.dumps(
        configuration or {}, sort_keys=True
    ):
        return True
    if epc.output_format != output_format:
        return True
    if json.dumps(epc.messages or [], sort_keys=True) != json.dumps(
        messages or [], sort_keys=True
    ):
        return True
    if str(epc.voice_input_column_id or "") != str(
        entry.get("voice_input_column_id") or ""
    ):
        return True
    return False


def _update_epc_fields(
    epc,
    entry,
    model_name,
    model_display_name,
    model_config,
    per_model_params,
    configuration,
    output_format,
    messages,
    voice_input_column,
    prompt_template,
    prompt_version,
):
    """Update EPC fields in-place and save."""
    epc.prompt_template = prompt_template
    epc.prompt_version = prompt_version
    epc.model = model_name
    epc.model_display_name = model_display_name
    epc.model_config = model_config
    epc.model_params = per_model_params
    epc.configuration = configuration
    epc.output_format = output_format
    epc.messages = messages
    epc.voice_input_column = voice_input_column

    new_name = entry.get("name")
    if new_name:
        edt_name = f"{new_name}-{model_name}" if new_name else model_name
        epc.name = edt_name
        epc.experiment_dataset.name = edt_name
        epc.experiment_dataset.save(update_fields=["name"])

    epc.save()


def _diff_and_update_configs(experiment, new_entries):
    """Diff old EPC/EAC configs with new entries using ID-based matching.

    For each entry in new_entries:
    - Has id → EXISTING config: compare fields, update in-place if changed
    - No id → NEW config: create EDT + EPC/EAC + pre-create columns
    - Old IDs not in update → REMOVED: soft-delete EDT + columns

    Returns:
        (rerun_prompt_ids, rerun_agent_ids) — IDs of changed/new configs to re-run.
    """
    from model_hub.services.column_service import create_experiment_column

    experiment_type = experiment.experiment_type
    snapshot_dataset = experiment.snapshot_dataset

    # Build ID maps for existing configs
    old_epcs = ExperimentPromptConfig.objects.filter(
        experiment_dataset__experiment=experiment,
        experiment_dataset__deleted=False,
    ).select_related("experiment_dataset")
    old_epc_by_id = {str(epc.id): epc for epc in old_epcs}

    old_eac_by_id = {}
    if experiment_type == "llm":
        old_eacs = ExperimentAgentConfig.objects.filter(
            experiment_dataset__experiment=experiment,
            experiment_dataset__deleted=False,
        ).select_related("experiment_dataset")
        old_eac_by_id = {str(eac.id): eac for eac in old_eacs}

    seen_epc_ids = set()
    seen_eac_ids = set()
    rerun_prompt_ids = []
    rerun_agent_ids = []

    # Compute next order value for new configs
    max_order = max((epc.order for epc in old_epcs), default=-1) + 1
    if experiment_type == "llm":
        max_order = max(
            max_order,
            max((eac.order for eac in old_eac_by_id.values()), default=-1) + 1,
        )
    order = max_order

    for entry in new_entries:
        is_agent = bool(entry.get("agent_id")) and bool(entry.get("agent_version"))
        entry_id = str(entry["id"]) if entry.get("id") else None

        if is_agent and experiment_type == "llm":
            if entry_id and entry_id in old_eac_by_id:
                # EXISTING agent — check for changes
                eac = old_eac_by_id[entry_id]
                seen_eac_ids.add(entry_id)
                changed = str(eac.graph_id) != str(entry["agent_id"]) or str(
                    eac.graph_version_id
                ) != str(entry["agent_version"])
                if changed:
                    from agent_playground.models.graph_version import GraphVersion as GV

                    gv = GV.no_workspace_objects.select_related("graph").get(
                        id=entry["agent_version"]
                    )
                    if gv.status == "draft":
                        raise ValueError(
                            f"Agent '{gv.graph.name} v{gv.version_number}' is in draft state and cannot be used in experiments."
                        )

                    eac.graph_id = entry["agent_id"]
                    eac.graph_version_id = entry["agent_version"]
                    new_name = (
                        entry.get("name") or f"{gv.graph.name} v{gv.version_number}"
                    )
                    eac.name = new_name
                    eac.experiment_dataset.name = new_name
                    eac.experiment_dataset.save(update_fields=["name"])
                    eac.save()
                    rerun_agent_ids.append(entry_id)
            else:
                # NEW agent → create EDT + EAC + output columns
                from agent_playground.models.graph_version import GraphVersion as GV
                from model_hub.views.experiment_runner import ExperimentRunner

                gv = GV.no_workspace_objects.select_related("graph").get(
                    id=entry["agent_version"]
                )
                if gv.status == "draft":
                    raise ValueError(
                        f"Agent '{gv.graph.name} v{gv.version_number}' is in draft state and cannot be used in experiments."
                    )
                agent_name = (
                    entry.get("name") or f"{gv.graph.name} v{gv.version_number}"
                )
                edt = ExperimentDatasetTable.objects.create(
                    name=agent_name,
                    experiment=experiment,
                    status=StatusType.NOT_STARTED.value,
                )
                eac = ExperimentAgentConfig.objects.create(
                    experiment_dataset=edt,
                    graph_id=entry["agent_id"],
                    graph_version_id=entry["agent_version"],
                    name=agent_name,
                    order=order,
                )

                # Pre-create agent output columns (idempotent) so FE
                # can render them + eval columns on the first poll.
                # Reload EAC with required relations for the utility.
                eac = ExperimentAgentConfig.objects.select_related(
                    "experiment_dataset",
                    "graph_version",
                    "graph_version__graph",
                ).get(id=eac.id)
                ExperimentRunner.create_agent_output_columns(
                    eac, str(experiment.snapshot_dataset_id)
                )

                seen_eac_ids.add(str(eac.id))
                rerun_agent_ids.append(str(eac.id))
                order += 1

        elif not is_agent:
            # Prompt entry
            model_spec = entry.get("model")
            model_params = entry.get("model_params", {})
            configuration = entry.get("configuration", {})
            output_format = entry.get("output_format", "string")
            messages = entry.get("messages")
            # Remap source column UUIDs → snapshot column UUIDs in messages
            if messages and snapshot_dataset:
                from model_hub.views.utils.utils import update_column_id

                snapshot_cols = (
                    Column.objects.filter(dataset=snapshot_dataset, deleted=False)
                    .exclude(source_id__isnull=True)
                    .exclude(source_id="")
                )
                col_id_str_mapping = {
                    col.source_id: str(col.id) for col in snapshot_cols
                }
                if col_id_str_mapping:
                    messages = [
                        update_column_id(dict(msg), col_id_str_mapping)
                        for msg in messages
                    ]
            voice_input_column_id = entry.get("voice_input_column_id")
            prompt_entry_name = entry.get("name", "")

            prompt_template = None
            prompt_version = None
            if experiment_type == "llm" and entry.get("prompt_id"):
                prompt_template = PromptTemplate.objects.filter(
                    id=entry["prompt_id"],
                ).first()
                prompt_version = PromptVersion.objects.filter(
                    id=entry["prompt_version"],
                ).first()
                if prompt_version and prompt_version.is_draft:
                    raise ValueError(
                        f"Prompt '{prompt_template.name if prompt_template else ''} {prompt_version.template_version}' is in draft state and cannot be used in experiments."
                    )

            voice_input_column = None
            if voice_input_column_id and snapshot_dataset:
                voice_input_column = Column.objects.filter(
                    id=voice_input_column_id,
                    dataset=snapshot_dataset,
                    deleted=False,
                ).first()

            if entry_id and entry_id in old_epc_by_id:
                # EXISTING prompt config — matched by ID
                epc = old_epc_by_id[entry_id]
                seen_epc_ids.add(entry_id)

                # Use existing values, override with incoming
                model_name = epc.model
                model_display_name = epc.model_display_name
                model_config = epc.model_config or {}
                per_model_params = epc.model_params or {}

                if model_spec is not None:
                    model_name, model_display_name, model_config = parse_model_spec(
                        model_spec
                    )
                    per_model_params = model_params

                if _has_prompt_config_changed(
                    epc,
                    entry,
                    model_name,
                    model_config,
                    per_model_params,
                    configuration,
                    output_format,
                    messages,
                    voice_input_column,
                ):
                    _update_epc_fields(
                        epc,
                        entry,
                        model_name,
                        model_display_name,
                        model_config,
                        per_model_params,
                        configuration,
                        output_format,
                        messages,
                        voice_input_column,
                        prompt_template,
                        prompt_version,
                    )
                    rerun_prompt_ids.append(entry_id)
            else:
                # NEW prompt — one entry = one EDT + one EPC
                model_name, model_display_name, model_config = parse_model_spec(
                    model_spec
                )

                if prompt_entry_name:
                    edt_name = f"{prompt_entry_name}-{model_name}"
                else:
                    edt_name = model_name

                edt = ExperimentDatasetTable.objects.create(
                    name=edt_name,
                    experiment=experiment,
                    status=StatusType.NOT_STARTED.value,
                )
                epc = ExperimentPromptConfig.objects.create(
                    experiment_dataset=edt,
                    prompt_template=prompt_template,
                    prompt_version=prompt_version,
                    name=edt_name,
                    model=model_name,
                    model_display_name=model_display_name,
                    model_config=model_config,
                    model_params=model_params,
                    configuration=configuration,
                    output_format=output_format,
                    order=order,
                    messages=messages,
                    voice_input_column=voice_input_column,
                )

                # Pre-create prompt output column (same as POST)
                col, _ = create_experiment_column(
                    dataset=snapshot_dataset,
                    source_id=edt.id,
                    name=edt_name,
                    output_format=output_format,
                    response_format=(
                        model_config.get("response_format") if model_config else None
                    ),
                    status=StatusType.NOT_STARTED.value,
                )
                edt.columns.add(col)

                seen_epc_ids.add(str(epc.id))
                rerun_prompt_ids.append(str(epc.id))
                order += 1

    # Soft-delete REMOVED prompt configs
    for epc_id, epc in old_epc_by_id.items():
        if epc_id not in seen_epc_ids:
            _soft_delete_edt_and_columns(epc.experiment_dataset)

    # Soft-delete REMOVED agent configs (LLM only)
    for eac_id, eac in old_eac_by_id.items():
        if eac_id not in seen_eac_ids:
            _soft_delete_edt_and_columns(eac.experiment_dataset)

    return rerun_prompt_ids, rerun_agent_ids


def _has_eval_changed(metric, entry, translated_mapping):
    """Check if any field on an eval metric has changed."""
    old_mapping = (metric.config or {}).get("mapping", {})
    old_config_inner = (metric.config or {}).get("config", {})
    new_config_inner = (entry.get("config") or {}).get("config", {})

    if str(metric.template_id) != str(entry["template_id"]):
        return True
    if json.dumps(old_mapping, sort_keys=True) != json.dumps(
        translated_mapping, sort_keys=True
    ):
        return True
    if json.dumps(old_config_inner, sort_keys=True) != json.dumps(
        new_config_inner, sort_keys=True
    ):
        return True
    if (metric.model or "") != (entry.get("model") or ""):
        return True
    if bool(metric.error_localizer) != bool(entry.get("error_localizer", False)):
        return True
    if str(metric.kb_id or "") != str(entry.get("kb_id") or ""):
        return True
    if metric.name != entry.get("name", ""):
        return True
    return False


def _diff_and_update_evals(
    experiment, new_eval_entries, organization, user, workspace=None
):
    """Diff old vs new eval configs using ID-based matching.

    For each entry in new_eval_entries:
    - Has id → EXISTING metric: compare fields, update in-place if changed
    - No id → NEW metric: create via _create_eval_metrics_inline
    - Old IDs not in update → REMOVED: soft-delete columns, remove from M2M

    Updates the experiment's M2M relationship and returns IDs of evals to re-run.
    """
    old_metrics = list(experiment.user_eval_template_ids.all())
    old_by_id = {str(m.id): m for m in old_metrics}

    # Build column_mapping for translating incoming mapping UUIDs (new evals only)
    column_mapping = _build_column_mapping_from_snapshot(experiment.snapshot_dataset)

    keep_ids = []
    rerun_ids = []
    seen_ids = set()

    for entry in new_eval_entries:
        entry_id = str(entry["id"]) if entry.get("id") else None

        if entry_id and entry_id in old_by_id:
            # EXISTING metric — check for changes
            metric = old_by_id[entry_id]
            seen_ids.add(entry_id)
            keep_ids.append(metric.id)

            # FE gets snapshot UUIDs from GET detail, so incoming mapping
            # is already in snapshot UUIDs — no translation needed.
            incoming_mapping = (entry.get("config") or {}).get("mapping", {})

            if _has_eval_changed(metric, entry, incoming_mapping):
                # Update in-place — same metric ID → same column source_ids → no duplicates
                new_config = entry["config"].copy()
                new_config["mapping"] = incoming_mapping
                metric.name = entry["name"]
                metric.template_id = entry["template_id"]
                metric.config = new_config
                metric.model = entry.get("model", "")
                metric.error_localizer = entry.get("error_localizer", False)
                metric.kb_id = entry.get("kb_id")
                metric.save()
                rerun_ids.append(entry_id)
        else:
            # NEW metric — create and pre-create eval columns
            metrics = _create_eval_metrics_inline(
                [entry],
                experiment,
                experiment.snapshot_dataset,
                organization,
                user,
                column_mapping=column_mapping,
                workspace=workspace,
            )
            new_metric = metrics[0]
            seen_ids.add(str(new_metric.id))
            keep_ids.append(new_metric.id)
            rerun_ids.append(str(new_metric.id))

    # Update M2M before pre-creating columns (empty_or_create_evals_column reads M2M)
    experiment.user_eval_template_ids.set(keep_ids)

    # Pre-create eval columns for new/changed evals (same as POST)
    if rerun_ids:
        from uuid import UUID as _UUID

        from model_hub.views.experiment_runner import ExperimentRunner

        exp_runner = ExperimentRunner(experiment.id)
        exp_runner.load_experiment()
        exp_runner.empty_or_create_evals_column(
            eval_template_ids=[_UUID(eid) for eid in rerun_ids]
        )

    # Soft-delete columns for REMOVED evals
    removed_metric_ids = [mid for mid in old_by_id if mid not in seen_ids]
    if removed_metric_ids and experiment.snapshot_dataset:
        from django.db.models import Q

        delete_q = Q()
        for mid in removed_metric_ids:
            delete_q |= Q(source_id=mid)
            delete_q |= Q(source_id__endswith=f"-sourceid-{mid}")

        Column.objects.filter(
            delete_q,
            dataset=experiment.snapshot_dataset,
            deleted=False,
        ).update(deleted=True)

    return rerun_ids


class ExperimentListV2APIView(generics.ListAPIView):
    """V2 experiment list with filtering, search, and pagination."""

    serializer_class = ExperimentListV2Serializer
    permission_classes = [IsAuthenticated]
    pagination_class = ExtendedPageNumberPagination
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = ExperimentFilter
    search_fields = ["name"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return (
            ExperimentsTable.objects.filter(
                dataset__organization=self.request.user.organization,
                deleted=False,
            )
            .select_related("dataset")
            .prefetch_related("user_eval_template_ids")
            .annotate(
                models_count=Count(
                    "experiment_datasets__prompt_config",
                    filter=Q(experiment_datasets__deleted=False),
                    distinct=True,
                ),
                agents_count=Count(
                    "experiment_datasets__agent_config",
                    filter=Q(experiment_datasets__deleted=False),
                    distinct=True,
                ),
            )
        )


# =============================================================================
# V2 Utility Helpers
# =============================================================================


def experiment_name_exists(name, dataset, exclude_id=None):
    """Check if an experiment name already exists for a dataset."""
    qs = ExperimentsTable.objects.filter(name=name, dataset=dataset, deleted=False)
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    return qs.exists()


def get_v2_output_format(experiment):
    """Get output_format from ExperimentPromptConfig instead of prompt_config JSON."""
    epc = ExperimentPromptConfig.objects.filter(
        experiment_dataset__experiment=experiment,
        experiment_dataset__deleted=False,
    ).first()
    return epc.output_format if epc else "string"


def get_working_dataset(experiment):
    """Return snapshot_dataset for V2 experiments."""
    if not experiment.snapshot_dataset:
        raise ValueError(
            f"Experiment {experiment.id} has no snapshot_dataset. "
            "V2 experiments must have a snapshot."
        )
    return experiment.snapshot_dataset


class ExperimentJsonSchemaView(APIView):
    """
    Get JSON schemas and images metadata for columns in an experiment's snapshot dataset.
    Delegates to the shared get_json_column_schemas() function.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request, experiment_id):
        try:
            from model_hub.views.develop_dataset import get_json_column_schemas

            organization = getattr(request, "organization", None) or request.user.organization
            experiment = ExperimentsTable.objects.select_related(
                "dataset", "snapshot_dataset"
            ).get(
                id=experiment_id,
                dataset__organization=organization,
                deleted=False,
            )
            dataset = get_working_dataset(experiment)
            result = get_json_column_schemas(dataset)
            return self._gm.success_response(result)
        except ExperimentsTable.DoesNotExist:
            return self._gm.not_found("Experiment not found")
        except ValueError as e:
            return self._gm.bad_request(str(e))
        except Exception as e:
            logger.exception(
                "Error getting JSON schema for experiment",
                experiment_id=str(experiment_id),
            )
            return self._gm.bad_request(str(e))


class ExperimentDerivedVariablesView(APIView):
    """
    Get derived variables from run prompt columns in an experiment's snapshot dataset.
    Delegates to the existing get_dataset_derived_variables() service function.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request, experiment_id):
        try:
            from model_hub.services.derived_variable_service import (
                get_dataset_derived_variables,
            )

            organization = getattr(request, "organization", None) or request.user.organization
            experiment = ExperimentsTable.objects.select_related(
                "dataset", "snapshot_dataset"
            ).get(
                id=experiment_id,
                dataset__organization=organization,
                deleted=False,
            )
            dataset = get_working_dataset(experiment)
            result = get_dataset_derived_variables(str(dataset.id), organization)
            return self._gm.success_response(result)
        except ExperimentsTable.DoesNotExist:
            return self._gm.not_found("Experiment not found")
        except ValueError as e:
            return self._gm.bad_request(str(e))
        except Exception as e:
            logger.exception(
                "Error getting derived variables for experiment",
                experiment_id=str(experiment_id),
            )
            return self._gm.bad_request(str(e))


# =============================================================================
# V2 Delete & Re-run Views
# =============================================================================


class ExperimentDeleteV2View(APIView):
    """V2 delete: org-scoped, cancels workflows, cleans up columns & EDTs."""

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def delete(self, request):
        try:
            experiment_ids = request.data.get("experiment_ids", [])
            if not experiment_ids:
                return self._gm.bad_request(get_error_message("MISSING_EXP_IDS"))

            organization = getattr(request, "organization", None) or request.user.organization
            experiments = ExperimentsTable.objects.filter(
                id__in=experiment_ids,
                dataset__organization=organization,
                deleted=False,
            )

            if not experiments.exists():
                return self._gm.not_found("No experiments found")

            # Cancel running Temporal workflows (best effort)
            from tfc.temporal.experiments import cancel_experiment_workflow

            for exp in experiments:
                try:
                    cancel_experiment_workflow(str(exp.id))
                except Exception:
                    pass

            # Collect EDT IDs for column cleanup
            edt_ids = list(
                ExperimentDatasetTable.objects.filter(
                    experiment__in=experiments
                ).values_list("id", flat=True)
            )

            # Soft-delete columns in snapshot datasets belonging to these EDTs
            if edt_ids:
                Column.objects.filter(
                    source_id__in=[str(edt_id) for edt_id in edt_ids],
                    source__in=[
                        SourceChoices.EXPERIMENT.value,
                        SourceChoices.EXPERIMENT_EVALUATION.value,
                    ],
                ).update(deleted=True)

            # Soft-delete EDT records
            ExperimentDatasetTable.objects.filter(experiment__in=experiments).update(
                deleted=True
            )

            # Soft-delete experiments
            updated_count = experiments.update(deleted=True)

            return self._gm.success_response(
                {
                    "message": f"{updated_count} experiments deleted successfully",
                    "deleted_count": updated_count,
                }
            )

        except Exception as e:
            logger.exception(f"Error in deleting experiment: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_DELETE_EXP"))


class ExperimentRerunV2View(APIView):
    """V2 re-run: org-scoped, uses V2 Temporal workflow.

    No manual workflow cancel needed — Temporal's TERMINATE_IF_RUNNING ID
    reuse policy automatically cancels any running workflow with the same ID.
    Cell reset is handled by the workflow itself (cleanup + setup activities).
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            serializer = ExperimentIdListSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(parse_serialized_errors(serializer))

            experiment_ids = serializer.validated_data["experiment_ids"]
            max_concurrent_rows = request.data.get("max_concurrent_rows", 10)
            organization = getattr(request, "organization", None) or request.user.organization

            experiments = ExperimentsTable.objects.filter(
                id__in=experiment_ids,
                dataset__organization=organization,
                deleted=False,
            )
            experiments.update(status=StatusType.QUEUED.value)

            from tfc.temporal.experiments import start_experiment_v2_workflow

            for experiment in experiments:
                try:
                    start_experiment_v2_workflow(
                        experiment_id=str(experiment.id),
                        max_concurrent_rows=max_concurrent_rows,
                    )
                    logger.info(f"Started V2 workflow for experiment {experiment.id}")
                except Exception as e:
                    logger.exception(
                        f"Failed V2 workflow for experiment {experiment.id}: {e}"
                    )

            return self._gm.success_response("Re-Run has started for the experiments.")

        except Exception as e:
            logger.exception(f"Error in re-run experiment: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_RE_RUN_EXP")
            )


class ExperimentRerunCellsV2View(APIView):
    """Rerun specific cells or columns in a V2 experiment.

    Accepts source_ids (EDT IDs for full column rerun) and/or
    cells ({source_id, row_id} pairs for individual cell rerun).
    Resets affected output cells and dependent eval cells to RUNNING,
    then starts a RerunCellsV2Workflow.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, experiment_id):
        try:
            serializer = ExperimentRerunCellsSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(parse_serialized_errors(serializer))

            organization = getattr(request, "organization", None) or request.user.organization
            experiment = ExperimentsTable.objects.filter(
                id=experiment_id,
                dataset__organization=organization,
                deleted=False,
            ).first()
            if not experiment:
                return self._gm.bad_request("Experiment not found.")

            if not experiment.snapshot_dataset_id:
                return self._gm.bad_request("Experiment has no snapshot dataset.")

            source_ids = serializer.validated_data.get("source_ids", [])
            cells = serializer.validated_data.get("cells", [])
            user_eval_metric_ids = serializer.validated_data.get(
                "user_eval_metric_ids", []
            )
            failed_only = serializer.validated_data.get("failed_only", False)

            snapshot_dataset_id = str(experiment.snapshot_dataset_id)

            # ── Cell-level rerun (column_id + row_id) ─────────────────
            if cells:
                from tfc.temporal.experiments import start_rerun_cells_v2_workflow

                column_ids = [cell["column_id"] for cell in cells]
                row_ids = list(set(str(cell["row_id"]) for cell in cells))

                columns = Column.objects.filter(id__in=column_ids, deleted=False)
                if not columns.exists():
                    return self._gm.bad_request("No valid columns found.")

                # Determine rerun type from column source
                col = columns.first()
                source = col.source

                if source == SourceChoices.EXPERIMENT.value:
                    # OUTPUT COLUMN → standard rerun (prompt + dependent evals)
                    edt_ids_set = set()
                    for c in columns:
                        if c.source_id:
                            edt_ids_set.add(str(c.source_id))

                    edts = ExperimentDatasetTable.objects.filter(
                        id__in=edt_ids_set, experiment=experiment
                    )
                    epc_ids = [
                        str(edt.prompt_config.id)
                        for edt in edts
                        if hasattr(edt, "prompt_config")
                        and edt.prompt_config is not None
                    ]
                    eac_ids = [
                        str(edt.agent_config.id)
                        for edt in edts
                        if hasattr(edt, "agent_config") and edt.agent_config is not None
                    ]

                    # Find output + dependent eval columns
                    output_col_ids = list(columns.values_list("id", flat=True))
                    eval_col_q = Q()
                    for edt_id in edt_ids_set:
                        eval_col_q |= Q(
                            source_id__startswith=str(edt_id),
                            source_id__contains="-sourceid-",
                        )
                    dep_eval_col_ids = (
                        list(
                            Column.objects.filter(
                                eval_col_q,
                                dataset_id=snapshot_dataset_id,
                                source__in=[
                                    SourceChoices.EXPERIMENT_EVALUATION.value,
                                    SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
                                ],
                                deleted=False,
                            ).values_list("id", flat=True)
                        )
                        if edt_ids_set
                        else []
                    )

                    all_col_ids = output_col_ids + dep_eval_col_ids

                    # Reset columns and cells
                    Column.objects.filter(id__in=all_col_ids).update(
                        status=StatusType.RUNNING.value
                    )
                    cell_filter = Q(
                        column_id__in=all_col_ids,
                        dataset_id=snapshot_dataset_id,
                        row_id__in=[cell["row_id"] for cell in cells],
                    )
                    if failed_only:
                        cell_filter &= Q(status=CellStatus.ERROR.value)
                    Cell.objects.filter(cell_filter).update(
                        status=CellStatus.RUNNING.value, value=""
                    )

                    experiment.status = StatusType.RUNNING.value
                    experiment.save(update_fields=["status"])

                    eval_metric_ids = [
                        str(m.id) for m in experiment.user_eval_template_ids.all()
                    ]

                    workflow_id = start_rerun_cells_v2_workflow(
                        experiment_id=str(experiment.id),
                        dataset_id=snapshot_dataset_id,
                        prompt_config_ids=epc_ids,
                        agent_config_ids=eac_ids,
                        row_ids=row_ids,
                        failed_only=failed_only,
                        eval_template_ids=eval_metric_ids,
                        max_concurrent_rows=request.data.get("max_concurrent_rows", 10),
                    )

                elif source in (
                    SourceChoices.EXPERIMENT_EVALUATION.value,
                    SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
                ):
                    # PER-EDT EVAL → eval-only rerun scoped to EDT
                    edt_ids_set = set()
                    metric_ids_set = set()
                    for c in columns:
                        if c.source_id and "-sourceid-" in c.source_id:
                            left, metric_id = c.source_id.split("-sourceid-")
                            # New format: {edt_id}-{col_id}-sourceid-{metric_id}
                            # EDT ID is always the first 36 chars (UUID)
                            edt_id = left[:36]
                            edt_ids_set.add(edt_id)
                            metric_ids_set.add(metric_id)

                    edt_id_strs = list(edt_ids_set)
                    metric_id_strs = list(metric_ids_set)

                    # Find all eval columns for these EDTs + metrics
                    eval_col_q = Q()
                    for eid in edt_id_strs:
                        for mid in metric_id_strs:
                            eval_col_q |= Q(
                                source_id__startswith=eid,
                                source_id__endswith=f"-sourceid-{mid}",
                            )
                    eval_column_ids = (
                        list(
                            Column.objects.filter(
                                eval_col_q,
                                source__in=[
                                    SourceChoices.EXPERIMENT_EVALUATION.value,
                                    SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
                                ],
                                dataset_id=snapshot_dataset_id,
                                deleted=False,
                            ).values_list("id", flat=True)
                        )
                        if edt_id_strs
                        else []
                    )

                    # Reset columns and cells
                    Column.objects.filter(id__in=eval_column_ids).update(
                        status=StatusType.RUNNING.value
                    )
                    cell_filter = Q(
                        column_id__in=eval_column_ids,
                        dataset_id=snapshot_dataset_id,
                        row_id__in=[cell["row_id"] for cell in cells],
                    )
                    if failed_only:
                        cell_filter &= Q(status=CellStatus.ERROR.value)
                    Cell.objects.filter(cell_filter).update(
                        status=CellStatus.RUNNING.value, value=""
                    )

                    experiment.status = StatusType.RUNNING.value
                    experiment.save(update_fields=["status"])

                    workflow_id = start_rerun_cells_v2_workflow(
                        experiment_id=str(experiment.id),
                        dataset_id=snapshot_dataset_id,
                        prompt_config_ids=[],
                        agent_config_ids=[],
                        row_ids=row_ids,
                        failed_only=failed_only,
                        eval_template_ids=metric_id_strs,
                        max_concurrent_rows=request.data.get("max_concurrent_rows", 10),
                        eval_only=True,
                        edt_ids=edt_id_strs,
                    )

                elif source in (
                    SourceChoices.EVALUATION.value,
                    SourceChoices.EVALUATION_TAGS.value,
                ):
                    # BASE EVAL → eval-only rerun scoped to rows
                    metric_ids_set = set()
                    for c in columns:
                        if c.source_id:
                            metric_ids_set.add(str(c.source_id))
                    metric_id_strs = list(metric_ids_set)

                    # Find base eval columns
                    eval_column_ids = list(
                        Column.objects.filter(
                            source_id__in=metric_id_strs,
                            source__in=[
                                SourceChoices.EVALUATION.value,
                                SourceChoices.EVALUATION_TAGS.value,
                            ],
                            dataset_id=snapshot_dataset_id,
                            deleted=False,
                        ).values_list("id", flat=True)
                    )

                    # Reset only the specific cells
                    Column.objects.filter(id__in=eval_column_ids).update(
                        status=StatusType.RUNNING.value
                    )
                    cell_filter = Q(
                        column_id__in=eval_column_ids,
                        dataset_id=snapshot_dataset_id,
                        row_id__in=[cell["row_id"] for cell in cells],
                    )
                    if failed_only:
                        cell_filter &= Q(status=CellStatus.ERROR.value)
                    Cell.objects.filter(cell_filter).update(
                        status=CellStatus.RUNNING.value, value=""
                    )

                    experiment.status = StatusType.RUNNING.value
                    experiment.save(update_fields=["status"])

                    workflow_id = start_rerun_cells_v2_workflow(
                        experiment_id=str(experiment.id),
                        dataset_id=snapshot_dataset_id,
                        prompt_config_ids=[],
                        agent_config_ids=[],
                        row_ids=row_ids,
                        failed_only=failed_only,
                        eval_template_ids=metric_id_strs,
                        max_concurrent_rows=request.data.get("max_concurrent_rows", 10),
                        eval_only=True,
                        base_eval_only=True,
                    )

                else:
                    return self._gm.bad_request(
                        f"Unsupported column source for rerun: {source}"
                    )

                return self._gm.success_response(
                    {
                        "message": "Cell rerun has started.",
                        "workflow_id": workflow_id,
                    },
                )

            # ── Eval-only rerun mode (bulk) ───────────────────────────
            if user_eval_metric_ids:
                metric_id_strs = [str(mid) for mid in user_eval_metric_ids]

                # Validate metric IDs belong to this experiment
                valid_metric_ids = set(
                    experiment.user_eval_template_ids.filter(
                        id__in=metric_id_strs
                    ).values_list("id", flat=True)
                )
                invalid = set(metric_id_strs) - {str(v) for v in valid_metric_ids}
                if invalid:
                    return self._gm.bad_request(
                        f"Eval metric IDs not found for this experiment: {invalid}"
                    )

                # Get EDT IDs — from cells if provided, otherwise all
                if cells:
                    edt_id_strs = list(set(str(cell["source_id"]) for cell in cells))
                else:
                    edt_ids = list(
                        ExperimentDatasetTable.objects.filter(
                            experiment=experiment,
                        ).values_list("id", flat=True)
                    )
                    edt_id_strs = [str(eid) for eid in edt_ids]

                # Per-EDT source_id is "{edt_id}-{col_id}-sourceid-{metric_id}",
                # so match by prefix+suffix rather than exact equality.
                per_edt_q = Q()
                for eid in edt_id_strs:
                    for mid in metric_id_strs:
                        per_edt_q |= Q(
                            source_id__startswith=eid,
                            source_id__endswith=f"-sourceid-{mid}",
                        )

                eval_col_q = (
                    per_edt_q
                    & Q(
                        source__in=[
                            SourceChoices.EXPERIMENT_EVALUATION.value,
                            SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
                        ]
                    )
                ) | Q(
                    source_id__in=metric_id_strs,
                    source__in=[
                        SourceChoices.EVALUATION.value,
                        SourceChoices.EVALUATION_TAGS.value,
                    ],
                )
                eval_columns = Column.objects.filter(
                    eval_col_q,
                    dataset_id=snapshot_dataset_id,
                    deleted=False,
                )
                eval_column_ids = list(eval_columns.values_list("id", flat=True))

                if not eval_column_ids:
                    return self._gm.bad_request(
                        "No eval columns found for the specified metrics."
                    )

                # Set columns to RUNNING
                Column.objects.filter(id__in=eval_column_ids).update(
                    status=StatusType.RUNNING.value
                )

                # Reset cells to RUNNING
                cell_filter = Q(
                    column_id__in=eval_column_ids,
                    dataset_id=snapshot_dataset_id,
                )
                if cells:
                    cell_row_ids = [cell["row_id"] for cell in cells]
                    cell_filter &= Q(row_id__in=cell_row_ids)
                if failed_only:
                    cell_filter &= Q(status=CellStatus.ERROR.value)

                # Collect row_ids from cells or failed_only filter
                row_ids = []
                if cells:
                    row_ids = list(set(str(cell["row_id"]) for cell in cells))
                elif failed_only:
                    row_ids = list(
                        Cell.objects.filter(cell_filter)
                        .values_list("row_id", flat=True)
                        .distinct()
                    )
                    row_ids = [str(rid) for rid in row_ids]

                Cell.objects.filter(cell_filter).update(
                    status=CellStatus.RUNNING.value,
                    value="",
                )

                # Set experiment to RUNNING
                experiment.status = StatusType.RUNNING.value
                experiment.save(update_fields=["status"])

                # Start eval-only workflow
                from tfc.temporal.experiments import start_rerun_cells_v2_workflow

                workflow_id = start_rerun_cells_v2_workflow(
                    experiment_id=str(experiment.id),
                    dataset_id=snapshot_dataset_id,
                    prompt_config_ids=[],
                    agent_config_ids=[],
                    row_ids=row_ids,
                    failed_only=failed_only,
                    eval_template_ids=metric_id_strs,
                    max_concurrent_rows=request.data.get("max_concurrent_rows", 10),
                    eval_only=True,
                    edt_ids=edt_id_strs if cells else [],
                )

                logger.info(
                    f"Started eval-only rerun workflow {workflow_id} "
                    f"for experiment {experiment.id}"
                )

                return self._gm.success_response(
                    {"message": "Eval rerun has started.", "workflow_id": workflow_id},
                )

            # ── Standard cell/column rerun mode ───────────────────────
            # Collect all EDT IDs from source_ids and cells
            all_edt_ids = set(str(sid) for sid in source_ids)
            # cell-level: map edt_id -> set of row_ids
            cell_row_map = defaultdict(set)
            for cell in cells:
                edt_id = str(cell["source_id"])
                all_edt_ids.add(edt_id)
                cell_row_map[edt_id].add(str(cell["row_id"]))

            # Fetch EDTs and determine EPC/EAC
            edts = ExperimentDatasetTable.objects.filter(
                id__in=all_edt_ids,
                experiment=experiment,
            ).select_related("prompt_config", "agent_config")

            found_edt_ids = set()
            epc_ids = []
            eac_ids = []
            for edt in edts:
                found_edt_ids.add(str(edt.id))
                try:
                    epc_ids.append(str(edt.prompt_config.id))
                except ExperimentPromptConfig.DoesNotExist:
                    pass
                try:
                    eac_ids.append(str(edt.agent_config.id))
                except ExperimentAgentConfig.DoesNotExist:
                    pass

            missing = all_edt_ids - found_edt_ids
            if missing:
                return self._gm.bad_request(
                    f"Source IDs not found for this experiment: {missing}"
                )

            if not epc_ids and not eac_ids:
                return self._gm.bad_request("No valid prompt or agent configs found.")

            # Build row_ids: union of all specific row_ids from cells
            # If there are source_ids without specific cells (full column reruns),
            # leave row_ids empty so setup activity processes all rows.
            row_ids = []
            has_full_column_reruns = bool(
                set(str(sid) for sid in source_ids) - set(cell_row_map.keys())
            )
            if not has_full_column_reruns and cell_row_map:
                for rids in cell_row_map.values():
                    row_ids.extend(rids)

            # Find output columns for affected EDTs
            output_columns = Column.objects.filter(
                source_id__in=[str(eid) for eid in all_edt_ids],
                source=SourceChoices.EXPERIMENT.value,
                dataset_id=snapshot_dataset_id,
            )
            output_column_ids = list(output_columns.values_list("id", flat=True))

            # Find dependent eval columns (source_id pattern: "{edt_id}-sourceid-{metric_id}")
            eval_col_q = Q()
            for edt_id_str in all_edt_ids:
                eval_col_q |= Q(source_id__startswith=f"{edt_id_str}-sourceid-")
            eval_columns = Column.objects.filter(
                eval_col_q,
                source__in=[
                    SourceChoices.EXPERIMENT_EVALUATION.value,
                    SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
                ],
                dataset_id=snapshot_dataset_id,
            )
            eval_column_ids = list(eval_columns.values_list("id", flat=True))

            all_column_ids = output_column_ids + eval_column_ids

            # Set columns to RUNNING
            if all_column_ids:
                Column.objects.filter(id__in=all_column_ids).update(
                    status=StatusType.RUNNING.value
                )

            # Set cells to RUNNING
            if all_column_ids:
                cell_filter = Q(
                    column_id__in=all_column_ids,
                    dataset_id=snapshot_dataset_id,
                )
                if row_ids:
                    cell_filter &= Q(row_id__in=row_ids)
                if failed_only:
                    cell_filter &= Q(status=CellStatus.ERROR.value)

                Cell.objects.filter(cell_filter).update(
                    status=CellStatus.RUNNING.value,
                    value="",
                )

            # Set experiment to RUNNING
            experiment.status = StatusType.RUNNING.value
            experiment.save(update_fields=["status"])

            # Get eval template IDs for the workflow
            eval_template_ids = [
                str(eid)
                for eid in experiment.user_eval_template_ids.values_list(
                    "id", flat=True
                )
            ]

            # Start workflow
            from tfc.temporal.experiments import start_rerun_cells_v2_workflow

            workflow_id = start_rerun_cells_v2_workflow(
                experiment_id=str(experiment.id),
                dataset_id=snapshot_dataset_id,
                prompt_config_ids=epc_ids,
                agent_config_ids=eac_ids,
                row_ids=list(set(row_ids)),
                failed_only=failed_only,
                eval_template_ids=eval_template_ids,
                max_concurrent_rows=request.data.get("max_concurrent_rows", 10),
            )

            logger.info(
                f"Started rerun-cells workflow {workflow_id} for experiment {experiment.id}"
            )

            return self._gm.success_response(
                {"message": "Cell rerun has started.", "workflow_id": workflow_id},
            )

        except Exception as e:
            logger.exception(f"Error in rerun-cells: {e}")
            return self._gm.internal_server_error_response(str(e))


class ExperimentStopV2View(APIView):
    """Stop a running V2 experiment.

    Cancels all Temporal workflows (main + reruns). DB cleanup (marking
    RUNNING cells as ERROR, columns/EDTs as FAILED, experiment as CANCELLED)
    is handled by each workflow's CancelledError handler via the
    stop_experiment_cleanup_activity.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, experiment_id):
        try:
            organization = getattr(request, "organization", None) or request.user.organization
            experiment = ExperimentsTable.objects.filter(
                id=experiment_id,
                dataset__organization=organization,
                deleted=False,
            ).first()

            if not experiment:
                return self._gm.not_found("Experiment not found")

            if experiment.status not in (
                StatusType.RUNNING.value,
                StatusType.QUEUED.value,
            ):
                return self._gm.bad_request(
                    f"Experiment is not running. Current status: {experiment.status}"
                )

            # Immediately mark experiment as CANCELLED so the UI reflects
            # the stopped state without waiting for Temporal cleanup.
            experiment.status = StatusType.CANCELLED.value
            experiment.save(update_fields=["status"])

            from tfc.temporal.experiments import cancel_all_experiment_workflows

            cancel_result = cancel_all_experiment_workflows(str(experiment.id))

            logger.info(
                f"Stop experiment {experiment.id}: "
                f"main={cancel_result['main_cancelled']}, "
                f"reruns={cancel_result['rerun_cancelled']}"
            )

            return self._gm.success_response(
                {
                    "message": "Experiment stopped.",
                    "experiment_id": str(experiment.id),
                    "workflows_cancelled": {
                        "main": cancel_result["main_cancelled"],
                        "reruns": cancel_result["rerun_cancelled"],
                    },
                }
            )

        except Exception as e:
            logger.exception(f"Error stopping experiment {experiment_id}: {e}")
            return self._gm.internal_server_error_response(
                "Unable to stop experiment. Please try again."
            )


class ExperimentNameSuggestionView(APIView):
    """Generate a suggested experiment name for a dataset.

    Format: DS_{dataset_name}_exp_{yy/mm/dd}[_vN]
    - Versioned: _v2, _v3, etc. when multiple experiments exist for the
      same dataset on the same day.
    - Max 98 characters. Dataset name is cropped if needed (min 4 chars).
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    MAX_LENGTH = 98
    MIN_NAME_LENGTH = 4

    def _build_suggestion(self, ds_name, date_str, suffix):
        """Build experiment name, cropping ds_name to fit within MAX_LENGTH."""
        fixed_length = len(f"DS__exp_{date_str}{suffix}")
        available = self.MAX_LENGTH - fixed_length
        if len(ds_name) > available:
            ds_name = ds_name[: max(available, self.MIN_NAME_LENGTH)]
        return f"DS_{ds_name}_exp_{date_str}{suffix}"

    def get(self, request, dataset_id):
        try:
            organization = getattr(request, "organization", None) or request.user.organization
            dataset = Dataset.objects.filter(
                id=dataset_id,
                organization=organization,
                deleted=False,
            ).first()

            if not dataset:
                return self._gm.not_found("Dataset not found")

            today = datetime.now(timezone.utc)
            date_str = today.strftime("%y/%m/%d")

            # Count today's experiments as starting version estimate
            today_count = ExperimentsTable.objects.filter(
                dataset=dataset,
                deleted=False,
                created_at__date=today.date(),
            ).count()

            # Collect all existing experiment names for collision check
            existing_names = set(
                ExperimentsTable.objects.filter(
                    dataset=dataset,
                    dataset__organization=organization,
                ).values_list("name", flat=True)
            )

            # Start from count-based version, increment on collision
            version = today_count + 1 if today_count >= 1 else 1
            suffix = f"_v{version}" if version >= 2 else ""
            suggested_name = self._build_suggestion(dataset.name, date_str, suffix)
            while suggested_name in existing_names and version <= 1000:
                version += 1
                suffix = f"_v{version}"
                suggested_name = self._build_suggestion(dataset.name, date_str, suffix)

            # Fallback: append short unique ID if still colliding
            if suggested_name in existing_names:
                uid = uuid.uuid4().hex[:6]
                suffix = f"_{uid}"
                suggested_name = self._build_suggestion(dataset.name, date_str, suffix)

            return self._gm.success_response({"suggested_name": suggested_name})

        except Exception as e:
            logger.exception(f"Error generating experiment name: {e}")
            return self._gm.internal_server_error_response(str(e))


class ExperimentNameValidationView(APIView):
    """Validate that an experiment name is unique within a dataset."""

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = getattr(request, "organization", None) or request.user.organization
        dataset_id = request.query_params.get("dataset_id")
        name = request.query_params.get("name", "").strip()

        if not dataset_id or not name:
            return self._gm.bad_request("dataset_id and name are required")

        dataset = Dataset.objects.filter(
            id=dataset_id, organization=organization, deleted=False
        ).first()
        if not dataset:
            return self._gm.not_found("Dataset not found")

        if experiment_name_exists(name, dataset):
            return self._gm.success_response(
                {"is_valid": False, "message": "Experiment name already exists"}
            )
        return self._gm.success_response({"is_valid": True})
