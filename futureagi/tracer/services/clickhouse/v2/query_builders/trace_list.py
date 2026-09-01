"""
v2 TraceList query builder — targets the CH 25.3 spans schema.

Same pattern as v2/span_list.py: SUBCLASS the v1 builder, rewrite the
compiled SQL output. The v1 TraceList builder reads from `spans` (legacy
24.10 columns) plus joins to `tracer_eval_logger` and `model_hub_score`.

`V2RewriteMixin` routes every inherited `build*` method's SQL through the v2
rewriter at one boundary (no per-method overrides). The only locally-defined
method is `build_count_query`, which carries a rollup fast-path; its SQL is
rewritten by the mixin just like every other.

`build_eval_query` / `build_annotation_query` are excluded from the span-column
rewrite. The eval query follows the independently configured authoritative
table on the CH25 connection; annotations retain their own source boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.read_budget import ReadDeadline, ReadDeadlineExceeded
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)


@dataclass(frozen=True)
class BoundedUserResolution:
    """Final page-scoped user labels plus the number of physical CH reads."""

    data: list[dict[str, str]]
    query_count: int


MAX_USER_PHYSICAL_IDENTITIES_PER_PAGE = 4_096


class UserEnrichmentLimitExceeded(ReadDeadlineExceeded):
    """A page's remap fan-in exceeded the optional enrichment read bound."""


