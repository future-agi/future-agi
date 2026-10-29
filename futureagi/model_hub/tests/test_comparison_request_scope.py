"""Comparison entry points must authorize all datasets before reads or work."""

import json
import uuid
from unittest.mock import patch

import pytest

from accounts.models.workspace import Workspace
from model_hub.models.develop_dataset import Cell, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.tests.test_dataset_cell_scope import _grid
from model_hub.tests.test_dataset_list_tenancy import tenants as tenants_fixture
from model_hub.tests.test_model_hub_related_scope import _request
from model_hub.views import develop_dataset as views

pytestmark = pytest.mark.django_db
tenants = tenants_fixture


def _target(a, b, scope):
    if scope == "organization":
        return b["dataset"], b["rows"][0]
    workspace = Workspace.objects.create(
        name="private", organization=a["org"], created_by=a["user"], is_default=False,
    )
    dataset, row, _, _ = _grid(a, workspace)
    return dataset, row


@pytest.mark.parametrize("scope", ["organization", "workspace"])
@pytest.mark.parametrize("endpoint", ["base", "compare", "stats", "add_eval", "start", "list", "preview"])
def test_comparison_rejects_foreign_dataset_before_side_effects(tenants, scope, endpoint, tmp_path):
    a, b, _ = tenants
    dataset, row = _target(a, b, scope)
    ids = [str(a["dataset"].id), str(dataset.id)]
    template_id = str(EvalTemplate.no_workspace_objects.create(
        name="synthetic-template", organization=a["org"], workspace=a["workspace"],
        config={"eval_type_id": "synthetic", "required_keys": []},
    ).id)
    payloads = {
        "base": (views.GetBaseColumnsView, "get", {"dataset_ids": ids}, {}),
        "compare": (views.CompareDatasetsView, "post", {"dataset_ids": [str(dataset.id)], "base_column_name": "question"}, {"dataset_id": a["dataset"].id}),
        "stats": (views.CompareDatasetsStatsView, "post", {"dataset_ids": ids, "base_column_name": "question"}, {"dataset_id": a["dataset"].id}),
        "add_eval": (views.AddCompareExperimentEvalView, "post", {"dataset_ids": ids, "name": "eval", "template_id": template_id, "config": {}}, {"dataset_id": a["dataset"].id}),
        "start": (views.CompareDatasetsStartEvalsProcess, "post", {"dataset_ids": ids, "user_eval_names": ["eval"]}, {"dataset_id": a["dataset"].id}),
        "list": (views.GetCompareEvalsListView, "post", {"dataset_ids": ids, "eval_type": "user"}, {}),
        "preview": (views.ComparePreviewRunEvalView, "post", {"dataset_ids": ids, "template_id": template_id, "config": {}, "dataset_info": {"base": {str(dataset.id): str(row.id)}}}, {}),
    }
    view, method, payload, kwargs = payloads[endpoint]
    before = list(Cell.all_objects.order_by("id").values())
    metric_count = UserEvalMetric.no_workspace_objects.count()
    with patch.object(views, "get_compare_local_dir", return_value=str(tmp_path / "cache")), patch.object(views, "get_compare_metadata_path", return_value=str(tmp_path / "metadata.json")), patch.object(views, "EvaluationRunner") as runner, patch.object(views, "process_eval_for_single_row", return_value={}):
        response = view.as_view()(_request(a, method, payload), **kwargs)
    assert response.status_code == 404, response.data
    runner.assert_not_called()
    assert list(Cell.all_objects.order_by("id").values()) == before
    assert not (tmp_path / "cache").exists()
    assert UserEvalMetric.no_workspace_objects.count() == metric_count


@pytest.mark.parametrize("endpoint", ["base", "stats", "start", "list"])
def test_comparison_own_dataset_controls(tenants, endpoint):
    a, _, _ = tenants
    ids = [str(a["dataset"].id)]
    payloads = {
        "base": (views.GetBaseColumnsView, "get", {"dataset_ids": ids}, {}),
        "stats": (views.CompareDatasetsStatsView, "post", {"dataset_ids": ids, "base_column_name": "question"}, {"dataset_id": a["dataset"].id}),
        "start": (views.CompareDatasetsStartEvalsProcess, "post", {"dataset_ids": ids, "user_eval_names": ["eval"]}, {"dataset_id": a["dataset"].id}),
        "list": (views.GetCompareEvalsListView, "post", {"dataset_ids": ids, "eval_type": "user"}, {}),
    }
    view, method, payload, kwargs = payloads[endpoint]
    response = view.as_view()(_request(a, method, payload), **kwargs)
    assert response.status_code == 200, response.data


