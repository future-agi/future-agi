from urllib.parse import urlencode

import structlog
from django.conf import settings
from django.db import IntegrityError
from django.http import HttpRequest, HttpResponseRedirect
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.utils import get_request_organization
from integrations.models import ConnectionStatus, IntegrationConnection
from integrations.serializers.contracts import IntegrationEmptyRequestSerializer
from integrations.services.github_app import (
    complete_github_install,
    start_github_install,
)
from integrations.services.source_hosts import (
    SourceHost,
    SourceHostError,
    source_host,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.api_serializers import StrictInputSerializer

logger = structlog.get_logger(__name__)


class GitHubInstallResultSerializer(serializers.Serializer):
    install_url = serializers.URLField()


class GitHubInstallResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GitHubInstallResultSerializer()


class SourceRepositoriesQuerySerializer(StrictInputSerializer):
    q = serializers.CharField(
        required=False, allow_blank=True, max_length=100, default=""
    )
    page = serializers.IntegerField(
        required=False, min_value=1, max_value=200, default=1
    )


class SourceRepositorySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()
    default_branch = serializers.CharField(allow_blank=True)
    private = serializers.BooleanField()


class SourceRepositoriesResultSerializer(serializers.Serializer):
    repositories = SourceRepositorySerializer(many=True)
    next_page = serializers.IntegerField(allow_null=True)


class SourceRepositoriesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SourceRepositoriesResultSerializer()


class SourceBranchSerializer(serializers.Serializer):
    name = serializers.CharField()
    is_default = serializers.BooleanField()


class SourceBranchesResultSerializer(serializers.Serializer):
    branches = SourceBranchSerializer(many=True)


class SourceBranchesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SourceBranchesResultSerializer()


def _error(message: str, status_code: int) -> Response:
    return Response(
        build_error_envelope(message, status_code=status_code), status=status_code
    )


class GitHubInstallView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        IntegrationEmptyRequestSerializer,
        responses={200: GitHubInstallResponseSerializer},
    )
    def post(self, request):
        organization = get_request_organization(request)
        workspace = getattr(request, "workspace", None)
        if (
            not organization
            or not workspace
            or not request.user.can_write_to_workspace(workspace)
        ):
            return _error("Workspace write access is required.", 403)
        try:
            install_url = start_github_install(
                organization=organization, workspace=workspace, user=request.user
            )
        except ValueError as exc:
            return _error(str(exc), 400)
        return Response({"status": True, "result": {"install_url": install_url}})


def _callback_redirect(outcome: str) -> HttpResponseRedirect:
    app_url = settings.APP_BASE_URL or settings.BASE_URL
    return HttpResponseRedirect(
        f"{app_url.rstrip('/')}/dashboard/settings/integrations?"
        f"{urlencode({'github': outcome})}"
    )


def github_install_callback(request: HttpRequest) -> HttpResponseRedirect:
    state = request.GET.get("state", "")
    if not state:
        return _callback_redirect("error")
    try:
        result = complete_github_install(
            state=state,
            setup_action=request.GET.get("setup_action", ""),
            code=request.GET.get("code", ""),
            installation_id=request.GET.get("installation_id", ""),
        )
    except (ValueError, SourceHostError, IntegrityError):
        logger.warning("github_install_callback_failed", exc_info=True)
        return _callback_redirect("error")
    return _callback_redirect(result.outcome)


def _source_connection(
    request, connection_id
) -> tuple[IntegrationConnection, SourceHost] | Response:
    organization = get_request_organization(request)
    workspace = getattr(request, "workspace", None)
    if not organization or not workspace:
        return _error("Workspace context is required.", 400)
    connection = IntegrationConnection.no_workspace_objects.filter(
        pk=connection_id, organization=organization, workspace=workspace
    ).first()
    host = source_host(connection.platform) if connection else None
    if connection is None or host is None:
        return _error("Source connection not found.", 404)
    if connection.status != ConnectionStatus.ACTIVE:
        return _error("Source connection is not active.", 409)
    return connection, host


def _host_failed(exc: SourceHostError, connection_id) -> Response:
    logger.warning(
        "source_host_listing_failed",
        connection_id=str(connection_id),
        code=exc.code,
    )
    return _error(exc.message, 503 if exc.retryable else 502)


class SourceRepositoriesView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=SourceRepositoriesQuerySerializer,
        responses={200: SourceRepositoriesResponseSerializer},
    )
    def get(self, request, connection_id):
        found = _source_connection(request, connection_id)
        if isinstance(found, Response):
            return found
        connection, host = found
        query = request.validated_query_data
        try:
            page = host.list_repositories(
                connection, query=query["q"], page=query["page"]
            )
        except SourceHostError as exc:
            return _host_failed(exc, connection_id)
        return Response(
            {
                "status": True,
                "result": {
                    "repositories": SourceRepositorySerializer(
                        page.repositories, many=True
                    ).data,
                    "next_page": page.next_page,
                },
            }
        )


class SourceBranchesView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(responses={200: SourceBranchesResponseSerializer})
    def get(self, request, connection_id, repository_id):
        found = _source_connection(request, connection_id)
        if isinstance(found, Response):
            return found
        connection, host = found
        try:
            branches = host.list_branches(connection, repository_id=repository_id)
        except SourceHostError as exc:
            return _host_failed(exc, connection_id)
        if branches is None:
            return _error("Repository not found in this connection.", 404)
        return Response(
            {
                "status": True,
                "result": {
                    "branches": SourceBranchSerializer(branches, many=True).data
                },
            }
        )
