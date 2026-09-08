from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call
from uuid import uuid4

import pytest

from model_hub.serializers.contracts import (
    DatasetRowDataRequestSerializer,
    ExperimentDatasetTableQuerySerializer,
    ExperimentTableRowsQuerySerializer,
)
from model_hub.services import bounded_dataset_read as bounded_read
from model_hub.services.bounded_dataset_read import (
    BoundedDatasetPageDepthExceeded,
    BoundedDatasetReadDeadline,
    BoundedDatasetReadDeadlineExceeded,
    BoundedDatasetReadLimitExceeded,
    assert_bounded_page,
    assert_bounded_projection,
)
from model_hub.views import experiments as experiments_view
from model_hub.views.experiments import DatasetExperimentsView


@pytest.mark.parametrize("page_size", ["0", "-1", "101", "not-an-integer"])
def test_legacy_experiment_table_query_rejects_unbounded_page_sizes(page_size):
    serializer = ExperimentDatasetTableQuerySerializer(
        data={"page_size": page_size, "current_page_index": "0"}
    )

    assert not serializer.is_valid()
    assert "page_size" in serializer.errors


def test_experiment_table_queries_reject_unknown_and_negative_page_fields():
    legacy = ExperimentDatasetTableQuerySerializer(
        data={"page_size": "10", "currentPageIndex": "0"}
    )
    current = ExperimentTableRowsQuerySerializer(
        data={"page_size": "10", "current_page_index": "-1"}
    )

    assert not legacy.is_valid()
    assert "currentPageIndex" in legacy.errors
    assert not current.is_valid()
    assert "current_page_index" in current.errors


def test_current_experiment_rows_query_preserves_boolean_and_search_semantics():
    serializer = ExperimentTableRowsQuerySerializer(
        data={
            "page_size": "100",
            "current_page_index": "2",
            "column_config_only": "false",
            "get_diff": "true",
            "search": "  Needle  ",
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {
        "page_size": 100,
        "current_page_index": 2,
        "column_config_only": False,
        "get_diff": True,
        "search": "Needle",
    }


def test_row_data_contract_rejects_more_than_one_sort_key():
    serializer = DatasetRowDataRequestSerializer(
        data={
            "row_id": str(uuid4()),
            "sort": [
                {"column_id": str(uuid4()), "type": "ascending"},
                {"column_id": str(uuid4()), "type": "descending"},
            ],
        }
    )

    assert not serializer.is_valid()
    assert "sort" in serializer.errors


def test_bounded_page_and_projection_limits_fail_before_query_work():
    assert_bounded_page(page_size=100, current_page_index=1000)
    assert_bounded_projection(column_count=128, page_row_count=100)

    with pytest.raises(BoundedDatasetPageDepthExceeded):
        assert_bounded_page(page_size=100, current_page_index=1001)
    with pytest.raises(BoundedDatasetReadLimitExceeded):
        assert_bounded_projection(column_count=129, page_row_count=1)


def test_interactive_deadline_applies_remaining_postgres_statement_timeout(
    monkeypatch,
):
    db_cursor = MagicMock()
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = db_cursor
    fake_connection = MagicMock(vendor="postgresql")
    fake_connection.cursor.return_value = cursor_context
    monkeypatch.setattr(bounded_read, "connection", fake_connection)

    deadline = BoundedDatasetReadDeadline(expires_at=108.5)
    monotonic = iter((100.0, 102.0, 108.5))
    monkeypatch.setattr(bounded_read.time, "monotonic", lambda: next(monotonic))

    deadline.before_query()
    deadline.before_query()
    with pytest.raises(BoundedDatasetReadDeadlineExceeded):
        deadline.checkpoint()

    assert db_cursor.execute.call_args_list == [
        call("SELECT set_config('statement_timeout', %s, true)", ["8500"]),
        call("SELECT set_config('statement_timeout', %s, true)", ["6500"]),
    ]


def test_experiment_search_indices_are_page_local_and_overlap_compatible():
    metadata = DatasetExperimentsView()._search_cell_metadata("aaaa", "aa")

    assert metadata == {
        "key_exists": True,
        "indices": [[0, 1], [1, 2], [2, 3]],
    }
    assert DatasetExperimentsView()._search_cell_metadata("abcd", "zz") is None


def test_empty_bulk_base_cell_prefetch_never_falls_back_to_per_cell_queries(
    monkeypatch,
):
    cell_manager = MagicMock()
    monkeypatch.setattr(experiments_view.Cell, "objects", cell_manager)
    row_id = uuid4()
    base_column_id = uuid4()
    cell = SimpleNamespace(
        row_id=row_id,
        row=SimpleNamespace(id=row_id),
        column_id=uuid4(),
        column=SimpleNamespace(source="others"),
    )
    experiment = SimpleNamespace(column=object(), column_id=base_column_id)

    base_value, base_value_infos = DatasetExperimentsView()._get_base_cell_data(
        cell,
        SimpleNamespace(id=base_column_id),
        "group",
        experiment,
        prefetched_base_cells={},
    )

    assert base_value is None
    assert base_value_infos == {}
    cell_manager.filter.assert_not_called()


def test_experiment_table_source_uses_row_first_bounded_query_shapes():
    repository_root = Path(__file__).resolve().parents[2]
    develop_source = (
        repository_root / "model_hub/views/develop_dataset.py"
    ).read_text()
    legacy_start = develop_source.index("class GetExperimentDatasetTableView")
    legacy_end = develop_source.index("class GetColumnDetailView", legacy_start)
    legacy = develop_source[legacy_start:legacy_end]

    assert "query_serializer=ExperimentDatasetTableQuerySerializer" in legacy
    assert ".filter(Exists(live_projected_cell))" in legacy
    assert '.order_by("order", "id")' in legacy
    assert "paginated_rows = list(rows[start:end])" in legacy
    assert "row_id__in=row_ids" in legacy
    assert "column.cell_set.filter" not in legacy
    assert "all_table_data = sorted" not in legacy
    assert "calculate_column_average" not in legacy

    experiments_source = (
        repository_root / "model_hub/views/experiments.py"
    ).read_text()
    current_start = experiments_source.index("class DatasetExperimentsView")
    current_end = experiments_source.index("class GetRowDiffView", current_start)
    current = experiments_source[current_start:current_end]

    assert "query_serializer=ExperimentTableRowsQuerySerializer" in current
    assert '__cell_set"' not in current
    assert "SQLQueryHandler.search_cells_by_text" not in current
    assert "calculate_column_average" not in current
    assert '.order_by("order", "id")' in current
    assert "row_id__in=row_ids_list" in current


def test_row_data_source_scopes_dataset_and_breaks_equal_order_with_uuid():
    repository_root = Path(__file__).resolve().parents[2]
    source = (repository_root / "model_hub/views/develop_dataset.py").read_text()
    start = source.index("class GetRowDataView")
    end = source.index("class GetExperimentDatasetTableView", start)
    row_data = source[start:end]

    assert "_request_dataset_queryset(request)" in row_data
    assert "requested_column_ids" in row_data
    assert "unknown_column_ids" in row_data
    assert "Q(order=current_row.order, id__gt=current_row.id)" in row_data
    assert '.order_by("order", "id")' in row_data
