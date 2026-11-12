"""Two-tenant proof for every experiment read and write dev's Data page and
Experiments list send (Capability contract).

The launch build shows dev's own experiment pages in the shell: the
Experiments list (``experiments/v2/list/``, re-run, stop, delete), the Data
page (detail, ``rows/``, the row drawer's legacy row read, ``json-schema/``,
the CSV ``download/``) and the stats read. Organization B must never read or
change organization A's experiment through any of them. Each refusal is
paired with organization A's own request (positive control), so a refusal
caused by a broken fixture cannot pass as isolation.

Organization comes from the caller's membership: every client here is a
member of exactly one organization and sends only its own workspace.
"""

import uuid
from unittest.mock import patch

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from conftest import WorkspaceAwareAPIClient
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

V2 = "/model-hub/experiments/v2"


def _body(response):
    """Response bytes, for plain and streaming (FileResponse) answers."""
    if getattr(response, "streaming", False):
        return b"".join(response.streaming_content)
    return response.content


def _ids(response):
    payload = response.json()
    if isinstance(payload, dict):
        payload = payload.get("results", payload.get("result", payload))
        if isinstance(payload, dict):
            payload = payload.get("results", [])
    return {str(row.get("id")) for row in payload if isinstance(row, dict)}


def _make_experiment(organization, workspace, user, tag):
    """A dataset with one input cell, its experiment snapshot with one output
    cell, and a completed experiment over it. The cell values are unique
    markers: finding one in a response means the experiment's data leaked."""
    secret_in = f"{tag}-input-{uuid.uuid4().hex}"
    secret_out = f"{tag}-output-{uuid.uuid4().hex}"
    dataset = Dataset.no_workspace_objects.create(
        name=f"{tag}-dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
    )
    input_column = Column.objects.create(
        name=f"{tag}-question",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(input_column.id)]
    dataset.save(update_fields=["column_order"])
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(
        dataset=dataset,
        column=input_column,
        row=row,
        value=secret_in,
        status=CellStatus.PASS.value,
    )

    snapshot = Dataset.no_workspace_objects.create(
        name=f"{tag}-snapshot",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.EXPERIMENT_SNAPSHOT.value,
    )
    output_column = Column.objects.create(
        name=f"{tag}-output",
        dataset=snapshot,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EXPERIMENT.value,
        status=StatusType.COMPLETED.value,
    )
    snapshot.column_order = [str(output_column.id)]
    snapshot.save(update_fields=["column_order"])
    snapshot_row = Row.objects.create(dataset=snapshot, order=0)
    output_cell = Cell.objects.create(
        dataset=snapshot,
        column=output_column,
        row=snapshot_row,
        value=secret_out,
        status=CellStatus.ERROR.value,
    )

    experiment = ExperimentsTable.no_workspace_objects.create(
        name=f"{tag}-experiment-{uuid.uuid4().hex[:8]}",
        dataset=dataset,
        snapshot_dataset=snapshot,
        user=user,
        status=StatusType.COMPLETED.value,
        experiment_type="llm",
        prompt_config=[],
    )
    edt = ExperimentDatasetTable.no_workspace_objects.create(
        name=f"{tag}-edt",
        status=StatusType.COMPLETED.value,
        experiment=experiment,
    )
    edt.columns.add(output_column)
    experiment.experiments_datasets.add(edt)
    return {
        "experiment": experiment,
        "dataset": dataset,
        "row": row,
        "snapshot_row": snapshot_row,
        "output_column": output_column,
        "output_cell": output_cell,
        "secrets": (secret_in.encode(), secret_out.encode()),
        "name": experiment.name.encode(),
    }


