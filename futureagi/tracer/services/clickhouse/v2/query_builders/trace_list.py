"""
v2 TraceList query builder — targets the CH 25.3 spans schema.

The schema-aware core reuses the legacy filter planner. Physical replacement
and page replay live in a separate mixin shared with voice calls, so callers
can adopt the CH25 identity contract without changing acquisition policies.
`V2RewriteMixin` translates SQL at the public builder boundary.

`build_eval_query` / `build_annotation_query` are excluded from the span-column
rewrite. The eval query follows the independently configured authoritative
table on the CH25 connection; annotations retain their own source boundary.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from django.conf import settings

from tracer.selectors.filter_seed_width import FilterSeedWidthPolicy
from tracer.services.clickhouse.query_builders.trace_list import (
    _LONG_WINDOW_ORDERED_ROOT_INITIAL_SLICE,
    _SELECTIVE_EXACT_TEXT_MIN_LENGTH,
    LatestFilterPredicate,
    TraceListQueryBuilder,
    _unix_microseconds,
)
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

# The width the short exact-string seed opens at and refuses to narrow below
# while its child witness stays time-unbounded. It is the fixed ceiling this
# lane's row budget replaced, kept as the floor so the budget can only ever buy
# MORE coverage per statement than the ceiling did - see
# ``filter_seed_width_policy``. Provisional: with an unbounded witness the
# dense per-statement cost does not shrink with the slice, so no measurement
# locates an optimum below it.
_SHORT_TEXT_SEED_PROVISIONAL_FLOOR = timedelta(hours=4)

# One week, the same ceiling ``FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS``
# declares: beyond it the envelope stops bounding this lane's own windows.
_MAX_WITNESS_SLACK_HOURS = 168

# The same two widths once ``FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS``
# bounds the witness. There the statement's cost is linear in the envelope's
# hours, so a narrower slice really is a cheaper statement and the row budget
# is a signal rather than a constant; one hour is the measured schedule's
# opening width and the granularity the ``toStartOfHour`` primary-key prefix
# prunes on.
_SHORT_TEXT_SEED_BOUNDED_WITNESS_FLOOR = timedelta(hours=1)

# The column names ClickHouse 25.3 returns for ``EXPLAIN ESTIMATE``. They are
# the discriminator the density probe's reducer uses to tell an empty estimate
# (the answer "no part matched", i.e. zero rows) from a result it cannot read.
_SEED_DENSITY_ESTIMATE_COLUMNS = frozenset(
    {"database", "table", "parts", "rows", "marks"}
)


def _floor_hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def _ceil_hour(moment: datetime) -> datetime:
    floored = _floor_hour(moment)
    return floored if floored == moment else floored + timedelta(hours=1)


def _caseless_ascii_ngram_anchor(value: str) -> str | None:
    """A necessary literal substring compatible with the existing ASCII index.

    The index lowercases ASCII while exact matching uses lowerUTF8. Root-locale
    Unicode lowercasing has two non-ASCII sources of ASCII letters: dotted I
    (which may also add a combining dot) and Kelvin sign. Split at i/k and all
    non-ASCII characters; the remaining ASCII letters, digits and punctuation
    have the same lowercase representation under both functions. This allows
    ordinary prose, not just digit/punctuation runs, to use the existing index.
    Retain every usable run in order: a long common phrase must not discard a
    shorter selective identifier elsewhere in the same exact literal. Unknown
    casing segments become wildcards, not guessed Unicode transformations.
    The hint remains a necessary condition only; exact Unicode comparison and
    complete latest-state replay still decide membership. No usable four-gram
    means the ordinary exact route, never an empty result.
    """
    runs = [
        run for run in re.findall(r"[^IiKk\x80-\U0010ffff]+", value) if len(run) >= 4
    ]
    if not runs:
        return None
    return (
        "%"
        + "%".join(
            run.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            for run in runs
        )
        + "%"
    )


class UserEnrichmentLimitExceeded(ReadDeadlineExceeded):
    """A page's remap fan-in exceeded the optional enrichment read bound."""


