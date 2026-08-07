"""Rollup-equals-raw integration tests for dashboard_attr_rollup.

Assert the fast-path returns the SAME per-(bucket, attr_value) average as the raw
spans breakdown across the three boundary cases (see the test names: backfill
fall-back, partial-window snap, soft-delete rebuild). Marked ``integration``; runs
against a live CH 25.3 (v2) and SKIPs when unreachable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import tracer.services.clickhouse.v2 as v2pkg
from tracer.services.clickhouse.v2 import get_v2_config
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)
from tracer.tests._ch_seed import seed_ch_spans

pytestmark = pytest.mark.integration

try:
    import clickhouse_connect
except ImportError:  # pragma: no cover
    clickhouse_connect = None

_ROLLUP_TABLE = "dashboard_attr_rollup"
# A fixed, hour-aligned anchor day so the snap math is deterministic.
_DAY = datetime(2024, 6, 1, tzinfo=UTC)
_COVERED_SINCE = datetime(2000, 1, 1, tzinfo=UTC)


def _client():
    cfg = get_v2_config()
    return clickhouse_connect.get_client(
        host=cfg["host"],
        port=cfg["http_port"],
        username=cfg["user"],
        password=cfg["password"] or "",
        database=cfg["database"],
        send_receive_timeout=30,
    )


@pytest.fixture(scope="module")
def ch():
    """Reachability gate + ensure the rollup table & MV exist (idempotent)."""
    if clickhouse_connect is None:
        pytest.skip("clickhouse-connect not installed")
    try:
        client = _client()
        client.command("SELECT 1")
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"CH 25.3 (v2) not reachable ({exc!r}); integration test")

    ddl = (
        Path(v2pkg.__file__).parent / "schema" / "021_dashboard_attr_rollup.sql"
    ).read_text()
    for stmt in (s.strip() for s in ddl.split(";")):
        if stmt:
            client.command(stmt)
    return client


def _span(project_id, start_time, latency_ms, final_status, country, deleted=False):
    """One span row in the adapter's PG-row input shape (seed_ch_spans)."""
    span_id = f"span_{uuid.uuid4().hex[:16]}"
    return {
        "id": span_id,
        "trace_id": str(uuid.uuid4()),
        "project_id": str(project_id),
        "parent_span_id": "",
        "name": "root",
        "observation_type": "llm",
        "status": "OK",
        "start_time": start_time,
        "end_time": start_time,
        "latency_ms": latency_ms,
        "span_attributes": {"final_status": final_status, "country": country},
        "created_at": start_time,
        "updated_at": start_time,
        "deleted": deleted,
    }


def _query(client, sql, params):
    res = client.query(sql, parameters=params)
    return [dict(zip(res.column_names, row, strict=False)) for row in res.result_rows]


def _avg_map(rows):
    """Map {(time_bucket, breakdown_value): value} for comparison."""
    return {(str(r["time_bucket"]), str(r["breakdown_value"])): float(r["value"]) for r in rows}


def _raw_sql():
    # Ground truth: avg latency per (hour, final_status) over the SNAPPED window,
    # from the current deduplicated, non-deleted root spans.
    return (
        "SELECT toStartOfHour(start_time) AS time_bucket,\n"
        "       attrs_string['final_status'] AS breakdown_value,\n"
        "       sum(toInt64(latency_ms)) / count() AS value\n"
        "FROM spans FINAL\n"
        "WHERE project_id IN %(project_ids)s\n"
        "  AND is_deleted = 0\n"
        "  AND parent_span_id = ''\n"
        "  AND start_time >= %(start_date)s\n"
        "  AND start_time < %(end_date)s\n"
        "GROUP BY time_bucket, breakdown_value\n"
        "ORDER BY time_bucket, breakdown_value"
    )


def _config(project_id, custom_start, custom_end):
    return {
        "project_ids": [str(project_id)],
        "granularity": "hour",
        "time_range": {
            "custom_start": custom_start.isoformat(),
            "custom_end": custom_end.isoformat(),
        },
        "metrics": [
            {"id": "latency", "name": "latency", "type": "system_metric",
             "aggregation": "avg"}
        ],
        "filters": [],
        "breakdowns": [
            {"type": "custom_attribute", "name": "final_status",
             "source": "traces", "display_name": "final_status",
             "attribute_type": "string"}
        ],
    }


