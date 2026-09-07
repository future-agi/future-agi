"""Offline native visibility contracts; no database/CDC setup or live claims.

SQL tests inspect the real generated predicates against the authoritative CDC
schema. ORM doubles evaluate the actual lookup kwargs so dropping an ownership
or parent-liveness condition fails the endpoint tests. No ClickHouse rows or
replication events are fabricated; real PeerDB lifecycle E2E remains separate.
"""

import inspect
import re
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest
from clickhouse_driver.errors import ServerException

from accounts.models.workspace import Workspace
from model_hub.models.develop_dataset import Column, Dataset
from tracer.services.clickhouse import dataset_filter_values as native
from tracer.services.clickhouse import schema
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.views import dashboard

pytestmark = pytest.mark.unit

ORG = UUID("10000000-0000-4000-8000-000000000001")
WORKSPACE = UUID("10000000-0000-4000-8000-000000000002")
DATASET = UUID("10000000-0000-4000-8000-000000000003")
COLUMN = UUID("10000000-0000-4000-8000-000000000004")
FOREIGN = UUID("20000000-0000-4000-8000-000000000001")


class ColumnLookup:
    """Only the queryset operations used by native column authorization."""

    def __init__(self, rows, filters):
        self.rows = rows
        self.filters = filters

    def select_related(self, relation):
        assert relation == "dataset"
        return self

    def filter(self, **kwargs):
        self.filters.append(kwargs)

        def matches(row):
            for name, expected in kwargs.items():
                actual = row
                for part in name.split("__"):
                    actual = getattr(actual, part)
                actual = getattr(actual, "pk", actual)
                expected = getattr(expected, "pk", expected)
                if actual != expected and str(actual) != str(expected):
                    return False
            return True

        return ColumnLookup([row for row in self.rows if matches(row)], self.filters)

    def get(self):
        if len(self.rows) != 1:
            raise Column.DoesNotExist
        return self.rows[0]

    def values_list(self, field, *, flat):
        assert field == "dataset_id" and flat is True
        return SimpleNamespace(
            first=lambda: self.rows[0].dataset_id if self.rows else None
        )


@pytest.fixture
def lane(monkeypatch):
    workspace = Workspace(id=WORKSPACE, organization_id=ORG, is_default=False)
    dataset = Dataset(
        id=DATASET, workspace=workspace, organization_id=ORG, deleted=False
    )
    column = Column(id=COLUMN, dataset=dataset, data_type="text", deleted=False)
    filters = []
    monkeypatch.setattr(Column, "objects", ColumnLookup([column], filters))
    pg_read = Mock(side_effect=lambda deadline, callback: callback())
    monkeypatch.setattr(dashboard, "_run_filter_value_pg_read", pg_read)
    analytics = Mock()
    analytics.execute_ch_query.return_value = SimpleNamespace(data=[{"val": "alpha"}])
    factory = Mock(return_value=analytics)
    monkeypatch.setattr(dashboard, "AnalyticsQueryService", factory)
    monkeypatch.setattr(dashboard, "is_clickhouse_enabled", lambda: True)
    deadline = Mock()
    deadline.remaining_ms.return_value = 321
    monkeypatch.setattr(dashboard.ReadDeadline, "start", Mock(return_value=deadline))
    request = SimpleNamespace(
        workspace=workspace,
        organization=SimpleNamespace(pk=ORG),
        user=SimpleNamespace(pk=FOREIGN),
        auth=None,
    )
    return SimpleNamespace(
        request=request,
        workspace=workspace,
        dataset=dataset,
        column=column,
        filters=filters,
        pg_read=pg_read,
        analytics=analytics,
        factory=factory,
        deadline=deadline,
        view=dashboard.DashboardViewSet(),
    )


def column_page(lane, **query):
    return lane.view._filter_values_dataset_column(
        lane.request,
        str(DATASET),
        str(COLUMN),
        query_params={"page_size": 1, **query},
        deadline=lane.deadline,
    )


def compact(sql):
    return " ".join(sql.split())


@pytest.mark.parametrize("dataset_scoped", [False, True])
@pytest.mark.parametrize("alias", ["c", "r", "d"])
@pytest.mark.parametrize("tombstone", ["deleted", "_peerdb_is_deleted"])
def test_every_cell_and_parent_tombstone_is_excluded(dataset_scoped, alias, tombstone):
    sql = compact(native.dataset_cell_visibility_sql(dataset_scoped=dataset_scoped))
    # Each positive deletion flag is rejected by an unconditional conjunction,
    # not an OR/default/coalesce that could revive a soft-deleted parent.
    assert re.search(rf"(?:^|AND |WHERE ){alias}\.{tombstone} = 0(?: |$)", sql)
    assert " OR " not in sql
    assert "FROM model_hub_row AS r FINAL" in sql
    assert "FROM model_hub_dataset AS d FINAL" in sql


