"""Typed response contracts for project/user graph endpoints."""

import json
from pathlib import Path

from tracer.serializers.filters import ObserveGraphDataResponseSerializer
from tracer.serializers.project import (
    ProjectGraphDataResponseSerializer,
    ProjectUserGraphDataResponseSerializer,
)
from tracer.views.project import ProjectView


def _repo_root():
    return Path(__file__).resolve().parents[3]


def _swagger():
    with (_repo_root() / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def test_project_graph_response_accepts_runtime_envelope():
    serializer = ProjectGraphDataResponseSerializer(
        data={
            "status": True,
            "result": {
                "system_metrics": {
                    "latency": [
                        {
                            "timestamp": "2026-08-11T00:00:00Z",
                            "value": 42.5,
                            "latency": 42.5,
                        }
                    ],
                    "tokens": [],
                    "cost": [],
                    "traffic": [],
                    "query_complete": True,
                    "query_status": "complete",
                    "query_sampled": False,
                },
                "evaluations": {},
            },
        }
    )

    assert serializer.is_valid(), serializer.errors


def test_project_user_detail_graph_accepts_runtime_envelope():
    serializer = ProjectUserGraphDataResponseSerializer(
        data={
            "status": True,
            "result": {
                "session": [{"timestamp": "2026-08-11T00:00:00Z", "session": 2}],
                "trace": [{"timestamp": "2026-08-11T00:00:00Z", "trace": 7}],
                "cost": [{"timestamp": "2026-08-11T00:00:00Z", "cost": 0.2}],
                "input_tokens": [
                    {"timestamp": "2026-08-11T00:00:00Z", "input_tokens": 11}
                ],
                "output_tokens": [
                    {"timestamp": "2026-08-11T00:00:00Z", "output_tokens": 5}
                ],
            },
        }
    )

    assert serializer.is_valid(), serializer.errors


def test_project_graph_decorators_use_runtime_response_contracts():
    assert ProjectView.get_graph_data._swagger_auto_schema["get"]["responses"][200] is (
        ProjectGraphDataResponseSerializer
    )
    assert (
        ProjectView.get_users_aggregate_graph_data._swagger_auto_schema["post"][
            "responses"
        ][200]
        is ObserveGraphDataResponseSerializer
    )
    assert (
        ProjectView.get_user_graph_data._swagger_auto_schema["post"]["responses"][200]
        is ProjectUserGraphDataResponseSerializer
    )


def test_checked_in_swagger_has_project_graph_response_contracts():
    swagger = _swagger()
    expected = {
        "/tracer/project/get_graph_data/": ("get", "ProjectGraphDataResponse"),
        "/tracer/project/get_users_aggregate_graph_data/": (
            "post",
            "ObserveGraphDataResponse",
        ),
        "/tracer/project/get_user_graph_data/": (
            "post",
            "ProjectUserGraphDataResponse",
        ),
    }

    for path, (method, definition) in expected.items():
        operation = swagger["paths"][path][method]
        assert operation["responses"]["200"]["schema"]["$ref"] == (
            f"#/definitions/{definition}"
        )
        assert operation["x-runtime-response-validation"] is True
