from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async
from django.db import connection
from django.test.utils import CaptureQueriesContext

from saml2_auth.tests.ws_helpers import (
    graph_socket,
    no_outbound_frame,
    receive_close_code,
)
from tracer.models.project import Project

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.integration]


@pytest.mark.asyncio
async def test_graph_consumer_foreign_project_frame_denied(
    idp_a, make_saml_token, saml_tenants
):
    """The graph route must reject a B project before processing its frame."""

    project_b = await Project.objects.acreate(
        organization=saml_tenants.org_b,
        workspace=saml_tenants.workspace_b,
        model_type="GenerativeLLM",
        name="B-only graph",
        trace_type="observe",
        user=saml_tenants.b_only,
    )
    _row, token = await sync_to_async(make_saml_token)(
        saml_tenants.ab, saml_tenants.org_a, idp_a
    )
    socket = await graph_socket(token)
    try:
        await socket.send_json_to({"projectId": str(project_b.id), "graph": ""})
        close_code = await receive_close_code(socket)

        assert close_code == 4003
    finally:
        await socket.disconnect()


@pytest.mark.asyncio
async def test_graph_consumer_revoked_inbound_frame_closes_4001_no_query(
    idp_a, make_saml_token, saml_tenants
):
    """A revoked open SAML graph socket stops before any tracer query runs."""

    project_b = await Project.objects.acreate(
        organization=saml_tenants.org_b,
        workspace=saml_tenants.workspace_b,
        model_type="GenerativeLLM",
        name="revoked B graph",
        trace_type="observe",
        user=saml_tenants.b_only,
    )
    row, token = await sync_to_async(make_saml_token)(
        saml_tenants.a1, saml_tenants.org_a, idp_a
    )
    socket = await graph_socket(token)
    try:
        row.is_active = False
        await sync_to_async(row.save)(update_fields=["is_active"])
        with CaptureQueriesContext(connection) as queries:
            await socket.send_json_to({"projectId": str(project_b.id), "graph": ""})
            close_code = await receive_close_code(socket)
        tracer_queries = [
            query["sql"]
            for query in queries.captured_queries
            if "tracer_" in query["sql"]
        ]

        assert close_code == 4001
        assert tracer_queries == []
        assert await no_outbound_frame(socket)
    finally:
        await socket.disconnect()
