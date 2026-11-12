"""Public metadata boundaries; run only in the qualified database test venue."""
# Imported fixtures are injected by pytest.
# ruff: noqa: F811

from copy import deepcopy
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from model_hub.models.experiments import ExperimentRun, ExperimentRunItem, ExperimentsTable
from model_hub.models.native_experiment_runs import NativeExperimentSnapshot, NativeRunBinding
from model_hub.serializers.experiment_run_api import (
    ExternalRunCreateRequestSerializer, ExternalRunHeaderSerializer,
    ExternalRunResultsRequestSerializer, ExperimentRunReadSerializer,
)
from model_hub.serializers.experiments import (
    ExperimentCreateV2Serializer, ExperimentDetailSerializer, ExperimentListSerializer,
    ExperimentListV2Serializer, ExperimentRunSerializer, ExperimentRunItemSerializer,
    ExperimentsTableSerializer, ExperimentsTableUpdateSerializer, ExperimentUpdateV2Serializer,
)
from model_hub.tests.test_experiment_run_api import external_api_fixture  # noqa: F401
from model_hub.tests.test_experiment_run_scope import CREATE_URL, request_body, scope_world  # noqa: F401
from model_hub.tests.test_experiment_scope_gate import counts
from model_hub.tests.test_experiment_version_params import create_run, version_world  # noqa: F401
from model_hub.tests.test_native_run_binding import bind, native_world  # noqa: F401
from tfc.utils.api_contracts import _validate_serializer
from tfc.utils.api_errors import build_error_envelope

