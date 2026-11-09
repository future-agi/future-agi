"""DSX launch L5: "Use in new experiments" on the real backend.

PUT develops/{id}/edit_dataset_behavior/ {experiment_default_eval_ids} stores
which dataset evals new experiments preselect; GET get_evals_list reports it as
is_experiment_default. Two organizations, one owner each: org B never sees or
changes org A's defaults. Every test starts from a positive control in org A,
so on the base (no field, the key silently dropped) each one fails.
"""

import uuid

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from conftest import WorkspaceAwareAPIClient
from model_hub.models.choices import DatasetSourceChoices, OwnerChoices
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context


class Tenant:
    def __init__(self, label):
        clear_workspace_context()
        tag = f"{label}-{uuid.uuid4().hex[:6]}"
        self.org = Organization.objects.create(name=f"L5 Org {tag}")
        self.user = User.objects.create_user(
            email=f"l5-{tag}@example.com",
            password="l5-test-password",
            name=f"L5 {label}",
            organization=self.org,
            organization_role=OrganizationRoles.OWNER,
        )
        membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
            user=self.user,
            organization=self.org,
            defaults={
                "role": OrganizationRoles.OWNER,
                "level": Level.OWNER,
                "is_active": True,
            },
        )
        self.workspace = Workspace.objects.create(
            name=f"L5 WS {tag}",
            organization=self.org,
            is_default=True,
            is_active=True,
            created_by=self.user,
        )
        WorkspaceMembership.no_workspace_objects.get_or_create(
            user=self.user,
            workspace=self.workspace,
            defaults={
                "role": "Workspace Owner",
                "level": Level.OWNER,
                "is_active": True,
                "organization_membership": membership,
            },
        )
        self.template = EvalTemplate.no_workspace_objects.create(
            name=f"l5-template-{tag}",
            organization=self.org,
            workspace=self.workspace,
            owner=OwnerChoices.USER.value,
            visible_ui=True,
            criteria="Is {{output}} polite?",
        )
        self.dataset = self.make_dataset(f"l5-dataset-{tag}")
        self.evals = [
            self.make_eval(self.dataset, f"l5-{label}-eval-{i}") for i in (1, 2)
        ]
        self.client = WorkspaceAwareAPIClient()
        self.client.force_authenticate(user=self.user)
        self.client.set_workspace(self.workspace)
        clear_workspace_context()

    def make_dataset(self, name):
        return Dataset.no_workspace_objects.create(
            name=name,
            organization=self.org,
            workspace=self.workspace,
            source=DatasetSourceChoices.BUILD.value,
            column_order=[],
            column_config={},
        )

    def make_eval(self, dataset, name, **extra):
        return UserEvalMetric.no_workspace_objects.create(
            name=name,
            dataset=dataset,
            organization=self.org,
            workspace=self.workspace,
            template=self.template,
            config={"mapping": {}},
            **extra,
        )

    def put_defaults(self, dataset, ids, **body):
        return self.client.put(
            f"/model-hub/develops/{dataset.id}/edit_dataset_behavior/",
            {"experiment_default_eval_ids": [str(i) for i in ids], **body},
            format="json",
        )

    def eval_list(self, dataset):
        return self.client.get(
            f"/model-hub/develops/{dataset.id}/get_evals_list/",
            {"eval_type": "user"},
        )

    def defaults_seen(self, dataset):
        response = self.eval_list(dataset)
        assert response.status_code == status.HTTP_200_OK, response.content
        return {
            item["name"]: item["is_experiment_default"]
            for item in response.json()["result"]["evals"]
        }

    def stored(self):
        return {
            e.name: e.is_experiment_default
            for e in UserEvalMetric.all_objects.filter(dataset=self.dataset)
        }


@pytest.fixture
def tenants(db):
    a = Tenant("a")
    b = Tenant("b")
    yield a, b
    a.client.stop_workspace_injection()
    b.client.stop_workspace_injection()


@pytest.mark.django_db
def test_defaults_are_stored_listed_replaced_and_cleared(tenants):
    a, _ = tenants
    one, two = a.evals

    assert a.put_defaults(a.dataset, [one.id]).status_code == status.HTTP_200_OK
    assert a.defaults_seen(a.dataset) == {one.name: True, two.name: False}

    # The list is the whole set: the second call replaces the first.
    assert a.put_defaults(a.dataset, [two.id]).status_code == status.HTTP_200_OK
    assert a.defaults_seen(a.dataset) == {one.name: False, two.name: True}

    assert a.put_defaults(a.dataset, []).status_code == status.HTTP_200_OK
    assert a.defaults_seen(a.dataset) == {one.name: False, two.name: False}


