from unittest.mock import MagicMock, call

import pytest

from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.services import dataset_table_snapshot as snapshot_module
from model_hub.services.dataset_table_snapshot import (
    DATASET_TABLE_EXACT_MAX_CELLS,
    DATASET_TABLE_EXACT_MAX_COLUMNS,
    DATASET_TABLE_SERVER_WALL_SECONDS,
    DATASET_TABLE_STATEMENT_TIMEOUT_MS,
    DatasetTableCursorError,
    DatasetTableExactLimitExceeded,
    DatasetTableReadDeadline,
    DatasetTableReadDeadlineExceeded,
    DatasetTableRevision,
    DatasetTableSnapshotChanged,
    assert_dataset_table_cells_within_limits,
    assert_dataset_table_response_within_limits,
    assert_dataset_table_revision,
    assert_dataset_table_shape_within_limits,
    begin_repeatable_read_snapshot,
    capture_dataset_table_revision,
    decode_dataset_table_cursor,
    encode_dataset_table_cursor,
)


def _revision():
    return DatasetTableRevision(
        revision="a" * 64,
        active_rows=501,
    )


def test_signed_dataset_cursor_round_trip_and_scope_binding(settings):
    settings.SECRET_KEY = "dataset-cursor-test-secret"
    token = encode_dataset_table_cursor(
        dataset_id="dataset-1",
        organization_id="org-1",
        workspace_id="workspace-1",
        revision=_revision(),
        page_index=1,
        page_size=500,
        seen_rows=500,
        last_order=499,
        last_id="row-499",
    )

    decoded = decode_dataset_table_cursor(
        token,
        dataset_id="dataset-1",
        organization_id="org-1",
        workspace_id="workspace-1",
        page_index=1,
        page_size=500,
    )
    assert decoded.revision == _revision()
    assert decoded.seen_rows == 500
    assert decoded.last_order == 499
    assert decoded.last_id == "row-499"

    with pytest.raises(DatasetTableCursorError) as exc_info:
        decode_dataset_table_cursor(
            token,
            dataset_id="dataset-other",
            organization_id="org-1",
            workspace_id="workspace-1",
            page_index=1,
            page_size=500,
        )
    assert exc_info.value.code == "dataset_cursor_mismatch"


def test_revision_check_fails_closed_when_read_only_fingerprint_changed(monkeypatch):
    monkeypatch.setattr(
        snapshot_module,
        "_read_revision_state",
        lambda *_args, **_kwargs: DatasetTableRevision(
            revision="b" * 64, active_rows=501
        ),
    )

    with pytest.raises(DatasetTableSnapshotChanged) as exc_info:
        assert_dataset_table_revision(dataset_id="dataset-1", revision=_revision())
    assert exc_info.value.code == "dataset_snapshot_changed"


def test_repeatable_read_sets_server_statement_wall_before_snapshot(monkeypatch):
    db_cursor = MagicMock()
    db_cursor.fetchone.return_value = ("100:200:150",)
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = db_cursor
    fake_connection = MagicMock(vendor="postgresql")
    fake_connection.cursor.return_value = cursor_context
    monkeypatch.setattr(snapshot_module, "connection", fake_connection)
    deadline = MagicMock()

    assert begin_repeatable_read_snapshot(deadline) == "100:200:150"
    assert db_cursor.execute.call_args_list == [
        call("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"),
        call("SELECT txid_current_snapshot()::text"),
    ]
    deadline.set_statement_timeout.assert_called_once_with()
    assert DATASET_TABLE_STATEMENT_TIMEOUT_MS == 8_500
    assert DATASET_TABLE_SERVER_WALL_SECONDS <= 8.5


def test_exact_read_deadline_reduces_every_statement_to_one_monotonic_wall(
    monkeypatch,
):
    db_cursor = MagicMock()
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = db_cursor
    fake_connection = MagicMock(vendor="postgresql")
    fake_connection.cursor.return_value = cursor_context
    monkeypatch.setattr(snapshot_module, "connection", fake_connection)

    deadline = DatasetTableReadDeadline(expires_at=108.5)
    monotonic = iter((100.0, 102.0, 108.5))
    monkeypatch.setattr(snapshot_module.time, "monotonic", lambda: next(monotonic))

    deadline.set_statement_timeout()
    deadline.set_statement_timeout()
    with pytest.raises(DatasetTableReadDeadlineExceeded):
        deadline.checkpoint()

    assert db_cursor.execute.call_args_list == [
        call(
            "SELECT set_config('statement_timeout', %s, true)",
            ["8500"],
        ),
        call(
            "SELECT set_config('statement_timeout', %s, true)",
            ["6500"],
        ),
    ]


