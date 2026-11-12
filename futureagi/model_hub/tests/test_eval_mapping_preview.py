"""Mapping previews read scoped saved rows without running an evaluation."""

from unittest.mock import patch

import pytest

from accounts.models.workspace import Workspace
from conftest import WorkspaceAwareAPIClient
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import (
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.tests.test_dsx_l4_experiment_eval_pin import (
    _second_tenant,
    _template,
    _world,
)

URL = "/model-hub/eval-mapping-preview/"
pytestmark = pytest.mark.django_db


@pytest.fixture
def preview(organization, workspace, user):
    world = _world(organization, workspace, user, "preview")
    template, versions = _template(organization, workspace, "preview")
    config = {"required_keys": ["question", "output", "expected"]}
    for version in versions:
        version.config_snapshot = config
        version.save(update_fields=["config_snapshot"])
    template.config = config
    template.save(update_fields=["config"])
    row = Row.no_workspace_objects.get(dataset=world["dataset"])
    body = {
        "dataset_id": str(world["dataset"].id),
        "row_ids": [str(row.id)],
        "template_id": str(template.id),
        "config": {
            "mapping": {"question": str(world["column"].id), "output": "output"},
            "version_pin": "v1",
        },
    }
    return world, template, row, body


def _snapshot(dataset, template):
    return {
        "cells": list(
            Cell.no_workspace_objects.filter(dataset=dataset).order_by("id").values()
        ),
        "rows": list(
            Row.no_workspace_objects.filter(dataset=dataset).order_by("id").values()
        ),
        "columns": list(
            Column.no_workspace_objects.filter(dataset=dataset).order_by("id").values()
        ),
        "bindings": list(
            UserEvalMetric.no_workspace_objects.filter(dataset=dataset).values()
        ),
        "versions": list(
            EvalTemplateVersion.objects.filter(eval_template=template)
            .order_by("id")
            .values()
        ),
    }


def test_preview_reads_values_and_never_calls_judge_or_writes(auth_client, preview):
    world, template, row, body = preview
    before = _snapshot(world["dataset"], template)
    with (
        patch("model_hub.views.separate_evals.run_eval_func") as playground_judge,
        patch(
            "model_hub.views.eval_runner.EvaluationRunner._run_evaluation"
        ) as dataset_judge,
    ):
        response = auth_client.post(URL, body, format="json")
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["version_number"] == 1
    assert result["rows"] == [
        {
            "row_id": str(row.id),
            "inputs_used": {
                "question": {
                    "column": "question",
                    "value": "What is 2+2?",
                    "status": "ok",
                    "note": None,
                },
                "output": {
                    "column": "run output",
                    "value": None,
                    "status": "pending",
                    "note": "Available after the run",
                },
                "expected": {
                    "column": None,
                    "value": None,
                    "status": "missing",
                    "note": "Not mapped",
                },
            },
        }
    ]
    playground_judge.assert_not_called()
    dataset_judge.assert_not_called()
    assert _snapshot(world["dataset"], template) == before


def test_composite_preview_refuses_without_judge_or_writes(auth_client, preview):
    world, template, _, body = preview
    template.template_type = "composite"
    template.save(update_fields=["template_type"])
    before = _snapshot(world["dataset"], template)
    with (
        patch("model_hub.views.separate_evals.run_eval_func") as playground_judge,
        patch(
            "model_hub.views.eval_runner.EvaluationRunner._run_evaluation"
        ) as dataset_judge,
    ):
        response = auth_client.post(URL, body, format="json")
    assert response.status_code == 400, response.content
    assert "Composite input previews are not supported." in response.content.decode()
    playground_judge.assert_not_called()
    dataset_judge.assert_not_called()
    assert _snapshot(world["dataset"], template) == before


def test_preview_uses_pinned_input_keys_and_preserves_row_order(auth_client, preview):
    world, template, row, body = preview
    other = Row.no_workspace_objects.create(dataset=world["dataset"], order=1)
    version = EvalTemplateVersion.objects.get(eval_template=template, version_number=2)
    version.config_snapshot = {"required_keys": ["different"]}
    version.save(update_fields=["config_snapshot"])
    body["row_ids"] = [str(other.id), str(row.id)]
    response = auth_client.post(URL, body, format="json")
    assert response.status_code == 200, response.content
    rows = response.json()["result"]["rows"]
    assert [item["row_id"] for item in rows] == body["row_ids"]
    assert set(rows[0]["inputs_used"]) == {"question", "output", "expected"}
    assert rows[0]["inputs_used"]["question"] == {
        "column": "question",
        "value": None,
        "status": "missing",
        "note": "Empty on this row",
    }


def test_preview_json_path_matches_execution_helper(auth_client, preview):
    from model_hub.views.eval_runner import _get_cell_value_with_json_path

    world, _, row, body = preview
    cell = Cell.no_workspace_objects.get(row=row, column=world["column"])
    cell.value = '{"answer": {"count": 0, "ok": false}}'
    cell.save(update_fields=["value"])
    body["config"]["mapping"]["question"] += ".answer.count"
    response = auth_client.post(URL, body, format="json")
    assert response.status_code == 200, response.content
    used = response.json()["result"]["rows"][0]["inputs_used"]["question"]
    assert used["value"] == _get_cell_value_with_json_path(cell, "answer.count") == "0"
    assert used["status"] == "ok"


@pytest.mark.parametrize("part", ["dataset_id", "row_ids", "template_id", "mapping"])
def test_preview_refuses_foreign_identifiers(auth_client, preview, part):
    world, template, _, body = preview
    org_b, ws_b, user_b, client_b = _second_tenant("Preview B")
    try:
        other = _world(org_b, ws_b, user_b, "preview-b")
        tpl_b, _ = _template(org_b, ws_b, "preview-b")
        row_b = Row.no_workspace_objects.get(dataset=other["dataset"])
        if part == "mapping":
            body["config"]["mapping"]["question"] = str(other["column"].id)
        else:
            body[part] = {
                "dataset_id": str(other["dataset"].id),
                "row_ids": [str(row_b.id)],
                "template_id": str(tpl_b.id),
            }[part]
        before = _snapshot(world["dataset"], template)
        response = auth_client.post(URL, body, format="json")
        assert response.status_code == 404, response.content
        assert "What is 2+2?" not in response.content.decode()
        assert _snapshot(world["dataset"], template) == before
    finally:
        client_b.stop_workspace_injection()


def _sibling_workspace(organization, user):
    """A second, non-default workspace in the same organization."""
    return Workspace.objects.create(
        name="preview-sibling",
        organization=organization,
        created_by=user,
        is_default=False,
        is_active=True,
    )


def test_preview_refuses_another_workspace_in_same_org(
    auth_client, preview, organization, user
):
    world, _, _, body = preview
    sibling = _sibling_workspace(organization, user)
    dataset_id = world["dataset"].id
    Dataset.no_workspace_objects.filter(id=dataset_id).update(workspace=sibling)
    response = auth_client.post(URL, body, format="json")
    assert response.status_code == 404, response.content
    assert "Dataset not found." in response.content.decode()


def test_preview_reads_a_dataset_with_no_workspace(
    auth_client, preview, organization, user
):
    world, _, _, body = preview
    # Seeded demo datasets and pre-workspace datasets carry no workspace; the
    # organization's default workspace previews them. A sibling workspace and
    # another organization's own default workspace do not.
    Dataset.no_workspace_objects.filter(id=world["dataset"].id).update(workspace=None)
    response = auth_client.post(URL, body, format="json")
    assert response.status_code == 200, response.content
    used = response.json()["result"]["rows"][0]["inputs_used"]["question"]
    assert used == {
        "column": "question",
        "value": "What is 2+2?",
        "status": "ok",
        "note": None,
    }
    sibling = WorkspaceAwareAPIClient()
    sibling.force_authenticate(user=user)
    sibling.set_workspace(_sibling_workspace(organization, user))
    *_, foreign = _second_tenant("Preview foreign")
    for other in (sibling, foreign):
        try:
            response = other.post(URL, body, format="json")
            assert response.status_code == 404, response.content
            assert "Dataset not found." in response.content.decode()
            assert "What is 2+2?" not in response.content.decode()
        finally:
            other.stop_workspace_injection()


@pytest.mark.parametrize("field", ["row", "column", "template", "version"])
def test_preview_refuses_deleted_dependencies(auth_client, preview, field):
    world, template, row, body = preview
    target = {
        "row": row,
        "column": world["column"],
        "template": template,
        "version": EvalTemplateVersion.objects.get(
            eval_template=template, version_number=1
        ),
    }[field]
    target.deleted = True
    target.save(update_fields=["deleted"])
    response = auth_client.post(URL, body, format="json")
    assert response.status_code in (400, 404), response.content


@pytest.mark.parametrize(
    "change",
    [
        {"row_ids": []},
        {"row_ids": ["not-a-uuid"]},
        {"row_ids": ["00000000-0000-0000-0000-000000000001"] * 11},
        {"config": {"mapping": {"question": {"path": "bad"}}}},
        {"run": True},
    ],
)
def test_preview_refuses_invalid_requests(auth_client, preview, change):
    *_, body = preview
    body.update(change)
    response = auth_client.post(URL, body, format="json")
    assert response.status_code == 400, response.content
