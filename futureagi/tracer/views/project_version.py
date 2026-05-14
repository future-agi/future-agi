import ast
import io
import json

import pandas as pd
import structlog
from django.db import models
from django.db.models import (
    Avg,
    Case,
    Count,
    Exists,
    F,
    FloatField,
    IntegerField,
    JSONField,
    OuterRef,
    Q,
    StdDev,
    Subquery,
    Value,
    When,
)
from django.db.models.fields import CharField
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Coalesce, Concat, JSONObject, Round
from django.http import FileResponse
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from model_hub.serializers.develop_annotations import (
    AnnotationProjectVersionMapperSerializer,
)
from tfc.routers import uses_db
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.error_codes import get_error_message, get_specific_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.parse_errors import parse_serialized_errors
from tracer.db_routing import DATABASE_FOR_PROJECT_VERSION_IDS
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion, ProjectVersionWinner
from tracer.models.trace import Trace
from tracer.serializers.project import ProjectVersionExportSerializer
from tracer.serializers.project_version import ProjectVersionSerializer
from tracer.utils.filters import ColType, FilterEngine
from tracer.utils.helper import (
    get_default_project_version_config,
    get_default_trace_config,
    update_column_config_based_on_eval_config,
    update_run_column_config_based_on_annotations,
)
from tracer.utils.sql_queries import SQL_query_handler

logger = structlog.get_logger(__name__)

## TODO: need a major revamp. queries are wrong.


