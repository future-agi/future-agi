import pytest
from rest_framework import serializers

from tracer.serializers.filters import eval_task_filters_field

BASE = {"project_id": "11111111-1111-1111-1111-111111111111"}


class TestEvalTaskDatePreset:
    """`filters.date_preset` records which time-window preset the user chose.

    The frontend still resolves it to `date_range` at save time, so the backend
    only stores it — nothing here reads it back when building a query.
    """

    def test_accepts_a_preset_token(self):
        out = eval_task_filters_field().run_validation(
            {**BASE, "date_preset": "12m"}
        )
        assert out["date_preset"] == "12m"

    def test_accepts_filters_without_a_preset(self):
        out = eval_task_filters_field().run_validation(
            {**BASE, "date_range": ["2025-08-21T05:00:00Z", "2026-08-21T18:30:00Z"]}
        )
        assert "date_preset" not in out

    def test_still_rejects_a_genuinely_unknown_key(self):
        with pytest.raises(serializers.ValidationError):
            eval_task_filters_field().run_validation({**BASE, "not_a_real_key": "x"})
