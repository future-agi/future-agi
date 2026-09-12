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

import re
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
    rewrite_v1_sql_to_v2,
)

# Every stored CH25 ``spans`` column, in the declared order of
# ``v2/schema/002_spans_v2.sql`` — that is, exactly the set an ordinary
# ``SELECT *`` returns (MATERIALIZED and ALIAS columns are excluded; no later
# schema file adds a stored column). The latest-state collapse that replaced
# ``FROM spans FINAL`` projects this set verbatim, so no consumer of the former
# source loses a column. ``test_span_latest_state_argmax`` pins it against the
# schema files; a stored column added there must be added here too.
_PHYSICAL_SPAN_COLUMNS = (
    "project_id",
    "observation_type",
    "service_name",
    "start_time",
    "trace_id",
    "id",
    "parent_span_id",
    "name",
    "end_time",
    "latency_ms",
    "org_id",
    "project_version_id",
    "end_user_id",
    "trace_session_id",
    "prompt_version_id",
    "prompt_label_id",
    "custom_eval_config_id",
    "status",
    "status_message",
    "model",
    "provider",
    "gen_ai_system",
    "gen_ai_operation",
    "operation_name",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost",
    "attrs_string",
    "attrs_number",
    "attrs_bool",
    "attributes_extra",
    "resource_attrs",
    "metadata",
    "input",
    "output",
    "input_gcs_url",
    "output_gcs_url",
    "tags",
    "span_events",
    "eval_status",
    "semconv_source",
    "created_at",
    "updated_at",
    "is_deleted",
    "_version",
)

# The deployed ReplacingMergeTree sorting key, in its declared order. The hour
# is a key *expression*, so it is grouped rather than selected; the other five
# are plain key columns and pass through the collapse unchanged.
_PHYSICAL_SPAN_KEY_COLUMNS = (
    "project_id",
    "observation_type",
    "service_name",
    "trace_id",
    "id",
)
_PHYSICAL_SPAN_GROUP_BY_SQL = (
    "project_id, observation_type, service_name, "
    "toStartOfHour(start_time), trace_id, id"
)

