"""Tenant-scoped unit aggregation and bounded streaming, not Python populations."""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING

from tracer.services.eval_tasks.sampling.policy import (
    RATE_DENOMINATOR,
    parse_time,
    rate_units,
    threshold,
)

if TYPE_CHECKING:
    from tracer.models.eval_task import EvalTask

BATCH_SIZE = 1000
READ_SETTINGS = {
    "max_execution_time": 30,
    "max_query_size": 2097152,
    "optimize_move_to_prewhere": 0,
    "query_plan_merge_expressions": 0,
    "timeout_overflow_mode": "throw",
    "max_threads": 2,
    "max_result_bytes": 134217728,
    "max_bytes_to_read": 8589934592,
    "max_memory_usage": 536870912,
    "max_rows_to_read": 20_000_000,
    "read_overflow_mode": "throw",
    "max_bytes_before_external_group_by": 134217728,
    "max_bytes_before_external_sort": 134217728,
    "max_result_rows": 2_000_000,
    "result_overflow_mode": "throw",
}


class SamplingParams(dict):
    """Bound statement parameters plus the statement's own read contract.

    ``policy`` lets typed errors name the policy's attribute keys, ``proof``
    marks a live first-admission proof and ``read_settings`` may only tighten
    the sampling read budget.
    """

    policy: dict | None = None
    proof: bool = False
    read_settings: dict | None = None


