"""HTTP append generations preserve canonical evidence and original identities."""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.urls import reverse

from model_hub.models.choices import CellStatus
from model_hub.models.develop_dataset import Cell, Row
from model_hub.models.experiments import (
    ExperimentDatasetTable,
    ExperimentRun,
    ExperimentRunItem,
)
from model_hub.models.native_experiment_runs import (
    NativeExampleBinding,
    NativeRunBinding,
)
from model_hub.serializers.experiments import (
    opens_in_full_results,
    with_full_results,
)
from model_hub.services import http_experiment_source as http
from model_hub.services.experiment_run_ingestion import (
    ExternalRunResultInput,
    ingest_external_run_results,
)
from model_hub.services.experiment_run_writes import _RUN_KEY, RunWriteError
from model_hub.services.experiment_submit import ensure_native_idle, state_of
from model_hub.services.native_run_binding import run_examples
from model_hub.tests.test_http_canonical_evidence import (
    _accept,
    _body,
    _complete,
    _dispatch,
    _evidence,
    _finish,
    _process,
    _reply,
    _retry,
    _run,
    _stage,
    _stop,
)
from model_hub.tests.test_http_experiment_source import tenants as tenants
from tfc.utils import outbound_destination

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


def _put(world, body, key):
    with patch(
        "tfc.temporal.experiments.start_experiment_v2_workflow",
        return_value="http-append-workflow",
    ):
        return world.actor["client"].put(
            f"/model-hub/experiments/v2/{world.experiment.pk}/",
            body,
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        )


def _original(state):
    return {
        "run": (
            ExperimentRun.no_workspace_objects.filter(pk=state.run.pk).values().get()
        ),
        "items": list(
            ExperimentRunItem.no_workspace_objects.filter(run=state.run)
            .order_by("id")
            .values()
        ),
        "binding": NativeRunBinding.objects.filter(run=state.run).values().get(),
        "examples": list(
            NativeExampleBinding.objects.filter(
                snapshot__experiment=state.world.experiment
            )
            .order_by("id")
            .values()
        ),
        "cells": list(
            Cell.no_workspace_objects.filter(
                column_id=state.column_id,
                row_id__in=[link.snapshot_row_id for link in state.world.links],
            )
            .order_by("id")
            .values()
        ),
    }


@pytest.fixture
def appended(tenants):
    world = _stage(tenants)
    run = _complete(world)
    state = SimpleNamespace(world=world, run=run, column_id=world.output.pk)
    state.before = _original(state)
    original = Row.no_workspace_objects.create(
        dataset=world.dataset, order=10, metadata={"split_names": ["holdout"]}
    )
    Cell.no_workspace_objects.create(
        dataset=world.dataset,
        row=original,
        column=world.dataset.column_set.get(),
        value="appended",
        status=CellStatus.PASS.value,
    )
    world.inputs[str(original.pk)] = "appended"
    state.body = {"append_only": True, "row_ids": [str(original.pk)]}
    state.key = str(uuid4())
    _body(_put(world, state.body, state.key))
    world.admission = state_of(world.experiment)["admission_id"]
    _accept(world)
    state.link = next(
        link
        for link in http._http_rows(http._http_snapshot(world.experiment))
        if str(link.source_row_id) == str(original.pk)
    )
    assert world.plan["row_ids"] == [str(state.link.snapshot_row_id)]
    assert _dispatch(world, state.link)["status"] == "COMPLETED"
    _finish(world)
    ensure_native_idle(world.experiment)
    state.append_run = _run(world)
    return state


def _assert_classic_append(state, source):
    cell = Cell.no_workspace_objects.get(
        column=http._http_output_column(state.world.experiment, source),
        row_id=state.link.snapshot_row_id,
    )
    assert cell.value == "answer:appended"
    assert cell.status == CellStatus.PASS.value
    assert not NativeExampleBinding.objects.filter(
        snapshot__experiment=state.world.experiment,
        snapshot_row_id=state.link.snapshot_row_id,
    ).exists()
    assert _original(state) == state.before
    assert opens_in_full_results(state.world.experiment) is True
    annotated = with_full_results(
        type(state.world.experiment).no_workspace_objects.filter(
            pk=state.world.experiment.pk
        )
    ).get()
    assert opens_in_full_results(annotated) is True


