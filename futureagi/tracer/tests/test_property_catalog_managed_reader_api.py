"""Execute the checked-in metrics handler with real readers and fake transports.

AST loading isolates the handler from unrelated Django model/app startup; it
does not replace the handler body, selector, qualification checks, or reader.
The existing full API suites continue covering framework/auth middleware.
"""

import ast
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from tfc.settings.settings import (
    property_catalog_managed_oss_reads,
    property_catalog_read_workspace_allowlist,
    property_catalog_reads_all_workspaces,
)
from tracer.services.clickhouse.v2.property_catalog.activation_control import (
    ActivationControlAction,
    ActivationControlEvent,
    ActivationControlTarget,
    activation_control_selector_for_deployment,
    initial_follow_request_id,
)
from tracer.services.clickhouse.v2.property_catalog.cursor import (
    PropertyCatalogCursorError,
)
from tracer.services.clickhouse.v2.property_catalog.reader import (
    PropertyCatalogBootstrapPending,
    PropertyCatalogReader,
    PropertyCatalogUnavailable,
    is_property_catalog_not_ready_error,
)
from tracer.tests.test_property_catalog_managed_reader_admission import (
    Executor,
    settings_for_oss,
)
from tracer.tests.test_unified_property_catalog_reader import (
    ORG_ID,
    PROJECT_ID,
    WORKSPACE_ID,
    _activation_row,
    _conflict_row,
    _scope,
)


class Response(dict):
    def __init__(self, payload, *, status=200):
        super().__init__()
        self.data, self.status_code = payload, status


def handler(executor, *, authorize=lambda *args, **kwargs: [PROJECT_ID]):
    source = Path(__file__).resolve().parents[1] / "views/dashboard.py"
    module = ast.parse(source.read_text())
    gate = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "_property_catalog_read_enabled_for_workspace")
    view = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "DashboardViewSet")
    method = next(node for node in view.body if isinstance(node, ast.FunctionDef) and node.name == "metrics")
    method.decorator_list = []
    deadline = SimpleNamespace(remaining_ms=lambda **kwargs: 1000)
    namespace = {
        "settings": settings_for_oss(),
        "property_catalog_managed_oss_reads": property_catalog_managed_oss_reads,
        "property_catalog_read_workspace_allowlist": property_catalog_read_workspace_allowlist,
        "property_catalog_reads_all_workspaces": property_catalog_reads_all_workspaces,
        "ReadDeadline": SimpleNamespace(start=lambda _: deadline), "METRICS_CATALOG_TIMEOUT_MS": 1000,
        "resolve_property_catalog_project_scope": authorize,
        "resolve_property_catalog_agent_scope": lambda *args, **kwargs: "",
        "cursor_scope_for_request": lambda *args, **kwargs: _scope(project_ids=kwargs["project_ids"]),
        "PropertyCatalogReadExecutor": lambda **kwargs: executor,
        "PropertyCatalogReader": PropertyCatalogReader,
        "activation_control_selector_for_deployment": activation_control_selector_for_deployment,
        "PropertyCatalogCursorError": PropertyCatalogCursorError,
        "PropertyCatalogBootstrapPending": PropertyCatalogBootstrapPending,
        "PropertyCatalogUnavailable": PropertyCatalogUnavailable,
        "is_property_catalog_not_ready_error": is_property_catalog_not_ready_error,
        "ReadDeadlineExceeded": type("ReadDeadlineExceeded", (Exception,), {}),
        "DatabaseError": type("DatabaseError", (Exception,), {}),
        "MetricsCatalogUnavailable": type("MetricsCatalogUnavailable", (Exception,), {}),
        "logger": Mock(),
        "status": SimpleNamespace(HTTP_400_BAD_REQUEST=400, HTTP_503_SERVICE_UNAVAILABLE=503, HTTP_500_INTERNAL_SERVER_ERROR=500),
    }
    exec(compile(ast.Module(body=[gate, method], type_ignores=[]), str(source), "exec"), namespace)
    gm = SimpleNamespace(
        success_response=lambda payload: Response(payload),
        custom_error_response=lambda status, message, **kwargs: Response(kwargs, status=status),
        bad_request=lambda message: Response({"message": message}, status=400),
    )
    request = SimpleNamespace(
        workspace=SimpleNamespace(id=WORKSPACE_ID),
        validated_query_data={"cursor_mode": True, "project_ids": [PROJECT_ID], "page_size": 50},
    )
    return lambda: namespace["metrics"](SimpleNamespace(_gm=gm), request)


def event(row, *, sequence=1, action=ActivationControlAction.ACTIVATE, previous="0" * 64):
    return ActivationControlEvent.create(
        control_sequence=sequence, request_id=str(UUID(int=sequence)), action=action,
        target=ActivationControlTarget(
            organization_id=ORG_ID, workspace_id=WORKSPACE_ID,
            catalog_epoch=row["catalog_epoch"], projection_version=row["projection_version"],
            catalog_revision=row["catalog_revision"], build_token=row["build_token"],
            activation_sha256=row["activation_sha256"],
        ), previous_control_sha256=previous, controlled_at=datetime(2026, 9, 5, tzinfo=UTC),
    )


def test_new_empty_workspace_returns_pending_without_invented_activation():
    executor = Executor([[], [], []])
    result = handler(executor, authorize=lambda *args, **kwargs: [])()
    assert result.status_code == 200
    assert result.data["metrics"] == [] and result.data["query_status"] == "pending"
    assert result.data["query_complete"] is False and result.data["query_exact"] is False
    assert result["Retry-After"] == "5"
    assert not {"catalog_epoch", "catalog_revision", "activation_fingerprint"} & result.data.keys()
    assert result.data["next_cursor"] is None


