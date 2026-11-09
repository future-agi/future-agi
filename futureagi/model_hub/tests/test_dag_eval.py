"""Decision-tree evals through their real routes: author, validate, test, run, paths."""

import copy
from unittest.mock import patch

import pytest

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from conftest import WorkspaceAwareAPIClient
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
from model_hub.models.evaluation import Evaluation
from model_hub.serializers.dag_eval import DagPathsResponseSerializer
from tfc.middleware.workspace_context import clear_workspace_context

JUDGE = "model_hub.views.utils.evals.run_eval_func"
CREATE = "/model-hub/eval-templates/create-dag/"
ADHOC = "/model-hub/eval-templates/dag/execute-adhoc/"


def tree_url(tree_id, tail=""):
    return f"/model-hub/eval-templates/{tree_id}/dag/{tail}"


def definition():
    """1 judge → 2 check on the reply → scores; the judge's No is its own score."""
    return {
        "schema": "fi-dag-v1",
        "root": "n1",
        "output_type": "score",
        "nodes": [
            {
                "id": "n1",
                "kind": "judge",
                "question": "Is the reply about {{topic}}?",
                "judge_model": "fixture-judge",
                "answers": [
                    {"label": "Yes", "next": "n2"},
                    {"label": "No", "next": "off"},
                ],
            },
            {
                "id": "n2",
                "kind": "check",
                "check": {"type": "contains", "on": "output", "value": "refund"},
                "answers": [
                    {"label": "Pass", "next": "good"},
                    {"label": "Fail", "next": "bad"},
                ],
            },
            {"id": "good", "kind": "leaf", "label": "Resolved", "score": 1},
            {"id": "bad", "kind": "leaf", "label": "Unresolved", "score": 0},
            {"id": "off", "kind": "leaf", "label": "Off topic", "score": 0.2},
        ],
    }


def client_for(user, workspace):
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    return client


@pytest.fixture
def api(db, organization, workspace, user):
    client = client_for(user, workspace)
    yield client
    client.stop_workspace_injection()
    clear_workspace_context()


def create(api, name="refund-tree", dag=None, **extra):
    body = {"name": name, "dag": dag or definition(), **extra}
    return api.post(CREATE, body, format="json")


def created(api, **kwargs):
    response = create(api, **kwargs)
    assert response.status_code == 200, response.content
    return response.json()["result"]["id"]


def seed_dataset(organization, workspace, user, replies):
    dataset = Dataset.no_workspace_objects.create(
        name="dag-replies",
        source=DatasetSourceChoices.BUILD.value,
        organization=organization,
        workspace=workspace,
        user=user,
    )
    columns = {
        name: Column.no_workspace_objects.create(
            name=name,
            data_type=DataTypeChoices.TEXT.value,
            dataset=dataset,
            source=SourceChoices.OTHERS.value,
            status=StatusType.COMPLETED.value,
        )
        for name in ("reply", "topic")
    }
    rows = []
    for order, reply in enumerate(replies):
        row = Row.no_workspace_objects.create(dataset=dataset, order=order)
        Cell.no_workspace_objects.create(
            dataset=dataset, row=row, column=columns["reply"], value=reply
        )
        Cell.no_workspace_objects.create(
            dataset=dataset, row=row, column=columns["topic"], value="billing"
        )
        rows.append(row)
    return dataset, columns, rows


def assert_paths_contract(response):
    assert response.status_code == 200, response.content
    payload = response.json()
    serializer = DagPathsResponseSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors
    assert serializer.data == payload
    return payload["result"]


def test_paths_contract_rejects_an_unstructured_result():
    serializer = DagPathsResponseSerializer(data={"result": {"paths": "invalid"}})
    assert not serializer.is_valid()
    assert "paths" in serializer.errors["result"]


@pytest.mark.django_db
def test_empty_paths_match_the_contract(api):
    tree_id = created(api)
    report = assert_paths_contract(api.get(tree_url(tree_id, "paths/")))
    assert report["paths"] == [] and report["runs"] == [] and report["n"] == 0
    assert report["run_id"] is None and report["dataset_id"] is None


def verdicts(*passes):
    return [
        {"output": "Passed" if ok else "Failed", "reason": f"judge said {ok}"}
        for ok in passes
    ]


