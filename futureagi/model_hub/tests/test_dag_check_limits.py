"""Oversized dataset checks must not persist a score computed from a prefix."""

from unittest.mock import patch

import pytest

from model_hub.models.develop_dataset import Cell
from model_hub.models.evaluation import Evaluation
from model_hub.tests.test_dag_eval import (
    JUDGE,
    client_for,
    created,
    seed_dataset,
    tree_url,
)
from model_hub.tests.test_dag_inputs import simple_tree
from tfc.middleware.workspace_context import clear_workspace_context


@pytest.fixture
def api(db, organization, workspace, user):
    client = client_for(user, workspace)
    yield client
    client.stop_workspace_injection()
    clear_workspace_context()


@pytest.mark.django_db
@pytest.mark.parametrize("on", ["output", "answer"])
@pytest.mark.parametrize("output_type", ["score", "pass_fail"])
@pytest.mark.parametrize(
    ("check", "value", "expected"),
    [
        pytest.param(
            {"type": "length", "op": "lte", "n": 20000},
            "x" * 20001,
            "billing",
            id="length-prefix-would-pass",
        ),
        pytest.param(
            {"type": "contains", "value": "z"},
            "x" * 20000 + "z",
            "billing",
            id="contains-tail-would-be-lost",
        ),
        pytest.param(
            {"type": "not_contains", "value": "z"},
            "x" * 20000 + "z",
            "billing",
            id="not-contains-prefix-would-pass",
        ),
        pytest.param(
            {"type": "equals_expected"},
            "x" * 20000 + "z",
            "x" * 20000,
            id="equality-prefix-would-pass",
        ),
        pytest.param(
            {"type": "equals_expected"},
            "x" * 20000 + "z",
            "x" * 20000 + "z",
            id="equality-identical-full-values",
        ),
        pytest.param(
            {"type": "json_path", "path": "$.ok", "op": "exists"},
            '{"padding":"' + "x" * 20000 + '","ok":true}',
            "billing",
            id="json-tail-would-be-lost",
        ),
        pytest.param(
            {"type": "json_path", "path": "$.ok", "op": "equals", "value": "true"},
            '{"ok":true}'.ljust(20000) + "!",
            "billing",
            id="json-invalid-suffix-would-be-hidden",
        ),
    ],
)
def test_oversized_dataset_checks_skip_without_scoring(
    api, organization, workspace, user, on, output_type, check, value, expected
):
    assert len(value) > 20000
    dag = simple_tree(
        {"kind": "check", "check": {**check, "on": on}},
        score=1,
        output_type=output_type,
    )
    tree_id = created(api, dag=dag)
    dataset, columns, rows = seed_dataset(organization, workspace, user, [value])
    Cell.no_workspace_objects.filter(dataset=dataset, column=columns["topic"]).update(
        value=expected
    )
    mapping = {"output": str(columns["topic"].id), "expected": str(columns["topic"].id)}
    mapping[on] = str(columns["reply"].id)
    with patch(
        JUDGE, side_effect=AssertionError("deterministic check asked a judge")
    ) as judge:
        response = api.post(
            tree_url(tree_id, "runs/"),
            {"dataset_id": str(dataset.id), "mapping": mapping},
            format="json",
        )
        assert response.status_code == 200, response.content
        run = response.json()["result"]
        reason = f"{on} exceeds the 20000-character check limit at question 1"
        assert run["n"] == 1
        assert run["rows"] == [
            {
                "row_id": str(rows[0].id),
                "value": None,
                "passed": None,
                "skipped": reason,
                "path_key": "skipped",
                "path_label": "",
            }
        ]
        parent = Evaluation.objects.get(id=run["run_id"])
        assert parent.value is None
        child = parent.child_evaluations.get()
        assert child.value is None
        assert child.output_float is None
        assert child.output_bool is None
        assert child.reason == f"Skipped · {reason}"
        assert child.data["dag"] == {
            "row_id": str(rows[0].id),
            "path": [],
            "path_key": "skipped",
            "path_label": "",
            "leaf": None,
        }
        paths = api.get(tree_url(tree_id, "paths/"), {"run_id": run["run_id"]})
        assert paths.status_code == 200, paths.content
        report = paths.json()["result"]
        assert report["n"] == 1
        assert report["paths"] == [
            {
                "path_key": "skipped",
                "label": "",
                "rows": [str(rows[0].id)],
                "n": 1,
                "share": 1,
                "mean": None,
            }
        ]
        judge.assert_not_called()
    # Reject scoring, not the stored source: the dataset cell remains intact.
    assert (
        Cell.no_workspace_objects.get(
            dataset=dataset, row=rows[0], column=columns["reply"]
        ).value
        == value
    )


@pytest.mark.django_db
@pytest.mark.parametrize("on", ["output", "answer"])
@pytest.mark.parametrize(
    "check",
    [
        pytest.param({"type": "length", "op": "lte", "n": 20000}, id="length"),
        pytest.param({"type": "contains", "value": "true"}, id="contains"),
        pytest.param({"type": "equals_expected"}, id="equality"),
        pytest.param(
            {"type": "json_path", "path": "$.ok", "op": "equals", "value": "true"},
            id="json",
        ),
    ],
)
def test_dataset_checks_still_score_at_and_below_limit(
    api, organization, workspace, user, on, check
):
    values = ['{"ok":true}'.rjust(size) for size in (19999, 20000)]
    dag = simple_tree({"kind": "check", "check": {**check, "on": on}}, score=1)
    tree_id = created(api, dag=dag)
    dataset, columns, _ = seed_dataset(organization, workspace, user, values)
    mapping = {"output": str(columns["topic"].id), "expected": str(columns["reply"].id)}
    mapping[on] = str(columns["reply"].id)
    with patch(
        JUDGE, side_effect=AssertionError("deterministic check asked a judge")
    ) as judge:
        response = api.post(
            tree_url(tree_id, "runs/"),
            {"dataset_id": str(dataset.id), "mapping": mapping},
            format="json",
        )
        judge.assert_not_called()
    assert response.status_code == 200, response.content
    run = response.json()["result"]
    assert run["n"] == 2
    for row in run["rows"]:
        assert row["skipped"] is None
        assert row["passed"] is True
        assert row["value"] == 1
        assert row["path_key"] == "n1:Pass>yes"
    parent = Evaluation.objects.get(id=run["run_id"])
    assert float(parent.value) == 1
    assert parent.child_evaluations.count() == 2
    for child in parent.child_evaluations.all():
        assert child.output_float == 1
        assert child.output_bool is True
        assert float(child.value) == 1
