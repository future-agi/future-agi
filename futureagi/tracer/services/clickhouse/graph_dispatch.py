from typing import Any, Dict, List

from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels

from tracer.services.clickhouse.query_builders import (
    AnnotationGraphQueryBuilder,
    EvalMetricsQueryBuilder,
    TimeSeriesQueryBuilder,
)
from tracer.utils.helper import get_annotation_labels_for_project


def format_system_metric_graph(
    ch_data: Dict[str, List[Dict[str, Any]]], metric_id: str
) -> Dict[str, Any]:
    metric_key = metric_id if metric_id in ch_data else "latency"
    metric_points = ch_data.get(metric_key, [])
    traffic_points = ch_data.get("traffic", [])
    traffic_by_ts = {
        t.get("timestamp"): t.get("traffic", 0) for t in traffic_points
    }
    return {
        "metric_name": metric_id,
        "data": [
            {
                "timestamp": p.get("timestamp"),
                "value": p.get("value", 0),
                "primary_traffic": traffic_by_ts.get(p.get("timestamp"), 0),
            }
            for p in metric_points
        ],
    }


def annotation_output_type(label: AnnotationsLabels, requested: str = None) -> str:
    if requested:
        return requested
    if label.type in (
        AnnotationTypeChoices.NUMERIC.value,
        AnnotationTypeChoices.STAR.value,
    ):
        return "float"
    if label.type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
        return "bool"
    if label.type == AnnotationTypeChoices.CATEGORICAL.value:
        return "str_list"
    return "text"


def fetch_system_metric_graph_ch(
    *,
    analytics,
    project_id: str,
    filters: List[Dict[str, Any]],
    interval: str,
    metric_id: str,
    timeout_ms: int = 30000,
) -> Dict[str, Any]:
    builder = TimeSeriesQueryBuilder(
        project_id=str(project_id),
        filters=filters,
        interval=interval,
    )
    query, params = builder.build()
    result = analytics.execute_ch_query(query, params, timeout_ms=timeout_ms)
    ch_data = builder.format_result(result.data, result.columns or [])
    return format_system_metric_graph(ch_data, metric_id)


def fetch_eval_graph_ch(
    *,
    analytics,
    project_id: str,
    filters: List[Dict[str, Any]],
    interval: str,
    req_data_config: Dict[str, Any],
    timeout_ms: int = 30000,
) -> Dict[str, Any]:
    builder = EvalMetricsQueryBuilder(
        project_id=str(project_id),
        custom_eval_config_id=str(req_data_config.get("id")),
        filters=filters,
        interval=interval,
        eval_output_type=req_data_config.get("eval_output_type", "SCORE"),
        choices=req_data_config.get("choices", []),
    )
    query, params = builder.build()
    result = analytics.execute_ch_query(query, params, timeout_ms=timeout_ms)
    return builder.format_result(result.data, result.columns or [])


def fetch_annotation_graph_ch(
    *,
    analytics,
    project_id: str,
    filters: List[Dict[str, Any]],
    interval: str,
    req_data_config: Dict[str, Any],
    observe_type: str,
    timeout_ms: int = 30000,
) -> Dict[str, Any]:
    label_id = req_data_config.get("id")
    if not label_id:
        raise ValueError("Annotation label ID is required")
    # Annotation labels can be project-local or org/shared labels that are
    # only connected to a project through Score rows. Use the same score-backed
    # lookup as list/filter config so graph metrics work for rendered labels.
    label = get_annotation_labels_for_project(project_id).get(id=label_id)
    builder = AnnotationGraphQueryBuilder(
        project_id=str(project_id),
        annotation_label_id=str(label_id),
        annotation_name=label.name,
        filters=filters,
        interval=interval,
        output_type=annotation_output_type(label, req_data_config.get("output_type")),
        value=req_data_config.get("value"),
        observe_type=observe_type,
    )
    query, params = builder.build()
    result = analytics.execute_ch_query(query, params, timeout_ms=timeout_ms)
    return builder.format_result(result.data, result.columns or [])
