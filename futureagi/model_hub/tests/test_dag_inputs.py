"""Decision-tree input propagation and exact scoring through the real views."""

import json
from unittest.mock import patch

import pytest

from accounts.models.workspace import Workspace
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation
from model_hub.tests.test_dag_eval import (
    ADHOC,
    JUDGE,
    created,
    seed_dataset,
    tree_url,
)
from model_hub.tests.test_dag_eval import (
    api as api,  # explicit shared fixture export
)

INPUTS = "/model-hub/eval-templates/dag/inputs/"


def simple_tree(node, score=0.499, output_type="score"):
    return {
        "root": "n1",
        "output_type": output_type,
        "nodes": [
            {
                "id": "n1",
                **node,
                "answers": [
                    {"label": "Pass", "next": "yes"},
                    {"label": "Fail", "next": "no"},
                ],
            },
            {"id": "yes", "kind": "leaf", "label": "Near", "score": score},
            {"id": "no", "kind": "leaf", "label": "No", "score": 0},
        ],
    }


def child_eval(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="input-reading-child",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="llm",
        template_type="single",
        config={"required_keys": ["output", "expected"]},
        output_type_normalized="pass_fail",
    )


@pytest.mark.django_db
def test_child_inputs_resolve_from_scope_not_client_metadata(
    api, organization, workspace, user
):
    child = child_eval(organization, workspace)
    dag = simple_tree(
        {"kind": "eval", "template_id": str(child.id), "required_keys": []}
    )
    tree_id = created(api, dag=dag)
    assert api.post(INPUTS, {"dag": dag}, format="json").json()["result"][
        "required_keys"
    ] == ["output", "expected"]
    stored = EvalTemplate.no_workspace_objects.get(id=tree_id)
    assert stored.config["required_keys"] == ["output", "expected"]
    assert "{{expected}}" in api.get(tree_url(tree_id)).json()["result"]["instructions"]
    dataset, columns, _ = seed_dataset(organization, workspace, user, ["answer"])
    with patch(
        JUDGE, return_value={"output": "Passed", "reason": "child scored"}
    ) as judge:
        missing = api.post(ADHOC, {"dag": dag, "output": "answer"}, format="json")
        assert missing.status_code == 400
        assert "{{expected}} is not mapped" in missing.json()["errors"]
        missing_run = api.post(
            tree_url(tree_id, "runs/"),
            {
                "dataset_id": str(dataset.id),
                "mapping": {"output": str(columns["reply"].id)},
            },
            format="json",
        )
        assert missing_run.status_code == 400
        judge.assert_not_called()
        assert not Evaluation.objects.filter(eval_template_id=tree_id).exists()
        tested = api.post(
            ADHOC,
            {"dag": dag, "output": "answer", "values": {"expected": "billing"}},
            format="json",
        )
        assert tested.status_code == 200, tested.content
        assert tested.json()["result"]["path_key"] == "n1:Pass>yes"
        assert judge.call_args.args[1] == {"output": "answer", "expected": "billing"}
        ran = api.post(
            tree_url(tree_id, "runs/"),
            {
                "dataset_id": str(dataset.id),
                "mapping": {
                    "output": str(columns["reply"].id),
                    "expected": str(columns["topic"].id),
                },
            },
            format="json",
        )
        assert ran.status_code == 200, ran.content
        assert ran.json()["result"]["rows"][0]["skipped"] is None
        assert judge.call_args.args[1] == {"output": "answer", "expected": "billing"}
    child.config["required_keys"].append("context")
    child.save()
    resolved = api.post(INPUTS, {"dag": dag}, format="json")
    assert resolved.json()["result"]["required_keys"] == [
        "output",
        "expected",
        "context",
    ]
    # A child moved outside the current workspace must not disclose its keys.
    child.workspace = Workspace.objects.create(
        name="other", organization=organization, created_by=user
    )
    child.save()
    assert api.post(INPUTS, {"dag": dag}, format="json").status_code == 400
    assert (
        api.post(
            ADHOC,
            {"dag": dag, "output": "answer", "values": {"expected": "billing"}},
            format="json",
        ).status_code
        == 400
    )


