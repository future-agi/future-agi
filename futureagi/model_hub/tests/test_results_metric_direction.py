"""WD-REST3: pinned metric direction agrees across every Results projection.

The lower-is-better REST case is the regression; higher-is-better is its
positive control. These tests are source deliverables, not execution receipts.
"""
import json
import uuid
from types import SimpleNamespace

import pytest
from django.urls import reverse

from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset
from model_hub.services.results_grid import assessed_scores
from model_hub.services.results_matrix import trial_value
from model_hub.services.results_root_causes import observed_failure_group, root_cause_projection
from model_hub.services.results_selection import filter_rows

pytestmark = pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")


def _result(response):
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["status"] is True
    return body["result"]


@pytest.mark.django_db
@pytest.mark.parametrize("output_type", ["numeric", "percentage"])
@pytest.mark.parametrize("higher,expected", [
    pytest.param(False, [True, False, True], id="lower-is-better-regression"),
    pytest.param(True, [False, True, True], id="higher-is-better-control"),
])
def test_external_numeric_results_use_pinned_direction(
        auth_client, organization, workspace, user, output_type, higher, expected):
    dataset = Dataset.no_workspace_objects.create(
        name="Results metric direction", organization=organization,
        workspace=workspace, user=user, column_order=[], column_config={})
    metric = _result(auth_client.post(
        reverse("dataset-external-metrics", kwargs={"dataset_id": dataset.id}),
        {"name": "error-rate", "output_type": output_type, "higher_is_better": higher,
         "pass_threshold": 0.5, "source_hash": "a" * 64}, format="json"))
    assert metric["higher_is_better"] is higher
    version = DatasetVersion.no_workspace_objects.create(dataset=dataset)
    values = [0.1, 0.9, 0.5]  # User repro plus the inclusive threshold boundary.
    examples = [DatasetVersionItem.no_workspace_objects.create(
        dataset_version=version, input={"case": index}) for index in range(len(values))]
    example_ids = [str(example.example_id) for example in examples]
    run_id, metric_id = str(uuid.uuid4()), metric["metric_id"]
    identities = [{"example_id": eid, "trial_number": 1} for eid in example_ids]
    created = _result(auth_client.post(reverse("external-experiment-run-create"), {
        "run_id": run_id, "dataset_version_id": str(version.id),
        "expected_items": identities, "evaluator_metric_ids": [metric_id],
    }, format="json"))
    pin = created["evaluator_bindings"][0]
    assert pin["config_snapshot"]["external"]["higher_is_better"] is higher
    assert pin["pass_threshold"] == 0.5
    assert pin["evaluator_version_id"] == metric["evaluator_version_id"]
    items = [{**identity, "receipt_key": f"{run_id}-{index}", "status": "completed",
              "output": {"answer": index}, "score_refs": [{
                  "metric_id": metric_id,
                  "evaluator_version_id": metric["evaluator_version_id"],
                  "status": "assessed", "value": value,
              }]} for index, (identity, value) in enumerate(zip(identities, values))]
    ingested = _result(auth_client.post(
        reverse("external-experiment-run-results", kwargs={"run_id": run_id}),
        {"items": items}, format="json"))
    assert ingested["run"]["status"] == "completed"

    url = reverse("dataset-results", kwargs={"dataset_id": dataset.id})
    query = {"runs": run_id, "baseline": "none"}
    matrix = _result(auth_client.get(url, {**query, "shape": "matrix"}))
    by_id = {row["row_id"]: row["cells"][run_id] for row in matrix["rows"]}
    # RED on the base: 0.1 -> False and 0.9 -> True when higher=False.
    assert [by_id[eid]["trials"][0] for eid in example_ids] == expected
    assert [by_id[eid]["failed_evals"][0] for eid in example_ids] == [
        [] if passed else [metric_id] for passed in expected]

    grid = _result(auth_client.get(url, query))
    grid_by_id = {row["example_id"]: row["results"][run_id] for row in grid["rows"]}
    assert [grid_by_id[eid]["scores"][metric_id]["value"] for eid in example_ids] == values
    for eid, value, passed in zip(example_ids, values, expected):
        full = _result(auth_client.get(url, {**query, "full": True, "example_id": eid}))
        trial = full["rows"][0]["results"][run_id]["trials_detail"][0]
        assert trial["scores"][metric_id]["value"] == value
        assert trial["scores"][metric_id]["passed"] is passed

    failed_ids = [eid for eid, passed in zip(example_ids, expected) if not passed]
    fail_filter = json.dumps([{"type": "fails", "metric": metric_id, "run": run_id}])
    for extra in ({"show": "failed"}, {"filters": fail_filter}):
        failed = _result(auth_client.get(url, {**query, **extra, "ids_only": True}))
        assert failed["example_ids"] == failed_ids
        assert failed["total"] == 1

    analysis = _result(auth_client.get(
        reverse("dataset-results-analysis", kwargs={"dataset_id": dataset.id}),
        {**query, "include": "root_causes"}))
    assert analysis["metrics"][0]["higher_is_better"] is higher
    assert analysis["observed_failures"]["availability"]["state"] == "available"
    assert analysis["observed_failures"]["groups"] == [{
        "run_id": run_id, "metric": metric_id, "failed_trials": 1,
        "assessed_trials": 3, "example_ids": failed_ids,
        "examples_total": 1, "examples_has_more": False, "causal": False,
    }]


