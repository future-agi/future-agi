"""Recovery validates existing execution evidence without another side effect."""

from copy import deepcopy
from uuid import uuid4

import pytest
from django.utils import timezone

from agent_playground.models import GraphExecution
from model_hub.models.dataset_versions import DatasetVersionItem
from model_hub.models.experiments import ExperimentRunItem
from model_hub.services import graph_version_task_adapter as adapter
from model_hub.services.experiment_run_writes import RunWriteError, transition_run
from model_hub.services.graph_result_recovery import recover_graph_run_item
from tfc.temporal.agent_playground.tests import test_experiment_run_adapter

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]
graph_run_factory = test_experiment_run_adapter.graph_run_factory


@pytest.fixture
def completed_claim(graph_run_factory, monkeypatch):
    create, version, scope = graph_run_factory
    run, _ = create(inputs=({"question": "stored evidence"},))

    def lost_response(**kwargs):
        raise adapter.GraphRunIndeterminateError("lost response")

    monkeypatch.setattr(adapter, "_default_executor", lost_response)
    with pytest.raises(adapter.GraphRunIndeterminateError, match="lost response"):
        adapter.run_graph_experiment(run_id=run.id, **scope)
    item = ExperimentRunItem.no_workspace_objects.get(run=run)
    execution = GraphExecution.no_workspace_objects.get()
    execution.status = "success"
    execution.output_payload = {"answer": "stored evidence"}
    execution.completed_at = timezone.now()
    execution.save(update_fields=["status", "output_payload", "completed_at"])
    monkeypatch.setattr(
        adapter,
        "_default_executor",
        lambda **kwargs: pytest.fail("must not redispatch"),
    )
    return run, item, execution, version, scope


@pytest.mark.parametrize("output", [None, {}, {"answer": "stored evidence"}])
def test_recovery_preserves_null_output_and_replays_after_finalization(
    completed_claim, output
):
    run, item, execution, _, scope = completed_claim
    execution.output_payload = output
    execution.save(update_fields=["output_payload"])
    recovered = recover_graph_run_item(item_id=item.id, **scope)
    assert recovered.output == output and recovered.status == "completed"
    assert recovered.trace_id is recovered.root_span_id is None
    assert adapter.run_graph_experiment(run_id=run.id, **scope).status == "completed"
    assert recover_graph_run_item(item_id=item.id, **scope).id == recovered.id


def test_failed_graph_recovers_failure_and_never_fabricates_scores(completed_claim):
    run, item, execution, _, scope = completed_claim
    execution.status = "failed"
    execution.error_message = "provider failed"
    execution.save(update_fields=["status", "error_message"])
    recovered = recover_graph_run_item(item_id=item.id, **scope)
    assert recovered.status == "failed"
    assert recovered.error == {"type": "graph_execution", "message": "provider failed"}
    assert recovered.score_refs == []
    assert adapter.run_graph_experiment(run_id=run.id, **scope).status == "failed"


