import json
from unittest.mock import patch

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from rest_framework import status
from traceai_openai import OpenAIInstrumentor

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tfc.constants.roles import OrganizationRoles
from tracer.models.project import Project

USER_CODE_EXAMPLE_PATH = "/tracer/users/get_code_example/"


@pytest.mark.django_db
def test_user_code_example_uses_scoped_observe_project(auth_client, observe_project):
    response = auth_client.get(
        USER_CODE_EXAMPLE_PATH,
        data={"project_id": str(observe_project.id)},
    )

    assert response.status_code == status.HTTP_200_OK
    code_example = response.json()["result"]
    assert f'project_name="{observe_project.name}"' in code_example
    assert "project_type=ProjectType.OBSERVE" in code_example
    assert "OpenAIInstrumentor().instrument" in code_example


@pytest.mark.django_db
def test_user_code_example_without_project_uses_default_project_name(auth_client):
    response = auth_client.get(USER_CODE_EXAMPLE_PATH)

    assert response.status_code == status.HTTP_200_OK
    code_example = response.json()["result"]
    assert 'project_name="New Project"' in code_example
    assert 'user_id="newuser@example.com"' in code_example


@pytest.mark.django_db
def test_user_code_example_uses_current_model_without_legacy_token_limit(auth_client):
    # Pins the example's request contract, not live provider availability.
    response = auth_client.get(USER_CODE_EXAMPLE_PATH)

    assert response.status_code == status.HTTP_200_OK
    code_example = response.json()["result"]
    assert 'model="gpt-6.1-terra"' in code_example
    assert "gpt-3.5-turbo" not in code_example
    assert "max_tokens" not in code_example


@pytest.mark.django_db
@pytest.mark.parametrize("scoped", [False, True])
def test_copied_user_example_sends_current_request_and_records_trace(
    auth_client, observe_project, scoped
):
    """Execute the returned source with real SDKs and an in-process HTTP peer.

    Only registration/export and the provider HTTP boundary are local substitutes;
    this does not assert that the named model is available from the live provider.
    """
    params = {"project_id": str(observe_project.id)} if scoped else {}
    response = auth_client.get(USER_CODE_EXAMPLE_PATH, data=params)
    assert response.status_code == status.HTTP_200_OK
    requests = []

    def complete(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "chatcmpl-example",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-6.1-terra",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "A synthetic haiku.",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 5,
                    "total_tokens": 13,
                },
            },
        )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    instrumentor = OpenAIInstrumentor()
    namespace = {}
    try:
        with (
            patch("fi_instrumentation.register", return_value=provider) as register,
            patch.dict(
                "os.environ",
                OPENAI_API_KEY="synthetic-example-key",
                OPENAI_BASE_URL="http://localhost/v1",
            ),
            patch.object(
                httpx.Client,
                "send",
                autospec=True,
                side_effect=lambda self, request, **kwargs: complete(request),
            ),
        ):
            exec(
                compile(response.json()["result"], "<user-example>", "exec"), namespace
            )
        assert requests == [
            {
                "model": "gpt-6.1-terra",
                "messages": [{"role": "user", "content": "Write a haiku."}],
            }
        ]
        assert register.call_args.kwargs["project_name"] == (
            observe_project.name if scoped else "New Project"
        )
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].attributes["session.id"] == "new-session"
        assert spans[0].attributes["user.id"] == "newuser@example.com"
    finally:
        if "client" in namespace:
            namespace["client"].close()
        instrumentor.uninstrument()
        provider.shutdown()


@pytest.mark.django_db
def test_user_code_example_rejects_non_observe_project(auth_client, project):
    response = auth_client.get(
        USER_CODE_EXAMPLE_PATH,
        data={"project_id": str(project.id)},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["message"] == "Project type must be 'observe'."


@pytest.mark.django_db
def test_user_code_example_hides_projects_outside_request_scope(
    auth_client,
    organization,
    user,
    workspace,
):
    other_workspace = Workspace.no_workspace_objects.create(
        name="Other Workspace",
        organization=organization,
        is_active=True,
        created_by=user,
    )
    other_workspace_project = Project.no_workspace_objects.create(
        name="Do Not Leak Same Org Workspace Project",
        organization=organization,
        workspace=other_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )

    other_organization = Organization.objects.create(name="Other Organization")
    other_user = User.objects.create_user(
        email="other-user@example.com",
        password="testpassword123",
        name="Other User",
        organization=other_organization,
        organization_role=OrganizationRoles.OWNER,
    )
    other_org_workspace = Workspace.no_workspace_objects.create(
        name="Other Org Workspace",
        organization=other_organization,
        is_default=True,
        is_active=True,
        created_by=other_user,
    )
    other_org_project = Project.no_workspace_objects.create(
        name="Do Not Leak Other Org Project",
        organization=other_organization,
        workspace=other_org_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )

    for out_of_scope_project in (other_workspace_project, other_org_project):
        response = auth_client.get(
            USER_CODE_EXAMPLE_PATH,
            data={"project_id": str(out_of_scope_project.id)},
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert out_of_scope_project.name.encode() not in response.content
        assert b"project_name=" not in response.content
