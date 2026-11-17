from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from model_hub.models.choices import SourceChoices
from model_hub.utils import eval_reasons


@pytest.mark.parametrize(
    "eval_name",
    ["accuracy", "no-reason-check", "gives-reason", "no-reason-check-reason"],
)
@pytest.mark.parametrize("filter_by_name", [False, True])
def test_reason_columns_join_values_under_the_full_eval_name(
    monkeypatch, eval_name, filter_by_name
):
    reason_column = SimpleNamespace(
        id="reason-column",
        name=f"{eval_name}-reason",
        source_id="value-column-sourceid-metric-id",
    )
    value_column = SimpleNamespace(
        id="value-column", name=eval_name, source_id="metric-id"
    )
    reason_columns = MagicMock()
    reason_columns.__iter__.return_value = iter([reason_column])
    reason_columns.filter.return_value = [reason_column]
    value_columns = MagicMock()
    value_columns.__iter__.return_value = iter([value_column])
    value_columns.filter.return_value = [value_column]
    column_manager = MagicMock()
    column_manager.filter.side_effect = lambda **kwargs: {
        SourceChoices.EVALUATION_REASON.value: reason_columns,
        SourceChoices.EVALUATION.value: value_columns,
    }[kwargs["source"]]
    monkeypatch.setattr(eval_reasons.Column, "objects", column_manager)

    reason_cells = MagicMock()
    reason_cells.order_by.return_value.values_list.return_value = [
        "First reason",
        "",
        None,
        "Second reason",
    ]
    value_cells = MagicMock()
    value_cells.order_by.return_value.values_list.return_value = [1, 0, None, 1]
    cell_manager = MagicMock()
    cell_manager.filter.side_effect = lambda **kwargs: {
        reason_column.id: reason_cells,
        value_column.id: value_cells,
    }[kwargs["column_id"]]
    monkeypatch.setattr(eval_reasons.Cell, "objects", cell_manager)

    template = SimpleNamespace(
        id="template-id", name="Template", criteria="Evaluation criteria"
    )
    metric_manager = MagicMock()
    metric_manager.filter.return_value.select_related.return_value = [
        SimpleNamespace(id="metric-id", template=template)
    ]
    monkeypatch.setattr(eval_reasons.UserEvalMetric, "objects", metric_manager)

    result = eval_reasons.get_eval_reasons(
        dataset_id="dataset-id",
        eval_names=[eval_name] if filter_by_name else None,
    )

    assert result == {
        eval_name: {
            "eval_reasons": ["First reason", "Second reason"],
            "eval_values": [1, 0, None, 1],
            "eval_template_id": "template-id",
            "user_eval_metric_id": "metric-id",
            "eval_template_id_criteria": "Evaluation criteria",
            "eval_template_name": "Template",
        }
    }
    metric_manager.filter.assert_called_once_with(id__in=["metric-id"])
    if filter_by_name:
        reason_columns.filter.assert_called_once_with(name__in=[f"{eval_name}-reason"])
        value_columns.filter.assert_called_once_with(name__in=[eval_name])
