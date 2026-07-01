"""
Project list filter + alert-count tests.

Two concerns, both backed by Postgres (these features never touch ClickHouse —
project name/tags and alert monitors live in PG; only volume/last-active come
from CH):

  1. ``apply_project_list_filters`` — the ``filters`` JSON operator support on
     ``GET /tracer/project/list_projects/`` (name/tags ×
     equals/contains/not_equals/not_contains). Tested directly against a
     ``Project`` queryset — no view, no auth, no ClickHouse.

  2. The Alerts column count (``issues``) — the per-project ``UserAlertMonitor``
     count surfaced by ``list_projects``. Tested through the endpoint.

Filter shape (mirrors the trace/span list convention)::

    filters=[{"column_id": "name"|"tags",
              "filter_config": {"filter_op": "equals"|"contains"
                                            |"not_equals"|"not_contains",
                                "filter_value": "<str>"}}]
"""

import json

import pytest
from rest_framework import status

from model_hub.models.ai_model import AIModel
from tracer.models.monitor import (
    ComparisonOperatorChoices,
    MonitorMetricTypeChoices,
    UserAlertMonitor,
)
from tracer.models.project import Project
from tracer.queries.projects import apply_project_list_filters

LIST_URL = "/tracer/project/list_projects/"


def _filters(*specs):
    """Build the ``filters`` JSON param from (column_id, filter_op, value)."""
    return json.dumps(
        [
            {
                "column_id": column_id,
                "filter_config": {"filter_op": op, "filter_value": value},
            }
            for (column_id, op, value) in specs
        ]
    )


@pytest.fixture
def filter_projects(db, organization, workspace):
    """Three observe projects with distinct names + tags (Postgres only).

      Checkout Service  tags=[prod, critical]
      Search Service    tags=[production]
      Billing API       tags=[beta]
    """
    specs = [
        ("Checkout Service", ["prod", "critical"]),
        ("Search Service", ["production"]),
        ("Billing API", ["beta"]),
    ]
    return [
        Project.objects.create(
            name=name,
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
            tags=tags,
        )
        for name, tags in specs
    ]


def _base_qs(organization):
    return Project.no_workspace_objects.filter(
        organization=organization, trace_type="observe", deleted=False
    )


def _names(queryset):
    return sorted(queryset.values_list("name", flat=True))


@pytest.mark.integration
class TestApplyProjectListFiltersName:
    """`filters` operator on the `name` column (direct, Postgres-only)."""

    def test_equals_is_exact_and_case_insensitive(self, organization, filter_projects):
        qs = apply_project_list_filters(
            _base_qs(organization), _filters(("name", "equals", "checkout service"))
        )
        assert _names(qs) == ["Checkout Service"]

    def test_equals_no_partial_match(self, organization, filter_projects):
        qs = apply_project_list_filters(
            _base_qs(organization), _filters(("name", "equals", "Checkout"))
        )
        assert _names(qs) == []

    def test_contains_substring(self, organization, filter_projects):
        qs = apply_project_list_filters(
            _base_qs(organization), _filters(("name", "contains", "Service"))
        )
        assert _names(qs) == ["Checkout Service", "Search Service"]

    def test_not_contains(self, organization, filter_projects):
        qs = apply_project_list_filters(
            _base_qs(organization), _filters(("name", "not_contains", "Service"))
        )
        assert _names(qs) == ["Billing API"]

    def test_not_equals(self, organization, filter_projects):
        qs = apply_project_list_filters(
            _base_qs(organization), _filters(("name", "not_equals", "Checkout Service"))
        )
        assert _names(qs) == ["Billing API", "Search Service"]

    def test_default_operator_is_contains(self, organization, filter_projects):
        raw = json.dumps(
            [{"column_id": "name", "filter_config": {"filter_value": "Service"}}]
        )
        qs = apply_project_list_filters(_base_qs(organization), raw)
        assert _names(qs) == ["Checkout Service", "Search Service"]


@pytest.mark.integration
class TestApplyProjectListFiltersTags:
    """`filters` operator on the `tags` column (direct, Postgres-only)."""

    def test_equals_is_exact_tag_membership(self, organization, filter_projects):
        # 'prod' is an exact tag on Checkout Service; 'production' must NOT match.
        qs = apply_project_list_filters(
            _base_qs(organization), _filters(("tags", "equals", "prod"))
        )
        assert _names(qs) == ["Checkout Service"]

    def test_equals_distinct_tag(self, organization, filter_projects):
        qs = apply_project_list_filters(
            _base_qs(organization), _filters(("tags", "equals", "production"))
        )
        assert _names(qs) == ["Search Service"]

    def test_contains_substring_across_tag_values(self, organization, filter_projects):
        # 'prod' substring matches the exact tag 'prod' AND the tag 'production'.
        qs = apply_project_list_filters(
            _base_qs(organization), _filters(("tags", "contains", "prod"))
        )
        assert _names(qs) == ["Checkout Service", "Search Service"]

    def test_contains_does_not_match_serialized_array_artifacts(
        self, organization, filter_projects
    ):
        # Matched per element via jsonb_array_elements_text, so the serialized
        # array's separator/quotes are not matchable — only the tag *values*.
        # A whole-array text cast of '["prod", "critical"]' would falsely match
        # the inter-element separator '", "'.
        qs = apply_project_list_filters(
            _base_qs(organization), _filters(("tags", "contains", '", "'))
        )
        assert _names(qs) == []

    def test_not_contains(self, organization, filter_projects):
        qs = apply_project_list_filters(
            _base_qs(organization), _filters(("tags", "not_contains", "prod"))
        )
        assert _names(qs) == ["Billing API"]

    def test_not_equals(self, organization, filter_projects):
        qs = apply_project_list_filters(
            _base_qs(organization), _filters(("tags", "not_equals", "prod"))
        )
        assert _names(qs) == ["Billing API", "Search Service"]


