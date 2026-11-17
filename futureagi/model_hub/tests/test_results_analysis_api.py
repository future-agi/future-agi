"""S1 analysis contracts; real database/auth integration, not execution receipts."""
import copy
import json
import uuid

import pytest
from django.urls import reverse

from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.serializers.results_analysis import (
    ResultsAnalysisQuerySerializer,
    ResultsAnalysisResponseSerializer,
)
from model_hub.services.experiment_run_creation import create_experiment_run
from model_hub.services.experiment_run_writes import _digest
from model_hub.services.external_metrics import (
    ExternalMetricDefinition, create_or_reuse_external_metric,
)
from model_hub.services.results_analysis import AnalysisUnavailable, build_analysis


@pytest.fixture
def analysis_fixture(organization, workspace, user):
    dataset = Dataset.no_workspace_objects.create(
        name="S1 analysis", organization=organization, workspace=workspace,
        user=user, column_order=[], column_config={},
    )
    version = DatasetVersion.no_workspace_objects.create(dataset=dataset)
    examples = [DatasetVersionItem.no_workspace_objects.create(
        dataset_version=version, input={"case": n},
    ) for n in range(3)]
    # Persist a real same-dataset template/version without executing an evaluator.
    external = create_or_reuse_external_metric(
        dataset=dataset, user=user, definition=ExternalMetricDefinition(
            name="quality", output_type="numeric", source_hash="a" * 64,
            higher_is_better=True, pass_threshold=0.5,
        ),
    )
    runs = []
    for values in ([0.0, 1.0], [1.0, 0.5]):
        run = create_experiment_run(
            run_id=uuid.uuid4(), dataset_version=version, created_by=user,
            task_kind="external", evaluator_metrics=[external.metric],
            expected_items=[(example.example_id, 1) for example in examples],
        )
        # Seed the same read fixture: completed header, one unrecorded planned row.
        ExperimentRun.no_workspace_objects.filter(pk=run.id).update(status="completed")
        run.refresh_from_db()
        binding = run.evaluator_versions[0]
        for example, value in zip(examples, values):
            ExperimentRunItem.no_workspace_objects.create(
                run=run, example_id=example.example_id, trial_number=1,
                status="completed", score_refs=[{
                    "metric_id": binding["metric_id"],
                    "evaluator_version_id": binding["evaluator_version_id"],
                    "status": "assessed", "value": value,
                }],
            )
        runs.append(run)
    selection = {"example_ids": [str(e.example_id) for e in examples],
                 "examples_total": 3, "complete": True}
    query = {"baseline": str(runs[0].id), "include": "root_causes"}
    return dataset, runs, selection, query


@pytest.mark.django_db
def test_nested_contract_and_exact_denominators(analysis_fixture):
    dataset, runs, selection, query = analysis_fixture
    result = build_analysis(dataset, runs, selection, query)
    contract = ResultsAnalysisResponseSerializer(data={"status": True, "result": result})
    assert contract.is_valid(), contract.errors
    assert result["examples_total"] == result["examples_in_view"] == 3
    metric = result["metrics"][0]
    assert [p["value"] for p in metric["per_run"]] == [0.5, 0.75]
    assert [p["n"] for p in metric["per_run"]] == [2, 2]
    assert [p["expected_n"] for p in metric["per_run"]] == [3, 3]
    pair = metric["vs_baseline"][0]
    assert pair["n"] == 2 and pair["delta"] == 0.25
    assert pair["ci95"] is None and pair["p_value"] is None
    assert pair["significant"] is None
    assert result["root_causes"] is None
    assert result["availability"]["root_causes"]["reason"]
    assert result["observed_failures"]["groups"][0]["causal"] is False


@pytest.mark.django_db
def test_filter_selection_is_not_visible_page(analysis_fixture):
    dataset, runs, selection, query = analysis_fixture
    selection["example_ids"] = selection["example_ids"][:1]
    result = build_analysis(dataset, runs, selection, query)
    assert result["examples_in_view"] == 1 and result["examples_total"] == 3
    assert result["metrics"][0]["vs_baseline"][0]["delta"] == 1
    selection["complete"] = False
    with pytest.raises(AnalysisUnavailable):
        build_analysis(dataset, runs, selection, query)


