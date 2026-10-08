"""Dedicated Slack OAuth install and channel discovery endpoints."""

import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.http import HttpResponseRedirect
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from accounts.utils import get_request_organization
from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
)
from integrations.services.credentials import CredentialManager
from integrations.services.slack_oauth import (
    consume_slack_state,
    exchange_slack_code,
    start_slack_install,
)
from integrations.services.slack_service import SlackApiError, SlackService
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope

logger = logging.getLogger(__name__)


class SlackInstallSerializer(serializers.Serializer):
    connection_id = serializers.UUIDField(required=False)


class SlackInstallView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(SlackInstallSerializer)
    def post(self, request):
        serializer = SlackInstallSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = get_request_organization(request)
        workspace = getattr(request, "workspace", None)
        if (
            not organization
            or not workspace
            or not request.user.can_write_to_workspace(workspace)
        ):
            return Response(
                build_error_envelope(
                    "Workspace write access is required.", status_code=403
                ),
                status=403,
            )
        try:
            result = start_slack_install(
                organization=organization,
                workspace=workspace,
                user=request.user,
                connection_id=serializer.validated_data.get("connection_id"),
            )
        except ValueError as exc:
            return Response(build_error_envelope(str(exc), status_code=400), status=400)
        return Response({"status": True, "result": result})


def _callback_redirect(*, state: str, connection_id: str = "") -> HttpResponseRedirect:
    app_url = settings.APP_BASE_URL or settings.BASE_URL
    query = {"slack": state}
    if connection_id:
        query["connection_id"] = connection_id
    return HttpResponseRedirect(
        f"{app_url.rstrip('/')}/dashboard/settings/integrations?{urlencode(query)}"
    )


def slack_oauth_callback(request):
    """Complete the installation using the single-use state, then redirect to UI."""
    state = request.GET.get("state", "")
    if not state:
        return _callback_redirect(state="error")
    try:
        context = consume_slack_state(state)
        if request.GET.get("error") or not request.GET.get("code"):
            return _callback_redirect(state="error")

        user = get_user_model().objects.get(pk=context["user_id"])
        organization = Organization.objects.get(pk=context["organization_id"])
        workspace = Workspace.objects.get(
            pk=context["workspace_id"], organization=organization, is_active=True
        )
        if not user.is_active or not user.can_write_to_workspace(workspace):
            return _callback_redirect(state="error")

        credentials = exchange_slack_code(request.GET["code"])
        validation = SlackService().validate_credentials(
            "https://slack.com", credentials
        )
        if not validation["valid"]:
            return _callback_redirect(state="error")

        encrypted = CredentialManager.encrypt(credentials)
        connection_id = context.get("connection_id")
        with transaction.atomic():
            if connection_id:
                connection = IntegrationConnection.objects.select_for_update().get(
                    pk=connection_id,
                    organization=organization,
                    workspace=workspace,
                    platform=IntegrationPlatform.SLACK,
                    deleted=False,
                )
                if (
                    CredentialManager.decrypt(
                        bytes(connection.encrypted_credentials)
                    ).get("team_id")
                    != credentials["team_id"]
                ):
                    return _callback_redirect(state="error")
                connection.encrypted_credentials = encrypted
                connection.display_name = credentials["team_name"] or "Slack"
                connection.status = ConnectionStatus.ACTIVE
                connection.status_message = ""
                connection.save(
                    update_fields=[
                        "encrypted_credentials",
                        "display_name",
                        "status",
                        "status_message",
                    ]
                )
            else:
                connection = IntegrationConnection.objects.create(
                    organization=organization,
                    workspace=workspace,
                    created_by=user,
                    platform=IntegrationPlatform.SLACK,
                    display_name=credentials["team_name"] or "Slack",
                    host_url="https://slack.com",
                    encrypted_credentials=encrypted,
                    project=None,
                    external_project_name=credentials["team_id"],
                    status=ConnectionStatus.ACTIVE,
                    backfill_completed=True,
                )
        return _callback_redirect(state="connected", connection_id=str(connection.pk))
    except (
        ValueError,
        IntegrityError,
        Organization.DoesNotExist,
        Workspace.DoesNotExist,
        IntegrationConnection.DoesNotExist,
        get_user_model().DoesNotExist,
    ):
        logger.warning("Slack OAuth callback could not complete", exc_info=True)
        return _callback_redirect(state="error")
    except Exception:
        logger.exception("Unexpected Slack OAuth callback failure")
        return _callback_redirect(state="error")


class SlackChannelsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, connection_id):
        organization = get_request_organization(request)
        workspace = getattr(request, "workspace", None)
        if not organization or not workspace:
            return Response(
                build_error_envelope("Workspace context is required.", status_code=400),
                status=400,
            )
        connection = IntegrationConnection.objects.filter(
            pk=connection_id,
            organization=organization,
            workspace=workspace,
            platform=IntegrationPlatform.SLACK,
            deleted=False,
        ).first()
        if not connection:
            return Response(
                build_error_envelope("Slack connection not found.", status_code=404),
                status=404,
            )
        if connection.status != ConnectionStatus.ACTIVE:
            return Response(
                build_error_envelope(
                    "Slack connection is not active.", status_code=409
                ),
                status=409,
            )
        cursor = request.query_params.get("cursor", "")
        if len(cursor) > 512:
            return Response(
                build_error_envelope("Invalid cursor.", status_code=400), status=400
            )
        try:
            credentials = CredentialManager.decrypt(
                bytes(connection.encrypted_credentials)
            )
            result = SlackService().get_channels(credentials, cursor=cursor)
            return Response({"status": True, "result": result})
        except (SlackApiError, ValueError):
            logger.warning(
                "Slack channel listing failed",
                extra={"connection_id": str(connection_id)},
                exc_info=True,
            )
            return Response(
                build_error_envelope(
                    "Could not list Slack channels. Reconnect Slack if the installation has expired.",
                    status_code=502,
                ),
                status=502,
            )
