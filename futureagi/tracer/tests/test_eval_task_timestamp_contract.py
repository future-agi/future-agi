"""Lifecycle timestamps are output, not task configuration, on every surface."""

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from django.utils import timezone

from mcp_server.generated_registry import GeneratedToolRegistry
from tracer.models.eval_task import EvalTask, RunType
from tracer.serializers.eval_task import EvalTaskSerializer

TIMESTAMPS = ("start_time", "end_time")


@pytest.mark.parametrize("field_name", TIMESTAMPS)
def test_serializer_lifecycle_timestamps_are_read_only(field_name):
    assert EvalTaskSerializer().fields[field_name].read_only


@pytest.mark.parametrize("field_name", TIMESTAMPS)
def test_openapi_lifecycle_timestamps_remain_read_only_response_fields(field_name):
    schema_path = (
        Path(__file__).resolve().parents[3]
        / "api_contracts"
        / "openapi"
        / "swagger.json"
    )
    schema = json.loads(schema_path.read_text())
    field = schema["definitions"]["EvalTask"]["properties"][field_name]
    assert field["readOnly"] is True
    assert field["type"] == "string"
    assert field["format"] == "date-time"
    assert field["x-nullable"] is True


@pytest.mark.parametrize("tool_name", ("create_eval_task", "update_eval_task"))
def test_mcp_task_inputs_do_not_advertise_lifecycle_timestamps(tool_name):
    tool = GeneratedToolRegistry.from_manifest().get(tool_name)
    assert not set(TIMESTAMPS).intersection(tool.input_schema["properties"])
    assert not set(TIMESTAMPS).intersection(tool.request["parameters"]["body"])


def _payload(project, config, run_type):
    return {
        "project": str(project.pk),
        "evals": [str(config.pk)],
        "name": "Synthetic timestamp contract",
        "run_type": run_type,
        "spans_limit": 10,
        "sampling_rate": 100,
    }


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
@pytest.mark.parametrize("run_type", (RunType.CONTINUOUS, RunType.HISTORICAL))
@pytest.mark.parametrize("client_time", ("2000-01-01T00:00:00Z", "not a timestamp"))
def test_create_ignores_client_lifecycle_timestamps(
    auth_client, project, custom_eval_config, monkeypatch, run_type, client_time
):
    starter = Mock()
    monkeypatch.setattr("tracer.views.eval_task.start_eval_task_workflow_sync", starter)
    response = auth_client.post(
        "/tracer/eval-task/",
        {
            **_payload(project, custom_eval_config, run_type),
            "start_time": client_time,
            "end_time": client_time,
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    task = EvalTask.objects.get(pk=response.json()["result"]["id"])
    assert task.start_time is None
    assert task.end_time is None
    starter.assert_called_once()


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
@pytest.mark.parametrize("run_type", (RunType.CONTINUOUS, RunType.HISTORICAL))
@pytest.mark.parametrize("method", ("put", "patch"))
def test_detail_edits_preserve_server_lifecycle_timestamps(
    auth_client, eval_task, custom_eval_config, run_type, method
):
    start = timezone.now() - timedelta(hours=2)
    end = start + timedelta(hours=1)
    EvalTask.objects.filter(pk=eval_task.pk).update(
        run_type=run_type,
        start_time=start,
        end_time=end,
        continuous_cursor=start,
    )
    response = getattr(auth_client, method)(
        f"/tracer/eval-task/{eval_task.pk}/",
        {
            **_payload(eval_task.project, custom_eval_config, run_type),
            "name": "Renamed timestamp contract",
            "start_time": "2000-01-01T00:00:00Z",
            "end_time": None,
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    eval_task.refresh_from_db()
    assert eval_task.name == "Renamed timestamp contract"
    assert eval_task.start_time == start
    assert eval_task.end_time == end
    serializer = EvalTaskSerializer(eval_task)
    assert response.json()["start_time"] == serializer.data["start_time"]
    assert response.json()["end_time"] == serializer.data["end_time"]
