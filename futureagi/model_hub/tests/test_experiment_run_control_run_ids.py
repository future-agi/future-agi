"""With run control on, Stop and Resume accept the results catalog's run IDs.

The new experiment page sends each canonical run's ``run_id`` (an ExperimentRun
ID, results_catalog.project_run) to the same Stop and Resume views as the
Manage runs dialog, which sends variant IDs. Each command must reach exactly
the named runs' own executions. Temporal is a recording stand-in keyed by the
exact workflow and run ID; everything before it (views, tenancy, selection,
admission) is real.
"""

from collections import namedtuple
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from django.utils import timezone
from temporalio.client import WorkflowExecutionStatus

from accounts.models import Organization
from model_hub.models.choices import SourceChoices
from model_hub.models.develop_dataset import Column
from model_hub.models.experiment_run_control import ExperimentRunControl
from model_hub.models.experiments import (
    ExperimentDatasetTable,
    ExperimentPromptConfig,
    ExperimentRun,
)
from model_hub.models.native_experiment_runs import NativeExperimentSnapshot
from model_hub.tests.test_experiment_run_control import call
from model_hub.tests.test_native_run_binding import bind, make_native_world
from model_hub.views.experiment_run_control import ExperimentResumeV2View
from model_hub.views.experiments import ExperimentStopV2View

pytestmark = pytest.mark.django_db

TEMPORAL = "model_hub.services.experiment_run_control.get_client"
Target = namedtuple("Target", "variant config run")


@pytest.fixture(autouse=True)
def run_control_on(settings):
    settings.EXPERIMENT_RUN_CONTROL_ENABLED = True


def native_experiment(organization, workspace, user, prompt_version):
    """Two prompt variants, each bound to its canonical run by one admission."""
    world = make_native_world(organization, workspace, user, prompt_version)
    variant = ExperimentDatasetTable.objects.create(
        name="B", experiment=world["experiment"]
    )
    config = ExperimentPromptConfig.objects.create(
        experiment_dataset=variant,
        prompt_version=prompt_version,
        prompt_template=prompt_version.original_template,
        name="B",
        model="model-b",
    )
    output = Column.objects.create(
        dataset=world["snapshot"],
        name="output-B",
        data_type="text",
        source=SourceChoices.EXPERIMENT.value,
        source_id=str(variant.id),
    )
    variant.columns.add(output)
    first = Target(world["variant"], world["config"], bind(world))
    second = Target(variant, config, bind(world, variant=variant, output_column=output))
    return world, [first, second]


@pytest.fixture
def native(organization, workspace, user, prompt_version):
    return native_experiment(organization, workspace, user, prompt_version)


def bound(target, workflow_id, phase="RUNNING"):
    """The control binding the worker writes when a variant's workflow starts."""
    return ExperimentRunControl.objects.create(
        experiment_dataset=target.variant,
        workflow_id=workflow_id,
        workflow_run_id=uuid4(),
        config_id=target.config.id,
        kind="prompt",
        phase=phase,
    )


def stamp(model, row, when):
    model._base_manager.filter(pk=row.pk).update(created_at=when)


class RecordingTemporal:
    """Exact executions keyed by workflow and run ID; records every signal."""

    def __init__(self, experiment):
        self.experiment_id = str(experiment.id)
        self.handles = {}
        self.client = SimpleNamespace(
            get_workflow_handle=Mock(side_effect=self._handle)
        )
        self.connect = AsyncMock(return_value=self.client)

    def _handle(self, workflow_id, run_id=None):
        return self.handles[(workflow_id, run_id)]

    def execution(self, binding, status="RUNNING", closed=None):
        state = {"status": status}
        identity = {
            "experiment_id": self.experiment_id,
            "config_id": str(binding.config_id),
            "workflow_run_id": str(binding.workflow_run_id),
        }

        async def query(name, **kwargs):
            assert name == "run_control"
            return {**identity, "status": state["status"]}

        async def signal(name, stopped, **kwargs):
            assert name == "set_run_stopped"
            state["status"] = "STOPPING" if stopped else "RUNNING"

        handle = SimpleNamespace(
            describe=AsyncMock(
                return_value=SimpleNamespace(
                    status=closed or WorkflowExecutionStatus.RUNNING
                )
            ),
            query=AsyncMock(side_effect=query),
            signal=AsyncMock(side_effect=signal),
        )
        self.handles[(binding.workflow_id, str(binding.workflow_run_id))] = handle
        return handle

    def patched(self):
        return patch(TEMPORAL, self.connect)


