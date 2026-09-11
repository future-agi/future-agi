"""
v2 SpanList query builder — targets the CH 25.3 spans schema.

Share legacy filter/pagination machinery, but override the physical identity
and latest-state boundaries for CH25's service/observation/start-hour key.
`V2RewriteMixin` translates the resulting SQL at the public build boundary.

The eval and annotation queries (`build_eval_query`, `build_annotation_query`)
target non-span tables, so both are excluded via `_v2_rewrite_exclude`.
`build_eval_query` follows the independently configured authoritative eval
table on the CH25 connection; `build_annotation_query` retains its own source.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tracer.services.clickhouse.query_builders.filters import normalize_filter_op
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    _parts,
    partition_span_filter_plans,
)
from tracer.services.clickhouse.query_builders.span_list import (
    SpanListQueryBuilder,
    _unix_microseconds,
)
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)


class SpanListQueryBuilderV2(V2RewriteMixin, SpanListQueryBuilder):
    """Drop-in v2 SpanList builder.

    Callers can swap import lines:
        v1: from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
        v2: from tracer.services.clickhouse.v2.query_builders.span_list  import SpanListQueryBuilderV2

    Or the dispatch layer can route per-query-type via the shadow harness
    (tracer/services/clickhouse/v2/shadow.py) so v1 and v2 run in parallel
    until the operator promotes the query type to v2_primary or v2_only.
    """

    _v2_rewrite_exclude = frozenset({"build_eval_query", "build_annotation_query"})

    # Use the v2 filter compiler so filters read the v2 dimension tables
    # (end_users, etc.) instead of the dropped legacy CDC tables.
    _FILTER_BUILDER_CLS = ClickHouseFilterBuilderV2
    _NORMAL_TIME_WHERE = (
        "AND start_time >= %(start_date)s AND start_time < %(end_date)s"
    )

    _FILTER_SOURCE_SCOPE = "WHERE"
    _FILTER_SEED_PREDICATE_SCOPE = "AND"
    _FILTER_ORDER_FIELDS = (
        "id",
        "trace_id",
        "project_id",
        "observation_type",
        "service_name",
    )
    _FILTER_READ_SETTINGS = (
        "SETTINGS optimize_move_to_prewhere = 0, "
        "optimize_move_to_prewhere_if_final = 0, "
        # PREWHERE controls alone do not stop outer exact-time predicates
        # being merged into the source and pruning a corrected replacement.
        # The boundary-hour RMT fixture covers this CH25 optimizer behavior.
        "enable_optimize_predicate_expression_to_final_subquery = 0, "
        "query_plan_merge_expressions = 0"
    )
    CONTENT_IDENTITY_FIELDS = (
        "project_id",
        "trace_id",
        "id",
        "_span_start_hour",
        "observation_type",
        "service_name",
    )

    def supports_filter_population_time_discovery(self):
        """Thin raw-column gap discovery, independent of statement abort caps.

        A latest matching span must have a physical timestamp in this raw
        population. Stale versions/tombstones only add false positives. Only
        compiler-proven necessary raw witnesses may narrow this proof;
        project-version, deletion and post-FINAL-only predicates must not.
        Positive Score seeds already cover their requested window efficiently.
        """
        start, end = self._bounded_request_window
        return bool(
            end - start > timedelta(hours=1)
            and not self._bounded_anchor_probe
            and self._bounded_sampling_rate is None
            and not self.sort_params
            and not self.supports_filter_candidate_seed_page()
        )

    def _filter_population_plans(self):
        plans, _ = partition_span_filter_plans(self.filters)
        return [
            plan
            for plan in plans
            if self._filter_population_plan_predicate(plan, ordinary_seed=True)
        ]

    def recommended_filter_cursor_seed_batch_size(self):
        # A one-row preview still needs to reject nonmatching raw-key witnesses.
        # Two-candidate pages repeatedly reread the same immutable primary range.
        # Keep that working set finite and small; publication and the has-more
        # sentinel still use the caller's page size after exact classification.
        if self.supports_filter_population_time_discovery() and any(
            plan.raw_witness_predicate == plan.raw_key_witness_predicate
            for plan in self._filter_population_plans()
        ):
            return 32
        return None

    def recommended_filter_cursor_adaptive_seed_batch_size(self):
        # Rejection-heavy typed-key streams should not repeatedly scan the
        # same hour in page-sized batches. The selector grows only after a
        # complete low-yield batch and caps acquisition at its existing
        # classifier working set. Public page size and exact replay stay fixed.
        if self.recommended_filter_cursor_seed_batch_size() is not None:
            return 200
        return None

    def _uses_thin_text_population_discovery(self):
        # Text Map values can dwarf timestamp reads. Only change acquisition
        # for explicit scalar positive text ANDs; exact seed/replay is unchanged.
        configs = [
            _parts(item)[1]
            for item in self.filters
            if not self.is_datetime_filter(item)
        ]
        return bool(configs) and all(
            str(cfg.get("col_type") or cfg.get("colType") or "").upper()
            == "SPAN_ATTRIBUTE"
            and (cfg.get("filter_type") or cfg.get("filterType")) in {"text", "string"}
            and normalize_filter_op(
                str(cfg.get("filter_op") or cfg.get("filterOp") or "")
            )
            in {"equals", "in"}
            and (
                (
                    value_types := cfg.get(
                        "attribute_value_types", cfg.get("attributeValueTypes")
                    )
                ) is None
                or (
                    isinstance(
                        values := cfg.get("filter_value", cfg.get("filterValue")), list
                    )
                    and bool(values)
                    and value_types == ["string"] * len(values)
                )
            )
            for cfg in configs
        )

    def recommended_filter_population_time_discovery_window(self):
        # Complete necessary-witness absence proofs avoid empty daily seeds.
        # Thin text/time probes stay daily; other equality/IN witnesses reuse
        # compiler-proven raw values and other supported leaves keep presence.
        start, end = self._bounded_request_window
        return (
            end - start
            if self._filter_population_plans()
            and not self._uses_thin_text_population_discovery()
            else timedelta(hours=24)
        )

    def _mixed_population_plans(self):
        # Discovery needs necessary witnesses, not the full conjunction.
        # Avoid loading a potentially large text Map just to locate an hour;
        # seed acquisition and latest-state replay still apply every leaf.
        raw_leaves = [
            (item, cfg)
            for item in self.filters
            if str(
                (cfg := _parts(item)[1]).get("col_type") or cfg.get("colType") or ""
            ).upper()
            == "SPAN_ATTRIBUTE"
        ]
        if not any(
            (cfg.get("filter_type") or cfg.get("filterType")) in {"text", "string"}
            for _, cfg in raw_leaves
        ):
            return []
        # Keep every cheap necessary conjunct: using just one common value
        # repeatedly visits hours with no joint match when results are sparse.
        # Compile together so each predicate retains distinct parameter names.
        plans, _ = partition_span_filter_plans([
            item for item, cfg in raw_leaves
            if (cfg.get("filter_type") or cfg.get("filterType")) in {"number", "boolean"}
            # Never extract one branch from a picker OR across physical Maps.
            and cfg.get("attribute_value_types", cfg.get("attributeValueTypes")) is None
        ])
        return [
            plan for plan in plans
            if not plan.exclude_group_matches
            and self._filter_population_plan_predicate(plan, ordinary_seed=True)
        ]

    def recommended_filter_population_time_discovery_windows(self):
        """Try adjacent recent ranges before a costly complete-year key scan.

        Each completed NULL advances only its own proven interval. Thin text
        probes retain daily widths; witness-filtered probes eventually widen
        to the remaining request. Neither policy truncates older history.
        """
        if not self._filter_population_plans():
            return None
        start, end = self._bounded_request_window
        width = end - start
        if self._uses_thin_text_population_discovery():
            return (min(width, timedelta(days=1)),)
        return tuple(
            dict.fromkeys(
                (min(width, timedelta(days=7)), min(width, timedelta(days=28)), width)
            )
        )

    def build_filter_population_time_discovery_query(self, *, slice_start, slice_end):
        start, end = self._bounded_request_window
        if not start <= slice_start < slice_end <= end:
            raise ValueError(
                "span population discovery must stay inside request window"
            )
        if (
            slice_end - slice_start
            > self.recommended_filter_population_time_discovery_window()
        ):
            raise ValueError("span population discovery exceeds its qualified window")
        if not self.supports_filter_population_time_discovery():
            raise ValueError("span population discovery is unavailable")
        population_plans = self._filter_population_plans()
        if self._uses_thin_text_population_discovery():
            # NULL proves only this adjacent day. Every raw hit replays its
            # complete physical hour; stale/missing values only add work.
            population_plans = []
        elif witnesses := self._mixed_population_plans():
            population_plans = witnesses
        key_scope = (
            "WHERE "
            + " AND ".join(
                f"({self._filter_population_plan_predicate(plan, ordinary_seed=True)})"
                for plan in population_plans
            )
            if population_plans
            else ""
        )
        params = {
            **self.params,
            "population_start_us": _unix_microseconds(slice_start),
            "population_end_us": _unix_microseconds(slice_end),
        }
        for plan in population_plans:
            params.update(
                {
                    key: value
                    for key, value in plan.params.items()
                    if f"%({key})s" in key_scope
                }
            )
        return (
            f"""
            SELECT maxOrNull(toUnixTimestamp64Micro(start_time)) AS newest_raw_time_us
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              AND start_time >= fromUnixTimestamp64Micro(%(population_start_us)s)
              AND start_time < fromUnixTimestamp64Micro(%(population_end_us)s)
            {key_scope}
        """,
            params,
        )

    @staticmethod
    def _start_hour(value):
        if not isinstance(value, datetime):
            raise ValueError("CH25 span identity requires a datetime")
        value = (
            value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        )
        return value.replace(minute=0, second=0, microsecond=0)

    def bounded_filter_row_identity(self, row):
        project = str(row.get("project_id") or self.project_id or "")
        if (
            not project
            or not row.get("trace_id")
            or not row.get("id")
            or row.get("observation_type") is None
            or row.get("service_name") is None
        ):
            raise ValueError("CH25 span requires its complete physical identity")
        allowed = (
            set(self.project_ids or ())
            if self.project_ids is not None
            else {str(self.project_id)}
        )
        if project not in allowed:
            raise ValueError("CH25 span identity escaped request scope")
        return (
            project,
            str(row["trace_id"]),
            str(row["id"]),
            self._start_hour(row["start_time"]),
            str(row["observation_type"]),
            str(row["service_name"]),
        )

    @staticmethod
    def bounded_filter_row_order_token(row):
        if row.get("observation_type") is None or row.get("service_name") is None:
            raise ValueError("CH25 span order requires observation type and service")
        return tuple(
            str(row.get(field, ""))
            for field in SpanListQueryBuilderV2._FILTER_ORDER_FIELDS
        )

    def _normalize_span_identities(self, span_identities):
        if span_identities is None:
            return None
        result = []
        for identity in span_identities:
            if len(identity) != 6:
                raise ValueError("CH25 content requires six-part physical identities")
            project, trace, span, hour, observation, service = identity
            result.append(
                self.bounded_filter_row_identity(
                    {
                        "project_id": project,
                        "trace_id": trace,
                        "id": span,
                        "start_time": hour,
                        "observation_type": observation,
                        "service_name": service,
                    }
                )
            )
        return tuple(dict.fromkeys(result))

    def _filter_identity_columns_sql(self):
        return (
            "project_id, trace_id, id, toStartOfHour(start_time), "
            "observation_type, service_name"
        )

    def _filter_seed_extra_columns_sql(self):
        return ", observation_type, service_name, _version"

    def _filter_order_sql(self, direction="older"):
        order = "DESC" if direction == "older" else "ASC"
        return (
            super()._filter_order_sql(direction)
            + f", observation_type {order}, service_name {order}"
        )

    def _filter_seed_plan_predicate(self, plan, *, ordinary_seed):
        # This outer WHERE runs after all versions in each physical hour have
        # crossed FINAL. Do not substitute it into raw prefix/time discovery,
        # whose necessary key witnesses and complete replay remain unchanged.
        if ordinary_seed and plan.post_final_scalar_seed_predicate is not None:
            return f"({plan.post_final_scalar_seed_predicate})"
        return super()._filter_seed_plan_predicate(plan, ordinary_seed=ordinary_seed)

    def _filter_population_plan_predicate(self, plan, *, ordinary_seed):
        # Equality/IN already have a typed raw value witness. Other scalar
        # leaves retain compiler key-only/absent metadata, never a promoted
        # post-FINAL predicate. Prefix and time discovery share this policy.
        if ordinary_seed and plan.raw_key_witness_predicate:
            return plan.raw_witness_predicate
        return super()._filter_population_plan_predicate(
            plan, ordinary_seed=ordinary_seed
        )

    def _filter_seed_source_sql(self, *, raw_key_predicate=""):
        # A raw timestamp is not an order bound for its replacement. Resolve
        # the complete boundary hours before applying slice/keyset/LIMIT or
        # mutable predicates. Only immutable key restrictions enter FINAL.
        return self._latest_window_source_sql(
            "filter_slice", raw_key_predicate=raw_key_predicate
        )

    def _filter_anchor_source_sql(self):
        return self._latest_window_source_sql("filter_anchor")

    def _latest_window_source_sql(self, prefix, *, raw_key_predicate=""):
        population_scope = ""
        if raw_key_predicate:
            # A latest matching span must have a raw version satisfying these
            # compiler-proven necessary raw witnesses. Select
            # immutable primary prefixes, then replay ALL versions in each
            # selected prefix with FINAL. Never filter mutable values, deletion
            # or exact timestamps out of the replacement input. A stale value
            # or tombstone only adds work; it cannot become a public match.
            # Prefixes (rather than every matching ID) also keep the IN set
            # proportional to active service/observation hours. No LIMIT or
            # sampling may truncate this population proof.
            population_scope = f"""
              AND (project_id, observation_type, service_name, toStartOfHour(start_time)) IN (
                  SELECT DISTINCT project_id, observation_type, service_name, toStartOfHour(start_time)
                  FROM {self.TABLE}
                  PREWHERE {self.project_filter_sql()}
                    AND toStartOfHour(start_time) >= toStartOfHour(fromUnixTimestamp64Micro(%({prefix}_start_us)s))
                    AND toStartOfHour(start_time) <= toStartOfHour(fromUnixTimestamp64Micro(%({prefix}_end_us)s - 1))
                  WHERE {raw_key_predicate}
              )
            """
        return f"""(
            SELECT * FROM {self.TABLE} FINAL
            PREWHERE {self.project_filter_sql()}
              AND toStartOfHour(start_time) >= toStartOfHour(fromUnixTimestamp64Micro(%({prefix}_start_us)s))
              AND toStartOfHour(start_time) <= toStartOfHour(fromUnixTimestamp64Micro(%({prefix}_end_us)s - 1))
              {population_scope}
        ) AS latest_seed_spans"""

    def _normal_span_source_sql(self):
        return f"""(
            SELECT * FROM {self.TABLE} FINAL
            PREWHERE {self.project_filter_sql()}
              AND toStartOfHour(start_time) >= toStartOfHour(toDateTime64(%(start_date)s, 6, 'UTC'))
              AND toStartOfHour(start_time) <= toStartOfHour(toDateTime64(%(end_date)s, 6, 'UTC'))
        ) AS latest_list_spans"""

    def _normal_span_identity_extra_sql(self):
        return ", service_name, _version"

    def _normal_span_order_sql(self, order):
        return (
            order
            + ", observation_type DESC, service_name DESC, id DESC, trace_id DESC, project_id DESC"
        )

    def _filter_candidate_scope_sql(self, identities, params):
        identities = self._normalize_span_identities(identities)
        params["candidate_span_identities"] = tuple(
            (project, trace, span, _unix_microseconds(hour), observation, service)
            for project, trace, span, hour, observation, service in identities
        )
        # Expose the native sparse-key expressions without replacing the full
        # physical-identity fence. Every version of each identity has the same
        # prefix, including tombstones and corrected timestamps in its hour.
        # _normalize_span_identities has already floored in UTC. Bind naive UTC
        # literals so driver formatting cannot shift them to the server zone.
        params["candidate_span_primary_prefixes"] = tuple(
            dict.fromkeys(
                (observation, service, hour.replace(tzinfo=None), trace)
                for _, trace, _, hour, observation, service in identities
            )
        )
        return """
              AND (observation_type, service_name, toStartOfHour(start_time), trace_id)
                  IN %(candidate_span_primary_prefixes)s
              AND (
                  toString(project_id), trace_id, id,
                  toUnixTimestamp64Micro(toDateTime64(toStartOfHour(start_time), 6, 'UTC')),
                  observation_type, service_name
              ) IN %(candidate_span_identities)s
        """

    def _filter_match_source_sql(self, candidate_scope):
        # FINAL chooses a single complete row, including NULLs and conflicting
        # equal-version payloads, before the shared compiler's aggregates run.
        return f"""(
            SELECT * FROM {self.TABLE} FINAL
            PREWHERE {self.project_filter_sql()}
              AND id IN %(candidate_span_ids)s
              {candidate_scope}
        ) AS latest_candidate_spans"""

    def _filter_match_outer_scope_sql(self, candidate_scope):
        return ""

    def _filter_match_identity_fragments(self):
        select = ", latest_service_name AS service_name, latest_version AS _version"
        aggregates = ", argMax(service_name, _version) AS latest_service_name, max(_version) AS latest_version"
        if self._bounded_identity_only:
            select += ", latest_observation_type AS observation_type"
            aggregates += (
                ", argMax(observation_type, _version) AS latest_observation_type"
            )
        return select, aggregates

    def _filter_match_limit_sql(self, limit, identities, explicit_limit):
        # A bare external ID can denote multiple physical spans. Only an
        # explicit navigation ambiguity sentinel may truncate that population.
        return (
            f"LIMIT {limit}"
            if identities is not None or explicit_limit is not None
            else ""
        )

    def build_content_query(self, span_ids, *, span_identities=None):
        identities = self._normalize_span_identities(span_identities)
        ids = tuple(dict.fromkeys(str(value) for value in span_ids if value))
        if not ids or identities == ():
            return "", {}
        if identities is not None and any(
            identity[2] not in ids for identity in identities
        ):
            raise ValueError("CH25 content identity escaped requested span IDs")
        params = {**self.params, "candidate_span_ids": ids}
        scope = (
            self._filter_candidate_scope_sql(identities, params)
            if identities is not None
            else ""
        )
        project_version = ""
        if self.project_version_id:
            params["project_version_id"] = self.project_version_id
            project_version = "AND project_version_id = %(project_version_id)s"
        # Exact identities are immutable full-hour coordinates. Do not reuse
        # an exact timestamp or a request +/- day filter before replacement.
        return (
            f"""
        SELECT project_id, trace_id, id, start_time, observation_type, service_name,
               _version, input, output, attributes_extra,
               attrs_string, attrs_number, attrs_bool
        FROM {self._filter_match_source_sql(scope)}
        WHERE is_deleted = 0 {project_version}
        {self._FILTER_READ_SETTINGS}
        """,
            params,
        )


__all__ = ["SpanListQueryBuilderV2"]