@pytest.mark.django_db
def test_create_stores_the_normalized_tree_and_its_outline(api, workspace):
    tree_id = created(api, description="")
    tree = EvalTemplate.no_workspace_objects.get(id=tree_id)
    assert tree.eval_type == "dag"
    assert tree.workspace_id == workspace.id
    assert tree.config["dag"]["nodes"][1]["check"] == {
        "type": "contains",
        "on": "output",
        "value": "refund",
    }
    assert tree.model == "fixture-judge"
    assert "1. Is the reply about {{topic}}? — Yes → question 2" in tree.criteria
    assert tree.description.startswith("Decision tree · 2 questions")
    assert EvalTemplateVersion.objects.filter(eval_template=tree).count() == 1
    detail = api.get(tree_url(tree_id)).json()["result"]
    assert detail["dag"] == tree.config["dag"]
    assert detail["version"] == "v1"
    generic = api.get(f"/model-hub/eval-templates/{tree_id}/detail/")
    assert generic.status_code == 200, generic.content
    assert generic.json()["result"]["eval_type"] == "dag"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["nodes"][1]["answers"][1].update(next="n1"), "2 loops back to 1"),
        (
            lambda d: d["nodes"][0]["answers"][0].update(next="nowhere"),
            "1 has an answer that leads nowhere",
        ),
        (lambda d: d["nodes"][2].update(score=3), "Scores are between 0 and 1"),
        (lambda d: d.update(root="good"), "Start from one question"),
        (
            lambda d: d["nodes"][1]["check"].update(
                type="regex", pattern="(", value=None
            ),
            "( is not a valid pattern",
        ),
        (
            lambda d: d["nodes"][0].update(kind="python"),
            "Node 1 has a kind that is not valid",
        ),
        (lambda d: d["nodes"][0].pop("judge_model"), "Question 1 needs a judge"),
    ],
)
def test_invalid_trees_are_refused_and_nothing_is_written(api, mutate, message):
    dag = definition()
    mutate(dag)
    response = create(api, dag=dag)
    assert response.status_code == 400, response.content
    assert message in response.json()["errors"]
    assert not EvalTemplate.no_workspace_objects.filter(name="refund-tree").exists()


@pytest.mark.django_db
def test_depth_and_judge_call_bounds_are_enforced(api):
    chain = [
        {
            "id": f"c{i}",
            "kind": "check",
            "check": {"type": "length", "on": "output", "op": "gte", "n": 0},
            "answers": [
                {"label": "Pass", "next": f"c{i + 1}" if i < 12 else "end"},
                {"label": "Fail", "next": "end"},
            ],
        }
        for i in range(13)
    ]
    deep = {
        "root": "c0",
        "nodes": chain + [{"id": "end", "kind": "leaf", "label": "End", "score": 1}],
    }
    response = create(api, dag=deep)
    assert "A path is at most 12 questions deep" in response.json()["errors"]
    options = [{"label": f"Option {i}", "next": "q2"} for i in range(10)]
    wide = {
        "root": "q1",
        "nodes": [
            {
                "id": "q1",
                "kind": "judge",
                "question": "Which?",
                "judge_model": "m",
                "options": options,
            },
            {
                "id": "q2",
                "kind": "judge",
                "question": "Which?",
                "judge_model": "m",
                "options": [{**o, "next": "q3"} for o in options],
            },
            {
                "id": "q3",
                "kind": "judge",
                "question": "Which?",
                "judge_model": "m",
                "options": [{**o, "next": "end"} for o in options],
            },
            {"id": "end", "kind": "leaf", "label": "End", "score": 1},
        ],
    }
    response = create(api, dag=wide)
    assert "A path asks at most 24 judge calls" in response.json()["errors"]


@pytest.mark.django_db
def test_patch_replaces_the_tree_only_when_valid(api):
    tree_id = created(api)
    dag = definition()
    dag["nodes"][1]["check"]["value"] = "credit"
    response = api.patch(tree_url(tree_id), {"dag": dag}, format="json")
    assert response.status_code == 200, response.content
    assert 'output contains "credit"' in response.json()["result"]["instructions"]
    broken = copy.deepcopy(dag)
    broken["nodes"][1]["answers"][0]["next"] = "n1"
    response = api.patch(tree_url(tree_id), {"dag": broken}, format="json")
    assert response.status_code == 400
    tree = EvalTemplate.no_workspace_objects.get(id=tree_id)
    assert tree.config["dag"]["nodes"][1]["check"]["value"] == "credit"