def post(view, world, user, workspace, ids):
    return call(
        view,
        "post",
        world["experiment"].id,
        user,
        workspace,
        {"run_ids": [str(value) for value in ids]},
    )


def assert_signalled(handle, stopped):
    handle.signal.assert_awaited_once()
    assert handle.signal.call_args.args[:2] == ("set_run_stopped", stopped)


def test_stop_with_a_run_id_stops_only_that_runs_execution(native, user, workspace):
    world, (first, second) = native
    temporal = RecordingTemporal(world["experiment"])
    named = temporal.execution(bound(first, "execution-first"))
    sibling = temporal.execution(bound(second, "execution-second"))
    with temporal.patched():
        response = post(ExperimentStopV2View, world, user, workspace, [first.run.id])
    assert response.status_code == 202, response.data
    assert response.data["result"]["runs"] == [
        {"run_id": str(first.run.id), "name": first.variant.name, "status": "STOPPING"}
    ]
    assert_signalled(named, True)
    sibling.describe.assert_not_awaited()
    sibling.signal.assert_not_awaited()


def test_header_stop_with_every_active_run_id_stops_every_run(native, user, workspace):
    world, targets = native
    temporal = RecordingTemporal(world["experiment"])
    handles = [
        temporal.execution(bound(target, f"execution-{index}"))
        for index, target in enumerate(targets)
    ]
    with temporal.patched():
        response = post(
            ExperimentStopV2View,
            world,
            user,
            workspace,
            [target.run.id for target in targets],
        )
    assert response.status_code == 202, response.data
    assert {
        run["run_id"]: run["status"] for run in response.data["result"]["runs"]
    } == {str(target.run.id): "STOPPING" for target in targets}
    for handle in handles:
        assert_signalled(handle, True)
    assert temporal.client.get_workflow_handle.call_count == len(targets)


def test_resume_with_a_run_id_resumes_only_that_run(native, user, workspace):
    world, (first, second) = native
    temporal = RecordingTemporal(world["experiment"])
    named = temporal.execution(
        bound(first, "execution-first", phase="STOPPED"), status="STOPPED"
    )
    sibling = temporal.execution(
        bound(second, "execution-second", phase="STOPPED"), status="STOPPED"
    )
    with temporal.patched():
        response = post(ExperimentResumeV2View, world, user, workspace, [first.run.id])
    assert response.status_code == 202, response.data
    assert response.data["result"]["runs"] == [
        {"run_id": str(first.run.id), "name": first.variant.name, "status": "RUNNING"}
    ]
    assert_signalled(named, False)
    sibling.describe.assert_not_awaited()
    sibling.signal.assert_not_awaited()


@pytest.mark.parametrize(
    "view,stopped",
    [(ExperimentStopV2View, True), (ExperimentResumeV2View, False)],
)
def test_variant_ids_select_exactly_as_before(native, user, workspace, view, stopped):
    world, (first, second) = native
    temporal = RecordingTemporal(world["experiment"])
    status = "RUNNING" if stopped else "STOPPED"
    named = temporal.execution(bound(first, "execution-first"), status=status)
    sibling = temporal.execution(bound(second, "execution-second"), status=status)
    with temporal.patched():
        response = post(view, world, user, workspace, [first.variant.id])
    assert response.status_code == 202, response.data
    assert [run["run_id"] for run in response.data["result"]["runs"]] == [
        str(first.variant.id)
    ]
    assert_signalled(named, stopped)
    sibling.signal.assert_not_awaited()


def test_variant_and_run_ids_in_one_selection(native, user, workspace):
    world, (first, second) = native
    temporal = RecordingTemporal(world["experiment"])
    handles = [
        temporal.execution(bound(first, "execution-first")),
        temporal.execution(bound(second, "execution-second")),
    ]
    with temporal.patched():
        response = post(
            ExperimentStopV2View,
            world,
            user,
            workspace,
            [first.variant.id, second.run.id],
        )
    assert response.status_code == 202, response.data
    assert {run["run_id"] for run in response.data["result"]["runs"]} == {
        str(first.variant.id),
        str(second.run.id),
    }
    for handle in handles:
        assert_signalled(handle, True)


