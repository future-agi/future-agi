"""W10-EXPERIMENTS-DATASETS-L01: catalog groups match immutable reader plans."""
import pytest

from model_hub.models.dataset_versions import DatasetVersion
from model_hub.serializers.results_runs import CanonicalRunSerializer
from model_hub.services import results_catalog
from model_hub.services.experiment_run_writes import _RUN_KEY, RunWriteError
from model_hub.tests.test_results_launch_reads import read, result, run_for, seed

pytestmark = [pytest.mark.django_db,
              pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")]


def test_catalog_separates_subset_retry_without_losing_original_runs(
        auth_client, organization, workspace, user):
    dataset, version, examples = seed(organization, workspace, user, count=2)
    original = run_for(version, examples, ordinal=1)
    compatible = run_for(version, list(reversed(examples)), ordinal=2)
    retry = run_for(version, examples[:1], ordinal=3)
    other_subset = run_for(version, examples[1:], ordinal=4)

    # Reordered identities remain a valid plan, not unavailable provenance.
    original_plan = original.metadata[_RUN_KEY]["plan"]
    compatible_plan = compatible.metadata[_RUN_KEY]["plan"]
    assert compatible_plan["identities"] == list(reversed(original_plan["identities"]))
    assert compatible_plan["plan_hash"] == original_plan["plan_hash"]

    rows = result(read(auth_client, dataset, "dataset-results-runs"))["results"]
    groups = {row["run_id"]: row["comparison_group"] for row in rows}
    assert set(groups) == {str(run.id) for run in (original, compatible, retry, other_subset)}
    assert groups[str(original.id)] == groups[str(compatible.id)]
    assert len({groups[str(run.id)] for run in (original, retry, other_subset)}) == 3
    assert all(groups.values())
    serialized = CanonicalRunSerializer(rows, many=True).data
    assert {row["run_id"]: row["comparison_group"] for row in serialized} == groups

    # Group selection, not relaxing the reader, makes both histories readable.
    mixed = read(auth_client, dataset, runs=f"{original.id},{retry.id}", baseline="none")
    assert mixed.status_code == 409
    result(read(auth_client, dataset, runs=str(retry.id), baseline="none"))
    result(read(auth_client, dataset, runs=f"{original.id},{compatible.id}", baseline="none"))


def test_comparison_groups_include_version_trials_and_complete_evaluator_bindings(
        organization, workspace, user, monkeypatch):
    dataset, version, examples = seed(organization, workspace, user, count=2)
    original = run_for(version, examples)
    more_trials = run_for(version, examples, ordinal=2, trials=2)
    other_version = DatasetVersion.no_workspace_objects.create(dataset=dataset)
    version_run = run_for(other_version, examples, ordinal=3)

    def group(run):
        return results_catalog.project_run(run, counts={}, progress={})["comparison_group"]

    original_group = group(original)
    assert len({original_group, group(more_trials), group(version_run)}) == 3
    binding = {"metric": {"metric_id": "metric", "evaluator_version_id": "version-a",
                          "pass_threshold": 0.5}}
    monkeypatch.setattr(results_catalog, "_bindings", lambda _run: binding)
    first_binding_group = group(original)
    assert first_binding_group != original_group
    binding["metric"]["pass_threshold"] = 0.9
    assert group(original) != first_binding_group


def test_unavailable_plan_does_not_claim_a_compatible_group(
        organization, workspace, user, monkeypatch):
    _, version, examples = seed(organization, workspace, user, count=2)
    run = run_for(version, examples)

    def unavailable(_run):
        raise RunWriteError("Immutable plan unavailable")

    monkeypatch.setattr(results_catalog, "_planned", unavailable)
    row = results_catalog.project_run(run, counts={}, progress={})
    assert row["comparison_group"] is None
    assert row["availability"]["provenance"]