class SamplingQuery:
    def __init__(
        self,
        task: EvalTask,
        policy: dict,
        version: str,
        snapshot: datetime,
        *,
        scan_window: dict | None = None,
    ) -> None:
        self.task, self.policy, self.version = task, policy, version
        self.scan_window = scan_window or (
            policy["window"]
            if policy["mode"] == "closed"
            else {
                "start": max(
                    parse_time(policy["window"]["start"]), snapshot - timedelta(days=1)
                ).isoformat(),
                "end": snapshot.isoformat(),
            }
        )
        self.params = SamplingParams(
            sp_project=str(task.project_id),
            sp_version=version,
            sp_start=parse_time(self.scan_window["start"]),
            sp_end=parse_time(self.scan_window["end"]),
            sp_snapshot=snapshot,
        )
        self.params.policy = policy
        self._counter = 0
        self.live_floor = None
        self.proof_row_ids = None
        self.proof_unit_ids = None
        self.member_unit_ids = None
        self.current_state = False

    def bind(self, value: object) -> str:
        self._counter += 1
        key = f"sp_value_{self._counter}"
        self.params[key] = value
        return f"%({key})s"

    def attribute(self, item: dict) -> tuple[str, str]:
        key = self.bind(item["key"])
        if item.get("source", "span") == "span":
            exists = f"(mapContains(s.attrs_string, {key}) OR mapContains(s.attrs_number, {key}) OR mapContains(s.attrs_bool, {key}) OR JSONHas(toJSONString(s.attributes_extra), {key}))"
            value = f"multiIf(mapContains(s.attrs_string, {key}), s.attrs_string[{key}], mapContains(s.attrs_number, {key}), toString(s.attrs_number[{key}]), mapContains(s.attrs_bool, {key}), if(s.attrs_bool[{key}], 'true', 'false'), JSON_VALUE(toJSONString(s.attributes_extra), {self._json_path(item['key'])}))"
        else:
            column = "resource_attrs" if item["source"] == "resource" else "metadata"
            exists = f"JSONHas(toJSONString(s.{column}), {key})"
            value = (
                f"JSON_VALUE(toJSONString(s.{column}), {self._json_path(item['key'])})"
            )
        return exists, value

    def _json_path(self, key: str) -> str:
        import json

        return self.bind("$." + json.dumps(key))

    def predicate(self, item: dict) -> str:
        exists, value = self.attribute(item)
        op = item["op"]
        if op in ("exists", "missing"):
            return exists if op == "exists" else f"NOT {exists}"
        if op in ("gt", "gte", "lt", "lte"):
            symbol = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
            return f"({exists} AND toFloat64OrNull({value}) {symbol} {self.bind(item['values'][0])})"
        values = tuple(
            str(v).lower() if isinstance(v, bool) else str(v) for v in item["values"]
        )
        symbol = "IN" if op == "in" else "NOT IN"
        return f"({exists} AND {value} {symbol} {self.bind(values)})"

    def _rule_case(self, values: list[str]) -> str:
        args = []
        for index, value in enumerate(values[:-1]):
            args.extend([f"rule_index = {index}", value])
        return f"multiIf({', '.join([*args, values[-1]])})" if args else values[-1]

    def _source(self) -> str:
        from tracer.selectors.eval_tasks.row_resolver import _build_sample_query
        from tracer.services.clickhouse.v2.id_remap_sql import resolved_id_expr

        filters = deepcopy(self.task.filters or {})
        filters["filters"] = list(filters.get("filters") or [])
        filters["filters"].append(
            {
                "column_id": "start_time",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [
                        self.scan_window["start"],
                        self.scan_window["end"],
                    ],
                },
            }
        )
        eligible, params = _build_sample_query(
            project_id=str(self.task.project_id),
            row_type=self.task.row_type,
            salt=self.version,
            sampling_rate=100,
            filters=filters,
            limit=None,
            source_table="sampling_source",
        )
        self.params.update(params)
        # Remap groups are expanded only for ids touched by this scoped window.
        maps = []
        for kind, column, table in (
            ("session", "trace_session_id", "trace_session_id_remap"),
            ("user", "end_user_id", "end_user_id_remap"),
        ):
            maps.append(f"""{kind}_map AS (
                SELECT any_id, argMin(survivor_id, toString(survivor_id)) AS survivor_id FROM (
                    SELECT arrayJoin(arrayDistinct(arrayConcat(groupArray(old_id), [new_id]))) AS any_id,
                           argMin(old_id, toString(old_id)) AS survivor_id
                    FROM {table} FINAL WHERE new_id IN (
                        SELECT new_id FROM {table} FINAL
                        WHERE old_id IN (SELECT {column} FROM raw) OR new_id IN (SELECT {column} FROM raw)
                    ) GROUP BY new_id
                ) GROUP BY any_id
            )""")
        session = "toString(s.trace_session_id)"
        user = "toString(s.end_user_id)"
        unit = self.policy["sampling_unit"]
        if unit == "attribute":
            exists, value = self.attribute(self.policy["unit_attribute"])
            identity = f"if({exists}, {value}, '')"
        else:
            identity = {
                "span": "s.id",
                "trace": "s.trace_id",
                "session": session,
                "user": user,
            }[unit]
        row_type = self.task.row_type
        row_id = {"spans": "s.id", "traces": "s.trace_id", "sessions": session}[
            row_type
        ]
        valid_id = f"({identity} IS NOT NULL AND {identity} NOT IN ('', '00000000-0000-0000-0000-000000000000'))"
        fallback_unit = {"spans": "span", "traces": "trace", "sessions": "session"}[
            row_type
        ]
        fallback = (
            "''"
            if self.policy["missing_id"] == "exclude"
            else f"concat('{fallback_unit}:', {row_id})"
        )
        unit_id = (
            f"if({valid_id}, concat('{unit}:', assumeNotNull({identity})), {fallback})"
        )
        rules = self.policy["strata"]
        conditions = []
        for index, rule in enumerate(rules):
            conditions.extend(
                [
                    " AND ".join(f"({self.predicate(p)})" for p in rule["filters"])
                    or "1",
                    str(index),
                ]
            )
        rule_expr = (
            f"multiIf({', '.join([*conditions, str(len(rules))])})"
            if conditions
            else "0"
        )
        groups = []
        for item in self.policy["group_by"]:
            exists, value = self.attribute(item)
            groups.append(f"toJSONString(tuple({exists}, {value}))")
        groups_expr = (
            "[" + ", ".join(groups) + "]" if groups else "CAST([], 'Array(String)')"
        )
        root = "" if row_type == "spans" else "AND s.parent_span_id = ''"
        eligible_id = {"spans": "id", "traces": "trace_id", "sessions": "session_id"}[
            row_type
        ]
        from tracer.services.clickhouse.v2.query_builders.span_list import (
            SpanListQueryBuilderV2,
        )

        hour_scope = """toStartOfHour(start_time) >= toStartOfHour(toDateTime64(%(sp_start)s, 6, 'UTC'))
            AND toStartOfHour(start_time) <= toStartOfHour(toDateTime64(%(sp_end)s, 6, 'UTC') - INTERVAL 1 MICROSECOND)"""
        root_traces = f"trace_id IN (SELECT trace_id FROM spans PREWHERE project_id = %(sp_project)s AND {hour_scope} WHERE parent_span_id = '')"
        scope = f"project_id = %(sp_project)s AND {hour_scope}"
        if row_type != "spans":
            # Row filters can match a child outside the root's event interval.
            # Replay complete traces, not only the root-window slice.
            scope = f"project_id = %(sp_project)s AND {root_traces}"
        elif self.live_floor is not None:
            self.params["sp_arrival_floor_ns"] = (
                int(self.live_floor.timestamp() * 1_000_000) * 1000
            )
            # A version lower bound cannot discard the maximum version of an
            # identity that has a change in this delta. Never push an upper
            # bound, tombstone or mutable attribute below latest-state replay.
            scope += " AND _version >= %(sp_arrival_floor_ns)s"
        if self.proof_unit_ids is not None or self.member_unit_ids is not None:
            from tracer.services.eval_tasks.sampling.unit_scope import (
                physical_unit_scope,
            )

            attribute_value = None
            if unit == "attribute":
                exists, value = self.attribute(self.policy["unit_attribute"])
                attribute_value = f"if({exists}, {value}, '')".replace("s.", "")
            snapshot = self.params["sp_snapshot"]
            self.params["sp_snapshot_ns"] = int(snapshot.timestamp() * 1_000_000) * 1000
            # Every seed scan is bounded to the eligibility hours (immutable
            # key coordinates shared by all versions) and to arrivals before
            # the snapshot, which the latest-state watermark excludes anyway.
            seed_bounds = f"{hour_scope} AND _version < %(sp_snapshot_ns)s"
            if self.proof_unit_ids is not None:
                unit_scope, unit_params = physical_unit_scope(
                    unit_ids=self.proof_unit_ids,
                    sampling_unit=unit,
                    row_type=row_type,
                    attribute_value_sql=attribute_value,
                    bounds_sql=seed_bounds,
                )
                scope = (
                    unit_scope
                    if row_type == "spans"
                    else f"{unit_scope} AND {root_traces}"
                )
            else:
                # Closed expansion: read only this batch's frozen members, not
                # the whole window once per batch. Whole traces (or sessions
                # with their remap aliases) keep row filters identical to a
                # full-window read.
                unit_scope, unit_params = physical_unit_scope(
                    unit_ids=self.member_unit_ids,
                    sampling_unit=unit,
                    row_type=row_type,
                    attribute_value_sql=attribute_value,
                    bounds_sql=seed_bounds,
                    span_closure="trace",
                    param_prefix="sp_member",
                )
                scope = f"({scope}) AND ({unit_scope})"
            self.params.update(unit_params)
        if self.proof_row_ids is not None:
            self.params["sp_current_ids"] = self.proof_row_ids
            if row_type == "spans":
                scope = "project_id = %(sp_project)s AND id IN %(sp_current_ids)s"
            elif row_type == "traces":
                scope = "project_id = %(sp_project)s AND trace_id IN %(sp_current_ids)s"
            else:
                from tracer.services.clickhouse.v2.id_remap_sql import (
                    bounded_survivor_map_subquery,
                )

                aliases = bounded_survivor_map_subquery(
                    "trace_session_id_remap", candidate_param="sp_current_ids"
                )
                scope = f"""project_id = %(sp_project)s AND trace_id IN (
                    SELECT trace_id FROM spans PREWHERE project_id = %(sp_project)s
                    WHERE trace_session_id IN %(sp_current_ids)s OR trace_session_id IN (
                        SELECT any_id FROM ({aliases}) WHERE survivor_id IN %(sp_current_ids)s
                    ))"""
        arrival = "greatest(created_at, updated_at, fromUnixTimestamp64Nano(toInt64(_version), 'UTC'))"
        consumer = " ".join(
            [
                eligible,
                unit_id,
                rule_expr,
                groups_expr,
                arrival,
                "project_id trace_id id start_time parent_span_id trace_session_id end_user_id is_deleted",
            ]
        )
        latest = SpanListQueryBuilderV2(
            project_id=str(self.task.project_id)
        )._latest_state_source_sql(
            "sampling_latest",
            scope_sql=scope,
            consumer_sql=consumer,
        )
        from tracer.services.eval_tasks.sampling.errors import (
            MAX_GROUP_VALUE_BYTES,
            MAX_UNIT_ID_BYTES,
            OVERSIZED_GROUP_MARKER,
            OVERSIZED_UNIT_MARKER,
        )

        # Oversized identities fail the read with a constant marker naming
        # which bound (and which group-by position) was exceeded. Values are
        # never truncated, hashed or silently dropped from the population.
        bounds_check = "".join(
            [
                f"\n              AND throwIf(length(p.unit_id) > {MAX_UNIT_ID_BYTES}, '{OVERSIZED_UNIT_MARKER}') = 0",
                *(
                    f"\n              AND throwIf(length(p.groups[{index + 1}]) > {MAX_GROUP_VALUE_BYTES}, '{OVERSIZED_GROUP_MARKER}{index}') = 0"
                    for index in range(len(self.policy["group_by"]))
                ),
            ]
        )
        canonical_session = resolved_id_expr("s.trace_session_id", "sm")
        canonical_user = resolved_id_expr("s.end_user_id", "um")
        watermark = (
            ""
            if self.current_state
            else "AND created_at < %(sp_snapshot)s AND source_arrival < %(sp_snapshot)s"
        )
        return f"""WITH raw AS (
            SELECT *, {arrival} AS source_arrival FROM {latest}
            WHERE is_deleted = 0 {watermark}
        ), {", ".join(maps)}, sampling_source AS (
            SELECT s.* EXCEPT(trace_session_id, end_user_id),
                   {canonical_session} AS trace_session_id, {canonical_user} AS end_user_id
            FROM raw AS s
            LEFT JOIN session_map AS sm ON s.trace_session_id = sm.any_id
            LEFT JOIN user_map AS um ON s.end_user_id = um.any_id
        ), eligible AS ({eligible}), projected AS (
            SELECT {row_id} AS row_id, {unit_id} AS unit_id, {rule_expr} AS rule_index,
                   {groups_expr} AS groups, s.start_time AS event_time, s.id AS span_id, s.source_arrival AS arrival
            FROM sampling_source AS s
            WHERE {row_id} IN (SELECT {eligible_id} FROM eligible) {root}
              AND s.start_time >= %(sp_start)s AND s.start_time < %(sp_end)s
        ), rows AS (
            SELECT p.row_id AS row_id, argMin(p.unit_id, tuple(p.event_time, p.span_id)) AS unit_id,
                   min(p.rule_index) AS rule_index,
                   argMin(p.groups, tuple(p.rule_index, p.event_time, p.span_id)) AS groups,
                   min(p.event_time) AS event_time, max(p.arrival) AS arrival
            FROM projected AS p WHERE p.row_id != '' AND p.row_id != '00000000-0000-0000-0000-000000000000'{bounds_check}
            GROUP BY p.row_id
        ), units AS (
            SELECT r.unit_id AS unit_id, min(r.rule_index) AS rule_index,
                   argMin(r.groups, tuple(r.rule_index, r.event_time, r.row_id)) AS groups,
                   count() AS evaluation_rows
            FROM rows AS r WHERE r.unit_id != '' GROUP BY r.unit_id
        )"""

    def _classified(self) -> str:
        source = self._source()
        rules = [*self.policy["strata"], self.policy["default"]]
        names = [self.bind(rule.get("name", "default")) for rule in rules]
        rule_name = self._rule_case(names)
        rates = self._rule_case(
            [self.bind(rule.get("rate", 100) / 100) for rule in rules]
        )
        thresholds = self._rule_case(
            [self.bind(threshold(rule.get("rate", 100))) for rule in rules]
        )
        target = []
        for rule in rules:
            count = (
                f"toUInt64({self.bind(rule['target_n'])})"
                if "target_n" in rule
                else f"toUInt64(intDiv(toUInt128(population_size) * toUInt128({self.bind(rate_units(rule.get('rate', 100)))}) + toUInt128({RATE_DENOMINATOR - 1}), toUInt128({RATE_DENOMINATOR})))"
            )
            if "rare_below" in rule:
                count = f"if(population_size <= {self.bind(rule['rare_below'])}, population_size, {count})"
            cap = (
                f"toUInt64({self.bind(rule['max_n'])})"
                if "max_n" in rule
                else "population_size"
            )
            target.append(
                f"least(population_size, {cap}, greatest(toUInt64({self.bind(rule.get('min_n', 0))}), {count}))"
            )
        k = self._rule_case(target)
        if self.policy["mode"] == "closed":
            probability, selected = (
                "toFloat64(sample_size) / population_size",
                "ordinal <= sample_size",
            )
        else:
            probability, selected = rates, f"concat('0', unit_hash) < {thresholds}"
        labelled = f"""{source}, labelled AS (
            SELECT *, toJSONString(tuple({rule_name}, groups)) AS stratum,
                   hex(SHA256(concat(%(sp_version)s, ':', unit_id))) AS unit_hash FROM units
        )"""
        if self.policy["mode"] == "live":
            return (
                labelled
                + f", classified AS (SELECT *, {probability} AS inclusion_probability, {selected} AS selected FROM labelled)"
            )
        return f"""{labelled}, ranked AS (
            SELECT *, count() OVER (PARTITION BY stratum) AS population_size,
                   row_number() OVER (PARTITION BY stratum ORDER BY unit_hash, unit_id) AS ordinal
            FROM labelled
        ), sized AS (SELECT *, {k} AS sample_size FROM ranked), classified AS (
            SELECT *, {probability} AS inclusion_probability, {selected} AS selected FROM sized
        )"""

    def members(self) -> tuple[str, dict]:
        if self.policy["mode"] != "closed":
            raise ValueError("Frozen membership requires a closed sampling window.")
        ctes = self._classified()
        return (
            ctes
            + " SELECT unit_id, stratum, inclusion_probability, population_size FROM classified WHERE selected",
            self.params,
        )

    def rows(
        self,
        *,
        arrival_floor: datetime | None = None,
        unit_ids: list[str] | None = None,
    ) -> tuple[str, dict]:
        self.live_floor = arrival_floor if self.policy["mode"] == "live" else None
        if unit_ids is not None:
            from tracer.services.eval_tasks.sampling.unit_scope import unit_batches

            identities = list(dict.fromkeys(str(unit) for unit in unit_ids))
            if (
                self.policy["mode"] != "closed"
                or not identities
                or len(list(unit_batches(identities))) != 1
            ):
                raise ValueError("Member expansion requires one bounded closed page.")
            # Push the page's units into every physical read; the exact
            # canonical membership test below still runs after latest replay.
            self.member_unit_ids = identities
        ctes = self._source() if self.policy["mode"] == "closed" else self._classified()
        predicates = ["r.unit_id != ''"]
        if unit_ids is not None:
            self.params["sp_member_units"] = tuple(self.member_unit_ids)
            predicates.append("r.unit_id IN %(sp_member_units)s")
        if arrival_floor is not None:
            self.params["sp_floor"] = arrival_floor
            predicates.append("r.arrival >= %(sp_floor)s")
        predicate = "WHERE " + " AND ".join(predicates)
        if self.policy["mode"] == "closed":
            # Frozen membership/probabilities come from PostgreSQL. Never rank
            # a new first-N population on subsequent polls.
            return (
                ctes
                + f" SELECT r.row_id, r.unit_id, '', 0, 0 FROM rows AS r {predicate}",
                self.params,
            )
        return (
            ctes
            + f""" SELECT r.row_id, r.unit_id, c.stratum, c.inclusion_probability, c.selected
            FROM rows AS r INNER JOIN classified AS c ON r.unit_id = c.unit_id {predicate}""",
            self.params,
        )

    def live_member_decisions(self, unit_ids: list[str]) -> tuple[str, dict]:
        """Classify only new candidate units across their full eligible snapshot.

        Event-day discovery never determines a unit's final stratum. Candidate
        identities and parameter bytes are finite; unit aggregation, earliest
        group witness and hash selection remain entirely inside ClickHouse.
        """
        from tracer.services.eval_tasks.sampling.unit_scope import unit_batches

        identities = list(dict.fromkeys(unit_ids))
        if (
            self.policy["mode"] != "live"
            or not identities
            or len(identities) > BATCH_SIZE
            or len(list(unit_batches(identities))) != 1
        ):
            raise ValueError("Live decisions require one bounded candidate-unit page.")
        lookback = self.params["sp_snapshot"] - parse_time(
            self.policy["window"]["start"]
        )
        if lookback > live_proof_max_lookback():
            from tracer.services.eval_tasks.sampling.errors import (
                SamplingProofBoundExceeded,
            )

            # The proof must classify a new contact across its whole eligible
            # history. Refuse rather than certify a classification of a slice.
            raise SamplingProofBoundExceeded(
                "This live policy started more than "
                f"{live_proof_max_lookback().days} days before the current "
                "snapshot, beyond the configured first-admission proof bound. "
                "Start a new live policy with a later start; no partial decision "
                "was saved."
            )
        self.proof_unit_ids = identities
        self.params.proof = True
        self.params.read_settings = live_proof_settings()
        self.live_floor = None
        self.scan_window = {
            "start": self.policy["window"]["start"],
            "end": self.params["sp_snapshot"].isoformat(),
        }
        self.params["sp_start"] = parse_time(self.scan_window["start"])
        self.params["sp_end"] = self.params["sp_snapshot"]
        self.params["sp_new_units"] = tuple(identities)
        source = self._classified()
        return (
            source
            + " SELECT unit_id, stratum, inclusion_probability, selected FROM classified WHERE unit_id IN %(sp_new_units)s",
            self.params,
        )

    def current_rows(self, row_ids: list[str]) -> tuple[str, dict]:
        """Exact current-state proof for bounded pending IDs, not an arrival delta.

        Missing rows are negative only after this point-scoped latest replay;
        absence from a poll/selected-unit page alone is never such a proof.
        """
        identities = tuple(dict.fromkeys(str(value) for value in row_ids))
        if not identities or len(identities) > BATCH_SIZE:
            raise ValueError("Pending row proofs require 1–1000 identities.")
        self.proof_row_ids = identities
        self.current_state = True
        self.live_floor = None
        if self.policy["mode"] == "live":
            self.scan_window = {
                "start": self.policy["window"]["start"],
                "end": self.params["sp_snapshot"].isoformat(),
            }
            self.params["sp_start"] = parse_time(self.scan_window["start"])
        source = self._source()
        return (
            source
            + " SELECT r.row_id, r.unit_id FROM rows AS r WHERE r.row_id IN %(sp_current_ids)s",
            self.params,
        )

    def preview(self) -> tuple[str, dict]:
        self.live_floor = None
        ctes = self._classified()
        # One extra cell proves overflow rather than silently truncating strata.
        return (
            ctes
            + """ SELECT stratum, count() AS eligible_units, sum(evaluation_rows) AS eligible_rows,
            sum(inclusion_probability) AS expected_units,
            sum(evaluation_rows * inclusion_probability) AS expected_rows,
            countIf(selected) AS selected_units, sumIf(evaluation_rows, selected) AS selected_rows
            FROM classified GROUP BY stratum ORDER BY stratum LIMIT 1001""",
            self.params,
        )


