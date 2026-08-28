"""Qualify the 12-month complex dashboard SLO with SELECT-only reads."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from uuid import UUID

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_service import (
    V2AnalyticsQueryService,
    reset_v2_query_client,
)


@dataclass(frozen=True)
class _TypedFilter:
    attribute_type: str
    key: str
    value: object

    def as_query_filter(self) -> dict[str, object]:
        return {
            "metric_type": "custom_attribute",
            "metric_name": self.key,
            "operator": "equal_to",
            "value": self.value,
            "attribute_type": self.attribute_type,
            "source": "traces",
        }


def _split_filter(raw: str) -> tuple[str, str]:
    key, separator, value = raw.partition("=")
    if not separator or not key.strip() or not value:
        raise CommandError("filters must use KEY=VALUE with both sides non-empty")
    return key.strip(), value


def _parse_filter(raw: str, attribute_type: str) -> _TypedFilter:
    key, value = _split_filter(raw)
    if attribute_type == "number":
        try:
            normalized: object = float(value)
        except ValueError as exc:
            raise CommandError(f"{key} requires a numeric value") from exc
    elif attribute_type == "boolean":
        lowered = value.strip().lower()
        if lowered not in {"true", "false"}:
            raise CommandError(f"{key} requires true or false")
        normalized = lowered == "true"
    else:
        normalized = value
    return _TypedFilter(attribute_type, key, normalized)


def _nearest_rank_percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("at least one timing is required")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


class Command(BaseCommand):
    help = (
        "Run a SELECT-only 12M latency + attribute filters + attribute "
        "breakdown benchmark and fail unless p95 meets the configured SLO."
    )

    def add_arguments(self, parser):
        parser.add_argument("--project-id", required=True)
        parser.add_argument("--breakdown-key", required=True)
        parser.add_argument("--string-filter", action="append", default=[])
        parser.add_argument("--number-filter", action="append", default=[])
        parser.add_argument("--boolean-filter", action="append", default=[])
        parser.add_argument(
            "--samples",
            type=int,
            default=settings.DASHBOARD_COMPLEX_QUERY_BENCHMARK_SAMPLES,
        )
        parser.add_argument(
            "--warmups",
            type=int,
            default=settings.DASHBOARD_COMPLEX_QUERY_BENCHMARK_WARMUPS,
        )
        parser.add_argument(
            "--target-ms",
            type=int,
            default=settings.DASHBOARD_COMPLEX_QUERY_P95_TARGET_MS,
        )

    @staticmethod
    def _query_config(options, filters: list[_TypedFilter]) -> dict:
        return {
            "project_ids": [options["project_id"]],
            "time_range": {"preset": "12M"},
            "granularity": "month",
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "source": "traces",
                    "aggregation": "avg",
                    "filters": [],
                }
            ],
            "filters": [item.as_query_filter() for item in filters],
            "breakdowns": [
                {
                    "type": "custom_attribute",
                    "name": options["breakdown_key"],
                    "source": "traces",
                    "attribute_type": "string",
                }
            ],
        }

    def handle(self, *args, **options):
        try:
            project_id = str(UUID(options["project_id"]))
        except ValueError as exc:
            raise CommandError("project-id must be a UUID") from exc
        options = {**options, "project_id": project_id}
        filters = [
            *(_parse_filter(item, "string") for item in options["string_filter"]),
            *(_parse_filter(item, "number") for item in options["number_filter"]),
            *(_parse_filter(item, "boolean") for item in options["boolean_filter"]),
        ]
        if not filters:
            raise CommandError("at least one typed attribute filter is required")
        samples = options["samples"]
        warmups = options["warmups"]
        target_ms = options["target_ms"]
        target_ceiling_ms = settings.DASHBOARD_COMPLEX_QUERY_P95_TARGET_MS
        hard_wall_ms = settings.DASHBOARD_COMPLEX_QUERY_HARD_WALL_MS
        if not 5 <= samples <= 100:
            raise CommandError("samples must be between 5 and 100")
        if not 0 <= warmups <= 10:
            raise CommandError("warmups must be between 0 and 10")
        if not 100 <= target_ms <= target_ceiling_ms:
            raise CommandError(
                "target-ms must be between 100 and the configured p95 ceiling"
            )

        query, params, _ = DashboardQueryBuilderV2(
            self._query_config(options, filters)
        ).build_all_queries()[0]
        if "FROM dashboard_root_spans AS spans FINAL" not in query:
            raise CommandError(
                "root-span analytical coverage is not enabled for the full 12M window"
            )
        if not query.lstrip().upper().startswith("SELECT"):
            raise CommandError("benchmark refused a non-SELECT statement")

        read_settings = {
            "max_threads": settings.DASHBOARD_TRACE_READ_MAX_THREADS,
            "max_bytes_to_read": settings.DASHBOARD_TRACE_READ_MAX_BYTES,
            "max_memory_usage": settings.DASHBOARD_TRACE_READ_MAX_MEMORY_BYTES,
            "read_overflow_mode": "throw",
            "max_result_rows": settings.DASHBOARD_TRACE_READ_MAX_RESULT_ROWS,
            "max_result_bytes": settings.DASHBOARD_TRACE_READ_MAX_RESULT_BYTES,
            "result_overflow_mode": "throw",
            "timeout_overflow_mode": "throw",
        }
        service = V2AnalyticsQueryService()
        timings_ms: list[float] = []
        try:
            for index in range(warmups + samples):
                started = time.perf_counter()
                service.execute_ch_query(
                    query,
                    params,
                    timeout_ms=hard_wall_ms,
                    settings=read_settings,
                )
                elapsed_ms = (time.perf_counter() - started) * 1_000
                if index >= warmups:
                    timings_ms.append(elapsed_ms)
        finally:
            reset_v2_query_client()

        p95_ms = _nearest_rank_percentile(timings_ms, 0.95)
        report = {
            "project_id": options["project_id"],
            "preset": "12M",
            "metric": "latency.avg",
            "filters": [
                {
                    "type": item.attribute_type,
                    "key": item.key,
                    "value": item.value,
                }
                for item in filters
            ],
            "breakdown": options["breakdown_key"],
            "samples": samples,
            "warmups": warmups,
            "p50_ms": round(_nearest_rank_percentile(timings_ms, 0.50), 2),
            "p95_ms": round(p95_ms, 2),
            "max_ms": round(max(timings_ms), 2),
            "target_p95_ms": target_ms,
            "hard_wall_ms": hard_wall_ms,
            "passed": p95_ms < target_ms,
        }
        self.stdout.write(json.dumps(report, sort_keys=True))
        if not report["passed"]:
            raise CommandError(
                f"dashboard p95 {p95_ms:.2f}ms did not meet {target_ms}ms"
            )
