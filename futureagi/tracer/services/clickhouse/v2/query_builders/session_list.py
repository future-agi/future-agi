"""
v2 SessionList query builder — targets the CH 25.3 spans schema.

Subclass + post-rewrite, same as v2/span_list.py and v2/trace_list.py.
The v1 SessionList builder aggregates spans by trace_session_id; v2's
materialized `trace_session_id` column is queried unchanged. `V2RewriteMixin`
routes every inherited `build*` method's SQL through the v2 rewriter at one
boundary. All of this builder's queries target the migrated `spans` schema, so
only the native CH25 span-attribute query is excluded from rewriting.
"""

from __future__ import annotations

from typing import Any

from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.v2.id_remap_sql import resolved_id_expr
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
    _append_v2_settings,
)


class SessionListQueryBuilderV2(V2RewriteMixin, SessionListQueryBuilder):
    """Drop-in v2 SessionList builder."""

    _FILTER_BUILDER_CLS = ClickHouseFilterBuilderV2

    # This method already emits native CH25 SQL. The generic rewrite would
    # reinterpret the compatibility alias `span_attributes_raw`.
    _v2_rewrite_exclude = frozenset({"build_span_attributes_query"})

    def build_span_attributes_query(
        self, session_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
        ids = tuple(dict.fromkeys(str(value) for value in session_ids if value))
        if not ids:
            return "", {}
        if len(ids) > 200:
            raise ValueError("attribute session page exceeds bounded limit")

        # The bounded endpoint does not call ``build`` before page hydration.
        # Bind its exact request window here and apply it to both candidate
        # acquisition and the authoritative four-field latest-state replay.
        attr_start_date, attr_end_date = self.parse_time_range(self.filters)
        params = {
            **self.params,
            "attr_session_ids": ids,
            "attr_start_date": attr_start_date,
            "attr_end_date": attr_end_date,
        }
        attr_exclusion, attr_exclusion_params = self.bounded_datetime_exclusion_sql(
            self.filters,
            column="start_time",
            param_prefix="session_attr_v2_time_exclusion",
        )
        params.update(attr_exclusion_params)
        attr_exclusion_fragment = (
            f"\n              AND {attr_exclusion}" if attr_exclusion else ""
        )
        ts_map_ctes = self._candidate_survivor_map_ctes(params, ids)
        resolved_ts = resolved_id_expr("latest_trace_session_id", "ts_remap")
        sql = f"""
        WITH
        {ts_map_ctes},
        candidate_root_identities AS (
            SELECT DISTINCT project_id, trace_id, id, start_time
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              AND toDate(start_time) BETWEEN
                  toDate(fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')) AND
                  toDate(fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC'))
              AND start_time >= fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')
              AND start_time < fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC'){attr_exclusion_fragment}
              AND (
                  trace_session_id IN %(attr_session_ids)s
                  OR trace_session_id IN (
                      SELECT any_id
                      FROM ts_survivor_map
                      WHERE survivor_id IN %(attr_session_ids)s
                  )
              )
              AND (parent_span_id IS NULL OR parent_span_id = '')
        ),
        latest_roots AS (
            SELECT
                project_id,
                trace_id,
                id,
                start_time,
                argMax(tuple(parent_span_id), _version).1 AS latest_parent_span_id,
                argMax(tuple(trace_session_id), _version).1 AS latest_trace_session_id,
                argMax(tuple(attributes_extra), _version).1 AS latest_attributes_extra,
                argMax(attrs_string, _version) AS latest_attrs_string,
                argMax(attrs_number, _version) AS latest_attrs_number,
                argMax(attrs_bool, _version) AS latest_attrs_bool,
                argMax(is_deleted, _version) AS latest_is_deleted
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              AND toDate(start_time) BETWEEN
                  toDate(fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')) AND
                  toDate(fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC'))
              AND start_time >= fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')
              AND start_time < fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC'){attr_exclusion_fragment}
              AND (project_id, trace_id, id, start_time) IN (
                  SELECT project_id, trace_id, id, start_time
                  FROM candidate_root_identities
              )
            GROUP BY project_id, trace_id, id, start_time
        )
        SELECT
            {resolved_ts} AS session_id,
            latest_attributes_extra AS span_attributes_raw,
            latest_attrs_string AS attrs_string,
            latest_attrs_number AS attrs_number,
            latest_attrs_bool AS attrs_bool
        FROM latest_roots
        LEFT JOIN ts_survivor_map AS ts_remap
            ON latest_trace_session_id = ts_remap.any_id
        WHERE latest_is_deleted = 0
          AND (latest_parent_span_id IS NULL OR latest_parent_span_id = '')
          AND (
            (latest_attributes_extra != '{{}}' AND latest_attributes_extra != '')
            OR length(mapKeys(latest_attrs_string)) > 0
            OR length(mapKeys(latest_attrs_number)) > 0
            OR length(mapKeys(latest_attrs_bool)) > 0
          )
          AND {resolved_ts} IN %(attr_session_ids)s
        LIMIT 500
        """
        return _append_v2_settings(sql), params


__all__ = ["SessionListQueryBuilderV2"]
