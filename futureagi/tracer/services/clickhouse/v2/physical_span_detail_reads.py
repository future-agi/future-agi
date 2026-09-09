"""One authorized CH25 physical span, independent of legacy four-part detail.

Only immutable six-key predicates enter FINAL. Expected timestamp/version are
an optimistic selection check AFTER the complete winner is read, never filters
which could resurrect a previous version. No eval/annotation lookup is attempted:
their bare span association cannot attest this physical reference.
"""

from datetime import UTC, datetime

from tracer.services.clickhouse.application_read_policy import application_read_settings


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


def build_physical_span_detail_query(*, span_id, reference):
    """The caller has validated this complete selector; no mutable bind exists."""
    return (
        """
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
            attrs_string, attrs_number, attrs_bool
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
    sql, params = build_physical_span_detail_query(span_id=span_id, reference=reference)
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
    return row
