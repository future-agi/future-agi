"""Shared environment-backed limits for bounded dataset table reads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings as django_settings

from tfc.settings.runtime_limit_loader import load_setting_snapshot, runtime_setting
from tfc.settings.runtime_setting_specs import (
    DATASET_READ_SETTING_SPECS,
    validate_dataset_read_settings,
)


def _setting(name: str) -> Any:
    return runtime_setting(name, DATASET_READ_SETTING_SPECS)


@dataclass(frozen=True, slots=True)
class DatasetReadLimits:
    cursor_max_age_seconds: int = _setting("DATASET_TABLE_CURSOR_MAX_AGE_SECONDS")
    server_wall_seconds: float = _setting("DATASET_TABLE_SERVER_WALL_SECONDS")
    exact_max_columns: int = _setting("DATASET_TABLE_EXACT_MAX_COLUMNS")
    exact_max_cells: int = _setting("DATASET_TABLE_EXACT_MAX_CELLS")
    exact_max_cell_value_bytes: int = _setting(
        "DATASET_TABLE_EXACT_MAX_CELL_VALUE_BYTES"
    )
    exact_max_cell_variable_bytes: int = _setting(
        "DATASET_TABLE_EXACT_MAX_CELL_VARIABLE_BYTES"
    )
    exact_max_schema_bytes: int = _setting("DATASET_TABLE_EXACT_MAX_SCHEMA_BYTES")
    exact_max_serialized_bytes: int = _setting(
        "DATASET_TABLE_EXACT_MAX_SERIALIZED_BYTES"
    )
    interactive_max_page_size: int = _setting("DATASET_INTERACTIVE_MAX_PAGE_SIZE")
    interactive_max_offset_rows: int = _setting("DATASET_INTERACTIVE_MAX_OFFSET_ROWS")
    row_adjacency_max_rows: int = _setting("DATASET_ROW_ADJACENCY_MAX_ROWS")


def load_dataset_read_limits(source: Any = django_settings) -> DatasetReadLimits:
    """Build one validated, immutable runtime settings snapshot."""

    return load_setting_snapshot(
        DatasetReadLimits,
        specs=DATASET_READ_SETTING_SPECS,
        source=source,
        fallback=django_settings,
        validator=validate_dataset_read_settings,
    )


DATASET_READ_LIMITS = load_dataset_read_limits()
