import contextlib
import uuid
from pathlib import Path
from unittest import mock

import pytest
from rest_framework import status

from model_hub.models.choices import (
    CellStatus,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.views.develop_dataset import CompareDatasetsView
from tfc.utils.storage import get_compare_local_dir


@pytest.fixture
def compare_datasets(db, organization, workspace):
    base_dataset = Dataset.objects.create(
        name="Base Compare Dataset",
        organization=organization,
        workspace=workspace,
    )
    other_dataset = Dataset.objects.create(
        name="Other Compare Dataset",
        organization=organization,
        workspace=workspace,
    )

    base_input = Column.objects.create(
        name="input",
        dataset=base_dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    other_input = Column.objects.create(
        name="input",
        dataset=other_dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    base_eval = Column.objects.create(
        name="judge",
        dataset=base_dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION.value,
        source_id=None,
    )
    other_eval = Column.objects.create(
        name="judge",
        dataset=other_dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION.value,
        source_id=None,
    )

    base_row = Row.objects.create(dataset=base_dataset, order=0)
    other_row = Row.objects.create(dataset=other_dataset, order=0)
    Cell.objects.create(
        dataset=base_dataset,
        column=base_input,
        row=base_row,
        value="alpha",
    )
    Cell.objects.create(
        dataset=other_dataset,
        column=other_input,
        row=other_row,
        value="alpha",
    )
    Cell.objects.create(
        dataset=base_dataset,
        column=base_eval,
        row=base_row,
        value="pass",
    )
    Cell.objects.create(
        dataset=other_dataset,
        column=other_eval,
        row=other_row,
        value="fail",
    )

    return {
        "base_dataset": base_dataset,
        "other_dataset": other_dataset,
        "base_input": base_input,
        "other_input": other_input,
        "base_eval": base_eval,
        "other_eval": other_eval,
        "base_row": base_row,
        "other_row": other_row,
    }


@pytest.fixture
def compare_eval_metrics(compare_datasets, organization, workspace, user):
    template = EvalTemplate.objects.create(
        name="Compare score template",
        organization=organization,
        workspace=workspace,
        config={"output": "score"},
    )
    metrics = []
    for key in ("base_dataset", "other_dataset"):
        dataset = compare_datasets[key]
        dataset.column_order = list(
            Column.objects.filter(dataset=dataset).values_list("id", flat=True)
        )
        dataset.column_order = [str(column_id) for column_id in dataset.column_order]
        dataset.save(update_fields=["column_order"])
        metrics.append(
            UserEvalMetric.objects.create(
                name="Compare score",
                organization=organization,
                workspace=workspace,
                user=user,
                dataset=dataset,
                template=template,
                status=StatusType.INACTIVE.value,
            )
        )
    return metrics


@pytest.mark.django_db
def test_compare_start_evals_creates_columns(auth_client, compare_eval_metrics):
    base_metric, other_metric = compare_eval_metrics
    original_orders = {
        metric.dataset_id: list(metric.dataset.column_order)
        for metric in compare_eval_metrics
    }

    response = auth_client.post(
        f"/model-hub/datasets/{base_metric.dataset_id}/compare-datasets/start-eval/",
        {
            "user_eval_names": [base_metric.name],
            "dataset_ids": [str(other_metric.dataset_id)],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.data
    for metric in compare_eval_metrics:
        metric.refresh_from_db()
        metric.dataset.refresh_from_db()
        assert metric.status == StatusType.NOT_STARTED.value
        column = Column.objects.get(dataset=metric.dataset, source_id=str(metric.id))
        reason = Column.objects.get(
            dataset=metric.dataset,
            source_id=f"{column.id}-sourceid-{metric.id}",
        )
        assert column.name == metric.name
        assert column.source == SourceChoices.EVALUATION.value
        assert column.data_type == DataTypeChoices.FLOAT.value
        assert reason.name == f"{metric.name}-reason"
        assert reason.source == SourceChoices.EVALUATION_REASON.value
        assert reason.data_type == DataTypeChoices.TEXT.value
        assert metric.dataset.column_order == [
            *original_orders[metric.dataset_id],
            str(column.id),
            str(reason.id),
        ]


@pytest.mark.django_db
def test_compare_start_evals_reuses_existing_columns(auth_client, compare_eval_metrics):
    base_metric, other_metric = compare_eval_metrics
    original_orders = {}
    reason_cells = []
    for metric in compare_eval_metrics:
        column = Column.objects.create(
            name=metric.name,
            dataset=metric.dataset,
            source_id=str(metric.id),
            source=SourceChoices.EVALUATION.value,
            data_type=DataTypeChoices.FLOAT.value,
        )
        reason = Column.objects.create(
            name=f"{metric.name}-reason",
            dataset=metric.dataset,
            source_id=f"{column.id}-sourceid-{metric.id}",
            source=SourceChoices.EVALUATION_REASON.value,
            data_type=DataTypeChoices.TEXT.value,
        )
        metric.dataset.column_order.extend([str(column.id), str(reason.id)])
        metric.dataset.save(update_fields=["column_order"])
        original_orders[metric.dataset_id] = list(metric.dataset.column_order)
        reason_cells.append(
            Cell.objects.create(
                dataset=metric.dataset,
                column=reason,
                row=Row.objects.get(dataset=metric.dataset),
                value="Previous reason",
                status=CellStatus.PASS.value,
            )
        )

    response = auth_client.post(
        f"/model-hub/datasets/{base_metric.dataset_id}/compare-datasets/start-eval/",
        {
            "user_eval_names": [base_metric.name],
            "dataset_ids": [str(other_metric.dataset_id)],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.data
    for metric in compare_eval_metrics:
        metric.refresh_from_db()
        metric.dataset.refresh_from_db()
        assert metric.status == StatusType.NOT_STARTED.value
        assert metric.dataset.column_order == original_orders[metric.dataset_id]
        assert Column.objects.filter(dataset=metric.dataset).count() == 4
    for cell in reason_cells:
        cell.refresh_from_db()
        assert cell.status == CellStatus.RUNNING.value
        assert cell.value == "Previous reason"


@pytest.mark.django_db
def test_compare_delete_route_get_is_method_guarded(auth_client):
    response = auth_client.get(f"/model-hub/datasets/delete-compare/{uuid.uuid4()}/")

    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert response.json()["detail"] == "Use DELETE to remove compare dataset files."


@pytest.mark.django_db
def test_compare_row_route_delete_is_method_guarded(auth_client):
    response = auth_client.delete(
        f"/model-hub/datasets/get-compare-row/{uuid.uuid4()}/{uuid.uuid4()}/"
    )

    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert (
        response.json()["detail"]
        == "Use DELETE /datasets/delete-compare/{compare_id}/ to remove compare dataset files."
    )


@pytest.mark.django_db
def test_compare_delete_falls_back_to_synchronous_cleanup(
    auth_client, monkeypatch, tmp_path
):
    compare_id = uuid.uuid4()
    monkeypatch.chdir(tmp_path)
    compare_dir = Path(get_compare_local_dir(compare_id))
    compare_dir.mkdir(parents=True)
    (compare_dir / "metadata.json").write_text("{}", encoding="utf-8")

    def raise_temporal_unavailable(*args, **kwargs):
        raise RuntimeError("Temporal unavailable")

    monkeypatch.setattr(
        "tfc.temporal.drop_in.start_activity",
        raise_temporal_unavailable,
    )
    monkeypatch.setattr(
        "model_hub.views.develop_dataset.delete_compare_folder",
        lambda compare_id: None,
    )

    response = auth_client.delete(f"/model-hub/datasets/delete-compare/{compare_id}/")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["result"] == {"message": "File(s) deleted successfully"}
    assert compare_dir.resolve().parent == tmp_path / "media" / "compare"
    assert not compare_dir.exists()


@pytest.mark.django_db
def test_compare_pagination_tolerates_eval_columns_without_source_id(
    compare_datasets, monkeypatch
):
    base_dataset = compare_datasets["base_dataset"]
    other_dataset = compare_datasets["other_dataset"]
    base_input = compare_datasets["base_input"]
    base_eval = compare_datasets["base_eval"]
    other_eval = compare_datasets["other_eval"]
    base_row = compare_datasets["base_row"]
    other_row = compare_datasets["other_row"]

    def fake_download_json_from_s3(object_key):
        return {
            "column_config": [
                {
                    "id": str(base_input.id),
                    "name": "input",
                    "data_type": DataTypeChoices.TEXT.value,
                }
            ],
            "table": [
                {
                    "row_id": str(base_row.id),
                    str(base_input.id): {
                        "cell_value": "alpha",
                        "cell_row_id": str(base_row.id),
                    },
                }
            ],
        }

    monkeypatch.setattr(
        "model_hub.views.develop_dataset.download_json_from_s3",
        fake_download_json_from_s3,
    )

    view = CompareDatasetsView()
    view.process_base_values = lambda *args, **kwargs: (True, {})
    columns_qs = Column.objects.filter(dataset__in=[base_dataset, other_dataset])
    dynamic_sources = [
        SourceChoices.RUN_PROMPT.value,
        SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
        SourceChoices.EVALUATION_TAGS.value,
        SourceChoices.OPTIMISATION_EVALUATION_TAGS.value,
        SourceChoices.EVALUATION.value,
        SourceChoices.EXPERIMENT_EVALUATION.value,
        SourceChoices.OPTIMISATION_EVALUATION.value,
        SourceChoices.EVALUATION_REASON.value,
    ]

    result = view.get_paginated_compare_json(
        compare_id=uuid.uuid4(),
        start=0,
        end=1,
        start_page=1,
        end_page=2,
        common_columns={"judge"},
        comparison_datasets=[base_dataset, other_dataset],
        columns_lookup={
            (base_dataset.id, "judge"): base_eval,
            (other_dataset.id, "judge"): other_eval,
        },
        dataset_id=base_dataset.id,
        columns_qs=columns_qs,
        common_base_values=["alpha"],
        dataset_info={
            "alpha": {
                str(base_dataset.id): str(base_row.id),
                str(other_dataset.id): str(other_row.id),
            }
        },
        base_column_name="input",
        dynamic_sources=dynamic_sources,
        result={},
    )

    eval_configs = [
        column
        for column in result["column_config"]
        if column.get("origin_type") == SourceChoices.EVALUATION.value
    ]
    assert len(eval_configs) == 2
    assert {column["source_id"] for column in eval_configs} == {None}
    assert result["table"][0][str(base_eval.id)]["cell_value"] == "pass"
    assert result["table"][0][str(other_eval.id)]["cell_value"] == "fail"


@pytest.fixture
def comparison_page_store(monkeypatch, tmp_path):
    import json

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COMPARE_DATASET_LOCAL_ROOT", str(tmp_path / "compare"))
    pages = {}

    def upload(*, compare_id, compare_json, page_name):
        pages[f"compare/{compare_id}/{page_name}"] = json.loads(
            json.dumps(compare_json)
        )

    def download(object_key):
        return pages[object_key]

    monkeypatch.setattr(
        "model_hub.views.develop_dataset.upload_compare_json_to_s3", upload
    )
    monkeypatch.setattr(
        "model_hub.views.develop_dataset.download_json_from_s3", download
    )
    return pages


@pytest.fixture
def comparison_cell_reads(monkeypatch):
    from model_hub.views import develop_dataset

    calls = []
    original = CompareDatasetsView.process_base_values
    original_prepare = develop_dataset._prepare_compare_dataset_impl

    def read_base(self, *args, **kwargs):
        calls.append("base")
        return original(self, *args, **kwargs)

    def prepare(*args, **kwargs):
        calls.append("prepare")
        return original_prepare(*args, **kwargs)

    monkeypatch.setattr(CompareDatasetsView, "process_base_values", read_base)
    monkeypatch.setattr(develop_dataset, "_prepare_compare_dataset_impl", prepare)
    return calls


@pytest.fixture
def other_org_dataset(db):
    from accounts.models.organization import Organization

    org = Organization.objects.create(name="Second organization")
    dataset = Dataset.objects.create(name="Second dataset", organization=org)
    input_column = Column.objects.create(
        name="input",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    output_column = Column.objects.create(
        name="judge",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(dataset=dataset, column=input_column, row=row, value="alpha")
    Cell.objects.create(
        dataset=dataset, column=output_column, row=row, value="other-org-row"
    )
    return dataset


def _compare_request(
    client, base, compared, *, download=False, compare_id=None, base_column_name="input"
):
    payload = {
        "dataset_ids": [str(compared.id)],
        "base_column_name": base_column_name,
        "page_size": 10,
        "current_page_index": 0,
    }
    if compare_id is not None:
        payload["compare_id"] = compare_id
    if download:
        payload["dataset_info"] = {
            "alpha": {
                str(base.id): str(Row.objects.get(dataset=base).id),
                str(compared.id): str(Row.objects.get(dataset=compared).id),
            }
        }
        payload["common_column_names"] = ["judge"]
    endpoint = "compare-datasets/download/" if download else "compare-datasets/"
    response = client.post(
        f"/model-hub/datasets/{base.id}/{endpoint}", payload, format="json"
    )
    if response.streaming:
        response._proof_body = b"".join(response.streaming_content)
    return response


def _assert_no_comparison_cells(response, cell_reads, captured):
    body = response._proof_body if response.streaming else response.content
    assert response.status_code == status.HTTP_404_NOT_FOUND, body
    assert not cell_reads
    assert not any(
        '"model_hub_cell"' in query["sql"] for query in captured.captured_queries
    )


@pytest.mark.django_db(transaction=True)
def test_compare_without_organization_returns_not_found_before_cells(
    compare_datasets, comparison_page_store, comparison_cell_reads
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from accounts.models.user import User
    from conftest import WorkspaceAwareAPIClient

    user = User.objects.create_user(
        email=f"compare-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Comparison user",
        organization=None,
    )
    User.objects.filter(pk=user.pk).update(organization=None)
    user.refresh_from_db()
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    base = compare_datasets["base_dataset"]
    compared = compare_datasets["other_dataset"]
    with CaptureQueriesContext(connection) as captured:
        response = _compare_request(client, base, compared)
    _assert_no_comparison_cells(response, comparison_cell_reads, captured)


@pytest.mark.django_db(transaction=True)
def test_compare_with_organization_and_no_workspace_checks_compared_dataset(
    user,
    compare_datasets,
    other_org_dataset,
    comparison_page_store,
    comparison_cell_reads,
    monkeypatch,
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from rest_framework.views import APIView

    from conftest import WorkspaceAwareAPIClient

    initial = APIView.initial

    def bound_initial(view, request, *args, **kwargs):
        request.organization = compare_datasets["base_dataset"].organization
        request.workspace = None
        return initial(view, request, *args, **kwargs)

    monkeypatch.setattr(APIView, "initial", bound_initial)
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    with CaptureQueriesContext(connection) as captured:
        response = _compare_request(
            client, compare_datasets["base_dataset"], other_org_dataset
        )
    _assert_no_comparison_cells(response, comparison_cell_reads, captured)


@pytest.mark.django_db(transaction=True)
def test_compare_checks_base_dataset_with_organization_and_no_workspace(
    user,
    compare_datasets,
    other_org_dataset,
    comparison_page_store,
    comparison_cell_reads,
    monkeypatch,
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from rest_framework.views import APIView

    from conftest import WorkspaceAwareAPIClient

    initial = APIView.initial

    def bound_initial(view, request, *args, **kwargs):
        request.organization = user.organization
        request.workspace = None
        return initial(view, request, *args, **kwargs)

    monkeypatch.setattr(APIView, "initial", bound_initial)
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    with CaptureQueriesContext(connection) as captured:
        response = _compare_request(
            client, other_org_dataset, compare_datasets["other_dataset"]
        )
    _assert_no_comparison_cells(response, comparison_cell_reads, captured)


@pytest.mark.django_db(transaction=True)
def test_compare_removed_member_returns_not_found_before_cells(
    organization, compare_datasets, comparison_page_store, comparison_cell_reads
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from conftest import WorkspaceAwareAPIClient

    member = User.objects.create_user(
        email=f"compare-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Previous member",
        organization=organization,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=member, organization=organization, is_active=False
    )
    assert member.organization_id == organization.id
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=member)
    with CaptureQueriesContext(connection) as captured:
        response = _compare_request(
            client, compare_datasets["base_dataset"], compare_datasets["other_dataset"]
        )
    _assert_no_comparison_cells(response, comparison_cell_reads, captured)


@pytest.mark.django_db(transaction=True)
def test_compare_own_datasets_returns_rows(
    auth_client, compare_datasets, comparison_page_store
):
    response = _compare_request(
        auth_client, compare_datasets["base_dataset"], compare_datasets["other_dataset"]
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    assert response.data["result"]["table"]
    assert '"cell_value":"fail"' in response.content.decode()


@pytest.mark.django_db(transaction=True)
def test_compare_cached_page_checks_dataset_scope(
    auth_client,
    user,
    compare_datasets,
    other_org_dataset,
    comparison_page_store,
    comparison_cell_reads,
    monkeypatch,
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from rest_framework.views import APIView

    from conftest import WorkspaceAwareAPIClient

    base = compare_datasets["base_dataset"]
    own = compare_datasets["other_dataset"]
    built = _compare_request(auth_client, base, own)
    assert built.status_code == status.HTTP_200_OK, built.content
    compare_id = built.data["result"]["metadata"]["compare_id"]
    comparison_cell_reads.clear()
    initial = APIView.initial

    def bound_initial(view, request, *args, **kwargs):
        request.organization = other_org_dataset.organization
        request.workspace = None
        return initial(view, request, *args, **kwargs)

    monkeypatch.setattr(APIView, "initial", bound_initial)
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    with CaptureQueriesContext(connection) as captured:
        response = _compare_request(client, base, own, compare_id=compare_id)
    _assert_no_comparison_cells(response, comparison_cell_reads, captured)


@pytest.mark.django_db(transaction=True)
def test_compare_download_checks_compared_dataset_before_cells(
    user, compare_datasets, other_org_dataset, comparison_page_store, monkeypatch
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from rest_framework.views import APIView

    from conftest import WorkspaceAwareAPIClient

    initial = APIView.initial

    def bound_initial(view, request, *args, **kwargs):
        request.organization = user.organization
        request.workspace = None
        return initial(view, request, *args, **kwargs)

    monkeypatch.setattr(APIView, "initial", bound_initial)
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    with CaptureQueriesContext(connection) as captured:
        response = _compare_request(
            client, compare_datasets["base_dataset"], other_org_dataset, download=True
        )
    _assert_no_comparison_cells(response, [], captured)


@pytest.mark.django_db(transaction=True)
def test_compare_download_checks_base_dataset_before_cells(
    user, compare_datasets, other_org_dataset, comparison_page_store, monkeypatch
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from rest_framework.views import APIView

    from conftest import WorkspaceAwareAPIClient

    initial = APIView.initial

    def bound_initial(view, request, *args, **kwargs):
        request.organization = user.organization
        request.workspace = None
        return initial(view, request, *args, **kwargs)

    monkeypatch.setattr(APIView, "initial", bound_initial)
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    with CaptureQueriesContext(connection) as captured:
        response = _compare_request(
            client, other_org_dataset, compare_datasets["other_dataset"], download=True
        )
    _assert_no_comparison_cells(response, [], captured)


@pytest.mark.django_db(transaction=True)
def test_compare_download_without_organization_returns_not_found_before_cells(
    compare_datasets,
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from accounts.models.user import User
    from conftest import WorkspaceAwareAPIClient

    member = User.objects.create_user(
        email=f"compare-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Comparison user",
        organization=None,
    )
    User.objects.filter(pk=member.pk).update(organization=None)
    member.refresh_from_db()
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=member)
    with CaptureQueriesContext(connection) as captured:
        response = _compare_request(
            client,
            compare_datasets["base_dataset"],
            compare_datasets["other_dataset"],
            download=True,
        )
    _assert_no_comparison_cells(response, [], captured)


@pytest.mark.django_db(transaction=True)
def test_compare_download_own_datasets_returns_rows(auth_client, compare_datasets):
    response = _compare_request(
        auth_client,
        compare_datasets["base_dataset"],
        compare_datasets["other_dataset"],
        download=True,
    )
    assert response.status_code == status.HTTP_200_OK
    assert b"fail" in response._proof_body


@pytest.mark.django_db(transaction=True)
def test_compare_download_removed_member_returns_not_found_before_cells(
    organization, compare_datasets
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from conftest import WorkspaceAwareAPIClient

    member = User.objects.create_user(
        email=f"compare-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Previous member",
        organization=organization,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=member, organization=organization, is_active=False
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=member)
    with CaptureQueriesContext(connection) as captured:
        response = _compare_request(
            client,
            compare_datasets["base_dataset"],
            compare_datasets["other_dataset"],
            download=True,
        )
    _assert_no_comparison_cells(response, [], captured)


@pytest.mark.django_db(transaction=True)
def test_compare_download_uses_rows_from_resolved_datasets(
    auth_client, compare_datasets, other_org_dataset
):
    base = compare_datasets["base_dataset"]
    compared = compare_datasets["other_dataset"]
    second_row = Row.objects.get(dataset=other_org_dataset)
    response = auth_client.post(
        f"/model-hub/datasets/{base.id}/compare-datasets/download/",
        {
            "dataset_ids": [str(compared.id)],
            "base_column_name": "input",
            "dataset_info": {
                "alpha": {
                    str(base.id): str(compare_datasets["base_row"].id),
                    str(compared.id): str(second_row.id),
                }
            },
            "common_column_names": ["judge"],
        },
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    csv = b"".join(response.streaming_content).decode()
    assert "pass" in csv
    assert "other-org-row" not in csv
    assert "judge-Other Compare Dataset" not in csv


@pytest.mark.django_db(transaction=True)
def test_compare_download_ignores_rows_from_another_dataset_in_organization(
    auth_client, compare_datasets
):
    base = compare_datasets["base_dataset"]
    compared = compare_datasets["other_dataset"]
    another = Dataset.objects.create(
        name="Additional Compare Dataset",
        organization=base.organization,
        workspace=base.workspace,
    )
    column = Column.objects.create(
        name="judge",
        dataset=another,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION.value,
    )
    row = Row.objects.create(dataset=another, order=0)
    Cell.objects.create(dataset=another, column=column, row=row, value="extra-row")
    response = auth_client.post(
        f"/model-hub/datasets/{base.id}/compare-datasets/download/",
        {
            "dataset_ids": [str(compared.id)],
            "base_column_name": "input",
            "dataset_info": {
                "alpha": {
                    str(base.id): str(compare_datasets["base_row"].id),
                    str(compared.id): str(row.id),
                }
            },
            "common_column_names": ["judge"],
        },
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    csv = b"".join(response.streaming_content).decode()
    assert "pass" in csv
    assert "extra-row" not in csv
    assert "judge-Other Compare Dataset" not in csv


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("download", [False, True])
@pytest.mark.parametrize("repeat_base", [False, True])
def test_compare_repeated_dataset_ids_return_bad_request(
    auth_client, compare_datasets, download, repeat_base
):
    base = compare_datasets["base_dataset"]
    compared = compare_datasets["other_dataset"]
    dataset_ids = (
        [str(base.id)] if repeat_base else [str(compared.id), str(compared.id)]
    )
    endpoint = "compare-datasets/download/" if download else "compare-datasets/"
    payload: dict[str, object] = {
        "dataset_ids": dataset_ids,
        "base_column_name": "input",
        "page_size": 10,
        "current_page_index": 0,
    }
    if download:
        payload["dataset_info"] = {
            "alpha": {
                str(base.id): str(compare_datasets["base_row"].id),
                str(compared.id): str(compare_datasets["other_row"].id),
            }
        }
        payload["common_column_names"] = ["judge"]
    response = auth_client.post(
        f"/model-hub/datasets/{base.id}/{endpoint}", payload, format="json"
    )
    if download:
        assert response.status_code == status.HTTP_200_OK, response.content
        csv = b"".join(response.streaming_content).decode()
        assert "judge-Base Compare Dataset" in csv
        assert csv.count("judge-Other Compare Dataset") == (0 if repeat_base else 1)
        assert csv.count("pass") == 1
        assert csv.count("fail") == (0 if repeat_base else 1)
    else:
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "Invalid dataset IDs provided." in response.content.decode()


@pytest.mark.django_db(transaction=True)
def test_compare_download_resolves_repeated_dataset_once(auth_client, compare_datasets):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    base = compare_datasets["base_dataset"]
    compared = compare_datasets["other_dataset"]
    with CaptureQueriesContext(connection) as queries:
        response = auth_client.post(
            f"/model-hub/datasets/{base.id}/compare-datasets/download/",
            {
                "dataset_ids": [str(compared.id), str(compared.id)],
                "base_column_name": "input",
                "dataset_info": {
                    "alpha": {
                        str(base.id): str(compare_datasets["base_row"].id),
                        str(compared.id): str(compare_datasets["other_row"].id),
                    }
                },
                "common_column_names": ["judge"],
            },
            format="json",
        )
    assert response.status_code == status.HTTP_200_OK, response.content
    compared_lookups = [
        query
        for query in queries
        if f'FROM "{Dataset._meta.db_table}"' in query["sql"]
        and compared.id.hex in query["sql"]
    ]
    assert len(compared_lookups) == 1


@pytest.mark.django_db(transaction=True)
def test_compare_download_base_in_selected_ids_returns_rows(
    auth_client, compare_datasets
):
    base = compare_datasets["base_dataset"]
    compared = compare_datasets["other_dataset"]
    response = auth_client.post(
        f"/model-hub/datasets/{base.id}/compare-datasets/download/",
        {
            "dataset_ids": [str(base.id), str(compared.id)],
            "base_column_name": "input",
            "dataset_info": {
                "alpha": {
                    str(base.id): str(compare_datasets["base_row"].id),
                    str(compared.id): str(compare_datasets["other_row"].id),
                }
            },
            "common_column_names": ["judge"],
        },
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    csv = b"".join(response.streaming_content).decode()
    assert csv.count("judge-Base Compare Dataset") == 1
    assert csv.count("judge-Other Compare Dataset") == 1
    assert "pass" in csv and "fail" in csv


@pytest.mark.django_db(transaction=True)
def test_compare_cached_page_repeated_ids_return_bad_request(
    auth_client, compare_datasets, comparison_page_store
):
    base = compare_datasets["base_dataset"]
    compared = compare_datasets["other_dataset"]
    built = _compare_request(auth_client, base, compared)
    assert built.status_code == status.HTTP_200_OK, built.content
    compare_id = built.data["result"]["metadata"]["compare_id"]
    response = auth_client.post(
        f"/model-hub/datasets/{base.id}/compare-datasets/",
        {
            "dataset_ids": [str(compared.id), str(compared.id)],
            "base_column_name": "input",
            "page_size": 10,
            "current_page_index": 0,
            "compare_id": compare_id,
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert "Invalid dataset IDs provided." in response.content.decode()


def _compare_dataset(organization, workspace, name, rows):
    """Create a dataset with input and judge columns and one row for each
    (input, judge) value pair in ``rows``."""
    dataset = Dataset.objects.create(
        name=name, organization=organization, workspace=workspace
    )
    columns = [
        Column.objects.create(
            name=column_name,
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        for column_name in ("input", "judge")
    ]
    for order, values in enumerate(rows):
        row = Row.objects.create(dataset=dataset, order=order)
        for column, value in zip(columns, values, strict=True):
            Cell.objects.create(dataset=dataset, column=column, row=row, value=value)
    return dataset


def _new_compare_pair(organization, workspace, label, base_value="alpha"):
    return [
        _compare_dataset(
            organization,
            workspace,
            f"{label} {index}",
            [(base_value, f"{label}-{index}")],
        )
        for index in range(2)
    ]


def _another_organization():
    """Create another organization with an owner and a default workspace."""
    from accounts.models.organization import Organization
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from accounts.models.workspace import Workspace, WorkspaceMembership
    from tfc.constants.levels import Level

    other_organization = Organization.objects.create(name="Additional organization")
    other_owner = User.objects.create_user(
        email=f"compare-owner-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Comparison owner",
        organization=other_organization,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=other_owner,
        organization=other_organization,
        level=Level.OWNER,
        is_active=True,
    )
    other_workspace = Workspace.objects.create(
        name="Additional workspace",
        organization=other_organization,
        is_default=True,
        is_active=True,
        created_by=other_owner,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=other_owner,
        workspace=other_workspace,
        level=Level.OWNER,
        is_active=True,
        organization_membership=membership,
    )
    return other_organization, other_owner, other_workspace


def _client_without_workspace(user):
    from conftest import WorkspaceAwareAPIClient

    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    return client


@contextlib.contextmanager
def _request_binding(organization, workspace=None):
    """Bind the organization and workspace of requests from clients that send
    no workspace header. Requests from a workspace-bound client inside the
    block would replace this binding, so the block must not make any."""
    from rest_framework.views import APIView

    initial = APIView.initial

    def bound_initial(view, request, *args, **kwargs):
        request.organization = organization
        request.workspace = workspace
        return initial(view, request, *args, **kwargs)

    APIView.initial = bound_initial
    try:
        yield
    finally:
        APIView.initial = initial


def _store_comparison(owner, base, compared, *, any_organization=False):
    """Build and store a comparison of ``base`` and ``compared`` as ``owner``
    with no bound workspace, and return its compare_id.

    With ``any_organization`` the datasets are resolved without the
    organization filter, so the stored comparison can pair datasets of two
    organizations.
    """
    from model_hub.views import develop_dataset

    resolver = (
        mock.patch.object(
            develop_dataset,
            "_request_dataset_queryset",
            lambda _request: Dataset.objects.filter(deleted=False),
        )
        if any_organization
        else contextlib.nullcontext()
    )
    with resolver, _request_binding(base.organization):
        built = _compare_request(_client_without_workspace(owner), base, compared)
    assert built.status_code == status.HTTP_200_OK, built.content
    return built.data["result"]["metadata"]["compare_id"]


def _download(
    client,
    base,
    datasets,
    *,
    compare_id=None,
    with_rows=False,
    base_column_name="input",
):
    """Download a comparison. Without ``with_rows`` the payload has the shape
    the comparison page sends: the selected datasets, the base column and the
    compare_id."""
    payload: dict[str, object] = {
        "dataset_ids": [str(dataset.id) for dataset in datasets],
        "base_column_name": base_column_name,
    }
    if compare_id is not None:
        payload["compare_id"] = compare_id
    if with_rows:
        payload["dataset_info"] = {
            "alpha": {
                str(dataset.id): str(Row.objects.get(dataset=dataset).id)
                for dataset in [base, *datasets]
            }
        }
        payload["common_column_names"] = ["judge"]
    response = client.post(
        f"/model-hub/datasets/{base.id}/compare-datasets/download/",
        payload,
        format="json",
    )
    if response.streaming:
        response._proof_body = b"".join(response.streaming_content)
    return response


def _body(response):
    body = response._proof_body if response.streaming else response.content
    return body.decode()


def _member_client(organization, workspace):
    """An active organization member, not an admin, bound to ``workspace``."""
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from accounts.models.workspace import WorkspaceMembership
    from conftest import WorkspaceAwareAPIClient
    from tfc.constants.levels import Level

    member = User.objects.create_user(
        email=f"compare-member-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Comparison member",
        organization=organization,
    )
    org_membership = OrganizationMembership.no_workspace_objects.create(
        user=member, organization=organization, level=Level.MEMBER, is_active=True
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=member,
        workspace=workspace,
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
        organization_membership=org_membership,
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=member)
    client.set_workspace(workspace)
    return client


@pytest.fixture
def compare_member_client(organization, workspace):
    client = _member_client(organization, workspace)
    yield client
    client.stop_workspace_injection()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("download", [False, True])
@pytest.mark.parametrize("scope", ["another_organization", "another_workspace"])
def test_compare_cached_id_requires_requested_datasets(
    auth_client,
    compare_member_client,
    compare_datasets,
    comparison_page_store,
    organization,
    workspace,
    scope,
    download,
):
    from accounts.models.workspace import Workspace
    from conftest import WorkspaceAwareAPIClient

    if scope == "another_organization":
        other_organization, other_user, other_workspace = _another_organization()
        builder = WorkspaceAwareAPIClient()
        builder.force_authenticate(user=other_user)
    else:
        other_organization = organization
        other_workspace = Workspace.objects.create(
            name="Additional workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=workspace.created_by,
        )
        builder = WorkspaceAwareAPIClient()
        builder.force_authenticate(user=workspace.created_by)

    builder.set_workspace(other_workspace)
    cached_base, cached_other = _new_compare_pair(
        other_organization, other_workspace, "Cached pair", "cached-alpha"
    )
    built = _compare_request(builder, cached_base, cached_other)
    assert built.status_code == status.HTTP_200_OK, built.content
    compare_id = built.data["result"]["metadata"]["compare_id"]
    builder.stop_workspace_injection()

    base, compared = _new_compare_pair(organization, workspace, "Requested pair")
    response = _compare_request(
        compare_member_client, base, compared, download=download, compare_id=compare_id
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND, (
        response._proof_body if response.streaming else response.content
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("download", [False, True])
@pytest.mark.parametrize("scope", ["another_organization", "another_workspace"])
def test_compare_cached_id_for_same_base_and_another_compared_dataset_returns_not_found(
    user,
    compare_member_client,
    comparison_page_store,
    organization,
    workspace,
    scope,
    download,
):
    """A comparison stored for the requested base and a dataset outside the
    request scope is not served for the base and the requested dataset."""
    from accounts.models.workspace import Workspace

    base = _compare_dataset(
        organization,
        workspace,
        "Requested base",
        [("alpha", "requested-base-alpha"), ("stored-beta", "requested-base-beta")],
    )
    compared = _compare_dataset(
        organization,
        workspace,
        "Requested compared",
        [("alpha", "requested-compared-alpha")],
    )
    if scope == "another_workspace":
        stored_organization = organization
        stored_workspace = Workspace.objects.create(
            name="Additional workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
    else:
        stored_organization, _, stored_workspace = _another_organization()
    stored = _compare_dataset(
        stored_organization,
        stored_workspace,
        "Stored compared",
        [("stored-beta", "stored-compared-beta")],
    )
    compare_id = _store_comparison(
        user, base, stored, any_organization=scope == "another_organization"
    )

    if download:
        response = _download(
            compare_member_client, base, [compared], compare_id=compare_id
        )
    else:
        response = _compare_request(
            compare_member_client, base, compared, compare_id=compare_id
        )
    body = _body(response)
    assert response.status_code == status.HTTP_404_NOT_FOUND, body
    assert "stored-" not in body

    own = _compare_request(compare_member_client, base, compared)
    assert own.status_code == status.HTTP_200_OK, own.content
    assert "requested-compared-alpha" in own.content.decode()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("download", [False, True])
@pytest.mark.parametrize(
    "compare_id_kind", ["another_organization", "mixed_organizations", "unknown"]
)
def test_compare_owner_without_workspace_gets_not_found_for_another_organization_datasets(
    user,
    comparison_page_store,
    organization,
    workspace,
    compare_id_kind,
    download,
):
    """An owner with no bound workspace passes the workspace check, and every
    named dataset is still resolved within the owner's organization, whether
    the compare_id is stored or unknown."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    other_organization, other_owner, other_workspace = _another_organization()
    own_base, own_compared = _new_compare_pair(organization, workspace, "Own pair")
    if compare_id_kind == "mixed_organizations":
        base = own_base
        compared = _compare_dataset(
            other_organization,
            other_workspace,
            "Stored pair 1",
            [("alpha", "Stored pair-1")],
        )
        compare_id = _store_comparison(user, base, compared, any_organization=True)
    else:
        base, compared = _new_compare_pair(
            other_organization, other_workspace, "Stored pair"
        )
        if compare_id_kind == "another_organization":
            compare_id = _store_comparison(other_owner, base, compared)
        else:
            compare_id = str(uuid.uuid4())

    client = _client_without_workspace(user)
    with _request_binding(organization):
        if download:
            own = _download(client, own_base, [own_compared], with_rows=True)
        else:
            own = _compare_request(client, own_base, own_compared)
        with CaptureQueriesContext(connection) as captured:
            if download:
                response = _download(
                    client, base, [compared], compare_id=compare_id, with_rows=True
                )
            else:
                response = _compare_request(
                    client, base, compared, compare_id=compare_id
                )
    assert own.status_code == status.HTTP_200_OK, _body(own)
    assert "Own pair-1" in _body(own)
    _assert_no_comparison_cells(response, [], captured)
    assert "Stored pair" not in _body(response)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("requester", ["owner", "member"])
def test_compare_matching_compare_id_serves_stored_pages_and_download(
    requester,
    auth_client,
    compare_member_client,
    comparison_page_store,
    comparison_cell_reads,
    organization,
    workspace,
):
    client = auth_client if requester == "owner" else compare_member_client
    base, compared = _new_compare_pair(organization, workspace, "Served pair")
    built = _compare_request(client, base, compared)
    assert built.status_code == status.HTTP_200_OK, built.content
    assert "prepare" in comparison_cell_reads
    compare_id = built.data["result"]["metadata"]["compare_id"]
    comparison_cell_reads.clear()

    cached = _compare_request(client, base, compared, compare_id=compare_id)
    assert cached.status_code == status.HTTP_200_OK, cached.content
    assert cached.data["result"]["metadata"]["compare_id"] == compare_id
    assert "Served pair-1" in cached.content.decode()
    assert "prepare" not in comparison_cell_reads

    csv = _download(client, base, [compared], compare_id=compare_id)
    assert csv.status_code == status.HTTP_200_OK, _body(csv)
    assert "Served pair-1" in _body(csv)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "request_shape", ["compare", "download_with_rows", "download_without_rows"]
)
def test_compare_unknown_compare_id_is_a_cache_miss(
    auth_client,
    comparison_page_store,
    comparison_cell_reads,
    organization,
    workspace,
    request_shape,
):
    """A compare_id with no stored comparison on this server is built again
    under that id from the requested datasets, and its download uses the rows
    sent with the request."""
    base, compared = _new_compare_pair(organization, workspace, "Fresh pair")
    compare_id = str(uuid.uuid4())
    if request_shape == "compare":
        response = _compare_request(auth_client, base, compared, compare_id=compare_id)
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.data["result"]["metadata"]["compare_id"] == compare_id
        assert "prepare" in comparison_cell_reads
        assert "Fresh pair-1" in response.content.decode()
    elif request_shape == "download_with_rows":
        response = _download(
            auth_client, base, [compared], compare_id=compare_id, with_rows=True
        )
        assert response.status_code == status.HTTP_200_OK, _body(response)
        assert "Fresh pair-1" in _body(response)
    else:
        response = _download(auth_client, base, [compared], compare_id=compare_id)
        assert response.status_code == status.HTTP_400_BAD_REQUEST, _body(response)
        assert "Unable to download dataset" in _body(response)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("with_compare_id", [False, True])
@pytest.mark.parametrize("selection", ["base_missing", "base_last"])
def test_compare_download_keeps_selected_dataset_order(
    auth_client,
    comparison_page_store,
    organization,
    workspace,
    selection,
    with_compare_id,
):
    base, compared = _new_compare_pair(organization, workspace, "Ordered pair")
    if selection == "base_missing":
        listed, expected = [compared], [base, compared]
    else:
        listed, expected = [compared, base], [compared, base]
    compare_id = None
    if with_compare_id:
        built = _compare_request(auth_client, base, compared)
        assert built.status_code == status.HTTP_200_OK, built.content
        compare_id = built.data["result"]["metadata"]["compare_id"]

    response = _download(
        auth_client,
        base,
        listed,
        compare_id=compare_id,
        with_rows=not with_compare_id,
    )
    assert response.status_code == status.HTTP_200_OK, _body(response)
    header = _body(response).splitlines()[0].split(",")
    assert header[0] == "input"
    dataset_order = list(
        dict.fromkeys(column.rsplit("-", 1)[1] for column in header[1:])
    )
    assert dataset_order == [dataset.name for dataset in expected]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("download", [False, True])
def test_compare_cached_id_with_another_base_column_returns_not_found(
    auth_client, comparison_page_store, organization, workspace, download
):
    base, compared = _new_compare_pair(organization, workspace, "Joined pair")
    built = _compare_request(auth_client, base, compared)
    assert built.status_code == status.HTTP_200_OK, built.content
    compare_id = built.data["result"]["metadata"]["compare_id"]
    if download:
        response = _download(
            auth_client,
            base,
            [compared],
            compare_id=compare_id,
            base_column_name="judge",
        )
    else:
        response = _compare_request(
            auth_client,
            base,
            compared,
            compare_id=compare_id,
            base_column_name="judge",
        )
    assert response.status_code == status.HTTP_404_NOT_FOUND, _body(response)
    assert "Joined pair" not in _body(response)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("download", [False, True])
def test_compare_member_without_workspace_returns_not_found(
    compare_member_client,
    compare_datasets,
    comparison_page_store,
    organization,
    workspace,
    monkeypatch,
    download,
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from rest_framework.views import APIView

    from accounts.models.workspace import Workspace

    another_workspace = Workspace.objects.create(
        name="Additional workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=workspace.created_by,
    )
    base, compared = _new_compare_pair(organization, another_workspace, "Other pair")
    compare_member_client.stop_workspace_injection()
    from conftest import WorkspaceAwareAPIClient

    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=compare_member_client.handler._force_user)
    initial = APIView.initial

    def bound_initial(view, request, *args, **kwargs):
        request.organization = organization
        request.workspace = None
        return initial(view, request, *args, **kwargs)

    monkeypatch.setattr(APIView, "initial", bound_initial)
    with CaptureQueriesContext(connection) as captured:
        response = _compare_request(client, base, compared, download=download)
    _assert_no_comparison_cells(response, [], captured)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("download", [False, True])
def test_compare_member_of_non_default_workspace_gets_not_found_for_default_workspace_datasets(
    compare_datasets,
    comparison_page_store,
    organization,
    workspace,
    download,
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from accounts.models.workspace import Workspace

    bound_workspace = Workspace.objects.create(
        name="Team workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=workspace.created_by,
    )
    assert bound_workspace.is_default is False
    assert compare_datasets["base_dataset"].workspace.is_default is True
    client = _member_client(organization, bound_workspace)
    own_base, own_compared = _new_compare_pair(organization, bound_workspace, "Team")
    own = _compare_request(client, own_base, own_compared, download=download)
    assert own.status_code == status.HTTP_200_OK, _body(own)
    assert "Team-1" in _body(own)

    with CaptureQueriesContext(connection) as captured:
        response = _compare_request(
            client,
            compare_datasets["base_dataset"],
            compare_datasets["other_dataset"],
            download=download,
        )
    client.stop_workspace_injection()
    _assert_no_comparison_cells(response, [], captured)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("download", [False, True])
def test_compare_owner_without_workspace_keeps_organization_scope(
    user, compare_datasets, comparison_page_store, download
):
    client = _client_without_workspace(user)
    with _request_binding(compare_datasets["base_dataset"].organization):
        response = _compare_request(
            client,
            compare_datasets["base_dataset"],
            compare_datasets["other_dataset"],
            download=download,
        )
    assert response.status_code == status.HTTP_200_OK, _body(response)
    assert "fail" in _body(response)
