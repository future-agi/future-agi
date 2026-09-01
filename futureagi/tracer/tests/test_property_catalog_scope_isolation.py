"""Tenant-scope regression tests for unified property catalog authorization."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tracer.models.project import Project
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.property_catalog.runtime_limits import RUNTIME_LIMITS
from tracer.services.dashboard_metrics_catalog import (
    _resolve_metrics_catalog_project_scope,
    resolve_property_catalog_project_scope,
)

ORGANIZATION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def test_property_catalog_project_scope_uses_explicit_workspace_manager():
    """Authorization cannot inherit an unrelated ambient workspace scope."""

    project_id = "11111111-1111-4111-8111-111111111111"
    workspace = SimpleNamespace(
        id="22222222-2222-4222-8222-222222222222",
        organization_id=ORGANIZATION_ID,
    )
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
        organization_id=ORGANIZATION_ID,
        trace_type="observe",
        id__in=[project_id],
    )
    ambient_manager.filter.assert_not_called()


def test_property_catalog_workspace_scope_materializes_every_eligible_observe_project():
    project_ids = [
        "11111111-1111-4111-8111-111111111111",
        "33333333-3333-4333-8333-333333333333",
    ]
    workspace = SimpleNamespace(
        id="22222222-2222-4222-8222-222222222222",
        organization_id=ORGANIZATION_ID,
    )
    explicit_manager = MagicMock()
    explicit_manager.filter.return_value.order_by.return_value.values_list.return_value = project_ids

    with (
        patch.object(Project, "no_workspace_objects", explicit_manager),
        patch(
            "tracer.services.dashboard_metrics_catalog._run_metrics_catalog_pg_read",
            side_effect=lambda _deadline, _family, read: read(),
        ),
    ):
        resolved = resolve_property_catalog_project_scope(
            workspace,
            (),
            include_workspace_projects=True,
            deadline=ReadDeadline.start(8_500),
        )

    assert resolved == project_ids
    explicit_manager.filter.assert_called_once_with(
        workspace=workspace,
        organization_id=ORGANIZATION_ID,
        trace_type="observe",
    )


def test_legacy_metrics_scope_does_not_inherit_observe_only_eligibility():
    project_ids = ["11111111-1111-4111-8111-111111111111"]
    workspace = SimpleNamespace(
        id="22222222-2222-4222-8222-222222222222",
        organization_id=ORGANIZATION_ID,
    )
    explicit_manager = MagicMock()
    explicit_manager.filter.return_value.order_by.return_value.values_list.return_value = project_ids

    with (
        patch.object(Project, "no_workspace_objects", explicit_manager),
        patch(
            "tracer.services.dashboard_metrics_catalog._run_metrics_catalog_pg_read",
            side_effect=lambda _deadline, _family, read: read(),
        ),
    ):
        resolved, explicit = _resolve_metrics_catalog_project_scope(
            workspace,
            "",
            include_workspace_projects=True,
            deadline=ReadDeadline.start(8_500),
        )

    assert resolved == project_ids
    assert explicit is False
    explicit_manager.filter.assert_called_once_with(
        workspace=workspace,
        organization_id=ORGANIZATION_ID,
    )


@pytest.mark.parametrize(
    "project_ids",
    [
        ["not-a-uuid"],
        ["11111111-1111-4111-8111-111111111111"] * (RUNTIME_LIMITS.max_projects + 1),
    ],
)
def test_property_catalog_project_scope_rejects_malformed_or_oversized_input(
    project_ids,
):
    workspace = SimpleNamespace(
        id="22222222-2222-4222-8222-222222222222",
        organization_id=ORGANIZATION_ID,
    )

    with (
        patch.object(Project, "no_workspace_objects") as manager,
        pytest.raises(ValueError),
    ):
        resolve_property_catalog_project_scope(
            workspace,
            project_ids,
            deadline=ReadDeadline.start(8_500),
        )

    manager.filter.assert_not_called()


def test_property_catalog_project_scope_rejects_mixed_foreign_ids():
    authorized = "11111111-1111-4111-8111-111111111111"
    foreign = "33333333-3333-4333-8333-333333333333"
    workspace = SimpleNamespace(
        id="22222222-2222-4222-8222-222222222222",
        organization_id=ORGANIZATION_ID,
    )
    explicit_manager = MagicMock()
    explicit_manager.filter.return_value.order_by.return_value.values_list.return_value = [
        authorized
    ]

    with (
        patch.object(Project, "no_workspace_objects", explicit_manager),
        patch(
            "tracer.services.dashboard_metrics_catalog._run_metrics_catalog_pg_read",
            side_effect=lambda _deadline, _family, read: read(),
        ),
        pytest.raises(ValueError, match="Some project_ids are invalid"),
    ):
        resolve_property_catalog_project_scope(
            workspace,
            [authorized, foreign],
            deadline=ReadDeadline.start(8_500),
        )
