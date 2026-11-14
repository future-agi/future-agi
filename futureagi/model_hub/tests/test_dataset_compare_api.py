import uuid
from pathlib import Path

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


def _compare_request(client, base, compared, *, download=False, compare_id=None):
    payload = {
        "dataset_ids": [str(compared.id)],
        "base_column_name": "input",
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
