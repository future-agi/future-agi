"""Real admission/receipt writers must align only equivalent evaluator pins."""

from copy import deepcopy
from uuid import uuid4

import pytest
from django.urls import reverse

from evaluations.run_settings import CREDENTIAL_FIELDS
from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.evals_metric import EvalTemplateVersion, UserEvalMetric
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.services.experiment_run_creation import create_experiment_run
from model_hub.services.experiment_run_writes import RunWriteError, _digest, _planned
from model_hub.services.native_run_binding import native_evaluator_definition
from model_hub.services.persisted_run_comparison import (
    RunComparisonError,
    compare_experiment_runs,
)
from model_hub.services.results_catalog import ResultsReadError, checked_runs
from model_hub.tests import test_native_run_binding as native
from model_hub.tests import test_native_run_recording as recording
from model_hub.tests.test_dataset_row_runs import read as read_row_runs

native_world = native.native_world
pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


def source_metric(world, metric, **changes):
    return UserEvalMetric.no_workspace_objects.create(
        **{
            "name": "Source evaluator",
            "template": metric.template,
            "pinned_version": metric.pinned_version,
            "dataset": world["dataset"],
            "organization": world["dataset"].organization,
            "workspace": world["dataset"].workspace,
            "user": world["created_by"],
            "config": deepcopy(metric.config),
            "model": metric.model,
            **changes,
        }
    )


def pair(
    world,
    *,
    kind="prompt",
    mapped=True,
    change=None,
    direction=False,
    binding_settings=None,
):
    if kind == "graph":
        world = native.graph_variant(world, world["dataset"].workspace)
    metric, version = native.add_native_eval(world)
    version.output_type_normalized = "numeric"
    version.pass_threshold = 0.5
    version.save(update_fields=["output_type_normalized", "pass_threshold"])
    if direction:
        version.config_snapshot = {"external": {"higher_is_better": True}}
        version.save(update_fields=["config_snapshot"])
    if binding_settings is not None:
        metric.config = deepcopy(binding_settings)
        metric.save(update_fields=["config"])
    source = source_metric(world, metric)
    if not mapped:
        world["experiment"].user_eval_template_ids.set([source])
        metric = source
    if change == "ambiguous":
        source_metric(world, metric)
    baseline = recording.prepared(world)
    for link in recording.rows(world):
        recording.start(world, baseline, link)
        recording.record(
            world,
            baseline,
            link,
            output="native answer",
            score_refs=[
                {
                    "metric_id": str(metric.id),
                    "evaluator_version_id": str(version.id),
                    "status": "assessed",
                    "value": 0.25,
                }
            ],
        )
    recording.finish_native_run(dataset=world["dataset"], run_id=baseline.id)
    if change in {"settings", "model"}:
        field, value = (
            ("config", {"threshold": 0.75})
            if change == "settings"
            else ("model", "other-model")
        )
        setattr(source, field, value)
        source.save(update_fields=[field])
    if change == "definition":
        version.criteria = "A different criterion"
        version.save(update_fields=["criteria"])
    if change == "version":
        source.pinned_version = EvalTemplateVersion.objects.create(
            eval_template=metric.template,
            version_number=2,
            is_default=False,
            organization=world["dataset"].organization,
            workspace=world["dataset"].workspace,
            output_type_normalized="numeric",
            pass_threshold=0.5,
        )
        source.save(update_fields=["pinned_version"])
    plan = [(example, trial) for example, trial in sorted(_planned(baseline))]
    dataset_version = baseline.dataset_version
    if change == "plan":
        plan = plan[:1]
    if change == "dataset_version":
        dataset_version = DatasetVersion.no_workspace_objects.create(
            dataset=world["dataset"]
        )
        for example, _ in plan:
            DatasetVersionItem.no_workspace_objects.create(
                dataset_version=dataset_version, example_id=example, input={}
            )
    candidate = create_experiment_run(
        run_id=uuid4(),
        dataset_version=dataset_version,
        created_by=world["created_by"],
        task_kind="external",
        expected_items=plan,
        evaluator_metrics=[source],
    )
    return world, baseline, candidate, metric, source


