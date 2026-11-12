"""The demo seed saves what it creates in the organization's default workspace.

The seed runs in a background activity with no request and no workspace bound,
so nothing assigns a workspace for it. Every object it creates that has a
workspace field is saved in the default workspace, the one the demo projects
use. Reads that look a dataset up by the caller's workspace then find the
demo dataset, and a member of another workspace does not see it.
"""

import json
from collections import Counter
from unittest.mock import patch

import pytest
from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.db import DatabaseError

from accounts.demo_seeding import seed_pending_demo_data
from accounts.models import Organization
from accounts.models.demo_seed import DemoSeedRequest
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace
from accounts.utils import first_signup
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    get_current_organization,
    get_current_workspace,
    workspace_context,
)

# Evaluation templates the demo dataset links its columns to. An install loads
# them with the system evaluations; the test database starts without them.
DEMO_EVAL_IDS = (101, 47, 66, 100)

# Models the seed must save with a workspace. The check covers every other
# model with a workspace field too; these make sure it is never vacuous.
REQUIRED_WORKSPACE_MODELS = {
    "model_hub.dataset",
    "model_hub.userevalmetric",
    "model_hub.runprompter",
    "model_hub.annotations",
    "model_hub.annotationslabels",
    "tracer.project",
}


@pytest.fixture
def signup(monkeypatch):
    monkeypatch.setenv("CH25_TRACE_DUAL_WRITE", "true")
    with (
        patch("accounts.utils.is_cloud_env", return_value=False),
        patch("accounts.utils._fire_deployment_telemetry_registration"),
        patch(
            "tfc.temporal.drop_in.start_activity",
            side_effect=TimeoutError("dispatch timed out"),
        ),
    ):
        yield lambda: first_signup(
            {
                "email": "workspace-seed@futureagi.com",
                "full_name": "Workspace Seed",
                "password": "SafePassword123!",
            }
        )


@pytest.fixture
def member_client():
    """API clients for a user acting in a given workspace."""
    from conftest import WorkspaceAwareAPIClient

    clients = []

    def make(user, workspace):
        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=user)
        client.set_workspace(workspace)
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.stop_workspace_injection()
    clear_workspace_context()


def add_demo_templates():
    from model_hub.models.evals_metric import EvalTemplate

    for eval_id in DEMO_EVAL_IDS:
        EvalTemplate.no_workspace_objects.get_or_create(
            eval_id=eval_id,
            defaults={"name": f"eval-{eval_id}", "config": {"config": {}}},
        )


def default_workspace(organization_id):
    return Workspace.no_workspace_objects.get(
        organization_id=organization_id, is_default=True, is_active=True
    )


def seed_in_background(organization_id):
    """Run the seed the way the background activity does: nothing bound."""
    assert get_current_organization() is None
    assert get_current_workspace() is None
    seed_pending_demo_data(str(organization_id))
    intent = DemoSeedRequest.objects.get(organization_id=organization_id)
    assert intent.traces_seeded and intent.datasets_seeded
    assert intent.completed_at is not None
    # Nothing stays bound after the seed.
    assert get_current_organization() is None
    assert get_current_workspace() is None


def workspace_models():
    """Every concrete model with a ``workspace`` foreign key to a Workspace."""
    found = []
    for model in apps.get_models():
        meta = model._meta
        if not meta.managed or meta.proxy or meta.swapped:
            continue
        try:
            field = meta.get_field("workspace")
        except FieldDoesNotExist:
            continue
        if field.concrete and field.is_relation and field.related_model is Workspace:
            found.append(model)
    return found


def workspace_rows(models):
    """The workspace of every row of ``models``, by model label and key."""
    rows = {}
    for model in models:
        try:
            rows[model._meta.label_lower] = dict(
                model._base_manager.values_list("pk", "workspace_id")
            )
        except DatabaseError:
            rows[model._meta.label_lower] = None
    return rows


def rows_added(before, after):
    added = {}
    for label, rows in after.items():
        if rows is None:
            continue
        known = before.get(label) or {}
        new = {pk: workspace for pk, workspace in rows.items() if pk not in known}
        if new:
            added[label] = new
    return added


def demo_dataset(organization_id, name="Demo-dataset"):
    from model_hub.models.develop_dataset import Dataset

    return Dataset.no_workspace_objects.get(
        organization_id=organization_id, source="demo", name=name
    )


def seeded_demo(signup):
    """A new sign-up whose demo seed ran in the background."""
    user = signup()
    add_demo_templates()
    seed_in_background(user.organization_id)
    return user, default_workspace(user.organization_id)


def splits_path(dataset):
    return f"/model-hub/datasets/{dataset.id}/splits/"


