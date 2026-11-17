"""Pinned jury aggregation controls the persisted result column contract."""

import json
from unittest.mock import patch

import pytest

from model_hub.models.develop_dataset import Cell, Column
from model_hub.models.evals_metric import EvalTemplateVersion
from model_hub.tasks.composite_runner import CompositeEvaluationRunner
from model_hub.tests import test_jury_dataset as dataset_fixtures
from model_hub.tests.test_composite_jury import save

pytest_plugins = ["model_hub.tests.test_composite_jury"]
bound = dataset_fixtures.bound


@pytest.mark.django_db
@pytest.mark.parametrize("saved_aggregation", [True, False])
@pytest.mark.parametrize("selection", ["pin", "binding"])
def test_saved_aggregation_controls_column_and_execution(
    bound, saved_aggregation, selection
):
    parent, child, client, dataset, metric, mapping = bound
    parent.aggregation_enabled = saved_aggregation
    parent.save(update_fields=["aggregation_enabled"])
    save(client, parent, parent.config["jury"])
    version = EvalTemplateVersion.objects.get_default(parent)
    assert version.config_snapshot["aggregation_enabled"] is saved_aggregation
    if selection == "pin":
        metric.pinned_version = version
        metric.save(update_fields=["pinned_version"])
    else:
        metric.config["config"] = {"jury_version_id": str(version.id)}
        metric.save(update_fields=["config"])
    parent.aggregation_enabled = not saved_aggregation
    parent.save(update_fields=["aggregation_enabled"])
    save(client, parent, parent.config["jury"])
    metric.refresh_from_db()
    expected_type = "float" if saved_aggregation else "text"
    runner = CompositeEvaluationRunner(metric.id)
    assert runner.template.aggregation_enabled is saved_aggregation
    with patch(
        "model_hub.views.utils.evals.run_eval_func",
        return_value={"output": 1, "output_type": "score"},
    ):
        runner.run_prompt()
    column = Column.objects.get(
        dataset=dataset, source_id=str(metric.id), deleted=False
    )
    assert column.data_type == expected_type
    assert Cell.objects.filter(column=column).count() == 2
    # Experiment pre-creation uses the classmethod without constructing a runner.
    config = CompositeEvaluationRunner.build_column_config(metric, dataset)
    assert config["data_type"] == expected_type


@pytest.mark.django_db
@pytest.mark.parametrize("saved_aggregation", [True, False])
def test_precreated_result_column_resolves_the_saved_template(bound, saved_aggregation):
    parent, child, client, dataset, metric, mapping = bound
    parent.aggregation_enabled = saved_aggregation
    parent.save(update_fields=["aggregation_enabled"])
    save(client, parent, parent.config["jury"])
    metric.pinned_version = EvalTemplateVersion.objects.get_default(parent)
    metric.save(update_fields=["pinned_version"])
    parent.aggregation_enabled = not saved_aggregation
    parent.save(update_fields=["aggregation_enabled"])
    metric.refresh_from_db()
    config = CompositeEvaluationRunner.build_column_config(metric, dataset)
    column = Column.objects.create(**config)
    assert column.data_type == ("float" if saved_aggregation else "text")


@pytest.mark.django_db
@pytest.mark.parametrize("saved_aggregation", [True, False])
@pytest.mark.parametrize("action", ["start", "add_and_run"])
def test_dataset_api_precreates_one_historically_typed_column(
    bound, saved_aggregation, action
):
    from model_hub.models.evals_metric import UserEvalMetric

    parent, child, client, dataset, metric, mapping = bound
    parent.aggregation_enabled = saved_aggregation
    parent.save(update_fields=["aggregation_enabled"])
    save(client, parent, parent.config["jury"])
    version = EvalTemplateVersion.objects.get_default(parent)
    metric.pinned_version = version
    metric.save(update_fields=["pinned_version"])
    parent.aggregation_enabled = not saved_aggregation
    parent.save(update_fields=["aggregation_enabled"])
    if action == "start":
        response = client.post(
            f"/model-hub/develops/{dataset.id}/start_evals_process/",
            {"user_eval_ids": [str(metric.id)]},
            format="json",
        )
    else:
        response = client.post(
            f"/model-hub/develops/{dataset.id}/add_user_eval/",
            {
                "name": "run-pinned-jury",
                "template_id": str(parent.id),
                "run": True,
                "config": {
                    "mapping": mapping,
                    "config": {"jury_version_id": str(version.id)},
                },
            },
            format="json",
        )
        metric = UserEvalMetric.objects.get(dataset=dataset, name="run-pinned-jury")
    assert response.status_code == 200, response.content
    columns = Column.objects.filter(
        dataset=dataset, source_id=str(metric.id), deleted=False
    )
    assert list(columns.values_list("data_type", flat=True)) == [
        "float" if saved_aggregation else "text"
    ]
    original_column_id = columns.get().id
    with patch(
        "model_hub.views.utils.evals.run_eval_func",
        return_value={"output": 1, "output_type": "score"},
    ):
        CompositeEvaluationRunner(metric.id).run_prompt()
    assert list(columns.values_list("id", flat=True)) == [original_column_id]
    assert Cell.objects.filter(column_id=original_column_id).count() == 2


