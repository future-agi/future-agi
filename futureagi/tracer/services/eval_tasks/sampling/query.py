"""Tenant-scoped unit aggregation and bounded streaming, not Python populations."""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from tracer.services.eval_tasks.sampling.policy import parse_time, threshold

if TYPE_CHECKING:
    from tracer.models.eval_task import EvalTask

BATCH_SIZE = 1000
READ_SETTINGS = {
    "max_execution_time": 30,
    "max_query_size": 2097152,
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


class SamplingQuery:
    def __init__(
        self, task: EvalTask, policy: dict, version: str, snapshot: datetime
    ) -> None:
        self.task, self.policy, self.version = task, policy, version
        self.params = {
            "sp_project": str(task.project_id),
            "sp_version": version,
            "sp_start": parse_time(policy["window"]["start"]),
            "sp_end": parse_time(policy["window"]["end"]),
            "sp_snapshot": snapshot,
        }
        self._counter = 0

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
                        self.policy["window"]["start"],
                        self.policy["window"]["end"],
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
        session = f"toString({resolved_id_expr('s.trace_session_id', 'sm')})"
        user = f"toString({resolved_id_expr('s.end_user_id', 'um')})"
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
        fallback = (
            "''"
            if self.policy["missing_id"] == "exclude"
            else f"concat('row:', {row_id})"
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
        return f"""WITH raw AS (
            SELECT id, trace_id, trace_session_id, end_user_id, parent_span_id, start_time, created_at,
                   greatest(created_at, updated_at, fromUnixTimestamp64Nano(toInt64(_version), 'UTC')) AS source_arrival,
                   attrs_string, attrs_number, attrs_bool, attributes_extra, resource_attrs, metadata FROM spans FINAL
            PREWHERE project_id = %(sp_project)s AND start_time >= %(sp_start)s AND start_time < %(sp_end)s
            WHERE is_deleted = 0 AND created_at < %(sp_snapshot)s AND source_arrival < %(sp_snapshot)s
        ), {", ".join(maps)}, eligible AS ({eligible}), projected AS (
            SELECT {row_id} AS row_id, {unit_id} AS unit_id, {rule_expr} AS rule_index,
                   {groups_expr} AS groups, s.start_time AS event_time, s.id AS span_id, s.source_arrival AS arrival
            FROM raw AS s
            LEFT JOIN session_map AS sm ON s.trace_session_id = sm.any_id
            LEFT JOIN user_map AS um ON s.end_user_id = um.any_id
            WHERE {row_id} IN (SELECT {eligible_id} FROM eligible) {root}
        ), rows AS (
            SELECT p.row_id AS row_id, argMin(p.unit_id, tuple(p.event_time, p.span_id)) AS unit_id,
                   min(p.rule_index) AS rule_index,
                   argMin(p.groups, tuple(p.rule_index, p.event_time, p.span_id)) AS groups,
                   min(p.event_time) AS event_time, max(p.arrival) AS arrival
            FROM projected AS p WHERE p.row_id != '' AND p.row_id != '00000000-0000-0000-0000-000000000000'
              AND throwIf(length(p.unit_id) > 2048, 'Sampling unit id exceeds 2048 bytes') = 0
              AND throwIf(arrayExists(g -> length(g) > 1024, p.groups), 'Sampling group value exceeds 1024 bytes') = 0
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
                else f"toUInt64(ceil(toFloat64(population_size) * {self.bind(rule.get('rate', 100) / 100)}))"
            )
            if "rare_below" in rule:
                count = f"if(population_size <= {self.bind(rule['rare_below'])}, population_size, {count})"
            target.append(
                f"least(population_size, toUInt64({self.bind(rule.get('max_n', 2**63 - 1))}), greatest(toUInt64({self.bind(rule.get('min_n', 0))}), {count}))"
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
        ctes = self._source() if self.policy["mode"] == "closed" else self._classified()
        predicates = ["r.unit_id != ''"]
        if unit_ids is not None:
            self.params["sp_member_units"] = tuple(unit_ids)
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

    def preview(self) -> tuple[str, dict]:
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


def stream_query(sql: str, params: dict) -> Iterator[list[tuple]]:
    from tracer.services.clickhouse.application_read_policy import (
        application_read_context,
    )
    from tracer.services.clickhouse.v2.query_service import get_v2_query_client

    if len(sql.encode("utf-8")) > READ_SETTINGS["max_query_size"]:
        raise ValueError(
            "Sampling query exceeds its compiler budget. Reduce the number of slices."
        )

    # This is an explicitly bounded cohort proof. Outer application mode drops
    # abort caps, so opt out rather than silently losing the selection budget.
    with application_read_context(enabled=False):
        with get_v2_query_client().execute_read_block_stream(
            sql,
            native_parameters(params),
            timeout_ms=30_000,
            block_size=BATCH_SIZE,
            settings=READ_SETTINGS,
        ) as blocks:
            yield from blocks
