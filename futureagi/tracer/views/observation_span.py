import concurrent.futures
import hashlib
import io
import json
import time
import uuid
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

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
from tracer.serializers.filters import (
    ObserveGraphDataRequestSerializer,
    ObserveGraphDataResponseSerializer,
)
from tracer.serializers.observation_span import (
    ObservationAttributeListQuerySerializer,
    ObservationAttributeListResponseSerializer,
    ObservationSpanSerializer,
    RootSpansQuerySerializer,
    RootSpansResponseSerializer,
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
    degraded_graph_response,
    fetch_annotation_graph_ch,
    fetch_eval_graph_ch,
    fetch_system_metric_graph_ch,
)
from tracer.services.clickhouse.page_dedup import paginate_deduped
from tracer.services.clickhouse.query_builders.filters import (
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.query_service import (
    AnalyticsQueryService,
    QueryResult,
    merge_guaranteed_span_attribute_keys,
)
from tracer.services.clickhouse.read_budget import (
    FUTURE_TAIL_PROBE_SETTINGS,
    FUTURE_TAIL_PROBE_TIMEOUT_MS,
    build_future_tail_probe,
    is_read_budget_error,
)
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

_BOUNDED_ANALYTICS_SETTINGS = {
    "timeout_overflow_mode": "throw",
    "max_threads": 2,
    "max_memory_usage": 268_435_456,
    "max_bytes_to_read": 1_073_741_824,
    "read_overflow_mode": "throw",
    "max_result_rows": 2000,
    "result_overflow_mode": "throw",
}

# Span pages are the only list shape without a projection ordered by
# (project_id, start_time). Use one thread and one physical index-granule-sized
# block for their prefix and fat-column reads: the process-wide driver default
# is 100k rows, which lets a single query hold several 64 MiB fat-row granules
# at once and narrowly breach the 256 MiB cap.
_SPAN_PREFIX_READ_SETTINGS = {
    **_BOUNDED_ANALYTICS_SETTINGS,
    "max_threads": 1,
    "max_block_size": 8192,
}
_SPAN_CONTENT_READ_SETTINGS = {
    **_BOUNDED_ANALYTICS_SETTINGS,
    "max_threads": 1,
    "max_block_size": 8192,
}

# Keep the complete list path below the API's three-second SLO while leaving
# enough room for a bounded seed, local classification, and a skinny
# cross-slice history proof. Most direct-write ids end there. Mutable ids alone
# receive an eight-id full-window classifier: on CH25's 64 MiB granules, a
# 50--75-id Map classifier can otherwise exceed the finite read envelope.
_SPAN_FILTER_SCAN_BUDGET_MS = 2100
_SPAN_FILTER_QUERY_TIMEOUT_MS = 600
_SPAN_FILTER_SCAN_MIN_SLICE = timedelta(minutes=1)
_SPAN_FILTER_SCAN_MAX_ATTEMPTS = 24
_SPAN_FILTER_SCAN_MIN_QUERY_MS = 25
_SPAN_FILTER_MAX_FUTURE_SKEW = timedelta(minutes=5)
_SPAN_FILTER_LOCAL_CLASSIFIER_BATCH_SIZE = 64
_SPAN_FILTER_GLOBAL_CLASSIFIER_BATCH_SIZE = 8
_SPAN_FILTER_CLASSIFIER_MAX_READS = 64


def _execute_bounded_span_filter_prefix(
    builder,
    analytics,
    *,
    budget_ms: int = _SPAN_FILTER_SCAN_BUDGET_MS,
    max_slices: int = _SPAN_FILTER_SCAN_MAX_ATTEMPTS,
    clock=time.monotonic,
) -> tuple[QueryResult, bool, bool]:
    """Read a deterministic span prefix under one shared read deadline.

    The all-span table has no projection ordered solely by project and time, so
    both raw Map predicates and an unfiltered multi-day top-K can exceed a
    per-query read budget. Adjacent slices start at one minute and grow
    geometrically after completed empty or sparse reads. The scalar path uses a
    physical ``(id, start_time)`` seed; attribute predicates are safe raw
    prefilters, never final classifications. Each novel id is reduced to exact
    latest state inside its slice, then a skinny ID/time probe proves whether
    physical history exists elsewhere in the request. A direct-write id with
    no outside history is already exact. Only cross-slice ids are reclassified
    over the complete request, in proactive eight-id batches, so a newer key
    clear, non-match, or tombstone still rejects an older match without making
    every ordinary row pay for a wide Map scan.

    All slices share one wall-clock deadline.  A slice receives only the
    remaining timeout, and resource-limit failures never contribute partial
    rows because the query settings use ``throw`` overflow modes.

    Returns:
        ``(result, page_complete, full_window_scanned)``. ``page_complete`` is
        true once the unique-ID prefix needed for deterministic pagination is
        full, even if older slices were not scanned. If neither the prefix nor
        the entire requested window is complete before the budget is exhausted,
        collected rows remain an exact (short) global prefix and the flag is
        false.
    """
    prefix_limit = builder.page_number * builder.page_size + 2 * builder.page_size
    max_result_rows = int(_BOUNDED_ANALYTICS_SETTINGS["max_result_rows"])
    started_at = clock()

    # Concatenating time slices is order-preserving only for the canonical
    # newest-first span order. Current list serializers do not expose custom
    # sorting, but fail closed if a future caller supplies one.
    if builder.sort_params:
        return (
            QueryResult([], 0, "clickhouse", 0),
            False,
            False,
        )

    # ``max_result_rows`` is a per-statement guard, not a pagination ceiling.
    # Deep pages continue a saturated time slice with the canonical
    # ``(start_time, id)`` keyset below, so no individual result crosses the
    # guard even when the deterministic prefix spans several statements.
    if prefix_limit <= 0:
        return (
            QueryResult([], 0, "clickhouse", 0),
            False,
            False,
        )

    start_date, end_date = builder.parse_time_range(builder.filters)
    if start_date >= end_date:
        return (
            QueryResult([], 0, "clickhouse", 0),
            True,
            True,
        )

    deadline = started_at + max(int(budget_ms), 1) / 1000
    scalar_latest_page = builder.supports_latest_candidate_page()
    if not scalar_latest_page:
        # Never fall back to the raw non-FINAL list compiler. Unsupported
        # eval/annotation/custom-sort shapes must be surfaced explicitly by the
        # view instead of returning stale rows or an apparently valid empty page.
        return QueryResult([], 0, "clickhouse", 0), False, False

    # Keep later content/enrichment phases scoped even if the deadline is
    # exhausted before the first slice can execute.
    builder.params["start_date"] = start_date
    builder.params["end_date"] = end_date

    rows: list[dict] = []
    seen_ids: set[str] = set()
    matched_ids: set[str] = set()
    prefix_proven = False
    classifier_reads = 0

    def _span_order_key(row):
        value = row.get("start_time")
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                value = value.astimezone(UTC).replace(tzinfo=None)
        else:
            value = datetime.min
        return value, str(row.get("id", ""))

    def _classify_candidates(
        candidate_ids: list[str],
        *,
        batch_size: int,
        window_start=None,
        window_end=None,
    ) -> tuple[list[dict], bool]:
        nonlocal classifier_reads
        classified_rows: list[dict] = []
        for batch_offset in range(0, len(candidate_ids), batch_size):
            candidate_batch = candidate_ids[batch_offset : batch_offset + batch_size]
            if classifier_reads >= _SPAN_FILTER_CLASSIFIER_MAX_READS:
                return classified_rows, False
            remaining_ms = int((deadline - clock()) * 1000)
            if remaining_ms < _SPAN_FILTER_SCAN_MIN_QUERY_MS:
                return classified_rows, False
            match_query, match_params = (
                builder.build_latest_attribute_candidate_matches(
                    candidate_batch,
                    window_start=window_start,
                    window_end=window_end,
                )
            )
            try:
                classifier_reads += 1
                match_result = analytics.execute_ch_query(
                    match_query,
                    match_params,
                    timeout_ms=min(_SPAN_FILTER_QUERY_TIMEOUT_MS, remaining_ms),
                    settings={
                        **_SPAN_PREFIX_READ_SETTINGS,
                        "max_result_rows": len(candidate_batch),
                    },
                )
            except Exception as exc:
                if not is_read_budget_error(exc):
                    raise
                logger.warning(
                    "bounded span candidate classification exceeded read budget",
                    candidate_count=len(candidate_batch),
                    error_type=type(exc).__name__,
                )
                return classified_rows, False
            candidate_id_set = set(candidate_batch)
            classified_rows.extend(
                row
                for row in match_result.data
                if str(row.get("id", "")) in candidate_id_set
            )
        return classified_rows, True

    def _cross_slice_ids(candidate_ids: list[str], *, slice_start, slice_end):
        nonlocal classifier_reads
        if not candidate_ids or (slice_start <= start_date and slice_end >= end_date):
            return set(), True
        if classifier_reads >= _SPAN_FILTER_CLASSIFIER_MAX_READS:
            return set(), False
        remaining_ms = int((deadline - clock()) * 1000)
        if remaining_ms < _SPAN_FILTER_SCAN_MIN_QUERY_MS:
            return set(), False
        query, params = builder.build_cross_slice_candidate_ids(
            candidate_ids,
            slice_start=slice_start,
            slice_end=slice_end,
        )
        try:
            classifier_reads += 1
            result = analytics.execute_ch_query(
                query,
                params,
                timeout_ms=min(_SPAN_FILTER_QUERY_TIMEOUT_MS, remaining_ms),
                settings={
                    **_SPAN_PREFIX_READ_SETTINGS,
                    "max_result_rows": len(candidate_ids),
                },
            )
        except Exception as exc:
            if not is_read_budget_error(exc):
                raise
            logger.warning(
                "bounded span cross-slice proof exceeded read budget",
                candidate_count=len(candidate_ids),
                error_type=type(exc).__name__,
            )
            return set(), False
        candidate_id_set = set(candidate_ids)
        return {
            str(row.get("id", ""))
            for row in result.data
            if str(row.get("id", "")) in candidate_id_set
        }, True

    scan_now = timezone.now()
    if timezone.is_naive(end_date):
        scan_now = scan_now.replace(tzinfo=None)
    # The UI may send end-of-local-day, hours ahead of server time. Clamp only
    # the sliced fallback (the healthy whole-window attempt above remains
    # exact) so its bounded attempts are not consumed by empty future ranges.
    cursor = min(end_date, scan_now + _SPAN_FILTER_MAX_FUTURE_SKEW)
    full_window_scanned = False
    slice_width = _SPAN_FILTER_SCAN_MIN_SLICE
    keyset_start_time = None
    keyset_id: str | None = None

    if cursor < end_date:
        remaining_ms = int((deadline - clock()) * 1000)
        if remaining_ms < _SPAN_FILTER_SCAN_MIN_QUERY_MS:
            return (
                QueryResult([], 0, "clickhouse", 0),
                False,
                False,
            )
        tail_query, tail_params = build_future_tail_probe(
            start=cursor,
            end=end_date,
            root_only=False,
            project_id=builder.project_id,
            project_ids=builder.project_ids,
        )
        try:
            tail_result = analytics.execute_ch_query(
                tail_query,
                tail_params,
                timeout_ms=min(FUTURE_TAIL_PROBE_TIMEOUT_MS, remaining_ms),
                settings=FUTURE_TAIL_PROBE_SETTINGS,
            )
        except Exception as exc:
            logger.warning(
                "span future-tail proof failed; returning degraded",
                project_id=builder.project_id,
                error_type=type(exc).__name__,
            )
            return (
                QueryResult([], 0, "clickhouse", 0),
                False,
                False,
            )
        tail_data = getattr(tail_result, "data", None)
        if not isinstance(tail_data, list) or tail_data:
            return (
                QueryResult([], 0, "clickhouse", 0),
                False,
                False,
            )

    for _ in range(max(int(max_slices), 0)):
        if prefix_proven:
            break
        if cursor <= start_date:
            full_window_scanned = True
            break

        remaining_ms = int((deadline - clock()) * 1000)
        if remaining_ms < _SPAN_FILTER_SCAN_MIN_QUERY_MS:
            break

        slice_start = max(start_date, cursor - slice_width)
        # Fetch a one-page duplicate margin. ReplacingMergeTree versions can
        # repeat IDs; completion is based on unique IDs, never raw row count.
        # If duplicates consume this entire bounded result before the unique
        # target is reached, the slice itself is not proven exhausted, so the
        # executor stops incomplete rather than skipping to the older minute.
        slice_limit = min(
            max_result_rows,
            max(
                builder.page_size,
                prefix_limit - len(rows) + builder.page_size,
            ),
        )
        # Keep the seed physical and narrow. The raw predicate is only a safe
        # candidate prefilter: an older matching version remains provisional.
        # The local latest-state classifier plus cross-slice proof below makes
        # acceptance exact for attributes and physical system metrics alike.
        slice_query, slice_params = builder.build_latest_attribute_candidate_seed_page(
            slice_start=slice_start,
            slice_end=cursor,
            limit=slice_limit,
            before_start_time=keyset_start_time,
            before_id=keyset_id,
        )
        slice_settings = {
            **_SPAN_PREFIX_READ_SETTINGS,
            "max_result_rows": slice_limit,
        }
        try:
            slice_result = analytics.execute_ch_query(
                slice_query,
                slice_params,
                timeout_ms=min(_SPAN_FILTER_QUERY_TIMEOUT_MS, remaining_ms),
                settings=slice_settings,
            )
        except Exception as exc:
            if not is_read_budget_error(exc):
                raise
            logger.warning(
                "bounded span prefix slice exceeded read budget",
                slice_seconds=int((cursor - slice_start).total_seconds()),
                error=str(exc)[:200],
            )
            # A sparse window may have widened geometrically after several
            # cheap, completed reads. If that wider candidate query crosses a
            # budget, retry only the still-unread interval at half the width;
            # completed newer windows remain proven. At the minimum width we
            # fail closed rather than returning a false-complete page.
            if slice_width > _SPAN_FILTER_SCAN_MIN_SLICE:
                slice_width = max(
                    _SPAN_FILTER_SCAN_MIN_SLICE,
                    slice_width / 2,
                )
                keyset_start_time = None
                keyset_id = None
                continue
            break

        slice_rows = list(slice_result.data)
        # The physical seed can contain several versions of one id. Its SQL is
        # ordered, but restoring the order here makes the frontier proof robust
        # to test doubles and distributed result merging.
        slice_rows.sort(key=_span_order_key, reverse=True)
        slice_exhausted = len(slice_rows) < slice_limit
        candidate_ids: list[str] = []
        for row in slice_rows[:slice_limit]:
            span_id = str(row.get("id", ""))
            if not span_id or span_id in seen_ids:
                continue
            # Mark before classification. A non-match or tombstone is a
            # conclusive classification for this request and must suppress
            # every older physical version in subsequent slices.
            seen_ids.add(span_id)
            candidate_ids.append(span_id)

        # First classify inside this bounded slice. For an id with no physical
        # history elsewhere in the request, that local result is already the
        # exact global latest state. Only cross-slice ids pay for a full-window
        # classifier.
        local_rows, classifier_complete = _classify_candidates(
            candidate_ids,
            batch_size=_SPAN_FILTER_LOCAL_CLASSIFIER_BATCH_SIZE,
            window_start=slice_start,
            window_end=cursor,
        )
        if not classifier_complete:
            break
        cross_ids, cross_complete = _cross_slice_ids(
            candidate_ids,
            slice_start=slice_start,
            slice_end=cursor,
        )
        if not cross_complete:
            break
        accepted_rows = [
            row for row in local_rows if str(row.get("id", "")) not in cross_ids
        ]
        if cross_ids:
            cross_candidates = [
                span_id for span_id in candidate_ids if span_id in cross_ids
            ]
            global_rows, classifier_complete = _classify_candidates(
                cross_candidates,
                batch_size=_SPAN_FILTER_GLOBAL_CLASSIFIER_BATCH_SIZE,
            )
            if not classifier_complete:
                break
            accepted_rows.extend(global_rows)
        for row in accepted_rows:
            span_id = str(row.get("id", ""))
            if not span_id or span_id in matched_ids:
                continue
            matched_ids.add(span_id)
            rows.append(row)

        if slice_exhausted and slice_start <= start_date:
            # This statement proved the final half-open request slice empty
            # below its returned rows, even if the prefix frontier lets us
            # return before the cursor-update block below.
            full_window_scanned = True

        if scalar_latest_page and len(rows) >= prefix_limit:
            ordered_matches = sorted(rows, key=_span_order_key, reverse=True)
            cutoff_key = _span_order_key(ordered_matches[prefix_limit - 1])
            if slice_exhausted:
                normalized_slice_start = (
                    slice_start.astimezone(UTC).replace(tzinfo=None)
                    if getattr(slice_start, "tzinfo", None) is not None
                    else slice_start
                )
                frontier_key = (normalized_slice_start, "")
            elif slice_rows:
                frontier_key = _span_order_key(slice_rows[-1])
            else:  # pragma: no cover - saturated empty results are impossible
                frontier_key = (datetime.max, "")
            if cutoff_key >= frontier_key:
                prefix_proven = True
                break

        if slice_exhausted:
            # This completed query exhausted the whole half-open slice.
            cursor = slice_start
            keyset_start_time = None
            keyset_id = None
            remaining_window = cursor - start_date
            if remaining_window > timedelta(0):
                slice_width = min(slice_width * 2, remaining_window)
        if not scalar_latest_page and len(rows) >= prefix_limit:
            break
        if not slice_exhausted:
            # Continue the exact same half-open time slice below its last
            # canonical key. A successful throw-mode top-K proves every row
            # above the key was read; strict ``(start_time, id)`` descent then
            # makes the concatenated chunks one exact global prefix. Physical
            # duplicates with the same key can be skipped safely because the
            # endpoint de-duplicates by id.
            last_row = slice_rows[slice_limit - 1]
            next_start_time = last_row.get("start_time")
            next_id = str(last_row.get("id", ""))
            if next_start_time is None or not next_id:
                break
            next_keyset = (next_start_time, next_id)
            if next_keyset == (keyset_start_time, keyset_id):
                break
            keyset_start_time, keyset_id = next_keyset

    if cursor <= start_date:
        full_window_scanned = True
    if scalar_latest_page:
        rows.sort(key=_span_order_key, reverse=True)
    page_complete = (
        prefix_proven
        or (not scalar_latest_page and len(rows) >= prefix_limit)
        or full_window_scanned
    )
    elapsed_ms = max((clock() - started_at) * 1000, 0)
    return (
        QueryResult(rows, len(rows), "clickhouse", elapsed_ms),
        page_complete,
        full_window_scanned,
    )


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
    project_ids: list[str] | None = None,
) -> dict[str, str]:
    """Resolve ``{trace_id: root_span_id}`` for *trace_ids*, returning only traces
    whose owning project is org/workspace-accessible. Collector traces have no PG
    ``Trace`` row, so the project_id is learned from CH and re-checked against the
    PG ``Project`` authority. FAIL CLOSED: an untenanted / cross-org trace is dropped
    (no key) — same response shape as before.

    ``project_ids`` (optional) only prunes the CH scan; the PG re-check stays the
    tenant boundary, so it can narrow results but never widen them. Pass a
    superset of the traces' owning projects, else a valid root is silently dropped.
    """
    if not trace_ids:
        return {}

    from tracer.services.clickhouse.v2 import get_reader

    with get_reader() as reader:
        roots = reader.root_ids_by_trace_ids(
            [str(tid) for tid in trace_ids], project_ids=project_ids
        )

    # Candidate project_ids from the lean root projection, to verify against PG.
    candidate_project_ids = {pid for _, pid in roots.values() if pid}
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

    return {
        tid: span_id
        for tid, (span_id, pid) in roots.items()
        if pid is not None and pid in allowed_project_ids
    }


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

            # Span telemetry is written directly to ClickHouse. Resolve the
            # row there first, then tenant-gate its project against the small
            # PostgreSQL Project table inside `_retrieve_clickhouse`.
            from tracer.services.clickhouse.query_service import (
                AnalyticsQueryService,
            )

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
            span_query,
            {"span_id": str(observation_span_id)},
            timeout_ms=750,
            settings=_BOUNDED_ANALYTICS_SETTINGS,
        )

        if not result.data:
            return self._gm.bad_request(get_error_message("OBSERVATION_SPAN_NOT_FOUND"))

        row = result.data[0]
        project_manager = getattr(Project, "no_workspace_objects", Project.objects)
        if not project_manager.filter(
            _project_workspace_scope_q(request, project_prefix=""),
            id=row["project_id"],
            organization=_get_request_organization(request),
        ).exists():
            return self._gm.bad_request(get_error_message("OBSERVATION_SPAN_NOT_FOUND"))

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

        if str(request.query_params.get("preview", "")).lower() in (
            "1",
            "true",
            "yes",
        ):
            return self._gm.success_response(
                {"observation_span": observation_span, "evals_metrics": {}}
            )

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
        try:
            children_result = analytics.execute_ch_query(
                children_query,
                {
                    "trace_id": str(row["trace_id"]),
                    "project_id": str(row["project_id"]),
                },
                timeout_ms=750,
                settings=_BOUNDED_ANALYTICS_SETTINGS,
            )
        except Exception as exc:
            logger.warning(
                "span detail child enrichment exceeded budget",
                span_id=str(observation_span_id),
                error=str(exc)[:200],
            )
            children_result = None
        children_span_ids = [
            str(r["id"]) for r in (children_result.data if children_result else [])
        ]

        # Fetch eval metrics from CH
        evals_metrics = {}
        evals_metrics_degraded = False
        if children_span_ids:
            try:
                eval_rows = analytics.get_children_eval_metrics_ch(children_span_ids)
            except Exception as exc:
                # Eval lifecycle columns were added after the original
                # eval_logger_v2 deployment. A lagging replica/schema must not
                # make an otherwise valid span detail disappear.
                eval_rows = []
                evals_metrics_degraded = True
                logger.warning(
                    "span detail eval enrichment unavailable",
                    span_id=str(observation_span_id),
                    error=str(exc)[:200],
                )

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

        result = {
            "observation_span": observation_span,
            "evals_metrics": evals_metrics,
        }
        if evals_metrics_degraded:
            result["evals_metrics_degraded"] = True
        return self._gm.success_response(result)

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

    @validated_request(
        query_serializer=RootSpansQuerySerializer,
        responses={200: RootSpansResponseSerializer},
    )
    @action(detail=False, methods=["get"], url_path="root-spans")
    def root_spans(self, request, *args, **kwargs):
        """
        Given a list of trace_ids, return the root span ID for each trace.
        Root span = the span where parent_span_id IS NULL for that trace.

        Query params (repeated): trace_ids (required,
        ?trace_ids=<id>&trace_ids=<id>) + optional project_ids (prunes the CH
        scan). Response: { "result": { "<trace_id>": "<span_id>", ... } }
        """
        try:
            trace_ids = request.validated_query_data["trace_ids"]
            project_ids = request.validated_query_data.get("project_ids") or None

            # Collector traces have no PG ``Trace`` row; the gate resolves the root
            # span + tenant from CH/PG-Project instead (fail closed). See selector.
            org = _get_request_organization(request)
            result = allowed_root_spans_for_request(
                trace_ids,
                organization=org,
                project_scope_q=_project_workspace_scope_q(request, project_prefix=""),
                project_ids=project_ids,
            )
            return self._gm.success_response(result)
        except Exception as e:
            # fail closed: any CH/PG error returns no data, never a partial leak
            logger.exception("Error fetching root spans", error=str(e))
            return self._gm.bad_request("Error fetching root spans")

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
        serializer = SpanListQuerySerializer(data=request.query_params)
        if not serializer.is_valid():
            return self._gm.bad_request(serializer.errors)
        validated_data = serializer.validated_data
        project_version_id = str(validated_data["project_version_id"])

        # Tenant gate via PG (ProjectVersion + Project.organization).
        try:
            project_version = ProjectVersion.objects.get(
                _project_workspace_scope_q(request),
                id=project_version_id,
                project__organization=_get_request_organization(request),
            )
        except ProjectVersion.DoesNotExist:
            return self._gm.bad_request("Project version not found or access denied")

        # CH is authoritative post-migration. A bounded-read failure is explicit
        # degraded state; any query/programming error must surface instead of
        # being disguised as valid PostgreSQL telemetry.
        analytics = AnalyticsQueryService()
        try:
            return self._list_spans_non_observe_clickhouse(
                request,
                project_version_id,
                project_version,
                analytics,
                validated_data,
            )
        except Exception as e:
            if not is_read_budget_error(e):
                raise
            logger.warning(
                "experiment span list exceeded read budget; returning degraded",
                project_version_id=project_version_id,
                error=str(e)[:200],
            )
            return self._gm.success_response(
                {
                    "metadata": {
                        "total_rows": 0,
                        "total_rows_is_lower_bound": True,
                        "query_complete": False,
                        "query_status": "degraded",
                        "query_error_code": "read_budget_exceeded",
                    },
                    "table": [],
                    "config": get_default_span_config(),
                }
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

            if not project_id:
                return self._gm.bad_request("project_id is required")

            try:
                Project.objects.get(
                    _project_workspace_scope_q(self.request, project_prefix=""),
                    id=project_id,
                    organization=org,
                )
            except Project.DoesNotExist:
                return self._gm.bad_request("Project not found or access denied")

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
                org_project_ids=None,
                org=org,
            )

        except Exception as e:
            if not is_read_budget_error(e):
                raise
            logger.warning(
                "span list exceeded read budget; returning an empty page",
                error=str(e)[:200],
            )
            return self._gm.success_response(
                {
                    "metadata": {
                        "total_rows": 0,
                        "total_rows_is_lower_bound": True,
                        "query_complete": False,
                        "query_status": "degraded",
                        "query_error_code": "read_budget_exceeded",
                    },
                    "table": [],
                    "config": get_default_span_config(),
                }
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
        preview_mode = bool(validated_data.get("preview", False))
        query_page_number = 0 if preview_mode else page_number
        query_page_size = min(page_size, 10) if preview_mode else page_size
        enrichment_error_codes: set[str] = set()

        def _enrichment_error_code(exc: Exception) -> str:
            return (
                "read_budget_exceeded" if is_read_budget_error(exc) else "query_failed"
            )

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

        # Get eval config IDs. Single-project uses the bounded CH lookup.
        # Cross-project pages omit optional eval columns: reading EvalLogger /
        # ObservationSpan from PostgreSQL here reintroduced the retired
        # telemetry path and could dominate the base CH span-list request.
        eval_config_ids = []
        if preview_mode:
            # TaskLivePreview only needs a bounded page of span/trace IDs; the
            # selected row is hydrated through the trace-detail endpoint. Do
            # not run eval-column discovery for this latency-sensitive picker:
            # it is unrelated to variable mapping and can add another
            # full-window ClickHouse read on large projects.
            eval_configs = []
            eval_config_ids = []
        elif org_scope:
            eval_configs = []
            eval_config_ids = []
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
            # Discover eval columns over the SAME window the user is viewing, not
            # a fixed 30 days: cover [requested-start, now] so a config with data
            # anywhere in the requested range keeps its column (no missing columns
            # on a 6-month view, no spurious empty columns on a 24h view).
            # candidate_config_ids keeps the scan bounded by the eval table's
            # leading sort key at any depth. Default (unfiltered) view → ~30 days.
            window_days = SpanListQueryBuilder.window_days_covering(filters)
            # Short-TTL cache: "which configs have data" changes on config
            # creation / first eval write, not per page load — the fast-path CH
            # read still costs ~0.4-0.9s per request at 10M eval rows (measured),
            # and this endpoint fires it on EVERY page. Key includes the
            # candidate set and window so a newly-created config or a different
            # time range gets a fresh entry; worst case a brand-new config's
            # column appears one TTL late.
            ids_with_data: set[str] = set()
            if candidate_ids:
                cache_key = (
                    "span_list_eval_cfgs:"
                    + hashlib.sha256(
                        (
                            str(project_id)
                            + "|"
                            + ",".join(sorted(candidate_ids))
                            + f"|w={window_days}"
                        ).encode()
                    ).hexdigest()
                )
                cached_ids = django_cache.get(cache_key)
                if cached_ids is not None:
                    ids_with_data = set(cached_ids)
                else:
                    try:
                        ids_with_data = set(
                            analytics.get_eval_config_ids_with_data_ch(
                                str(project_id),
                                timeout_ms=750,
                                candidate_config_ids=candidate_ids,
                                window_days=window_days,
                            )
                        )
                        django_cache.set(cache_key, list(ids_with_data), timeout=120)
                    except Exception as exc:
                        enrichment_error_codes.add(_enrichment_error_code(exc))
                        # Optional grid columns must not block base span data.
                        logger.warning(
                            "span eval-column discovery exceeded budget",
                            project_id=str(project_id),
                            error=str(exc)[:200],
                        )
            eval_configs = [c for c in project_configs if str(c.id) in ids_with_data]
            eval_config_ids = [str(c.id) for c in eval_configs]

        # Labels can be project-local or org/shared labels that are referenced
        # by span scores. Use the score-backed helper so span columns and
        # annotation filters match the actual data returned from ClickHouse.
        annotation_labels = (
            []
            if preview_mode
            else get_annotation_labels_for_project(
                project_id, project_ids=org_project_ids if org_scope else None
            )
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
            page_number=query_page_number,
            page_size=query_page_size,
            eval_config_ids=eval_config_ids,
            annotation_label_ids=annotation_label_ids,
        )

        # Phase 1: Paginated spans (light columns — no input/output).
        #
        # The all-span table has no project/time-ordered projection. Evaluate
        # every canonical newest-first page through adjacent time slices so an
        # unfiltered 30-day top-K cannot scan the full tenant before LIMIT.
        # Map-backed predicates use the same exact executor. It returns an
        # explicit incomplete prefix when the proof cannot finish.
        bounded_filter_path = builder.requires_bounded_filter_scan()
        # Every supported request uses the bounded latest-state executor.  An
        # unsupported shape must be explicit; silently falling back to the raw
        # non-FINAL compiler can resurrect stale values and tombstoned spans.
        bounded_prefix_path = True
        if not builder.supports_latest_candidate_page():
            return self._gm.success_response(
                {
                    "metadata": {
                        "total_rows": 0,
                        "total_rows_is_lower_bound": True,
                        "has_more": False,
                        "query_complete": False,
                        "query_status": "degraded",
                        "query_error_code": "unsupported_filter_shape",
                    },
                    "table": [],
                    "config": get_default_span_config(),
                }
            )
        phase1_query_complete = True
        phase1_full_window_scanned = False
        try:
            (
                result,
                phase1_query_complete,
                phase1_full_window_scanned,
            ) = _execute_bounded_span_filter_prefix(builder, analytics)
        except UnsupportedFilterShapeError:
            return self._gm.success_response(
                {
                    "metadata": {
                        "total_rows": 0,
                        "total_rows_is_lower_bound": True,
                        "has_more": False,
                        "query_complete": False,
                        "query_status": "degraded",
                        "query_error_code": "unsupported_filter_shape",
                    },
                    "table": [],
                    "config": get_default_span_config(),
                }
            )
        phase1_proven_rows = len(
            {str(row.get("id", "")) for row in result.data if row.get("id")}
        )

        # The executor replenishes saturated slices with a strict keyset until
        # it proves the exact unique prefix plus a sentinel (or exhausts the
        # window).  Slicing here therefore cannot skip rows even when physical
        # ReplacingMergeTree versions exceed the historical duplicate margin.
        result.data, has_more = paginate_deduped(
            result.data, "id", query_page_number, query_page_size
        )
        total_count = (
            query_page_number * query_page_size
            + len(result.data)
            + (1 if has_more else 0)
            if phase1_query_complete
            else phase1_proven_rows
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

        # Phases 1b/2/3 are independent once the page ids are known —
        # run them concurrently so request latency is Phase1 + max(rest), not
        # the serial sum. `analytics.ch_client` pools connections behind a lock
        # (see ClickHouseClient._get_client), so concurrent execute_ch_query
        # calls are safe. Any worker exception propagates via .result() and is
        # handled by the endpoint's outer try/except, same as the serial code.
        def _fetch_content():
            if not span_ids:
                return [], None
            content_query, content_params = builder.build_content_query(span_ids)
            if not content_query:
                return [], None
            try:
                content_rows = analytics.execute_ch_query(
                    content_query,
                    content_params,
                    timeout_ms=750,
                    settings=_SPAN_CONTENT_READ_SETTINGS,
                ).data
                returned_ids = {
                    str(row.get("id", "")) for row in content_rows if row.get("id")
                }
                content_error = (
                    None if set(span_ids).issubset(returned_ids) else "query_failed"
                )
                if content_error is not None:
                    logger.warning(
                        "span content enrichment returned fewer spans than requested",
                        returned=len(returned_ids),
                        requested=len(span_ids),
                        project_id=str(project_id) if project_id else None,
                    )
                return content_rows, content_error
            except Exception as exc:
                logger.warning(
                    "span content enrichment exceeded budget",
                    project_id=str(project_id) if project_id else None,
                    error=str(exc)[:200],
                )
                return [], _enrichment_error_code(exc)

        def _fetch_preview_hydration():
            """Read display fields plus only attributes selected by filters."""
            if not span_ids:
                return [], None
            preview_query, preview_params = builder.build_preview_hydration_query(
                span_ids
            )
            if not preview_query:
                return [], None
            try:
                preview_rows = analytics.execute_ch_query(
                    preview_query,
                    preview_params,
                    timeout_ms=750,
                    settings=_SPAN_CONTENT_READ_SETTINGS,
                ).data
                returned_ids = {
                    str(row.get("id", "")) for row in preview_rows if row.get("id")
                }
                preview_error = (
                    None if set(span_ids).issubset(returned_ids) else "query_failed"
                )
                return preview_rows, preview_error
            except Exception as exc:
                logger.warning(
                    "span preview hydration exceeded budget",
                    project_id=str(project_id) if project_id else None,
                    error_type=type(exc).__name__,
                )
                return [], _enrichment_error_code(exc)

        def _fetch_evals():
            if not (span_ids and eval_config_ids):
                return {}, None
            eval_query, eval_params = builder.build_eval_query(
                span_ids, created_after=page_min_created_at
            )
            if not eval_query:
                return {}, None
            try:
                eval_result = analytics.execute_ch_query(
                    eval_query,
                    eval_params,
                    timeout_ms=750,
                    settings=_BOUNDED_ANALYTICS_SETTINGS,
                )
                return SpanListQueryBuilder.pivot_eval_results(eval_result.data), None
            except Exception as exc:
                logger.warning(
                    "span eval enrichment exceeded budget",
                    project_id=str(project_id) if project_id else None,
                    error=str(exc)[:200],
                )
                return {}, _enrichment_error_code(exc)

        def _fetch_annotations():
            if not (span_ids and annotation_label_ids):
                return {}, None
            ann_query, ann_params = builder.build_annotation_query(
                span_ids, created_after=page_min_created_at
            )
            if not ann_query:
                return {}, None
            try:
                ann_result = analytics.execute_ch_query(
                    ann_query,
                    ann_params,
                    timeout_ms=750,
                    settings=_BOUNDED_ANALYTICS_SETTINGS,
                )
                return (
                    SpanListQueryBuilder.pivot_annotation_results(
                        ann_result.data, label_types
                    ),
                    None,
                )
            except Exception as exc:
                logger.warning(
                    "span annotation enrichment exceeded budget",
                    project_id=str(project_id) if project_id else None,
                    error=str(exc)[:200],
                )
                return {}, _enrichment_error_code(exc)

        # The task preview is deliberately lean. It point-hydrates display
        # fields plus only the custom attributes selected by the request; it
        # does not read span content, evals, annotations, or an exact count.
        # Keeping those broad secondary reads out prevents a six-month request
        # from blocking the variable-mapping picker.
        # Exact counts are intentionally outside the list request's critical
        # path. On the production whale tenant they consumed most of the 750ms
        # budget before content could start. The prefix length is an honest,
        # monotonic lower bound and pagination still uses ``has_more``.
        count_is_lower_bound = True
        content_error = None
        eval_error = None
        annotation_error = None
        if preview_mode:
            content_rows, content_error = _fetch_preview_hydration()
            eval_map = {}
            annotation_map = {}
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                content_f = pool.submit(_fetch_content)
                evals_f = pool.submit(_fetch_evals)
                anns_f = pool.submit(_fetch_annotations)
                content_rows, content_error = content_f.result()
                eval_map, eval_error = evals_f.result()
                annotation_map, annotation_error = anns_f.result()

        enrichment_error_codes.update(
            code
            for code in (content_error, eval_error, annotation_error)
            if code is not None
        )

        # Phase 1b merge: input/output/attributes_extra AND the typed attr maps
        # (attrs_string/attrs_number/attrs_bool) onto the page rows. The typed
        # maps are read by flatten_span_attributes_into_entry() below to populate
        # custom span-attribute columns — build_content_query fetches them, so
        # dropping them here renders every typed-map custom column empty. Use the
        # shared helper (null-safe factory defaults for the map keys), matching
        # the trace-list read path.
        merge_content_rows(
            result.data,
            content_rows,
            id_key="id",
            keys=(
                "trace_id",
                "name",
                "observation_type",
                "status",
                "start_time",
                "end_time",
                "latency_ms",
                "cost",
                "total_tokens",
                "prompt_tokens",
                "completion_tokens",
                "model",
                "provider",
                "end_user_id",
                "created_at",
                "input",
                "output",
                "attributes_extra",
                "attrs_string",
                "attrs_number",
                "attrs_bool",
            ),
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
        end_user_ids = (
            set()
            if preview_mode
            else {
                str(r.get("end_user_id")) for r in result.data if r.get("end_user_id")
            }
        )
        end_user_map = {}
        if end_user_ids:
            from tracer.services.clickhouse.v2.end_user_dict_reader import (
                resolve_end_user_fields,
            )

            try:
                end_user_map = resolve_end_user_fields(end_user_ids)
            except Exception as exc:
                enrichment_error_codes.add(_enrichment_error_code(exc))
                logger.warning(
                    "span end-user enrichment failed",
                    project_id=str(project_id) if project_id else None,
                    error=str(exc)[:200],
                )

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

        metadata = {"total_rows": total_count, "has_more": has_more}
        if (
            count_is_lower_bound
            or preview_mode
            or (bounded_filter_path and not phase1_full_window_scanned)
        ):
            metadata["total_rows_is_lower_bound"] = True
        if bounded_prefix_path:
            metadata.update(
                {
                    "query_complete": phase1_query_complete,
                    "query_status": (
                        "complete" if phase1_query_complete else "degraded"
                    ),
                }
            )
            if not phase1_query_complete:
                metadata["query_error_code"] = "read_budget_exceeded"
        if enrichment_error_codes:
            metadata.update(
                {
                    "query_complete": False,
                    "query_status": "degraded",
                    "query_error_code": (
                        "query_failed"
                        if "query_failed" in enrichment_error_codes
                        else "read_budget_exceeded"
                    ),
                }
            )

        response = {
            "metadata": metadata,
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
        enrichment_error_codes: set[str] = set()

        def _enrichment_error_code(exc: Exception) -> str:
            return (
                "read_budget_exceeded" if is_read_budget_error(exc) else "query_failed"
            )

        # Get eval configs from PG (small config table)
        eval_configs = list(
            CustomEvalConfig.objects.filter(
                project_id=project_id,
                deleted=False,
            ).select_related("eval_template")
        )
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

        # Phase 1: Paginated spans (light columns — no input/output).
        # Every canonical newest-first page shares the same exact sliced prefix
        # executor as the observe grid/task preview. This prevents unfiltered
        # and indexed requests from doing a full-window top-K on the all-span
        # table, which has no project/time-ordered projection.
        bounded_filter_path = builder.requires_bounded_filter_scan()
        bounded_prefix_path = True
        if not builder.supports_latest_candidate_page():
            return self._gm.success_response(
                {
                    "column_config": get_default_span_config(),
                    "metadata": {
                        "total_rows": 0,
                        "total_rows_is_lower_bound": True,
                        "has_more": False,
                        "query_complete": False,
                        "query_status": "degraded",
                        "query_error_code": "unsupported_filter_shape",
                    },
                    "table": [],
                }
            )
        phase1_query_complete = True
        phase1_full_window_scanned = False
        try:
            (
                result,
                phase1_query_complete,
                phase1_full_window_scanned,
            ) = _execute_bounded_span_filter_prefix(builder, analytics)
        except UnsupportedFilterShapeError:
            return self._gm.success_response(
                {
                    "column_config": get_default_span_config(),
                    "metadata": {
                        "total_rows": 0,
                        "total_rows_is_lower_bound": True,
                        "has_more": False,
                        "query_complete": False,
                        "query_status": "degraded",
                        "query_error_code": "unsupported_filter_shape",
                    },
                    "table": [],
                }
            )
        phase1_proven_rows = len(
            {str(row.get("id", "")) for row in result.data if row.get("id")}
        )
        # The bounded executor has already replenished an exact unique prefix
        # plus sentinel under its shared deadline.
        result.data, has_more = paginate_deduped(
            result.data, "id", page_number, page_size
        )

        # Phase 1b: Fetch input/output for the page
        span_ids = [str(row.get("id", "")) for row in result.data]
        if span_ids:
            content_query, content_params = builder.build_content_query(span_ids)
            if content_query:
                try:
                    content_result = analytics.execute_ch_query(
                        content_query,
                        content_params,
                        timeout_ms=750,
                        settings=_SPAN_CONTENT_READ_SETTINGS,
                    )
                    content_map = {str(r.get("id", "")): r for r in content_result.data}
                    if not set(span_ids).issubset(content_map):
                        enrichment_error_codes.add("query_failed")
                        logger.warning(
                            "prototype span content enrichment returned fewer spans than requested",
                            returned=len(content_map),
                            requested=len(span_ids),
                            project_id=project_id,
                        )
                    merge_content_rows(
                        result.data,
                        content_result.data,
                        id_key="id",
                        keys=(
                            "trace_id",
                            "name",
                            "observation_type",
                            "status",
                            "start_time",
                            "end_time",
                            "latency_ms",
                            "cost",
                            "total_tokens",
                            "prompt_tokens",
                            "completion_tokens",
                            "model",
                            "provider",
                            "end_user_id",
                            "created_at",
                            "input",
                            "output",
                            "attributes_extra",
                            "attrs_string",
                            "attrs_number",
                            "attrs_bool",
                        ),
                    )
                except Exception as exc:
                    enrichment_error_codes.add(_enrichment_error_code(exc))
                    logger.warning(
                        "prototype span content enrichment exceeded budget",
                        project_id=project_id,
                        error=str(exc)[:200],
                    )

        # Do not make task/eval setup wait for a second full-window scan merely
        # to render a footer. The proven prefix is an honest lower bound;
        # ``has_more`` remains the pagination signal.
        total_count = (
            page_number * page_size + len(result.data) + (1 if has_more else 0)
            if phase1_query_complete
            else phase1_proven_rows
        )
        count_is_lower_bound = True

        # Phase 2: Eval scores
        eval_map = {}
        if span_ids and eval_config_ids:
            eval_query, eval_params = builder.build_eval_query(span_ids)
            if eval_query:
                try:
                    eval_result = analytics.execute_ch_query(
                        eval_query,
                        eval_params,
                        timeout_ms=750,
                        settings=_BOUNDED_ANALYTICS_SETTINGS,
                    )
                    eval_map = SpanListQueryBuilder.pivot_eval_results(eval_result.data)
                except Exception as exc:
                    enrichment_error_codes.add(_enrichment_error_code(exc))
                    logger.warning(
                        "prototype span eval enrichment exceeded budget",
                        project_id=project_id,
                        error=str(exc)[:200],
                    )

        # Phase 3: Annotations
        annotation_map = {}
        if span_ids and annotation_label_ids:
            ann_query, ann_params = builder.build_annotation_query(span_ids)
            if ann_query:
                try:
                    ann_result = analytics.execute_ch_query(
                        ann_query,
                        ann_params,
                        timeout_ms=750,
                        settings=_BOUNDED_ANALYTICS_SETTINGS,
                    )
                    annotation_map = SpanListQueryBuilder.pivot_annotation_results(
                        ann_result.data, label_types
                    )
                except Exception as exc:
                    enrichment_error_codes.add(_enrichment_error_code(exc))
                    logger.warning(
                        "prototype span annotation enrichment exceeded budget",
                        project_id=project_id,
                        error=str(exc)[:200],
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

        metadata = {"total_rows": total_count, "has_more": has_more}
        if count_is_lower_bound or (
            bounded_filter_path and not phase1_full_window_scanned
        ):
            metadata["total_rows_is_lower_bound"] = True
        if bounded_prefix_path:
            metadata.update(
                {
                    "query_complete": phase1_query_complete,
                    "query_status": (
                        "complete" if phase1_query_complete else "degraded"
                    ),
                }
            )
            if not phase1_query_complete:
                metadata["query_error_code"] = "read_budget_exceeded"
        if enrichment_error_codes:
            metadata.update(
                {
                    "query_complete": False,
                    "query_status": "degraded",
                    "query_error_code": (
                        "query_failed"
                        if "query_failed" in enrichment_error_codes
                        else "read_budget_exceeded"
                    ),
                }
            )

        response = {
            "column_config": column_config,
            "metadata": metadata,
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
                            observe_type="span",
                        )
                    )
                except Exception as exc:
                    logger.warning(
                        "span graph query failed; returning degraded series",
                        project_id=project_id,
                        metric_id=metric_id,
                        error=str(exc)[:200],
                    )
                    return self._gm.success_response(
                        degraded_graph_response(metric_id, exc)
                    )
            elif type == "EVAL":
                metric_id = req_data_config.get("id", "")
                try:
                    return self._gm.success_response(
                        fetch_eval_graph_ch(
                            analytics=analytics,
                            project_id=project_id,
                            filters=filters,
                            interval=interval,
                            req_data_config=req_data_config,
                            observe_type="span",
                        )
                    )
                except Exception as exc:
                    logger.warning(
                        "span eval graph query failed; returning degraded series",
                        project_id=project_id,
                        metric_id=metric_id,
                        error=str(exc)[:200],
                    )
                    return self._gm.success_response(
                        degraded_graph_response(metric_id, exc)
                    )
            elif type == "ANNOTATION":
                metric_id = req_data_config.get("id", "")
                try:
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
                except Exception as exc:
                    logger.warning(
                        "span annotation graph query failed; returning degraded series",
                        project_id=project_id,
                        metric_id=metric_id,
                        error=str(exc)[:200],
                    )
                    return self._gm.success_response(
                        degraded_graph_response(metric_id, exc)
                    )
            return self._gm.bad_request("Filter property type is not valid")

        except Exception as e:
            logger.exception(f"Error in fetching graph data: {str(e)}")
            return self._gm.bad_request("Error fetching graph data")

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
            if not self._attribute_project_for_request(request, project_id):
                return self._gm.not_found("Project not found")

            result, discovery_state = self._get_span_attribute_inventory(project_id)
            return self._eval_attribute_list_response(result, discovery_state)

        except Exception as e:
            logger.exception(f"error fetching span attributes list: {str(e)}")
            return self._gm.bad_request("Unable to fetch span attributes")

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
            if not self._attribute_project_for_request(request, project_id):
                return self._gm.not_found("Project not found")

            span_attribute_keys, discovery_state = self._get_span_attribute_inventory(
                project_id
            )

            if row_type == "spans" or row_type == "voiceCalls":
                # voiceCalls share the spans surface for the picker; they
                # have their own evaluator pipeline upstream of EvalTask.
                return self._eval_attribute_list_response(
                    self._merge_saved_mapping_paths(
                        project_id, row_type, span_attribute_keys
                    ),
                    discovery_state,
                )

            (
                max_spans,
                max_traces,
                cardinality_state,
            ) = self._observed_mapping_cardinality_with_status(project_id)

            if row_type == "traces":
                paths = self._build_trace_attribute_paths(
                    project_id,
                    span_attribute_keys,
                    max_spans=max_spans,
                )
                return self._eval_attribute_list_response(
                    self._merge_saved_mapping_paths(project_id, row_type, paths),
                    discovery_state,
                    cardinality_state,
                )

            if row_type == "sessions":
                paths = self._build_session_attribute_paths(
                    project_id,
                    span_attribute_keys,
                    max_traces=max_traces,
                    max_spans=max_spans,
                )
                return self._eval_attribute_list_response(
                    self._merge_saved_mapping_paths(project_id, row_type, paths),
                    discovery_state,
                    cardinality_state,
                )

            return self._gm.bad_request(
                f"Unknown row_type {row_type!r}. Expected one of: "
                "spans, traces, sessions, voiceCalls."
            )

        except Exception as e:
            logger.exception(f"error fetching eval attributes list: {str(e)}")
            return self._gm.bad_request("Unable to fetch evaluation attributes")

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
    _OBSERVED_MAX_INPUT_ROWS = 10_000
    _OBSERVED_MAX_WINDOW_DAYS = 30
    _MAX_SPAN_PATH_POSITIONS = 50
    _MAX_TRACE_PATH_POSITIONS = 20
    _MAX_SAVED_MAPPING_CONFIGS = 1000
    _MAX_SAVED_MAPPING_PATHS = 2000
    _MAX_MAPPING_PATH_LENGTH = 512

    @staticmethod
    def _attribute_query_state(
        *,
        query_status: str,
        query_error_code: str | None = None,
        query_sampled: bool = False,
    ) -> dict:
        state = {
            "query_complete": query_status == "complete",
            "query_status": query_status,
            "query_sampled": query_sampled,
        }
        if query_error_code:
            state["query_error_code"] = query_error_code
        return state

    @classmethod
    def _merge_attribute_query_states(cls, *states: dict) -> dict:
        """Collapse discovery/cardinality status without hiding either bound."""
        query_sampled = any(state.get("query_sampled", False) for state in states)
        degraded = [
            state for state in states if state.get("query_status") == "degraded"
        ]
        if degraded:
            error_codes = {state.get("query_error_code") for state in degraded if state}
            return cls._attribute_query_state(
                query_status="degraded",
                query_error_code=(
                    "query_failed"
                    if "query_failed" in error_codes
                    else "read_budget_exceeded"
                ),
                query_sampled=query_sampled,
            )
        if any(state.get("query_status") == "sampled" for state in states):
            return cls._attribute_query_state(
                query_status="sampled",
                query_error_code="sample_limit",
                query_sampled=True,
            )
        return cls._attribute_query_state(query_status="complete")

    def _eval_attribute_list_response(self, paths: list[str], *states: dict):
        """Preserve the legacy result array and add an honest query contract."""
        response = self._gm.success_response(paths)
        response.data.update(self._merge_attribute_query_states(*states))
        return response

    @staticmethod
    def _attribute_project_for_request(request, project_id: str):
        """Tenant gate an attribute request before any ClickHouse telemetry read."""
        project_manager = getattr(Project, "no_workspace_objects", Project.objects)
        return (
            project_manager.filter(
                _project_workspace_scope_q(request, project_prefix=""),
                id=project_id,
                organization=_get_request_organization(request),
                deleted=False,
            )
            .only("id")
            .first()
        )

    def _merge_saved_mapping_paths(
        self, project_id: str, row_type: str, discovered_paths: list[str]
    ) -> list[str]:
        """Preserve valid saved mappings when bounded CH discovery misses a rare key.

        Attribute discovery deliberately samples a bounded recent prefix so it
        remains sub-second on high-volume projects. That inventory is not
        complete. Project-scoped eval mappings are small PostgreSQL config data,
        so unioning their already-selected paths prevents an existing mapping
        from disappearing merely because its source key fell outside the CH
        sample. The query and response are both capped.
        """
        mappings = CustomEvalConfig.objects.filter(
            project_id=project_id,
            deleted=False,
        ).values_list("mapping", flat=True)[: self._MAX_SAVED_MAPPING_CONFIGS]

        saved_paths: set[str] = set()
        for mapping in mappings:
            if not isinstance(mapping, dict):
                continue
            for path in mapping.values():
                if (
                    not isinstance(path, str)
                    or not path
                    or len(path) > self._MAX_MAPPING_PATH_LENGTH
                ):
                    continue
                if row_type in ("spans", "voiceCalls"):
                    is_valid_shape = not path.startswith(("spans.", "traces."))
                elif row_type == "traces":
                    is_valid_shape = (
                        path in self._TRACE_PUBLIC_FIELDS or path.startswith("spans.")
                    )
                else:
                    is_valid_shape = (
                        path in self._SESSION_PUBLIC_FIELDS
                        or path.startswith("traces.")
                    )
                if is_valid_shape:
                    saved_paths.add(path)
                if len(saved_paths) >= self._MAX_SAVED_MAPPING_PATHS:
                    break
            if len(saved_paths) >= self._MAX_SAVED_MAPPING_PATHS:
                break

        result = list(dict.fromkeys(discovered_paths))
        seen = set(result)
        result.extend(path for path in sorted(saved_paths) if path not in seen)
        return result

    def _get_span_attribute_inventory(self, project_id: str) -> tuple[list, dict]:
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
        try:
            discovered = analytics.get_span_attribute_keys_ch(str(project_id))
            discovery_state = self._attribute_query_state(
                query_status=getattr(discovered, "query_status", "sampled"),
                query_error_code=getattr(
                    discovered, "query_error_code", "sample_limit"
                ),
                query_sampled=getattr(discovered, "query_sampled", True),
            )
        except Exception as exc:
            logger.warning(
                "eval attribute inventory discovery failed",
                project_id=str(project_id),
                error_type=type(exc).__name__,
            )
            discovered = []
            discovery_state = self._attribute_query_state(
                query_status="degraded",
                query_error_code=(
                    "read_budget_exceeded"
                    if is_read_budget_error(exc)
                    else "query_failed"
                ),
            )
        raw = merge_guaranteed_span_attribute_keys(discovered)

        keys = []
        for item in raw or []:
            if isinstance(item, dict):
                k = item.get("key")
                if k:
                    keys.append(k)
            elif isinstance(item, str) and item:
                keys.append(item)
        return keys, discovery_state

    def _get_span_attribute_keys(self, project_id: str) -> list:
        """Backward-compatible list-only facade for non-picker callers."""
        keys, _query_state = self._get_span_attribute_inventory(project_id)
        return keys

    def _observed_mapping_cardinality_with_status(
        self, project_id: str
    ) -> tuple[int, int, dict]:
        """Return sampled ``(spans/trace, traces/session)`` from ClickHouse.

        Span and trace telemetry is direct-to-ClickHouse, so the variable picker
        must not size nested paths from empty PostgreSQL telemetry tables. One
        recent, bounded aggregate computes both dimensions and is cached because
        trace/session picker requests commonly arrive together.
        """
        cache_key = f"eval-mapping-cardinality:v3:{project_id}"
        try:
            cached = django_cache.get(cache_key)
        except Exception:
            cached = None
        if cached is not None:
            return (
                int(cached[0]),
                int(cached[1]),
                self._attribute_query_state(
                    query_status="sampled",
                    query_error_code="sample_limit",
                    query_sampled=True,
                ),
            )

        query = """
            SELECT
                max(span_count) AS max_spans_per_trace,
                max(
                    if(
                        isNull(session_id)
                        OR toString(session_id) = ''
                        OR toString(session_id) =
                           '00000000-0000-0000-0000-000000000000',
                        0,
                        session_trace_count
                    )
                ) AS max_traces_per_session
            FROM
            (
                SELECT
                    span_count,
                    session_id,
                    count() OVER (PARTITION BY session_id)
                        AS session_trace_count
                FROM
                (
                    SELECT
                        trace_id,
                        any(trace_session_id) AS session_id,
                        uniqExact(id) AS span_count,
                        max(start_time) AS last_seen
                    FROM
                    (
                        SELECT id, trace_id, trace_session_id, start_time
                        FROM spans
                        PREWHERE project_id = %(project_id)s
                        WHERE is_deleted = 0
                          AND start_time >=
                              now() - toIntervalDay(%(window_days)s)
                        LIMIT %(input_sample_rows)s
                    )
                    GROUP BY trace_id
                    ORDER BY last_seen DESC
                    LIMIT %(sample_size)s
                )
            )
        """
        try:
            result = AnalyticsQueryService().execute_ch_query(
                query,
                {
                    "project_id": str(project_id),
                    "window_days": self._OBSERVED_MAX_WINDOW_DAYS,
                    "input_sample_rows": self._OBSERVED_MAX_INPUT_ROWS,
                    "sample_size": self._OBSERVED_MAX_SAMPLE_SIZE,
                },
                timeout_ms=750,
                settings=_BOUNDED_ANALYTICS_SETTINGS,
            )
            row = result.data[0] if result.data else {}
            max_spans = min(
                max(int(row.get("max_spans_per_trace") or 0), 1),
                self._MAX_SPAN_PATH_POSITIONS,
            )
            max_traces = min(
                max(int(row.get("max_traces_per_session") or 0), 1),
                self._MAX_TRACE_PATH_POSITIONS,
            )
        except Exception as exc:
            logger.warning(
                "eval mapping cardinality exceeded ClickHouse read budget",
                project_id=str(project_id),
                error=str(exc)[:200],
            )
            # Always expose the first nested slot even while analytics is
            # degraded so a saved ``spans.0.*`` mapping remains selectable.
            return (
                1,
                1,
                self._attribute_query_state(
                    query_status="degraded",
                    query_error_code=(
                        "read_budget_exceeded"
                        if is_read_budget_error(exc)
                        else "query_failed"
                    ),
                ),
            )

        try:
            django_cache.set(cache_key, (max_spans, max_traces), timeout=60)
        except Exception:
            pass
        return (
            max_spans,
            max_traces,
            self._attribute_query_state(
                query_status="sampled",
                query_error_code="sample_limit",
                query_sampled=True,
            ),
        )

    def _observed_mapping_cardinality(self, project_id: str) -> tuple[int, int]:
        """Backward-compatible cardinality-only facade."""
        max_spans, max_traces, _query_state = (
            self._observed_mapping_cardinality_with_status(project_id)
        )
        return max_spans, max_traces

    def _max_spans_per_trace(self, project_id: str) -> int:
        """Max sampled CH span count used to size ``spans.<n>`` paths."""
        return self._observed_mapping_cardinality(project_id)[0]

    def _max_traces_per_session(self, project_id: str) -> int:
        """Max sampled CH trace count used to size ``traces.<n>`` paths."""
        return self._observed_mapping_cardinality(project_id)[1]

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
        self,
        project_id: str,
        span_attribute_keys: list,
        *,
        max_spans: int | None = None,
    ) -> list:
        """Trace-level paths: trace fields + ``spans.<n>.<key>`` for each
        index up to the observed max spans-per-trace."""
        paths = list(self._TRACE_PUBLIC_FIELDS)
        if max_spans is None:
            max_spans = self._max_spans_per_trace(project_id)
        for i in range(max_spans):
            for field in self._SPAN_PUBLIC_FIELDS:
                paths.append(f"spans.{i}.{field}")
            for key in span_attribute_keys:
                paths.append(f"spans.{i}.{key}")
        return paths

    def _build_session_attribute_paths(
        self,
        project_id: str,
        span_attribute_keys: list,
        *,
        max_traces: int | None = None,
        max_spans: int | None = None,
    ) -> list:
        """Session-level paths: session fields + ``traces.<i>.<trace_field>``
        + ``traces.<i>.spans.<j>.<key>`` up to the observed max traces-per-
        session and spans-per-trace."""
        paths = list(self._SESSION_PUBLIC_FIELDS)
        if max_traces is None:
            max_traces = self._max_traces_per_session(project_id)
        if max_spans is None:
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

        except Exception as exc:
            logger.exception(
                "evaluation details read failed",
                error_type=type(exc).__name__,
            )
            response = self._gm.bad_request(
                "Evaluation details could not be loaded. Please try again."
            )
            response.data["code"] = (
                "read_budget_exceeded" if is_read_budget_error(exc) else "query_failed"
            )
            return response

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
                        tracer_project_id_for_source,
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
                    tracer_project_id = tracer_project_id_for_source(
                        "observation_span", observation_span
                    )
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
                            **(
                                {"tracer_project_id": tracer_project_id}
                                if tracer_project_id
                                else {}
                            ),
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
            response = self._gm.bad_request(
                "Span navigation could not be completed. Please try again."
            )
            response.data["code"] = (
                "read_budget_exceeded" if is_read_budget_error(e) else "query_failed"
            )
            return response

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
            response = self._gm.bad_request(
                "Span navigation could not be completed. Please try again."
            )
            response.data["code"] = (
                "read_budget_exceeded" if is_read_budget_error(e) else "query_failed"
            )
            return response


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
