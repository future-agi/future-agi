"""Existing Python columns cannot be repurposed through a non-code rerun.

Exercise the registered route, authenticated/scoped lookups and real writes;
only task enqueueing is recorded. No provider or code execution is enabled.
"""

import uuid

import pytest
from django.test import TestCase

from model_hub.models.develop_dataset import Column
from model_hub.tests import test_dataset_code_columns_disabled as disabled_tests
from model_hub.tests.test_dataset_code_columns_disabled import (
    DISABLED,
    HARMLESS_CONDITIONAL,
    _assert_disabled,
    _Recorder,
    _state,
)

# Reuse the isolated two-tenant fixture without collecting its tests twice.
r566 = disabled_tests.r566

OPERATIONS = (
    "classify",
    "extract_entities",
    "extract_json",
    "conditional",
    "vector_db",
    "api_call",
)


def _config(tenant, operation):
    source = str(tenant["question"].id)
    return {
        "classify": {"column_id": source, "labels": ["yes", "no"]},
        "extract_entities": {"column_id": source, "instruction": "Find names"},
        "extract_json": {"column_id": source, "json_key": "answer"},
        "conditional": {"config": HARMLESS_CONDITIONAL},
        "vector_db": {"column_id": source, "sub_type": "qdrant"},
        "api_call": {
            "url": "http://127.0.0.1:9/fixture",
            "method": "GET",
            "output_type": "text",
        },
    }[operation]


@pytest.fixture
def all_queues(r566, monkeypatch):
    from model_hub.views import dynamic_columns

    queues = list(r566["queued"].values())
    for name in (
        "classify_column_async",
        "extract_async",
        "extract_json_async",
        "add_vector_db_column_async",
        "add_api_column_async",
    ):
        recorder = _Recorder()
        monkeypatch.setattr(getattr(dynamic_columns, name), "delay", recorder)
        queues.append(recorder)
    return queues


@pytest.mark.django_db
@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("stored", [False, True], ids=["edit-config", "stored-config"])
def test_noncode_rerun_cannot_clear_existing_python_column(
    r566, all_queues, operation, stored
):
    a = r566["a"]
    config = _config(a, operation)
    body = {"operation_type": operation}
    if stored:
        Column.all_objects.filter(id=a["python"].id).update(metadata=config)
    else:
        body["config"] = config
    before = _state(a)

    response = r566["clients"]["a"].post(
        f"/model-hub/columns/{a['python'].id}/rerun-operation/", body, format="json"
    )

    _assert_disabled(response)
    assert _state(a) == before
    assert all(not queue.calls for queue in all_queues)
    # The read API still exposes the preserved values and status.
    table = r566["clients"]["a"].get(
        f"/model-hub/develops/{a['dataset'].id}/get-dataset-table/",
        {"page_size": 10, "current_page_index": 0},
    )
    assert table.status_code == 200, table.content
    cells = [
        row[str(a["python"].id)]
        for row in sorted(table.json()["result"]["table"], key=lambda row: row["order"])
    ]
    assert [cell["cell_value"] for cell in cells] == [f"a-py_out-{i}" for i in range(3)]
    assert [cell["status"] for cell in cells] == ["pass"] * 3


@pytest.mark.django_db
@pytest.mark.parametrize("operation", OPERATIONS)
def test_noncode_rerun_on_other_column_still_dispatches(r566, all_queues, operation):
    a = r566["a"]
    # Reruns enqueue only after their locked state changes commit.
    with TestCase.captureOnCommitCallbacks(execute=True):
        response = r566["clients"]["a"].post(
            f"/model-hub/columns/{a['question'].id}/rerun-operation/",
            {"operation_type": operation, "config": _config(a, operation)},
            format="json",
        )
        assert all(not queue.calls for queue in all_queues)
    assert response.status_code == 200, response.content
    assert sum(len(queue.calls) for queue in all_queues) == 1


@pytest.mark.django_db
@pytest.mark.parametrize("operation", OPERATIONS)
def test_noncode_rerun_keeps_auth_scope_and_validation(r566, all_queues, operation):
    from conftest import WorkspaceAwareAPIClient

    a, b = r566["a"], r566["b"]
    before_a, before_b = _state(a), _state(b)
    body = {"operation_type": operation, "config": _config(a, operation)}
    url = f"/model-hub/columns/{a['python'].id}/rerun-operation/"
    for target in (url, f"/model-hub/columns/{uuid.uuid4()}/rerun-operation/"):
        response = r566["clients"]["b"].post(target, body, format="json")
        assert response.status_code == 404, response.content
        assert DISABLED not in response.content.decode()
    response = WorkspaceAwareAPIClient().post(url, body, format="json")
    assert response.status_code in (401, 403)
    response = r566["clients"]["a"].post(
        url, {**body, "operationType": "not-an-alias"}, format="json"
    )
    assert response.status_code == 400
    assert response.json()["details"]["operationType"] == ["Unknown field."]
    assert all(not queue.calls for queue in all_queues)
    assert _state(a) == before_a
    assert _state(b) == before_b
