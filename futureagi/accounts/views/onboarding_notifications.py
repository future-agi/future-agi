import structlog
from django.core.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.models import NotificationChannel
from accounts.serializers.contracts import (
    ACCOUNTS_ERROR_RESPONSES,
    AccountsEmptyRequestSerializer,
)
from accounts.serializers.onboarding_notifications import (
    NotificationChannelTestResponseSerializer,
    NotificationSettingsPatchRequestSerializer,
    NotificationSettingsResponseSerializer,
)
from accounts.services.onboarding.context import resolve_onboarding_context
from accounts.services.onboarding.notification_preferences import (
    notification_settings_payload,
    serialize_channel,
    test_notification_channel,
    upsert_notification_channel,
    upsert_notification_preference,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


def _validation_detail(exc):
    if hasattr(exc, "message_dict"):
        return exc.message_dict
    if hasattr(exc, "messages"):
        return exc.messages
    return str(exc)


class NotificationSettingsView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        responses={
            200: NotificationSettingsResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
    )
    def get(self, request):
        context = resolve_onboarding_context(request)
        if not context.organization:
            return self._gm.bad_request("Organization context is required.")
        return self._gm.success_response(
            notification_settings_payload(
                user=request.user,
                organization=context.organization,
                workspace=context.workspace,
                can_manage=context.permissions["can_manage_workspace"],
            )
        )

    @validated_request(
        request_serializer=NotificationSettingsPatchRequestSerializer,
        responses={
            200: NotificationSettingsResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        strict_request_validation=False,
        reject_unknown_fields=True,
    )
    def patch(self, request):
        context = resolve_onboarding_context(request)
        if not context.organization:
            return self._gm.bad_request("Organization context is required.")

        serializer = NotificationSettingsPatchRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return self._gm.bad_request(serializer.errors)
        data = serializer.validated_data
        can_manage = context.permissions["can_manage_workspace"]

        try:
            for item in data.get("preferences", []):
                scope = item.get("scope") or "user"
                if scope in {"workspace", "organization"} and not can_manage:
                    return self._gm.forbidden_response(
                        "Workspace admin access is required."
                    )
                upsert_notification_preference(
                    organization=context.organization,
                    workspace=(
                        context.workspace
                        if scope in {"workspace", "user_workspace"}
                        else None
                    ),
                    user=(
                        request.user if scope in {"user", "user_workspace"} else None
                    ),
                    actor=request.user,
                    scope=scope,
                    family=item["family"],
                    channel=item["channel"],
                    enabled=item["enabled"],
                    mute_until=item.get("mute_until"),
                    frequency_cap_minutes=item.get("frequency_cap_minutes"),
                    settings=item.get("settings") or {},
                )
            for item in data.get("channels", []):
                if not can_manage:
                    return self._gm.forbidden_response(
                        "Workspace admin access is required."
                    )
                scope = item.get("scope") or "workspace"
                upsert_notification_channel(
                    organization=context.organization,
                    workspace=context.workspace if scope == "workspace" else None,
                    actor=request.user,
                    channel_id=item.get("id"),
                    type=item["type"],
                    display_name=item["display_name"],
                    config=item["config"] if "config" in item else None,
                    is_active=item.get("is_active", True),
                    metadata=item["metadata"] if "metadata" in item else None,
                )
        except ValidationError as exc:
            return self._gm.bad_request(_validation_detail(exc))
        except NotificationChannel.DoesNotExist:
            return self._gm.not_found("Notification channel not found.")
        except Exception as exc:
            logger.exception(
                "notification_settings_update_failed",
                error=str(exc),
                user_id=str(getattr(request.user, "id", "")),
            )
            return self._gm.internal_server_error_response(
                "Failed to update notification settings"
            )

        return self._gm.success_response(
            notification_settings_payload(
                user=request.user,
                organization=context.organization,
                workspace=context.workspace,
                can_manage=can_manage,
            )
        )


class NotificationChannelTestView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=AccountsEmptyRequestSerializer,
        responses={
            200: NotificationChannelTestResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        strict_request_validation=False,
        reject_unknown_fields=True,
    )
    def post(self, request, channel_id):
        context = resolve_onboarding_context(request)
        if not context.organization:
            return self._gm.bad_request("Organization context is required.")
        if not context.permissions["can_manage_workspace"]:
            return self._gm.forbidden_response("Workspace admin access is required.")
        try:
            channel = NotificationChannel.no_workspace_objects.get(
                id=channel_id,
                organization=context.organization,
            )
            if channel.workspace_id and (
                not context.workspace or channel.workspace_id != context.workspace.id
            ):
                return self._gm.not_found("Notification channel not found.")
            channel = test_notification_channel(channel=channel, actor=request.user)
            return self._gm.success_response({"channel": serialize_channel(channel)})
        except NotificationChannel.DoesNotExist:
            return self._gm.not_found("Notification channel not found.")
