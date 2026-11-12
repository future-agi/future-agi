"""Ordinary composites reject DAG children without changing standalone DAGs."""

from unittest.mock import patch

import pytest

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import CompositeEvalChild, EvalTemplate

CREATE = "/model-hub/eval-templates/create-composite/"
DAG_CREATE = "/model-hub/eval-templates/create-dag/"
DAG_EXECUTE = "/model-hub/eval-templates/dag/execute-adhoc/"
ERROR = "DAG evaluations cannot be used as composite children. Run them separately."

pytestmark = [pytest.mark.e2e, pytest.mark.django_db]


def definition(output_type):
    return {
        "schema": "fi-dag-v1",
        "root": "check",
        "output_type": output_type,
        "nodes": [
            {
                "id": "check",
                "kind": "check",
                "check": {"type": "contains", "on": "output", "value": "refund"},
                "answers": [
                    {"label": "Pass", "next": "good"},
                    {"label": "Fail", "next": "bad"},
                ],
            },
            {"id": "good", "kind": "leaf", "label": "Resolved", "score": 1},
            {"id": "bad", "kind": "leaf", "label": "Unresolved", "score": 0},
        ],
    }


@pytest.fixture(params=["pass_fail", "score"])
def dag_child(request, auth_client):
    response = auth_client.post(
        DAG_CREATE,
        {"name": "standalone-tree", "dag": definition(request.param)},
        format="json",
    )
    assert response.status_code == 200, response.data
    tree = EvalTemplate.no_workspace_objects.get(id=response.data["result"]["id"])
    assert tree.eval_type == "dag"
    assert tree.template_type == "single"
    assert tree.visible_ui is True
    return tree


def ordinary_child(organization, workspace, axis, eval_type="llm", name="ordinary"):
    return EvalTemplate.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        template_type="single",
        eval_type=eval_type,
        output_type_normalized=axis,
        config={"output": "Pass/Fail" if axis == "pass_fail" else "score"},
        visible_ui=True,
    )


@pytest.mark.parametrize("legacy_axis", [False, True])
@pytest.mark.parametrize("mixed", [False, True])
def test_create_rejects_dag_before_writing_parent_or_links(
    auth_client, organization, workspace, dag_child, legacy_axis, mixed
):
    axis = dag_child.output_type_normalized
    child = ordinary_child(organization, workspace, axis)
    ids = [str(child.id), str(dag_child.id)] if mixed else [str(dag_child.id)]
    before_links = CompositeEvalChild.objects.count()
    response = auth_client.post(
        CREATE,
        {
            "name": "rejected-composite",
            "child_template_ids": ids,
            "composite_child_axis": "" if legacy_axis else axis,
        },
        format="json",
    )
    assert response.status_code == 400, response.data
    assert ERROR in str(response.data)
    assert not EvalTemplate.no_workspace_objects.filter(
        name="rejected-composite"
    ).exists()
    assert CompositeEvalChild.objects.count() == before_links
    dag_child.refresh_from_db()
    assert dag_child.deleted is False