def test_appended_http_row_records_its_exact_canonical_plan(appended):
    world = appended.world
    _assert_classic_append(appended, world.source)
    run = appended.append_run
    (link,) = run_examples(run.native_binding, run)
    assert run.dataset_version.parent_version_id == appended.run.dataset_version_id
    assert run.metadata[_RUN_KEY]["plan"]["identities"] == [
        {"example_id": str(link.version_item.example_id), "trial_number": 1}
    ]
    assert link.snapshot_row_id == appended.link.snapshot_row_id
    item = ExperimentRunItem.no_workspace_objects.get(run=run)
    column_id = str(world.dataset.column_set.get().pk)
    assert item.input == {"values": {column_id: "appended"}}
    assert item.output == "answer:appended"
    assert link.version_item.metadata == {"split_names": ["holdout"]}
    assert world.dataset.dataset_versions.count() == 2
    before = list(
        Cell.no_workspace_objects.filter(row_id=appended.link.snapshot_row_id)
        .order_by("id")
        .values()
    )
    with patch.object(outbound_destination, "request") as send:
        _body(_put(world, appended.body, appended.key))
        assert http.run_http_snapshot_row(
            str(world.experiment.pk),
            str(world.source.pk),
            str(appended.link.snapshot_row_id),
            world.admission,
        )["status"] == "BLOCKED"
    send.assert_not_called()
    assert list(
        Cell.no_workspace_objects.filter(row_id=appended.link.snapshot_row_id)
        .order_by("id")
        .values()
    ) == before
    assert _original(appended) == appended.before
    assert world.dataset.dataset_versions.count() == 2


@pytest.mark.parametrize("canonical", [False, True])
def test_retry_of_appended_http_row_preserves_original_canonical_evidence(
    appended, canonical
):
    world = appended.world
    body = {
        "cells": [
            {
                "column_id": str(world.output.pk),
                "row_id": str(appended.link.snapshot_row_id),
            }
        ],
        "acknowledge_http_side_effects": True,
    }
    if canonical:
        body["cells"][0].pop("row_id")
        body["cells"][0].update(
            run_id=str(appended.append_run.pk),
            example_id=str(appended.link.version_item.example_id),
        )
    key = str(uuid4())
    previous_admission = world.admission
    _body(_retry(world, body, key))
    world.admission = state_of(world.experiment)["admission_id"]
    assert world.admission != previous_admission
    _accept(world)
    assert world.plan["row_ids"] == [str(appended.link.snapshot_row_id)]
    assert _dispatch(world, appended.link)["status"] == "COMPLETED"
    _finish(world)
    ensure_native_idle(world.experiment)
    _assert_classic_append(appended, world.source)
    run = _run(world)
    assert run.dataset_version_id == appended.append_run.dataset_version_id
    assert (
        run.metadata[_RUN_KEY]["plan"]
        == (appended.append_run.metadata[_RUN_KEY]["plan"])
    )
    assert NativeRunBinding.objects.filter(variant=world.source).count() == 3
    assert world.dataset.dataset_versions.count() == 2
    with patch.object(outbound_destination, "request") as send:
        _body(_retry(world, body, key))
    send.assert_not_called()
    assert state_of(world.experiment)["admission_id"] == world.admission
    assert _original(appended) == appended.before


