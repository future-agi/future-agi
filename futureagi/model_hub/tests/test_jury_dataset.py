"""Dataset jury bindings, immutable definitions and stored run reports."""

import json
from unittest.mock import patch

import pytest

from model_hub.models.choices import OwnerChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplateVersion, UserEvalMetric
from model_hub.models.evaluation import Evaluation
from model_hub.serializers.composite_jury import JuryReportSerializer
from model_hub.tasks.composite_runner import CompositeEvaluationRunner
from model_hub.tests.test_composite_jury import definition, report_url, save

pytest_plugins = ["model_hub.tests.test_composite_jury"]


@pytest.fixture
def bound(jury, user):
    parent, child, client = jury
    body = definition(child)
    body["seats"][0].update(level="step", step_kind="llm", reduce="all_pass")
    body["seats"].append(
        {"id": "safe", "kind": "check", "check_key": "safe", "weight": 1}
    )
    save(client, parent, body)
    parent.refresh_from_db()
    dataset = Dataset.objects.create(
        name="jury-rows", organization=parent.organization, workspace=parent.workspace
    )
    columns = {
        key: Column.objects.create(
            name=key, dataset=dataset, data_type="text", source="OTHERS"
        )
        for key in ("output", "steps", "checks")
    }
    for order, passed in enumerate((True, False)):
        row = Row.objects.create(dataset=dataset, order=order)
        values = {
            "output": f"row-{order}",
            "steps": json.dumps(
                [{"kind": "llm", "mapping": {"output": f"step-{order}"}}]
            ),
            "checks": json.dumps([{"key": "safe", "passed": passed}]),
        }
        for key, value in values.items():
            Cell.objects.create(
                dataset=dataset,
                row=row,
                column=columns[key],
                value=value,
                status="pass",
            )
    mapping = {key: str(col.id) for key, col in columns.items()}
    metric = UserEvalMetric.objects.create(
        name="jury-binding",
        template=parent,
        dataset=dataset,
        user=user,
        organization=parent.organization,
        workspace=parent.workspace,
        config={"mapping": mapping},
    )
    return parent, child, client, dataset, metric, mapping


@pytest.mark.django_db
def test_jury_save_creates_immutable_composite_version(jury):
    parent, child, client = jury
    save(client, parent, definition(child))
    first = EvalTemplateVersion.objects.get_default(parent)
    assert first is not None
    assert first.config_snapshot["jury"]["seats"][0]["level"] == "row"
    assert first.config_snapshot["children"][0]["child_id"] == str(child.id)
    save(client, parent, definition(child, level="step", step_kind="llm", reduce="min"))
    first.refresh_from_db()
    assert first.config_snapshot["jury"]["seats"][0]["level"] == "row"
    assert EvalTemplateVersion.objects.get_default(parent).id != first.id


@pytest.mark.django_db
def test_dataset_jury_resolves_json_and_groups_rows(bound):
    parent, child, client, dataset, metric, mapping = bound
    with patch(
        "model_hub.views.utils.evals.run_eval_func",
        return_value={"output": 1, "reason": "row judge", "output_type": "score"},
    ) as judge:
        runner = CompositeEvaluationRunner(metric.id)
        runner.run_prompt()
    assert [call.args[1]["output"] for call in judge.call_args_list] == [
        "step-0",
        "step-1",
    ]
    report = client.get(report_url(parent), {"dataset_id": str(dataset.id)})
    assert report.status_code == 200, report.content
    body = report.json()["result"]
    assert body["n"] == 2
    assert body["pairs"][0]["agreement"] == 0.5
    assert body["pairs"][0]["n"] == 2
    assert len(body["split_rows"]) == 1
    assert body["dataset_id"] == str(dataset.id)
    assert [row["aggregate_score"] for row in body["rows"]] == [1, 0.5]
    assert (
        Evaluation.objects.filter(
            organization=parent.organization, eval_config__jury_run_id=body["run_id"]
        ).count()
        == 2
    )
    with patch("model_hub.views.utils.evals.run_eval_func") as judge:
        assert (
            client.get(report_url(parent), {"run_id": body["run_id"]}).json()["result"][
                "pairs"
            ]
            == body["pairs"]
        )
        judge.assert_not_called()


@pytest.mark.django_db
def test_pinned_jury_ignores_later_live_settings(bound):
    parent, child, client, dataset, metric, mapping = bound
    version = EvalTemplateVersion.objects.get_default(parent)
    assert version is not None
    metric.pinned_version = version
    metric.save(update_fields=["pinned_version"])
    save(client, parent, definition(child))
    with patch(
        "model_hub.views.utils.evals.run_eval_func",
        return_value={"output": 1, "reason": "pinned", "output_type": "score"},
    ) as judge:
        CompositeEvaluationRunner(metric.id).run_prompt()
    assert [call.args[1]["output"] for call in judge.call_args_list] == [
        "step-0",
        "step-1",
    ]
    body = client.get(report_url(parent), {"dataset_id": str(dataset.id)}).json()[
        "result"
    ]
    assert body["version_id"] == str(version.id)
    assert len(body["children"]) == 2


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["steps", "checks"])
def test_missing_jury_binding_is_rejected_before_writes(bound, field):
    parent, child, client, dataset, metric, mapping = bound
    del mapping[field]
    count = UserEvalMetric.objects.count()
    response = client.post(
        f"/model-hub/develops/{dataset.id}/add_user_eval/",
        {
            "name": "invalid-jury",
            "template_id": str(parent.id),
            "run": False,
            "config": {"mapping": mapping},
        },
        format="json",
    )
    assert response.status_code == 400, response.content
    assert field in str(response.json())
    assert UserEvalMetric.objects.count() == count


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["system", "legacy"])
def test_unsupported_jury_parent_refused_before_dataset_write(bound, kind):
    parent, child, client, dataset, metric, mapping = bound
    if kind == "system":
        parent.owner = OwnerChoices.SYSTEM.value
    else:
        parent.workspace = None
    parent.save()
    count = UserEvalMetric.objects.count()
    response = client.post(
        f"/model-hub/develops/{dataset.id}/add_user_eval/",
        {
            "name": "unsupported-jury",
            "template_id": str(parent.id),
            "run": False,
            "config": {"mapping": mapping},
        },
        format="json",
    )
    assert response.status_code in (400, 404), response.content
    assert UserEvalMetric.objects.count() == count


def test_report_representation_keeps_each_report_shape():
    """A run report renders as before; only a dataset report has its ids."""
    run_report = {"runs": [], "run_id": None, "seats": []}
    assert JuryReportSerializer(run_report).data == {
        **run_report,
        "aggregate_score": None,
        "aggregate_pass": None,
    }
    dataset_report = {
        "runs": [],
        "run_id": "6f0f9a52-4f3b-4c55-9f8e-2a8d0b1f7c11",
        "kind": "dataset",
        "dataset_id": "0b6c3d9e-1a2f-4e5d-8c7b-6a5f4e3d2c1b",
        "experiment_id": None,
        "version_id": None,
        "n": 0,
        "complete": True,
        "rows": [],
        "children": [],
        "pairs": [],
        "split_rows": [],
    }
    assert JuryReportSerializer(dataset_report).data == {
        **dataset_report,
        "aggregate_score": None,
        "aggregate_pass": None,
    }
