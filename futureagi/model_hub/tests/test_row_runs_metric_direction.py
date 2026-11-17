"""Declared evaluator direction survives admission and agrees on row reads."""

import uuid
from copy import deepcopy
from types import SimpleNamespace

import pytest
from django.db import transaction
from django.urls import reverse

from model_hub.models.dataset_versions import DatasetVersionItem
from model_hub.models.evals_metric import EvalTemplateVersion
from model_hub.models.experiments import ExperimentRun
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.serializers.row_runs import DatasetRowRunsResponseSerializer
from model_hub.services import http_experiment_source as http
from model_hub.services.experiment_submit import stage_submission
from model_hub.services.external_metrics import (
    ExternalMetricDefinition,
    create_or_reuse_external_metric,
)
from model_hub.services.native_run_binding import native_evaluator_pins
from model_hub.services.results_root_causes import pinned_score_passes
from model_hub.services.row_runs import _evals
from model_hub.tests.test_dataset_row_runs import (
    dataset_with_rows,
    read,
    result,
    run_of,
    tried,
    version_of,
)
from model_hub.tests.test_http_experiment_source import entry
from model_hub.tests.test_native_run_binding import add_native_eval, bind
from model_hub.tests.test_native_run_binding import native_world as native_world

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


def _admit(world, kind):
    if kind == "native":
        return bind(world)
    experiment = world["experiment"]
    config = entry()
    config["variable_mapping"] = {"question": str(world["source_column"].pk)}
    source = http.persist_http_source(experiment, config)
    http.create_http_output_column(experiment, source)
    with transaction.atomic():
        submission = stage_submission(
            experiment,
            {},
            "create",
            authorized_workspace=world["dataset"].workspace,
        )
        http.bind_http_submission(experiment, submission["admission_id"])
    return (
        NativeRunBinding.objects.select_related("run")
        .get(variant=source, execution_id=submission["admission_id"])
        .run
    )


@pytest.mark.parametrize("kind", ["native", "http"])
@pytest.mark.parametrize("higher", [False, True])
def test_admitted_pin_keeps_declared_direction(native_world, kind, higher):
    metric, version = add_native_eval(native_world)
    version.config_snapshot = {
        "external": {"higher_is_better": higher},
        "unrelated": "not copied to the run pin",
    }
    version.output_type_normalized = "percentage"
    version.pass_threshold = 0.5
    version.save()

    run = _admit(native_world, kind)
    pin = run.evaluator_versions[0]
    assert pin["metric_id"] == str(metric.pk)
    assert pin["evaluator_version_id"] == str(version.pk)
    assert pin["higher_is_better"] is higher
    assert "config_snapshot" not in pin
    assert [pinned_score_passes(value, pin) for value in (0.1, 0.5, 0.9)] == (
        [False, True, True] if higher else [True, True, False]
    )
    run.refresh_from_db()
    assert run.evaluator_versions == [pin]

    # Old stored pins must not acquire a direction during validation/readback.
    historical = [
        {key: value for key, value in pin.items() if key != "higher_is_better"}
    ]
    before = deepcopy(historical)
    validated = native_evaluator_pins(
        [metric],
        native_world["dataset"],
        run.native_binding.snapshot,
        pinned=historical,
        workspace=native_world["dataset"].workspace,
    )
    assert validated == before
    assert historical == before
    assert pinned_score_passes(0.5, validated[0]) is None


@pytest.mark.parametrize("kind", ["native", "http"])
@pytest.mark.parametrize(
    "snapshot",
    [
        {},
        {"external": None},
        {"external": {"higher_is_better": None}},
        {"external": {"higher_is_better": "false"}},
        {"external": {"higher_is_better": 0}},
    ],
)
def test_admission_does_not_invent_numeric_direction(native_world, kind, snapshot):
    _, version = add_native_eval(native_world)
    version.config_snapshot = snapshot
    version.output_type_normalized = "percentage"
    version.pass_threshold = 0.5
    version.choice_scores = {"low": 0, "high": 1}
    version.save()
    pin = _admit(native_world, kind).evaluator_versions[0]
    assert "higher_is_better" not in pin
    assert pinned_score_passes(0.5, pin) is None


