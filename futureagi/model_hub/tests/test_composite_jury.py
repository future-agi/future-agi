"""Jury configuration, execution and readback through real composite routes."""

from unittest.mock import patch

import pytest

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from conftest import WorkspaceAwareAPIClient
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import CompositeEvalChild, EvalTemplate
from model_hub.models.evaluation import Evaluation
from model_hub.serializers.composite_jury import JuryReportResponseSerializer
from tfc.middleware.workspace_context import clear_workspace_context


@pytest.fixture
def jury(db, organization, workspace, user):
    parent = EvalTemplate.no_workspace_objects.create(
        name="jury-parent",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        template_type="composite",
        config={},
        aggregation_enabled=True,
        aggregation_function="avg",
        pass_threshold=0.5,
    )
    child = EvalTemplate.no_workspace_objects.create(
        name="jury-child",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "score"},
        output_type_normalized="percentage",
        pass_threshold=0.5,
    )
    CompositeEvalChild.objects.create(parent=parent, child=child, order=0, weight=1)
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    yield parent, child, client
    client.stop_workspace_injection()
    clear_workspace_context()


def config_url(parent):
    return f"/model-hub/eval-templates/{parent.id}/composite/jury/config/"


def report_url(parent):
    return f"/model-hub/eval-templates/{parent.id}/composite/jury/"


def assert_report_contract(response):
    assert response.status_code == 200, response.content
    payload = response.json()
    serializer = JuryReportResponseSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors
    # Validate types above; render the original snapshot without input trimming.
    # DRF represents omitted optional nullable fields as null.
    expected = {
        "result": {
            "aggregate_score": None,
            "aggregate_pass": None,
            **payload["result"],
        }
    }
    assert JuryReportResponseSerializer(payload).data == expected
    return payload["result"]


def test_report_contract_rejects_an_unstructured_result():
    serializer = JuryReportResponseSerializer(data={"result": {"seats": "invalid"}})
    assert not serializer.is_valid()
    assert "seats" in serializer.errors["result"]


@pytest.mark.django_db
def test_empty_report_matches_the_contract(jury):
    parent, _, client = jury
    report = assert_report_contract(client.get(report_url(parent)))
    assert report == {"runs": [], "run_id": None, "seats": []}


def definition(child, **overrides):
    return {
        "seats": [
            {
                "id": "judge-a",
                "kind": "eval",
                "child_id": str(child.id),
                "model": "fixture-model-a",
                "level": "row",
                "weight": 1,
                **overrides,
            }
        ]
    }


def save(client, parent, body):
    response = client.put(config_url(parent), body, format="json")
    assert response.status_code == 200, response.content
    return response.json()["result"]


def execute(client, parent, mapping):
    response = client.post(
        f"/model-hub/eval-templates/{parent.id}/composite/execute/",
        {"mapping": mapping},
        format="json",
    )
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert_report_contract(
        client.get(report_url(parent), {"run_id": result["evaluation_id"]})
    )
    return result


@pytest.mark.django_db
def test_round_trip_two_seats_for_same_child(jury):
    parent, child, client = jury
    body = definition(child)
    body["seats"].append(
        {**body["seats"][0], "id": "judge-b", "model": "fixture-model-b"}
    )
    result = save(client, parent, body)
    assert result["seats"] == body["seats"]
    response = client.get(config_url(parent))
    assert response.status_code == 200
    assert response.json()["result"] == result
    parent.refresh_from_db()
    assert parent.config["jury"] == result


@pytest.mark.django_db
@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body.update(seats=[]),
        lambda body: body["seats"].append(body["seats"][0].copy()),
        lambda body: body["seats"][0].update(weight=-1),
        lambda body: body["seats"][0].update(level="unknown"),
        lambda body: body["seats"][0].update(level="step", step_kind="unknown"),
        lambda body: body["seats"][0].update(reduce="execute_python"),
        lambda body: body["seats"][0].update(url="https://example.com"),
    ],
)
def test_invalid_definitions_do_not_write(jury, mutate):
    parent, child, client = jury
    body = definition(child)
    mutate(body)
    response = client.put(config_url(parent), body, format="json")
    assert response.status_code == 400, response.content
    parent.refresh_from_db()
    assert parent.config == {}


