"""Classification summaries use frozen labels and stored scores, never eval calls."""

import json
import uuid

import pytest
from django.urls import reverse

from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Cell, Column, Dataset
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.serializers.results_analysis import AnalysisValueSerializer
from model_hub.services.experiment_run_creation import create_experiment_run
from model_hub.services.experiment_run_writes import _digest
from model_hub.services.external_metrics import (
    ExternalMetricDefinition,
    create_or_reuse_external_metric,
)
from model_hub.services.results_analysis import build_analysis
from model_hub.tests import test_results_expected_on_earlier_version as earlier


@pytest.fixture
def classification_fixture(organization, workspace, user):
    def seed(rows):
        dataset = Dataset.no_workspace_objects.create(
            name="Classification",
            organization=organization,
            workspace=workspace,
            user=user,
            column_order=[],
            column_config={},
        )
        column = Column.no_workspace_objects.create(
            dataset=dataset,
            name="Expected label",
            data_type="text",
            source="OTHERS",
        )
        snapshot = Dataset.no_workspace_objects.create(
            name="Frozen classification",
            organization=organization,
            workspace=workspace,
            user=user,
            column_order=[],
            column_config={},
        )
        captured = Column.no_workspace_objects.create(
            dataset=snapshot,
            name=column.name,
            data_type="text",
            source="OTHERS",
        )
        version = DatasetVersion.no_workspace_objects.create(
            dataset=dataset,
            metadata={
                "capture": "native-snapshot-v1",
                "columns": {str(column.id): str(captured.id)},
            },
        )
        examples = [
            DatasetVersionItem.no_workspace_objects.create(
                dataset_version=version,
                input={
                    "values": {str(column.id): row["expected"]}
                    if "expected" in row
                    else {}
                },
            )
            for row in rows
        ]
        metrics = [
            create_or_reuse_external_metric(
                dataset=dataset,
                user=user,
                definition=ExternalMetricDefinition(
                    name=name,
                    output_type=output,
                    source_hash=source * 64,
                    higher_is_better=True,
                    pass_threshold=0.5,
                ),
            ).metric
            for name, output, source in (
                ("Binary", "pass_fail", "a"),
                ("Numeric", "numeric", "b"),
            )
        ]
        run = create_experiment_run(
            run_id=uuid.uuid4(),
            dataset_version=version,
            created_by=user,
            task_kind="external",
            evaluator_metrics=metrics,
            expected_items=[(example.example_id, 1) for example in examples],
        )
        for example, row in zip(examples, rows, strict=False):
            if not row.get("recorded", True):
                continue
            ExperimentRunItem.no_workspace_objects.create(
                run=run,
                example_id=example.example_id,
                trial_number=1,
                status=row.get("item_status", "completed"),
                error=row.get("error"),
                score_refs=[
                    {
                        "metric_id": pin["metric_id"],
                        "evaluator_version_id": pin["evaluator_version_id"],
                        "status": row.get("score_status", "assessed"),
                        "value": row["score"],
                    }
                    for pin in run.evaluator_versions
                ]
                if "score" in row
                else [],
            )
        selection = {
            "example_ids": [str(example.example_id) for example in examples],
            "examples_total": len(examples),
            "complete": True,
        }
        query = {"baseline": "none", "expected_column_id": column.id}
        return dataset, column, run, selection, query

    return seed


def _result(fixture):
    dataset, _, run, selection, query = fixture
    return build_analysis(dataset, [run], selection, query)


def _binary(result):
    return next(metric for metric in result["metrics"] if metric["name"] == "Binary")


def _summary(fixture):
    return _binary(_result(fixture))["per_run"][0]["classification_summary"]


def _request(auth_client, fixture, **extra):
    dataset, column, run, _, _ = fixture
    return auth_client.get(
        reverse("dataset-results-analysis", kwargs={"dataset_id": dataset.id}),
        {
            "runs": str(run.id),
            "baseline": "none",
            "expected_column_id": str(column.id),
            **extra,
        },
    )