# Every consumer of a latest-state source fences on the request/slice window,
# excludes tombstones and carries the version, and the cursor orders on the
# full identity. Project that floor unconditionally; every other stored column
# is projected only when the consuming statement names it, so the collapse
# reads no more than the ``SELECT * FROM spans FINAL`` it replaced (ClickHouse
# prunes unused columns out of a ``SELECT *`` subquery, but cannot prune a
# column that an aggregate names).
_PHYSICAL_SPAN_FLOOR_COLUMNS = frozenset(
    {*_PHYSICAL_SPAN_KEY_COLUMNS, "start_time", "is_deleted", "_version"}
)
_PHYSICAL_SPAN_COLUMN_REFERENCE_RE = re.compile(
    r"\b(" + "|".join(_PHYSICAL_SPAN_COLUMNS) + r")\b"
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
    # The two ``*_final`` suppressions this clause used to carry are gone with
    # the FINAL sources they guarded; they are no-ops on a plain aggregation.
    # The remaining two still hold: PREWHERE controls alone do not stop outer
    # exact-time predicates being merged into the source and pruning a
    # corrected replacement. The boundary-hour RMT fixture covers this CH25
    # optimizer behavior.
    _FILTER_READ_SETTINGS = (
        "SETTINGS optimize_move_to_prewhere = 0, query_plan_merge_expressions = 0"
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
        project-version, deletion and latest-state-only predicates must not.
        Positive Score seeds already cover their requested window efficiently.
        """
        start, end = self._bounded_request_window
        return bool(
            end - start > timedelta(hours=1)
            and not self._bounded_anchor_probe
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
                )
                is None
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
        if (
            self._bounded_sampling_rate is not None
            and not self._filter_population_plans()
        ):
            # The hourly raw population is a complete superset of the sampled
            # task population without collapsing rows to latest state.
            return end - start
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
        plans, _ = partition_span_filter_plans(
            [
                item
                for item, cfg in raw_leaves
                if (cfg.get("filter_type") or cfg.get("filterType"))
                in {"number", "boolean"}
                # Never extract one branch from a picker OR across physical Maps.
                and cfg.get("attribute_value_types", cfg.get("attributeValueTypes"))
                is None
            ]
        )
        return [
            plan
            for plan in plans
            if not plan.exclude_group_matches
            and self._filter_population_plan_predicate(plan, ordinary_seed=True)
        ]

    def recommended_filter_population_time_discovery_windows(self):
        """Try adjacent recent ranges before a costly complete-year key scan.

        Each completed NULL advances only its own proven interval. Thin text
        probes retain daily widths; witness-filtered probes eventually widen
        to the remaining request. Neither policy truncates older history.
        """
        plans = self._filter_population_plans()
        start, end = self._bounded_request_window
        if self._bounded_sampling_rate is not None and not plans:
            return (end - start,)
        if not plans:
            return None
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
        if self._bounded_sampling_rate == 0:
            return (
                "SELECT CAST(NULL AS Nullable(Int64)) AS newest_raw_time_us",
                dict(self.params),
            )
        if self._bounded_sampling_rate is not None and not population_plans:
            # Sampling needs trace/span IDs, which turns a year-scale max-time
            # aggregate into a wide raw scan on dense projects. An unsampled
            # populated hour is a conservative superset: the unchanged seed
            # applies the exact hash and latest-state classifier before use.
            # Grouping in the deployed hourly projection shape keeps this
            # timestamp-only proof cheap without permitting a false absence.
            population_hour_start = slice_start.replace(
                minute=0, second=0, microsecond=0
            )
            population_hour_end = slice_end.replace(minute=0, second=0, microsecond=0)
            if population_hour_end < slice_end:
                population_hour_end += timedelta(hours=1)
            params = {
                **self.params,
                "population_start_us": _unix_microseconds(slice_start),
                "population_hour_start_us": _unix_microseconds(population_hour_start),
                "population_hour_end_us": _unix_microseconds(population_hour_end),
            }
            return (
                f"""
                SELECT if(
                    newest_hour_us IS NULL,
                    CAST(NULL AS Nullable(Int64)),
                    greatest(newest_hour_us, %(population_start_us)s)
                ) AS newest_raw_time_us
                FROM (
                    SELECT maxOrNull(
                        toUnixTimestamp64Micro(
                            toDateTime64(population_hour, 6, 'UTC')
                        )
                    ) AS newest_hour_us
                    FROM (
                        SELECT
                            project_id,
                            toStartOfHour(start_time) AS population_hour,
                            count() AS population_count
                        FROM {self.TABLE}
                        PREWHERE {self.project_filter_sql()}
                        WHERE toStartOfHour(start_time) >=
                            fromUnixTimestamp64Micro(%(population_hour_start_us)s)
                          AND toStartOfHour(start_time) <
                            fromUnixTimestamp64Micro(%(population_hour_end_us)s)
                        GROUP BY project_id, population_hour
                    )
                )
                """,
                params,
            )
        if self._uses_thin_text_population_discovery():
            # NULL proves only this adjacent day. Every raw hit replays its
            # complete physical hour; stale/missing values only add work.
            population_plans = []
        elif witnesses := self._mixed_population_plans():
            population_plans = witnesses
        population_predicates = [
            f"({self._filter_population_plan_predicate(plan, ordinary_seed=True)})"
            for plan in population_plans
        ]
        if self._bounded_sampling_rate is not None:
            # Sampling is stable across physical versions, so stale versions
            # can only add conservative timestamp witnesses.
            population_predicates.append(
                "modulo(cityHash64(%(bounded_sampling_salt)s, "
                "toString(project_id), toString(trace_id), toString(id)), 100) "
                "< %(bounded_sampling_rate)s"
            )
        key_scope = (
            "WHERE " + " AND ".join(population_predicates)
            if population_predicates
            else ""
        )
        params = {
            **self.params,
            "population_start_us": _unix_microseconds(slice_start),
            "population_end_us": _unix_microseconds(slice_end),
        }
        if self._bounded_sampling_rate is not None:
            params.update(
                bounded_sampling_salt=str(self._bounded_sampling_salt),
                bounded_sampling_rate=float(self._bounded_sampling_rate),
            )
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
        # been collapsed. Do not substitute it into raw prefix/time discovery,
        # whose necessary key witnesses and complete replay remain unchanged.
        if ordinary_seed and plan.post_final_scalar_seed_predicate is not None:
            return f"({plan.post_final_scalar_seed_predicate})"
        return super()._filter_seed_plan_predicate(plan, ordinary_seed=ordinary_seed)

    def _filter_population_plan_predicate(self, plan, *, ordinary_seed):
        # Equality/IN already have a typed raw value witness. Other scalar
        # leaves retain compiler key-only/absent metadata, never a promoted
        # latest-state predicate. Prefix and time discovery share this policy.
        if ordinary_seed and plan.raw_key_witness_predicate:
            return plan.raw_witness_predicate
        return super()._filter_population_plan_predicate(
            plan, ordinary_seed=ordinary_seed
        )

    @staticmethod
    def _latest_state_packed_columns(consumer_sql):
        """The stored columns one latest-state winner tuple carries.

        ``argMax(tuple(<packed columns>), _version)`` elects ONE stored row per
        ReplacingMergeTree key, so no published field can come from a different
        version than its neighbours. Equal versions have no storage winner at
        all; packing the columns into a single tuple still forbids assembling a
        row out of two tied versions, which is the property the engine's FINAL
        merge provided and on which the shared compiler's per-column ``argMax``
        aggregates downstream depend.

        The set is the invariant floor plus every stored column ``consumer_sql``
        names, so an aggregate never pins a fat payload column the consuming
        statement does not read. The shared compiler emits legacy column tokens
        that only reach CH25 names at the rewrite boundary, so resolve those
        names here before reading the reference set; the rewritten copy is used
        for that decision alone and never emitted.
        """
        referenced = set(
            _PHYSICAL_SPAN_COLUMN_REFERENCE_RE.findall(
                rewrite_v1_sql_to_v2(consumer_sql)
            )
        )
        return tuple(
            column
            for column in _PHYSICAL_SPAN_COLUMNS
            if column not in _PHYSICAL_SPAN_KEY_COLUMNS
            and (column in _PHYSICAL_SPAN_FLOOR_COLUMNS or column in referenced)
        )

    def _latest_state_source_sql(self, alias, *, scope_sql, consumer_sql):
        """Latest state for every identity the scope admits, as a FROM source.

        ``scope_sql`` may restrict only immutable primary-key coordinates: all
        versions of an admitted identity share them, so the collapse input is
        complete and a stale value or tombstone can only add work, never a
        public match. Mutable predicates (deletion, exact timestamps, attribute
        values) stay in the caller's outer scope, where they see latest state.

        ``GROUP BY`` is the deployed sorting key in its declared order, so the
        ``optimize_aggregation_in_order`` the v2 settings boundary appends can
        stream the collapse in primary-key order rather than buffering the
        scope. This replaces ``SELECT * FROM spans FINAL``: predicates,
        ordering and keysets are unchanged, and the projection covers exactly
        the columns that source's consumers read.

        The winner tuple is unpacked ONE LEVEL ABOVE the aggregate that builds
        it. Unpacking it in the same SELECT would alias ``_physical_winner.N``
        to the name of a column that ``argMax(tuple(...))`` itself names, and
        the ClickHouse 25.3 analyzer rejects that alias cycle with
        UNKNOWN_IDENTIFIER before it reads a byte. The trace lane's root replay
        (``v2/query_builders/trace_list.py``) nests for the same reason.
        """
        packed = self._latest_state_packed_columns(consumer_sql)
        keys_sql = ", ".join(_PHYSICAL_SPAN_KEY_COLUMNS)
        unpacked_sql = ",\n                   ".join(
            f"_physical_winner.{position} AS {column}"
            for position, column in enumerate(packed, start=1)
        )
        return f"""(
            SELECT {keys_sql},
                   {unpacked_sql}
            FROM (
                SELECT {keys_sql},
                       argMax(tuple({", ".join(packed)}), _version) AS _physical_winner
                FROM {self.TABLE}
                PREWHERE {scope_sql}
                GROUP BY {_PHYSICAL_SPAN_GROUP_BY_SQL}
            ) AS replayed_{alias}
        ) AS {alias}"""

    def _filter_seed_source_sql(self, *, raw_key_predicate="", consumer_sql=""):
        # A raw timestamp is not an order bound for its replacement. Resolve
        # the complete boundary hours before applying slice/keyset/LIMIT or
        # mutable predicates. Only immutable key restrictions enter the
        # latest-state collapse.
        return self._latest_window_source_sql(
            "filter_slice",
            raw_key_predicate=raw_key_predicate,
            consumer_sql=consumer_sql,
        )

    def _filter_anchor_source_sql(self, *, consumer_sql=""):
        return self._latest_window_source_sql(
            "filter_anchor", consumer_sql=consumer_sql
        )

    def _latest_window_source_sql(
        self, prefix, *, raw_key_predicate="", consumer_sql=""
    ):
        population_scope = ""
        if raw_key_predicate:
            # A latest matching span must have a raw version satisfying these
            # compiler-proven necessary raw witnesses. Select
            # immutable primary prefixes, then replay ALL versions in each
            # selected prefix. Never filter mutable values, deletion
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
        return self._latest_state_source_sql(
            "latest_seed_spans",
            scope_sql=f"""{self.project_filter_sql()}
              AND toStartOfHour(start_time) >= toStartOfHour(fromUnixTimestamp64Micro(%({prefix}_start_us)s))
              AND toStartOfHour(start_time) <= toStartOfHour(fromUnixTimestamp64Micro(%({prefix}_end_us)s - 1))
              {population_scope}""",
            consumer_sql=consumer_sql,
        )

    def _normal_span_source_sql(self, *, consumer_sql=""):
        return self._latest_state_source_sql(
            "latest_list_spans",
            scope_sql=f"""{self.project_filter_sql()}
              AND toStartOfHour(start_time) >= toStartOfHour(toDateTime64(%(start_date)s, 6, 'UTC'))
              AND toStartOfHour(start_time) <= toStartOfHour(toDateTime64(%(end_date)s, 6, 'UTC'))""",
            consumer_sql=consumer_sql,
        )

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

    def _filter_match_source_sql(self, candidate_scope, *, consumer_sql=""):
        # The collapse yields one complete row per identity, including NULLs
        # and conflicting equal-version payloads, before the shared compiler's
        # per-column aggregates run over it.
        return self._latest_state_source_sql(
            "latest_candidate_spans",
            scope_sql=f"""{self.project_filter_sql()}
              AND id IN %(candidate_span_ids)s
              {candidate_scope}""",
            consumer_sql=consumer_sql,
        )

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
        content_columns_sql = (
            "project_id, trace_id, id, start_time, observation_type, "
            "service_name, _version, input, output, attributes_extra, "
            "attrs_string, attrs_number, attrs_bool"
        )
        source = self._filter_match_source_sql(
            scope, consumer_sql=f"{content_columns_sql} {project_version}"
        )
        return (
            f"""
        SELECT {content_columns_sql}
        FROM {source}
        WHERE is_deleted = 0 {project_version}
        {self._FILTER_READ_SETTINGS}
        """,
            params,
        )


__all__ = ["SpanListQueryBuilderV2"]