def test_recovered_metrics_are_pinned_and_not_assessed(completed_claim):
    run, item, _, _, scope = completed_claim
    snapshot = {"metric_id": str(uuid4()), "evaluator_version_id": str(uuid4())}
    run.evaluator_versions = [snapshot]
    run.save(update_fields=["evaluator_versions"])
    recovered = recover_graph_run_item(item_id=item.id, **scope)
    assert recovered.score_refs == [
        {**snapshot, "status": "not_assessed", "explanation": "No callback supplied"}
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        "pending",
        "unknown",
        "unfinished",
        "execution_deleted",
        "wrong_input",
        "frozen_expected",
        "wrong_link",
        "wrong_receipt",
        "invalid_trace",
        "wrong_version",
        "version_deleted",
        "success_error",
        "cancelled",
        "wrong_tenant",
        "wrong_workspace",
        "membership_deleted",
        "invalid_plan",
    ],
)
def test_invalid_recovery_is_atomic(completed_claim, mutation):
    run, item, execution, version, scope = completed_claim
    if mutation in {"pending", "unknown"}:
        GraphExecution.no_workspace_objects.filter(pk=execution.pk).update(
            status=mutation
        )
    elif mutation == "unfinished":
        GraphExecution.no_workspace_objects.filter(pk=execution.pk).update(
            completed_at=None
        )
    elif mutation == "execution_deleted":
        GraphExecution.no_workspace_objects.filter(pk=execution.pk).update(deleted=True)
    elif mutation == "wrong_input":
        GraphExecution.no_workspace_objects.filter(pk=execution.pk).update(
            input_payload={}
        )
    elif mutation == "frozen_expected":
        ExperimentRunItem.no_workspace_objects.filter(pk=item.pk).update(
            expected="changed"
        )
    elif mutation in {"wrong_link", "wrong_receipt", "invalid_trace"}:
        metadata = deepcopy(item.metadata)
        write = metadata["_futureagi_run_write"]
        if mutation == "wrong_link":
            write["graph_execution"]["workflow_id"] = "another-workflow"
        elif mutation == "wrong_receipt":
            write["claim_receipt"]["receipt_key"] = "external-receipt"
        else:
            write["graph_execution"]["trace_id"] = "invalid"
        ExperimentRunItem.no_workspace_objects.filter(pk=item.pk).update(
            metadata=metadata
        )
    elif mutation == "wrong_version":
        other_version = type(version).no_workspace_objects.create(
            graph_id=version.graph_id, version_number=2, status="inactive"
        )
        GraphExecution.no_workspace_objects.filter(pk=execution.pk).update(
            graph_version=other_version
        )
    elif mutation == "version_deleted":
        type(version).no_workspace_objects.filter(pk=version.pk).update(deleted=True)
    elif mutation == "success_error":
        GraphExecution.no_workspace_objects.filter(pk=execution.pk).update(
            error_message="inconsistent"
        )
    elif mutation == "cancelled":
        transition_run(run_id=run.id, status="cancelled", **scope)
    elif mutation == "wrong_tenant":
        scope = {**scope, "organization_id": uuid4()}
    elif mutation == "wrong_workspace":
        scope = {**scope, "workspace_id": uuid4()}
    elif mutation == "membership_deleted":
        DatasetVersionItem.no_workspace_objects.filter(
            dataset_version_id=run.dataset_version_id, example_id=item.example_id
        ).update(deleted=True)
    elif mutation == "invalid_plan":
        metadata = deepcopy(run.metadata)
        metadata["_futureagi_run_write"]["plan"]["plan_hash"] = "invalid"
        type(run).no_workspace_objects.filter(pk=run.pk).update(metadata=metadata)
    item.refresh_from_db()
    before = (item.status, deepcopy(item.metadata), item.output, item.error)
    with pytest.raises((RunWriteError, adapter.GraphRunIndeterminateError)):
        recover_graph_run_item(item_id=item.id, **scope)
    item.refresh_from_db()
    assert (item.status, item.metadata, item.output, item.error) == before


@pytest.mark.parametrize(
    "mutation", ["graph_output", "result_output", "result_receipt"]
)
def test_terminal_recovery_refuses_changed_evidence(completed_claim, mutation):
    _, item, execution, _, scope = completed_claim
    recovered = recover_graph_run_item(item_id=item.id, **scope)
    if mutation == "graph_output":
        GraphExecution.no_workspace_objects.filter(pk=execution.pk).update(
            output_payload={"different": 1}
        )
    elif mutation == "result_output":
        ExperimentRunItem.no_workspace_objects.filter(pk=item.pk).update(
            output={"different": 1}
        )
    else:
        metadata = deepcopy(recovered.metadata)
        metadata["_futureagi_run_write"]["result_receipt"]["receipt_key"] = "foreign"
        ExperimentRunItem.no_workspace_objects.filter(pk=item.pk).update(
            metadata=metadata
        )
    with pytest.raises(RunWriteError):
        recover_graph_run_item(item_id=item.id, **scope)