@pytest.mark.django_db
def test_test_on_typed_values_asks_the_judge_and_follows_the_check(api):
    tree_id = created(api)
    with patch(JUDGE, side_effect=verdicts(True)) as judge:
        response = api.post(
            ADHOC,
            {
                "dag": definition(),
                "template_id": tree_id,
                "output": "Your refund is on its way",
                "values": {"topic": "billing"},
                "pass_threshold": 0.5,
            },
            format="json",
        )
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["path_key"] == "n1:Yes>n2:Pass>good"
    assert result["path_label"] == "1 Yes → 2 Pass → Resolved"
    assert result["score"] == 1 and result["passed"] is True
    assert result["judge_calls"] == 1
    assert [s["kind"] for s in result["path"]] == ["judge", "check"]
    args, kwargs = judge.call_args
    assert args[1] == {"topic": "billing", "output": "Your refund is on its way"}
    assert args[2].criteria == "Is the reply about {{topic}}?\n\nOutput:\n{{output}}"
    assert kwargs["model"] == "fixture-judge"
    assert not Evaluation.objects.filter(eval_template_id=tree_id).exists()


@pytest.mark.django_db
def test_test_refuses_unmapped_variables_and_reports_a_failed_judge(api):
    response = api.post(ADHOC, {"dag": definition(), "output": "x"}, format="json")
    assert response.status_code == 400
    assert "{{topic}} is not mapped" in response.json()["errors"]
    with patch(JUDGE, side_effect=RuntimeError("upstream down")):
        response = api.post(
            ADHOC,
            {"dag": definition(), "output": "x", "values": {"topic": "t"}},
            format="json",
        )
    result = response.json()["result"]
    assert result["leaf"] is None and result["skipped"].endswith("at question 1")


@pytest.mark.django_db
def test_test_on_a_dataset_row_reads_the_mapped_cells(
    api, organization, workspace, user
):
    dataset, columns, rows = seed_dataset(organization, workspace, user, ["No luck"])
    with patch(JUDGE, side_effect=verdicts(True)) as judge:
        response = api.post(
            ADHOC,
            {
                "dag": definition(),
                "output": "We issued a refund",
                "dataset_id": str(dataset.id),
                "row_id": str(rows[0].id),
                "mapping": {"topic": str(columns["topic"].id)},
            },
            format="json",
        )
    assert response.status_code == 200, response.content
    assert judge.call_args.args[1]["topic"] == "billing"
    assert response.json()["result"]["path_key"] == "n1:Yes>n2:Pass>good"


@pytest.mark.django_db
def test_run_stores_every_row_and_paths_read_without_judging(
    api, organization, workspace, user
):
    tree_id = created(api)
    dataset, columns, rows = seed_dataset(
        organization, workspace, user, ["refund sent", "no idea", "refund queued"]
    )
    mapping = {"output": str(columns["reply"].id), "topic": str(columns["topic"].id)}
    with patch(JUDGE, side_effect=verdicts(True, True, False)):
        response = api.post(
            tree_url(tree_id, "runs/"),
            {"dataset_id": str(dataset.id), "mapping": mapping},
            format="json",
        )
    assert response.status_code == 200, response.content
    run = response.json()["result"]
    assert [r["path_key"] for r in run["rows"]] == [
        "n1:Yes>n2:Pass>good",
        "n1:Yes>n2:Fail>bad",
        "n1:No>off",
    ]
    stored = Evaluation.objects.get(id=run["run_id"])
    assert stored.workspace_id == workspace.id
    assert stored.child_evaluations.count() == 3
    with patch(JUDGE, side_effect=AssertionError("paths asked a judge")):
        report = api.get(tree_url(tree_id, "paths/"), {"run_id": run["run_id"]})
    paths = assert_paths_contract(report)
    assert paths["run_id"] == run["run_id"] and paths["n"] == 3
    assert {p["label"]: p["n"] for p in paths["paths"]} == {
        "1 Yes → 2 Pass → Resolved": 1,
        "1 Yes → 2 Fail → Unresolved": 1,
        "1 No → Off topic": 1,
    }
    assert paths["runs"][0]["run_id"] == run["run_id"]