@pytest.mark.django_db
def test_exact_classification_metrics_in_existing_summary(
    auth_client,
    classification_fixture,
):
    fixture = classification_fixture(
        [
            {"expected": True, "score": 1},
            {"expected": " true ", "score": 1},
            {"expected": False, "score": 1},
            {"expected": "0", "score": 0},
            {"expected": "pass", "score": 0},
            {"expected": 1, "score": 0},
        ]
    )
    before = (
        ExperimentRun.no_workspace_objects.count(),
        ExperimentRunItem.no_workspace_objects.count(),
        DatasetVersionItem.no_workspace_objects.count(),
    )
    response = _request(auth_client, fixture)
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert _binary(result)["per_run"][0]["classification_summary"] == {
        "expected_column_id": str(fixture[1].id),
        "tp": 2,
        "fp": 1,
        "tn": 1,
        "fn": 2,
        "n_used": 6,
        "n_skipped": 0,
        "precision": 2 / 3,
        "recall": 0.5,
        "f1": 4 / 7,
        "accuracy": 0.5,
    }
    numeric = next(m for m in result["metrics"] if m["name"] == "Numeric")
    assert "classification_summary" not in numeric["per_run"][0]
    assert _binary(result)["per_run"][0]["value"] == 0.5
    assert before == (
        ExperimentRun.no_workspace_objects.count(),
        ExperimentRunItem.no_workspace_objects.count(),
        DatasetVersionItem.no_workspace_objects.count(),
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "rows,counts,rates",
    [
        (
            [{"expected": False, "score": 0}, {"expected": "FAIL", "score": 0}],
            (0, 0, 2, 0, 2, 0),
            (None, None, None, 1.0),
        ),
        (
            [{"expected": True, "score": 0}, {"expected": "1", "score": 0}],
            (0, 0, 0, 2, 2, 0),
            (None, 0.0, 0.0, 0.0),
        ),
        (
            [{"expected": False, "score": 1}, {"expected": "false", "score": 1}],
            (0, 2, 0, 0, 2, 0),
            (0.0, None, 0.0, 0.0),
        ),
        (
            [{"score": 1}, {"expected": None, "score": 0}],
            (0, 0, 0, 0, 0, 2),
            (None, None, None, None),
        ),
    ],
)
def test_undefined_ratios_are_null(classification_fixture, rows, counts, rates):
    fixture = classification_fixture(rows)
    assert _summary(fixture) == {
        "expected_column_id": str(fixture[1].id),
        **dict(
            zip(("tp", "fp", "tn", "fn", "n_used", "n_skipped"), counts, strict=False)
        ),
        **dict(zip(("precision", "recall", "f1", "accuracy"), rates, strict=False)),
    }


@pytest.mark.django_db
def test_unusable_and_missing_trials_are_skipped(classification_fixture):
    fixture = classification_fixture(
        [
            {"expected": True, "score": 1},
            {"expected": False, "score": 0},
            {"score": 1},
            {"expected": None, "score": 1},
            {"expected": "", "score": 1},
            {"expected": "unknown", "score": 1},
            {"expected": 2, "score": 1},
            {"expected": [], "score": 1},
            {"expected": True, "score": 0.5},
            {"expected": True, "score": "1"},
            {"expected": True, "score": 1, "score_status": "not_assessed"},
            {"expected": True, "score": 1, "score_status": "invalid"},
            {"expected": True, "score": 1, "item_status": "failed"},
            {"expected": True, "score": 1, "error": "task failed"},
            {"expected": True, "recorded": False},
            {"expected": True},
        ]
    )
    assert _summary(fixture) == {
        "expected_column_id": str(fixture[1].id),
        "tp": 1,
        "fp": 0,
        "tn": 1,
        "fn": 0,
        "n_used": 2,
        "n_skipped": 14,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
        "accuracy": 1.0,
    }


@pytest.mark.django_db
@pytest.mark.parametrize("output", ["pass_fail", "boolean", "bool", "binary"])
def test_binary_output_aliases_use_stored_zero_and_one(
    classification_fixture,
    output,
):
    fixture = classification_fixture([{"expected": True, "score": 1}])
    run = fixture[2]
    for pin in run.evaluator_versions:
        if pin["output_type_normalized"] == "pass_fail":
            pin["output_type_normalized"] = output
            pin.pop("config_snapshot", None)
    assert _summary(fixture) == {
        "expected_column_id": str(fixture[1].id),
        "tp": 1,
        "fp": 0,
        "tn": 0,
        "fn": 0,
        "n_used": 1,
        "n_skipped": 0,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
        "accuracy": 1.0,
    }