@pytest.mark.django_db
def test_a_save_without_the_field_keeps_the_defaults(tenants):
    a, _ = tenants
    one, two = a.evals
    assert a.put_defaults(a.dataset, [one.id]).status_code == status.HTTP_200_OK

    response = a.client.put(
        f"/model-hub/develops/{a.dataset.id}/edit_dataset_behavior/",
        {"dataset_config": {"dismiss_banner": True}},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert a.defaults_seen(a.dataset) == {one.name: True, two.name: False}


@pytest.mark.django_db
def test_an_eval_of_another_dataset_refuses_the_whole_request(tenants):
    a, _ = tenants
    one, two = a.evals
    other_dataset = a.make_dataset(f"l5-other-{uuid.uuid4().hex[:6]}")
    other_eval = a.make_eval(other_dataset, "l5-a-other-dataset-eval")
    experiment_eval = a.make_eval(
        a.dataset, "l5-a-experiment-eval", show_in_sidebar=False
    )
    assert a.put_defaults(a.dataset, [one.id]).status_code == status.HTTP_200_OK

    for foreign in (other_eval.id, experiment_eval.id, uuid.uuid4()):
        response = a.put_defaults(a.dataset, [two.id, foreign])
        assert response.status_code == status.HTTP_400_BAD_REQUEST, foreign

    assert a.defaults_seen(a.dataset) == {one.name: True, two.name: False}
    assert (
        UserEvalMetric.all_objects.get(id=other_eval.id).is_experiment_default is False
    )


@pytest.mark.django_db
def test_org_b_cannot_set_or_clear_org_a_defaults(tenants):
    a, b = tenants
    a_one, a_two = a.evals
    assert a.put_defaults(a.dataset, [a_one.id]).status_code == status.HTTP_200_OK
    before = a.stored()
    assert before == {a_one.name: True, a_two.name: False}

    # B on A's dataset: refused, whether it names A's evals or clears them.
    # The view's own catch-all turns its Http404 into a 500 today; the status
    # is the edit_dataset_behavior org-filter fix's (lane L4), so this asserts
    # the refusal and that nothing of A's changed, not one status code.
    for ids in ([a_two.id], [], [a_one.id, a_two.id]):
        response = b.put_defaults(a.dataset, ids)
        assert response.status_code >= 400, (ids, response.status_code)
        assert "Dataset behavior updated" not in response.content.decode()
        assert a.stored() == before

    # B on its own dataset, naming A's evals: refused, nothing changes anywhere.
    response = b.put_defaults(b.dataset, [a_two.id])
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert a.stored() == before
    assert set(b.stored().values()) == {False}

    # B's own defaults work and stay B's.
    assert b.put_defaults(b.dataset, [b.evals[0].id]).status_code == status.HTTP_200_OK
    assert a.stored() == before
    assert b.defaults_seen(b.dataset) == {b.evals[0].name: True, b.evals[1].name: False}


@pytest.mark.django_db
def test_org_b_never_reads_org_a_defaults(tenants):
    a, b = tenants
    a_one, _ = a.evals
    assert a.put_defaults(a.dataset, [a_one.id]).status_code == status.HTTP_200_OK
    assert a.defaults_seen(a.dataset)[a_one.name] is True

    response = b.eval_list(a.dataset)
    assert response.status_code >= 400
    body = response.content.decode()
    for e in a.evals:
        assert e.name not in body and str(e.id) not in body

    seen_by_b = b.defaults_seen(b.dataset)
    assert set(seen_by_b) == {e.name for e in b.evals}
    assert set(seen_by_b.values()) == {False}


@pytest.mark.django_db
def test_the_service_refuses_a_dataset_of_another_organization(tenants):
    from model_hub.services.experiment_defaults import (
        ExperimentDefaultsError,
        set_experiment_defaults,
    )

    a, b = tenants
    a_one, a_two = a.evals
    assert set_experiment_defaults(
        dataset=a.dataset, organization=a.org, eval_ids=[a_one.id]
    ) == [str(a_one.id)]
    before = a.stored()

    with pytest.raises(ExperimentDefaultsError):
        set_experiment_defaults(dataset=a.dataset, organization=b.org, eval_ids=[])
    with pytest.raises(ExperimentDefaultsError):
        set_experiment_defaults(
            dataset=a.dataset, organization=b.org, eval_ids=[a_two.id]
        )
    with pytest.raises(ExperimentDefaultsError):
        set_experiment_defaults(
            dataset=b.dataset, organization=b.org, eval_ids=[a_two.id]
        )
    with pytest.raises(ExperimentDefaultsError):
        set_experiment_defaults(
            dataset=a.dataset, organization=a.org, eval_ids=["not-a-uuid"]
        )

    assert a.stored() == before
