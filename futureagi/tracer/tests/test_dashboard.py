"""
Tests for the Custom Dashboards feature.

Covers:
- Dashboard CRUD API
- DashboardWidget CRUD API
- DashboardQueryBuilder (all metric types, time ranges, filters, breakdowns)
- Serializer validation
- Metrics discovery endpoint
- Query execution (mocked ClickHouse)
"""

import uuid
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings as django_settings

from tracer.models.dashboard import Dashboard, DashboardWidget
from tracer.serializers.dashboard import (
    DashboardCreateUpdateSerializer,
    DashboardDetailSerializer,
    DashboardSerializer,
    DashboardWidgetSerializer,
)
from tracer.views.dashboard import DashboardViewSet
from tracer.services.clickhouse.query_builders.dashboard import (
    AGGREGATIONS,
    SYSTEM_METRICS,
    DashboardQueryBuilder,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def dashboard(db, workspace, user):
    return Dashboard.objects.create(
        workspace=workspace,
        name="Test Dashboard",
        description="A test dashboard",
        created_by=user,
        updated_by=user,
    )


@pytest.fixture
def dashboard_widget(db, dashboard, user):
    return DashboardWidget.objects.create(
        dashboard=dashboard,
        name="Latency Chart",
        position=0,
        width=6,
        height=4,
        query_config={
            "project_ids": [str(uuid.uuid4())],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
        },
        chart_config={"chart_type": "line"},
        created_by=user,
    )


@pytest.fixture
def sample_query_config():
    return {
        "project_ids": [str(uuid.uuid4())],
        "granularity": "day",
        "time_range": {"preset": "7D"},
        "metrics": [
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
            }
        ],
        "filters": [],
        "breakdowns": [],
    }


# ===========================================================================
# Dashboard CRUD API
# ===========================================================================