@pytest.mark.integration
class TestApplyProjectListFiltersEdgeCases:
    """Combined filters + malformed / empty / unknown input (Postgres-only)."""

    def test_name_and_tag_combined_are_anded(self, organization, filter_projects):
        qs = apply_project_list_filters(
            _base_qs(organization),
            _filters(("name", "contains", "Service"), ("tags", "contains", "prod")),
        )
        assert _names(qs) == ["Checkout Service", "Search Service"]

    def test_none_param_is_noop(self, organization, filter_projects):
        qs = apply_project_list_filters(_base_qs(organization), None)
        assert _names(qs) == ["Billing API", "Checkout Service", "Search Service"]

    def test_empty_filters_is_noop(self, organization, filter_projects):
        qs = apply_project_list_filters(_base_qs(organization), "[]")
        assert _names(qs) == ["Billing API", "Checkout Service", "Search Service"]

    def test_malformed_json_is_noop_not_raise(self, organization, filter_projects):
        # Must not raise — the list should still render unfiltered.
        qs = apply_project_list_filters(_base_qs(organization), "{not valid json")
        assert _names(qs) == ["Billing API", "Checkout Service", "Search Service"]

    def test_blank_value_is_skipped(self, organization, filter_projects):
        qs = apply_project_list_filters(
            _base_qs(organization), _filters(("name", "equals", ""))
        )
        assert _names(qs) == ["Billing API", "Checkout Service", "Search Service"]

    def test_unknown_column_is_skipped(self, organization, filter_projects):
        qs = apply_project_list_filters(
            _base_qs(organization), _filters(("description", "contains", "x"))
        )
        assert _names(qs) == ["Billing API", "Checkout Service", "Search Service"]

    def test_non_string_value_is_skipped(self, organization, filter_projects):
        # A non-string filter_value (e.g. a number) is skipped, never coerced
        # into a query that could 500.
        raw = json.dumps(
            [
                {
                    "column_id": "name",
                    "filter_config": {"filter_op": "equals", "filter_value": 123},
                }
            ]
        )
        qs = apply_project_list_filters(_base_qs(organization), raw)
        assert _names(qs) == ["Billing API", "Checkout Service", "Search Service"]


@pytest.mark.integration
@pytest.mark.api
class TestProjectListAlertCount:
    """The Alerts column (`issues`) reflects the per-project monitor count."""

    def _make_alert(self, organization, workspace, project, name):
        return UserAlertMonitor.objects.create(
            name=name,
            metric_type=MonitorMetricTypeChoices.values[0],
            threshold_operator=ComparisonOperatorChoices.values[0],
            organization=organization,
            workspace=workspace,
            project=project,
        )

    def test_issues_counts_alerts_per_project(
        self, auth_client, organization, workspace, filter_projects
    ):
        checkout, search, billing = filter_projects
        self._make_alert(organization, workspace, checkout, "a1")
        self._make_alert(organization, workspace, checkout, "a2")
        self._make_alert(organization, workspace, search, "a3")
        # billing has none

        resp = auth_client.get(LIST_URL, {"project_type": "observe", "page_size": 25})
        assert resp.status_code == status.HTTP_200_OK
        table = (resp.json().get("result") or resp.json())["table"]
        issues = {row["name"]: row["issues"] for row in table}
        assert issues["Checkout Service"] == 2
        assert issues["Search Service"] == 1
        assert issues["Billing API"] == 0

    def test_soft_deleted_alerts_not_counted(
        self, auth_client, organization, workspace, filter_projects
    ):
        checkout = filter_projects[0]
        a = self._make_alert(organization, workspace, checkout, "a1")
        a.deleted = True
        a.save(update_fields=["deleted"])

        resp = auth_client.get(LIST_URL, {"project_type": "observe", "page_size": 25})
        table = (resp.json().get("result") or resp.json())["table"]
        issues = {row["name"]: row["issues"] for row in table}
        assert issues["Checkout Service"] == 0


@pytest.mark.integration
@pytest.mark.api
class TestProjectListFilterEndpoint:
    """The `filters` param works through the full endpoint (pagination + the
    trace_count / run_count aggregates), not just the helper in isolation —
    guards the RawSQL tag annotation coexisting with the view's Count()s.
    """

    def test_tags_contains_filter_returns_matching_projects(
        self, auth_client, filter_projects
    ):
        filters = json.dumps(
            [
                {
                    "column_id": "tags",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "contains",
                        "filter_value": "prod",
                    },
                }
            ]
        )
        resp = auth_client.get(
            LIST_URL,
            {"project_type": "observe", "page_size": 25, "filters": filters},
        )
        assert resp.status_code == status.HTTP_200_OK
        table = (resp.json().get("result") or resp.json())["table"]
        names = sorted(row["name"] for row in table)
        assert names == ["Checkout Service", "Search Service"]