def test_an_earlier_run_never_stops_the_newer_runs_execution(native, user, workspace):
    world, (first, _) = native
    start = timezone.now() - timedelta(minutes=10)
    earlier_execution = bound(first, "execution-earlier")
    newer_run = bind(world, execution_id=uuid4())
    newer_execution = bound(first, "execution-newer")
    ExperimentRun._base_manager.filter(pk=first.run.pk).update(
        created_at=start,
        completed_at=start + timedelta(seconds=30),
        status="completed",
    )
    stamp(ExperimentRunControl, earlier_execution, start + timedelta(seconds=1))
    stamp(ExperimentRun, newer_run, start + timedelta(minutes=1))
    stamp(
        ExperimentRunControl, newer_execution, start + timedelta(minutes=1, seconds=1)
    )
    temporal = RecordingTemporal(world["experiment"])
    earlier = temporal.execution(
        earlier_execution, closed=WorkflowExecutionStatus.COMPLETED
    )
    newer = temporal.execution(newer_execution)
    with temporal.patched():
        stale = post(ExperimentStopV2View, world, user, workspace, [first.run.id])
        current = post(ExperimentStopV2View, world, user, workspace, [newer_run.id])
    assert stale.status_code == 409, stale.data
    assert current.status_code == 202, current.data
    assert current.data["result"]["runs"] == [
        {"run_id": str(newer_run.id), "name": first.variant.name, "status": "STOPPING"}
    ]
    assert_signalled(newer, True)
    earlier.signal.assert_not_awaited()


def test_a_run_whose_execution_has_not_bound_never_reaches_an_earlier_one(
    native, user, workspace
):
    world, (first, _) = native
    start = timezone.now() - timedelta(minutes=10)
    earlier_execution = bound(first, "execution-earlier")
    newer_run = bind(world, execution_id=uuid4())
    stamp(ExperimentRun, first.run, start)
    stamp(ExperimentRunControl, earlier_execution, start + timedelta(seconds=1))
    stamp(ExperimentRun, newer_run, start + timedelta(minutes=1))
    temporal = RecordingTemporal(world["experiment"])
    earlier = temporal.execution(earlier_execution)
    with temporal.patched():
        response = post(ExperimentStopV2View, world, user, workspace, [newer_run.id])
    assert response.status_code == 409, response.data
    earlier.signal.assert_not_awaited()


def test_a_run_without_a_variant_is_refused_without_any_signal(native, user, workspace):
    world, (first, _) = native
    snapshot = NativeExperimentSnapshot.objects.get(experiment=world["experiment"])
    external = ExperimentRun.objects.create(
        experiment=world["experiment"],
        dataset_version=snapshot.dataset_version,
        task_kind=ExperimentRun.TaskKind.EXTERNAL,
        status=ExperimentRun.Status.RUNNING,
    )
    temporal = RecordingTemporal(world["experiment"])
    temporal.execution(bound(first, "execution-first"))
    with temporal.patched():
        response = post(ExperimentStopV2View, world, user, workspace, [external.id])
    assert response.status_code == 409, response.data
    temporal.client.get_workflow_handle.assert_not_called()


@pytest.mark.parametrize("view", [ExperimentStopV2View, ExperimentResumeV2View])
def test_run_ids_of_other_experiments_and_organizations_are_refused(
    native, organization, workspace, user, prompt_version, view
):
    world, (first, _) = native
    other_world, (other, _) = native_experiment(
        organization, workspace, user, prompt_version
    )
    temporal = RecordingTemporal(world["experiment"])
    temporal.execution(bound(first, "execution-first"))
    temporal.execution(bound(other, "execution-other"))
    with temporal.patched():
        # Another experiment's run, alone and next to this experiment's run.
        alone = post(view, world, user, workspace, [other.run.id])
        mixed = post(view, world, user, workspace, [first.run.id, other.run.id])
    dataset = other_world["experiment"].dataset
    dataset.organization = Organization.objects.create(
        name="Other synthetic organization"
    )
    dataset.save(update_fields=["organization"])
    with temporal.patched():
        # The same run once its experiment belongs to another organization,
        # named on this experiment and on its own experiment.
        foreign_here = post(view, world, user, workspace, [other.run.id])
        foreign_own = post(view, other_world, user, workspace, [other.run.id])
    for response in (alone, mixed, foreign_here):
        assert response.status_code == 404, response.data
        assert "Run not found." in str(response.data)
    assert foreign_own.status_code == 404, foreign_own.data
    temporal.connect.assert_not_awaited()