@pytest.mark.django_db
def test_listed_in_evals_but_not_offered_to_dataset_pickers(
    api, organization, workspace, user
):
    tree_id = created(api)
    listed = api.post(
        "/model-hub/eval-templates/list/", {"owner_filter": "user"}, format="json"
    )
    assert listed.status_code == 200, listed.content
    items = {i["id"]: i for i in listed.json()["result"]["items"]}
    assert items[tree_id]["eval_type"] == "dag"
    only = api.post(
        "/model-hub/eval-templates/list/",
        {"owner_filter": "user", "filters": {"eval_type": ["dag"]}},
        format="json",
    )
    assert [i["id"] for i in only.json()["result"]["items"]] == [tree_id]
    dataset, _, _ = seed_dataset(organization, workspace, user, ["x"])
    picker = api.get(
        f"/model-hub/develops/{dataset.id}/get_evals_list/",
        {"eval_categories": "user_built"},
    )
    assert picker.status_code == 200, picker.content
    assert tree_id not in str(picker.content)


@pytest.mark.django_db
def test_other_organizations_and_workspaces_are_refused(
    api, organization, workspace, user
):
    own_tree = created(api)
    own_dataset, own_columns, _ = seed_dataset(
        organization, workspace, user, ["refund"]
    )
    other_org = Organization.objects.create(name="dag-foreign-org")
    other_user = User.objects.create_user(
        email="dag-foreign@example.com",
        password="fixture-password",
        name="Foreign",
        organization=other_org,
    )
    foreign_ws = Workspace.objects.create(
        name="dag-foreign-ws",
        organization=other_org,
        created_by=other_user,
        is_default=True,
    )
    sibling_ws = Workspace.objects.create(
        name="dag-sibling-ws",
        organization=organization,
        created_by=user,
        is_default=False,
    )
    stored = EvalTemplate.no_workspace_objects.get(id=own_tree)
    for org, ws, owner in (
        (other_org, foreign_ws, other_user),
        (organization, sibling_ws, user),
    ):
        tree = EvalTemplate.no_workspace_objects.create(
            name=f"dag-foreign-{ws.id}",
            organization=org,
            workspace=ws,
            owner=OwnerChoices.USER.value,
            eval_type="dag",
            config=stored.config,
            model="fixture-judge",
        )
        dataset, columns, rows = seed_dataset(org, ws, owner, ["refund"])
        mapping = {
            "output": str(columns["reply"].id),
            "topic": str(columns["topic"].id),
        }
        own_mapping = {
            "output": str(own_columns["reply"].id),
            "topic": str(own_columns["topic"].id),
        }
        with patch(JUDGE, side_effect=AssertionError("judge called across tenants")):
            assert api.get(tree_url(tree.id)).status_code == 404
            assert (
                api.patch(
                    tree_url(tree.id), {"description": "x"}, format="json"
                ).status_code
                == 404
            )
            assert api.get(tree_url(tree.id, "paths/")).status_code == 404
            assert (
                api.post(
                    tree_url(tree.id, "runs/"),
                    {"dataset_id": str(own_dataset.id), "mapping": own_mapping},
                    format="json",
                ).status_code
                == 404
            )
            assert (
                api.post(
                    tree_url(own_tree, "runs/"),
                    {"dataset_id": str(dataset.id), "mapping": mapping},
                    format="json",
                ).status_code
                == 404
            )
            assert (
                api.post(
                    ADHOC,
                    {
                        "dag": definition(),
                        "output": "x",
                        "template_id": str(tree.id),
                        "values": {"topic": "t"},
                    },
                    format="json",
                ).status_code
                == 404
            )
            assert (
                api.post(
                    ADHOC,
                    {
                        "dag": definition(),
                        "output": "x",
                        "dataset_id": str(dataset.id),
                        "row_id": str(rows[0].id),
                        "mapping": {"topic": str(columns["topic"].id)},
                    },
                    format="json",
                ).status_code
                == 404
            )
        tree.refresh_from_db()
        assert tree.description != "x"
        assert not Evaluation.objects.filter(eval_template=tree).exists()
    foreign_eval = EvalTemplate.no_workspace_objects.create(
        name="dag-foreign-eval",
        organization=other_org,
        workspace=foreign_ws,
        owner=OwnerChoices.USER.value,
        config={"eval_type_id": "CustomPromptEvaluator"},
    )
    dag = definition()
    dag["nodes"][1] = {
        "id": "n2",
        "kind": "eval",
        "template_id": str(foreign_eval.id),
        "answers": [
            {"label": "Pass", "next": "good"},
            {"label": "Fail", "next": "bad"},
        ],
    }
    response = create(api, name="foreign-eval-tree", dag=dag)
    assert response.status_code == 400
    assert (
        f"{foreign_eval.id} is not an eval in this workspace"
        in response.json()["errors"]
    )
