"""Tests that column min/max/median share average's output_type normalization."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from model_hub.models.choices import CellStatus, SourceChoices
from tfc.utils.functions import _score_extrema, calculate_column_average


class FakeColumn:
    DoesNotExist = type("DoesNotExist", (Exception,), {})

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeQuerySet:
    def __init__(self, cells):
        self._cells = list(cells)

    def exists(self):
        return bool(self._cells)

    def count(self):
        return len(self._cells)

    def filter(self, **kwargs):
        def matches(cell):
            for key, value in kwargs.items():
                if "__" in key:
                    continue
                if getattr(cell, key) != value:
                    return False
            return True

        return FakeQuerySet([cell for cell in self._cells if matches(cell)])

    def order_by(self, *args):
        return self

    def values_list(self, field, flat=False):
        return [getattr(cell, field) for cell in self._cells]

    def __iter__(self):
        return iter(self._cells)


def _cell(**kwargs):
    defaults = {
        "id": "cell",
        "value": None,
        "status": CellStatus.PASS.value,
        "deleted": False,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _metric(output, extra_config=None, **template_kwargs):
    config = {"output": output}
    if extra_config:
        config.update(extra_config)
    template = SimpleNamespace(config=config, **template_kwargs)
    return SimpleNamespace(template=template)


def _run(column, metric):
    with (
        patch("tfc.utils.functions.Column", FakeColumn),
        patch("tfc.utils.functions.UserEvalMetric.objects.get", return_value=metric),
    ):
        return calculate_column_average(column)


@pytest.mark.unit
def test_score_extrema_empty_and_scale():
    assert _score_extrema([]) == {"min": None, "max": None, "median": None}
    assert _score_extrema([0.2, 0.8, 0.5], scale=100) == {
        "min": 20.0,
        "max": 80.0,
        "median": 50.0,
    }
    assert _score_extrema([1, 5, 10], scale=1) == {
        "min": 1.0,
        "max": 10.0,
        "median": 5.0,
    }


@pytest.mark.unit
def test_pass_fail_extrema_use_scale_100():
    cells = FakeQuerySet(
        [
            _cell(id="1", value="Passed"),
            _cell(id="2", value="Failed"),
            _cell(id="3", value="Passed"),
        ]
    )
    column = FakeColumn(
        id="col-pass-fail",
        name="eval",
        source=SourceChoices.EVALUATION.value,
        source_id="metric-1",
        cell_set=cells,
    )
    stats = _run(column, _metric("Pass/Fail"))
    assert stats["average"] == 66.67
    assert stats["min"] == 0.0
    assert stats["max"] == 100.0
    assert stats["median"] == 100.0


@pytest.mark.unit
def test_score_output_extrema_use_scale_100():
    cells = FakeQuerySet(
        [
            _cell(id="1", value="0.2"),
            _cell(id="2", value="0.8"),
            _cell(id="3", value="0.5"),
        ]
    )
    column = FakeColumn(
        id="col-score",
        name="eval",
        source=SourceChoices.EVALUATION.value,
        source_id="metric-1",
        cell_set=cells,
    )
    stats = _run(column, _metric("score"))
    assert stats["average"] == 50.0
    assert stats["min"] == 20.0
    assert stats["max"] == 80.0
    assert stats["median"] == 50.0


@pytest.mark.unit
def test_numeric_output_extrema_use_scale_100():
    cells = FakeQuerySet(
        [
            _cell(id="1", value="0.1"),
            _cell(id="2", value="0.9"),
        ]
    )
    column = FakeColumn(
        id="col-numeric",
        name="eval",
        source=SourceChoices.EVALUATION.value,
        source_id="metric-1",
        cell_set=cells,
    )
    stats = _run(column, _metric("numeric"))
    assert stats["average"] == 50.0
    assert stats["min"] == 10.0
    assert stats["max"] == 90.0
    assert stats["median"] == 50.0


@pytest.mark.unit
def test_reason_choices_extrema_use_scale_100():
    # Cells are prefiltered to CellStatus.PASS; this branch still scales 0/1 by 100.
    cells = FakeQuerySet(
        [
            _cell(id="1", status="pass"),
            _cell(id="2", status="pass"),
        ]
    )
    column = FakeColumn(
        id="col-reason",
        name="eval",
        source=SourceChoices.EVALUATION.value,
        source_id="metric-1",
        cell_set=cells,
    )
    stats = _run(column, _metric("reason"))
    assert stats["average"] == 100.0
    assert stats["min"] == 100.0
    assert stats["max"] == 100.0
    assert stats["median"] == 100.0


@pytest.mark.unit
def test_numeric_choices_percentage_extrema_use_scale_100():
    cells = FakeQuerySet(
        [
            _cell(id="1", value=[0.2]),
            _cell(id="2", value=[0.8]),
            _cell(id="3", value=[0.5]),
        ]
    )
    column = FakeColumn(
        id="col-choices-pct",
        name="eval",
        source=SourceChoices.EVALUATION.value,
        source_id="metric-1",
        cell_set=cells,
    )
    template_kwargs = {
        "owner": "user",
        "choices": [0.2, 0.5, 0.8],
        "multi_choice": False,
    }
    stats = _run(column, _metric("choices", **template_kwargs))
    assert stats["is_numeric_eval"] is True
    assert stats["is_numeric_eval_percentage"] is True
    assert stats["average"] == 50.0
    assert stats["min"] == 20.0
    assert stats["max"] == 80.0
    assert stats["median"] == 50.0


@pytest.mark.unit
def test_numeric_choices_non_percentage_extrema_unscaled():
    cells = FakeQuerySet(
        [
            _cell(id="1", value=[1]),
            _cell(id="2", value=[5]),
            _cell(id="3", value=[10]),
        ]
    )
    column = FakeColumn(
        id="col-choices-abs",
        name="eval",
        source=SourceChoices.EVALUATION.value,
        source_id="metric-1",
        cell_set=cells,
    )
    template_kwargs = {
        "owner": "user",
        "choices": [1, 5, 10],
        "multi_choice": False,
    }
    stats = _run(column, _metric("choices", **template_kwargs))
    assert stats["is_numeric_eval"] is True
    assert stats["is_numeric_eval_percentage"] is False
    assert stats["average"] == 5.33
    assert stats["min"] == 1.0
    assert stats["max"] == 10.0
    assert stats["median"] == 5.0
