"""Dataset comparison consumers must authorize the whole selection before use."""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.tests.test_dataset_compare_api import (
    _another_organization,
    _assert_compare_not_found,
    _compare_dataset,
)
from model_hub.views import develop_dataset

pytestmark = pytest.mark.django_db


def _state(dataset):
    return (
        Dataset.no_workspace_objects.get(pk=dataset.pk).column_order,
        *(
            list(
                model.no_workspace_objects.filter(dataset=dataset).order_by("pk").values()
            )
            for model in (Column, Row, Cell, UserEvalMetric)
        ),
    )


@pytest.fixture
def selection(organization, workspace, user):
    other_org, _, other_workspace = _another_organization()
    sibling = Workspace.objects.create(
        name="Separate workspace", organization=organization, created_by=user
    )

    def dataset(org, ws, name):
        return _compare_dataset(org, ws, name, [("match", name)])

    own = dataset(organization, workspace, "Base")
    peer = dataset(organization, workspace, "Peer")
    foreign = dataset(other_org, other_workspace, "Other tenant")
    outside = dataset(organization, sibling, "Outside selected workspace")
    template = EvalTemplate.objects.create(
        name="scope-test-template",
        organization=organization,
        workspace=workspace,
        config={"output": "score", "required_keys": ["input"]},
    )
    initial = [_state(ds) for ds in (foreign, outside)]
    yield SimpleNamespace(
        own=own, peer=peer, foreign=foreign, outside=outside, template=template
    )
    assert [_state(ds) for ds in (foreign, outside)] == initial


def _bad_id(selection, kind):
    return uuid.uuid4() if kind == "unknown" else getattr(selection, kind).pk


def _stats(client, base, other):
    return client.post(
        f"/model-hub/datasets/{base}/compare-stats/",
        {"dataset_ids": [str(other)], "base_column_name": "input"},
        format="json",
    )


@pytest.mark.parametrize("kind", ["foreign", "outside", "unknown"])
@pytest.mark.parametrize("in_path", [True, False])
def test_stats_rejects_unscoped_selection(auth_client, selection, kind, in_path):
    base, other = selection.own.pk, _bad_id(selection, kind)
    if in_path:
        base, other = other, base
    _assert_compare_not_found(_stats(auth_client, base, other))


def test_stats_accepts_owned_selection(auth_client, selection):
    base, other = selection.own.pk, selection.peer.pk
    response = _stats(auth_client, base, other)
    assert response.status_code == status.HTTP_200_OK, response.content
    assert set(response.data["result"]) == {str(base), str(other)}


def _add_eval(client, selection, base, other, run, *, save_as_template=False):
    return client.post(
        f"/model-hub/datasets/{base}/compare-datasets/add-eval/",
        {
            "name": "compare-score",
            "dataset_ids": [str(other)],
            "run": run,
            "save_as_template": save_as_template,
            "template_id": str(selection.template.pk),
            "config": {"mapping": {"input": "input"}, "reason_column": False},
        },
        format="json",
    )


@pytest.mark.parametrize("kind", ["foreign", "outside", "unknown"])
@pytest.mark.parametrize("in_path", [True, False])
@pytest.mark.parametrize("run", [True, False])
def test_add_eval_rejects_entire_mixed_selection(
    auth_client, selection, kind, in_path, run
):
    initial = _state(selection.own)
    base, other = selection.own.pk, _bad_id(selection, kind)
    if in_path:
        base, other = other, base
    response = _add_eval(
        auth_client, selection, base, other, run, save_as_template=True
    )
    _assert_compare_not_found(response)
    assert _state(selection.own) == initial
    assert not EvalTemplate.no_workspace_objects.filter(name="compare-score").exists()


@pytest.mark.parametrize("run", [True, False])
def test_add_eval_accepts_owned_selection(auth_client, selection, run):
    response = _add_eval(
        auth_client, selection, selection.own.pk, selection.peer.pk, run
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    for ds in (selection.own, selection.peer):
        metric = UserEvalMetric.no_workspace_objects.get(
            dataset=ds, name="compare-score"
        )
        input_column = Column.objects.get(dataset=ds, name="input")
        assert metric.config["mapping"]["input"] == str(input_column.pk)
        columns = Column.objects.filter(dataset=ds, source_id=str(metric.pk))
        if run:
            ds.refresh_from_db()
            assert str(columns.get().pk) in ds.column_order
        else:
            assert not columns.exists()


def _preview(client, selection, dataset_ids, dataset_info):
    return client.post(
        "/model-hub/datasets/compare/preview-run-eval/",
        {
            "template_id": str(selection.template.pk),
            "dataset_ids": dataset_ids,
            "dataset_info": dataset_info,
            "config": {"mapping": {"input": "input"}, "config": {}},
        },
        format="json",
    )


def _row(dataset):
    return str(Row.no_workspace_objects.get(dataset=dataset).pk)


@pytest.mark.parametrize("kind", ["foreign", "outside", "unknown"])
@pytest.mark.parametrize("source", ["dataset", "row", "later_row", "unlisted_dataset"])
def test_preview_rejects_unscoped_ids_before_evaluation(
    auth_client, selection, monkeypatch, kind, source
):
    own = str(selection.own.pk)
    dataset_ids = [own]
    info = {"first": {own: _row(selection.own)}}
    other = str(_bad_id(selection, kind))
    row = str(uuid.uuid4()) if kind == "unknown" else _row(getattr(selection, kind))
    if source == "dataset":
        dataset_ids.append(other)
    elif source == "unlisted_dataset":
        info["first"][other] = row
    else:
        info["first" if source == "row" else "second"] = {own: row}
    runner = Mock()
    monkeypatch.setattr(develop_dataset, "EvaluationRunner", runner)
    _assert_compare_not_found(_preview(auth_client, selection, dataset_ids, info))
    runner.assert_not_called()


def test_preview_checks_row_dataset_pair_even_when_both_are_visible(
    auth_client, selection, monkeypatch
):
    runner = Mock()
    monkeypatch.setattr(develop_dataset, "EvaluationRunner", runner)
    own, peer = str(selection.own.pk), str(selection.peer.pk)
    wrong_pair = {"first": {own: _row(selection.peer)}}
    _assert_compare_not_found(_preview(auth_client, selection, [own, peer], wrong_pair))
    runner.assert_not_called()


def test_preview_evaluates_only_the_owned_first_group(
    auth_client, selection, monkeypatch
):
    own, peer = str(selection.own.pk), str(selection.peer.pk)
    first = {own: _row(selection.own), peer: _row(selection.peer)}
    second = {peer: str(Row.objects.create(dataset=selection.peer, order=1).pk)}
    evaluate = Mock(side_effect=lambda *args: {"row_id": str(args[2].pk)})
    monkeypatch.setattr(develop_dataset, "EvaluationRunner", Mock())
    monkeypatch.setattr(develop_dataset, "process_eval_for_single_row", evaluate)
    response = _preview(
        auth_client, selection, [own, peer], {"first": first, "second": second}
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    assert sorted(item["row_id"] for item in response.data["result"]["responses"]) == (
        sorted(first.values())
    )
    assert evaluate.call_count == 2