def test_backfill_window_before_coverage_falls_back(ch, settings):
    # (a) A window starting before COVERED_SINCE must route to spans, not a
    # partial rollup. Pure routing assertion on the real builder call-path.
    settings.DASHBOARD_ATTR_ROLLUP_ENABLED = True
    settings.DASHBOARD_ATTR_ROLLUP_COVERED_SINCE = _DAY + timedelta(days=1)
    config = _config(uuid.uuid4(), _DAY + timedelta(hours=10), _DAY + timedelta(hours=13))
    sql, _, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]
    assert _ROLLUP_TABLE not in sql
    assert "FROM spans" in sql


def test_partial_window_snapped_rollup_equals_raw(ch, settings):
    # (b) A non-hour-aligned window: the snapped rollup equals the raw over the
    # snapped window.
    settings.DASHBOARD_ATTR_ROLLUP_ENABLED = True
    settings.DASHBOARD_ATTR_ROLLUP_COVERED_SINCE = _COVERED_SINCE
    project_id = uuid.uuid4()

    spans = []
    for hour, statuses in {10: ["ok", "ok", "err"], 11: ["ok", "err"], 12: ["ok"]}.items():
        for i, st in enumerate(statuses):
            spans.append(
                _span(project_id, _DAY + timedelta(hours=hour, minutes=i),
                      latency_ms=100 * (hour - 9) + i, final_status=st, country="US")
            )
    # A span in hour 13 — excluded by the snapped end (13:30 → 13:00).
    spans.append(_span(project_id, _DAY + timedelta(hours=13, minutes=5),
                       latency_ms=9999, final_status="ok", country="US"))
    seed_ch_spans(spans, client=ch)

    config = _config(project_id, _DAY + timedelta(hours=10, minutes=30),
                     _DAY + timedelta(hours=13, minutes=30))
    rollup_sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]
    assert _ROLLUP_TABLE in rollup_sql
    # Snapped window is what we compare on (whole hours: 10:00 .. 13:00).
    assert (params["start_date"].hour, params["start_date"].minute) == (10, 0)
    assert (params["end_date"].hour, params["end_date"].minute) == (13, 0)

    rollup = _avg_map(_query(ch, rollup_sql, params))
    raw = _avg_map(_query(ch, _raw_sql(), params))
    assert rollup == pytest.approx(raw)
    assert rollup  # non-empty


def test_soft_delete_rebuild_equals_raw(ch, settings):
    # (c) Soft-delete reconciliation: the live rollup over-counts a soft-deleted
    # span; after the rebuild command, rollup == raw (deleted excluded).
    from django.core.management import call_command

    settings.DASHBOARD_ATTR_ROLLUP_ENABLED = True
    settings.DASHBOARD_ATTR_ROLLUP_COVERED_SINCE = _COVERED_SINCE
    project_id = uuid.uuid4()

    keep = [
        _span(project_id, _DAY + timedelta(hours=10, minutes=i), 100 + i, "ok", "US")
        for i in range(3)
    ]
    doomed = _span(project_id, _DAY + timedelta(hours=10, minutes=30), 5000, "ok", "US")
    seed_ch_spans([*keep, doomed], client=ch)

    # Soft-delete: re-insert the same row (same ORDER BY key) flagged deleted.
    tombstone = dict(doomed, deleted=True)
    seed_ch_spans([tombstone], client=ch)

    # Reconcile the aggregate, then compare via the real builder call-path.
    call_command("rebuild_dashboard_attr_rollup")

    config = _config(project_id, _DAY + timedelta(hours=10), _DAY + timedelta(hours=11))
    rollup_sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]
    assert _ROLLUP_TABLE in rollup_sql

    rollup = _avg_map(_query(ch, rollup_sql, params))
    raw = _avg_map(_query(ch, _raw_sql(), params))
    assert rollup == pytest.approx(raw)
    # The 5000ms doomed span is gone — avg reflects only the kept spans.
    # CH `toStartOfHour` returns a tz-naive DateTime, so `_avg_map` keys the
    # bucket without a tzinfo suffix; match that when building the lookup key.
    bucket_key = (str((_DAY + timedelta(hours=10)).replace(tzinfo=None)), "ok")
    assert rollup[bucket_key] == pytest.approx(sum(100 + i for i in range(3)) / 3)