@pytest.mark.parametrize(
    "higher,values,expected",
    [
        (False, [0.1, 0.5, 0.9], [True, True, False]),
        (True, [0.1, 0.5, 0.9], [False, True, True]),
        (None, [0.1, 0.5, 0.9], [None, None, None]),
    ],
)
def test_row_summary_and_results_agree_on_numeric_direction(
    auth_client, organization, workspace, user, higher, values, expected
):
    dataset, rows = dataset_with_rows(organization, workspace, user, 1)
    version, items = version_of(dataset, rows)
    binding = create_or_reuse_external_metric(
        dataset=dataset,
        user=user,
        definition=ExternalMetricDefinition(
            name="Threshold",
            output_type="numeric",
            source_hash="c" * 64,
            higher_is_better=higher,
            pass_threshold=0.5,
        ),
    )
    key = str(binding.metric.pk)
    runs = []
    for value in values:
        run = run_of(version, items, user, binding)
        tried(run, items[rows[0].id], binding, value)
        runs.append(run)
    pins_before = [deepcopy(run.evaluator_versions) for run in runs]
    data = result(read(auth_client, dataset, rows[0]))
    by_id = {entry["run_id"]: entry for entry in data["runs"]}
    assert data["metrics"][0]["higher_is_better"] is higher
    assert [
        by_id[str(run.pk)]["trials_detail"][0]["scores"][key]["passed"] for run in runs
    ] == expected
    assert data["summary"]["primary_scored"] == (0 if higher is None else 3)
    assert data["summary"]["primary_pass"] == (0 if higher is None else 2)
    assert data["summary"]["flags"]["hard_case"] is False
    assert data["summary"]["flags"]["fails_of_runs"] is None
    assert [by_id[str(run.pk)]["evals"][key]["passed"] for run in runs] == expected

    results_url = reverse("dataset-results", kwargs={"dataset_id": dataset.pk})
    for run, passed, pins in zip(runs, expected, pins_before, strict=True):
        matrix = result(
            auth_client.get(
                results_url,
                {
                    "runs": str(run.pk),
                    "baseline": "none",
                    "shape": "matrix",
                },
            )
        )
        assert matrix["rows"][0]["cells"][str(run.pk)]["trials"] == [passed]
        run.refresh_from_db()
        assert run.evaluator_versions == pins


def test_historical_and_declared_pins_keep_their_own_row_verdicts(
    auth_client, native_world
):
    metric, old_version = add_native_eval(native_world)
    old_version.output_type_normalized = "percentage"
    old_version.pass_threshold = 0.5
    old_version.save()
    historical = bind(native_world)
    assert "higher_is_better" not in historical.evaluator_versions[0]

    new_version = EvalTemplateVersion.objects.create(
        eval_template=old_version.eval_template,
        version_number=2,
        config_snapshot={"external": {"higher_is_better": False}},
        output_type_normalized="percentage",
        pass_threshold=0.5,
        organization=old_version.organization,
        workspace=old_version.workspace,
    )
    metric.pinned_version = new_version
    metric.save(update_fields=["pinned_version"])
    passing = bind(native_world, execution_id=uuid.uuid4())
    failing = bind(native_world, execution_id=uuid.uuid4())
    runs = [historical, passing, failing]
    key = str(metric.pk)
    dataset = native_world["dataset"]
    row = native_world["source_rows"][0]
    item = DatasetVersionItem.no_workspace_objects.get(
        dataset_version=historical.dataset_version, legacy_row=row
    )
    for run, version, value in (
        (historical, old_version, 0.5),
        (passing, new_version, 0.5),
        (failing, new_version, 0.9),
    ):
        tried(run, item, SimpleNamespace(metric=metric, version=version), value)
        ExperimentRun.no_workspace_objects.filter(pk=run.pk).update(status="completed")
    pins_before = [deepcopy(run.evaluator_versions) for run in runs]

    data = result(read(auth_client, dataset, row))
    assert data["metrics"][0]["key"] == key
    assert data["metrics"][0]["higher_is_better"] is None
    assert data["summary"]["primary_scored"] == 2
    assert data["summary"]["primary_pass"] == 1
    assert data["summary"]["flags"]["hard_case"] is False
    assert data["summary"]["flags"]["fails_of_runs"] is None
    serialized = DatasetRowRunsResponseSerializer(
        {"status": True, "result": data}
    ).data["result"]
    for response in (data, serialized):
        by_id = {entry["run_id"]: entry for entry in response["runs"]}
        assert [by_id[str(run.pk)]["evals"][key]["passed"] for run in runs] == [
            None,
            True,
            False,
        ]
        assert [
            by_id[str(run.pk)]["trials_detail"][0]["scores"][key]["passed"]
            for run in runs
        ] == [None, True, False]

    results_url = reverse("dataset-results", kwargs={"dataset_id": dataset.pk})
    for run, passed, pins in zip(runs, [None, True, False], pins_before, strict=True):
        grid = result(
            auth_client.get(
                results_url,
                {
                    "runs": str(run.pk),
                    "baseline": "none",
                    "full": True,
                    "example_id": str(item.example_id),
                },
            )
        )
        trial = grid["rows"][0]["results"][str(run.pk)]["trials_detail"][0]
        assert trial["scores"][key]["passed"] is passed
        run.refresh_from_db()
        assert run.evaluator_versions == pins


