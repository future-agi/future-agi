"""S1 Results reads: real persisted plans, real route/auth scope; no mocked ACLs.

Run after P02 registers dataset-results-runs and dataset-results. These tests
are source deliverables, not evidence that the composed launch build passed.
"""
import json
import uuid

import pytest
from django.urls import reverse
from rest_framework.exceptions import ValidationError

from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.serializers.dataset_results import DatasetResultsQuerySerializer
from model_hub.serializers.results_runs import ResultsRunsQuerySerializer
from model_hub.services.experiment_run_writes import _RUN_KEY, _digest, _identities
from model_hub.services.results_matrix import trial_value

pytestmark = [pytest.mark.django_db,
              pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")]


def seed(organization, workspace, user, count=3):
    dataset = Dataset.no_workspace_objects.create(
        name="Results contract", organization=organization, workspace=workspace,
        user=user, column_order=[], column_config={})
    version = DatasetVersion.no_workspace_objects.create(dataset=dataset)
    examples = [DatasetVersionItem(dataset_version=version,
                input={"question": f"case {i:04d}"}, expected={"answer": i},
                metadata={"split_names": ["test"], "bucket": i % 2})
                for i in range(count)]
    DatasetVersionItem.no_workspace_objects.bulk_create(examples)
    examples.sort(key=lambda x: str(x.example_id))
    return dataset, version, examples


def run_for(version, examples, ordinal=1, status="completed", trials=1):
    identities = [{"example_id": str(e.example_id), "trial_number": t}
                  for e in examples for t in range(1, trials + 1)]
    # Preserve input order, but hash the canonical plan just like pin_run_plan.
    metadata = {"name": f"run {ordinal}", _RUN_KEY: {"plan": {
        "identities": identities, "plan_hash": _digest(_identities(identities))}}}
    return ExperimentRun.no_workspace_objects.create(
        dataset_version=version, task_kind="external", status=status,
        ordinal=ordinal, metadata=metadata, evaluator_versions=[])


def record(run, example, trial=1, status="completed", **kwargs):
    return ExperimentRunItem.no_workspace_objects.create(
        run=run, example_id=example.example_id, trial_number=trial,
        status=status, output={"text": "actual output"}, **kwargs)


def read(client, dataset, route="dataset-results", **params):
    return client.get(reverse(route, kwargs={"dataset_id": dataset.id}), params)


def result(response):
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["status"] is True
    return body["result"]


@pytest.mark.parametrize("data", [
    {"runs": str(uuid.uuid4()), "shape": "matrix", "page_size": 501},
    {"runs": str(uuid.uuid4()), "page_size": 1001},
    {"runs": str(uuid.uuid4()), "full": True},
    {"runs": str(uuid.uuid4()), "ids_only": True, "shape": "matrix"},
    {"runs": str(uuid.uuid4()), "agg": "best-of-fabricated"},
    {"runs": str(uuid.uuid4()), "filters": "not-json"},
    {"runs": str(uuid.uuid4()), "filters": '[{"type":[]}]'},
    {"runs": str(uuid.uuid4()), "filters": '[{"type":"meta","key":"x","op":[],"value":1}]'},
    {"runs": str(uuid.uuid4()), "filters": '[{"type":"meta","key":"x","op":"eq","value":NaN}]'},
    {"runs": str(uuid.uuid4()), "filters": '[{"type":"expression","expr":"x"}]'},
    {"runs": str(uuid.uuid4()), "filters": '[{"type":"rows","ids":["bad"]}]'},
    {"runs": str(uuid.uuid4()), "unexpected": True},
])
def test_rejects_unsupported_queries(data):
    with pytest.raises(ValidationError):
        DatasetResultsQuerySerializer(data=data).is_valid(raise_exception=True)


def test_catalog_rejects_unknown_includes():
    with pytest.raises(ValidationError):
        ResultsRunsQuerySerializer(data={"include": "credentials"}).is_valid(
            raise_exception=True)


def test_grid_matrix_ids_and_neighbors_share_1001_examples(
        auth_client, organization, workspace, user):
    ds, version, examples = seed(organization, workspace, user, 1001)
    run = run_for(version, examples)
    record(run, examples[0], cost=0, latency_ms=0)
    q = {"runs": str(run.id), "baseline": "none"}
    first = result(read(auth_client, ds, **q, page_size=1000))
    second = result(read(auth_client, ds, **q, page=1, page_size=1000))
    assert first["total"] == second["total"] == 1001
    assert first["pagination"]["has_more"] is True
    assert second["pagination"]["has_more"] is False
    assert first["summary"]["examples_total"] == 1001
    assert first["scope"]["complete"] is True
    ids = [r["example_id"] for r in first["rows"] + second["rows"]]
    matrix_ids = []
    for page in range(3):
        data = result(read(auth_client, ds, **q, shape="matrix", page=page,
                           page_size=500))
        matrix_ids.extend(r["row_id"] for r in data["rows"])
    assert ids == matrix_ids == [str(e.example_id) for e in examples]
    ids_pages = [result(read(auth_client, ds, **q, ids_only=True, page=p,
                            page_size=1000)) for p in range(2)]
    assert ids == sum([p["example_ids"] for p in ids_pages], [])
    full = result(read(auth_client, ds, **q, full=True, example_id=ids[1000]))
    assert full["rows"][0]["neighbors"] == {
        "prev": ids[999], "next": None, "position": 1001, "total": 1001}
    assert full["rows"][0]["results"][str(run.id)]["status"] == "not_reached"


def test_catalog_continues_past_500_and_has_both_baseline_aliases(
        auth_client, organization, workspace, user):
    ds, version, examples = seed(organization, workspace, user, 1)
    for i in range(501):
        run_for(version, examples, ordinal=i + 1)
    first = result(read(auth_client, ds, "dataset-results-runs", limit=500))
    last = result(read(auth_client, ds, "dataset-results-runs", limit=500, offset=500))
    assert first["count"] == last["count"] == 501
    assert len(first["results"]) == 500 and len(last["results"]) == 1
    assert first["next"] == 500 and last["has_more"] is False
    assert first["pinned_run_id"] is None and first["baseline"]["run_id"] is None
    assert first["results"][0]["trials"] is None  # no invented persisted setting
    assert first["results"][0]["concurrency"] is None
    assert first["results"][0]["ordinal"] == 501


def test_all_ids_are_validated_before_returning_counts(
        auth_client, organization, workspace, user):
    ds, version, examples = seed(organization, workspace, user)
    other, other_version, other_examples = seed(organization, workspace, user)
    own = run_for(version, examples)
    foreign = run_for(other_version, other_examples)
    for extra in ({"runs": f"{own.id},{foreign.id}"},
                  {"runs": str(own.id), "baseline": str(foreign.id)},
                  {"runs": str(own.id), "full": True,
                   "example_id": str(other_examples[0].example_id)},
                  {"runs": str(own.id), "filters": json.dumps([
                      {"type": "rows", "ids": [str(other_examples[0].example_id)]}])}):
        response = read(auth_client, ds, **extra)
        assert response.status_code == 404
        assert "rows" not in response.json().get("result", {})
    response = read(auth_client, ds, "dataset-results-runs",
                    dataset_version_id=str(other_version.id))
    assert response.status_code == 404


def test_deleted_run_and_incompatible_version_fail_closed(
        auth_client, organization, workspace, user):
    ds, version, examples = seed(organization, workspace, user)
    own = run_for(version, examples)
    second_version = DatasetVersion.no_workspace_objects.create(dataset=ds)
    other = run_for(second_version, examples, ordinal=2)
    assert read(auth_client, ds, runs=f"{own.id},{other.id}").status_code == 409
    ExperimentRun.no_workspace_objects.filter(id=own.id).update(deleted=True)
    assert read(auth_client, ds, runs=str(own.id)).status_code == 404


def test_filter_applies_before_paging_and_full_neighbors(
        auth_client, organization, workspace, user):
    ds, version, examples = seed(organization, workspace, user, 5)
    run = run_for(version, examples)
    chosen = [str(examples[i].example_id) for i in (0, 2, 4)]
    filters = json.dumps([{"type": "rows", "ids": chosen}])
    q = {"runs": str(run.id), "filters": filters, "sort": "order"}
    grid = result(read(auth_client, ds, **q, page_size=2))
    assert grid["total"] == 3 and grid["summary"]["examples_total"] == 5
    full = result(read(auth_client, ds, **q, full=True, example_id=chosen[1]))
    assert full["rows"][0]["neighbors"] == {
        "prev": chosen[0], "next": chosen[2], "position": 2, "total": 3}


def test_matrix_preserves_pending_error_infra_and_zero_cost(
        auth_client, organization, workspace, user):
    ds, version, examples = seed(organization, workspace, user, 1)
    run = run_for(version, examples, status="running", trials=4)
    record(run, examples[0], trial=1, cost=0, latency_ms=0)
    record(run, examples[0], trial=2, status="failed", error={"kind": "task_error"})
    record(run, examples[0], trial=3, status="failed", error={"kind": "infra"})
    cell = result(read(auth_client, ds, runs=str(run.id), shape="matrix"))["rows"][0]["cells"][str(run.id)]
    assert cell["trials"] == [None, "error", "infra", None]
    assert cell["cost_usd"] == [0, None, None, None]
    assert cell["wall_ms"] == [0, None, None, None]
    assert [x["status"] for x in cell["trace_summary"]] == [
        "completed", "error", "infra", "queued"]
    assert cell["trial_numbers"] == [1, 2, 3, 4]
    # A queued fourth trial must not hide errors in already recorded trials.
    filtered = result(read(auth_client, ds, runs=str(run.id), show="errors"))
    assert filtered["total"] == 1


@pytest.mark.parametrize("value", [0, 1, False, True])
def test_trial_value_preserves_binary_assessment(value):
    assert trial_value({"status": "completed", "scores": {"m": {
        "status": "assessed", "value": value}}, "error": None}, "m") == value


def test_get_does_not_backfill_unknown_plan(auth_client, organization, workspace, user):
    ds, version, _ = seed(organization, workspace, user)
    run = ExperimentRun.no_workspace_objects.create(dataset_version=version)
    before = ExperimentRunItem.no_workspace_objects.count()
    assert read(auth_client, ds, runs=str(run.id)).status_code == 409
    assert ExperimentRunItem.no_workspace_objects.count() == before


def test_read_scope_cannot_be_overridden_by_query(auth_client, organization, workspace, user):
    ds, _, _ = seed(organization, workspace, user)
    response = read(auth_client, ds, "dataset-results-runs", organization_id=str(organization.id))
    assert response.status_code == 400


@pytest.mark.parametrize("other_organization", [False, True])
def test_foreign_org_and_workspace_reject_every_read_shape(
        auth_client, organization, workspace, user, other_organization):
    from accounts.models.organization import Organization
    from accounts.models.workspace import Workspace
    org = Organization.objects.create(name="Other tenant") if other_organization else organization
    ws = Workspace.objects.create(name="Other workspace", organization=org,
                                  is_default=False, is_active=True, created_by=user)
    ds, version, examples = seed(org, ws, user)
    run = run_for(version, examples)
    assert read(auth_client, ds, "dataset-results-runs").status_code == 404
    for extra in ({}, {"shape": "matrix"}, {"ids_only": True},
                  {"full": True, "example_id": str(examples[0].example_id)}):
        response = read(auth_client, ds, runs=str(run.id), **extra)
        assert response.status_code == 404
        assert str(run.id) not in response.content.decode()


def test_unauthenticated_read_refused(api_client, organization, workspace, user):
    ds, version, examples = seed(organization, workspace, user)
    run = run_for(version, examples)
    assert read(api_client, ds, "dataset-results-runs").status_code in (401, 403)
    assert read(api_client, ds, runs=str(run.id)).status_code in (401, 403)


def test_baseline_aliases_and_explicit_none(auth_client, organization, workspace, user):
    ds, version, examples = seed(organization, workspace, user)
    run = run_for(version, examples)
    url = reverse("dataset-baseline", kwargs={"dataset_id": ds.id})
    assert auth_client.put(url, {"run_id": str(run.id)}, format="json").status_code == 200
    catalog = result(read(auth_client, ds, "dataset-results-runs"))
    assert catalog["pinned_run_id"] == catalog["baseline"]["run_id"] == str(run.id)
    grid = result(read(auth_client, ds, runs=str(run.id), baseline="none"))
    assert grid["summary"]["baseline_run_id"] is None


def test_config_is_allowlisted_and_external_metadata_is_not_enforcement(
        auth_client, organization, workspace, user):
    ds, version, examples = seed(organization, workspace, user)
    run = run_for(version, examples)
    # Synthetic sentinels, not credentials; assert arbitrary config never echoes.
    run.model_config = {"model": {"name": "fixture-model", "headers": {"X-Test": "hidden-sentinel"}},
                        "tools": [{"config": {"private": "hidden-sentinel"}}]}
    run.metadata["effective_settings"] = {"trials": 99, "concurrency": 99}
    run.save()
    response = read(auth_client, ds, "dataset-results-runs", include="config")
    data = result(response)["results"][0]
    assert "hidden-sentinel" not in response.content.decode()
    assert data["config"]["model"]["name"] == "fixture-model"
    assert data["effective_settings"] is None


def test_output_bounds_fail_explicitly(auth_client, organization, workspace, user):
    ds, version, examples = seed(organization, workspace, user, 1)
    run = run_for(version, examples)
    item = record(run, examples[0])
    item.output = "x" * (5 * 1024 * 1024)
    item.save()
    response = read(auth_client, ds, runs=str(run.id), full=True,
                    example_id=str(examples[0].example_id))
    assert response.status_code == 503
    assert response.json()["status"] is False


def test_missing_eval_does_not_become_pass_and_mixed_evals_are_not_flaky():
    from model_hub.services.results_grid import assessed_scores
    from model_hub.services.results_selection import _flaky
    a, b, version = [str(uuid.uuid4()) for _ in range(3)]
    bindings = {key: {"evaluator_version_id": version, "output_type_normalized": "boolean"}
                for key in (a, b)}
    scores = assessed_scores([{"metric_id": a, "evaluator_version_id": version,
                               "status": "assessed", "value": 1}], bindings)
    assert trial_value({"status": "completed", "scores": scores}) is None
    cell = {"trials_detail": [{"scores": {a: {"passed": True}, b: {"passed": False}}}]}
    assert _flaky(cell) is False


# Fix-forward regressions use the persisted external definition and run pinning.
def pinned_run(version, examples, user, output_type="pass_fail", higher=True, trials=1):
    from model_hub.services.external_metrics import (
        ExternalMetricDefinition, create_or_reuse_external_metric,
    )
    from model_hub.services.experiment_run_creation import create_experiment_run
    binding = create_or_reuse_external_metric(
        dataset=version.dataset, user=user, definition=ExternalMetricDefinition(
            name=f"fixture-{output_type}-{higher}", output_type=output_type,
            source_hash="a" * 64, higher_is_better=higher))
    run = create_experiment_run(
        run_id=uuid.uuid4(), dataset_version=version, created_by=user,
        task_kind="external", evaluator_metrics=[binding.metric],
        expected_items=[(e.example_id, n) for e in examples for n in range(1, trials + 1)])
    ExperimentRun.no_workspace_objects.filter(pk=run.id).update(status="completed")
    run.refresh_from_db()
    return run, binding


def score_record(run, example, binding, value, **kwargs):
    return record(run, example, score_refs=[{
        "metric_id": str(binding.metric.id), "evaluator_version_id": str(binding.version.id),
        "status": "assessed", "value": value}], **kwargs)


def test_default_two_run_sort_orders_all_read_shapes(auth_client, organization, workspace, user):
    ds, version, examples = seed(organization, workspace, user, 4)
    base, binding = pinned_run(version, examples, user)
    candidate, _ = pinned_run(version, examples, user)
    for example, bv, cv in zip(examples, [0, 1, 1, 0], [1, 1, 0, 0]):
        score_record(base, example, binding, bv)
        score_record(candidate, example, binding, cv)
    # defaultSortFor({runCount: 2, baselineId}) -> regressed; runResultsParams sends it.
    query = {"runs": f"{base.id},{candidate.id}", "baseline": str(base.id), "sort": "regressed"}
    expected = [str(examples[i].example_id) for i in (2, 0, 1, 3)]
    for shape, collection, key in [({}, "rows", "example_id"),
            ({"shape": "matrix"}, "rows", "row_id"), ({"ids_only": True}, "example_ids", None)]:
        pages = [result(read(auth_client, ds, **query, **shape, page=p, page_size=2)) for p in (0, 1)]
        rows = pages[0][collection] + pages[1][collection]
        assert ([r[key] for r in rows] if key else rows) == expected
        assert pages[0]["total"] == pages[1]["total"] == 4
    full = result(read(auth_client, ds, **query, full=True, example_id=expected[1]))
    assert full["rows"][0]["neighbors"] == {"prev": expected[0], "next": expected[2], "position": 2, "total": 4}
    candidate_only = result(read(auth_client, ds, **{**query, "runs": str(candidate.id)}, ids_only=True))
    assert candidate_only["example_ids"] == expected
    assert read(auth_client, ds, **{**query, "baseline": "none"}).status_code == 400


def test_persisted_pass_fail_without_threshold_agrees_everywhere(auth_client, organization, workspace, user):
    ds, version, examples = seed(organization, workspace, user, 3)
    run, binding = pinned_run(version, examples, user)
    pin = run.evaluator_versions[0]
    assert pin["output_type_normalized"] == "pass_fail" and pin["pass_threshold"] is None
    for example, value in zip(examples, [0, 1]):
        score_record(run, example, binding, value)
    query = {"runs": str(run.id), "baseline": "none"}
    for measure in ("pass_at_1", str(binding.metric.id)):
        rows = result(read(auth_client, ds, **query, shape="matrix", measure=measure))["rows"]
        assert [r["cells"][str(run.id)]["trials"] for r in rows] == [[0], [1], [None]]
    for extra in ({"show": "failed"}, {"filters": json.dumps([{
            "type": "fails", "metric": str(binding.metric.id), "run": str(run.id)}])}):
        rows = result(read(auth_client, ds, **query, **extra))["rows"]
        assert [r["example_id"] for r in rows] == [str(examples[0].example_id)]


@pytest.mark.parametrize("output_type,higher,expected_type", [("pass_fail", True, "pass_fail"),
    ("percentage", False, "score"), ("numeric", False, "score")])
def test_metric_definition_matches_frontend_contract(auth_client, organization, workspace, user,
                                                      output_type, higher, expected_type):
    ds, version, examples = seed(organization, workspace, user, 1)
    run, binding = pinned_run(version, examples, user, output_type, higher)
    definition = result(read(auth_client, ds, "dataset-results-runs"))["metric_defs"][0]
    assert definition["key"] == str(binding.metric.id)
    assert definition["kind"] == "eval" and definition["output_type"] == expected_type
    assert definition["higher_is_better"] is higher
    assert definition["versions"][str(run.id)] == str(binding.version.id)


@pytest.mark.parametrize("expected,cost,latency", [({"answer": 42}, 0, 0), (42, 0.25, 17),
    ({"value": "literal"}, None, None), (False, 0, 0), (None, None, None)])
def test_expected_and_drawer_telemetry_adapters(auth_client, organization, workspace, user,
                                               expected, cost, latency):
    ds, version, examples = seed(organization, workspace, user, 1)
    DatasetVersionItem.no_workspace_objects.filter(pk=examples[0].pk).update(expected=expected)
    run = run_for(version, examples)
    record(run, examples[0], cost=cost, latency_ms=latency)
    for full in (False, True):
        extra = {"full": True, "example_id": str(examples[0].example_id)} if full else {}
        row = result(read(auth_client, ds, runs=str(run.id), **extra))["rows"][0]
        assert row["expected"]["value"] == expected
        assert type(row["expected"]["value"]) is type(expected)
        cell = row["results"][str(run.id)]
        assert cell["cost"] == cost and cell["latency_ms"] == latency
        if full:
            assert cell["trials_detail"][0]["cost"] == cost
            assert cell["trials_detail"][0]["latency_ms"] == latency
    matrix = result(read(auth_client, ds, runs=str(run.id), shape="matrix"))["rows"][0]["cells"][str(run.id)]
    assert matrix["cost_usd"] == [cost] and matrix["wall_ms"] == [latency]


def test_progress_counts_assessed_examples_not_claimed_trials(auth_client, organization, workspace, user):
    ds, version, examples = seed(organization, workspace, user, 3)
    run, binding = pinned_run(version, examples, user, trials=2)
    ExperimentRun.no_workspace_objects.filter(pk=run.id).update(status="running")
    for n in (1, 2):
        score_record(run, examples[0], binding, 1, trial=n)
        record(run, examples[1], trial=n, status="running")
        record(run, examples[2], trial=n, status="failed", error={"kind": "task_error"})
    for catalog in (True, False):
        payload = result(read(auth_client, ds, "dataset-results-runs")) if catalog else result(read(auth_client, ds, runs=str(run.id)))
        progress = (payload["results"] if catalog else payload["summary"]["runs"])[0]["progress"]
        assert {k: progress[k] for k in ("total", "done", "scored", "errors")} == {"total": 3, "done": 2, "scored": 1, "errors": 1}
        assert progress["status"] == "Running"
    ExperimentRun.no_workspace_objects.filter(pk=run.id).update(status="completed")
    terminal = result(read(auth_client, ds, "dataset-results-runs"))["results"][0]
    assert terminal["progress"]["status"] in {"Completed", "PartialCompleted", "Failed"}
    assert terminal["progress"]["done"] == 2  # status alone does not invent the third row


# r2 regressions: real P01 snapshots/bindings and the real shared R3/R5 selector.
from model_hub.tests import test_native_run_binding as native_helpers

native_world = native_helpers.native_world
legacy_native_world = native_helpers.legacy_native_world


def population(dataset, runs, **kwargs):
    from model_hub.services.results_selection import select_analysis_population
    return select_analysis_population(dataset=dataset, runs=runs, **{
        "filters": [], "baseline_id": "none", "max_items": 40000, **kwargs})


def test_native_projection_passes_authorized_dataset_and_translates_errors(native_world):
    from model_hub.services.results_catalog import native_projection, ResultsReadError
    w = native_world
    run = native_helpers.bind(w)
    before = (ExperimentRun.no_workspace_objects.count(), ExperimentRunItem.no_workspace_objects.count())
    projected = native_projection(run)
    assert projected["run_id"] == str(run.id)
    assert projected["dataset_id"] == str(w["dataset"].id)
    assert projected["effective_settings"] == {"trials": 1, "concurrency": 10}
    Dataset.no_workspace_objects.filter(pk=w["snapshot"].pk).update(deleted=True)
    with pytest.raises(ResultsReadError) as error:
        native_projection(run)
    assert error.value.status == 404
    assert before == (ExperimentRun.no_workspace_objects.count(), ExperimentRunItem.no_workspace_objects.count())


@pytest.mark.parametrize("legacy", [False, True])
def test_native_snapshot_evaluators_project_on_all_reads(auth_client, request, legacy):
    w = request.getfixturevalue("legacy_native_world" if legacy else "native_world")
    metric, pin = native_helpers.add_native_eval(w)
    run = native_helpers.bind(w)
    before = (ExperimentRun.no_workspace_objects.count(), ExperimentRunItem.no_workspace_objects.count())
    catalog = result(read(auth_client, w["dataset"], "dataset-results-runs", include="config"))
    definition = catalog["metric_defs"][0]
    assert definition["key"] == str(metric.id) and definition["name"] == metric.name
    assert definition["versions"][str(run.id)] == str(pin.id)
    assert catalog["results"][0]["effective_settings"]["trials"] == 1
    grid = result(read(auth_client, w["dataset"], runs=str(run.id), baseline="none"))
    assert grid["summary"]["metrics"][0]["key"] == str(metric.id)
    selected = population(w["dataset"], [run])
    assert selected["example_ids"] == [row["example_id"] for row in grid["rows"]]
    response = auth_client.get(reverse("dataset-results-analysis", kwargs={"dataset_id": w["dataset"].id}),
                               {"runs": str(run.id), "baseline": "none"})
    analysis = result(response)
    assert analysis["examples_total"] == analysis["examples_in_view"] == grid["total"] == 2
    assert analysis["metrics"][0]["key"] == str(metric.id)
    assert metric.dataset_id == w["snapshot"].id != w["dataset"].id
    assert before == (ExperimentRun.no_workspace_objects.count(), ExperimentRunItem.no_workspace_objects.count())


# A soft-deleted evaluator, template or pinned version stays readable as
# retired (same rule as external runs; test_native_eval_delete_read.py and
# test_native_eval_delete_followups.py); every other break still 404s.
@pytest.mark.parametrize("broken", ["metric_source", "metric_dataset",
    "metric_workspace", "version_template",
    "snapshot_deleted", "snapshot_version", "variant_deleted", "config_deleted",
    "output_deleted", "binding_missing", "identity_hash"])
def test_native_evaluator_provenance_fails_before_names_or_counts(auth_client, native_world, broken):
    from accounts.models.workspace import Workspace
    from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion, UserEvalMetric
    from model_hub.models.native_experiment_runs import NativeExperimentSnapshot, NativeRunBinding
    from model_hub.services.results_catalog import metric_definitions, ResultsReadError
    w = native_world
    metric, pin = native_helpers.add_native_eval(w)
    run = native_helpers.bind(w)
    if broken == "metric_source":
        UserEvalMetric.no_workspace_objects.filter(pk=metric.pk).update(source_id=str(uuid.uuid4()))
    elif broken == "metric_dataset":
        other, _, _ = seed(w["dataset"].organization, w["dataset"].workspace, w["created_by"], 1)
        UserEvalMetric.no_workspace_objects.filter(pk=metric.pk).update(dataset=other)
    elif broken == "metric_workspace":
        other = Workspace.no_workspace_objects.create(name="Other metric scope",
            organization=w["dataset"].organization, created_by=w["created_by"], is_default=False)
        UserEvalMetric.no_workspace_objects.filter(pk=metric.pk).update(workspace=other)
    elif broken == "version_template":
        other = EvalTemplate.no_workspace_objects.create(name="unrelated", owner="user", eval_type="llm",
            organization=w["dataset"].organization, workspace=w["dataset"].workspace)
        EvalTemplateVersion.no_workspace_objects.filter(pk=pin.pk).update(eval_template=other)
    elif broken == "snapshot_deleted":
        Dataset.no_workspace_objects.filter(pk=w["snapshot"].pk).update(deleted=True)
    elif broken == "snapshot_version":
        other = DatasetVersion.no_workspace_objects.create(dataset=w["dataset"])
        NativeExperimentSnapshot.objects.filter(experiment=w["experiment"]).update(dataset_version=other)
    elif broken in {"variant_deleted", "config_deleted", "output_deleted"}:
        obj = w[{"variant_deleted": "variant", "config_deleted": "config",
                 "output_deleted": "output_column"}[broken]]
        type(obj).no_workspace_objects.filter(pk=obj.pk).update(deleted=True)
    elif broken == "binding_missing":
        NativeRunBinding.objects.filter(run=run).delete()
    else:
        NativeRunBinding.objects.filter(run=run).update(request_hash="0" * 64)
    with pytest.raises(ResultsReadError) as error:
        metric_definitions([run])
    assert error.value.status == 404
    for route in ("dataset-results-runs", "dataset-results", "dataset-results-analysis"):
        response = read(auth_client, w["dataset"], route,
                        **({} if route == "dataset-results-runs" else {"runs": str(run.id)}))
        assert response.status_code == 404, response.content
        assert metric.name not in response.content.decode()
        assert "examples_total" not in response.content.decode()


def test_external_runs_cannot_reuse_native_snapshot_evaluators(native_world):
    from model_hub.services.results_catalog import checked_runs, metric_definitions, ResultsReadError
    w = native_world
    native_helpers.add_native_eval(w)
    native = native_helpers.bind(w)
    examples = list(native.dataset_version.items.all())
    external = run_for(native.dataset_version, examples, ordinal=2)
    external.evaluator_versions = native.evaluator_versions
    external.save(update_fields=["evaluator_versions"])
    for operation in (lambda: checked_runs(w["dataset"], [external.id]),
                      lambda: metric_definitions([external])):
        with pytest.raises(ResultsReadError) as error:
            operation()
        assert error.value.status == 404


def test_analysis_population_is_full_ordered_filtered_identity_set(auth_client, organization, workspace, user):
    ds, version, examples = seed(organization, workspace, user, 1001)
    run = run_for(version, examples)
    filters = [{"type": "meta", "key": "bucket", "op": "eq", "value": 0}]
    selected = population(ds, [run], filters=filters)
    expected = [str(e.example_id) for e in examples if e.metadata["bucket"] == 0]
    assert selected == {"example_ids": expected, "examples_total": 1001, "complete": True}
    grid = result(read(auth_client, ds, runs=str(run.id), filters=json.dumps(filters),
                       baseline="none", ids_only=True, page_size=1000))
    assert selected["example_ids"] == grid["example_ids"]
    analysis = result(read(auth_client, ds, "dataset-results-analysis", runs=str(run.id),
                           baseline="none", filters=json.dumps(filters)))
    assert analysis["examples_total"] == 1001 and analysis["examples_in_view"] == len(expected)
    empty = population(ds, [run], filters=[{"type": "rows", "ids": []}])
    assert empty == {"example_ids": [], "examples_total": 1001, "complete": True}


@pytest.mark.parametrize("kind", ["rows", "meta", "split", "score", "fails", "error", "flaky", "latency", "cost"])
def test_analysis_filter_dialect_matches_r3(auth_client, organization, workspace, user, kind):
    ds, version, examples = seed(organization, workspace, user, 3)
    run, binding = pinned_run(version, examples, user, trials=2)
    for n, value in ((1, 0), (2, 1)):
        score_record(run, examples[0], binding, value, trial=n, cost=0.5, latency_ms=10)
        record(run, examples[1], trial=n, status="failed", error={"kind": "infra"})
    pills = {"rows": {"ids": [str(examples[0].example_id)]},
        "meta": {"key": "bucket", "op": "eq", "value": 0}, "split": {"values": ["test"]},
        "score": {"metric": str(binding.metric.id), "op": "gte", "value": 0.5},
        "fails": {"metric": str(binding.metric.id)}, "error": {}, "flaky": {},
        "latency": {"op": "gt", "value": 1}, "cost": {"op": "gt", "value": 0}}
    filters = [{"type": kind, **pills[kind]}]
    selected = population(ds, [run], filters=filters)
    grid = result(read(auth_client, ds, runs=str(run.id), baseline="none",
                       filters=json.dumps(filters), ids_only=True))
    assert selected["example_ids"] == grid["example_ids"]
    assert selected["examples_total"] == 3 and selected["complete"] is True


@pytest.mark.parametrize("case,status", [("unsupported", 400), ("foreign_run", 404),
    ("foreign_baseline", 404), ("foreign_row", 404), ("foreign_metric", 404),
    ("deleted_run", 404), ("incompatible", 409), ("work", 503), ("invalid_cap", 400)])
def test_analysis_selector_preserves_http_errors(organization, workspace, user, case, status):
    from rest_framework.exceptions import APIException
    ds, version, examples = seed(organization, workspace, user, 3)
    run = run_for(version, examples)
    other, other_version, other_examples = seed(organization, workspace, user, 1)
    foreign = run_for(other_version, other_examples)
    kwargs = {}
    runs = [run]
    if case == "unsupported": kwargs["filters"] = [{"type": "unsupported"}]
    elif case == "foreign_run": runs.append(foreign)
    elif case == "foreign_baseline": kwargs["baseline_id"] = str(foreign.id)
    elif case == "foreign_row": kwargs["filters"] = [{"type": "rows", "ids": [str(other_examples[0].example_id)]}]
    elif case == "foreign_metric": kwargs["filters"] = [{"type": "fails", "metric": str(uuid.uuid4())}]
    elif case == "deleted_run": ExperimentRun.no_workspace_objects.filter(pk=run.pk).update(deleted=True)
    elif case == "incompatible": runs.append(run_for(version, examples[:1], ordinal=2))
    elif case == "work": kwargs["max_items"] = 2
    else: kwargs["max_items"] = True
    with pytest.raises(APIException) as error:
        population(ds, runs, **kwargs)
    assert error.value.status_code == status


def test_analysis_limits_count_trials_before_filtering(organization, workspace, user):
    from rest_framework.exceptions import APIException
    ds, version, examples = seed(organization, workspace, user, 2)
    runs = [run_for(version, examples, ordinal=n, trials=2) for n in (1, 2)]
    assert population(ds, runs, max_items=8)["examples_total"] == 2
    with pytest.raises(APIException) as error:
        population(ds, runs, max_items=7, filters=[{"type": "rows", "ids": []}])
    assert error.value.status_code == 503
    large = run_for(version, examples[:1], ordinal=3, trials=10001)
    with pytest.raises(APIException) as error:
        population(ds, [large])
    assert error.value.status_code == 503


@pytest.mark.parametrize("sealed,expected", [(False, "partial"), (True, "PartialCompleted")])
def test_partial_progress_uses_persisted_seal(auth_client, organization, workspace, user, sealed, expected):
    from django.utils import timezone
    ds, version, examples = seed(organization, workspace, user, 3)
    run = run_for(version, examples, status="partial")
    record(run, examples[0])
    ExperimentRun.no_workspace_objects.filter(pk=run.pk).update(completed_at=timezone.now() if sealed else None)
    catalog = result(read(auth_client, ds, "dataset-results-runs"))["results"][0]
    summary = result(read(auth_client, ds, runs=str(run.id)))["summary"]["runs"][0]
    for row in (catalog, summary):
        assert row["status"] == "partial" and row["progress"]["status"] == expected
        assert row["progress"]["done"] == 1 and row["progress"]["total"] == 3
