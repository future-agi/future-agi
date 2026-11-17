"""GET /model-hub/experiments/data/ lists only the caller's experiments.

An experiment belongs to an organization and a workspace through its dataset.
"""

import pytest
from rest_framework import status

import model_hub.views.experiments  # noqa: F401  (imported with no workspace bound)
from accounts.models.workspace import Workspace, WorkspaceMembership
from conftest import WorkspaceAwareAPIClient
from model_hub.models.choices import DatasetSourceChoices, StatusType
from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import ExperimentsTable
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.tests.two_tenants import make_tenant

URL = "/model-hub/experiments/data/"


def _dataset(tenant, name, workspace="tenant"):
    return Dataset.no_workspace_objects.create(
        name=name,
        organization=tenant.organization,
        workspace=tenant.workspace if workspace == "tenant" else workspace,
        source=DatasetSourceChoices.BUILD.value,
    )


def _experiment(dataset, name):
    return ExperimentsTable.no_workspace_objects.create(
        name=name, dataset=dataset, status=StatusType.COMPLETED.value
    )


def _listed_ids(response):
    assert response.status_code == status.HTTP_200_OK, response.content
    return {row["id"] for row in response.data["results"]}


@pytest.fixture
def tenants(db):
    tenant_a = make_tenant("A")
    tenant_b = make_tenant("B")
    yield tenant_a, tenant_b
    tenant_a.client.stop_workspace_injection()
    tenant_b.client.stop_workspace_injection()


@pytest.mark.integration
@pytest.mark.api
class TestExperimentListTenantScope:
    def test_experiment_list_hides_other_organization_experiments(self, tenants):
        tenant_a, tenant_b = tenants
        a_in_workspace = _experiment(_dataset(tenant_a, "A data"), "A experiment")
        a_no_workspace = _experiment(
            _dataset(tenant_a, "A data, no workspace", workspace=None),
            "A experiment, no workspace",
        )
        b_own = _experiment(_dataset(tenant_b, "B data"), "B experiment")

        listed = _listed_ids(tenant_b.client.get(URL, {"page": 1, "limit": 50}))

        assert str(a_in_workspace.id) not in listed
        assert str(a_no_workspace.id) not in listed
        assert str(b_own.id) in listed

    def test_experiment_list_dataset_filter_stays_in_caller_scope(self, tenants):
        tenant_a, tenant_b = tenants
        a_dataset = _dataset(tenant_a, "A data")
        _experiment(a_dataset, "A experiment")

        response = tenant_b.client.get(URL, {"dataset_id": str(a_dataset.id)})

        assert _listed_ids(response) == set()

    def test_experiment_list_keeps_callers_own_experiments(self, tenants):
        _, tenant_b = tenants
        first = _dataset(tenant_b, "B first")
        second = _dataset(tenant_b, "B second", workspace=None)
        in_first = _experiment(first, "alpha run")
        in_second = _experiment(second, "beta run")

        everything = _listed_ids(tenant_b.client.get(URL))
        by_dataset = _listed_ids(
            tenant_b.client.get(URL, {"dataset_id": str(first.id)})
        )
        by_search = _listed_ids(tenant_b.client.get(URL, {"search": "beta"}))

        assert everything == {str(in_first.id), str(in_second.id)}
        assert by_dataset == {str(in_first.id)}
        assert by_search == {str(in_second.id)}

    def test_experiment_list_other_workspace_hidden_in_same_organization(self, tenants):
        _, tenant_b = tenants
        clear_workspace_context()
        team_workspace = Workspace.no_workspace_objects.create(
            name="B team workspace",
            organization=tenant_b.organization,
            is_default=False,
            is_active=True,
            created_by=tenant_b.user,
        )
        WorkspaceMembership.no_workspace_objects.create(
            user=tenant_b.user,
            workspace=team_workspace,
            role="Workspace Owner",
            level=Level.OWNER,
            is_active=True,
        )
        default_only = _experiment(_dataset(tenant_b, "B default"), "default run")
        team_only = _experiment(
            _dataset(tenant_b, "B team", workspace=team_workspace), "team run"
        )
        team_client = WorkspaceAwareAPIClient()
        team_client.force_authenticate(user=tenant_b.user)
        team_client.set_workspace(team_workspace)
        try:
            listed = _listed_ids(team_client.get(URL))
        finally:
            team_client.stop_workspace_injection()

        assert listed == {str(team_only.id)}
        assert str(default_only.id) not in listed
