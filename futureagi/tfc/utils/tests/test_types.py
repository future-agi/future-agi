"""
Unit tests for ClickhouseDatatypes.get_data_type.

get_data_type infers the ClickHouse column type for a Python value before it is
written to ClickHouse (see model_hub/utils/clickhouse.py). Because bool is a
subclass of int, the bool check must run before the int check; otherwise every
boolean value is classified as INTEGER and the BOOLEAN branch is dead.

Run with: futureagi/bin/test tfc/utils/tests/test_types.py -v
"""

import uuid
from datetime import datetime

import pytest

from tfc.utils.types import ClickhouseDatatypes


class TestClickhouseDatatypesGetDataType:
    """Tests for ClickhouseDatatypes.get_data_type type inference."""

    @pytest.mark.unit
    def test_bool_is_classified_as_boolean_not_integer(self):
        # Regression: bool subclasses int, so an int-first check misclassifies
        # booleans as INTEGER. Both True and False must resolve to BOOLEAN.
        assert ClickhouseDatatypes.get_data_type(True) == ClickhouseDatatypes.BOOLEAN
        assert ClickhouseDatatypes.get_data_type(False) == ClickhouseDatatypes.BOOLEAN

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("hello", ClickhouseDatatypes.STRING),
            (42, ClickhouseDatatypes.INTEGER),
            (3.14, ClickhouseDatatypes.FLOAT),
            (datetime(2024, 1, 1), ClickhouseDatatypes.DATE),
            ([1, 2, 3], ClickhouseDatatypes.LIST),
            ({"a": 1}, ClickhouseDatatypes.JSON),
            (uuid.uuid4(), ClickhouseDatatypes.UUID),
        ],
    )
    def test_other_types_are_unaffected(self, value, expected):
        assert ClickhouseDatatypes.get_data_type(value) == expected
