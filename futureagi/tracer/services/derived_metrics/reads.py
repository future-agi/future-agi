"""Committed metric reads. Never read a draft, raw source, or frozen run as a KPI.

A request owns one definition/manifest pin across current, previous and evidence
queries. Scalar reads project only moments/support from the bucket checkpoint
and bounded delta chain. Replacement collapse precedes deletion predicates.
"""

import json
import math
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from django.conf import settings
from django.core import signing
from django.db.models import Q
from django.utils import timezone
from django.utils.module_loading import import_string

from tracer.models.derived_metric import DerivedMetricVersion, MetricRefreshAttempt
from tracer.services.derived_metrics.bounded_reads import KeyRange, committed_branches
from tracer.services.derived_metrics.errors import MetricInputError
from tracer.services.derived_metrics.feature_flag import metrics_enabled
from tracer.services.derived_metrics.parts import (
    SCALAR_PART_COLUMNS,
    combine_parts_select,
)
from tracer.services.derived_metrics.publication import (
    ClickHousePublicationStore,
    committed_rows_select,
    pinned_read,
)
from tracer.services.derived_metrics.queries import QUERY_SETTINGS, UNCERTAINTY_COLUMNS
from tracer.services.derived_metrics.retention import retention_cutoff
from tracer.services.derived_metrics.service import MetricUnavailableError, shape_result
from tracer.services.derived_metrics.spec import require_computable
from tracer.services.derived_metrics.storage import BUCKET_TOTALS, FIELD_VALUES
from tracer.services.derived_metrics.unit_reads import field_recipe

QUARTER = timedelta(minutes=15)
NOUNS = {
    "conversation": "conversations",
    "span": "spans",
    "trace": "traces",
    "user": "users",
}
CONTEXTS = {"conversation": "sessions", "span": "spans", "trace": "traces"}
CURSOR_SALT = "metric-drivers-v1"


