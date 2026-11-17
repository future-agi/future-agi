"""A failed row-context query preserves its error and writes no partial result."""

# ruff: noqa: F811
from contextlib import ExitStack
from unittest.mock import PropertyMock, patch

import pytest
from django.db import OperationalError

from model_hub.models.develop_dataset import Cell
from model_hub.tests.test_experiment_version_params import (
    body,
    create_run,
    setup_run,
    version_world,  # noqa: F401
)
from model_hub.views import experiment_runner

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("failed_lookup", ["row", "column", "dataset", "experiment"])
def test_context_lookup_failure_preserves_error_without_writing_cells(
    auth_client, version_world, failed_lookup
):
    record = create_run(auth_client, body(version_world, {}))
    setup = setup_run(record)
    experiment = record.experiment_dataset.experiment
    cells = Cell.objects.filter(dataset_id=experiment.snapshot_dataset_id)
    before = list(cells.order_by("id").values("id", "value", "value_infos", "status"))
    failure = OperationalError("synthetic connection timeout during row setup")
    with ExitStack() as stack:
        if failed_lookup == "dataset":
            stack.enter_context(
                patch.object(
                    experiment_runner.Column,
                    "dataset",
                    new_callable=PropertyMock,
                    side_effect=failure,
                )
            )
        else:
            model = {
                "row": experiment_runner.Row,
                "column": experiment_runner.Column,
                "experiment": experiment_runner.ExperimentsTable,
            }[failed_lookup]
            stack.enter_context(patch.object(model.objects, "get", side_effect=failure))
        provider = stack.enter_context(patch.object(experiment_runner, "RunPrompt"))
        cleanup = stack.enter_context(
            patch.object(
                experiment_runner,
                "close_old_connections",
                wraps=experiment_runner.close_old_connections,
            )
        )
        with pytest.raises(OperationalError) as raised:
            experiment_runner._process_row_impl(
                row_id=setup["row_ids"][0],
                column_id=setup["column_id"],
                dataset_id=experiment.snapshot_dataset_id,
                experiment_id=experiment.id,
                messages=setup["messages"],
                model=setup["model_name"],
                model_config=setup["model_config"],
                output_format=setup["output_format"],
                run_prompt_config=setup["run_prompt_config"],
                skip_celery_evals=True,
            )
        assert raised.value is failure
        provider.assert_not_called()
        assert cleanup.call_count == 2
    assert (
        list(cells.order_by("id").values("id", "value", "value_infos", "status"))
        == before
    )
