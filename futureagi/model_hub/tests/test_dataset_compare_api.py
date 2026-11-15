import contextlib
import csv
import json
import shutil
import time
import uuid
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest import mock
from unittest.mock import Mock

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
from model_hub.utils.compare_storage import CompareObjectUnavailable, created_at_now
from model_hub.views.develop_dataset import CompareDatasetsView
from tfc.utils.storage import get_compare_local_dir, get_compare_metadata_path


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
@pytest.mark.parametrize("cleanup_queued", [False, True])
def test_compare_delete_removes_api_marker_with_queued_or_fallback_cleanup(
    auth_client, compare_datasets, monkeypatch, tmp_path, cleanup_queued
):
    """Exit removes the marker the API wrote, even when a worker is available.

    In the distributed install a worker cannot see this process's root, so a
    cleanup handed to it leaves the marker behind.
    """
    compare_id = uuid.uuid4()
    monkeypatch.chdir(tmp_path)
    compare_dir = Path(get_compare_local_dir(compare_id))
    compare_dir.mkdir(parents=True)
    (compare_dir / "metadata.json").write_text("{}", encoding="utf-8")
    dataset_ids = [
        str(compare_datasets["base_dataset"].id),
        str(compare_datasets["other_dataset"].id),
    ]
    # The comparison's shared metadata, as the page writer publishes it.
    shared = {
        f"compare/{compare_id}/metadata.json": {
            "status": "completed",
            "created_at": created_at_now(),
            "base_dataset_id": dataset_ids[0],
            "comparison_datasets": dataset_ids,
        }
    }

    def read(compare_id, name):
        object_key = f"compare/{compare_id}/{name}"
        if object_key not in shared:
            raise CompareObjectUnavailable(name)
        return shared[object_key]

    def raise_temporal_unavailable(*args, **kwargs):
        if not cleanup_queued:
            raise RuntimeError("Temporal unavailable")

    monkeypatch.setattr("model_hub.views.develop_dataset.read_compare_json", read)
    monkeypatch.setattr(
        "tfc.temporal.drop_in.start_activity",
        raise_temporal_unavailable,
    )
    monkeypatch.setattr(
        "model_hub.utils.compare_storage.delete_compare_objects",
        lambda compare_id: shared.clear(),
    )
    monkeypatch.setattr(
        "model_hub.views.develop_dataset.delete_compare_metadata",
        lambda compare_id: shared.pop(f"compare/{compare_id}/metadata.json"),
    )

    response = auth_client.delete(f"/model-hub/datasets/delete-compare/{compare_id}/")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["result"] == {"message": "File(s) deleted successfully"}
    assert compare_dir.resolve().parent == tmp_path / "media" / "compare"
    assert not compare_dir.exists()
    assert shared == {}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "cleanup_queued",
    [True, False],
    ids=["cleanup-activity-queued", "synchronous-fallback"],
)
def test_compare_delete_logs_a_local_folder_it_cannot_remove(
    auth_client, compare_datasets, monkeypatch, tmp_path, cleanup_queued
):
    import os
    import shutil

    from structlog.testing import capture_logs

    compare_id = uuid.uuid4()
    monkeypatch.chdir(tmp_path)
    compare_dir = Path(get_compare_local_dir(compare_id))
    compare_dir.mkdir(parents=True)
    (compare_dir / "page_1.json").write_text("{}", encoding="utf-8")
    dataset_ids = [
        str(compare_datasets["base_dataset"].id),
        str(compare_datasets["other_dataset"].id),
    ]
    shared = {
        f"compare/{compare_id}/metadata.json": {
            "status": "completed",
            "created_at": created_at_now(),
            "base_dataset_id": dataset_ids[0],
            "comparison_datasets": dataset_ids,
        },
        f"compare/{compare_id}/page_1.json": {},
    }

    def read(compare_id, name):
        object_key = f"compare/{compare_id}/{name}"
        if object_key not in shared:
            raise CompareObjectUnavailable(name)
        return shared[object_key]

    def remove_shared_objects(compare_id):
        for object_key in list(shared):
            if object_key.startswith(f"compare/{compare_id}/"):
                del shared[object_key]

    started = []

    def start_activity(*args, **kwargs):
        if not cleanup_queued:
            raise RuntimeError("Temporal unavailable")
        started.append((args, kwargs))

    real_rmtree = shutil.rmtree

    def rmtree(path, ignore_errors=False, onerror=None, **kwargs):
        # The folder's page cannot be removed (e.g. its permissions changed).
        if os.fspath(path) != os.fspath(get_compare_local_dir(compare_id)):
            return real_rmtree(
                path, ignore_errors=ignore_errors, onerror=onerror, **kwargs
            )
        error = PermissionError(13, "Permission denied")
        if ignore_errors:
            return None
        if onerror is None:
            raise error
        page = str(compare_dir / "page_1.json")
        onerror(os.unlink, page, (type(error), error, None))
        return None

    monkeypatch.setattr("model_hub.views.develop_dataset.read_compare_json", read)
    monkeypatch.setattr(
        "model_hub.views.develop_dataset.delete_compare_metadata",
        lambda compare_id: shared.pop(f"compare/{compare_id}/metadata.json"),
    )
    monkeypatch.setattr(
        "model_hub.utils.compare_storage.delete_compare_objects",
        remove_shared_objects,
    )
    monkeypatch.setattr("tfc.temporal.drop_in.start_activity", start_activity)
    monkeypatch.setattr(shutil, "rmtree", rmtree)

    with capture_logs() as logs:
        response = auth_client.delete(
            f"/model-hub/datasets/delete-compare/{compare_id}/"
        )

    # The metadata is gone, so the comparison is no longer served; the folder
    # is left to the local sweep and the failure is logged, not hidden. When
    # the cleanup activity cannot be queued, the shared objects are removed
    # synchronously and the response is the same.
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["result"] == {"message": "File(s) deleted successfully"}
    assert compare_dir.exists()
    if cleanup_queued:
        assert len(started) == 1
        assert shared == {f"compare/{compare_id}/page_1.json": {}}
    else:
        assert started == []
        assert shared == {}
    failures = [
        log for log in logs if log["event"] == "compare_local_folder_delete_failed"
    ]
    assert len(failures) == 1
    assert failures[0]["log_level"] == "warning"
    assert failures[0]["compare_id"] == str(compare_id)
    assert failures[0]["exc_info"] is True


