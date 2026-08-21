"""Tenant-scope regression tests for unified property catalog authorization."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tracer.models.project import Project
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.dashboard_metrics_catalog import (
    resolve_property_catalog_project_scope,
)


def test_property_catalog_project_scope_uses_explicit_workspace_manager():
    """Authorization cannot inherit an unrelated ambient workspace scope."""

    project_id = "11111111-1111-4111-8111-111111111111"
    workspace = SimpleNamespace(id="22222222-2222-4222-8222-222222222222")
    explicit_manager = MagicMock()
    explicit_manager.filter.return_value.order_by.return_value.values_list.return_value = [
        project_id
    ]

    with (
        patch.object(Project, "no_workspace_objects", explicit_manager),
        patch.object(Project, "objects") as ambient_manager,
        patch(
            "tracer.services.dashboard_metrics_catalog._run_metrics_catalog_pg_read",
            side_effect=lambda _deadline, _family, read: read(),
        ),
    ):
        resolved = resolve_property_catalog_project_scope(
            workspace,
            [project_id],
            deadline=ReadDeadline.start(8_500),
        )

    assert resolved == [project_id]
    explicit_manager.filter.assert_called_once_with(
        workspace=workspace,
        id__in=[project_id],
    )
    ambient_manager.filter.assert_not_called()