@pytest.mark.parametrize("value,passed", [(0, False), (0.5, None), (1, True)])
def test_type_only_pass_fail_threshold_preserves_fractional_unknown(
    auth_client, native_world, value, passed
):
    metric, version = add_native_eval(native_world)
    version.output_type_normalized = "pass_fail"
    version.pass_threshold = 0.5
    version.save()
    run = bind(native_world)
    pin = deepcopy(run.evaluator_versions[0])
    assert "higher_is_better" not in pin
    key = str(metric.pk)
    dataset = native_world["dataset"]
    row = native_world["source_rows"][0]
    item = DatasetVersionItem.no_workspace_objects.get(
        dataset_version=run.dataset_version, legacy_row=row
    )
    tried(run, item, SimpleNamespace(metric=metric, version=version), value)
    ExperimentRun.no_workspace_objects.filter(pk=run.pk).update(status="completed")

    data = result(read(auth_client, dataset, row))
    assert data["metrics"][0]["higher_is_better"] is True
    assert data["metrics"][0]["pass_threshold"] == 0.5
    assert data["summary"]["primary_scored"] == int(passed is not None)
    assert data["summary"]["primary_pass"] == int(passed is True)
    serialized = DatasetRowRunsResponseSerializer(
        {"status": True, "result": data}
    ).data["result"]
    for response in (data, serialized):
        entry = response["runs"][0]
        assert entry["evals"][key] == {
            "value": value,
            "n": 1,
            "status": "assessed",
            "std": 0,
            "flaky": False,
            "passed": passed,
        }
        assert entry["trials_detail"][0]["scores"][key]["passed"] is passed
    results_url = reverse("dataset-results", kwargs={"dataset_id": dataset.pk})
    grid = result(
        auth_client.get(
            results_url,
            {
                "runs": str(run.pk),
                "baseline": "none",
                "full": True,
                "example_id": str(item.example_id),
            },
        )
    )
    trial = grid["rows"][0]["results"][str(run.pk)]["trials_detail"][0]
    assert trial["scores"][key]["passed"] is passed
    run.refresh_from_db()
    assert run.evaluator_versions == [pin]


@pytest.mark.parametrize("direction,passed", [(False, True), (True, False)])
def test_aggregate_verdict_uses_the_mean_and_original_threshold(direction, passed):
    pin = {
        "output_type_normalized": "percentage",
        "higher_is_better": direction,
        "pass_threshold": 0.6,
    }
    tries = [
        {
            "scores": {
                "metric": {
                    "status": "assessed",
                    "value": value,
                    "passed": pinned_score_passes(value, pin),
                }
            }
        }
        for value in (0.25, 0.75)
    ]
    assert _evals(tries, {"metric": pin}) == {
        "metric": {
            "value": 0.5,
            "n": 2,
            "status": "assessed",
            "std": 0.25,
            "flaky": True,
            "passed": passed,
        }
    }
    assert _evals([], {"metric": pin}) == {
        "metric": {
            "value": None,
            "n": 0,
            "status": "not_assessed",
            "std": 0,
            "flaky": False,
            "passed": None,
        }
    }
