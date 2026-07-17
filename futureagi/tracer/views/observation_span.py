import concurrent.futures
import hashlib
import io
import json
import uuid
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta

import pandas as pd
import structlog
from django.core.cache import cache as django_cache
from django.db import close_old_connections
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
from django.db.models.functions import JSONObject, Round
from django.http import FileResponse
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from agentic_eval.core.embeddings.embedding_manager import EmbeddingManager
from analytics.utils import (
    MixpanelEvents,
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
from tfc.utils.api_contracts import validated_request
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.span_notes import SpanNotes
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.serializers.filters import (
    ObserveGraphDataRequestSerializer,
    ObserveGraphDataResponseSerializer,
)
from tracer.serializers.observation_span import (
    ObservationAttributeListQuerySerializer,
    ObservationAttributeListResponseSerializer,
    ObservationSpanSerializer,
    SpanExportQuerySerializer,
    SpanIndexQuerySerializer,
    SpanListQuerySerializer,
    SpanObserveIndexQuerySerializer,
    SpanObserveListQuerySerializer,
    SubmitFeedbackActionTypeSerializer,
    SubmitFeedbackSerializer,
)
from tracer.serializers.trace import TraceSerializer
from tracer.services.clickhouse.graph_dispatch import (
    fetch_annotation_graph_ch,
    fetch_eval_graph_ch,
    fetch_system_metric_graph_ch,
)
from tracer.services.clickhouse.page_dedup import paginate_deduped
from tracer.services.clickhouse.query_service import AnalyticsQueryService
from tracer.services.clickhouse.v2.span_selectors import (
    flatten_span_attributes_into_entry,
    merge_content_rows,
)
from tracer.utils.annotations import build_annotation_subqueries
from tracer.utils.create_otel_span import create_single_otel_span
from tracer.utils.eval import (
    evaluate_observation_span,
    evaluate_observation_span_observe,
)
from tracer.utils.filters import FilterEngine
from tracer.utils.helper import (
    FieldConfig,
    get_annotation_labels_for_project,
    get_default_span_config,
    update_column_config_based_on_eval_config,
    update_span_column_config_based_on_annotations,
)
from tracer.utils.otel import (
    ResourceLimitError,
    calculate_cost_from_tokens,
)
from tracer.utils.sql_queries import SQL_query_handler

logger = structlog.get_logger(__name__)


class AddObservationSpanAnnotationsSerializer(serializers.Serializer):
    observation_span_id = serializers.CharField(required=False, allow_blank=True)
    trace_id = serializers.UUIDField(required=False)
    annotation_values = serializers.DictField(child=serializers.JSONField())
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs.get("observation_span_id") and not attrs.get("trace_id"):
            raise serializers.ValidationError(
                "observation_span_id or trace_id is required."
            )
        return attrs


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


def _get_request_organization(request):
    return getattr(request, "organization", None) or request.user.organization


def _project_workspace_scope_q(request, project_prefix="project__"):
    workspace = getattr(request, "workspace", None)
    if not workspace:
        return Q()

    workspace_field = f"{project_prefix}workspace"
    organization_field = f"{project_prefix}organization_id"
    organization_id = getattr(workspace, "organization_id", None) or getattr(
        _get_request_organization(request), "id", None
    )

    if getattr(workspace, "is_default", False):
        return (
            Q(**{workspace_field: workspace})
            | Q(
                **{
                    f"{workspace_field}__is_default": True,
                    f"{workspace_field}__organization_id": organization_id,
                }
            )
            | Q(
                **{
                    f"{workspace_field}__isnull": True,
                    organization_field: organization_id,
                }
            )
        )

    return Q(**{workspace_field: workspace})


def allowed_root_spans_for_request(
    trace_ids: list[str],
    *,
    organization,
    project_scope_q,
) -> dict[str, str]:
    """Resolve ``{trace_id: root_span_id}`` for *trace_ids*, returning only traces
    whose owning project is org/workspace-accessible. Collector traces have no PG
    ``Trace`` row, so the project_id is learned from CH and re-checked against the
    PG ``Project`` authority. FAIL CLOSED: an untenanted / cross-org trace is dropped
    (no key) — same response shape as before.
    """
    if not trace_ids:
        return {}

    from tracer.services.clickhouse.v2 import get_reader

    with get_reader() as reader:
        ch_spans = reader.list_by_trace_ids([str(tid) for tid in trace_ids])

    # Root spans only (CH stores parent_span_id as a non-nullable String; root
    # spans carry ""). Collect the candidate project_ids to verify against PG.
    root_spans = [s for s in ch_spans if not s.parent_span_id]
    candidate_project_ids = {str(s.project_id) for s in root_spans if s.project_id}
    if not candidate_project_ids:
        return {}

    allowed_project_ids = {
        str(pid)
        for pid in Project.objects.filter(
            project_scope_q,
            id__in=candidate_project_ids,
            organization=organization,
        ).values_list("id", flat=True)
    }
    if not allowed_project_ids:
        return {}

    result: dict[str, str] = {}
    for span in root_spans:
        pid = str(span.project_id) if span.project_id else None
        if pid is None or pid not in allowed_project_ids:
            # FAIL CLOSED: untenanted or cross-org span — never returned.
            continue
        tid = str(span.trace_id)
        # list_by_trace_ids orders by (trace_id, start_time, id) so the first
        # parentless span per trace wins.
        if tid not in result:
            result[tid] = str(span.id)
    return result


class ObservationSpanView(BaseModelViewSetMixin, ModelViewSet):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    serializer_class = ObservationSpanSerializer

    def get_queryset(self):
        observation_span_id = self.kwargs.get("pk")
        # Get base queryset with automatic filtering from mixin
        query_Set = (
            super()
            .get_queryset()
            .filter(project__organization=_get_request_organization(self.request))
        )

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

    @staticmethod
    def _to_iso(value):
        if not value:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)

    def _span_queryset_postgres(self, request, project_id, project_version_id=None):
        qs = ObservationSpan.no_workspace_objects.filter(
            _project_workspace_scope_q(request),
            project_id=project_id,
            project__organization=_get_request_organization(request),
        )
        if project_version_id:
            qs = qs.filter(project_version_id=project_version_id)
        return qs.select_related("trace", "end_user").order_by(
            "-start_time", "-created_at"
        )

    def _span_row_from_postgres(self, span):
        end_user = getattr(span, "end_user", None)
        return {
            "span_id": span.id,
            "input": span.input,
            "output": span.output,
            "trace_id": str(span.trace_id),
            "created_at": self._to_iso(span.created_at),
            "node_type": span.observation_type,
            "span_name": span.name,
            "user_id": getattr(end_user, "user_id", None) if end_user else None,
            "user_id_type": (
                getattr(end_user, "user_id_type", None) if end_user else None
            ),
            "user_id_hash": (
                getattr(end_user, "user_id_hash", None) if end_user else None
            ),
            "start_time": self._to_iso(span.start_time),
            "status": span.status,
            "latency_ms": span.latency_ms,
            "total_tokens": span.total_tokens,
            "prompt_tokens": span.prompt_tokens,
            "completion_tokens": span.completion_tokens,
            "model": span.model,
            "provider": span.provider,
            "cost": round(span.cost, 6) if span.cost else 0,
        }

    def _list_spans_postgres(
        self, request, project_id, validated_data, project_version_id=None
    ):
        qs = self._span_queryset_postgres(
            request, project_id, project_version_id=project_version_id
        )
        total_count = qs.count()
        page_number = validated_data.get("page_number", 0)
        page_size = validated_data.get("page_size", 30)
        start = page_number * page_size
        rows = [
            self._span_row_from_postgres(span) for span in qs[start : start + page_size]
        ]
        column_config = get_default_span_config()
        return self._gm.success_response(
            {
                "metadata": {"total_rows": total_count},
                "table": rows,
                "config": column_config,
                "column_config": column_config,
            }
        )

    @staticmethod
    def _metric_field(metric_id):
        return {
            "latency": "latency_ms",
            "avg_latency": "latency_ms",
            "latency_ms": "latency_ms",
            "tokens": "total_tokens",
            "total_tokens": "total_tokens",
            "prompt_tokens": "prompt_tokens",
            "completion_tokens": "completion_tokens",
            "cost": "cost",
        }.get(metric_id, metric_id)

    def _system_metric_graph_postgres(
        self, request, project_id, filters, interval, metric_id
    ):
        field_name = self._metric_field(metric_id)
        rows = []
        for span in self._span_queryset_postgres(request, project_id):
            value = getattr(span, field_name, None)
            if value is None:
                continue
            rows.append(
                {
                    "timestamp": self._to_iso(span.start_time or span.created_at),
                    "value": float(value),
                }
            )
        return {"metric_name": metric_id, "data": rows}

    def retrieve(self, request, *args, **kwargs):
        try:
            observation_span_id = kwargs.get("pk")

            # Cross-store tenant gate. CH `spans` rows don't carry org_id
            # filterability, so we enforce the project__organization scope
            # here in PG before any CH dispatch. KEEP-PG.
            try:
                ObservationSpan.objects.only("id").get(
                    _project_workspace_scope_q(request),
                    id=observation_span_id,
                    project__organization=_get_request_organization(request),
                )
            except ObservationSpan.DoesNotExist:
                logger.exception(
                    f"Observation span with id {observation_span_id} does not exist for this organization."
                )
                return self._gm.bad_request(
                    get_error_message("OBSERVATION_SPAN_NOT_FOUND")
                )

            # ClickHouse dispatch for span detail
            from tracer.services.clickhouse.query_service import (
                AnalyticsQueryService,
            )

            # CH-only path post-migration. The legacy ORM body that lived
            # below (PromptVersion lookup → EvalLogger scan → eval_metrics
            # pivot) was removed per D-027: CH is the authoritative span +
            # eval store, and the equivalent CH pivot lives in
            # `_retrieve_clickhouse`.
            analytics = AnalyticsQueryService()
            return self._retrieve_clickhouse(request, observation_span_id, analytics)
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
                tags, toJSONString(attributes_extra) AS span_attributes,
                span_events, provider,
                toJSONString(metadata) AS metadata_json,
                custom_eval_config_id,
                attrs_string, attrs_number, attrs_bool
            FROM spans
            WHERE id = %(span_id)s
              AND is_deleted = 0
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
            for k, v in (row.get("attrs_string") or {}).items():
                span_attrs[k] = v
            for k, v in (row.get("attrs_number") or {}).items():
                span_attrs[k] = v
            for k, v in (row.get("attrs_bool") or {}).items():
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

        # Build metadata from CH JSON column
        metadata_raw = row.get("metadata_json") or "{}"
        metadata = _parse_json(metadata_raw, default={})

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
              AND is_deleted = 0
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
            eval_rows = analytics.get_children_eval_metrics_ch(children_span_ids)

            # Get config names from PG (small config table)
            config_ids = list({r["config_id"] for r in eval_rows if r.get("config_id")})
            config_name_map = {}
            config_output_type_map = {}
            if config_ids:
                configs = CustomEvalConfig.objects.filter(
                    id__in=config_ids
                ).select_related("eval_template")
                for c in configs:
                    config_name_map[str(c.id)] = c.name
                    config_output_type_map[str(c.id)] = _get_configured_output_type(c)

            # Keys with a completed score or an error — a terminal result always
            # wins over a non-terminal/skipped marker regardless of CH row order.
            terminal_keys: set[str] = set()
            # Precedence among non-terminal/skipped rows for the same key.
            _status_rank = {"pending": 1, "running": 2, "skipped": 3}

            for eval_row in eval_rows:
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

                _row_status = (eval_row.get("status") or "").lower()
                if (
                    eval_row.get("error")
                    or eval_row.get("output_str") == "ERROR"
                    or _row_status == "errored"
                ):
                    evals_metrics[key] = {
                        "score": None,
                        "name": f"{config_name}{name_suffix}",
                        "explanation": eval_row.get("error_message"),
                        "error": True,
                    }
                    terminal_keys.add(key)
                    continue

                # A non-terminal lifecycle status wins over the output columns:
                # the CH mirror stores 0 for a NULL bool, so a queued/running/
                # skipped row can carry stale output that would otherwise be
                # rendered as a real score. Surface the status marker instead
                # (a completed row for the same key still overrides it below).
                status = (eval_row.get("status") or "").lower()
                if status in _status_rank:
                    if key not in terminal_keys:
                        existing = evals_metrics.get(key)
                        if not (
                            existing
                            and _status_rank.get(existing.get("status"), 0)
                            >= _status_rank[status]
                        ):
                            entry = {
                                "score": None,
                                "name": f"{config_name}{name_suffix}",
                                "explanation": eval_row.get("eval_explanation"),
                                "status": status,
                            }
                            if status == "skipped" and eval_row.get("skipped_reason"):
                                entry["skipped_reason"] = eval_row.get("skipped_reason")
                                if not entry["explanation"]:
                                    entry["explanation"] = eval_row.get(
                                        "skipped_reason"
                                    )
                            evals_metrics[key] = entry
                    continue

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
                    terminal_keys.add(key)

        return self._gm.success_response(
            {"observation_span": observation_span, "evals_metrics": evals_metrics}
        )

    @action(detail=False, methods=["get"])
    def retrieve_loading(self, request, *args, **kwargs):
        # CH25-TODO: this endpoint serves "still computing" placeholders
        # for evals not yet completed. It walks project_version.eval_tags
        # (PG only) and inner-loops EvalLogger lookups by (span FK, config
        # FK), which are both PG primary keys. Leaving PG-resident until
        # EvalLogger lives in CH as well — at that point the inner loop
        # becomes a single CH eval-lookup keyed by (span_id, config_id).
        try:
            observation_span_id = request.query_params.get("observation_span_id")
            if not observation_span_id:
                return self._gm.bad_request("observation_span_id is required")

            try:
                observation_span_obj = ObservationSpan.objects.get(
                    _project_workspace_scope_q(request),
                    id=observation_span_id,
                    project__organization=_get_request_organization(request),
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
            observation_spans = observation_spans.filter(
                _project_workspace_scope_q(request),
                project__organization=_get_request_organization(request),
            )
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

            # Collector traces have no PG ``Trace`` row; the gate resolves the root
            # span + tenant from CH/PG-Project instead (fail closed). See selector.
            org = _get_request_organization(request)
            result = allowed_root_spans_for_request(
                trace_ids,
                organization=org,
                project_scope_q=_project_workspace_scope_q(request, project_prefix=""),
            )
            return self._gm.success_response(result)
        except Exception as e:
            # fail closed: any CH/PG error returns no data, never a partial leak
            return self._gm.bad_request(f"Error fetching root spans: {str(e)}")

    @action(detail=False, methods=["post"])
    def bulk_create(self, request, *args, **kwargs):
        try:
            observation_span_data = self.request.data.get("observation_spans")
            if observation_span_data is None:
                observation_span_data = self.request.data.get("spans", [])
            if not observation_span_data:
                return self._gm.bad_request("observation_spans is required")

            for observation_span in observation_span_data:
                if not observation_span.get("id"):
                    observation_span["id"] = f"span_{uuid.uuid4().hex[:16]}"
                observation_span["project"] = Project.objects.get(
                    _project_workspace_scope_q(self.request, project_prefix=""),
                    id=observation_span["project"],
                    organization=_get_request_organization(self.request),
                )
                if observation_span.get("project_version"):
                    observation_span["project_version"] = ProjectVersion.objects.get(
                        _project_workspace_scope_q(self.request),
                        id=observation_span["project_version"],
                        project=observation_span["project"],
                        project__organization=_get_request_organization(self.request),
                    )
                observation_span["trace"] = Trace.objects.get(
                    _project_workspace_scope_q(self.request),
                    id=observation_span["trace"],
                    project=observation_span["project"],
                    project__organization=_get_request_organization(self.request),
                )

                prompt_tokens = observation_span.get("prompt_tokens") or 0
                completion_tokens = observation_span.get("completion_tokens") or 0
                model = observation_span.get("model")
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
                serializer = self.get_serializer(data=request.data)
                if serializer.is_valid():
                    observation_span = serializer.save(id=request.data["id"])

                    return self._gm.success_response(
                        {"id": observation_span.id}, status=201
                    )
            else:
                serializer = self.get_serializer(data=request.data)
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
        except ValueError as e:
            logger.warning(f"Invalid OTEL observation span payload: {str(e)}")
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
            serializer = SpanListQuerySerializer(data=request.query_params)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            validated_data = serializer.validated_data
            project_version_id = str(validated_data["project_version_id"])

            # Tenant gate via PG (ProjectVersion + Project.organization).
            project_version = ProjectVersion.objects.get(
                _project_workspace_scope_q(request),
                id=project_version_id,
                project__organization=_get_request_organization(request),
            )

            # ClickHouse dispatch
            from tracer.services.clickhouse.query_service import (
                AnalyticsQueryService,
            )

            # CH-only path post-migration. D-027: the previous PG fallback
            # (huge ObservationSpan.objects.filter + per-config metric
            # annotations + Score subqueries + Python pivot, ~270 LOC)
            # was deleted. CH is the authoritative span + eval store; the
            # eval/annotation pivots live in `_list_spans_non_observe_clickhouse`
            # via SpanListQueryBuilder.
            analytics = AnalyticsQueryService()
            try:
                return self._list_spans_non_observe_clickhouse(
                    request,
                    project_version_id,
                    project_version,
                    analytics,
                    validated_data,
                )
            except Exception:
                logger.warning(
                    "list_spans_clickhouse_failed, falling back to postgres",
                    project_version_id=project_version_id,
                    exc_info=True,
                )
                return self._list_spans_postgres(
                    request,
                    str(project_version.project_id),
                    validated_data,
                    project_version_id=project_version_id,
                )

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
                    _project_workspace_scope_q(request),
                    id=observation_span_id,
                    project__organization=_get_request_organization(request),
                )
            except ObservationSpan.DoesNotExist:
                raise Exception("Observation span not found")  # noqa: B904

            try:
                custom_eval_config = CustomEvalConfig.objects.get(
                    _project_workspace_scope_q(request),
                    id=custom_eval_config_id,
                    project__organization=_get_request_organization(request),
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
                _project_workspace_scope_q(request),
                id=span_id,
                project__organization=_get_request_organization(request),
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
                    _project_workspace_scope_q(request),
                    id=observation_span_id,
                    project__organization=_get_request_organization(request),
                )
            except ObservationSpan.DoesNotExist:
                raise Exception("Observation span not found")  # noqa: B904

            try:
                custom_eval_config = CustomEvalConfig.objects.get(
                    _project_workspace_scope_q(request),
                    id=custom_eval_config_id,
                    project__organization=_get_request_organization(request),
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

    @validated_request(query_serializer=SpanObserveListQuerySerializer)
    @action(detail=False, methods=["get"])
    def list_spans_observe(self, request, *args, **kwargs):
        try:
            validated_data = request.validated_query_data

            project_id = (
                str(validated_data["project_id"])
                if validated_data.get("project_id")
                else None
            )
            org = _get_request_organization(request)

            org_project_ids = None
            if project_id:
                try:
                    Project.objects.get(
                        _project_workspace_scope_q(self.request, project_prefix=""),
                        id=project_id,
                        organization=org,
                    )
                except Project.DoesNotExist:
                    return self._gm.bad_request("Project not found or access denied")
            else:
                org_project_ids = list(
                    Project.objects.filter(
                        _project_workspace_scope_q(self.request, project_prefix=""),
                        organization=org,
                        deleted=False,
                    ).values_list("id", flat=True)
                )

            # ClickHouse dispatch
            from tracer.services.clickhouse.query_service import (
                AnalyticsQueryService,
            )

            # CH-only path post-migration. D-027: the previous PG fallback
            # body (ObservationSpan.objects.filter + per-config metric
            # annotations + Score subqueries + Python pivot, ~350 LOC) was
            # deleted. CH is the authoritative span + eval store and the
            # pivot now lives in `_list_spans_clickhouse` via
            # SpanListQueryBuilder. A CH read failure surfaces via the outer
            # handler instead of silently degrading to the empty post-migration
            # Postgres path, which masked CH failures as "0 rows".
            analytics = AnalyticsQueryService()
            return self._list_spans_clickhouse(
                request,
                project_id,
                validated_data,
                analytics,
                org_project_ids=org_project_ids,
                org=org,
            )

        except Exception as e:
            logger.exception(f"Error in fetching the spans list of observe: {str(e)}")
            return self._gm.bad_request(
                f"error fetching the spans list of observe {str(e)}"
            )

    def _list_spans_clickhouse(
        self,
        request,
        project_id,
        validated_data,
        analytics,
        org_project_ids=None,
        org=None,
    ):
        """List spans using ClickHouse backend.

        Builder class is resolved via the v1↔v2 dispatch — set
        CH25_QUERY_TYPES_V2_PRIMARY=SPAN_LIST (or V2_ONLY) to flip this
        endpoint to the CH 25.3 schema. Defaults to v1 (CH 24.10) until
        flipped. See tracer/services/clickhouse/v2/dispatch.py.
        """
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder
        from tracer.services.clickhouse.v2.dispatch import get_query_builder_class

        BuilderCls = get_query_builder_class("SPAN_LIST")  # noqa: N806

        org_scope = bool(org_project_ids)
        if org is None:
            org = _get_request_organization(request)
        # The v2 builder is a subclass of the v1 builder, so the pivot
        # helpers below (called as classmethods on the v1 name) work for
        # both — keep the v1 import for those static calls.

        filters = list(validated_data.get("filters", []) or [])
        page_number = validated_data["page_number"]
        page_size = validated_data["page_size"]

        # P3b step2 precondition — user_id → end_user reverse-resolve (CH, not PG).
        # The old PG `EndUser.objects.get(user_id=…).id` FREEZES post-step2: a
        # NET-NEW user (first seen after the ingest get_or_create is dropped) has
        # NO `tracer_enduser` row, only a CH `end_users` row keyed by its
        # deterministic id + spans carrying that id — so the PG lookup raised
        # "User not found" and the list was empty for it. Instead, inject a
        # synthetic `user_id` filter and let the SHIPPED, remap-aware
        # `ClickHouseFilterBuilder._build_enduser_string_condition` resolve it:
        # it builds the curated id-set from `end_users FINAL` (historical + net-new
        # deterministic + straddler's both) and matches it against each span's
        # `end_user_id` resolved new→old via `end_user_id_remap`. This REPLACES the
        # bespoke `end_user_id=` builder arg (the only non-test caller of it) with
        # the canonical filter path — zero duplicated SQL, and net-new now returns
        # rows. Pre-flip a no-op vs the old single-id filter (gate B): historical /
        # straddler resolve to the same curated id-set. An unknown user resolves to
        # an EMPTY id-set → empty list (was an exception; net-new is no longer
        # "not found", the intended fix).
        user_id = validated_data.get("user_id")
        if user_id:
            filters.append(
                {
                    "column_id": "user_id",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": str(user_id),
                    },
                }
            )

        # Get eval config IDs. Single-project uses the fast CH dict-lookup;
        # org-scoped falls back to a PG EvalLogger scan.
        eval_config_ids = []
        if org_scope:
            _eval_ids = (
                EvalLogger.objects.filter(
                    observation_span__project_id__in=org_project_ids
                )
                .values("custom_eval_config_id")
                .distinct()
            )
            eval_configs = CustomEvalConfig.objects.filter(
                id__in=_eval_ids, deleted=False
            ).select_related("eval_template")
            eval_config_ids = [str(c.id) for c in eval_configs]
        else:
            # PERF: resolve this project's configs from PG first (indexed by the
            # project FK), then ask CH which of them have recent data via a
            # ``custom_eval_config_id IN (…)`` scope — the eval table's leading
            # sort key, so CH prunes to just those configs. This replaces the old
            # full-table trace-join discovery (tens of seconds / OOM-prone at
            # scale) with a sub-second read. See
            # AnalyticsQueryService.get_eval_config_ids_with_data_ch.
            project_configs = list(
                CustomEvalConfig.objects.filter(
                    project_id=project_id, deleted=False
                ).select_related("eval_template")
            )
            candidate_ids = [str(c.id) for c in project_configs]
            # Short-TTL cache: "which configs have data" changes on config
            # creation / first eval write, not per page load — the fast-path CH
            # read still costs ~0.4-0.9s per request at 10M eval rows (measured),
            # and this endpoint fires it on EVERY page. Key includes the
            # candidate set so a newly-created config gets a fresh entry; worst
            # case a brand-new config's column appears one TTL late.
            ids_with_data: set[str] = set()
            if candidate_ids:
                cache_key = (
                    "span_list_eval_cfgs:"
                    + hashlib.sha256(
                        (
                            str(project_id) + "|" + ",".join(sorted(candidate_ids))
                        ).encode()
                    ).hexdigest()
                )
                cached_ids = django_cache.get(cache_key)
                if cached_ids is not None:
                    ids_with_data = set(cached_ids)
                else:
                    ids_with_data = set(
                        analytics.get_eval_config_ids_with_data_ch(
                            str(project_id),
                            timeout_ms=30000,
                            candidate_config_ids=candidate_ids,
                        )
                    )
                    django_cache.set(cache_key, list(ids_with_data), timeout=120)
            eval_configs = [c for c in project_configs if str(c.id) in ids_with_data]
            eval_config_ids = [str(c.id) for c in eval_configs]

        # Labels can be project-local or org/shared labels that are referenced
        # by span scores. Use the score-backed helper so span columns and
        # annotation filters match the actual data returned from ClickHouse.
        annotation_labels = get_annotation_labels_for_project(
            project_id, project_ids=org_project_ids if org_scope else None
        )
        annotation_label_ids = [str(lbl.id) for lbl in annotation_labels]
        label_types = {str(lbl.id): lbl.type for lbl in annotation_labels}

        # No `end_user_id=` arg: the user filter is now a synthetic `user_id`
        # filter in `filters` (resolved via the remap-aware `end_users` path
        # above), so the builder's bespoke single-id end_user path is unused here.
        builder = BuilderCls(
            project_id=None if org_scope else str(project_id),
            project_ids=[str(p) for p in org_project_ids] if org_scope else None,
            filters=filters,
            page_number=page_number,
            page_size=page_size,
            eval_config_ids=eval_config_ids,
            annotation_label_ids=annotation_label_ids,
        )

        # Phase 1: Paginated spans (light columns — no input/output).
        #
        # Progressive time slices: the sort is `start_time DESC`, so every row
        # in a newer slice sorts before every older row — a slice that already
        # yields the full prefix [0, offset + 2*page_size) IS the global
        # prefix, and older data cannot change the page. Try the newest slices
        # first (7d → 30d → 180d), fall back to the full requested window only
        # when the prefix is short. Typical pages fill from recent data and
        # read ~100x fewer rows (measured 0.02-0.17s vs ~1.2s over 18 months at
        # 10M spans). Slices that don't narrow the requested window are
        # skipped, so a "last 24h" view still runs exactly one query.
        prefix_needed = (page_number * page_size) + 2 * page_size
        win_start, _win_end = SpanListQueryBuilder.parse_time_range(filters)
        utc_now = datetime.utcnow()
        slice_starts = [
            utc_now - timedelta(days=d)
            for d in (7, 30, 180)
            if win_start is None or utc_now - timedelta(days=d) > win_start
        ]
        result = None
        for since in [*slice_starts, None]:
            query, params = builder.build(since=since)
            result = analytics.execute_ch_query(query, params, timeout_ms=10000)
            if len(result.data) >= prefix_needed:
                break

        # Prefix-dedup pagination: Phase 1 dropped `LIMIT 1 BY id` (its
        # O(window) full sort OOM-crashed CH — see SpanListQueryBuilder.build)
        # and instead fetched the sorted prefix [0, offset + 2*page_size).
        # De-dup the prefix by span id and slice the page — every page is a
        # disjoint slice of the same globally de-duplicated stream, so a span
        # can never appear on two pages and none is skipped. See page_dedup.py.
        result.data, has_more = paginate_deduped(
            result.data, "id", page_number, page_size
        )

        span_ids = [str(row.get("id", "")) for row in result.data]
        # Oldest created_at on the page — lower bound for the eval/annotation
        # reads below. Both tables are PARTITION BY toYYYYMM(created_at) and an
        # eval/score row cannot be created before its span row exists, so the
        # bound (with a 7-day margin in the builder) only prunes partitions
        # that cannot hold matches — measured 55x fewer rows read.
        page_created_ats = [
            row.get("created_at") for row in result.data if row.get("created_at")
        ]
        page_min_created_at = min(page_created_ats) if page_created_ats else None

        # Phases 1b/2/3 + count are independent once the page ids are known —
        # run them concurrently so request latency is Phase1 + max(rest), not
        # the serial sum. `analytics.ch_client` pools connections behind a lock
        # (see ClickHouseClient._get_client), so concurrent execute_ch_query
        # calls are safe. Any worker exception propagates via .result() and is
        # handled by the endpoint's outer try/except, same as the serial code.
        def _fetch_content():
            if not span_ids:
                return []
            content_query, content_params = builder.build_content_query(span_ids)
            if not content_query:
                return []
            return analytics.execute_ch_query(
                content_query, content_params, timeout_ms=10000
            ).data

        def _fetch_count():
            count_query, count_params = builder.build_count_query()
            # Short-TTL cache keyed by the exact query + bindings: the count
            # re-scans the full filtered window (measured 0.65-1.15s at 10M+
            # rows) and is identical across pages of the same view. Value is
            # exact; staleness is bounded by the TTL.
            count_key = (
                "span_list_count:"
                + hashlib.sha256(
                    (count_query + repr(sorted(count_params.items(), key=str))).encode()
                ).hexdigest()
            )
            cached_total = django_cache.get(count_key)
            if cached_total is not None:
                return cached_total
            count_result = analytics.execute_ch_query(
                count_query, count_params, timeout_ms=10000
            )
            total = count_result.data[0].get("total", 0) if count_result.data else 0
            django_cache.set(count_key, total, timeout=60)
            return total

        def _fetch_evals():
            if not (span_ids and eval_config_ids):
                return {}
            eval_query, eval_params = builder.build_eval_query(
                span_ids, created_after=page_min_created_at
            )
            if not eval_query:
                return {}
            eval_result = analytics.execute_ch_query(
                eval_query, eval_params, timeout_ms=5000
            )
            return SpanListQueryBuilder.pivot_eval_results(eval_result.data)

        def _fetch_annotations():
            if not (span_ids and annotation_label_ids):
                return {}
            ann_query, ann_params = builder.build_annotation_query(
                span_ids, created_after=page_min_created_at
            )
            if not ann_query:
                return {}
            ann_result = analytics.execute_ch_query(
                ann_query, ann_params, timeout_ms=5000
            )
            return SpanListQueryBuilder.pivot_annotation_results(
                ann_result.data, label_types
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            content_f = pool.submit(_fetch_content)
            count_f = pool.submit(_fetch_count)
            evals_f = pool.submit(_fetch_evals)
            anns_f = pool.submit(_fetch_annotations)
            content_rows = content_f.result()
            total_count = count_f.result()
            eval_map = evals_f.result()
            annotation_map = anns_f.result()

        # Phase 1b merge: input/output/attributes_extra onto the page rows
        content_map = {str(r.get("id", "")): r for r in content_rows}
        for row in result.data:
            c = content_map.get(str(row.get("id", "")), {})
            row["input"] = c.get("input", "")
            row["output"] = c.get("output", "")
            row["attributes_extra"] = c.get("attributes_extra", "{}")

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

        # Batch-resolve end_user UUIDs → (user_id, user_id_type, user_id_hash)
        # so each row can surface the human-readable user identifier. CH only
        # stores the UUID; the curated display fields live on the v2 `end_users`
        # dimension (its dict). P3b step2 precondition: swap the PG
        # `EndUser.objects.filter(id__in=…)` lookup (which is EMPTY for a net-new
        # user's id — no PG row post-flip) for the SHIPPED, remap-aware
        # `end_user_dict_reader.resolve_end_user_fields`. It resolves each id
        # new→old through `end_user_id_remap` then `dictGetOrNull`s the curated
        # fields, so a net-new span's deterministic id (no remap entry → resolves
        # to itself) still yields its `end_users` fields, a straddler's new-id
        # span resolves to the old curated row, and a missing/orphan id → all-None
        # (faithful to the old FK miss). Returns {id (str): {user_id,
        # user_id_type, user_id_hash}}.
        end_user_ids = {
            str(r.get("end_user_id")) for r in result.data if r.get("end_user_id")
        }
        end_user_map = {}
        if end_user_ids:
            from tracer.services.clickhouse.v2.end_user_dict_reader import (
                resolve_end_user_fields,
            )

            end_user_map = resolve_end_user_fields(end_user_ids)

        # Format response matching PG format
        table_data = []
        for row in result.data:
            span_id = str(row.get("id", ""))
            cost = row.get("cost")
            eu = (
                end_user_map.get(str(row.get("end_user_id")))
                if row.get("end_user_id")
                else None
            )
            entry = {
                "span_id": span_id,
                "input": row.get("input", ""),
                "output": row.get("output", ""),
                "trace_id": str(row.get("trace_id", "")),
                "created_at": row.get("created_at"),
                "node_type": row.get("observation_type", ""),
                "span_name": row.get("name", ""),
                # `eu` is now a {user_id, user_id_type, user_id_hash} dict from
                # `resolve_end_user_fields` (was a PG EndUser instance) — read by
                # key, defaulting to None (the all-None record for a missing id).
                "user_id": eu.get("user_id") if eu else None,
                "user_id_type": eu.get("user_id_type") if eu else None,
                "user_id_hash": eu.get("user_id_hash") if eu else None,
                "start_time": row.get("start_time"),
                "status": row.get("status"),
                "latency_ms": row.get("latency_ms"),
                "total_tokens": row.get("total_tokens"),
                "prompt_tokens": row.get("prompt_tokens"),
                "completion_tokens": row.get("completion_tokens"),
                "model": row.get("model"),
                "provider": row.get("provider"),
                "cost": round(cost, 6) if cost else 0,
            }

            # Add eval metrics
            span_evals = eval_map.get(span_id, {})
            for config in eval_configs:
                config_id = str(config.id)
                if config_id not in span_evals:
                    continue
                val = span_evals[config_id]
                # Lifecycle marker — ``{"status": ...}`` (pending/running/skipped)
                # or ``{"error": True}`` (errored): pass the whole marker through
                # on the ``config_id`` column so the cell renders the
                # loading / pending / skipped / error state instead of a blank.
                if isinstance(val, dict) and (
                    isinstance(val.get("status"), str) or val.get("error")
                ):
                    entry[config_id] = val
                # CHOICES eval: spread per-choice percentages into separate
                # columns keyed ``{config_id}**{choice}`` to match the
                # column config produced by
                # ``update_column_config_based_on_eval_config``.
                elif isinstance(val, dict) and not val.get("error") and val:
                    for choice, pct in val.items():
                        entry[f"{config_id}**{choice}"] = pct
                else:
                    entry[config_id] = val
                    if isinstance(val, dict):
                        entry[config_id] = val.get("score")
                    else:
                        entry[config_id] = val

            # Add annotations
            span_annotations = annotation_map.get(span_id, {})
            for label in annotation_labels:
                label_id = str(label.id)
                if label_id in span_annotations:
                    entry[label_id] = span_annotations[label_id]

            # Include span attributes (typed maps + attributes_extra) for custom columns
            flatten_span_attributes_into_entry(entry, row)

            table_data.append(entry)

        response = {
            "metadata": {"total_rows": total_count},
            "table": table_data,
            "config": column_config,
        }

        return self._gm.success_response(response)

    def _list_spans_non_observe_clickhouse(
        self, request, project_version_id, project_version, analytics, validated_data
    ):
        """List spans (non-observe, prompt version/eval task views) using ClickHouse backend.

        Same v1↔v2 dispatch as `_list_spans_clickhouse` — flips together via
        CH25_QUERY_TYPES_V2_PRIMARY=SPAN_LIST.
        """
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder
        from tracer.services.clickhouse.v2.dispatch import get_query_builder_class

        BuilderCls = get_query_builder_class("SPAN_LIST")  # noqa: N806

        filters = validated_data.get("filters", [])
        page_number = validated_data.get("page_number", 0)
        page_size = validated_data.get("page_size", 30)

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

        # Labels can be project-local or org/shared labels that are referenced
        # by span scores. Use the score-backed helper so span columns and
        # annotation filters match the actual data returned from ClickHouse.
        annotation_labels = get_annotation_labels_for_project(project_id)
        annotation_label_ids = [str(lbl.id) for lbl in annotation_labels]
        label_types = {str(lbl.id): lbl.type for lbl in annotation_labels}

        builder = BuilderCls(
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

        # Prefix-dedup pagination: Phase 1 dropped `LIMIT 1 BY id` (its
        # O(window) full sort OOM-crashed CH — see SpanListQueryBuilder.build)
        # and instead fetched the sorted prefix [0, offset + 2*page_size).
        # De-dup the prefix by span id and slice the page — every page is a
        # disjoint slice of the same globally de-duplicated stream, so a span
        # can never appear on two pages and none is skipped. See page_dedup.py.
        result.data, has_more = paginate_deduped(
            result.data, "id", page_number, page_size
        )

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
            count_query, count_params, timeout_ms=10000
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
                if isinstance(val, dict) and (
                    isinstance(val.get("status"), str) or val.get("error")
                ):
                    # Lifecycle marker — loading/pending/skipped or errored.
                    entry[config_id] = val
                elif (
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

    @validated_request(
        request_serializer=ObserveGraphDataRequestSerializer,
        responses={200: ObserveGraphDataResponseSerializer},
    )
    @action(detail=False, methods=["post"])
    def get_graph_methods(self, request, *args, **kwargs):
        """
        Fetch data for the observe graph with optimized queries
        """
        try:
            body = request.validated_data
            project_id = str(body["project_id"])

            project = Project.objects.get(
                _project_workspace_scope_q(self.request, project_prefix=""),
                id=project_id,
                organization=_get_request_organization(request),
            )
            if project.trace_type != "observe":
                raise Exception("Project should be of type observe")

            filters = body["filters"]
            _property = body["property"]
            interval = body["interval"]
            req_data_config = body["req_data_config"]

            type = req_data_config.get("type", None)
            if type not in ["EVAL", "ANNOTATION", "SYSTEM_METRIC"]:
                return self._gm.bad_request("Filter property type is not valid")

            # CH-only path post-migration. D-027: the previous PG fallback
            # (ObservationSpan.objects.filter + per-config eval-metric
            # annotations + Score subqueries + Python pivot, ~270 LOC) was
            # deleted. SPAN_GRAPH is served by the three CH helpers
            # (fetch_system_metric_graph_ch / fetch_eval_graph_ch /
            # fetch_annotation_graph_ch).
            analytics = AnalyticsQueryService()
            if type == "SYSTEM_METRIC":
                metric_id = req_data_config.get("id", "latency")
                try:
                    return self._gm.success_response(
                        fetch_system_metric_graph_ch(
                            analytics=analytics,
                            project_id=project_id,
                            filters=filters,
                            interval=interval,
                            metric_id=metric_id,
                        )
                    )
                except Exception:
                    logger.warning(
                        "span_graph_clickhouse_failed, falling back to postgres",
                        project_id=project_id,
                        metric_id=metric_id,
                        exc_info=True,
                    )
                    return self._gm.success_response(
                        self._system_metric_graph_postgres(
                            request, project_id, filters, interval, metric_id
                        )
                    )
            elif type == "EVAL":
                return self._gm.success_response(
                    fetch_eval_graph_ch(
                        analytics=analytics,
                        project_id=project_id,
                        filters=filters,
                        interval=interval,
                        req_data_config=req_data_config,
                    )
                )
            elif type == "ANNOTATION":
                return self._gm.success_response(
                    fetch_annotation_graph_ch(
                        analytics=analytics,
                        project_id=project_id,
                        filters=filters,
                        interval=interval,
                        req_data_config=req_data_config,
                        observe_type="span",
                    )
                )
            return self._gm.bad_request("Filter property type is not valid")

        except Exception as e:
            logger.exception(f"Error in fetching graph data: {str(e)}")
            return self._gm.bad_request(f"Error fetching graph data: {str(e)}")

    @validated_request(
        query_serializer=ObservationAttributeListQuerySerializer,
        responses={200: ObservationAttributeListResponseSerializer},
    )
    @action(detail=False, methods=["get"])
    def get_span_attributes_list(self, request, *args, **kwargs):
        """Distinct span_attributes keys for a project (spans surface).

        Query params:
            filters: JSON {"project_id": "<uuid>"} (required)

        Returns:
            List of attribute key strings.
        """
        try:
            project_id = request.validated_query_data["filters"]["project_id"]

            result = self._get_span_attribute_keys(project_id)
            return self._gm.success_response(result)

        except Exception as e:
            logger.exception(f"error fetching span attributes list: {str(e)}")
            return self._gm.bad_request(
                f"error fetching the span attributes list {str(e)}"
            )

    @validated_request(
        query_serializer=ObservationAttributeListQuerySerializer,
        responses={200: ObservationAttributeListResponseSerializer},
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
            project_id = request.validated_query_data["filters"]["project_id"]
            row_type = request.validated_query_data["row_type"]

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
        """Project's distinct span_attributes keys, sourced from CH.

        Single source for both ``get_span_attributes_list`` (which wraps
        it in a DRF response) and the trace + session path builders.

        CH returns ``[{"key": ..., "type": ...}, ...]`` (spans picker
        renders type chips); the trace + session path builders need
        bare strings. The normalization loop below collapses both
        shapes to ``list[str]`` so callers never see dicts f-stringed
        into paths like ``traces.0.spans.0.{'key': '...', ...}``.

        CH25 close-out (2026-05-26): PG fallback removed alongside the
        routing toggle. Span attribute keys come from the CH ``attrs_*``
        typed-Map indexes (the authoritative inventory).
        """
        analytics = AnalyticsQueryService()
        raw = analytics.get_span_attribute_keys_ch(str(project_id))

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
        sample_trace_ids = list(
            Trace.objects.filter(project_id=project_id)
            .order_by("-created_at")
            .values_list("id", flat=True)[: self._OBSERVED_MAX_SAMPLE_SIZE]
        )
        if not sample_trace_ids:
            return 0

        # CH-routed: per_trace_aggregate returns {trace_id: {span_count, ...}}
        # for the sampled traces; we just need the max span_count across
        # them. One CH call replaces the prior values/annotate/aggregate
        # roll-up against the PG spans table.
        from tracer.services.clickhouse.v2 import get_reader

        with get_reader() as reader:
            agg = reader.per_trace_aggregate([str(t) for t in sample_trace_ids])
        if not agg:
            return 0
        return max((row.get("span_count", 0) for row in agg.values()), default=0)

    def _max_traces_per_session(self, project_id: str) -> int:
        """Max trace count observed across the project's most recent sessions."""
        sample_session_ids = (
            TraceSession.objects.filter(project_id=project_id)
            .order_by("-created_at")
            .values_list("id", flat=True)[: self._OBSERVED_MAX_SAMPLE_SIZE]
        )
        agg = (
            Trace.objects.filter(session_id__in=sample_session_ids)
            .values("session_id")
            .annotate(trace_count=Count("id"))
            .aggregate(max_count=Max("trace_count"))
        )
        return agg["max_count"] or 0

    _SPAN_PUBLIC_FIELDS = (
        "latency_ms",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cost",
        "response_time",
        "model",
        "name",
        "observation_type",
        "status",
        "status_message",
        "provider",
    )

    def _build_trace_attribute_paths(
        self, project_id: str, span_attribute_keys: list
    ) -> list:
        """Trace-level paths: trace fields + ``spans.<n>.<key>`` for each
        index up to the observed max spans-per-trace."""
        paths = list(self._TRACE_PUBLIC_FIELDS)
        max_spans = self._max_spans_per_trace(project_id)
        for i in range(max_spans):
            for field in self._SPAN_PUBLIC_FIELDS:
                paths.append(f"spans.{i}.{field}")
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
                for field in self._SPAN_PUBLIC_FIELDS:
                    paths.append(f"traces.{i}.spans.{j}.{field}")
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
        # Span- and trace-target rows both anchor to observation_span_id;
        # session rows don't and are served by /trace-session/:id/eval_logs/.
        row = analytics.get_eval_detail_ch(observation_span_id, custom_eval_config_id)
        if not row:
            return self._gm.bad_request(
                "No eval logger found for the given observation span id and custom eval config id"
            )

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
            )

            analytics = AnalyticsQueryService()
            # CH-only path post-migration. EvalLogger reads previously
            # served as a PG fallback; the CH variant reads from
            # `tracer_eval_logger` via the CDC pipeline and is now the
            # only routed path.
            return self._get_evaluation_details_clickhouse(
                observation_span_id, custom_eval_config_id, analytics
            )

            # Mirror the ClickHouse filter; excludes session-target rows.
            eval_logger = EvalLogger.objects.filter(
                observation_span_id=observation_span_id,
                custom_eval_config_id=custom_eval_config_id,
                target_type__in=["span", "trace"],
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
            serializer = SpanExportQuerySerializer(data=request.query_params)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            validated_data = serializer.validated_data

            response = self.list_spans_observe(request, export=True)

            if response.status_code != 200:
                return response

            project_id = str(validated_data["project_id"])
            project = Project.objects.get(
                _project_workspace_scope_q(self.request, project_prefix=""),
                id=project_id,
                organization=_get_request_organization(request),
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

    @validated_request(request_serializer=AddObservationSpanAnnotationsSerializer)
    @action(detail=False, methods=["post"])
    def add_annotations(self, request, *args, **kwargs):
        try:
            data = request.validated_data
            observation_span_id = data.get("observation_span_id")
            annotation_values = data.get("annotation_values")
            trace_id = data.get("trace_id")
            notes = data.get("notes")

            if (not observation_span_id and not trace_id) or not annotation_values:
                raise Exception(
                    "Observation span id and annotation values are required"
                )

            try:
                if observation_span_id:
                    observation_span = ObservationSpan.objects.get(
                        _project_workspace_scope_q(request),
                        id=observation_span_id,
                        project__organization=_get_request_organization(request),
                    )
                elif trace_id:
                    observation_span = ObservationSpan.objects.get(
                        _project_workspace_scope_q(request),
                        trace_id=trace_id,
                        project__organization=_get_request_organization(request),
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
                    #
                    # Resolve a default queue item up-front so the upsert
                    # lookup keys on queue_item — the per-queue Score
                    # uniqueness ``(source, label, annotator, queue_item)``
                    # would otherwise produce duplicate orphan rows on
                    # repeated writes from this legacy endpoint. Falls
                    # back to NULL if the source has no resolvable scope
                    # (rare, e.g. orphaned span).
                    from model_hub.utils.annotation_queue_helpers import (
                        resolve_default_queue_item_for_source,
                    )

                    default_item = resolve_default_queue_item_for_source(
                        "observation_span",
                        observation_span,
                        request.user.organization,
                        request.user,
                    )
                    if default_item is None:
                        # Per-queue Score uniqueness requires a queue_item.
                        # Skip rather than insert with queue_item=NULL —
                        # NULL ≠ NULL in Postgres, so a silent orphan
                        # insert could accumulate duplicates the on_commit
                        # auto-attach hook can no longer migrate safely.
                        failed_labels.append(label_id)
                        logger.warning(
                            "score_skip_no_default_queue_scope",
                            source_type="observation_span",
                            source_id=str(observation_span.pk),
                            label_id=str(annotation_label.pk),
                        )
                        continue
                    score, _ = Score.no_workspace_objects.update_or_create(
                        observation_span_id=observation_span.pk,
                        label_id=annotation_label.pk,
                        annotator_id=request.user.pk,
                        queue_item=default_item,
                        deleted=False,
                        defaults={
                            "source_type": "observation_span",
                            "value": score_value,
                            "score_source": "human",
                            "notes": notes or "",
                            "organization": request.user.organization,
                        },
                    )
                    if notes is not None:
                        from model_hub.models.annotation_queues import QueueItemNote

                        if notes:
                            QueueItemNote.no_workspace_objects.update_or_create(
                                queue_item=default_item,
                                annotator=request.user,
                                deleted=False,
                                defaults={
                                    "notes": notes,
                                    "organization": request.user.organization,
                                    "workspace": getattr(request, "workspace", None)
                                    or default_item.workspace,
                                },
                            )
                        else:
                            QueueItemNote.no_workspace_objects.filter(
                                queue_item=default_item,
                                annotator=request.user,
                                deleted=False,
                            ).update(deleted=True, deleted_at=timezone.now())

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
                _project_workspace_scope_q(request, project_prefix=""),
                id=label_id,
                organization=_get_request_organization(request),
            )
            # Check if label is in use by active annotation tasks
            if Annotations.objects.filter(labels=label_id, deleted=False).exists():
                return self._gm.bad_request(
                    "Cannot delete label: it is in use by active annotation tasks"
                )
            label.delete()
            Score.objects.filter(
                label_id=label_id, organization=_get_request_organization(request)
            ).update(deleted=True)

            return self._gm.success_response(
                {"message": "Annotation label deleted successfully"}
            )
        except AnnotationsLabels.DoesNotExist:
            return self._gm.bad_request("Annotation label not found")
        except Exception as e:
            return self._gm.bad_request(f"error deleting the annotation label {str(e)}")

    @validated_request(query_serializer=SpanIndexQuerySerializer)
    @action(detail=False, methods=["get"])
    def get_trace_id_by_index_spans_as_base(self, request, *args, **kwargs):
        """
        Get the previous and next span id by index for non-observe projects.
        Mirrors the query/filter logic of list_spans.
        """
        # CH25-TODO: this endpoint is the prev/next navigation companion
        # to list_spans (non-observe). It needs the same eval/annotation
        # filter pivot that the CH SpanListQueryBuilder produces plus a
        # cursor-style "find by start_time before/after span_id" step.
        #
        # Wave-3 partial coverage (commit 93c5c415f): the reader exposes
        # `prev_next_span_by_start_time(project_id=, span_id=,
        # project_version_id=, observation_type=)` which covers the
        # unfiltered walk but
        #   (a) returns span_ids while this endpoint returns trace_ids,
        #       and
        #   (b) does not accept the eval/annotation/span-attribute
        #       filters this endpoint applies (FilterEngine pivots +
        #       build_annotation_subqueries) before walking.
        # The frontend always sends `filters` (could be []) so a
        # drop-in swap would silently change the navigation set under
        # any non-empty filter. Staying PG-only.
        #
        # Reader-gap proposal:
        #   prev_next_trace_id_by_span_start_time(*, project_id,
        #       span_id, project_version_id=None, observation_type=None,
        #       filters=None) -> tuple[Optional[str], Optional[str]]
        # where `filters` accepts the SpanListQueryBuilder filter shape
        # (system metrics + eval pivots + annotation joins + span
        # attributes) and the return is (prev_trace_id, next_trace_id).
        try:
            query = request.validated_query_data
            span_id = query["span_id"]
            project_version_id = str(query["project_version_id"])

            project_version = ProjectVersion.objects.get(
                _project_workspace_scope_q(request),
                id=project_version_id,
                project__organization=_get_request_organization(request),
            )

            base_query = ObservationSpan.objects.filter(
                _project_workspace_scope_q(request),
                project_version_id=project_version_id,
                project__organization=_get_request_organization(request),
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
                        observation_span__project__organization=_get_request_organization(
                            request
                        ),
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

            filters = query["filters"]
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
                    if (f.get("filter_config") or {}).get("col_type")
                    not in annotation_col_types
                    and f.get("column_id") not in annotation_column_ids
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

    @validated_request(query_serializer=SpanObserveIndexQuerySerializer)
    @action(detail=False, methods=["get"])
    def get_trace_id_by_index_spans_as_observe(self, request, *args, **kwargs):
        """
        Get the previous and next trace id by index for observe projects.
        Mirrors the query/filter logic of list_spans_as_observe.
        """
        # CH25-TODO: observe sibling of get_trace_id_by_index_spans_as_base.
        # Same reader-gap rationale — staying on PG.
        #
        # Wave-3 partial coverage (commit 93c5c415f):
        # `prev_next_span_by_start_time` does the unfiltered walk but
        #   (a) returns span_ids while this endpoint returns trace_ids,
        #   (b) does not accept the eval/annotation/span-attribute
        #       filters this endpoint applies before walking, and
        #   (c) the observe variant also applies an `end_user_id` scope
        #       (from EndUser lookup) that the reader method doesn't
        #       expose.
        # The frontend always sends `filters` (could be []) so a
        # drop-in swap would silently change the navigation set under
        # any non-empty filter. Staying PG-only.
        #
        # Reader-gap proposal (shared with non-observe variant above):
        #   prev_next_trace_id_by_span_start_time(*, project_id,
        #       span_id, project_version_id=None, observation_type=None,
        #       end_user_id=None, filters=None)
        #       -> tuple[Optional[str], Optional[str]]
        try:
            query = request.validated_query_data
            span_id = query["span_id"]
            project_id = str(query["project_id"])
            user_id = query.get("user_id") or None

            # P3b step2 precondition — user_id → end_user reverse-resolve (CH, not
            # PG). The old PG `EndUser.objects.get(user_id=…).id` raised "User not
            # found" for a NET-NEW user (no `tracer_enduser` row post-step2). Read
            # the curated id-SET from CH `end_users` instead (historical + net-new
            # deterministic + straddler's both — the state-robust reverse-resolve,
            # PG_ORM_READ_MIGRATION). The id-set then filters the spans below via
            # `end_user_id__in` so a straddler's old + new ids both match.
            #
            # NOTE this endpoint's prev/next WALK stays PG (a documented CH25-TODO
            # reader-gap above): a span carrying a resolved end_user_id is matched
            # in PG `tracer_observationspan`. Post-step2 in production the collector
            # writes the deterministic end_user_id onto the PG span, so the walk
            # finds a net-new user's spans; it only fails to in a CH-ONLY rehearsal
            # where the net-new spans were manufactured in CH but not PG. An empty
            # id-set (unknown user) now yields an empty walk instead of raising —
            # net-new is no longer "User not found", the intended fix.
            end_user_ids: list[str] = []
            if user_id:
                from tracer.services.clickhouse.v2.end_user_dict_reader import (
                    resolve_end_user_ids_by_user_id,
                )

                end_user_ids = resolve_end_user_ids_by_user_id(
                    user_id, project_id=project_id
                )

            project = Project.objects.get(
                _project_workspace_scope_q(request, project_prefix=""),
                id=project_id,
                organization=_get_request_organization(request),
            )
            if project.trace_type not in ("observe", "experiment"):
                raise Exception("Project should be of type observe or experiment")

            base_query = ObservationSpan.objects.filter(
                _project_workspace_scope_q(request),
                project_id=project_id,
                project__organization=_get_request_organization(request),
            ).annotate(
                node_type=F("observation_type"),
                span_id=F("id"),
                span_name=F("name"),
                user_id=F("end_user__user_id"),
                user_id_type=F("end_user__user_id_type"),
                user_id_hash=F("end_user__user_id_hash"),
            )

            if end_user_ids:
                # IN over the curated id-set so a straddler's old + new ids both
                # match (single-id `=` would miss half its spans post-flip).
                base_query = base_query.filter(end_user_id__in=end_user_ids)

            eval_configs = CustomEvalConfig.objects.filter(
                id__in=EvalLogger.objects.filter(
                    observation_span__project_id=project_id,
                    observation_span__project__organization=_get_request_organization(
                        request
                    ),
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
                        observation_span__project__organization=_get_request_organization(
                            request
                        ),
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

            annotation_labels = get_annotation_labels_for_project(project_id)
            base_query = build_annotation_subqueries(
                base_query,
                annotation_labels,
                request.user.organization,
                span_filter_kwargs={"observation_span_id": OuterRef("id")},
            )

            filters = query["filters"]

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
                    if (f.get("filter_config") or {}).get("col_type")
                    not in annotation_col_types
                    and f.get("column_id") not in annotation_column_ids
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

    CH25-TODO: this helper feeds the legacy compare_traces and the
    PG-only retrieve fallback (now removed). The orphaned-span tree
    walk + dummy-parent construction is too entangled with the PG
    schema to lift to CH without a dedicated reader method (would
    need orphaned-span detection that compares parent_span_id against
    the same trace's id set). Staying PG-only until compare_traces is
    either retired or its callers move to the CH retrieve path.
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
