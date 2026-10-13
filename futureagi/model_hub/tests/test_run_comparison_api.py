import uuid

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models.user import OrgApiKey
from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.serializers.run_comparison import RunComparisonResponseSerializer
from model_hub.services.experiment_run_writes import _digest


def _binding():
    return {
        "metric_id": str(uuid.uuid4()),
        "evaluator_version_id": str(uuid.uuid4()),
        "template_id": str(uuid.uuid4()),
        "version_number": 1,
        "binding_config": {"threshold": 0.5},
        "binding_model": "test",
        "prompt_messages": [],
        "config_snapshot": {"temperature": 0},
        "criteria": "quality",
        "model": "test",
        "output_type_normalized": "float",
        "pass_threshold": 0.5,
        "choice_scores": {},
    }


def _run(version, examples, binding, *, items=()):
    identities = [
        {"example_id": str(example.example_id), "trial_number": 1}
        for example in examples
    ]
    identities.sort(key=lambda item: (item["example_id"], item["trial_number"]))
    run = ExperimentRun.no_workspace_objects.create(
        dataset_version=version,
        task_kind="external",
        metadata={
            "_futureagi_run_write": {
                "plan": {
                    "identities": identities,
                    "plan_hash": _digest(identities),
                }
            }
        },
        evaluator_versions=[binding],
    )
    for example, value, trace in items:
        ExperimentRunItem.no_workspace_objects.create(
            run=run,
            example_id=example.example_id,
            trial_number=1,
            status="completed",
            score_refs=[
                {
                    "metric_id": binding["metric_id"],
                    "evaluator_version_id": binding["evaluator_version_id"],
                    "status": "assessed",
                    "value": value,
                }
            ],
            trace_id=trace,
            root_span_id=f"span-{trace}" if trace else None,
        )
    return run