@pytest.mark.django_db
@pytest.mark.parametrize("change", ["version", "plan", "evaluator", "config"])
def test_incompatible_pins_never_invent_comparison(analysis_fixture, change):
    dataset, runs, selection, query = analysis_fixture
    candidate = runs[1]
    if change == "version":
        candidate.dataset_version = DatasetVersion.no_workspace_objects.create(dataset=dataset)
    elif change == "plan":
        candidate.metadata = copy.deepcopy(candidate.metadata)
        plan = candidate.metadata["_futureagi_run_write"]["plan"]
        # Plans sort UUIDs; the unrecorded row is last in selection, not plan.
        missing_id = selection["example_ids"][-1]
        assert not ExperimentRunItem.no_workspace_objects.filter(
            run=candidate, example_id=missing_id, deleted=False,
        ).exists()
        plan["identities"] = [
            identity for identity in plan["identities"]
            if identity["example_id"] != missing_id
        ]
        assert len(plan["identities"]) == 2
        plan["plan_hash"] = _digest(plan["identities"])
    else:
        candidate.evaluator_versions = copy.deepcopy(candidate.evaluator_versions)
        binding = candidate.evaluator_versions[0]
        if change == "evaluator":
            binding["evaluator_version_id"] = str(uuid.uuid4())
        else:
            binding["config_snapshot"] = {"temperature": 1}
    result = build_analysis(dataset, runs, selection, query)
    pair = result["metrics"][0]["vs_baseline"][0]
    assert pair["delta"] is None and pair["n"] == 0
    assert pair["availability"]["state"] == "incompatible"
    assert result["verdicts"][0]["status"] == "unavailable"


@pytest.mark.django_db
def test_none_zero_evidence_and_running_are_distinct(analysis_fixture):
    dataset, runs, selection, query = analysis_fixture
    query["baseline"] = "none"
    ExperimentRunItem.no_workspace_objects.filter(run__in=runs).delete()
    runs[1].status = "running"
    result = build_analysis(dataset, runs, selection, query)
    assert result["baseline_run_id"] is None and result["candidate_run_id"] is None
    assert result["verdicts"] == [] and result["running"] is True
    assert result["metrics"][0]["vs_baseline"] == []
    for value in result["metrics"][0]["per_run"]:
        assert value["value"] is None and value["n"] == 0
        assert value["unavailable_reason"] == "No assessed trials in this scope."


@pytest.mark.parametrize("extra", [
    {"runs": "not-a-uuid"}, {"filters": "{}"}, {"filters": "[NaN]"},
    {"include": "anything"}, {"group_by": "arbitrary"}, {"tenant_id": "x"},
    {"runs": ",".join([str(uuid.uuid4())] * 9)},
])
def test_invalid_query_fails_explicitly(extra):
    data = {"runs": str(uuid.uuid4()), **extra}
    assert not ResultsAnalysisQuerySerializer(data=data).is_valid()


def _url(dataset):
    return reverse("dataset-results-analysis", kwargs={"dataset_id": dataset.id})


def _params(runs, **extra):
    return {"runs": ",".join(str(r.id) for r in runs), **extra}


@pytest.mark.django_db
def test_real_api_envelope_and_read_only(auth_client, analysis_fixture):
    dataset, runs, _, _ = analysis_fixture
    before = (ExperimentRun.no_workspace_objects.count(),
              ExperimentRunItem.no_workspace_objects.count())
    response = auth_client.get(_url(dataset), _params(runs, baseline="none"))
    assert response.status_code == 200, response.content
    contract = ResultsAnalysisResponseSerializer(data=response.json())
    assert contract.is_valid(), contract.errors
    assert response.json()["result"]["baseline_run_id"] is None
    assert before == (ExperimentRun.no_workspace_objects.count(),
                      ExperimentRunItem.no_workspace_objects.count())