def upload(client, candidate, value=0.75):
    pin = candidate.evaluator_versions[0]
    response = client.post(
        reverse("external-experiment-run-results", kwargs={"run_id": candidate.id}),
        {
            "items": [
                {
                    "example_id": example,
                    "trial_number": trial,
                    "receipt_key": f"external-{example}",
                    "status": "completed",
                    "output": "external answer",
                    "score_refs": [
                        {
                            "metric_id": pin["metric_id"],
                            "evaluator_version_id": pin["evaluator_version_id"],
                            "status": "assessed",
                            "value": value,
                        }
                    ],
                }
                for example, trial in sorted(_planned(candidate))
            ]
        },
        format="json",
    )
    assert response.status_code == 200, response.content


def read(client, world, route="dataset-results", **query):
    return client.get(reverse(route, kwargs={"dataset_id": world["dataset"].id}), query)


def evidence():
    return (
        list(
            ExperimentRun.all_objects.order_by("id").values(
                "id", "evaluator_versions", "metadata", "updated_at"
            )
        ),
        list(
            ExperimentRunItem.no_workspace_objects.order_by("id").values(
                "id", "score_refs", "metadata", "output", "updated_at"
            )
        ),
    )


@pytest.mark.parametrize("kind", ["prompt", "graph"])
@pytest.mark.parametrize("mapped", [False, True], ids=["source", "snapshot"])
def test_external_and_native_scores_share_authorized_identity(
    auth_client, native_world, kind, mapped
):
    world, baseline, candidate, metric, source = pair(
        native_world, kind=kind, mapped=mapped
    )
    upload(auth_client, candidate)
    before = evidence()
    response = read(auth_client, world, "dataset-results-runs", include="metrics")
    assert response.status_code == 200, response.content
    catalog = response.json()["result"]
    groups = {row["run_id"]: row["comparison_group"] for row in catalog["results"]}
    assert groups[str(baseline.id)] == groups[str(candidate.id)]
    assert isinstance(groups[str(baseline.id)], str)
    assert len(catalog["metric_defs"]) == 1
    definition = catalog["metric_defs"][0]
    key = str(source.id)
    assert {
        row["run_id"]: row["metrics"][key]["value"] for row in catalog["results"]
    } == {
        str(baseline.id): 0.25,
        str(candidate.id): 0.75,
    }
    assert definition["key"] == key
    assert definition["local_metric_ids"] == {
        str(baseline.id): str(metric.id),
        str(candidate.id): str(source.id),
    }
    assert definition["uem_ids"] == {str(baseline.id): str(metric.id)}
    for shape in ("grid", "matrix"):
        response = read(
            auth_client,
            world,
            runs=f"{baseline.id},{candidate.id}",
            baseline=str(baseline.id),
            shape=shape,
            **({"measure": key} if shape == "matrix" else {}),
        )
        assert response.status_code == 200, response.content
        data = response.json()["result"]
        assert data["total"] == 2
        assert data["summary"]["aggregates"][key][str(baseline.id)]["value"] == 0.25
        assert data["summary"]["aggregates"][key][str(candidate.id)]["value"] == 0.75
        assert data["summary"]["aggregates"][key][str(candidate.id)]["n"] == 2
        for row in data["rows"]:
            for run, value in ((baseline, 0.25), (candidate, 0.75)):
                cell = row["cells" if shape == "matrix" else "results"][str(run.id)]
                assert cell["scores"][key]["value"] == value
                if shape == "matrix":
                    # The binary trial channel never coerces continuous scores.
                    assert cell["trials"] == [None]
    # The baseline may be outside the displayed runs; delta sorting still
    # resolves its local references before projecting the shared metric key.
    response = read(
        auth_client,
        world,
        runs=str(candidate.id),
        baseline=str(baseline.id),
        sort=f"delta:{key}:{candidate.id}:desc",
        show="changed",
    )
    assert response.status_code == 200, response.content
    assert response.json()["result"]["total"] == 2
    assert evidence() == before