class ProjectVersionView(BaseModelViewSetMixin, ModelViewSet):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    serializer_class = ProjectVersionSerializer

    def get_queryset(self):
        project_version_id = self.kwargs.get("pk")

        # Get base queryset with automatic filtering from mixin
        query_Set = super().get_queryset()

        if project_version_id:
            return query_Set.filter(id=project_version_id)

        project_id = self.request.query_params.get("project_id")
        search_name = self.request.query_params.get("search_name", "")
        deleted = self.request.query_params.get("deleted", False)

        if search_name:
            query_Set = query_Set.filter(name__icontains=search_name)

        if project_id:
            query_Set = query_Set.filter(project_id=project_id)

        if deleted:
            query_Set = query_Set.filter(deleted=deleted)

        return query_Set

    def create(self, request, *args, **kwargs):
        """
        Create a new project version.
        """
        try:
            serializer = self.get_serializer(data=request.data)

            if serializer.is_valid():
                project = serializer.validated_data.get("project")

                # Get the count of existing versions for this project
                version_num = ProjectVersion.objects.filter(project=project).count() + 1
                version = f"v{version_num}"

                serializer.validated_data["version"] = version
                serializer.validated_data["config"] = get_default_trace_config()
                project_version = serializer.save()

                return self._gm.success_response(
                    {
                        "project_version_id": str(project_version.id),
                        "version": project_version.version,
                    }
                )
            return self._gm.bad_request(parse_serialized_errors(serializer))
        except Exception as e:
            logger.exception(f"Error in creating project version: {str(e)}")
            return self._gm.bad_request(
                get_error_message("ERROR_CREATING_PROJECT_VERSION")
            )

    @action(detail=False, methods=["post"])
    def project_version_winner(self, request, *args, **kwargs):
        try:
            project_id = self.request.data.get("project_id")
            req_config = self.request.data.get("config")
            parsed_config = req_config
            if parsed_config is None:
                return self._gm.bad_request(get_error_message("CONFIG_MISSING"))

            project = Project.objects.get(
                id=project_id,
                organization=getattr(self.request, "organization", None)
                or self.request.user.organization,
            )
            result = {}

            # Build the base query with all necessary annotations
            base_query = (
                ObservationSpan.objects.filter(project_id=project_id)
                .values("project_version_id")
                .annotate(
                    avg_latency_ms=Coalesce(
                        Round(
                            Avg(
                                Case(
                                    When(parent_span_id__isnull=True, then="latency_ms")
                                )
                            ),
                            2,
                        ),
                        0.0,
                    ),
                    avg_cost=Coalesce(
                        Round(Avg("cost", output_field=FloatField()), 8),
                        0.0,
                    ),
                )
            )

            # Create a subquery for each eval config ID from project config
            project_config = project.config or get_default_project_version_config()
            # Add annotations for each eval metric dynamically

            # Get all eval configs from the project version
            eval_configs = CustomEvalConfig.objects.filter(
                id__in=EvalLogger.objects.filter(
                    observation_span__project_id=project_id
                )
                .values("custom_eval_config_id")
                .distinct(),
                deleted=False,
            ).select_related("eval_template")

            # Add annotations for each eval metric dynamically
            for config in eval_configs:
                choices = (
                    config.eval_template.choices
                    if config.eval_template.choices
                    else None
                )

                metric_subquery = (
                    EvalLogger.objects.filter(
                        trace__project_version_id=OuterRef("project_version_id"),
                        custom_eval_config_id=config.id,
                    )
                    .exclude(Q(output_str="ERROR") | Q(error=True))
                    .values("custom_eval_config_id")
                    .annotate(
                        float_score=Round(Avg("output_float") * 100, 2),
                        bool_score=Round(
                            Avg(
                                Case(
                                    When(output_bool=True, then=100.0),
                                    When(output_bool=False, then=0.0),
                                    default=None,
                                    output_field=FloatField(),
                                )
                            ),
                            2,
                        ),
                        str_list_score=JSONObject(
                            **{
                                f"{value}": JSONObject(
                                    score=Round(
                                        100.0
                                        * Count(
                                            Case(
                                                When(
                                                    output_str_list__contains=[value],
                                                    then=1,
                                                ),
                                                default=None,
                                                output_field=IntegerField(),
                                            )
                                        )
                                        / Count("output_str_list"),
                                        2,
                                    )
                                )
                                for value in choices or []
                            }
                        ),
                    )
                    .values("float_score", "bool_score", "str_list_score")[:1]
                )

                base_query = base_query.annotate(
                    **{
                        f"metric_{config.id}": Case(
                            When(
                                Exists(
                                    EvalLogger.objects.filter(
                                        trace__project_version_id=OuterRef(
                                            "project_version_id"
                                        ),
                                        custom_eval_config_id=config.id,
                                        output_float__isnull=False,
                                    )
                                ),
                                then=JSONObject(
                                    score=Subquery(
                                        metric_subquery.values("float_score")
                                    )
                                ),
                            ),
                            When(
                                Exists(
                                    EvalLogger.objects.filter(
                                        trace__project_version_id=OuterRef(
                                            "project_version_id"
                                        ),
                                        custom_eval_config_id=config.id,
                                        output_bool__in=[True, False],
                                    )
                                ),
                                then=JSONObject(
                                    score=Subquery(metric_subquery.values("bool_score"))
                                ),
                            ),
                            When(
                                Exists(
                                    EvalLogger.objects.filter(
                                        trace__project_version_id=OuterRef(
                                            "project_version_id"
                                        ),
                                        custom_eval_config_id=config.id,
                                        output_str_list__isnull=False,
                                    )
                                ),
                                then=Subquery(metric_subquery.values("str_list_score")),
                            ),
                            default=None,
                            output_field=JSONField(),
                        )
                    }
                )

            for obj in base_query:
                sum = 0
                for key, value in parsed_config.items():
                    if key == "avg_latency_ms":
                        sum -= obj["avg_latency_ms"] * value
                    elif key == "avg_cost":
                        sum += obj["avg_cost"] * value
                    else:
                        if "**" in key:
                            key_parts = key.split("**")
                            eval_id, choice = key_parts
                            req_obj = obj.get(f"metric_{eval_id}")

                            if (
                                req_obj is not None
                                and isinstance(req_obj, dict)
                                and choice in req_obj
                            ):
                                sum += req_obj[choice]["score"] * value
                        else:
                            req_obj = obj.get(f"metric_{key}")
                            if (
                                req_obj is not None
                                and isinstance(req_obj, dict)
                                and "score" in req_obj
                            ):
                                sum += req_obj["score"] * value

                # Update the project version with the calculated sum
                project_version = ProjectVersion.objects.get(
                    id=obj["project_version_id"]
                )
                project_version.avg_eval_score = sum
                project_version.save(update_fields=["avg_eval_score"])
                result[project_version.id] = sum

            # Create dict with project version scores and ranks
            version_scores = {
                str(version): {
                    "score": score,
                    "rank": None,  # Will be populated after sorting
                }
                for version, score in result.items()
            }

            # Sort versions by score and assign ranks
            sorted_versions = sorted(
                version_scores.items(), key=lambda x: x[1]["score"], reverse=True
            )
            current_rank = 1
            for i, (version_id, data) in enumerate(sorted_versions):
                if i > 0 and data["score"] == sorted_versions[i - 1][1]["score"]:
                    version_scores[version_id]["rank"] = version_scores[
                        sorted_versions[i - 1][0]
                    ]["rank"]
                else:
                    version_scores[version_id]["rank"] = current_rank
                    current_rank += 1

            winner_id = sorted_versions[0][0]
            winner = ProjectVersion.objects.get(
                id=winner_id,
                project__organization=getattr(self.request, "organization", None)
                or self.request.user.organization,
            )

            try:
                project_version_winner_qs = ProjectVersionWinner.objects.get(
                    project=project, eval_config=parsed_config
                )
                project_version_winner_qs.winner_version = winner
                project_version_winner_qs.version_mapper = version_scores
                project_version_winner_qs.save()
            except ProjectVersionWinner.DoesNotExist:
                ProjectVersionWinner.objects.create(
                    project=project,
                    eval_config=parsed_config,
                    winner_version=winner,
                    version_mapper=version_scores,
                )

            # updating rank
            # Update rank column config
            for col in project_config:
                if col.get("id") == "rank":
                    col["is_visible"] = True
                    break
            project.config = project_config
            project.save(update_fields=["config"])

            return self._gm.success_response(
                {"message": "Winner Eval Id is", "project_version_winner": winner.id}
            )

        except Exception as e:
            logger.exception(f"Error in updating project version winner: {str(e)}")

            return self._gm.bad_request(
                f"Error updating Project Version Winner: {get_error_message('ERROR_UPDATING_PROJECT_VERSION_WINNNER')}"
            )

    @action(detail=False, methods=["post"])
    def get_export_data(self, request, *args, **kwargs):
        try:
            serializer = ProjectVersionExportSerializer(data=request.data)
            if serializer.is_valid():
                validated_data = serializer.data
                project_id = validated_data["project_id"]
                project_version_ids = validated_data["runs_ids"]

            else:
                return self._gm.bad_request(serializer.errors)

            request_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            # Build the base query with all necessary annotations
            base_query = ObservationSpan.objects.filter(
                project_id=project_id,
            )

            if project_version_ids:
                base_query = base_query.filter(
                    project_version_id__in=project_version_ids
                )

            base_query = base_query.values("project_version_id").annotate(
                run_name=Concat(
                    F("project_version__name"),
                    Value(" - "),
                    F("project_version__version"),
                    output_field=CharField(),
                ),
                version=F("project_version__version"),
                avg_eval_score=F("project_version__avg_eval_score"),
                row_avg_latency_ms=Coalesce(
                    Round(
                        Avg(Case(When(parent_span_id__isnull=True, then="latency_ms"))),
                        2,
                    ),
                    0.0,
                ),
                row_avg_cost=Coalesce(
                    Round(Avg("cost", output_field=FloatField()), 6),
                    0.0,
                ),
            )

            # Create a subquery for each eval config ID from project config
            try:
                project = Project.objects.get(
                    id=project_id,
                    organization=request_organization,
                )
            except Project.DoesNotExist:
                return self._gm.bad_request(get_error_message("PROJECT_NOT_FOUND"))

            # Get all eval configs from the project version
            eval_configs = CustomEvalConfig.objects.filter(
                id__in=EvalLogger.objects.filter(
                    observation_span__project_id=project_id
                )
                .values("custom_eval_config_id")
                .distinct(),
                deleted=False,
            ).select_related("eval_template")

            # Add annotations for each eval metric dynamically
            for config in eval_configs:
                choices = (
                    config.eval_template.choices
                    if config.eval_template.choices
                    else None
                )

                metric_subquery = (
                    EvalLogger.objects.filter(
                        trace__project_version_id=OuterRef("project_version_id"),
                        custom_eval_config_id=config.id,
                    )
                    .exclude(Q(output_str="ERROR") | Q(error=True))
                    .values("custom_eval_config_id")
                    .annotate(
                        float_score=Round(Avg("output_float") * 100, 2),
                        bool_score=Round(
                            Avg(
                                Case(
                                    When(output_bool=True, then=100),
                                    When(output_bool=False, then=0),
                                    default=None,
                                    output_field=FloatField(),
                                )
                            ),
                            2,
                        ),
                        str_list_score=JSONObject(
                            **{
                                f"{value}": JSONObject(
                                    score=Round(
                                        100.0
                                        * Count(
                                            Case(
                                                When(
                                                    output_str_list__contains=[value],
                                                    then=1,
                                                ),
                                                default=None,
                                                output_field=IntegerField(),
                                            )
                                        )
                                        / Count("output_str_list"),
                                        2,
                                    )
                                )
                                for value in choices or []
                            }
                        ),
                    )
                    .values("float_score", "bool_score", "str_list_score")[:1]
                )

                base_query = base_query.annotate(
                    **{
                        f"metric_{config.id}": Case(
                            When(
                                Exists(
                                    EvalLogger.objects.filter(
                                        trace__project_version_id=OuterRef(
                                            "project_version_id"
                                        ),
                                        custom_eval_config_id=config.id,
                                        output_float__isnull=False,
                                    )
                                ),
                                then=JSONObject(
                                    score=Subquery(
                                        metric_subquery.values("float_score")
                                    )
                                ),
                            ),
                            When(
                                Exists(
                                    EvalLogger.objects.filter(
                                        trace__project_version_id=OuterRef(
                                            "project_version_id"
                                        ),
                                        custom_eval_config_id=config.id,
                                        output_bool__in=[True, False],
                                    )
                                ),
                                then=JSONObject(
                                    score=Subquery(metric_subquery.values("bool_score"))
                                ),
                            ),
                            When(
                                Exists(
                                    EvalLogger.objects.filter(
                                        trace__project_version_id=OuterRef(
                                            "project_version_id"
                                        ),
                                        custom_eval_config_id=config.id,
                                        output_str_list__isnull=False,
                                    )
                                ),
                                then=Subquery(metric_subquery.values("str_list_score")),
                            ),
                            default=None,
                            output_field=JSONField(),
                        )
                    }
                )

            # Add Avg Annotations
            annotation_labels = AnnotationsLabels.objects.filter(project__id=project_id)
            annotation_labels = annotation_labels.exclude(
                type=AnnotationTypeChoices.TEXT.value
            )

            parsed_choices = []
            for label in annotation_labels:
                if label.type == AnnotationTypeChoices.CATEGORICAL.value:
                    parsed_choices = [
                        option["label"] for option in label.settings["options"]
                    ]

                score_base_filter = {
                    "observation_span__project_version_id": OuterRef(
                        "project_version_id"
                    ),
                    "label_id": label.id,
                    "organization": request_organization,
                    "deleted": False,
                }

                # Project version annotation rollup. Reads from the unified
                # ``Score`` model — the JSON paths (``value__value``,
                # ``value__selected__contains``) match Score.value's schema.
                # Pre-deprecation this queried ``TraceAnnotation`` (whose
                # value lives in typed columns ``annotation_value_float``
                # etc.), so those JSON paths returned no rows. Swapping to
                # Score makes the query correct.
                metric_subquery = (
                    Score.objects.filter(
                        observation_span__project_version_id=OuterRef(
                            "project_version_id"
                        ),
                        label_id=label.id,
                        observation_span__project__organization=request_organization,
                        deleted=False,
                    )
                    .values("label_id")
                    .annotate(
                        # Score stores numeric labels as ``{"value": <float>}``
                        # but STAR labels as ``{"rating": <float>}`` (see
                        # tracer/views/annotation.py:_to_score_value).
                        # Coalesce both so star ratings show up in rollups.
                        annotation_float_score=Round(
                            Avg(
                                Coalesce(
                                    Cast(
                                        KeyTextTransform("value", "value"),
                                        FloatField(),
                                    ),
                                    Cast(
                                        KeyTextTransform("rating", "value"),
                                        FloatField(),
                                    ),
                                )
                            ),
                            2,
                        ),
                        annotation_bool_score=Round(
                            Avg(
                                Case(
                                    When(value__value="up", then=100),
                                    When(value__value="down", then=0),
                                    default=None,
                                    output_field=FloatField(),
                                )
                            ),
                            2,
                        ),
                        annotation_str_list_score=JSONObject(
                            **{
                                f"{value}": JSONObject(
                                    score=Round(
                                        100.0
                                        * Count(
                                            Case(
                                                When(
                                                    value__selected__contains=[value],
                                                    then=1,
                                                ),
                                                default=None,
                                                output_field=IntegerField(),
                                            )
                                        )
                                        / Count(
                                            "id",
                                            filter=~Q(value__selected__isnull=True),
                                        ),
                                        2,
                                    )
                                )
                                for value in parsed_choices or []
                            }
                        ),
                    )
                    .values(
                        "annotation_float_score",
                        "annotation_bool_score",
                        "annotation_str_list_score",
                    )[:1]
                )

                base_query = base_query.annotate(
                    **{
                        f"annotation_{label.id}": Case(
                            When(
                                Exists(
                                    Score.objects.filter(
                                        **score_base_filter,
                                    )
                                    # Numeric stores ``{value: float}``; STAR
                                    # stores ``{rating: float}``. Existence
                                    # check accepts either path so star
                                    # ratings aren't filtered out.
                                    .filter(
                                        Q(value__value__isnull=False)
                                        | Q(value__rating__isnull=False)
                                    )
                                    .filter(
                                        label__type__in=[
                                            AnnotationTypeChoices.NUMERIC.value,
                                            AnnotationTypeChoices.STAR.value,
                                        ],
                                    )
                                ),
                                then=JSONObject(
                                    score=Subquery(
                                        metric_subquery.values("annotation_float_score")
                                    )
                                ),
                            ),
                            When(
                                Exists(
                                    Score.objects.filter(
                                        **score_base_filter,
                                    )
                                    .exclude(value__value__isnull=True)
                                    .filter(
                                        label__type=AnnotationTypeChoices.THUMBS_UP_DOWN.value,
                                    )
                                ),
                                then=JSONObject(
                                    score=Subquery(
                                        metric_subquery.values("annotation_bool_score")
                                    )
                                ),
                            ),
                            When(
                                Exists(
                                    Score.objects.filter(
                                        **score_base_filter,
                                    ).exclude(value__selected__isnull=True)
                                ),
                                then=Subquery(
                                    metric_subquery.values("annotation_str_list_score")
                                ),
                            ),
                            default=None,
                            output_field=JSONField(),
                        )
                    }
                )

            # Apply filters
            filter_params = self.request.data.get("filters", [])
            if filter_params:
                combined_filter_conditions = Q()

                # System metrics filters
                system_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_system_metrics(filter_params)
                )
                if system_filter_conditions.children:
                    combined_filter_conditions &= system_filter_conditions

                # Separate annotation filters from eval filters since
                # annotations are JSON objects
                annotation_col_types = {"ANNOTATION"}
                annotation_column_ids = {"my_annotations", "annotator"}
                non_annotation_filters = [
                    f
                    for f in filter_params
                    if f.get("col_type") not in annotation_col_types
                    and (f.get("column_id") or f.get("columnId"))
                    not in annotation_column_ids
                ]

                # Eval metric filters (excluding annotation filters)
                eval_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_non_system_metrics(
                        non_annotation_filters
                    )
                )
                if eval_filter_conditions.children:
                    combined_filter_conditions &= eval_filter_conditions

                # Annotation filters (score, annotator, my_annotations)
                annotation_filter_conditions, extra_annotations = (
                    FilterEngine.get_filter_conditions_for_voice_call_annotations(
                        filter_params,
                        user_id=request.user.id,
                        span_filter_kwargs={
                            "observation_span__project_version_id": OuterRef(
                                "project_version_id"
                            )
                        },
                    )
                )
                if extra_annotations:
                    base_query = base_query.annotate(**extra_annotations)
                if annotation_filter_conditions.children:
                    combined_filter_conditions &= annotation_filter_conditions

                # Apply combined filters
                if combined_filter_conditions.children:
                    base_query = base_query.filter(combined_filter_conditions)

            # Apply sorting
            sort_params = self.request.data.get("sort_params", [])
            if sort_params:
                sort_conditions = []
                for sort_param in sort_params:
                    column_id = str(sort_param.get("column_id"))
                    direction = sort_param.get("direction", "asc")
                    col_type = sort_param.get("col_type", ColType.EVAL_METRIC.value)

                    if column_id in [
                        "avg_latency",
                        "avg_cost",
                        "run_name",
                        "avgLatency",
                        "avgCost",
                        "runName",
                    ]:
                        map = {
                            "avg_latency": "row_avg_latency_ms",
                            "avg_cost": "row_avg_cost",
                            "run_name": "run_name",
                            "avgLatency": "row_avg_latency_ms",
                            "avgCost": "row_avg_cost",
                            "runName": "run_name",
                        }
                        sort_field = (
                            f"{'-' if direction == 'desc' else ''}{map[column_id]}"
                        )
                        sort_conditions.append(sort_field)
                        logger.debug(f"System metric sort field: {sort_field}")

                    elif column_id is not None and column_id != "rank":
                        if "**" in column_id:
                            # Handle metric choice sorting (e.g., metric_123**choice)
                            parts = column_id.split("**")
                            if len(parts) == 2:
                                metric_id, choice = parts
                                if col_type == ColType.EVAL_METRIC.value:
                                    metric_column_id = (
                                        f"metric_{metric_id}__{choice}__score"
                                    )
                                elif col_type == ColType.ANNOTATION_RUNS.value:
                                    metric_column_id = (
                                        f"annotation_{metric_id}__{choice}__score"
                                    )
                                else:
                                    continue

                                if metric_column_id:
                                    sort_field = f"{'-' if direction == 'desc' else ''}{metric_column_id}"
                                    sort_conditions.append(sort_field)
                                    logger.debug(
                                        f"Metric choice sort field: {sort_field}"
                                    )
                            continue

                        metric_column_id = None
                        if col_type == ColType.EVAL_METRIC.value:
                            metric_column_id = f"metric_{column_id}__score"
                        elif col_type == ColType.ANNOTATION_RUNS.value:
                            metric_column_id = f"annotation_{column_id}__score"
                        elif col_type == ColType.ANNOTATION.value:
                            metric_column_id = f"annotation_{column_id}"

                        if metric_column_id:
                            if direction == "desc":
                                sort_field = f"{metric_column_id}"
                            else:
                                sort_field = f"-{metric_column_id}"
                            sort_conditions.append(sort_field)
                            logger.debug(f"Metric sort field: {sort_field}")

                if sort_conditions:
                    logger.debug(f"Final sort conditions: {sort_conditions}")
                    base_query = base_query.order_by(*sort_conditions)

            # Get winner config
            winner_config = {}
            project_version_winner = (
                ProjectVersionWinner.objects.filter(project=project)
                .order_by("-created_at")
                .first()
            )
            if project_version_winner:
                winner_config = project_version_winner.version_mapper

            # Apply pagination
            page_number = int(self.request.query_params.get("page_number", 0))
            page_size = int(self.request.query_params.get("page_size", 30))
            start = page_number * page_size
            end = start + page_size

            # Get total count and paginated results
            base_query.count()

            # # Log all available columns for sorting
            # if results := base_query.order_by('project_version_id').first():
            #     available_columns = list(results.keys())
            #     logger.info(f"Available columns for sorting in base_query: {available_columns}")

            #     # Log column values as well
            #     logger.info("Sample column values from first result:")
            #     for column, value in results.items():
            #         logger.info(f"  {column}: {value} (type: {type(value).__name__})")

            #     # Also log eval config IDs and annotation label IDs for reference
            #     eval_config_ids = [config.id for config in eval_configs]
            #     annotation_label_ids = [label.id for label in annotation_labels]
            #     logger.info(f"Eval config IDs (use as metric_<id>): {eval_config_ids}")
            #     logger.info(f"Annotation label IDs (use as annotation_<id>): {annotation_label_ids}")
            # else:
            #     logger.info("No results found in base_query to show available columns")

            results = base_query[start:end]

            # Process results into final format
            table_data = []
            for result in results:
                version_id = str(result["project_version_id"])
                row = {
                    "id": version_id,
                    "rank": winner_config.get(version_id, {}).get("rank", 0),
                    "avg_cost": result["row_avg_cost"],
                    "avg_latency": result["row_avg_latency_ms"],
                    "run_name": result["run_name"],
                }

                # Add eval metrics from annotated fields
                for config in eval_configs:
                    data = result.get(f"metric_{config.id}")

                    if data and "score" in data:
                        row[str(config.name)] = data["score"]
                    elif data:
                        for key, value in data.items():
                            row[str(config.name) + " ( " + key + " )"] = value.get(
                                "score"
                            )

                for label in annotation_labels:
                    data = result.get(f"annotation_{label.id}", None)
                    if data and "score" in data:
                        row[str(label.name)] = data["score"]
                    elif data:
                        for key, value in data.items():
                            row[str(label.name) + " ( " + key + " )"] = value["score"]

                table_data.append(row)

            # Apply filters and sorting for rank column
            if filter_params:
                for filter_param in filter_params:
                    if filter_param.get("column_id") == "rank":
                        filter_engine = FilterEngine(table_data)
                        table_data = filter_engine.apply_filters([filter_param])

            if sort_params:
                for sort_param in sort_params:
                    column_id = str(sort_param.get("column_id"))
                    direction = sort_param.get("direction", "asc")

                    if column_id == "rank":
                        table_data = sorted(
                            table_data,
                            key=lambda x: x.get("rank", 0),
                            reverse=(direction == "desc"),
                        )

            df = pd.DataFrame(table_data)

            # Convert to CSV buffer
            buffer = io.BytesIO()
            df.to_csv(buffer, index=False, encoding="utf-8")
            buffer.seek(0)

            # Create the response with the file
            filename = f"{project.name or 'project'}.csv"
            response = FileResponse(
                buffer, as_attachment=True, filename=filename, content_type="text/csv"
            )

            return response

        except Exception as e:
            logger.exception(f"Error in fetching the export data: {str(e)}")
            return self._gm.bad_request(
                f"Error getting export data: {get_error_message('FAILED_TO_GET_EXPORT_DATA')}"
            )

    @action(detail=False, methods=["post"])
    def delete_runs(self, request, *args, **kwargs):
        project_version_ids = self.request.data.get("ids", [])

        try:
            # Bulk update deleted status and timestamp for all specified IDs
            project_versions = ProjectVersion.objects.filter(id__in=project_version_ids)
            for project_version in project_versions:
                # Soft delete related models
                project_version.traces.update(deleted=True, deleted_at=timezone.now())
                project_version.winner_version.update(
                    deleted=True, deleted_at=timezone.now()
                )
                project_version.observation_spans.update(
                    deleted=True, deleted_at=timezone.now()
                )

                # Mark the project version as deleted
                project_version.deleted = True
                project_version.deleted_at = timezone.now()
                project_version.save()

            return self._gm.success_response(
                {
                    "message": "Successfully deleted project versions",
                    "deleted_ids": project_version_ids,
                }
            )
        except Exception as e:
            logger.exception(f"Error in deleting project version: {str(e)}")

            return self._gm.bad_request(
                f"Error deleting project versions: {get_error_message('ERROR_DELETING_PROJECT_VERSION')}"
            )

    @action(detail=False, methods=["post"])
    def update_project_version_config(self, request, *args, **kwargs):
        try:
            project_version_id = self.request.data.get("project_version_id")
            visibility = self.request.data.get("visibility", {})
            try:
                project_version = ProjectVersion.objects.get(
                    id=project_version_id,
                    project__organization=getattr(self.request, "organization", None)
                    or self.request.user.organization,
                )
            except ProjectVersion.DoesNotExist:
                return self._gm.bad_request("Project version not found")

            config = project_version.config

            for key, value in visibility.items():
                config_entry = next(
                    (item for item in config if item.get("id") == key), None
                )
                if config_entry:
                    config_entry["is_visible"] = value

            project_version.config = config
            project_version.save()

            return self._gm.success_response({"project_version_id": project_version.id})
        except Exception as e:
            logger.exception(f"Error in updating project version config: {str(e)}")

            return self._gm.bad_request(
                f"Error updating project version config: {get_error_message('ERROR_UPDATING_PROJECT_VERSION_CONFIG')}"
            )

    @action(detail=False, methods=["get"])
    @uses_db(
        DATABASE_FOR_PROJECT_VERSION_IDS,
        feature_key="feature:project_version_ids",
    )
    def get_project_version_ids(self, request, *args, **kwargs):
        try:
            # KNOWN PERF BUG (independent of routing): this paginates in
            # memory after serializing the entire queryset. Worth fixing
            # separately to push pagination down to SQL.
            queryset = self.get_queryset().using(DATABASE_FOR_PROJECT_VERSION_IDS)
            serializer = self.get_serializer(queryset, many=True)

            project_version_ids = [
                {"id": project_version["id"], "name": project_version["name"]}
                for project_version in serializer.data
            ]

            page_number = self.request.query_params.get("page_number", 0)
            page_size = self.request.query_params.get("page_size", 30)
            start = int(page_number) * int(page_size)
            end = start + int(page_size)
            total = len(project_version_ids)

            project_version_ids = project_version_ids[start:end]
            return self._gm.success_response(
                {
                    "project_version_ids": project_version_ids,
                    "count": total,
                    "next": end < total,
                }
            )
        except Exception as e:
            logger.exception(f"Error in fetching project version ids: {str(e)}")

            return self._gm.bad_request(
                f"Error getting project version ids: {get_error_message('ERROR_GETTING_PROJECT_VERSION_IDS')}"
            )

    @action(detail=False, methods=["post"])
    def add_annotations(self, request, *args, **kwargs):
        try:
            project_version_id = self.request.data.get("project_version_id")
            annotation_values = self.request.data.get("annotation_values")
            project_version = ProjectVersion.objects.get(
                id=project_version_id,
                project__organization=getattr(self.request, "organization", None)
                or self.request.user.organization,
            )

            if not project_version:
                raise Exception("Project version id is required")

            curr_annotation = project_version.annotations

            if curr_annotation is None:
                if annotation_values is not None:
                    annotation_values["organization"] = (
                        getattr(self.request, "organization", None)
                        or self.request.user.organization
                    )
                    serializer = AnnotationProjectVersionMapperSerializer(
                        data=annotation_values
                    )
                    if serializer.is_valid():
                        annotation = serializer.save(
                            organization=getattr(self.request, "organization", None)
                            or self.request.user.organization
                        )
                else:
                    raise Exception("Annotation details are required")
            else:
                for key, value in annotation_values.items():
                    if hasattr(curr_annotation, key):
                        field = getattr(curr_annotation, key)
                        if isinstance(
                            field, models.Manager
                        ):  # Check if it's a many-to-many field
                            field.set(value)  # Use set() for many-to-many fields
                        else:
                            setattr(curr_annotation, key, value)
                curr_annotation.save()
                annotation = curr_annotation

            project_version.annotations = annotation
            project_version.save()

            return self._gm.success_response({"annotation_id": annotation.id})
        except Exception as e:
            logger.exception(f"Error in updating/adding annotations: {str(e)}")

            return self._gm.bad_request(
                f"Error updating/adding annotations: {get_error_message('FAILED_TO_UPDATE_ANNOTATION')}"
            )

    @action(detail=False, methods=["get"])
    def get_run_insights(self, request, *args, **kwargs):
        try:
            project_version_id = self.request.query_params.get("project_version_id")

            if not project_version_id:
                raise Exception("Project version id is required")

            # Get trace IDs in a single query
            trace_ids = Trace.objects.filter(
                project_version_id=project_version_id
            ).values_list("id", flat=True)
            if not trace_ids:
                return self._gm.success_response(
                    {
                        "trace_ids": [],
                        "system_metrics": {"avg_latency_ms": 0, "avg_cost": 0},
                        "eval_metrics": {},
                        "avg_score": 0,
                    }
                )

            # System Metrics - Optimize with aggregation
            spans_metrics = ObservationSpan.objects.filter(
                trace_id__in=trace_ids
            ).aggregate(
                avg_latency=Avg("latency_ms", filter=Q(parent_span_id__isnull=True)),
                avg_cost=Round(
                    Avg(
                        "cost", filter=Q(cost__isnull=False), output_field=FloatField()
                    ),
                    6,
                ),
                latency_stddev=StdDev(
                    "latency_ms", filter=Q(parent_span_id__isnull=True)
                ),
                cost_stddev=StdDev(
                    "cost", filter=Q(cost__isnull=False), output_field=FloatField()
                ),
                count=Count(
                    "id",
                    filter=Q(prompt_tokens__isnull=False)
                    & Q(completion_tokens__isnull=False),
                ),
                avg_tokens=Round(
                    Avg(F("total_tokens"), filter=Q(total_tokens__isnull=False)), 2
                ),
            )

            # Get spans for outlier detection with fewer fields
            spans = ObservationSpan.objects.filter(trace_id__in=trace_ids).values(
                "trace_id",
                "latency_ms",
                "total_tokens",
                "prompt_tokens",
                "completion_tokens",
                "cost",
                "parent_span_id",
            )

            latency_mean = spans_metrics["avg_latency"] or 0
            latency_std = spans_metrics["latency_stddev"] or 0
            cost_mean = spans_metrics["avg_cost"] or 0
            cost_std = spans_metrics["cost_stddev"] or 0

            # Identify outliers
            latency_failed_trace_ids = set()
            cost_failed_trace_ids = set()

            for span in spans:
                if span["latency_ms"] and span["parent_span_id"] is None:
                    z_score_latency = (
                        (span["latency_ms"] - latency_mean) / latency_std
                        if latency_std
                        else 0
                    )

                    if abs(z_score_latency) > 1.96:
                        latency_failed_trace_ids.add(span["trace_id"])

                if span.get("cost"):
                    z_score_cost = (
                        (float(span["cost"]) - cost_mean) / cost_std if cost_std else 0
                    )
                    if abs(z_score_cost) > 1.96:
                        cost_failed_trace_ids.add(span["trace_id"])

            # Eval Metrics - Optimize with aggregation

            rows = SQL_query_handler.evals_insight_query(project_version_id)
            eval_metrics = {}
            for row in rows:
                try:
                    if row is None or len(row) == 0:
                        continue

                    custom_eval_config_id = str(row[0])
                    custom_eval_config_name = row[1]
                    row[2]
                    total_count = row[3]
                    avg_float_score = row[4] * 100 if row[4] is not None else row[4]
                    avg_bool_fail_score = row[5] * 100 if row[5] is not None else row[5]
                    avg_bool_pass_score = row[6] * 100 if row[6] is not None else row[6]
                    total_errors_count = row[8]

                    try:
                        str_list_score = (
                            ast.literal_eval(row[7]) if row[7] is not None else row[7]
                        )
                    except ValueError as e:
                        str_list_score = row[7]
                        logger.exception(
                            f"Value Error in parsing str list score: {str(e)} | {row = }"
                        )
                    except Exception as e:
                        logger.exception(
                            f"Error in parsing str list score: {str(e)} | {row = }"
                        )
                        raise Exception(  # noqa: B904
                            f"Error in parsing str list score: {str(e)} | {row = }"
                        )

                    try:
                        failed_trace_ids = (
                            ast.literal_eval(row[9]) if row[9] is not None else row[9]
                        )
                    except ValueError as e:
                        failed_trace_ids = row[9]
                        logger.exception(
                            f"Value Error in parsing failed trace ids: {str(e)} | {row = }"
                        )
                    except Exception as e:
                        logger.exception(
                            f"Error in parsing failed trace ids: {str(e)} | {row = }"
                        )
                        raise Exception(  # noqa: B904
                            f"Error in parsing failed trace ids: {str(e)} | {row = }"
                        )

                    eval_type = "bool"
                    if avg_float_score is not None:
                        eval_type = "float"
                    elif str_list_score is not None:
                        eval_type = "str_list"

                    parsed_error_message = None
                    if total_errors_count is not None and total_errors_count > 0:
                        eval_logger_error = (
                            EvalLogger.objects.filter(
                                trace__project_version_id=project_version_id,
                                custom_eval_config_id=custom_eval_config_id,
                            )
                            .filter(Q(error=True) | Q(output_str="ERROR"))
                            .order_by("-created_at")
                            .first()
                        )

                        if eval_logger_error:
                            error_message = (
                                eval_logger_error.error_message
                                if eval_logger_error.error_message
                                else eval_logger_error.eval_explanation
                            )
                            parsed_error_message = (
                                get_specific_error_message(error_message)
                                if error_message is not None
                                else None
                            )

                    eval_metrics[custom_eval_config_id] = {
                        "name": custom_eval_config_name,
                        "total_count": total_count,
                        "avg_float_score": avg_float_score,
                        "avg_bool_fail_score": avg_bool_fail_score,
                        "avg_bool_pass_score": avg_bool_pass_score,
                        "str_list_score": str_list_score,
                        "total_errors_count": total_errors_count,
                        "failed_trace_ids": failed_trace_ids,
                        "eval_type": eval_type,
                        "error_message": parsed_error_message,
                    }
                except Exception as e:
                    logger.exception(
                        f"Error in parsing eval metrics: {str(e)} with row: {row}"
                    )
                    continue

            # Add system metric failures
            eval_metrics["failed_latency"] = {
                "failed_trace_ids": list(latency_failed_trace_ids),
                "failed_trace_count": len(latency_failed_trace_ids),
                "name": "Abnormal High Latency Detected",
            }

            eval_metrics["failed_cost"] = {
                "failed_trace_ids": list(cost_failed_trace_ids),
                "failed_trace_count": len(cost_failed_trace_ids),
                "name": "Abnormal High cost",
            }

            return self._gm.success_response(
                {
                    "trace_ids": list(trace_ids),
                    "system_metrics": {
                        "avg_latency_ms": spans_metrics["avg_latency"] or 0,
                        "avg_cost": spans_metrics["avg_cost"] or 0,
                        "avg_tokens": spans_metrics["avg_tokens"] or 0,
                    },
                    "eval_metrics": eval_metrics,
                }
            )

        except Exception as e:
            logger.exception(f"Error in fetching run insights: {str(e)}")

            return self._gm.bad_request(
                f"Error getting run insights: {get_error_message('ERROR_GETTING_RUN_INSIGHTS')}"
            )

    @action(detail=False, methods=["get"])
    def list_runs(self, request, *args, **kwargs):
        """
        Get a paginated list of all projects for the organization.
        """
        try:
            project_id = self.request.query_params.get(
                "project_id"
            ) or self.request.query_params.get("projectId")
            if not project_id:
                raise Exception("Project id is required")

            request_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            # Get project and validate access
            try:
                project = Project.objects.get(
                    id=project_id,
                    organization=request_organization,
                )
            except Project.DoesNotExist:
                return self._gm.bad_request("Project not found")

            # Get configuration objects once
            eval_configs = list(
                CustomEvalConfig.objects.filter(
                    id__in=EvalLogger.objects.filter(
                        observation_span__project_id=project_id
                    )
                    .values("custom_eval_config_id")
                    .distinct(),
                    deleted=False,
                ).select_related("eval_template")
            )

            annotation_labels = list(
                AnnotationsLabels.objects.filter(project__id=project_id).exclude(
                    type=AnnotationTypeChoices.TEXT.value
                )
            )

            # Build project config
            project_config = get_default_project_version_config()
            project_config = update_column_config_based_on_eval_config(
                project_config, eval_configs
            )
            project_config = update_run_column_config_based_on_annotations(
                project_config, annotation_labels
            )

            # Build the base query with system metrics
            base_query = (
                ObservationSpan.objects.filter(
                    Q(project_version__deleted=False)
                    | Q(project_version__deleted=None),
                    project_id=project_id,
                )
                .values("project_version_id")
                .annotate(
                    run_name=Concat(
                        F("project_version__name"),
                        Value(" - "),
                        F("project_version__version"),
                        output_field=CharField(),
                    ),
                    version=F("project_version__version"),
                    avg_eval_score=F("project_version__avg_eval_score"),
                    row_avg_latency_ms=Coalesce(
                        Round(
                            Avg(
                                Case(
                                    When(parent_span_id__isnull=True, then="latency_ms")
                                )
                            ),
                            2,
                        ),
                        0.0,
                    ),
                    row_avg_cost=Coalesce(
                        Round(Avg("cost", output_field=FloatField()), 6),
                        0.0,
                    ),
                )
            )

            # Add eval metric annotations
            for config in eval_configs:
                choices = (
                    config.eval_template.choices if config.eval_template.choices else []
                )

                metric_subquery = (
                    EvalLogger.objects.filter(
                        trace__project_version_id=OuterRef("project_version_id"),
                        custom_eval_config_id=config.id,
                    )
                    .exclude(Q(output_str="ERROR") | Q(error=True))
                    .values("custom_eval_config_id")
                    .annotate(
                        float_score=Round(Avg("output_float") * 100, 2),
                        bool_score=Round(
                            Avg(
                                Case(
                                    When(output_bool=True, then=100.0),
                                    When(output_bool=False, then=0.0),
                                    default=None,
                                    output_field=FloatField(),
                                )
                            ),
                            2,
                        ),
                        str_list_score=(
                            JSONObject(
                                **{
                                    f"{value}": JSONObject(
                                        score=Round(
                                            100.0
                                            * Count(
                                                Case(
                                                    When(
                                                        output_str_list__contains=[
                                                            value
                                                        ],
                                                        then=1,
                                                    ),
                                                    default=None,
                                                    output_field=IntegerField(),
                                                )
                                            )
                                            / Count("output_str_list"),
                                            2,
                                        )
                                    )
                                    for value in choices
                                }
                            )
                            if choices
                            else Value("{}", output_field=JSONField())
                        ),
                    )
                    .values("float_score", "bool_score", "str_list_score")[:1]
                )

                base_query = base_query.annotate(
                    **{
                        f"metric_{config.id}": Case(
                            When(
                                Exists(
                                    EvalLogger.objects.filter(
                                        trace__project_version_id=OuterRef(
                                            "project_version_id"
                                        ),
                                        custom_eval_config_id=config.id,
                                        output_float__isnull=False,
                                    )
                                ),
                                then=JSONObject(
                                    score=Subquery(
                                        metric_subquery.values("float_score")
                                    )
                                ),
                            ),
                            When(
                                Exists(
                                    EvalLogger.objects.filter(
                                        trace__project_version_id=OuterRef(
                                            "project_version_id"
                                        ),
                                        custom_eval_config_id=config.id,
                                        output_bool__in=[True, False],
                                    )
                                ),
                                then=JSONObject(
                                    score=Subquery(metric_subquery.values("bool_score"))
                                ),
                            ),
                            When(
                                Exists(
                                    EvalLogger.objects.filter(
                                        trace__project_version_id=OuterRef(
                                            "project_version_id"
                                        ),
                                        custom_eval_config_id=config.id,
                                        output_str_list__isnull=False,
                                    )
                                ),
                                then=Subquery(metric_subquery.values("str_list_score")),
                            ),
                            default=None,
                            output_field=JSONField(),
                        )
                    }
                )

            # Add annotation metric annotations
            for label in annotation_labels:
                parsed_choices = []
                if label.type == AnnotationTypeChoices.CATEGORICAL.value:
                    parsed_choices = [
                        option["label"] for option in label.settings.get("options", [])
                    ]

                score_base_filter = {
                    "observation_span__project_version_id": OuterRef(
                        "project_version_id"
                    ),
                    "label_id": label.id,
                    "organization": request_organization,
                    "deleted": False,
                }

                # Same Score-based rollup as the first metric_subquery —
                # see comment above for context.
                metric_subquery = (
                    Score.objects.filter(
                        observation_span__project_version_id=OuterRef(
                            "project_version_id"
                        ),
                        label_id=label.id,
                        observation_span__project__organization=request_organization,
                        deleted=False,
                    )
                    .values("label_id")
                    .annotate(
                        # Score stores numeric labels as ``{"value": <float>}``
                        # but STAR labels as ``{"rating": <float>}`` (see
                        # tracer/views/annotation.py:_to_score_value).
                        # Coalesce both so star ratings show up in rollups.
                        annotation_float_score=Round(
                            Avg(
                                Coalesce(
                                    Cast(
                                        KeyTextTransform("value", "value"),
                                        FloatField(),
                                    ),
                                    Cast(
                                        KeyTextTransform("rating", "value"),
                                        FloatField(),
                                    ),
                                )
                            ),
                            2,
                        ),
                        annotation_bool_score=Round(
                            Avg(
                                Case(
                                    When(value__value="up", then=100),
                                    When(value__value="down", then=0),
                                    default=None,
                                    output_field=FloatField(),
                                )
                            ),
                            2,
                        ),
                        annotation_str_list_score=(
                            JSONObject(
                                **{
                                    f"{value}": JSONObject(
                                        score=Round(
                                            100.0
                                            * Count(
                                                Case(
                                                    When(
                                                        value__selected__contains=[
                                                            value
                                                        ],
                                                        then=1,
                                                    ),
                                                    default=None,
                                                    output_field=IntegerField(),
                                                )
                                            )
                                            / Count(
                                                "id",
                                                filter=~Q(value__selected__isnull=True),
                                            ),
                                            2,
                                        )
                                    )
                                    for value in parsed_choices
                                }
                            )
                            if parsed_choices
                            else Value("{}", output_field=JSONField())
                        ),
                    )
                    .values(
                        "annotation_float_score",
                        "annotation_bool_score",
                        "annotation_str_list_score",
                    )[:1]
                )

                base_query = base_query.annotate(
                    **{
                        f"annotation_{label.id}": Case(
                            When(
                                Exists(
                                    Score.objects.filter(
                                        **score_base_filter,
                                    )
                                    # Numeric stores ``{value: float}``; STAR
                                    # stores ``{rating: float}``. Existence
                                    # check accepts either path so star
                                    # ratings aren't filtered out.
                                    .filter(
                                        Q(value__value__isnull=False)
                                        | Q(value__rating__isnull=False)
                                    )
                                    .filter(
                                        label__type__in=[
                                            AnnotationTypeChoices.NUMERIC.value,
                                            AnnotationTypeChoices.STAR.value,
                                        ],
                                    )
                                ),
                                then=JSONObject(
                                    score=Subquery(
                                        metric_subquery.values("annotation_float_score")
                                    )
                                ),
                            ),
                            When(
                                Exists(
                                    Score.objects.filter(
                                        **score_base_filter,
                                    )
                                    .exclude(value__value__isnull=True)
                                    .filter(
                                        label__type=AnnotationTypeChoices.THUMBS_UP_DOWN.value,
                                    )
                                ),
                                then=JSONObject(
                                    score=Subquery(
                                        metric_subquery.values("annotation_bool_score")
                                    )
                                ),
                            ),
                            When(
                                Exists(
                                    Score.objects.filter(
                                        **score_base_filter,
                                    ).exclude(value__selected__isnull=True)
                                ),
                                then=Subquery(
                                    metric_subquery.values("annotation_str_list_score")
                                ),
                            ),
                            default=None,
                            output_field=JSONField(),
                        )
                    }
                )

            # Apply filters at database level
            filter_params = self.request.query_params.get("filters", "[]")
            try:
                filter_params = json.loads(filter_params)
            except json.JSONDecodeError:
                filter_params = []

            if filter_params:
                combined_filter_conditions = Q()

                # System metrics filters
                system_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_system_metrics(filter_params)
                )
                if system_filter_conditions.children:
                    combined_filter_conditions &= system_filter_conditions

                # Separate annotation filters from eval filters since
                # annotations are JSON objects
                annotation_col_types = {"ANNOTATION"}
                annotation_column_ids = {"my_annotations", "annotator"}
                non_annotation_filters = [
                    f
                    for f in filter_params
                    if f.get("col_type") not in annotation_col_types
                    and (f.get("column_id") or f.get("columnId"))
                    not in annotation_column_ids
                ]

                # Eval metric filters (excluding annotation filters)
                eval_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_non_system_metrics(
                        non_annotation_filters
                    )
                )
                if eval_filter_conditions.children:
                    combined_filter_conditions &= eval_filter_conditions

                # Annotation filters (score, annotator, my_annotations)
                annotation_filter_conditions, extra_annotations = (
                    FilterEngine.get_filter_conditions_for_voice_call_annotations(
                        filter_params,
                        user_id=request.user.id,
                        span_filter_kwargs={
                            "observation_span__project_version_id": OuterRef(
                                "project_version_id"
                            )
                        },
                    )
                )
                if extra_annotations:
                    base_query = base_query.annotate(**extra_annotations)
                if annotation_filter_conditions.children:
                    combined_filter_conditions &= annotation_filter_conditions

                # Apply combined filters
                if combined_filter_conditions.children:
                    base_query = base_query.filter(combined_filter_conditions)

            # Apply sorting at database level
            sort_params = self.request.data.get("sort_params", [])
            sort_conditions = []
            post_process_sorts = []  # For sorts that need post-processing

            if sort_params:
                for sort_param in sort_params:
                    column_id = str(sort_param.get("column_id"))
                    direction = sort_param.get("direction", "asc")
                    col_type = sort_param.get("col_type", ColType.EVAL_METRIC.value)

                    if column_id in [
                        "avg_latency",
                        "avg_cost",
                        "run_name",
                        "avgLatency",
                        "avgCost",
                        "runName",
                    ]:
                        field_map = {
                            "avg_latency": "row_avg_latency_ms",
                            "avg_cost": "row_avg_cost",
                            "run_name": "run_name",
                            "avgLatency": "row_avg_latency_ms",
                            "avgCost": "row_avg_cost",
                            "runName": "run_name",
                        }
                        sort_field = f"{'-' if direction == 'desc' else ''}{field_map[column_id]}"
                        sort_conditions.append(sort_field)

                    elif column_id == "rank":
                        # Rank needs post-processing, add to post_process_sorts
                        post_process_sorts.append(sort_param)

                    elif column_id and "**" in column_id:
                        # Metric choice sorting - needs post-processing
                        post_process_sorts.append(sort_param)

                    elif column_id:
                        # Regular metric/annotation sorting
                        if col_type == ColType.EVAL_METRIC.value:
                            metric_column_id = f"metric_{column_id}__score"
                        elif col_type == ColType.ANNOTATION_RUNS.value:
                            metric_column_id = f"annotation_{column_id}__score"
                        elif col_type == ColType.ANNOTATION.value:
                            metric_column_id = f"annotation_{column_id}"
                        else:
                            continue

                        sort_field = (
                            f"{'-' if direction == 'desc' else ''}{metric_column_id}"
                        )
                        sort_conditions.append(sort_field)

            # Apply database-level sorting
            if sort_conditions:
                base_query = base_query.order_by(*sort_conditions)

            # Get winner config for rank calculation
            winner_config = {}
            winner_prop_config = {}
            project_version_winner = (
                ProjectVersionWinner.objects.filter(project=project)
                .order_by("-created_at")
                .first()
            )

            if project_version_winner:
                winner_config = project_version_winner.version_mapper
                winner_prop_config = project_version_winner.eval_config
                # Update project config to show rank column
                for col in project_config:
                    if hasattr(col, "get") and col.get("id") == "rank":
                        if hasattr(col, "__setitem__"):
                            col["is_visible"] = True
                        break

            # Get total count before pagination
            total_count = base_query.count()

            # Apply pagination
            page_number = int(self.request.query_params.get("page_number", 0))
            page_size = int(self.request.query_params.get("page_size", 30))
            start = page_number * page_size
            end = start + page_size

            # Get paginated results
            results = list(base_query[start:end])

            # Process results into final format
            table_data = []
            for result in results:
                version_id = str(result["project_version_id"])
                row = {
                    "id": version_id,
                    "score": (
                        round(result["avg_eval_score"], 2)
                        if result["avg_eval_score"]
                        else None
                    ),
                    "rank": (
                        winner_config.get(version_id, {}).get("rank", 0)
                        if winner_config
                        else 0
                    ),
                    "avg_cost": result["row_avg_cost"],
                    "avg_latency": result["row_avg_latency_ms"],
                    "run_name": result["run_name"],
                }

                # Add eval metrics from annotated fields
                for config in eval_configs:
                    data = result.get(f"metric_{config.id}")
                    if data and isinstance(data, dict):
                        if "score" in data:
                            row[str(config.id)] = round(data["score"], 2)
                        else:
                            # Handle choice-based metrics
                            for key, value in data.items():
                                if isinstance(value, dict) and "score" in value:
                                    row[f"{config.id}**{key}"] = round(
                                        value["score"], 2
                                    )

                # Add annotation metrics
                for label in annotation_labels:
                    data = result.get(f"annotation_{label.id}")
                    if data and isinstance(data, dict):
                        if "score" in data:
                            row[str(label.id)] = round(data["score"], 2)
                        else:
                            # Handle choice-based annotations
                            for key, value in data.items():
                                if isinstance(value, dict) and "score" in value:
                                    row[f"{label.id}**{key}"] = round(value["score"], 2)

                table_data.append(row)

            # Apply post-processing sorts (rank, metric choices)
            if post_process_sorts:
                for sort_param in post_process_sorts:
                    column_id = str(sort_param.get("column_id"))
                    direction = sort_param.get("direction", "asc")
                    reverse = direction == "desc"

                    if column_id == "rank":
                        table_data.sort(key=lambda x: x.get("rank", 0), reverse=reverse)
                    elif "**" in column_id:
                        table_data.sort(
                            key=lambda x: x.get(column_id, 0) if column_id in x else 0,
                            reverse=reverse,
                        )

            # Apply rank-based filters (post-processing)
            if filter_params:
                for filter_param in filter_params:
                    if filter_param.get("column_id") == "rank":
                        filter_engine = FilterEngine(table_data)
                        table_data = filter_engine.apply_filters([filter_param])

            response = {
                "column_config": project_config,
                "table": table_data,
                "metadata": {
                    "total_rows": total_count,
                },
                "project_version_winnner_config": winner_prop_config,
            }

            return self._gm.success_response(response)

        except Exception as e:
            logger.exception(f"Error in fetching the project version list: {str(e)}")
            return self._gm.bad_request(
                f"error fetching the project versions list {get_error_message('ERROR_FETCHING_PROJECT_VERSION')}"
            )
