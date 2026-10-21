"""Tenant-reauthorization guards for asynchronous exact Observe reads."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist


@pytest.mark.unit
@pytest.mark.django_db
def test_exact_observe_worker_reauthorizes_project_before_clickhouse(
    observe_project,
    organization,
    workspace,
):
    from tracer.services.clickhouse.v2 import query_service
    from tracer.tasks import exact_aggregation

    identity = {
        "project_id": str(observe_project.id),
        "organization_id": str(organization.id),
        "workspace_id": str(workspace.id),
        "filters": [],
    }
    reader = MagicMock(
        return_value={
            "nodes": [],
            "edges": [],
            "path_edges": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        }
    )
    with patch(
        "tracer.services.clickhouse.exact_graph_reads.read_exact_agent_graph",
        reader,
    ):
        with patch.object(
            query_service,
            "V2AnalyticsQueryService",
            return_value=object(),
        ):
            exact_aggregation._observe_payload("observe-agent-graph", identity)
    reader.assert_called_once()

    observe_project.deleted = True
    observe_project.save(update_fields=["deleted"])
    reader.reset_mock()
    with pytest.raises(ValueError, match="project scope is unavailable"):
        exact_aggregation._observe_payload("observe-agent-graph", identity)
    reader.assert_not_called()


@pytest.mark.unit
@pytest.mark.django_db
@pytest.mark.parametrize("scope_failure", ["missing_org", "foreign_workspace"])
def test_exact_observe_worker_rejects_invalid_scope_before_clickhouse(
    observe_project,
    organization,
    workspace,
    scope_failure,
):
    from tracer.tasks import exact_aggregation

    identity = {
        "project_id": str(observe_project.id),
        "organization_id": str(organization.id),
        "workspace_id": str(workspace.id),
        "filters": [],
    }
    if scope_failure == "missing_org":
        identity.pop("organization_id")
    else:
        identity["workspace_id"] = str(uuid4())

    reader = MagicMock()
    with patch(
        "tracer.services.clickhouse.exact_graph_reads.read_exact_agent_graph",
        reader,
    ):
        with pytest.raises((ValueError, ObjectDoesNotExist)):
            exact_aggregation._observe_payload("observe-agent-graph", identity)
    reader.assert_not_called()


@pytest.mark.unit
def test_exact_observe_dispatch_serializes_trusted_tenant_scope():
    from tracer.services.clickhouse import graph_dispatch

    cache.clear()
    with patch(
        "tracer.tasks.exact_aggregation.refresh_exact_aggregation_snapshot.apply_async"
    ) as enqueue:
        graph_dispatch.fetch_agent_graph_ch(
            project_id="11111111-1111-4111-8111-111111111111",
            filters=[],
            organization_id="22222222-2222-4222-8222-222222222222",
            workspace_id="33333333-3333-4333-8333-333333333333",
        )

    identity = enqueue.call_args.kwargs["kwargs"]["identity"]
    assert identity["project_id"] == "11111111-1111-4111-8111-111111111111"
    assert identity["organization_id"] == "22222222-2222-4222-8222-222222222222"
    assert identity["workspace_id"] == "33333333-3333-4333-8333-333333333333"