@pytest.fixture
def comparison_api_fixture(organization, workspace, user):
    dataset = Dataset.no_workspace_objects.create(
        name="comparison-api-dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    version = DatasetVersion.no_workspace_objects.create(dataset=dataset)
    examples = [
        DatasetVersionItem.no_workspace_objects.create(
            dataset_version=version, input={"case": number}
        )
        for number in (1, 2)
    ]
    binding = _binding()
    baseline = _run(
        version,
        examples,
        binding,
        items=((examples[0], 0.2, "trace-b0"), (examples[1], 0.8, "trace-b1")),
    )
    candidate = _run(
        version,
        examples,
        binding,
        items=((examples[0], 0.7, "trace-c0"), (examples[1], 0.3, "trace-c1")),
    )
    return {
        "dataset": dataset,
        "version": version,
        "examples": examples,
        "binding": binding,
        "baseline": baseline,
        "candidate": candidate,
        "scope": {
            "baseline_run_id": str(baseline.id),
            "candidate_run_id": str(candidate.id),
            "metric_id": binding["metric_id"],
        },
    }


@pytest.mark.django_db
def test_compare_route_returns_cases_counts_missing_and_traces(
    auth_client, comparison_api_fixture
):
    fixture = comparison_api_fixture
    response = auth_client.post(
        reverse("experiment-run-compare"), fixture["scope"], format="json"
    )

    assert response.status_code == status.HTTP_200_OK
    contract = RunComparisonResponseSerializer(data=response.json())
    assert contract.is_valid(), contract.errors
    result = response.json()["result"]
    assert result["baseline_run_id"] == str(fixture["baseline"].id)
    assert result["candidate_run_id"] == str(fixture["candidate"].id)
    assert result["baseline_counts"] == {
        "expected": 2,
        "recorded": 2,
        "missing": 0,
        "completed": 2,
        "failed": 0,
        "assessed": 2,
    }
    assert result["comparison"]["comparable_count"] == 2
    cases = {case["example_id"]: case for case in result["comparison"]["cases"]}
    assert cases[str(fixture["examples"][0].example_id)]["status"] == "improved"
    assert cases[str(fixture["examples"][1].example_id)]["status"] == "regressed"
    assert result["missing_by_side"] == {"baseline": [], "candidate": []}
    traces = {
        (trace["example_id"], trace["trial_number"]): trace
        for trace in result["trace_refs"]
    }
    assert {trace["baseline"]["trace_id"] for trace in traces.values()} == {
        "trace-b0",
        "trace-b1",
    }
    assert {trace["candidate"]["trace_id"] for trace in traces.values()} == {
        "trace-c0",
        "trace-c1",
    }


@pytest.mark.django_db
def test_compare_route_does_not_expose_other_organization(
    auth_client, comparison_api_fixture, user, workspace
):
    fixture = comparison_api_fixture
    other_org = type(fixture["dataset"].organization).objects.create(name="other-org")
    hidden_dataset = Dataset.no_workspace_objects.create(
        name="other-comparison-dataset",
        organization=other_org,
        workspace=None,
        user=user,
        column_order=[],
        column_config={},
    )
    hidden_version = DatasetVersion.no_workspace_objects.create(dataset=hidden_dataset)
    hidden_example = DatasetVersionItem.no_workspace_objects.create(
        dataset_version=hidden_version, input={"hidden": True}
    )
    hidden_run = _run(
        hidden_version,
        [hidden_example],
        fixture["binding"],
        items=((hidden_example, 1.0, "hidden"),),
    )

    response = auth_client.post(
        reverse("experiment-run-compare"),
        {
            "baseline_run_id": str(fixture["baseline"].id),
            "candidate_run_id": str(hidden_run.id),
            "metric_id": fixture["binding"]["metric_id"],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["status"] is False


@pytest.mark.django_db
def test_compare_route_rejects_unknown_and_non_finite_input(
    auth_client, comparison_api_fixture
):
    fixture = comparison_api_fixture
    url = reverse("experiment-run-compare")
    unknown = auth_client.post(
        url,
        {**fixture["scope"], "unexpected": True},
        format="json",
    )
    assert unknown.status_code == status.HTTP_400_BAD_REQUEST
    assert "unexpected" in unknown.json()["details"]

    non_finite = auth_client.post(
        url,
        {**fixture["scope"], "passing_score": "NaN"},
        format="json",
    )
    assert non_finite.status_code == status.HTTP_400_BAD_REQUEST
    assert "passing_score" in non_finite.json()["details"]


@pytest.mark.django_db
def test_compare_route_rejects_incompatible_runs(auth_client, comparison_api_fixture):
    fixture = comparison_api_fixture
    changed = dict(fixture["binding"])
    changed["config_snapshot"] = {"temperature": 1}
    candidate = _run(
        fixture["version"],
        fixture["examples"],
        changed,
        items=((fixture["examples"][0], 0.7, "changed"),),
    )
    response = auth_client.post(
        reverse("experiment-run-compare"),
        {
            "baseline_run_id": str(fixture["baseline"].id),
            "candidate_run_id": str(candidate.id),
            "metric_id": fixture["binding"]["metric_id"],
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "binding snapshots" in str(response.json())


@pytest.mark.django_db
def test_compare_route_uses_real_api_key_authentication(
    comparison_api_fixture, user, organization, workspace
):
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import WorkspaceMembership
    from tfc.constants.levels import Level

    fixture = comparison_api_fixture
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(level=Level.VIEWER, role="Viewer")
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(level=Level.WORKSPACE_VIEWER, role="workspace_viewer")
    assert user.can_write_to_workspace(workspace) is False
    key = OrgApiKey.no_workspace_objects.create(
        name="comparison-api-test-key",
        organization=organization,
        type="user",
        user=user,
        workspace=workspace,
        api_key="comparison-api-public-key",
        secret_key="comparison-api-secret-key",
    )
    client = APIClient()
    response = client.post(
        reverse("experiment-run-compare"),
        fixture["scope"],
        format="json",
        HTTP_X_API_KEY=key.api_key,
        HTTP_X_SECRET_KEY=key.secret_key,
    )

    assert response.status_code == status.HTTP_200_OK
    contract = RunComparisonResponseSerializer(data=response.json())
    assert contract.is_valid(), contract.errors
    result = response.json()["result"]
    assert result["metric_id"] == fixture["binding"]["metric_id"]
    assert result["baseline_counts"]["assessed"] == 2


@pytest.mark.django_db
def test_compare_route_requires_authentication(api_client, comparison_api_fixture):
    response = api_client.post(
        reverse("experiment-run-compare"),
        comparison_api_fixture["scope"],
        format="json",
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
@pytest.mark.parametrize(
    "field", ["organization_id", "workspace_id", "organization", "workspace"]
)
def test_compare_route_rejects_tenant_fields_in_body(
    auth_client, comparison_api_fixture, field
):
    response = auth_client.post(
        reverse("experiment-run-compare"),
        {**comparison_api_fixture["scope"], field: str(uuid.uuid4())},
        format="json",
    )
    assert response.status_code == 400
    assert field in response.json()["details"]


@pytest.mark.django_db
@pytest.mark.parametrize("threshold", ["NaN", "Infinity", "-Infinity", True, "invalid"])
def test_compare_route_rejects_non_finite_or_boolean_threshold(
    auth_client, comparison_api_fixture, threshold
):
    response = auth_client.post(
        reverse("experiment-run-compare"),
        {**comparison_api_fixture["scope"], "passing_score": threshold},
        format="json",
    )
    assert response.status_code == 400
    assert "passing_score" in response.json()["details"]


@pytest.mark.django_db
@pytest.mark.parametrize("legacy_null", [False, True])
def test_compare_route_preserves_default_workspace_scope(
    auth_client, comparison_api_fixture, user, organization, legacy_null
):
    from accounts.models.workspace import Workspace

    fixture = comparison_api_fixture
    if legacy_null:
        # QuerySet update preserves a legacy null instead of model auto-assignment.
        Dataset.no_workspace_objects.filter(id=fixture["dataset"].id).update(
            workspace_id=None
        )
    else:
        other = Workspace.no_workspace_objects.create(
            name="hidden-workspace",
            organization=organization,
            created_by=user,
            is_default=False,
        )
        Dataset.no_workspace_objects.filter(id=fixture["dataset"].id).update(
            workspace=other
        )
    response = auth_client.post(
        reverse("experiment-run-compare"), fixture["scope"], format="json"
    )
    assert response.status_code == (200 if legacy_null else 404)
    if legacy_null:
        assert response.json()["result"]["comparison"]["comparable_count"] == 2
    else:
        assert response.json()["status"] is False


@pytest.mark.django_db
def test_compare_route_preserves_both_missing_cases(
    auth_client, comparison_api_fixture
):
    fixture = comparison_api_fixture
    ExperimentRunItem.no_workspace_objects.filter(
        run_id__in=[fixture["baseline"].id, fixture["candidate"].id],
        example_id=fixture["examples"][0].example_id,
    ).delete()
    response = auth_client.post(
        reverse("experiment-run-compare"), fixture["scope"], format="json"
    )
    assert response.status_code == 200
    result = response.json()["result"]
    missing = [
        {"example_id": str(fixture["examples"][0].example_id), "trial_number": 1}
    ]
    assert result["missing_by_side"] == {"baseline": missing, "candidate": missing}
    assert result["baseline_counts"]["missing"] == 1
    assert result["baseline_counts"]["recorded"] == 1
    assert len(result["comparison"]["cases"]) == 1


@pytest.mark.django_db
def test_compare_route_reports_size_refusal_without_partial_results(
    auth_client, comparison_api_fixture, monkeypatch
):
    from model_hub.services import persisted_run_comparison

    monkeypatch.setattr(persisted_run_comparison, "_MAX_ITEMS", 1)
    response = auth_client.post(
        reverse("experiment-run-compare"),
        comparison_api_fixture["scope"],
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["status"] is False
    assert "at most 1 identities" in str(response.json())
    assert "comparison" not in response.json()


def test_compare_route_has_typed_checked_in_contract():
    import json
    from pathlib import Path

    contract = json.loads(
        (
            Path(__file__).resolve().parents[3] / "api_contracts/openapi/swagger.json"
        ).read_text()
    )
    operation = contract["paths"]["/model-hub/experiment-runs/compare/"]["post"]
    assert operation["operationId"] == "experiment_runs_compare"
    assert operation["x-runtime-request-validation"] is True
    assert operation["x-runtime-response-validation"] is True
    request_schema = next(
        parameter["schema"]
        for parameter in operation["parameters"]
        if parameter["in"] == "body"
    )
    assert request_schema == {"$ref": "#/definitions/RunComparisonRequest"}
    fields = contract["definitions"]["RunComparisonRequest"]["properties"]
    assert set(fields) == {
        "baseline_run_id",
        "candidate_run_id",
        "metric_id",
        "passing_score",
        "higher_is_better",
    }
    assert operation["responses"]["200"]["schema"] == {
        "$ref": "#/definitions/RunComparisonResponse"
    }
    assert set(contract["definitions"]["RunComparisonResult"]["properties"]) == {
        "baseline_run_id",
        "candidate_run_id",
        "dataset_version_id",
        "metric_id",
        "evaluator_version_id",
        "comparison",
        "baseline_counts",
        "candidate_counts",
        "missing_by_side",
        "trace_refs",
    }
    assert (
        "comparable_count"
        in contract["definitions"]["RunComparisonSummary"]["properties"]
    )


def test_compare_route_does_not_fallback_from_denied_auth_organization():
    from rest_framework.test import APIRequestFactory, force_authenticate

    from accounts.models.organization import Organization
    from accounts.models.user import User
    from model_hub.views.run_comparison import RunComparisonView

    request = APIRequestFactory().post(
        "/",
        {
            "baseline_run_id": str(uuid.uuid4()),
            "candidate_run_id": str(uuid.uuid4()),
            "metric_id": str(uuid.uuid4()),
        },
        format="json",
    )
    # An authenticated user can retain a legacy org FK after membership removal.
    request.organization = None
    user = User(
        email="removed@example.invalid", organization=Organization(id=uuid.uuid4())
    )
    force_authenticate(request, user=user)
    response = RunComparisonView.as_view()(request)
    assert response.status_code == 404
    assert response.data["status"] is False