def test_revision_read_is_one_fixed_cardinality_read_only_query(monkeypatch):
    db_cursor = MagicMock()
    db_cursor.fetchone.return_value = (
        "2026-08-19T00:00:00+00:00",
        False,
        "2026-08-19T00:00:00+00:00",
        10,
        501,
        500,
        "2026-08-19T00:00:01+00:00",
        11,
        10,
        10,
        "2026-08-19T00:00:02+00:00",
        12,
        5_000,
        5_000,
        "2026-08-19T00:00:03+00:00",
        13,
    )
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = db_cursor
    fake_connection = MagicMock(vendor="postgresql")
    fake_connection.cursor.return_value = cursor_context
    monkeypatch.setattr(snapshot_module, "connection", fake_connection)

    revision = capture_dataset_table_revision(dataset_id="dataset-1", snapshot="1:2:")

    sql = db_cursor.execute.call_args.args[0]
    assert revision.active_rows == 500
    assert len(revision.revision) == 64
    assert "model_hub_dataset_table_revision" not in sql
    assert "model_hub_row" in sql
    assert "model_hub_column" in sql
    assert "model_hub_cell" in sql
    assert "txid_current()::bigint AS current_txid" in sql
    assert "current_txid - age(dataset.xmin)" in sql
    assert "current_txid - age(dataset_row.xmin)" in sql
    assert "current_txid - age(dataset_column.xmin)" in sql
    assert "current_txid - age(dataset_cell.xmin)" in sql
    assert "xmin::text" not in sql
    assert "INSERT" not in sql
    assert "UPDATE" not in sql
    assert "TRIGGER" not in sql
    assert db_cursor.execute.call_count == 1


def test_revision_read_fails_closed_when_dataset_is_missing(monkeypatch):
    db_cursor = MagicMock()
    db_cursor.fetchone.return_value = None
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = db_cursor
    fake_connection = MagicMock(vendor="postgresql")
    fake_connection.cursor.return_value = cursor_context
    monkeypatch.setattr(snapshot_module, "connection", fake_connection)

    with pytest.raises(
        snapshot_module.DatasetTableSnapshotUnavailable,
        match="revision fingerprint is unavailable",
    ):
        capture_dataset_table_revision(dataset_id="dataset-1", snapshot="1:2:")


@pytest.mark.django_db(transaction=True)
def test_revision_fingerprint_changes_for_every_dataset_table_family(
    organization,
    workspace,
):
    dataset = Dataset.objects.create(
        name="Revision fingerprint dataset",
        organization=organization,
        workspace=workspace,
    )
    column = Column.objects.create(
        name="input",
        data_type=DataTypeChoices.TEXT.value,
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.objects.create(dataset=dataset, order=1)
    cell = Cell.objects.create(
        dataset=dataset,
        row=row,
        column=column,
        value="before",
    )

    def revision():
        return capture_dataset_table_revision(
            dataset_id=str(dataset.id),
            snapshot="local-integration-snapshot",
        )

    observed = [revision()]
    Dataset.objects.filter(id=dataset.id).update(name="Updated dataset")
    observed.append(revision())
    Row.objects.filter(id=row.id).update(order=2)
    observed.append(revision())
    Column.objects.filter(id=column.id).update(name="updated_input")
    observed.append(revision())
    Cell.objects.filter(id=cell.id).update(value="after")
    observed.append(revision())

    assert len({item.revision for item in observed}) == len(observed)
    assert all(item.active_rows == 1 for item in observed)


def test_exact_shape_and_cell_preflights_fail_before_materialization(monkeypatch):
    db_cursor = MagicMock()
    db_cursor.fetchone.side_effect = [
        (0, DATASET_TABLE_EXACT_MAX_COLUMNS + 1, 0),
        (DATASET_TABLE_EXACT_MAX_CELLS + 1, 1, 1),
    ]
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = db_cursor
    fake_connection = MagicMock(vendor="postgresql")
    fake_connection.cursor.return_value = cursor_context
    monkeypatch.setattr(snapshot_module, "connection", fake_connection)
    deadline = MagicMock()

    with pytest.raises(DatasetTableExactLimitExceeded):
        assert_dataset_table_shape_within_limits(
            dataset_id="dataset-1", deadline=deadline
        )
    with pytest.raises(DatasetTableExactLimitExceeded):
        assert_dataset_table_cells_within_limits(
            row_ids=["00000000-0000-0000-0000-000000000001"],
            column_ids=["00000000-0000-0000-0000-000000000002"],
            deadline=deadline,
        )
    assert db_cursor.execute.call_count == 2


def test_exact_response_has_a_hard_serialized_byte_ceiling(monkeypatch):
    monkeypatch.setattr(snapshot_module, "DATASET_TABLE_EXACT_MAX_SERIALIZED_BYTES", 32)

    with pytest.raises(DatasetTableExactLimitExceeded):
        assert_dataset_table_response_within_limits({"table": [{"value": "x" * 64}]})