@pytest.mark.django_db
@pytest.mark.parametrize("on", [None, "calls"])
def test_tool_call_input_reaches_typed_tests_rows_and_stored_runs(
    api, organization, workspace, user, on
):
    check = {"type": "tool_called", "value": "lookup"}
    if on:
        check["on"] = on
    variable = on or "tool_calls"
    dag = simple_tree({"kind": "check", "check": check}, score=1)
    tree_id = created(api, dag=dag)
    assert api.post(INPUTS, {"dag": dag}, format="json").json()["result"][
        "required_keys"
    ] == ["output", variable]
    missing = api.post(ADHOC, {"dag": dag, "output": "answer"}, format="json")
    assert missing.status_code == 400
    assert f"{{{{{variable}}}}} is not mapped" in missing.json()["errors"]
    calls = json.dumps([{"function": {"name": "lookup", "arguments": "{}"}}])
    for value, passed in [(calls, True), ("[]", False), ('[{"name":"other"}]', False)]:
        tested = api.post(
            ADHOC,
            {"dag": dag, "output": "answer", "values": {variable: value}},
            format="json",
        )
        assert tested.status_code == 200, tested.content
        assert tested.json()["result"]["passed"] is passed
    invalid = api.post(
        ADHOC,
        {"dag": dag, "output": "answer", "values": {variable: "not json"}},
        format="json",
    )
    assert invalid.json()["result"]["passed"] is None
    assert "JSON array" in invalid.json()["result"]["skipped"]
    dataset, columns, rows = seed_dataset(organization, workspace, user, [calls])
    mapping = {"output": str(columns["topic"].id), variable: str(columns["reply"].id)}
    tested_row = api.post(
        ADHOC,
        {
            "dag": dag,
            "output": "answer",
            "dataset_id": str(dataset.id),
            "row_id": str(rows[0].id),
            "mapping": mapping,
        },
        format="json",
    )
    assert tested_row.json()["result"]["passed"] is True
    ran = api.post(
        tree_url(tree_id, "runs/"),
        {"dataset_id": str(dataset.id), "mapping": mapping},
        format="json",
    )
    assert ran.status_code == 200, ran.content
    row = Evaluation.objects.get(parent_evaluation_id=ran.json()["result"]["run_id"])
    assert row.output_bool is True
    assert row.data["dag"]["path_key"] == "n1:Pass>yes"


@pytest.mark.django_db
@pytest.mark.parametrize("output_type", ["score", "pass_fail"])
@pytest.mark.parametrize("score,passed", [(0.499, False), (0.5, True), (0.501, True)])
def test_leaf_precision_survives_thresholding_and_storage(
    api, organization, workspace, user, output_type, score, passed
):
    dag = simple_tree(
        {"kind": "check", "check": {"type": "contains", "value": "answer"}},
        score,
        output_type,
    )
    tree_id = created(api, dag=dag, pass_threshold=0.5)
    tested = api.post(
        ADHOC, {"dag": dag, "output": "answer", "pass_threshold": 0.5}, format="json"
    )
    result = tested.json()["result"]
    value = int(passed) if output_type == "pass_fail" else score
    assert result["score"] == result["leaf"]["score"] == score
    assert result["passed"] is passed
    assert result["value"] == value
    dataset, columns, _ = seed_dataset(organization, workspace, user, ["answer"])
    ran = api.post(
        tree_url(tree_id, "runs/"),
        {
            "dataset_id": str(dataset.id),
            "mapping": {"output": str(columns["reply"].id)},
        },
        format="json",
    )
    assert ran.status_code == 200, ran.content
    run = ran.json()["result"]
    row = Evaluation.objects.get(parent_evaluation_id=run["run_id"])
    assert row.output_float == score
    assert row.output_bool is passed
    assert float(row.value) == value
    report = api.get(tree_url(tree_id, "paths/"), {"run_id": run["run_id"]}).json()[
        "result"
    ]
    assert report["paths"][0]["mean"] == score
