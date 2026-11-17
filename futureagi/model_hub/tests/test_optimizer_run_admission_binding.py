"""Admission ownership regressions with the real worker binding recheck."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.optimize_dataset import OptimizeDataset
from model_hub.serializers.dataset_optimization import (
    DatasetOptimizationCreateSerializer,
)
from tfc.middleware.workspace_context import workspace_context


@pytest.fixture
def admission_request(organization, workspace, user):
    assert workspace.is_default, "SETUP_AQ-XWS-OCB: request workspace is default"
    assert workspace.organization_id == organization.id, (
        "SETUP_AQ-XWS-OCB: request ownership agrees"
    )
    return SimpleNamespace(
        organization=organization,
        workspace=workspace,
        user=user,
    )


def _column(organization, workspace):
    # Create legacy rows with no ambient workspace rather than mocking signals
    # or rewriting ownership after save. Keep production model validation active.
    with workspace_context(workspace=workspace, organization=organization):
        dataset = Dataset.objects.create(
            name="Optimizer admission dataset",
            organization=organization,
            workspace=workspace,
        )
        column = Column.objects.create(
            name="Optimizer output",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        dataset.refresh_from_db()
    assert (dataset.organization_id, dataset.workspace_id) == (
        organization.id,
        workspace.id if workspace is not None else None,
    ), "SETUP_AQ-XWS-OCB: dataset ownership persisted without ambient substitution"
    assert column.dataset_id == dataset.id, (
        "SETUP_AQ-XWS-OCB: column belongs to the fixture dataset"
    )
    return column


def _serializer(request, column_id):
    return DatasetOptimizationCreateSerializer(
        data={
            "name": "Optimizer admission binding",
            "column_id": str(column_id),
            "optimizer_algorithm": OptimizeDataset.OptimizerAlgorithm.RANDOM_SEARCH,
            "optimizer_config": {"num_variations": 1},
            "user_eval_template_ids": [],
        },
        context={"request": request},
    )


def _assert_worker_accepts(run, column):
    from tfc.temporal.dataset_optimization import activities

    verify_run_binding = getattr(activities, "_verify_run_binding", None)
    assert callable(verify_run_binding), (
        "AQ-XWS-OCB: worker must expose the persisted binding recheck"
    )
    # Reload without request context or the serializer instance's transient flag.
    with workspace_context(workspace=None, organization=None, user=None):
        worker_run = OptimizeDataset.objects.select_related("column__dataset").get(
            id=run.id
        )
        try:
            dataset = verify_run_binding(worker_run)
        except ValueError:
            pytest.fail(
                "AQ-XWS-OCB: admitted run must pass the worker binding recheck",
                pytrace=False,
            )
    assert dataset.id == column.dataset_id, (
        "AQ-XWS-OCB: worker recheck must return the admitted dataset"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("legacy", [False, True], ids=["same-workspace", "legacy-null"])
def test_default_request_persists_exact_dataset_ownership(admission_request, legacy):
    request = admission_request
    column = _column(request.organization, None if legacy else request.workspace)
    with workspace_context(
        workspace=request.workspace,
        organization=request.organization,
        user=request.user,
    ):
        serializer = _serializer(request, column.id)
        assert serializer.is_valid(), (
            "SETUP_AQ-XWS-OCB: default request admits its dataset column"
        )
        run = serializer.save()
        run.refresh_from_db()
    assert (run.organization_id, run.workspace_id) == (
        column.dataset.organization_id,
        column.dataset.workspace_id,
    ), "AQ-XWS-OCB: default request must persist exact dataset ownership including NULL"
    _assert_worker_accepts(run, column)


@pytest.mark.django_db
@pytest.mark.parametrize("legacy", [False, True], ids=["same-workspace", "legacy-null"])
def test_admission_binding_does_not_require_ambient_context(admission_request, legacy):
    request = admission_request
    column = _column(request.organization, None if legacy else request.workspace)
    with workspace_context(workspace=None, organization=None, user=None):
        serializer = _serializer(request, column.id)
        assert serializer.is_valid(), (
            "SETUP_AQ-XWS-OCB: explicit request scope admits the fixture column"
        )
        run = serializer.save()
        run.refresh_from_db()
    assert (run.organization_id, run.workspace_id) == (
        column.dataset.organization_id,
        column.dataset.workspace_id,
    ), "AQ-XWS-OCB: run ownership must come from the dataset without ambient context"
    _assert_worker_accepts(run, column)


@pytest.mark.django_db
@pytest.mark.parametrize("foreign_kind", ["workspace", "organization"])
def test_foreign_and_missing_columns_have_identical_refusal(
    admission_request, foreign_kind
):
    request = admission_request
    with workspace_context(workspace=None, organization=None, user=None):
        foreign_org = (
            Organization.objects.create(name="Foreign optimizer organization")
            if foreign_kind == "organization"
            else request.organization
        )
        foreign_workspace = Workspace.objects.create(
            name="Foreign optimizer workspace",
            organization=foreign_org,
            is_default=foreign_kind == "organization",
            created_by=request.user,
        )
    foreign_column = _column(foreign_org, foreign_workspace)
    local_column = _column(request.organization, request.workspace)
    missing_id = uuid4()
    with workspace_context(workspace=None, organization=None, user=None):
        assert not Column.objects.filter(id=missing_id).exists(), (
            "SETUP_AQ-XWS-OCB: missing column UUID is absent"
        )
    with workspace_context(
        workspace=request.workspace,
        organization=request.organization,
        user=request.user,
    ):
        allowed = _serializer(request, local_column.id)
        foreign = _serializer(request, foreign_column.id)
        missing = _serializer(request, missing_id)
        assert allowed.is_valid(), "AQ-XWS-OCB: same-scope column remains admitted"
        assert not foreign.is_valid(), "AQ-XWS-OCB: foreign column must be refused"
        assert not missing.is_valid(), "AQ-XWS-OCB: missing column must be refused"
    assert foreign.errors == missing.errors, (
        "AQ-XWS-OCB: foreign and missing column refusals must have identical shape"
    )
    assert set(missing.errors) == {"column_id"}, (
        "AQ-XWS-OCB: refusal must retain the column validation error shape"
    )
    assert [str(error) for error in missing.errors["column_id"]] == [
        "Column with this ID does not exist."
    ], "AQ-XWS-OCB: refusal must retain the existing not-found message"
    assert [error.code for error in missing.errors["column_id"]] == ["invalid"], (
        "AQ-XWS-OCB: refusal must retain the existing validation error code"
    )
