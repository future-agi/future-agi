"""Annotation summary aggregation reading from the unified Score model.

Replaces ``SQLQueryHandler.get_annotation_summary_stats`` which read from the
legacy ``model_hub_annotations`` + ``Cell.feedback_info['annotation']`` tables.
After the unified-Score migration, those legacy tables only hold un-migrated
test data; real annotations live on ``Score`` (``source_type='dataset_row'``).

Returns the same set of DataFrames the legacy SQL produced so the pandas
aggregation logic in ``AnnotationSummaryView`` (Pearson correlation,
Fleiss kappa, cosine similarity, etc.) stays untouched.

Returned dict keys:
- ``header_data``: per-label aggregates
- ``metric_calc``: long-form ``(label_id, row_id, user_id, value)`` for
  inter-annotator agreement math
- ``graph``: numeric histograms per label
- ``heatmap``: numeric histograms per ``(label, user)``
- ``annotator_performance``: per-user ``(user_id, name, avg_time, annotations)``
- ``dataset_annot_summary``: dataset-wide ``(not_deleted_rows, fully_annotated_rows)``
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Dict, List, Optional

import pandas as pd
import structlog

from model_hub.models.develop_dataset import Row
from model_hub.models.score import Score

logger = structlog.get_logger(__name__)


# Label types the summary view surfaces. STAR is folded into NUMERIC for stats;
# THUMBS_UP_DOWN is intentionally skipped (legacy summary didn't render it).
NUMERIC_LIKE = ("numeric", "star")
SUPPORTED_TYPES = ("numeric", "star", "categorical", "text")

NUMERIC_BUCKETS = 8


def _scalar_value(value: Any, label_type: str) -> Optional[str]:
    """Extract a string scalar from Score.value JSON, mirroring legacy `cell.value`."""
    if value is None:
        return None
    if not isinstance(value, dict):
        return str(value)
    if label_type == "numeric":
        v = value.get("value")
        return None if v is None else str(v)
    if label_type == "star":
        v = value.get("rating")
        return None if v is None else str(v)
    if label_type == "categorical":
        sel = value.get("selected") or []
        if not isinstance(sel, list):
            sel = [sel]
        # Legacy consumer parses with ``ast.literal_eval``; emit a Python list literal.
        return repr([str(s) for s in sel])
    if label_type == "text":
        return value.get("text")
    return None


def _numeric_min_max(label_type: str, settings: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    """Return (min, max) for numeric histogram bucketing. Star uses 1..no_of_stars."""
    if label_type == "numeric":
        try:
            return float(settings.get("min")), float(settings.get("max"))
        except (TypeError, ValueError):
            return None, None
    if label_type == "star":
        try:
            return 1.0, float(settings.get("no_of_stars", 5))
        except (TypeError, ValueError):
            return None, None
    return None, None


def _header_min_max_strings(label_type: str, settings: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Header_data uses string min/max, with min_length/max_length for text."""
    if label_type == "numeric":
        return _str_or_none(settings.get("min")), _str_or_none(settings.get("max"))
    if label_type == "star":
        return "1", _str_or_none(settings.get("no_of_stars", 5))
    if label_type == "text":
        return _str_or_none(settings.get("min_length")), _str_or_none(settings.get("max_length"))
    return None, None


def _str_or_none(v: Any) -> Optional[str]:
    return None if v is None else str(v)


def _categorical_options(settings: Dict[str, Any]) -> List[str]:
    return [
        opt.get("label")
        for opt in (settings.get("options") or [])
        if opt.get("label") is not None
    ]


def _bucket_edges(min_val: float, max_val: float) -> List[tuple]:
    """Return list of ``(bucket_index, bucket_min, bucket_max)`` tuples."""
    if max_val <= min_val:
        return [(1, round(min_val, 2), round(max_val, 2))]
    step = (max_val - min_val) / NUMERIC_BUCKETS
    return [
        (
            i,
            round(min_val + (i - 1) * step, 2),
            round(min_val + i * step, 2),
        )
        for i in range(1, NUMERIC_BUCKETS + 1)
    ]