@pytest.mark.parametrize(
    "field",
    [
        *sorted(CREDENTIAL_FIELDS),
        "aws_secret_access_key",
        "key",
    ],
)
def test_credential_rotation_preserves_external_scored_comparisons(
    auth_client, native_world, field
):
    settings = {
        "mapping": {"api_key": "input-column"},
        "choice_scores": {"credentials": 0.25},
        "run_config": {"temperature": 0.2, "steps": [{"limit": 2}]},
    }
    if field == "key":
        for section in (
            settings,
            settings["run_config"],
            settings["run_config"]["steps"][0],
        ):
            section["api_base"] = "https://example.invalid"

    def configured(revision):
        result = deepcopy(settings)
        for section in (
            result,
            result["run_config"],
            result["run_config"]["steps"][0],
        ):
            # Inert reference-shaped fixtures, never real credential values.
            section[field] = {"secret_ref": revision}
        return result

    world, native_run, baseline, metric, source = pair(
        native_world, binding_settings=configured(1)
    )
    upload(auth_client, baseline, value=0.25)
    source.config = configured(2)
    source.save(update_fields=["config"])

    def admitted():
        return create_experiment_run(
            run_id=uuid4(),
            dataset_version=baseline.dataset_version,
            created_by=world["created_by"],
            task_kind="external",
            expected_items=list(_planned(baseline)),
            evaluator_metrics=[source],
        )

    candidate = admitted()
    upload(auth_client, candidate)
    assert candidate.evaluator_versions == baseline.evaluator_versions
    assert candidate.evaluator_versions[0]["binding_config"] == settings
    native_pin = native_run.evaluator_versions[0]
    assert native_pin["binding_config"] == {"sha256": _digest(configured(1))}
    assert native_pin["definition_hash"] == native_evaluator_definition(
        metric, metric.pinned_version
    )
    metric.refresh_from_db()
    source.refresh_from_db()
    assert metric.config == configured(1)
    assert source.config == configured(2)
    before = evidence()
    response = read(auth_client, world, "dataset-results-runs", include="metrics")
    assert response.status_code == 200, response.content
    catalog = response.json()["result"]
    groups = {row["run_id"]: row["comparison_group"] for row in catalog["results"]}
    group = groups[str(baseline.id)]
    assert isinstance(group, str)
    assert groups == {str(run.id): group for run in (native_run, baseline, candidate)}
    key = str(source.id)
    for shape in ("grid", "matrix"):
        response = read(
            auth_client,
            world,
            runs=f"{baseline.id},{candidate.id}",
            baseline=str(baseline.id),
            shape=shape,
            **({"measure": key} if shape == "matrix" else {}),
        )
        assert response.status_code == 200, response.content
        data = response.json()["result"]
        assert data["total"] == 2
        for run, value in ((baseline, 0.25), (candidate, 0.75)):
            aggregate = data["summary"]["aggregates"][key][str(run.id)]
            assert (aggregate["value"], aggregate["n"]) == (value, 2)
            for row in data["rows"]:
                cell = row["cells" if shape == "matrix" else "results"][str(run.id)]
                assert cell["scores"][key]["value"] == value

    def compared(other):
        return compare_experiment_runs(
            baseline_run_id=baseline.id,
            candidate_run_id=other.id,
            organization_id=world["dataset"].organization_id,
            workspace_id=world["dataset"].workspace_id,
            metric_id=source.id,
        )

    comparison = compared(candidate).comparison
    assert comparison.comparable_count == 2
    assert comparison.baseline.score_average == 0.25
    assert comparison.candidate.score_average == 0.75
    assert [case.score_delta for case in comparison.cases] == [0.5, 0.5]
    replay = create_experiment_run(
        run_id=baseline.id,
        dataset_version=baseline.dataset_version,
        created_by=world["created_by"],
        task_kind="external",
        expected_items=list(_planned(baseline)),
        evaluator_metrics=[source],
    )
    assert replay.evaluator_versions == baseline.evaluator_versions
    assert evidence() == before

    # A real setting still changes both comparison paths after the rotation.
    source.config["run_config"]["temperature"] = 0.4
    source.save(update_fields=["config"])
    changed = admitted()
    upload(auth_client, changed)
    before = evidence()
    response = read(auth_client, world, "dataset-results-runs")
    assert response.status_code == 200, response.content
    groups = {
        row["run_id"]: row["comparison_group"]
        for row in response.json()["result"]["results"]
    }
    assert groups[str(baseline.id)] == group
    assert groups[str(changed.id)] != group
    for shape in ("grid", "matrix"):
        response = read(
            auth_client,
            world,
            runs=f"{baseline.id},{changed.id}",
            baseline=str(baseline.id),
            shape=shape,
        )
        assert response.status_code == 409, response.content
    with pytest.raises(RunComparisonError) as error:
        compared(changed)
    assert str(error.value) == "selected evaluator binding snapshots must match"
    assert evidence() == before


