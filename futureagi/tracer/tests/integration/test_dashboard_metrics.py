import json
import uuid
from datetime import UTC, datetime

import pytest

from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)


def _config(project_id, metric, start, end, *, workspace_id=None):
    config = {
        "project_ids": [str(project_id)],
        "granularity": "day",
        "timezone": "UTC",
        "time_range": {
            "custom_start": start.isoformat(),
            "custom_end": end.isoformat(),
        },
        "metrics": [metric],
        "filters": [],
        "breakdowns": [],
    }
    if workspace_id:
        config["workspace_id"] = str(workspace_id)
    return config


def _execute(client, config):
    sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]
    return client._client.execute(sql, params)


@pytest.mark.django_db
def test_eval_metrics_bucket_by_trace_time_and_keep_non_trace_eval_time(clean_ch):
    project_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    template_id = uuid.uuid4()
    trace_id = uuid.uuid4().hex
    trace_day = datetime(2026, 1, 10, 10, tzinfo=UTC)
    eval_day = datetime(2026, 1, 12, 10, tzinfo=UTC)

    clean_ch._client.execute(
        """
        INSERT INTO spans (
            project_id, observation_type, start_time, trace_id, id,
            parent_span_id, name, latency_ms, created_at, updated_at,
            is_deleted, _version
        ) VALUES
        """,
        [
            (
                project_id,
                "span",
                trace_day,
                trace_id,
                "root",
                "",
                "root",
                1000,
                trace_day,
                trace_day,
                0,
                1,
            )
        ],
    )

    def eval_config(linked_trace_id, score):
        return json.dumps(
            json.dumps(
                {"trace_id": linked_trace_id, "output": {"output": score}}
            )
        )

    clean_ch._client.execute(
        """
        INSERT INTO usage_apicalllog (
            id, log_id, organization_id, workspace_id, status, config,
            source, source_id, deleted, created_at, updated_at,
            _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version
        ) VALUES
        """,
        [
            (
                1,
                uuid.uuid4(),
                organization_id,
                workspace_id,
                "success",
                eval_config(trace_id, 0.2),
                "tracer",
                str(template_id),
                0,
                eval_day,
                eval_day,
                eval_day,
                0,
                1,
            ),
            (
                2,
                uuid.uuid4(),
                organization_id,
                workspace_id,
                "success",
                eval_config(trace_id, 0.8),
                "tracer",
                str(template_id),
                0,
                eval_day.replace(hour=11),
                eval_day.replace(hour=11),
                eval_day.replace(hour=11),
                0,
                2,
            ),
            (
                3,
                uuid.uuid4(),
                organization_id,
                workspace_id,
                "success",
                eval_config("", 0.4),
                "eval_playground",
                str(template_id),
                0,
                eval_day.replace(hour=12),
                eval_day.replace(hour=12),
                eval_day.replace(hour=12),
                0,
                3,
            ),
            (
                4,
                uuid.uuid4(),
                organization_id,
                workspace_id,
                "success",
                eval_config(uuid.uuid4().hex, 1.0),
                "tracer",
                str(template_id),
                0,
                eval_day.replace(hour=13),
                eval_day.replace(hour=13),
                eval_day.replace(hour=13),
                0,
                4,
            ),
        ],
    )

    metric = {
        "id": str(template_id),
        "name": "accuracy",
        "type": "eval_metric",
        "config_id": str(template_id),
        "output_type": "SCORE",
        "aggregation": "avg",
    }
    trace_rows = _execute(
        clean_ch,
        _config(
            project_id,
            metric,
            trace_day.replace(hour=0),
            trace_day.replace(day=11, hour=0),
            workspace_id=workspace_id,
        ),
    )
    eval_rows = _execute(
        clean_ch,
        _config(
            project_id,
            metric,
            eval_day.replace(hour=0),
            eval_day.replace(day=13, hour=0),
            workspace_id=workspace_id,
        ),
    )

    assert trace_rows == [(trace_day.replace(hour=0), pytest.approx(0.8))]
    assert eval_rows == [(eval_day.replace(hour=0), pytest.approx(0.4))]


@pytest.mark.django_db
def test_latency_uses_one_latest_root_span_per_trace(clean_ch):
    project_id = uuid.uuid4()
    day = datetime(2026, 1, 10, 10, tzinfo=UTC)
    trace_one = uuid.uuid4().hex
    trace_two = uuid.uuid4().hex

    def span(trace_id, span_id, parent_span_id, latency_ms, version):
        return (
            project_id,
            "span",
            day,
            trace_id,
            span_id,
            parent_span_id,
            span_id,
            latency_ms,
            day,
            day,
            0,
            version,
        )

    clean_ch._client.execute(
        """
        INSERT INTO spans (
            project_id, observation_type, start_time, trace_id, id,
            parent_span_id, name, latency_ms, created_at, updated_at,
            is_deleted, _version
        ) VALUES
        """,
        [
            span(trace_one, "root-one", "", 1000, 1),
            span(trace_one, "root-one", "", 2000, 2),
            span(trace_one, "child-one", "root-one", 10000, 3),
            span(trace_two, "root-two", "", 4000, 1),
        ],
    )

    rows = _execute(
        clean_ch,
        _config(
            project_id,
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
            },
            day.replace(hour=0),
            day.replace(day=11, hour=0),
        ),
    )

    assert rows == [(day.replace(hour=0), pytest.approx(3000.0))]