def runs_path(dataset):
    return f"/model-hub/datasets/{dataset.id}/runs/"


def allow_optimization(monkeypatch):
    # Dataset optimization is an EE feature; the test licence does not carry it.
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", lambda *args, **kwargs: None)


@pytest.mark.django_db(transaction=True)
def test_seed_saves_every_workspace_object_in_the_default_workspace(signup):
    user = signup()
    add_demo_templates()
    workspace = default_workspace(user.organization_id)
    # Another organization's default workspace exists next to this one.
    neighbour = Organization.objects.create(name="Neighbour org")
    neighbour_workspace = Workspace.objects.create(
        name="Default Workspace",
        organization=neighbour,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    models = workspace_models()
    before = workspace_rows(models)

    seed_in_background(user.organization_id)

    after = workspace_rows(models)
    unreadable = sorted(label for label, rows in after.items() if rows is None)
    added = rows_added(before, after)
    print("SEEDED_WORKSPACE_MODELS", sorted(added))
    print("UNREADABLE_WORKSPACE_MODELS", unreadable)
    assert REQUIRED_WORKSPACE_MODELS.isdisjoint(unreadable)
    assert REQUIRED_WORKSPACE_MODELS <= set(added), sorted(added)
    outside = Counter(
        (label, str(row_workspace))
        for label, rows in added.items()
        for row_workspace in rows.values()
        if row_workspace != workspace.id
    )
    assert not outside, (
        f"saved outside the default workspace {workspace.id}: {dict(outside)}"
    )
    assert all(
        row_workspace != neighbour_workspace.id
        for rows in added.values()
        for row_workspace in rows.values()
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "order",
    ["traces_then_datasets", "datasets_then_traces"],
    ids=["background-order", "reseed-migration-order"],
)
def test_organization_without_a_default_workspace_gets_one_for_both_paths(
    signup, order
):
    from accounts.user_onboard import create_demo_traces_and_spans, upload_demo_dataset
    from model_hub.models.develop_dataset import Dataset
    from tracer.models.project import Project

    user = signup()
    add_demo_templates()
    second = Organization.objects.create(name="Second org")
    OrganizationMembership.objects.create(
        user=user, organization=second, role="Owner", level=70, is_active=True
    )
    assert not Workspace.no_workspace_objects.filter(organization=second).exists()

    if order == "traces_then_datasets":
        DemoSeedRequest.objects.create(user=user, organization=second)
        seed_in_background(second.id)
    else:
        upload_demo_dataset(str(second.id), str(user.id))
        assert create_demo_traces_and_spans(str(second.id), str(user.id)) is None

    workspaces = Workspace.no_workspace_objects.filter(organization=second)
    assert workspaces.count() == 1
    workspace = workspaces.get()
    assert workspace.is_default and workspace.is_active
    assert workspace.name == "Default Workspace"
    assert workspace.created_by_id == user.id
    projects = Project.no_workspace_objects.filter(organization=second, source="demo")
    datasets = Dataset.no_workspace_objects.filter(organization=second, source="demo")
    assert projects.count() == 2
    assert datasets.count() == 2
    assert {project.workspace_id for project in projects} == {workspace.id}
    assert {dataset.workspace_id for dataset in datasets} == {workspace.id}


@pytest.mark.django_db(transaction=True)
def test_demo_objects_stay_out_of_another_workspace(signup, member_client):
    from model_hub.models.develop_annotations import Annotations, AnnotationsLabels
    from model_hub.models.develop_dataset import Dataset
    from model_hub.models.evals_metric import UserEvalMetric
    from model_hub.models.run_prompt import RunPrompter

    user = signup()
    add_demo_templates()
    workspace = default_workspace(user.organization_id)
    team = Workspace.objects.create(
        name="Team workspace",
        organization_id=user.organization_id,
        is_default=False,
        is_active=True,
        created_by=user,
    )

    seed_in_background(user.organization_id)

    organization_id = user.organization_id
    seeded = {
        "datasets": Dataset.no_workspace_objects.filter(
            organization_id=organization_id, source="demo"
        ),
        "evaluation metrics": UserEvalMetric.no_workspace_objects.filter(
            organization_id=organization_id
        ),
        "run prompts": RunPrompter.no_workspace_objects.filter(
            organization_id=organization_id
        ),
        "annotations": Annotations.no_workspace_objects.filter(
            organization_id=organization_id
        ),
        "annotation labels": AnnotationsLabels.no_workspace_objects.filter(
            organization_id=organization_id
        ),
    }
    for name, rows in seeded.items():
        assert rows.exists(), name
        assert set(rows.values_list("workspace_id", flat=True)) == {workspace.id}, name

    # The shared managers: a member working in the other workspace sees none.
    with workspace_context(team, organization=team.organization):
        for name, rows in seeded.items():
            assert not rows.model.objects.filter(
                organization_id=organization_id
            ).exists(), name
    with workspace_context(workspace, organization=workspace.organization):
        assert (
            Dataset.objects.filter(
                organization_id=organization_id, source="demo"
            ).count()
            == 2
        )

    # The dataset read: found from the default workspace, not from the other.
    demo = demo_dataset(organization_id)
    in_team = member_client(user, team).get(splits_path(demo))
    assert in_team.status_code == 404, in_team.content
    in_default = member_client(user, workspace).get(splits_path(demo))
    assert in_default.status_code == 200, in_default.content


@pytest.mark.django_db(transaction=True)
def test_splits_read_finds_the_seeded_datasets(signup, member_client):
    user, workspace = seeded_demo(signup)
    client = member_client(user, workspace)
    for name in ("Demo-dataset", "Image-Demo-dataset"):
        response = client.get(splits_path(demo_dataset(user.organization_id, name)))
        assert response.status_code == 200, (name, response.content)
        assert response.json()["result"]["splits"] == []


@pytest.mark.django_db(transaction=True)
def test_runs_and_optimization_reads_answer_for_the_seeded_dataset(
    signup, member_client, monkeypatch
):
    allow_optimization(monkeypatch)
    user, workspace = seeded_demo(signup)
    client = member_client(user, workspace)
    demo = demo_dataset(user.organization_id)
    runs = client.get(runs_path(demo))
    assert runs.status_code == 200, runs.content
    optimizations = client.get(
        "/model-hub/dataset-optimization/", {"dataset_id": str(demo.id)}
    )
    assert optimizations.status_code == 200, optimizations.content


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "The dataset runs read lists experiment runs recorded against a dataset "
        "version. The demo seed saves its experiment as an experiment table "
        "with no dataset version and no run, so the read has nothing to list "
        "whatever workspace the dataset is in."
    ),
)
@pytest.mark.django_db(transaction=True)
def test_runs_read_lists_the_seeded_experiment(signup, member_client):
    from model_hub.models.experiments import ExperimentsTable

    user, workspace = seeded_demo(signup)
    demo = demo_dataset(user.organization_id)
    experiment = ExperimentsTable.no_workspace_objects.get(
        dataset=demo, name="Response Generation"
    )
    response = member_client(user, workspace).get(runs_path(demo))
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["count"] >= 1, result
    assert str(experiment.id) in json.dumps(result["results"])


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "The dataset optimization read lists optimization runs of the newer "
        "kind, those with an optimizer algorithm. The demo seed saves the "
        "older optimization record, which that read does not include, "
        "whatever workspace the dataset is in."
    ),
)
@pytest.mark.django_db(transaction=True)
def test_optimization_read_lists_the_seeded_optimization(
    signup, member_client, monkeypatch
):
    from model_hub.models.develop_optimisation import OptimizationDataset

    allow_optimization(monkeypatch)
    user, workspace = seeded_demo(signup)
    demo = demo_dataset(user.organization_id)
    optimization = OptimizationDataset.no_workspace_objects.get(
        dataset=demo, name="Optimization"
    )
    response = member_client(user, workspace).get(
        "/model-hub/dataset-optimization/", {"dataset_id": str(demo.id)}
    )
    assert response.status_code == 200, response.content
    table = response.json()["result"]["table"]
    assert str(optimization.id) in json.dumps(table), table


@pytest.mark.django_db(transaction=True)
def test_seed_delivered_again_creates_nothing_twice(signup):
    user = signup()
    add_demo_templates()
    models = workspace_models()
    seed_in_background(user.organization_id)
    first = workspace_rows(models)
    workspaces = set(
        Workspace.no_workspace_objects.filter(
            organization_id=user.organization_id
        ).values_list("id", flat=True)
    )

    # A finished seed delivered again returns at once.
    seed_pending_demo_data(str(user.organization_id))
    assert workspace_rows(models) == first
    # A seed restarted after both stages committed repeats neither stage.
    DemoSeedRequest.objects.filter(organization_id=user.organization_id).update(
        completed_at=None
    )
    seed_pending_demo_data(str(user.organization_id))
    assert workspace_rows(models) == first
    assert (
        set(
            Workspace.no_workspace_objects.filter(
                organization_id=user.organization_id
            ).values_list("id", flat=True)
        )
        == workspaces
    )
    assert (
        DemoSeedRequest.objects.get(organization_id=user.organization_id).completed_at
        is not None
    )
