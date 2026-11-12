"""J03.REST-004: retiring a definition must not erase immutable Results evidence.

Real API/ORM regressions; no mocked authorization, catalog, or deletion route.
Authored before the production candidate; execution is deferred to the runner.
"""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.urls import reverse

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.evals_metric import EvalTemplateVersion, UserEvalMetric
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.services.experiment_run_creation import create_experiment_run
from model_hub.services.experiment_run_writes import RunWriteError
from model_hub.services.results_catalog import ResultsReadError, validate_evaluators
from model_hub.tests import test_results_launch_reads as reads
from tfc.middleware.workspace_context import set_workspace_context, workspace_context

pytestmark = [pytest.mark.django_db,
              pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")]


@pytest.fixture
def world(organization, workspace, user):
    dataset, version, examples = reads.seed(organization, workspace, user, 1)
    run, binding = reads.pinned_run(version, examples, user)
    score = reads.score_record(run, examples[0], binding, 0)
    healthy = reads.run_for(version, examples, ordinal=2)
    reads.record(healthy, examples[0])
    return SimpleNamespace(dataset=dataset, version=version, examples=examples,
                           run=run, healthy=healthy, binding=binding, score=score)


def retire(client, world):
    response = client.delete(reverse("delete-evals", kwargs={
        "dataset_id": world.dataset.id, "eval_id": world.binding.metric.id,
    }), {"delete_column": True}, format="json")
    assert response.status_code == 200, response.content
    assert response.json()["status"] is True
    assert UserEvalMetric._base_manager.get(pk=world.binding.metric.pk).deleted


def catalog(client, world, **query):
    return reads.result(reads.read(client, world.dataset, "dataset-results-runs", **query))


def by_id(page):
    return {run["run_id"]: run for run in page["results"]}


def new_run(world, user):
    return create_experiment_run(
        run_id=uuid4(), dataset_version=world.version, created_by=user,
        task_kind="external", evaluator_metrics=[world.binding.metric],
        expected_items=[(world.examples[0].example_id, 1)],
    )


def assert_retired(projected, binding):
    assert projected["availability"]["evaluators"]
    assert projected["eval_versions"] == [{
        "metric_id": str(binding.metric.id),
        "evaluator_version_id": str(binding.version.id), "retired": True,
    }]


def test_delete_keeps_mixed_catalog_scores_and_every_results_shape(auth_client, world, user):
    w = world
    metric_id = str(w.binding.metric.id)
    old_pin = deepcopy(w.run.evaluator_versions)
    old_metadata = deepcopy(w.run.metadata)
    old_scores = deepcopy(w.score.score_refs)
    before_counts = (ExperimentRun.no_workspace_objects.count(),
                     ExperimentRunItem.no_workspace_objects.count())
    assert not Column.no_workspace_objects.filter(
        dataset=w.dataset, source_id=metric_id).exists()
    before = catalog(auth_client, w, include="metrics")
    assert set(by_id(before)) == {str(w.run.id), str(w.healthy.id)}
    retire(auth_client, w)

    # RED on the base: the first GET after the successful DELETE returns 404.
    after = catalog(auth_client, w, include="metrics")
    assert after["count"] == before["count"] == 2
    projected = by_id(after)
    assert set(projected) == set(by_id(before))
    assert_retired(projected[str(w.run.id)], w.binding)
    assert projected[str(w.healthy.id)]["availability"]["evaluators"] is None
    assert projected[str(w.healthy.id)]["eval_versions"] == []
    assert projected[str(w.run.id)]["metrics"] == by_id(before)[str(w.run.id)]["metrics"]
    assert projected[str(w.run.id)]["metrics"][metric_id]["value"] == 0
    assert after["metric_defs"] == before["metric_defs"]
    assert len(catalog(auth_client, w)["results"]) == 2  # default page, no includes
    assert catalog(auth_client, w, baseline=str(w.run.id))["count"] == 2

    for params in ({}, {"shape": "matrix"}, {"ids_only": True},
                   {"full": True, "example_id": str(w.examples[0].example_id)}):
        data = reads.result(reads.read(auth_client, w.dataset, runs=str(w.run.id), **params))
        assert_retired(data["summary"]["runs"][0], w.binding)
    grid = reads.result(reads.read(auth_client, w.dataset, runs=str(w.run.id)))
    assert grid["rows"][0]["results"][str(w.run.id)]["scores"][metric_id]["value"] == 0
    reads.result(reads.read(auth_client, w.dataset, runs=str(w.healthy.id)))

    # The same stale in-memory metric remains forbidden for a NEW run.
    with pytest.raises(RunWriteError, match="evaluator binding"):
        new_run(w, user)
    w.run.refresh_from_db()
    w.score.refresh_from_db()
    assert w.run.evaluator_versions == old_pin and w.run.metadata == old_metadata
    assert w.score.score_refs == old_scores
    assert before_counts == (ExperimentRun.no_workspace_objects.count(),
                             ExperimentRunItem.no_workspace_objects.count())


def test_live_evaluator_positive_control(auth_client, world, user):
    page = catalog(auth_client, world, include="metrics")
    projected = by_id(page)[str(world.run.id)]
    assert projected["availability"]["evaluators"] is None
    assert projected["eval_versions"][0]["retired"] is False
    admitted = new_run(world, user)
    assert admitted.evaluator_versions[0]["evaluator_version_id"] == str(world.binding.version.id)
    reads.result(reads.read(auth_client, world.dataset, runs=str(world.run.id)))


@pytest.mark.parametrize("definition", ["template", "version"])
def test_archived_definition_is_readable_but_not_admitted(auth_client, world, user, definition):
    obj = world.binding.metric.template if definition == "template" else world.binding.version
    type(obj)._base_manager.filter(pk=obj.pk).update(deleted=True)
    projected = by_id(catalog(auth_client, world))[str(world.run.id)]
    assert_retired(projected, world.binding)
    reads.result(reads.read(auth_client, world.dataset, runs=str(world.run.id)))
    with pytest.raises(RunWriteError):
        new_run(world, user)


def test_retired_version_is_reported_per_pin_not_per_metric(auth_client, world, user):
    w = world
    newer = EvalTemplateVersion.objects.create_version(
        w.binding.metric.template, user=user, organization=w.dataset.organization,
        workspace=w.dataset.workspace, config_snapshot=w.binding.version.config_snapshot,
    )
    UserEvalMetric.no_workspace_objects.filter(pk=w.binding.metric.pk).update(pinned_version=newer)
    current = new_run(w, user)
    EvalTemplateVersion._base_manager.filter(pk=w.binding.version.pk).update(deleted=True)
    projected = by_id(catalog(auth_client, w))
    assert_retired(projected[str(w.run.id)], w.binding)
    assert projected[str(current.id)]["availability"]["evaluators"] is None
    assert projected[str(current.id)]["eval_versions"] == [{
        "metric_id": str(w.binding.metric.id),
        "evaluator_version_id": str(newer.id), "retired": False,
    }]


def test_archived_legacy_null_scope_still_uses_explicit_dataset_scope(auth_client, world):
    w = world
    UserEvalMetric._base_manager.filter(pk=w.binding.metric.pk).update(
        deleted=True, organization_id=None, workspace_id=None)
    EvalTemplateVersion._base_manager.filter(pk=w.binding.version.pk).update(
        organization_id=None, workspace_id=None)
    template = w.binding.metric.template
    type(template)._base_manager.filter(pk=template.pk).update(workspace_id=None)
    assert_retired(by_id(catalog(auth_client, w))[str(w.run.id)], w.binding)


@pytest.mark.parametrize("target,field", [
    ("metric", "dataset_id"), ("metric", "organization_id"), ("metric", "workspace_id"),
    ("version", "organization_id"), ("version", "workspace_id"),
    ("template", "organization_id"), ("template", "workspace_id"),
])
def test_archived_objects_keep_dataset_org_workspace_checks(auth_client, world, user, target, field):
    other_org = Organization.objects.create(name="Retirement scope control")
    other_ws = Workspace.objects.create(name="Other", organization=other_org,
                                       is_default=False, is_active=True, created_by=user)
    other_ds = Dataset.no_workspace_objects.create(
        name="Other", organization=other_org, workspace=other_ws,
        user=user, column_order=[], column_config={})
    foreign = {"dataset_id": other_ds.id, "organization_id": other_org.id,
               "workspace_id": other_ws.id}
    w = world
    retire(auth_client, w)
    obj = {"metric": w.binding.metric, "version": w.binding.version,
           "template": w.binding.metric.template}[target]
    type(obj)._base_manager.filter(pk=obj.pk).update(**{field: foreign[field]})
    for route, params in [("dataset-results-runs", {}),
                          ("dataset-results", {"runs": str(w.run.id)})]:
        response = reads.read(auth_client, w.dataset, route, **params)
        assert response.status_code == 404, response.content
        # The 404 error envelope carries its message in "result"; what must never
        # come back is a results payload for the archived, out-of-scope object.
        assert not isinstance(response.json().get("result"), (dict, list)), response.content
    reads.result(reads.read(auth_client, w.dataset, runs=str(w.healthy.id)))


@pytest.mark.parametrize("field", ["metric_id", "evaluator_version_id", "template_id"])
def test_archive_lookup_never_substitutes_for_missing_or_mismatched_pin(auth_client, world, field):
    retire(auth_client, world)
    pins = deepcopy(world.run.evaluator_versions)
    pins[0][field] = str(uuid4())
    ExperimentRun.no_workspace_objects.filter(pk=world.run.pk).update(evaluator_versions=pins)
    response = reads.read(auth_client, world.dataset, runs=str(world.run.id))
    assert response.status_code == 404, response.content
    reads.result(reads.read(auth_client, world.dataset, runs=str(world.healthy.id)))


def test_archived_version_must_belong_to_metrics_template(auth_client, world, user):
    _, other = reads.pinned_run(world.version, world.examples, user, output_type="numeric")
    retire(auth_client, world)
    pins = deepcopy(world.run.evaluator_versions)
    pins[0]["evaluator_version_id"] = str(other.version.id)
    ExperimentRun.no_workspace_objects.filter(pk=world.run.pk).update(evaluator_versions=pins)
    response = reads.read(auth_client, world.dataset, runs=str(world.run.id))
    assert response.status_code == 404, response.content


def test_archive_lookup_respects_denied_workspace_scope(world):
    with workspace_context(None):
        set_workspace_context(require_workspace=True)
        with pytest.raises(ResultsReadError) as error:
            validate_evaluators(world.dataset, [world.run])
    assert error.value.status == 404
