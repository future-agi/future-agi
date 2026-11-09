"""Bound atomic transactions before allocating row/cell model instances."""

from types import SimpleNamespace

import pandas as pd
import pytest

from model_hub.services import file_import_limits as limits
from model_hub.views.datasets.create.file_upload import normalize_cell_value


@pytest.mark.parametrize("shape", [(100_001, 1), (1, 1_001), (100_000, 11)])
def test_transaction_shape_limits(shape):
    data = SimpleNamespace(shape=shape, columns=SimpleNamespace(is_unique=True))
    with pytest.raises(ValueError, match="Split the file"):
        limits.check_import_shape(data)


def test_boundary_shape_is_accepted():
    limits.check_import_shape(
        SimpleNamespace(shape=(100_000, 10), columns=SimpleNamespace(is_unique=True))
    )


def test_normalized_payload_bound_counts_utf8_bytes(monkeypatch):
    monkeypatch.setattr(limits, "MAX_IMPORT_VALUE_BYTES", 5)
    data = pd.DataFrame({"input": ["日本"]})
    with pytest.raises(ValueError, match="Split the file"):
        limits.prepare_import_values(
            data,
            [SimpleNamespace(name="input", data_type="text")],
            set(),
            normalize_cell_value,
        )


def test_preparation_preserves_structured_json_and_nulls():
    values = limits.prepare_import_values(
        pd.DataFrame({"input": [{"ok": True}, None]}),
        [SimpleNamespace(name="input", data_type="json")],
        set(),
        normalize_cell_value,
    )
    assert values == {"input": {0: '{"ok": true}', 1: None}}


def test_duplicate_trimmed_headers_are_rejected():
    with pytest.raises(ValueError, match="Column names must be unique"):
        limits.check_import_shape(pd.DataFrame([[1, 2]], columns=["input", "input"]))