def _binding(**overrides):
    return {"evaluator_version_id": "version", "output_type_normalized": None,
            "pass_threshold": 0.5,
            "config_snapshot": {"external": {"higher_is_better": False}}, **overrides}


def _score(binding, value, status="assessed"):
    return assessed_scores([{"metric_id": "metric", "evaluator_version_id": "version",
                             "status": status, "value": value}], {"metric": binding})["metric"]


def _record(value, status="assessed", execution="completed"):
    return SimpleNamespace(example_id="example", status=execution,
                           scores={"metric": {"value": value, "status": status}})


def _assert_unavailable(binding, score, record):
    assert score["passed"] is None
    trial = {"status": "completed", "scores": {"metric": score}}
    assert trial_value(trial) is None
    selection = SimpleNamespace(
        runs=[SimpleNamespace(id="run")], baseline_run_id=None,
        bindings={"run": {"metric": binding}}, rows=[{
            "example_id": "example", "results": {"run": {"trials_detail": [trial]}}}])
    assert filter_rows(selection, {"show": "failed"}) == []
    assert filter_rows(selection, {"filters": [{
        "type": "fails", "metric": "metric", "run": "run"}]}) == []
    assert observed_failure_group("run", "metric", [record], binding) is None
    projection = root_cause_projection([], requested=True)
    assert projection["observed_failures"]["availability"]["state"] == "unavailable"


@pytest.mark.parametrize("snapshot", [
    None, {}, {"external": None}, {"external": {}},
    {"external": {"higher_is_better": "false"}},
    {"external": {"higher_is_better": 0}},
])
def test_unknown_numeric_direction_is_unavailable_not_pass_or_fail(snapshot):
    binding = _binding(config_snapshot=snapshot)
    score = _score(binding, 0.9)
    assert score["value"] == 0.9 and score["status"] == "assessed"
    _assert_unavailable(binding, score, _record(0.9))


@pytest.mark.parametrize("higher,expected", [
    (False, [True, False, True]), (True, [False, True, True]),
])
@pytest.mark.parametrize("legacy_only", [False, True])
def test_external_snapshot_precedes_legacy_and_explicit_legacy_is_supported(
        higher, expected, legacy_only):
    binding = _binding(
        higher_is_better=higher if legacy_only else not higher,
        config_snapshot={} if legacy_only else {"external": {"higher_is_better": higher}})
    for value, passed in zip([0.1, 0.9, 0.5], expected):
        score = _score(binding, value)
        assert score["passed"] is passed
        assert trial_value({"status": "completed", "scores": {"metric": score}}) is passed
        group = observed_failure_group("run", "metric", [_record(value)], binding)
        assert group["failed_trials"] == int(not passed)
        assert group["assessed_trials"] == 1


@pytest.mark.parametrize("threshold", [
    None, "0.5", True, float("nan"), float("inf"), -float("inf"),
    pytest.param(10 ** 1000, id="overflowing-threshold"),
])
def test_unusable_numeric_threshold_does_not_fabricate_a_verdict(threshold):
    binding = _binding(pass_threshold=threshold)
    _assert_unavailable(binding, _score(binding, 0.9), _record(0.9))


@pytest.mark.parametrize("status,value", [
    ("not_assessed", 0.9), ("invalid", 0.9), ("assessed", None),
    ("assessed", "0.9"), ("assessed", float("nan")), ("assessed", float("inf")),
])
def test_unassessed_and_invalid_evidence_stays_unknown(status, value):
    binding = _binding()
    score = _score(binding, value, status)
    assert score["value"] is None and score["status"] != "assessed"
    _assert_unavailable(binding, score, _record(value, status))


def test_missing_evidence_and_failed_execution_do_not_gain_assessed_verdicts():
    binding = _binding()
    score = assessed_scores([], {"metric": binding})["metric"]
    assert score["status"] == "not_assessed"
    _assert_unavailable(binding, score, _record(None, "not_assessed"))
    assert observed_failure_group("run", "metric", [
        _record(0.9, execution="failed")], binding) is None


@pytest.mark.parametrize("output_type", ["pass_fail", "boolean", "bool", "binary"])
@pytest.mark.parametrize("value", [0, 1, False, True])
def test_explicit_binary_outcome_does_not_require_numeric_direction(output_type, value):
    binding = _binding(output_type_normalized=output_type, config_snapshot={},
                       pass_threshold=None)
    score = _score(binding, value)
    assert score["passed"] is bool(value)
    assert trial_value({"status": "completed", "scores": {"metric": score}}) is bool(value)
    group = observed_failure_group("run", "metric", [_record(value)], binding)
    assert group["assessed_trials"] == 1
    assert group["failed_trials"] == int(not value)
