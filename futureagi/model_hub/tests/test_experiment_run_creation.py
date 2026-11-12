import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agent_playground.models.graph import Graph
from agent_playground.models.graph_version import GraphVersion
from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.models.experiments import ExperimentRun, ExperimentsTable
from model_hub.services.experiment_run_creation import (
    create_experiment_run,
)
from model_hub.services.experiment_run_writes import RunWriteError


@pytest.fixture
def run_inputs(organization, workspace, user):
    dataset = Dataset.objects.create(
        name="Run creation dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    version = DatasetVersion.objects.create(dataset=dataset)
    items = [
        DatasetVersionItem.objects.create(
            dataset_version=version, input={"number": number}
        )
        for number in (1, 2)
    ]
    return dataset, version, items, user


def _create(version, items, user, **overrides):
    return create_experiment_run(
        run_id=overrides.pop("run_id", uuid.uuid4()),
        dataset_version=version,
        created_by=user,
        task_kind=overrides.pop("task_kind", "external"),
        expected_items=overrides.pop(
            "expected_items",
            [(item.example_id, trial) for item in items for trial in (1, 2)],
        ),
        **overrides,
    )


@pytest.mark.django_db
def test_create_external_run_pins_two_trials_atomically(run_inputs):
    _, version, items, user = run_inputs

    run = _create(version, items, user)

    assert run.status == ExperimentRun.Status.DRAFT
    assert run.dataset_version_id == version.id
    assert {
        (entry["example_id"], entry["trial_number"])
        for entry in run.metadata["_futureagi_run_write"]["plan"]["identities"]
    } == {(str(item.example_id), trial) for item in items for trial in (1, 2)}


@pytest.mark.django_db
def test_same_run_uuid_replays_frozen_header_and_conflicting_request_fails(run_inputs):
    _, version, items, user = run_inputs
    run_id = uuid.uuid4()
    first = _create(run_id=run_id, version=version, items=items, user=user)
    replay = _create(run_id=run_id, version=version, items=items, user=user)

    assert replay.id == first.id
    assert replay.metadata == first.metadata

    with pytest.raises(RunWriteError, match="already bound"):
        _create(
            run_id=run_id,
            version=version,
            items=items,
            user=user,
            task_kind="prompt",
        )


@pytest.mark.django_db
def test_evaluator_snapshot_resolves_pinned_version_and_survives_default_change(
    run_inputs, organization, workspace, user
):
    _, version, items, _ = run_inputs
    template = EvalTemplate.no_workspace_objects.create(
        name="run_evaluator",
        organization=organization,
        workspace=workspace,
        owner="user",
        eval_type="llm",
    )
    first = EvalTemplateVersion.objects.create(
        eval_template=template,
        version_number=1,
        is_default=True,
        organization=organization,
        workspace=workspace,
        config_snapshot={"prompt": "first"},
    )
    metric = UserEvalMetric.no_workspace_objects.create(
        name="Metric",
        organization=organization,
        workspace=workspace,
        template=template,
        dataset=version.dataset,
        user=user,
    )
    run = _create(version, items, user, evaluator_metrics=[metric])

    first.is_default = False
    first.save(update_fields=["is_default"])
    second = EvalTemplateVersion.objects.create(
        eval_template=template,
        version_number=2,
        is_default=True,
        organization=organization,
        workspace=workspace,
        config_snapshot={"prompt": "second"},
    )

    run.refresh_from_db()
    assert run.evaluator_versions[0]["evaluator_version_id"] == str(first.id)
    assert run.evaluator_versions[0]["config_snapshot"] == {"prompt": "first"}
    assert second.id != first.id

    replay = _create(version, items, user, run_id=run.id, evaluator_metrics=[metric])
    assert replay.evaluator_versions == run.evaluator_versions
    newer = _create(version, items, user, evaluator_metrics=[metric])
    assert newer.evaluator_versions[0]["evaluator_version_id"] == str(second.id)
    metric.pinned_version = first
    metric.save(update_fields=["pinned_version"])
    pinned = _create(version, items, user, evaluator_metrics=[metric])
    assert pinned.evaluator_versions[0]["evaluator_version_id"] == str(first.id)


@pytest.mark.django_db
def test_create_rolls_back_header_when_plan_pin_fails(run_inputs):
    _, version, items, user = run_inputs
    run_id = uuid.uuid4()
    with pytest.raises(RunWriteError, match="outside the dataset"):
        _create(
            run_id=run_id,
            version=version,
            items=items,
            user=user,
            expected_items=[(uuid.uuid4(), 1)],
        )

    assert not ExperimentRun.all_objects.filter(id=run_id).exists()


@pytest.mark.django_db
def test_invalid_task_reference_is_rejected(run_inputs):
    _, version, items, user = run_inputs

    with pytest.raises(RunWriteError, match="prompt_version"):
        _create(
            version=version,
            items=items,
            user=user,
            prompt_version=object(),
        )


@pytest.mark.django_db
def test_create_rejects_experiment_from_another_dataset(run_inputs):
    dataset, version, items, user = run_inputs
    other = Dataset.objects.create(
        name="Unrelated dataset",
        organization=dataset.organization,
        workspace=dataset.workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    experiment = ExperimentsTable.objects.create(name="Other experiment", dataset=other)
    with pytest.raises(RunWriteError, match="experiment must belong"):
        _create(version, items, user, experiment=experiment)
    assert not ExperimentRun.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("denied", ["organization", "workspace"])
def test_actor_membership_is_checked_against_dataset(run_inputs, denied):
    dataset, version, items, user = run_inputs
    membership = OrganizationMembership.no_workspace_objects.get(
        user=user, organization=dataset.organization
    )
    if denied == "organization":
        membership.is_active = False
        membership.save(update_fields=["is_active"])
    else:
        membership.level = 3
        membership.save(update_fields=["level"])
        WorkspaceMembership.no_workspace_objects.filter(
            user=user, workspace=dataset.workspace
        ).update(is_active=False)
    with pytest.raises(RunWriteError, match=denied):
        _create(version, items, user)
    assert not ExperimentRun.objects.exists()


@pytest.mark.django_db
def test_prompt_snapshot_and_default_workspace_null_compatibility(
    run_inputs, prompt_version
):
    _, version, items, user = run_inputs
    template = prompt_version.original_template
    template.workspace = None
    template.save(update_fields=["workspace"])
    config = {"temperature": 0.2, "nested": {"keep": True}}
    run = _create(
        version,
        items,
        user,
        task_kind="prompt",
        prompt_version=prompt_version,
        model_config=config,
    )
    config["nested"]["keep"] = False
    snapshot = run.metadata["_futureagi_run_write"]["creation"]["task_snapshot"]
    assert snapshot["prompt_config_snapshot"] == prompt_version.prompt_config_snapshot
    assert run.model_config["nested"]["keep"] is True
    assert run.prompt_version_id == prompt_version.id


@pytest.mark.django_db
@pytest.mark.parametrize("invalid", ["draft", "deleted", "workspace"])
def test_prompt_reference_refuses_inaccessible_or_draft_versions(
    run_inputs, prompt_version, invalid
):
    dataset, version, items, user = run_inputs
    if invalid in {"draft", "deleted"}:
        setattr(prompt_version, "is_draft" if invalid == "draft" else "deleted", True)
        prompt_version.save()
    else:
        other = Workspace.no_workspace_objects.create(
            name="Other prompt workspace",
            organization=dataset.organization,
            created_by=user,
        )
        template = prompt_version.original_template
        template.workspace = other
        template.save(update_fields=["workspace"])
    with pytest.raises(RunWriteError, match="prompt version"):
        _create(version, items, user, task_kind="prompt", prompt_version=prompt_version)


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["active", "inactive", "draft"])
def test_graph_reference_accepts_only_executable_versions(run_inputs, status):
    dataset, version, items, user = run_inputs
    graph = Graph.no_workspace_objects.create(
        name="Graph task",
        organization=dataset.organization,
        workspace=dataset.workspace,
        created_by=user,
    )
    graph_version = GraphVersion.no_workspace_objects.create(
        graph=graph, version_number=1, status=status
    )
    if status == "draft":
        with pytest.raises(RunWriteError, match="graph version"):
            _create(
                version, items, user, task_kind="graph", agent_version=graph_version
            )
    else:
        run = _create(
            version, items, user, task_kind="graph", agent_version=graph_version
        )
        assert run.agent_version_id == graph_version.id


@pytest.mark.django_db
@pytest.mark.parametrize(
    "metadata", [{"_futureagi_run_write": {}}, {"_futureagi_run_write": []}, []]
)
def test_caller_cannot_poison_creation_envelope(run_inputs, metadata):
    _, version, items, user = run_inputs
    with pytest.raises(RunWriteError):
        _create(version, items, user, metadata=metadata)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "broken", [{"_futureagi_run_write": []}, {"_futureagi_run_write": {"creation": []}}]
)
def test_malformed_existing_creation_receipt_is_an_explicit_conflict(
    run_inputs, broken
):
    _, version, items, user = run_inputs
    run = _create(version, items, user)
    ExperimentRun.no_workspace_objects.filter(id=run.id).update(metadata=broken)
    with pytest.raises(RunWriteError, match="already bound"):
        _create(version, items, user, run_id=run.id)


@pytest.mark.django_db(transaction=True)
def test_concurrent_identical_creation_replays_one_run(run_inputs):
    _, version, items, user = run_inputs
    run_id = uuid.uuid4()
    barrier = Barrier(2)

    def create():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return _create(version, items, user, run_id=run_id).id
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result(timeout=30)
            for future in [pool.submit(create), pool.submit(create)]
        ]
    assert results == [run_id, run_id]
    assert ExperimentRun.no_workspace_objects.filter(id=run_id).count() == 1