class TraceListQueryBuilderV2(V2RewriteMixin, TraceListQueryBuilder):
    """Drop-in v2 TraceList builder.

    Callers swap one import line:
        v1: from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
        v2: from tracer.services.clickhouse.v2.query_builders.trace_list  import TraceListQueryBuilderV2

    Or route via the shadow harness in v2/shadow.py.
    """

    _v2_rewrite_exclude = frozenset(
        {
            "build_eval_query",
            "build_eval_replay_query",
            "build_annotation_query",
        }
    )

    # Use the v2 filter compiler so filters read the v2 dimension tables
    # (end_users, etc.) instead of the dropped legacy CDC tables.
    _FILTER_BUILDER_CLS = ClickHouseFilterBuilderV2

    @staticmethod
    def filter_classifier_has_exact_start_time_identity() -> bool:
        """CH25 replacement identity uses start hour, not exact producer time."""

        return False

    @staticmethod
    def filter_classifier_physical_group_by(*, org_scope: bool) -> str:
        """Collapse by the complete deployed CH25 ReplacingMergeTree key."""

        project_prefix = "project_id, " if org_scope else ""
        return (
            f"{project_prefix}observation_type, service_name, "
            "toStartOfHour(start_time), trace_id, id"
        )

    def build_span_attributes_query(
        self,
        trace_ids: list[str],
        attribute_keys: Iterable[str] | None = None,
        *,
        trace_identities: Iterable[tuple[str, str]] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Hydrate the latest live value of each requested key on a trace page.

        A trace's physical span fanout is unbounded. Packing every span's full
        maps into one ``groupArray`` merely hides that fanout from the result-row
        limit while retaining unbounded ClickHouse/Python memory. This query
        projects only requested keys and deterministically selects one value per
        ``(project, trace, key)``: the value on the latest live physical span by
        ``(start_time, id)``. ReplacingMergeTree versions are collapsed before
        tombstones and key presence are evaluated. Therefore result cardinality
        is bounded by ``page trace identities * requested keys`` regardless of
        historical span/value fanout, without sampling or truncation.

        It intentionally does not reuse the list's date window: child spans can
        start more than a day after their root. Exact page-scoped project/trace
        pairs are the membership boundary, including in organization mode where
        trace ids are customer controlled and can collide across tenants.
        """

        normalized_trace_ids = tuple(
            dict.fromkeys(str(trace_id) for trace_id in trace_ids if trace_id)
        )
        requested_keys = tuple(
            dict.fromkeys(str(key) for key in (attribute_keys or ()) if key)
        )
        if not normalized_trace_ids or not requested_keys:
            return "", {}

        if trace_identities is None:
            if self.project_ids is not None:
                raise ValueError(
                    "multi-project attribute hydration requires exact trace identities"
                )
            if not self.project_id:
                raise ValueError("attribute hydration requires a project identity")
            normalized_trace_identities = tuple(
                (str(self.project_id), trace_id) for trace_id in normalized_trace_ids
            )
        else:
            normalized_trace_identities = tuple(
                dict.fromkeys(
                    (str(candidate_project_id), str(candidate_trace_id))
                    for candidate_project_id, candidate_trace_id in trace_identities
                    if candidate_project_id and candidate_trace_id
                )
            )
            requested_trace_id_set = set(normalized_trace_ids)
            allowed_project_ids = (
                set(self.project_ids or ())
                if self.project_ids is not None
                else {str(self.project_id)}
            )
            if not normalized_trace_identities or any(
                candidate_project_id not in allowed_project_ids
                or candidate_trace_id not in requested_trace_id_set
                for candidate_project_id, candidate_trace_id in normalized_trace_identities
            ):
                raise ValueError("attribute hydration identities escaped request scope")

        params: dict[str, Any] = {
            **self.params,
            "attr_trace_identities": normalized_trace_identities,
            # clickhouse-driver renders a single-element tuple as a scalar
            # String. ARRAY JOIN requires an Array even when only one key was
            # requested, so bind the de-duplicated, insertion-ordered keys as
            # a list rather than a tuple.
            "requested_attribute_keys": list(requested_keys),
        }
        query = f"""
        SELECT
            toString(project_id) AS project_id,
            trace_id,
            attribute_key,
            argMax(candidate_attribute_value_json, tuple(start_time, id))
                AS attribute_value_json
        FROM (
            SELECT
                project_id,
                trace_id,
                id,
                start_time,
                attribute_key,
                multiIf(
                    notEmpty(JSONExtractRaw(latest_attributes_extra, attribute_key)),
                        JSONExtractRaw(latest_attributes_extra, attribute_key),
                    mapContains(latest_attrs_bool, attribute_key),
                        if(latest_attrs_bool[attribute_key] != 0, 'true', 'false'),
                    mapContains(latest_attrs_number, attribute_key),
                        if(
                            isFinite(latest_attrs_number[attribute_key]),
                            toString(latest_attrs_number[attribute_key]),
                            'null'
                        ),
                    mapContains(latest_attrs_string, attribute_key),
                        toJSONString(latest_attrs_string[attribute_key]),
                    ''
                ) AS candidate_attribute_value_json
            FROM (
                SELECT
                    project_id,
                    trace_id,
                    id,
                    start_time,
                    argMax(tuple(attributes_extra), _version).1
                        AS latest_attributes_extra,
                    argMax(attrs_string, _version) AS latest_attrs_string,
                    argMax(attrs_number, _version) AS latest_attrs_number,
                    argMax(attrs_bool, _version) AS latest_attrs_bool,
                    argMax(is_deleted, _version) AS latest_is_deleted
                FROM {self.TABLE}
                PREWHERE (toString(project_id), trace_id)
                    IN %(attr_trace_identities)s
                  AND {self.project_filter_sql()}
                GROUP BY project_id, trace_id, id, start_time
            ) AS latest_physical_spans
            ARRAY JOIN %(requested_attribute_keys)s AS attribute_key
            WHERE latest_is_deleted = 0
        ) AS projected_attribute_values
        WHERE notEmpty(candidate_attribute_value_json)
        GROUP BY project_id, trace_id, attribute_key
        """
        return query, params

    @staticmethod
    def _trace_tags_select_sql() -> str:
        """Project tags from the bounded latest trace row, without a dictionary."""

        return "ifNull(nullIf(latest_trace_tags, ''), '[]') AS trace_tags"

    def _trace_tags_join_sql(self) -> str:
        """Resolve trace tags directly from the CH25 ``traces`` table.

        The page identity already limits this read to at most the requested
        trace IDs. Collapse those rows by the ReplacingMergeTree version,
        discard a latest tombstone, and join on both tenant and trace identity.
        This preserves the dictionary's missing-row ``[]`` contract while
        avoiding a runtime ``dictGet`` privilege dependency.
        """

        return f"""
        LEFT ANY JOIN (
            SELECT
                project_id AS trace_tags_project_id,
                toString(id) AS trace_tags_trace_id,
                argMax(tags, _version) AS latest_trace_tags,
                argMax(is_deleted, _version) AS latest_trace_is_deleted
            FROM traces
            PREWHERE {self.project_filter_sql()}
              AND id IN %(content_trace_ids)s
            GROUP BY project_id, id
            HAVING latest_trace_is_deleted = 0
        ) AS latest_trace_tags_rows
          ON latest_physical_roots.project_id = trace_tags_project_id
         AND latest_physical_roots.trace_id = trace_tags_trace_id
        """

    def build_user_id_query(
        self,
        trace_ids: list[str],
        *,
        trace_identities: Iterable[tuple[str, str]] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build phase one of bounded, dictionary-free user enrichment.

        Return at most one target row per supplied ``(project_id, trace_id)``.
        The selected page users are frozen into one scalar array, then the remap
        lookup discovers and expands only consolidation groups touched by those
        IDs. The result freezes each trace's canonical id and the finite physical
        ids phase two may inspect without a tenant-global remap aggregate.

        ``trace_identities`` is authoritative when supplied.  It is mandatory
        for an organization-scoped page because trace text is not globally
        unique across projects.  The trace-only form remains for the single-
        project builder contract and direct callers.
        """

        page_trace_ids = tuple(
            dict.fromkeys(str(value) for value in trace_ids if value)
        )
        page_trace_identities = tuple(
            dict.fromkeys(
                (str(project_id), str(trace_id))
                for project_id, trace_id in (trace_identities or ())
                if project_id and trace_id
            )
        )
        target_count = (
            len(page_trace_identities) if page_trace_identities else len(page_trace_ids)
        )
        if target_count == 0:
            return "", {}
        if self.page_size <= 0 or target_count > self.page_size:
            raise ValueError(
                "user enrichment trace identities exceed the bounded page size"
            )

        params: dict[str, Any] = dict(self.params)
        if page_trace_identities:
            params["user_trace_identities"] = page_trace_identities
            page_filter = "(sp.project_id, sp.trace_id) IN %(user_trace_identities)s"
        else:
            params["user_trace_ids"] = page_trace_ids
            page_filter = (
                f"{self.project_filter_sql('sp')} AND sp.trace_id IN %(user_trace_ids)s"
            )
        span_window = self._span_time_window(params, column="sp.start_time")

        query = f"""
        WITH
        (
            SELECT groupArray(tuple(project_id, trace_id, selected_end_user_id))
            FROM (
                SELECT
                    project_id,
                    trace_id,
                    assumeNotNull(
                        argMax(
                            tuple(latest_end_user_id),
                            tuple(start_time, id)
                        ).1
                    ) AS selected_end_user_id
                FROM (
                    SELECT
                        sp.project_id,
                        sp.trace_id,
                        sp.id,
                        sp.start_time,
                        argMax(tuple(sp.end_user_id), sp._version).1
                            AS latest_end_user_id,
                        argMax(sp.is_deleted, sp._version) AS latest_is_deleted
                    FROM spans AS sp
                    PREWHERE {page_filter}
                      {span_window}
                    GROUP BY sp.project_id, sp.trace_id, sp.id, sp.start_time
                ) AS latest_page_spans
                WHERE latest_is_deleted = 0
                  AND latest_end_user_id IS NOT NULL
                  AND latest_end_user_id !=
                      toUUID('00000000-0000-0000-0000-000000000000')
                GROUP BY project_id, trace_id
            ) AS selected_page_users
        ) AS page_trace_user_rows,
        latest_trace_users AS (
            SELECT
                tupleElement(page_user, 1) AS project_id,
                tupleElement(page_user, 2) AS trace_id,
                tupleElement(page_user, 3) AS selected_end_user_id
            FROM (
                SELECT arrayJoin(page_trace_user_rows) AS page_user
            )
        ),
        page_end_user_group_ids AS (
            SELECT DISTINCT remap_match.new_id
            FROM end_user_id_remap AS remap_match FINAL
            WHERE remap_match.old_id IN (
                SELECT selected_end_user_id FROM latest_trace_users
            )
               OR remap_match.new_id IN (
                SELECT selected_end_user_id FROM latest_trace_users
            )
        ),
        remap_lookup AS (
            SELECT
                any_id,
                argMin(
                    group_survivor_end_user_id,
                    tuple(toString(group_survivor_end_user_id), toString(new_id))
                ) AS survivor_end_user_id,
                argMin(
                    all_physical_end_user_ids,
                    tuple(toString(group_survivor_end_user_id), toString(new_id))
                ) AS physical_end_user_ids
            FROM (
                SELECT
                    new_id,
                    argMin(old_id, toString(old_id)) AS group_survivor_end_user_id,
                    arrayDistinct(
                        arrayPushBack(
                            groupUniqArray({MAX_USER_PHYSICAL_IDENTITIES_PER_PAGE + 1})(
                                old_id
                            ),
                            new_id
                        )
                    ) AS all_physical_end_user_ids
                FROM end_user_id_remap FINAL
                WHERE new_id IN (SELECT new_id FROM page_end_user_group_ids)
                GROUP BY new_id
            )
            ARRAY JOIN all_physical_end_user_ids AS any_id
            GROUP BY any_id
        ),
        resolved_trace_users AS (
            SELECT
                trace_users.project_id,
                trace_users.trace_id,
                trace_users.selected_end_user_id,
                if(
                    remap.any_id =
                        toUUID('00000000-0000-0000-0000-000000000000'),
                    trace_users.selected_end_user_id,
                    remap.survivor_end_user_id
                ) AS resolved_end_user_id,
                if(
                    remap.any_id =
                        toUUID('00000000-0000-0000-0000-000000000000'),
                    [trace_users.selected_end_user_id],
                    remap.physical_end_user_ids
                ) AS physical_end_user_ids
            FROM latest_trace_users AS trace_users
            LEFT ANY JOIN remap_lookup AS remap
              ON trace_users.selected_end_user_id = remap.any_id
        )
        SELECT
            toString(project_id) AS project_id,
            toString(trace_id) AS trace_id,
            toString(resolved_end_user_id) AS resolved_end_user_id,
            arrayMap(
                value -> toString(value),
                physical_end_user_ids
            ) AS physical_end_user_ids
        FROM resolved_trace_users
        """
        return query, params

    def build_user_dimension_query(
        self, target_rows: Iterable[Mapping[str, Any]]
    ) -> tuple[str, dict[str, Any]]:
        """Build phase two with an explicit finite composite identity predicate."""

        identities = tuple(
            sorted(
                {
                    (str(row.get("project_id", "")), str(physical_id))
                    for row in target_rows
                    for physical_id in (row.get("physical_end_user_ids") or ())
                    if row.get("project_id") and physical_id
                }
            )
        )
        if not identities:
            return "", {}
        if len(identities) > MAX_USER_PHYSICAL_IDENTITIES_PER_PAGE:
            raise UserEnrichmentLimitExceeded(
                "user enrichment physical identity limit exceeded"
            )

        query = """
        SELECT
            toString(eu.project_id) AS project_id,
            toString(eu.end_user_id) AS end_user_id,
            eu.user_id AS user_id,
            eu.version AS version
        FROM end_users AS eu FINAL
        PREWHERE (eu.project_id, eu.end_user_id)
            IN %(user_physical_identities)s
        WHERE eu.is_deleted = 0
        """
        return query, {
            **self.params,
            "user_physical_identities": identities,
        }

    def resolve_user_ids_for_trace_identities(
        self,
        trace_identities: Iterable[tuple[str, str]],
        analytics,
        *,
        timeout_ms: int = 10_000,
        settings: dict[str, Any] | None = None,
        timeout_ms_provider: Callable[[], int] | None = None,
    ) -> BoundedUserResolution:
        """Execute the two page-bounded reads and merge by tenant + trace.

        Phase one scans the selected spans once and the remap table once.  Its
        finite result is frozen into the explicit composite identity predicate
        used by phase two, allowing ClickHouse to prune ``end_users`` by its
        physical primary key.  Keeping the project in the returned key prevents
        same-text trace ids in two organization projects from overwriting or
        leaking labels.
        """

        identities = tuple(dict.fromkeys(trace_identities))
        if not identities:
            return BoundedUserResolution(data=[], query_count=0)

        local_deadline = ReadDeadline.start(timeout_ms)

        def remaining_ms() -> int:
            if timeout_ms_provider is not None:
                return timeout_ms_provider()
            return local_deadline.remaining_ms()

        target_query, target_params = self.build_user_id_query(
            [trace_id for _, trace_id in identities],
            trace_identities=identities,
        )
        target_result = analytics.execute_ch_query(
            target_query,
            target_params,
            timeout_ms=remaining_ms(),
            settings=settings,
        )
        target_rows = target_result.data
        dimension_query, dimension_params = self.build_user_dimension_query(target_rows)
        if not dimension_query:
            return BoundedUserResolution(data=[], query_count=1)

        dimension_result = analytics.execute_ch_query(
            dimension_query,
            dimension_params,
            timeout_ms=remaining_ms(),
            settings=settings,
        )
        live_users = {
            (str(row.get("project_id", "")), str(row.get("end_user_id", ""))): row
            for row in dimension_result.data
            if row.get("user_id")
        }

        resolved_rows: list[dict[str, str]] = []
        for target in target_rows:
            project_id = str(target.get("project_id", ""))
            trace_id = str(target.get("trace_id", ""))
            resolved_id = str(target.get("resolved_end_user_id", ""))
            candidates = [
                live_users[(project_id, str(physical_id))]
                for physical_id in (target.get("physical_end_user_ids") or ())
                if (project_id, str(physical_id)) in live_users
            ]
            if not candidates:
                continue
            selected = max(
                candidates,
                key=lambda row: (
                    str(row.get("end_user_id", "")) == resolved_id,
                    row.get("version"),
                    str(row.get("end_user_id", "")),
                ),
            )
            resolved_rows.append(
                {
                    "project_id": project_id,
                    "trace_id": trace_id,
                    "user_id": str(selected["user_id"]),
                }
            )

        return BoundedUserResolution(data=resolved_rows, query_count=2)

    def build_count_query(self) -> tuple[str, dict[str, Any]]:
        """Pagination count.

        Fast path: when no per-row filter / search / project-version is set,
        read from the pre-aggregated ``trace_count_rollup`` (schema 012). The
        rollup keys on (project_id, hour) and stores ``uniqExactState(trace_id)``
        for root spans, so the count over any time window is O(buckets).

        Empirically: on the 78K-span dev dataset this drops the count from
        ~20ms (raw uniq over spans) to ~3ms. At trillion-row prod scale the
        raw path scales linearly with row count while the rollup stays
        O(hours × projects); the rollup is the only path that survives.

        Slow path (with filters): fall back to v1's uniq over spans. The
        rollup can't answer filtered counts because it doesn't know about
        attribute-level filter predicates.
        """
        # Fast-path: rollup-backed count is safe whenever the only filters
        # the caller supplied are time bounds (the rollup is itself keyed by
        # hour so the time range applies natively). Search/project_version
        # and any attribute filter still require raw scan.
        non_time_filters = [
            f
            for f in (self.filters or [])
            if (f.get("column_id") or f.get("columnId"))
            not in ("created_at", "start_time")
        ]
        if not non_time_filters and not self.search and not self.project_version_id:
            # Ensure start_date / end_date are bound even if build() wasn't
            # called first (count is sometimes invoked standalone, e.g. for
            # pagination prefetch). parse_time_range honours any time filter
            # the caller passed and defaults to 30d (see base.py).
            start_date, end_date = self.parse_time_range(self.filters or [])
            params = dict(self.params)
            params["start_date"] = start_date
            params["end_date"] = end_date
            # toStartOfHour requires DateTime, not String — explicitly cast
            # the bound %(start_date)s / %(end_date)s. CH's clickhouse-connect
            # binds Python datetime as ISO-8601 String which would otherwise
            # fail toStartOfHour with ILLEGAL_TYPE_OF_ARGUMENT.
            sql = """
        SELECT uniqExactMerge(uniq_traces_state) AS total
        FROM trace_count_rollup
        WHERE project_id = %(project_id)s
          AND hour >= toStartOfHour(toDateTime(%(start_date)s))
          AND hour <  toStartOfHour(toDateTime(%(end_date)s)) + INTERVAL 1 HOUR
            """
            # V2RewriteMixin appends the v2 SETTINGS to the returned SQL.
            return sql, params

        # Slow path: v1's raw uniq over spans; the mixin rewrites + applies SETTINGS.
        return super().build_count_query()


__all__ = [
    "TraceListQueryBuilderV2",
]