@pytest.mark.django_db
@pytest.mark.parametrize("reference", ["runs", "baseline", "cand"])
def test_foreign_reference_rejects_whole_response(auth_client, analysis_fixture, reference):
    dataset, runs, _, _ = analysis_fixture
    # A separate organization is real data: no auth or tenancy monkeypatch.
    foreign = type(dataset.organization).objects.create(name="Other analysis organization")
    other = Dataset.no_workspace_objects.create(
        name="Foreign", organization=foreign, user=dataset.user,
        column_order=[], column_config={},
    )
    version = DatasetVersion.no_workspace_objects.create(dataset=other)
    foreign_run = ExperimentRun.no_workspace_objects.create(dataset_version=version)
    params = _params(runs)
    params[reference] = (params["runs"] + "," if reference == "runs" else "") + str(foreign_run.id)
    response = auth_client.get(_url(dataset), params)
    assert response.status_code == 404
    assert "examples_total" not in json.dumps(response.json())
    assert "Foreign" not in json.dumps(response.json())


@pytest.mark.django_db
def test_deleted_run_and_unknown_metric(auth_client, analysis_fixture):
    dataset, runs, _, _ = analysis_fixture
    response = auth_client.get(_url(dataset), _params(runs, bmetric="unknown"))
    assert response.status_code == 400
    runs[1].deleted = True
    runs[1].save(update_fields=["deleted"])
    response = auth_client.get(_url(dataset), _params(runs))
    assert response.status_code == 404


# Shared with P11 through ANALYSIS-CONTRACT.json.representativeResponse.
# Synthetic serializer fixture ONLY; not an execution or tenant acceptance receipt.
ANALYSIS_REPRESENTATIVE_JSON = r'''{
  "status": true,
  "result": {
    "dataset_id": "00000000-0000-4000-8000-000000000001", "running": false,
    "runs": [{"run_id": "00000000-0000-4000-8000-000000000002", "dataset_id": "00000000-0000-4000-8000-000000000001", "experiment_id": null, "dataset_version_id": "00000000-0000-4000-8000-000000000003", "task_kind": "external", "label": "#1", "name": null, "status": "completed", "planned_trials": 3, "recorded_trials": 2, "missing_trials": 1}],
    "metrics": [{"key": "00000000-0000-4000-8000-000000000004", "name": "00000000-0000-4000-8000-000000000004", "family": "eval", "aggregation": "assessed_trial_mean", "denominator_unit": "example_trial", "higher_is_better": true, "pass_threshold": 0.5, "per_run": [{"run_id": "00000000-0000-4000-8000-000000000002", "value": 0.5, "n": 2, "expected_n": 3, "missing_n": 1, "evaluator_version_id": "00000000-0000-4000-8000-000000000005", "ci95": null, "score_status_counts": {"assessed": 2}, "unavailable_reason": null}], "vs_baseline": []}],
    "verdicts": [], "baseline_run_id": null, "baseline_label": null, "candidate_run_id": null,
    "examples_in_view": 3, "examples_total": 3, "scope": {"population": "full_filtered_plan", "complete": true},
    "default_evals": [], "preference": null, "human": null, "agent": null, "worklist": null, "breakdown": null, "breakdown_options": [], "pattern": null, "root_causes": null, "observed_failures": null,
    "availability": {
      "uncertainty": {"state": "unavailable", "reason": "Not estimated in S1."},
      "candidate": {"state": "unavailable", "reason": "Selection is not a ranked winner."},
      "root_causes": {"state": "unavailable", "reason": "No authorized topology join in S1."},
      "ops": {"state": "unavailable", "reason": "Not projected in S1."},
      "actions": {"state": "unavailable", "reason": "Canonical-to-native mapping is unverified."},
      "preference": {"state": "unavailable", "reason": "No preference evidence."},
      "human": {"state": "unavailable", "reason": "No human evidence."},
      "agent": {"state": "unavailable", "reason": "No agent evidence."},
      "worklist": {"state": "unavailable", "reason": "Not projected in S1."},
      "breakdown": {"state": "unavailable", "reason": "Not projected in S1."}
    }
  }
}'''


def test_shared_representative_response_has_exact_nested_contract():
    payload = json.loads(ANALYSIS_REPRESENTATIVE_JSON)
    serializer = ResultsAnalysisResponseSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors
    assert json.loads(json.dumps(serializer.data)) == payload


