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

from django.conf import settings

from tracer.selectors.filter_seed_width import (
    FilterSeedWidthPolicy,
    reduce_density_estimate,
)
from tracer.services.clickhouse.query_builders.filter_seed_witness import (
    ceil_hour,
    floor_hour,
)
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

    def _has_text_attribute_leaf(self):
        # Text Map values dwarf key and timestamp reads, so a text lane keeps
        # the narrow rung whatever its operators are. Discovery itself no
        # longer compares a value, so no leaf-shape carve-out is needed.
        return any(
            str(
                (cfg := _parts(item)[1]).get("col_type") or cfg.get("colType") or ""
            ).upper()
            == "SPAN_ATTRIBUTE"
            and (cfg.get("filter_type") or cfg.get("filterType")) in {"text", "string"}
            for item in self.filters
        )

    def _row_budgeted_span_lane(self):
        """Whether this read's two wide statements are row-budgeted.

        Exactly the reads whose filter compiles to a typed-Map population
        witness - the shapes whose seed replays ``attrs_string`` for every row
        of the coordinates the witness names, and whose absence proof carries
        that witness. A time-only list has no attribute predicate to be blind
        about; a native-column list's statements were never measured here.
        Both keep the wall-clock schedule they ship with today.

        A CANDIDATE-SEED (Score relation) LANE IS EXCLUDED even when it also
        carries an attribute leaf. That lane does not issue the raw seed this
        budget models: it acquires through a live Score relation, and it asks
        for the WHOLE request window as one slice on purpose - see
        ``recommended_filter_query_timeout_ms``, "a full-window Score relation
        should not fail at the tiny chronological-slice cutoff". Declaring an
        hour-floored row budget there would replace that one statement with a
        slice per hour, which is neither what was measured nor what that lane
        wants.
        """

        return bool(
            self._filter_population_plans()
            and not self.supports_filter_candidate_seed_page()
        )

    def filter_seed_width_policy(self):
        """Budget the span seed by the rows it reads, not by hours.

        WHAT THIS REPLACES. The seed used to double blindly from five minutes
        to a two-day ceiling: the width of the next slice was chosen from
        nothing at all, so a slice that would read gigabytes was issued exactly
        like one that would read megabytes. Measured read-only against
        production on the high-volume tenant, a 30-day span list whose seed
        reached a 48-hour slice ended that statement at its deadline, and eight
        empty slices had been spent getting there.

        The budget is in ROWS because the seed's cost is in rows: this lane's
        statement replays the typed Map of every physical row inside its slice
        (3.73 KB of ``attrs_string`` per row, measured), and a project's
        density varies by orders of magnitude across its own retention, so no
        one hour count is right at both ends. Each following slice is sized
        from the rows the previous statement actually read, and any width above
        the policy's unprobed cap must first be costed by
        ``build_filter_seed_density_probe_query``.

        The floor is ONE HOUR and so is the opening width, because the seed's
        own key predicate is on ``toStartOfHour(start_time)``: a five-minute
        slice reads exactly the granules the whole hour reads, so the old
        5m/10m/20m/40m schedule paid for one hour four times over before it
        covered it once. An hour is the narrowest slice that buys anything.

        Only the acquisition boundary moves. Slices stay contiguous and
        half-open, predicates, ordering, the exact latest-state classifier and
        the signed cursor payload are untouched, and a narrower slice defers
        its older part to the next adjacent slice rather than skipping it.
        """

        if not self._row_budgeted_span_lane():
            return None
        hour = timedelta(hours=1)
        return FilterSeedWidthPolicy(
            initial_width=hour,
            min_width=hour,
            target_read_rows=settings.FILTER_SELECTOR_SPAN_SEED_TARGET_READ_ROWS,
        )

    def filter_population_discovery_width_policy(self):
        """The same budget, for the absence proof, at that statement's rate.

        The proof and the seed read the same interval of the same table and are
        both linear in the rows inside it, but they read DIFFERENT columns: the
        proof reads ``start_time`` and the thin Map ``.keys`` stream its witness
        names, never a Map VALUE, so it walks about five times the seed's rows
        per second (measured read-only against production at one worker: 43.9
        bytes and ~1.5M rows per second, against the seed's 3.73 KB and ~0.3M).
        One number cannot serve both, so each declares its own; everything
        else - the width lattice, the floor, the unprobed cap, the proportional
        fit and its one refinement - is the same policy object doing the same
        arithmetic.

        THE PROOF'S WIDTHS ARE STILL BOUNDED BY ITS LADDER. This budget only
        ever NARROWS what
        ``recommended_filter_population_time_discovery_windows`` proposes: the
        wall-clock rungs remain the outer contract the statement validates
        itself against, and the row budget refuses the rung whose interval the
        primary index costs above the budget.

        The unprobed cap is a DAY rather than the policy's four-hour default,
        because a day is the narrowest rung this lane ships - the width a text
        lane already issues with nothing measured at all. It is NOT a promise
        that nothing narrows: a non-text lane whose proof cannot be costed (an
        estimate this request cannot read, or a spent probe allowance) falls
        back to a day where the ladder would have proposed 7 or 28. That costs
        statements, never history - intervals are contiguous and half-open, the
        remainder is the next proof's, and the next proof's own read rows widen
        it again.
        """

        if not self._row_budgeted_span_lane():
            return None
        hour = timedelta(hours=1)
        return FilterSeedWidthPolicy(
            initial_width=hour,
            min_width=hour,
            target_read_rows=(
                settings.FILTER_SELECTOR_SPAN_POPULATION_DISCOVERY_TARGET_READ_ROWS
            ),
            unsignalled_cap=timedelta(days=1),
        )

    def supports_filter_seed_density_probe(self):
        """A lane probes for density exactly when it declares a row budget."""

        return self.filter_seed_width_policy() is not None

    def build_filter_seed_density_probe_query(self, *, slice_start, slice_end):
        """Cost a proposed interval from the primary index, reading no data.

        This is the density proof both of this lane's row budgets require
        before an interval wider than their unprobed cap may be issued.

        IT READS NO COLUMN DATA. ``EXPLAIN ESTIMATE`` is not executed: it
        answers from the primary index and the skip indexes alone, and for
        every part they cannot exclude it reports the parts, granules
        (``marks``) and ``rows`` a real statement WOULD read. Measured
        read-only against production carrying this lane's witness, it returns
        54 bytes read and answers a 30-day interval in about 0.7 s. That holds
        for every conjunct it carries, whether or not the conjunct would be a
        row-level read in an EXECUTED statement - which is why this statement
        may carry the population witness VERBATIM rather than needing a second,
        index-only spelling of the same predicate.

        IT CARRIES THE CONJUNCTION. The plain time-range form would answer for
        a population this lane never reads. Measured read-only against
        production over the same 30-day interval, the plain estimate is 219
        parts / 92.0M rows and the witness-carrying estimate is 172 parts /
        88.9M rows for a key held by most parts; the two diverge much further
        for a key absent from most of the retained history, which is the shape
        that made the old proof expensive. Costing against the plain count
        would shrink every interval by the ratio of the two and turn a page
        into a crawl.

        The conjunct carried for each plan is its ``raw_index_witness_predicate``
        - key presence plus the deployed value/ngram ``indexHint`` companions -
        falling back to ``raw_key_witness_predicate`` for a plan with no value
        index, so a lane whose leaf carries no companion is still costed
        against ITS population rather than the plain time range. Both are
        necessary conditions of the same matches, and the statements this
        estimate sizes carry the same key presence and the same hints or more,
        so their index analysis prunes at least as much: the estimate stays an
        upper bound on the rows they read, and the error points at a NARROWER
        issued interval.

        DO NOT "IMPROVE" THE TIME PREDICATE INTO ``toStartOfHour(start_time)``.
        ``spans`` carries aggregate PROJECTIONs keyed on ``(project_id,
        toStartOfHour(start_time) AS hour, ...)`` which do not store
        ``start_time``; spelled as ``hour`` the optimizer could route this
        statement to a projection and ``rows`` would then be that projection's
        AGGREGATE rows - orders of magnitude below the interval, an estimate
        far inside the budget, and the widest possible interval APPROVED. Raw
        ``start_time`` bounds prune identically through the key expression's
        monotonicity, plus the table's ``PARTITION BY toDate(start_time)``.

        It answers a COST question only. It never decides membership, never
        prunes a candidate and never reaches the published page.
        """

        request_start, request_end = self._bounded_request_window
        if not request_start <= slice_start < slice_end <= request_end:
            raise ValueError("seed density probe must stay inside the request window")
        if not self.supports_filter_seed_density_probe():
            raise ValueError("seed density probe is unavailable")
        probe_start = max(request_start, floor_hour(slice_start))
        probe_end = min(request_end, ceil_hour(slice_end))
        params = {
            **self.params,
            "seed_density_start_us": _unix_microseconds(probe_start),
            "seed_density_end_us": _unix_microseconds(probe_end),
        }
        witnesses = ""
        for plan in self._filter_population_plans():
            witness = plan.raw_index_witness_predicate or plan.raw_key_witness_predicate
            if not witness:
                continue
            witnesses += f"\n              AND ({witness})"
            params.update(
                {
                    key: value
                    for key, value in plan.params.items()
                    if f"%({key})s" in witness
                }
            )
        return (
            f"""
            EXPLAIN ESTIMATE
            SELECT count()
            FROM {self.TABLE}
            WHERE {self.project_filter_sql()}
              AND start_time >= fromUnixTimestamp64Micro(%(seed_density_start_us)s)
              AND start_time < fromUnixTimestamp64Micro(%(seed_density_end_us)s){witnesses}
            """,
            params,
        )

    def filter_seed_density_probe_estimate(self, rows, columns=None):
        """Reduce one ``EXPLAIN ESTIMATE`` result to a policy's row bound.

        The reading of that result - and in particular the refusal to read an
        EMPTY estimate table as the integer zero - is shared with the trace
        lane's identical statement; see ``reduce_density_estimate``.
        """

        return reduce_density_estimate(rows, columns, table=self.TABLE)

    def recommended_filter_initial_slice_width(self):
        """Open a row-budgeted read at its policy's own width, not at five
        minutes: below an hour a slice reads the same granules for less
        coverage, because the seed's key predicate is hour-aligned."""

        policy = self.filter_seed_width_policy()
        if policy is None:
            return super().recommended_filter_initial_slice_width()
        start, end = self._bounded_request_window
        width = min(end - start, policy.initial_width)
        # The selector clips its own five-minute default to shorter requests,
        # but REFUSES an explicit recommendation below it, so a request window
        # narrower than that keeps the selector's default rather than naming a
        # width the bounded contract rejects.
        return width if width >= timedelta(minutes=5) else None

    def recommended_filter_population_time_discovery_window(self):
        # Complete necessary-witness absence proofs avoid empty daily seeds.
        # Text lanes stay daily; other witness lanes reuse the compiler's
        # index/key companions over the remaining request window.
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
            if self._filter_population_plans() and not self._has_text_attribute_leaf()
            else timedelta(hours=24)
        )

    def recommended_filter_population_time_discovery_windows(self):
        """Try adjacent recent ranges before a costly complete-year key scan.

        Each completed NULL advances only its own proven interval. Text lanes
        retain daily widths; other witness-filtered probes eventually widen to
        the remaining request. Neither policy truncates older history.
        """
        plans = self._filter_population_plans()
        start, end = self._bounded_request_window
        if self._bounded_sampling_rate is not None and not plans:
            return (end - start,)
        if not plans:
            return None
        width = end - start
        if self._has_text_attribute_leaf():
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
        # Discovery locates a populated interval; it never decides membership.
        # A leaf carrying physical value indexes contributes key presence plus
        # those index companions, so the probe prunes granules through the
        # deployed blooms without decompressing the attribute value stream.
        # NULL proves only this adjacent interval, and every raw hit replays
        # its complete physical hour: stale or missing values only add work.
        population_predicates = [
            f"({predicate})"
            for plan in population_plans
            if (predicate := self._population_discovery_witness(plan))
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

    def _population_discovery_witness(self, plan):
        """The raw witness the discovery aggregate carries for one plan.

        The index witness when the value has a companion behind it: key
        presence plus the deployed blooms, which prune granules without
        reading a Map value. A value too large for its companions leaves that
        witness as bare key presence, and for a key most spans carry that is
        no discovery at all - every hour is a hit, each hit replays one hour,
        and a week is walked two statements per hour (measured read-only
        against production: twelve statements for six hours and no row). So
        a companion-less plan discovers by its raw value witness instead,
        which this statement can afford: unlike the seed it carries no
        comparison of its own, so the value is inlined here once either way.
        Both witnesses are necessary conditions of the same matches; only the
        hour they locate and the columns they read differ.
        """

        index_witness = plan.raw_index_witness_predicate
        if index_witness and index_witness != plan.raw_key_witness_predicate:
            return index_witness
        return plan.raw_witness_predicate or self._filter_population_plan_predicate(
            plan, ordinary_seed=True
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
        # The seed carries the exact comparison once whatever happens here, so
        # the compiler decides whether this SECOND copy of the value still fits
        # under the parser limit, and hands back key presence when it does not.
        if ordinary_seed and plan.raw_key_witness_predicate:
            return plan.population_witness_predicate
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