def _switch_pins(bound, first_aggregation):
    """Save two jury versions that differ only in aggregation and pin the first."""
    parent, child, client, dataset, metric, mapping = bound
    parent.aggregation_enabled = first_aggregation
    parent.save(update_fields=["aggregation_enabled"])
    save(client, parent, parent.config["jury"])
    first = EvalTemplateVersion.objects.get_default(parent)
    parent.aggregation_enabled = not first_aggregation
    parent.save(update_fields=["aggregation_enabled"])
    save(client, parent, parent.config["jury"])
    second = EvalTemplateVersion.objects.get_default(parent)
    assert first.config_snapshot["aggregation_enabled"] is first_aggregation
    assert second.config_snapshot["aggregation_enabled"] is (not first_aggregation)
    metric.pinned_version = first
    metric.save(update_fields=["pinned_version"])
    return second


def _run(metric_id):
    with patch(
        "model_hub.views.utils.evals.run_eval_func",
        return_value={"output": 1, "output_type": "score"},
    ):
        CompositeEvaluationRunner(metric_id).run_prompt()


def _edit_pin(client, dataset, metric, mapping, version, run):
    response = client.post(
        f"/model-hub/develops/{dataset.id}/edit_and_run_user_eval/{metric.id}/",
        {
            "name": metric.name,
            "template_id": str(metric.template_id),
            "run": run,
            "config": {
                "mapping": mapping,
                "config": {"jury_version_id": str(version.id)},
            },
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    metric.refresh_from_db()
    assert metric.pinned_version_id == version.id


def _assert_rewritten(column, aggregation, parent):
    cells = list(Cell.objects.filter(column=column, deleted=False))
    assert len(cells) == 2
    for cell in cells:
        infos = cell.value_infos
        infos = json.loads(infos) if isinstance(infos, str) else infos
        assert cell.status == "pass", (cell.status, cell.value)
        assert infos["aggregation_enabled"] is aggregation
        assert infos["composite_id"] == str(parent.id)
        if aggregation:
            float(cell.value)
        else:
            assert cell.value == (infos["summary"] or "")


@pytest.mark.django_db
@pytest.mark.parametrize("first_aggregation", [True, False])
def test_pin_switch_rerun_retypes_the_one_result_column(bound, first_aggregation):
    """Run V1, save V2 through the drawer route, Start, then run the runner."""
    parent, child, client, dataset, metric, mapping = bound
    second = _switch_pins(bound, first_aggregation)
    _run(metric.id)
    columns = Column.objects.filter(
        dataset=dataset, source_id=str(metric.id), deleted=False
    )
    original = columns.get()
    assert original.data_type == ("float" if first_aggregation else "text")
    _edit_pin(client, dataset, metric, mapping, second, run=False)
    response = client.post(
        f"/model-hub/develops/{dataset.id}/start_evals_process/",
        {"user_eval_ids": [str(metric.id)]},
        format="json",
    )
    assert response.status_code == 200, response.content
    expected = "text" if first_aggregation else "float"
    # Dataset precreation retypes the existing column before any result is written.
    assert list(columns.values_list("id", "data_type")) == [(original.id, expected)]
    stale = Cell.objects.filter(column=original, deleted=False)
    assert stale.count() == 2
    assert set(stale.values_list("value", "status")) == {("", "running")}
    _run(metric.id)
    assert list(columns.values_list("id", "data_type")) == [(original.id, expected)]
    _assert_rewritten(original, not first_aggregation, parent)


@pytest.mark.django_db
@pytest.mark.parametrize("first_aggregation", [True, False])
def test_runner_retypes_the_existing_column_without_precreation(
    bound, first_aggregation
):
    """Edit-and-run queues the switched pin directly; the runner must reconcile."""
    parent, child, client, dataset, metric, mapping = bound
    second = _switch_pins(bound, first_aggregation)
    _run(metric.id)
    columns = Column.objects.filter(
        dataset=dataset, source_id=str(metric.id), deleted=False
    )
    original = columns.get()
    _edit_pin(client, dataset, metric, mapping, second, run=True)
    _run(metric.id)
    expected = "text" if first_aggregation else "float"
    assert list(columns.values_list("id", "data_type")) == [(original.id, expected)]
    _assert_rewritten(original, not first_aggregation, parent)
    dataset.refresh_from_db()
    assert dataset.column_order.count(str(original.id)) == 1


@pytest.mark.django_db
def test_runner_prefers_an_existing_column_of_the_selected_type(bound):
    """Identity duplicates left by earlier runs: reuse the one already typed."""
    parent, child, client, dataset, metric, mapping = bound
    second = _switch_pins(bound, True)
    _run(metric.id)
    columns = Column.objects.filter(
        dataset=dataset, source_id=str(metric.id), deleted=False
    )
    original = columns.get()
    assert original.data_type == "float"
    typed = Column.objects.create(
        name=metric.name,
        data_type="text",
        source=original.source,
        source_id=original.source_id,
        dataset=dataset,
    )
    metric.pinned_version = second
    metric.save(update_fields=["pinned_version"])
    _run(metric.id)
    assert set(columns.values_list("id", "data_type")) == {
        (original.id, "float"),
        (typed.id, "text"),
    }
    _assert_rewritten(typed, False, parent)
    assert Cell.objects.filter(column=original, deleted=False).count() == 2
    assert set(
        Cell.objects.filter(column=original, deleted=False).values_list(
            "status", flat=True
        )
    ) == {"pass"}