@pytest.mark.parametrize("scope", ["binding", "template", "version"])
def test_definition_identity_filters_only_credentials_without_mutation(
    native_world, scope
):
    from model_hub.services.evaluator_comparison_identity import _identity

    metric, version = native.add_native_eval(native_world)
    target, field = {
        "binding": (metric, "config"),
        "template": (metric.template, "config"),
        "version": (version, "config_snapshot"),
    }[scope]
    settings = {
        "api_key": {"secret_ref": 1},
        "mapping": {"api_key": "input-column"},
        "choice_scores": {"credentials": 0.25},
        "run_config": {"provider": "first-provider", "temperature": 0.2},
    }
    setattr(target, field, deepcopy(settings))
    initial = _identity(metric, version)
    integrity = native_evaluator_definition(metric, version)
    settings["api_key"] = {"secret_ref": 2}
    setattr(target, field, deepcopy(settings))
    assert _identity(metric, version) == initial
    assert getattr(target, field) == settings
    assert native_evaluator_definition(metric, version) != integrity
    for section, name, value in (
        ("mapping", "api_key", "other-column"),
        ("choice_scores", "credentials", 0.75),
        ("run_config", "provider", "other-provider"),
        ("run_config", "temperature", 0.4),
    ):
        changed = deepcopy(settings)
        changed[section][name] = value
        setattr(target, field, changed)
        assert (
            _identity(metric, version)["definition_hash"]
            != (initial["definition_hash"])
        )
        assert getattr(target, field) == changed


@pytest.mark.parametrize(
    "change",
    [
        "settings",
        "model",
        "definition",
        "version",
        "plan",
        "dataset_version",
        "ambiguous",
    ],
)
def test_changed_or_ambiguous_evaluators_do_not_compare(
    auth_client, native_world, change
):
    world, baseline, candidate, _, _ = pair(native_world, change=change)
    upload(auth_client, candidate)
    response = read(auth_client, world, "dataset-results-runs")
    assert response.status_code == 200, response.content
    groups = {
        row["run_id"]: row["comparison_group"]
        for row in response.json()["result"]["results"]
    }
    assert groups[str(baseline.id)] != groups[str(candidate.id)]
    for shape in ("grid", "matrix"):
        response = read(
            auth_client,
            world,
            runs=f"{baseline.id},{candidate.id}",
            baseline=str(baseline.id),
            shape=shape,
        )
        assert response.status_code == 409, response.content


def test_snapshot_identity_cannot_authorize_external_metric_reuse(native_world):
    world, baseline, candidate, metric, _ = pair(native_world)
    with pytest.raises(RunWriteError) as error:
        create_experiment_run(
            run_id=uuid4(),
            dataset_version=baseline.dataset_version,
            created_by=world["created_by"],
            task_kind="external",
            expected_items=list(_planned(baseline)),
            evaluator_metrics=[metric],
        )
    assert str(error.value) == (
        "evaluator binding was not found in the version dataset"
    )
    ExperimentRun.all_objects.filter(pk=candidate.pk).update(
        evaluator_versions=baseline.evaluator_versions
    )
    with pytest.raises(ResultsReadError) as error:
        checked_runs(world["dataset"], [candidate.id])
    assert error.value.status == 404


