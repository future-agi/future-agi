import uuid

import pytest
from django.core.exceptions import ValidationError

from model_hub.models.dataset_versions import DatasetVersion
from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import (
    ExperimentRun,
    ExperimentRunItem,
    ExperimentsTable,
)
from model_hub.serializers.experiments import (
    ExperimentRunItemSerializer,
    ExperimentRunSerializer,
)
from model_hub.utils.workspace_scope import scoped_dataset_version_queryset
from tfc.middleware.workspace_context import clear_workspace_context


def test_experiment_run_contract_serializes_dataset_version_relation():
    dataset_version_id = uuid.uuid4()
    dataset_version = DatasetVersion(id=dataset_version_id)
    run = ExperimentRun(
        dataset_version=dataset_version,
        task_kind=ExperimentRun.TaskKind.EXTERNAL,
        metadata={"source": "ci"},
        evaluator_versions=[{"id": "eval-v1"}],
    )

    data = ExperimentRunSerializer(run).data

    assert data["dataset_version_id"] == dataset_version_id
    assert data["task_kind"] == "external"
    assert data["status"] == ExperimentRun.Status.DRAFT
    assert data["metadata"] == {"source": "ci"}
    assert data["evaluator_versions"] == [{"id": "eval-v1"}]


def test_experiment_run_item_contract_preserves_trace_and_trial_fields():
    run = ExperimentRun(dataset_version=DatasetVersion(id=uuid.uuid4()))
    item = ExperimentRunItem(
        run=run,
        example_id=uuid.uuid4(),
        trial_number=2,
        status=ExperimentRunItem.Status.COMPLETED,
        output={"answer": "ok"},
        trace_id="trace-123",
        root_span_id="span-456",
        score_refs=[{"evaluator_version_id": "eval-v1", "score": 1.0}],
    )

    data = ExperimentRunItemSerializer(item).data

    # DRF's relational primary-key field keeps UUID values typed until the
    # renderer serializes the response body.
    assert data["run_id"] == run.id
    assert data["example_id"] == str(item.example_id)
    assert data["trial_number"] == 2
    assert data["status"] == "completed"
    assert data["trace_id"] == "trace-123"
    assert data["root_span_id"] == "span-456"
    assert data["score_refs"] == [{"evaluator_version_id": "eval-v1", "score": 1.0}]


def test_experiment_run_item_has_one_result_per_example_and_trial():
    constraints = {
        tuple(constraint.fields) for constraint in ExperimentRunItem._meta.constraints
    }

    assert ("run", "example_id", "trial_number") in constraints


def _dataset(*, organization_id, workspace_id):
    return Dataset(
        id=uuid.uuid4(),
        name="dataset",
        organization_id=organization_id,
        workspace_id=workspace_id,
    )


def test_experiment_run_rejects_version_from_another_dataset():
    organization_id, workspace_id = uuid.uuid4(), uuid.uuid4()
    first = _dataset(organization_id=organization_id, workspace_id=workspace_id)
    second = _dataset(organization_id=organization_id, workspace_id=workspace_id)
    experiment = ExperimentsTable(dataset=first, name="experiment")
    run = ExperimentRun(
        experiment=experiment,
        dataset_version=DatasetVersion(
            dataset=second,
            organization_id=organization_id,
            workspace_id=workspace_id,
        ),
    )

    with pytest.raises(ValidationError, match="experiment dataset"):
        run.clean()

    serializer = ExperimentRunSerializer(
        instance=run,
        data={"status": "running"},
        partial=True,
    )
    assert not serializer.is_valid()
    assert "dataset_version" in serializer.errors


def test_experiment_run_accepts_version_from_its_dataset():
    organization_id, workspace_id = uuid.uuid4(), uuid.uuid4()
    dataset = _dataset(organization_id=organization_id, workspace_id=workspace_id)
    version = DatasetVersion(
        dataset=dataset,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )
    version.clean()
    run = ExperimentRun(
        experiment=ExperimentsTable(dataset=dataset, name="experiment"),
        dataset_version=version,
    )
    serializer = ExperimentRunSerializer(
        instance=run,
        data={"status": "running", "metadata": {}, "evaluator_versions": []},
        partial=True,
    )
    assert serializer.is_valid(), serializer.errors


def test_dataset_versions_require_tenant_context():
    clear_workspace_context()

    assert list(scoped_dataset_version_queryset()) == []


def test_run_serializer_rejects_invalid_json_shapes():
    run = ExperimentRun(dataset_version=DatasetVersion(id=uuid.uuid4()))
    serializer = ExperimentRunSerializer(
        instance=run,
        data={
            "metadata": [],
            "model_config": "invalid",
            "evaluator_versions": {},
        },
        partial=True,
    )

    assert not serializer.is_valid()
    assert set(serializer.errors) == {
        "metadata",
        "model_config",
        "evaluator_versions",
    }


def test_run_serializer_rejects_completion_before_start():
    run = ExperimentRun(dataset_version=DatasetVersion(id=uuid.uuid4()))
    serializer = ExperimentRunSerializer(
        instance=run,
        data={
            "started_at": "2026-10-12T23:00:00Z",
            "completed_at": "2026-10-11T23:00:00Z",
        },
        partial=True,
    )

    assert not serializer.is_valid()
    assert "completed_at" in serializer.errors


def test_run_item_serializer_rejects_invalid_metric_values():
    item = ExperimentRunItem(
        run=ExperimentRun(dataset_version=DatasetVersion(id=uuid.uuid4())),
        example_id=uuid.uuid4(),
    )
    serializer = ExperimentRunItemSerializer(
        instance=item,
        data={
            "trial_number": 0,
            "latency_ms": -1,
            "cost": -1,
            "score_refs": "not-a-list",
        },
        partial=True,
    )

    assert not serializer.is_valid()
    assert set(serializer.errors) == {
        "trial_number",
        "latency_ms",
        "cost",
        "score_refs",
    }


@pytest.mark.parametrize("field", ["latency_ms", "cost"])
def test_run_item_unknown_metrics_remain_nullable(field):
    serializer = ExperimentRunItemSerializer()

    assert serializer.fields[field].run_validation(None) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("trial_number", 0),
        ("latency_ms", -1),
        ("cost", -1),
        ("score_refs", "not-a-list"),
    ],
)
def test_run_item_model_rejects_invalid_metrics(field, value):
    item = ExperimentRunItem(example_id=uuid.uuid4())
    setattr(item, field, value)

    with pytest.raises(ValidationError, match=field):
        item.clean()
