#!/usr/bin/env python3
"""Machine-check the CATALOG OpenAPI impact inventory.

The release documentation describes the structural Swagger diff from the
qualifier's immutable source base to the current checked-in contract.  This
module keeps that list executable: it follows transitive ``#/definitions``
references so operations changed only through a shared schema cannot disappear
from the human inventory.

It is a local/offline verifier.  It reads files and ``git show`` output only;
it has no database, cloud, container, or network behavior.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from safety import BASE_COMMIT

SWAGGER_RELATIVE_PATH = Path("api_contracts/openapi/swagger.json")
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})

EXPECTED_DIRECT_OPERATION_COUNT = 32
EXPECTED_TRANSITIVE_ONLY_OPERATION_COUNT = 33
EXPECTED_CHANGED_DEFINITION_COUNT = 48

EXPECTED_OPERATION_GROUPS: dict[str, tuple[tuple[str, str], ...]] = {
    "property_registry": (
        ("GET", "/api/traces/span-attribute-keys/"),
        ("GET", "/tracer/dashboard/metrics/"),
        ("GET", "/tracer/dashboard/filter_values/"),
    ),
    "observe_lists": (
        ("GET", "/tracer/trace/list_traces/"),
        ("GET", "/tracer/trace/list_traces_of_session/"),
        ("GET", "/tracer/trace/list_voice_calls/"),
        ("GET", "/tracer/observation-span/list_spans/"),
        ("GET", "/tracer/observation-span/list_spans_observe/"),
        ("GET", "/tracer/trace-session/list_sessions/"),
    ),
    "graphs_and_dashboard": (
        ("POST", "/tracer/trace/get_graph_methods/"),
        ("POST", "/tracer/observation-span/get_graph_methods/"),
        ("POST", "/tracer/trace-session/get_session_graph_data/"),
        ("POST", "/tracer/project/get_user_metrics/"),
        ("POST", "/tracer/project/get_users_aggregate_graph_data/"),
        ("POST", "/tracer/project/get_user_graph_data/"),
        ("POST", "/tracer/dashboard/query/"),
        ("POST", "/tracer/dashboard/{dashboard_pk}/widgets/preview/"),
        ("GET", "/tracer/charts/fetch_graph/"),
    ),
    "eval_task": (
        ("GET", "/tracer/eval-task/"),
        ("POST", "/tracer/eval-task/"),
        ("GET", "/tracer/eval-task/{id}/"),
        ("PUT", "/tracer/eval-task/{id}/"),
        ("PATCH", "/tracer/eval-task/{id}/"),
        ("GET", "/tracer/eval-task/get_eval_details/"),
        ("GET", "/tracer/eval-task/get_eval_task_logs/"),
        ("GET", "/tracer/eval-task/get_usage/"),
        ("GET", "/tracer/eval-task/list_eval_tasks/"),
        ("GET", "/tracer/eval-task/list_eval_tasks_with_project_name/"),
        ("PATCH", "/tracer/eval-task/update_eval_task/"),
    ),
    "annotation_automation": (
        (
            "GET",
            "/model-hub/annotation-queues/{queue_id}/automation-rules/",
        ),
        (
            "POST",
            "/model-hub/annotation-queues/{queue_id}/automation-rules/",
        ),
        (
            "GET",
            "/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/",
        ),
        (
            "PUT",
            "/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/",
        ),
        (
            "PATCH",
            "/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/",
        ),
        ("POST", "/model-hub/annotation-queues/{queue_id}/items/add-items/"),
    ),
    "ai_filter": (("POST", "/model-hub/ai-filter/"),),
    "dataset": (
        ("GET", "/model-hub/develops/{dataset_id}/get-dataset-table/"),
        ("POST", "/model-hub/develops/{dataset_id}/get-row-data/"),
        (
            "GET",
            "/model-hub/develops/{experiment_dataset_id}/get-experiment-dataset-table/",
        ),
    ),
    "experiment_rows": (
        ("GET", "/model-hub/experiments/v2/{experiment_id}/rows/"),
        ("GET", "/model-hub/experiments/v2/{experiment_id}/rows/{row_id}/"),
        ("GET", "/model-hub/experiments/{experiment_id}/"),
        ("GET", "/model-hub/experiments/{experiment_id}/{row_id}/"),
    ),
    "simulation": (
        ("GET", "/simulate/api/run-tests/"),
        ("GET", "/simulate/prompt-templates/{prompt_template_id}/simulations/"),
        ("POST", "/simulate/prompt-templates/{prompt_template_id}/simulations/"),
        (
            "GET",
            "/simulate/prompt-templates/{prompt_template_id}/simulations/{run_test_id}/",
        ),
        (
            "PATCH",
            "/simulate/prompt-templates/{prompt_template_id}/simulations/{run_test_id}/",
        ),
        ("GET", "/simulate/run-tests/"),
        ("POST", "/simulate/run-tests/create/"),
        ("GET", "/simulate/run-tests/{run_test_id}/"),
        ("PATCH", "/simulate/run-tests/{run_test_id}/"),
        ("PATCH", "/simulate/run-tests/{run_test_id}/components/"),
        ("POST", "/simulate/run-tests/{run_test_id}/eval-configs/"),
        (
            "POST",
            "/simulate/run-tests/{run_test_id}/eval-configs/{eval_config_id}/update/",
        ),
        ("GET", "/simulate/run-tests/{run_test_id}/preview-executions/"),
        (
            "GET",
            "/simulate/test-executions/{test_execution_id}/preview-calls/",
        ),
    ),
    "prompt_eval_logs_and_metrics": (
        ("GET", "/model-hub/get-eval-logs-details"),
        ("GET", "/model-hub/get-eval-metrics"),
        ("POST", "/model-hub/get-eval-metrics"),
        ("GET", "/model-hub/prompt/metrics/"),
        ("GET", "/model-hub/prompt/span-metrics/"),
    ),
    "eval_playground": (("POST", "/model-hub/eval-playground/"),),
    "alert_graphs": (
        ("POST", "/tracer/user-alerts/preview-graph/"),
        ("GET", "/tracer/user-alerts/{id}/graph/"),
    ),
}


def expected_operations() -> frozenset[tuple[str, str]]:
    operations = [
        operation
        for group_operations in EXPECTED_OPERATION_GROUPS.values()
        for operation in group_operations
    ]
    if len(operations) != len(set(operations)):
        raise AssertionError("CATALOG OpenAPI inventory contains a duplicate operation")
    return frozenset(operations)


def _definition_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if (
                key == "$ref"
                and isinstance(child, str)
                and child.startswith("#/definitions/")
            ):
                refs.add(child.removeprefix("#/definitions/"))
            refs.update(_definition_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.update(_definition_refs(child))
    return refs


def _transitive_definition_refs(
    roots: set[str], definitions: dict[str, Any]
) -> set[str]:
    pending = list(roots)
    resolved: set[str] = set()
    while pending:
        name = pending.pop()
        if name in resolved:
            continue
        resolved.add(name)
        pending.extend(_definition_refs(definitions.get(name, {})) - resolved)
    return resolved


def compute_inventory(base: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    base_definitions = base.get("definitions") or {}
    current_definitions = current.get("definitions") or {}
    changed_definitions = {
        name
        for name in set(base_definitions) | set(current_definitions)
        if base_definitions.get(name) != current_definitions.get(name)
    }

    direct: set[tuple[str, str]] = set()
    transitive_only: set[tuple[str, str]] = set()
    for path, path_spec in (current.get("paths") or {}).items():
        for method, operation in path_spec.items():
            if method not in HTTP_METHODS:
                continue
            identity = (method.upper(), path)
            is_direct = (base.get("paths") or {}).get(path, {}).get(method) != operation
            roots = _definition_refs(operation)
            inherits_change = bool(
                _transitive_definition_refs(roots, current_definitions)
                & changed_definitions
            )
            if is_direct:
                direct.add(identity)
            elif inherits_change:
                transitive_only.add(identity)

    return {
        "base_commit": BASE_COMMIT,
        "changed_definitions": frozenset(changed_definitions),
        "direct_operations": frozenset(direct),
        "transitive_only_operations": frozenset(transitive_only),
        "impacted_operations": frozenset(direct | transitive_only),
    }


def verify_repo_inventory(repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    current = json.loads(
        (repo_root / SWAGGER_RELATIVE_PATH).read_text(encoding="utf-8")
    )
    base = json.loads(
        subprocess.check_output(
            [
                "git",
                "-C",
                str(repo_root),
                "show",
                f"{BASE_COMMIT}:{SWAGGER_RELATIVE_PATH.as_posix()}",
            ],
            text=True,
        )
    )
    inventory = compute_inventory(base, current)
    expected = expected_operations()

    errors = []
    actual = inventory["impacted_operations"]
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        errors.append(f"missing={missing!r}; unexpected={unexpected!r}")
    counts = {
        "direct": len(inventory["direct_operations"]),
        "transitive_only": len(inventory["transitive_only_operations"]),
        "changed_definitions": len(inventory["changed_definitions"]),
    }
    expected_counts = {
        "direct": EXPECTED_DIRECT_OPERATION_COUNT,
        "transitive_only": EXPECTED_TRANSITIVE_ONLY_OPERATION_COUNT,
        "changed_definitions": EXPECTED_CHANGED_DEFINITION_COUNT,
    }
    if counts != expected_counts:
        errors.append(f"counts={counts!r}; expected_counts={expected_counts!r}")
    if errors:
        raise AssertionError("CATALOG OpenAPI inventory drifted: " + " | ".join(errors))
    return inventory


def main() -> int:
    repo_root = Path(__file__).resolve().parents[3]
    inventory = verify_repo_inventory(repo_root)
    payload = {
        "base_commit": inventory["base_commit"],
        "changed_definition_count": len(inventory["changed_definitions"]),
        "direct_operation_count": len(inventory["direct_operations"]),
        "transitive_only_operation_count": len(inventory["transitive_only_operations"]),
        "impacted_operation_count": len(inventory["impacted_operations"]),
        "groups": {
            group: [f"{method} {path}" for method, path in operations]
            for group, operations in EXPECTED_OPERATION_GROUPS.items()
        },
    }
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
