"""Regression tests for simulate call-execution default column order.

Frontend grid cell renderers match by snake_case `id` values; any regression
back to camelCase will render empty cells in the run-detail grid (TH-4231).
"""

import re

import pytest

from simulate.utils.test_execution import (
    DEFAULT_CHAT_SIM_COL,
    DEFAULT_VOICE_SIM_COL,
    LEGACY_SIM_COLUMN_ID_MAP,
)

SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")


@pytest.mark.unit
@pytest.mark.parametrize(
    "columns",
    [DEFAULT_VOICE_SIM_COL, DEFAULT_CHAT_SIM_COL],
    ids=["voice", "chat"],
)
def test_default_columns_use_snake_case_ids(columns):
    for col in columns:
        assert SNAKE_CASE.match(col["id"]), (
            f"column id {col['id']!r} must be snake_case; the frontend "
            "run-detail grid matches cell renderers by snake_case id and "
            "renders empty cells for camelCase ids."
        )


@pytest.mark.unit
@pytest.mark.parametrize("columns", [DEFAULT_VOICE_SIM_COL, DEFAULT_CHAT_SIM_COL])
def test_csat_column_is_visible_by_default(columns):
    csat = next(col for col in columns if col["id"] == "overall_score")
    assert csat["column_name"] == "CSAT"
    assert csat["visible"] is True


@pytest.mark.unit
def test_legacy_id_map_targets_known_snake_case_ids():
    known_ids = {col["id"] for col in DEFAULT_VOICE_SIM_COL}
    known_ids.update(col["id"] for col in DEFAULT_CHAT_SIM_COL)
    for legacy_id, new_id in LEGACY_SIM_COLUMN_ID_MAP.items():
        assert SNAKE_CASE.match(new_id), f"{legacy_id} -> {new_id} not snake_case"
        assert new_id in known_ids, (
            f"legacy mapping {legacy_id} -> {new_id} points at an id not "
            "present in DEFAULT_VOICE_SIM_COL or DEFAULT_CHAT_SIM_COL"
        )


@pytest.mark.unit
def test_legacy_id_map_covers_expected_legacy_ids():
    # If we reintroduce a camelCase id in the defaults, this test flags the
    # missing entry in LEGACY_SIM_COLUMN_ID_MAP.
    expected = {
        "callDetails",
        "overallScore",
        "aiInterruptionCount",
        "userInterruptionCount",
        "turnCount",
        "agentTalkPercentage",
        "serviceProviderCallId",
        "totalTokens",
        "inputTokens",
        "outputTokens",
        "avgLatencyMs",
    }
    assert expected <= set(LEGACY_SIM_COLUMN_ID_MAP.keys())