@pytest.mark.django_db
def test_population_beyond_grid_page_keeps_last_example(analysis_fixture):
    dataset, runs, selection, query = analysis_fixture
    extra = [DatasetVersionItem.no_workspace_objects.create(
        dataset_version=runs[0].dataset_version, input={"case": index},
    ) for index in range(998)]
    ids = selection["example_ids"] + [str(e.example_id) for e in extra]
    plan = sorted([{"example_id": e, "trial_number": 1} for e in ids],
                  key=lambda value: value["example_id"])
    for run, value in zip(runs, [0.0, 1.0]):
        run.metadata["_futureagi_run_write"]["plan"] = {
            "identities": plan, "plan_hash": _digest(plan),
        }
        binding = run.evaluator_versions[0]
        ExperimentRunItem.no_workspace_objects.create(
            run=run, example_id=extra[-1].example_id, trial_number=1,
            status="completed", score_refs=[{
                "metric_id": binding["metric_id"],
                "evaluator_version_id": binding["evaluator_version_id"],
                "status": "assessed", "value": value,
            }],
        )
    selection = {"example_ids": ids, "examples_total": 1001, "complete": True}
    result = build_analysis(dataset, runs, selection, query)
    assert result["examples_total"] == 1001
    pair = result["metrics"][0]["vs_baseline"][0]
    assert pair["n"] == 3 and pair["delta"] == 0.5


@pytest.fixture
def isolated_analysis_population(analysis_fixture, monkeypatch):
    # Isolate the P03 selection seam, not authentication or dataset/run scoping.
    # P03 integration remains covered by test_real_api_envelope_and_read_only.
    dataset, runs, selection, _ = analysis_fixture

    def population(authorized_dataset, authorized_runs, query):
        assert authorized_dataset.id == dataset.id
        assert {run.id for run in authorized_runs} <= {run.id for run in runs}
        return dict(selection)

    monkeypatch.setattr("model_hub.views.results_analysis._population", population)
    return analysis_fixture


def _set_boundary_plan(run, example_ids, size):
    identities = [{"example_id": value, "trial_number": 1} for value in example_ids]
    identities += [{"example_id": example_ids[0], "trial_number": trial}
                   for trial in range(2, size - 1)]
    identities.sort(key=lambda item: (item["example_id"], item["trial_number"]))
    assert len(identities) == size
    run.metadata = {"_futureagi_run_write": {"plan": {
        "identities": identities, "plan_hash": _digest(identities),
    }}}
    run.save(update_fields=["metadata"])
    return identities


@pytest.mark.django_db
@pytest.mark.parametrize("size,expected_status", [(10000, 200), (10001, 503)])
def test_plan_work_bound_is_unavailable_not_incompatible(
    auth_client, isolated_analysis_population, size, expected_status,
):
    dataset, runs, selection, _ = isolated_analysis_population
    _set_boundary_plan(runs[0], selection["example_ids"], size)
    response = auth_client.get(_url(dataset), _params(runs[:1], baseline="none"))
    assert response.status_code == expected_status, response.content
    if expected_status == 503:
        assert "examples_total" not in json.dumps(response.json())
    else:
        assert response.json()["result"]["runs"][0]["planned_trials"] == size


@pytest.mark.django_db
@pytest.mark.parametrize("size,expected_status", [(10000, 200), (10001, 503)])
def test_recorded_item_work_bound_is_unavailable_not_incompatible(
    auth_client, isolated_analysis_population, size, expected_status,
):
    dataset, runs, selection, _ = isolated_analysis_population
    run = runs[0]
    identities = _set_boundary_plan(run, selection["example_ids"], 10000)
    if size == 10001:
        # The primitive checks its work ceiling BEFORE outside-plan identities.
        identities = identities + [{"example_id": selection["example_ids"][0],
                                     "trial_number": 10001}]
    recorded = {(str(item.example_id), item.trial_number)
                for item in ExperimentRunItem.no_workspace_objects.filter(run=run)}
    ExperimentRunItem.no_workspace_objects.bulk_create([
        ExperimentRunItem(run=run, example_id=item["example_id"],
                          trial_number=item["trial_number"], status="completed")
        for item in identities
        if (item["example_id"], item["trial_number"]) not in recorded
    ], batch_size=1000)
    response = auth_client.get(_url(dataset), _params([run], baseline="none"))
    assert response.status_code == expected_status, response.content
    if expected_status == 200:
        assert response.json()["result"]["runs"][0]["recorded_trials"] == 10000
    else:
        assert "examples_total" not in json.dumps(response.json())