def get_annotation_summary_data(dataset_id, organization_id=None) -> Dict[str, pd.DataFrame]:
    """Build the 6 DataFrames the AnnotationSummaryView expects, from Score.

    The caller (the view) has already authorized the dataset belongs to
    ``organization_id``; we re-apply the org filter here as defense-in-depth so
    a malformed Score row can't leak across orgs through this query.
    """
    base_filter = dict(
        dataset_row__dataset_id=dataset_id,
        deleted=False,
        label__deleted=False,
        # Score has 6 source FKs; explicitly require source_type='dataset_row'
        # so a row with a stale dataset_row FK but different source_type can't
        # contaminate the result.
        source_type="dataset_row",
    )
    if organization_id is not None:
        base_filter["organization_id"] = organization_id

    scores = Score.objects.filter(**base_filter).select_related(
        "label", "annotator", "dataset_row"
    )

    rows: List[Dict[str, Any]] = []
    # Per-label metadata, keyed by label_id (str).
    label_meta: Dict[str, Dict[str, Any]] = {}
    annotator_names: Dict[str, str] = {}

    for s in scores.iterator():
        if not s.label or s.label.type not in SUPPORTED_TYPES:
            continue
        scalar = _scalar_value(s.value, s.label.type)
        if scalar is None:
            continue
        label_id = str(s.label.id)
        # Treat star as numeric for summary aggregation downstream.
        summary_type = "numeric" if s.label.type in NUMERIC_LIKE else s.label.type
        label_meta.setdefault(
            label_id,
            {
                "name": s.label.name,
                "type": s.label.type,           # original
                "summary_type": summary_type,    # folded
                "settings": s.label.settings or {},
            },
        )

        annotator_id = str(s.annotator_id) if s.annotator_id else None
        if annotator_id:
            annotator_names[annotator_id] = (
                s.annotator.name if s.annotator else "Unknown"
            )

        rows.append(
            {
                "label_id": label_id,
                "row_id": str(s.dataset_row_id) if s.dataset_row_id else None,
                "user_id": annotator_id,
                "value": scalar,
            }
        )

    return {
        "header_data": _header_data(rows, label_meta),
        "metric_calc": _metric_calc(rows),
        "graph": _graph(rows, label_meta),
        "heatmap": _heatmap(rows, label_meta),
        "annotator_performance": _annotator_performance(rows, annotator_names),
        "dataset_annot_summary": _dataset_annot_summary(rows, dataset_id),
    }