@pytest.mark.django_db
def test_compare_delete_quietly_skips_a_local_folder_already_gone(
    auth_client, compare_datasets, monkeypatch, tmp_path
):
    from structlog.testing import capture_logs

    compare_id = uuid.uuid4()
    monkeypatch.chdir(tmp_path)
    dataset_ids = [
        str(compare_datasets["base_dataset"].id),
        str(compare_datasets["other_dataset"].id),
    ]
    shared = {
        f"compare/{compare_id}/metadata.json": {
            "status": "completed",
            "created_at": created_at_now(),
            "base_dataset_id": dataset_ids[0],
            "comparison_datasets": dataset_ids,
        }
    }

    def read(compare_id, name):
        object_key = f"compare/{compare_id}/{name}"
        if object_key not in shared:
            raise CompareObjectUnavailable(name)
        return shared[object_key]

    monkeypatch.setattr("model_hub.views.develop_dataset.read_compare_json", read)
    monkeypatch.setattr(
        "model_hub.views.develop_dataset.delete_compare_metadata",
        lambda compare_id: shared.pop(f"compare/{compare_id}/metadata.json"),
    )
    monkeypatch.setattr("tfc.temporal.drop_in.start_activity", lambda *a, **k: None)

    with capture_logs() as logs:
        response = auth_client.delete(
            f"/model-hub/datasets/delete-compare/{compare_id}/"
        )

    assert response.status_code == status.HTTP_200_OK
    assert not [
        log for log in logs if log["event"] == "compare_local_folder_delete_failed"
    ]


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

    def fake_read_compare_json(compare_id, name):
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
        "model_hub.views.develop_dataset.read_compare_json",
        fake_read_compare_json,
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
        total_pages=1,
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


class _ComparisonPageStore(dict):
    """Comparison pages by storage key, with every key read and written."""

    def __init__(self):
        super().__init__()
        self.reads = []
        self.writes = []

    def keys_used_under(self, compare_id):
        prefix = f"compare/{compare_id}/"
        return [key for key in [*self.reads, *self.writes] if key.startswith(prefix)]


