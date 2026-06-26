import hashlib
import traceback
from math import ceil

import redis
import structlog
from django.conf import settings
from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet

logger = structlog.get_logger(__name__)

FI_AUTH_REVOKE_CHANNEL = "fi:auth:revoke"


def _publish_key_revocation(api_key: str, secret_key: str) -> None:
    """
    Publish a cache-bust to fi-collector so revoked keys are evicted immediately.
    """
    try:
        cache_key = hashlib.sha256(f"{api_key}:{secret_key}".encode()).hexdigest()
        r = redis.Redis.from_url(
            getattr(settings, "REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
        r.publish(FI_AUTH_REVOKE_CHANNEL, cache_key)
    except Exception:
        logger.warning("fi_auth_revoke_publish_failed", exc_info=True)

from accounts.models import OrgApiKey
from accounts.serializers.contracts import (
    ACCOUNTS_ERROR_RESPONSES,
    AccountsStringResultResponseSerializer,
    SecretKeyCreateResponseSerializer,
    SecretKeyListResponseSerializer,
    SecretKeysResponseSerializer,
)
from accounts.serializers.org_api_key import (
    CreateSecretKeySerializer,
    OrgApiKeySerializer,
    UserSecretKeySerializer,
)
from analytics.utils import (
    MixpanelEvents,
    get_mixpanel_properties,
    track_mixpanel_event,
)
from model_hub.models.api_key import mask_key
from tfc.constants.roles import RolePermissions
from tfc.utils.api_contracts import validated_request
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods


class GetKeysView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(
        responses={200: SecretKeysResponseSerializer, **ACCOUNTS_ERROR_RESPONSES}
    )
    def get(self, request, *args, **kwargs):
        user_organization = (
            getattr(request, "organization", None) or self.request.user.organization
        )
        if request.headers.get("X-Api-Key") is not None:
            properties = get_mixpanel_properties(user=request.user)
            track_mixpanel_event(MixpanelEvents.SDK_INIT.value, properties)

        try:
            apiKeys = OrgApiKey.no_workspace_objects.filter(
                organization=user_organization, type="system", enabled=True
            )
            if not apiKeys.exists():
                org_api_key = OrgApiKey.no_workspace_objects.create(
                    organization=user_organization, type="system"
                )
            else:
                org_api_key = apiKeys.first()

            serialized_keys = OrgApiKeySerializer(org_api_key)
            return Response({"status": "success", "data": serialized_keys.data})

        except Exception:
            traceback.print_exc()
            return self._gm.internal_server_error_response(
                "Failed to retrieve API keys"
            )


class SecretKeyAPIViewSet(ViewSet):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    # Map frontend field names (camelCase) to database field names
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

    @swagger_auto_schema(
        responses={200: SecretKeyListResponseSerializer, **ACCOUNTS_ERROR_RESPONSES}
    )
    @action(detail=False, methods=["GET"])
    def get_secret_keys(self, request, *args, **kwargs):
        user_organization = (
            getattr(request, "organization", None) or self.request.user.organization
        )

        try:
            search = request.query_params.get("search", None)
            # Support both 'current_page_index' (frontend) and 'page_number' (legacy)
            page_number = int(
                request.query_params.get("current_page_index")
                or request.query_params.get("page_number", 0)
            )
            page_size = int(request.query_params.get("page_size", 20))

            # Sorting parameters from frontend
            sort_field = request.query_params.get("sort_field", "created_at")
            sort_order = request.query_params.get("sort_order", "desc")

            # Map frontend field name to database field
            db_sort_field = self.SORT_FIELD_MAP.get(sort_field, "created_at")
            order_prefix = "-" if sort_order == "desc" else ""
            order_by = f"{order_prefix}{db_sort_field}"

            # Build base queryset with select_related to avoid N+1 queries
            apiKeys = (
                OrgApiKey.objects.filter(organization=user_organization, deleted=False)
                .select_related("user")
                .order_by(order_by)
            )

            if search:
                apiKeys = apiKeys.filter(name__icontains=search)

            # Get total count for pagination metadata
            total_count = apiKeys.count()

            if total_count == 0:
                return self._gm.success_response(
                    {
                        "metadata": {
                            "total_rows": 0,
                            "total_pages": 0,
                            "page_number": page_number,
                            "page_size": page_size,
                        },
                        "table": [],
                    }
                )

            # Database-level pagination (much more efficient)
            start = page_number * page_size
            paginated_keys = apiKeys[start : start + page_size]

            table = [
                {
                    "id": key.id,
                    "key_name": key.name,
                    "api_key": mask_key(key.api_key),
                    "secret_key": mask_key(key.secret_key),
                    "created_by": key.user.name if key.user else None,
                    "created_at": key.created_at,
                    "enabled": key.enabled,
                    "type": key.type,
                }
                for key in paginated_keys
            ]

            response = {
                "metadata": {
                    "total_rows": total_count,
                    "total_pages": ceil(total_count / page_size),
                    "page_number": page_number,
                    "page_size": page_size,
                },
                "table": table,
            }
            return self._gm.success_response(response)

        except Exception:
            traceback.print_exc()
            return self._gm.bad_request(get_error_message("FAILED_TO_GET_KEYS"))

    @validated_request(
        request_serializer=UserSecretKeySerializer,
        responses={
            200: AccountsStringResultResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @action(detail=False, methods=["POST"])
    def enable_key(self, request, *args, **kwargs):
        try:
            key_id = request.validated_data.get("key_id")
            if not key_id:
                return self._gm.bad_request(get_error_message("KEY_ID_REQUIRED"))

            try:
                apikey = OrgApiKey.objects.get(
                    id=key_id,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
                if apikey.enabled:
                    return self._gm.bad_request(get_error_message("API_KEY_ENABLED"))
                apikey.enabled = True
                apikey.save(update_fields=["enabled"])

                return self._gm.success_response(f"{apikey.name} has been enabled")
            except OrgApiKey.DoesNotExist:
                return self._gm.bad_request(get_error_message("KEY_DOES_NOT_EXIST"))

        except Exception:
            traceback.print_exc()
            return self._gm.bad_request(get_error_message("SECRET_KEY_NOT_ENABLED"))

    @validated_request(
        request_serializer=UserSecretKeySerializer,
        responses={
            200: AccountsStringResultResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @action(detail=False, methods=["POST"])
    def disable_key(self, request, *args, **kwargs):
        try:
            key_id = request.validated_data.get("key_id")
            if not key_id:
                return self._gm.bad_request(get_error_message("KEY_ID_REQUIRED"))

            try:
                apikey = OrgApiKey.objects.get(
                    id=key_id,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
                if not apikey.enabled:
                    return self._gm.bad_request(get_error_message("API_KEY_DISABLED"))
                apikey.enabled = False
                apikey.save(update_fields=["enabled"])
                _publish_key_revocation(apikey.api_key, apikey.secret_key)

                return self._gm.success_response(f"{apikey.name} has been disabled")
            except OrgApiKey.DoesNotExist:
                return self._gm.bad_request(get_error_message("KEY_DOES_NOT_EXIST"))

        except Exception:
            traceback.print_exc()
            return self._gm.bad_request(get_error_message("SECRET_KEY_NOT_DISABLED"))

    @validated_request(
        request_serializer=CreateSecretKeySerializer,
        responses={200: SecretKeyCreateResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    @action(detail=False, methods=["POST"])
    def generate_secret_key(self, request, *args, **kwargs):
        try:
            key_name = request.validated_data.get("key_name")

            if OrgApiKey.objects.filter(
                name=key_name,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            ).exists():
                return self._gm.bad_request(get_error_message("KEY_NAME_EXISTS"))

            org_key = OrgApiKey.objects.create(
                name=key_name,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                type="user",
                user=request.user,
            )
            response = {
                "key_id": org_key.id,
                "key_name": org_key.name,
                "api_key": org_key.api_key,
                "masked_api_key": mask_key(org_key.api_key),
                "secret_key": org_key.secret_key,
                "masked_secret_key": mask_key(org_key.secret_key),
            }
            return self._gm.success_response(response)
        except Exception:
            traceback.print_exc()
            return self._gm.bad_request(get_error_message("UNABLE_TO_GENERATE_KEY"))

    @validated_request(
        request_serializer=UserSecretKeySerializer,
        responses={
            200: AccountsStringResultResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @action(detail=False, methods=["DELETE"])
    def delete_secret_key(self, request, *args, **kwargs):
        try:
            # Check role in the current org (membership first, FK fallback)
            _current_org = (
                getattr(request, "organization", None) or request.user.organization
            )
            from accounts.models.organization_membership import OrganizationMembership

            _mem = (
                OrganizationMembership.no_workspace_objects.filter(
                    user=request.user, organization=_current_org, is_active=True
                ).first()
                if _current_org
                else None
            )
            _role = _mem.role if _mem else request.user.organization_role
            if _role not in RolePermissions.OWNER_ROLES:
                return self._gm.bad_request(get_error_message("UNAUTHORIZED_ACCESS"))

            key_id = request.validated_data.get("key_id")
            if not key_id:
                return self._gm.bad_request(get_error_message("KEY_ID_REQUIRED"))
            org_key = OrgApiKey.objects.get(
                id=key_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
            )
            org_key.deleted = True
            org_key.deleted_at = timezone.now()
            org_key.save(update_fields=["deleted", "deleted_at"])
            _publish_key_revocation(org_key.api_key, org_key.secret_key)

            return self._gm.success_response(f"Successfully deleted {org_key.name}")
        except OrgApiKey.DoesNotExist:
            return self._gm.bad_request(get_error_message("KEY_DOES_NOT_EXIST"))
        except Exception:
            traceback.print_exc()
            return self._gm.bad_request(get_error_message("FAILED_TO_DELETE_KEY"))