def _header_data(rows, label_meta) -> pd.DataFrame:
    """Per-label aggregates. Columns match the legacy SQL header_data shape."""
    columns = [
        "label_id",
        "label_name",
        "type",
        "count_records",
        "sum_value",
        "avg_value",
        "avg_time_taken",
        "mode_value",
        "stddev_value",
        "label_coverage",
        "cat_label_counts",
        "min_value",
        "max_value",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(rows)
    out = []
    for label_id, group in df.groupby("label_id"):
        meta = label_meta[label_id]
        original_type = meta["type"]
        summary_type = meta["summary_type"]
        settings = meta["settings"]

        numeric_clean = pd.to_numeric(group["value"], errors="coerce").dropna()

        cat_counts: Dict[str, int] = {}
        if summary_type == "categorical":
            import ast as _ast

            options = _categorical_options(settings)
            counts: Dict[str, int] = defaultdict(int)
            for raw in group["value"]:
                if not raw:
                    continue
                # ``raw`` was emitted as ``repr([str, ...])`` in
                # ``_scalar_value``; parse it back to a list and exact-match
                # against options. Substring matching shadowed options where
                # one option name was a prefix/substring of another (e.g.
                # ``"A"`` matched on every value containing ``"AA"``).
                try:
                    selected = _ast.literal_eval(raw)
                    if not isinstance(selected, list):
                        selected = [selected]
                except (ValueError, SyntaxError):
                    selected = []
                selected_norm = {str(s) for s in selected}
                for opt in options:
                    if opt is not None and str(opt) in selected_norm:
                        counts[opt] += 1
            for opt in options:
                cat_counts.setdefault(opt, counts.get(opt, 0))

        min_str, max_str = _header_min_max_strings(original_type, settings)

        out.append(
            {
                "label_id": label_id,
                "label_name": meta["name"],
                "type": summary_type,
                "count_records": int(len(group)),
                "sum_value": float(numeric_clean.sum()) if not numeric_clean.empty else None,
                "avg_value": float(numeric_clean.mean()) if not numeric_clean.empty else None,
                # Score doesn't track per-annotation time spent — leave NaN so
                # the consumer's ``replace(0, nan).dropna()`` filter excludes
                # it from ETA math.
                "avg_time_taken": float("nan"),
                "mode_value": (
                    float(numeric_clean.mode().iloc[0])
                    if not numeric_clean.empty and not numeric_clean.mode().empty
                    else None
                ),
                "stddev_value": (
                    round(float(numeric_clean.std()), 2)
                    if numeric_clean.size > 1
                    else None
                ),
                # All Score rows are present-by-construction; legacy computed
                # coverage at cell level which doesn't translate.
                "label_coverage": 100.0,
                "cat_label_counts": json.dumps(cat_counts) if cat_counts else json.dumps({}),
                "min_value": min_str,
                "max_value": max_str,
            }
        )
    return pd.DataFrame(out, columns=columns)


def _metric_calc(rows) -> pd.DataFrame:
    """Long-form rows for downstream Pearson / Fleiss / cosine math."""
    if not rows:
        return pd.DataFrame(columns=["label_id", "row_id", "user_id", "value"])
    return pd.DataFrame(rows)[["label_id", "row_id", "user_id", "value"]]


def _graph(rows, label_meta) -> pd.DataFrame:
    """Numeric histogram per label — bucketed into ``NUMERIC_BUCKETS`` bins."""
    columns = ["label_id", "bucket", "bucket_min", "bucket_max", "count"]
    if not rows:
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(rows)
    out = []
    for label_id, group in df.groupby("label_id"):
        meta = label_meta[label_id]
        if meta["summary_type"] != "numeric":
            continue
        min_val, max_val = _numeric_min_max(meta["type"], meta["settings"])
        if min_val is None or max_val is None or max_val <= min_val:
            continue

        numeric = pd.to_numeric(group["value"], errors="coerce").dropna()
        edges = _bucket_edges(min_val, max_val)
        last_bucket_idx = edges[-1][0] if edges else None
        for bucket, bmin, bmax in edges:
            # Last bucket is [bmin, bmax]; earlier buckets are [bmin, bmax).
            # Without this, the maximum value (e.g. star rating 5/5) falls out
            # of every bucket and disappears from the histogram.
            if bucket == last_bucket_idx:
                count = int(((numeric >= bmin) & (numeric <= bmax)).sum())
            else:
                count = int(((numeric >= bmin) & (numeric < bmax)).sum())
            out.append(
                {
                    "label_id": label_id,
                    "bucket": bucket,
                    "bucket_min": bmin,
                    "bucket_max": bmax,
                    "count": count,
                }
            )
    return pd.DataFrame(out, columns=columns)


def _heatmap(rows, label_meta) -> pd.DataFrame:
    """Per-(label, user) numeric histograms."""
    columns = ["label_id", "user_id", "bucket", "bucket_min", "bucket_max", "count"]
    if not rows:
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(rows)
    df = df[df["user_id"].notna()]
    out = []
    for label_id, group in df.groupby("label_id"):
        meta = label_meta[label_id]
        if meta["summary_type"] != "numeric":
            continue
        min_val, max_val = _numeric_min_max(meta["type"], meta["settings"])
        if min_val is None or max_val is None or max_val <= min_val:
            continue

        edges = _bucket_edges(min_val, max_val)
        last_bucket_idx = edges[-1][0] if edges else None
        for user_id, user_group in group.groupby("user_id"):
            numeric = pd.to_numeric(user_group["value"], errors="coerce").dropna()
            for bucket, bmin, bmax in edges:
                # Last bucket is inclusive on the upper bound — see _graph().
                if bucket == last_bucket_idx:
                    count = int(((numeric >= bmin) & (numeric <= bmax)).sum())
                else:
                    count = int(((numeric >= bmin) & (numeric < bmax)).sum())
                out.append(
                    {
                        "label_id": label_id,
                        "user_id": user_id,
                        "bucket": bucket,
                        "bucket_min": bmin,
                        "bucket_max": bmax,
                        "count": count,
                    }
                )
    return pd.DataFrame(out, columns=columns)


def _annotator_performance(rows, annotator_names) -> pd.DataFrame:
    """Per-annotator stats. Score doesn't store time spent → ``avg_time`` is NaN."""
    columns = ["user_id", "name", "avg_time", "annotations"]
    if not rows:
        return pd.DataFrame(columns=columns)
    df = pd.DataFrame(rows)
    df = df[df["user_id"].notna()]
    if df.empty:
        return pd.DataFrame(columns=columns)
    grouped = df.groupby("user_id").size().reset_index(name="annotations")
    grouped["name"] = grouped["user_id"].map(lambda u: annotator_names.get(u, "Unknown"))
    grouped["avg_time"] = float("nan")
    return grouped[columns]


def _dataset_annot_summary(rows, dataset_id) -> pd.DataFrame:
    """Single-row DataFrame with ``not_deleted_rows`` and ``fully_annotated_rows``.

    Adapted from legacy semantics: ``num_required`` becomes the count of
    distinct labels used anywhere in the dataset's Score rows; a row is
    "fully annotated" when it has a Score for every such label.
    """
    not_deleted = Row.objects.filter(dataset_id=dataset_id, deleted=False).count()

    if not rows:
        return pd.DataFrame(
            [{"not_deleted_rows": not_deleted, "fully_annotated_rows": 0}]
        )

    df = pd.DataFrame(rows)
    df = df[df["row_id"].notna()]
    if df.empty:
        return pd.DataFrame(
            [{"not_deleted_rows": not_deleted, "fully_annotated_rows": 0}]
        )

    distinct_labels_in_use = df["label_id"].nunique()
    if distinct_labels_in_use == 0:
        fully = 0
    else:
        per_row_label_count = df.groupby("row_id")["label_id"].nunique()
        fully = int((per_row_label_count == distinct_labels_in_use).sum())

    return pd.DataFrame(
        [{"not_deleted_rows": not_deleted, "fully_annotated_rows": fully}]
    )
