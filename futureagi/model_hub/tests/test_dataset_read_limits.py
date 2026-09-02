from types import SimpleNamespace

import pytest

from model_hub.services.dataset_read_limits import load_dataset_read_limits


def test_dataset_read_limits_accept_bounded_operator_overrides():
    limits = load_dataset_read_limits(
        SimpleNamespace(
            DATASET_TABLE_SERVER_WALL_SECONDS=5.0,
            DATASET_TABLE_EXACT_MAX_COLUMNS=64,
            DATASET_INTERACTIVE_MAX_PAGE_SIZE=50,
            DATASET_ROW_ADJACENCY_MAX_ROWS=25,
        )
    )

    assert limits.server_wall_seconds == 5.0
    assert limits.exact_max_columns == 64
    assert limits.interactive_max_page_size == 50
    assert limits.row_adjacency_max_rows == 25


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("DATASET_TABLE_CURSOR_MAX_AGE_SECONDS", 59),
        ("DATASET_TABLE_SERVER_WALL_SECONDS", 31.0),
        ("DATASET_TABLE_EXACT_MAX_COLUMNS", 0),
        ("DATASET_INTERACTIVE_MAX_PAGE_SIZE", 501),
        ("DATASET_ROW_ADJACENCY_MAX_ROWS", 501),
    ),
)
def test_dataset_read_limits_reject_unsafe_overrides(name, value):
    with pytest.raises(ValueError):
        load_dataset_read_limits(SimpleNamespace(**{name: value}))


def test_dataset_read_limits_reject_inconsistent_overrides():
    with pytest.raises(ValueError, match="adjacency rows"):
        load_dataset_read_limits(
            SimpleNamespace(
                DATASET_INTERACTIVE_MAX_PAGE_SIZE=25,
                DATASET_ROW_ADJACENCY_MAX_ROWS=26,
            )
        )