@pytest.mark.parametrize("run_id", ["original", "unknown"])
def test_named_append_retry_refuses_example_outside_run(appended, run_id):
    world = appended.world
    before = _evidence(world)
    body = {
        "cells": [
            {
                "column_id": str(world.output.pk),
                "run_id": str(appended.run.pk if run_id == "original" else uuid4()),
                "example_id": str(appended.link.version_item.example_id),
            }
        ],
        "acknowledge_http_side_effects": True,
    }
    with (
        patch.object(outbound_destination, "request") as send,
        patch("tfc.temporal.experiments.start_experiment_v2_workflow") as start,
    ):
        response = world.actor["client"].post(
            f"/model-hub/experiments/v2/{world.experiment.pk}/rerun-cells/",
            body,
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        )
    assert response.status_code == 400
    assert send.call_count == start.call_count == 0
    assert _evidence(world) == before


def test_source_added_after_append_records_the_existing_generation(appended):
    world = appended.world
    previous_source = world.source
    config = http.public_http_source(previous_source)
    config.pop("id")
    config.pop("order")
    config["name"] = "Another endpoint"
    body = {"append_only": True, "prompt_config": [config]}
    _body(_put(world, body, str(uuid4())))
    admission = state_of(world.experiment)
    source_ids = list(admission["http_plan"]["targets"])
    assert len(source_ids) == 1 and str(previous_source.pk) not in source_ids
    world.source = ExperimentDatasetTable.no_workspace_objects.get(pk=source_ids[0])
    world.output = http._http_output_column(world.experiment, world.source)
    world.admission = admission["admission_id"]
    _accept(world)
    links = [*world.links, appended.link]
    assert set(world.plan["row_ids"]) == {str(link.snapshot_row_id) for link in links}
    for link in links:
        assert _dispatch(world, link)["status"] == "COMPLETED"
    _finish(world)
    ensure_native_idle(world.experiment)
    run = _run(world)
    assert run.status == "completed" and run.completed_at is not None
    assert run.dataset_version_id == appended.append_run.dataset_version_id
    expected = {str(link.version_item.example_id) for link in links}
    assert world.dataset.dataset_versions.count() == 2
    assert {
        identity["example_id"]
        for identity in run.metadata[_RUN_KEY]["plan"]["identities"]
    } == expected
    assert {
        str(item.example_id)
        for item in ExperimentRunItem.no_workspace_objects.filter(run=run)
    } == expected
    assert ExperimentRunItem.no_workspace_objects.filter(run=run).count() == 3
    assert Cell.no_workspace_objects.filter(
        column=world.output, status=CellStatus.PASS.value
    ).count() == 3
    _assert_classic_append(appended, world.source)
    _assert_classic_append(appended, previous_source)
    assert (
        NativeRunBinding.objects.filter(snapshot__experiment=world.experiment).count()
        == 3
    )


def test_external_upload_cannot_claim_an_admitted_http_run(tenants):
    world = _stage(tenants, pending=True)
    run = _run(world)
    with pytest.raises(RunWriteError, match="admitted execution writer"):
        ingest_external_run_results(
            run_id=run.pk,
            organization_id=world.dataset.organization_id,
            workspace_id=world.dataset.workspace_id,
            results=[
                ExternalRunResultInput(
                    example_id=world.links[0].version_item.example_id,
                    trial_number=1,
                    receipt_key="caller-result",
                    status="completed",
                    output="not dispatched",
                )
            ],
        )
    run.refresh_from_db()
    assert run.status == "draft" and run.completed_at is None
    assert not ExperimentRunItem.no_workspace_objects.filter(run=run).exists()


