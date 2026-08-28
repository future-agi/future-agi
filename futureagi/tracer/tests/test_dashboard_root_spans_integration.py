"""Exact root-fact parity tests against ClickHouse 25.3."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import tracer.services.clickhouse.v2 as v2pkg
from tracer.services.clickhouse.v2 import get_v2_config
from tracer.services.clickhouse.v2.apply_schema_rewriter import split_statements
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)
from tracer.tests._ch_seed import seed_ch_spans

pytestmark = pytest.mark.integration

try:
    import clickhouse_connect
except ImportError:  # pragma: no cover
    clickhouse_connect = None

_DAY = datetime(2025, 6, 1, tzinfo=UTC)


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
    if clickhouse_connect is None:
        pytest.skip("clickhouse-connect not installed")
    try:
        client = _client()
        client.command("SELECT 1")
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"CH 25.3 (v2) not reachable ({exc!r})")

    ddl = (
        Path(v2pkg.__file__).parent / "schema" / "029_dashboard_root_spans.sql"
    ).read_text()
    for statement in split_statements(ddl):
        client.command(statement)
    try:
        yield client
    finally:
        client.close()


def _span(project_id, *, root, latency, country, model, deleted=False):
    trace_id = str(uuid.uuid4())
    span_id = f"span_{uuid.uuid4().hex[:16]}"
    return {
        "id": span_id,
        "trace_id": trace_id,
        "project_id": str(project_id),
        "parent_span_id": "" if root else f"parent_{uuid.uuid4().hex[:16]}",
        "name": "root" if root else "child",
        "observation_type": "llm",
        "status": "OK",
        "start_time": _DAY,
        "end_time": _DAY + timedelta(milliseconds=latency),
        "latency_ms": latency,
        "span_attributes": {
            "user.country": country,
            "llm.model_name": model,
            "llm_present": True,
        },
        "created_at": _DAY,
        "updated_at": _DAY,
        "deleted": deleted,
    }


def _config(project_id):
    return {
        "project_ids": [str(project_id)],
        "time_range": {
            "custom_start": (_DAY - timedelta(days=1)).isoformat(),
            "custom_end": (_DAY + timedelta(days=1)).isoformat(),
        },
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
        "filters": [
            {
                "metric_type": "custom_attribute",
                "metric_name": "user.country",
                "operator": "equal_to",
                "value": "Mexico",
                "attribute_type": "string",
                "source": "traces",
            },
            {
                "metric_type": "custom_attribute",
                "metric_name": "llm_present",
                "operator": "equal_to",
                "value": True,
                "attribute_type": "boolean",
                "source": "traces",
            },
        ],
        "breakdowns": [
            {
                "type": "custom_attribute",
                "name": "llm.model_name",
                "source": "traces",
                "attribute_type": "string",
            }
        ],
    }


def _rows(client, sql, params):
    return client.query(sql, parameters=params).result_rows


def test_complex_root_fact_query_matches_raw_spans(ch, settings):
    project_id = uuid.uuid4()
    seed_ch_spans(
        [
            _span(project_id, root=True, latency=100, country="Mexico", model="gpt-4"),
            _span(project_id, root=True, latency=300, country="Mexico", model="gpt-4"),
            _span(project_id, root=True, latency=900, country="US", model="gpt-5"),
            _span(
                project_id, root=False, latency=9_999, country="Mexico", model="gpt-4"
            ),
        ],
        client=ch,
    )

    settings.DASHBOARD_ROOT_SPANS_ENABLED = False
    settings.DASHBOARD_ROOT_SPANS_COVERED_SINCE = None
    raw_sql, raw_params, _ = DashboardQueryBuilderV2(
        _config(project_id)
    ).build_all_queries()[0]
    settings.DASHBOARD_ROOT_SPANS_ENABLED = True
    settings.DASHBOARD_ROOT_SPANS_COVERED_SINCE = datetime(2000, 1, 1, tzinfo=UTC)
    settings.DASHBOARD_ROOT_SPANS_PROJECT_ALLOWLIST = {str(project_id)}
    settings.DASHBOARD_ROOT_SPANS_ALL_PROJECTS_COVERED = False
    root_sql, root_params, _ = DashboardQueryBuilderV2(
        _config(project_id)
    ).build_all_queries()[0]

    assert "FROM spans FINAL" in raw_sql
    assert "FROM dashboard_root_spans AS spans FINAL" in root_sql
    assert _rows(ch, root_sql, root_params) == _rows(ch, raw_sql, raw_params)


def test_root_fact_tombstone_does_not_resurrect_a_trace(ch, settings):
    project_id = uuid.uuid4()
    root = _span(
        project_id,
        root=True,
        latency=100,
        country="Mexico",
        model="gpt-4",
    )
    seed_ch_spans([root], client=ch)
    seed_ch_spans([{**root, "deleted": True}], client=ch)
    settings.DASHBOARD_ROOT_SPANS_ENABLED = True
    settings.DASHBOARD_ROOT_SPANS_COVERED_SINCE = datetime(2000, 1, 1, tzinfo=UTC)
    settings.DASHBOARD_ROOT_SPANS_PROJECT_ALLOWLIST = {str(project_id)}
    settings.DASHBOARD_ROOT_SPANS_ALL_PROJECTS_COVERED = False

    sql, params, _ = DashboardQueryBuilderV2(_config(project_id)).build_all_queries()[0]

    assert _rows(ch, sql, params) == []