class _TraceRootReplayV2:
    """CH25 physical replacement and hydration, independent of acquisition policy."""

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

    @staticmethod
    def _filter_project_version_is_immutable() -> bool:
        return False

    @staticmethod
    def _filter_classifier_root_order() -> str:
        return (
            "tuple(latest_start_time, grouped_id, grouped_root_observation_type, "
            "grouped_root_service_name, grouped_root_start_hour)"
        )

    @staticmethod
    def _filter_classifier_root_identity_fields() -> tuple[tuple[str, str], ...]:
        return (
            ("grouped_id", "root_span_id"),
            ("latest_start_time", "start_time"),
            ("grouped_root_observation_type", "_root_observation_type"),
            ("grouped_root_service_name", "_root_service_name"),
            ("grouped_root_start_hour", "_root_start_hour"),
            ("latest_root_version", "_root_version"),
        )

    @staticmethod
    def _filter_classifier_latest_select_sql(aggregate_sql: str) -> str:
        """Pack the compiler's argMax expressions into ONE physical winner.

        This consumes only the closed aggregate grammar emitted by the legacy
        classifier/latest-predicate compiler, never user SQL. Fail closed if a
        future compiler emits a different aggregate rather than mixing winners.
        Tuple wrapping preserves NULLs, including tuple-valued plan arguments.
        Equal-version conflicts have no unique storage winner; one tuple still
        prevents constructing a row from fields of different tied versions.
        """
        pattern = re.compile(
            r"\s*argMax\((.*?),\s*_peerdb_version\)(\.1)?\s+AS\s+(\w+)\s*(?:,|$)",
            re.DOTALL,
        )
        values, aliases = [], []
        position = 0
        while aggregate_sql[position:].strip():
            match = pattern.match(aggregate_sql, position)
            if match is None:
                raise ValueError("unsupported latest trace aggregate")
            source, suffix, alias = match.groups()
            values.append(source)
            aliases.append(f"_physical_winner.{len(values)}{suffix or ''} AS {alias}")
            position = match.end()
        values.extend(("_peerdb_version", "project_version_id"))
        aliases.extend(
            (
                f"_physical_winner.{len(values) - 1} AS latest_root_version",
                f"_physical_winner.{len(values)} AS _latest_root_project_version",
            )
        )
        return ",\n".join(
            [
                "observation_type AS grouped_root_observation_type",
                "service_name AS grouped_root_service_name",
                "toStartOfHour(start_time) AS grouped_root_start_hour",
                f"argMax(tuple({', '.join(values)}), _peerdb_version) AS _physical_winner",
                *aliases,
            ]
        )

    def _validated_root_identity(self, row: dict[str, Any]) -> tuple:
        """Full immutable storage key plus the observed time/version, or fail closed."""
        project = str(row.get("project_id") or self.project_id or "")
        allowed = (
            set(self.project_ids or ())
            if self.project_ids is not None
            else {str(self.project_id)}
        )
        start, hour = row.get("start_time"), row.get("_root_start_hour")
        kind, service = row.get("_root_observation_type"), row.get("_root_service_name")
        version = row.get("_root_version")
        if (
            project not in allowed
            or not row.get("trace_id")
            or not row.get("root_span_id")
            or not isinstance(start, datetime)
            or not isinstance(hour, datetime)
            or not isinstance(kind, str)
            or not kind
            or not isinstance(service, str)
            or not isinstance(version, int)
            or isinstance(version, bool)
            or version < 0
        ):
            raise ValueError(
                "v2 trace hydration requires a complete scoped root identity"
            )
        start_us, hour_us = _unix_microseconds(start), _unix_microseconds(hour)
        if hour_us != start_us // 3_600_000_000 * 3_600_000_000:
            raise ValueError(
                "v2 trace hydration root hour does not contain latest time"
            )
        # Keep the legacy prefix for callers; physical scan coordinates below
        # deliberately use the hour instead of the mutable exact timestamp.
        return (
            project,
            str(row["trace_id"]),
            str(row["root_span_id"]),
            start_us,
            kind,
            service,
            hour_us,
            version,
        )

    def bounded_filter_page_hydration_identity(
        self, row: dict[str, Any]
    ) -> tuple | None:
        # Missing/malformed replay metadata is drift, not an exception that
        # bypasses the selector's existing unhydrated-checkpoint rollback.
        try:
            return self._validated_root_identity(row)
        except ValueError:
            return None

    def content_root_identities_for_rows(
        self, rows: list[dict[str, Any]]
    ) -> list[tuple]:
        return [self._validated_root_identity(row) for row in rows]

    def content_root_rows_match(
        self, expected: list[dict[str, Any]], actual: list[dict[str, Any]]
    ) -> bool:
        try:
            expected_ids = self.content_root_identities_for_rows(expected)
            actual_ids = self.content_root_identities_for_rows(actual)
        except ValueError:
            return False
        return (
            len(set(expected_ids)) == len(expected_ids)
            and len(actual_ids) == len(expected_ids)
            and set(actual_ids) == set(expected_ids)
        )

    def _root_replay_content_fields(self) -> list[tuple[str, str]]:
        return [
            ("input", "input"),
            ("output", "output"),
            ("attrs_string", "attrs_string"),
            ("attrs_number", "attrs_number"),
            ("attrs_bool", "attrs_bool"),
            ("attributes_extra", "attributes_extra"),
            ("metadata", "metadata"),
        ]

    def _root_replay_content_extra_select(self) -> list[str]:
        return [self._trace_tags_select_sql()]

    def _root_replay_content_join_sql(self) -> str:
        return self._trace_tags_join_sql()

    def _root_replay_query(
        self, identities: list[tuple], *, content: bool
    ) -> tuple[str, dict[str, Any]]:
        """Replay the finite page by immutable coordinates, with no time/version prefilter."""
        if not identities:
            return "", {}
        if len(identities) > 512 or len(
            {identity[:2] for identity in identities}
        ) != len(identities):
            raise ValueError(
                "trace replay requires one bounded root per trace identity"
            )
        prefix = "content" if content else "page_hydration"
        params = {
            **self.params,
            f"{prefix}_trace_ids": tuple(
                dict.fromkeys(identity[1] for identity in identities)
            ),
            f"{prefix}_root_identities": tuple(identities),
            f"{prefix}_physical_keys": tuple(
                (
                    project,
                    kind,
                    service,
                    datetime.fromtimestamp(hour // 1_000_000, UTC).replace(tzinfo=None),
                    trace,
                    span,
                )
                for project, trace, span, _, kind, service, hour, _ in identities
            ),
            # Expose native primary-index coordinates independently of the
            # String-cast project identity and ORDER BY-only span suffix.
            # This redundant superset improves sparse-index pruning without
            # filtering mutable time/version/content before replacement.
            f"{prefix}_primary_prefixes": tuple(
                dict.fromkeys(
                    (
                        kind,
                        service,
                        datetime.fromtimestamp(hour // 1_000_000, UTC).replace(
                            tzinfo=None
                        ),
                        trace,
                    )
                    for _, trace, _, _, kind, service, hour, _ in identities
                )
            ),
        }
        # Each source expression is inside the same tuple, so a newer NULL
        # replaces an old value instead of argMax skipping it.
        fields = [
            ("start_time", "start_time"),
            ("parent_span_id", "latest_parent_span_id"),
            ("is_deleted", "latest_is_deleted"),
            ("project_version_id", "latest_project_version_id"),
            ("_version", "_root_version"),
        ]
        if content:
            fields += self._root_replay_content_fields()
        else:
            fields += [
                ("trace_name", "trace_name"),
                ("name", "span_name"),
                ("status", "status"),
                ("end_time", "end_time"),
                ("latency_ms", "latency_ms"),
                ("cost", "cost"),
                ("total_tokens", "total_tokens"),
                ("prompt_tokens", "prompt_tokens"),
                ("completion_tokens", "completion_tokens"),
                ("model", "model"),
                ("provider", "provider"),
                ("trace_session_id", "trace_session_id"),
            ]
        projected = [
            f"_root_snapshot.{index} AS {alias}"
            for index, (_, alias) in enumerate(fields, start=1)
        ]
        selected = [
            "toString(project_id) AS project_id",
            "trace_id",
            "root_span_id",
            "start_time",
            "_root_observation_type",
            "_root_service_name",
            "_root_start_hour",
            "_root_version",
        ]
        selected += [
            "toJSONString(metadata) AS metadata" if alias == "metadata" else alias
            for _, alias in fields[5:]
        ]
        if content:
            selected.extend(self._root_replay_content_extra_select())
        else:
            selected.append("_root_observation_type AS observation_type")
        project_version = ""
        if self.project_version_id:
            params["project_version_id"] = self.project_version_id
            project_version = "AND latest_project_version_id = %(project_version_id)s"
        query = f"""
        SELECT {", ".join(selected)}
        FROM (
            SELECT project_id, trace_id, root_span_id,
                _root_observation_type, _root_service_name, _root_start_hour,
                {", ".join(projected)}
            FROM (
                SELECT project_id, trace_id, id AS root_span_id,
                    observation_type AS _root_observation_type,
                    service_name AS _root_service_name,
                    toStartOfHour(start_time) AS _root_start_hour,
                    argMax(tuple({", ".join(source for source, _ in fields)}), _version) AS _root_snapshot
                FROM {self.TABLE}
                PREWHERE {self.project_filter_sql()}
                  AND (observation_type, service_name,
                       toStartOfHour(start_time), trace_id)
                      IN %({prefix}_primary_prefixes)s
                  AND (toString(project_id), observation_type, service_name,
                       toStartOfHour(start_time), trace_id, id)
                      IN %({prefix}_physical_keys)s
                GROUP BY project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id
            ) AS replayed_root_versions
        ) AS latest_physical_roots
        {self._root_replay_content_join_sql() if content else ""}
        WHERE latest_is_deleted = 0
          AND (latest_parent_span_id IS NULL OR latest_parent_span_id = '')
          {project_version}
        ORDER BY start_time DESC, trace_id DESC, project_id DESC
        LIMIT {len(identities)}
        """
        return query, params

    def build_filter_page_hydration_query(
        self, candidate_rows: list[dict[str, Any]]
    ) -> tuple[str, dict[str, Any]]:
        return self._root_replay_query(
            self.content_root_identities_for_rows(candidate_rows), content=False
        )

    def build_content_query(
        self, trace_ids: list[str], *, root_identities: list[tuple] | None = None
    ) -> tuple[str, dict[str, Any]]:
        if not trace_ids:
            return "", {}
        if not root_identities or any(
            len(identity) != 8 for identity in root_identities
        ):
            raise ValueError(
                "v2 content replay requires complete root identities; no four-part fallback"
            )
        rows = []
        for (
            project,
            trace,
            span,
            start,
            kind,
            service,
            hour,
            version,
        ) in root_identities:
            rows.append(
                {
                    "project_id": project,
                    "trace_id": trace,
                    "root_span_id": span,
                    "start_time": datetime.fromtimestamp(
                        start // 1_000_000, UTC
                    ).replace(microsecond=start % 1_000_000),
                    "_root_observation_type": kind,
                    "_root_service_name": service,
                    "_root_start_hour": datetime.fromtimestamp(hour // 1_000_000, UTC),
                    "_root_version": version,
                }
            )
        identities = self.content_root_identities_for_rows(rows)
        if {identity[1] for identity in identities} != set(map(str, trace_ids)):
            raise ValueError("v2 content replay identity escaped requested traces")
        return self._root_replay_query(identities, content=True)

class _TraceListQueryBuilderV2Core(_TraceRootReplayV2, TraceListQueryBuilder):
    """Schema-aware planner shared before the public SQL rewrite boundary."""

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

    def _uses_attribute_coordinate_replay(self) -> bool:
        """Prune public typed-attribute replay by complete immutable prefixes."""

        if (
            self.project_id is None
            or self.project_ids is not None
            or self._bounded_identity_only
            or self._bounded_internal_scan
            or self._bounded_bulk_scan
            or self._bounded_population_proof
            or self._bounded_global_span_witnesses
            or self._bounded_membership_filters is not None
            or self._bounded_sampling_rate is not None
            or self.search
            or self.sort_params
        ):
            return False
        plans, residual = self._partition_trace_filter_plans(self._bounded_filters())
        any_plans = [plan for plan in plans if plan.scope == "any"]
        return bool(
            any_plans
            and not residual
            and all(
                plan.aggregates
                and any(
                    column in " ".join(plan.aggregates)
                    for column in (
                        "span_attr_str",
                        "span_attr_num",
                        "span_attr_bool",
                        "span_attributes_raw",
                    )
                )
                for plan in any_plans
            )
        )

    def _uses_scalar_coordinate_replay(self) -> bool:
        # Numeric raw-witness accelerators retain their scalar-only contract.
        if not self._uses_attribute_coordinate_replay():
            return False
        plans, _ = self._partition_trace_filter_plans(self._bounded_filters())
        return all(
            "JSON" not in " ".join(plan.aggregates)
            for plan in plans
            if plan.scope == "any"
        )

    def _filter_classifier_coordinate_predicate(self) -> str:
        if not self._uses_attribute_coordinate_replay():
            return ""
        # The deployed sparse primary key stops before trace_id. Harvest the
        # complete indexed prefix from narrow columns first, then replay Map
        # values only at those coordinates. All child timestamps, physical
        # versions and tombstones participate; neither a raw value nor the
        # selected seed root is allowed to choose the replay population.
        # No inner LIMIT: an overflowing coordinate set must throw, not hide
        # a span. The outer full replacement-key argMax remains authoritative.
        return f"""
                  AND (observation_type, service_name, toStartOfHour(start_time), trace_id) IN (
                      SELECT DISTINCT observation_type, service_name,
                          toStartOfHour(start_time), trace_id
                      FROM {self.TABLE}
                      PREWHERE {self.project_filter_sql()}
                          AND trace_id IN %(candidate_trace_ids)s
                  )
        """

    def recommended_filter_initial_classify_batch_size(self):
        return 50 if self._public_boolean_candidate_seed_plan() is not None else None

    def supports_filter_empty_seed_root_time_discovery(self) -> bool:
        """Typed Boolean/short-string IN may skip only proven raw-root gaps."""
        leaves = self._active_non_time_filters()
        if (
            self.project_version_id is not None
            or not self.supports_filter_root_time_discovery()
            or not self._uses_scalar_coordinate_replay()
            or len(leaves) != 1
        ):
            return False
        config = leaves[0].get("filter_config") or {}
        values, types = config.get("filter_value"), config.get("attribute_value_types")
        if not (
            config.get("col_type") == "SPAN_ATTRIBUTE"
            and config.get("filter_type") == "text"
            and config.get("filter_op") == "in"
            and isinstance(values, list)
            and values
            and isinstance(types, list)
            and (
                (all(type(value) is bool for value in values)
                 and types == ["boolean"] * len(values))
                or (
                    all(type(value) is str
                        and 0 < len(value.strip()) < _SELECTIVE_EXACT_TEXT_MIN_LENGTH
                        for value in values)
                    and types == ["string"] * len(values)
                )
            )
        ):
            return False
        plans, residual = self._partition_trace_filter_plans(self._bounded_filters())
        return bool(
            not residual
            and len(plans) == 1
            and plans[0].scope == "any"
            and not plans[0].exclude_group_matches
        )

    def filter_candidate_seed_requires_empty_prefix(self) -> bool:
        return self._public_boolean_candidate_seed_plan() is not None

    def recommended_filter_initial_slice_width(self):
        # A row-budgeted lane opens at its policy's own initial width, clamped
        # to the request: the shared candidate-witness contract declines any
        # window of an hour or less, but a two-hour window still reaches this
        # lane and is narrower than the floor the policy declares.
        policy = self.filter_seed_width_policy()
        if policy is not None:
            start, end = self._bounded_request_window
            return min(end - start, policy.initial_width)
        if self.filter_candidate_seed_requires_empty_prefix():
            start, end = self._bounded_request_window
            return min(end - start, _LONG_WINDOW_ORDERED_ROOT_INITIAL_SLICE)
        return super().recommended_filter_initial_slice_width()

    def filter_seed_width_policy(self) -> FilterSeedWidthPolicy | None:
        """Budget the short exact-string seed by rows read, not by hours.

        The lane has two width schedules because it has two cost shapes, and
        ``FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS`` selects between them:

        * slack zero (the legacy any-span escape hatch; the default is 1 h):
          the witness is time-unbounded, its flat term dominates, and the
          floor and initial width are both the four hours of the fixed ceiling
          this budget replaced - ``_SHORT_TEXT_SEED_PROVISIONAL_FLOOR``. Narrowing below
          that would pay the same flat cost for a fraction of the coverage,
          which is why the row budget can only widen this mode.
        * slack above zero: the witness scan is confined to the roots'
          own hours plus the slack, so the statement's cost is linear in the
          envelope's hours (~0.46-0.49 GB per hour measured) and a narrower
          slice really is a cheaper statement. Floor and initial width both
          drop to ``_SHORT_TEXT_SEED_BOUNDED_WITNESS_FLOOR``, one hour, which
          is the opening width of the measured 1 h -> 2 h -> 4 h schedule; the
          row budget then does real work in both directions, halving a dense
          slice back down toward that hour.

        Both modes keep the same ``target_read_rows`` and the same four-hour
        ``unsignalled_cap``: a transport that reports no progress justifies no
        more than the ceiling this lane started from, whichever mode is on.
        That number is also the UNPROBED cap - the widest slice either mode may
        issue on the strength of the previous statement alone. Anything wider
        must be costed first by ``build_filter_seed_density_probe_query``,
        because the doubling rule is blind to the next slice's contents and a
        sparse tail that ends in dense history is exactly where that blindness
        is expensive.
        The post-discovery reset follows the floor by construction - it is
        ``max(1 h, policy.min_width)`` - so it is one hour in the bounded mode
        and four in the unbounded one, with no second constant to keep in step.

        Why the unbounded mode's floor is what it is. The long-text and
        numeric lanes prune raw granules by value, so the base builder lets
        them widen to the whole request window. A short exact literal only prunes by
        key/value bloom, and with an unbounded witness this seed's cost has two
        terms, neither of which a wall-clock ceiling tracks:

        * A flat term, which dominates: the child witness in this CTE is
          time-unbounded in that mode (any raw span of a candidate trace may
          carry the value, whenever it started), so every statement scans the
          trace-id bloom's false positives across retained history. A measured unbounded child
          lookup over a fifteen-minute dense root window read 52.4M rows /
          4.29 GB after the trace-id, key and value blooms had each roughly
          halved the granule count (55,689 -> 27,219 -> 14,528 -> 9,069 ->
          7,902). Modelling the bloom pass rate as ``1 - 0.999 ** roots`` over
          a ~107M-row history fits that funnel, so read rows here chiefly
          measure the *roots inside the slice*. Extrapolating that one point
          (k = 676 roots in fifteen minutes of the densest tenant) the term
          would pass 90% of its ceiling within about an hour of the same
          traffic, after which it is paid per statement whatever the width.
          That is an extrapolation from a single measurement, not a measured
          saturation curve: nothing has been read at two widths of the same
          data.
        * A linear term: the ``attrs_string`` materialized for the traces whose
          raw roots fall inside the slice, ~0.48 GB per slice-hour on a dense
          project. It grows with the slice's *row* population, not its width,
          and a project's density varies by two orders of magnitude across its
          own retention: four hours of the same project's sparse history cost
          110-220 ms server-side whatever the width.

        What the budget can and cannot do therefore follows from the flat
        term. Doubling fires only below a quarter of the target, which at the
        default is roughly four or five roots in the slice, so the budget
        *widens across near-empty history* - the regime a fixed four-hour
        ceiling walked one slice at a time down a 166 h sparse tail. Anywhere
        the user's own results live, every width is over budget instead, and
        there the budget must not be allowed to buy less coverage than the
        fixed ceiling it replaces. Hence the unbounded mode's floor, and its
        initial width, are both the four hours that ceiling was: there this
        lane only ever *widens* on sparse history and otherwise holds at four
        hours, which is at least the previous behaviour in both regimes.
        Halving therefore applies only to a width that first grew above four
        hours and then ran into data.

        The four-hour floor is provisional, and it is provisional precisely
        because the flat term above is what a narrower slice cannot buy back.
        It is not a measured optimum, and no measurement says where narrowing
        an unbounded-witness statement stops paying. Bounding the witness is
        the other half of that question, and it is what the slack switch
        settles: the 3.66M rows / 4.47 GB / 3.8 s at ``max_threads`` 1 and
        1.76 s at 2 measured for a dense four-hour slice is a measurement of
        the *bounded* statement, confined to the window plus an hour of slack -
        which this builder emits only when the switch is on, and never at the
        default.

        Slices at or above an hour are whole-hour multiples of the immutable
        ``toStartOfHour`` primary-key prefix; their boundaries snap onto an hour
        only after an empty-seed root-time discovery hit. This changes only
        physical chunking; predicates, order, pagination and the exact
        latest-state classifier are untouched, and the cursor resumes the
        remaining window on the next request.
        """
        if not self._uses_short_text_candidate_seed():
            return None
        floor = (
            _SHORT_TEXT_SEED_BOUNDED_WITNESS_FLOOR
            if self._short_text_seed_witness_slack()
            else _SHORT_TEXT_SEED_PROVISIONAL_FLOOR
        )
        return FilterSeedWidthPolicy(
            initial_width=floor,
            min_width=floor,
            target_read_rows=settings.FILTER_SELECTOR_TEXT_SEED_TARGET_READ_ROWS,
        )

    def pin_filter_seed_witness_slack_hours(self, hours: int | None) -> None:
        """Finish a pagination with the witness slack it STARTED with.

        The slack decides which traces are candidates at all, so changing it
        between two hops of one cursor moves the boundary underneath a
        half-published page: rows already returned can become non-candidates
        (the user sees a duplicate when the next hop re-seeds) and rows already
        skipped can become candidates (the user never sees them). An operator
        turning the knob mid-request is a real event, not a hypothetical - the
        setting is deliberately runtime-tunable.

        So a continuation carries the slack it was minted with and pins it
        here. ``None`` clears the pin and returns the builder to the runtime
        setting, which is both the legacy behaviour and what a cursor minted
        before this field carried resolves to.
        """

        if hours is None:
            self._pinned_witness_slack_hours = None
            return
        if isinstance(hours, bool) or not isinstance(hours, int):
            raise ValueError("pinned witness slack must be whole hours")
        if not 0 <= hours <= _MAX_WITNESS_SLACK_HOURS:
            raise ValueError("pinned witness slack is outside the supported range")
        self._pinned_witness_slack_hours = hours

    def filter_seed_witness_slack_hours(self) -> int | None:
        """The slack this read will use, for the cursor to carry forward.

        ``None`` means this request has no witness envelope to preserve - the
        lane is not active - and a cursor minted from it carries no slack, so
        it stays byte-identical to one minted before the field existed.
        """

        if not self._uses_short_text_candidate_seed():
            return None
        return int(self._short_text_seed_witness_slack().total_seconds() // 3600)

    def _short_text_seed_witness_slack(self) -> timedelta:
        """Hours of lag this lane's witness envelope allows; zero means none.

        Zero is the legacy contract and remains the escape hatch: no envelope
        is emitted at all and the witness CTE stays time-unbounded. The setting
        is read per statement rather than cached, so an operator change takes
        effect on the next read - EXCEPT inside a running pagination, where
        ``pin_filter_seed_witness_slack_hours`` holds the value the cursor was
        minted with so a mid-flight change cannot skip or duplicate rows.
        """

        if not self._uses_short_text_candidate_seed():
            return timedelta(0)
        pinned = getattr(self, "_pinned_witness_slack_hours", None)
        hours = (
            pinned
            if pinned is not None
            else int(settings.FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS)
        )
        return timedelta(hours=min(max(hours, 0), _MAX_WITNESS_SLACK_HOURS))

    def _scalar_candidate_witness_envelope(
        self, *, root_start: datetime, root_end: datetime
    ) -> tuple[str, dict[str, Any]]:
        """Bound the short exact-string seed's witness scan, when switched on.

        Off (slack zero, the legacy escape hatch - the default is 1 h) this
        emits nothing and the statement is byte-identical to the unbounded
        contract. On, the witness must start
        inside ``[hour_floor(root_start) - slack, hour_ceil(root_end) + slack)``
        where ``[root_start, root_end]`` is the interval of roots the calling
        statement can publish. Every root it publishes therefore keeps any
        witness within ``slack`` of itself, and the hour alignment matches the
        immutable ``toStartOfHour`` primary-key prefix the bound prunes on.

        The bound is spelled exactly like the sibling root-population subquery
        in the same CTE - epoch microseconds through
        ``fromUnixTimestamp64Micro`` - rather than as a datetime literal, so
        neither bound depends on how the driver or the server resolves a
        timezone. Because both ends are whole hours this is identical in
        meaning to the measured shape, which additionally spelled the
        hour-aligned prefix out.

        This narrows candidacy only. The exact latest-state classifier is a
        separate statement and stays unbounded, so a published row is still an
        exact any-span match; the envelope can only omit a trace whose sole
        witness lies outside it. Physical versions and tombstones still
        participate - the CTE deliberately carries no ``is_deleted`` filter -
        so this bounds *when* a witness row starts, never which versions count.
        """

        slack = self._short_text_seed_witness_slack()
        if not slack:
            return "", {}
        witness_start = _floor_hour(root_start) - slack
        witness_end = _ceil_hour(root_end) + slack
        fragment = (
            "\n              AND start_time >= "
            "fromUnixTimestamp64Micro(%(filter_witness_start_us)s)"
            "\n              AND start_time < "
            "fromUnixTimestamp64Micro(%(filter_witness_end_us)s)"
        )
        return fragment, {
            # The datetime pair is bound for the orchestration contract only,
            # exactly as ``filter_slice_start``/``_end`` are; SQL reads the
            # microsecond pair so a boundary microsecond cannot be rounded off.
            "filter_witness_start": witness_start,
            "filter_witness_end": witness_end,
            "filter_witness_start_us": _unix_microseconds(witness_start),
            "filter_witness_end_us": _unix_microseconds(witness_end),
        }

    def recommended_filter_classify_batch_size(self) -> int | None:
        if self._uses_attribute_coordinate_replay():
            return 200
        return super().recommended_filter_classify_batch_size()

    def recommended_filter_cursor_seed_batch_size(self) -> int | None:
        if self._uses_attribute_coordinate_replay():
            # Harvest one ordered root prefix before indexed exact replay.
            # Twenty-six-root seeds repeatedly reread the same narrow primary
            # index ranges for sparse/zero-default/negative scalar predicates.
            # Batching changes only working-set size, never predicates, order,
            # publication or the existing request/statement safety budgets.
            return 200
        return super().recommended_filter_cursor_seed_batch_size()

    def allow_filter_anchor_probe_for_initial_continuation(self) -> bool:
        if self._uses_attribute_coordinate_replay():
            # The all-history string sentinel can consume a full Map scan
            # before the indexed page replay. Cursor acquisition can start
            # directly with ordered roots and retain the same exact classifier.
            # Numeric candidate-first seeds are separate and remain enabled.
            return False
        return super().allow_filter_anchor_probe_for_initial_continuation()

    def _scalar_candidate_root_population_predicate(self) -> str:
        if not self._uses_scalar_coordinate_replay():
            return ""
        # Any latest live matching root must also exist as a raw physical root
        # in this exact interval. Old root versions only add false positives;
        # the subsequent full-key latest-state classifier removes them.
        # Restrict immutable trace IDs, NOT the child's start_time. Neither a
        # population LIMIT nor the current page keyset may prune child history.
        return f"""
              AND trace_id IN (
                  SELECT trace_id FROM {self.TABLE}
                  PREWHERE {self.project_filter_sql()}
                      AND start_time >= fromUnixTimestamp64Micro(%(filter_slice_start_us)s)
                      AND start_time < fromUnixTimestamp64Micro(%(filter_slice_end_us)s)
                  WHERE parent_span_id IS NULL OR parent_span_id = ''
              )
        """

    def _positive_text_candidate_witnesses(
        self,
    ) -> Iterator[tuple[LatestFilterPredicate, str, list[str]]]:
        """Yield compiler-proven positive string-only witnesses and their values.

        Shared acquisition shape for both typed-string regimes below. It proves
        only that a positive ``span_attr_str`` value witness exists and extracts
        the bound literals it compares; no length, operation or index policy is
        applied here. Numeric/Boolean branches, JSON, negation, missing-key
        defaults and residual filters are rejected by the existing compiler and
        ``_uses_scalar_coordinate_replay`` contract, not by this helper.
        """
        if not self._uses_scalar_coordinate_replay():
            return
        for plan in self._candidate_witness_plans():
            witness = self._public_scalar_candidate_witness_predicate(plan)
            if (
                not witness
                or "span_attr_str[" not in witness
                or "span_attr_num" in witness
                or "span_attr_bool" in witness
            ):
                continue
            key_params = set(
                re.findall(r"%\((\w+)\)s", plan.raw_key_witness_predicate or "")
            )
            value_params = set(re.findall(r"%\((\w+)\)s", witness)) - key_params
            values: list[str] = []
            for name in sorted(value_params):
                value = plan.params.get(name)
                values.extend(value if isinstance(value, (list, tuple)) else [value])
            if values and all(isinstance(value, str) for value in values):
                yield plan, witness, values

    @property
    def _long_text_candidate_seed_plan(self):
        """Memoize the long-text plan against the inputs that decided it."""

        return self._seed_plan_cached(
            "long_text", self._compute_long_text_candidate_seed_plan
        )

    def _compute_long_text_candidate_seed_plan(self):
        """Find a selective, compiler-proven necessary typed-string witness.

        Long positive text filters otherwise classify hundreds of unrelated
        roots per read. Length selects an execution plan only, never semantics.
        Missing-key defaults, negation, JSON and mixed-type witnesses must still
        satisfy the existing compiler's exhaustive raw-witness contract.
        """
        for plan, witness, values in self._positive_text_candidate_witnesses():
            if not all(
                len(value.strip()) >= _SELECTIVE_EXACT_TEXT_MIN_LENGTH
                for value in values
            ):
                continue
            anchors = [_caseless_ascii_ngram_anchor(value) for value in values]
            if any(anchor is None for anchor in anchors):
                continue
            params = dict(plan.params)
            hints = []
            for index, anchor in enumerate(anchors):
                name = f"long_text_ngram_{index}"
                params[name] = anchor
                # Match schema 023's index expression exactly. indexHint
                # only prunes impossible granules; it is never a value
                # predicate or a substitute for latest-state replay.
                hints.append(
                    "arrayStringConcat(arrayMap(x -> lower(x), "
                    f"mapValues(span_attr_str))) LIKE %({name})s"
                )
            return replace(
                plan,
                params=params,
                raw_graph_value_witness_predicate=(
                    "indexHint(" + " OR ".join(hints) + ") AND (" + witness + ")"
                ),
            )
        return None

    @property
    def _short_text_candidate_seed_plan(self):
        """Memoize the short-text plan against the inputs that decided it."""

        return self._seed_plan_cached(
            "short_text", self._compute_short_text_candidate_seed_plan
        )

    def _compute_short_text_candidate_seed_plan(self):
        """Seed short exact strings from the compiler's own typed value bloom.

        Selectivity policy for positive typed-string acquisition. A seed is
        worth running only when one usable value index already exists, and
        which index that is depends on value length and operation alone:

        * long values (and only they) can anchor schema 023's concatenated
          lowercase LIKE index, so substring operations stay on the long
          regime above;
        * a short literal has no usable LIKE anchor, but exact membership does
          carry the compiler's value companion over ``mapValues(span_attr_str)``
          (``has``/``hasAny`` plus its ASCII legacy variant), emitted for
          ``equals``/``in`` only. That companion is a necessary condition, so
          it may prune raw granules; exact Unicode comparison and the complete
          six-key latest-state classifier still decide membership.

        Everything else — mixed value lengths, substring/negative operations,
        JSON, structured overflow, versioned requests, relational or end-user
        seeds — keeps the existing acquisition path unchanged. This is the same
        flat scalar typed-map AND envelope the numeric lane requires: at most
        ten leaves, each its own typed Map plan, and no residual filter.
        """
        leaves = self._active_non_time_filters()
        if (
            self.project_version_id is not None
            or not 1 <= len(leaves) <= 10
            or not self._uses_scalar_coordinate_replay()
            or self._positive_exact_end_user_seed_filter() is not None
            or self._positive_relational_seed_filter() is not None
        ):
            return None
        plans, residual = self._partition_trace_filter_plans(self._bounded_filters())
        if (
            residual
            or len(plans) != len(leaves)
            or not all(
                plan.scope == "any"
                and plan.aggregates
                and all(
                    any(
                        column in sql
                        for column in (
                            "span_attr_num",
                            "span_attr_str",
                            "span_attr_bool",
                        )
                    )
                    for sql in plan.aggregates
                )
                for plan in plans
            )
        ):
            return None
        for plan, witness, values in self._positive_text_candidate_witnesses():
            if (
                all(
                    0 < len(value.strip()) < _SELECTIVE_EXACT_TEXT_MIN_LENGTH
                    for value in values
                )
                and "mapValues(span_attr_str)" in witness
            ):
                return plan
        return None

    def _public_long_text_candidate_seed_plan(self):
        """Stable seam over the once-compiled long-text plan."""
        return self._long_text_candidate_seed_plan

    def _public_short_text_candidate_seed_plan(self):
        """Stable seam over the once-compiled short-text plan."""
        return self._short_text_candidate_seed_plan

    def _public_text_candidate_seed_plan(self):
        """Either typed-string regime, whichever value index is available."""
        return (
            self._public_long_text_candidate_seed_plan()
            or self._public_short_text_candidate_seed_plan()
        )

    @property
    def _short_text_candidate_seed_lane(self) -> bool:
        """Compile the three seed plans once per input shape, not per hook call.

        Deciding this recompiles ``_partition_trace_filter_plans`` several
        times over, and the bounded-witness hooks
        (``_scalar_candidate_witness_envelope`` ->
        ``_short_text_seed_witness_slack`` -> here) ask for it on every
        statement and twice inside ``filter_seed_width_policy``.

        The cache is keyed on the inputs that decide the answer, NOT held for
        the life of the builder. An earlier revision of this memo asserted that
        ``filters`` is assigned in ``__init__`` and never rebound; that premise
        is false. ``tracer/selectors/eval_tasks/row_resolver.py`` rebinds
        ``builder.filters`` after construction on a production path, and at
        least five test modules do the same. That rebind is harmless today only
        because its builder carries ``bounded_identity_only=True``, which forces
        every text seed plan below ``_uses_scalar_coordinate_replay`` to None
        whatever the filters say - a coincidence of the current call sites, not
        an invariant. A lane-changing rebind against a life-of-builder cache
        would serve a stale plan, and a stale plan here means a seed statement
        restricted by a predicate the caller has removed: an under-inclusive
        page that silently drops rows.

        So ``_seed_plan_cache_key`` freezes every input the three plans read -
        the list is ``_SEED_PLAN_CACHE_INPUTS``, and a test walks the plans'
        whole call graph and fails if it drifts - and any change to them
        recomputes. Only the
        plans are cached: the slack setting and the width policy are still read
        per statement, so an operator change still takes effect on the next
        read.
        """
        return self._seed_plan_cached("lane", self._compute_short_text_seed_lane)

    def _compute_short_text_seed_lane(self) -> bool:
        return (
            super()._public_scalar_candidate_seed_plan() is None
            and self._public_long_text_candidate_seed_plan() is None
            and self._public_short_text_candidate_seed_plan() is not None
        )

    #: Every instance attribute the three text seed plans read, directly or
    #: through the base-class helpers they call (``_bounded_filters``,
    #: ``_active_non_time_filters``, ``_uses_attribute_coordinate_replay`` and
    #: ``_uses_scalar_coordinate_replay``, ``_candidate_witness_plans``,
    #: ``_positive_exact_end_user_seed_filter``,
    #: ``_positive_relational_seed_filter``). Nothing outside this tuple may be
    #: able to change a plan; ``test_the_seed_plan_cache_key_covers_every_input
    #: _the_plans_read`` walks that call graph and fails if the list drifts.
    _SEED_PLAN_CACHE_INPUTS = (
        "filters",
        "search",
        "sort_params",
        "project_id",
        "project_ids",
        "project_version_id",
        "eval_config_ids",
        "annotation_label_ids",
        "_eval_config_ids_known",
        "_annotation_label_set_known",
        "_bounded_identity_only",
        "_bounded_internal_scan",
        "_bounded_bulk_scan",
        "_bounded_population_proof",
        "_bounded_sampling_rate",
        "_bounded_membership_filters",
        "_bounded_global_span_witnesses",
        "_bounded_include_filter_witnesses",
        # Derived from filters at construction and NOT recomputed on a rebind,
        # so it is a genuine independent input rather than a shadow of
        # ``filters``: a builder whose filters were rebound still answers with
        # the window it was constructed with.
        "_bounded_request_window",
    )

    def _seed_plan_cache_key(self) -> tuple[str, ...]:
        """Freeze every input the three text seed plans read.

        ``repr`` rather than a hash: the filter leaves carry datetimes, UUIDs
        and nested dicts, and an equal-valued rebind whose dicts were built in
        a different insertion order only costs a recompute, never a wrong
        answer. Anything that is not read here must not be able to change the
        plans - which is a claim about a call graph, so a test walks it rather
        than a comment asserting it.
        """

        return tuple(
            repr(getattr(self, name, None)) for name in self._SEED_PLAN_CACHE_INPUTS
        )

    def _seed_plan_cached(self, name: str, compute: Callable[[], Any]) -> Any:
        """Return ``compute()`` memoized for as long as the inputs hold still."""

        key = self._seed_plan_cache_key()
        cached = getattr(self, "_seed_plan_memo", None)
        if cached is None or cached[0] != key:
            cached = (key, {})
            self._seed_plan_memo = cached
        values = cached[1]
        if name not in values:
            values[name] = compute()
        return values[name]

    def _uses_short_text_candidate_seed(self) -> bool:
        """True when the short exact-string lane is the active seed plan."""
        return self._short_text_candidate_seed_lane

    def _public_boolean_candidate_seed_plan(self):
        leaves = self._active_non_time_filters()
        if (
            self.project_version_id is not None
            or not self._uses_scalar_coordinate_replay()
            or len(leaves) != 1
        ):
            return None
        config = leaves[0].get("filter_config") or {}
        if not (
            config.get("col_type") == "SPAN_ATTRIBUTE"
            and config.get("filter_type") == "boolean"
            and config.get("filter_op") == "equals"
            and type(config.get("filter_value")) is bool
        ):
            return None
        plans, residual = self._partition_trace_filter_plans(self._bounded_filters())
        if residual or len(plans) != 1:
            return None
        plan = plans[0]
        # V2 classifies ONE physical tuple, so even false requires a raw row
        # with this typed key and value. Keep the shared legacy/graph default
        # guard unchanged; never prune versions from the exact classifier.
        plan = replace(plan, raw_graph_value_witness_predicate=plan.raw_witness_predicate)
        return plan if self._public_scalar_candidate_witness_predicate(plan) else None

    def _public_scalar_candidate_seed_plan(self):
        return (
            super()._public_scalar_candidate_seed_plan()
            or self._public_text_candidate_seed_plan()
            or self._public_boolean_candidate_seed_plan()
        )

    def filter_candidate_seed_is_optional(self) -> bool:
        if self._public_boolean_candidate_seed_plan() is not None:
            return False
        # Index-assisted long-text acquisition is an ordered, exhaustive
        # necessary root population, followed by the unchanged latest-state
        # classifier. Run it as the actual query plan, not an abortable probe:
        # application reads deliberately do not honor speculative statement
        # time/scan caps. Skipping it there would walk and hydrate unrelated
        # roots until request continuation, without using the available index.
        # Numeric speculative lanes and other seed types retain their own
        # policy. Memory safety, exact ordering and pagination are unchanged.
        if (
            super()._public_scalar_candidate_seed_plan() is None
            and self._public_text_candidate_seed_plan() is not None
        ):
            return False
        # One compiler-proven positive numeric value witness can seed up to ten
        # flat scalar typed-map AND leaves, including negative/absence siblings.
        # This is the required acquisition plan under uncapped reads. It is
        # still only a raw all-child-history superset: root-window ordering
        # and the complete six-key latest-state classifier stay authoritative.
        # Do not promote structured/residual, root/user/relation, versioned,
        # sampled or internal requests to an unbounded speculative global Set.
        active_filters = self._active_non_time_filters()
        if (
            self.project_version_id is None
            and self._uses_scalar_coordinate_replay()
            and 1 <= len(active_filters) <= 10
            and super()._public_scalar_candidate_seed_plan() is not None
            and self._positive_exact_end_user_seed_filter() is None
            and self._positive_relational_seed_filter() is None
        ):
            plans, residual = self._partition_trace_filter_plans(
                self._bounded_filters()
            )
            if (
                not residual
                and len(plans) == len(active_filters)
                and all(
                    plan.scope == "any"
                    and plan.aggregates
                    and all(
                        any(
                            column in sql
                            for column in (
                                "span_attr_num",
                                "span_attr_str",
                                "span_attr_bool",
                            )
                        )
                        for sql in plan.aggregates
                    )
                    for plan in plans
                )
            ):
                return False
        return super().filter_candidate_seed_is_optional()

    def build_filter_candidate_seed_page(self, **kwargs):
        """Acquire one slice of candidate roots. THE CONTRACT this lane obeys.

        Three sentences a reviewer should be able to hold at once, because
        every knob below is a consequence of one of them.

        1. ACQUISITION ONLY. This statement produces a candidate superset. The
           exact latest-state classifier is a separate, unbounded statement and
           it alone decides membership; a published row is always an exact
           any-span match. Nothing here can make a non-match appear.
        2. A SEED STATEMENT NEVER KNOWINGLY READS MORE THAN ~THE ROW BUDGET.
           The width of the slice is chosen from the rows the previous
           statement read (``filter_seed_width_policy``), and any width above
           the unprobed cap must first be costed by a density probe
           (``build_filter_seed_density_probe_query``), which answers from the
           primary index and reads no column data at all. A sparse tail is
           therefore crossed at index cost, not at slice cost.
           Narrowing never skips: slices are contiguous and half-open, so a
           shrunk slice defers its older part to the next adjacent one.
        3. THE WITNESS ENVELOPE IS THE ONE PLACE CANDIDACY IS NARROWED, and it
           is the one behaviour change a user could observe. With
           ``FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS`` above zero (one
           hour by default) a trace is a candidate only if a span carrying the
           value STARTS within that slack of the roots this statement can
           publish. A trace whose only matching span arrives more than the
           slack after its root is omitted from a FILTERED list - it is still
           in the unfiltered list, still fully readable, and still returned by
           the same filter over a window that contains the span's own hour.
           Setting the slack to zero restores the unbounded contract with no
           deploy. A running pagination keeps the slack it started with, which
           the signed cursor carries, so the boundary cannot move mid-page.
        """

        # A long-text witness starts with the requested root population, not
        # every retained trace in the project. The child history is unbounded
        # in time; the root interval only selects necessary immutable trace IDs.
        # No inner LIMIT, raw is_deleted or current-root identity can prune the
        # witness. Stale values remain candidates for exact latest-state replay.
        if (
            super()._public_scalar_candidate_seed_plan() is None
            and self._public_text_candidate_seed_plan() is not None
            and self._positive_exact_end_user_seed_filter() is None
            and self._positive_relational_seed_filter() is None
        ):
            return TraceListQueryBuilder.build_filter_ordered_seed_page(
                self,
                **kwargs,
                _positive_scalar_candidate_first=True,
                _restrict_scalar_root_population=True,
            )
        return super().build_filter_candidate_seed_page(**kwargs)

    def supports_filter_seed_density_probe(self) -> bool:
        """Only the row-budgeted short exact-string lane probes for density."""

        return self._uses_short_text_candidate_seed()

    def build_filter_seed_density_probe_query(
        self, *, slice_start: datetime, slice_end: datetime
    ) -> tuple[str, dict[str, Any]]:
        """Estimate, from the primary index alone, how dense a candidate is.

        This is the density proof ``FilterSeedWidthPolicy`` requires before the
        row budget may issue a slice wider than its unprobed cap. The reactive
        doubling rule sizes the next slice from the PREVIOUS one's read rows,
        so it crosses a sparse tail cheaply and then drops its accumulated
        width onto whatever comes next; measured in production, eight doublings
        over a 166 h sparse tail issued a 128 h slice that read over 12 GiB in
        one statement. This probe is what makes that impossible: the widened
        slice is costed before it is issued, and shrunk to fit the budget.

        IT READS NO COLUMN DATA. ``EXPLAIN ESTIMATE`` answers from the primary
        index: for every part the key condition selects it reports the parts,
        granules (``marks``) and ``rows`` that a real statement WOULD read.
        That is exactly the question the width policy asks, and it is a
        different order of cost from asking it with ``count()``: the counting
        form reads the smallest column of every row in the slice, which on the
        measured 128 h proposal was 20.5M rows / 492 MB / 2.1 s at one worker.
        The index form reads marks.

        Deliberately the cheapest statement that answers the question:

        * the key condition is ``project_id`` plus a half-open ``start_time``
          range over ``[hour_floor(start), hour_ceil(end))`` clamped to the
          request window. Both ends are whole hours, so the range is expressed
          exactly by the ``toStartOfHour(start_time)`` primary-key component
          the server prunes on - the same shape, and the same reasoning, as
          this lane's own witness envelope. Rounding OUT rather than in keeps
          the estimate an upper bound on the slice it is approving, so it can
          only make the issued slice narrower, never wider;
        * no attribute predicate, no typed-Map access, no child witness, no
          ``FINAL``, no ordering, no per-trace sets, and in particular NO
          ``IN`` subquery: ``EXPLAIN`` executes a scalar/``IN`` subquery to
          plan around it, which would put a real read back inside a statement
          whose whole purpose is not to have one;
        * no ``is_deleted`` predicate either. It is not part of the primary
          key, so it could not narrow an index estimate; leaving it out keeps
          the statement honest about what it measures - every physical row in
          the granules the slice touches, tombstones and stale versions
          included, which is what the seed would have to walk past.

        The estimate is an UPPER BOUND, twice over: whole granules, and every
        physical version inside them. Both errors point the same way, at a
        narrower issued slice.

        DO NOT "IMPROVE" THE TIME PREDICATE INTO ``toStartOfHour(start_time)``.
        It looks like the tighter spelling of the primary-key component, and
        it would silently break this statement in the one direction that is
        not safe. ``spans`` carries aggregate PROJECTIONs keyed on
        ``(project_id, toStartOfHour(start_time) AS hour, ...)`` (schema 002
        and 007). They do not store ``start_time``, so a predicate on the raw
        column cannot be answered from them and the estimate describes the
        base table - which is what the production measurement of the counting
        form showed, at 492 MB of base-table reads. Spelled as ``hour``, the
        optimizer could route the statement to a projection instead, and
        ``rows`` would then be that projection's AGGREGATE rows: orders of
        magnitude smaller than the slice, an estimate far below the budget,
        and the widest possible slice APPROVED. Raw ``start_time`` bounds are
        also what this lane's own seed and witness emit, and the hour-aligned
        range prunes identically through the key expression's monotonicity -
        plus the table's ``PARTITION BY toDate(start_time)``, which bounds the
        parts the estimate can even consider.

        One consequence of the ``EXPLAIN`` prefix worth stating: the v2
        rewrite boundary appends its required ``SETTINGS`` only to statements
        beginning ``SELECT``/``WITH``, so this one carries none. Both settings
        it would add are inert here - there is no ``FINAL`` for
        ``use_skip_indexes_if_final`` to protect, and ``optimize_use_projections``
        already defaults to on while the paragraph above keeps projections out
        of reach.

        It answers a COST question only. It never decides membership, never
        prunes candidates and never reaches the published page: shrinking a
        slice defers its older part to the next contiguous slice, so the scan
        stays exact whatever the probe says, and a probe that fails or returns
        an unreadable shape simply pins the width at the unprobed cap.

        ON THE CAPS THIS STATEMENT CARRIES. The selector clamps the probe to
        one worker and one gibibyte of memory, and asks for a one-second
        deadline and a one-gibibyte byte cap. Measured in production, only the
        first two reach the server: ``application_read_settings`` zeroes the
        byte caps and ``timeout_ms`` is not a statement deadline on the
        application read path. That gap mattered while the probe was a count;
        for an index estimate it is close to moot, because there is no data
        read for a byte cap to bound.
        """

        request_start, request_end = self.parse_time_range(self.filters)
        if not request_start <= slice_start < slice_end <= request_end:
            raise ValueError("seed density probe must stay inside the request window")
        if not self.supports_filter_seed_density_probe():
            raise ValueError("seed density probe is unavailable")
        probe_start = max(request_start, _floor_hour(slice_start))
        probe_end = min(request_end, _ceil_hour(slice_end))
        params = {
            **self.params,
            "seed_density_start_us": _unix_microseconds(probe_start),
            "seed_density_end_us": _unix_microseconds(probe_end),
        }
        return (
            f"""
            EXPLAIN ESTIMATE
            SELECT count()
            FROM {self.TABLE}
            WHERE {self.project_filter_sql()}
              AND start_time >= fromUnixTimestamp64Micro(%(seed_density_start_us)s)
              AND start_time < fromUnixTimestamp64Micro(%(seed_density_end_us)s)
            """,
            params,
        )

    def filter_seed_density_probe_estimate(
        self,
        rows: Iterable[Mapping[str, Any]],
        columns: Iterable[str] | None = None,
    ) -> int | None:
        """Reduce one ``EXPLAIN ESTIMATE`` result to the policy's row bound.

        The statement above returns the estimate table, one row per table it
        would read: ``database``, ``table``, ``parts``, ``rows``, ``marks``.
        The policy consumes a single integer, so the lane that emitted the
        statement is also the one that says how to read it back.

        Two shapes have to be told apart, and the ``columns`` the transport
        reports are what tells them apart - not the row count:

        * the estimate table with NO rows is the answer ``zero``. A key
          condition that selects no part at all is exactly the sparse-tail
          case this lane crosses by doubling, and reading it as "unknown"
          would pin the tail at the unprobed cap - the regression the row
          budget exists to avoid;
        * anything else - a transport that answered something other than this
          statement, or a server whose estimate table changed shape - is
          ``None``, meaning unknown, and the caller keeps the unprobed cap.

        Only this builder's own table counts toward the estimate. A statement
        naming one table can only answer for one table; an extra row would
        mean the result is not the one this method is documented to read.
        """

        names = {str(name) for name in (columns or ())}
        if not _SEED_DENSITY_ESTIMATE_COLUMNS.issubset(names):
            return None
        estimate = 0
        for row in rows or ():
            if not isinstance(row, Mapping):
                return None
            if str(row.get("table") or "") != self.TABLE:
                return None
            counted = row.get("rows")
            if isinstance(counted, bool) or not isinstance(counted, (int, float)):
                return None
            estimate += max(0, int(counted))
        return estimate

    def supports_filter_windowed_candidate_seed_page(self) -> bool:
        return bool(
            self._uses_scalar_coordinate_replay()
            # Text already starts with this population. Do not repeat the same
            # failed query before falling back to the exact ordered-root walk.
            # The selector also offers this fallback only for an *optional*
            # candidate seed, and both text regimes are required, so the gate
            # is unreachable for them by construction. Application reads
            # deliberately strip statement time/scan caps, so there is no
            # in-statement recovery to fall back to either: the short lane's
            # declared row budget is what bounds a text seed's working set.
            and super()._public_scalar_candidate_seed_plan() is not None
        )

    def build_filter_windowed_candidate_seed_page(self, **kwargs):
        """One alternative after a failed global seed, never an unconditional scan."""
        if not self.supports_filter_windowed_candidate_seed_page():
            raise ValueError("windowed scalar candidate seed is unavailable")
        return TraceListQueryBuilder.build_filter_ordered_seed_page(
            self,
            **kwargs,
            _positive_scalar_candidate_first=True,
            _restrict_scalar_root_population=True,
        )

    def recommended_filter_candidate_witness_fallback_classify_batch_size(
        self,
    ) -> int | None:
        if self._uses_attribute_coordinate_replay():
            return 200
        return (
            super().recommended_filter_candidate_witness_fallback_classify_batch_size()
        )

    def prefer_filter_candidate_witness_probe_first(self) -> bool:
        # The old finite Map probe can exceed its read cap before the exact
        # indexed replay would finish. Avoid that extra scan on this lane.
        return (
            False
            if self._uses_attribute_coordinate_replay()
            else super().prefer_filter_candidate_witness_probe_first()
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
        ``(latest_start_time, id, observation_type, service_name)``. Versions
        collapse on the six-part storage key with one coherent tuple before
        tombstones and key presence are evaluated. Therefore result cardinality
        is bounded by ``page trace identities * requested keys`` regardless of
        historical span/value fanout, without sampling or truncation.

        It intentionally does not reuse the list's date window: child spans can
        start more than a day after their root. Exact page-scoped project/trace
        pairs are the membership boundary, including in organization mode where
        trace ids are customer controlled and can collide across tenants.
        Conflicting equal-version rows have no unique winner; this does not
        promise equivalence to FINAL's unspecified tie selection.
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
            argMax(candidate_attribute_value_json,
                tuple(latest_start_time, id, observation_type, service_name))
                AS attribute_value_json
        FROM (
            SELECT
                project_id,
                trace_id,
                id,
                observation_type,
                service_name,
                latest_start_time,
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
                    observation_type,
                    service_name,
                    argMax(tuple(start_time, attributes_extra, attrs_string,
                        attrs_number, attrs_bool, is_deleted), _version) AS latest_span,
                    latest_span.1 AS latest_start_time,
                    latest_span.2 AS latest_attributes_extra,
                    latest_span.3 AS latest_attrs_string,
                    latest_span.4 AS latest_attrs_number,
                    latest_span.5 AS latest_attrs_bool,
                    latest_span.6 AS latest_is_deleted
                FROM {self.TABLE}
                PREWHERE (toString(project_id), trace_id)
                    IN %(attr_trace_identities)s
                  AND {self.project_filter_sql()}
                GROUP BY project_id, observation_type, service_name,
                    toStartOfHour(start_time), trace_id, id
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


class TraceListQueryBuilderV2(V2RewriteMixin, _TraceListQueryBuilderV2Core):
    """Public CH25 trace builder: rewrite the shared planner's output once."""

    # The mixin precedes the core in the MRO and defines an empty default.
    # Declare exclusions at the public boundary so eval/annotation SQL keeps
    # its independent source contract, including nested eval replay queries.
    _v2_rewrite_exclude = _TraceListQueryBuilderV2Core._v2_rewrite_exclude


__all__ = [
    "TraceListQueryBuilderV2",
]
