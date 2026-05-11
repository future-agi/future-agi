import concurrent.futures
import io
import json
import traceback
from collections import defaultdict
from typing import Dict, List
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
from time import time

import pandas as pd
import structlog
from django.db import close_old_connections, connection
from django.db.models import (
    Avg,
    Case,
    Count,
    Exists,
    F,
    FloatField,
    IntegerField,
    JSONField,
    Max,
    OuterRef,
    Q,
    Subquery,
    When,
)
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, JSONObject, Round
from django.http import FileResponse
from django.utils import timezone
from litellm import cost_per_token
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from agentic_eval.core.embeddings.embedding_manager import EmbeddingManager

logger = structlog.get_logger(__name__)
from analytics.utils import (
    MixpanelEvents,
    MixpanelModes,
    MixpanelSources,
    MixpanelTypes,
    get_mixpanel_properties,
    track_mixpanel_event,
)
from model_hub.models.choices import (
    AnnotationTypeChoices,
    DataTypeChoices,
    FeedbackSourceChoices,
)
from model_hub.models.develop_annotations import Annotations, AnnotationsLabels
from model_hub.models.evals_metric import Feedback
from model_hub.models.run_prompt import PromptVersion
from model_hub.models.score import Score
from model_hub.views.scores import (
    _auto_complete_queue_items,
    _auto_create_queue_items_for_default_queues,
)
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EndUser, EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.span_notes import SpanNotes
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.services.clickhouse.query_service import (
    AnalyticsQueryService,
    QueryType,
)
from tracer.serializers.observation_span import (
    ObservationSpanSerializer,
    SpanExportSerializer,
    SubmitFeedbackActionTypeSerializer,
    SubmitFeedbackSerializer,
)
from tracer.serializers.trace import TraceSerializer
from tracer.utils.annotations import build_annotation_subqueries
from tracer.utils.create_otel_span import create_single_otel_span
from tracer.utils.eval import (
    evaluate_observation_span,
    evaluate_observation_span_observe,
)
from tracer.utils.eval_tasks import parsing_evaltask_filters
from tracer.utils.filters import FilterEngine
from tracer.utils.graphs_optimized import (
    get_annotation_graph_data,
    get_eval_graph_data,
    get_system_metric_data,
)
from tracer.utils.helper import (
    FieldConfig,
    generate_timestamps,
    get_annotation_labels_for_project,
    get_default_span_config,
    update_column_config_based_on_eval_config,
    update_span_column_config_based_on_annotations,
)
from tracer.utils.otel import (
    ResourceLimitError,
    SpanAttributes,
    calculate_cost_from_tokens,
)
from tracer.utils.sql_queries import SQL_query_handler


def _validate_add_annotation_value(
    validate_fn, annotation_type, label_settings, given_value
):
    """Map the raw add_annotations value to typed fields and validate.

    Returns an error message string, or None if valid.
    """
    from model_hub.models.choices import AnnotationTypeChoices

    value = value_float = value_bool = value_str_list = None
    if annotation_type == AnnotationTypeChoices.TEXT.value:
        value = str(given_value) if given_value is not None else None
    elif annotation_type in [
        AnnotationTypeChoices.NUMERIC.value,
        AnnotationTypeChoices.STAR.value,
    ]:
        try:
            value_float = float(given_value)
        except (TypeError, ValueError):
            return f"Expected a numeric value, got: {given_value}"
    elif annotation_type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
        if isinstance(given_value, bool):
            value_bool = given_value
        elif isinstance(given_value, str):
            value_bool = given_value.lower() in ("up", "true", "1")
        else:
            return f"Expected a boolean value, got: {given_value}"
    elif annotation_type == AnnotationTypeChoices.CATEGORICAL.value:
        if isinstance(given_value, list):
            value_str_list = given_value
        elif isinstance(given_value, str):
            value_str_list = [v.strip() for v in given_value.split(",")]
        else:
            return f"Expected a list or string, got: {type(given_value).__name__}"
    else:
        value = str(given_value) if given_value is not None else None

    return validate_fn(
        label_type=annotation_type,
        label_settings=label_settings,
        value=value,
        value_float=value_float,
        value_bool=value_bool,
        value_str_list=value_str_list,
    )


def _to_score_value(annotation_type, given_value):
    """Convert AnnotateDrawer value format → Score.value JSON format."""
    if annotation_type in [
        AnnotationTypeChoices.STAR.value,
    ]:
        return {"rating": float(given_value)}
    elif annotation_type == AnnotationTypeChoices.NUMERIC.value:
        return {"value": float(given_value)}
    elif annotation_type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
        return {"value": str(given_value)}
    elif annotation_type == AnnotationTypeChoices.CATEGORICAL.value:
        return {
            "selected": given_value if isinstance(given_value, list) else [given_value]
        }
    else:
        # text and fallback
        return {"text": str(given_value)}


def _get_configured_output_type(custom_eval_config):
    """Get the configured output type from an eval's template config.

    Returns the output type string ("Pass/Fail", "score", "choices") or None
    if unavailable.
    """
    if (
        custom_eval_config
        and getattr(custom_eval_config, "eval_template", None)
        and custom_eval_config.eval_template
    ):
        eval_template_config = custom_eval_config.eval_template.config or {}
        return eval_template_config.get("output")
    return None


def _build_eval_metric_entry(
    output_float, output_bool, output_str_list, configured_output_type
):
    """Determine score and outputType based on eval template config.

    For Pass/Fail evals, prioritises output_bool over output_float so that
    stale float values (left behind by re-runs) don't mask the boolean result.

    Returns (score, output_type_str) or (None, None) when no score data exists.
    """
    # str_list can come from CH as a JSON string '[]' or from PG as a Python list
    parsed_str_list = None
    if output_str_list:
        if isinstance(output_str_list, list):
            parsed_str_list = output_str_list
        elif isinstance(output_str_list, str) and output_str_list.startswith("["):
            try:
                parsed_str_list = json.loads(output_str_list)
            except json.JSONDecodeError:
                pass

    # str_list always wins (choices type) - but only if it has data
    if parsed_str_list and len(parsed_str_list) > 0:
        return parsed_str_list, "str_list"

    # Config says Pass/Fail → prefer output_bool
    if configured_output_type == "Pass/Fail" and output_bool is not None:
        return (100.0 if output_bool else 0.0), "bool"

    # Float score (default path, or fallback for Pass/Fail when output_bool is absent)
    if output_float is not None:
        score = round(output_float * 100, 2)
        # If config says Pass/Fail but only float is stored (e.g. DeterministicEvaluator),
        # preserve the configured output type so the frontend renders Pass/Fail correctly.
        if configured_output_type == "Pass/Fail":
            return score, "Pass/Fail"
        return score, configured_output_type or "float"

    # Bool without Pass/Fail config
    if output_bool is not None:
        return (100.0 if output_bool else 0.0), "bool"

    return None, None