def native_parameters(params: dict) -> dict:
    """Keep DateTime64 microseconds: native driver datetime escaping truncates."""
    return {
        key: (
            value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        ).strftime("%Y-%m-%d %H:%M:%S.%f")
        if isinstance(value, datetime)
        else value
        for key, value in params.items()
    }


def live_proof_max_lookback() -> timedelta:
    """Configured bound on a live first-admission proof's eligibility span."""

    from django.conf import settings

    days = int(getattr(settings, "EVAL_SAMPLING_LIVE_PROOF_MAX_DAYS", 366))
    if not 1 <= days <= 3660:
        raise ValueError("EVAL_SAMPLING_LIVE_PROOF_MAX_DAYS must be 1-3660.")
    return timedelta(days=days)


def live_proof_settings() -> dict:
    """Read ceiling for one live first-admission proof page."""

    from django.conf import settings

    rows = int(
        getattr(
            settings,
            "EVAL_SAMPLING_LIVE_PROOF_MAX_ROWS",
            READ_SETTINGS["max_rows_to_read"],
        )
    )
    if not 1 <= rows <= READ_SETTINGS["max_rows_to_read"]:
        raise ValueError(
            "EVAL_SAMPLING_LIVE_PROOF_MAX_ROWS must not exceed the sampling read ceiling."
        )
    return {"max_rows_to_read": rows}