@pytest.mark.django_db
@pytest.mark.parametrize("damage", ["missing_plan", "malformed_plan", "hash", "outside_plan"])
def test_invalid_immutable_evidence_still_returns_409(
    auth_client, isolated_analysis_population, damage,
):
    dataset, runs, _, _ = isolated_analysis_population
    run = runs[0]
    if damage == "outside_plan":
        ExperimentRunItem.no_workspace_objects.create(
            run=run, example_id=uuid.uuid4(), trial_number=1, status="completed",
        )
    else:
        run.metadata = copy.deepcopy(run.metadata)
        plan = run.metadata["_futureagi_run_write"]["plan"]
        if damage == "missing_plan":
            del run.metadata["_futureagi_run_write"]["plan"]
        elif damage == "malformed_plan":
            plan["identities"] = {}
        else:
            plan["plan_hash"] = "invalid"
        run.save(update_fields=["metadata"])
    response = auth_client.get(_url(dataset), _params([run], baseline="none"))
    assert response.status_code == 409, response.content
    assert "examples_total" not in json.dumps(response.json())


@pytest.mark.django_db
@pytest.mark.parametrize("run_count", [1, 2])
def test_raw_absolute_api_scope_still_rejects_candidate(
    auth_client, isolated_analysis_population, run_count,
):
    dataset, runs, _, _ = isolated_analysis_population
    selected = runs[:run_count]
    response = auth_client.get(
        _url(dataset), _params(selected, baseline="none", cand=str(selected[-1].id)),
    )
    assert response.status_code == 400, response.content


@pytest.mark.django_db
def test_real_api_keeps_external_direction_and_observed_failures(auth_client, analysis_fixture):
    dataset, runs, _, query = analysis_fixture
    response = auth_client.get(_url(dataset), _params(runs, **query))
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    metric = result["metrics"][0]
    assert metric["higher_is_better"] is True
    assert metric["pass_threshold"] == 0.5
    assert [value["value"] for value in metric["per_run"]] == [0.5, 0.75]
    assert metric["vs_baseline"][0]["n"] == 2
    assert metric["vs_baseline"][0]["delta"] == 0.25
    assert result["observed_failures"]["groups"][0]["failed_trials"] == 1
    assert result["observed_failures"]["groups"][0]["causal"] is False
    assert all("creation" in run.metadata["_futureagi_run_write"] for run in runs)


@pytest.mark.django_db
def test_metrics_are_named_as_the_data_tab_names_them(analysis_fixture):
    """W33-SUMMARY-NAMES: the Summary names an evaluation, never by its id."""
    dataset, runs, selection, query = analysis_fixture
    result = build_analysis(dataset, runs, selection, query)
    metric = result["metrics"][0]
    assert metric["key"] == str(uuid.UUID(runs[0].evaluator_versions[0]["metric_id"]))
    assert metric["name"] == "quality"
    contract = ResultsAnalysisResponseSerializer(data={"status": True, "result": result})
    assert contract.is_valid(), contract.errors


@pytest.mark.django_db
def test_metric_names_are_read_only_in_the_dataset_scope(analysis_fixture, user):
    from model_hub.services.results_analysis import _metric_names

    dataset, runs, _, _ = analysis_fixture
    other = Dataset.no_workspace_objects.create(
        name="Other analysis dataset", organization=dataset.organization,
        workspace=dataset.workspace, user=user, column_order=[], column_config={},
    )
    foreign = create_or_reuse_external_metric(
        dataset=other, user=user, definition=ExternalMetricDefinition(
            name="other dataset metric", output_type="numeric", source_hash="b" * 64,
            higher_is_better=True, pass_threshold=0.5,
        ),
    ).metric
    own = str(uuid.UUID(runs[0].evaluator_versions[0]["metric_id"]))
    assert _metric_names(dataset, runs, [own, str(foreign.id)]) == {own: "quality"}
    assert _metric_names(dataset, runs, []) == {}
