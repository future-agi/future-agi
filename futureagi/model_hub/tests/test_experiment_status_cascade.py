"""Unit test for the optimistic-lock cascade gate (PR #331, issue #320) in
experiment_runner.check_and_update_experiment_dataset_status.

Pre-fix: in the no-columns branch the experiment-level cascade
(check_and_update_experiment_status) fired UNCONDITIONALLY, so a worker that LOST the
optimistic update (.update() -> 0, because a concurrent worker already transitioned the
dataset) still cascaded -> duplicate experiment-status work.
Post-fix: cascade fires only when this worker performed the transition
(dataset_updated > 0) or the dataset was already COMPLETED.

We drive the REAL function body and control the optimistic-update result by patching
the ExperimentDatasetTable / Column managers, spying the cascade. No DB required.
Pre-fix this asserts a cascade that DID happen -> fails; post-fix -> passes.

Run:  cd futureagi && pytest model_hub/tests/test_experiment_status_cascade.py
"""

import pytest

pytestmark = pytest.mark.unit

from unittest.mock import MagicMock, patch

from model_hub.views import experiment_runner
from model_hub.views.experiment_runner import (
    check_and_update_experiment_dataset_status,
)

NOT_COMPLETED = "in_progress"  # any status != StatusType.COMPLETED.value


def _no_columns_dataset():
    ed = MagicMock()
    ed.id = "ed-1"
    ed.status = NOT_COMPLETED
    ed.experiment.id = "exp-1"
    return ed


@patch.object(experiment_runner, "check_and_update_experiment_status")
@patch.object(experiment_runner, "Column")
@patch.object(experiment_runner, "ExperimentDatasetTable")
def test_no_cascade_when_optimistic_update_loses(mock_edt, mock_column, mock_cascade):
    mock_edt.objects.filter.return_value.first.return_value = _no_columns_dataset()
    mock_column.objects.filter.return_value.values_list.return_value = []
    # Losing worker: concurrent worker already transitioned -> update affects 0 rows.
    mock_edt.objects.filter.return_value.update.return_value = 0

    check_and_update_experiment_dataset_status("ed-1")

    mock_cascade.assert_not_called()


@patch.object(experiment_runner, "check_and_update_experiment_status")
@patch.object(experiment_runner, "Column")
@patch.object(experiment_runner, "ExperimentDatasetTable")
def test_cascade_when_optimistic_update_wins(mock_edt, mock_column, mock_cascade):
    mock_edt.objects.filter.return_value.first.return_value = _no_columns_dataset()
    mock_column.objects.filter.return_value.values_list.return_value = []
    mock_edt.objects.filter.return_value.update.return_value = 1  # we won the race

    check_and_update_experiment_dataset_status("ed-1")

    mock_cascade.assert_called_once_with("exp-1")
