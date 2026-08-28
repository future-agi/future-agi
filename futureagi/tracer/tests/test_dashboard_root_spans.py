from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)

_COVERED_SINCE = datetime(2000, 1, 1, tzinfo=UTC)


def _config(*, filters=None, breakdowns=None, preset="12M"):
    return {
        "project_ids": ["00000000-0000-0000-0000-000000000001"],
        "time_range": {"preset": preset},
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
        "filters": filters or [],
        "breakdowns": breakdowns or [],
    }


def _attribute_filter(name, value, attribute_type):
    return {
        "metric_type": "custom_attribute",
        "metric_name": name,
        "operator": "equal_to",
        "value": value,
        "attribute_type": attribute_type,
        "source": "traces",
    }


def _attribute_breakdown(name, attribute_type="string"):
    return {
        "type": "custom_attribute",
        "name": name,
        "source": "traces",
        "attribute_type": attribute_type,
    }


def _enable(settings, *, covered_since=_COVERED_SINCE):
    settings.DASHBOARD_ROOT_SPANS_ENABLED = True
    settings.DASHBOARD_ROOT_SPANS_COVERED_SINCE = covered_since
    settings.DASHBOARD_ROOT_SPANS_PROJECT_ALLOWLIST = {
        "00000000-0000-0000-0000-000000000001"
    }
    settings.DASHBOARD_ROOT_SPANS_ALL_PROJECTS_COVERED = False


@pytest.mark.unit
def test_covered_12m_complex_trace_query_uses_root_facts(settings):
    _enable(settings)
    config = _config(
        filters=[
            _attribute_filter("user.country", "Mexico", "string"),
            _attribute_filter("llm_present", True, "boolean"),
        ],
        breakdowns=[_attribute_breakdown("llm.model_name")],
    )

    sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

    assert "FROM dashboard_root_spans AS spans FINAL" in sql
    assert "FROM spans" not in sql
    assert "(parent_span_id IS NULL OR parent_span_id = '')" in sql
    assert "attrs_string['user.country']" in sql
    assert "attrs_bool['llm_present']" in sql
    assert "attrs_string[%(_custom_bd_key_0)s]" in sql
    assert params["_custom_bd_key_0"] == "llm.model_name"
    assert params["f_0_val"] == "Mexico"
    assert params["f_1_val"] is True


@pytest.mark.unit
def test_root_facts_support_annotation_breakdown_and_attribute_filter(settings):
    _enable(settings)
    config = _config(
        filters=[_attribute_filter("user.country", "Mexico", "string")],
        breakdowns=[
            {
                "type": "annotation_metric",
                "name": "00000000-0000-0000-0000-000000000099",
                "label_id": "00000000-0000-0000-0000-000000000099",
                "output_type": "numeric",
                "source": "annotations",
            }
        ],
    )

    sql, _, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

    assert "FROM dashboard_root_spans AS s FINAL" in sql
    assert "LEFT JOIN model_hub_score AS ann0" in sql
    assert "FROM spans AS s" not in sql


@pytest.mark.unit
def test_root_facts_are_fail_closed_without_flag_or_coverage(settings):
    settings.DASHBOARD_ROOT_SPANS_ENABLED = False
    settings.DASHBOARD_ROOT_SPANS_COVERED_SINCE = _COVERED_SINCE

    sql, _, _ = DashboardQueryBuilderV2(_config()).build_all_queries()[0]

    assert "dashboard_root_spans" not in sql
    assert "FROM spans FINAL" in sql

    settings.DASHBOARD_ROOT_SPANS_ENABLED = True
    settings.DASHBOARD_ROOT_SPANS_COVERED_SINCE = None
    sql, _, _ = DashboardQueryBuilderV2(_config()).build_all_queries()[0]
    assert "dashboard_root_spans" not in sql
    assert "FROM spans FINAL" in sql


@pytest.mark.unit
def test_root_facts_reject_a_window_before_coverage(settings):
    _enable(settings, covered_since=datetime.now(UTC) - timedelta(days=30))

    sql, _, _ = DashboardQueryBuilderV2(_config()).build_all_queries()[0]

    assert "dashboard_root_spans" not in sql
    assert "FROM spans FINAL" in sql


@pytest.mark.unit
def test_root_facts_reject_a_project_outside_verified_scope(settings):
    _enable(settings)
    settings.DASHBOARD_ROOT_SPANS_PROJECT_ALLOWLIST = {
        "00000000-0000-0000-0000-000000000099"
    }

    sql, _, _ = DashboardQueryBuilderV2(_config()).build_all_queries()[0]

    assert "dashboard_root_spans" not in sql
    assert "FROM spans FINAL" in sql


@pytest.mark.unit
def test_root_facts_accept_explicit_all_project_coverage(settings):
    _enable(settings)
    settings.DASHBOARD_ROOT_SPANS_PROJECT_ALLOWLIST = set()
    settings.DASHBOARD_ROOT_SPANS_ALL_PROJECTS_COVERED = True

    sql, _, _ = DashboardQueryBuilderV2(_config()).build_all_queries()[0]

    assert "FROM dashboard_root_spans AS spans FINAL" in sql


@pytest.mark.unit
def test_root_facts_keep_user_id_remap_source(settings):
    _enable(settings)
    config = _config(
        filters=[
            {
                "metric_type": "system_metric",
                "metric_name": "user",
                "operator": "equal_to",
                "value": "customer@example.com",
                "source": "traces",
            }
        ]
    )

    sql, _, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

    assert "dashboard_root_spans" not in sql
    assert "dashboard_candidate_end_user_ids" in sql
    assert "FROM spans" in sql


@pytest.mark.unit
def test_root_facts_schema_is_additive_and_preserves_versions():
    schema_path = (
        Path(__file__).parents[1]
        / "services"
        / "clickhouse"
        / "v2"
        / "schema"
        / "029_dashboard_root_spans.sql"
    )
    sql = schema_path.read_text()
    executable = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    ).upper()

    assert "CREATE TABLE IF NOT EXISTS DASHBOARD_ROOT_SPANS" in executable
    assert "ENGINE = REPLACINGMERGETREE(_VERSION, IS_DELETED)" in executable
    assert (
        "CREATE MATERIALIZED VIEW IF NOT EXISTS DASHBOARD_ROOT_SPANS_MV" in executable
    )
    assert "WHERE PARENT_SPAN_ID = ''" in executable
    assert "ATTRS_STRING" in executable
    assert "ATTRS_NUMBER" in executable
    assert "ATTRS_BOOL" in executable
    assert "ATTRIBUTES_EXTRA" in executable
    assert "ALTER TABLE SPANS" not in executable
    assert "TRUNCATE" not in executable
    assert "DROP TABLE" not in executable
    mv_sql = executable.split(
        "CREATE MATERIALIZED VIEW IF NOT EXISTS DASHBOARD_ROOT_SPANS_MV",
        maxsplit=1,
    )[1]
    assert "ARRAY JOIN" not in mv_sql
    assert "GROUP BY" not in mv_sql
    assert " JOIN " not in mv_sql