def effective_window(start, end):
    """Floor both instants in UTC, never bucket in the caller's offset zone."""
    if start.tzinfo is None or end.tzinfo is None:
        raise MetricInputError({"window": "Use timezone-aware instants."})
    if not start < end or end - start > timedelta(days=90) or end > timezone.now():
        raise MetricInputError({"window": "Use a closed window of at most 90 days."})

    def floor(value):
        value = value.astimezone(UTC)
        return value.replace(minute=value.minute // 15 * 15, second=0, microsecond=0)

    start, end = floor(start), floor(end)
    if start == end:
        raise MetricInputError(
            {"window": "Choose a window spanning at least one 15-minute bucket."}
        )
    return {
        "window_start": start,
        "window_end": end,
        "rounding": "Rounded to 15 minutes",
    }


def require_materialized(definition):
    require_computable(definition)
    if definition.level == "user":
        raise MetricUnavailableError(
            "user_reads_unavailable",
            "User metrics are on demand; the user-level reader is not available.",
            value_mode="on_demand",
        )


class MetricReadStore(ClickHousePublicationStore):
    """Bound result bytes/rows as well as work; overflow is never partial data."""

    def execute(self, sql, params=None, **kwargs):
        return self.client.execute(
            sql,
            params or {},
            settings={
                **QUERY_SETTINGS,
                "max_result_rows": 4590,
                "max_result_bytes": 16 * 1024 * 1024,
                "use_query_cache": 0,
                # The scalar compiler uses legacy-compatible SQL. Avoid repeated
                # analyzer expansion of its nested support/interval expressions.
                "enable_analyzer": 0,
                # All source pruning is explicit on immutable keys; recursively
                # pushing predicates through the statistical expression tree adds
                # planning work without reducing its already compacted input.
                "enable_optimize_predicate_expression": 0,
                "optimize_move_to_prewhere": 0,
            },
            **kwargs,
        )


@contextmanager
def read_store():
    """Dedicated local connection, using the same shard as publication."""
    from clickhouse_driver import Client

    from tracer.services.clickhouse.v2 import get_v2_config

    config = get_v2_config()
    client = Client(
        host=config["host"],
        port=config["tcp_port"],
        user=config["user"],
        password=config["password"],
        database=config["database"],
        connect_timeout=10,
        send_receive_timeout=60,
    )
    try:
        yield MetricReadStore(client)
    finally:
        client.disconnect()


def _check_metric(metric):
    if not metrics_enabled(org=metric.organization_id, project=metric.project_id):
        raise MetricUnavailableError("metrics_disabled", "Metrics are not enabled.")
    project = metric.project
    if (
        metric.deleted
        or project.deleted
        or metric.organization_id != project.organization_id
        or metric.workspace_id != project.workspace_id
    ):
        raise MetricUnavailableError(
            message="Metric project scope is no longer available."
        )


@contextmanager
def metric_read(metric, *, store=None):
    _check_metric(metric)
    if metric.active_version is None:
        raise MetricUnavailableError(
            "metric_recalculating", "No completed active definition is available yet."
        )
    require_materialized(metric.active_version)
    with nullcontext(store) if store is not None else read_store() as connection:
        with pinned_read(connection, metric.project_id, metric_id=metric.id) as (
            manifest,
            definition,
        ):
            if definition is None or definition.metric_id != metric.id:
                raise MetricUnavailableError(
                    message="The committed definition is unavailable."
                )
            require_materialized(definition)
            yield connection, manifest, definition


def provenance(manifest, definition):
    return {
        "manifest_id": manifest.id,
        "generation": manifest.generation,
        "version_id": definition.id,
        "version": definition.version,
        "definition_hash": definition.definition_hash,
        "updated_at": manifest.published_by.committed_at,
        "backfill_days_done": definition.backfill_days_done,
        "backfill_days_total": definition.backfill_days_total,
    }


def _rows(store, sql, params):
    # Injection of a writer-owned connection never relaxes the read budgets.
    reader = (
        store if isinstance(store, MetricReadStore) else MetricReadStore(store.client)
    )
    rows, columns = reader.execute(sql, params, with_column_types=True)
    result = []
    for values in rows:
        row = dict(zip((c[0] for c in columns), values, strict=True))
        if "ci" in row:
            row.update(zip(UNCERTAINTY_COLUMNS, row.pop("ci"), strict=True))
        result.append(row)
    return result


def bucket_select(metric, definition, manifest, window):
    """Only immutable keys prune branches; tombstones win before being removed."""
    sketches = definition.kind in {"unique_count", "percentile"}
    columns = (
        *BUCKET_TOTALS.logical_key,
        "generation",
        "version",
        "is_deleted",
        "workspace_id",
        *SCALAR_PART_COLUMNS,
    )
    if sketches:
        columns += ("uniq_state", "quantile_state")
    branches, params = committed_branches(
        BUCKET_TOTALS,
        manifest,
        org_id=metric.organization_id,
        project_id=metric.project_id,
        key_in={"metric_id": (metric.id,), "metric_version": (definition.version,)},
        key_relation=KeyRange(
            ("bucket_start",),
            (window["window_start"] - QUARTER,),
            (window["window_end"] - QUARTER,),
            prefix="read_range",
        ),
        columns=tuple(dict.fromkeys(columns)),
        param_prefix="read_bucket",
    )
    if not branches:
        raise MetricUnavailableError(
            message="No committed bucket checkpoint is available."
        )
    params["read_workspace"] = str(metric.workspace_id)
    return (
        "SELECT * FROM (SELECT * FROM ("
        + " UNION ALL ".join(branches)
        + ") ORDER BY generation DESC, version DESC LIMIT 1 BY "
        + ", ".join(BUCKET_TOTALS.logical_key)
        + ") WHERE is_deleted = 0 AND workspace_id = toUUID(%(read_workspace)s)",
        params,
    )


def _aggregate_select(sql, definition, params, *, grouped=False, per_period=False):
    recipe = field_recipe(definition)
    if definition.kind not in {"unique_count", "percentile"}:
        params.update(recipe.formula.parameters)
        return combine_parts_select(
            sql,
            recipe.formula,
            rollup="cohort" if grouped else "overall",
            per_period=per_period,
            packed_uncertainty=True,
        )
    # Sketches are only meaningful for census inputs; never infer an IPW sketch.
    params["read_percentile"] = recipe.source.field_spec.get("percentile", 0.95)
    aggregate = (
        "uniqCombined64Merge(uniq_state)"
        if definition.kind == "unique_count"
        else "quantileTDigestMerge(%(read_percentile)s)(quantile_state)"
    )
    group = "period_key" if per_period else ("group_key" if grouped else "''")
    group_clause = f"GROUP BY {group}" if grouped or per_period else ""
    scope_partition = "PARTITION BY period_key" if per_period else ""
    return f"""SELECT group_key, if(status = 'ok', raw_value, NULL) AS value,
        value AS numerator, CAST(NULL, 'Nullable(Float64)') AS denominator,
        status, unit_count, valid_count, missing_count, invalid_probability_count,
        'not_estimable' AS ci_status, 'not_computed' AS ci_reason, '' AS ci_method, '' AS ci_interval,
        CAST(NULL, 'Nullable(Float64)') AS std_error, CAST(NULL, 'Nullable(Float64)') AS ci_low,
        CAST(NULL, 'Nullable(Float64)') AS ci_high, CAST(NULL, 'Nullable(Float64)') AS effective_n,
        CAST(NULL, 'Nullable(UInt64)') AS ci_df
        FROM (SELECT {group} AS group_key, sum(src.unit_count) AS unit_count,
            sum(src.valid_count) AS valid_count, sum(src.missing_count) AS missing_count,
            sum(if(mixed_scope AND notEmpty(scope_key), src.unit_count, invalid_count)) AS invalid_probability_count,
            sum(known_sampled) AS sampled_count, toFloat64({aggregate}) AS raw_value,
            multiIf(unit_count = 0, 'no_data', invalid_probability_count > 0, 'invalid_probability',
                sampled_count > 0, 'sampled_inputs_not_supported', valid_count = 0, 'missing_inputs',
                NOT isFinite(raw_value), 'non_finite', 'ok') AS status
            FROM (SELECT *, minIf(scope_min, nonempty_scope_count > 0) OVER ({scope_partition}) !=
                maxIf(scope_max, nonempty_scope_count > 0) OVER ({scope_partition}) AS mixed_scope FROM ({sql})) AS src
            {group_clause})"""


def _aggregate(store, metric, definition, manifest, window):
    sql, params = bucket_select(metric, definition, manifest, window)
    return _rows(store, _aggregate_select(sql, definition, params), params)[0]


def _aggregate_periods(store, metric, definition, manifest, current, previous):
    """Compute independent periods without replacing stored cohort/scope keys.

    The shared compiler partitions its scope guard and stratum merge by period,
    applying exactly the same estimator as an independent overall read.
    """
    sql, params = bucket_select(
        metric,
        definition,
        manifest,
        {**current, "window_start": previous["window_start"]},
    )
    params["read_current_start"] = current["window_start"]
    sql = f"""SELECT *,
        if(bucket_start >= %(read_current_start)s, 'current', 'previous') AS period_key
        FROM ({sql})"""
    rows = _rows(
        store, _aggregate_select(sql, definition, params, per_period=True), params
    )
    by_period = {row["group_key"]: {**row, "group_key": ""} for row in rows}
    # GROUP BY returns no row for an empty period. Match the overall compiler's
    # explicit no-data result, including its unavailable-CI reason, not zero.
    empty = {
        **empty_row(),
        "ci_status": "not_estimable",
        "ci_reason": "not_computed"
        if definition.kind in {"unique_count", "percentile"}
        else "no_value",
        "ci_method": "",
        "ci_interval": "",
        "std_error": None,
        "ci_low": None,
        "ci_high": None,
        "effective_n": None,
        "ci_df": None,
    }
    return tuple(by_period.get(period, empty) for period in ("current", "previous"))


def point(row, definition):
    shaped = shape_result(row, include_uncertainty=True)
    recipe = field_recipe(definition)
    return {
        "value": shaped["value"],
        "point_status": shaped["status"],
        "rate_eligible": bool(recipe.formula and recipe.formula.rate_eligible),
        "weighted": definition.weighting == "inverse_probability",
        "unit": definition.unit,
        "value_format": definition.value_format,
        "unit_noun": NOUNS[definition.level],
        "value_mode": "materialized",
        "eligible_count": shaped["unit_count"],
        "evaluated_count": shaped["valid_count"],
        "missing_count": shaped["missing_count"],
        "uncertainty": shaped["uncertainty"],
    }


def window_is_calculated(definition, window):
    """A completed bounded backfill does not prove earlier windows were empty."""
    cursor = getattr(definition, "backfill_cursor", None) or {}
    if not cursor.get("start"):
        return True  # legacy versions have no bounded-backfill cursor
    start = datetime.fromisoformat(cursor["start"])
    if start.tzinfo is None:
        raise MetricUnavailableError(message="Metric backfill coverage is unavailable.")
    return window["window_start"] >= start


def require_calculated_window(definition, window):
    if not window_is_calculated(definition, window):
        raise MetricUnavailableError(
            "window_not_calculated",
            "This window predates the completed metric backfill. Choose a later start.",
        )


def read_kpi(metric, data, *, store=None):
    window = effective_window(data["window_start"], data["window_end"])
    duration = window["window_end"] - window["window_start"]
    previous = {
        **window,
        "window_start": window["window_start"] - duration,
        "window_end": window["window_start"],
    }
    with metric_read(metric, store=store) as (store, manifest, definition):
        require_calculated_window(definition, window)
        if window_is_calculated(definition, previous):
            current_row, previous_row = _aggregate_periods(
                store, metric, definition, manifest, window, previous
            )
            prior = point(previous_row, definition)
        else:
            current_row = _aggregate(store, metric, definition, manifest, window)
            prior = None
        current = point(current_row, definition)
        delta = (
            current["value"] - prior["value"]
            if prior is not None
            and current["value"] is not None
            and prior["value"] is not None
            else None
        )
        if delta is not None and not math.isfinite(delta):
            delta = None
        return {
            "provenance": provenance(manifest, definition),
            "effective_window": window,
            "current": current,
            "previous": prior,
            "delta": delta,
            "delta_uncertainty": {
                "status": "not_estimable",
                "reason": "no_value"
                if delta is None
                else "paired_covariance_unavailable",
                "method": "",
                "std_error": None,
                "ci_low": None,
                "ci_high": None,
            },
        }


def read_trend(metric, data, *, store=None):
    window = effective_window(data["window_start"], data["window_end"])
    count = data.get("points", 30)
    if not 1 <= count <= 90:
        raise MetricInputError({"points": "Use 1 to 90 points."})
    buckets = int((window["window_end"] - window["window_start"]) / QUARTER)
    width = math.ceil(buckets / count)
    with metric_read(metric, store=store) as (store, manifest, definition):
        require_calculated_window(definition, window)
        group_by = data.get("group_by", "")
        dimensions = list(definition.group_dims or [])
        allowed_groups = dimensions or (
            [definition.cohort_key] if definition.cohort_key else []
        )
        if group_by and group_by not in allowed_groups:
            raise MetricInputError(
                {"group_by": "Choose a dimension stored by this definition."}
            )
        sql, params = bucket_select(metric, definition, manifest, window)
        params.update(read_start=window["window_start"], read_step=width * 900)
        # The full window reaches combine_parts_select before its scope guard.
        # Point/group boundaries therefore cannot hide mixed sampling scopes.
        group = "group_key" if group_by else "''"
        if group_by in dimensions:
            params["read_dimension"] = dimensions.index(group_by) + 1
            # Dimensions are an array of tagged JSON strings. Preserve structural
            # missing/mixed/Other markers instead of turning them into empty values.
            group = "if(group_key IN ('[\"other\"]', '[\"mixed\"]'), group_key, JSONExtractString(group_key, %(read_dimension)s))"
        sql = f"""SELECT * REPLACE(toJSONString(tuple(
            intDiv(dateDiff('second', toDateTime(%(read_start)s, 'UTC'), bucket_start), %(read_step)s), {group}
            )) AS group_key) FROM ({sql})"""
        rows = _rows(
            store, _aggregate_select(sql, definition, params, grouped=True), params
        )
        result = []
        seen = set()
        for row in rows:
            index, key = json.loads(row["group_key"])
            index = int(
                index
            )  # CH may JSON-quote Int64 values without losing precision.
            seen.add(index)
            start = window["window_start"] + index * width * QUARTER
            result.append(
                {
                    **point(row, definition),
                    "group_key": key,
                    "window_start": start,
                    "window_end": min(start + width * QUARTER, window["window_end"]),
                }
            )
        if not group_by:
            for index in range(math.ceil(buckets / width)):
                if index in seen:
                    continue
                start = window["window_start"] + index * width * QUARTER
                result.append(
                    {
                        **point(empty_row(), definition),
                        "group_key": "",
                        "window_start": start,
                        "window_end": min(
                            start + width * QUARTER, window["window_end"]
                        ),
                    }
                )
        result.sort(key=lambda row: (row["window_start"], row["group_key"]))
        return {
            "provenance": provenance(manifest, definition),
            "effective_window": window,
            "results": result,
        }


def empty_row():
    return {
        "group_key": "",
        "value": None,
        "numerator": None,
        "denominator": None,
        "status": "no_data",
        "unit_count": 0,
        "valid_count": 0,
        "missing_count": 0,
        "invalid_probability_count": 0,
    }


def _retention_policy(metric):
    provider = getattr(settings, "METRICS_RETENTION_DAYS_PROVIDER", "")
    if not provider:
        raise MetricUnavailableError(
            message="Current evidence retention policy is unavailable."
        )
    policy = import_string(provider)
    return lambda: policy(metric.organization_id)


def _fields_select(metric, definition, manifest, window, *, ids=None, cutoff=None):
    keys = {
        "field_id": (metric.id,),
        "field_version": (definition.version,),
        "level": (definition.level,),
    }
    if ids is not None:
        keys["unit_id"] = tuple(ids)
    sql, params = committed_rows_select(
        FIELD_VALUES,
        manifest,
        org_id=metric.organization_id,
        project_id=metric.project_id,
        key_in=keys,
        param_prefix="evidence",
    )
    params.update(
        evidence_start=window["window_start"],
        evidence_end=window["window_end"],
        evidence_workspace=str(metric.workspace_id),
    )
    where = """is_deleted = 0 AND workspace_id = toUUID(%(evidence_workspace)s)
        AND unit_start >= %(evidence_start)s AND unit_start < %(evidence_end)s"""
    projection = "*"
    if cutoff is not None:
        params["evidence_cutoff"] = cutoff
        if ids is None:
            where += " AND unit_start >= %(evidence_cutoff)s"
        else:
            expired = "unit_start < %(evidence_cutoff)s"
            replacements = [
                f"if({expired}, NULL, {name}) AS {name}"
                for name in (
                    "value_number",
                    "value_bool",
                    "value_text",
                    "part_n",
                    "part_d",
                    "sample_probability",
                    "population_size",
                    "settled_at",
                )
            ]
            replacements += [
                f"if({expired}, map(), {name}) AS {name}"
                for name in ("inputs", "inputs_bool", "inputs_text")
            ]
            replacements += [
                f"if({expired}, '', {name}) AS {name}"
                for name in (
                    "session_id",
                    "user_id",
                    "group_key",
                    "sampling_scope",
                    "sampling_stratum",
                    "sampling_design",
                )
            ]
            replacements += [
                f"if({expired}, 'no_value', status) AS status",
                f"if({expired}, 'retention_expired', no_value_reason) AS no_value_reason",
            ]
            projection = "* REPLACE(" + ", ".join(replacements) + ")"
    return f"SELECT {projection} FROM ({sql}) WHERE {where}", params


def _unit_value(row, unit_id=None):
    if row is None:
        return {
            "unit_id": unit_id,
            "status": "not_calculated",
            "no_value_reason": "not_calculated",
            "value_number": None,
            "value_bool": None,
            "value_text": None,
            "inputs": {},
            "inputs_bool": {},
            "inputs_text": {},
            "eligible_count": 0,
            "evaluated_count": 0,
            "missing_count": 0,
            "influence": None,
        }
    state = (
        "value"
        if row["status"] == "ok"
        else ("in_progress" if row["status"] == "open" else "no_value")
    )
    numeric_inputs = {
        key: value if value is None or math.isfinite(value) else None
        for key, value in row["inputs"].items()
    }
    return {
        "unit_id": row["unit_id"],
        "status": state,
        "no_value_reason": row["no_value_reason"] if state == "no_value" else "",
        **{
            key: row[key] if state == "value" else None
            for key in ("value_number", "value_text")
        },
        "value_bool": bool(row["value_bool"])
        if state == "value" and row["value_bool"] is not None
        else None,
        "inputs": numeric_inputs if state != "in_progress" else {},
        "inputs_bool": {
            key: None if value is None else bool(value)
            for key, value in row["inputs_bool"].items()
        }
        if state != "in_progress"
        else {},
        "inputs_text": row["inputs_text"] if state != "in_progress" else {},
        "eligible_count": int(
            state != "in_progress" and row["no_value_reason"] != "retention_expired"
        ),
        "evaluated_count": int(
            state == "value" or row["no_value_reason"] == "zero_denominator"
        ),
        "missing_count": int(
            row["no_value_reason"]
            in {"missing_inputs", "attribute_absent", "not_evaluated"}
        ),
        "influence": row.get("influence"),
    }


def _cursor_context(metric, definition, manifest, window):
    return sha256(
        ":".join(
            map(
                str,
                (
                    metric.id,
                    definition.id,
                    manifest.id,
                    window["window_start"],
                    window["window_end"],
                ),
            )
        ).encode()
    ).hexdigest()


def _driver_influence_select(sql, recipe, total, params):
    """Use the estimator's complete cases, not the displayed per-unit value.

    A Boolean OR can have finite parts despite a missing required input. Conversely,
    a complete ratio with d=0 has no per-unit value but still contributes to the
    pooled numerator. Neither status='ok' nor status!='open' proves eligibility.
    """
    formula = recipe.formula
    unpack = ", ".join(f"inputs['{name}'] AS v_{name}" for name in formula.required)
    complete = (
        f"({formula.complete_sql}) AND isNotNull(part_n) AND isFinite(part_n) "
        "AND isNotNull(part_d) AND isFinite(part_d)"
    )
    params.update(
        driver_value=total["value"],
        driver_n=total["numerator"],
        driver_d=total["denominator"],
    )
    contribution = "part_n / nullIf(sample_probability, 0)"
    influence = contribution
    if formula.kind not in {"sum", "count_if"}:
        influence = f"%(driver_value)s - (%(driver_n)s - {contribution}) / nullIf(%(driver_d)s - part_d / nullIf(sample_probability, 0), 0)"
    return f"""SELECT *, if(ifNull({complete}, 0) AND invalid_probability = 0
        AND isFinite(sample_probability) AND sample_probability > 0 AND sample_probability <= 1
        AND isNotNull(%(driver_value)s) AND isFinite(raw_influence), raw_influence, NULL) AS influence,
        ifNull(abs(influence), -1.) AS rank_score FROM
        (SELECT *, {influence} AS raw_influence FROM
            (SELECT *, {unpack} FROM ({sql}) WHERE status != 'open'))"""


def _failing_unit_predicate(definition):
    # No-value/open rows are not Boolean failures. Count failures have a truthful
    # zero effect, so filtering only by negative influence would lose them all.
    if definition.kind in {"share", "count"}:
        return "status = 'ok' AND value_bool = 0"
    # Complete zero-denominator rows still contribute to pooled estimates and
    # effects, but have no per-unit value and must not consume failing-row slots.
    # Among valued units, select those pulling the value down, not merely the
    # largest absolute contributors (which can instead be pulling it up).
    return "status = 'ok' AND influence < 0"


def read_units(metric, data, *, drivers=False, store=None, retention_days=None):
    window = effective_window(data["window_start"], data["window_end"])
    ids = None if drivers else tuple(dict.fromkeys(data["unit_ids"]))
    if ids is not None and not 1 <= len(ids) <= 100:
        raise MetricInputError({"unit_ids": "Use 1 to 100 unit ids."})
    limit = data.get("limit", 20) if drivers else len(ids)
    if not 1 <= limit <= 100:
        raise MetricInputError({"limit": "Use 1 to 100 rows."})
    with metric_read(metric, store=store) as (store, manifest, definition):
        if drivers and definition.kind in {"unique_count", "percentile"}:
            raise MetricUnavailableError(
                "metric_drivers_unavailable",
                "Exact leave-one-out drivers cannot be recovered from distinct or percentile sketches.",
            )
        if (
            not drivers
            and data["context"] == "users"
            and definition.level == "conversation"
        ):
            raise MetricUnavailableError(
                "user_projection_unavailable",
                "Conversation values on Users require the shared per-user projection reader.",
            )
        if not drivers and data["context"] != CONTEXTS[definition.level]:
            raise MetricInputError(
                {"context": "Use the metric's native grain for unit values."}
            )
        policy = retention_days or _retention_policy(metric)
        cutoff = retention_cutoff(policy())
        if drivers and cutoff is not None and window["window_start"] < cutoff:
            raise MetricUnavailableError(
                "retention_expired",
                "This window includes expired evidence; anonymous KPI totals remain readable.",
            )
        sql, params = _fields_select(
            metric, definition, manifest, window, ids=ids, cutoff=cutoff
        )
        context = _cursor_context(metric, definition, manifest, window)
        after = data.get("after", "")
        if drivers:
            require_calculated_window(definition, window)
            total = _aggregate(store, metric, definition, manifest, window)
            if definition.level == "span":
                # Older manifests may contain span totals without evidence. A
                # partial backfill must not masquerade as a complete driver list.
                evidence_count = _rows(
                    store,
                    f"SELECT count() AS count FROM ({sql}) WHERE status != 'open'",
                    params,
                )[0]["count"]
                if evidence_count != total["unit_count"]:
                    raise MetricUnavailableError(
                        "span_evidence_unavailable",
                        "Committed span evidence is incomplete; refresh this window before reading drivers.",
                    )
            recipe = field_recipe(definition)
            sql = _driver_influence_select(sql, recipe, total, params)
            sql = f"SELECT * FROM ({sql}) WHERE {_failing_unit_predicate(definition)}"
            condition = "1"
            if after:
                try:
                    cursor = signing.loads(after, salt=CURSOR_SALT)
                    if cursor["context"] != context or not math.isfinite(
                        cursor["score"]
                    ):
                        raise ValueError
                    params.update(
                        after_score=cursor["score"], after_unit=cursor["unit"]
                    )
                except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
                    raise MetricInputError(
                        {
                            "after": "Cursor changed or expired. Start from the first page."
                        }
                    ) from exc
                condition = "rank_score < %(after_score)s OR (rank_score = %(after_score)s AND unit_id > %(after_unit)s)"
            sql = f"SELECT * FROM ({sql}) WHERE {condition} ORDER BY rank_score DESC, unit_id LIMIT %(read_limit)s"
        else:
            sql += " ORDER BY unit_id LIMIT %(read_limit)s"
        params["read_limit"] = limit + 1
        rows = _rows(store, sql, params)
        final_cutoff = retention_cutoff(policy())
        if final_cutoff and any(
            row["unit_start"] < final_cutoff
            and row["no_value_reason"] != "retention_expired"
            for row in rows
        ):
            raise MetricUnavailableError(
                "retention_expired",
                "Evidence retention changed during the read. Retry.",
            )
        next_cursor = None
        if drivers:
            if len(rows) > limit:
                last = rows[limit - 1]
                next_cursor = signing.dumps(
                    {
                        "context": context,
                        "score": last["rank_score"],
                        "unit": last["unit_id"],
                    },
                    salt=CURSOR_SALT,
                )
            results = [_unit_value(row) for row in rows[:limit]]
        else:
            by_id = {row["unit_id"]: row for row in rows}
            results = [_unit_value(by_id.get(unit), unit) for unit in ids]
        return {
            "provenance": provenance(manifest, definition),
            "effective_window": window,
            "results": results,
            "next_cursor": next_cursor,
        }


def read_history(metric, data):
    """Bounded keyset merge; attempts must explicitly include this metric version."""
    _check_metric(metric)
    from tracer.models.derived_metric import DerivedMetricRun
    from tracer.serializers.derived_metric import DerivedMetricRunSerializer

    snapshots = DerivedMetricRun.objects.filter(
        definition__metric=metric, kind="snapshot"
    ).select_related("definition")
    versions = DerivedMetricVersion.objects.filter(metric=metric)
    attempts = MetricRefreshAttempt.objects.filter(
        project_id=metric.project_id,
        metric_versions__contains=[{"metric_id": str(metric.id)}],
    )
    before = data.get("before")
    if before:
        anchor = (
            versions.filter(pk=before).first()
            or attempts.filter(pk=before).first()
            or snapshots.filter(pk=before).first()
        )
        if anchor is None:
            raise MetricInputError({"before": "Unknown history cursor in this metric."})
        condition = Q(created_at__lt=anchor.created_at) | Q(
            created_at=anchor.created_at, id__lt=anchor.id
        )
        versions, attempts = versions.filter(condition), attempts.filter(condition)
        snapshots = snapshots.filter(condition)
    limit = data.get("limit", 30)
    if not 1 <= limit <= 100:
        raise MetricInputError({"limit": "Use 1 to 100 history entries."})
    rows = [
        (v.created_at, v.id, "version", v)
        for v in versions.order_by("-created_at", "-id")[: limit + 1]
    ]
    rows += [
        (a.created_at, a.id, "recalculation", a)
        for a in attempts.order_by("-created_at", "-id")[: limit + 1]
    ]
    rows += [
        (s.created_at, s.id, "snapshot", s)
        for s in snapshots.order_by("-created_at", "-id")[: limit + 1]
    ]
    rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
    from tracer.serializers.derived_metric import DerivedMetricVersionSerializer

    return {
        "results": [
            {
                "kind": kind,
                "created_at": created,
                "definition": DerivedMetricVersionSerializer(obj).data
                if kind == "version"
                else None,
                "snapshot": DerivedMetricRunSerializer(obj).data
                if kind == "snapshot"
                else None,
                "status": obj.backfill_status
                if kind == "version"
                else obj.status
                if kind == "snapshot"
                else obj.state,
            }
            for created, _id, kind, obj in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1][1]) if len(rows) > limit else None,
    }