def stream_query(
    sql: str,
    params: dict,
    *,
    policy: dict | None = None,
    proof: bool = False,
    settings: dict | None = None,
) -> Iterator[list[tuple]]:
    from tracer.services.clickhouse.application_read_policy import (
        application_read_context,
    )
    from tracer.services.clickhouse.v2.query_service import get_v2_query_client
    from tracer.services.eval_tasks.sampling.errors import classify_read_error

    if len(sql.encode("utf-8")) > READ_SETTINGS["max_query_size"]:
        raise ValueError(
            "Sampling query exceeds its compiler budget. Reduce the number of slices."
        )
    policy = policy if policy is not None else getattr(params, "policy", None)
    proof = proof or bool(getattr(params, "proof", False))
    settings = (
        settings if settings is not None else getattr(params, "read_settings", None)
    )
    read_settings = {**READ_SETTINGS, **(settings or {})}
    if any(
        key in read_settings and read_settings[key] > READ_SETTINGS[key]
        for key in (
            "max_execution_time",
            "max_rows_to_read",
            "max_bytes_to_read",
            "max_memory_usage",
        )
    ):
        raise ValueError("Sampling read settings may only tighten the read budget.")

    # This is an explicitly bounded cohort proof. Outer application mode drops
    # abort caps, so opt out rather than silently losing the selection budget.
    try:
        with application_read_context(enabled=False):
            with get_v2_query_client().execute_read_block_stream(
                sql,
                native_parameters(params),
                timeout_ms=30_000,
                block_size=BATCH_SIZE,
                settings=read_settings,
            ) as blocks:
                yield from blocks
    except Exception as exc:
        typed = classify_read_error(exc, policy=policy, proof=proof)
        if typed is None:
            raise
        raise typed from exc


