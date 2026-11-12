"""Native identity integration tests; run with real PostgreSQL, not mocked auth."""
import uuid

import pytest

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.choices import SourceChoices
from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import (
    ExperimentAgentConfig, ExperimentDatasetTable, ExperimentPromptConfig,
    ExperimentRun, ExperimentsTable,
)
from model_hub.models.native_experiment_runs import (
    NativeExampleBinding, NativeExperimentSnapshot, NativeRunBinding,
)
from model_hub.services.dataset_snapshot import create_dataset_snapshot
from model_hub.services.experiment_run_writes import RunWriteError
from model_hub.services.native_run_binding import bind_native_run
from model_hub.services.native_run_projection import project_run, unbound_experiment
from tfc.middleware.workspace_context import (
    get_current_organization, get_current_user, get_current_workspace, workspace_context,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def native_world(organization, workspace, user, prompt_version):
    return make_native_world(organization, workspace, user, prompt_version)


@pytest.fixture
def legacy_native_world(organization, workspace, user, prompt_version):
    # Model pre-existing legacy data, not a new dataset in a workspace request.
    # Base save signals fill null workspaces from the ambient test context,
    # so create the source, snapshot and version without that context.
    # Restore it before authorization, recording and projection are exercised.
    with workspace_context(None, organization=organization, user=user):
        world = make_native_world(organization, None, user, prompt_version)
    world["authorized_workspace"] = workspace
    assert world["dataset"].workspace_id is None
    return world


def make_native_world(organization, workspace, user, prompt_version):
    dataset = Dataset.objects.create(
        name="Native source", organization=organization, workspace=workspace,
        user=user, column_order=[], column_config={},
    )
    column = Column.objects.create(
        dataset=dataset, name="input", data_type="text", source=SourceChoices.OTHERS.value,
    )
    rows = [Row.objects.create(dataset=dataset, order=n) for n in (10, 20)]
    for n, row in enumerate(rows):
        Cell.objects.create(dataset=dataset, column=column, row=row, value=f"input-{n}")
    experiment = ExperimentsTable.objects.create(
        dataset=dataset, name="Native experiment", user=user,
    )
    snapshot, columns, row_map = create_dataset_snapshot(dataset, experiment)
    variant = ExperimentDatasetTable.objects.create(name="A", experiment=experiment)
    config = ExperimentPromptConfig.objects.create(
        experiment_dataset=variant, prompt_version=prompt_version,
        prompt_template=prompt_version.original_template, name="A", model="model-a",
        model_params={"temperature": 0.2},
    )
    output = Column.objects.create(
        dataset=snapshot, name="output-A", data_type="text",
        source=SourceChoices.EXPERIMENT.value, source_id=str(variant.id),
    )
    variant.columns.add(output)
    return {
        "dataset": dataset, "experiment": experiment, "variant": variant,
        "output_column": output, "created_by": user, "config": config,
        "source_rows": rows, "source_column": column, "row_map": row_map,
        "snapshot": snapshot, "execution_id": uuid.uuid4(),
    }


def bind(world, **changes):
    kwargs = {key: world[key] for key in (
        "dataset", "experiment", "variant", "output_column", "created_by", "execution_id",
    )}
    if "authorized_workspace" in world:
        kwargs["authorized_workspace"] = world["authorized_workspace"]
    kwargs["effective_settings"] = {"trials": 1, "concurrency": 10}
    return bind_native_run(**{**kwargs, **changes})


def test_snapshot_persists_exact_mapping_and_json_identities(native_world):
    w = native_world
    snapshot = NativeExperimentSnapshot.objects.get(experiment=w["experiment"])
    links = list(NativeExampleBinding.objects.filter(snapshot=snapshot))
    assert {link.source_row_id for link in links} == {r.id for r in w["source_rows"]}
    expected = {row.id: f"input-{index}" for index, row in enumerate(w["source_rows"])}
    for link in links:
        assert link.snapshot_row_id == w["row_map"][link.source_row_id].id
        assert link.version_item.example_id != link.snapshot_row_id
        assert link.version_item.legacy_row_id == link.source_row_id
        assert link.version_item.input == {
            "values": {str(w["source_column"].id): expected[link.source_row_id]}
        }
    w["experiment"].refresh_from_db()
    identities = w["experiment"].run_metadata["native_snapshot"]["identities"]
    assert len(identities) == 2
    assert all(isinstance(entry, dict) for entry in identities)
    assert all(isinstance(value, str) for entry in identities for value in entry.values())


def test_variants_share_examples_but_not_canonical_run_ids(native_world):
    w = native_world
    first = bind(w)
    variant = ExperimentDatasetTable.objects.create(name="B", experiment=w["experiment"])
    ExperimentPromptConfig.objects.create(
        experiment_dataset=variant, prompt_version=w["config"].prompt_version,
        prompt_template=w["config"].prompt_template, name="B", model="model-b",
    )
    output = Column.objects.create(
        dataset=w["snapshot"], name="output-B", data_type="text",
        source=SourceChoices.EXPERIMENT.value,
    )
    variant.columns.add(output)
    second = bind(w, variant=variant, output_column=output)
    assert first.id not in {second.id, w["variant"].id, w["config"].id, w["output_column"].id}
    assert first.dataset_version_id == second.dataset_version_id
    assert first.metadata["_futureagi_run_write"]["plan"] == second.metadata["_futureagi_run_write"]["plan"]
    assert first.items.count() == second.items.count() == 0


def test_replay_and_new_execution_are_distinct(native_world):
    first = bind(native_world)
    replay = bind(native_world)
    another = bind(native_world, execution_id=uuid.uuid4())
    assert first.id == replay.id != another.id
    assert NativeRunBinding.objects.count() == 2
    assert ExperimentRun.objects.count() == 2
    with pytest.raises(RunWriteError):
        bind(native_world, effective_settings={"trials": 1, "concurrency": 2})


def test_source_mutation_cannot_change_pinned_input(native_world):
    w = native_world
    link = NativeExampleBinding.objects.filter(snapshot__experiment=w["experiment"]).first()
    before = link.version_item.input
    Cell.objects.filter(dataset=w["dataset"]).update(value="changed later")
    Row.objects.filter(dataset=w["dataset"]).update(order=99)
    run = bind(w)
    assert DatasetVersionItem.objects.get(id=link.version_item_id).input == before
    assert run.dataset_version.items.count() == 2


@pytest.mark.parametrize("which", ["dataset", "variant", "output", "version", "snapshot_row"])
def test_deleted_references_fail_closed(native_world, which):
    w = native_world
    objects = {
        "dataset": w["dataset"], "variant": w["variant"], "output": w["output_column"],
        "version": NativeExperimentSnapshot.objects.get(experiment=w["experiment"]).dataset_version,
        "snapshot_row": next(iter(w["row_map"].values())),
    }
    objects[which].delete()
    with pytest.raises(RunWriteError):
        bind(w)
    assert ExperimentRun.objects.count() == 0


def test_foreign_workspace_dataset_is_not_accepted(native_world, user):
    w = native_world
    workspace = Workspace.objects.create(
        name="Other", organization=w["dataset"].organization, created_by=user,
    )
    foreign = Dataset.objects.create(
        name="Foreign", organization=w["dataset"].organization, workspace=workspace,
        column_order=[], column_config={},
    )
    with pytest.raises(RunWriteError):
        bind(w, dataset=foreign)
    assert ExperimentRun.objects.count() == 0


def test_foreign_organization_and_foreign_variant_are_refused(native_world, user):
    w = native_world
    org = Organization.objects.create(name="Other native org")
    dataset = Dataset.objects.create(name="Other", organization=org, column_order=[])
    other = ExperimentsTable.objects.create(name="Other", dataset=dataset, user=user)
    variant = ExperimentDatasetTable.objects.create(name="Other", experiment=other)
    with pytest.raises(RunWriteError):
        bind(w, variant=variant)
    with pytest.raises(RunWriteError):
        bind(w, dataset=dataset)
    assert ExperimentRun.objects.count() == 0


def test_snapshot_is_not_recreated_and_does_not_backfill_old_experiment(native_world):
    w = native_world
    count = Dataset.objects.count()
    with pytest.raises(RunWriteError):
        create_dataset_snapshot(w["dataset"], w["experiment"])
    assert Dataset.objects.count() == count
    old = ExperimentsTable.objects.create(name="Old", dataset=w["dataset"], user=w["created_by"])
    status = unbound_experiment(old)
    assert status["comparable"] is False
    assert status["reason"] == "native_identity_not_recorded"
    assert str(old.id) in status["classic_url"]
    assert not NativeExperimentSnapshot.objects.filter(experiment=old).exists()


def test_projection_sanitizes_config_and_has_no_read_writes(native_world):
    w = native_world
    w["config"].model_params = {"temperature": 0.4, "api_key": "fixture-do-not-project"}
    w["config"].configuration = {"headers": {"Authorization": "fixture"}, "tools": [{"name": "search", "credentials": "fixture"}]}
    w["config"].save()
    run = bind(w)
    before = (ExperimentRun.objects.count(), NativeRunBinding.objects.count())
    payload = project_run(run, dataset=w["dataset"])
    assert payload["run_id"] == str(run.id)
    assert payload["trials"] == 1
    assert payload["concurrency"] == 10
    assert payload["trial_reducer"] is None
    assert "fixture" not in str(payload["config"])
    assert before == (ExperimentRun.objects.count(), NativeRunBinding.objects.count())


def test_no_silent_nondefault_trial_or_unknown_policy(native_world):
    with pytest.raises(RunWriteError):
        bind(native_world, effective_settings={"trials": 3, "concurrency": 10})
    with pytest.raises(RunWriteError):
        bind(native_world, requested_settings={"trials": 3})
    with pytest.raises(RunWriteError):
        bind(native_world, effective_settings={"trials": 1, "cost_cap": 1})


def graph_variant(w, workspace):
    from agent_playground.models.graph import Graph
    from agent_playground.models.graph_version import GraphVersion

    graph = Graph.objects.create(
        name="Native graph", organization=w["dataset"].organization,
        workspace=workspace, created_by=w["created_by"],
    )
    version = GraphVersion.objects.create(graph=graph, version_number=1, status="inactive")
    variant = ExperimentDatasetTable.objects.create(name="Graph", experiment=w["experiment"])
    config = ExperimentAgentConfig.objects.create(
        experiment_dataset=variant, graph=graph, graph_version=version, name="Graph",
    )
    output = Column.objects.create(
        dataset=w["snapshot"], name="graph", data_type="text",
        source=SourceChoices.EXPERIMENT.value, source_id=str(variant.id),
    )
    variant.columns.add(output)
    return {**w, "variant": variant, "output_column": output, "config": config}


def test_graph_variant_uses_existing_admitted_version(native_world):
    w = graph_variant(native_world, native_world["dataset"].workspace)
    run = bind(w)
    assert run.task_kind == "graph"
    assert run.agent_version_id == w["config"].graph_version_id
    assert project_run(run, dataset=w["dataset"])["native_config_id"] == str(w["config"].id)
    assert w["output_column"].source == SourceChoices.EXPERIMENT.value
    assert w["variant"].columns.filter(pk=w["output_column"].pk).exists()


def add_native_eval(world):
    from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion, UserEvalMetric

    w = world
    template = EvalTemplate.no_workspace_objects.create(
        name=f"native-eval-{uuid.uuid4().hex}", owner="user", eval_type="llm",
        organization=w["dataset"].organization, workspace=w.get("authorized_workspace", w["dataset"].workspace),
    )
    pin = EvalTemplateVersion.objects.create(
        eval_template=template, version_number=1, is_default=True,
        organization=w["dataset"].organization, workspace=w.get("authorized_workspace", w["dataset"].workspace),
        config_snapshot={"fixture": "pin"},
    )
    metric = UserEvalMetric.no_workspace_objects.create(
        name=template.name, template=template, pinned_version=pin, dataset=w["snapshot"],
        source_id=str(w["experiment"].id), user=w["created_by"],
        organization=w["dataset"].organization, workspace=w.get("authorized_workspace", w["dataset"].workspace),
    )
    w["experiment"].user_eval_template_ids.add(metric)
    return metric, pin


def test_add_native_eval_uses_unique_paired_names_per_call(native_world):
    first, first_pin = add_native_eval(native_world)
    second, second_pin = add_native_eval(native_world)
    assert first.template_id != second.template_id
    assert first.template.name != second.template.name
    assert first.name != second.name
    assert first.name == first.template.name
    assert second.name == second.template.name
    assert first_pin.eval_template_id == first.template_id
    assert second_pin.eval_template_id == second.template_id
    assert set(native_world["experiment"].user_eval_template_ids.values_list("id", flat=True)) == {
        first.id, second.id,
    }


def test_native_snapshot_evaluator_is_pinned_without_source_dataset_alias(native_world):
    metric, pin = add_native_eval(native_world)
    run = bind(native_world)
    assert run.evaluator_versions[0]["metric_id"] == str(metric.id)
    assert run.evaluator_versions[0]["evaluator_version_id"] == str(pin.id)
    assert run.dataset_version.dataset_id == native_world["dataset"].id
    assert metric.dataset_id == native_world["snapshot"].id
    assert bind(native_world).evaluator_versions == run.evaluator_versions
    pin.delete()
    with pytest.raises(RunWriteError):
        bind(native_world)


def test_requested_version_reuse_requires_exact_captured_inputs(native_world):
    w = native_world
    version = NativeExperimentSnapshot.objects.get(experiment=w["experiment"]).dataset_version
    other = ExperimentsTable.objects.create(dataset=w["dataset"], name="Second", user=w["created_by"])
    create_dataset_snapshot(w["dataset"], other, dataset_version=version)
    assert NativeExperimentSnapshot.objects.get(experiment=other).dataset_version_id == version.id
    Cell.objects.filter(dataset=w["dataset"]).update(value="changed")
    third = ExperimentsTable.objects.create(dataset=w["dataset"], name="Third", user=w["created_by"])
    before = Dataset.objects.count()
    with pytest.raises(RunWriteError):
        create_dataset_snapshot(w["dataset"], third, dataset_version=version)
    assert Dataset.objects.count() == before
    assert not NativeExperimentSnapshot.objects.filter(experiment=third).exists()


def test_actor_membership_is_rechecked_without_security_mocks(native_world):
    from accounts.models.organization_membership import OrganizationMembership

    w = native_world
    OrganizationMembership.no_workspace_objects.filter(
        user=w["created_by"], organization=w["dataset"].organization,
    ).update(is_active=False)
    with pytest.raises(RunWriteError):
        bind(w)
    assert ExperimentRun.objects.count() == 0


def configure_prompt(world, version_settings, **overrides):
    from model_hub.models.run_prompt import PromptVersion

    config = world["config"]
    config.prompt_version = PromptVersion.no_workspace_objects.create(
        original_template=config.prompt_template, template_version="fix-regression",
        prompt_config_snapshot={"messages": [{"role": "user", "content": "hello"}],
                                "configuration": version_settings},
        variable_names={},
    )
    config.model_config, config.model_params, config.configuration = {}, {}, {}
    for key, value in overrides.items():
        setattr(config, key, value)
    config.save()
    return config


def test_native_input_has_valid_source(native_world):
    column = Column.no_workspace_objects.get(pk=native_world["source_column"].pk)
    assert column.source == SourceChoices.OTHERS.value
    column.full_clean()


@pytest.mark.parametrize("scope", ["missing", "nondefault", "foreign", "inactive"])
def test_legacy_scope_requires_live_same_org_default(legacy_native_world, scope):
    w = legacy_native_world
    workspace = w["authorized_workspace"]
    if scope == "missing":
        workspace = None
    elif scope in {"nondefault", "foreign"}:
        org = (Organization.objects.create(name="Foreign scope") if scope == "foreign"
               else w["dataset"].organization)
        workspace = Workspace.no_workspace_objects.create(
            name="Rejected scope", organization=org, created_by=w["created_by"],
            is_default=scope == "foreign",
        )
    else:
        Workspace.no_workspace_objects.filter(pk=workspace.pk).update(is_active=False)
    with pytest.raises(RunWriteError):
        bind(w, authorized_workspace=workspace)
    assert not ExperimentRun.no_workspace_objects.exists()


def test_legacy_scope_rechecks_actor_membership(legacy_native_world):
    from accounts.models.organization_membership import OrganizationMembership

    w = legacy_native_world
    OrganizationMembership.no_workspace_objects.filter(
        user=w["created_by"], organization=w["dataset"].organization,
    ).update(is_active=False)
    with pytest.raises(RunWriteError):
        bind(w)
    assert not ExperimentRun.no_workspace_objects.exists()


def test_legacy_fixture_preserves_null_tenants_and_restores_context(legacy_native_world):
    w = legacy_native_world
    workspace = w["authorized_workspace"]
    assert get_current_workspace() == workspace
    assert get_current_organization() == w["dataset"].organization
    assert get_current_user() == w["created_by"]
    version = NativeExperimentSnapshot.objects.get(experiment=w["experiment"]).dataset_version
    for instance in (w["dataset"], w["snapshot"], version):
        instance.refresh_from_db()
        assert instance.workspace_id is None
        assert instance.organization_id == workspace.organization_id
