"""N03: a real bound token cannot reach any view without an accessible landing."""

from unittest.mock import Mock

import pytest
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from accounts.authentication import APIKeyAuthentication, generate_encrypted_message
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.monitor import UserAlertMonitor, UserAlertMonitorLog
from tracer.models.project import Project
from tracer.views.monitor import UserAlertMonitorLogView

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clean_context():
    clear_workspace_context()
    yield
    clear_workspace_context()


def probe():
    reached = Mock()

    class Probe(APIView):
        authentication_classes = [APIKeyAuthentication]
        permission_classes = [IsAuthenticated]

        def get(self, request):
            reached(request.workspace)
            return Response(
                {
                    "workspace": str(request.workspace.pk)
                    if request.workspace is not None
                    else None
                }
            )

        head = post = patch = delete = get

    return Probe.as_view(), reached


@pytest.mark.parametrize("method", ["get", "head", "post", "patch", "delete"])
@pytest.mark.parametrize(
    "path",
    [
        "/tracer/dashboard/",
        "/accounts/update-user-full-name/",
        "/accounts/workspace/switch/",
    ],
)
@pytest.mark.parametrize("actor", ["a2", "v", "a1", "a1b"])
def test_bound_session_without_landing_refused_before_every_view(
    saml_tenants, idp_a, make_saml_token, actor, path, method
):
    user = getattr(saml_tenants, actor)
    third = Workspace.objects.create(
        name="Explicit A3 control",
        organization=saml_tenants.org_a,
        created_by=saml_tenants.a1,
    )
    # Global org roles also have no accessible landing when all workspaces are inactive.
    Workspace._base_manager.filter(organization=saml_tenants.org_a).update(
        is_active=False
    )
    _, token = make_saml_token(user, saml_tenants.org_a, idp_a)
    before = list(WorkspaceMembership._base_manager.order_by("pk").values())
    view, reached = probe()
    request = getattr(APIRequestFactory(), method)(
        path, HTTP_AUTHORIZATION=f"Bearer {token}"
    )
    response = view(request)
    assert response.status_code == 403, response.data
    assert response.data["code"] == "workspace_required"
    reached.assert_not_called()
    assert list(WorkspaceMembership._base_manager.order_by("pk").values()) == before
    # Every denial has an allowed same-view control: a bound owner naming A3.
    clear_workspace_context()
    Workspace._base_manager.filter(pk=third.pk).update(is_active=True)
    _, owner_token = make_saml_token(saml_tenants.a1, saml_tenants.org_a, idp_a)
    request = getattr(APIRequestFactory(), method)(
        path,
        HTTP_AUTHORIZATION=f"Bearer {owner_token}",
        HTTP_X_WORKSPACE_ID=str(third.pk),
    )
    response = view(request)
    assert response.status_code == 200, response.data
    reached.assert_called_once()
    assert reached.call_args.args[0].pk == third.pk


def test_ordinary_unbound_detail_stays_404_with_allowed_same_route_control(
    saml_tenants,
):
    project = Project.objects.create(
        name="Unbound detail control",
        organization=saml_tenants.org_a,
        workspace=saml_tenants.workspace_a,
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    alert = UserAlertMonitor.objects.create(
        name="Unbound detail control",
        organization=saml_tenants.org_a,
        workspace=saml_tenants.workspace_a,
        project=project,
        metric_type="count_of_errors",
        threshold_operator="greater_than",
        threshold_type="static",
        critical_threshold_value=0.1,
        alert_frequency=60,
    )
    log = UserAlertMonitorLog.objects.create(
        alert=alert, type="critical", message="private"
    )
    before = list(UserAlertMonitorLog._base_manager.filter(pk=log.pk).values())
    route = UserAlertMonitorLogView.as_view({"get": "retrieve"})
    for user, expected in ((saml_tenants.a2, 404), (saml_tenants.a1, 200)):
        clear_workspace_context()
        row = AuthToken.no_workspace_objects.create(
            user=user,
            auth_type=AuthTokenType.ACCESS.value,
            is_active=True,
            last_used_at=timezone.now(),
        )
        assert row.auth_origin != "saml"
        token = generate_encrypted_message({"user_id": str(user.pk), "id": str(row.pk)})
        request = APIRequestFactory().get(
            f"/tracer/monitor-logs/{log.pk}/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        response = route(request, pk=str(log.pk))
        assert response.status_code == expected, response.data
        authenticated_request = response.renderer_context["request"]
        assert authenticated_request.user.pk == user.pk
        assert getattr(authenticated_request, "auth_scope", None) is None
        if expected == 200:
            assert str(response.data["id"]) == str(log.pk)
    assert list(UserAlertMonitorLog._base_manager.filter(pk=log.pk).values()) == before