@pytest.mark.parametrize("method", ["get", "delete"])
@pytest.mark.parametrize("own", [False, True])
def test_cached_comparison_requires_all_metadata_datasets(tenants, tmp_path, method, own):
    a, b, _ = tenants
    owner = a if own else b
    compare_id = uuid.uuid4()
    row = owner["rows"][0]
    metadata = {
        "status": "completed", "file_row_ids": {str(row.id): "page_1"}, "total_pages": 1,
        "comparison_datasets": [str(owner["dataset"].id)], "base_dataset_id": str(owner["dataset"].id),
        "base_column_name": "question", "common_column_names": [], "dataset_info": {},
    }
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps(metadata))
    kwargs = {"compare_id": compare_id}
    if method == "get":
        kwargs["row_id"] = row.id
    with patch.object(views, "get_compare_metadata_path", return_value=str(path)), patch.object(views, "download_json_from_s3", return_value={"table": [{"row_id": str(row.id)}]}) as read, patch("tfc.temporal.drop_in.start_activity") as dispatch:
        response = views.GetCompareDatasetRow.as_view()(_request(a, method, {}), **kwargs)
    assert response.status_code == (200 if own else 404), response.data
    if not own:
        read.assert_not_called()
        dispatch.assert_not_called()
    assert json.loads(path.read_text()) == metadata


@pytest.mark.parametrize("endpoint", ["add_eval", "preview"])
@pytest.mark.parametrize("foreign_row", [False, True])
def test_comparison_execution_requires_rows_to_match_authorized_datasets(tenants, endpoint, foreign_row):
    a, b, _ = tenants
    template = EvalTemplate.no_workspace_objects.create(
        name="synthetic", organization=a["org"], workspace=a["workspace"],
        config={"eval_type_id": "synthetic", "required_keys": []},
    )
    row = b["rows"][0] if foreign_row else a["rows"][0]
    payload = {"name": "eval", "template_id": str(template.id), "config": {"mapping": {}, "config": {}},
               "dataset_ids": [str(a["dataset"].id)]}
    with patch.object(views, "EvaluationRunner"), patch.object(views, "process_eval_for_single_row", return_value={}) as process:
        if endpoint == "preview":
            payload.pop("name")
            payload["dataset_info"] = {"base": {str(a["dataset"].id): str(row.id)}}
            response = views.ComparePreviewRunEvalView.as_view()(_request(a, "post", payload))
        else:
            # This endpoint has no row map; use a foreign dataset instead.
            if foreign_row:
                payload["dataset_ids"].append(str(b["dataset"].id))
            response = views.AddCompareExperimentEvalView.as_view()(_request(a, "post", payload), dataset_id=a["dataset"].id)
    assert response.status_code == (404 if foreign_row else 200), response.data
    if foreign_row:
        process.assert_not_called()
        assert not UserEvalMetric.no_workspace_objects.exists()


@pytest.mark.parametrize("endpoint", ["add_eval", "preview"])
@pytest.mark.parametrize("foreign", [False, True])
def test_comparison_template_scope_before_work(tenants, endpoint, foreign):
    a, b, _ = tenants
    owner = b if foreign else a
    template = EvalTemplate.no_workspace_objects.create(
        name="private-template", organization=owner["org"], workspace=owner["workspace"],
        owner="user", config={"eval_type_id": "synthetic", "required_keys": []},
    )
    payload = {"name": "eval", "template_id": str(template.id), "config": {"mapping": {}, "config": {}},
               "dataset_ids": [str(a["dataset"].id)]}
    with patch.object(views, "EvaluationRunner") as runner, patch.object(views, "process_eval_for_single_row", return_value={}):
        if endpoint == "preview":
            payload.pop("name")
            payload["dataset_info"] = {"base": {str(a["dataset"].id): str(a["rows"][0].id)}}
            response = views.ComparePreviewRunEvalView.as_view()(_request(a, "post", payload))
        else:
            response = views.AddCompareExperimentEvalView.as_view()(_request(a, "post", payload), dataset_id=a["dataset"].id)
    assert response.status_code == (404 if foreign else 200), response.data
    if foreign:
        runner.assert_not_called()
        assert not UserEvalMetric.no_workspace_objects.exists()


def test_cell_read_keeps_partial_results_when_row_was_deleted(tenants):
    a, _, _ = tenants
    Row.all_objects.filter(pk=a["rows"][1].pk).update(deleted=True)
    response = views.GetCellDataView.as_view()(_request(a, "post", {
        "row_ids": [str(r.id) for r in a["rows"]], "column_ids": [str(a["column"].id)],
    }))
    assert response.status_code == 200, response.data
    assert set(response.data["result"]) == {str(a["rows"][0].id)}