def _member_client(org_name, email_tag):
    """A user who is a member of exactly one new organization, with a client
    that sends only that organization's workspace."""
    org = Organization.objects.create(name=org_name)
    clear_workspace_context()
    set_workspace_context(organization=org)
    user = User.objects.create_user(
        email=f"l3-tenancy-{email_tag}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name=f"L3 {email_tag}",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    workspace = Workspace.objects.create(
        name=f"{org_name} WS",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        role="Workspace Owner",
        level=Level.OWNER,
        is_active=True,
        organization_membership=membership,
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    clear_workspace_context()
    return {"org": org, "user": user, "workspace": workspace, "client": client}


@pytest.fixture
def tenants(db):
    a = _member_client("L3 Tenancy Org A", "a")
    b = _member_client("L3 Tenancy Org B", "b")
    a["exp"] = _make_experiment(a["org"], a["workspace"], a["user"], "orga")
    b["exp"] = _make_experiment(b["org"], b["workspace"], b["user"], "orgb")
    clear_workspace_context()
    yield {"a": a, "b": b}
    for side in (a, b):
        side["client"].stop_workspace_injection()
    clear_workspace_context()


def _leaks(response, exp):
    body = _body(response)
    return [m for m in (*exp["secrets"], exp["name"]) if m in body]


def _workspace_member(org, workspace, tag):
    """A member (not an owner) of ``workspace`` only, with a client that
    sends that workspace."""
    clear_workspace_context()
    set_workspace_context(workspace=workspace, organization=org)
    user = User.objects.create_user(
        email=f"l3-tenancy-{tag}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name=f"L3 {tag}",
        organization=org,
        organization_role=OrganizationRoles.MEMBER,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.update_or_create(
        user=user,
        organization=org,
        defaults={
            "role": OrganizationRoles.MEMBER,
            "level": Level.MEMBER,
            "is_active": True,
        },
    )
    WorkspaceMembership.no_workspace_objects.update_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "role": OrganizationRoles.WORKSPACE_MEMBER,
            "level": Level.MEMBER,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    clear_workspace_context()
    return client


@pytest.fixture
def no_workspace(tenants):
    """Organization A's experiment, its dataset and its snapshot have no
    workspace, like seeded demo data and datasets older than workspaces."""
    a = tenants["a"]
    exp = a["exp"]
    Dataset.no_workspace_objects.filter(
        id__in=[exp["dataset"].id, exp["experiment"].snapshot_dataset_id]
    ).update(workspace=None)
    sibling = Workspace.objects.create(
        name="L3 Tenancy Org A sibling",
        organization=a["org"],
        is_default=False,
        is_active=True,
        created_by=a["user"],
    )
    clients = {
        "member": _workspace_member(a["org"], a["workspace"], "a-member"),
        "sibling": _workspace_member(a["org"], sibling, "a-sibling"),
        "foreign": tenants["b"]["client"],
    }
    yield exp, clients
    clients["member"].stop_workspace_injection()
    clients["sibling"].stop_workspace_injection()
    clear_workspace_context()


NO_WORKSPACE_READS = [
    ("{v2}/{id}/", None),
    ("{v2}/{id}/rows/", {"current_page_index": 0, "page_size": 10}),
    ("{v2}/{id}/stats/", None),
]


@pytest.mark.integration
@pytest.mark.api
class TestExperimentListTenancy:
    def test_v2_list_for_a_dataset_shows_only_the_callers_experiments(self, tenants):
        a, b = tenants["a"], tenants["b"]
        a_id = str(a["exp"]["experiment"].id)
        own = a["client"].get(
            f"{V2}/list/", {"dataset_id": str(a["exp"]["dataset"].id)}
        )
        assert own.status_code == 200
        assert a_id in _ids(own)

        theirs = b["client"].get(
            f"{V2}/list/", {"dataset_id": str(a["exp"]["dataset"].id)}
        )
        assert theirs.status_code in (200, 400, 403, 404)
        if theirs.status_code == 200:
            assert a_id not in _ids(theirs)
        assert _leaks(theirs, a["exp"]) == []

    def test_v2_list_and_search_never_return_another_orgs_experiment(self, tenants):
        a, b = tenants["a"], tenants["b"]
        a_id = str(a["exp"]["experiment"].id)
        b_id = str(b["exp"]["experiment"].id)
        listed = b["client"].get(f"{V2}/list/")
        assert listed.status_code == 200
        assert b_id in _ids(listed)
        assert a_id not in _ids(listed)
        searched = b["client"].get(
            f"{V2}/list/", {"search": a["exp"]["experiment"].name}
        )
        assert searched.status_code == 200
        assert a_id not in _ids(searched)
        assert _leaks(searched, a["exp"]) == []

    def test_legacy_experiment_selector_list_is_scoped(self, tenants):
        # DevelopExperimentSelect on the Data page reads /experiment-detail/.
        a, b = tenants["a"], tenants["b"]
        a_id = str(a["exp"]["experiment"].id)
        params = {"dataset_id": str(a["exp"]["dataset"].id), "page": 1}
        own = a["client"].get("/model-hub/experiment-detail/", params)
        assert own.status_code == 200
        assert a_id in _ids(own)
        theirs = b["client"].get("/model-hub/experiment-detail/", params)
        assert theirs.status_code in (200, 400, 403, 404)
        if theirs.status_code == 200:
            assert a_id not in _ids(theirs)
        assert _leaks(theirs, a["exp"]) == []


@pytest.mark.integration
@pytest.mark.api
class TestExperimentReadTenancy:
    @pytest.mark.parametrize(
        "path,params",
        [
            ("{v2}/{id}/", None),
            ("{v2}/{id}/rows/", {"current_page_index": 0, "page_size": 10}),
            ("{v2}/{id}/rows/", {"column_config_only": True}),
            ("{v2}/{id}/json-schema/", None),
            ("{v2}/{id}/stats/", None),
        ],
    )
    def test_reads_refuse_another_org(self, tenants, path, params):
        a, b = tenants["a"], tenants["b"]
        url = path.format(v2=V2, id=a["exp"]["experiment"].id)
        own = a["client"].get(url, params or {})
        assert own.status_code == 200, own.content[:300]
        theirs = b["client"].get(url, params or {})
        assert theirs.status_code >= 400
        assert _leaks(theirs, a["exp"]) == []

    def test_legacy_row_read_refuses_another_org(self, tenants):
        # The Data page's row drawer reads /experiments/{id}/{row_id}/.
        a, b = tenants["a"], tenants["b"]
        url = (
            f"/model-hub/experiments/{a['exp']['experiment'].id}/"
            f"{a['exp']['snapshot_row'].id}/"
        )
        own = a["client"].get(url)
        assert own.status_code == 200, own.content[:300]
        theirs = b["client"].get(url)
        assert theirs.status_code >= 400
        assert _leaks(theirs, a["exp"]) == []

    @pytest.mark.parametrize(
        "prefix", [V2, "/model-hub/experiments"], ids=["v2", "legacy"]
    )
    def test_csv_download_refuses_another_org(self, tenants, prefix):
        # Dev's Data page Download button; both routes share one view.
        a, b = tenants["a"], tenants["b"]
        url = f"{prefix}/{a['exp']['experiment'].id}/download/"
        own = a["client"].get(url)
        assert own.status_code == 200
        own_body = _body(own)
        assert a["exp"]["secrets"][0] in own_body
        theirs = b["client"].get(url)
        assert _leaks(theirs, a["exp"]) == [], (
            f"org B read org A's experiment CSV (status {theirs.status_code})"
        )
        assert theirs.status_code >= 400


@pytest.mark.integration
@pytest.mark.api
class TestExperimentWriteTenancy:
    def test_delete_of_another_orgs_experiment_changes_nothing(self, tenants):
        a, b = tenants["a"], tenants["b"]
        target = a["exp"]["experiment"]
        theirs = b["client"].delete(
            f"{V2}/delete/", {"experiment_ids": [str(target.id)]}, format="json"
        )
        assert theirs.status_code >= 400
        target.refresh_from_db()
        assert target.deleted is False
        # Positive control: B deletes its own experiment the same way.
        own = b["client"].delete(
            f"{V2}/delete/",
            {"experiment_ids": [str(b["exp"]["experiment"].id)]},
            format="json",
        )
        assert own.status_code == 200, own.content[:300]
        b["exp"]["experiment"].refresh_from_db()
        assert b["exp"]["experiment"].deleted is True

    def test_rerun_of_another_orgs_experiment_starts_nothing(self, tenants):
        a, b = tenants["a"], tenants["b"]
        target = a["exp"]["experiment"]
        with patch(
            "tfc.temporal.experiments.start_experiment_v2_workflow",
            side_effect=lambda **kwargs: f"experiment-{kwargs['experiment_id']}",
        ) as start:
            theirs = b["client"].post(
                f"{V2}/re-run/",
                {"experiment_ids": [str(target.id)]},
                format="json",
            )
            assert theirs.status_code >= 400
            assert start.call_count == 0
            target.refresh_from_db()
            assert target.status == StatusType.COMPLETED.value
            # Positive control: B re-runs its own experiment.
            own = b["client"].post(
                f"{V2}/re-run/",
                {"experiment_ids": [str(b["exp"]["experiment"].id)]},
                format="json",
            )
            assert own.status_code == 200, own.content[:300]
            assert start.call_count >= 1
        target.refresh_from_db()
        assert target.status == StatusType.COMPLETED.value

    def test_rerun_cells_of_another_orgs_experiment_starts_nothing(self, tenants):
        a, b = tenants["a"], tenants["b"]
        exp = a["exp"]
        body = {
            "cells": [
                {
                    "column_id": str(exp["output_column"].id),
                    "row_id": str(exp["snapshot_row"].id),
                }
            ],
            "failed_only": True,
        }
        with patch("tfc.temporal.experiments.start_rerun_cells_v2_workflow") as start:
            theirs = b["client"].post(
                f"{V2}/{exp['experiment'].id}/rerun-cells/", body, format="json"
            )
            assert theirs.status_code >= 400
            assert start.call_count == 0
        exp["output_cell"].refresh_from_db()
        assert exp["output_cell"].status == CellStatus.ERROR.value
        assert exp["output_cell"].value.encode() == exp["secrets"][1]

    def test_stop_of_another_orgs_experiment_changes_nothing(self, tenants):
        a, b = tenants["a"], tenants["b"]
        target = a["exp"]["experiment"]
        target.status = StatusType.RUNNING.value
        target.save(update_fields=["status"])
        with patch(
            "tfc.temporal.experiments.cancel_all_experiment_workflows"
        ) as cancel:
            theirs = b["client"].post(f"{V2}/{target.id}/stop/", {}, format="json")
        assert theirs.status_code >= 400
        assert cancel.call_count == 0
        target.refresh_from_db()
        assert target.status == StatusType.RUNNING.value


@pytest.mark.integration
@pytest.mark.api
class TestExperimentOnDatasetWithNoWorkspace:
    """An older experiment (no recorded runs, no native snapshot identity) on
    a dataset with no workspace opens from its organization's default
    workspace, and from no other workspace or organization."""

    @pytest.mark.parametrize("path,params", NO_WORKSPACE_READS)
    def test_default_workspace_member_reads_it(self, no_workspace, path, params):
        exp, clients = no_workspace
        url = path.format(v2=V2, id=exp["experiment"].id)
        response = clients["member"].get(url, params or {})
        assert response.status_code == 200, response.content[:300]

    @pytest.mark.parametrize("actor", ["sibling", "foreign"])
    @pytest.mark.parametrize("path,params", NO_WORKSPACE_READS)
    def test_other_workspaces_and_organizations_get_not_found(
        self, no_workspace, path, params, actor
    ):
        exp, clients = no_workspace
        url = path.format(v2=V2, id=exp["experiment"].id)
        response = clients[actor].get(url, params or {})
        assert response.status_code == 404, response.content[:300]
        assert _leaks(response, exp) == []