def live_scan_windows(
    task: EvalTask, policy: dict, ceiling: datetime, floor: datetime | None
) -> Iterator[dict]:
    """Discover only event days touched by a bounded physical-arrival delta.

    _version is the CH25 ingestion/change clock, including backdated spans;
    event timestamps only choose which partition-sized follow-up reads to run.
    """
    params = {
        "project": str(task.project_id),
        "start": parse_time(policy["window"]["start"]),
        "ceiling": ceiling,
        "ceiling_ns": int(ceiling.timestamp() * 1_000_000) * 1000,
    }
    version_floor = ""
    if floor is not None:
        params["floor_ns"] = int(floor.timestamp() * 1_000_000) * 1000
        version_floor = "AND _version >= %(floor_ns)s"
    sql = f"""SELECT toDate(start_time, 'UTC') AS day FROM spans
        PREWHERE project_id = %(project)s AND _version < %(ceiling_ns)s {version_floor}
        WHERE start_time >= %(start)s AND start_time < %(ceiling)s
        GROUP BY day ORDER BY day LIMIT 367"""
    days = []
    for block in stream_query(sql, params):
        days.extend(row[0] for row in block)
        if len(days) > 366:
            raise ValueError(
                "Live catch-up touches more than 366 event days. Narrow the policy start."
            )
    for value in days:
        day = value if isinstance(value, date) else date.fromisoformat(str(value))
        start = datetime.combine(day, time.min, tzinfo=UTC)
        yield {
            "start": max(start, params["start"]).isoformat(),
            "end": min(start + timedelta(days=1), ceiling).isoformat(),
        }


def sampling_clock() -> datetime:
    """Use the same clock as CH25 physical arrival versions, not the API host."""
    rows = [
        row for block in stream_query("SELECT now64(6, 'UTC')", {}) for row in block
    ]
    if len(rows) != 1 or not isinstance(rows[0][0], datetime):
        raise RuntimeError("Could not resolve the sampling arrival watermark.")
    value = rows[0][0]
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