def test_visibility_fields_exist_in_authoritative_cdc_schema():
    for definition in (
        schema.CDC_MODEL_HUB_CELL,
        schema.CDC_MODEL_HUB_ROW,
        schema.CDC_MODEL_HUB_DATASET,
    ):
        assert "deleted UInt8 DEFAULT 0" in definition
        assert "_peerdb_is_deleted Int8 DEFAULT 0" in definition
        assert "_peerdb_version" in definition
    assert "row_id UUID" in schema.CDC_MODEL_HUB_CELL
    assert "dataset_id UUID" in schema.CDC_MODEL_HUB_ROW
    assert "workspace_id UUID" in schema.CDC_MODEL_HUB_DATASET


def test_row_membership_is_dataset_bound_and_scans_keep_tenant_scope():
    scoped = compact(native.dataset_cell_visibility_sql(dataset_scoped=True))
    assert "(c.dataset_id, c.row_id) IN ( SELECT r.dataset_id, r.id" in scoped
    assert "r.dataset_id IN ( SELECT d.id" in scoped
    assert "d.workspace_id = toUUID(%(workspace_id)s)" in scoped
    assert "r.dataset_id = toUUID(%(dataset_id)s)" in scoped
    assert "d.id = toUUID(%(dataset_id)s)" in scoped
    assert "%(dataset_id)s" not in native.dataset_cell_visibility_sql()


def test_real_orm_queryset_compiles_live_dataset_and_exact_tenant_without_db():
    workspace = Workspace(id=WORKSPACE, organization_id=ORG)
    query = native.live_dataset_column_queryset(
        workspace=workspace,
        dataset_id=DATASET,
        column_id=COLUMN,
    )
    sql, params = query.query.sql_with_params()
    assert 'NOT "model_hub_dataset"."deleted"' in sql
    assert 'NOT "model_hub_column"."deleted"' in sql
    assert '"model_hub_dataset"."workspace_id" = %s' in sql
    assert '"model_hub_dataset"."organization_id" = %s' in sql
    assert '"model_hub_column"."dataset_id" = %s' in sql
    assert '"model_hub_column"."id" = %s' in sql
    assert {ORG, WORKSPACE, DATASET, COLUMN}.issubset(set(params))


@pytest.mark.parametrize(
    "hidden",
    [
        "dataset_deleted",
        "column_deleted",
        "workspace",
        "organization",
        "dataset_id",
        "column_id",
    ],
)
@pytest.mark.parametrize("source", ["datasets", "dataset_column"])
def test_explicit_dataset_id_never_reads_hidden_or_foreign_column(lane, hidden, source):
    if hidden == "dataset_deleted":
        lane.dataset.deleted = True
    elif hidden == "column_deleted":
        lane.column.deleted = True
    elif hidden == "workspace":
        lane.dataset.workspace = Workspace(id=FOREIGN, organization_id=ORG)
    elif hidden == "organization":
        lane.dataset.organization_id = FOREIGN
    elif hidden == "dataset_id":
        lane.column.dataset_id = FOREIGN
    else:
        lane.column.id = FOREIGN
    lane.request.validated_query_data = {
        "metric_name": str(COLUMN),
        "metric_type": "custom_column",
        "source": source,
        "dataset_id": str(DATASET),
        "project_ids": [],
        "page_size": 1,
        "property_id": f"dataset_column:{COLUMN}",
    }
    response = inspect.unwrap(dashboard.DashboardViewSet.filter_values)(
        lane.view, lane.request
    )
    assert response.status_code == 200
    assert response.data["result"] == {"values": []}
    lane.factory.assert_not_called()


@pytest.mark.parametrize("explicit", [False, True])
def test_catalog_dataset_column_routes_to_same_native_visibility_contract(
    lane, explicit
):
    lane.request.validated_query_data = {
        "property_id": f"dataset_column:{COLUMN}",
        "metric_name": str(COLUMN),
        "metric_type": "custom_column",
        "source": "datasets",
        "project_ids": [],
        "page_size": 1,
        **({"dataset_id": str(DATASET)} if explicit else {}),
    }
    response = inspect.unwrap(dashboard.DashboardViewSet.filter_values)(
        lane.view, lane.request
    )
    assert response.status_code == 200
    assert response.data["result"]["values"] == [{"value": "alpha", "label": "alpha"}]
    sql, params = lane.analytics.execute_ch_query.call_args.args
    assert native.dataset_cell_visibility_sql(dataset_scoped=True) in sql
    assert params["workspace_id"] == str(WORKSPACE)
    assert params["dataset_id"] == str(DATASET)
    assert params["column_id"] == str(COLUMN)