@pytest.mark.django_db
def test_each_run_uses_its_own_stored_scores(classification_fixture):
    fixture = classification_fixture(
        [
            {"expected": True, "score": 1},
            {"expected": False, "score": 0},
        ]
    )
    dataset, column, run, selection, query = fixture
    other = ExperimentRun.no_workspace_objects.create(
        dataset_version=run.dataset_version,
        task_kind=run.task_kind,
        evaluator_versions=run.evaluator_versions,
        metadata=run.metadata,
    )
    for item in ExperimentRunItem.no_workspace_objects.filter(run=run):
        refs = [{**ref, "value": 1 - ref["value"]} for ref in item.score_refs]
        ExperimentRunItem.no_workspace_objects.create(
            run=other,
            example_id=item.example_id,
            trial_number=1,
            status="completed",
            score_refs=refs,
        )
    result = build_analysis(dataset, [run, other], selection, query)
    summaries = [v["classification_summary"] for v in _binary(result)["per_run"]]
    assert summaries == [
        {
            "expected_column_id": str(column.id),
            "tp": 1,
            "fp": 0,
            "tn": 1,
            "fn": 0,
            "n_used": 2,
            "n_skipped": 0,
            "precision": 1.0,
            "recall": 1.0,
            "f1": 1.0,
            "accuracy": 1.0,
        },
        {
            "expected_column_id": str(column.id),
            "tp": 0,
            "fp": 1,
            "tn": 0,
            "fn": 1,
            "n_used": 2,
            "n_skipped": 0,
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "accuracy": 0.0,
        },
    ]


@pytest.mark.django_db
def test_full_filtered_population_is_used(auth_client, classification_fixture):
    fixture = classification_fixture(
        [
            {"expected": True, "score": 1},
            {"expected": True, "score": 0},
        ]
    )
    response = _request(
        auth_client,
        fixture,
        filters=json.dumps(
            [
                {"type": "rows", "ids": fixture[3]["example_ids"][1:]},
            ]
        ),
    )
    assert response.status_code == 200, response.content
    summary = _binary(response.json()["result"])["per_run"][0]["classification_summary"]
    assert summary == {
        "expected_column_id": str(fixture[1].id),
        "tp": 0,
        "fp": 0,
        "tn": 0,
        "fn": 1,
        "n_used": 1,
        "n_skipped": 0,
        "precision": None,
        "recall": 0.0,
        "f1": 0.0,
        "accuracy": 0.0,
    }


@pytest.mark.django_db
@pytest.mark.parametrize("scope", ["foreign", "uncaptured", "unknown"])
def test_expected_column_must_belong_to_dataset_version(
    auth_client,
    classification_fixture,
    scope,
):
    fixture = classification_fixture([{"expected": True, "score": 1}])
    if scope == "foreign":
        column_id = classification_fixture([{"expected": False, "score": 0}])[1].id
    elif scope == "uncaptured":
        column_id = Column.no_workspace_objects.create(
            dataset=fixture[0],
            name="Later label",
            data_type="text",
            source="OTHERS",
        ).id
    else:
        column_id = uuid.uuid4()
    response = _request(auth_client, fixture, expected_column_id=str(column_id))
    assert response.status_code == 400, response.content
    message = "Expected column must belong to the run's dataset version."
    assert response.json()["details"] == {"expected_column_id": [message]}
    assert response.json()["message"] == f"expected_column_id: {message}"


@pytest.mark.django_db
def test_snapshot_column_alias_reads_the_frozen_value(
    auth_client,
    classification_fixture,
):
    fixture = classification_fixture([{"expected": True, "score": 1}])
    column = str(fixture[1].id)
    captured = fixture[2].dataset_version.metadata["columns"][column]
    response = _request(auth_client, fixture, expected_column_id=captured)
    assert response.status_code == 200, response.content
    summary = _binary(response.json()["result"])["per_run"][0]["classification_summary"]
    assert summary == {
        "expected_column_id": captured,
        "tp": 1,
        "fp": 0,
        "tn": 0,
        "fn": 0,
        "n_used": 1,
        "n_skipped": 0,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
        "accuracy": 1.0,
    }


