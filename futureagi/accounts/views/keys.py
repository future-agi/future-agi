from math import ceil

import structlog
from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet

from accounts.api_key_constants import PERSONAL_KEY_CREATE_URL
from accounts.api_key_policy import (
    can_manage_member_keys,
    is_retired_key,
    live_membership,
    require_personal_key_session,
)
from accounts.api_key_revocation import (
    publish_key_revocation as _publish_key_revocation,
)
from accounts.authentication import allows_no_workspace
from accounts.models import OrgApiKey
from accounts.models.workspace import WorkspaceMembership
from accounts.permissions import RequiresIndependentAuth, RequiresPersonalKeySession
from accounts.serializers.contracts import (
    ACCOUNTS_ERROR_RESPONSES,
    AccountsStringResultResponseSerializer,
    SecretKeyCreateResponseSerializer,
    SecretKeyListResponseSerializer,
    SecretKeysRetiredResponseSerializer,
)
from accounts.serializers.org_api_key import (
    CreateSecretKeySerializer,
    UserSecretKeySerializer,
)
from model_hub.models.api_key import mask_key
from tfc.utils.api_contracts import validated_request
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class GetKeysView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        # drf-yasg otherwise adds an implicit successful GET response.
        responses={
            200: None,
            410: SecretKeysRetiredResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        }
    )
    def get(self, request, *args, **kwargs):
        return Response(
            {"code": "org_keys_retired", "create_url": PERSONAL_KEY_CREATE_URL},
            status=410,
        )


