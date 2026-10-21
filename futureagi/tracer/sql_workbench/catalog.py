"""Versioned SQL catalog. Physical names and scope predicates are server-owned."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

CATALOG_VERSION = 1
IDENTITY_SEMANTICS = "Raw storage identifiers: merged session/user identities are not consolidated. Joins and distinct counts may differ from Observe after an identity merge."


class CatalogUnavailable(Exception):
    pass


MAX_WINDOW = timedelta(days=31)
MAX_CONFIGS = 1000


@dataclass(frozen=True)
class Scope:
    organization_id: UUID
    workspace_id: UUID
    project_id: UUID
    start: datetime
    end: datetime
    eval_config_ids: tuple[UUID, ...] = ()
    eval_table: str = "tracer_eval_logger"
    database: str = "default"
    derived_metrics_enabled: bool = False

    def __post_init__(self):
        for value in (self.organization_id, self.workspace_id, self.project_id):
            if not isinstance(value, UUID) or value.int == 0:
                raise ValueError(
                    "A non-null organization, workspace and project are required."
                )
        if not self.start.tzinfo or not self.end.tzinfo:
            raise ValueError("Use timezone-aware timestamps.")
        if not timedelta(0) < self.end - self.start <= MAX_WINDOW:
            raise ValueError("Select a time window of at most 31 days.")
        if len(self.eval_config_ids) > MAX_CONFIGS or any(
            not isinstance(value, UUID) or value.int == 0
            for value in self.eval_config_ids
        ):
            raise ValueError("Invalid evaluation configuration scope.")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,63}", self.database):
            raise ValueError("Invalid SQL source database.")
        if self.eval_table not in {"tracer_eval_logger", "tracer_eval_logger_v2"}:
            raise ValueError("Unsupported evaluation store.")


# The wire catalog is also the editor's completion source. Never expose physical
# tables, credentials, dictionary names, or dynamically discovered system metadata.
COLUMNS = {
    "spans": {
        "id": "String",
        "trace_id": "String",
        "session_id": "Nullable(UUID)",
        "raw_session_id": "Nullable(UUID)",
        "user_id": "Nullable(UUID)",
        "raw_user_id": "Nullable(UUID)",
        "name": "String",
        "start_time": "DateTime64",
        "end_time": "Nullable(DateTime64)",
        "latency_ms": "Int32",
        "status": "String",
        "model": "String",
        "provider": "String",
        "total_tokens": "Int32",
        "cost": "Float64",
        "attrs_string": "Map(String, String)",
        "attrs_number": "Map(String, Float64)",
    },
    "traces": {
        "id": "UUID",
        "session_id": "Nullable(UUID)",
        "raw_session_id": "Nullable(UUID)",
        "name": "Nullable(String)",
        "external_id": "Nullable(String)",
        "created_at": "DateTime64",
        "metadata": "String",
    },
    "sessions": {
        "id": "UUID",
        "raw_id": "UUID",
        "external_id": "String",
        "first_seen": "DateTime64",
        "locale": "String",
    },
    "users": {
        "id": "UUID",
        "raw_id": "UUID",
        "external_id": "String",
        "user_id_type": "Nullable(String)",
        "first_seen": "DateTime64",
        "metadata": "String",
    },
    "eval_results": {
        "id": "UUID",
        "trace_id": "Nullable(UUID)",
        "span_id": "Nullable(String)",
        "session_id": "Nullable(UUID)",
        "raw_session_id": "Nullable(UUID)",
        "config_id": "UUID",
        "target_type": "String",
        "score": "Nullable(Float64)",
        "passed": "Nullable(UInt8)",
        "label": "Nullable(String)",
        "error": "UInt8",
        "created_at": "DateTime64",
        "run_id": "Nullable(String)",
        "eval_task_id": "Nullable(String)",
        "policy_version": "Nullable(String)",
        "sampling_unit": "Nullable(String)",
        "sampling_unit_id": "Nullable(String)",
        "stratum": "Nullable(String)",
        "inclusion_probability": "Nullable(Float64)",
        "sampling_window_start": "Nullable(DateTime64)",
        "sampling_window_end": "Nullable(DateTime64)",
        "population_size": "Nullable(UInt64)",
    },
    "annotations": {
        "id": "UUID",
        "trace_id": "Nullable(UUID)",
        "span_id": "Nullable(String)",
        "session_id": "Nullable(UUID)",
        "raw_session_id": "Nullable(UUID)",
        "label_id": "UUID",
        "value": "String",
        "notes": "Nullable(String)",
        "created_at": "DateTime64",
        "score_source": "String",
    },
}

COLUMNS["derived_metrics"] = {
    "org_id": "UUID",
    "workspace_id": "UUID",
    "project_id": "UUID",
    "metric_id": "UUID",
    "metric_version": "UInt32",
    "run_id": "UUID",
    "result_id": "UUID",
    "rollup": "String",
    "group_key": "String",
    "value": "Nullable(Float64)",
    "numerator": "Nullable(Float64)",
    "denominator": "Nullable(Float64)",
    "status": "String",
    "unit_count": "UInt64",
    "valid_count": "UInt64",
    "missing_count": "UInt64",
    "invalid_probability_count": "UInt64",
    "window_start": "DateTime64",
    "window_end": "DateTime64",
    "as_of": "DateTime64",
    "source_run_id": "String",
    "definition_hash": "String",
}

DESCRIPTIONS = {
    "spans": "One live span in the selected start-time window; no prompt or output payloads.",
    "traces": "One live trace created in the selected window.",
    "sessions": "Sessions first seen in the window; locale is the latest present span locale in that window.",
    "users": "Users first seen in the window (not active-user counts).",
    "eval_results": "Live scores created in the window with project-owned config and target; run_id is null until real execution provenance exists; eval_task_id is the task key. Sampling metadata is nullable for legacy results.",
    "derived_metrics": "Committed derived-metric attempts; windows fully contained in the selected window. Requires the derived metrics schema and reader grants.",
    "annotations": "Unified scores with explicit organization, workspace and tracer-project ownership; legacy null ownership is excluded.",
}


def catalog(
    derived_metrics_available=False,
    unavailable_reason="Derived metrics schema is not enabled.",
):
    return [
        {
            "name": name,
            "description": DESCRIPTIONS[name],
            "available": name != "derived_metrics" or derived_metrics_available,
            "unavailable_reason": (
                unavailable_reason
                if name == "derived_metrics" and not derived_metrics_available
                else ""
            ),
            "columns": [
                {"name": column, "type": kind} for column, kind in columns.items()
            ],
        }
        for name, columns in COLUMNS.items()
    ]


def sources(scope: Scope) -> dict[str, str]:
    """Only canonical UUIDs and datetimes enter these server-authored templates."""
    from datetime import timezone

    project = f"toUUID('{scope.project_id}')"
    org = f"toUUID('{scope.organization_id}')"
    workspace = f"toUUID('{scope.workspace_id}')"
    start = scope.start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    end = scope.end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")

    def window(column):
        return (
            f"{column} >= toDateTime64('{start}', 6, 'UTC') AND "
            f"{column} < toDateTime64('{end}', 6, 'UTC')"
        )

    span_scope = f"project_id = {project} AND org_id = {org} AND is_deleted = 0 AND {window('start_time')}"
    trace_scope = (
        f"project_id = {project} AND is_deleted = 0 AND {window('created_at')}"
    )
    session_scope = (
        f"project_id = {project} AND is_deleted = 0 AND {window('first_seen')}"
    )
    configs = ", ".join(f"toUUID('{value}')" for value in scope.eval_config_ids)
    config_scope = f"custom_eval_config_id IN ({configs})" if configs else "0"
    # The eval store has no project key. BOTH config ownership and a typed
    # project-owned target are required. NULL keys never grant access.
    targets = (
        f"((target_type = 'span' AND trace_id IS NOT NULL AND tuple(observation_span_id, toString(trace_id)) IN (SELECT id, trace_id FROM spans FINAL WHERE {span_scope})) "
        f"OR (target_type = 'trace' AND trace_id IN (SELECT id FROM traces FINAL WHERE {trace_scope})) "
        f"OR (target_type = 'session' AND trace_session_id IN (SELECT trace_session_id FROM trace_sessions FINAL WHERE {session_scope})))"
    )
    live_eval = (
        "is_deleted = 0"
        if scope.eval_table.endswith("_v2")
        else "deleted = 0 AND _peerdb_is_deleted = 0"
    )

    def sampling(key, data_type="String"):
        return f"JSONExtract(output_metadata, '_task_selection', 'sampling', '{key}', 'Nullable({data_type})')"

    probability = sampling("inclusion_probability", "Float64")
    population = sampling("population_size", "Int64")
    sampling_projection = ", ".join(
        [
            f"{sampling('policy_version')} AS policy_version",
            f"{sampling('sampling_unit')} AS sampling_unit",
            f"{sampling('sampling_unit_id')} AS sampling_unit_id",
            f"{sampling('stratum')} AS stratum",
            f"if({probability} > 0 AND {probability} <= 1 AND isFinite({probability}), {probability}, NULL) AS inclusion_probability",
            "parseDateTime64BestEffortOrNull(JSONExtractString(output_metadata, '_task_selection', 'sampling', 'window', 'start'), 6, 'UTC') AS sampling_window_start",
            "parseDateTime64BestEffortOrNull(JSONExtractString(output_metadata, '_task_selection', 'sampling', 'window', 'end'), 6, 'UTC') AS sampling_window_end",
            f"if({population} >= 0, toUInt64({population}), NULL) AS population_size",
        ]
    )
    result = {
        "spans": f"SELECT id, trace_id, trace_session_id AS session_id, trace_session_id AS raw_session_id, end_user_id AS user_id, end_user_id AS raw_user_id, name, start_time, end_time, latency_ms, status, model, provider, total_tokens, cost, attrs_string, attrs_number FROM spans FINAL WHERE {span_scope}",
        "traces": f"SELECT id, session_id, session_id AS raw_session_id, name, external_id, created_at, metadata FROM traces FINAL WHERE {trace_scope}",
        "sessions": f"SELECT s.trace_session_id AS id, s.trace_session_id AS raw_id, s.external_session_id AS external_id, s.first_seen, coalesce(l.locale, '') AS locale FROM (SELECT trace_session_id, external_session_id, first_seen FROM trace_sessions FINAL WHERE {session_scope}) AS s LEFT JOIN (SELECT trace_session_id, argMaxIf(attrs_string['locale'], tuple(start_time, id), mapContains(attrs_string, 'locale')) AS locale FROM spans FINAL WHERE {span_scope} AND trace_session_id IS NOT NULL GROUP BY trace_session_id) AS l ON s.trace_session_id = l.trace_session_id",
        "users": f"SELECT end_user_id AS id, end_user_id AS raw_id, user_id AS external_id, user_id_type, first_seen, metadata FROM end_users FINAL WHERE project_id = {project} AND organization_id = {org} AND is_deleted = 0 AND {window('first_seen')}",
        "eval_results": f"SELECT id, trace_id, observation_span_id AS span_id, trace_session_id AS session_id, trace_session_id AS raw_session_id, custom_eval_config_id AS config_id, target_type, output_float AS score, output_bool AS passed, output_str AS label, error, created_at, CAST(NULL AS Nullable(String)) AS run_id, eval_task_id, {sampling_projection} FROM {scope.eval_table} FINAL WHERE {live_eval} AND {window('created_at')} AND {config_scope} AND {targets}",
        "annotations": f"SELECT id, trace_id, observation_span_id AS span_id, trace_session_id AS session_id, trace_session_id AS raw_session_id, label_id, value, notes, created_at, score_source FROM model_hub_score FINAL WHERE organization_id = {org} AND workspace_id = {workspace} AND tracer_project_id = {project} AND deleted = 0 AND _peerdb_is_deleted = 0 AND {window('created_at')}",
    }

    if scope.derived_metrics_enabled:
        columns = ", ".join(COLUMNS["derived_metrics"])
        result["derived_metrics"] = (
            f"SELECT {columns} FROM derived_metrics WHERE org_id = {org} AND workspace_id = {workspace} AND project_id = {project} AND window_start >= toDateTime64('{start}', 6, 'UTC') AND window_end <= toDateTime64('{end}', 6, 'UTC')"
        )
    return result