def test_new_project_waits_for_coverage_without_invalidating_selected_catalog():
    row = _activation_row(covered_project_ids=())
    followed = event(row, action=ActivationControlAction.FOLLOW)
    qualified = {**asdict(followed.target), "activation_sequence": 1, "latest_variants": 1}
    executor = Executor([[followed.as_row()], [qualified], [row]])
    result = handler(executor)()
    assert result.status_code == 200 and result.data["query_status"] == "pending"
    assert result.data["query_complete"] is False
    assert result["Retry-After"] == "5"
    assert not {"catalog_epoch", "catalog_revision", "activation_fingerprint"} & result.data.keys()
    assert len(executor.calls) == 3


@pytest.mark.parametrize("action", [ActivationControlAction.ACTIVATE, ActivationControlAction.ROLLBACK])
def test_new_project_outside_explicit_pinned_catalog_does_not_poll_forever(action):
    row = _activation_row(covered_project_ids=())
    executor = Executor([[event(row, action=action).as_row()], [row]])
    assert handler(executor)().status_code == 503
    assert len(executor.calls) == 2


@pytest.mark.parametrize("projection", [1, 3])
def test_new_qualified_empty_result_binds_actual_identity_not_legacy_settings(projection):
    row = _activation_row(catalog_epoch=19, projection_version=projection)
    metadata = _conflict_row(catalog_metadata_only=1, catalog_count_all=0, catalog_count_custom_attribute=0)
    executor = Executor([[event(row).as_row()], [row], [metadata]])
    result = handler(executor)()
    assert result.status_code == 200
    assert result.data["query_complete"] is True and result.data["query_status"] == "complete"
    assert result.data["catalog_epoch"] == 19 and result.data["catalog_revision"] == 17
    assert executor.calls[1][1]["catalog_epoch"] == 19


@pytest.mark.parametrize("failure", ["disable", "digest", "unselected_build"])
def test_explicit_disable_corruption_and_unselected_build_never_bootstrap(failure):
    row = _activation_row()
    first = event(row)
    rows = [first.as_row()]
    if failure == "disable":
        rows.append(event(row, sequence=2, action=ActivationControlAction.DISABLE, previous=first.control_sha256).as_row())
    elif failure == "digest":
        rows[0]["control_sha256"] = "f" * 64
    executor = Executor([[], [{"catalog_history_exists": 1}], []] if failure == "unselected_build" else [rows])
    result = handler(executor)()
    assert result.status_code == 503
    assert result.data["code"] == "service_unavailable"


def test_explicit_rollback_reads_older_exact_target():
    older = _activation_row()
    newer = _activation_row(catalog_revision=18)
    first = event(newer)
    rollback = event(older, sequence=2, action=ActivationControlAction.ROLLBACK, previous=first.control_sha256)
    metadata = _conflict_row(catalog_metadata_only=1, catalog_count_all=0, catalog_count_custom_attribute=0)
    executor = Executor([[first.as_row(), rollback.as_row()], [older], [metadata]])
    result = handler(executor)()
    assert result.status_code == 200 and result.data["catalog_revision"] == 17
    assert executor.calls[1][1]["catalog_revision"] == 17


def test_foreign_project_rejection_precedes_control_or_compatibility_reads():
    def reject(*args, **kwargs):
        raise ValueError("foreign project")
    executor = Executor([])
    assert handler(executor, authorize=reject)().status_code == 400
    assert executor.calls == []


def test_prepared_initial_follow_returns_pending_then_reads_only_qualified_catalog():
    row = _activation_row(lifecycle_mode="initial_backfill", lineage_anchor_revision=17, activation_sequence=1)
    initial_target = event(row).target
    prepared = ActivationControlEvent.create(
        control_sequence=1,
        request_id=initial_follow_request_id("property_catalog_dev_oss", initial_target),
        action=ActivationControlAction.FOLLOW, target=initial_target,
        previous_control_sha256="0" * 64, controlled_at=datetime(2026, 9, 5, tzinfo=UTC),
    ).as_row()
    # Hold publication indefinitely: no unqualified data query or legacy fallback.
    pending = Executor([[prepared], [], [], [prepared]])
    result = handler(pending)()
    assert result.status_code == 200 and result.data["query_status"] == "pending"
    assert result.data["metrics"] == [] and result.data["next_cursor"] is None
    assert result.data["query_complete"] is False and result.data["query_exact"] is False
    assert len(pending.calls) == 4
    assert not {"catalog_epoch", "catalog_revision", "activation_fingerprint"} & result.data.keys()
    qualified = {**prepared, "activation_sequence": 1, "latest_variants": 1,
                 "catalog_revision": initial_target.catalog_revision,
                 "build_token": initial_target.build_token,
                 "activation_sha256": initial_target.activation_sha256}
    metadata = _conflict_row(catalog_metadata_only=1, catalog_count_all=0, catalog_count_custom_attribute=0)
    ready = Executor([[prepared], [qualified], [row], [metadata]])
    result = handler(ready)()
    assert result.status_code == 200 and result.data["query_status"] == "complete"
    assert result.data["catalog_revision"] == 17
    assert ready.calls[2][1]["catalog_revision"] == row["catalog_revision"]
    assert ready.calls[2][1]["catalog_exact_activation"] == 1


def test_rollback_to_unqualified_prepared_target_is_not_pending_or_data_access():
    row = _activation_row()
    first = event(row, action=ActivationControlAction.FOLLOW)
    rollback = event(row, sequence=2, action=ActivationControlAction.ROLLBACK, previous=first.control_sha256)
    executor = Executor([[first.as_row(), rollback.as_row()], []])
    assert handler(executor)().status_code == 503
    assert len(executor.calls) == 2