class ObservationSpanView(BaseModelViewSetMixin, ModelViewSet):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    serializer_class = ObservationSpanSerializer

    def get_queryset(self):
        observation_span_id = self.kwargs.get("pk")
        # Get base queryset with automatic filtering from mixin
        query_Set = super().get_queryset()

        if observation_span_id:
            return query_Set.filter(id=observation_span_id)

        project_id = self.request.query_params.get("project_id")
        project_version_id = self.request.query_params.get("project_version_id")
        trace_id = self.request.query_params.get("trace_id")
        page_number = self.request.query_params.get("page_number", 0)
        page_size = self.request.query_params.get("page_size", 30)

        if project_id:
            query_Set = query_Set.filter(project_id=project_id)

        if project_version_id:
            query_Set = query_Set.filter(project_version_id=project_version_id)

        if trace_id:
            query_Set = query_Set.filter(trace_id=trace_id)

        start = int(page_number) * int(page_size)
        end = start + int(page_size)

        return query_Set[start:end]

    def retrieve(self, request, *args, **kwargs):
        try:
            observation_span_id = kwargs.get("pk")

            # ClickHouse dispatch for span detail
            from tracer.services.clickhouse.query_service import (
                AnalyticsQueryService,
                QueryType,
            )

            analytics = AnalyticsQueryService()
            if analytics.should_use_clickhouse(QueryType.TRACE_DETAIL):
                try:
                    return self._retrieve_clickhouse(
                        request, observation_span_id, analytics
                    )
                except Exception as e:
                    logger.warning(
                        "CH span retrieve failed, falling back to PG", error=str(e)
                    )

            try:
                observation_span_obj = ObservationSpan.objects.get(
                    id=observation_span_id,
                    project__organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
            except ObservationSpan.DoesNotExist:
                logger.exception(
                    f"Observation span with id {observation_span_id} does not exist for this organization."
                )
                return self._gm.bad_request(
                    get_error_message("OBSERVATION_SPAN_NOT_FOUND")
                )

            serializer = self.get_serializer(observation_span_obj)
            observation_span = serializer.data

            if observation_span["prompt_version"]:
                try:
                    prompt_version = PromptVersion.objects.get(
                        id=observation_span["prompt_version"]
                    )
                    observation_span["prompt_template_id"] = str(
                        prompt_version.original_template.id
                    )
                    observation_span["prompt_name"] = (
                        str(prompt_version.original_template.name)
                        + " - "
                        + str(prompt_version.template_version)
                    )
                except PromptVersion.DoesNotExist:
                    observation_span["prompt_version"] = None

            eval_tags = (
                observation_span_obj.project_version.eval_tags
                if observation_span_obj.project_version
                else []
            )
            custom_eval_config_ids = {
                eval_tag["custom_eval_config_id"]
                for eval_tag in eval_tags
                if "custom_eval_config_id" in eval_tag
            }

            # Fetch all children span IDs
            children_span_ids = fetch_children_span_ids(observation_span_obj)
            children_span_ids.append(observation_span["id"])

            # Prepare eval metrics dictionary
            evals_metrics = {}

            eval_logger_objs = EvalLogger.objects.filter(
                Q(deleted=False) | Q(deleted__isnull=True),
                observation_span_id__in=children_span_ids,
            ).select_related("custom_eval_config__eval_template", "observation_span")

            # Loop through each eval_logger

            name_suffix = ""

            for eval_logger in eval_logger_objs:
                # Fetch the CustomEvalConfig to get the name and choices

                custom_eval_config = eval_logger.custom_eval_config
                config_name = custom_eval_config.name if custom_eval_config else None
                # For external scores without a CustomEvalConfig,
                # use eval_type_id as the config identifier and display name.
                config_id = str(custom_eval_config.id) if custom_eval_config else None
                if not config_name:
                    config_name = eval_logger.eval_type_id or "score"
                if not config_id:
                    config_id = eval_logger.eval_type_id or str(eval_logger.id)

                # Add child span suffix to name if this is a child span
                name_suffix = (
                    f" ( child span - {eval_logger.observation_span.id} )"
                    if str(eval_logger.observation_span.id) != str(observation_span_id)
                    else ""
                )

                if (
                    custom_eval_config
                    and str(custom_eval_config.id) in custom_eval_config_ids
                ):
                    custom_eval_config_ids.remove(str(custom_eval_config.id))

                # Handle error case
                if eval_logger.error or eval_logger.output_str == "ERROR":
                    key = f"{config_id}**{eval_logger.observation_span.id}"
                    evals_metrics[key] = {
                        "score": None,
                        "name": f"{config_name}{name_suffix}",
                        "explanation": eval_logger.error_message,
                        "error": True,
                    }

                else:
                    configured_output_type = _get_configured_output_type(
                        custom_eval_config
                    )
                    score, output_type = _build_eval_metric_entry(
                        eval_logger.output_float,
                        eval_logger.output_bool,
                        eval_logger.output_str_list,
                        configured_output_type,
                    )
                    if score is not None or output_type is not None:
                        key = f"{config_id}**{eval_logger.observation_span.id}"
                        evals_metrics[key] = {
                            "score": score,
                            "name": f"{config_name}{name_suffix}",
                            "explanation": eval_logger.eval_explanation,
                            "output_type": output_type,
                        }

            if custom_eval_config_ids:
                # Fetch all CustomEvalConfig objects in a single query to avoid N+1
                custom_eval_configs_map = {
                    str(config.id): config
                    for config in CustomEvalConfig.objects.filter(
                        id__in=custom_eval_config_ids
                    )
                }

                for custom_eval_config_id in custom_eval_config_ids:
                    # Find matching eval tag for this custom eval config ID
                    matching_eval_tag = next(
                        (
                            tag
                            for tag in eval_tags
                            if str(tag.get("custom_eval_config_id"))
                            == str(custom_eval_config_id)
                        ),
                        None,
                    )
                    if (
                        not matching_eval_tag
                        or matching_eval_tag.get("type") != "OBSERVATION_SPAN_TYPE"
                        or matching_eval_tag.get("value").upper()
                        != observation_span["observation_type"].upper()
                    ):
                        continue

                    key = f"{custom_eval_config_id}**{observation_span_id}"

                    # Get config from the pre-fetched map
                    custom_eval_config = custom_eval_configs_map.get(
                        str(custom_eval_config_id)
                    )
                    if not custom_eval_config:
                        logger.exception(
                            f"Custom eval config with id {custom_eval_config_id} does not exist."
                        )
                        return self._gm.bad_request(
                            get_error_message("CUSTOM_EVAL_CONFIG_NOT_FOUND")
                        )
                    if custom_eval_config.deleted:
                        continue
                    evals_metrics[key] = {
                        "score": None,
                        "name": f"{custom_eval_config.name}",
                        "explanation": None,
                        "loading": True,
                    }

            if observation_span["cost"] and observation_span["cost"] > 0:
                observation_span["cost"] = round(observation_span["cost"], 6)

            return self._gm.success_response(
                {"observation_span": observation_span, "evals_metrics": evals_metrics}
            )
        except Exception as e:
            logger.exception(f"Error in fetching observation span: {str(e)}")
            return self._gm.bad_request(
                f"Error retrieving observation span {get_error_message('FAILED_GET_OBSERVATION_SPAN')}"
            )

    def _retrieve_clickhouse(self, request, observation_span_id, analytics):
        """Retrieve span detail from ClickHouse with eval metrics."""
        from tracer.constants.provider_logos import PROVIDER_LOGOS

        # Fetch span from CH — query the denormalized `spans` table which has
        # renamed columns vs PG. Map them back to the expected field names.
        span_query = """
            SELECT
                id, project_id, project_version_id, trace_id, parent_span_id,
                name, observation_type, start_time, end_time, input, output,
                model, '' AS model_parameters, latency_ms, prompt_tokens,
                completion_tokens, total_tokens, cost, status, status_message,
                tags, span_attributes_raw AS span_attributes,
                span_events, provider,
                metadata_map,
                custom_eval_config_id,
                span_attr_str, span_attr_num, span_attr_bool
            FROM spans
            WHERE id = %(span_id)s
              AND _peerdb_is_deleted = 0
            LIMIT 1
        """
        result = analytics.execute_ch_query(
            span_query, {"span_id": str(observation_span_id)}, timeout_ms=5000
        )

        if not result.data:
            return self._gm.bad_request(get_error_message("OBSERVATION_SPAN_NOT_FOUND"))

        row = result.data[0]
        provider = row.get("provider")

        # Parse JSON string fields from CH (stored as String columns)
        import json as _json

        def _parse_json(val, default=None):
            """Safely parse a JSON string; return default if not a string or invalid."""
            if default is None:
                default = {}
            if not val or not isinstance(val, str):
                return val if val is not None else default
            try:
                return _json.loads(val)
            except (ValueError, TypeError):
                return default

        # Build span_attributes from the raw JSON string or decomposed maps

        span_attrs_raw = row.get("span_attributes") or "{}"
        try:
            span_attrs = (
                _json.loads(span_attrs_raw)
                if isinstance(span_attrs_raw, str)
                else span_attrs_raw
            )
        except (ValueError, TypeError):
            span_attrs = {}
        if not span_attrs:
            # Fall back to reconstructing from decomposed maps
            span_attrs = {}
            for k, v in (row.get("span_attr_str") or {}).items():
                span_attrs[k] = v
            for k, v in (row.get("span_attr_num") or {}).items():
                span_attrs[k] = v
            for k, v in (row.get("span_attr_bool") or {}).items():
                span_attrs[k] = bool(v)
        # Fallback: if CH has no span_attributes, try PG
        if not span_attrs:
            try:
                pg_span = ObservationSpan.objects.only(
                    "span_attributes", "eval_attributes"
                ).get(id=observation_span_id)
                span_attrs = pg_span.span_attributes or pg_span.eval_attributes or {}
            except ObservationSpan.DoesNotExist:
                pass

        # Build metadata from metadata_map
        metadata_map = row.get("metadata_map") or {}
        metadata = dict(metadata_map) if metadata_map else {}

        observation_span = {
            "id": str(row["id"]),
            "project": str(row["project_id"]),
            "project_version": (
                str(row["project_version_id"])
                if row.get("project_version_id")
                else None
            ),
            "trace": str(row["trace_id"]),
            "parent_span_id": (
                str(row["parent_span_id"]) if row.get("parent_span_id") else None
            ),
            "name": row.get("name"),
            "observation_type": row.get("observation_type"),
            "start_time": row.get("start_time"),
            "end_time": row.get("end_time"),
            "input": _parse_json(row.get("input")),
            "output": _parse_json(row.get("output")),
            "model": row.get("model"),
            "model_parameters": _parse_json(row.get("model_parameters")),
            "latency_ms": row.get("latency_ms"),
            "org_id": None,
            "org_user_id": None,
            "prompt_tokens": row.get("prompt_tokens"),
            "completion_tokens": row.get("completion_tokens"),
            "total_tokens": row.get("total_tokens"),
            "response_time": None,
            "eval_id": None,
            "cost": (
                round(row["cost"], 6)
                if row.get("cost") and row["cost"] > 0
                else row.get("cost")
            ),
            "status": row.get("status"),
            "status_message": row.get("status_message"),
            "tags": _parse_json(row.get("tags"), default=[]),
            "metadata": metadata,
            "span_events": _parse_json(row.get("span_events"), default=[]),
            "provider": provider,
            "provider_logo": PROVIDER_LOGOS.get(provider.lower()) if provider else None,
            "span_attributes": span_attrs,
            "custom_eval_config": (
                str(row["custom_eval_config_id"])
                if row.get("custom_eval_config_id")
                else None
            ),
            "eval_status": None,
            "prompt_version": None,
        }

        # Handle prompt version name (from PG, small config table)
        if observation_span["prompt_version"]:
            try:
                prompt_version = PromptVersion.objects.get(
                    id=observation_span["prompt_version"]
                )
                observation_span["prompt_template_id"] = str(
                    prompt_version.original_template.id
                )
                observation_span["prompt_name"] = (
                    str(prompt_version.original_template.name)
                    + " - "
                    + str(prompt_version.template_version)
                )
            except PromptVersion.DoesNotExist:
                observation_span["prompt_version"] = None

        # Fetch children span IDs from CH
        children_query = """
            SELECT DISTINCT id
            FROM spans
            WHERE trace_id = %(trace_id)s
              AND project_id = %(project_id)s
              AND _peerdb_is_deleted = 0
        """
        children_result = analytics.execute_ch_query(
            children_query,
            {"trace_id": str(row["trace_id"]), "project_id": str(row["project_id"])},
            timeout_ms=5000,
        )
        children_span_ids = [str(r["id"]) for r in children_result.data]

        # Fetch eval metrics from CH
        evals_metrics = {}
        if children_span_ids:
            eval_query = """
                SELECT
                    toString(observation_span_id) AS span_id,
                    toString(custom_eval_config_id) AS config_id,
                    output_float,
                    output_bool,
                    output_str_list,
                    eval_explanation,
                    error,
                    error_message,
                    output_str
                FROM tracer_eval_logger FINAL
                WHERE observation_span_id IN %(span_ids)s
                  AND _peerdb_is_deleted = 0
                  AND (deleted = 0 OR deleted IS NULL)
            """
            eval_result = analytics.execute_ch_query(
                eval_query, {"span_ids": children_span_ids}, timeout_ms=5000
            )

            # Get config names from PG (small config table)
            config_ids = list(
                {r["config_id"] for r in eval_result.data if r.get("config_id")}
            )
            config_name_map = {}
            config_output_type_map = {}
            if config_ids:
                configs = CustomEvalConfig.objects.filter(
                    id__in=config_ids
                ).select_related("eval_template")
                for c in configs:
                    config_name_map[str(c.id)] = c.name
                    config_output_type_map[str(c.id)] = _get_configured_output_type(c)

            for eval_row in eval_result.data:
                config_id = eval_row.get("config_id")
                span_id = eval_row.get("span_id")
                config_name = config_name_map.get(
                    config_id, eval_row.get("eval_type_id", "score")
                )
                if not config_name:
                    config_name = "score"

                name_suffix = (
                    f" ( child span - {span_id} )"
                    if span_id != str(observation_span_id)
                    else ""
                )

                key = f"{config_id}**{span_id}"

                if eval_row.get("error") or eval_row.get("output_str") == "ERROR":
                    evals_metrics[key] = {
                        "score": None,
                        "name": f"{config_name}{name_suffix}",
                        "explanation": eval_row.get("error_message"),
                        "error": True,
                    }
                else:
                    configured_output_type = config_output_type_map.get(config_id)
                    score, output_type = _build_eval_metric_entry(
                        eval_row.get("output_float"),
                        eval_row.get("output_bool"),
                        eval_row.get("output_str_list"),
                        configured_output_type,
                    )
                    if score is not None or output_type is not None:
                        evals_metrics[key] = {
                            "score": score,
                            "name": f"{config_name}{name_suffix}",
                            "explanation": eval_row.get("eval_explanation"),
                            "output_type": output_type,
                        }

        return self._gm.success_response(
            {"observation_span": observation_span, "evals_metrics": evals_metrics}
        )

    @action(detail=False, methods=["get"])
    def retrieve_loading(self, request, *args, **kwargs):
        try:
            observation_span_id = request.query_params.get("observation_span_id")
            if not observation_span_id:
                return self._gm.bad_request("observation_span_id is required")

            try:
                observation_span_obj = ObservationSpan.objects.get(
                    id=observation_span_id,
                    project__organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
            except ObservationSpan.DoesNotExist:
                logger.exception(
                    f"Observation span with id {observation_span_id} does not exist for this organization."
                )
                return self._gm.bad_request(
                    get_error_message("OBSERVATION_SPAN_NOT_FOUND")
                )

            serializer = self.get_serializer(observation_span_obj)
            observation_span = serializer.data

            # Get project version and eval_tags
            project_version = observation_span_obj.project_version
            if not project_version:
                return self._gm.bad_request(
                    "Project version not found for this observation span"
                )

            eval_tags = project_version.eval_tags or []

            # Fetch all children span IDs
            children_span_ids = fetch_children_span_ids(observation_span_obj)
            children_span_ids.append(observation_span["id"])

            # Prepare eval metrics dictionary
            evals_metrics = {}

            # Get all relevant observation spans
            observation_spans = ObservationSpan.objects.filter(id__in=children_span_ids)
            eval_tags = observation_span_obj.project_version.eval_tags

            eval_config_mapping = {
                str(eval_tag["custom_eval_config_id"]): eval_tag["value"]
                for eval_tag in eval_tags
                if eval_tag["type"] == "OBSERVATION_SPAN_TYPE"
            }

            custom_eval_config_ids = {
                eval_tag["custom_eval_config_id"] for eval_tag in eval_tags
            }
            custom_eval_configs = CustomEvalConfig.objects.filter(
                id__in=custom_eval_config_ids, deleted=False
            ).select_related("eval_template")
            name_suffix = ""

            for custom_eval_config in custom_eval_configs:
                for span in observation_spans:
                    if (
                        span.observation_type
                        != eval_config_mapping.get(str(custom_eval_config.id)).lower()
                    ):
                        continue

                    eval_logger = EvalLogger.objects.filter(
                        observation_span=span, custom_eval_config=custom_eval_config
                    ).first()

                    config_name = custom_eval_config.name

                    name_suffix = (
                        f" ( child span - {span.id} )"
                        if str(span.id) != str(observation_span_id)
                        else ""
                    )

                    if not eval_logger:
                        key = f"{custom_eval_config.id}**{span.id}"
                        evals_metrics[key] = {
                            "score": None,
                            "name": f"{config_name}{name_suffix}",
                            "explanation": None,
                            "loading": True,
                        }
                        continue

                    # Handle error case
                    if eval_logger.error or eval_logger.output_str == "ERROR":
                        key = f"{custom_eval_config.id}**{span.id}"
                        evals_metrics[key] = {
                            "score": None,
                            "name": f"{config_name}{name_suffix}",
                            "explanation": eval_logger.error_message,
                            "error": True,
                        }

                    else:
                        configured_output_type = _get_configured_output_type(
                            custom_eval_config
                        )
                        score, output_type = _build_eval_metric_entry(
                            eval_logger.output_float,
                            eval_logger.output_bool,
                            eval_logger.output_str_list,
                            configured_output_type,
                        )
                        if score is not None or output_type is not None:
                            key = f"{custom_eval_config.id}**{span.id}"
                            evals_metrics[key] = {
                                "score": score,
                                "name": f"{config_name}{name_suffix}",
                                "explanation": eval_logger.eval_explanation,
                                "output_type": output_type,
                            }

            return self._gm.success_response(
                {"observation_span": observation_span, "evals_metrics": evals_metrics}
            )

        except Exception as e:
            logger.exception(f"Error in fetching observation span: {str(e)}")
            return self._gm.bad_request(
                f"Error retrieving observation span {get_error_message('FAILED_GET_OBSERVATION_SPAN')}"
            )

    @action(detail=False, methods=["get"], url_path="root-spans")
    def root_spans(self, request, *args, **kwargs):
        """
        Given a list of trace_ids, return the root span ID for each trace.
        Root span = the span where parent_span_id IS NULL for that trace.

        Query param: trace_ids (repeated, e.g. ?trace_ids=<id>&trace_ids=<id>)
        Response: { "result": { "<trace_id>": "<span_id>", ... } }
        """
        try:
            trace_ids = request.query_params.getlist("trace_ids")
            if not trace_ids:
                return self._gm.bad_request("trace_ids is required")

            org = (
                getattr(request, "organization", None) or request.user.organization
            )
            spans = ObservationSpan.objects.filter(
                trace_id__in=trace_ids,
                parent_span_id__isnull=True,
                project__organization=org,
            ).values("id", "trace_id")

            result = {str(s["trace_id"]): str(s["id"]) for s in spans}
            return self._gm.success_response(result)
        except Exception as e:
            return self._gm.bad_request(f"Error fetching root spans: {str(e)}")

    @action(detail=False, methods=["post"])
    def bulk_create(self, request, *args, **kwargs):
        try:
            observation_span_data = self.request.data.get("observation_spans", [])
            for observation_span in observation_span_data:
                observation_span["project"] = Project.objects.get(
                    id=observation_span["project"],
                    organization=getattr(self.request, "organization", None)
                    or self.request.user.organization,
                )
                observation_span["project_version"] = ProjectVersion.objects.get(
                    id=observation_span["project_version"],
                    project__organization=getattr(self.request, "organization", None)
                    or self.request.user.organization,
                )
                observation_span["trace"] = Trace.objects.get(
                    id=observation_span["trace"],
                    project__organization=getattr(self.request, "organization", None)
                    or self.request.user.organization,
                )

                prompt_tokens = (
                    observation_span["prompt_tokens"]
                    if observation_span["prompt_tokens"] is not None
                    else 0
                )
                completion_tokens = (
                    observation_span["completion_tokens"]
                    if observation_span["completion_tokens"] is not None
                    else 0
                )
                model = (
                    observation_span["model"]
                    if observation_span["model"] is not None
                    else None
                )
                cost = calculate_cost_from_tokens(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    model=model,
                    organization_id=(
                        getattr(request, "organization", None)
                        or request.user.organization
                    ).id,
                )

                observation_span["cost"] = cost

            spans = [ObservationSpan(**req) for req in observation_span_data]
            added_observation_spans = ObservationSpan.objects.bulk_create(spans)
            ids = [span.id for span in added_observation_spans]
            return self._gm.success_response({"Observation Span IDs": ids})
        except Exception as e:
            logger.exception(f"Error in creating observation spans in bulk: {str(e)}")
            return self._gm.bad_request(
                f"Error creating bulk observation spans: {get_error_message('FAILED_TO_CREATE_OBS_SPAN_BULK')}"
            )

    def create(self, request, *args, **kwargs):
        try:
            if "id" in self.request.data:
                serializer = ObservationSpanSerializer(data=request.data)
                if serializer.is_valid():
                    observation_span = serializer.save(id=request.data["id"])

                    return self._gm.success_response(
                        {"id": observation_span.id}, status=201
                    )
            else:
                serializer = ObservationSpanSerializer(data=request.data)
                if serializer.is_valid():
                    observation_span = serializer.save()

                    return self._gm.success_response(
                        {"id": observation_span.id}, status=201
                    )
            return self._gm.bad_request(serializer.errors)
        except Exception as e:
            logger.exception(f"Error in creating observation span: {str(e)}")
            return self._gm.bad_request(
                f"Error creating observation span: {get_error_message('FAILED_CREATION_OBSERVATION_SPAN')}"
            )

    @action(detail=False, methods=["post"])
    def create_otel_span(self, request, *args, **kwargs):
        try:
            data_arr = self.request.data
            organization_id = (
                getattr(self.request, "organization", None)
                or self.request.user.organization
            ).id
            user_id = self.request.user.id
            workspace_id = getattr(getattr(request, "workspace", None), "id", None)
            created_span_ids = []

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                future_to_config = {
                    executor.submit(
                        create_single_otel_span,
                        data,
                        organization_id,
                        user_id,
                        workspace_id,
                    ): data
                    for data in data_arr
                }

                for future in concurrent.futures.as_completed(future_to_config):
                    observation_span = future.result()
                    created_span_ids.append(observation_span.id)

            if request.headers.get("X-Api-Key") is not None:
                properties = get_mixpanel_properties(
                    user=request.user, span=observation_span
                )
                track_mixpanel_event(
                    MixpanelEvents.SDK_OBSERVE_CREATE.value, properties
                )
            return self._gm.success_response({"ids": created_span_ids}, status=201)
        except ResourceLimitError as e:
            logger.warning(
                f"Resource limit error in creating observation span: {str(e)}"
            )
            return self._gm.bad_request(str(e))
        except Exception as e:
            logger.exception(f"Error in creating observation span: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Error creating observation span: {get_error_message('FAILED_CREATION_OBSERVATION_SPAN')}"
            )

    @action(detail=False, methods=["get"])
    def list_spans(self, request, *args, **kwargs):
        """
        List spans filtered by project ID and project version ID with optimized queries.
        """
        try:
            project_version_id = self.request.query_params.get(
                "project_version_id"
            ) or self.request.query_params.get("projectVersionId")
            if not project_version_id:
                raise Exception("Project version id is required")

            project_version = ProjectVersion.objects.get(
                id=project_version_id,
                project__organization=getattr(self.request, "organization", None)
                or self.request.user.organization,
            )

            # ClickHouse dispatch
            from tracer.services.clickhouse.query_service import (
                AnalyticsQueryService,
                QueryType,
            )

            analytics = AnalyticsQueryService()
            if analytics.should_use_clickhouse(QueryType.SPAN_LIST):
                try:
                    return self._list_spans_non_observe_clickhouse(
                        request, project_version_id, project_version, analytics
                    )
                except Exception as e:
                    logger.warning(
                        "CH list_spans failed, falling back to PG", error=str(e)
                    )

            # Base query with annotations
            base_query = ObservationSpan.objects.filter(
                project_version_id=project_version_id,
                project__organization=getattr(request, "organization", None)
                or request.user.organization,
            ).annotate(
                children_count=Subquery(
                    ObservationSpan.objects.filter(parent_span_id=OuterRef("id"))
                    .values("parent_span_id")
                    .annotate(count=Count("id"))
                    .values("count")[:1]
                ),
                node_type=F("observation_type"),
                observation_span_id=F("id"),
                span_id=F("id"),
                span_name=F("name"),
            )

            # Get all eval configs for the project
            eval_configs = CustomEvalConfig.objects.filter(
                id__in=EvalLogger.objects.filter(
                    observation_span__project_id=project_version.project.id
                )
                .values("custom_eval_config_id")
                .distinct(),
                deleted=False,
            ).select_related("eval_template")

            # Add annotations for each eval config dynamically
            for config in eval_configs:
                choices = (
                    config.eval_template.choices
                    if config.eval_template.choices
                    else None
                )

                metric_subquery = (
                    EvalLogger.objects.filter(
                        observation_span_id=OuterRef("id"),
                        custom_eval_config_id=config.id,
                        observation_span__project__organization=getattr(
                            request, "organization", None
                        )
                        or request.user.organization,
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
                                        observation_span_id=OuterRef("id"),
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
                                        observation_span_id=OuterRef("id"),
                                        custom_eval_config_id=config.id,
                                        output_bool__isnull=False,
                                    )
                                ),
                                then=JSONObject(
                                    score=Subquery(metric_subquery.values("bool_score"))
                                ),
                            ),
                            When(
                                Exists(
                                    EvalLogger.objects.filter(
                                        observation_span_id=OuterRef("id"),
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

            # Add Span Annotations
            annotation_labels = get_annotation_labels_for_project(
                project_version.project.id
            )
            base_query = build_annotation_subqueries(
                base_query,
                annotation_labels,
                request.user.organization,
                span_filter_kwargs={"observation_span_id": OuterRef("id")},
            )

            # Apply filters - combine all filter conditions for better performance
            filters = self.request.query_params.get(
                "filters", []
            ) or self.request.query_params.get("filters", [])
            if filters:
                filters = json.loads(filters)
            if filters:
                # Combine all filter conditions into a single Q object
                combined_filter_conditions = Q()

                # Get system metric filters
                system_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_system_metrics(filters)
                )
                if system_filter_conditions:
                    combined_filter_conditions &= system_filter_conditions

                # Separate annotation filters from eval filters since
                # annotations are JSON objects
                annotation_col_types = {"ANNOTATION"}
                annotation_column_ids = {"my_annotations", "annotator"}
                non_annotation_filters = [
                    f
                    for f in filters
                    if f.get("col_type") not in annotation_col_types
                    and (f.get("column_id") or f.get("columnId"))
                    not in annotation_column_ids
                ]

                # Get eval metric filters (excluding annotation filters)
                eval_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_non_system_metrics(
                        non_annotation_filters
                    )
                )
                if eval_filter_conditions:
                    combined_filter_conditions &= eval_filter_conditions

                # Get annotation filters (score, annotator, my_annotations)
                annotation_filter_conditions, extra_annotations = (
                    FilterEngine.get_filter_conditions_for_voice_call_annotations(
                        filters,
                        user_id=request.user.id,
                        span_filter_kwargs={"observation_span_id": OuterRef("id")},
                    )
                )
                if extra_annotations:
                    base_query = base_query.annotate(**extra_annotations)
                if annotation_filter_conditions:
                    combined_filter_conditions &= annotation_filter_conditions

                # Get span attribute filters
                span_attribute_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_span_attributes(filters)
                )
                if span_attribute_filter_conditions:
                    combined_filter_conditions &= span_attribute_filter_conditions

                # Apply has_eval filter (only spans with evals)
                has_eval_condition = FilterEngine.get_filter_conditions_for_has_eval(
                    filters, observe_type="span"
                )
                if has_eval_condition:
                    combined_filter_conditions &= has_eval_condition

                # Apply has_annotation filter
                has_annotation_condition = (
                    FilterEngine.get_filter_conditions_for_has_annotation(
                        filters, observe_type="span"
                    )
                )
                if has_annotation_condition:
                    combined_filter_conditions &= has_annotation_condition

                # Apply combined filters in a single operation
                if combined_filter_conditions:
                    base_query = base_query.filter(combined_filter_conditions)

            base_query = base_query.order_by("-start_time", "-id")

            # Get total count before pagination
            total_count = base_query.count()

            # Apply pagination
            page_number = int(self.request.query_params.get("page_number", 0)) or int(
                self.request.query_params.get("pageNumber", 0)
            )
            page_size = int(self.request.query_params.get("page_size", 30)) or int(
                self.request.query_params.get("pageSize", 30)
            )
            start = page_number * page_size
            base_query = base_query[start : start + page_size]

            # Prepare column config
            column_config = get_default_span_config()
            column_config = update_column_config_based_on_eval_config(
                column_config, eval_configs
            )
            column_config = update_span_column_config_based_on_annotations(
                column_config, annotation_labels
            )

            # Process results
            table_data = []
            for span in base_query:
                result = {
                    "node_type": span.observation_type,
                    "span_id": str(span.id),
                    "input": span.input,
                    "output": span.output,
                    "trace_id": str(span.trace_id),
                    "span_name": span.name,
                    "start_time": span.start_time,
                    "status": span.status,
                }

                # Add eval metrics from annotated fields
                for config in eval_configs:
                    data = getattr(span, f"metric_{config.id}")
                    if data and "score" in data:
                        result[str(config.id)] = data["score"]
                    elif data:
                        for key, value in data.items():
                            result[str(config.id) + "**" + key] = value["score"]

                # Add annotations to the result
                for label in annotation_labels:
                    ann_data = getattr(span, f"annotation_{label.id}", None)
                    if ann_data is not None:
                        result[str(label.id)] = ann_data

                table_data.append(result)

            response = {
                "column_config": column_config,
                "metadata": {"total_rows": total_count},
                "table": table_data,
            }

            return self._gm.success_response(response)

        except Exception as e:
            logger.exception(f"Error in fetching the spans list: {str(e)}")
            return self._gm.bad_request(
                f"error fetching the spans list {get_error_message('FAILED_TO_FETCH_TRACE_LIST')}"
            )

    @action(detail=False, methods=["post"])
    def submit_feedback(self, request, *args, **kwargs):
        try:
            serializer = SubmitFeedbackSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            validated_data = serializer.validated_data
            observation_span_id = validated_data.get("observation_span_id", None)
            custom_eval_config_id = validated_data.get("custom_eval_config_id", None)
            feedback_value = validated_data.get("feedback_value", None)
            feedback_explanation = validated_data.get("feedback_explanation", None)
            feedback_improvement = validated_data.get("feedback_improvement", None)

            try:
                observation_span = ObservationSpan.objects.get(
                    id=observation_span_id,
                    project__organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
            except ObservationSpan.DoesNotExist:
                raise Exception("Observation span not found")  # noqa: B904

            try:
                custom_eval_config = CustomEvalConfig.objects.get(
                    id=custom_eval_config_id,
                    project__organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
            except CustomEvalConfig.DoesNotExist:
                raise Exception("Custom eval config not found")  # noqa: B904

            try:
                EvalLogger.objects.get(
                    observation_span=observation_span,
                    custom_eval_config_id=custom_eval_config_id,
                    deleted=False,
                )
            except EvalLogger.DoesNotExist:
                raise Exception("No eval associated with this span ")  # noqa: B904

            eval_template = custom_eval_config.eval_template

            feedback = Feedback.objects.create(
                source=(
                    FeedbackSourceChoices.EXPERIMENT.value
                    if observation_span.project_version
                    else FeedbackSourceChoices.OBSERVE.value
                ),
                source_id=observation_span_id,
                value=feedback_value,
                explanation=feedback_explanation,
                eval_template=eval_template,
                feedback_improvement=feedback_improvement,
                user=request.user,
                custom_eval_config_id=custom_eval_config_id,
                organization=observation_span.project.organization,
                workspace=observation_span.project.workspace,
            )

            trace = Trace.objects.get(id=observation_span.trace.id)
            trace_data = TraceSerializer(trace).data

            # get_fewshots = RAG()
            embedding_manager = EmbeddingManager()

            embedding_manager.data_formatter(
                eval_id=eval_template.id,
                row_dict=trace_data,
                inputs_formater=[observation_span.id],
                organization_id=observation_span.project.organization.id,
                workspace_id=(
                    observation_span.project.workspace.id
                    if observation_span.project.workspace
                    else None
                ),
            )
            embedding_manager.close()

            return self._gm.success_response({"feedback_id": str(feedback.id)})
        except Exception as e:
            logger.exception(f"Error in submitting the feedback: {str(e)}")
            return self._gm.bad_request(
                f"Error submitting feedback: {get_error_message('FAILED_TO_CREATE_FEEDBACK')}"
            )

    @action(detail=False, methods=["post"], url_path="update-tags")
    def update_tags(self, request, *args, **kwargs):
        """Update tags for an observation span."""
        try:
            span_id = request.data.get("span_id")
            if not span_id:
                return self._gm.bad_request("span_id is required")
            span = ObservationSpan.objects.get(
                id=span_id,
                project__organization=getattr(request, "organization", None)
                or request.user.organization,
            )
            tags = request.data.get("tags")
            if tags is None:
                return self._gm.bad_request("tags field is required")
            if not isinstance(tags, list):
                return self._gm.bad_request("tags must be a list")
            span.tags = tags
            span.save(update_fields=["tags"])
            return self._gm.success_response({"id": str(span.id), "tags": span.tags})
        except ObservationSpan.DoesNotExist:
            return self._gm.bad_request("Observation span not found")
        except Exception as e:
            logger.exception(f"Error updating span tags: {e}")
            return self._gm.bad_request("Error updating tags")

    @action(detail=False, methods=["post"])
    def submit_feedback_action_type(self, request, *args, **kwargs):
        try:
            serializer = SubmitFeedbackActionTypeSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            validated_data = serializer.validated_data
            observation_span_id = validated_data.get("observation_span_id", None)
            action_type = validated_data.get("action_type", None)
            custom_eval_config_id = validated_data.get("custom_eval_config_id", None)
            feedback_id = validated_data.get("feedback_id", None)

            try:
                feedback = Feedback.objects.get(
                    id=feedback_id, user=request.user, source_id=observation_span_id
                )
                feedback.action_type = action_type
                feedback.save(update_fields=["action_type"])
            except Feedback.DoesNotExist:
                raise Exception("Feedback not found")  # noqa: B904

            try:
                observation_span = ObservationSpan.objects.get(
                    id=observation_span_id,
                    project__organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
            except ObservationSpan.DoesNotExist:
                raise Exception("Observation span not found")  # noqa: B904

            try:
                custom_eval_config = CustomEvalConfig.objects.get(
                    id=custom_eval_config_id,
                    project__organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
            except CustomEvalConfig.DoesNotExist:
                raise Exception("Custom eval config not found")  # noqa: B904

            if action_type == "retune":
                pass  ### This is coz we are using mapping_fields fxn in utils

            elif action_type == "recalculate":
                try:
                    eval_logger = EvalLogger.objects.get(
                        observation_span=observation_span,
                        custom_eval_config=custom_eval_config,
                        deleted=False,
                    )
                    task_id = eval_logger.eval_task_id

                    eval_logger.deleted = True
                    eval_logger.deleted_at = timezone.now()
                    eval_logger.save(update_fields=["deleted", "deleted_at"])
                except EvalLogger.DoesNotExist:
                    raise Exception("No eval associated with this span")  # noqa: B904

                properties = get_mixpanel_properties(
                    user=request.user,
                    span=observation_span,
                    eval=custom_eval_config.eval_template,
                    count=1,
                    type=MixpanelTypes.FEEDBACK.value,
                )
                track_mixpanel_event(MixpanelEvents.EVAL_RUN_STARTED.value, properties)

                if observation_span.project_version:
                    status = evaluate_observation_span(
                        str(observation_span.id),
                        str(custom_eval_config.id),
                        task_id,
                        feedback_id,
                    )
                else:
                    status = evaluate_observation_span_observe(
                        str(observation_span.id),
                        str(custom_eval_config.id),
                        task_id,
                        feedback_id,
                    )

                if status:
                    count = 1
                    failed = 0
                else:
                    failed = 1
                    count = 0
                properties = get_mixpanel_properties(
                    user=request.user,
                    span=observation_span,
                    eval=custom_eval_config.eval_template,
                    count=count,
                    failed=failed,
                    type=MixpanelTypes.FEEDBACK.value,
                )
                track_mixpanel_event(
                    MixpanelEvents.EVAL_RUN_COMPLETED.value, properties
                )

            return self._gm.success_response(
                {"message": "Action type submitted successfully"}
            )
        except Exception as e:
            logger.exception(f"Error in submitting the feedback action type: {str(e)}")
            return self._gm.bad_request(
                f"Error submitting feedback action type: {str(e)}"
            )

    @action(detail=False, methods=["get"])
    def list_spans_observe(self, request, *args, **kwargs):
        try:
            query_data = {"filters": request.query_params.get("filters", "[]")}
            if query_data["filters"]:
                query_data["filters"] = json.loads(query_data["filters"])
            serializer = SpanExportSerializer(data=query_data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            validated_data = serializer.validated_data
            export = kwargs.get("export", False) if kwargs else False

            project_id = self.request.query_params.get(
                "project_id"
            ) or self.request.query_params.get("projectId")
            user_id = request.query_params.get("user_id") or request.query_params.get(
                "userId"
            )
            if not project_id:
                raise Exception("Project id is required")

            project = Project.objects.get(
                id=project_id,
                organization=getattr(self.request, "organization", None)
                or self.request.user.organization,
            )

            # ClickHouse dispatch
            from tracer.services.clickhouse.query_service import (
                AnalyticsQueryService,
                QueryType,
            )

            analytics = AnalyticsQueryService()
            if analytics.should_use_clickhouse(QueryType.SPAN_LIST):
                try:
                    return self._list_spans_clickhouse(
                        request, project_id, validated_data, analytics
                    )
                except Exception as e:
                    logger.warning(
                        "CH span list failed, falling back to PG", error=str(e)
                    )

            # Get pagination parameters
            page_number = int(self.request.query_params.get("page_number", 0)) or int(
                self.request.query_params.get("pageNumber", 0)
            )
            page_size = int(self.request.query_params.get("page_size", 30)) or int(
                self.request.query_params.get("pageSize", 30)
            )

            end_user_id = None
            if user_id:
                try:
                    end_user_id = str(
                        EndUser.objects.get(
                            user_id=user_id,
                            organization=getattr(request, "organization", None)
                            or request.user.organization,
                            project=project,
                        ).id
                    )
                except EndUser.DoesNotExist as e:
                    raise Exception("User not found for the given user_id") from e

            # Base query with annotations
            base_query = ObservationSpan.objects.filter(
                project_id=project_id,
                project__organization=getattr(request, "organization", None)
                or request.user.organization,
            ).select_related("trace")

            if end_user_id:
                base_query = base_query.filter(end_user_id=end_user_id)

            base_query = base_query.annotate(
                node_type=F("observation_type"),
                span_id=F("id"),
                span_name=F("name"),
                user_id=F("end_user__user_id"),
                user_id_type=F("end_user__user_id_type"),
                user_id_hash=F("end_user__user_id_hash"),
            )

            # Get all eval configs for the project
            eval_configs = CustomEvalConfig.objects.filter(
                id__in=EvalLogger.objects.filter(
                    observation_span__project_id=project_id,
                    observation_span__project__organization=getattr(
                        request, "organization", None
                    )
                    or request.user.organization,
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
                        observation_span_id=OuterRef("id"),
                        custom_eval_config_id=config.id,
                        observation_span__project__organization=getattr(
                            request, "organization", None
                        )
                        or request.user.organization,
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
                                        observation_span_id=OuterRef("id"),
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
                                        observation_span_id=OuterRef("id"),
                                        custom_eval_config_id=config.id,
                                        output_bool__isnull=False,
                                    )
                                ),
                                then=JSONObject(
                                    score=Subquery(metric_subquery.values("bool_score"))
                                ),
                            ),
                            When(
                                Exists(
                                    EvalLogger.objects.filter(
                                        observation_span_id=OuterRef("id"),
                                        custom_eval_config_id=config.id,
                                        output_str_list__isnull=False,
                                    )
                                ),
                                then=Subquery(metric_subquery.values("str_list_score")),
                            ),
                            default=None,
                            output_field=JSONField(),
                        ),
                    }
                )

            # Add Span Annotations
            annotation_labels = AnnotationsLabels.objects.filter(
                project__id=project_id,
                project__organization=getattr(request, "organization", None)
                or request.user.organization,
            )
            base_query = build_annotation_subqueries(
                base_query,
                annotation_labels,
                request.user.organization,
                span_filter_kwargs={"observation_span_id": OuterRef("id")},
            )

            # Apply filters - combine all filter conditions for better performance
            filters = validated_data.get("filters", [])
            if filters:
                # Combine all filter conditions into a single Q object
                combined_filter_conditions = Q()

                # Get system metric filters
                system_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_system_metrics(filters)
                )
                if system_filter_conditions:
                    combined_filter_conditions &= system_filter_conditions

                # Separate annotation filters from eval filters since
                # annotations are JSON objects
                annotation_col_types = {"ANNOTATION"}
                annotation_column_ids = {"my_annotations", "annotator"}
                non_annotation_filters = [
                    f
                    for f in filters
                    if f.get("col_type") not in annotation_col_types
                    and (f.get("column_id") or f.get("columnId"))
                    not in annotation_column_ids
                ]

                # Get eval metric filters (excluding annotation filters)
                eval_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_non_system_metrics(
                        non_annotation_filters
                    )
                )
                if eval_filter_conditions:
                    combined_filter_conditions &= eval_filter_conditions

                # Get annotation filters (score, annotator, my_annotations)
                annotation_filter_conditions, extra_annotations = (
                    FilterEngine.get_filter_conditions_for_voice_call_annotations(
                        filters,
                        user_id=request.user.id,
                        span_filter_kwargs={"observation_span_id": OuterRef("id")},
                    )
                )
                if extra_annotations:
                    base_query = base_query.annotate(**extra_annotations)
                if annotation_filter_conditions:
                    combined_filter_conditions &= annotation_filter_conditions

                # Get span attribute filters
                span_attribute_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_span_attributes(filters)
                )
                if span_attribute_filter_conditions:
                    combined_filter_conditions &= span_attribute_filter_conditions

                # Get has_eval filter (only spans with evals)
                has_eval_condition = FilterEngine.get_filter_conditions_for_has_eval(
                    filters, observe_type="span"
                )
                if has_eval_condition:
                    combined_filter_conditions &= has_eval_condition

                # Apply has_annotation filter
                has_annotation_condition = (
                    FilterEngine.get_filter_conditions_for_has_annotation(
                        filters, observe_type="span"
                    )
                )
                if has_annotation_condition:
                    combined_filter_conditions &= has_annotation_condition

                # Apply combined filters in a single operation
                if combined_filter_conditions:
                    base_query = base_query.filter(combined_filter_conditions)

            base_query = base_query.order_by("-start_time", "-id")

            # Get total count before pagination
            total_count = base_query.count()

            # Apply pagination
            start = page_number * page_size
            base_query = base_query if export else base_query[start : start + page_size]

            # Prepare column config
            column_config = get_default_span_config()
            column_config.append(
                asdict(
                    FieldConfig(
                        id="user_id", name="User Id", is_visible=True, group_by=None
                    )
                )
            )
            column_config.append(
                asdict(
                    FieldConfig(
                        id="user_id_type",
                        name="User Id Type",
                        is_visible=False,
                        group_by=None,
                    )
                )
            )
            column_config.append(
                asdict(
                    FieldConfig(
                        id="user_id_hash",
                        name="User Id Hash",
                        is_visible=False,
                        group_by=None,
                    )
                )
            )
            column_config.append(
                asdict(
                    FieldConfig(
                        id="latency_ms",
                        name="Latency (ms)",
                        is_visible=True,
                        group_by=None,
                    )
                )
            )
            column_config.append(
                asdict(
                    FieldConfig(
                        id="total_tokens",
                        name="Total Tokens",
                        is_visible=False,
                        group_by=None,
                    )
                )
            )
            column_config.append(
                asdict(
                    FieldConfig(id="cost", name="Cost", is_visible=True, group_by=None)
                )
            )
            column_config = update_column_config_based_on_eval_config(
                column_config, eval_configs
            )
            column_config = update_span_column_config_based_on_annotations(
                column_config, annotation_labels
            )

            # Process results
            table_data = []
            for span in base_query:
                result = {
                    "span_id": str(span.id),
                    "input": span.input,
                    "output": span.output,
                    "trace_id": str(span.trace.id),
                    "created_at": span.created_at.isoformat() + "Z",
                    "node_type": span.node_type or "",
                    "span_name": span.name,
                    "user_id": span.end_user.user_id if span.end_user else None,
                    "user_id_type": (
                        span.end_user.user_id_type if span.end_user else None
                    ),
                    "user_id_hash": (
                        span.end_user.user_id_hash if span.end_user else None
                    ),
                    "start_time": span.start_time,
                    "status": span.status,
                    "latency_ms": span.latency_ms,
                    "total_tokens": span.total_tokens,
                    "cost": round(span.cost, 6) if span.cost else 0,
                }

                # Add eval metrics from annotated fields
                for config in eval_configs:
                    data = getattr(span, f"metric_{config.id}")
                    if data and "score" in data:
                        result[str(config.id)] = data["score"]
                    elif data:
                        for key, value in data.items():
                            result[str(config.id) + "**" + key] = value["score"]

                for label in annotation_labels:
                    ann_data = getattr(span, f"annotation_{label.id}", None)
                    if ann_data is not None:
                        result[str(label.id)] = ann_data

                # Include span attributes as flat keys for custom columns
                # Skip large values (raw I/O, messages) to keep response size manageable
                if span.span_attributes and isinstance(span.span_attributes, dict):
                    _SKIP_ATTR_PREFIXES = (
                        "raw.",
                        "llm.input_messages",
                        "llm.output_messages",
                        "input.value",
                        "output.value",
                    )
                    for key, value in span.span_attributes.items():
                        if key not in result and not key.startswith(
                            _SKIP_ATTR_PREFIXES
                        ):
                            if isinstance(value, str) and len(value) > 500:
                                result[key] = value[:500] + "..."
                            else:
                                result[key] = value

                table_data.append(result)

            response = {
                "metadata": {"total_rows": total_count},
                "table": table_data,
                "config": column_config,
            }

            return self._gm.success_response(response)

        except Exception as e:
            logger.exception(f"Error in fetching the spans list of observe: {str(e)}")
            return self._gm.bad_request(
                f"error fetching the spans list of observe {str(e)}"
            )

    def _list_spans_clickhouse(self, request, project_id, validated_data, analytics):
        """List spans using ClickHouse backend."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        filters = validated_data.get("filters", [])
        page_number = int(request.query_params.get("page_number", 0)) or int(
            request.query_params.get("pageNumber", 0)
        )
        page_size = int(request.query_params.get("page_size", 30)) or int(
            request.query_params.get("pageSize", 30)
        )

        user_id = request.query_params.get("user_id") or request.query_params.get(
            "userId"
        )
        end_user_id = None
        if user_id:
            try:
                end_user_id = str(
                    EndUser.objects.get(
                        user_id=user_id,
                        organization=request.user.organization,
                        project_id=project_id,
                    ).id
                )
            except EndUser.DoesNotExist:
                raise Exception("User not found for the given user_id")

        # Resolve in-filter user_id (string) → end_user_id UUIDs. Spans table
        # keys end-users by UUID; a raw `user_id = 'customer_001'` clause
        # references a non-existent CH column and crashes the query. Rewrite
        # the filter to `end_user_id` scoped to this project + organization.
        _resolved: List[Dict] = []
        for _f in filters:
            _col = _f.get("column_id") or _f.get("columnId")
            _cfg = _f.get("filter_config") or _f.get("filterConfig") or {}
            _col_type = _cfg.get("col_type") or _cfg.get("colType") or "NORMAL"
            if _col == "user_id" and _col_type == "NORMAL":
                _val = _cfg.get("filter_value", _cfg.get("filterValue"))
                _vals = _val if isinstance(_val, list) else [_val]
                _vals = [v for v in _vals if v]
                if not _vals:
                    _resolved.append(_f)
                    continue
                _ids = [
                    str(u) for u in EndUser.objects.filter(
                        user_id__in=_vals,
                        organization=request.user.organization,
                        project_id=project_id,
                        deleted=False,
                    ).values_list("id", flat=True)
                ]
                if not _ids:
                    _ids = ["00000000-0000-0000-0000-000000000000"]
                _resolved.append({
                    "column_id": "end_user_id",
                    "filter_config": {
                        "col_type": "NORMAL",
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": _ids,
                    },
                })
                continue
            _resolved.append(_f)
        filters = _resolved

        # Get eval config IDs from CH (fast) instead of PG EvalLogger scan
        eval_config_ids = []
        ch_result = analytics.execute_ch_query(
            "SELECT DISTINCT toString(custom_eval_config_id) AS cid "
            "FROM tracer_eval_logger FINAL "
            "WHERE _peerdb_is_deleted = 0 "
            "AND dictGet('trace_dict', 'project_id', "
            "trace_id) = toUUID(%(pid)s)",
            {"pid": str(project_id)},
            timeout_ms=30000,
        )
        ch_ids = [r.get("cid", "") for r in ch_result.data if r.get("cid")]
        if ch_ids:
            eval_configs = CustomEvalConfig.objects.filter(
                id__in=ch_ids, deleted=False
            ).select_related("eval_template")
            eval_config_ids = [str(c.id) for c in eval_configs]
        else:
            eval_configs = []

        # Get annotation labels from PG (small config table)
        annotation_labels = AnnotationsLabels.objects.filter(
            project__id=project_id, project__organization=request.user.organization
        )
        annotation_label_ids = [str(l.id) for l in annotation_labels]
        label_types = {str(l.id): l.type for l in annotation_labels}

        builder = SpanListQueryBuilder(
            project_id=str(project_id),
            filters=filters,
            page_number=page_number,
            page_size=page_size,
            eval_config_ids=eval_config_ids,
            annotation_label_ids=annotation_label_ids,
            end_user_id=end_user_id,
        )

        # Phase 1: Paginated spans (light columns — no input/output)
        query, params = builder.build()
        result = analytics.execute_ch_query(query, params, timeout_ms=10000)

        # Truncate to page_size (query fetches page_size+1 for has_more detection)
        has_more = len(result.data) > page_size
        if has_more:
            result.data = result.data[:page_size]

        # Phase 1b: Fetch input/output/span_attributes_raw for the page
        span_ids = [str(row.get("id", "")) for row in result.data]
        if span_ids:
            content_query, content_params = builder.build_content_query(span_ids)
            if content_query:
                content_result = analytics.execute_ch_query(
                    content_query, content_params, timeout_ms=10000
                )
                content_map = {str(r.get("id", "")): r for r in content_result.data}
                for row in result.data:
                    c = content_map.get(str(row.get("id", "")), {})
                    row["input"] = c.get("input", "")
                    row["output"] = c.get("output", "")
                    row["span_attributes_raw"] = c.get("span_attributes_raw", "{}")

        # Count
        count_query, count_params = builder.build_count_query()
        count_result = analytics.execute_ch_query(
            count_query, count_params, timeout_ms=5000
        )
        total_count = count_result.data[0].get("total", 0) if count_result.data else 0

        # Phase 2: Eval scores
        eval_map = {}
        if span_ids and eval_config_ids:
            eval_query, eval_params = builder.build_eval_query(span_ids)
            if eval_query:
                eval_result = analytics.execute_ch_query(
                    eval_query, eval_params, timeout_ms=5000
                )
                eval_map = SpanListQueryBuilder.pivot_eval_results(eval_result.data)

        # Phase 3: Annotations
        annotation_map = {}
        if span_ids and annotation_label_ids:
            ann_query, ann_params = builder.build_annotation_query(span_ids)
            if ann_query:
                ann_result = analytics.execute_ch_query(
                    ann_query, ann_params, timeout_ms=5000
                )
                annotation_map = SpanListQueryBuilder.pivot_annotation_results(
                    ann_result.data, label_types
                )

        # Build column config (from PG config tables)
        column_config = get_default_span_config()
        column_config.append(
            asdict(
                FieldConfig(
                    id="user_id", name="User Id", is_visible=True, group_by=None
                )
            )
        )
        column_config.append(
            asdict(
                FieldConfig(
                    id="user_id_type",
                    name="User Id Type",
                    is_visible=False,
                    group_by=None,
                )
            )
        )
        column_config.append(
            asdict(
                FieldConfig(
                    id="user_id_hash",
                    name="User Id Hash",
                    is_visible=False,
                    group_by=None,
                )
            )
        )
        column_config.append(
            asdict(
                FieldConfig(
                    id="latency_ms", name="Latency (ms)", is_visible=True, group_by=None
                )
            )
        )
        column_config.append(
            asdict(
                FieldConfig(
                    id="total_tokens",
                    name="Total Tokens",
                    is_visible=False,
                    group_by=None,
                )
            )
        )
        column_config.append(
            asdict(FieldConfig(id="cost", name="Cost", is_visible=True, group_by=None))
        )
        column_config = update_column_config_based_on_eval_config(
            column_config, eval_configs
        )
        column_config = update_span_column_config_based_on_annotations(
            column_config, annotation_labels
        )

        # Batch-resolve end_user UUIDs → (user_id, user_id_type,
        # user_id_hash) so each row can surface the human-readable user
        # identifier. CH only stores the UUID; the display fields live on
        # the PG EndUser table.
        end_user_ids = {
            str(r.get("end_user_id"))
            for r in result.data
            if r.get("end_user_id")
        }
        end_user_map = {}
        if end_user_ids:
            end_user_map = {
                str(eu.id): eu
                for eu in EndUser.objects.filter(id__in=end_user_ids).only(
                    "id", "user_id", "user_id_type", "user_id_hash"
                )
            }

        # Format response matching PG format
        table_data = []
        for row in result.data:
            span_id = str(row.get("id", ""))
            cost = row.get("cost")
            eu = end_user_map.get(str(row.get("end_user_id"))) if row.get(
                "end_user_id"
            ) else None
            entry = {
                "span_id": span_id,
                "input": row.get("input", ""),
                "output": row.get("output", ""),
                "trace_id": str(row.get("trace_id", "")),
                "created_at": row.get("created_at"),
                "node_type": row.get("observation_type", ""),
                "span_name": row.get("name", ""),
                "user_id": getattr(eu, "user_id", None) if eu else None,
                "user_id_type": getattr(eu, "user_id_type", None) if eu else None,
                "user_id_hash": getattr(eu, "user_id_hash", None) if eu else None,
                "start_time": row.get("start_time"),
                "status": row.get("status"),
                "latency_ms": row.get("latency_ms"),
                "total_tokens": row.get("total_tokens"),
                "cost": round(cost, 6) if cost else 0,
            }

            # Add eval metrics
            span_evals = eval_map.get(span_id, {})
            for config in eval_configs:
                config_id = str(config.id)
                if config_id not in span_evals:
                    continue
                val = span_evals[config_id]
                # CHOICES eval: spread per-choice percentages into separate
                # columns keyed ``{config_id}**{choice}`` to match the
                # column config produced by
                # ``update_column_config_based_on_eval_config``.
                if (
                    isinstance(val, dict)
                    and not val.get("error")
                    and val
                ):
                    for choice, pct in val.items():
                        entry[f"{config_id}**{choice}"] = pct
                else:
                    entry[config_id] = val
                    if isinstance(value, dict):
                        entry[config_id] = value.get("score")
                    else:
                        entry[config_id] = value

            # Add annotations
            span_annotations = annotation_map.get(span_id, {})
            for label in annotation_labels:
                label_id = str(label.id)
                if label_id in span_annotations:
                    entry[label_id] = span_annotations[label_id]

            # Include span attributes for custom columns
            raw_attrs = row.get("span_attributes_raw", "{}")
            try:
                attrs = (
                    json.loads(raw_attrs)
                    if isinstance(raw_attrs, str)
                    else (raw_attrs or {})
                )
            except (json.JSONDecodeError, TypeError):
                attrs = {}
            _SKIP_ATTR_PREFIXES = (
                "raw.",
                "llm.input_messages",
                "llm.output_messages",
                "input.value",
                "output.value",
            )
            for key, value in attrs.items():
                if key not in entry and not key.startswith(_SKIP_ATTR_PREFIXES):
                    if isinstance(value, str) and len(value) > 500:
                        entry[key] = value[:500] + "..."
                    else:
                        entry[key] = value

            table_data.append(entry)

        response = {
            "metadata": {"total_rows": total_count},
            "table": table_data,
            "config": column_config,
        }

        return self._gm.success_response(response)

    def _list_spans_non_observe_clickhouse(
        self, request, project_version_id, project_version, analytics
    ):
        """List spans (non-observe, prompt version/eval task views) using ClickHouse backend."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        filters_raw = self.request.query_params.get(
            "filters", []
        ) or self.request.query_params.get("filters", [])
        filters = (
            json.loads(filters_raw)
            if isinstance(filters_raw, str) and filters_raw
            else []
        )

        page_number = int(self.request.query_params.get("page_number", 0)) or int(
            self.request.query_params.get("pageNumber", 0)
        )
        page_size = int(self.request.query_params.get("page_size", 30)) or int(
            self.request.query_params.get("pageSize", 30)
        )

        project_id = str(project_version.project_id)

        # Get eval configs from PG (small config table)
        eval_configs = CustomEvalConfig.objects.filter(
            id__in=EvalLogger.objects.filter(
                observation_span__project_id=project_id,
            )
            .values("custom_eval_config_id")
            .distinct(),
            deleted=False,
        ).select_related("eval_template")
        eval_config_ids = [str(c.id) for c in eval_configs]

        # Get annotation labels from PG (small config table)
        annotation_labels = AnnotationsLabels.objects.filter(project__id=project_id)
        annotation_label_ids = [str(l.id) for l in annotation_labels]
        label_types = {str(l.id): l.type for l in annotation_labels}

        builder = SpanListQueryBuilder(
            project_id=project_id,
            filters=filters,
            page_number=page_number,
            page_size=page_size,
            eval_config_ids=eval_config_ids,
            annotation_label_ids=annotation_label_ids,
            project_version_id=str(project_version_id),
        )

        # Phase 1: Paginated spans (light columns — no input/output)
        query, params = builder.build()
        result = analytics.execute_ch_query(query, params, timeout_ms=10000)

        # Truncate to page_size (query fetches page_size+1 for has_more detection)
        has_more = len(result.data) > page_size
        if has_more:
            result.data = result.data[:page_size]

        # Phase 1b: Fetch input/output for the page
        span_ids = [str(row.get("id", "")) for row in result.data]
        if span_ids:
            content_query, content_params = builder.build_content_query(span_ids)
            if content_query:
                content_result = analytics.execute_ch_query(
                    content_query, content_params, timeout_ms=10000
                )
                content_map = {str(r.get("id", "")): r for r in content_result.data}
                for row in result.data:
                    c = content_map.get(str(row.get("id", "")), {})
                    row["input"] = c.get("input", "")
                    row["output"] = c.get("output", "")

        # Count
        count_query, count_params = builder.build_count_query()
        count_result = analytics.execute_ch_query(
            count_query, count_params, timeout_ms=5000
        )
        total_count = count_result.data[0].get("total", 0) if count_result.data else 0

        # Phase 2: Eval scores
        eval_map = {}
        if span_ids and eval_config_ids:
            eval_query, eval_params = builder.build_eval_query(span_ids)
            if eval_query:
                eval_result = analytics.execute_ch_query(
                    eval_query, eval_params, timeout_ms=5000
                )
                eval_map = SpanListQueryBuilder.pivot_eval_results(eval_result.data)

        # Phase 3: Annotations
        annotation_map = {}
        if span_ids and annotation_label_ids:
            ann_query, ann_params = builder.build_annotation_query(span_ids)
            if ann_query:
                ann_result = analytics.execute_ch_query(
                    ann_query, ann_params, timeout_ms=5000
                )
                annotation_map = SpanListQueryBuilder.pivot_annotation_results(
                    ann_result.data, label_types
                )

        # Build column config
        column_config = get_default_span_config()
        column_config = update_column_config_based_on_eval_config(
            column_config, eval_configs
        )
        column_config = update_span_column_config_based_on_annotations(
            column_config, annotation_labels
        )

        # Format response matching PG format
        table_data = []
        for row in result.data:
            span_id = str(row.get("id", ""))
            entry = {
                "node_type": row.get("observation_type", ""),
                "span_id": span_id,
                "input": row.get("input", ""),
                "output": row.get("output", ""),
                "trace_id": str(row.get("trace_id", "")),
                "span_name": row.get("name", ""),
                "start_time": row.get("start_time"),
                "status": row.get("status"),
            }

            # Add eval metrics
            span_evals = eval_map.get(span_id, {})
            for config in eval_configs:
                config_id = str(config.id)
                if config_id not in span_evals:
                    continue
                val = span_evals[config_id]
                if (
                    isinstance(val, dict)
                    and not val.get("error")
                    and not val.get("score")
                    and val
                ):
                    for choice, pct in val.items():
                        entry[f"{config_id}**{choice}"] = pct
                elif isinstance(val, dict):
                    entry[config_id] = val.get("score")
                else:
                    entry[config_id] = val

            # Add annotations
            span_annotations = annotation_map.get(span_id, {})
            for label in annotation_labels:
                label_id = str(label.id)
                if label_id in span_annotations:
                    entry[label_id] = span_annotations[label_id]

            table_data.append(entry)

        response = {
            "column_config": column_config,
            "metadata": {"total_rows": total_count},
            "table": table_data,
        }

        return self._gm.success_response(response)

    @action(detail=False, methods=["post"])
    def get_graph_methods(self, request, *args, **kwargs):
        """
        Fetch data for the observe graph with optimized queries
        """
        try:
            project_id = self.request.data.get("project_id", None)
            if not project_id:
                raise Exception("Project id is required")

            project = Project.objects.get(
                id=project_id,
                organization=getattr(self.request, "organization", None)
                or self.request.user.organization,
            )
            if project.trace_type != "observe":
                raise Exception("Project should be of type observe")

            # Base query with annotations
            base_query = (
                ObservationSpan.objects.filter(
                    project_id=project_id,
                    project__organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
                .select_related("trace")
                .annotate(
                    node_type=F("observation_type"),
                    span_id=F("id"),
                    span_name=F("name"),
                    user_id=F("end_user__user_id"),
                )
            )

            # Get all eval configs for the project
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
                        observation_span_id=OuterRef("id"),
                        custom_eval_config_id=config.id,
                        observation_span__project__organization=getattr(
                            request, "organization", None
                        )
                        or request.user.organization,
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
                                        observation_span_id=OuterRef("id"),
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
                                        observation_span_id=OuterRef("id"),
                                        custom_eval_config_id=config.id,
                                        output_bool__isnull=False,
                                    )
                                ),
                                then=JSONObject(
                                    score=Subquery(metric_subquery.values("bool_score"))
                                ),
                            ),
                            When(
                                Exists(
                                    EvalLogger.objects.filter(
                                        observation_span_id=OuterRef("id"),
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

            # Add Span Annotations (read from unified Score model)
            annotation_labels = get_annotation_labels_for_project(project_id)
            for label in annotation_labels:
                score_qs = Score.objects.filter(
                    observation_span_id=OuterRef("id"),
                    label_id=label.id,
                    deleted=False,
                )
                if label.type == AnnotationTypeChoices.NUMERIC.value:
                    subq = score_qs.annotate(
                        _val=Cast(KeyTextTransform("value", "value"), FloatField())
                    ).values("_val")[:1]
                elif label.type == AnnotationTypeChoices.STAR.value:
                    subq = score_qs.annotate(
                        _val=Cast(KeyTextTransform("rating", "value"), FloatField())
                    ).values("_val")[:1]
                elif label.type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
                    subq = score_qs.annotate(
                        _val=KeyTextTransform("value", "value")
                    ).values("_val")[:1]
                elif label.type == AnnotationTypeChoices.CATEGORICAL.value:
                    subq = score_qs.values("value__selected")[:1]
                else:
                    subq = score_qs.annotate(
                        _val=KeyTextTransform("text", "value")
                    ).values("_val")[:1]

                base_query = base_query.annotate(
                    **{f"annotation_{label.id}": Subquery(subq)}
                )

            # Apply filters - combine all filter conditions for better performance
            filters = self.request.data.get("filters", [])
            if filters:
                # Combine all filter conditions into a single Q object
                combined_filter_conditions = Q()

                # Get system metric filters
                system_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_system_metrics(filters)
                )
                if system_filter_conditions:
                    combined_filter_conditions &= system_filter_conditions

                # Get non-system metric filters (excluding span attributes)
                eval_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_non_system_metrics(filters)
                )
                if eval_filter_conditions:
                    combined_filter_conditions &= eval_filter_conditions

                # Get span attribute filters
                span_attribute_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_span_attributes(filters)
                )
                if span_attribute_filter_conditions:
                    combined_filter_conditions &= span_attribute_filter_conditions

                # Get has_eval filter (only spans with evals)
                has_eval_condition = FilterEngine.get_filter_conditions_for_has_eval(
                    filters, observe_type="span"
                )
                if has_eval_condition:
                    combined_filter_conditions &= has_eval_condition

                # Apply has_annotation filter
                has_annotation_condition = (
                    FilterEngine.get_filter_conditions_for_has_annotation(
                        filters, observe_type="span"
                    )
                )
                if has_annotation_condition:
                    combined_filter_conditions &= has_annotation_condition

                # Apply combined filters in a single operation
                if combined_filter_conditions:
                    base_query = base_query.filter(combined_filter_conditions)

            # Default sorting by created_at
            base_query = base_query.order_by("-created_at")

            total_final_span_queryset = base_query

            # Get parameters
            property = self.request.data.get("property", "count")
            interval = self.request.data.get("interval", "hour")

            req_data_config = self.request.data.get("req_data_config", None)

            if not req_data_config:
                return self._gm.bad_request("Req data config property is required")

            type = req_data_config.get("type", None)
            if type not in ["EVAL", "ANNOTATION", "SYSTEM_METRIC"]:
                return self._gm.bad_request("Filter property type is not valid")

            # ClickHouse dispatch for span graphs
            from tracer.services.clickhouse.query_builders import (
                EvalMetricsQueryBuilder,
                TimeSeriesQueryBuilder,
            )
            from tracer.services.clickhouse.query_service import (
                AnalyticsQueryService,
                QueryType,
            )

            analytics = AnalyticsQueryService()

            if type == "SYSTEM_METRIC" and analytics.should_use_clickhouse(
                QueryType.SPAN_GRAPH
            ):
                try:
                    metric_id = req_data_config.get("id", "latency")
                    builder = TimeSeriesQueryBuilder(
                        project_id=str(project_id),
                        filters=filters,
                        interval=interval,
                        metric_name=metric_id,
                    )
                    query, params = builder.build()
                    result = analytics.execute_ch_query(query, params, timeout_ms=5000)
                    ch_data = builder.format_result(result.data, result.columns or [])
                    metric_key = metric_id if metric_id in ch_data else "latency"
                    metric_points = ch_data.get(metric_key, [])
                    traffic_points = ch_data.get("traffic", [])
                    traffic_by_ts = {
                        t.get("timestamp"): t.get("traffic", 0) for t in traffic_points
                    }
                    graph_data = {
                        "metric_name": metric_id,
                        "data": [
                            {
                                "timestamp": p.get("timestamp"),
                                "value": p.get("value", 0),
                                "primary_traffic": traffic_by_ts.get(
                                    p.get("timestamp"), 0
                                ),
                            }
                            for p in metric_points
                        ],
                    }
                    return self._gm.success_response(graph_data)
                except Exception as e:
                    logger.warning(
                        "CH span system-metric graph failed, falling back to PG",
                        error=str(e),
                    )

            if type == "EVAL" and analytics.should_use_clickhouse(QueryType.SPAN_GRAPH):
                try:
                    eval_config_id = req_data_config.get("id")
                    eval_output_type = req_data_config.get("eval_output_type", "SCORE")
                    choices = req_data_config.get("choices", [])
                    builder = EvalMetricsQueryBuilder(
                        project_id=str(project_id),
                        custom_eval_config_id=str(eval_config_id),
                        filters=filters,
                        interval=interval,
                        eval_output_type=eval_output_type,
                        choices=choices,
                    )
                    query, params = builder.build()
                    result = analytics.execute_ch_query(query, params, timeout_ms=5000)
                    graph_data = builder.format_result(
                        result.data, result.columns or []
                    )
                    return self._gm.success_response(graph_data)
                except Exception as e:
                    logger.warning(
                        "CH span eval graph failed, falling back to PG", error=str(e)
                    )

            if type == "EVAL":
                graph_data = get_eval_graph_data(
                    interval=interval,
                    filters=filters,
                    property=property,
                    observe_type="span",
                    req_data_config=req_data_config,
                    eval_logger_filters={
                        "span_ids_queryset": total_final_span_queryset
                    },
                )

            elif type == "ANNOTATION":
                graph_data = get_annotation_graph_data(
                    interval=interval,
                    filters=filters,
                    property=property,
                    observe_type="span",
                    req_data_config=req_data_config,
                    annotation_logger_filters={
                        "span_ids_queryset": total_final_span_queryset
                    },
                )

            elif type == "SYSTEM_METRIC":
                graph_data = get_system_metric_data(
                    interval=interval,
                    filters=filters,
                    property=property,
                    req_data_config=req_data_config,
                    system_metric_filters={
                        "span_ids_queryset": total_final_span_queryset
                    },
                    observe_type="span",
                )

            if not graph_data:
                # Add debug information
                logger.info(
                    f"""
                    Graph data empty with params:
                    - Project ID: {project_id}
                    - Property: {property}
                    - Interval: {interval}
                """
                )

            return self._gm.success_response(graph_data)

        except Exception as e:
            logger.exception(f"Error in fetching graph data: {str(e)}")
            return self._gm.bad_request(f"Error fetching graph data: {str(e)}")

    @action(detail=False, methods=["get"])
    def get_span_attributes_list(self, request, *args, **kwargs):
        """Distinct span_attributes keys for a project (spans surface).

        Query params:
            filters: JSON {"project_id": "<uuid>"} (required)

        Returns:
            List of attribute key strings.
        """
        try:
            filters = self.request.query_params.get("filters", "{}")
            if filters:
                filters = json.loads(filters)

            project_id = filters.get("project_id")
            if not project_id:
                return self._gm.bad_request("project_id is required")

            result = self._get_span_attribute_keys(project_id)
            return self._gm.success_response(result)

        except Exception as e:
            logger.exception(f"error fetching span attributes list: {str(e)}")
            return self._gm.bad_request(
                f"error fetching the span attributes list {str(e)}"
            )

    @action(detail=False, methods=["get"])
    def get_eval_attributes_list(self, request, *args, **kwargs):
        """Attribute paths the EvalPicker exposes per row_type.

        Query params:
            filters: JSON {"project_id": "<uuid>"} (required)
            row_type: spans | traces | sessions (default spans;
                      voiceCalls aliases to spans)

        Returns:
            spans/voiceCalls: distinct span_attributes keys
            traces:           trace fields + spans.<n>.<key>
            sessions:         session fields + traces.<i>.<trace_field>
                              + traces.<i>.spans.<j>.<key>

        Indexed positions are sized to the project's observed maxes;
        ordering of ``traces.<i>`` / ``spans.<n>`` slots is decided at
        resolve time (see ``_resolve_session_path`` / ``_resolve_trace_path``).
        """
        try:
            filters = self.request.query_params.get("filters", "{}")
            if filters:
                filters = json.loads(filters)

            project_id = filters.get("project_id")
            if not project_id:
                return self._gm.bad_request("project_id is required")

            row_type = self.request.query_params.get("row_type", "spans")

            if row_type == "spans" or row_type == "voiceCalls":
                # voiceCalls share the spans surface for the picker; they
                # have their own evaluator pipeline upstream of EvalTask.
                return self.get_span_attributes_list(request, *args, **kwargs)

            span_attribute_keys = self._get_span_attribute_keys(project_id)

            if row_type == "traces":
                paths = self._build_trace_attribute_paths(
                    project_id, span_attribute_keys
                )
                return self._gm.success_response(paths)

            if row_type == "sessions":
                paths = self._build_session_attribute_paths(
                    project_id, span_attribute_keys
                )
                return self._gm.success_response(paths)

            return self._gm.bad_request(
                f"Unknown row_type {row_type!r}. Expected one of: "
                "spans, traces, sessions, voiceCalls."
            )

        except Exception as e:
            logger.exception(f"error fetching eval attributes list: {str(e)}")
            return self._gm.bad_request(
                f"error fetching the eval attributes list {str(e)}"
            )

    # Trace + session model fields the resolver allow-lists; mirrors the
    # frozensets in tracer.utils.eval. Hand-synced so a model change shows
    # up in both places at review time.
    _TRACE_PUBLIC_FIELDS = (
        "input",
        "output",
        "name",
        "error",
        "tags",
        "metadata",
        "external_id",
    )
    _SESSION_PUBLIC_FIELDS = ("name", "bookmarked")

    # Cap on how many entities to scan when computing observed maxes.
    # Most projects' traces have a few-to-dozens of spans; bounding the
    # sample keeps the path enumeration query cheap.
    _OBSERVED_MAX_SAMPLE_SIZE = 100

    def _get_span_attribute_keys(self, project_id: str) -> list:
        """Project's distinct span_attributes keys. CH-first, PG fallback.

        Single source for both ``get_span_attributes_list`` (which wraps
        it in a DRF response) and the trace + session path builders.

        CH returns ``[{"key": ..., "type": ...}, ...]`` (spans picker
        renders type chips); the trace + session path builders need
        bare strings. The normalization loop below collapses both
        shapes to ``list[str]`` so callers never see dicts f-stringed
        into paths like ``traces.0.spans.0.{'key': '...', ...}``.
        """
        raw = None
        analytics = AnalyticsQueryService()
        if analytics.should_use_clickhouse(QueryType.SPAN_LIST):
            try:
                ch_result = analytics.get_span_attribute_keys_ch(str(project_id))
                if ch_result:
                    raw = ch_result
            except Exception as ch_err:
                logger.warning(
                    "CH span attribute keys failed in get_eval_attributes_list, "
                    "falling back to PG",
                    error=str(ch_err),
                )

        if raw is None:
            raw = SQL_query_handler.get_span_attributes_for_project(project_id)

        keys = []
        for item in raw or []:
            if isinstance(item, dict):
                k = item.get("key")
                if k:
                    keys.append(k)
            elif isinstance(item, str) and item:
                keys.append(item)
        return keys

    def _max_spans_per_trace(self, project_id: str) -> int:
        """Max span count observed across the project's most recent traces.

        Bounds the indexed positions exposed under ``spans.<n>.<...>``.
        Samples the most recent ``_OBSERVED_MAX_SAMPLE_SIZE`` traces to
        keep the aggregate cheap on large projects.
        """
        sample_trace_ids = Trace.objects.filter(
            project_id=project_id
        ).order_by("-created_at").values_list("id", flat=True)[
            : self._OBSERVED_MAX_SAMPLE_SIZE
        ]
        agg = (
            ObservationSpan.objects.filter(trace_id__in=sample_trace_ids)
            .values("trace_id")
            .annotate(span_count=Count("id"))
            .aggregate(max_count=Max("span_count"))
        )
        return agg["max_count"] or 0

    def _max_traces_per_session(self, project_id: str) -> int:
        """Max trace count observed across the project's most recent sessions."""
        sample_session_ids = TraceSession.objects.filter(
            project_id=project_id
        ).order_by("-created_at").values_list("id", flat=True)[
            : self._OBSERVED_MAX_SAMPLE_SIZE
        ]
        agg = (
            Trace.objects.filter(session_id__in=sample_session_ids)
            .values("session_id")
            .annotate(trace_count=Count("id"))
            .aggregate(max_count=Max("trace_count"))
        )
        return agg["max_count"] or 0

    def _build_trace_attribute_paths(
        self, project_id: str, span_attribute_keys: list
    ) -> list:
        """Trace-level paths: trace fields + ``spans.<n>.<key>`` for each
        index up to the observed max spans-per-trace."""
        paths = list(self._TRACE_PUBLIC_FIELDS)
        max_spans = self._max_spans_per_trace(project_id)
        for i in range(max_spans):
            for key in span_attribute_keys:
                paths.append(f"spans.{i}.{key}")
        return paths

    def _build_session_attribute_paths(
        self, project_id: str, span_attribute_keys: list
    ) -> list:
        """Session-level paths: session fields + ``traces.<i>.<trace_field>``
        + ``traces.<i>.spans.<j>.<key>`` up to the observed max traces-per-
        session and spans-per-trace."""
        paths = list(self._SESSION_PUBLIC_FIELDS)
        max_traces = self._max_traces_per_session(project_id)
        max_spans = self._max_spans_per_trace(project_id)
        for i in range(max_traces):
            for trace_field in self._TRACE_PUBLIC_FIELDS:
                paths.append(f"traces.{i}.{trace_field}")
            for j in range(max_spans):
                for key in span_attribute_keys:
                    paths.append(f"traces.{i}.spans.{j}.{key}")
        return paths

    @action(detail=False, methods=["get"])
    def get_observation_span_fields(self, request, *args, **kwargs):
        try:
            # Get fields from observation span model
            fields = []
            for field in ObservationSpan._meta.get_fields():
                field_type = field.get_internal_type()

                # Map Django field types to DataTypeChoices
                if field_type == "JSONField":
                    field_type = DataTypeChoices.JSON.value
                elif field_type == "CharField" or field_type == "TextField":
                    field_type = DataTypeChoices.TEXT.value
                elif field_type == "BooleanField":
                    field_type = DataTypeChoices.BOOLEAN.value
                elif field_type == "IntegerField":
                    field_type = DataTypeChoices.INTEGER.value
                elif field_type == "FloatField" or field_type == "DecimalField":
                    field_type = DataTypeChoices.FLOAT.value
                elif field_type == "ArrayField":
                    field_type = DataTypeChoices.ARRAY.value
                elif field_type == "DateTimeField":
                    field_type = DataTypeChoices.DATETIME.value
                else:
                    field_type = DataTypeChoices.OTHERS.value

                fields.append({"name": field.name, "type": field_type})

            # Add virtual field for child spans (not a model field)
            fields.append({"name": "child_spans", "type": DataTypeChoices.JSON.value})

            return self._gm.success_response(fields)

        except Exception as e:
            logger.exception(f"Error in getting observation span fields: {str(e)}")
            return self._gm.bad_request(
                f"Error getting observation span fields: {str(e)}"
            )

    def _get_evaluation_details_clickhouse(
        self, observation_span_id, custom_eval_config_id, analytics
    ):
        """Get evaluation details from ClickHouse."""
        query = """
            SELECT
                output_float,
                output_bool,
                output_str_list,
                output_str,
                eval_explanation,
                error,
                error_message,
                output_metadata
            FROM tracer_eval_logger FINAL
            WHERE observation_span_id = %(span_id)s
              AND custom_eval_config_id = %(config_id)s
              AND target_type = 'span'
              AND _peerdb_is_deleted = 0
              AND (deleted = 0 OR deleted IS NULL)
            LIMIT 1
        """
        result = analytics.execute_ch_query(
            query,
            {
                "span_id": str(observation_span_id),
                "config_id": str(custom_eval_config_id),
            },
            timeout_ms=5000,
        )

        if not result.data:
            return self._gm.bad_request(
                "No eval logger found for the given observation span id and custom eval config id"
            )

        row = result.data[0]

        output_metadata = row.get("output_metadata")
        if not output_metadata or not isinstance(output_metadata, dict):
            output_metadata = {}

        # Handle error case — consistent with retrieve() and _retrieve_clickhouse()
        if row.get("error") or row.get("output_str") == "ERROR":
            return self._gm.success_response(
                {
                    "error_analysis": output_metadata.get("error_analysis"),
                    "selected_input_key": output_metadata.get("selected_input_key"),
                    "input_data": output_metadata.get("input_data"),
                    "input_types": output_metadata.get("input_types"),
                    "score": None,
                    "explanation": row.get("error_message"),
                    "error": True,
                }
            )

        evaluation_result = (
            row.get("output_bool")
            if row.get("output_bool") is not None
            else (
                row.get("output_float")
                if row.get("output_float") is not None
                else row.get("output_str_list")
            )
        )
        evaluation_explanation = (
            row.get("eval_explanation")
            if row.get("eval_explanation")
            else row.get("error_message")
        )

        return self._gm.success_response(
            {
                "error_analysis": output_metadata.get("error_analysis"),
                "selected_input_key": output_metadata.get("selected_input_key"),
                "input_data": output_metadata.get("input_data"),
                "input_types": output_metadata.get("input_types"),
                "score": evaluation_result,
                "explanation": evaluation_explanation,
            }
        )

    @action(detail=False, methods=["get"])
    def get_evaluation_details(self, request, *args, **kwargs):
        try:
            observation_span_id = self.request.query_params.get(
                "observation_span_id", None
            )
            custom_eval_config_id = self.request.query_params.get(
                "custom_eval_config_id", None
            )

            if not observation_span_id or not custom_eval_config_id:
                return self._gm.bad_request(
                    "Observation span id and custom eval config id are required"
                )

            # ClickHouse dispatch
            from tracer.services.clickhouse.query_service import (
                AnalyticsQueryService,
                QueryType,
            )

            analytics = AnalyticsQueryService()
            if analytics.should_use_clickhouse(QueryType.TRACE_DETAIL):
                try:
                    return self._get_evaluation_details_clickhouse(
                        observation_span_id, custom_eval_config_id, analytics
                    )
                except Exception as e:
                    logger.warning(
                        "CH eval details failed, falling back to PG", error=str(e)
                    )

            eval_logger = EvalLogger.objects.filter(
                observation_span_id=observation_span_id,
                custom_eval_config_id=custom_eval_config_id,
            ).first()

            if not eval_logger:
                return self._gm.bad_request(
                    "No eval logger found for the given observation span id and custom eval config id"
                )

            output_metadata = eval_logger.output_metadata

            if not output_metadata or not isinstance(output_metadata, dict):
                output_metadata = {}

            if eval_logger.error or eval_logger.output_str == "ERROR":
                return self._gm.success_response(
                    {
                        "error_analysis": output_metadata.get("error_analysis"),
                        "selected_input_key": output_metadata.get("selected_input_key"),
                        "input_data": output_metadata.get("input_data"),
                        "input_types": output_metadata.get("input_types"),
                        "score": None,
                        "explanation": eval_logger.error_message,
                        "error": True,
                    }
                )

            evaluation_result = (
                eval_logger.output_bool
                if eval_logger.output_bool is not None
                else (
                    eval_logger.output_float
                    if eval_logger.output_float is not None
                    else eval_logger.output_str_list
                )
            )
            evaluation_explanation = (
                eval_logger.eval_explanation
                if eval_logger.eval_explanation
                else eval_logger.error_message
            )

            result = {
                "error_analysis": output_metadata.get("error_analysis", None),
                "selected_input_key": output_metadata.get("selected_input_key", None),
                "input_data": output_metadata.get("input_data", None),
                "input_types": output_metadata.get("input_types", None),
                "score": evaluation_result,
                "explanation": evaluation_explanation,
            }

            return self._gm.success_response(result)

        except Exception as e:
            return self._gm.bad_request(
                f"error fetching the eval attributes list {str(e)}"
            )

    @action(detail=False, methods=["get"])
    def get_spans_export_data(self, request, *args, **kwargs):
        try:
            response = self.list_spans_observe(request, export=True)

            if response.status_code != 200:
                return response

            project_id = self.request.query_params.get(
                "project_id"
            ) or self.request.query_params.get("projectId")
            project = Project.objects.get(
                id=project_id,
                organization=getattr(self.request, "organization", None)
                or self.request.user.organization,
            )

            result = response.data.get("result")
            table_data = result.get("table", None)

            df = pd.DataFrame(table_data)

            # Convert to CSV buffer
            buffer = io.BytesIO()
            df.to_csv(buffer, index=False, encoding="utf-8")
            buffer.seek(0)

            # Create the response with the file
            filename = f"{project.name or 'project'}_spans.csv"
            response = FileResponse(
                buffer, as_attachment=True, filename=filename, content_type="text/csv"
            )

            return response

        except Exception as e:
            logger.exception(f"Error in exporting the spans list of observe: {str(e)}")
            return self._gm.bad_request(get_error_message(""))

    @action(detail=False, methods=["post"])
    def add_annotations(self, request, *args, **kwargs):
        try:
            observation_span_id = self.request.data.get("observation_span_id")
            annotation_values = self.request.data.get("annotation_values")
            trace_id = self.request.data.get("trace_id")
            notes = self.request.data.get("notes")

            if (not observation_span_id and not trace_id) or not annotation_values:
                raise Exception(
                    "Observation span id and annotation values are required"
                )

            try:
                if observation_span_id:
                    observation_span = ObservationSpan.objects.get(
                        id=observation_span_id,
                        project__organization=getattr(request, "organization", None)
                        or request.user.organization,
                    )
                elif trace_id:
                    observation_span = ObservationSpan.objects.get(
                        trace_id=trace_id,
                        project__organization=getattr(request, "organization", None)
                        or request.user.organization,
                        parent_span_id__isnull=True,
                    )
            except ObservationSpan.DoesNotExist:
                raise Exception("Observation span not found")  # noqa: B904

            failed_labels = []
            success_labels = []
            for label_id, given_annotation_value in annotation_values.items():
                try:
                    try:
                        annotation_label = AnnotationsLabels.objects.get(
                            id=label_id,
                            organization=getattr(request, "organization", None)
                            or request.user.organization,
                        )
                    except AnnotationsLabels.DoesNotExist:
                        raise Exception("Annotation label not found")  # noqa: B904

                    annotation_type = annotation_label.type

                    # Validate annotation value against label type and settings
                    from tracer.utils.annotation_validation import (
                        validate_annotation_value as validate_ann_value,
                    )

                    validation_error = _validate_add_annotation_value(
                        validate_ann_value,
                        annotation_type,
                        annotation_label.settings,
                        given_annotation_value,
                    )
                    if validation_error:
                        failed_labels.append(label_id)
                        continue

                    score_value = _to_score_value(
                        annotation_type, given_annotation_value
                    )

                    # Write to unified Score model.
                    # Use no_workspace_objects + _id fields to avoid the
                    # LEFT JOIN on nullable workspace FK that triggers
                    # PostgreSQL's "FOR UPDATE cannot be applied to the
                    # nullable side of an outer join".
                    Score.no_workspace_objects.update_or_create(
                        observation_span_id=observation_span.pk,
                        label_id=annotation_label.pk,
                        annotator_id=request.user.pk,
                        deleted=False,
                        defaults={
                            "source_type": "observation_span",
                            "value": score_value,
                            "score_source": "human",
                            "notes": notes or "",
                            "organization": request.user.organization,
                        },
                    )

                    success_labels.append(label_id)

                    # update projectversion annotations

                    if observation_span.project_version is not None:
                        annotation = observation_span.project_version.annotations
                        if annotation is not None:
                            annotation.labels.add(annotation_label)
                            annotation.save()
                        else:
                            annotation = Annotations.objects.create(
                                organization=getattr(request, "organization", None)
                                or request.user.organization,
                                name=f"Annotation for {observation_span.project_version.name}",
                            )
                            annotation.labels.add(annotation_label)
                            observation_span.project_version.annotations = annotation
                            observation_span.project_version.save()
                except AnnotationsLabels.DoesNotExist:
                    failed_labels.append(label_id)

            # Auto-create queue items for default queues and auto-complete (bidirectional sync)
            if success_labels:
                try:
                    _auto_create_queue_items_for_default_queues(
                        "observation_span", observation_span, success_labels
                    )
                except Exception:
                    logger.exception(
                        "Error in auto-creating queue items for default queues"
                    )
                try:
                    _auto_complete_queue_items(
                        "observation_span", observation_span, request.user
                    )
                except Exception:
                    logger.exception("Error in auto-completing queue items")

            if notes:
                try:
                    span_note = SpanNotes.objects.get(
                        span=observation_span, created_by_user=request.user
                    )
                    span_note.notes = notes
                    span_note.save(update_fields=["notes"])
                except SpanNotes.DoesNotExist:
                    SpanNotes.objects.create(
                        span=observation_span,
                        notes=notes,
                        created_by_user=request.user,
                        created_by_annotator=str(request.user.id),
                    )

            return self._gm.success_response(
                {
                    "id": str(observation_span.id),
                    "failed_labels": failed_labels,
                    "success_labels": success_labels,
                }
            )
        except Exception as e:
            logger.exception(f"Error in adding annotations: {str(e)}")

            return self._gm.bad_request(
                f"Error adding annotations: {get_error_message('FAILED_TO_ADD_ANNOTATIONS')}"
            )

    @action(detail=False, methods=["delete"])
    def delete_annotation_label(self, request, *args, **kwargs):
        try:
            label_id = self.request.query_params.get("label_id")
            if not label_id:
                return self._gm.bad_request("label_id query parameter is required")
            label = AnnotationsLabels.objects.get(
                id=label_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
            )
            # Check if label is in use by active annotation tasks
            if Annotations.objects.filter(labels=label_id, deleted=False).exists():
                return self._gm.bad_request(
                    "Cannot delete label: it is in use by active annotation tasks"
                )
            label.delete()
            Score.objects.filter(label_id=label_id).update(deleted=True)

            return self._gm.success_response(
                {"message": "Annotation label deleted successfully"}
            )
        except AnnotationsLabels.DoesNotExist:
            return self._gm.bad_request("Annotation label not found")
        except Exception as e:
            return self._gm.bad_request(f"error deleting the annotation label {str(e)}")

    @action(detail=False, methods=["get"])
    def get_trace_id_by_index_spans_as_base(self, request, *args, **kwargs):
        """
        Get the previous and next span id by index for non-observe projects.
        Mirrors the query/filter logic of list_spans.
        """
        try:
            span_id = request.query_params.get("span_id") or request.query_params.get(
                "spanId"
            )
            if not span_id:
                raise Exception("Span id is required")

            project_version_id = request.query_params.get(
                "project_version_id"
            ) or request.query_params.get("projectVersionId")
            if not project_version_id:
                raise Exception("Project version id is required")

            project_version = ProjectVersion.objects.get(
                id=project_version_id,
                project__organization=getattr(request, "organization", None)
                or request.user.organization,
            )

            base_query = ObservationSpan.objects.filter(
                project_version_id=project_version_id,
                project__organization=getattr(request, "organization", None)
                or request.user.organization,
            ).annotate(
                node_type=F("observation_type"),
                span_id=F("id"),
                span_name=F("name"),
            )

            eval_configs = CustomEvalConfig.objects.filter(
                id__in=EvalLogger.objects.filter(
                    observation_span__project_id=project_version.project.id
                )
                .values("custom_eval_config_id")
                .distinct(),
                deleted=False,
            ).select_related("eval_template")

            for config in eval_configs:
                choices = (
                    config.eval_template.choices
                    if config.eval_template.choices
                    else None
                )
                metric_subquery = (
                    EvalLogger.objects.filter(
                        observation_span_id=OuterRef("id"),
                        custom_eval_config_id=config.id,
                        observation_span__project__organization=getattr(
                            request, "organization", None
                        )
                        or request.user.organization,
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
                                        observation_span_id=OuterRef("id"),
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
                                        observation_span_id=OuterRef("id"),
                                        custom_eval_config_id=config.id,
                                        output_bool__isnull=False,
                                    )
                                ),
                                then=JSONObject(
                                    score=Subquery(metric_subquery.values("bool_score"))
                                ),
                            ),
                            When(
                                Exists(
                                    EvalLogger.objects.filter(
                                        observation_span_id=OuterRef("id"),
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

            annotation_labels = get_annotation_labels_for_project(
                project_version.project.id
            )
            base_query = build_annotation_subqueries(
                base_query,
                annotation_labels,
                request.user.organization,
                span_filter_kwargs={"observation_span_id": OuterRef("id")},
            )

            filters = request.query_params.get("filters", [])
            if filters:
                try:
                    filters = json.loads(filters)
                except json.JSONDecodeError as e:
                    return self._gm.bad_request(
                        f"Invalid JSON format in filters parameter: {str(e)}"
                    )

                combined_filter_conditions = Q()

                system_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_system_metrics(filters)
                )
                if system_filter_conditions:
                    combined_filter_conditions &= system_filter_conditions

                annotation_col_types = {"ANNOTATION"}
                annotation_column_ids = {"my_annotations", "annotator"}
                non_annotation_filters = [
                    f
                    for f in filters
                    if f.get("col_type") not in annotation_col_types
                    and (f.get("column_id") or f.get("columnId"))
                    not in annotation_column_ids
                ]

                eval_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_non_system_metrics(
                        non_annotation_filters
                    )
                )
                if eval_filter_conditions:
                    combined_filter_conditions &= eval_filter_conditions

                annotation_filter_conditions, extra_annotations = (
                    FilterEngine.get_filter_conditions_for_voice_call_annotations(
                        filters,
                        user_id=request.user.id,
                        span_filter_kwargs={"observation_span_id": OuterRef("id")},
                    )
                )
                if extra_annotations:
                    base_query = base_query.annotate(**extra_annotations)
                if annotation_filter_conditions:
                    combined_filter_conditions &= annotation_filter_conditions

                span_attribute_conditions = (
                    FilterEngine.get_filter_conditions_for_span_attributes(filters)
                )
                if span_attribute_conditions:
                    combined_filter_conditions &= span_attribute_conditions

                if combined_filter_conditions:
                    base_query = base_query.filter(combined_filter_conditions)

            base_query = base_query.order_by("-start_time", "-id")

            current_span = base_query.filter(id=span_id).values("start_time").first()
            if not current_span:
                raise Exception("Span not found in the list")

            previous_trace = None
            next_trace = None

            if current_span["start_time"] is not None:
                previous_trace = (
                    base_query.filter(start_time__lt=current_span["start_time"])
                    .order_by("-start_time")
                    .values_list("trace_id", flat=True)
                    .first()
                )
                next_trace = (
                    base_query.filter(start_time__gt=current_span["start_time"])
                    .order_by("start_time")
                    .values_list("trace_id", flat=True)
                    .first()
                )

            response = {
                "next_trace_id": str(previous_trace) if previous_trace else None,
                "previous_trace_id": str(next_trace) if next_trace else None,
            }

            return self._gm.success_response(response)

        except Exception as e:
            logger.exception(f"Error fetching span id by index: {str(e)}")
            return self._gm.bad_request(f"error fetching the span id by index {str(e)}")

    @action(detail=False, methods=["get"])
    def get_trace_id_by_index_spans_as_observe(self, request, *args, **kwargs):
        """
        Get the previous and next trace id by index for observe projects.
        Mirrors the query/filter logic of list_spans_as_observe.
        """
        try:
            span_id = request.query_params.get("span_id") or request.query_params.get(
                "spanId"
            )
            if not span_id:
                raise Exception("Span id is required")

            user_id = request.query_params.get("user_id") or request.query_params.get(
                "userId"
            )

            end_user_id = None
            if user_id:
                try:
                    end_user_id = str(
                        EndUser.objects.get(
                            user_id=user_id,
                            organization=getattr(request, "organization", None)
                            or request.user.organization,
                            project=project,
                        ).id
                    )
                except EndUser.DoesNotExist as e:
                    raise Exception("User not found for the given user_id") from e

            project_id = request.query_params.get(
                "project_id"
            ) or request.query_params.get("projectId")
            if not project_id:
                raise Exception("Project id is required")

            project = Project.objects.get(
                id=project_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
            )
            if project.trace_type not in ("observe", "experiment"):
                raise Exception("Project should be of type observe or experiment")

            base_query = ObservationSpan.objects.filter(
                project_id=project_id,
                project__organization=getattr(request, "organization", None)
                or request.user.organization,
            ).annotate(
                node_type=F("observation_type"),
                span_id=F("id"),
                span_name=F("name"),
                user_id=F("end_user__user_id"),
                user_id_type=F("end_user__user_id_type"),
                user_id_hash=F("end_user__user_id_hash"),
            )

            if end_user_id:
                base_query = base_query.filter(end_user_id=end_user_id)

            eval_configs = CustomEvalConfig.objects.filter(
                id__in=EvalLogger.objects.filter(
                    observation_span__project_id=project_id,
                    observation_span__project__organization=getattr(
                        request, "organization", None
                    )
                    or request.user.organization,
                )
                .values("custom_eval_config_id")
                .distinct(),
                deleted=False,
            ).select_related("eval_template")

            for config in eval_configs:
                choices = (
                    config.eval_template.choices
                    if config.eval_template.choices
                    else None
                )
                metric_subquery = (
                    EvalLogger.objects.filter(
                        observation_span_id=OuterRef("id"),
                        custom_eval_config_id=config.id,
                        observation_span__project__organization=getattr(
                            request, "organization", None
                        )
                        or request.user.organization,
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
                                        observation_span_id=OuterRef("id"),
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
                                        observation_span_id=OuterRef("id"),
                                        custom_eval_config_id=config.id,
                                        output_bool__isnull=False,
                                    )
                                ),
                                then=JSONObject(
                                    score=Subquery(metric_subquery.values("bool_score"))
                                ),
                            ),
                            When(
                                Exists(
                                    EvalLogger.objects.filter(
                                        observation_span_id=OuterRef("id"),
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

            annotation_labels = AnnotationsLabels.objects.filter(
                project__id=project_id,
                project__organization=getattr(request, "organization", None)
                or request.user.organization,
            )
            base_query = build_annotation_subqueries(
                base_query,
                annotation_labels,
                request.user.organization,
                span_filter_kwargs={"observation_span_id": OuterRef("id")},
            )

            filters = request.query_params.get("filters", "[]")
            try:
                if filters:
                    filters = json.loads(filters)
            except json.JSONDecodeError as e:
                return self._gm.bad_request(
                    f"Invalid JSON format in filters parameter: {str(e)}"
                )

            if filters:
                combined_filter_conditions = Q()

                system_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_system_metrics(filters)
                )
                if system_filter_conditions:
                    combined_filter_conditions &= system_filter_conditions

                annotation_col_types = {"ANNOTATION"}
                annotation_column_ids = {"my_annotations", "annotator"}
                non_annotation_filters = [
                    f
                    for f in filters
                    if f.get("col_type") not in annotation_col_types
                    and (f.get("column_id") or f.get("columnId"))
                    not in annotation_column_ids
                ]

                eval_filter_conditions = (
                    FilterEngine.get_filter_conditions_for_non_system_metrics(
                        non_annotation_filters
                    )
                )
                if eval_filter_conditions:
                    combined_filter_conditions &= eval_filter_conditions

                annotation_filter_conditions, extra_annotations = (
                    FilterEngine.get_filter_conditions_for_voice_call_annotations(
                        filters,
                        user_id=request.user.id,
                        span_filter_kwargs={"observation_span_id": OuterRef("id")},
                    )
                )
                if extra_annotations:
                    base_query = base_query.annotate(**extra_annotations)
                if annotation_filter_conditions:
                    combined_filter_conditions &= annotation_filter_conditions

                span_attribute_conditions = (
                    FilterEngine.get_filter_conditions_for_span_attributes(filters)
                )
                if span_attribute_conditions:
                    combined_filter_conditions &= span_attribute_conditions

                has_eval_condition = FilterEngine.get_filter_conditions_for_has_eval(
                    filters, observe_type="span"
                )
                if has_eval_condition:
                    combined_filter_conditions &= has_eval_condition

                # Apply has_annotation filter
                has_annotation_condition = (
                    FilterEngine.get_filter_conditions_for_has_annotation(
                        filters, observe_type="span"
                    )
                )
                if has_annotation_condition:
                    combined_filter_conditions &= has_annotation_condition

                if combined_filter_conditions:
                    base_query = base_query.filter(combined_filter_conditions)

            base_query = base_query.order_by("-start_time", "-id")

            current_span = base_query.filter(id=span_id).values("start_time").first()
            if not current_span:
                raise Exception("Span not found in the list")

            previous_trace = None
            next_trace = None

            if current_span["start_time"] is not None:
                previous_trace = (
                    base_query.filter(start_time__lt=current_span["start_time"])
                    .order_by("-start_time")
                    .values_list("trace_id", flat=True)
                    .first()
                )
                next_trace = (
                    base_query.filter(start_time__gt=current_span["start_time"])
                    .order_by("start_time")
                    .values_list("trace_id", flat=True)
                    .first()
                )

            response = {
                "next_trace_id": str(previous_trace) if previous_trace else None,
                "previous_trace_id": str(next_trace) if next_trace else None,
            }

            return self._gm.success_response(response)

        except Exception as e:
            logger.exception(f"Error fetching span id by index (observe): {str(e)}")
            return self._gm.bad_request(f"error fetching the span id by index {str(e)}")


def get_observation_spans(filters):
    """
    Fetch an observation span based on its ID.
    Filters is a required object that must contain the following fields:
    - project_id (optional)
    - project_version_id (optional)
    - trace_id (optional)
    """
    project_id = filters.get("project_id", None)
    project_version_id = filters.get("project_version_id", None)
    trace_id = filters.get("trace_id", None)

    if not project_id and not project_version_id and not trace_id:
        raise Exception(
            "At least one of the following fields is required: observation_span_id, project_id, project_version_id, trace_id."
        )

    base_filters = {
        "project": project_id,
        "project_version": project_version_id,
        "trace": trace_id,
    }
    base_filters = {k: v for k, v in base_filters.items() if v is not None}

    response_data = []

    # Process actual parent spans
    response_data.extend(_process_parent_spans(base_filters))

    # Process orphaned spans
    response_data.extend(_process_orphaned_spans(base_filters))

    return response_data


def fetch_children_span_ids(root_span: ObservationSpan):
    try:
        rows = SQL_query_handler.fetch_children_ids_query(str(root_span.id))

        result_ids = [str(row[0]) for row in rows]

        return result_ids

    except Exception as e:
        logger.exception(f"Error in fetching children span ids: {str(e)}")
        return []


def fetch_children(root_span: ObservationSpan):
    try:
        close_old_connections()

        span_map = {}  # span_id -> span data structure
        parent_map = {}  # span_id -> parent_id

        rows = SQL_query_handler.fetch_children_query(str(root_span.id))
        updated_rows = [
            {
                "id": row[0],
                "parent_span_id": row[1],
                "name": row[2],
                "observation_type": row[3],
                "prompt_tokens": row[4],
                "total_tokens": row[5],
                "latency_ms": row[6],
                "completion_tokens": row[7],
                "span_events": row[8],
                "trace_id": row[9],
                "cost": row[10],
            }
            for row in rows
        ]

        # Batch queries to reduce DB hits
        total_span_ids = [span["id"] for span in updated_rows]

        eval_counts = fetch_evals_count(total_span_ids)
        annotation_counts = fetch_annotation_count(total_span_ids)

        # Build span objects
        for span in updated_rows:
            data = span
            if data["cost"] and data["cost"] > 0:
                data["cost"] = round(data["cost"], 6)
            data["total_evals_count"] = eval_counts.get(span["id"], 0)
            data["total_annotations_count"] = annotation_counts.get(span["id"], 0)
            span_map[span["id"]] = {"observation_span": data, "children": []}
            parent_map[span["id"]] = span["parent_span_id"]

        # Build tree
        root_data = {
            "id": root_span.id,
            "name": root_span.name,
            "observation_type": root_span.observation_type,
            "prompt_tokens": root_span.prompt_tokens,
            "total_tokens": root_span.total_tokens,
            "latency_ms": root_span.latency_ms,
            "completion_tokens": root_span.completion_tokens,
            "span_events": root_span.span_events,
            "total_evals_count": eval_counts.get(root_span.id, 0),
            "total_annotations_count": annotation_counts.get(root_span.trace.id, 0),
            "trace_id": str(root_span.trace.id),
            "parent_span_id": str(root_span.parent_span_id),
            "cost": (
                round(root_span.cost, 6) if root_span.cost and root_span.cost > 0 else 0
            ),
        }
        root_node = {"observation_span": root_data, "children": []}
        span_map[root_span.id] = root_node

        for span_id, node in span_map.items():
            parent_id = parent_map.get(span_id)
            if parent_id is not None and parent_id in span_map:
                children_list = span_map[parent_id].get("children", [])
                if isinstance(children_list, list):
                    children_list.append(node)

        return root_node["children"]

    except Exception as e:
        logger.exception(f"Error in fetching children: {str(e)}")
    finally:
        close_old_connections()


def fetch_annotation_count(span_ids: list[str]):
    """
    Fetch annotation count for a list of span ids.

    Args:
        span_ids (list[str]): List of span ids
    Returns:
        dict: Dictionary mapping span id to annotation count
    """
    annotation_results = (
        Score.objects.filter(
            observation_span_id__in=span_ids,
            deleted=False,
        )
        .values("observation_span_id")
        .annotate(count=Count("id"))
    )

    return {row["observation_span_id"]: row["count"] for row in annotation_results}


def fetch_evals_count(span_ids: list[str]):
    """
    Fetch evals count for a list of span ids.

    Args:
        span_ids (list[str]): List of span ids
    Returns:
        dict: Dictionary mapping span id to evals count
    """
    eval_results = (
        EvalLogger.objects.filter(observation_span_id__in=span_ids)
        .values("observation_span_id")
        .annotate(count=Count("id"))
    )

    return {row["observation_span_id"]: row["count"] for row in eval_results}


def _process_parent_spans(base_filters):
    """
    Process spans that have no parent (root spans).

    Args:
        base_filters (dict): Base query filters

    Returns:
        list: List of observation span data with children
    """
    parent_filters = {**base_filters, "parent_span_id__isnull": True}
    parent_spans = ObservationSpan.objects.filter(**parent_filters).order_by(
        "start_time"
    )

    return [_build_span_response(parent_span) for parent_span in parent_spans]


def _process_orphaned_spans(base_filters):
    """
    Process orphaned spans (spans with missing parents) and create dummy parents.

    Args:
        base_filters (dict): Base query filters

    Returns:
        list: List of dummy parent spans with their orphaned children
    """
    orphaned_spans = _find_orphaned_spans(base_filters)
    if not orphaned_spans:
        return []

    orphaned_groups = _group_orphaned_spans_by_parent(orphaned_spans)
    return [
        _create_dummy_parent_response(parent_id, children, base_filters)
        for parent_id, children in orphaned_groups.items()
    ]


def _find_orphaned_spans(base_filters):
    """
    Find spans that reference non-existent parent spans.

    Args:
        base_filters (dict): Base query filters

    Returns:
        list: List of orphaned ObservationSpan objects
    """
    parent_exists = ObservationSpan.objects.filter(
        id=OuterRef("parent_span_id"), **base_filters
    )

    orphaned_spans = (
        ObservationSpan.objects.filter(**base_filters, parent_span_id__isnull=False)
        .annotate(parent_exists=Exists(parent_exists))
        .filter(parent_exists=False)
    )

    return list(orphaned_spans)


def _group_orphaned_spans_by_parent(orphaned_spans):
    """
    Group orphaned spans by their missing parent_span_id.

    Args:
        orphaned_spans (list): List of orphaned ObservationSpan objects

    Returns:
        dict: Dictionary mapping parent_id to list of child spans
    """
    orphaned_groups = defaultdict(list)
    for span in orphaned_spans:
        orphaned_groups[span.parent_span_id].append(span)
    return orphaned_groups


def _create_dummy_parent_response(missing_parent_id, child_spans, base_filters):
    """
    Create a dummy parent span response for orphaned children.

    Args:
        missing_parent_id (str): ID of the missing parent span
        child_spans (list): List of orphaned child spans
        base_filters (dict): Base query filters

    Returns:
        dict: Dummy parent span response with children
    """
    earliest_child = child_spans[0]

    dummy_parent_data = _create_dummy_parent_data(
        missing_parent_id, earliest_child, base_filters
    )

    dummy_children = [_build_span_response(child_span) for child_span in child_spans]

    return {"observation_span": dummy_parent_data, "children": dummy_children}


def _create_dummy_parent_data(missing_parent_id, reference_child, base_filters):
    """
    Create dummy parent span data structure.

    Args:
        missing_parent_id (str): ID of the missing parent span
        reference_child (ObservationSpan): Child span to inherit org data from
        base_filters (dict): Base query filters

    Returns:
        dict: Dummy parent span data
    """
    return {
        "id": missing_parent_id,
        "project": base_filters.get("project"),
        "project_version": base_filters.get("project_version"),
        "trace": base_filters.get("trace"),
        "parent_span_id": None,
        "name": f"[Missing Span] {missing_parent_id}",
        "observation_type": "unknown",
        "org_id": reference_child.org_id,
        "org_user_id": reference_child.org_user_id,
        "metadata": {"is_dummy": True, "reason": "Parent span not yet exported"},
    }


def _build_span_response(span):
    """
    Build span response with eval and annotation counts.

    Args:
        span (ObservationSpan): The observation span object

    Returns:
        dict: Span response with observation_span data and children
    """
    data = ObservationSpanSerializer(span).data

    if data["cost"] and data["cost"] > 0:
        data["cost"] = round(data["cost"], 6)

    data["total_evals_count"] = _get_evals_count(span.id)
    data["total_annotations_count"] = _get_annotations_count(span)

    if data["prompt_version"]:
        try:
            prompt_version = PromptVersion.objects.get(id=data["prompt_version"])
            data["prompt_template_id"] = str(prompt_version.original_template.id)
            data["prompt_name"] = (
                str(prompt_version.original_template.name)
                + " - "
                + str(prompt_version.template_version)
            )

        except PromptVersion.DoesNotExist:
            data["prompt_version"] = None

    return {"observation_span": data, "children": fetch_children(span)}


def _get_evals_count(span_id):
    """
    Get evaluation count for a span.

    Args:
        span_id (str): The span ID

    Returns:
        int: Number of evaluations
    """
    count = EvalLogger.objects.filter(observation_span_id=span_id).count()
    return count if count is not None else 0


def _get_annotations_count(span):
    """
    Get annotation count for a span.

    Args:
        span (ObservationSpan): The observation span object

    Returns:
        int: Number of annotations
    """
    count = Score.objects.filter(observation_span=span, deleted=False).count()
    return count if count is not None else 0
