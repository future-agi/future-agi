"""S1 shared registration/detail contracts; execute on the composed candidate.

Provider, authentication, workspace and billing behavior are not mocked. The
route contract tests deliberately require completed P03/P04 registration.
"""
import uuid
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.urls import resolve
from rest_framework.exceptions import NotFound
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.choices import SourceChoices
from model_hub.models.dataset_versions import DatasetVersion
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import (
    ExperimentDatasetTable, ExperimentPromptConfig, ExperimentRun, ExperimentsTable,
)
from model_hub.models.native_experiment_runs import (
    NativeExampleBinding, NativeExperimentSnapshot, NativeRunBinding,
)
from model_hub.serializers.experiments import (
    ExperimentDetailRunQuerySerializer, ExperimentDetailV2Serializer,
)
from model_hub.services.dataset_snapshot import create_dataset_snapshot
from model_hub.services.native_run_binding import bind_native_run

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]

@pytest.fixture
def detail_world(organization, workspace, user, prompt_version):
    dataset = Dataset.objects.create(
        name="Results source", organization=organization, workspace=workspace,
        user=user, column_order=[], column_config={},
    )
    column = Column.objects.create(
        dataset=dataset, name="input", data_type="text", source=SourceChoices.OTHERS.value,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(dataset=dataset, column=column, row=row, value="original input")
    experiment = ExperimentsTable.objects.create(
        dataset=dataset, name="Results detail", user=user, experiment_type="llm",
    )
    snapshot, _, _ = create_dataset_snapshot(dataset, experiment)
    variant = ExperimentDatasetTable.objects.create(name="A", experiment=experiment)
    config = ExperimentPromptConfig.objects.create(
        experiment_dataset=variant, prompt_template=prompt_version.original_template,
        prompt_version=prompt_version, name="A", model="fixture-model",
        model_params={"temperature": 0.2},
    )
    output = Column.objects.create(
        dataset=snapshot, name="output", data_type="text",
        source=SourceChoices.EXPERIMENT.value, source_id=str(variant.id),
    )
    variant.columns.add(output)
    request = SimpleNamespace(user=user, organization=organization, workspace=workspace, method="GET")
    return SimpleNamespace(
        dataset=dataset, experiment=experiment, variant=variant, config=config,
        output=output, snapshot=snapshot, request=request, user=user,
    )

def add_run(world):
    return bind_native_run(
        dataset=world.dataset, experiment=world.experiment, variant=world.variant,
        execution_id=uuid.uuid4(), output_column=world.output, created_by=world.user,
        effective_settings={"trials": 1, "concurrency": 10},
    )

def serialize(world, **query):
    return ExperimentDetailV2Serializer(
        world.experiment,
        context={"request": world.request, "runs_query": query},
    ).data

def get_detail(world, params=None, *, authenticated=True):
    from model_hub.views.experiments import ExperimentsTableV2DetailView

    request = APIRequestFactory().get(
        f"/model-hub/experiments/v2/{world.experiment.id}/", params or {},
    )
    request.organization = world.request.organization
    request.workspace = world.request.workspace
    if authenticated:
        force_authenticate(request, user=world.user)
    return ExperimentsTableV2DetailView.as_view()(
        request, experiment_id=world.experiment.id,
    )

def test_native_models_are_registered_by_package():
    import model_hub.models as package

    for cls in (NativeExperimentSnapshot, NativeExampleBinding, NativeRunBinding):
        assert getattr(package, cls.__name__) is cls
        assert apps.get_model("model_hub", cls.__name__) is cls

def test_detail_preserves_classic_fields_and_adds_canonical_run(detail_world):
    run = add_run(detail_world)
    payload = serialize(detail_world)
    assert {"prompt_configs", "agent_configs", "user_eval_metrics", "dataset_id", "id"} <= payload.keys()
    assert payload["prompt_configs"][0]["id"] == str(detail_world.config.id)
    assert payload["agent_configs"] == []
    assert payload["user_eval_metrics"] == []
    assert payload["runs"][0]["run_id"] == str(run.id)
    assert payload["runs"][0]["run_id"] != str(detail_world.config.id)
    assert payload["runs"][0]["output_column_id"] == str(detail_world.output.id)
    assert payload["runs"][0]["effective_settings"] == {"trials": 1, "concurrency": 10}
    assert payload["runs"][0]["trial_reducer"] is None
    assert payload["runs_total"] == 1
    assert payload["runs_has_more"] is False
    assert payload["runs_next"] is None

def test_detail_does_not_create_or_backfill_runs(detail_world):
    before = (ExperimentRun.objects.count(), NativeRunBinding.objects.count())
    payload = serialize(detail_world)
    assert payload["runs"] == []
    assert payload["runs_total"] == 0
    assert payload["results_availability"]["reason"] == "native_runs_not_recorded"
    assert before == (ExperimentRun.objects.count(), NativeRunBinding.objects.count())

def test_historical_detail_is_explicitly_unavailable_and_classic_readable(detail_world):
    old = ExperimentsTable.objects.create(
        name="Classic history", dataset=detail_world.dataset, user=detail_world.user,
    )
    payload = ExperimentDetailV2Serializer(
        old, context={"request": detail_world.request},
    ).data
    assert payload["runs"] == []
    assert payload["results_availability"]["reason"] == "native_identity_not_recorded"
    assert payload["results_availability"]["classic_url"] == f"/dashboard/develop/experiment/{old.id}/data"
    assert not NativeExperimentSnapshot.objects.filter(experiment=old).exists()

def test_catalog_continuation_is_followable_without_duplicate_runs(detail_world):
    from model_hub.services.results_catalog import experiment_runs_detail, list_results_runs

    expected = {str(add_run(detail_world).id) for _ in range(3)}
    first = experiment_runs_detail(detail_world.request, detail_world.experiment, limit=2)
    assert first["runs_total"] == 3 and first["runs_has_more"] is True
    assert first["runs_next"] == 2
    last = list_results_runs(detail_world.request, detail_world.dataset.id, {
        "experiment_id": detail_world.experiment.id, "include": ["config", "lineage"],
        "limit": 2, "offset": first["runs_next"],
    })
    ids = [r["run_id"] for r in first["runs"] + last["results"]]
    assert len(ids) == len(set(ids)) == 3 and set(ids) == expected
    assert last["has_more"] is False and last["next"] is None

def test_detail_does_not_silently_accept_old_paging_controls(detail_world):
    assert get_detail(detail_world, {"runs_offset": 100001}).status_code == 400

def test_projection_sanitizes_config_and_leaves_unknown_values_null(detail_world):
    detail_world.config.model_params = {"temperature": 0.2, "api_key": "test-only-marker"}
    detail_world.config.configuration = {"headers": {"Authorization": "test-only-marker"}}
    detail_world.config.save()
    add_run(detail_world)
    run = serialize(detail_world)["runs"][0]
    assert "test-only-marker" not in str(run)
    assert run["trial_reducer"] is None and run["error_policy"] is None
    assert run["requested_settings"] is None

def test_detail_refuses_missing_request_context(detail_world):
    with pytest.raises(NotFound):
        ExperimentDetailV2Serializer(detail_world.experiment).data

@pytest.mark.parametrize("foreign", ["organization", "workspace", "deleted_dataset"])
def test_detail_scope_is_checked_before_any_run_metadata(detail_world, foreign):
    add_run(detail_world)
    if foreign == "organization":
        detail_world.request.organization = Organization.objects.create(name="Foreign")
    elif foreign == "workspace":
        detail_world.request.workspace = Workspace.objects.create(
            name="Foreign workspace", organization=detail_world.dataset.organization,
            created_by=detail_world.user,
        )
    else:
        detail_world.dataset.delete()
    with pytest.raises(NotFound):
        serialize(detail_world)
    response = get_detail(detail_world)
    assert response.status_code == 404
    assert "fixture-model" not in str(response.data)

def test_mixed_scope_version_rejects_catalog_instead_of_partial_count(detail_world):
    run = add_run(detail_world)
    foreign = Dataset.objects.create(
        name="Unrelated", organization=detail_world.dataset.organization,
        workspace=detail_world.dataset.workspace, column_order=[], column_config={},
    )
    version = DatasetVersion.no_workspace_objects.create(
        dataset=foreign, organization=foreign.organization, workspace=foreign.workspace,
        created_by=detail_world.user, content_hash="f" * 64,
    )
    # Deliberate stored corruption must not be hidden by filtering a partial list.
    ExperimentRun.no_workspace_objects.filter(pk=run.pk).update(dataset_version=version)
    with pytest.raises(NotFound):
        serialize(detail_world)

def test_deleted_version_does_not_leak_a_run_count(detail_world):
    run = add_run(detail_world)
    run.dataset_version.delete()
    with pytest.raises(NotFound):
        serialize(detail_world)

def test_deleted_run_is_not_listed(detail_world):
    add_run(detail_world).delete()
    assert serialize(detail_world)["runs_total"] == 0

@pytest.mark.parametrize("params", [
    {"runs_limit": 0}, {"runs_limit": 101}, {"runs_limit": "bad"},
    {"runs_offset": -1}, {"runs_offset": 100001},
])
def test_detail_query_validation(params):
    # Unknown query fields are rejected by validated_request, not by DRF fields.
    assert not set(params).intersection(ExperimentDetailRunQuerySerializer().fields)

def test_detail_query_uses_catalog_continuation_not_rejected_offsets():
    query = ExperimentDetailRunQuerySerializer(data={})
    assert query.is_valid(), query.errors
    assert query.validated_data == {}

def test_detail_get_has_real_envelope_and_validates_query(detail_world):
    add_run(detail_world)
    ok = get_detail(detail_world)
    assert ok.status_code == 200
    assert ok.data["status"] is True and len(ok.data["result"]["runs"]) == 1
    assert get_detail(detail_world, {"runs_limit": 101}).status_code == 400
    assert get_detail(detail_world, {"unexpected": "value"}).status_code == 400
    assert get_detail(detail_world, authenticated=False).status_code in {401, 403}

@pytest.mark.parametrize("suffix", ["runs/", "results/", "results/analysis/"])
def test_s1_dataset_views_are_registered_on_composed_head(suffix):
    # This is an integration requirement, not evidence of an accepted route.
    match = resolve(f"/model-hub/datasets/{uuid.uuid4()}/{suffix}")
    assert hasattr(match.func.view_class, "get")
    assert not hasattr(match.func.view_class, "post")

@pytest.mark.parametrize("suffix", ["", "rows/", "stats/"])
def test_classic_detail_routes_remain_registered(suffix):
    match = resolve(f"/model-hub/experiments/v2/{uuid.uuid4()}/{suffix}")
    assert hasattr(match.func.view_class, "get")

def test_existing_baseline_mutation_methods_remain_registered():
    match = resolve(f"/model-hub/datasets/{uuid.uuid4()}/baseline/")
    assert all(hasattr(match.func.view_class, method) for method in ("get", "put", "delete"))

from unittest.mock import patch
from django.db import transaction
from model_hub.tests import test_experiment_append_variant as append_helpers
from model_hub.tests import test_experiment_v2_results_tenancy as tenancy
from model_hub.tests import test_native_run_binding as identity
from model_hub.services import results_catalog
from model_hub.services.experiment_run_writes import RunWriteError

tenants = tenancy.tenants
native_world = identity.native_world

@pytest.mark.parametrize("operation", ["update", "append"])
@pytest.mark.parametrize("unavailable", [False, True])
def test_classic_mutations_keep_authenticated_detail_context(tenants, monkeypatch, operation, unavailable):
    actor = tenants["a"]
    experiment, old, template, version, url = append_helpers.prepare(actor)
    original = results_catalog.experiment_runs_detail
    calls = []
    def projection(request, instance, **kwargs):
        assert request.user.pk == actor["user"].pk
        assert request.method == "PUT" and instance.pk == experiment.pk
        calls.append(request)
        if unavailable:
            raise results_catalog.ResultsReadError("Results temporarily unavailable", 503)
        return original(request, instance, **kwargs)
    monkeypatch.setattr(results_catalog, "experiment_runs_detail", projection)
    data = {} if operation == "update" else {"append_only": True, "prompt_config": [
        append_helpers.entry(template, version, "gpt-4o")]}
    # Only external workflow transport is replaced, never auth or billing.
    with patch(append_helpers.START, return_value="fixture-workflow") as start:
        response = actor["client"].put(url, data, format="json")
    assert response.status_code == 200, response.content
    body = response.json()["result"]
    assert calls and body["id"] == str(experiment.id)
    assert str(old.id) in {c["id"] for c in body["prompt_configs"]}
    assert body["runs_availability"]["available"] is (not unavailable)
    if unavailable:
        assert body["runs"] is None and body["runs_total"] is None
        assert body["runs_has_more"] is None and body["runs_next"] is None
        assert body["runs_availability"]["status"] == 503
    if operation == "append":
        start.assert_called_once()
        assert len(body["prompt_configs"]) == 2
    else:
        start.assert_not_called()

def test_detail_get_preserves_results_read_error(tenants, monkeypatch):
    actor = tenants["a"]
    _, _, _, _, url = append_helpers.prepare(actor)
    def unavailable(request, instance, **kwargs):
        assert request.user.pk == actor["user"].pk and request.method == "GET"
        raise results_catalog.ResultsReadError("Results temporarily unavailable", 503)
    monkeypatch.setattr(results_catalog, "experiment_runs_detail", unavailable)
    response = actor["client"].get(url)
    assert response.status_code == 503 and response.json()["status"] is False

@pytest.mark.parametrize("kind", ["prompt", "graph"])
def test_retire_bound_variant_put_then_get_preserves_evidence(native_world, kind):
    from model_hub.services.native_run_recording import mark_native_run
    from model_hub.views.experiments import ExperimentsTableV2DetailView

    w = native_world if kind == "prompt" else identity.graph_variant(
        native_world, native_world["dataset"].workspace)
    run = identity.bind(w)
    mark_native_run(dataset=w["dataset"], run_id=run.id, status="cancelled")
    immutable = (run.dataset_version_id, run.evaluator_versions, run.metadata)
    url = f"/model-hub/experiments/v2/{w['experiment'].id}/"
    def request(method):
        req = getattr(APIRequestFactory(), method)(url, {"prompt_config": []} if method == "put" else {}, format="json")
        req.organization = w["dataset"].organization
        req.workspace = w["dataset"].workspace
        force_authenticate(req, user=w["created_by"])
        return ExperimentsTableV2DetailView.as_view()(req, experiment_id=w["experiment"].id)
    with patch(append_helpers.START) as dispatch:
        updated = request("put")
        assert updated.status_code == 200, updated.data
        dispatch.assert_not_called()
    read = request("get")
    assert read.status_code == 200, read.data
    assert read.data["result"]["runs"] == []
    run = ExperimentRun.all_objects.get(pk=run.pk)
    assert run.deleted is True
    assert immutable == (run.dataset_version_id, run.evaluator_versions, run.metadata)
    assert NativeRunBinding.objects.filter(run=run).exists()
    assert NativeExampleBinding.objects.filter(snapshot__experiment=w["experiment"]).count() == 2

def test_retirement_refuses_active_or_corrupt_run_atomically(native_world):
    from model_hub.views.experiments import _soft_delete_edt_and_columns
    w = native_world
    run = identity.bind(w)
    with pytest.raises(ValueError):
        _soft_delete_edt_and_columns(w["variant"])
    assert not ExperimentRun.all_objects.get(pk=run.pk).deleted
    assert not Column.all_objects.get(pk=w["output_column"].pk).deleted
    ExperimentRun.no_workspace_objects.filter(pk=run.pk).update(status="cancelled")
    NativeRunBinding.objects.filter(run=run).update(request_hash="0" * 64)
    with pytest.raises(ValueError):
        _soft_delete_edt_and_columns(w["variant"])
    assert not ExperimentRun.all_objects.get(pk=run.pk).deleted
    assert not Column.all_objects.get(pk=w["output_column"].pk).deleted

def test_variant_retirement_rolls_back_with_enclosing_edit(native_world):
    from model_hub.views.experiments import _soft_delete_edt_and_columns
    w = native_world
    run = identity.bind(w)
    ExperimentRun.no_workspace_objects.filter(pk=run.pk).update(status="cancelled")
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            _soft_delete_edt_and_columns(w["variant"])
            raise RuntimeError("later edit failed")
    assert not ExperimentRun.all_objects.get(pk=run.pk).deleted
    assert not Column.all_objects.get(pk=w["output_column"].pk).deleted

def test_native_admission_product_path_creates_canonical_binding(native_world):
    """Required COR-001 control: no manual bind_native_run in this path.

    This assertion must remain red until the full admitted worker bridge lands;
    registration/detail alone must never be mistaken for S1 completion.
    """
    from model_hub.services.experiment_submit import stage_submission, admit_submission
    w = native_world
    with transaction.atomic():
        stage_submission(w["experiment"], {}, "create", max_concurrent_rows=10)
    with patch(append_helpers.START, return_value="fixture-workflow"):
        admit_submission(w["experiment"].id)
    binding = NativeRunBinding.objects.get(variant=w["variant"])
    assert binding.run.status == "queued"
