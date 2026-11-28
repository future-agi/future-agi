"""One authorized CH25 physical span, independent of legacy four-part detail.

Only immutable six-key predicates enter FINAL. Expected timestamp/version are
an optimistic selection check AFTER the complete winner is read, never filters
which could resurrect a previous version. No eval/annotation lookup is attempted:
their bare span association cannot attest this physical reference.
"""

from datetime import UTC, datetime

from tracer.services.clickhouse.application_read_policy import application_read_settings
from tracer.services.clickhouse.v2.trajectory_hydration import (
    hydrate_span_dicts,
    items_query_settings,
    trajectory_read_state,
)


class PhysicalSpanDetailError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _utc(value):
    if not isinstance(value, datetime):
        raise ValueError("A physical span timestamp must be a datetime")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _microseconds(value):
    delta = _utc(value) - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def build_physical_span_detail_query(*, span_id, reference, state=None):
    """The caller has validated this complete selector; no mutable bind exists.

    ``state`` is the caller's read-state snapshot (resolved when None)."""
    if state is None:
        state = trajectory_read_state()
    traj_select = (
        """,
            input_item_hashes, output_item_hashes, tool_def_hashes,
            item_scope_id, payload_encoding, payload_envelope,
            created_at"""
        if state.projects_pointers
        else ""
    )
    return (
        f"""
        SELECT
            toString(project_id) AS project_id, trace_id, id,
            observation_type, service_name, start_time,
            toStartOfHour(start_time) AS start_hour, toString(_version) AS _version,
            is_deleted, parent_span_id, name, end_time, input, output, model,
            latency_ms, prompt_tokens, completion_tokens, total_tokens, cost,
            status, status_message, tags, span_events, provider,
            attributes_extra AS span_attributes, project_version_id,
            custom_eval_config_id, toString(trace_session_id) AS trace_session_id,
            toJSONString(metadata) AS metadata_json,
            toJSONString(resource_attrs) AS resource_attrs,
            attrs_string, attrs_number, attrs_bool{traj_select}
        FROM spans FINAL
        PREWHERE project_id = %(physical_project_id)s
          AND observation_type = %(physical_observation_type)s
          AND service_name = %(physical_service_name)s
          AND toStartOfHour(start_time) = fromUnixTimestamp64Micro(%(physical_hour_us)s, 'UTC')
          AND trace_id = %(physical_trace_id)s
          AND id = %(physical_span_id)s
    """,
        {
            "physical_project_id": str(reference["project_id"]),
            "physical_trace_id": reference["trace_id"],
            "physical_span_id": span_id,
            "physical_hour_us": _microseconds(reference["start_hour"]),
            "physical_observation_type": reference["observation_type"],
            "physical_service_name": reference["service_name"],
        },
    )


def read_physical_span_detail(*, analytics, span_id, reference, authorized_project_id):
    """Return one complete winner or fail explicitly, with no bare-ID fallback."""
    if str(reference["project_id"]) != str(authorized_project_id):
        raise PhysicalSpanDetailError("span_reference_not_found")
    # One snapshot: the projection and its hydration agree. A probe failure
    # is TrajectoryHydrationUnavailable (503), never a preview.
    state = trajectory_read_state(analytics)
    sql, params = build_physical_span_detail_query(
        span_id=span_id, reference=reference, state=state
    )
    result = analytics.execute_ch_query(
        sql,
        params,
        settings=application_read_settings(
            {
                "max_threads": 1,
                "optimize_move_to_prewhere": 0,
                "optimize_move_to_prewhere_if_final": 0,
                "enable_optimize_predicate_expression_to_final_subquery": 0,
                "query_plan_merge_expressions": 0,
                "use_skip_indexes_if_final": 0,
                "do_not_merge_across_partitions_select_final": 0,
            }
        ),
    )
    rows = result.data
    if getattr(result, "complete", True) is not True or not isinstance(rows, list):
        raise PhysicalSpanDetailError("span_reference_unavailable")
    if not rows:
        raise PhysicalSpanDetailError("span_reference_not_found")
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise PhysicalSpanDetailError("span_reference_unavailable")
    row = dict(rows[0])
    try:
        actual = (
            str(row["project_id"]),
            row["trace_id"],
            row["id"],
            _utc(row["start_hour"]),
            row["observation_type"],
            row["service_name"],
        )
        expected = (
            str(reference["project_id"]),
            reference["trace_id"],
            span_id,
            reference["start_hour"],
            reference["observation_type"],
            reference["service_name"],
        )
        version = row["_version"]
        if (
            actual != expected
            or _utc(row["start_time"]).replace(minute=0, second=0, microsecond=0)
            != actual[3]
            or type(version) is not str
            or not version.isascii()
            or not version.isdecimal()
            or str(int(version)) != version
            or not 0 <= int(version) <= 2**64 - 1
            or row["is_deleted"] not in (0, 1)
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise PhysicalSpanDetailError("span_reference_unavailable") from None
    if row["is_deleted"]:
        raise PhysicalSpanDetailError("span_reference_not_found")
    if (
        _utc(row["start_time"]) != reference["expected_start_time"]
        or version != reference["expected_version"]
    ):
        raise PhysicalSpanDetailError("span_reference_changed")
    row.update(start_time=_utc(row["start_time"]), start_hour=actual[3])

    if state.projects_pointers:
        hydrate_span_dicts(
            [row],
            lambda traj_sql, traj_params: list(
                analytics.execute_ch_query(
                    traj_sql,
                    traj_params,
                    settings=items_query_settings(traj_params),
                ).data
                or []
            ),
            project_id_fallback=str(row["project_id"]),
            attr_key="span_attributes",
            state=state,
            surface="physical_span_detail",
        )
        row.pop("created_at", None)
    return row
