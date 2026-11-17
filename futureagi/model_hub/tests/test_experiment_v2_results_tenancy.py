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

import csv
import io
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
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
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
    snapshot_input_column = Column.objects.create(
        name=input_column.name,
        dataset=snapshot,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
        source_id=str(input_column.id),
    )
    output_column = Column.objects.create(
        name=f"{tag}-output",
        dataset=snapshot,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EXPERIMENT.value,
        status=StatusType.COMPLETED.value,
    )
    snapshot.column_order = [str(snapshot_input_column.id), str(output_column.id)]
    snapshot.save(update_fields=["column_order"])
    snapshot_row = Row.objects.create(dataset=snapshot, order=0)
    Cell.objects.create(
        dataset=snapshot,
        column=snapshot_input_column,
        row=snapshot_row,
        value=secret_in,
        status=CellStatus.PASS.value,
    )
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
        "snapshot_input_column": snapshot_input_column,
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
        assert a["exp"]["secrets"][1] in own_body
        theirs = b["client"].get(url)
        assert _leaks(theirs, a["exp"]) == [], (
            f"org B read org A's experiment CSV (status {theirs.status_code})"
        )
        assert theirs.status_code >= 400

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")
    @pytest.mark.parametrize("prefix", [V2, "/model-hub/experiments"])
    def test_csv_download_includes_api_created_variants(self, tenants, prefix):
        actor = tenants["a"]
        dataset = actor["exp"]["dataset"]
        input_column = Column.objects.get(dataset=dataset)
        input_column.name = "question"
        input_column.save(update_fields=["name"])
        Cell.objects.filter(dataset=dataset).update(value="Captured first input")
        second_row = Row.objects.create(dataset=dataset, order=1)
        Cell.objects.create(
            dataset=dataset,
            row=second_row,
            column=input_column,
            value="Captured second input",
        )
        prompt = PromptTemplate.no_workspace_objects.create(
            name="CSV prompt",
            organization=actor["org"],
            workspace=actor["workspace"],
            created_by=actor["user"],
        )
        version = PromptVersion.no_workspace_objects.create(
            original_template=prompt,
            template_version="v1",
            is_default=True,
            is_draft=False,
            prompt_config_snapshot={
                "messages": [{"role": "user", "content": "Export regression"}],
                "configuration": {"temperature": 0, "max_tokens": 64},
            },
        )
        template = EvalTemplate.no_workspace_objects.create(
            name="csv-evaluation",
            organization=actor["org"],
            workspace=actor["workspace"],
            owner=OwnerChoices.USER.value,
            config={
                "required_keys": ["output"],
                "output": "Pass/Fail",
                "eval_type_id": "CustomCodeEval",
            },
            criteria="Is {{output}} correct?",
            model="turing_small",
            eval_type="code",
        )
        EvalTemplateVersion.objects.create_version(
            eval_template=template,
            config_snapshot=template.config,
            criteria=template.criteria,
            model=template.model,
            user=actor["user"],
            organization=actor["org"],
            workspace=actor["workspace"],
        )
        payload = {
            "name": "CSV API experiment",
            "dataset_id": str(dataset.id),
            "experiment_type": "llm",
            "user_eval_metrics": [
                {
                    "template_id": str(template.id),
                    "name": "CSV evaluation",
                    "config": {"mapping": {"output": str(input_column.id)}},
                    "model": "turing_small",
                }
            ],
            "prompt_config": [
                {
                    "name": "Candidate",
                    "prompt_id": str(prompt.id),
                    "prompt_version": str(version.id),
                    "model": "gpt-4o-mini",
                }
            ],
        }
        with patch(
            "tfc.temporal.experiments.start_experiment_v2_workflow",
            return_value="csv-export-workflow",
        ) as start:
            created = actor["client"].post(f"{V2}/", payload, format="json")
        assert created.status_code == 200, created.content
        assert start.call_count == 1
        experiment = ExperimentsTable.no_workspace_objects.get(
            dataset=dataset, name=payload["name"]
        )
        # The real create path must not need an artificial legacy M2M link.
        assert experiment.experiments_datasets.count() == 0
        assert experiment.experiment_datasets.count() == 1
        variant = experiment.experiment_datasets.get()
        snapshot = experiment.snapshot_dataset
        output = variant.columns.get(source=SourceChoices.EXPERIMENT.value)
        score = variant.columns.get(source=SourceChoices.EXPERIMENT_EVALUATION.value)
        reason = variant.columns.get(source=SourceChoices.EVALUATION_REASON.value)
        rows = list(Row.objects.filter(dataset=snapshot).order_by("order", "id"))
        assert len(rows) == 2
        for row, values in zip(
            rows,
            [
                ("First output", "1", "First explanation"),
                ("Second output", "0", "Second explanation"),
            ],
            strict=True,
        ):
            for column, value in zip((output, score, reason), values, strict=True):
                Cell.objects.update_or_create(
                    dataset=snapshot,
                    row=row,
                    column=column,
                    defaults={"value": value, "status": CellStatus.PASS.value},
                )
        retired = ExperimentDatasetTable.objects.create(
            experiment=experiment,
            name="Retired candidate",
            deleted=True,
        )
        retired_output = Column.objects.create(
            dataset=snapshot,
            name="Retired output",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EXPERIMENT.value,
        )
        retired.columns.add(retired_output)
        Cell.objects.create(
            dataset=snapshot,
            row=rows[0],
            column=retired_output,
            value="Retired result",
        )
        Cell.objects.filter(dataset=dataset).update(value="Live input edited")
        Row.objects.create(dataset=dataset, order=2)

        url = f"{prefix}/{experiment.id}/download/"
        response = actor["client"].get(url)

        assert response.status_code == 200
        assert list(csv.reader(io.StringIO(_body(response).decode("utf-8")))) == [
            [
                "question",
                "Candidate-gpt-4o-mini",
                "CSV evaluation-Candidate-gpt-4o-mini",
                "CSV evaluation-Candidate-gpt-4o-mini-reason",
            ],
            ["Captured first input", "First output", "1", "First explanation"],
            ["Captured second input", "Second output", "0", "Second explanation"],
        ]
        sibling_workspace = Workspace.no_workspace_objects.create(
            name="CSV sibling workspace",
            organization=actor["org"],
            created_by=actor["user"],
            is_default=False,
            is_active=True,
        )
        sibling = _workspace_member(actor["org"], sibling_workspace, "csv-sibling")
        try:
            for client in (tenants["b"]["client"], sibling):
                refused = client.get(url)
                assert refused.status_code == 404
                assert refused.json()["detail"] == "Experiment not found"
        finally:
            sibling.stop_workspace_injection()
            clear_workspace_context()

    @pytest.mark.parametrize("source_row_deleted", [False, True])
    def test_csv_download_preserves_snapshot_rows_and_values(
        self, tenants, source_row_deleted
    ):
        actor = tenants["a"]
        exp = actor["exp"]
        snapshot = exp["experiment"].snapshot_dataset
        template = EvalTemplate.objects.create(
            name="CSV evaluation",
            organization=actor["org"],
            workspace=actor["workspace"],
        )
        metric = UserEvalMetric.objects.create(
            name="CSV evaluation",
            template=template,
            dataset=snapshot,
            organization=actor["org"],
            workspace=actor["workspace"],
            user=actor["user"],
            source_id=str(exp["experiment"].id),
        )
        exp["experiment"].user_eval_template_ids.add(metric)
        score = Column.objects.create(
            dataset=snapshot,
            name="score",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION.value,
            source_id=str(metric.id),
        )
        reason = Column.objects.create(
            dataset=snapshot,
            name="reason",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION_REASON.value,
            source_id=f"{score.id}-sourceid-{metric.id}",
        )
        variant_score = Column.objects.create(
            dataset=snapshot,
            name="variant score",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EXPERIMENT_EVALUATION.value,
        )
        exp["experiment"].experiments_datasets.get().columns.add(variant_score)
        for column, value in (
            (score, "0.75"),
            (reason, "Captured evaluation"),
            (variant_score, "1"),
        ):
            Cell.objects.create(
                dataset=snapshot,
                row=exp["snapshot_row"],
                column=column,
                value=value,
            )
        # A snapshot row with only an output must still be exported.
        sparse_row = Row.objects.create(dataset=snapshot, order=1)
        Cell.objects.create(
            dataset=snapshot,
            row=sparse_row,
            column=exp["output_column"],
            value="Output without an input cell",
        )
        Cell.objects.filter(dataset=exp["dataset"]).update(value="Live input edited")
        Row.objects.filter(pk=exp["row"].pk).update(deleted=source_row_deleted)
        live_row = Row.objects.create(dataset=exp["dataset"], order=2)
        Cell.objects.create(
            dataset=exp["dataset"],
            row=live_row,
            column=Column.objects.get(dataset=exp["dataset"]),
            value="Added after the snapshot",
        )

        response = actor["client"].get(f"{V2}/{exp['experiment'].id}/download/")

        assert response.status_code == 200
        records = list(csv.DictReader(io.StringIO(_body(response).decode("utf-8"))))
        assert records == [
            {
                exp["snapshot_input_column"].name: exp["secrets"][0].decode(),
                exp["output_column"].name: exp["secrets"][1].decode(),
                "score": "0.75",
                "reason": "Captured evaluation",
                "variant score": "1",
            },
            {
                exp["snapshot_input_column"].name: "",
                exp["output_column"].name: "Output without an input cell",
                "score": "",
                "reason": "",
                "variant score": "",
            },
        ]

    def test_csv_download_with_only_snapshot_outputs(self, tenants):
        actor = tenants["a"]
        exp = actor["exp"]
        Column.objects.filter(pk=exp["snapshot_input_column"].pk).update(deleted=True)

        response = actor["client"].get(f"{V2}/{exp['experiment'].id}/download/")

        assert response.status_code == 200
        records = list(csv.DictReader(io.StringIO(_body(response).decode("utf-8"))))
        assert records == [
            {
                exp["output_column"].name: exp["secrets"][1].decode(),
            }
        ]

    @pytest.mark.parametrize("prefix", [V2, "/model-hub/experiments"])
    def test_csv_download_without_snapshot_uses_source_rows(self, tenants, prefix):
        actor = tenants["a"]
        exp = actor["exp"]
        experiment = exp["experiment"]
        experiment.snapshot_dataset = None
        experiment.save(update_fields=["snapshot_dataset"])
        output = Column.objects.create(
            dataset=exp["dataset"],
            name="Legacy output",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EXPERIMENT.value,
        )
        experiment.experiments_datasets.get().columns.set([output])
        ExperimentDatasetTable.no_workspace_objects.filter(
            experiment=experiment
        ).update(experiment=None)
        assert experiment.experiment_datasets.count() == 0
        Cell.objects.create(
            dataset=exp["dataset"],
            row=exp["row"],
            column=output,
            value="Legacy result",
        )

        response = actor["client"].get(f"{prefix}/{experiment.id}/download/")

        assert response.status_code == 200
        records = list(csv.DictReader(io.StringIO(_body(response).decode("utf-8"))))
        assert records == [
            {
                exp["snapshot_input_column"].name: exp["secrets"][0].decode(),
                "Legacy output": "Legacy result",
            }
        ]


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