@pytest.mark.parametrize("legacy_axis", [False, True])
@pytest.mark.parametrize("mixed", [False, True])
def test_update_rejects_dag_without_mutating_parent_or_existing_link(
    auth_client, organization, workspace, dag_child, legacy_axis, mixed
):
    axis = dag_child.output_type_normalized
    child = ordinary_child(organization, workspace, axis)
    create = auth_client.post(
        CREATE,
        {
            "name": "unchanged-composite",
            "description": "Keep this description",
            "child_template_ids": [str(child.id)],
            "composite_child_axis": "" if legacy_axis else axis,
        },
        format="json",
    )
    assert create.status_code == 200, create.data
    parent = EvalTemplate.objects.get(id=create.data["result"]["id"])
    link = CompositeEvalChild.objects.get(parent=parent, child=child)
    before_links = CompositeEvalChild.objects.filter(parent=parent).count()
    ids = [str(child.id), str(dag_child.id)] if mixed else [str(dag_child.id)]
    response = auth_client.patch(
        f"/model-hub/eval-templates/{parent.id}/composite/",
        {
            "name": "must-not-save",
            "description": "Must not save either",
            "aggregation_function": "min",
            "child_template_ids": ids,
        },
        format="json",
    )
    assert response.status_code == 400, response.data
    assert ERROR in str(response.data)
    parent.refresh_from_db()
    link.refresh_from_db()
    assert parent.name == "unchanged-composite"
    assert parent.description == "Keep this description"
    assert parent.aggregation_function == "weighted_avg"
    assert parent.composite_child_axis == ("" if legacy_axis else axis)
    assert not link.deleted
    assert link.child_id == child.id and link.weight == 1.0
    assert CompositeEvalChild.objects.filter(parent=parent).count() == before_links
    assert not CompositeEvalChild.objects.filter(
        parent=parent, child=dag_child
    ).exists()


@pytest.mark.parametrize("eval_type", ["llm", "code", "agent"])
@pytest.mark.parametrize("axis", ["pass_fail", "percentage"])
def test_ordinary_children_still_create_and_replace(
    auth_client, organization, workspace, eval_type, axis
):
    first = ordinary_child(organization, workspace, axis, eval_type, "first-child")
    second = ordinary_child(organization, workspace, axis, eval_type, "second-child")
    create = auth_client.post(
        CREATE,
        {
            "name": "ordinary-composite",
            "child_template_ids": [str(first.id)],
            "composite_child_axis": axis,
        },
        format="json",
    )
    assert create.status_code == 200, create.data
    parent_id = create.data["result"]["id"]
    assert create.data["result"]["children"][0]["child_id"] == str(first.id)
    update = auth_client.patch(
        f"/model-hub/eval-templates/{parent_id}/composite/",
        {
            "child_template_ids": [str(second.id)],
            "child_weights": {str(second.id): 2.5},
        },
        format="json",
    )
    assert update.status_code == 200, update.data
    assert update.data["result"]["children"][0]["child_id"] == str(second.id)
    link = CompositeEvalChild.objects.get(parent_id=parent_id, deleted=False)
    assert link.child_id == second.id and link.weight == 2.5


def test_picker_list_excludes_dag_before_pagination_only_when_requested(
    auth_client, organization, workspace, dag_child
):
    axis = dag_child.output_type_normalized
    child = ordinary_child(organization, workspace, axis)
    filters = {
        "names": [child.name, dag_child.name],
        "output_type": [axis],
        "template_type": ["single"],
    }
    body = {"owner_filter": "user", "page_size": 1, "filters": filters}
    standalone = auth_client.post(
        "/model-hub/eval-templates/list/", body, format="json"
    )
    assert standalone.status_code == 200, standalone.data
    assert standalone.data["result"]["total"] == 2
    composite = auth_client.post(
        "/model-hub/eval-templates/list/",
        {**body, "filters": {**filters, "eval_type_not": ["dag"]}},
        format="json",
    )
    assert composite.status_code == 200, composite.data
    result = composite.data["result"]
    assert result["total"] == 1
    assert [item["id"] for item in result["items"]] == [str(child.id)]


def test_standalone_dag_still_executes(auth_client, dag_child):
    with patch(
        "model_hub.views.utils.evals.run_eval_func",
        side_effect=AssertionError("Check-only DAG must not invoke a judge"),
    ) as judge:
        response = auth_client.post(
            DAG_EXECUTE,
            {
                "template_id": str(dag_child.id),
                "dag": dag_child.config["dag"],
                "output": "Your refund is on its way",
            },
            format="json",
        )
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["score"] == 1
    assert result["passed"] is True
    assert result["path_key"] == "check:Pass>good"
    assert result["judge_calls"] == 0
    judge.assert_not_called()