class SecretKeyAPIViewSet(ViewSet):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    # Personal credential management does not write workspace data. The live
    # membership, session issuer and per-key ownership gates below own it.
    workspace_write_exempt = True
    SORT_FIELD_MAP = {
        "keyName": "name",
        "key_name": "name",
        "createdAt": "created_at",
        "created_at": "created_at",
        "createdBy": "user__name",
        "created_by": "user__name",
        "enabled": "enabled",
        "type": "type",
    }

    def get_permissions(self):
        permissions = list(self.permission_classes)
        if self.action == "generate_secret_key":
            permissions += [RequiresIndependentAuth, RequiresPersonalKeySession]
        return [permission() for permission in permissions]

    def _keys(self, request, *, own=False):
        org = getattr(request, "organization", None) or request.user.organization
        if org is None or live_membership(request.user.pk, org.pk) is None:
            raise PermissionDenied("Active organization membership is required.")
        # Personal credentials are org-scoped. no_workspace_objects still
        # returns none() for an authenticated empty landing because OrgApiKey
        # has a workspace FK. Bypass that data manager only here, with explicit
        # live org, soft-delete, credential-type and ownership filters.
        rows = OrgApiKey._base_manager.filter(
            organization=org, deleted=False, type="user"
        )
        if own or not can_manage_member_keys(request):
            rows = rows.filter(user=request.user)
        return rows

    @swagger_auto_schema(
        responses={200: SecretKeyListResponseSerializer, **ACCOUNTS_ERROR_RESPONSES}
    )
    @action(detail=False, methods=["GET"])
    def get_secret_keys(self, request, *args, **kwargs):
        try:
            page = max(
                0,
                int(
                    request.query_params.get("current_page_index")
                    or request.query_params.get("page_number", 0)
                ),
            )
            size = min(200, max(1, int(request.query_params.get("page_size", 20))))
        except (TypeError, ValueError):
            return self._gm.bad_request("Invalid pagination")
        field = self.SORT_FIELD_MAP.get(
            request.query_params.get("sort_field", "created_at"), "created_at"
        )
        prefix = "-" if request.query_params.get("sort_order", "desc") == "desc" else ""
        rows = (
            self._keys(request)
            .select_related("user")
            .order_by(f"{prefix}{field}", "id")
        )
        if request.query_params.get("search"):
            rows = rows.filter(name__icontains=request.query_params["search"])
        count = rows.count()
        return self._gm.success_response(
            {
                "metadata": {
                    "total_rows": count,
                    "total_pages": ceil(count / size),
                    "page_number": page,
                    "page_size": size,
                },
                "table": [
                    {
                        "id": key.pk,
                        "key_name": key.name,
                        "api_key": mask_key(key.api_key),
                        "secret_key": key.secret_key_hint or "********",
                        "created_by": key.user.name if key.user else None,
                        "created_at": key.created_at,
                        "enabled": key.enabled,
                        "type": key.type,
                    }
                    for key in rows[page * size : (page + 1) * size]
                ],
            }
        )

    @validated_request(
        request_serializer=UserSecretKeySerializer,
        responses={
            200: AccountsStringResultResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @allows_no_workspace("POST")
    @action(detail=False, methods=["POST"])
    def enable_key(self, request, *args, **kwargs):
        key = (
            self._keys(request, own=True)
            .filter(pk=request.validated_data["key_id"])
            .first()
        )
        if key is None:
            return self._gm.bad_request(get_error_message("KEY_DOES_NOT_EXIST"))
        if is_retired_key(key):
            return Response(
                {"code": "api_key_retired", "create_url": PERSONAL_KEY_CREATE_URL},
                status=403,
            )
        if key.enabled:
            return self._gm.bad_request(get_error_message("API_KEY_ENABLED"))
        key.enabled = True
        key.save(update_fields=["enabled"])
        return self._gm.success_response(f"{key.name} has been enabled")

    @validated_request(
        request_serializer=UserSecretKeySerializer,
        responses={
            200: AccountsStringResultResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @allows_no_workspace("POST")
    @action(detail=False, methods=["POST"])
    def disable_key(self, request, *args, **kwargs):
        key = self._keys(request).filter(pk=request.validated_data["key_id"]).first()
        if key is None:
            return self._gm.bad_request(get_error_message("KEY_DOES_NOT_EXIST"))
        if not key.enabled:
            return self._gm.bad_request(get_error_message("API_KEY_DISABLED"))
        key.enabled = False
        key.save(update_fields=["enabled"])
        _publish_key_revocation(key)
        return self._gm.success_response(f"{key.name} has been disabled")

    @validated_request(
        request_serializer=CreateSecretKeySerializer,
        responses={200: SecretKeyCreateResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    @allows_no_workspace("POST")
    @action(detail=False, methods=["POST"])
    def generate_secret_key(self, request, *args, **kwargs):
        # The sole OrgApiKey issuer. Keep provenance here as well as in the
        # permission, so direct internal calls cannot bypass the session gate.
        require_personal_key_session(request)
        data = request.validated_data
        org = request.organization
        workspace = None
        if data.get("workspace_id"):
            membership = (
                WorkspaceMembership.no_workspace_objects.select_related("workspace")
                .filter(
                    user=request.user,
                    workspace_id=data["workspace_id"],
                    is_active=True,
                    workspace__organization=org,
                    workspace__is_active=True,
                    workspace__deleted=False,
                )
                .first()
            )
            if membership is None:
                raise PermissionDenied("Active workspace membership is required.")
            workspace = membership.workspace
        if self._keys(request, own=True).filter(name=data["key_name"]).exists():
            return self._gm.bad_request(get_error_message("KEY_NAME_EXISTS"))
        key = OrgApiKey.no_workspace_objects.create(
            name=data["key_name"],
            organization=org,
            type="user",
            user=request.user,
            workspace=workspace,
        )
        try:
            from tfc.deployment_telemetry.events import record_event

            record_event(
                "api_key_created",
                actor_type="api_key",
                actor_id=key.id,
                source="web",
                organization_id=key.organization_id,
            )
        except Exception:
            logger.debug("deployment_telemetry_api_key_event_failed")
        response = self._gm.success_response(
            {
                "key_id": key.id,
                "key_name": key.name,
                "api_key": key.api_key,
                "masked_api_key": mask_key(key.api_key),
                "secret_key": key.secret_key,
                "masked_secret_key": mask_key(key.secret_key),
            }
        )
        response["Cache-Control"] = "no-store"
        return response

    @validated_request(
        request_serializer=UserSecretKeySerializer,
        responses={
            200: AccountsStringResultResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @allows_no_workspace("DELETE")
    @action(detail=False, methods=["DELETE"])
    def delete_secret_key(self, request, *args, **kwargs):
        key = self._keys(request).filter(pk=request.validated_data["key_id"]).first()
        if key is None:
            return self._gm.bad_request(get_error_message("KEY_DOES_NOT_EXIST"))
        key.deleted, key.enabled, key.deleted_at = True, False, timezone.now()
        key.save(update_fields=["deleted", "enabled", "deleted_at"])
        _publish_key_revocation(key)
        return self._gm.success_response(f"Successfully deleted {key.name}")