def test_admitted_mapping_survives_live_edits_and_creation_replay(
    auth_client, native_world
):
    world, baseline, candidate, _, source = pair(native_world)
    upload(auth_client, candidate)
    before = evidence()
    source.config = {"threshold": 0.9}
    source.save(update_fields=["config"])
    replay = create_experiment_run(
        run_id=candidate.id,
        dataset_version=candidate.dataset_version,
        created_by=world["created_by"],
        task_kind="external",
        expected_items=list(_planned(candidate)),
        evaluator_metrics=[source],
    )
    assert replay.evaluator_versions == candidate.evaluator_versions
    assert native.bind(world).evaluator_versions == baseline.evaluator_versions
    response = read(
        auth_client, world, runs=f"{baseline.id},{candidate.id}", baseline="none"
    )
    assert response.status_code == 200, response.content
    assert response.json()["result"]["total"] == 2
    assert evidence() == before


def test_historical_pins_do_not_gain_inferred_identity(auth_client, native_world):
    world, baseline, candidate, _, _ = pair(native_world)
    pins = deepcopy(baseline.evaluator_versions)
    for pin in pins:
        pin.pop("comparison", None)
    ExperimentRun.all_objects.filter(pk=baseline.pk).update(evaluator_versions=pins)
    before = evidence()
    response = read(
        auth_client, world, runs=f"{baseline.id},{candidate.id}", baseline="none"
    )
    assert response.status_code == 409, response.content
    assert evidence() == before


@pytest.mark.parametrize("corrupt", ["foreign", "duplicate", "version"])
def test_local_score_references_are_checked_before_mapping(
    auth_client, native_world, corrupt
):
    world, baseline, candidate, _, _ = pair(native_world)
    upload(auth_client, candidate)
    item = baseline.items.order_by("id").first()
    refs = deepcopy(item.score_refs)
    if corrupt == "duplicate":
        refs.append(deepcopy(refs[0]))
    else:
        refs[0]["metric_id" if corrupt == "foreign" else "evaluator_version_id"] = str(
            uuid4()
        )
    ExperimentRunItem.no_workspace_objects.filter(pk=item.pk).update(score_refs=refs)
    response = read(
        auth_client, world, runs=f"{baseline.id},{candidate.id}", baseline="none"
    )
    assert response.status_code == 409, response.content


def completed_native(world, values):
    run = recording.prepared(world)
    for link in recording.rows(world):
        recording.start(world, run, link)
        recording.record(
            world,
            run,
            link,
            output="native answer",
            score_refs=[
                {
                    "metric_id": pin["metric_id"],
                    "evaluator_version_id": pin["evaluator_version_id"],
                    "status": "assessed",
                    "value": values[pin["metric_id"]],
                }
                for pin in run.evaluator_versions
            ],
        )
    recording.finish_native_run(dataset=world["dataset"], run_id=run.id)
    return run


@pytest.mark.parametrize("kind", ["prompt", "graph"])
def test_row_runs_keep_local_scores_definitions_counts_and_order(
    auth_client, native_world, kind
):
    world, baseline, candidate, metric, source = pair(
        native_world, kind=kind, direction=True
    )
    upload(auth_client, candidate)
    metric.name = "A native evaluator"
    metric.save(update_fields=["name"])
    source.name = "Z source evaluator"
    source.save(update_fields=["name"])
    local, shared = str(metric.id), str(source.id)
    sibling = completed_native({**world, "execution_id": uuid4()}, {local: 0.875})
    before = evidence()
    for sort, expected in (
        ("best", [sibling, baseline, candidate]),
        ("worst", [baseline, sibling, candidate]),
    ):
        response = read_row_runs(
            auth_client, world["dataset"], world["source_rows"][0], sort=sort
        )
        assert response.status_code == 200, response.content
        data = response.json()["result"]
        assert [m["key"] for m in data["metrics"]] == [local, shared]
        assert data["summary"]["runs"] == 3
        assert data["summary"]["primary_metric_name"] == "A native evaluator"
        assert data["summary"]["primary_scored"] == 2
        assert data["summary"]["primary_pass"] == 1
        assert [r["run_id"] for r in data["runs"]] == [str(run.id) for run in expected]
        entries = {entry["run_id"]: entry for entry in data["runs"]}
        for run, key, value in (
            (baseline, local, 0.25),
            (sibling, local, 0.875),
            (candidate, shared, 0.75),
        ):
            entry = entries[str(run.id)]
            assert set(entry["evals"]) == {key}
            assert entry["evals"][key]["value"] == value
            assert entry["evals"][key]["n"] == 1
            assert entry["evals"][key]["status"] == "assessed"
            assert len(entry["trials_detail"]) == 1
            scores = entry["trials_detail"][0]["scores"]
            assert set(scores) == {key}
            assert scores[key]["value"] == value
    assert evidence() == before


