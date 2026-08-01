"""Focused wire-contract tests for project and eval-task list endpoints."""

import json
from pathlib import Path

from tracer.serializers.eval_task import EvalTaskListResponseSerializer
from tracer.serializers.project import ProjectListResponseSerializer
from tracer.utils.helper import get_default_eval_task_config


def _swagger():
    repo_root = Path(__file__).resolve().parents[3]
    with (repo_root / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def test_project_list_contract_accepts_actual_envelope():
    payload = {
        "status": True,
        "result": {
            "metadata": {
                "total_rows": 1,
                "page_number": 0,
                "page_size": 20,
                "total_pages": 1,
            },
            "table": [
                {
                    "id": "11111111-1111-4111-8111-111111111111",
                    "name": "Synthetic traces",
                    "last_30_days_vol": 42,
                    "daily_volume": [1, 2, 3],
                    "created_at": "2026-07-30T10:00:00Z",
                    "updated_at": "2026-07-30T11:00:00Z",
                    "last_active": None,
                    "run_count": 2,
                    "issues": 0,
                    "tags": ["synthetic"],
                }
            ],
        },
    }

    serializer = ProjectListResponseSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors


def test_eval_task_list_contract_accepts_actual_envelope():
    payload = {
        "status": True,
        "result": {
            "metadata": {"total_rows": 1},
            "table": [
                {
                    "id": "22222222-2222-4222-8222-222222222222",
                    "name": "Synthetic attribute task",
                    "status": "running",
                    "run_type": "historical",
                    "filters_applied": {
                        "filters": [
                            {
                                "column_id": "prompt_slug",
                                "filter_config": {
                                    "filter_type": "text",
                                    "filter_op": "equals",
                                    "filter_value": "synthetic_prompt_v2",
                                },
                            }
                        ]
                    },
                    "created_at": "2026-07-30T10:00:00Z",
                    "evals_applied": ["Synthetic evaluator"],
                    "sampling_rate": 100.0,
                    "last_run": "2026-07-30T10:01:00Z",
                }
            ],
            "config": get_default_eval_task_config(),
        },
    }

    serializer = EvalTaskListResponseSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors


def test_openapi_wires_actual_list_response_envelopes():
    swagger = _swagger()
    expected = {
        "/tracer/project/list_projects/": "ProjectListResponse",
        "/tracer/eval-task/list_eval_tasks/": "EvalTaskListResponse",
    }
    for path, definition in expected.items():
        response_schema = swagger["paths"][path]["get"]["responses"]["200"]["schema"]
        assert response_schema["$ref"].rsplit("/", 1)[-1] == definition