pytestmark = [pytest.mark.django_db(transaction=True),
              pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")]
START = "tfc.temporal.experiments.start_experiment_v2_workflow"
CODE = "reserved_metadata_key"
CALLER = {"purpose": "metadata regression"}
PRIVATE = {"dataset_version_id": "private-version", "identities": [{"source_row_id": "private-row"}]}


def test_projection_is_shallow_bounded_and_non_mutating():
    from model_hub.services.run_metadata_public import public_run_metadata, MAX_METADATA_KEYS

    class NoTraversal:
        def __str__(self):
            raise AssertionError("internal snapshot was stringified")

    private = NoTraversal()
    stored = {**CALLER, "native_snapshot": private}
    assert public_run_metadata(stored) == CALLER
    assert stored["native_snapshot"] is private
    assert public_run_metadata(None) == {}
    assert public_run_metadata([]) == {}
    assert public_run_metadata({}) == {}
    # Historical malformed objects fail closed rather than an unbounded copy.
    assert public_run_metadata(dict.fromkeys(range(MAX_METADATA_KEYS + 1))) == {}
    nested = {"native_snapshot": "caller-owned nested data"}
    assert public_run_metadata({"nested": nested}) == {"nested": nested}


@pytest.mark.parametrize("serializer", [ExperimentRunSerializer, ExperimentRunItemSerializer])
def test_model_contract_metadata_fields_project_without_mutation(serializer):
    stored = {**CALLER, "native_snapshot": deepcopy(PRIVATE)}
    before = deepcopy(stored)
    field = serializer().fields["metadata"]
    assert field.to_representation(stored) == CALLER
    assert stored == before
    assert field.to_representation({"native_snapshot": PRIVATE}) == {}


@pytest.mark.parametrize("serializer", [ExternalRunHeaderSerializer, ExperimentRunReadSerializer])
def test_external_header_serializer_projects_without_mutation(serializer):
    stored = {**CALLER, "nested": {"retained": [1, False, None]}, "native_snapshot": PRIVATE}
    before = deepcopy(stored)
    assert serializer().fields["metadata"].to_representation(stored) == {
        **CALLER, "nested": {"retained": [1, False, None]}
    }
    assert stored == before


@pytest.mark.parametrize("mode", ["get", "put", "replay", "append"])
@pytest.mark.parametrize("caller", [{}, CALLER])
@pytest.mark.parametrize("stored_snapshot", ["server", "legacy-caller"])
def test_experiment_detail_and_update_paths_project_metadata(
    auth_client, scope_world, settings, mode, caller, stored_snapshot
):
    settings.EXPERIMENT_SCOPE_ENABLED = True
    record = create_run(auth_client, request_body(scope_world, metadata=caller))
    experiment = record.experiment_dataset.experiment
    experiment.refresh_from_db()
    assert "native_snapshot" in experiment.run_metadata
    if stored_snapshot == "legacy-caller":
        # A historical caller/admin value must be hidden without repairing storage.
        experiment.run_metadata["native_snapshot"] = deepcopy(PRIVATE)
        experiment.save(update_fields=["run_metadata"])
    original = deepcopy(experiment.run_metadata)
    assert "native_snapshot" in original
    url = f"{CREATE_URL}{experiment.id}/"
    if mode == "get":
        response = auth_client.get(url)
    elif mode == "replay":
        from model_hub.services.experiment_submit import request_hash
        # Exercise the replay response branch without changing durable admission receipts.
        with patch("model_hub.services.experiment_submit.state_of", return_value={
            "state": "accepted", "request_hash": request_hash({}, "update")
        }), patch("model_hub.services.experiment_submit.admit_submission"):
            response = auth_client.put(url, {}, format="json")
    else:
        ExperimentRun.no_workspace_objects.filter(
            native_binding__snapshot__experiment=experiment
        ).update(status="completed", completed_at=timezone.now())
        ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(status="Completed")
        body = {}
        if mode == "append":
            entry = request_body(scope_world)["prompt_config"][0]
            body = {"append_only": True, "prompt_config": [{**entry, "name": "Additional variant"}]}
        with patch(START, return_value="metadata-boundary"):
            response = auth_client.put(url, body, format="json")
    assert response.status_code == 200, response.content
    assert response.json()["result"]["metadata"] == caller
    experiment.refresh_from_db()
    assert experiment.run_metadata == original


def _external_results_body(body):
    return {"items": [{
        "example_id": body["expected_items"][0]["example_id"],
        "trial_number": 1, "receipt_key": "metadata-case", "status": "completed",
        # Only the metadata root is reserved, not arbitrary result payloads.
        "output": {"native_snapshot": "caller-owned result data"},
    }]}


def _seed_legacy_external_run(fixture, user, body):
    from model_hub.services.experiment_run_creation import create_experiment_run
    from model_hub.services.experiment_run_writes import _RUN_KEY, _digest

    # The current creator rejects reserved roots. Create valid state first;
    # only this test fixture may persist the historical caller-owned value.
    historical_metadata = deepcopy(body["metadata"])
    run = create_experiment_run(
        run_id=UUID(body["run_id"]), dataset_version=fixture["version"], created_by=user,
        task_kind="external", expected_items=[
            (item["example_id"], item["trial_number"]) for item in body["expected_items"]
        ], evaluator_metrics=(), model_config=body["model_config"], metadata={
            key: value for key, value in historical_metadata.items() if key != "native_snapshot"
        },
    )
    # Match the old creation hash, including poisoned caller metadata but not
    # the server envelope. Preserve its creation fields and pinned plan receipt.
    server_receipts = deepcopy(run.metadata[_RUN_KEY])
    server_receipts["creation"]["request_hash"] = _digest({
        "dataset_version_id": str(run.dataset_version_id),
        "created_by_id": server_receipts["creation"]["created_by_id"],
        "task_kind": run.task_kind,
        "expected_items": server_receipts["plan"]["identities"],
        "experiment_id": None,
        "prompt_version_id": None,
        "agent_version_id": None,
        "evaluator_metric_ids": [],
        "model_config": run.model_config,
        "metadata": historical_metadata,
    })
    historical_metadata[_RUN_KEY] = server_receipts
    run.metadata = historical_metadata
    run.save(update_fields=["metadata"])
    return run


def _external_rows():
    return tuple(list(model.all_objects.order_by("id").values())
                 for model in (ExperimentRun, ExperimentRunItem))


@pytest.mark.parametrize("mode", ["create", "create-replay", "detail", "ingest", "ingest-replay"])
def test_external_header_paths_project_legacy_metadata(auth_client, external_api_fixture, mode):
    from model_hub.views.experiment_run_api import _header

    body = external_api_fixture["body"]
    create_url = reverse("external-experiment-run-create")
    created = auth_client.post(create_url, body, format="json")
    assert created.status_code == 200, created.content
    assert created.json()["result"]["metadata"] == body["metadata"]
    run = ExperimentRun.no_workspace_objects.get(pk=body["run_id"])
    # Model an old/admin row. Keep the original immutable request hash for replay.
    run.metadata = {**run.metadata, "native_snapshot": deepcopy(PRIVATE)}
    run.save(update_fields=["metadata"])
    original = deepcopy(run.metadata)
    assert _header(run)["metadata"] == body["metadata"]
    if mode == "create":
        # Inject a persisted legacy service row, not a fake header/serializer.
        # A normal new request cannot create this value after input rejection.
        with patch("model_hub.views.experiment_run_api.create_experiment_run", return_value=run) as create:
            response = auth_client.post(create_url, body, format="json")
        create.assert_called_once()
        assert create.call_args.kwargs["metadata"] == body["metadata"]
    elif mode == "create-replay":
        response = auth_client.post(create_url, body, format="json")
    elif mode == "detail":
        response = auth_client.get(reverse("experiment-run-detail", kwargs={"run_id": run.id}))
    else:
        url = reverse("external-experiment-run-results", kwargs={"run_id": run.id})
        results = _external_results_body(body)
        response = auth_client.post(url, results, format="json")
        if mode == "ingest-replay":
            assert response.status_code == 200, response.content
            assert response.json()["result"]["run"]["metadata"] == body["metadata"]
            response = auth_client.post(url, results, format="json")
        item = ExperimentRunItem.no_workspace_objects.get(run=run)
        assert item.output == results["items"][0]["output"]
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert (result["run"] if mode.startswith("ingest") else result)["metadata"] == body["metadata"]
    run.refresh_from_db()
    assert run.metadata == original


def test_admin_metadata_projection_excludes_raw_form_field(user):
    from model_hub.admin import ExperimentsTableAdmin

    model_admin = ExperimentsTableAdmin(ExperimentsTable, AdminSite())
    experiment = ExperimentsTable(run_metadata={**CALLER, "native_snapshot": deepcopy(PRIVATE)})
    before = deepcopy(experiment.run_metadata)
    request = RequestFactory().get("/admin/model_hub/experimentstable/")
    request.user = user
    assert "run_metadata" not in model_admin.get_form(request).base_fields
    assert "public_metadata" in model_admin.get_readonly_fields(request, experiment)
    assert model_admin.public_metadata(experiment) == CALLER
    assert experiment.run_metadata == before


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("value", [None, "forged", {}, PRIVATE])
@pytest.mark.parametrize("method", ["post", "put"])
def test_reserved_metadata_create_update_is_typed_400_without_writes(
    auth_client, scope_world, settings, enabled, value, method
):
    settings.EXPERIMENT_SCOPE_ENABLED = enabled
    # The update target is real so the no-mutation check is meaningful.
    record = create_run(auth_client, request_body(scope_world)) if method == "put" else None
    before = counts()
    stored = deepcopy(record.experiment_dataset.experiment.run_metadata) if record else None
    body = request_body(scope_world, metadata={"native_snapshot": value}) if method == "post" else {
        "metadata": {"native_snapshot": value}
    }
    url = CREATE_URL if record is None else f"{CREATE_URL}{record.experiment_dataset.experiment_id}/"
    with patch(START) as start, patch("model_hub.views.experiments.create_dataset_snapshot") as snapshot:
        response = getattr(auth_client, method)(url, body, format="json")
    assert response.status_code == 400, response.content
    assert response.json()["code"] == CODE
    assert counts() == before
    start.assert_not_called()
    snapshot.assert_not_called()
    if record:
        experiment = record.experiment_dataset.experiment
        experiment.refresh_from_db()
        assert experiment.run_metadata == stored


@pytest.mark.parametrize("serializer", [
    ExperimentCreateV2Serializer, ExperimentUpdateV2Serializer,
    ExperimentsTableSerializer, ExperimentsTableUpdateSerializer,
    ExperimentRunSerializer, ExperimentRunItemSerializer,
    ExternalRunCreateRequestSerializer, ExternalRunResultsRequestSerializer,
])
@pytest.mark.parametrize("partial", [False, True])
def test_reserved_key_rejected_before_other_validation(serializer, partial):
    # Unknown-field processing must not erase the typed reserved-key error.
    _, errors, valid = _validate_serializer(serializer, {"metadata": {"native_snapshot": PRIVATE}},
                                           reject_unknown_fields=True, partial=partial)
    assert not valid
    assert build_error_envelope(errors)["code"] == CODE


@pytest.mark.parametrize("mode", ["create", "create-replay", "ingest", "ingest-replay"])
@pytest.mark.parametrize("value", [None, "forged", {}, PRIVATE])
def test_external_reserved_metadata_inputs_are_typed_400_without_writes(
    auth_client, external_api_fixture, user, mode, value
):
    clean = deepcopy(external_api_fixture["body"])
    body = {**clean, "metadata": {**clean["metadata"], "native_snapshot": value}}
    url = reverse("external-experiment-run-create")
    if mode == "create-replay":
        _seed_legacy_external_run(external_api_fixture, user, body)
    elif mode.startswith("ingest"):
        created = auth_client.post(url, clean, format="json")
        assert created.status_code == 200, created.content
        run = ExperimentRun.no_workspace_objects.get(pk=clean["run_id"])
        run.metadata = {**run.metadata, "native_snapshot": deepcopy(PRIVATE)}
        run.save(update_fields=["metadata"])
        url = reverse("external-experiment-run-results", kwargs={"run_id": run.id})
        results = _external_results_body(clean)
        if mode == "ingest-replay":
            accepted = auth_client.post(url, results, format="json")
            assert accepted.status_code == 200, accepted.content
        body = {**results, "metadata": {"native_snapshot": value}}
    before = _external_rows()
    with (
        patch("model_hub.views.experiment_run_api.create_experiment_run") as create,
        patch("model_hub.views.experiment_run_api.ingest_external_run_results") as ingest,
    ):
        response = auth_client.post(url, body, format="json")
    assert response.status_code == 400, response.content
    assert response.json()["code"] == CODE
    create.assert_not_called()
    ingest.assert_not_called()
    assert _external_rows() == before


def test_ingestion_does_not_enable_caller_metadata_edits(auth_client, external_api_fixture):
    clean = external_api_fixture["body"]
    created = auth_client.post(reverse("external-experiment-run-create"), clean, format="json")
    assert created.status_code == 200, created.content
    before = _external_rows()
    url = reverse("external-experiment-run-results", kwargs={"run_id": clean["run_id"]})
    with patch("model_hub.views.experiment_run_api.ingest_external_run_results") as ingest:
        response = auth_client.post(url, {**_external_results_body(clean), "metadata": CALLER}, format="json")
    assert response.status_code == 400, response.content
    assert "Unknown field." in response.content.decode()
    ingest.assert_not_called()
    assert _external_rows() == before


def test_update_does_not_enable_caller_metadata_edits(auth_client, scope_world):
    record = create_run(auth_client, request_body(scope_world))
    experiment = record.experiment_dataset.experiment
    before = deepcopy(experiment.run_metadata)
    response = auth_client.put(f"{CREATE_URL}{experiment.id}/", {"metadata": CALLER}, format="json")
    assert response.status_code == 400
    assert "Unknown field." in response.content.decode()
    experiment.refresh_from_db()
    assert experiment.run_metadata == before


@pytest.mark.parametrize("serializer", [ExperimentDetailSerializer, ExperimentListSerializer, ExperimentListV2Serializer])
def test_experiment_list_and_legacy_detail_do_not_advertise_metadata(serializer):
    assert not {"run_metadata", "metadata"}.intersection(serializer().fields)


def test_public_run_lists_do_not_advertise_metadata():
    from model_hub.serializers.dataset_runs import DatasetRunSerializer
    from model_hub.serializers.results_runs import CanonicalRunSerializer
    for serializer in (DatasetRunSerializer, CanonicalRunSerializer):
        assert not {"run_metadata", "metadata"}.intersection(serializer().fields)


def test_native_binding_ignores_forged_json_snapshot(native_world):
    experiment = native_world["experiment"]
    snapshot = NativeExperimentSnapshot.objects.get(experiment=experiment)
    forged = {"dataset_version_id": str(uuid4()), "identities": []}
    experiment.run_metadata = {"native_snapshot": forged}
    experiment.save(update_fields=["run_metadata"])
    run = bind(native_world)
    binding = NativeRunBinding.objects.get(run=run)
    assert binding.snapshot_id == snapshot.id
    assert run.dataset_version_id == snapshot.dataset_version_id
    plan = run.metadata["_futureagi_run_write"]["plan"]["identities"]
    assert {(entry["example_id"], entry["trial_number"]) for entry in plan} == {
        (str(link.version_item.example_id), 1) for link in snapshot.examples.select_related("version_item")
    }
    experiment.refresh_from_db()
    assert experiment.run_metadata == {"native_snapshot": forged}