@pytest.mark.django_db
@pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")
def test_earlier_version_expected_alias_matches_source_column(
    auth_client,
    organization,
    workspace,
    user,
):
    dataset, _, column = earlier.two_column_dataset(organization, workspace, user)
    Cell.no_workspace_objects.filter(column=column).update(value="true")
    first = earlier.experiment_on(dataset, column, user, "Saved version")
    version = earlier.saved_version(first)
    Cell.no_workspace_objects.filter(column=column).update(value="false")
    later = Column.no_workspace_objects.create(
        dataset=dataset,
        name="Later label",
        data_type="text",
        source="OTHERS",
    )
    second = earlier.experiment_on(
        dataset,
        column,
        user,
        "Earlier version",
        rows_from_version=version,
    )
    unrelated = earlier.experiment_on(
        dataset,
        column,
        user,
        "Unselected experiment",
        rows_from_version=version,
    )
    assert earlier.saved_version(second).id == version.id
    assert str(second.column_id) not in version.metadata["columns"].values()
    assert str(later.id) not in version.metadata["columns"]
    # Neither live source cells nor the selected experiment's cells supply labels.
    Cell.no_workspace_objects.filter(column_id=second.column_id).update(value="false")
    metric = create_or_reuse_external_metric(
        dataset=dataset,
        user=user,
        definition=ExternalMetricDefinition(
            name="Binary",
            output_type="pass_fail",
            source_hash="c" * 64,
            higher_is_better=True,
            pass_threshold=0.5,
        ),
    ).metric
    examples = list(
        DatasetVersionItem.no_workspace_objects.filter(
            dataset_version=version,
        ).order_by("id")
    )
    run = create_experiment_run(
        run_id=uuid.uuid4(),
        dataset_version=version,
        created_by=user,
        experiment=second,
        task_kind="external",
        evaluator_metrics=[metric],
        expected_items=[(example.example_id, 1) for example in examples],
    )
    pin = run.evaluator_versions[0]
    for index, example in enumerate(examples):
        ExperimentRunItem.no_workspace_objects.create(
            run=run,
            example_id=example.example_id,
            trial_number=1,
            status="completed",
            score_refs=[
                {
                    "metric_id": pin["metric_id"],
                    "evaluator_version_id": pin["evaluator_version_id"],
                    "status": "assessed",
                    "value": int(index == 0),
                }
            ],
        )
    fixture = dataset, column, run, None, None
    metrics = {
        "tp": 1,
        "fp": 0,
        "tn": 0,
        "fn": 2,
        "n_used": 3,
        "n_skipped": 0,
        "precision": 1.0,
        "recall": 1 / 3,
        "f1": 0.5,
        "accuracy": 1 / 3,
    }
    for column_id in (column.id, second.column_id):
        response = _request(auth_client, fixture, expected_column_id=str(column_id))
        assert response.status_code == 200, response.content
        summary = _binary(response.json()["result"])["per_run"][0]
        assert summary["classification_summary"] == {
            "expected_column_id": str(column_id),
            **metrics,
        }
    later_alias = second.native_snapshot_binding.column_map[str(later.id)]
    for column_id in (later.id, later_alias, unrelated.column_id):
        response = _request(auth_client, fixture, expected_column_id=str(column_id))
        assert response.status_code == 400, response.content
        message = "Expected column must belong to the run's dataset version."
        assert response.json()["details"] == {"expected_column_id": [message]}
        assert response.json()["message"] == f"expected_column_id: {message}"


@pytest.mark.django_db
def test_repeated_trials_have_explicit_denominators(classification_fixture):
    fixture = classification_fixture([{"expected": True, "score": 1}])
    run = fixture[2]
    plan = run.metadata["_futureagi_run_write"]["plan"]
    plan["identities"].append({**plan["identities"][0], "trial_number": 2})
    plan["plan_hash"] = _digest(plan["identities"])
    item = ExperimentRunItem.no_workspace_objects.get(run=run)
    ExperimentRunItem.no_workspace_objects.create(
        run=run,
        example_id=item.example_id,
        trial_number=2,
        status="completed",
        score_refs=[{**ref, "value": 0} for ref in item.score_refs],
    )
    assert _summary(fixture) == {
        "expected_column_id": str(fixture[1].id),
        "tp": 1,
        "fp": 0,
        "tn": 0,
        "fn": 1,
        "n_used": 2,
        "n_skipped": 0,
        "precision": 1.0,
        "recall": 0.5,
        "f1": 2 / 3,
        "accuracy": 0.5,
    }


@pytest.mark.django_db
def test_version_without_captured_column_is_rejected(
    auth_client,
    classification_fixture,
):
    fixture = classification_fixture([{"expected": True, "score": 1}])
    version = fixture[2].dataset_version
    version.metadata = {}
    version.save(update_fields=["metadata"])
    response = _request(auth_client, fixture)
    assert response.status_code == 400, response.content
    assert response.json()["details"] == {
        "expected_column_id": [
            "Expected column must belong to the run's dataset version.",
        ],
    }


@pytest.mark.django_db
def test_classification_is_opt_in(classification_fixture):
    fixture = classification_fixture([{"expected": True, "score": 1}])
    fixture[4].pop("expected_column_id")
    result = _result(fixture)
    assert all(
        "classification_summary" not in value
        for metric in result["metrics"]
        for value in metric["per_run"]
    )


def test_classification_field_is_optional_and_read_only():
    field = AnalysisValueSerializer().fields["classification_summary"]
    assert field.read_only is True
    assert field.required is False