@pytest.mark.django_db
def test_foreign_org_and_workspace_ids_are_refused(jury):
    parent, child, client = jury
    other_org = Organization.objects.create(name="foreign-jury-org")
    other_user = User.objects.create_user(
        email="foreign-jury@example.com",
        password="fixture-password",
        name="Foreign jury",
        organization=other_org,
    )
    foreign_workspace = Workspace.objects.create(
        name="foreign-jury-workspace",
        organization=other_org,
        created_by=other_user,
        is_default=True,
    )
    same_org_workspace = Workspace.objects.create(
        name="other-jury-workspace",
        organization=parent.organization,
        created_by=client.handler._force_user,
        is_default=False,
    )
    for org, ws in [
        (other_org, foreign_workspace),
        (parent.organization, same_org_workspace),
    ]:
        foreign = EvalTemplate.no_workspace_objects.create(
            name=f"foreign-{ws.id}",
            organization=org,
            workspace=ws,
            owner=OwnerChoices.USER.value,
            template_type="composite",
            config={},
        )
        for method in ("get", "put"):
            response = getattr(client, method)(
                config_url(foreign), definition(child), format="json"
            )
            assert response.status_code == 404, response.content
        assert client.get(report_url(foreign)).status_code == 404
        foreign_child = EvalTemplate.no_workspace_objects.create(
            name=f"foreign-child-{ws.id}",
            organization=org,
            workspace=ws,
            owner=OwnerChoices.USER.value,
            config={},
        )
        response = client.put(
            config_url(parent), definition(foreign_child), format="json"
        )
        assert response.status_code == 400, response.content


@pytest.mark.django_db
def test_two_seats_score_independently_and_report_is_read_only(jury):
    parent, child, client = jury
    body = definition(child)
    body["seats"].append(
        {**body["seats"][0], "id": "judge-b", "model": "fixture-model-b"}
    )
    save(client, parent, body)
    with patch(
        "model_hub.views.utils.evals.run_eval_func",
        side_effect=[
            {"output": 1.0, "reason": "first verdict"},
            {"output": 0.0, "reason": "second verdict"},
        ],
    ) as judge:
        result = execute(client, parent, {"output": "sample"})
    assert judge.call_count == 2
    assert [call.kwargs["model"] for call in judge.call_args_list] == [
        "fixture-model-a",
        "fixture-model-b",
    ]
    assert result["aggregate_score"] == 0.5
    evaluation = Evaluation.objects.get(id=result["evaluation_id"])
    assert [seat["score"] for seat in evaluation.data["jury"]["seats"]] == [1.0, 0.0]
    with patch(
        "model_hub.views.utils.evals.run_eval_func",
        side_effect=AssertionError("read called a judge"),
    ):
        response = client.get(report_url(parent), {"run_id": result["evaluation_id"]})
    assert response.status_code == 200, response.content
    report = response.json()["result"]
    assert report["run_id"] == result["evaluation_id"]
    assert report["seats"][0]["id"] == "judge-a"
    assert report["disagreement"] is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("reducer", "expected"), [("all_pass", 0.0), ("mean", 0.5), ("min", 0.0)]
)
def test_steps_reduce_only_matching_kind(jury, reducer, expected):
    parent, child, client = jury
    save(
        client, parent, definition(child, level="step", step_kind="llm", reduce=reducer)
    )
    with patch(
        "model_hub.views.utils.evals.run_eval_func",
        side_effect=[
            {"output": 1.0},
            {"output": 0.0},
        ],
    ) as judge:
        result = execute(
            client,
            parent,
            {
                "steps": [
                    {"kind": "llm", "mapping": {"output": "one"}},
                    {"kind": "tool", "mapping": {"output": "ignored"}},
                    {"kind": "llm", "mapping": {"output": "two"}},
                ]
            },
        )
    assert judge.call_count == 2
    assert [call.args[1]["output"] for call in judge.call_args_list] == ["one", "two"]
    assert result["aggregate_score"] == expected


@pytest.mark.django_db
def test_missing_steps_and_checks_cannot_silently_pass(jury):
    parent, child, client = jury
    body = definition(child, level="step", step_kind="llm", reduce="all_pass")
    body["seats"].append(
        {"id": "check-safe", "kind": "check", "check_key": "safe", "weight": 1}
    )
    save(client, parent, body)
    with patch("model_hub.views.utils.evals.run_eval_func") as judge:
        result = execute(client, parent, {"output": "no steps or checks"})
    judge.assert_not_called()
    assert result["aggregate_score"] is None
    assert result["aggregate_pass"] is None
    assert result["failed_children"] == 2


@pytest.mark.django_db
def test_check_reads_boolean_evidence_without_judge_call(jury):
    parent, child, client = jury
    save(
        client,
        parent,
        {
            "seats": [
                {"id": "check-safe", "kind": "check", "check_key": "safe", "weight": 1}
            ]
        },
    )
    with patch("model_hub.views.utils.evals.run_eval_func") as judge:
        result = execute(client, parent, {"checks": [{"key": "safe", "passed": False}]})
    judge.assert_not_called()
    assert result["aggregate_score"] == 0.0
    assert result["aggregate_pass"] is False
    report = client.get(report_url(parent), {"run_id": result["evaluation_id"]}).json()[
        "result"
    ]
    assert report["seats"][0]["check_key"] == "safe"
