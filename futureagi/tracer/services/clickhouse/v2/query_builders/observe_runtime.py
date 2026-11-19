"""Exact numbered Observe pages for a previously measured Runtime latency cohort."""

from django.conf import settings

from tracer.services.clickhouse.query_builders.base import _unix_microseconds
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
    _rendered_literal_bytes,
)
from tracer.services.clickhouse.runtime_cohorts import (
    MAX_COHORT_TRACES,
    latency_cohort_token,
    resolve_cohort,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)

# Leave space under CH's 256 KiB parser ceiling for the fixed query and settings.
_COHORT_LITERAL_BUDGET = 192 * 1024


class RuntimeLatencyQueryBuilderV2(TraceListQueryBuilderV2):
    def build_runtime_latency_page(self, sort_params):
        """Rank narrow replay coordinates, not hydrated spans or one grid block.

        The raw in-window witness matches cohort seed discovery. Canonical root
        selection then sees every physical version of these finite identities,
        including roots outside the load window. Return the exact count even
        beyond the last numbered page. Only page-sized tuples leave ClickHouse.
        """
        token = latency_cohort_token(sort_params, self.filters)
        if (
            not token
            or not self.project_id
            or self.project_ids is not None
            or self.project_version_id
            or self.search
            or self.page_number < 0
            or not 1 <= self.page_size <= 500
        ):
            raise UnsupportedFilterShapeError("Invalid runtime latency sort")
        window = self.analyze_bounded_datetime_filters(self.filters)
        bounds = (_unix_microseconds(window.start), _unix_microseconds(window.end))
        try:
            project, ids = resolve_cohort(
                token, [self.project_id], latency_window=bounds
            )
        except ValueError as exc:
            raise UnsupportedFilterShapeError(str(exc)) from exc
        if (
            len(ids) > MAX_COHORT_TRACES
            or any(not isinstance(i, str) or not i for i in ids)
            or sum(_rendered_literal_bytes(i) + 2 for i in ids) > _COHORT_LITERAL_BUDGET
        ):
            raise UnsupportedFilterShapeError(
                "Runtime latency cohort exceeds the statement budget"
            )
        if not ids:
            return "SELECT 0 AS total, [] AS page", {}
        params = {
            "project_id": project,
            "cohort_ids": ids,
            "candidate_start_date_us": bounds[0],
            "candidate_end_date_us": bounds[1],
            "page_start": self.page_number * self.page_size + 1,
            "page_size": self.page_size,
            # Application transport normally clears byte caps. The fixed rank
            # statement carries its own (tested) cap, never a client setting.
            "read_bytes": min(settings.OBSERVABILITY_LIST_MAX_BYTES, 256 * 1024**2),
        }
        winner = self._filter_classifier_latest_select_sql("""
            argMax(tuple(parent_span_id), _peerdb_version).1 AS latest_parent_span_id,
            argMax(start_time, _peerdb_version) AS latest_start_time,
            argMax(is_deleted, _peerdb_version) AS latest_is_deleted,
            argMax(tuple(latency_ms), _peerdb_version).1 AS latest_latency_ms
        """)
        fields = ", ".join(
            source for source, _ in self._filter_classifier_root_identity_fields()
        )
        return (
            f"""
            SELECT count() AS total, arraySlice(arraySort(
                r -> tuple(isNull(r.3), -toFloat64(ifNull(r.3, 0)), r.1),
                groupArray(tuple(member, root_key, root_latency))
            ), %(page_start)s, %(page_size)s) AS page
            FROM (
                SELECT member, argMinIf(tuple(tuple({fields}),
                    if(latest_parent_span_id IS NULL OR latest_parent_span_id = '', latest_latency_ms, NULL)),
                    {self._runtime_root_order()}, eligible) AS root,
                    root.1 AS root_key, root.2 AS root_latency
                FROM (
                    SELECT trace_id AS member, id AS grouped_id, {winner},
                        (latest_is_deleted = 0 AND {self._runtime_root_condition()}) AS eligible,
                        max(is_deleted = 0 AND start_time >= fromUnixTimestamp64Micro(%(candidate_start_date_us)s)
                            AND start_time < fromUnixTimestamp64Micro(%(candidate_end_date_us)s)) AS seed
                    FROM spans PREWHERE project_id = %(project_id)s AND trace_id IN %(cohort_ids)s
                    GROUP BY {self.filter_classifier_physical_group_by(org_scope=False)}
                ) GROUP BY member HAVING max(seed) AND countIf(eligible) > 0
            )
            SETTINGS max_bytes_to_read = %(read_bytes)s, max_result_bytes = 8388608,
                read_overflow_mode = 'throw', result_overflow_mode = 'throw'""",
            params,
        )

    def runtime_latency_page_rows(self, page):
        names = [alias for _, alias in self._filter_classifier_root_identity_fields()]
        return [
            {
                "project_id": self.project_id,
                "trace_id": trace_id,
                "latency_ms": latency,
                **dict(zip(names, key, strict=True)),
            }
            for trace_id, key, latency in page
        ]