def test_column_read_preserves_bound_parameters_deadline_and_exact_page(lane):
    needle = "x' OR 1=1 --"
    response = column_page(lane, search=needle)
    assert response.status_code == 200
    result = response.data["result"]
    assert result["query_complete"] is True and result["query_status"] == "complete"
    assert result["has_more"] is False and result["next_cursor"] is None
    sql, params = lane.analytics.execute_ch_query.call_args.args
    assert "FROM model_hub_cell AS c FINAL" in sql
    assert needle not in sql and params["search"] == needle
    assert params["result_limit"] == 5001
    options = lane.analytics.execute_ch_query.call_args.kwargs
    assert options["timeout_ms"] == 321
    assert options["settings"]["max_result_rows"] == 5001
    assert options["settings"]["result_overflow_mode"] == "throw"
    assert lane.filters[-1]["dataset__deleted"] is False
    assert lane.filters[-1]["dataset__organization_id"] == ORG


@pytest.mark.parametrize(
    "metric", ["cell_status", "column_name", "column_source", "eval_template"]
)
def test_system_cell_vocabularies_share_parent_and_cell_liveness(lane, metric):
    response = lane.view._filter_values_dataset(
        lane.request,
        metric,
        "system_metric",
        query_params={"page_size": 1},
        deadline=lane.deadline,
    )
    assert response.status_code == 200
    sql, params = lane.analytics.execute_ch_query.call_args.args
    assert native.dataset_cell_visibility_sql() in sql
    assert "FROM model_hub_cell AS c FINAL" in sql
    assert params["workspace_id"] == str(WORKSPACE)


def test_dataset_names_remain_live_workspace_scoped_without_requiring_cells(lane):
    response = lane.view._filter_values_dataset(
        lane.request,
        "dataset",
        "system_metric",
        query_params={"page_size": 1},
        deadline=lane.deadline,
    )
    assert response.status_code == 200
    sql = compact(lane.analytics.execute_ch_query.call_args.args[0])
    assert "FROM model_hub_dataset FINAL" in sql
    assert "WHERE _peerdb_is_deleted = 0 AND deleted = 0" in sql
    assert "workspace_id = toUUID(%(workspace_id)s)" in sql
    assert "model_hub_cell" not in sql and "model_hub_row" not in sql


def test_authorization_timeout_and_disabled_ch_fail_closed(lane, monkeypatch):
    lane.pg_read.side_effect = ReadDeadlineExceeded("scope deadline")
    assert column_page(lane).status_code == 503
    lane.factory.assert_not_called()
    lane.pg_read.side_effect = lambda deadline, callback: callback()
    monkeypatch.setattr(dashboard, "is_clickhouse_enabled", lambda: False)
    assert column_page(lane).status_code == 503
    lane.factory.assert_not_called()


@pytest.mark.parametrize(
    "error,status",
    [
        (ServerException("budget", 159), 503),
        (ServerException("missing CDC column", 47), 500),
        (RuntimeError("broken query"), 500),
    ],
)
def test_native_read_errors_are_not_exact_empty_or_retried(lane, error, status):
    lane.analytics.execute_ch_query.side_effect = error
    response = column_page(lane)
    assert response.status_code == status
    assert response.data.get("result") != {"values": []}
    assert lane.analytics.execute_ch_query.call_count == 1


def test_signed_native_cursor_rejects_vocabulary_drift(lane):
    lane.analytics.execute_ch_query.return_value = SimpleNamespace(
        data=[{"val": "alpha"}, {"val": "beta"}]
    )
    first = column_page(lane)
    assert first.status_code == 200
    cursor = first.data["result"]["next_cursor"]
    assert cursor
    unchanged = column_page(lane, cursor=cursor)
    assert unchanged.data["result"]["values"] == [{"value": "beta", "label": "beta"}]
    lane.analytics.execute_ch_query.return_value = SimpleNamespace(
        data=[{"val": "alpha"}]
    )
    assert column_page(lane, cursor=cursor).status_code == 400
