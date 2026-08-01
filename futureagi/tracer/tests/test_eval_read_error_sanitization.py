import json
import uuid
from unittest.mock import MagicMock

import pytest
from rest_framework import status

from model_hub.utils import eval_list
from model_hub.views import separate_evals
from tracer.views import eval_task as eval_task_views

PRIVATE_ERROR = "Code: 159. DB::Exception: private ClickHouse stack trace"


def _raise_private_error(*args, **kwargs):
    raise RuntimeError(PRIVATE_ERROR)


def _assert_public_query_error(response, message):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["code"] == "query_failed"
    assert response.data["message"] == message
    assert PRIVATE_ERROR not in json.dumps(response.data)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("path", "params", "message"),
    [
        (
            "/tracer/eval-task/list_eval_tasks/",
            {},
            "Evaluation tasks could not be loaded. Please try again.",
        ),
        (
            "/tracer/eval-task/list_eval_tasks_with_project_name/",
            {},
            "Evaluation tasks could not be loaded. Please try again.",
        ),
        (
            "/tracer/eval-task/get_eval_task_logs/",
            {"eval_task_id": str(uuid.uuid4())},
            "Evaluation task logs could not be loaded. Please try again.",
        ),
        (
            "/tracer/eval-task/get_usage/",
            {"eval_task_id": str(uuid.uuid4())},
            "Evaluation task usage could not be loaded. Please try again.",
        ),
        (
            "/tracer/eval-task/get_eval_details/",
            {"eval_id": str(uuid.uuid4())},
            "Evaluation task details could not be loaded. Please try again.",
        ),
    ],
)
def test_eval_task_read_endpoints_do_not_leak_private_query_errors(
    auth_client,
    monkeypatch,
    path,
    params,
    message,
):
    monkeypatch.setattr(
        eval_task_views.EvalTaskView,
        "_scope_eval_task_queryset",
        _raise_private_error,
    )

    response = auth_client.get(path, params)

    _assert_public_query_error(response, message)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("path", "payload", "message"),
    [
        (
            "/model-hub/get-eval-template-names",
            {},
            "Evaluation template names could not be loaded. Please try again.",
        ),
        (
            "/model-hub/eval-templates/list/",
            {},
            "Evaluation templates could not be loaded. Please try again.",
        ),
    ],
)
def test_eval_catalog_list_endpoints_do_not_leak_private_query_errors(
    auth_client,
    monkeypatch,
    path,
    payload,
    message,
):
    monkeypatch.setattr(eval_list, "build_eval_list_queryset", _raise_private_error)

    response = auth_client.post(path, payload, format="json")

    _assert_public_query_error(response, message)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("suffix", "message"),
    [
        (
            "detail/",
            "Evaluation template details could not be loaded. Please try again.",
        ),
        (
            "versions/",
            "Evaluation template versions could not be loaded. Please try again.",
        ),
    ],
)
def test_eval_settings_get_endpoints_do_not_leak_private_query_errors(
    auth_client,
    monkeypatch,
    suffix,
    message,
):
    monkeypatch.setattr(
        separate_evals.EvalTemplate.no_workspace_objects,
        "get",
        MagicMock(side_effect=RuntimeError(PRIVATE_ERROR)),
    )
    template_id = uuid.uuid4()

    response = auth_client.get(f"/model-hub/eval-templates/{template_id}/{suffix}")

    _assert_public_query_error(response, message)