class TestDashboardCRUD:
    @pytest.mark.django_db
    def test_create_dashboard(self, auth_client, workspace):
        response = auth_client.post(
            "/tracer/dashboard/",
            {"name": "My Dashboard", "description": "Test description"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["name"] == "My Dashboard"
        assert data["description"] == "Test description"
        assert data["id"] is not None

    @pytest.mark.django_db
    def test_create_dashboard_empty_name_rejected(self, auth_client):
        response = auth_client.post(
            "/tracer/dashboard/",
            {"name": "", "description": "No name"},
            format="json",
        )
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_list_dashboards(self, auth_client, dashboard):
        response = auth_client.get("/tracer/dashboard/")
        assert response.status_code == 200
        data = response.json()["result"]
        assert len(data) >= 1
        names = [d["name"] for d in data]
        assert "Test Dashboard" in names

    @pytest.mark.django_db
    def test_retrieve_dashboard(self, auth_client, dashboard, dashboard_widget):
        response = auth_client.get(f"/tracer/dashboard/{dashboard.id}/")
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["name"] == "Test Dashboard"
        assert "widgets" in data
        assert len(data["widgets"]) == 1

    @pytest.mark.django_db
    def test_update_dashboard(self, auth_client, dashboard):
        response = auth_client.put(
            f"/tracer/dashboard/{dashboard.id}/",
            {"name": "Updated Dashboard", "description": "Updated desc"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["name"] == "Updated Dashboard"

    @pytest.mark.django_db
    def test_partial_update_dashboard(self, auth_client, dashboard):
        response = auth_client.patch(
            f"/tracer/dashboard/{dashboard.id}/",
            {"name": "Patched Name"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["name"] == "Patched Name"
        assert data["description"] == "A test dashboard"

    @pytest.mark.django_db
    def test_delete_dashboard(self, auth_client, dashboard):
        response = auth_client.delete(f"/tracer/dashboard/{dashboard.id}/")
        assert response.status_code == 200
        dashboard.refresh_from_db()
        assert dashboard.deleted is True

    @pytest.mark.django_db
    def test_deleted_dashboard_not_in_list(self, auth_client, dashboard):
        dashboard.deleted = True
        dashboard.save()
        response = auth_client.get("/tracer/dashboard/")
        assert response.status_code == 200
        data = response.json()["result"]
        ids = [d["id"] for d in data]
        assert str(dashboard.id) not in ids

    @pytest.mark.django_db
    def test_list_dashboard_has_widget_count(
        self, auth_client, dashboard, dashboard_widget
    ):
        response = auth_client.get("/tracer/dashboard/")
        assert response.status_code == 200
        data = response.json()["result"]
        d = next(item for item in data if item["id"] == str(dashboard.id))
        assert d["widget_count"] == 1


# ===========================================================================
# DashboardWidget CRUD API
# ===========================================================================


class TestDashboardWidgetCRUD:
    @pytest.mark.django_db
    def test_create_widget(self, auth_client, dashboard):
        response = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/",
            {
                "name": "New Widget",
                "position": 0,
                "width": 12,
                "height": 6,
                "query_config": {"metrics": [], "project_ids": []},
                "chart_config": {},
            },
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["name"] == "New Widget"
        assert data["width"] == 12

    @pytest.mark.django_db
    def test_create_widget_default_name(self, auth_client, dashboard):
        response = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/",
            {
                "position": 0,
                "query_config": {},
                "chart_config": {},
            },
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["name"] == "Untitled"

    @pytest.mark.django_db
    def test_create_widget_invalid_width(self, auth_client, dashboard):
        response = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/",
            {
                "name": "Too Wide",
                "position": 0,
                "width": 15,
                "query_config": {},
                "chart_config": {},
            },
            format="json",
        )
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_update_widget(self, auth_client, dashboard, dashboard_widget):
        response = auth_client.patch(
            f"/tracer/dashboard/{dashboard.id}/widgets/{dashboard_widget.id}/",
            {"name": "Updated Widget"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["name"] == "Updated Widget"

    @pytest.mark.django_db
    def test_delete_widget(self, auth_client, dashboard, dashboard_widget):
        response = auth_client.delete(
            f"/tracer/dashboard/{dashboard.id}/widgets/{dashboard_widget.id}/"
        )
        assert response.status_code == 200
        dashboard_widget.refresh_from_db()
        assert dashboard_widget.deleted is True

    @pytest.mark.django_db
    def test_create_widget_for_nonexistent_dashboard(self, auth_client):
        fake_id = uuid.uuid4()
        response = auth_client.post(
            f"/tracer/dashboard/{fake_id}/widgets/",
            {"name": "Orphan", "query_config": {}, "chart_config": {}},
            format="json",
        )
        assert response.status_code == 404


# ===========================================================================
# Metrics Discovery Endpoint
# ===========================================================================


class TestMetricsEndpoint:
    @pytest.mark.django_db
    def test_metrics_without_project_ids_returns_all(self, auth_client):
        """Unified metrics endpoint returns all metrics even without project_ids."""
        response = auth_client.get("/tracer/dashboard/metrics/")
        assert response.status_code == 200
        data = response.json()["result"]
        assert "metrics" in data

    @pytest.mark.django_db
    def test_metrics_returns_system_metrics(self, auth_client, observe_project):
        response = auth_client.get(
            f"/tracer/dashboard/metrics/?project_ids={observe_project.id}"
        )
        assert response.status_code == 200
        data = response.json()["result"]
        # Unified API returns flat {"metrics": [...]} array
        assert "metrics" in data
        metric_names = [m["name"] for m in data["metrics"]]
        assert "latency" in metric_names
        assert "cost" in metric_names

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=False)
    @patch("tracer.views.dashboard.SQL_query_handler.get_span_attributes_for_project")
    def test_metrics_suppresses_customer_attribute_aliases_when_canonical_metric_exists(
        self,
        mock_get_span_attrs,
        _mock_clickhouse_enabled,
        auth_client,
        observe_project,
    ):
        mock_get_span_attrs.return_value = [
            "call.bot_wpm",
            "call.user_wpm",
            "freeform.attr",
        ]

        response = auth_client.get(
            f"/tracer/dashboard/metrics/?project_ids={observe_project.id}"
        )

        assert response.status_code == 200
        metrics = response.json()["result"]["metrics"]
        metric_names = [m["name"] for m in metrics]
        assert "bot_wpm" in metric_names
        assert "user_wpm" in metric_names
        assert "call.bot_wpm" not in metric_names
        assert "call.user_wpm" not in metric_names
        assert "freeform.attr" in metric_names

    def test_clickhouse_custom_attribute_discovery_includes_raw_attribute_sources(self):
        viewset = DashboardViewSet()
        mock_ch = MagicMock()
        mock_ch.execute_read.side_effect = [
            (
                [
                    {
                        "span_attr_str": {"typed.attr": "value"},
                        "span_attr_num": {"typed.score": 1.0},
                        "span_attr_bool": {"typed.enabled": True},
                    },
                ],
                [],
                1.0,
            ),
            (
                [
                    {
                        "attrs": '{"livekit.customer_id": "cust-1", "livekit.score": 0.95}'
                    },
                ],
                [],
                1.0,
            ),
            (
                [
                    {
                        "attrs": '{"livekit.connected": true, "nested": {"ignored": true}}'
                    },
                ],
                [],
                1.0,
            ),
        ]

        attrs = viewset._fetch_clickhouse_custom_attributes(mock_ch, ["project-1"])

        typed_sql = mock_ch.execute_read.call_args_list[0].args[0]
        spans_raw_sql = mock_ch.execute_read.call_args_list[1].args[0]
        cdc_raw_sql = mock_ch.execute_read.call_args_list[2].args[0]
        assert "SELECT span_attr_str, span_attr_num, span_attr_bool" in typed_sql
        assert "ARRAY JOIN" not in typed_sql
        assert "mapKeys" not in typed_sql
        assert "JSONExtractKeys" not in typed_sql
        assert "LIMIT 500" in typed_sql
        assert "span_attributes_raw AS attrs" in spans_raw_sql
        assert "span_attributes AS attrs" in cdc_raw_sql
        assert "PREWHERE project_id IN %(project_ids)s" in spans_raw_sql
        assert "LIMIT 200" in spans_raw_sql
        assert mock_ch.execute_read.call_args_list[0].args[1]["project_ids"] == [
            "project-1"
        ]
        assert attrs == [
            {"key": "livekit.connected", "type": "boolean"},
            {"key": "livekit.customer_id", "type": "text"},
            {"key": "livekit.score", "type": "number"},
            {"key": "typed.attr", "type": "text"},
            {"key": "typed.enabled", "type": "boolean"},
            {"key": "typed.score", "type": "number"},
        ]


# ===========================================================================
# DashboardQueryBuilder
# ===========================================================================


class TestDashboardQueryBuilder:
    def test_system_metric_query(self, sample_query_config):
        builder = DashboardQueryBuilder(sample_query_config)
        queries = builder.build_all_queries()
        assert len(queries) == 1
        sql, params, metric_info = queries[0]
        assert "latency_ms" in sql
        assert "avg" in sql.lower()
        assert "toStartOfDay" in sql
        assert params["project_ids"] == sample_query_config["project_ids"]

    def test_all_system_metrics(self):
        for metric_name in SYSTEM_METRICS:
            config = {
                "project_ids": ["proj1"],
                "granularity": "day",
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": metric_name,
                        "name": metric_name,
                        "type": "system_metric",
                        "aggregation": "avg",
                    }
                ],
            }
            builder = DashboardQueryBuilder(config)
            queries = builder.build_all_queries()
            assert len(queries) == 1

    def test_all_aggregations(self):
        for agg_name in AGGREGATIONS:
            config = {
                "project_ids": ["proj1"],
                "granularity": "day",
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": "latency",
                        "name": "latency",
                        "type": "system_metric",
                        "aggregation": agg_name,
                    }
                ],
            }
            builder = DashboardQueryBuilder(config)
            queries = builder.build_all_queries()
            assert len(queries) == 1

    def test_eval_metric_query(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "hour",
            "time_range": {"preset": "today"},
            "metrics": [
                {
                    "id": "e1",
                    "name": "accuracy",
                    "type": "eval_metric",
                    "config_id": str(uuid.uuid4()),
                    "output_type": "SCORE",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 1
        sql, params, _ = queries[0]
        assert "tracer_eval_logger" in sql
        assert "output_float" in sql

    def test_eval_metric_pass_fail(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "e2",
                    "name": "pass_rate",
                    "type": "eval_metric",
                    "config_id": str(uuid.uuid4()),
                    "output_type": "PASS_FAIL",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "output_bool" in sql

    def test_system_metric_sum_aggregation(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "cost",
                    "name": "cost",
                    "type": "system_metric",
                    "aggregation": "sum",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "sum(cost)" in sql

    def test_system_metric_median_aggregation(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "median",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "quantile(0.5)(latency_ms)" in sql

    def test_system_metric_count_distinct_aggregation(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "model",
                    "name": "model",
                    "type": "system_metric",
                    "aggregation": "count_distinct",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "uniq(model)" in sql

    def test_project_metric_count_uses_distinct_projects(self):
        config = {
            "project_ids": ["proj1", "proj2"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "project",
                    "name": "project",
                    "type": "system_metric",
                    "aggregation": "count",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "uniq(project_id)" in sql

    def test_latency_metric_uses_root_spans_only(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "min",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "(parent_span_id IS NULL OR parent_span_id = '')" in sql

    def test_eval_metric_pass_rate_aggregation(self):
        config = {
            "project_ids": ["proj1"],
            "organization_id": str(uuid.uuid4()),
            "workspace_id": str(uuid.uuid4()),
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "e_pass_rate",
                    "name": "accuracy",
                    "type": "eval_metric",
                    "config_id": str(uuid.uuid4()),
                    "output_type": "PASS_FAIL",
                    "aggregation": "pass_rate",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "countIf(" in sql
        assert "/ nullIf(count(), 0)" in sql

    def test_eval_metric_fail_count_aggregation(self):
        config = {
            "project_ids": ["proj1"],
            "organization_id": str(uuid.uuid4()),
            "workspace_id": str(uuid.uuid4()),
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "e_fail_count",
                    "name": "accuracy",
                    "type": "eval_metric",
                    "config_id": str(uuid.uuid4()),
                    "output_type": "PASS_FAIL",
                    "aggregation": "fail_count",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "countIf(" in sql
        assert "AS value" in sql

    def test_annotation_metric_query(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "30D"},
            "metrics": [
                {
                    "id": "a1",
                    "name": "quality",
                    "type": "annotation_metric",
                    "label_id": str(uuid.uuid4()),
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        assert "model_hub_score" in sql
        assert "JSONExtractFloat(a.value, 'value')" in sql
        assert params["annotation_label_id"]

    def test_annotation_star_metric_uses_rating_value(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "30D"},
            "metrics": [
                {
                    "id": "a_star",
                    "name": "quality_star",
                    "type": "annotation_metric",
                    "label_id": str(uuid.uuid4()),
                    "aggregation": "avg",
                    "output_type": "star",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "model_hub_score" in sql
        assert "JSONExtractFloat(a.value, 'rating')" in sql

    def test_custom_attribute_query(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "c1",
                    "name": "my_metric",
                    "type": "custom_attribute",
                    "attribute_key": "custom.score",
                    "attribute_type": "number",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "span_attr_num" in sql
        assert "custom.score" in sql
        assert "tracer_observation_span" in sql
        assert "span_attributes_raw" in sql
        assert "JSONExtractFloat" in sql
        assert "PREWHERE project_id IN %(project_ids)s" in sql
        assert "JSONHas(span_attributes, 'custom.score')" in sql

    def test_custom_attribute_query_falls_back_to_raw_string_attributes(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "customer_tier",
                    "name": "customer_tier",
                    "type": "custom_attribute",
                    "attribute_key": "customer.tier",
                    "attribute_type": "string",
                    "aggregation": "count_distinct",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "span_attr_str" in sql
        assert "customer.tier" in sql
        assert "tracer_observation_span" in sql
        assert "span_attributes_raw" in sql
        assert "JSONExtractString" in sql
        assert "PREWHERE project_id IN %(project_ids)s" in sql
        assert "JSONHas(span_attributes, 'customer.tier')" in sql

    def test_multiple_metrics(self, sample_query_config):
        sample_query_config["metrics"].append(
            {
                "id": "cost",
                "name": "cost",
                "type": "system_metric",
                "aggregation": "sum",
            }
        )
        builder = DashboardQueryBuilder(sample_query_config)
        queries = builder.build_all_queries()
        assert len(queries) == 2

    def test_breakdown_system(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "breakdowns": [{"type": "system_metric", "name": "model"}],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "breakdown_value" in sql
        assert "model" in sql

    def test_breakdown_custom_attribute(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "breakdowns": [
                {"type": "custom_attribute", "name": "env", "attribute_type": "string"}
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "span_attr_str" in sql
        assert "breakdown_value" in sql


class TestDashboardQueryBuilderTimeRanges:
    def test_preset_7d(self):
        config = {
            "project_ids": ["p1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        start, end = builder.parse_time_range()
        assert (end - start).days <= 7

    def test_preset_today(self):
        config = {
            "project_ids": ["p1"],
            "granularity": "hour",
            "time_range": {"preset": "today"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        start, end = builder.parse_time_range()
        assert start.hour == 0 and start.minute == 0

    def test_preset_yesterday(self):
        config = {
            "project_ids": ["p1"],
            "granularity": "hour",
            "time_range": {"preset": "yesterday"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        start, end = builder.parse_time_range()
        assert start.date() == (datetime.utcnow() - timedelta(days=1)).date()

    def test_custom_time_range(self):
        config = {
            "project_ids": ["p1"],
            "granularity": "day",
            "time_range": {
                "custom_start": "2025-01-01T00:00:00",
                "custom_end": "2025-01-31T23:59:59",
            },
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        start, end = builder.parse_time_range()
        assert start.year == 2025 and start.month == 1 and start.day == 1

    def test_all_granularities(self):
        for gran in ("minute", "hour", "day", "week", "month", "year"):
            config = {
                "project_ids": ["p1"],
                "granularity": gran,
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": "latency",
                        "name": "latency",
                        "type": "system_metric",
                        "aggregation": "avg",
                    }
                ],
            }
            builder = DashboardQueryBuilder(config)
            queries = builder.build_all_queries()
            assert len(queries) == 1


class TestDashboardQueryBuilderFilters:
    def test_global_system_filter(self):
        config = {
            "project_ids": ["p1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "filters": [
                {
                    "metric_type": "system_metric",
                    "metric_name": "cost",
                    "operator": "greater_than",
                    "value": 0.01,
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        assert "cost" in sql
        assert any("val" in k for k in params)

    def test_custom_attr_key_injection_rejected(self):
        config = {
            "project_ids": ["p1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "m",
                    "name": "injected",
                    "type": "custom_attribute",
                    "attribute_key": "key'] OR 1=1 --",
                    "attribute_type": "number",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        with pytest.raises(ValueError, match="Invalid attribute key"):
            builder.build_all_queries()

    def test_unknown_metric_type_raises(self):
        config = {
            "project_ids": ["p1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {"id": "x", "name": "x", "type": "unknown_type", "aggregation": "avg"}
            ],
        }
        builder = DashboardQueryBuilder(config)
        with pytest.raises(ValueError, match="Unknown metric type"):
            builder.build_all_queries()


class TestDashboardQueryBuilderFormatResults:
    def test_format_empty_results(self):
        config = {
            "project_ids": ["p1"],
            "granularity": "day",
            "time_range": {
                "custom_start": "2025-01-01T00:00:00",
                "custom_end": "2025-01-03T23:59:59",
            },
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        result = builder.format_results(
            [({"id": "latency", "name": "latency", "aggregation": "avg"}, [])]
        )
        assert "metrics" in result
        assert len(result["metrics"]) == 1
        series = result["metrics"][0]["series"]
        assert len(series) == 1
        assert series[0]["name"] == "total"
        # All buckets filled with null (Jan 1, 2, 3)
        assert len(series[0]["data"]) == 3
        assert all(d["value"] is None for d in series[0]["data"])

    def test_format_with_data(self):
        config = {
            "project_ids": ["p1"],
            "granularity": "day",
            "time_range": {
                "custom_start": "2025-01-01T00:00:00",
                "custom_end": "2025-01-04T23:59:59",
            },
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        result = builder.format_results(
            [
                (
                    {"id": "latency", "name": "latency", "aggregation": "avg"},
                    [
                        {"time_bucket": datetime(2025, 1, 1), "value": 123.456789},
                        {"time_bucket": datetime(2025, 1, 2), "value": 200.1},
                    ],
                )
            ]
        )
        metrics = result["metrics"]
        assert len(metrics) == 1
        series = metrics[0]["series"]
        assert len(series) == 1
        assert series[0]["name"] == "total"
        # 4 day buckets (Jan 1-4), 2 with data + 2 filled with null
        assert len(series[0]["data"]) == 4
        non_null = [d for d in series[0]["data"] if d["value"] is not None]
        assert len(non_null) == 2
        assert metrics[0]["unit"] == "ms"

    def test_format_with_breakdown(self):
        config = {
            "project_ids": ["p1"],
            "granularity": "day",
            "time_range": {
                "custom_start": "2025-01-01T00:00:00",
                "custom_end": "2025-01-02T23:59:59",
            },
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "breakdowns": [{"type": "system_metric", "name": "model"}],
        }
        builder = DashboardQueryBuilder(config)
        result = builder.format_results(
            [
                (
                    {"id": "latency", "name": "latency", "aggregation": "avg"},
                    [
                        {
                            "time_bucket": datetime(2025, 1, 1),
                            "value": 100.0,
                            "breakdown_value": "gpt-4",
                        },
                        {
                            "time_bucket": datetime(2025, 1, 1),
                            "value": 200.0,
                            "breakdown_value": "gpt-3.5",
                        },
                    ],
                )
            ]
        )
        series = result["metrics"][0]["series"]
        assert len(series) == 2
        series_names = [s["name"] for s in series]
        assert "gpt-4" in series_names
        assert "gpt-3.5" in series_names


# ===========================================================================
# Serializer Validation
# ===========================================================================


class TestSerializerValidation:
    def test_widget_serializer_width_too_large(self):
        data = {
            "name": "Test",
            "position": 0,
            "width": 20,
            "height": 4,
            "query_config": {},
            "chart_config": {},
        }
        serializer = DashboardWidgetSerializer(data=data)
        assert not serializer.is_valid()
        assert "width" in serializer.errors

    def test_widget_serializer_width_zero(self):
        data = {
            "name": "Test",
            "position": 0,
            "width": 0,
            "height": 4,
            "query_config": {},
            "chart_config": {},
        }
        serializer = DashboardWidgetSerializer(data=data)
        assert not serializer.is_valid()
        assert "width" in serializer.errors

    def test_widget_serializer_height_zero(self):
        data = {
            "name": "Test",
            "position": 0,
            "width": 6,
            "height": 0,
            "query_config": {},
            "chart_config": {},
        }
        serializer = DashboardWidgetSerializer(data=data)
        assert not serializer.is_valid()
        assert "height" in serializer.errors

    def test_widget_serializer_valid(self):
        data = {
            "name": "Test",
            "position": 0,
            "width": 6,
            "height": 4,
            "query_config": {"metrics": []},
            "chart_config": {"chart_type": "line"},
        }
        serializer = DashboardWidgetSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_widget_serializer_query_config_must_be_dict(self):
        data = {
            "name": "Test",
            "position": 0,
            "width": 6,
            "height": 4,
            "query_config": "not a dict",
            "chart_config": {},
        }
        serializer = DashboardWidgetSerializer(data=data)
        assert not serializer.is_valid()
        assert "query_config" in serializer.errors

    def test_dashboard_create_serializer_strips_name(self):
        data = {"name": "  My Dashboard  ", "description": "test"}
        serializer = DashboardCreateUpdateSerializer(data=data)
        assert serializer.is_valid()
        assert serializer.validated_data["name"] == "My Dashboard"

    def test_dashboard_create_serializer_blank_name(self):
        data = {"name": "   ", "description": "test"}
        serializer = DashboardCreateUpdateSerializer(data=data)
        assert not serializer.is_valid()
        assert "name" in serializer.errors


# ===========================================================================
# Query Execution (mocked ClickHouse) via Dashboard query action
# ===========================================================================


class TestDashboardQueryExecution:
    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AnalyticsQueryService")
    def test_query_action(self, mock_analytics_cls, auth_client, observe_project):
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.data = [{"time_bucket": "2025-01-01T00:00:00", "value": 123.45}]
        mock_service.execute_ch_query.return_value = mock_result
        mock_analytics_cls.return_value = mock_service

        response = auth_client.post(
            "/tracer/dashboard/query/",
            {
                "project_ids": [str(observe_project.id)],
                "granularity": "day",
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": "latency",
                        "name": "latency",
                        "type": "system_metric",
                        "aggregation": "avg",
                    }
                ],
            },
            format="json",
        )
        assert response.status_code == 200

    @pytest.mark.django_db
    def test_query_action_missing_project_ids_still_works(self, auth_client):
        """Query endpoint accepts requests without project_ids (unified picker)."""
        response = auth_client.post(
            "/tracer/dashboard/query/",
            {
                "granularity": "day",
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": "latency",
                        "name": "latency",
                        "type": "system_metric",
                        "aggregation": "avg",
                        "source": "traces",
                    }
                ],
            },
            format="json",
        )
        assert response.status_code == 200

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AnalyticsQueryService")
    def test_query_action_project_breakdown_uses_longer_timeout(
        self, mock_analytics_cls, auth_client, observe_project
    ):
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.data = [
            {
                "time_bucket": "2025-01-01T00:00:00",
                "breakdown_value": str(observe_project.id),
                "value": 123.45,
            }
        ]
        mock_service.execute_ch_query.return_value = mock_result
        mock_analytics_cls.return_value = mock_service

        response = auth_client.post(
            "/tracer/dashboard/query/",
            {
                "project_ids": [str(observe_project.id)],
                "granularity": "day",
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": "latency",
                        "name": "latency",
                        "type": "system_metric",
                        "aggregation": "avg",
                    }
                ],
                "breakdowns": [{"type": "system_metric", "name": "project"}],
            },
            format="json",
        )
        assert response.status_code == 200
        _, kwargs = mock_service.execute_ch_query.call_args
        assert kwargs["timeout_ms"] == 30000

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AnalyticsQueryService")
    def test_query_action_simulation_custom_attribute_routes_to_trace_builder(
        self, mock_analytics_cls, auth_client, observe_project
    ):
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.data = [{"time_bucket": "2025-01-01T00:00:00", "value": 0.01}]
        mock_service.execute_ch_query.return_value = mock_result
        mock_analytics_cls.return_value = mock_service

        response = auth_client.post(
            "/tracer/dashboard/query/",
            {
                "project_ids": [str(observe_project.id)],
                "granularity": "day",
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": "cost_breakdown.stt",
                        "name": "cost_breakdown.stt",
                        "type": "custom_attribute",
                        "attribute_key": "cost_breakdown.stt",
                        "attribute_type": "number",
                        "aggregation": "avg",
                        "source": "simulation",
                    }
                ],
            },
            format="json",
        )

        assert response.status_code == 200
        sql = mock_service.execute_ch_query.call_args.args[0]
        assert "span_attr_num" in sql
        assert "simulate_call_execution" not in sql

    def test_query_action_simulation_metric_failure_does_not_blank_other_metrics(
        self,
    ):
        viewset = DashboardViewSet()
        mock_service = MagicMock()
        success_result = MagicMock()
        success_result.data = [
            {"time_bucket": "2025-01-01T00:00:00", "value": 1.0},
        ]
        mock_service.execute_ch_query.side_effect = [
            Exception("Code: 47 unknown column"),
            success_result,
        ]

        sim_config = {
            "workflow": "simulation",
            "workspace_id": str(uuid.uuid4()),
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "duration",
                    "name": "duration",
                    "type": "system_metric",
                    "aggregation": "avg",
                    "source": "simulation",
                },
                {
                    "id": "success_rate",
                    "name": "success_rate",
                    "type": "system_metric",
                    "aggregation": "avg",
                    "source": "simulation",
                },
            ],
        }

        results = viewset._run_simulation_analytics_queries(
            mock_service,
            sim_config,
        )

        assert mock_service.execute_ch_query.call_count == 2
        assert results[0][0]["name"] == "duration"
        assert results[0][1] == []
        assert results[1][0]["name"] == "success_rate"
        assert results[1][1] == success_result.data

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AnalyticsQueryService")
    def test_query_action_simulation_metric_preserves_simulation_units(
        self, mock_analytics_cls, auth_client
    ):
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.data = [{"time_bucket": "2025-01-01T00:00:00", "value": 12.5}]
        mock_service.execute_ch_query.return_value = mock_result
        mock_analytics_cls.return_value = mock_service

        response = auth_client.post(
            "/tracer/dashboard/query/",
            {
                "workflow": "simulation",
                "granularity": "day",
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": "duration",
                        "name": "duration",
                        "displayName": "Duration",
                        "type": "system_metric",
                        "aggregation": "avg",
                        "source": "simulation",
                    }
                ],
            },
            format="json",
        )

        assert response.status_code == 200
        metric = response.json()["result"]["metrics"][0]
        assert metric["unit"] == "s"

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AnalyticsQueryService")
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    def test_filter_values_simulation_excludes_deleted_rows_and_handles_numeric_columns(
        self, _mock_enabled, mock_analytics_cls, auth_client
    ):
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.data = []
        mock_service.execute_ch_query.return_value = mock_result
        mock_analytics_cls.return_value = mock_service

        response = auth_client.get(
            "/tracer/dashboard/filter_values/?source=simulation&metric_name=duration&metric_type=system_metric"
        )

        assert response.status_code == 200
        sql = mock_service.execute_ch_query.call_args.args[0]
        assert "c.deleted = 0" in sql
        assert "c.duration_seconds IS NOT NULL" in sql
        assert "c.duration_seconds != ''" not in sql


class TestDashboardTraceTimeoutSelection:
    def test_default_trace_timeout_is_short(self):
        viewset = DashboardViewSet()
        timeout = viewset._get_trace_query_timeout_ms(
            {
                "metrics": [
                    {
                        "id": "latency",
                        "name": "latency",
                        "type": "system_metric",
                        "aggregation": "avg",
                    }
                ],
                "breakdowns": [],
            }
        )
        assert timeout == 10000

    def test_project_breakdown_uses_longer_timeout(self):
        viewset = DashboardViewSet()
        timeout = viewset._get_trace_query_timeout_ms(
            {
                "metrics": [
                    {
                        "id": "latency",
                        "name": "latency",
                        "type": "system_metric",
                        "aggregation": "avg",
                    }
                ],
                "breakdowns": [{"type": "system_metric", "name": "project"}],
            }
        )
        assert timeout == 30000

    def test_eval_metric_uses_longer_timeout(self):
        viewset = DashboardViewSet()
        timeout = viewset._get_trace_query_timeout_ms(
            {
                "metrics": [
                    {
                        "id": "eval1",
                        "name": "accuracy",
                        "type": "eval_metric",
                        "aggregation": "avg",
                    }
                ],
                "breakdowns": [],
            }
        )
        assert timeout == 30000


class TestDashboardMetricSourceNormalization:
    def test_simulation_custom_attribute_is_rerouted_to_traces(self):
        viewset = DashboardViewSet()
        normalized = viewset._normalize_metric_sources(
            [
                {
                    "id": "cost_breakdown.stt",
                    "type": "custom_attribute",
                    "source": "simulation",
                }
            ]
        )

        assert normalized[0]["source"] == "traces"

    def test_non_custom_simulation_metric_keeps_simulation_source(self):
        viewset = DashboardViewSet()
        normalized = viewset._normalize_metric_sources(
            [{"id": "stt_cost", "type": "system_metric", "source": "simulation"}]
        )

        assert normalized[0]["source"] == "simulation"


# ===========================================================================
# Widget Query Execution (mocked ClickHouse)
# ===========================================================================


class TestWidgetQueryExecution:
    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.get_clickhouse_client")
    def test_execute_query(
        self,
        mock_get_client,
        mock_enabled,
        auth_client,
        dashboard,
        dashboard_widget,
        observe_project,
    ):
        # Update widget to use a real project_id so validation passes
        dashboard_widget.query_config["project_ids"] = [str(observe_project.id)]
        dashboard_widget.save()

        mock_client = MagicMock()
        mock_client.execute_read.return_value = (
            [(datetime(2025, 1, 1), 123.45)],
            [("time_bucket", "DateTime"), ("value", "Float64")],
            5.0,
        )
        mock_get_client.return_value = mock_client

        response = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/{dashboard_widget.id}/query/"
        )
        assert response.status_code == 200
        data = response.json()["result"]
        assert "metrics" in data
        assert "time_range" in data

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=False)
    def test_execute_query_clickhouse_disabled(
        self, mock_enabled, auth_client, dashboard, dashboard_widget
    ):
        response = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/{dashboard_widget.id}/query/"
        )
        assert response.status_code == 400

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.get_clickhouse_client")
    def test_execute_query_simulation_custom_attribute_routes_to_trace_builder(
        self,
        mock_get_client,
        mock_enabled,
        auth_client,
        dashboard,
        dashboard_widget,
        observe_project,
    ):
        dashboard_widget.query_config = {
            "workflow": "simulation",
            "project_ids": [str(observe_project.id)],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "cost_breakdown.stt",
                    "name": "cost_breakdown.stt",
                    "type": "custom_attribute",
                    "attribute_key": "cost_breakdown.stt",
                    "attribute_type": "number",
                    "aggregation": "avg",
                    "source": "simulation",
                }
            ],
        }
        dashboard_widget.save(update_fields=["query_config"])

        mock_client = MagicMock()
        mock_client.execute_read.return_value = (
            [(datetime(2025, 1, 1), 0.01)],
            [("time_bucket", "DateTime"), ("value", "Float64")],
            5.0,
        )
        mock_get_client.return_value = mock_client

        response = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/{dashboard_widget.id}/query/"
        )

        assert response.status_code == 200
        sql = mock_client.execute_read.call_args.args[0]
        assert "span_attr_num" in sql
        assert "simulate_call_execution" not in sql

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.get_clickhouse_client")
    def test_preview_query(
        self, mock_get_client, mock_enabled, auth_client, dashboard, observe_project
    ):
        mock_client = MagicMock()
        mock_client.execute_read.return_value = (
            [(datetime(2025, 1, 1), 50.0)],
            [("time_bucket", "DateTime"), ("value", "Float64")],
            3.0,
        )
        mock_get_client.return_value = mock_client

        response = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/preview/",
            {
                "query_config": {
                    "project_ids": [str(observe_project.id)],
                    "granularity": "day",
                    "time_range": {"preset": "7D"},
                    "metrics": [
                        {
                            "id": "cost",
                            "name": "cost",
                            "type": "system_metric",
                            "aggregation": "sum",
                        }
                    ],
                }
            },
            format="json",
        )
        assert response.status_code == 200

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.get_clickhouse_client")
    def test_preview_query_project_breakdown_uses_longer_timeout(
        self, mock_get_client, mock_enabled, auth_client, dashboard, observe_project
    ):
        mock_client = MagicMock()
        mock_client.execute_read.return_value = (
            [(datetime(2025, 1, 1), str(observe_project.id), 50.0)],
            [
                ("time_bucket", "DateTime"),
                ("breakdown_value", "String"),
                ("value", "Float64"),
            ],
            3.0,
        )
        mock_get_client.return_value = mock_client

        response = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/preview/",
            {
                "query_config": {
                    "project_ids": [str(observe_project.id)],
                    "granularity": "day",
                    "time_range": {"preset": "7D"},
                    "metrics": [
                        {
                            "id": "latency",
                            "name": "latency",
                            "type": "system_metric",
                            "aggregation": "avg",
                        }
                    ],
                    "breakdowns": [{"type": "system_metric", "name": "project"}],
                }
            },
            format="json",
        )
        assert response.status_code == 200
        _, kwargs = mock_client.execute_read.call_args
        assert kwargs["timeout_ms"] == 30000
        data = response.json()["result"]
        assert "metrics" in data

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    def test_preview_query_missing_config(self, mock_enabled, auth_client, dashboard):
        response = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/preview/",
            {"query_config": {}},
            format="json",
        )
        assert response.status_code == 400


# ===========================================================================
# Model tests
# ===========================================================================


class TestDashboardModel:
    @pytest.mark.django_db
    def test_dashboard_str(self, dashboard):
        assert str(dashboard) == "Test Dashboard"

    @pytest.mark.django_db
    def test_widget_str(self, dashboard_widget):
        assert "Test Dashboard" in str(dashboard_widget)
        assert "Latency Chart" in str(dashboard_widget)

    @pytest.mark.django_db
    def test_dashboard_soft_delete(self, dashboard):
        dashboard.delete()
        dashboard.refresh_from_db()
        assert dashboard.deleted is True
        assert dashboard.deleted_at is not None

    @pytest.mark.django_db
    def test_widget_cascade_visibility(self, dashboard, dashboard_widget):
        """Widgets should be filtered by deleted=False in queryset."""
        dashboard_widget.deleted = True
        dashboard_widget.save()
        active_widgets = DashboardWidget.objects.filter(
            dashboard=dashboard, deleted=False
        )
        assert active_widgets.count() == 0

    @pytest.mark.django_db
    def test_widget_default_values(self, dashboard, user):
        widget = DashboardWidget.objects.create(
            dashboard=dashboard,
            created_by=user,
        )
        assert widget.name == "Untitled"
        assert widget.width == 12
        assert widget.height == 4
        assert widget.position == 0
        assert widget.query_config == {}
        assert widget.chart_config == {}


# ===========================================================================
# Frontend Payload Simulation Tests
# ===========================================================================
# These tests simulate the exact payloads the React frontend sends
# to ensure the full round-trip works without errors.


class TestFrontendPayloadSimulation:
    """Test DashboardQueryBuilder with payloads matching what the frontend sends."""

    # --- System metrics (all aggregations) ---

    @pytest.mark.parametrize("metric_name", list(SYSTEM_METRICS.keys()))
    def test_all_system_metrics(self, metric_name):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "30D"},
            "metrics": [
                {
                    "id": metric_name,
                    "name": metric_name,
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "filters": [],
            "breakdowns": [],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 1
        sql, params, info = queries[0]
        assert "project_id IN" in sql
        assert "start_time >=" in sql
        assert info["type"] == "system_metric"

    @pytest.mark.parametrize("agg", list(AGGREGATIONS.keys()))
    def test_all_aggregations_with_latency(self, agg):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": agg,
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 1

    # --- Eval metrics (frontend sends config_id as the UUID) ---

    def test_eval_metric_frontend_payload(self):
        eval_uuid = str(uuid.uuid4())
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "30D"},
            "metrics": [
                {
                    "id": eval_uuid,
                    "name": "Coherence",
                    "type": "eval_metric",
                    "config_id": eval_uuid,
                    "aggregation": "avg",
                }
            ],
            "filters": [],
            "breakdowns": [],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 1
        sql, params, _ = queries[0]
        assert "tracer_eval_logger" in sql
        assert params["eval_config_id"] == eval_uuid

    # --- Annotation metrics ---

    def test_annotation_metric_frontend_payload(self):
        label_uuid = str(uuid.uuid4())
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": label_uuid,
                    "name": "Quality",
                    "type": "annotation_metric",
                    "label_id": label_uuid,
                    "aggregation": "avg",
                }
            ],
            "filters": [],
            "breakdowns": [],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 1
        sql, params, _ = queries[0]
        assert "model_hub_score" in sql
        assert params["annotation_label_id"] == label_uuid

    # --- Custom attribute metrics ---

    def test_custom_attr_number_frontend_payload(self):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "30D"},
            "metrics": [
                {
                    "id": "llm.token_count.prompt",
                    "name": "llm.token_count.prompt",
                    "type": "custom_attribute",
                    "attribute_key": "llm.token_count.prompt",
                    "attribute_type": "number",
                    "aggregation": "avg",
                }
            ],
            "filters": [],
            "breakdowns": [],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "span_attr_num" in sql
        assert "llm.token_count.prompt" in sql

    def test_custom_attr_string_frontend_payload(self):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "hour",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "llm.model",
                    "name": "llm.model",
                    "type": "custom_attribute",
                    "attribute_key": "llm.model",
                    "attribute_type": "string",
                    "aggregation": "count",
                }
            ],
            "filters": [],
            "breakdowns": [],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        # count() aggregation doesn't reference the column, just verify query builds
        assert "FROM spans" in sql
        assert "count()" in sql

    # --- Multiple metrics at once ---

    def test_mixed_metrics_frontend_payload(self):
        eval_uuid = str(uuid.uuid4())
        config = {
            "project_ids": ["proj-1", "proj-2"],
            "granularity": "day",
            "time_range": {"preset": "30D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                },
                {
                    "id": "cost",
                    "name": "cost",
                    "type": "system_metric",
                    "aggregation": "sum",
                },
                {
                    "id": eval_uuid,
                    "name": "Coherence",
                    "type": "eval_metric",
                    "config_id": eval_uuid,
                    "aggregation": "avg",
                },
            ],
            "filters": [],
            "breakdowns": [],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 3

    # --- Filters ---

    def test_system_filter_frontend_payload(self):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "filters": [
                {
                    "metric_type": "system_metric",
                    "metric_name": "cost",
                    "operator": "greater_than",
                    "value": "0.01",
                }
            ],
            "breakdowns": [],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        assert "cost" in sql
        assert params["f_0_val"] == 0.01

    def test_custom_attr_filter_frontend_payload(self):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "filters": [
                {
                    "metric_type": "custom_attribute",
                    "metric_name": "llm.model",
                    "operator": "contains",
                    "value": "gpt-4",
                    "attribute_type": "string",
                }
            ],
            "breakdowns": [],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        assert "span_attr_str" in sql
        assert "llm.model" in sql

    def test_eval_filter_frontend_payload(self):
        eval_uuid = str(uuid.uuid4())
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "filters": [
                {
                    "metric_type": "eval_metric",
                    "metric_name": eval_uuid,
                    "operator": "greater_than",
                    "value": "0.5",
                    "output_type": "SCORE",
                }
            ],
            "breakdowns": [],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        assert "tracer_eval_logger" in sql
        assert "trace_id IN" in sql

    # --- Breakdowns ---

    def test_breakdown_model_frontend_payload(self):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "breakdowns": [{"name": "model", "type": "system_metric"}],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "breakdown_value" in sql
        assert "model" in sql

    def test_breakdown_custom_attr_frontend_payload(self):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "breakdowns": [
                {
                    "name": "llm.model",
                    "type": "custom_attribute",
                    "attribute_type": "string",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "span_attr_str" in sql
        assert "breakdown_value" in sql

    # --- Time ranges ---

    @pytest.mark.parametrize(
        "preset", ["30m", "6h", "today", "yesterday", "7D", "30D", "3M", "6M", "12M"]
    )
    def test_all_time_presets(self, preset):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": preset},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 1

    # --- Edge cases ---

    def test_empty_filters_and_breakdowns(self):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "filters": [],
            "breakdowns": [],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 1

    def test_five_metrics_max(self):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": name,
                    "name": name,
                    "type": "system_metric",
                    "aggregation": "avg",
                }
                for name in ["latency", "cost", "tokens", "error_rate", "input_tokens"]
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 5

    def test_format_results_full_roundtrip(self):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {
                "custom_start": "2025-01-01T00:00:00",
                "custom_end": "2025-01-03T23:59:59",
            },
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                },
                {
                    "id": "cost",
                    "name": "cost",
                    "type": "system_metric",
                    "aggregation": "sum",
                },
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 2

        # Simulate ClickHouse returning data
        mock_results = [
            (
                queries[0][2],  # metric_info
                [
                    {"time_bucket": datetime(2025, 1, 1), "value": 100.5},
                    {"time_bucket": datetime(2025, 1, 2), "value": 120.3},
                ],
            ),
            (
                queries[1][2],
                [
                    {"time_bucket": datetime(2025, 1, 1), "value": 0.05},
                    {"time_bucket": datetime(2025, 1, 2), "value": 0.08},
                ],
            ),
        ]
        result = builder.format_results(mock_results)

        assert "metrics" in result
        assert len(result["metrics"]) == 2
        assert result["granularity"] == "day"
        assert result["metrics"][0]["name"] == "latency"
        assert result["metrics"][0]["unit"] == "ms"
        assert result["metrics"][1]["name"] == "cost"
        assert result["metrics"][1]["unit"] == "$"
        # 3 day buckets (Jan 1-3), 2 with data + 1 filled with null
        data = result["metrics"][0]["series"][0]["data"]
        assert len(data) == 3
        non_null = [d for d in data if d["value"] is not None]
        assert len(non_null) == 2


# ===========================================================================
# Security and Edge Case Tests
# ===========================================================================

from tracer.serializers.dashboard import DashboardQuerySerializer
from tracer.services.clickhouse.query_builders.dashboard import (
    FILTER_OPERATORS,
    GRANULARITY_TO_CH,
    METRIC_UNITS,
    PRESET_RANGES,
    _coerce_filter_value,
    _generate_time_buckets,
    _sanitize_attr_key,
)
from tracer.services.clickhouse.query_builders.dashboard_base import (
    DashboardQueryBuilderBase,
)


class TestQueryBuilderSecurity:
    """Security tests for DashboardQueryBuilder to prevent injection and misuse."""

    def test_unknown_metric_name_falls_back_to_custom_attribute(
        self, sample_query_config
    ):
        """Verify that passing an unknown metric_name falls back to custom attribute query."""
        sample_query_config["metrics"] = [
            {
                "name": "nonexistent_metric",
                "type": "system_metric",
                "aggregation": "avg",
            }
        ]
        builder = DashboardQueryBuilder(sample_query_config)
        sql, params = builder.build_metric_query(sample_query_config["metrics"][0])
        # Falls back to custom attribute — queries span_attr_num map
        assert "span_attr_num" in sql or "span_attr_str" in sql

    def test_sql_injection_in_metric_name_blocked(self, sample_query_config):
        """Verify that a SQL injection attempt in metric_name is safely handled."""
        sample_query_config["metrics"] = [
            {
                "name": "1; DROP TABLE spans--",
                "type": "system_metric",
                "aggregation": "avg",
            }
        ]
        builder = DashboardQueryBuilder(sample_query_config)
        # Falls back to custom attribute, which rejects unsafe attribute keys
        with pytest.raises(ValueError, match="Invalid attribute key"):
            builder.build_metric_query(sample_query_config["metrics"][0])

    def test_like_metacharacters_escaped(self):
        """Verify that _coerce_filter_value escapes % in LIKE patterns."""
        result = _coerce_filter_value("100%", "str_contains")
        assert result == "%100\\%%"

    def test_like_underscore_escaped(self):
        """Verify that _coerce_filter_value escapes underscore in LIKE patterns."""
        result = _coerce_filter_value("test_val", "str_contains")
        assert "\\_" in result
        assert result == "%test\\_val%"

    def test_filter_value_parameterized_not_interpolated(self, sample_query_config):
        """Verify filter values go through %(param)s placeholders, not f-string interpolation."""
        sample_query_config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "latency",
                "operator": "greater_than",
                "value": 100,
            }
        ]
        builder = DashboardQueryBuilder(sample_query_config)
        sql, params = builder.build_metric_query(sample_query_config["metrics"][0])
        # The SQL should use %(f_0_val)s placeholder, not the raw value
        assert "%(f_0_val)s" in sql
        assert "f_0_val" in params

    def test_aggregation_fallback_uses_avg(self, sample_query_config):
        """Verify unknown aggregation falls back to avg safely."""
        sample_query_config["metrics"] = [
            {
                "name": "latency",
                "type": "system_metric",
                "aggregation": "unknown_agg_xyz",
            }
        ]
        builder = DashboardQueryBuilder(sample_query_config)
        sql, _ = builder.build_metric_query(sample_query_config["metrics"][0])
        # AGGREGATIONS.get("unknown_agg_xyz", "avg({col})") falls back to avg
        assert "avg(" in sql


class TestQueryBuilderEdgeCases:
    """Edge case tests for DashboardQueryBuilder."""

    def test_empty_metrics_list(self, sample_query_config):
        """Verify build_all_queries handles empty metrics gracefully."""
        sample_query_config["metrics"] = []
        builder = DashboardQueryBuilder(sample_query_config)
        results = builder.build_all_queries()
        assert results == []

    def test_single_metric_no_breakdown(self, sample_query_config):
        """Basic case with one metric, no filters, no breakdowns."""
        sample_query_config["filters"] = []
        sample_query_config["breakdowns"] = []
        builder = DashboardQueryBuilder(sample_query_config)
        results = builder.build_all_queries()
        assert len(results) == 1
        sql, params, info = results[0]
        assert "time_bucket" in sql
        assert "breakdown_value" not in sql
        assert info["name"] == "latency"

    def test_max_series_cap(self, sample_query_config):
        """Verify format_results caps at MAX_SERIES (100)."""
        sample_query_config["time_range"] = {
            "custom_start": "2025-01-01T00:00:00",
            "custom_end": "2025-01-02T00:00:00",
        }
        sample_query_config["breakdowns"] = [{"name": "model", "type": "system_metric"}]
        builder = DashboardQueryBuilder(sample_query_config)
        # Generate 150 breakdown values
        rows = [
            {
                "time_bucket": datetime(2025, 1, 1),
                "value": float(i),
                "breakdown_value": f"model-{i}",
            }
            for i in range(150)
        ]
        result = builder.format_results(
            [({"id": "latency", "name": "latency", "aggregation": "avg"}, rows)]
        )
        series = result["metrics"][0]["series"]
        assert len(series) <= 100

    def test_zero_total_in_pie_data(self, sample_query_config):
        """Verify no division by zero when all values are zero."""
        sample_query_config["time_range"] = {
            "custom_start": "2025-01-01T00:00:00",
            "custom_end": "2025-01-02T00:00:00",
        }
        builder = DashboardQueryBuilder(sample_query_config)
        rows = [
            {"time_bucket": datetime(2025, 1, 1), "value": 0},
        ]
        result = builder.format_results(
            [({"id": "latency", "name": "latency", "aggregation": "avg"}, rows)]
        )
        # Should complete without error
        assert result["metrics"][0]["series"][0]["data"][0]["value"] == 0

    def test_custom_date_range_parsing(self, sample_query_config):
        """Verify custom start/end dates are parsed correctly."""
        sample_query_config["time_range"] = {
            "custom_start": "2024-06-15T10:30:00",
            "custom_end": "2024-06-20T18:00:00",
        }
        builder = DashboardQueryBuilder(sample_query_config)
        start, end = builder.parse_time_range()
        assert start.year == 2024
        assert start.month == 6
        assert start.day == 15
        assert start.hour == 10
        assert end.day == 20
        assert end.hour == 18

    def test_minute_granularity_generates_correct_buckets(self):
        """Verify bucket count for 1-hour range with minute granularity."""
        start = datetime(2025, 1, 1, 0, 0, 0)
        end = datetime(2025, 1, 1, 1, 0, 0)
        buckets = _generate_time_buckets(start, end, "minute")
        # 0:00 through 1:00 inclusive = 61 buckets
        assert len(buckets) == 61

    def test_very_large_time_range_buckets(self):
        """Verify 12M with minute granularity produces output (potentially large but bounded)."""
        start = datetime(2024, 1, 1, 0, 0, 0)
        end = datetime(2024, 1, 2, 0, 0, 0)  # 1 day at minute granularity
        buckets = _generate_time_buckets(start, end, "minute")
        # 1440 minutes in a day + 1 for inclusive end
        assert len(buckets) == 1441

    def test_preset_ranges_all_valid(self):
        """Verify all PRESET_RANGES produce valid (start, end) tuples."""
        for preset_key in PRESET_RANGES:
            config = {
                "project_ids": ["test-project"],
                "granularity": "day",
                "time_range": {"preset": preset_key},
                "metrics": [],
            }
            builder = DashboardQueryBuilder(config)
            start, end = builder.parse_time_range()
            assert isinstance(start, datetime)
            assert isinstance(end, datetime)
            assert start <= end, f"Preset {preset_key}: start > end"

    def test_granularity_to_ch_mapping(self):
        """Verify all granularities map to valid ClickHouse functions."""
        expected_functions = {
            "minute": "toStartOfMinute",
            "hour": "toStartOfHour",
            "day": "toStartOfDay",
            "week": "toMonday",
            "month": "toStartOfMonth",
            "year": "toStartOfYear",
        }
        for gran, expected_fn in expected_functions.items():
            assert GRANULARITY_TO_CH[gran] == expected_fn


class TestDashboardQuerySerializer:
    """Tests for the DashboardQuerySerializer validation."""

    def test_valid_query_config_passes(self):
        """Verify a fully valid query config passes serializer validation."""
        data = {
            "workflow": "observability",
            "project_ids": ["proj-1"],
            "time_range": {"preset": "7D"},
            "granularity": "day",
            "metrics": [
                {"name": "latency", "type": "system_metric", "aggregation": "avg"}
            ],
        }
        serializer = DashboardQuerySerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_missing_metrics_fails(self):
        """Verify missing metrics field fails validation."""
        data = {
            "workflow": "observability",
            "project_ids": ["proj-1"],
            "time_range": {"preset": "7D"},
            "granularity": "day",
        }
        serializer = DashboardQuerySerializer(data=data)
        assert not serializer.is_valid()
        assert "metrics" in serializer.errors

    def test_empty_metrics_list_fails(self):
        """Verify empty metrics list fails validation (min_length=1)."""
        data = {
            "workflow": "observability",
            "project_ids": ["proj-1"],
            "time_range": {"preset": "7D"},
            "granularity": "day",
            "metrics": [],
        }
        serializer = DashboardQuerySerializer(data=data)
        assert not serializer.is_valid()
        assert "metrics" in serializer.errors

    def test_too_many_metrics_fails(self):
        """Verify >5 metrics fails validation (max_length=5)."""
        data = {
            "workflow": "observability",
            "project_ids": ["proj-1"],
            "time_range": {"preset": "7D"},
            "granularity": "day",
            "metrics": [
                {"name": f"m{i}", "type": "system_metric", "aggregation": "avg"}
                for i in range(6)
            ],
        }
        serializer = DashboardQuerySerializer(data=data)
        assert not serializer.is_valid()
        assert "metrics" in serializer.errors

    def test_invalid_granularity_fails(self):
        """Verify an invalid granularity value fails validation."""
        data = {
            "workflow": "observability",
            "project_ids": ["proj-1"],
            "time_range": {"preset": "7D"},
            "granularity": "microsecond",
            "metrics": [
                {"name": "latency", "type": "system_metric", "aggregation": "avg"}
            ],
        }
        serializer = DashboardQuerySerializer(data=data)
        assert not serializer.is_valid()
        assert "granularity" in serializer.errors

    def test_missing_time_range_uses_default(self):
        """Verify missing time_range fails validation (required=True)."""
        data = {
            "workflow": "observability",
            "project_ids": ["proj-1"],
            "granularity": "day",
            "metrics": [
                {"name": "latency", "type": "system_metric", "aggregation": "avg"}
            ],
        }
        serializer = DashboardQuerySerializer(data=data)
        assert not serializer.is_valid()
        assert "time_range" in serializer.errors


class TestFilterOperators:
    """Tests for FILTER_OPERATORS templates producing valid SQL patterns."""

    def test_all_filter_operators_produce_valid_sql(self):
        """Iterate FILTER_OPERATORS dict, verify each template produces valid SQL."""
        for op_name, template in FILTER_OPERATORS.items():
            # Templates with format placeholders need prefix and idx
            if "{prefix}" in template and "{idx}" in template:
                result = template.format(prefix="f_", idx=0)
            else:
                result = template
            # Should produce a non-empty string
            assert len(result) > 0, f"Operator {op_name} produced empty SQL"
            # Should not contain un-replaced format placeholders
            assert (
                "{" not in result
            ), f"Operator {op_name} has unresolved placeholder: {result}"

    def test_between_operator_requires_two_values(self, sample_query_config):
        """Verify between operator with non-list value is skipped."""
        sample_query_config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "latency",
                "operator": "between",
                "value": "single_value",  # Should be a list of 2
            }
        ]
        builder = DashboardQueryBuilder(sample_query_config)
        sql, params = builder.build_metric_query(sample_query_config["metrics"][0])
        # Should not have BETWEEN since value is not a list of 2
        assert "BETWEEN" not in sql

    def test_string_contains_case_insensitive(self):
        """Verify str_contains uses LIKE (case-insensitive matching via _coerce_filter_value)."""
        assert "LIKE" in FILTER_OPERATORS["str_contains"]

    def test_is_set_operator_generates_not_null(self):
        """Verify is_set produces != '' (NOT NULL equivalent for strings)."""
        assert FILTER_OPERATORS["is_set"] == "!= ''"

    def test_is_not_set_operator_generates_null(self):
        """Verify is_not_set produces = '' (NULL equivalent for strings)."""
        assert FILTER_OPERATORS["is_not_set"] == "= ''"


class TestDashboardQueryBuilderBase:
    """Tests for the DashboardQueryBuilderBase shared base class."""

    def test_base_class_build_metric_query_raises_not_implemented(self):
        """Verify that calling build_metric_query on the base class raises NotImplementedError."""
        config = {
            "granularity": "day",
            "metrics": [
                {"name": "test", "type": "system_metric", "aggregation": "avg"}
            ],
        }
        base = DashboardQueryBuilderBase(config)
        with pytest.raises(NotImplementedError):
            base.build_metric_query(config["metrics"][0])

    def test_base_class_build_all_queries_dispatches_to_subclass(self):
        """Verify build_all_queries calls build_metric_query for each metric."""

        class TestSubclass(DashboardQueryBuilderBase):
            def build_metric_query(self, metric):
                return f"SELECT 1 -- {metric['name']}", {"key": "val"}

            def parse_time_range(self):
                return datetime(2025, 1, 1), datetime(2025, 1, 2)

        config = {
            "granularity": "day",
            "metrics": [
                {"name": "metric_a", "type": "system_metric", "aggregation": "avg"},
                {"name": "metric_b", "type": "system_metric", "aggregation": "sum"},
            ],
        }
        builder = TestSubclass(config)
        results = builder.build_all_queries()
        assert len(results) == 2
        assert "metric_a" in results[0][0]
        assert "metric_b" in results[1][0]
        assert results[0][2]["name"] == "metric_a"
        assert results[1][2]["name"] == "metric_b"

    def test_format_metric_result_basic(self):
        """Verify _format_metric_result produces correct structure with basic data."""
        config = {
            "granularity": "day",
            "metrics": [],
            "breakdowns": [],
        }
        from datetime import timezone as _tz

        base = DashboardQueryBuilderBase(config)
        # Buckets must use UTC-aware ISO format to match _build_series_data output
        all_buckets = [
            datetime(2025, 1, 1, tzinfo=_tz.utc).isoformat(),
            datetime(2025, 1, 2, tzinfo=_tz.utc).isoformat(),
        ]
        metric_info = {"id": "latency", "name": "latency", "aggregation": "avg"}
        rows = [
            {"time_bucket": datetime(2025, 1, 1), "value": 42.5},
        ]
        result = base._format_metric_result(
            metric_info, rows, all_buckets, {"latency": "ms"}
        )
        assert result["name"] == "latency"
        assert result["unit"] == "ms"
        assert len(result["series"]) == 1
        assert result["series"][0]["name"] == "total"
        assert len(result["series"][0]["data"]) == 2
        assert result["series"][0]["data"][0]["value"] == 42.5

    def test_format_metric_result_with_name_map(self):
        """Verify _format_metric_result resolves breakdown values via name_map."""
        config = {
            "granularity": "day",
            "metrics": [],
            "breakdowns": [{"name": "project"}],
        }
        base = DashboardQueryBuilderBase(config)
        all_buckets = [datetime(2025, 1, 1).isoformat()]
        metric_info = {"id": "latency", "name": "latency", "aggregation": "avg"}
        rows = [
            {
                "time_bucket": datetime(2025, 1, 1),
                "value": 50.0,
                "breakdown_value": "uuid-123",
            },
        ]
        name_map = {"uuid-123": "My Project"}
        result = base._format_metric_result(
            metric_info,
            rows,
            all_buckets,
            {"latency": "ms"},
            name_map=name_map,
            name_map_breakdown="project",
        )
        series_names = [s["name"] for s in result["series"]]
        assert "My Project" in series_names

    def test_format_metric_result_uses_metric_id_for_unit_lookup(self):
        config = {
            "granularity": "day",
            "metrics": [],
            "breakdowns": [],
        }
        base = DashboardQueryBuilderBase(config)
        all_buckets = [datetime(2025, 1, 1).isoformat()]
        metric_info = {"id": "duration", "name": "Duration", "aggregation": "avg"}
        rows = [{"time_bucket": datetime(2025, 1, 1), "value": 42.5}]
        result = base._format_metric_result(metric_info, rows, all_buckets, {"duration": "s"})
        assert result["name"] == "Duration"
        assert result["unit"] == "s"
