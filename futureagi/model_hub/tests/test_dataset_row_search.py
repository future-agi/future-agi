"""Row detail must use the same searched navigation set as the Data grid."""

from contextlib import nullcontext
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from rest_framework.response import Response

from model_hub.serializers.contracts import DatasetRowDataRequestSerializer
from model_hub.views import develop_dataset as views

ROW = UUID("00000000-0000-4000-8000-000000000001")
NONMATCH = UUID("00000000-0000-4000-8000-000000000002")
NEXT_MATCH = UUID("00000000-0000-4000-8000-000000000003")
COLUMN = UUID("00000000-0000-4000-8000-000000000004")
CELL = UUID("00000000-0000-4000-8000-000000000005")
DATASET = UUID("00000000-0000-4000-8000-000000000006")
SEARCH = {"key": "Needle", "type": ["text", "image", "audio"]}


@pytest.mark.parametrize("search", [SEARCH, {"key": "Needle"}, {}])
def test_row_contract_accepts_grid_search(search):
    serializer = DatasetRowDataRequestSerializer(
        data={"row_id": str(ROW), "filters": [], "sort": [], "search": search}
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["search"] == search


@pytest.mark.parametrize("search", [[], "needle", 42])
def test_row_contract_rejects_non_object_search(search):
    serializer = DatasetRowDataRequestSerializer(
        data={"row_id": str(ROW), "search": search}
    )
    assert not serializer.is_valid()
    assert "search" in serializer.errors


def test_row_contract_defaults_search_without_weakening_unknown_field_rejection():
    serializer = DatasetRowDataRequestSerializer(data={"row_id": str(ROW)})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data.get("search", {}) == {}
    unknown = DatasetRowDataRequestSerializer(
        data={"row_id": str(ROW), "not_a_row_field": True}
    )
    assert not unknown.is_valid()
    assert "not_a_row_field" in unknown.errors


@pytest.fixture
def row_read(monkeypatch):
    """Mock only persistence/services; execute the view and shared search helper."""
    dataset = SimpleNamespace(id=DATASET, source="build")
    column = SimpleNamespace(id=COLUMN)
    row = SimpleNamespace(id=ROW, order=0, _dataset_sort_value="needle")
    cell = SimpleNamespace(
        id=CELL, column_id=COLUMN, value="Needle in a populated cell",
        value_infos={}, feedback_info={}, status="completed",
    )
    events = []
    rows = MagicMock(name="all_rows")
    searched = MagicMock(name="searched_rows")
    columns = MagicMock(name="columns")
    cells = MagicMock(name="scoped_cells")
    state = SimpleNamespace(outside=False)

    rows.order_by.return_value = rows
    columns.exclude.return_value = columns
    columns.order_by.return_value.__getitem__.return_value = [column]
    dataset_query = MagicMock()
    dataset_query.filter.return_value.first.return_value = dataset
    column_query = MagicMock()
    column_query.filter.return_value = columns
    cell_manager = MagicMock()
    cell_manager.filter.return_value = cells

    def filter_rows(source, *args, **kwargs):
        result = MagicMock()
        if "id__in" in kwargs:
            events.append("search")
            assert source is rows
            assert set(kwargs["id__in"]) == {ROW, NEXT_MATCH}
            assert kwargs["dataset"] == DATASET
            return searched
        if "id" in kwargs:
            events.append("current")
            result.first.return_value = (
                None if source is searched and state.outside else row
            )
        else:
            events.append("next")
            result.order_by.return_value = result
            result.values_list.return_value = (
                [NEXT_MATCH] if source is searched else [NONMATCH, NEXT_MATCH]
            )
        return result

    rows.filter.side_effect = lambda *a, **kw: filter_rows(rows, *a, **kw)
    searched.filter.side_effect = lambda *a, **kw: filter_rows(searched, *a, **kw)

    def filter_cells(**kwargs):
        if set(kwargs) == {"column_id__in"}:
            assert list(kwargs["column_id__in"]) == [str(COLUMN)]
            return cells
        result = MagicMock()
        if "id__in" in kwargs:
            assert set(kwargs["id__in"]) == {CELL}
            assert kwargs["dataset"] == DATASET
            result.values.return_value.values_list.return_value = [ROW, NEXT_MATCH]
        else:
            result.select_related.return_value = [cell]
        return result

    cells.filter.side_effect = filter_cells
    monkeypatch.setattr(views, "_request_dataset_queryset", lambda request: dataset_query)
    monkeypatch.setattr(views, "_request_row_queryset", lambda request, ds: rows)
    monkeypatch.setattr(views, "_request_column_queryset", lambda request: column_query)
    monkeypatch.setattr(views.Cell, "no_workspace_objects", cell_manager)
    monkeypatch.setattr(views, "connection", SimpleNamespace(vendor="sqlite"))
    monkeypatch.setattr(views, "transaction", SimpleNamespace(atomic=nullcontext))
    monkeypatch.setattr(views, "BoundedDatasetReadDeadline", MagicMock())
    monkeypatch.setattr(views, "assert_bounded_projection", lambda **kwargs: None)
    monkeypatch.setattr(views, "assert_dataset_table_response_within_limits", lambda data: None)
    monkeypatch.setattr(views, "validate_read_columns", lambda *args: None)
    search_sql = MagicMock(return_value=[(CELL, True, [[0, 5]])])
    monkeypatch.setattr(views.SQLQueryHandler, "search_cells_by_text", search_sql)

    def apply_filters(self, cells, selected, filters, errors, columns):
        events.append("filters")
        return selected

    def apply_sort(self, cells, selected, sort, errors, columns):
        events.append("sort")
        return selected

    monkeypatch.setattr(views.GetDatasetTableView, "_apply_filters", apply_filters)
    monkeypatch.setattr(views.GetDatasetTableView, "_apply_sorting", apply_sort)
    from model_hub.services import row_sources
    monkeypatch.setattr(row_sources, "attach_row_sources", lambda *args, **kwargs: None)
    view = views.GetRowDataView()
    gm = MagicMock()
    gm.success_response.side_effect = lambda data: Response({"result": data})
    gm.not_found.side_effect = lambda message: Response({"detail": message}, status=404)
    monkeypatch.setattr(view, "_gm", gm)

    def read(**nav):
        # Serializer coverage above is independent of navigation coverage here:
        # accepting search but failing to apply it must also stay RED.
        request = SimpleNamespace(validated_data={"row_id": ROW, **nav})
        return unwrap(type(view).post)(view, request, DATASET)

    return SimpleNamespace(
        read=read, state=state, events=events, search_sql=search_sql, gm=gm
    )


@pytest.mark.parametrize("direction", [None, "ascending", "descending"])
def test_search_narrows_current_and_next_before_navigation(row_read, direction):
    sort = [] if direction is None else [{"column_id": str(COLUMN), "type": direction}]
    response = row_read.read(search=SEARCH, sort=sort)
    assert response.status_code == 200
    current = response.data["result"]["current"]
    assert current["row_id"] == str(ROW)
    assert current[str(COLUMN)]["cell_value"] == "Needle in a populated cell"
    assert response.data["result"]["next"]["row_id"] == [NEXT_MATCH]
    row_read.search_sql.assert_called_once_with("needle", DATASET)
    assert row_read.events == (
        ["search", "sort", "current", "next"] if sort
        else ["search", "current", "next"]
    )


def test_search_composes_with_filters_before_sort(row_read):
    response = row_read.read(
        search=SEARCH, filters=[{"column_id": str(COLUMN)}],
        sort=[{"column_id": str(COLUMN), "type": "ascending"}],
    )
    assert response.status_code == 200
    assert row_read.events == ["filters", "search", "sort", "current", "next"]


def test_row_outside_search_is_404_but_unfiltered_fallback_still_reads_it(row_read):
    row_read.state.outside = True
    narrowed = row_read.read(search=SEARCH)
    assert narrowed.status_code == 404
    assert "next" not in row_read.events
    row_read.events.clear()
    alone = row_read.read(filters=[], sort=[])
    assert alone.status_code == 200
    assert alone.data["result"]["current"]["row_id"] == str(ROW)
    assert row_read.events == ["current", "next"]


def test_unsearched_row_keeps_existing_navigation(row_read):
    response = row_read.read()
    assert response.status_code == 200
    assert response.data["result"]["next"]["row_id"] == [NONMATCH, NEXT_MATCH]
    row_read.search_sql.assert_not_called()
    assert row_read.events == ["current", "next"]