@pytest.fixture
def comparison_page_store(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COMPARE_DATASET_LOCAL_ROOT", str(tmp_path / "compare"))
    pages = _ComparisonPageStore()

    def write(compare_id, name, data):
        key = f"compare/{compare_id}/{name}"
        pages.writes.append(key)
        pages[key] = json.loads(json.dumps(data))

    def read(compare_id, name):
        key = f"compare/{compare_id}/{name}"
        pages.reads.append(key)
        if key not in pages:
            raise CompareObjectUnavailable(name)
        return json.loads(json.dumps(pages[key]))

    monkeypatch.setattr("model_hub.views.develop_dataset.write_compare_json", write)
    monkeypatch.setattr("model_hub.views.develop_dataset.read_compare_json", read)
    monkeypatch.setattr(
        "model_hub.views.develop_dataset.start_local_sweeper", lambda: None
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
    client,
    base,
    compared,
    *,
    download=False,
    compare_id=None,
    base_column_name="input",
    current_page_index=0,
):
    payload = {
        "dataset_ids": [str(compared.id)],
        "base_column_name": base_column_name,
        "page_size": 10,
        "current_page_index": current_page_index,
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
def test_compare_download_rejects_rows_outside_resolved_datasets(
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
    # Private comparison downloads reject misattributed rows, rather than
    # silently dropping them from a partial CSV.
    assert response.status_code == status.HTTP_404_NOT_FOUND, response.content
    assert "other-org-row" not in response.content.decode()


@pytest.mark.django_db(transaction=True)
def test_compare_download_rejects_rows_from_another_dataset_in_organization(
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
    assert response.status_code == status.HTTP_404_NOT_FOUND, response.content
    assert "extra-row" not in response.content.decode()


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


def _assert_compare_not_found(response):
    # Pin the actual HTTP JSON envelope consumed by the frontend, not a body
    # generated with the same helper as the implementation under test.
    message = "This comparison was not found or has expired. Open the comparison again."
    assert response.status_code == status.HTTP_404_NOT_FOUND, response.content
    assert response.json() == {
        "status": False,
        "type": "not_found",
        "code": "compare_not_found",
        "detail": message,
        "message": message,
        "error": message,
        "result": message,
    }


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
def test_compare_cached_id_of_another_scope_builds_requested_datasets(
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
    body = _body(response)
    assert response.status_code == status.HTTP_200_OK, body
    assert "Requested pair-1" in body
    assert "Cached pair" not in body
    if not download:
        assert response.data["result"]["metadata"]["compare_id"] != compare_id


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("download", [False, True])
@pytest.mark.parametrize("scope", ["another_organization", "another_workspace"])
def test_compare_cached_id_for_same_base_and_another_compared_dataset_is_not_served(
    user,
    compare_member_client,
    comparison_page_store,
    organization,
    workspace,
    scope,
    download,
):
    """A comparison stored for the requested base and a dataset outside the
    request scope is not served for the base and the requested dataset: the
    request is handled like one with a compare_id that has no stored
    comparison."""
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
    assert "stored-" not in body
    if download:
        _assert_compare_not_found(response)
    else:
        assert response.status_code == status.HTTP_200_OK, body
        assert "requested-compared-alpha" in body
        assert response.data["result"]["metadata"]["compare_id"] != compare_id


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
    from the requested datasets under a new compare_id, and nothing is read
    or stored as pages under the id sent. Its download uses the rows sent with the
    request."""
    base, compared = _new_compare_pair(organization, workspace, "Fresh pair")
    compare_id = str(uuid.uuid4())
    if request_shape == "compare":
        response = _compare_request(auth_client, base, compared, compare_id=compare_id)
        assert response.status_code == status.HTTP_200_OK, response.content
        new_compare_id = response.data["result"]["metadata"]["compare_id"]
        assert new_compare_id != compare_id
        assert "prepare" in comparison_cell_reads
        assert "Fresh pair-1" in response.content.decode()
        assert comparison_page_store.keys_used_under(compare_id) == [
            f"compare/{compare_id}/metadata.json"
        ]
        assert not Path(get_compare_local_dir(compare_id)).exists()
        assert f"compare/{new_compare_id}/page_1.json" in comparison_page_store
    elif request_shape == "download_with_rows":
        response = _download(
            auth_client, base, [compared], compare_id=compare_id, with_rows=True
        )
        assert response.status_code == status.HTTP_200_OK, _body(response)
        assert "Fresh pair-1" in _body(response)
    else:
        response = _download(auth_client, base, [compared], compare_id=compare_id)
        _assert_compare_not_found(response)


def _page_of_another_selection(label):
    """A stored comparison page in the shape the comparison builder writes."""
    column_id = str(uuid.uuid4())
    return {
        "column_config": [
            {
                "id": column_id,
                "name": f"{label} column",
                "data_type": DataTypeChoices.TEXT.value,
            }
        ],
        "table": [
            {
                "row_id": str(uuid.uuid4()),
                column_id: {
                    "cell_value": f"{label} value",
                    "cell_row_id": str(uuid.uuid4()),
                },
            }
        ],
    }


@pytest.mark.django_db(transaction=True)
def test_compare_id_without_stored_metadata_reads_none_of_its_pages(
    auth_client, comparison_page_store, organization, workspace
):
    """Storage can hold pages under a compare_id that has no stored comparison
    on this server, written for another selection. A later block request
    with that id builds the requested comparison under a new compare_id, and
    reads only metadata and writes nothing under the id sent."""
    base, compared = _new_compare_pair(organization, workspace, "Requested pair")
    compare_id = str(uuid.uuid4())
    comparison_page_store[f"compare/{compare_id}/page_2.json"] = (
        _page_of_another_selection("Earlier selection")
    )

    response = _compare_request(
        auth_client, base, compared, compare_id=compare_id, current_page_index=1
    )

    body = _body(response)
    assert response.status_code == status.HTTP_200_OK, body
    assert "Earlier selection" not in body
    new_compare_id = response.data["result"]["metadata"]["compare_id"]
    assert new_compare_id != compare_id
    assert response.data["result"]["table"] == []
    assert response.data["result"]["metadata"]["total_rows"] == 1
    assert comparison_page_store.keys_used_under(compare_id) == [
        f"compare/{compare_id}/metadata.json"
    ]
    assert not Path(get_compare_local_dir(compare_id)).exists()
    assert f"compare/{new_compare_id}/page_1.json" in comparison_page_store.writes


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("download", [False, True])
def test_compare_reads_only_the_pages_of_the_stored_comparison(
    auth_client, comparison_page_store, organization, workspace, download
):
    """A block after the last page of a stored comparison is empty, even when
    storage has a later page under the same compare_id."""
    base, compared = _new_compare_pair(organization, workspace, "One page pair")
    built = _compare_request(auth_client, base, compared)
    assert built.status_code == status.HTTP_200_OK, built.content
    compare_id = built.data["result"]["metadata"]["compare_id"]
    comparison_page_store[f"compare/{compare_id}/page_2.json"] = (
        _page_of_another_selection("Later page")
    )
    comparison_page_store.reads.clear()

    if download:
        response = _download(auth_client, base, [compared], compare_id=compare_id)
    else:
        response = _compare_request(
            auth_client, base, compared, compare_id=compare_id, current_page_index=1
        )

    body = _body(response)
    assert response.status_code == status.HTTP_200_OK, body
    assert "Later page" not in body
    if download:
        assert "One page pair-1" in body
    else:
        assert response.data["result"]["metadata"]["compare_id"] == compare_id
        assert response.data["result"]["table"] == []
    assert comparison_page_store.reads == [f"compare/{compare_id}/metadata.json"]


def _base_values(response, base):
    base_column_id = str(Column.objects.get(dataset=base, name="input").id)
    table = response.data["result"]["table"]
    return [row[base_column_id]["cell_value"] for row in table]


@pytest.mark.django_db(transaction=True)
def test_compare_later_blocks_return_rows_of_the_requested_comparison(
    auth_client, comparison_page_store, comparison_cell_reads, organization, workspace
):
    """Later blocks sent with the returned compare_id get the next pages of
    that comparison. On a server with no stored comparison for the id, the
    block is the requested page of a new build under a new compare_id, and
    the blocks after it are served from that build."""
    values = [f"value-{index:02d}" for index in range(25)]
    base, compared = [
        _compare_dataset(
            organization,
            workspace,
            f"Paged {index}",
            [(value, f"Paged {index} {value}") for value in values],
        )
        for index in range(2)
    ]
    first = _compare_request(auth_client, base, compared)
    assert first.status_code == status.HTTP_200_OK, first.content
    compare_id = first.data["result"]["metadata"]["compare_id"]
    blocks = [first] + [
        _compare_request(
            auth_client, base, compared, compare_id=compare_id, current_page_index=page
        )
        for page in (1, 2)
    ]
    for block in blocks:
        assert block.status_code == status.HTTP_200_OK, block.content
        assert block.data["result"]["metadata"]["compare_id"] == compare_id
        assert block.data["result"]["metadata"]["total_rows"] == len(values)
    block_values = [_base_values(block, base) for block in blocks]
    assert [len(page) for page in block_values] == [10, 10, 5]
    assert sorted(value for page in block_values for value in page) == values
    assert comparison_cell_reads.count("prepare") == 1

    # Losing a replica's local directory must not lose the shared comparison.
    shutil.rmtree(get_compare_local_dir(compare_id))
    cached = _compare_request(
        auth_client, base, compared, compare_id=compare_id, current_page_index=1
    )
    assert cached.status_code == status.HTTP_200_OK, cached.content
    assert cached.data["result"]["metadata"]["compare_id"] == compare_id
    assert comparison_cell_reads.count("prepare") == 1
    del comparison_page_store[f"compare/{compare_id}/metadata.json"]
    rebuilt = _compare_request(
        auth_client, base, compared, compare_id=compare_id, current_page_index=1
    )
    assert rebuilt.status_code == status.HTTP_200_OK, rebuilt.content
    new_compare_id = rebuilt.data["result"]["metadata"]["compare_id"]
    assert new_compare_id != compare_id
    assert comparison_cell_reads.count("prepare") == 2
    assert (
        rebuilt.data["result"]["table"]
        == comparison_page_store[f"compare/{new_compare_id}/page_2.json"]["table"]
    )
    assert len(_base_values(rebuilt, base)) == 10
    assert set(_base_values(rebuilt, base)) <= set(values)

    last = _compare_request(
        auth_client, base, compared, compare_id=new_compare_id, current_page_index=2
    )
    assert last.status_code == status.HTTP_200_OK, last.content
    assert last.data["result"]["metadata"]["compare_id"] == new_compare_id
    assert comparison_cell_reads.count("prepare") == 2
    assert (
        last.data["result"]["table"]
        == comparison_page_store[f"compare/{new_compare_id}/page_3.json"]["table"]
    )
    assert len(_base_values(last, base)) == 5


class _TimeWithoutWaits:
    """The time module, except that a wait fails the request at once."""

    def __getattr__(self, name):
        return getattr(time, name)

    @staticmethod
    def sleep(seconds):
        raise AssertionError("waited for a stored comparison")


def _comparison_shown(response):
    """The columns, rows and counts a comparison response shows, without the
    ids each build assigns."""
    result = response.data["result"]
    rows = sorted(
        json.dumps(
            {key: value for key, value in row.items() if key != "row_id"},
            sort_keys=True,
        )
        for row in result.get("table", [])
    )
    counts = {
        key: value for key, value in result["metadata"].items() if key != "compare_id"
    }
    return result.get("column_config"), rows, counts


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("download", [False, True])
@pytest.mark.parametrize(
    "stored_state", ["completed", "too_few_pages", "still_building"]
)
def test_compare_id_of_another_selection_gets_the_unknown_id_response(
    auth_client,
    comparison_page_store,
    organization,
    workspace,
    monkeypatch,
    stored_state,
    download,
):
    """A compare_id stored on this server for another selection gets the same
    response as a compare_id with no stored comparison. Its page count is
    not checked and its build is not waited for."""
    from model_hub.views import develop_dataset

    stored_base, stored_compared = _new_compare_pair(
        organization, workspace, "Stored pair"
    )
    built = _compare_request(auth_client, stored_base, stored_compared)
    assert built.status_code == status.HTTP_200_OK, built.content
    compare_id = built.data["result"]["metadata"]["compare_id"]
    metadata_path = Path(get_compare_metadata_path(compare_id))
    metadata = json.loads(metadata_path.read_text())
    if stored_state == "too_few_pages":
        metadata["total_pages"] = 0
    elif stored_state == "still_building":
        metadata.update(status="processing", total_processed=1)
    stored_metadata = json.dumps(metadata)
    metadata_path.write_text(stored_metadata)
    comparison_page_store[f"compare/{compare_id}/metadata.json"] = metadata
    comparison_page_store.reads.clear()
    comparison_page_store.writes.clear()
    monkeypatch.setattr(develop_dataset, "time", _TimeWithoutWaits())

    base, compared = _new_compare_pair(organization, workspace, "Requested pair")
    if download:
        responses = [
            _download(auth_client, base, [compared], compare_id=sent, with_rows=True)
            for sent in (compare_id, str(uuid.uuid4()))
        ]
    else:
        responses = [
            _compare_request(auth_client, base, compared, compare_id=sent)
            for sent in (compare_id, str(uuid.uuid4()))
        ]
    stored_id_response, unknown_id_response = responses

    body = _body(stored_id_response)
    assert stored_id_response.status_code == status.HTTP_200_OK, body
    assert unknown_id_response.status_code == status.HTTP_200_OK
    assert "Requested pair-1" in body
    assert "Stored pair" not in body
    if download:
        assert body == _body(unknown_id_response)
    else:
        assert _comparison_shown(stored_id_response) == _comparison_shown(
            unknown_id_response
        )
        assert stored_id_response.data["result"]["metadata"]["compare_id"] not in (
            compare_id,
            unknown_id_response.data["result"]["metadata"]["compare_id"],
        )
    assert metadata_path.read_text() == stored_metadata
    assert comparison_page_store.keys_used_under(compare_id) == [
        f"compare/{compare_id}/metadata.json"
    ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("download", [False, True])
def test_compare_id_still_building_checks_dataset_scope_first(
    user, comparison_page_store, organization, monkeypatch, download
):
    """A compare_id stored for the requested selection and still being built
    returns 404 at once when the datasets are not found in the request's
    organization. The page count and the build are not checked first."""
    from model_hub.views import develop_dataset

    other_organization, other_owner, other_workspace = _another_organization()
    base, compared = _new_compare_pair(
        other_organization, other_workspace, "Building pair"
    )
    compare_id = _store_comparison(other_owner, base, compared)
    metadata_path = Path(get_compare_metadata_path(compare_id))
    metadata = json.loads(metadata_path.read_text())
    metadata.update(status="processing", total_processed=1)
    metadata_path.write_text(json.dumps(metadata))
    comparison_page_store[f"compare/{compare_id}/metadata.json"] = metadata
    monkeypatch.setattr(develop_dataset, "time", _TimeWithoutWaits())

    client = _client_without_workspace(user)
    with _request_binding(organization):
        if download:
            response = _download(client, base, [compared], compare_id=compare_id)
        else:
            response = _compare_request(client, base, compared, compare_id=compare_id)
    body = _body(response)
    assert response.status_code == status.HTTP_404_NOT_FOUND, body
    assert "Building pair" not in body


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
def test_compare_cached_id_with_another_base_column_is_not_served(
    auth_client, comparison_page_store, organization, workspace, download
):
    """Joined on the judge column, the pair has no common value. The pages
    stored for the input column are not served, and download has no rows."""
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
    body = _body(response)
    assert "Joined pair" not in body
    if download:
        _assert_compare_not_found(response)
    else:
        assert response.status_code == status.HTTP_200_OK, body
        assert not response.data["result"].get("table")
        assert response.data["result"]["metadata"]["total_rows"] == 0
        assert response.data["result"]["metadata"]["total_pages"] == 0
        assert response.data["result"]["metadata"]["compare_id"] != compare_id


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


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "candidate_state",
    ["unknown", "expired", "another_selection", "another_workspace", "another_org"],
)
@pytest.mark.parametrize("current_page_index", [0, 1])
def test_compare_empty_cache_miss_has_fresh_id_and_zero_counts(
    user,
    compare_member_client,
    comparison_page_store,
    comparison_cell_reads,
    organization,
    workspace,
    candidate_state,
    current_page_index,
):
    from accounts.models.workspace import Workspace

    compare_id = str(uuid.uuid4())
    if candidate_state != "unknown":
        stored_org, stored_owner, stored_workspace = organization, user, workspace
        if candidate_state == "another_org":
            stored_org, stored_owner, stored_workspace = _another_organization()
        elif candidate_state == "another_workspace":
            stored_workspace = Workspace.objects.create(
                name="Empty comparison sibling",
                organization=organization,
                created_by=user,
                is_default=False,
                is_active=True,
            )
        stored_base, stored_peer = _new_compare_pair(
            stored_org, stored_workspace, "Nonempty cached pair"
        )
        compare_id = _store_comparison(stored_owner, stored_base, stored_peer)
        if candidate_state == "expired":
            metadata = comparison_page_store[f"compare/{compare_id}/metadata.json"]
            created = datetime.fromisoformat(metadata["created_at"])
            metadata["created_at"] = (created - timedelta(hours=3)).isoformat()

    base = _compare_dataset(organization, workspace, "Empty base", [("left", "a")])
    peer = _compare_dataset(organization, workspace, "Empty peer", [("right", "b")])
    before = json.loads(json.dumps(comparison_page_store))
    comparison_page_store.reads.clear()
    comparison_page_store.writes.clear()
    comparison_cell_reads.clear()

    response = _compare_request(
        compare_member_client,
        base,
        peer,
        compare_id=compare_id,
        current_page_index=current_page_index,
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    result = response.json()["result"]
    fresh_id = result["metadata"]["compare_id"]
    assert fresh_id != compare_id
    assert str(uuid.UUID(fresh_id)) == fresh_id
    assert result["metadata"] == {
        "compare_id": fresh_id,
        "total_rows": 0,
        "total_pages": 0,
    }
    assert not result.get("table")
    assert "Nonempty cached pair" not in _body(response)
    assert "prepare" not in comparison_cell_reads
    assert comparison_page_store == before
    assert comparison_page_store.keys_used_under(compare_id) == [
        f"compare/{compare_id}/metadata.json"
    ]


@pytest.mark.django_db(transaction=True)
def test_compare_ordered_peer_swap_mints_fresh_id_and_preserves_download_order(
    auth_client, comparison_page_store, comparison_cell_reads, organization, workspace
):
    base, peer_b, peer_c = [
        _compare_dataset(organization, workspace, name, [("alpha", name)])
        for name in ("Ordered base", "Ordered B", "Ordered C")
    ]

    def compare(peers, compare_id=None):
        return auth_client.post(
            f"/model-hub/datasets/{base.id}/compare-datasets/",
            {
                "dataset_ids": [str(peer.id) for peer in peers],
                "base_column_name": "input",
                "compare_id": compare_id,
                "page_size": 10,
            },
            format="json",
        )

    first = compare([peer_b, peer_c])
    assert first.status_code == status.HTTP_200_OK, first.content
    old_id = first.json()["result"]["metadata"]["compare_id"]
    old_objects = json.loads(json.dumps(comparison_page_store))
    comparison_page_store.reads.clear()
    comparison_page_store.writes.clear()
    comparison_cell_reads.clear()

    swapped = compare([peer_c, peer_b], old_id)
    assert swapped.status_code == status.HTTP_200_OK, swapped.content
    result = swapped.json()["result"]
    new_id = result["metadata"]["compare_id"]
    assert new_id != old_id
    assert comparison_cell_reads.count("prepare") == 1
    assert [col["dataset_id"] for col in result["column_config"][1:]] == [
        str(ds.id) for ds in (base, peer_c, peer_b)
    ]
    assert result["table"]
    for ds in (base, peer_c, peer_b):
        col_id = str(Column.objects.get(dataset=ds, name="judge").id)
        assert result["table"][0][col_id]["cell_value"] == ds.name
    assert comparison_page_store[f"compare/{new_id}/metadata.json"][
        "comparison_datasets"
    ] == [str(ds.id) for ds in (base, peer_c, peer_b)]
    assert comparison_page_store.keys_used_under(old_id) == [
        f"compare/{old_id}/metadata.json"
    ]
    assert all(comparison_page_store[key] == value for key, value in old_objects.items())

    # The old peer ordering is not an accepted download cache candidate.
    _assert_compare_not_found(
        _download(auth_client, base, [peer_c, peer_b], compare_id=old_id)
    )
    # Stored metadata includes common_columns.union({base_column_name}), so
    # cached exports also include each dataset's input. Pin the entire CSV,
    # including values and peer order, rather than narrowing that contract.
    assert set(
        comparison_page_store[f"compare/{new_id}/metadata.json"]["common_column_names"]
    ) == {"input", "judge"}
    cached_export = _download(
        auth_client, base, [peer_c, peer_b], compare_id=new_id
    )
    assert cached_export.status_code == status.HTTP_200_OK, _body(cached_export)
    assert list(csv.reader(StringIO(_body(cached_export)))) == [
        [
            "input",
            "judge-Ordered base",
            "input-Ordered base",
            "judge-Ordered C",
            "input-Ordered C",
            "judge-Ordered B",
            "input-Ordered B",
        ],
        [
            "alpha", "Ordered base", "alpha", "Ordered C", "alpha", "Ordered B", "alpha"
        ],
    ]

    # The rejected old ordering can only fall back to validated client rows.
    # _download(with_rows=True) supplies common_column_names=["judge"], so this
    # mode has a distinct, narrower projection, still ordered [base, C, B].
    client_export = _download(
        auth_client, base, [peer_c, peer_b], compare_id=old_id, with_rows=True
    )
    assert client_export.status_code == status.HTTP_200_OK, _body(client_export)
    assert list(csv.reader(StringIO(_body(client_export)))) == [
        ["input", "judge-Ordered base", "judge-Ordered C", "judge-Ordered B"],
        ["alpha", "Ordered base", "Ordered C", "Ordered B"],
    ]
    reused = compare([peer_c, peer_b], new_id)
    assert reused.status_code == status.HTTP_200_OK, reused.content
    assert reused.json()["result"]["metadata"]["compare_id"] == new_id
    assert comparison_cell_reads.count("prepare") == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("cache_state", ["unknown", "expired"])
@pytest.mark.parametrize("with_rows", [False, True])
def test_compare_download_cache_miss_envelope_and_validated_fallback(
    auth_client,
    comparison_page_store,
    organization,
    workspace,
    cache_state,
    with_rows,
):
    base, peer = _new_compare_pair(organization, workspace, "Download fallback")
    compare_id = str(uuid.uuid4())
    if cache_state == "expired":
        built = _compare_request(auth_client, base, peer)
        assert built.status_code == status.HTTP_200_OK, built.content
        compare_id = built.json()["result"]["metadata"]["compare_id"]
        metadata = comparison_page_store[f"compare/{compare_id}/metadata.json"]
        created = datetime.fromisoformat(metadata["created_at"])
        metadata["created_at"] = (created - timedelta(hours=3)).isoformat()
    before = json.loads(json.dumps(comparison_page_store))
    comparison_page_store.reads.clear()
    comparison_page_store.writes.clear()

    response = _download(
        auth_client, base, [peer], compare_id=compare_id, with_rows=with_rows
    )
    if with_rows:
        assert response.status_code == status.HTTP_200_OK, _body(response)
        assert _body(response).splitlines()[0].split(",") == [
            "input",
            f"judge-{base.name}",
            f"judge-{peer.name}",
        ]
        assert "Download fallback-1" in _body(response)
    else:
        _assert_compare_not_found(response)
    assert comparison_page_store == before
    assert comparison_page_store.keys_used_under(compare_id) == [
        f"compare/{compare_id}/metadata.json"
    ]
    if with_rows:
        # A cache miss never makes misattributed client row IDs acceptable,
        # including when the same ID had a valid comparison before expiry.
        peer_row_id = str(Row.objects.get(dataset=peer).id)
        invalid = auth_client.post(
            f"/model-hub/datasets/{base.id}/compare-datasets/download/",
            {
                "dataset_ids": [str(peer.id)],
                "base_column_name": "input",
                "compare_id": compare_id,
                "dataset_info": {
                    "alpha": {str(base.id): peer_row_id, str(peer.id): peer_row_id}
                },
                "common_column_names": ["judge"],
            },
            format="json",
        )
        assert invalid.status_code == status.HTTP_404_NOT_FOUND, invalid.content
        assert invalid.json()["code"] == "not_found"
        assert comparison_page_store == before
        assert comparison_page_store.writes == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("selection_failure", ["missing_base", "deleted_peer"])
@pytest.mark.parametrize("storage_state", ["missing", "unavailable"])
def test_compare_download_dataset_failure_precedes_cache_lookup(
    auth_client,
    comparison_page_store,
    organization,
    workspace,
    monkeypatch,
    selection_failure,
    storage_state,
):
    from model_hub.utils.compare_storage import CompareStorageUnavailable

    base, peer = _new_compare_pair(organization, workspace, "Invalid selection")
    if selection_failure == "missing_base":
        base.id = uuid.uuid4()
    else:
        Dataset.no_workspace_objects.filter(id=peer.id).update(deleted=True)
    unavailable = Mock(
        side_effect=(
            CompareObjectUnavailable("metadata.json")
            if storage_state == "missing"
            else CompareStorageUnavailable("metadata.json")
        )
    )
    monkeypatch.setattr("model_hub.views.develop_dataset.read_compare_json", unavailable)
    response = _download(auth_client, base, [peer], compare_id=str(uuid.uuid4()))
    assert response.status_code == status.HTTP_404_NOT_FOUND, response.content
    assert response.json()["code"] == "not_found"
    unavailable.assert_not_called()
    assert comparison_page_store.writes == []


@pytest.mark.django_db(transaction=True)
def test_compare_download_without_id_or_rows_is_still_bad_request(
    auth_client, compare_datasets
):
    response = _download(
        auth_client, compare_datasets["base_dataset"], [compare_datasets["other_dataset"]]
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert response.json()["code"] == "invalid"


@pytest.fixture
def comparison_cleanup_calls(monkeypatch, comparison_page_store):
    cleanup = Mock()
    monkeypatch.setattr("tfc.temporal.drop_in.start_activity", cleanup)
    monkeypatch.setattr(
        "model_hub.views.develop_dataset.delete_compare_metadata",
        lambda compare_id: comparison_page_store.pop(
            f"compare/{compare_id}/metadata.json", None
        ),
    )
    return cleanup


def _comparison_endpoint(client, endpoint, base, peer, compare_id, row_id):
    if endpoint == "compare":
        return _compare_request(client, base, peer, compare_id=compare_id)
    if endpoint == "download":
        return _download(client, base, [peer], compare_id=compare_id)
    if endpoint == "row":
        return client.get(f"/model-hub/datasets/get-compare-row/{compare_id}/{row_id}/")
    assert endpoint == "delete"
    return client.delete(f"/model-hub/datasets/delete-compare/{compare_id}/")


def _stored_first_row_id(store, compare_id):
    return store[f"compare/{compare_id}/page_1.json"]["table"][0]["row_id"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("endpoint", ["compare", "download", "row", "delete"])
@pytest.mark.parametrize(
    "access_path",
    [
        "default_member",
        "workspace_member",
        "bound_owner",
        "unbound_owner",
        "bound_admin",
        "unbound_admin",
        "legacy_default_member",
    ],
)
def test_compare_endpoints_allow_current_workspace_access(
    user,
    organization,
    workspace,
    comparison_page_store,
    comparison_cleanup_calls,
    endpoint,
    access_path,
):
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import Workspace
    from tfc.constants.levels import Level

    target = workspace
    if access_path == "workspace_member":
        target = Workspace.objects.create(
            name="Accessible team",
            organization=organization,
            created_by=user,
            is_default=False,
            is_active=True,
        )
    base, peer = _new_compare_pair(organization, target, "Accessible pair")
    if access_path == "legacy_default_member":
        Dataset.no_workspace_objects.filter(id__in=[base.id, peer.id]).update(
            workspace=None
        )
    compare_id = _store_comparison(user, base, peer)
    row_id = _stored_first_row_id(comparison_page_store, compare_id)
    if "owner" in access_path:
        caller = user
    else:
        member_client = _member_client(organization, target)
        caller = member_client.handler._force_user
        member_client.stop_workspace_injection()
        if "admin" in access_path:
            OrganizationMembership.no_workspace_objects.filter(
                user=caller, organization=organization
            ).update(level=Level.ADMIN)
    client = _client_without_workspace(caller)
    bound = None if access_path.startswith("unbound") else target
    with _request_binding(organization, bound):
        response = _comparison_endpoint(client, endpoint, base, peer, compare_id, row_id)
    assert response.status_code == status.HTTP_200_OK, _body(response)
    if endpoint == "delete":
        comparison_cleanup_calls.assert_called_once()
        assert f"compare/{compare_id}/metadata.json" not in comparison_page_store
    else:
        assert "Accessible pair-1" in _body(response)
        comparison_cleanup_calls.assert_not_called()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("endpoint", ["compare", "download", "row", "delete"])
@pytest.mark.parametrize(
    "scope",
    [
        "sibling_workspace",
        "other_organization",
        "unbound_member",
        "unbound_owner_other_org",
        "bound_unjoined_workspace",
        "mismatched_workspace_org",
    ],
)
def test_compare_endpoints_refuse_inaccessible_scope(
    user,
    organization,
    workspace,
    comparison_page_store,
    comparison_cleanup_calls,
    comparison_cell_reads,
    endpoint,
    scope,
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from accounts.models.workspace import Workspace

    member_client = _member_client(organization, workspace)
    caller = member_client.handler._force_user
    member_client.stop_workspace_injection()
    stored_org, stored_owner, target = organization, user, workspace
    bound = workspace
    if scope in (
        "other_organization", "unbound_owner_other_org", "mismatched_workspace_org"
    ):
        stored_org, stored_owner, target = _another_organization()
    elif scope in ("sibling_workspace", "bound_unjoined_workspace"):
        target = Workspace.objects.create(
            name="Inaccessible sibling",
            organization=organization,
            created_by=user,
            is_default=False,
            is_active=True,
        )
    if scope in ("bound_unjoined_workspace", "mismatched_workspace_org"):
        bound = target
    elif scope.startswith("unbound"):
        bound = None
    if scope == "unbound_owner_other_org":
        caller = user
    base, peer = _new_compare_pair(stored_org, target, "Inaccessible pair")
    compare_id = _store_comparison(stored_owner, base, peer)
    row_id = _stored_first_row_id(comparison_page_store, compare_id)
    before = json.loads(json.dumps(comparison_page_store))
    comparison_cell_reads.clear()
    comparison_page_store.writes.clear()
    client = _client_without_workspace(caller)
    with (
        _request_binding(organization, bound),
        CaptureQueriesContext(connection) as captured,
    ):
        response = _comparison_endpoint(client, endpoint, base, peer, compare_id, row_id)
    _assert_no_comparison_cells(response, comparison_cell_reads, captured)
    if endpoint in ("compare", "download"):
        # Selection/access failures must not trigger the download rebuild flow.
        assert response.json()["code"] == "not_found"
    else:
        _assert_compare_not_found(response)
    assert comparison_page_store == before
    assert comparison_page_store.writes == []
    comparison_cleanup_calls.assert_not_called()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("endpoint", ["compare", "download", "row", "delete"])
@pytest.mark.parametrize(
    "removed_membership", ["workspace", "organization", "unbound_owner", "unbound_admin"]
)
@pytest.mark.parametrize("removal", ["deactivate", "delete"])
def test_compare_endpoints_recheck_membership_after_comparison_build(
    user,
    organization,
    workspace,
    comparison_page_store,
    comparison_cleanup_calls,
    comparison_cell_reads,
    endpoint,
    removed_membership,
    removal,
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import WorkspaceMembership
    from tfc.constants.levels import Level

    base, peer = _new_compare_pair(organization, workspace, "Previously accessible")
    if removed_membership == "unbound_owner":
        caller = user
    else:
        member_client = _member_client(organization, workspace)
        caller = member_client.handler._force_user
        member_client.stop_workspace_injection()
        if removed_membership == "unbound_admin":
            OrganizationMembership.no_workspace_objects.filter(
                user=caller, organization=organization
            ).update(level=Level.ADMIN)
    client = _client_without_workspace(caller)
    bound = None if removed_membership.startswith("unbound") else workspace
    with _request_binding(organization, bound):
        built = _compare_request(client, base, peer)
        assert built.status_code == status.HTTP_200_OK, built.content
        compare_id = built.json()["result"]["metadata"]["compare_id"]
        row_id = _stored_first_row_id(comparison_page_store, compare_id)
        detail = _comparison_endpoint(client, "row", base, peer, compare_id, row_id)
        assert detail.status_code == status.HTTP_200_OK, detail.content
    before = json.loads(json.dumps(comparison_page_store))
    memberships = (
        WorkspaceMembership.no_workspace_objects.filter(user=caller, workspace=workspace)
        if removed_membership == "workspace"
        else OrganizationMembership.no_workspace_objects.filter(
            user=caller, organization=organization
        )
    )
    if removal == "delete":
        memberships.delete()
    else:
        assert memberships.update(is_active=False) == 1
    assert caller.organization_id == organization.id
    comparison_cell_reads.clear()
    comparison_page_store.reads.clear()
    comparison_page_store.writes.clear()
    # Same user object and stale bound workspace: current membership still wins.
    with (
        _request_binding(organization, bound),
        CaptureQueriesContext(connection) as captured,
    ):
        response = _comparison_endpoint(client, endpoint, base, peer, compare_id, row_id)
    _assert_no_comparison_cells(response, comparison_cell_reads, captured)
    assert response.json()["code"] == (
        "not_found" if endpoint in ("compare", "download") else "compare_not_found"
    )
    assert comparison_page_store == before
    assert comparison_page_store.reads == []
    assert comparison_page_store.writes == []
    comparison_cleanup_calls.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_compare_request_starts_the_api_sweeper(
    auth_client, compare_datasets, comparison_page_store, monkeypatch
):
    """The process writing a local marker also starts its expiry sweep."""
    sweeper = Mock()
    monkeypatch.setattr("model_hub.views.develop_dataset.start_local_sweeper", sweeper)
    response = _compare_request(
        auth_client, compare_datasets["base_dataset"], compare_datasets["other_dataset"]
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    compare_id = response.data["result"]["metadata"]["compare_id"]
    assert Path(get_compare_local_dir(compare_id), "metadata.json").exists()
    assert f"compare/{compare_id}/metadata.json" in comparison_page_store
    sweeper.assert_called_once_with()