@pytest.mark.parametrize("deleted", [False, True])
def test_append_reads_keep_original_and_appended_evidence(appended, deleted):
    world, run = appended.world, appended.append_run
    assert run.evaluator_versions == []
    if deleted:
        Row.no_workspace_objects.filter(pk=appended.link.source_row_id).update(
            deleted=True,
        )
    before = _evidence(world)
    client = world.actor["client"]
    detail = _body(client.get(f"/model-hub/experiments/v2/{world.experiment.pk}/"))
    catalog = _body(
        client.get(
            reverse(
                "dataset-results-runs",
                kwargs={"dataset_id": world.dataset.pk},
            ),
            {"experiment_id": str(world.experiment.pk)},
        )
    )
    for entries in (detail["runs"], catalog["results"]):
        assert [(e["run_id"], e["dataset_version_id"]) for e in entries] == [
            (str(run.pk), str(run.dataset_version_id)),
            (str(appended.run.pk), str(appended.run.dataset_version_id)),
        ]
    rows = _body(
        client.get(
            reverse(
                "dataset-row-runs",
                kwargs={
                    "dataset_id": world.dataset.pk,
                    "row_id": appended.link.source_row_id,
                },
            ),
            {"version": 1},
        )
    )
    assert [(r["run_id"], r["example_id"], r["output"]) for r in rows["runs"]] == [
        (str(run.pk), str(appended.link.version_item.example_id), "answer:appended"),
    ]
    assert rows["runs"][0]["dataset_version"]["id"] == str(run.dataset_version_id)
    assert _evidence(world) == before


@pytest.mark.parametrize("change", ["unrelated", "cycle", "tenant"])
def test_append_reads_refuse_invalid_provenance(appended, tenants, change):
    world, run = appended.world, appended.append_run
    if change != "tenant":
        world.dataset.dataset_versions.filter(pk=run.dataset_version_id).update(
            parent_version_id=run.dataset_version_id if change == "cycle" else None,
        )
    before = _evidence(world)
    client = tenants["b"]["client"] if change == "tenant" else world.actor["client"]
    assert (
        client.get(
            f"/model-hub/experiments/v2/{world.experiment.pk}/",
        ).status_code
        == 404
    )
    assert (
        client.get(
            reverse(
                "dataset-results-runs",
                kwargs={"dataset_id": world.dataset.pk},
            ),
            {"experiment_id": str(world.experiment.pk)},
        ).status_code
        == 404
    )
    assert _evidence(world) == before


def test_appended_evidence_survives_source_deletion_and_another_generation(appended):
    world, run = appended.world, appended.append_run
    before = _evidence(world)
    Row.no_workspace_objects.filter(pk=appended.link.source_row_id).update(deleted=True)
    row = Row.no_workspace_objects.create(dataset=world.dataset, order=20)
    _body(_put(world, {"append_only": True, "row_ids": [str(row.pk)]}, str(uuid4())))
    (link,) = run_examples(run.native_binding, run)
    assert link.version_item.input == appended.link.version_item.input
    assert link.version_item.metadata == {"split_names": ["holdout"]}
    item = ExperimentRunItem.no_workspace_objects.get(run=run)
    assert item.output == "answer:appended"
    assert list(
        ExperimentRunItem.no_workspace_objects.filter(run=run).order_by("id").values()
    ) == [item for item in before["items"] if item["run_id"] == run.pk]
    assert world.dataset.dataset_versions.count() == 3


@pytest.mark.parametrize("field", ["organization_id", "workspace_id"])
def test_appended_generation_rejects_foreign_tenant(appended, tenants, field):
    run = appended.append_run
    setattr(
        run.dataset_version,
        field,
        tenants["b"]["org" if field == "organization_id" else "workspace"].pk,
    )
    with pytest.raises(RunWriteError, match="HTTP example map"):
        run_examples(run.native_binding, run)


def test_appended_stop_ignores_late_transport(appended):
    world = appended.world
    _body(
        _retry(
            world,
            {
                "cells": [
                    {
                        "column_id": str(world.output.pk),
                        "row_id": str(appended.link.snapshot_row_id),
                    }
                ],
                "acknowledge_http_side_effects": True,
            },
            str(uuid4()),
        )
    )
    world.admission = state_of(world.experiment)["admission_id"]
    _accept(world)
    stopped = {}

    def stop_before_response(**kwargs):
        _stop(world)
        stopped.update(_evidence(world))
        return _reply("appended")

    with patch.object(
        outbound_destination,
        "request",
        side_effect=stop_before_response,
    ) as send:
        assert _process(world, appended.link.snapshot_row_id)["status"] == "BLOCKED"
    send.assert_called_once()
    assert _evidence(world) == stopped