@pytest.mark.parametrize("kind", ["prompt", "graph"])
@pytest.mark.parametrize("members", ["source_and_snapshot", "two_snapshots"])
def test_colliding_aliases_keep_single_run_evidence_readable(
    auth_client, native_world, kind, members
):
    world = native_world
    if kind == "graph":
        world = native.graph_variant(world, world["dataset"].workspace)
    metric, version = native.add_native_eval(world)
    version.output_type_normalized = "numeric"
    version.pass_threshold = 0.5
    version.save(update_fields=["output_type_normalized", "pass_threshold"])
    source = source_metric(world, metric)
    other = source
    if members == "two_snapshots":
        other = source_metric(
            world,
            metric,
            name="Another snapshot evaluator",
            dataset=world["snapshot"],
            source_id=str(world["experiment"].id),
        )
    world["experiment"].user_eval_template_ids.add(other)
    values = {str(metric.id): 0.25, str(other.id): 0.875}
    baseline = completed_native(world, values)
    candidate = create_experiment_run(
        run_id=uuid4(),
        dataset_version=baseline.dataset_version,
        created_by=world["created_by"],
        task_kind="external",
        expected_items=list(_planned(baseline)),
        evaluator_metrics=[source],
    )
    upload(auth_client, candidate)
    # Admission, not the reader, fixes and seals every colliding local identity.
    assert {
        pin["metric_id"]: pin["comparison"]["metric_id"]
        for pin in baseline.evaluator_versions
    } == {key: key for key in values}
    before = evidence()
    assert native.bind(world).evaluator_versions == baseline.evaluator_versions
    response = read(auth_client, world, "dataset-results-runs", include="metrics")
    assert response.status_code == 200, response.content
    catalog = response.json()["result"]
    runs = {entry["run_id"]: entry for entry in catalog["results"]}
    assert set(runs) == {str(baseline.id), str(candidate.id)}
    assert (
        runs[str(baseline.id)]["comparison_group"]
        != (runs[str(candidate.id)]["comparison_group"])
    )
    assert runs[str(baseline.id)]["availability"]["provenance"] is None
    assert runs[str(baseline.id)]["metrics"] == {
        key: {"value": value, "n": 2, "rule": "assessed_trial_mean"}
        for key, value in values.items()
    }
    assert {definition["key"] for definition in catalog["metric_defs"]} == (
        set(values) | {str(source.id)}
    )
    for shape in ("grid", "matrix"):
        response = read(
            auth_client,
            world,
            runs=str(baseline.id),
            baseline="none",
            shape=shape,
            **({"measure": str(metric.id)} if shape == "matrix" else {}),
        )
        assert response.status_code == 200, response.content
        data = response.json()["result"]
        assert data["total"] == 2
        assert {m["key"] for m in data["summary"]["metrics"]} == set(values)
        for key, value in values.items():
            aggregate = data["summary"]["aggregates"][key][str(baseline.id)]
            assert aggregate["value"] == value
            assert aggregate["n"] == 2
        for row in data["rows"]:
            cell = row["cells" if shape == "matrix" else "results"][str(baseline.id)]
            assert {key: score["value"] for key, score in cell["scores"].items()} == (
                values
            )
        response = read(
            auth_client,
            world,
            runs=f"{baseline.id},{candidate.id}",
            baseline=str(baseline.id),
            shape=shape,
        )
        assert response.status_code == 409, response.content
    assert evidence() == before
