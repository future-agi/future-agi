"""No trace API response names a Future AGI Cloud host.

The shared trace page, the span drawer and the trace tree render what these
responses carry. A futureagi.com URL in any field makes the viewer's browser
request production (an icon) or leave the install (a link), on a self-hosted
install and on every local or cloud test stack. Two tenants, each with spans
from every provider the ClickHouse reader maps.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.tests._vendor_hosts import assert_no_vendor_urls, vendor_urls

PROVIDERS = ("openai", "anthropic", "google", "gcp.vertex.agent", "vapi", "retell")


def _result(response):
    body = response.json()
    return body.get("result", body)


def _spans(project, trace, ch_seed, label):
    now = timezone.now()
    spans = []
    for index, provider in enumerate(PROVIDERS):
        spans.append(
            ObservationSpan.objects.create(
                id=f"span_{label}_{index}_{uuid.uuid4().hex[:8]}",
                project=project,
                trace=trace,
                name=f"{provider} call",
                observation_type="llm",
                start_time=now - timedelta(seconds=10 - index),
                end_time=now - timedelta(seconds=9 - index),
                input={"messages": [{"role": "user", "content": "Hello"}]},
                output={"choices": [{"message": {"content": "Hi"}}]},
                model="test-model",
                provider=provider,
                prompt_tokens=3,
                completion_tokens=2,
                total_tokens=5,
                cost=0.0001,
                latency_ms=1000,
                status="OK",
                metadata={},
            )
        )
    ch_seed(spans)
    return spans


def _second_tenant(user):
    """Tenant B: its own organization, workspace, project and trace. The test
    user acts in it the way the UI does, through the request's workspace."""
    suffix = uuid.uuid4().hex[:8]
    org = Organization.objects.create(name=f"Tenant B {suffix}")
    workspace = Workspace.no_workspace_objects.create(
        name=f"Tenant B Workspace {suffix}",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    project = Project.objects.create(
        name="Tenant B Project",
        organization=org,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        metadata={},
        config=[],
    )
    trace = Trace.objects.create(project=project, name="Tenant B Trace")
    return workspace, project, trace


def _share_and_resolve(auth_client, api_client, trace):
    created = auth_client.post(
        "/tracer/shared-links/",
        data={
            "resource_type": "trace",
            "resource_id": str(trace.id),
            "access_type": "public",
        },
        format="json",
    )
    assert created.status_code == status.HTTP_201_CREATED, created.content
    link = _result(created)
    assert_no_vendor_urls(created.json(), "POST /tracer/shared-links/")

    detail = auth_client.get(f"/tracer/shared-links/{link['id']}/")
    assert detail.status_code == status.HTTP_200_OK, detail.content
    assert_no_vendor_urls(detail.json(), "GET /tracer/shared-links/<id>/")

    resolved = api_client.get(f"/tracer/shared/{link['token']}/")
    assert resolved.status_code == status.HTTP_200_OK, resolved.content
    return resolved.json()


def _flatten(nodes):
    for node in nodes:
        yield node["observation_span"]
        yield from _flatten(node.get("children") or [])


def _assert_tenant(auth_client, api_client, trace, spans):
    payload = _share_and_resolve(auth_client, api_client, trace)
    assert_no_vendor_urls(payload, "GET /tracer/shared/<token>/ (shared trace page)")

    served = {s["id"]: s for s in _flatten(payload["data"]["observation_spans"])}
    assert set(served) == {s.id for s in spans}
    for span in spans:
        # The shared page draws this path from the page's own origin.
        logo = served[span.id]["provider_logo"]
        assert isinstance(logo, str), (span.provider, logo)
        assert logo.startswith("/") and not logo.startswith("//"), logo

    for span in spans:
        response = auth_client.get(f"/tracer/observation-span/{span.id}/")
        assert response.status_code == status.HTTP_200_OK, response.content
        assert_no_vendor_urls(
            response.json(), f"GET /tracer/observation-span/{span.provider}/"
        )
    return set(served)


def test_scanner_flags_hosts_not_addresses():
    flagged = {
        "icon": "https://app.futureagi.com/static/providers/openai.svg",
        "apex": "see https://futureagi.com/legal.",
        "relative": "//cdn.futureagi.com/x.png",
        "port": "http://api.futureagi.com:443/v1",
        "markdown": "[open](https://app.futureagi.com/dashboard)",
    }
    clean = {
        "email": "test-1234@futureagi.com",
        "path": "/icons/settings/openai.png",
        "lookalike": "https://futureagi.com.example.test/x",
        "suffix": "https://notfutureagi.com/x",
        "other": "https://example.test/futureagi.com",
    }
    assert {p.split(".", 1)[1] for p, _ in vendor_urls(flagged)} == set(flagged)
    assert list(vendor_urls(clean)) == []


@pytest.mark.django_db
def test_trace_responses_of_two_tenants_name_no_vendor_host(
    auth_client, api_client, user, workspace, project, trace, ch_seed
):
    spans_a = _spans(project, trace, ch_seed, "a")
    seen_a = _assert_tenant(auth_client, api_client, trace, spans_a)

    workspace_b, project_b, trace_b = _second_tenant(user)
    spans_b = _spans(project_b, trace_b, ch_seed, "b")
    auth_client.set_workspace(workspace_b)
    seen_b = _assert_tenant(auth_client, api_client, trace_b, spans_b)

    # Each tenant's share carries only its own spans.
    assert not seen_a & seen_b
